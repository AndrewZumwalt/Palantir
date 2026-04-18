"""Dashboard API endpoints for the teacher dashboard."""

from __future__ import annotations

import sqlite3
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query

from palantir.redis_client import Keys
from palantir.web.dependencies import get_db, get_redis, verify_auth
from palantir.web.rate_limit import rate_limit_read

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(verify_auth), Depends(rate_limit_read)],
)


@router.get("/status")
async def get_system_status(redis: aioredis.Redis = Depends(get_redis)):
    """Get overall system status including all service health."""
    statuses = await redis.hgetall(Keys.SERVICE_STATUS)
    privacy_mode = await redis.get(Keys.PRIVACY_MODE) == "1"

    return {
        "privacy_mode": privacy_mode,
        "services": statuses,
        "timestamp": time.time(),
    }


@router.get("/attendance")
async def get_current_attendance(
    db: sqlite3.Connection = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Get current attendance (who is present right now)."""
    present_ids = await redis.smembers(Keys.PRESENT_PERSONS)

    if not present_ids:
        return {"present": [], "count": 0}

    placeholders = ",".join("?" for _ in present_ids)
    rows = db.execute(
        f"SELECT id, name, role FROM persons WHERE id IN ({placeholders})",
        list(present_ids),
    ).fetchall()

    return {
        "present": [dict(row) for row in rows],
        "count": len(rows),
    }


@router.get("/events/recent")
async def get_recent_events(
    limit: int = Query(50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    """Get recent events from the log."""
    rows = db.execute(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    return {"events": [dict(row) for row in rows]}
