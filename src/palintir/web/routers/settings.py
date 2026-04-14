"""Settings and system configuration API endpoints."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from palintir.models import PrivacyModeEvent
from palintir.redis_client import Channels, Keys, publish
from palintir.web.dependencies import get_redis, verify_auth

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(verify_auth)])


@router.get("/privacy")
async def get_privacy_status(redis: aioredis.Redis = Depends(get_redis)):
    """Get current privacy mode status."""
    enabled = await redis.get(Keys.PRIVACY_MODE) == "1"
    return {"privacy_mode": enabled}


@router.post("/privacy")
async def toggle_privacy(enabled: bool, redis: aioredis.Redis = Depends(get_redis)):
    """Toggle privacy mode on/off."""
    await redis.set(Keys.PRIVACY_MODE, "1" if enabled else "0")
    event = PrivacyModeEvent(enabled=enabled, source="web")
    await publish(redis, Channels.SYSTEM_PRIVACY, event)
    return {"privacy_mode": enabled}


@router.get("/config")
async def get_public_config():
    """Return non-secret config values the UI can display."""
    from palintir.config import load_config

    cfg = load_config()
    return {
        "retention_days": cfg.privacy.data_retention_days,
        "auto_delete_on_unenroll": cfg.privacy.auto_delete_on_unenroll,
        "auth_configured": bool(cfg.auth_token),
        "anthropic_configured": bool(cfg.anthropic_api_key),
        "automation_enabled": cfg.automation.enabled,
        "allow_shell_commands": cfg.automation.allow_shell_commands,
        "camera": {
            "width": cfg.camera.width,
            "height": cfg.camera.height,
            "fps": cfg.camera.fps,
        },
        "engagement": {
            "smoothing_window_seconds": cfg.engagement.smoothing_window_seconds,
        },
    }
