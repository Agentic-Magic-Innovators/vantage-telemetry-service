"""Tests for GitHub webhook → PR lifecycle telemetry mapping."""
import hashlib
import hmac
import json

import pytest

from app.github_webhook import (
    events_from_github_webhook,
    events_from_pull_request,
    events_from_pull_request_review,
    verify_github_signature,
)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_signature_accepts_valid_hmac():
    body = b'{"action":"opened"}'
    secret = "test-secret"
    assert verify_github_signature(body, _sign(body, secret), secret) is True


def test_verify_github_signature_rejects_invalid_hmac():
    body = b'{"action":"opened"}'
    assert verify_github_signature(body, "sha256=deadbeef", "test-secret") is False


def test_pull_request_opened_includes_metric_fields():
    payload = {
        "action": "opened",
        "repository": {"name": "vantage-telemetry-mcp"},
        "pull_request": {
            "number": 1,
            "title": "feat: test",
            "html_url": "https://github.com/org/repo/pull/1",
            "created_at": "2026-08-12T10:00:00Z",
            "user": {"login": "alice"},
            "changed_files": 8,
            "additions": 220,
            "deletions": 14,
        },
    }

    events = events_from_pull_request(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "pr_opened"
    assert event["pullRequestId"] == 1
    assert event["repoId"] == "vantage-telemetry-mcp"
    assert event["filesChanged"] == 8
    assert event["linesAdded"] == 220
    assert event["occurredAt"] == "2026-08-12T10:00:00+00:00"
    assert event["client"] == "github-webhook"


def test_pull_request_merged_requires_merge_commit_sha():
    payload = {
        "action": "closed",
        "repository": {"name": "vantage-telemetry-mcp"},
        "pull_request": {
            "number": 1,
            "title": "feat: test",
            "merged": True,
            "merge_commit_sha": "82932b1abc",
            "merged_at": "2026-08-12T16:00:00Z",
            "user": {"login": "alice"},
            "changed_files": 8,
        },
    }

    events = events_from_pull_request(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "pr_merged"
    assert event["commitHash"] == "82932b1abc"
    assert event["occurredAt"] == "2026-08-12T16:00:00+00:00"


def test_pull_request_review_submitted_maps_review_state():
    payload = {
        "action": "submitted",
        "repository": {"name": "vantage-telemetry-mcp"},
        "pull_request": {
            "number": 1,
            "title": "feat: test",
            "html_url": "https://github.com/org/repo/pull/1",
            "user": {"login": "alice"},
        },
        "review": {
            "state": "approved",
            "submitted_at": "2026-08-12T12:00:00Z",
            "user": {"login": "reviewer1"},
        },
    }

    events = events_from_pull_request_review(payload)
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "pr_reviewed"
    assert event["reviewState"] == "approved"
    assert event["userId"] == "reviewer1"


def test_events_from_github_webhook_dispatch():
    payload = {
        "action": "synchronize",
        "repository": {"name": "payments"},
        "pull_request": {
            "number": 42,
            "title": "Improve retries",
            "updated_at": "2026-08-12T14:00:00Z",
            "user": {"login": "alice"},
            "changed_files": 3,
        },
    }
    events = events_from_github_webhook("pull_request", payload)
    assert len(events) == 1
    assert events[0]["type"] == "pr_updated"


def test_built_pr_events_pass_validation():
    from app.main import _validate_telemetry_event

    opened = events_from_pull_request({
        "action": "opened",
        "repository": {"name": "payments"},
        "pull_request": {
            "number": 42,
            "title": "Improve retries",
            "created_at": "2026-08-12T10:00:00Z",
            "user": {"login": "alice"},
            "changed_files": 3,
        },
    })[0]
    _validate_telemetry_event(opened)

    merged = events_from_pull_request({
        "action": "closed",
        "repository": {"name": "payments"},
        "pull_request": {
            "number": 42,
            "title": "Improve retries",
            "merged": True,
            "merge_commit_sha": "abc123",
            "merged_at": "2026-08-12T16:00:00Z",
            "user": {"login": "alice"},
        },
    })[0]
    _validate_telemetry_event(merged)


@pytest.mark.asyncio
async def test_github_webhook_endpoint_ingests_signed_payload(client):
    from unittest.mock import AsyncMock, patch

    payload = {
        "action": "opened",
        "repository": {"name": "vantage-telemetry-mcp"},
        "pull_request": {
            "number": 99,
            "title": "Webhook test",
            "created_at": "2026-08-12T10:00:00Z",
            "user": {"login": "bot"},
            "changed_files": 2,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    with patch("app.main.add_metric", new_callable=AsyncMock, return_value="accepted"):
        response = await client.post(
            "/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body, "webhook-test-secret"),
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ingested"] == 1
    assert data["githubEvent"] == "pull_request"
