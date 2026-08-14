"""Basic telemetry-service tests (no PostgreSQL required for validation logic)."""
import pytest
from app.bridge_auth import issue_bridge_token, verify_bridge_token


def test_bridge_token_roundtrip():
    token = issue_bridge_token(user_id="u@test.com", team_id="team-a", role="admin")
    ctx = verify_bridge_token(token)
    assert ctx is not None
    assert ctx["userId"] == "u@test.com"
    assert ctx["role"] == "admin"


def test_telemetry_event_validation():
    from app.main import _validate_telemetry_event

    _validate_telemetry_event({
        "schemaVersion": "1.0",
        "eventId": "abc",
        "eventType": "usage",
        "type": "usage",
        "occurredAt": "2026-08-13T12:00:00Z",
    })

    with pytest.raises(ValueError):
        _validate_telemetry_event({"schemaVersion": "2.0", "eventId": "x", "eventType": "usage", "type": "usage", "occurredAt": "t"})
