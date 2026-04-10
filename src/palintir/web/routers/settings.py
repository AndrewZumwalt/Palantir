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
