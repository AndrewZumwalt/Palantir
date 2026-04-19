"""System status, retention, and administration API."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from palantir.config import PalantirConfig
from palantir.redis_client import Channels, publish
from palantir.web.dependencies import get_config, get_db, get_redis, verify_auth
from palantir.web.rate_limit import rate_limit_read, rate_limit_write

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(verify_auth)])

SERVICE_NAMES = ["audio", "vision", "brain", "tts", "eventlog", "web"]


@router.get("/status", dependencies=[Depends(rate_limit_read)])
async def get_system_status(
    request: Request,
    redis: aioredis.Redis = Depends(get_redis),
    config: PalantirConfig = Depends(get_config),
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
async def get_retention(config: PalantirConfig = Depends(get_config)):
    return {"retention_days": config.privacy.data_retention_days}


@router.post("/retention/cleanup", dependencies=[Depends(rate_limit_write)])
async def trigger_retention_cleanup(
    db: sqlite3.Connection = Depends(get_db),
    config: PalantirConfig = Depends(get_config),
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


class ReloadRequest(BaseModel):
    # Which services to reload. Empty list => all reloadable services.
    services: list[str] = []


# Services that respond to soft-reload requests. `web` is deliberately
# excluded — it reloads itself implicitly whenever the browser tab refreshes,
# and restarting it mid-request would drop the caller's connection.
RELOADABLE_SERVICES = ["audio", "vision", "brain", "tts", "eventlog"]


@router.post("/reload", dependencies=[Depends(rate_limit_write)])
async def reload_services(
    req: ReloadRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Soft-reload backend services in-place.

    Publishes a SYSTEM_RELOAD request with a unique reload_id. Each service
    reports progress via SYSTEM_RELOAD_PROGRESS, which the web service bridges
    to the frontend over WebSocket. This is the backing endpoint for the UI
    "power cycle" button — it rebuilds model state and clears caches without
    actually restarting the systemd units (which would drop this request).
    """
    targets = req.services or list(RELOADABLE_SERVICES)
    # Filter unknown names so a typo can't hang the UI forever.
    targets = [s for s in targets if s in RELOADABLE_SERVICES]
    reload_id = secrets.token_hex(8)

    await publish(
        redis,
        Channels.SYSTEM_RELOAD,
        {"reload_id": reload_id, "services": targets},
    )
    # Emit a synthetic "requested" event so the UI can show each target as
    # pending even before the service's own handler fires.
    for name in targets:
        await publish(
            redis,
            Channels.SYSTEM_RELOAD_PROGRESS,
            {
                "reload_id": reload_id,
                "service": name,
                "status": "pending",
                "message": "reload requested",
            },
        )
    return {"reload_id": reload_id, "services": targets}


@router.get("/persons", dependencies=[Depends(rate_limit_read)])
async def list_persons(db: sqlite3.Connection = Depends(get_db)):
    """Small endpoint used by the automation UI for dropdowns."""
    rows = db.execute(
        "SELECT id, name, role FROM persons WHERE active = 1 ORDER BY name"
    ).fetchall()
    return {"persons": [dict(r) for r in rows]}
