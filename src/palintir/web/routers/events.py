"""Event log API endpoints."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Query

from palintir.web.dependencies import get_db, verify_auth
from palintir.web.rate_limit import rate_limit_read, rate_limit_write

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(verify_auth)])


@router.get("", dependencies=[Depends(rate_limit_read)])
async def list_events(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: str | None = None,
    person_id: str | None = None,
    since: str | None = None,  # ISO 8601
    until: str | None = None,
    db: sqlite3.Connection = Depends(get_db),
):
    """Filtered list of events, newest first."""
    where_clauses: list[str] = []
    params: list = []

    if type:
        # Allow comma-separated list
        types = [t.strip() for t in type.split(",") if t.strip()]
        placeholders = ",".join("?" for _ in types)
        where_clauses.append(f"e.type IN ({placeholders})")
        params.extend(types)

    if person_id:
        where_clauses.append("e.person_id = ?")
        params.append(person_id)

    if since:
        where_clauses.append("e.created_at >= ?")
        params.append(since)

    if until:
        where_clauses.append("e.created_at <= ?")
        params.append(until)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Count for pagination info
    total = db.execute(
        f"SELECT COUNT(*) as cnt FROM events e {where_sql}",
        params,
    ).fetchone()["cnt"]

    rows = db.execute(
        f"SELECT e.id, e.type, e.person_id, e.data, e.created_at, p.name as person_name "
        f"FROM events e "
        f"LEFT JOIN persons p ON e.person_id = p.id "
        f"{where_sql} "
        f"ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    events = []
    for row in rows:
        item = dict(row)
        if item.get("data"):
            try:
                item["data"] = json.loads(item["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        events.append(item)

    return {
        "events": events,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/types", dependencies=[Depends(rate_limit_read)])
async def get_event_types(db: sqlite3.Connection = Depends(get_db)):
    """Return distinct event types present in the log."""
    rows = db.execute(
        "SELECT DISTINCT type FROM events ORDER BY type"
    ).fetchall()
    return {"types": [r["type"] for r in rows]}


@router.delete("/{event_id}", dependencies=[Depends(rate_limit_write)])
async def delete_event(event_id: int, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    return {"deleted": cursor.rowcount > 0}
