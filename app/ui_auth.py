"""UI authentication endpoints — Admin (username/password) and User (Google OAuth)."""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

TELEMETRY_SESSION_COOKIE = "vantage_telemetry_session"

from .auth import (
    TELEMETRY_SESSION_COOKIE as _TELEMETRY_COOKIE,
    admin_exists,
    approve_user,
    create_admin,
    create_token,
    create_ui_session,
    delete_ui_session,
    get_user,
    is_email_authorized,
    list_user_tokens,
    list_users,
    register_user,
    reject_user,
    rotate_token,
    verify_admin_password,
    verify_ui_session,
)
from .bridge_auth import verify_bridge_token

router = APIRouter(prefix="/api/auth", tags=["ui-auth"])

_SESSION_COOKIE = _TELEMETRY_COOKIE
_SESSION_TTL_HOURS = 8

_failed_attempts: dict[str, list[float]] = {}
_cfg: dict = {}


def set_config(cfg: dict) -> None:
    _cfg.clear()
    _cfg.update(cfg)


def _ui_cfg() -> dict:
    return _cfg.get("ui", {})


def _email_login_enabled() -> bool:
    flag = os.environ.get("VANTAGE_UI_ALLOW_EMAIL_LOGIN", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return bool(_ui_cfg().get("allowEmailLogin", False))


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=_SESSION_TTL_HOURS * 3600,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE, path="/", samesite="strict")


def _get_session_token(request: Request) -> str:
    return request.cookies.get(_SESSION_COOKIE, "")


async def _auth_context(request: Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        bridge = verify_bridge_token(auth[7:].strip())
        if bridge:
            return {
                "role": bridge["role"],
                "userId": bridge["userId"],
                "teamId": bridge.get("teamId") or "",
            }
    token = _get_session_token(request)
    if not token:
        return None
    return await verify_ui_session(token)


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    window = 300
    recent = [t for t in _failed_attempts.get(ip, []) if now - t < window]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many failed login attempts — try again in 5 minutes")
    _failed_attempts[ip] = recent


def _record_failure(ip: str) -> None:
    attempts = _failed_attempts.get(ip, [])
    attempts.append(time.time())
    _failed_attempts[ip] = attempts


class AdminLoginBody(BaseModel):
    username: str
    password: str


class GoogleLoginBody(BaseModel):
    credential: str


class UserEmailLoginBody(BaseModel):
    email: str


class RegisterBody(BaseModel):
    email: str
    name: str = ""


@router.get("/config")
async def auth_config():
    google_client_id = _ui_cfg().get("googleClientId", "")
    return {
        "googleEnabled": bool(google_client_id),
        "googleClientId": google_client_id,
        "emailLoginEnabled": _email_login_enabled(),
        "adminExists": await admin_exists(),
    }


@router.get("/me")
async def me(request: Request):
    ctx = await _auth_context(request)
    if ctx is None:
        raise HTTPException(401, "Not authenticated")
    return ctx


@router.post("/admin/login")
async def admin_login(body: AdminLoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)
    ok = await verify_admin_password(body.username, body.password)
    if not ok:
        _record_failure(ip)
        raise HTTPException(401, "Invalid username or password")
    token = await create_ui_session("admin", body.username.strip().lower(), _SESSION_TTL_HOURS, service="telemetry")
    _set_session_cookie(response, token)
    return {"role": "admin", "userId": body.username.strip().lower()}


@router.post("/google")
async def google_login(body: GoogleLoginBody, response: Response):
    google_client_id = _ui_cfg().get("googleClientId", "")
    if not google_client_id:
        raise HTTPException(503, "Google login is not configured on this server")
    try:
        from google.oauth2 import id_token  # type: ignore
        from google.auth.transport import requests as google_requests  # type: ignore
        import asyncio

        idinfo = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            body.credential,
            google_requests.Request(),
            google_client_id,
        )
    except Exception as exc:
        raise HTTPException(401, f"Google token verification failed: {exc}") from exc

    email: str = idinfo.get("email", "")
    if not email:
        raise HTTPException(401, "Google token has no email claim")

    allowed: list[str] = _ui_cfg().get("allowedEmails", [])
    if not await is_email_authorized(email, allowed):
        raise HTTPException(403, "not_registered")

    token = await create_ui_session("user", email, _SESSION_TTL_HOURS, service="telemetry")
    _set_session_cookie(response, token)
    return {"role": "user", "userId": email}


