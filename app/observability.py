import json, asyncio, datetime, html, pathlib
from collections import defaultdict
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from . import db, productivity

_SESSION_COOKIE = "vantage_session"

ROOT = pathlib.Path(__file__).parent.parent
METRICS_PATH = ROOT / "data" / "metrics.jsonl"
METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)

router = APIRouter()

# Fields promoted to dedicated columns; everything else goes into payload JSONB.
_SCALAR_COLS = frozenset({"type", "timestamp", "userId", "teamId", "route"})


def _client_from_token_note(note: str) -> str | None:
    n = (note or "").lower()
    if "cursor" in n:
        return "cursor"
    if "continue" in n:
        return "continue"
    if "cline" in n:
        return "cline"
    if "codex" in n:
        return "codex"
    if "claude" in n:
        return "claude-code"
    return None


def _resolve_client_id(record: dict) -> str:
    """Stable client id for byClient rollups (handles legacy events missing `client`)."""
    c = (record.get("client") or "").strip().lower()
    if c and c not in ("unknown", "missing"):
        if c.startswith("cursor") or "cursor" in c:
            return "cursor"
        if c == "api":
            # Generic HTTP clients (Cursor BYOK, scripts) — prefer auth token note when present.
            from_api_note = _client_from_token_note(record.get("tokenNote") or record.get("authNote") or "")
            if from_api_note:
                return from_api_note
        return c

    from_note = _client_from_token_note(record.get("tokenNote") or record.get("authNote") or "")
    if from_note:
        return from_note

    event_type = record.get("type", "")
    if event_type in ("cursor_usage", "cursor_commit"):
        return "cursor"
    if event_type == "agent_turn":
        return "codex"
    if event_type == "inference" and (record.get("route") or record.get("model")):
        # Gateway inference without a stamped client — last resort from generic source labels.
        source = (record.get("source") or "").lower()
        if "cursor" in source:
            return "cursor"
        if "codex" in source:
            return "codex"
        if "continue" in source:
            return "continue"
    # productivity/audit/session_outcome/productivity_task/usage/cost are now sent by
    # every MCP client (Cursor, Codex, Claude Code, Continue, Cline) via the log_* tools,
    # each of which always stamps `client` from the MCP handshake — so a genuinely missing
    # client here means the source is unknown, not "must be Cursor" (that was a stale
    # assumption from when Cursor was the only client).
    return "unknown"


def _with_resolved_client(record: dict) -> dict:
    out = dict(record)
    out["client"] = _resolve_client_id(out)
    return out


# Cursor DB watcher attributes code hashes, not LLM billing tokens. When only hash
# counts are available we apply a conservative completion-side estimate so Tool
# Usage and RAW Events are not stuck at zero for Cursor activity.
_CURSOR_TOKENS_PER_HASH = 25


