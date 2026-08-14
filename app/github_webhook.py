"""Map GitHub webhook payloads to Vantage PR lifecycle telemetry events."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import uuid
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_TEAM_ID = os.environ.get("GITHUB_DEFAULT_TEAM_ID", "unknown")
DEFAULT_CLIENT = "github-webhook"


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Validate X-Hub-Signature-256 from GitHub."""
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def _iso_ts(value: str | None) -> str:
    if not value:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
    return value.replace("Z", "+00:00")


def _repo_id(payload: dict) -> str:
    repo = payload.get("repository") or {}
    return str(repo.get("name") or repo.get("full_name") or "unknown")


def _pr_author(pr: dict) -> str:
    user = pr.get("user") or {}
    return str(user.get("login") or user.get("email") or "unknown")


def _base_envelope(event_type: str, occurred_at: str, repo_id: str) -> dict:
    return {
        "eventId": str(uuid.uuid4()),
        "schemaVersion": SCHEMA_VERSION,
        "eventType": event_type,
        "type": event_type,
        "occurredAt": occurred_at,
        "timestamp": occurred_at,
        "client": DEFAULT_CLIENT,
        "collectionMethod": "github_webhook",
        "repoId": repo_id,
        "teamId": DEFAULT_TEAM_ID,
        "privacyClassification": "internal-metadata",
        "redactionApplied": False,
    }


def _pr_metrics(pr: dict) -> dict:
    metrics: dict[str, Any] = {}
    if pr.get("changed_files") is not None:
        metrics["filesChanged"] = int(pr["changed_files"])
    if pr.get("additions") is not None:
        metrics["linesAdded"] = int(pr["additions"])
    if pr.get("deletions") is not None:
        metrics["linesDeleted"] = int(pr["deletions"])
    return metrics


def _pr_common(pr: dict, repo_id: str) -> dict:
    return {
        "pullRequestId": int(pr["number"]),
        "repoId": repo_id,
        "title": str(pr.get("title") or ""),
        "userId": _pr_author(pr),
        "prUrl": str(pr.get("html_url") or ""),
    }


def events_from_pull_request(payload: dict) -> list[dict]:
    """Convert a GitHub pull_request webhook payload to telemetry events."""
    action = str(payload.get("action") or "")
    pr = payload.get("pull_request") or {}
    if not pr.get("number"):
        return []

    repo_id = _repo_id(payload)
    common = _pr_common(pr, repo_id)
    metrics = _pr_metrics(pr)
    events: list[dict] = []

    if action == "opened":
        event = {
            **_base_envelope("pr_opened", _iso_ts(pr.get("created_at")), repo_id),
            **common,
            **metrics,
        }
        events.append(event)

    elif action == "reopened":
        event = {
            **_base_envelope("pr_opened", _iso_ts(pr.get("updated_at") or pr.get("created_at")), repo_id),
            **common,
            **metrics,
        }
        events.append(event)

    elif action == "synchronize":
        event = {
            **_base_envelope("pr_updated", _iso_ts(pr.get("updated_at")), repo_id),
            **common,
            **metrics,
        }
        events.append(event)

    elif action == "closed":
        if pr.get("merged"):
            merge_sha = str(pr.get("merge_commit_sha") or "")
            if not merge_sha:
                return []
            event = {
                **_base_envelope("pr_merged", _iso_ts(pr.get("merged_at") or pr.get("closed_at")), repo_id),
                **common,
                **metrics,
                "commitHash": merge_sha,
            }
            events.append(event)
        else:
            event = {
                **_base_envelope("pr_closed", _iso_ts(pr.get("closed_at")), repo_id),
                **common,
                **metrics,
            }
            events.append(event)

    return events


def events_from_pull_request_review(payload: dict) -> list[dict]:
    """Convert a GitHub pull_request_review webhook payload to telemetry events."""
    action = str(payload.get("action") or "")
    if action != "submitted":
        return []

    pr = payload.get("pull_request") or {}
    review = payload.get("review") or {}
    if not pr.get("number"):
        return []

    repo_id = _repo_id(payload)
    review_state = str(review.get("state") or "commented").lower()
    reviewer = (review.get("user") or {}).get("login") or _pr_author(pr)

    event = {
        **_base_envelope("pr_reviewed", _iso_ts(review.get("submitted_at")), repo_id),
        **_pr_common(pr, repo_id),
        "userId": str(reviewer),
        "reviewState": review_state,
    }
    return [event]


def events_from_github_webhook(github_event: str, payload: dict) -> list[dict]:
    """Dispatch GitHub webhook by X-GitHub-Event header value."""
    if github_event == "pull_request":
        return events_from_pull_request(payload)
    if github_event == "pull_request_review":
        return events_from_pull_request_review(payload)
    return []