@router.post("/user/login")
async def user_email_login(body: UserEmailLoginBody, response: Response):
    if _ui_cfg().get("googleClientId", "") and not _email_login_enabled():
        raise HTTPException(400, "Google Sign-In is configured — use that instead")
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email address is required")
    allowed: list[str] = _ui_cfg().get("allowedEmails", [])
    if not await is_email_authorized(email, allowed):
        raise HTTPException(403, "not_registered")
    token = await create_ui_session("user", email, _SESSION_TTL_HOURS, service="telemetry")
    _set_session_cookie(response, token)
    return {"role": "user", "userId": email}


@router.post("/register")
async def register(body: RegisterBody):
    """Public, unauthenticated: request a dashboard account. Never grants
    access by itself -- creates a 'pending' row an admin must approve
    (see /admin/pending-users) before the email can actually log in."""
    try:
        result = await register_user(body.email, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.get("/register/status")
async def register_status(email: str):
    """Public: lets the login page tell someone whether their request is
    still pending, approved, or was never submitted -- without requiring
    them to already be logged in."""
    user = await get_user(email)
    return {"status": user["status"] if user else "not_found"}


async def _require_session(request: Request) -> dict:
    ctx = await _auth_context(request)
    if ctx is None:
        raise HTTPException(401, "Not authenticated")
    return ctx


async def _require_admin(request: Request) -> dict:
    ctx = await _require_session(request)
    if ctx.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return ctx


@router.get("/admin/pending-users")
async def admin_pending_users(request: Request):
    await _require_admin(request)
    return {"users": await list_users(status="pending")}


@router.get("/admin/users")
async def admin_all_users(request: Request):
    await _require_admin(request)
    return {"users": await list_users()}


@router.post("/admin/users/{email}/approve")
async def admin_approve_user(email: str, request: Request):
    ctx = await _require_admin(request)
    ok = await approve_user(email, ctx["userId"])
    if not ok:
        raise HTTPException(404, "User not found")
    return {"ok": True, "email": email.strip().lower(), "status": "approved"}


@router.post("/admin/users/{email}/reject")
async def admin_reject_user(email: str, request: Request):
    await _require_admin(request)
    ok = await reject_user(email)
    if not ok:
        raise HTTPException(404, "User not found")
    return {"ok": True, "email": email.strip().lower(), "status": "rejected"}


@router.get("/my-tokens")
async def my_tokens(request: Request):
    ctx = await _require_session(request)
    tokens = await list_user_tokens(ctx["userId"])
    return {"tokens": tokens, "userId": ctx["userId"]}


@router.post("/my-token")
async def create_my_token(request: Request):
    ctx = await _require_session(request)
    result = await create_token(
        ctx["userId"],
        "",
        ["inference", "telemetry"],
        "IDE Client Token",
    )
    return result


@router.post("/my-token/{token_id}/rotate")
async def rotate_my_token(token_id: str, request: Request):
    ctx = await _require_session(request)
    user_tokens = await list_user_tokens(ctx["userId"])
    if not any(t["id"] == token_id for t in user_tokens):
        raise HTTPException(403, "Token not found or does not belong to your account")
    result = await rotate_token(token_id)
    if result is None:
        raise HTTPException(404, "Token not found or already revoked")
    return result


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = _get_session_token(request)
    if token:
        await delete_ui_session(token)
    _clear_session_cookie(response)
    return {"ok": True}
