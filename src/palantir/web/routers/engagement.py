"""Engagement scoring API endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from palantir.eventlog.aggregator import EngagementAggregator
from palantir.web.dependencies import get_db, verify_auth
from palantir.web.rate_limit import rate_limit_read

router = APIRouter(
    prefix="/api/engagement",
    tags=["engagement"],
    dependencies=[Depends(verify_auth), Depends(rate_limit_read)],
)


def _get_aggregator(db: sqlite3.Connection = Depends(get_db)) -> EngagementAggregator:
    return EngagementAggregator(db)


@router.get("/session/{session_id}")
async def get_session_engagement(
    session_id: str,
    aggregator: EngagementAggregator = Depends(_get_aggregator),
):
    """Get per-student engagement scores for a session."""
    scores = aggregator.get_session_scores(session_id)
    return {"session_id": session_id, "scores": scores}


@router.get("/current")
async def get_current_engagement(
    db: sqlite3.Connection = Depends(get_db),
    aggregator: EngagementAggregator = Depends(_get_aggregator),
):
    """Get engagement scores for the current active session."""
    session = db.execute(
        "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if not session:
        return {"session_id": None, "scores": []}

    session_id = session["id"]
    scores = aggregator.get_session_scores(session_id)
    return {"session_id": session_id, "scores": scores}


@router.get("/person/{person_id}")
async def get_person_engagement(
    person_id: str,
    limit: int = 10,
    aggregator: EngagementAggregator = Depends(_get_aggregator),
):
    """Get engagement trend across recent sessions for a person."""
    trend = aggregator.get_person_trend(person_id, limit=limit)
    return {"person_id": person_id, "sessions": trend}


@router.get("/heatmap/{session_id}")
async def get_engagement_heatmap(
    session_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Get time-series engagement data for the heatmap visualization.

    Returns per-student engagement states bucketed into 1-minute intervals.
    """
    rows = db.execute(
        "SELECT e.person_id, p.name, e.state, e.sampled_at "
        "FROM engagement_samples e "
        "JOIN persons p ON e.person_id = p.id "
        "WHERE e.session_id = ? "
        "ORDER BY p.name, e.sampled_at",
        (session_id,),
    ).fetchall()

    if not rows:
        return {"session_id": session_id, "students": [], "time_buckets": []}

    # Bucket into 1-minute intervals
    from collections import defaultdict
    from datetime import datetime

    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    all_minutes: set[str] = set()
    student_names: dict[str, str] = {}

    for row in rows:
        pid = row["person_id"]
        student_names[pid] = row["name"]
        ts = row["sampled_at"]
        # Truncate to minute
        if isinstance(ts, str):
            minute = ts[:16]  # "YYYY-MM-DD HH:MM"
        else:
            minute = ts.strftime("%Y-%m-%d %H:%M")
        all_minutes.add(minute)
        buckets[pid][minute].append(row["state"])

    # Compute dominant state per student per minute
    time_buckets = sorted(all_minutes)
    students = []
    for pid, name in sorted(student_names.items(), key=lambda x: x[1]):
        states_by_minute = []
        for minute in time_buckets:
            samples = buckets[pid].get(minute, [])
            if samples:
                # Majority state for this minute
                from collections import Counter
                dominant = Counter(samples).most_common(1)[0][0]
                states_by_minute.append(dominant)
            else:
                states_by_minute.append(None)
        students.append({
            "person_id": pid,
            "name": name,
            "states": states_by_minute,
        })

    return {
        "session_id": session_id,
        "time_buckets": time_buckets,
        "students": students,
    }
