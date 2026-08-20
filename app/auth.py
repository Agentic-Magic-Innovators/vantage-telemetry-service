"""User-specific API token authentication for Vantage Harness — PostgreSQL backend."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Iterable

from fastapi import Header, HTTPException, Request

from . import db

logger = logging.getLogger(__name__)

_auth_enforced: bool = False

# Name of the httponly dashboard login cookie (harness uses vantage_session).
UI_SESSION_COOKIE = "vantage_session"
TELEMETRY_SESSION_COOKIE = "vantage_telemetry_session"


@dataclass(frozen=True)
class AuthContext:
    token_id: str
    user_id: str
    team_id: str
    scopes: list[str]
    auth_type: str
    token_note: str = ""

    def as_state(self) -> dict:
        return {
            "tokenId": self.token_id,
            "userId": self.user_id,
            "teamId": self.team_id,
            "scopes": self.scopes,
            "authType": self.auth_type,
            "tokenNote": self.token_note or "",
        }


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _ts_str(dt: datetime.datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


def _is_auth_enforced() -> bool:
    global _auth_enforced
    if _auth_enforced:
        return True
    if bool(os.environ.get("VANTAGE_API_KEY")) or db.is_available():
        _auth_enforced = True
    return _auth_enforced


def _hash_token(token: str) -> str:
    pepper = os.environ.get("VANTAGE_TOKEN_PEPPER", "")
    if pepper:
        digest = hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"sha256:{digest}"


def _make_token() -> str:
    return f"vh_live_{secrets.token_urlsafe(32)}"


def _token_prefix(token: str) -> str:
    return token[:16] + "..."


def _scopes_list(scopes: Iterable[str] | None) -> list[str]:
    values = sorted({str(s).strip() for s in (scopes or ["inference"]) if str(s).strip()})
    return values or ["inference"]


def _legacy_scopes() -> list[str]:
    raw = os.environ.get("VANTAGE_LEGACY_SCOPES", "")
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return ["inference", "telemetry", "read"]


def _row_to_public(row) -> dict:
    scopes = row["scopes"]
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "teamId": row["team_id"] or "",
        "tokenPrefix": row["token_prefix"],
        "scopes": scopes or [],
        "enabled": bool(row["enabled"]),
        "createdAt": _ts_str(row["created_at"]),
        "lastUsedAt": _ts_str(row["last_used_at"]),
        "revokedAt": _ts_str(row["revoked_at"]) if row["revoked_at"] else None,
        "note": row["note"] or "",
    }


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token CRUD
# ---------------------------------------------------------------------------

async def create_token(
    user_id: str,
    team_id: str = "",
    scopes: list[str] | None = None,
    note: str = "",
) -> dict:
    global _auth_enforced
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("userId is required")
    pool = db.get_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")
    token = _make_token()
    token_id = f"tok_{uuid.uuid4().hex[:20]}"
    scopes_val = _scopes_list(scopes)
    now = _utc_now()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO api_tokens
                (id, user_id, team_id, token_hash, token_prefix, scopes, enabled, created_at, note)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7, $8)
            """,
            token_id,
            user_id,
            str(team_id or "").strip(),
            _hash_token(token),
            _token_prefix(token),
            scopes_val,
            now,
            str(note or "").strip(),
        )
    _auth_enforced = True
    return {
        "id": token_id,
        "userId": user_id,
        "teamId": str(team_id or "").strip(),
        "scopes": scopes_val,
        "token": token,
    }


async def list_user_tokens(user_id: str) -> list[dict]:
    pool = db.get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, team_id, token_prefix, scopes, enabled,
                   created_at, last_used_at, revoked_at, note
            FROM api_tokens
            WHERE user_id = $1 AND enabled = TRUE AND revoked_at IS NULL
            ORDER BY created_at DESC
            """,
            user_id,
        )
    return [_row_to_public(r) for r in rows]


async def list_tokens() -> list[dict]:
    pool = db.get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, team_id, token_prefix, scopes, enabled,
                   created_at, last_used_at, revoked_at, note
            FROM api_tokens
            ORDER BY created_at DESC
            """
        )
    return [_row_to_public(r) for r in rows]


