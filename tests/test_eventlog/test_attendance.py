"""Tests for attendance state transitions."""

from __future__ import annotations

from palantir.eventlog.attendance import AttendanceTracker


def test_clear_present_closes_records_without_ending_session(temp_db):
    temp_db.execute(
        "INSERT INTO persons (id, name, role) VALUES (?, ?, ?)",
        ("p1", "Alice", "student"),
    )
    temp_db.commit()

    tracker = AttendanceTracker(temp_db)
    session_id = tracker.start_session()
    assert tracker.person_seen("p1") is True

    assert tracker.clear_present() == ["p1"]

    assert tracker.session_active is True
    assert tracker.present_count == 0
    row = temp_db.execute(
        "SELECT exited_at, duration_seconds FROM attendance_records "
        "WHERE session_id = ? AND person_id = ?",
        (session_id, "p1"),
    ).fetchone()
    assert row["exited_at"] is not None
    assert row["duration_seconds"] >= 0
