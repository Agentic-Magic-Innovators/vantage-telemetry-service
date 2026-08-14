"""Vantage Telemetry Service — ingestion and metrics query API."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import db as _db
from .auth import create_admin, admin_exists, require_scope, sync_admin_password
from .bridge_auth import verify_bridge_token
from .observability import add_metric, metrics_summary
from .secrets import require_secret
from .ui_auth import router as ui_auth_router, set_config as _set_ui_auth_config

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "telemetry.config.json"
STATIC_DIR = ROOT / "static"
PORT = int(os.environ.get("VANTAGE_TELEMETRY_PORT", "50224"))
INTERNAL_KEY = require_secret("TELEMETRY_INTERNAL_KEY", "dev-internal-key-change-me")
TELEMETRY_SESSION_COOKIE = "vantage_telemetry_session"


def load_config() -> dict:
    cfg: dict = {"ui": {}, "gateway": {"host": "127.0.0.1", "port": PORT}}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ui = cfg.setdefault("ui", {})
    if os.environ.get("VANTAGE_UI_GOOGLE_CLIENT_ID"):
        ui["googleClientId"] = os.environ["VANTAGE_UI_GOOGLE_CLIENT_ID"]
    allowed = os.environ.get("VANTAGE_UI_ALLOWED_EMAILS", "").strip()
    if allowed:
        ui["allowedEmails"] = [e.strip() for e in allowed.split(",") if e.strip()]
    ttl = os.environ.get("VANTAGE_UI_SESSION_TTL_HOURS")
    if ttl:
        try:
            ui["sessionTtlHours"] = int(ttl)
        except ValueError:
            pass
    return cfg


cfg = load_config()


class TelemetryEvent(BaseModel):
    eventId: str | None = Field(default=None)
    schemaVersion: str | None = None
    eventType: str | None = None
    type: str | None = None
    occurredAt: str | None = None
    client: str | None = None
    userId: str | None = None
    teamId: str | None = None
    model_config = ConfigDict(extra="allow")


def _validate_telemetry_event(event: dict) -> None:
    schema_version = event.get("schemaVersion")
    event_id = event.get("eventId")
    if schema_version is not None:
        if not isinstance(schema_version, str) or schema_version.split(".", 1)[0] != "1":
            raise ValueError(f"Unsupported telemetry schemaVersion: {schema_version!r}")
        required = ("eventId", "eventType", "occurredAt", "type")
        missing = [f for f in required if not event.get(f)]
        if missing:
            raise ValueError("Versioned telemetry payload missing: " + ", ".join(missing))
        if event.get("eventType") != event.get("type"):
            raise ValueError("eventType must match legacy type")
    pr_event_types = {"pr_opened", "pr_reviewed", "pr_updated", "pr_merged", "pr_closed"}
    if event.get("type") in pr_event_types:
        required_pr = ("pullRequestId", "repoId", "occurredAt")
        missing_pr = [f for f in required_pr if not event.get(f)]
        if missing_pr:
            raise ValueError("PR telemetry payload missing: " + ", ".join(missing_pr))
        if event.get("type") == "pr_reviewed" and not event.get("reviewState"):
            raise ValueError("pr_reviewed telemetry requires reviewState")
        if event.get("type") == "pr_merged" and not event.get("commitHash"):
            raise ValueError("pr_merged telemetry requires commitHash")


async def _ingest_event(event: dict, request: Request | None = None) -> dict:
    if request is not None:
        auth_ctx = getattr(request.state, "auth", {}) or {}
        event.setdefault("userId", auth_ctx.get("userId") or request.headers.get("x-user-id", "unknown"))
        event.setdefault("teamId", auth_ctx.get("teamId") or request.headers.get("x-team-id", "unknown"))
        event.setdefault("tokenId", auth_ctx.get("tokenId", ""))
        event.setdefault("authType", auth_ctx.get("authType", ""))
        event.setdefault("tokenNote", auth_ctx.get("tokenNote", ""))
    disposition = await add_metric(event)
    return {
        "status": "accepted" if disposition == "accepted" else "duplicate",
        "eventId": event.get("eventId"),
    }


def _check_internal_key(x_internal_key: str | None = Header(default=None, alias="X-Internal-Key")) -> None:
    if not x_internal_key or not _safe_eq(x_internal_key, INTERNAL_KEY):
        raise HTTPException(status_code=403, detail="Invalid internal key")


def _safe_eq(a: str, b: str) -> bool:
    import hmac as _hmac
    return _hmac.compare_digest(a.encode(), b.encode())


async def _metrics_auth_context(request: Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        bridge = verify_bridge_token(auth[7:].strip())
        if bridge:
            return {"role": bridge["role"], "userId": bridge["userId"], "teamId": bridge.get("teamId")}
    token = request.cookies.get(TELEMETRY_SESSION_COOKIE) or request.cookies.get("vantage_session", "")
    if token:
        from .auth import verify_ui_session
        ctx = await verify_ui_session(token, service="telemetry")
        if ctx:
            return ctx
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _db.init_pool()

    _admin_user = os.environ.get("VANTAGE_ADMIN_USERNAME", "admin")
    _admin_pass = os.environ.get("VANTAGE_ADMIN_PASSWORD", "")
    if _admin_pass:
        try:
            if not await admin_exists():
                await create_admin(_admin_user, _admin_pass)
                print(f"[Auth] Bootstrap admin created: {_admin_user}")
            elif await sync_admin_password(_admin_user, _admin_pass):
                print(f"[Auth] Admin password synced from VANTAGE_ADMIN_PASSWORD: {_admin_user}")
        except Exception as exc:
            print(f"[Auth] Bootstrap admin setup failed: {exc}")

    yield
    await _db.close_pool()


app = FastAPI(
    title="Vantage Telemetry Service",
    version="1.0.0",
    lifespan=lifespan,
)

_origins = os.environ.get("CORS_ORIGINS", "http://localhost:50225,http://localhost:50226,http://127.0.0.1:50225").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_set_ui_auth_config(cfg)
app.include_router(ui_auth_router)


@app.get("/health")
async def health():
    return {"status": "ok", "db": _db.is_available()}


@app.get("/")
async def root():
    return {
        "service": "vantage-telemetry-service",
        "telemetry": "/v1/telemetry",
        "webhooks": "/v1/webhooks/github",
        "metrics": "/metrics",
        "dashboard": "/dashboard",
    }


@app.post("/v1/telemetry", tags=["Telemetry"])
async def telemetry(payload: TelemetryEvent, request: Request, _auth=Depends(require_scope("telemetry"))):
    try:
        event = payload.model_dump(exclude_none=True)
        if not isinstance(event, dict):
            raise ValueError("Telemetry payload must be a JSON object")
        _validate_telemetry_event(event)
        return await _ingest_event(event, request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid telemetry payload: {e}") from e


@app.post("/v1/internal/events", tags=["Internal"])
async def internal_events(request: Request, _=Depends(_check_internal_key)):
    try:
        event = await request.json()
        if not isinstance(event, dict):
            raise ValueError("Event must be a JSON object")
        return await _ingest_event(event)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/v1/webhooks/github", tags=["Webhooks"])
async def github_webhook(request: Request):
    """Ingest GitHub pull_request and pull_request_review webhooks as PR lifecycle telemetry."""
    from .github_webhook import events_from_github_webhook, verify_github_signature

    body = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
    allow_unsigned = os.environ.get("VANTAGE_ALLOW_UNSIGNED_WEBHOOKS", "").lower() in ("1", "true", "yes")
    signature = request.headers.get("X-Hub-Signature-256")

    if secret:
        if not verify_github_signature(body, signature, secret):
            raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")
    elif not allow_unsigned:
        raise HTTPException(status_code=503, detail="GITHUB_WEBHOOK_SECRET is not configured")

    github_event = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    events = events_from_github_webhook(github_event, payload)
    if not events:
        return {"status": "ignored", "githubEvent": github_event, "ingested": 0}

    results = []
    for event in events:
        try:
            _validate_telemetry_event(event)
            results.append(await _ingest_event(event))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid telemetry from webhook: {exc}") from exc

    return {
        "status": "ok",
        "githubEvent": github_event,
        "ingested": len(results),
        "results": results,
    }


@app.get("/metrics")
async def metrics(
    request: Request,
    userId: str | None = None,
    teamId: str | None = None,
    days: int = 30,
    client: str | None = None,
):
    ctx = await _metrics_auth_context(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if ctx.get("role") == "user":
        userId = ctx["userId"]
        teamId = None
    days = max(1, min(days, 365))
    summary = await metrics_summary(user_id=userId, team_id=teamId, days=days, client_id=client)
    is_user_scope = ctx.get("role") == "user"
    if is_user_scope:
        summary["byTeam"] = []
        summary["byUser"] = []
        summary["byClient"] = []
    summary["_scope"] = "user" if is_user_scope else "admin"
    summary["_scopedUserId"] = userId if is_user_scope else None
    summary["_apiVersion"] = "1.0"
    return summary


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/dashboard")
    def dashboard():
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, "Telemetry UI not built — see vantage-telemetry-ui")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("GATEWAY_HOST", cfg.get("gateway", {}).get("host", "127.0.0.1"))
    uvicorn.run(app, host=host, port=PORT)
