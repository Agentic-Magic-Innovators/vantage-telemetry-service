"""JWT bridge tokens for embedded telemetry UI (harness-ui -> telemetry-service)."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import base64

from .secrets import require_secret

_SECRET = require_secret("TELEMETRY_BRIDGE_JWT_SECRET", "dev-bridge-secret-change-me").encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_bridge_token(*, user_id: str, team_id: str, role: str, ttl_seconds: int = 300) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    exp = now + datetime.timedelta(seconds=ttl_seconds)
    payload = {
        "sub": user_id,
        "teamId": team_id,
        "role": role,
        "scope": "read",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_SECRET, body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}"


def verify_bridge_token(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        expected = hmac.new(_SECRET, body, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig_b64), expected):
            return None
        payload = json.loads(body.decode())
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(datetime.datetime.now(datetime.timezone.utc).timestamp()):
        return None
    if payload.get("scope") != "read":
        return None
    return {
        "userId": payload.get("sub", ""),
        "teamId": payload.get("teamId", ""),
        "role": payload.get("role", "user"),
    }
