"""PostgreSQL pool for telemetry-service."""
from __future__ import annotations

import json
import os

_pool = None
_db_available: bool = False


def _resolve_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://vantage:vantage@localhost:5432/vantage_migration",
    )


DATABASE_URL: str = _resolve_database_url()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id          BIGSERIAL    PRIMARY KEY,
    event_id    TEXT,
    type        TEXT         NOT NULL DEFAULT 'inference',
    timestamp   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id     TEXT,
    team_id     TEXT,
    route       TEXT,
    payload     JSONB        NOT NULL DEFAULT '{}'
);
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS event_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_event_id ON metrics (event_id) WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_metrics_user_ts  ON metrics (user_id,  timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_team_ts  ON metrics (team_id,  timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_type_ts  ON metrics (type,     timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_route_ts ON metrics (route,    timestamp DESC);

CREATE TABLE IF NOT EXISTS api_tokens (
    id           TEXT        PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    team_id      TEXT,
    token_hash   TEXT        NOT NULL UNIQUE,
    token_prefix TEXT        NOT NULL,
    scopes       JSONB       NOT NULL DEFAULT '["inference"]',
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens (token_hash);

CREATE TABLE IF NOT EXISTS admins (
    id            TEXT        PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT        PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    name          TEXT,
    status        TEXT        NOT NULL DEFAULT 'pending',
    requested_at  TIMESTAMPTZ NOT NULL,
    approved_at   TIMESTAMPTZ,
    approved_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

CREATE TABLE IF NOT EXISTS ui_sessions (
    id           TEXT        PRIMARY KEY,
    token_hash   TEXT        NOT NULL UNIQUE,
    role         TEXT        NOT NULL,
    user_id      TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    service      TEXT        NOT NULL DEFAULT 'telemetry'
);
CREATE INDEX IF NOT EXISTS idx_sessions_hash ON ui_sessions (token_hash);
ALTER TABLE ui_sessions ADD COLUMN IF NOT EXISTS service TEXT NOT NULL DEFAULT 'telemetry';
CREATE INDEX IF NOT EXISTS idx_sessions_service_hash ON ui_sessions (service, token_hash);
"""


async def _init_conn(conn) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool() -> None:
    global _pool, _db_available
    try:
        import asyncpg  # noqa: PLC0415
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            init=_init_conn,
        )
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        _db_available = True
        print("[DB] PostgreSQL connected — schema ready")
    except Exception as exc:
        print(f"[DB] PostgreSQL unavailable ({exc}) — falling back to JSONL")
        _db_available = False


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool():
    return _pool


def is_available() -> bool:
    return _db_available
