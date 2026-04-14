"""System status, retention, and administration API."""

from __future__ import annotations

import json
import sqlite3
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from palintir.config import PalintirConfig
from palintir.redis_client import Keys
from palintir.web.dependencies import get_config, get_db, get_redis, verify_auth
from palintir.web.rate_limit import rate_limit_read, rate_limit_write

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(verify_auth)])

SERVICE_NAMES = ["audio", "vision", "brain", "tts", "eventlog", "web"]


@router.get("/status", dependencies=[Depends(rate_limit_read)])
async def get_system_status(
    request: Request,
    redis: aioredis.Redis = Depends(get_redis),
    config: PalintirConfig = Depends(get_config),
):
    """Return per-service status from the most recent heartbeat.

    Each service publishes ServiceStatus every ~10s. We cache the latest
    message per-service in Redis via the web service's subscriber.
    """
    services = []
    now = time.time()
    for name in SERVICE_NAMES:
        key = f"status:{name}"
        raw = await redis.get(key)
        if raw:
            try:
                data = json.loads(raw)
                # Parse timestamp to determine staleness
                ts = data.get("timestamp")
                stale = False
                if ts:
                    from datetime import datetime
                    try:
                        ts_parsed = datetime.fromisoformat(ts).timestamp()
                        stale = (now - ts_parsed) > 30
                    except ValueError:
                        stale = True
                services.append({
                    "name": name,
                    "healthy": data.get("healthy", False) and not stale,
                    "uptime_seconds": data.get("uptime_seconds", 0),
                    "details": data.get("details", {}),
                    "last_seen": ts,
                    "stale": stale,
                })
            except (json.JSONDecodeError, TypeError):
                services.append({
                    "name": name,
                    "healthy": False,
                    "stale": True,
                    "details": {},
                })
        else:
            services.append({
                "name": name,
                "healthy": False,
                "stale": True,
                "details": {},
                "last_seen": None,
            })

    return {
        "services": services,
        "web_uptime_seconds": time.monotonic() - request.app.state.start_time,
    }


class RetentionUpdate(BaseModel):
    retention_days: int


@router.get("/retention", dependencies=[Depends(rate_limit_read)])
async def get_retention(config: PalintirConfig = Depends(get_config)):
    return {"retention_days": config.privacy.data_retention_days}


@router.post("/retention/cleanup", dependencies=[Depends(rate_limit_write)])
async def trigger_retention_cleanup(
    db: sqlite3.Connection = Depends(get_db),
    config: PalintirConfig = Depends(get_config),
):
    """Manually trigger a retention cleanup."""
    retention_days = config.privacy.data_retention_days
    before_events = db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
    db.execute(
        "DELETE FROM events WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",),
    )
    db.execute(
        "DELETE FROM engagement_samples WHERE sampled_at < datetime('now', '-30 days')"
    )
    db.commit()
    after_events = db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
    return {
        "retention_days": retention_days,
        "events_deleted": before_events - after_events,
    }


@router.get("/stats", dependencies=[Depends(rate_limit_read)])
async def get_stats(db: sqlite3.Connection = Depends(get_db)):
    """Aggregate counts for the system overview."""
    return {
        "persons": db.execute("SELECT COUNT(*) as c FROM persons WHERE active = 1").fetchone()["c"],
        "sessions": db.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"],
        "events": db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"],
        "engagement_samples": db.execute(
            "SELECT COUNT(*) as c FROM engagement_samples"
        ).fetchone()["c"],
        "automation_rules": db.execute(
            "SELECT COUNT(*) as c FROM automation_rules"
        ).fetchone()["c"],
        "conversations": db.execute(
            "SELECT COUNT(*) as c FROM conversations"
        ).fetchone()["c"],
    }


@router.get("/persons", dependencies=[Depends(rate_limit_read)])
async def list_persons(db: sqlite3.Connection = Depends(get_db)):
    """Small endpoint used by the automation UI for dropdowns."""
    rows = db.execute(
        "SELECT id, name, role FROM persons WHERE active = 1 ORDER BY name"
    ).fetchall()
    return {"persons": [dict(r) for r in rows]}
