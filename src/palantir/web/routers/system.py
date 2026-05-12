"""System status, retention, and administration API."""

from __future__ import annotations

import json
import platform
import secrets
import sqlite3
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
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


class CameraScanning(BaseModel):
    enabled: bool
    device: int | None = None


class CameraDeviceRequest(BaseModel):
    device: int


def _selected_camera_device(config: PalantirConfig, raw: str | None) -> int:
    if raw is None:
        return int(config.camera.device)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(config.camera.device)


def _camera_backend() -> int:
    import cv2

    if platform.system().lower() == "windows":
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def _probe_camera(index: int) -> dict:
    import cv2

    cap = cv2.VideoCapture(index, _camera_backend())
    try:
        opened = bool(cap.isOpened())
        if opened:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        else:
            width = height = 0
            fps = 0.0
        return {
            "index": index,
            "name": f"Camera {index}",
            "available": opened,
            "width": width,
            "height": height,
            "fps": round(fps, 1),
        }
    finally:
        cap.release()


@router.get("/camera/scanning", dependencies=[Depends(rate_limit_read)])
async def get_camera_scanning(
    redis: aioredis.Redis = Depends(get_redis),
    config: PalantirConfig = Depends(get_config),
):
    """Return whether the vision service is currently scanning the local camera.

    Reads the persisted mode from Redis.  When the value is missing we
    treat the system as in relay mode (the launcher's default for
    -LocalAudio), so the dashboard's toggle starts in the off position.
    """
    mode = await redis.get("state:camera_mode")
    device = _selected_camera_device(config, await redis.get("state:camera_device"))
    return {"scanning": mode == "local", "mode": mode or "relay", "device": device}


@router.post("/camera/scanning", dependencies=[Depends(rate_limit_write)])
async def set_camera_scanning(
    body: CameraScanning,
    redis: aioredis.Redis = Depends(get_redis),
    config: PalantirConfig = Depends(get_config),
):
    """Toggle the vision service between local cv2 and Pi relay capture.

    Use case (Windows): browser enrollment needs the camera, but
    afterwards the operator wants the vision service to take it back
    over for live face recognition.  This endpoint publishes a
    SYSTEM_CAMERA_MODE message; the vision service swaps its capture
    in-place via _reconfigure_camera() -- no launcher restart needed.
    """
    new_mode = "local" if body.enabled else "relay"
    device = (
        int(body.device)
        if body.device is not None
        else _selected_camera_device(config, await redis.get("state:camera_device"))
    )
    await redis.set("state:camera_device", str(device))
    await publish(redis, Channels.SYSTEM_CAMERA_MODE, {"mode": new_mode, "device": device})
    # Persist so a launcher restart honors the operator's choice.
    await redis.set("state:camera_mode", new_mode)
    return {"scanning": body.enabled, "mode": new_mode, "device": device}


@router.get("/camera/devices", dependencies=[Depends(rate_limit_read)])
async def list_camera_devices(
    max_index: int = 8,
    redis: aioredis.Redis = Depends(get_redis),
    config: PalantirConfig = Depends(get_config),
):
    """Probe local OpenCV camera indexes so the operator can pick a USB camera."""
    max_index = max(0, min(max_index, 16))
    selected = _selected_camera_device(config, await redis.get("state:camera_device"))
    try:
        devices = [_probe_camera(i) for i in range(max_index + 1)]
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="OpenCV is not installed") from exc

    if all(d["index"] != selected for d in devices):
        devices.append(
            {
                "index": selected,
                "name": f"Camera {selected}",
                "available": False,
                "width": 0,
                "height": 0,
                "fps": 0.0,
            }
        )
    for device in devices:
        device["selected"] = device["index"] == selected
    return {"selected_device": selected, "devices": devices}


@router.post("/camera/device", dependencies=[Depends(rate_limit_write)])
async def set_camera_device(
    body: CameraDeviceRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Persist and apply the local camera index used by the vision service."""
    device = int(body.device)
    if device < 0 or device > 16:
        raise HTTPException(status_code=400, detail="Camera device must be 0-16")
    mode = await redis.get("state:camera_mode") or "relay"
    await redis.set("state:camera_device", str(device))
    await publish(redis, Channels.SYSTEM_CAMERA_MODE, {"mode": mode, "device": device})
    return {"device": device, "mode": mode, "scanning": mode == "local"}
