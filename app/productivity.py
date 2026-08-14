"""Productivity scoring: turns raw telemetry events (client-side coding sessions,
gateway inference metrics, Cursor AI-attribution data) into outcome-oriented
productivity signals.

Deliberately does NOT reduce productivity to lines-of-code or request counts --
those are gameable vanity metrics (see docs/productivity_measurement.md for the
research basis: SPACE, DORA, flow efficiency, NASA-TLX-style effort framing).
Instead this module computes a small set of named components:

- taskCompletionRate  : fraction of coding sessions that ended in a git commit
                        rather than being abandoned (outcome-based, not effort-based)
- reworkRatio         : fraction of written lines that were undone in the same
                        session (churn/thrash signal — SPACE "efficiency & flow")
- contextSwitchRate   : file switches per active hour (flow fragmentation)
- frictionScore       : 0-100, higher = more friction (fallback rate, latency,
                        cache-miss rate on the inference path)
- aiContributionPct   : Cursor's own AI-vs-human line attribution per commit
                        (diagnostic only — not folded into the composite score,
                        since a higher AI share is not inherently "more productive")
- loopEfficiency      : commits shipped per inference call spent getting there
                        (diagnostic only, same reasoning)

The composite `productivityScore` is a weighted blend of only the three
monotonic, outcome-oriented components (completion, low rework, low friction).
Components built from insufficient data are dropped and the remaining weights
renormalized rather than defaulting to a misleading number.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median


# Composite weights for components with an unambiguous "higher is better" sense.
# AI-contribution % and loop efficiency are intentionally excluded from the
# composite (see module docstring) and reported only as diagnostics.
_COMPLETION_WEIGHT = 0.45
_LOW_REWORK_WEIGHT = 0.30
_LOW_FRICTION_WEIGHT = 0.25

_MIN_SESSIONS_FOR_COMPLETION = 1
_MIN_CALLS_FOR_FRICTION = 3


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct * (len(s) - 1)))))
    return s[k]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _group_by_user(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for x in items:
        grouped[x.get("userId") or "unknown"].append(x)
    return grouped


def _friction_score(inference_items: list[dict]) -> tuple[float | None, dict]:
    """0-100 friction score (higher = worse) from the inference path.

    Blends fallback rate (local model failed/was unhelpful and had to escalate),
    p95 latency (normalized against a generous 15s ceiling), and cache-miss rate.
    Returns (score_or_None, raw_components) -- None when there isn't enough
    volume to trust the rate (see _MIN_CALLS_FOR_FRICTION).
    """
    n = len(inference_items)
    if n < _MIN_CALLS_FOR_FRICTION:
        return None, {"sampleSize": n}

    fallback_rate = sum(1 for x in inference_items if x.get("fallbackUsed")) / n
    cache_rate = sum(1 for x in inference_items if x.get("cacheHit") or x.get("reason") in ("cache_hit", "semantic_cache_hit")) / n
    latencies = [float(x.get("latencyMs") or 0) for x in inference_items]
    p95_latency = _percentile(latencies, 0.95)
    latency_norm = _clamp01(p95_latency / 15000.0)

    score = 100.0 * (0.5 * fallback_rate + 0.3 * latency_norm + 0.2 * (1.0 - cache_rate))
    return round(score, 1), {
        "sampleSize": n,
        "fallbackRate": round(fallback_rate, 3),
        "cacheHitRate": round(cache_rate, 3),
        "p95LatencyMs": round(p95_latency, 1),
    }


def _rework_and_switch(session_items: list[dict]) -> dict:
    """session_items: 'productivity' events for one user."""
    n = len(session_items)
    total_active_sec = sum(x.get("activeCodingTimeSec", 0.0) for x in session_items)
    if n == 0:
        return {
            "reworkRatio": None,
            "contextSwitchRate": None,
            "sampleSize": 0,
            "totalActiveCodingTimeSec": 0.0,
        }

    total_added = sum(x.get("linesAdded", 0) for x in session_items)
    total_rework = sum(x.get("reworkLines", 0) for x in session_items)
    total_switches = sum(x.get("contextSwitchCount", 0) for x in session_items)

    rework_ratio = (total_rework / total_added) if total_added > 0 else 0.0
    switch_rate = (total_switches / (total_active_sec / 3600.0)) if total_active_sec > 0 else 0.0
    return {
        "reworkRatio": round(_clamp01(rework_ratio), 3),
        "contextSwitchRate": round(switch_rate, 2),
        "sampleSize": n,
        "totalActiveCodingTimeSec": round(total_active_sec, 1),
    }


def _task_completion(outcome_items: list[dict]) -> tuple[float | None, dict]:
    n = len(outcome_items)
    if n < _MIN_SESSIONS_FOR_COMPLETION:
        return None, {"sampleSize": n}
    committed = sum(1 for x in outcome_items if x.get("outcome") == "committed")
    return round(committed / n, 3), {"sampleSize": n, "committed": committed, "abandoned": n - committed}


def _ai_contribution(cursor_commit_items: list[dict]) -> tuple[float | None, dict]:
    pcts = [float(x["v2AiPercentage"]) for x in cursor_commit_items if x.get("v2AiPercentage") is not None]
    if not pcts:
        return None, {"sampleSize": 0}
    return round(sum(pcts) / len(pcts), 1), {"sampleSize": len(pcts)}


def _loop_efficiency(outcome_items: list[dict], inference_items: list[dict]) -> tuple[float | None, dict]:
    """Shipped commits per inference call — how many AI round-trips it took to
    ship a unit of work. Diagnostic only: a low value can mean either a hard
    task or an inefficient loop, which this signal alone can't distinguish."""
    n_calls = len(inference_items)
    n_committed = sum(1 for x in outcome_items if x.get("outcome") == "committed")
    if n_calls == 0:
        return None, {"inferenceCalls": 0, "commitsShipped": n_committed}
    return round(n_committed / n_calls, 4), {"inferenceCalls": n_calls, "commitsShipped": n_committed}