async def revoke_token(token_id: str) -> bool:
    pool = db.get_pool()
    if not pool:
        return False
    now = _utc_now()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE api_tokens
            SET enabled = FALSE, revoked_at = COALESCE(revoked_at, $1)
            WHERE id = $2
            """,
            now,
            token_id,
        )
    return result.endswith("1")


async def rotate_token(token_id: str) -> dict | None:
    pool = db.get_pool()
    if not pool:
        return None
    token = _make_token()
    new_id = f"tok_{uuid.uuid4().hex[:20]}"
    now = _utc_now()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT user_id, team_id, scopes, note
                FROM api_tokens
                WHERE id = $1 AND enabled = TRUE AND revoked_at IS NULL
                """,
                token_id,
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE api_tokens SET enabled = FALSE, revoked_at = $1 WHERE id = $2",
                now,
                token_id,
            )
            scopes = row["scopes"]
            if isinstance(scopes, str):
                scopes = json.loads(scopes)
            await conn.execute(
                """
                INSERT INTO api_tokens
                    (id, user_id, team_id, token_hash, token_prefix, scopes, enabled, created_at, note)
                VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7, $8)
                """,
                new_id,
                row["user_id"],
                row["team_id"] or "",
                _hash_token(token),
                _token_prefix(token),
                scopes or ["inference"],
                now,
                row["note"] or "",
            )
    return {
        "id": new_id,
        "userId": row["user_id"],
        "teamId": row["team_id"] or "",
        "scopes": scopes or ["inference"],
        "token": token,
    }


# ---------------------------------------------------------------------------
# Admin (username + password) auth
# ---------------------------------------------------------------------------

async def admin_exists() -> bool:
    pool = db.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM admins LIMIT 1")
    return row is not None


async def create_admin(username: str, password: str) -> dict:
    username = username.strip().lower()
    if not username or not password:
        raise ValueError("username and password are required")
    pool = db.get_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")
    admin_id = f"adm_{uuid.uuid4().hex[:20]}"
    password_hash = await asyncio.to_thread(_hash_password, password)
    now = _utc_now()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO admins (id, username, password_hash, created_at) VALUES ($1, $2, $3, $4)",
            admin_id,
            username,
            password_hash,
            now,
        )
    return {"id": admin_id, "username": username}


async def sync_admin_password(username: str, password: str) -> bool:
    """Update password for an existing admin (dev/migration convenience)."""
    username = username.strip().lower()
    if not username or not password:
        return False
    pool = db.get_pool()
    if not pool:
        return False
    password_hash = await asyncio.to_thread(_hash_password, password)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE admins SET password_hash = $1 WHERE username = $2",
            password_hash,
            username,
        )
    return result.endswith("1")


async def verify_admin_password(username: str, password: str) -> bool:
    username = username.strip().lower()
    pool = db.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM admins WHERE username = $1", username
        )
    if row is None:
        return False
    return await asyncio.to_thread(_verify_password, password, row["password_hash"])


# ---------------------------------------------------------------------------
# Self-service user registration (admin-approval gated)
# ---------------------------------------------------------------------------
# A "user" account here only grants the restricted dashboard "user" role
# (personal activity/usage, never team-wide analytics or config -- see
# require_scope/_ui_session_context). Registration never creates an admin.

_USER_STATUSES = {"pending", "approved", "rejected"}


def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"] or "",
        "status": row["status"],
        "requestedAt": _ts_str(row["requested_at"]),
        "approvedAt": _ts_str(row["approved_at"]) if row["approved_at"] else None,
        "approvedBy": row["approved_by"] or "",
    }


async def get_user(email: str) -> dict | None:
    email = email.strip().lower()
    pool = db.get_pool()
    if not pool or not email:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
    return _row_to_user(row) if row else None


