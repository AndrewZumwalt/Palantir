"""Attendance tracking state machine.

Tracks when people enter and exit the classroom based on face
detection events. Manages session-level attendance records.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

import structlog

logger = structlog.get_logger()


class AttendanceTracker:
    """Tracks classroom attendance based on face detection events.

    A person is considered:
    - ENTERED: when their face is first detected
    - PRESENT: while their face continues to be detected
    - EXITED: when their face hasn't been seen for exit_timeout_seconds
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        exit_timeout_seconds: int = 300,
    ):
        self._db = db
        self._exit_timeout = timedelta(seconds=exit_timeout_seconds)

        # In-memory state: person_id -> last seen timestamp
        self._last_seen: dict[str, datetime] = {}
        # person_id -> attendance_record_id for the current visit
        self._active_records: dict[str, int] = {}
        # Current session
        self._session_id: str | None = None

    def start_session(self, name: str | None = None) -> str:
        """Start a new class session."""
        self._session_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO sessions (id, name) VALUES (?, ?)",
            (self._session_id, name or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"),
        )
        self._db.commit()
        self._last_seen.clear()
        self._active_records.clear()
        logger.info("session_started", session_id=self._session_id, name=name)
        return self._session_id

    def end_session(self) -> dict:
        """End the current session and finalize all attendance records.

        Returns:
            Summary dict with attendance counts and durations.
        """
        if not self._session_id:
            return {}

        now = datetime.now()

        # Close all active attendance records
        for person_id, record_id in self._active_records.items():
            self._close_record(record_id, now)

        # Update session end time
        self._db.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (now.isoformat(), self._session_id),
        )
        self._db.commit()

        # Get summary
        rows = self._db.execute(
            "SELECT p.name, a.entered_at, a.exited_at, a.duration_seconds "
            "FROM attendance_records a JOIN persons p ON a.person_id = p.id "
            "WHERE a.session_id = ?",
            (self._session_id,),
        ).fetchall()

        summary = {
            "session_id": self._session_id,
            "total_attendees": len(rows),
            "records": [dict(row) for row in rows],
        }

        logger.info("session_ended", session_id=self._session_id, attendees=len(rows))

        self._session_id = None
        self._last_seen.clear()
        self._active_records.clear()

        return summary

    def person_seen(self, person_id: str) -> bool:
        """Record that a person was detected in the current frame.

        Args:
            person_id: The detected person's ID.

        Returns:
            True if this is a new entry (person wasn't present before).
        """
        now = datetime.now()
        is_new_entry = False

        if person_id not in self._active_records:
            # New entry
            is_new_entry = True
            record_id = self._create_record(person_id, now)
            self._active_records[person_id] = record_id
            logger.info("person_entered", person_id=person_id)

        self._last_seen[person_id] = now
        return is_new_entry

    def check_exits(self) -> list[str]:
        """Check for people who have left (not seen for exit_timeout).

        Call this periodically (e.g., every 30 seconds).

        Returns:
            List of person_ids who just exited.
        """
        now = datetime.now()
        exited = []

        for person_id, last_seen in list(self._last_seen.items()):
            if now - last_seen > self._exit_timeout:
                # Person has left
                if person_id in self._active_records:
                    self._close_record(self._active_records[person_id], last_seen)
                    del self._active_records[person_id]
                del self._last_seen[person_id]
                exited.append(person_id)
                logger.info("person_exited", person_id=person_id)

        if exited:
            self._db.commit()

        return exited

    def clear_present(self, exited_at: datetime | None = None) -> list[str]:
        """Close all active attendance records without ending the session."""
        if not self._active_records:
            self._last_seen.clear()
            return []

        now = exited_at or datetime.now()
        exited = list(self._active_records.keys())
        for record_id in self._active_records.values():
            self._close_record(record_id, now)

        self._db.commit()
        self._active_records.clear()
        self._last_seen.clear()
        logger.info("attendance_present_cleared", count=len(exited))
        return exited

    def get_present(self) -> list[str]:
        """Get list of person_ids currently present."""
        return list(self._active_records.keys())

    def _create_record(self, person_id: str, entered_at: datetime) -> int:
        """Create a new attendance record."""
        cursor = self._db.execute(
            "INSERT INTO attendance_records (session_id, person_id, entered_at) "
            "VALUES (?, ?, ?)",
            (self._session_id, person_id, entered_at.isoformat()),
        )
        self._db.commit()
        return cursor.lastrowid

    def _close_record(self, record_id: int, exited_at: datetime) -> None:
        """Close an attendance record with exit time and duration."""
        row = self._db.execute(
            "SELECT entered_at FROM attendance_records WHERE id = ?",
            (record_id,),
        ).fetchone()

        if row:
            entered = datetime.fromisoformat(row["entered_at"])
            duration = (exited_at - entered).total_seconds()
            self._db.execute(
                "UPDATE attendance_records SET exited_at = ?, duration_seconds = ? WHERE id = ?",
                (exited_at.isoformat(), duration, record_id),
            )

    @property
    def session_active(self) -> bool:
        return self._session_id is not None

    @property
    def present_count(self) -> int:
        return len(self._active_records)