def _event_tokens(record: dict) -> int:
    """Best-effort token count for dashboard rollups."""
    for key in ("totalTokens", "estimatedTokens"):
        try:
            value = int(record.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    if record.get("type") == "cursor_usage":
        try:
            hash_count = int(record.get("hashCount") or 0)
        except (TypeError, ValueError):
            hash_count = 0
        if hash_count > 0:
            return hash_count * _CURSOR_TOKENS_PER_HASH
    return 0


def _event_duration_ms(record: dict) -> float:
    """Best-effort AI wait / response duration in milliseconds."""
    for key in ("durationMs", "latencyMs"):
        try:
            value = float(record.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    if record.get("type") == "cursor_usage":
        try:
            t_min = float(record.get("cursorTimestampMin"))
            t_max = float(record.get("cursorTimestampMax"))
            delta = t_max - t_min
            if delta > 0:
                return delta
        except (TypeError, ValueError):
            pass
    return 0.0


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def _write_jsonl_sync(line: str) -> None:
    with open(METRICS_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def _jsonl_contains_event_id(event_id: str) -> bool:
    if not event_id or not METRICS_PATH.exists():
        return False
    try:
        with open(METRICS_PATH, "r", encoding="utf-8-sig") as f:
            for line in f:
                try:
                    if json.loads(line).get("eventId") == event_id:
                        return True
                except (json.JSONDecodeError, TypeError):
                    continue
    except OSError:
        return False
    return False


async def add_metric(record: dict) -> str:
    """Persist one metric and return ``accepted`` or ``duplicate``.

    Legacy inference/client events without an eventId remain append-only. New
    telemetry envelope events are idempotent across MCP delivery retries.
    """
    record = _with_resolved_client(record)
    event_id = str(record.get("eventId") or "").strip() or None
    record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rec_type = record.get("type", "inference")

    client_telemetry_types = (
        "productivity", "audit", "productivity_task", "usage", "cost",
        "cursor_usage", "cursor_commit", "agent_turn", "session_outcome",
        "search_performed",
    )
    if rec_type in client_telemetry_types and not record.get("route"):
        print(
            "[Client Telemetry] "
            f"type={rec_type} "
            f"userId={record.get('userId', 'unknown')} "
            f"teamId={record.get('teamId', 'unknown')} "
            f"client={record.get('client', '-')} "
            f"source={record.get('source', '')} "
            f"action={record.get('action', '')} "
            f"hashCount={record.get('hashCount', '')} "
            f"payload={json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)}"
        )
    else:
        print(
            "[Harness Route] "
            f"route={record.get('route')} "
            f"model={record.get('model')} "
            f"reason={record.get('reason')} "
            f"userId={record.get('userId', 'unknown')} "
            f"teamId={record.get('teamId', 'unknown')} "
            f"fallback={record.get('fallbackUsed', False)} "
            f"repoHits={record.get('repoContextHits', 0)} "
            f"webSearchHits={record.get('webSearchHits', 0)} "
            f"searchGrounding={record.get('googleSearchGrounding', False)} "
            f"grounded={record.get('grounded', False)} "
            f"source={record.get('source')} "
            f"latencyMs={record.get('latencyMs')} "
            f"responseLen={record.get('responseLen', '?')} "
            f"doneReason={record.get('doneReason') or '-'}"
        )

    pool = db.get_pool()
    if pool:
        try:
            payload = {k: v for k, v in record.items() if k not in _SCALAR_COLS}
            ts_str = record.get("timestamp", "")
            ts = (
                datetime.datetime.fromisoformat(ts_str)
                if ts_str
                else datetime.datetime.now(datetime.timezone.utc)
            )
            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    INSERT INTO metrics (event_id, type, timestamp, user_id, team_id, route, payload)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (event_id) WHERE event_id IS NOT NULL DO NOTHING
                    """,
                    event_id,
                    record.get("type", "inference"),
                    ts,
                    record.get("userId") or None,
                    record.get("teamId") or None,
                    record.get("route") or None,
                    payload,
                )
            return "duplicate" if result.endswith(" 0") else "accepted"
        except Exception as exc:
            print(f"[DB] metric insert failed ({exc}) — writing to JSONL fallback")

    if event_id and await asyncio.to_thread(_jsonl_contains_event_id, event_id):
        return "duplicate"
    await asyncio.to_thread(_write_jsonl_sync, json.dumps(record) + "\n")
    return "accepted"


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def _row_to_record(row) -> dict:
    """Reconstruct a flat metric dict from a PostgreSQL row."""
    record = dict(row["payload"] or {})
    record["type"] = row["type"]
    ts = row["timestamp"]
    record["timestamp"] = ts.isoformat() if ts else ""
    record["userId"] = row["user_id"] or ""
    record["teamId"] = row["team_id"] or ""
    record["route"] = row["route"] or ""
    if "event_id" in row and row["event_id"]:
        record["eventId"] = row["event_id"]
    return _with_resolved_client(record)


async def get_metrics(user_id: str | None = None, team_id: str | None = None) -> list:
    pool = db.get_pool()
    if pool:
        try:
            conditions: list[str] = []
            params: list = []
            if user_id:
                params.append(user_id)
                conditions.append(f"user_id = ${len(params)}")
            if team_id:
                params.append(team_id)
                conditions.append(f"team_id = ${len(params)}")
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            query = f"SELECT event_id, type, timestamp, user_id, team_id, route, payload FROM metrics {where} ORDER BY timestamp ASC"
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            return [_row_to_record(r) for r in rows]
        except Exception as exc:
            print(f"[DB] metrics query failed ({exc}) — reading from JSONL fallback")

    return _read_jsonl(user_id=user_id, team_id=team_id)


def _read_jsonl(user_id: str | None = None, team_id: str | None = None) -> list:
    if not METRICS_PATH.exists():
        return []
    items = []
    for line in METRICS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    if user_id:
        items = [x for x in items if x.get("userId") == user_id]
    if team_id:
        items = [x for x in items if x.get("teamId") == team_id]
    return items


# ---------------------------------------------------------------------------
# Summary computation (pure; no I/O)
# ---------------------------------------------------------------------------

def _compute_summary(items: list) -> dict:
    client_types = (
        "productivity", "audit", "productivity_task", "usage", "cost",
        "cursor_usage", "cursor_commit", "agent_turn", "session_outcome",
        "coding_session_started", "coding_session_ended", "file_created",
        "file_modified", "file_deleted", "git_commit", "git_push_detected",
        "git_branch_changed", "collector_heartbeat", "search_performed", "pr_opened", "pr_reviewed",
        "pr_updated", "pr_merged", "pr_closed",
    )
    inference_items = [
        x for x in items
        if "route" in x and x.get("type", "inference") not in client_types
    ]
    prod_items = [x for x in items if x.get("type") in ("productivity", "productivity_task")]
    audit_items = [x for x in items if x.get("type") == "audit"]
    usage_items = [x for x in items if x.get("type") == "usage"]
    cost_items = [x for x in items if x.get("type") == "cost"]
    cursor_usage_items = [x for x in items if x.get("type") == "cursor_usage"]
    cursor_commit_items = [x for x in items if x.get("type") == "cursor_commit"]
    agent_turn_items = [x for x in items if x.get("type") == "agent_turn"]
    session_outcome_items = [x for x in items if x.get("type") == "session_outcome"]
    pr_items = [x for x in items if str(x.get("type", "")).startswith("pr_")]
    # Non-MCP telemetry sources that self-identify their client directly
    # (Cursor's local DB watcher, Codex's notify hook) rather than through
    # the log_* MCP tools' handshake-derived client name.
    client_sourced_items = [x for x in items if x.get("type") in client_types]

    total = len(inference_items)
    local = sum(1 for x in inference_items if x.get("route") == "local")
    cloud = sum(1 for x in inference_items if x.get("route") == "cloud")
    blocked = sum(1 for x in inference_items if x.get("policyBlockedCloud"))
    cache_hits = sum(1 for x in inference_items if x.get("reason") == "cache_hit")
    all_cloud_cost = sum(x.get("estimatedAllCloudCostUsd", 0) for x in inference_items)
    actual_cost = sum(x.get("actualCostUsd", 0) for x in inference_items)
    savings = max(0.0, all_cloud_cost - actual_cost)
    savings_rate = round((savings / all_cloud_cost) * 100, 2) if all_cloud_cost > 0 else 0
    cache_rate = round(100 * cache_hits / total, 1) if total > 0 else 0.0

    _team: dict = defaultdict(
        lambda: {"requests": 0, "localRequests": 0, "cloudRequests": 0, "savingsUsd": 0.0, "actualCostUsd": 0.0}
    )
    _user: dict = defaultdict(
        lambda: {"teamId": "unknown", "requests": 0, "cloudRequests": 0, "savingsUsd": 0.0, "actualCostUsd": 0.0}
    )
    for x in inference_items:
        t = x.get("teamId") or "unknown"
        u = x.get("userId") or "unknown"
        s_val = max(0.0, x.get("estimatedAllCloudCostUsd", 0) - x.get("actualCostUsd", 0))
        _team[t]["requests"] += 1
        if x.get("route") == "local":
            _team[t]["localRequests"] += 1
        elif x.get("route") == "cloud":
            _team[t]["cloudRequests"] += 1
        _team[t]["savingsUsd"] += s_val
        _team[t]["actualCostUsd"] += x.get("actualCostUsd", 0)
        _user[u]["teamId"] = t
        _user[u]["requests"] += 1
        if x.get("route") == "cloud":
            _user[u]["cloudRequests"] += 1
        _user[u]["savingsUsd"] += s_val
        _user[u]["actualCostUsd"] += x.get("actualCostUsd", 0)

    by_team = sorted(_team.items(), key=lambda kv: kv[1]["requests"], reverse=True)
    by_user = sorted(_user.items(), key=lambda kv: kv[1]["requests"], reverse=True)[:20]

    _client: dict = defaultdict(
        lambda: {
            "events": 0, "linesAdded": 0, "linesDeleted": 0,
            "tokens": 0, "actualCostUsd": 0.0, "savingsUsd": 0.0,
        }
    )
    for x in client_sourced_items:
        c = _resolve_client_id(x)
        _client[c]["events"] += 1
        _client[c]["linesAdded"] += x.get("linesAdded", 0)
        _client[c]["linesDeleted"] += x.get("linesDeleted", 0)
        _client[c]["tokens"] += _event_tokens(x)
        _client[c]["actualCostUsd"] += x.get("actualCostUsd", 0)
        _client[c]["savingsUsd"] += x.get("savingsUsd", 0)

    by_client = sorted(_client.items(), key=lambda kv: kv[1]["events"], reverse=True)

    client_cost_actual = sum(x.get("actualCostUsd", 0) for x in cost_items)
    client_cost_savings = sum(x.get("savingsUsd", 0) for x in cost_items)
    client_telemetry = {
        "auditCount": len(audit_items),
        "productivitySessionCount": len(prod_items),
        "usageEventCount": len(usage_items),
        "costEventCount": len(cost_items),
        "totalLinesAdded": sum(x.get("linesAdded", 0) for x in prod_items),
        "totalLinesDeleted": sum(x.get("linesDeleted", 0) for x in prod_items),
        "totalClientTokens": sum(x.get("totalTokens", 0) for x in usage_items),
        "clientActualCostUsd": round(client_cost_actual, 6),
        "clientSavingsUsd": round(client_cost_savings, 6),
    }

    # Decision-oriented Phase 1 summaries. These remain descriptive signals;
    # none are collapsed into a developer ranking or productivity score.
    active_users = {x.get("userId") for x in items if x.get("userId") and x.get("userId") != "unknown"}
    token_bearing_items = usage_items + inference_items + cursor_usage_items
    total_tokens = sum(_event_tokens(x) for x in token_bearing_items)
    prompt_tokens = sum(int(x.get("promptTokens") or 0) for x in usage_items + inference_items)
    completion_tokens = sum(int(x.get("completionTokens") or 0) for x in usage_items + inference_items)
    for x in cursor_usage_items:
        est = _event_tokens(x)
        if est > 0:
            completion_tokens += est
    active_time_sec = sum(float(x.get("activeCodingTimeSec") or x.get("durationSec") or 0) for x in prod_items)
    ai_wait_ms = sum(_event_duration_ms(x) for x in usage_items + inference_items + cursor_usage_items + agent_turn_items)
    rework_lines = sum(int(x.get("reworkLines") or 0) for x in prod_items)
    lines_added = sum(int(x.get("linesAdded") or 0) for x in prod_items)
    context_switches = sum(int(x.get("contextSwitchCount") or 0) for x in prod_items)

    by_tool_map: dict = defaultdict(lambda: {"events": 0, "users": set(), "tokens": 0, "durationMs": 0.0, "costUsd": 0.0})
    for x in usage_items + inference_items + cursor_usage_items + agent_turn_items:
        tool = _resolve_client_id(x)
        bucket = by_tool_map[tool]
        bucket["events"] += 1
        if x.get("userId"):
            bucket["users"].add(x["userId"])
        bucket["tokens"] += _event_tokens(x)
        bucket["durationMs"] += _event_duration_ms(x)
        bucket["costUsd"] += float(x.get("actualCostUsd") or 0)

    by_model_map: dict = defaultdict(lambda: {"requests": 0, "tokens": 0, "costUsd": 0.0})
    # Aggregate models from usage, inference, cursor_usage, and agent_turn events
    # (all events that carry model information for AI Usage dashboard)
    for x in usage_items + inference_items + cursor_usage_items + agent_turn_items:
        model = x.get("model") or "unknown"
        by_model_map[model]["requests"] += 1
        by_model_map[model]["tokens"] += _event_tokens(x)
        by_model_map[model]["costUsd"] += float(x.get("actualCostUsd") or 0)

    pr_by_id: dict[str, dict] = {}
    for event in sorted(pr_items, key=lambda x: x.get("timestamp") or x.get("occurredAt") or ""):
        pr_id = str(event.get("pullRequestId") or event.get("prNumber") or "unknown")
        key = f"{event.get('repoId') or 'unknown'}:{pr_id}"
        row = pr_by_id.setdefault(key, {
            "pullRequestId": pr_id, "repoId": event.get("repoId") or "unknown",
            "title": event.get("title") or "", "userId": event.get("userId") or "unknown",
            "teamId": event.get("teamId") or "unknown", "status": "unknown",
            "openedAt": None, "mergedAt": None, "reviewCount": 0,
            "filesChanged": 0, "linesAdded": 0, "linesDeleted": 0,
            "tokens": 0, "tool": event.get("client") or "unknown",
        })
        row.update({k: event[k] for k in ("title", "userId", "teamId") if event.get(k)})
        row["tool"] = event.get("client") or row["tool"]
        if event.get("type") == "pr_opened":
            row["status"] = "open"
            row["openedAt"] = event.get("occurredAt") or event.get("timestamp")
        elif event.get("type") == "pr_reviewed":
            row["reviewCount"] += 1
        elif event.get("type") == "pr_merged":
            row["status"] = "merged"
            row["mergedAt"] = event.get("occurredAt") or event.get("timestamp")
        elif event.get("type") == "pr_closed":
            row["status"] = "closed"
        row["filesChanged"] = max(row["filesChanged"], int(event.get("filesChanged") or 0))
        row["linesAdded"] = max(row["linesAdded"], int(event.get("linesAdded") or 0))
        row["linesDeleted"] = max(row["linesDeleted"], int(event.get("linesDeleted") or 0))
        row["tokens"] = max(row["tokens"], int(event.get("totalTokens") or 0))

    def _parse_ts(value):
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    cycle_hours = []
    for row in pr_by_id.values():
        opened, merged = _parse_ts(row["openedAt"]), _parse_ts(row["mergedAt"])
        row["cycleTimeHours"] = round((merged - opened).total_seconds() / 3600, 2) if opened and merged else None
        if row["cycleTimeHours"] is not None:
            cycle_hours.append(row["cycleTimeHours"])

    versioned = [x for x in items if x.get("schemaVersion")]
    automatic = [x for x in items if x.get("collectionMethod") in ("automatic", "hook")]
    attributed = [x for x in items if x.get("attributionConfidence") is not None]
    high_conf = [x for x in attributed if float(x.get("attributionConfidence") or 0) >= 0.8]
    redacted = [x for x in items if x.get("redactionApplied")]
    sensitive_events = [x for x in items if x.get("policyBlockedCloud") or x.get("type") == "security_incident"]

    insights = {
        "activeDevelopers": len(active_users),
        "tokens": {"total": total_tokens, "prompt": prompt_tokens, "completion": completion_tokens},
        "time": {"activeCodingSec": round(active_time_sec, 1), "aiWaitMs": round(ai_wait_ms, 1)},
        "flow": {
            "sessions": len(prod_items), "reworkLines": rework_lines,
            "reworkRatePercent": round(100 * rework_lines / lines_added, 1) if lines_added else 0.0,
            "contextSwitches": context_switches,
        },
        "security": {"policyBlocks": blocked, "sensitiveEvents": len(sensitive_events), "redactions": len(redacted)},
        "dataQuality": {
            "totalEvents": len(items), "versionedEvents": len(versioned),
            "versionedCoveragePercent": round(100 * len(versioned) / len(items), 1) if items else 0.0,
            "automaticCoveragePercent": round(100 * len(automatic) / len(items), 1) if items else 0.0,
            "attributedEvents": len(attributed),
            "highConfidenceAttributionPercent": round(100 * len(high_conf) / len(attributed), 1) if attributed else 0.0,
        },
        "byTool": [
            {"tool": tool, "events": v["events"], "activeUsers": len(v["users"]), "tokens": v["tokens"],
             "durationMs": round(v["durationMs"], 1), "costUsd": round(v["costUsd"], 6)}
            for tool, v in sorted(by_tool_map.items(), key=lambda kv: kv[1]["events"], reverse=True)
        ],
        "byModel": [
            {"model": model, "requests": v["requests"], "tokens": v["tokens"], "costUsd": round(v["costUsd"], 6)}
            for model, v in sorted(by_model_map.items(), key=lambda kv: kv[1]["tokens"], reverse=True)
        ],
        "pr": {
            "opened": sum(1 for x in pr_items if x.get("type") == "pr_opened"),
            "merged": sum(1 for x in pr_items if x.get("type") == "pr_merged"),
            "reviewed": sum(1 for x in pr_items if x.get("type") == "pr_reviewed"),
            "medianCycleTimeHours": (
                round(
                    (sorted(cycle_hours)[(len(cycle_hours) - 1) // 2] + sorted(cycle_hours)[len(cycle_hours) // 2]) / 2,
                    2,
                ) if cycle_hours else None
            ),
        },
    }

    return {
        "totalRequests": total,
        "localRequests": local,
        "cloudRequests": cloud,
        "policyBlocks": blocked,
        "cacheHits": cache_hits,
        "cacheHitRatePercent": cache_rate,
        "estimatedAllCloudCostUsd": round(all_cloud_cost, 6),
        "actualCostUsd": round(actual_cost, 6),
        "estimatedSavingsUsd": round(savings, 6),
        "savingsRatePercent": savings_rate,
        "recent": inference_items[-20:],
        "byTeam": [
            {
                "teamId": t,
                "requests": v["requests"],
                "localRequests": v["localRequests"],
                "cloudRequests": v["cloudRequests"],
                "estimatedSavingsUsd": round(v["savingsUsd"], 6),
                "actualCostUsd": round(v["actualCostUsd"], 6),
            }
            for t, v in by_team
        ],
        "byUser": [
            {
                "userId": u,
                "teamId": v["teamId"],
                "requests": v["requests"],
                "cloudRequests": v["cloudRequests"],
                "estimatedSavingsUsd": round(v["savingsUsd"], 6),
                "actualCostUsd": round(v["actualCostUsd"], 6),
            }
            for u, v in by_user
        ],
        "byClient": [
            {
                "client": c,
                "events": v["events"],
                "linesAdded": v["linesAdded"],
                "linesDeleted": v["linesDeleted"],
                "tokens": v["tokens"],
                "actualCostUsd": round(v["actualCostUsd"], 6),
                "savingsUsd": round(v["savingsUsd"], 6),
            }
            for c, v in by_client
        ],
        "productivity": prod_items[-50:],
        "audit": audit_items[-50:],
        "usage": usage_items[-20:],
        "cost": cost_items[-20:],
        "cursorUsage": cursor_usage_items[-30:],
        "cursorCommit": cursor_commit_items[-20:],
        "agentTurn": agent_turn_items[-20:],
        "sessionOutcome": session_outcome_items[-20:],
        # Feed the Raw Events tab from the persisted stream directly instead
        # of reconstructing it from independently capped category arrays.
        "rawEvents": items[-100:],
        "clientTelemetry": client_telemetry,
        "productivityScores": productivity.compute_productivity_scores(items),
        "insights": insights,
        "pullRequests": list(pr_by_id.values())[-50:],
    }


async def metrics_summary(
    user_id: str | None = None,
    team_id: str | None = None,
    days: int = 30,
    client_id: str | None = None,
) -> dict:
    items = [_with_resolved_client(x) for x in await get_metrics(user_id=user_id, team_id=team_id)]
    if client_id and client_id != "all":
        items = [x for x in items if _resolve_client_id(x) == client_id]
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    previous_cutoff = cutoff - datetime.timedelta(days=days)

    def in_range(item, start, end):
        raw = item.get("occurredAt") or item.get("timestamp")
        if not raw:
            # Legacy JSONL fixtures/events without timestamps are treated as
            # current rather than silently disappearing from the dashboard.
            return end == now
        try:
            ts = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            return start <= ts < end
        except (TypeError, ValueError):
            return False

    current_items = [x for x in items if in_range(x, cutoff, now)]
    previous_items = [x for x in items if in_range(x, previous_cutoff, cutoff)]
    summary = _compute_summary(current_items)
    previous = _compute_summary(previous_items)
    summary["period"] = {"days": days, "from": cutoff.isoformat(), "to": now.isoformat()}
    summary["comparison"] = {
        "totalRequests": previous["totalRequests"],
        "activeDevelopers": previous["insights"]["activeDevelopers"],
        "tokens": previous["insights"]["tokens"]["total"],
        "activeCodingSec": previous["insights"]["time"]["activeCodingSec"],
        "mergedPrs": previous["insights"]["pr"]["merged"],
        "actualCostUsd": previous["actualCostUsd"],
    }
    return summary


# ---------------------------------------------------------------------------
# Legacy sync helper — used by dashboard_html() and tests that write JSONL
# ---------------------------------------------------------------------------

def dashboard_html() -> str:
    """Generate dashboard HTML from JSONL (legacy path, used by tests)."""
    items = _read_jsonl()
    s = _compute_summary(items)

    rows = ""
    for x in reversed(s["recent"]):
        ts = x.get("timestamp", "")
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = ts.replace("T", " ")[:19] if "T" in ts else ts

        route = str(x.get("route", ""))
        route_class = "badge-cloud" if route == "cloud" else "badge-local"
        route_html = f"<span class='badge {route_class}'>{html.escape(route.upper(), quote=True)}</span>"
        fallback = x.get("fallbackUsed", False)
        fallback_html = (
            "<span class='badge badge-warning'>Yes</span>"
            if fallback
            else "<span class='text-muted'>No</span>"
        )
        req_all = float(x.get("estimatedAllCloudCostUsd", 0) or 0)
        req_act = float(x.get("actualCostUsd", 0) or 0)
        req_sav = max(0.0, req_all - req_act)

        def _e(v):
            return html.escape(str(v), quote=True)

        rows += (
            f"<tr>"
            f"<td>{_e(ts)}</td>"
            f"<td class='user-col'>{_e(x.get('userId', ''))}</td>"
            f"<td>{_e(x.get('teamId', ''))}</td>"
            f"<td>{route_html}</td>"
            f"<td class='model-col'>{_e(x.get('model', ''))}</td>"
            f"<td class='reason-col'>{_e(x.get('reason', ''))}</td>"
            f"<td>{_e(x.get('estimatedTokens', 0))}</td>"
            f"<td>{fallback_html}</td>"
            f"<td>{_e(x.get('repoContextHits', 0))}</td>"
            f"<td>{_e(x.get('latencyMs', 0))}</td>"
            f"<td style='color: var(--route-local)'>${req_sav:.4f}</td>"
            f"</tr>"
        )

    team_rows = ""
    for t in s["byTeam"]:
        team_rows += (
            f"<tr>"
            f"<td><strong>{html.escape(str(t['teamId']), quote=True)}</strong></td>"
            f"<td>{t['requests']}</td>"
            f"<td>{t['localRequests']}</td>"
            f"<td>{t['cloudRequests']}</td>"
            f"<td style='color: var(--route-local)'>${t['estimatedSavingsUsd']:.4f}</td>"
            f"<td style='color: var(--text-muted)'>${t['actualCostUsd']:.4f}</td>"
            f"</tr>"
        )
    if not team_rows:
        team_rows = "<tr><td colspan='6' class='text-muted' style='text-align:center;padding:20px'>No data yet.</td></tr>"

    user_rows = ""
    for u in s["byUser"]:
        user_rows += (
            f"<tr>"
            f"<td class='user-col'>{html.escape(str(u['userId']), quote=True)}</td>"
            f"<td>{html.escape(str(u['teamId']), quote=True)}</td>"
            f"<td>{u['requests']}</td>"
            f"<td>{u['cloudRequests']}</td>"
            f"<td style='color: var(--route-local)'>${u['estimatedSavingsUsd']:.4f}</td>"
            f"<td style='color: var(--text-muted)'>${u['actualCostUsd']:.4f}</td>"
            f"</tr>"
        )
    if not user_rows:
        user_rows = "<tr><td colspan='6' class='text-muted' style='text-align:center;padding:20px'>No data yet.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <title>Vantage Dashboard</title>
    <style>
        body {{ font-family: sans-serif; margin: 40px; background: #f4f6f8; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }}
        .badge {{ padding: 2px 8px; border-radius: 12px; font-size: 12px; }}
        .badge-local {{ background: #d1fae5; color: #065f46; }}
        .badge-cloud {{ background: #e0e7ff; color: #3730a3; }}
        .badge-warning {{ background: #fef3c7; color: #92400e; }}
        .text-muted {{ color: #6b7280; }}
        .user-col {{ font-family: monospace; font-size: 12px; color: #6b7280; }}
        .model-col {{ font-family: monospace; font-size: 13px; }}
        .reason-col {{ color: #6b7280; }}
    </style>
</head>
<body>
<h1>Vantage Dashboard</h1>
<p>Total: {s["totalRequests"]} | Local: {s["localRequests"]} | Cloud: {s["cloudRequests"]} | Savings: ${s["estimatedSavingsUsd"]:.4f}</p>
<h2>Per-Team Breakdown</h2>
<table><thead><tr><th>Team</th><th>Requests</th><th>Local</th><th>Cloud</th><th>Savings</th><th>Cost</th></tr></thead>
<tbody>{team_rows}</tbody></table>
<h2>Top Spenders</h2>
<table><thead><tr><th>User</th><th>Team</th><th>Requests</th><th>Cloud</th><th>Savings</th><th>Cost</th></tr></thead>
<tbody>{user_rows}</tbody></table>
<h2>Recent Activity</h2>
<table><thead><tr><th>Time</th><th>User</th><th>Team</th><th>Route</th><th>Model</th><th>Reason</th><th>Tokens</th><th>Fallback</th><th>Repo Hits</th><th>Latency</th><th>Savings</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


# Routes — dashboard/metrics served from app.main (auth-aware). Legacy HTML helper kept for tests.

static_dir = pathlib.Path(__file__).parent / "static"


# Intentionally no @router.get("/metrics") or @router.get("/dashboard") here;
# vantage-telemetry-service registers those in app.main with required authentication.