async def register_user(email: str, name: str = "") -> dict:
    """Create (or re-submit) a pending registration.

    Idempotent by design: registering an already-pending email just returns
    its current pending state; registering an already-approved email tells
    the caller they can log in; re-registering a previously-rejected email
    resets it back to pending so a person isn't permanently locked out by
    one rejection (e.g. a typo'd email the first time).
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    pool = db.get_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")

    now = _utc_now()
    existing = await get_user(email)
    if existing and existing["status"] == "approved":
        return {"status": "approved", "alreadyExists": True}
    if existing and existing["status"] == "pending":
        return {"status": "pending", "alreadyExists": True}

    user_id = existing["id"] if existing else f"usr_{uuid.uuid4().hex[:20]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, email, name, status, requested_at, approved_at, approved_by)
            VALUES ($1, $2, $3, 'pending', $4, NULL, NULL)
            ON CONFLICT (email) DO UPDATE
                SET status = 'pending', name = COALESCE(NULLIF($3, ''), users.name),
                    requested_at = $4, approved_at = NULL, approved_by = NULL
            """,
            user_id,
            email,
            name.strip(),
            now,
        )
    return {"status": "pending", "alreadyExists": False}


async def list_users(status: str | None = None) -> list[dict]:
    pool = db.get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM users WHERE status = $1 ORDER BY requested_at DESC", status
            )
        else:
            rows = await conn.fetch("SELECT * FROM users ORDER BY requested_at DESC")
    return [_row_to_user(r) for r in rows]


async def approve_user(email: str, approved_by: str) -> bool:
    email = email.strip().lower()
    pool = db.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status = 'approved', approved_at = $1, approved_by = $2 WHERE email = $3",
            _utc_now(),
            approved_by,
            email,
        )
    return result.endswith("1")


async def reject_user(email: str) -> bool:
    email = email.strip().lower()
    pool = db.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status = 'rejected', approved_at = NULL, approved_by = NULL WHERE email = $1",
            email,
        )
    return result.endswith("1")


async def is_email_authorized(email: str, allowed_emails: list[str]) -> bool:
    """True if `email` may use the restricted "user" dashboard role, via
    either the config-file bootstrap allowlist or an approved registration.
    """
    email_norm = email.strip().lower()
    if allowed_emails and email_norm in {e.strip().lower() for e in allowed_emails}:
        return True
    user = await get_user(email_norm)
    return bool(user and user["status"] == "approved")


# ---------------------------------------------------------------------------
# UI session tokens (dashboard login)
# ---------------------------------------------------------------------------

async def create_ui_session(
    role: str, user_id: str, ttl_hours: int = 8, service: str = "harness"
) -> str:
    token = secrets.token_urlsafe(32)
    session_id = f"ses_{uuid.uuid4().hex[:20]}"
    now = _utc_now()
    expires = now + datetime.timedelta(hours=ttl_hours)
    pool = db.get_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ui_sessions (id, token_hash, role, user_id, created_at, expires_at, service)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            session_id,
            _hash_token(token),
            role,
            user_id,
            now,
            expires,
            service,
        )
    return token


