"""Attendance API endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from palintir.web.dependencies import get_db, verify_auth
from palintir.web.rate_limit import rate_limit_read

router = APIRouter(
    prefix="/api/attendance",
    tags=["attendance"],
    dependencies=[Depends(verify_auth), Depends(rate_limit_read)],
)


@router.get("/current")
async def get_current_session(db: sqlite3.Connection = Depends(get_db)):
    """Get the current active session and who's present."""
    session = db.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if not session:
        return {"session": None, "records": []}

    records = db.execute(
        "SELECT a.*, p.name, p.role FROM attendance_records a "
        "JOIN persons p ON a.person_id = p.id "
        "WHERE a.session_id = ? ORDER BY a.entered_at",
        (session["id"],),
    ).fetchall()

    return {
        "session": dict(session),
        "records": [dict(r) for r in records],
    }


@router.get("/history")
async def get_session_history(
    limit: int = 20,
    db: sqlite3.Connection = Depends(get_db),
):
    """Get past session summaries."""
    sessions = db.execute(
        "SELECT s.*, "
        "(SELECT COUNT(DISTINCT person_id) FROM attendance_records WHERE session_id = s.id) as attendee_count "
        "FROM sessions s ORDER BY s.started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    return {"sessions": [dict(s) for s in sessions]}


@router.get("/history/{session_id}")
async def get_session_detail(
    session_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Get detailed attendance for a specific session."""
    session = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not session:
        return {"error": "Session not found"}

    records = db.execute(
        "SELECT a.*, p.name, p.role FROM attendance_records a "
        "JOIN persons p ON a.person_id = p.id "
        "WHERE a.session_id = ? ORDER BY a.entered_at",
        (session_id,),
    ).fetchall()

    return {
        "session": dict(session),
        "records": [dict(r) for r in records],
    }


@router.get("/person/{person_id}")
async def get_person_attendance(
    person_id: str,
    limit: int = 30,
    db: sqlite3.Connection = Depends(get_db),
):
    """Get attendance history for a specific person."""
    records = db.execute(
        "SELECT a.*, s.name as session_name FROM attendance_records a "
        "LEFT JOIN sessions s ON a.session_id = s.id "
        "WHERE a.person_id = ? ORDER BY a.entered_at DESC LIMIT ?",
        (person_id, limit),
    ).fetchall()

    return {"person_id": person_id, "records": [dict(r) for r in records]}