def _composite_score(completion_rate: float | None, rework_ratio: float | None, friction_score: float | None) -> tuple[float | None, dict]:
    components = []
    if completion_rate is not None:
        components.append((_COMPLETION_WEIGHT, completion_rate))
    if rework_ratio is not None:
        components.append((_LOW_REWORK_WEIGHT, 1.0 - rework_ratio))
    if friction_score is not None:
        components.append((_LOW_FRICTION_WEIGHT, 1.0 - friction_score / 100.0))

    if not components:
        return None, {"reason": "insufficient_data"}

    weight_sum = sum(w for w, _ in components)
    score = sum(w * v for w, v in components) / weight_sum
    return round(100.0 * score, 1), {"componentsUsed": len(components), "weightCoverage": round(weight_sum, 2)}


def compute_productivity_scores(items: list[dict]) -> dict:
    """Compute per-user productivity components + composite score from a flat
    list of telemetry records (as returned by observability.get_metrics()).

    Returns {"byUser": {userId: {...}}, "overall": {...}}. Any component that
    can't be computed for a user due to insufficient sample size is reported
    as null rather than a misleading default.
    """
    session_items = [x for x in items if x.get("type") == "productivity"]
    outcome_items = [x for x in items if x.get("type") == "session_outcome"]
    cursor_commit_items = [x for x in items if x.get("type") == "cursor_commit"]
    inference_items = [x for x in items if "route" in x and x.get("type", "inference") not in (
        "productivity", "audit", "productivity_task", "usage", "cost", "session_outcome",
        "cursor_usage", "cursor_commit",
    )]

    users = set()
    for group in (session_items, outcome_items, cursor_commit_items, inference_items):
        users.update(_group_by_user(group).keys())

    by_session = _group_by_user(session_items)
    by_outcome = _group_by_user(outcome_items)
    by_commit = _group_by_user(cursor_commit_items)
    by_inference = _group_by_user(inference_items)

    by_user: dict[str, dict] = {}
    for user in users:
        rework = _rework_and_switch(by_session.get(user, []))
        completion_rate, completion_meta = _task_completion(by_outcome.get(user, []))
        friction, friction_meta = _friction_score(by_inference.get(user, []))
        ai_pct, ai_meta = _ai_contribution(by_commit.get(user, []))
        loop_eff, loop_meta = _loop_efficiency(by_outcome.get(user, []), by_inference.get(user, []))
        composite, composite_meta = _composite_score(completion_rate, rework["reworkRatio"], friction)

        by_user[user] = {
            "productivityScore": composite,
            "productivityScoreMeta": composite_meta,
            "taskCompletionRate": completion_rate,
            "taskCompletionMeta": completion_meta,
            "reworkRatio": rework["reworkRatio"],
            "contextSwitchRate": rework["contextSwitchRate"],
            "sessionSampleSize": rework["sampleSize"],
            "frictionScore": friction,
            "frictionMeta": friction_meta,
            "aiContributionPct": ai_pct,
            "aiContributionMeta": ai_meta,
            "loopEfficiency": loop_eff,
            "loopEfficiencyMeta": loop_meta,
        }

    valid_scores = [v["productivityScore"] for v in by_user.values() if v["productivityScore"] is not None]
    overall = {
        "usersScored": len(valid_scores),
        "usersTotal": len(by_user),
        "medianProductivityScore": round(median(valid_scores), 1) if valid_scores else None,
    }

    return {"byUser": by_user, "overall": overall}
