"""Tests for Top Spenders and per-team rollup accuracy."""
from app.observability import _compute_summary


def test_top_spenders_include_mcp_cost_and_usage_events():
    items = [
        {
            "type": "usage",
            "userId": "alice@example.com",
            "teamId": "LTE",
            "totalTokens": 1200,
        },
        {
            "type": "cost",
            "userId": "alice@example.com",
            "teamId": "LTE",
            "actualCostUsd": 2.5,
            "savingsUsd": 1.5,
        },
        {
            "type": "cursor_usage",
            "userId": "bob@example.com",
            "teamId": "LTE",
            "estimatedTokens": 800,
            "hashCount": 32,
        },
        {
            "route": "cloud",
            "userId": "bob@example.com",
            "teamId": "platform",
            "estimatedAllCloudCostUsd": 4.0,
            "actualCostUsd": 3.0,
        },
    ]

    summary = _compute_summary(items)

    assert summary["totalRequests"] == 4
    assert summary["actualCostUsd"] == 5.5  # 2.5 + 3.0
    assert summary["estimatedSavingsUsd"] == 2.5  # 1.5 + (4.0 - 3.0)

    by_user = {row["userId"]: row for row in summary["byUser"]}
    assert by_user["alice@example.com"]["requests"] == 2
    assert by_user["alice@example.com"]["actualCostUsd"] == 2.5
    assert by_user["bob@example.com"]["requests"] == 2
    assert by_user["bob@example.com"]["actualCostUsd"] == 3.0

    by_team = {row["teamId"]: row for row in summary["byTeam"]}
    assert by_team["LTE"]["requests"] == 3
    assert by_team["LTE"]["actualCostUsd"] == 2.5
    assert by_team["platform"]["requests"] == 1
    assert by_team["platform"]["cloudRequests"] == 1


def test_productivity_sessions_exclude_task_events_and_merge_outcome():
    items = [
        {
            "type": "productivity",
            "userId": "alice@example.com",
            "teamId": "LTE",
            "activeCodingTimeSec": 120.0,
            "linesAdded": 40,
            "linesDeleted": 5,
            "filesModifiedCount": 2,
            "filesModifiedList": ["a.py", "b.py"],
        },
        {
            "type": "session_outcome",
            "userId": "alice@example.com",
            "teamId": "LTE",
            "activeCodingTimeSec": 120.0,
            "linesAdded": 40,
            "linesDeleted": 5,
            "outcome": "committed",
        },
        {
            "type": "productivity_task",
            "userId": "alice@example.com",
            "teamId": "LTE",
            "taskId": "TASK-1",
            "linesAdded": 999,
            "linesDeleted": 0,
        },
    ]

    summary = _compute_summary(items)

    assert summary["clientTelemetry"]["productivitySessionCount"] == 1
    assert summary["clientTelemetry"]["productivityTaskCount"] == 1
    assert summary["insights"]["flow"]["sessions"] == 1
    assert summary["insights"]["flow"]["committedSessions"] == 1
    assert summary["insights"]["time"]["activeCodingSec"] == 120.0
    assert summary["productivity"][0]["outcome"] == "committed"
    assert all(row.get("type") != "productivity_task" for row in summary["productivity"])


def test_productivity_sessions_support_legacy_active_seconds_field():
    items = [
        {
            "type": "productivity",
            "userId": "bob@example.com",
            "teamId": "LTE",
            "activeSeconds": 90,
            "linesAdded": 10,
            "linesDeleted": 0,
        }
    ]

    summary = _compute_summary(items)
    assert summary["insights"]["time"]["activeCodingSec"] == 90.0

    items = [
        {"type": "cost", "userId": "low", "teamId": "a", "actualCostUsd": 0.5},
        {"type": "cost", "userId": "high", "teamId": "b", "actualCostUsd": 9.0},
        {"type": "usage", "userId": "mid", "teamId": "c", "totalTokens": 100},
    ]

    summary = _compute_summary(items)
    assert summary["byUser"][0]["userId"] == "high"
    assert summary["byTeam"][0]["teamId"] == "b"
