"""Self-service user registration + admin approval (no PostgreSQL required for
these gating checks -- the security-critical property under test is that the
admin endpoints reject unauthenticated callers, which holds regardless of DB
availability)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_register_rejects_invalid_email(client):
    res = await client.post("/api/auth/register", json={"email": "not-an-email"})
    assert res.status_code == 400


async def test_register_without_database_returns_503(client):
    # No PostgreSQL is connected in this test process (app lifespan isn't
    # run via ASGITransport) -- register_user must fail loudly, not pretend
    # to succeed or silently grant access.
    res = await client.post("/api/auth/register", json={"email": "new@example.com"})
    assert res.status_code == 503


async def test_register_status_unknown_user_is_not_found(client):
    res = await client.get("/api/auth/register/status", params={"email": "nobody@example.com"})
    assert res.status_code == 200
    assert res.json()["status"] == "not_found"


async def test_pending_users_requires_admin_session(client):
    res = await client.get("/api/auth/admin/pending-users")
    assert res.status_code == 401


async def test_all_users_requires_admin_session(client):
    res = await client.get("/api/auth/admin/users")
    assert res.status_code == 401


async def test_approve_requires_admin_session(client):
    res = await client.post("/api/auth/admin/users/someone@example.com/approve")
    assert res.status_code == 401


async def test_reject_requires_admin_session(client):
    res = await client.post("/api/auth/admin/users/someone@example.com/reject")
    assert res.status_code == 401


async def test_login_with_unregistered_email_reports_not_registered(client, monkeypatch):
    monkeypatch.setenv("VANTAGE_UI_ALLOW_EMAIL_LOGIN", "true")
    from app import ui_auth
    ui_auth.set_config({"ui": {"allowedEmails": ["only-this-one@example.com"]}})

    res = await client.post("/api/auth/user/login", json={"email": "stranger@example.com"})
    assert res.status_code == 403
    assert res.json()["detail"] == "not_registered"
