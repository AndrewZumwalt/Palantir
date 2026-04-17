"""Engagement score aggregation.

Computes per-student, per-session aggregate engagement scores
from raw engagement samples stored in the database.
"""

from __future__ import annotations

import sqlite3

import structlog

from palantir.models import EngagementState

logger = structlog.get_logger()

# Weight multipliers for engagement states
STATE_WEIGHTS = {
    EngagementState.WORKING: 1.0,
    EngagementState.COLLABORATING: 1.2,
    EngagementState.PHONE: 0.0,
    EngagementState.SLEEPING: 0.0,
    EngagementState.DISENGAGED: 0.2,
    EngagementState.UNKNOWN: 0.5,
}


class EngagementAggregator:
    """Computes aggregate engagement scores from raw samples."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def save_sample(
        self,
        session_id: str | None,
        person_id: str,
        state: EngagementState,
        confidence: float,
    ) -> None:
        """Store a single engagement sample."""
        self._db.execute(
            "INSERT INTO engagement_samples (session_id, person_id, state, confidence) "
            "VALUES (?, ?, ?, ?)",
            (session_id, person_id, state.value, confidence),
        )
        self._db.commit()

    def get_session_scores(self, session_id: str) -> list[dict]:
        """Compute per-student engagement scores for a session.

        Score formula:
            score = sum(weight[state] * count[state]) / total_samples * 100

        Returns:
            List of dicts: [{person_id, name, score, breakdown}]
        """
        rows = self._db.execute(
            "SELECT e.person_id, p.name, e.state, COUNT(*) as count "
            "FROM engagement_samples e "
            "JOIN persons p ON e.person_id = p.id "
            "WHERE e.session_id = ? "
            "GROUP BY e.person_id, e.state "
            "ORDER BY p.name",
            (session_id,),
        ).fetchall()

        # Group by person
        person_data: dict[str, dict] = {}
        for row in rows:
            pid = row["person_id"]
            if pid not in person_data:
                person_data[pid] = {
                    "person_id": pid,
                    "name": row["name"],
                    "states": {},
                    "total": 0,
                }
            state_name = row["state"]
            count = row["count"]
            person_data[pid]["states"][state_name] = count
            person_data[pid]["total"] += count

        # Compute scores
        results = []
        for pid, data in person_data.items():
            total = data["total"]
            if total == 0:
                continue

            weighted_sum = 0.0
            for state_name, count in data["states"].items():
                try:
                    state = EngagementState(state_name)
                    weight = STATE_WEIGHTS.get(state, 0.5)
                except ValueError:
                    weight = 0.5
                weighted_sum += weight * count

            score = round((weighted_sum / total) * 100, 1)

            results.append({
                "person_id": data["person_id"],
                "name": data["name"],
                "score": score,
                "total_samples": total,
                "breakdown": data["states"],
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_person_trend(self, person_id: str, limit: int = 10) -> list[dict]:
        """Get engagement score trend across recent sessions for a person."""
        rows = self._db.execute(
            "SELECT s.id as session_id, s.name as session_name, s.started_at, "
            "e.state, COUNT(*) as count "
            "FROM engagement_samples e "
            "JOIN sessions s ON e.session_id = s.id "
            "WHERE e.person_id = ? "
            "GROUP BY s.id, e.state "
            "ORDER BY s.started_at DESC",
            (person_id,),
        ).fetchall()

        sessions: dict[str, dict] = {}
        for row in rows:
            sid = row["session_id"]
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "session_name": row["session_name"],
                    "date": row["started_at"],
                    "states": {},
                    "total": 0,
                }
            sessions[sid]["states"][row["state"]] = row["count"]
            sessions[sid]["total"] += row["count"]

        results = []
        for sid, data in list(sessions.items())[:limit]:
            total = data["total"]
            weighted = sum(
                STATE_WEIGHTS.get(EngagementState(s), 0.5) * c
                for s, c in data["states"].items()
            )
            score = round((weighted / total) * 100, 1) if total else 0
            data["score"] = score
            results.append(data)

        return results