async def verify_ui_session(token: str, service: str | None = None) -> dict | None:
    if not token:
        return None
    pool = db.get_pool()
    if not pool:
        return None
    token_hash = _hash_token(token)
    now = _utc_now()
    async with pool.acquire() as conn:
        if service:
            row = await conn.fetchrow(
                """
                UPDATE ui_sessions SET last_used_at = $1
                WHERE token_hash = $2 AND expires_at > $3 AND service = $4
                RETURNING id, role, user_id
                """,
                now,
                token_hash,
                now,
                service,
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE ui_sessions SET last_used_at = $1
                WHERE token_hash = $2 AND expires_at > $3
                RETURNING id, role, user_id
                """,
                now,
                token_hash,
                now,
            )
    if row is None:
        return None
    return {"sessionId": row["id"], "role": row["role"], "userId": row["user_id"]}


async def delete_ui_session(token: str) -> bool:
    if not token:
        return False
    pool = db.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM ui_sessions WHERE token_hash = $1", _hash_token(token)
        )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Request-level token lookup (called per inference request)
# ---------------------------------------------------------------------------

def _extract_token(authorization: str | None, x_api_key: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _legacy_enabled() -> bool:
    # Opt-in, not opt-out: a single shared VANTAGE_API_KEY only proves the
    # caller holds *a* valid credential, not which user/team it's acting as --
    # _legacy_context below trusts the client-supplied X-User-Id/X-Team-Id
    # headers verbatim, so anyone holding the shared key can forge attribution
    # for another user's telemetry, cost, and audit data. Defaulting this off
    # means a fresh deployment only gets that exposure if an operator
    # deliberately turns it on for migration purposes.
    return os.environ.get("VANTAGE_LEGACY_API_KEY_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def _legacy_context(request: Request, provided: str) -> AuthContext | None:
    expected = os.environ.get("VANTAGE_API_KEY", "")
    if not expected or not _legacy_enabled():
        return None
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        return None
    logger.warning(
        "Legacy shared-key auth accepted a request with client-supplied "
        "identity headers (x-user-id=%r, x-team-id=%r) -- these are NOT "
        "verified against the credential and can be forged by anyone holding "
        "the shared VANTAGE_API_KEY. Migrate this caller to a per-user token "
        "(create_token) and disable VANTAGE_LEGACY_API_KEY_ENABLED.",
        request.headers.get("x-user-id", "anonymous"),
        request.headers.get("x-team-id", "default"),
    )
    return AuthContext(
        token_id="legacy-env",
        user_id=request.headers.get("x-user-id", "anonymous"),
        team_id=request.headers.get("x-team-id", "default"),
        scopes=_legacy_scopes(),
        auth_type="legacy_env",
    )


async def _lookup_context(provided: str) -> AuthContext | None:
    if not provided:
        return None
    pool = db.get_pool()
    if not pool:
        return None
    token_hash = _hash_token(provided)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE api_tokens
                SET last_used_at = $1
                WHERE token_hash = $2 AND enabled = TRUE AND revoked_at IS NULL
                RETURNING id, user_id, team_id, scopes, note
                """,
                _utc_now(),
                token_hash,
            )
    except Exception:
        return None
    if row is None:
        return None
    scopes = row["scopes"]
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    return AuthContext(
        token_id=row["id"],
        user_id=row["user_id"],
        team_id=row["team_id"] or "default",
        scopes=scopes or [],
        auth_type="user_token",
        token_note=row["note"] or "",
    )


async def _ui_session_context(request: Request) -> AuthContext | None:
    """Fall back to the dashboard's own login cookie for requests the dashboard makes
    to itself (e.g. the Playground calling /v1/chat/completions). A logged-in dashboard
    user (admin or an allowedEmails Google user) is already authenticated — they
    shouldn't need to separately provision and paste an API token to use the Playground.
    """
    token = request.cookies.get(UI_SESSION_COOKIE, "")
    if not token:
        return None
    session = await verify_ui_session(token)
    if session is None:
        return None
    return AuthContext(
        token_id=session["sessionId"],
        user_id=session["userId"],
        team_id="dashboard",
        scopes=["inference", "telemetry", "read"],
        auth_type="ui_session",
    )


def require_scope(scope: str):
    async def _dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> AuthContext | None:
        provided = _extract_token(authorization, x_api_key)
        if not _is_auth_enforced():
            logger.warning(
                "No VANTAGE_API_KEY and no database configured -- accepting "
                "request with unverified client-supplied identity headers "
                "(x-user-id=%r, x-team-id=%r). Fine for a single-developer "
                "local run; never expose this service to a network in this "
                "state.",
                request.headers.get("x-user-id", "unknown"),
                request.headers.get("x-team-id", "unknown"),
            )
            request.state.auth = {
                "tokenId": "dev-open",
                "userId": request.headers.get("x-user-id", "unknown"),
                "teamId": request.headers.get("x-team-id", "unknown"),
                "scopes": ["inference", "telemetry", "read"],
                "authType": "dev_open",
                "tokenNote": "",
            }
            return None

        ctx = await _lookup_context(provided)
        if ctx is None:
            ctx = _legacy_context(request, provided)
        if ctx is None:
            ctx = await _ui_session_context(request)
        if ctx is None:
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
        if scope not in ctx.scopes:
            raise HTTPException(status_code=403, detail=f"Token lacks required scope: {scope}")
        request.state.auth = ctx.as_state()
        return ctx

    return _dependency
