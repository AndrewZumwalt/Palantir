"""Settings and system configuration API endpoints."""

from __future__ import annotations

import secrets as _secrets
import sqlite3

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from palantir.models import PrivacyModeEvent
from palantir.redis_client import Channels, Keys, publish
from palantir.settings_store import KNOWN_SETTINGS, get_setting, set_setting
from palantir.web.dependencies import get_db, get_redis, verify_auth
from palantir.web.rate_limit import rate_limit_read, rate_limit_write

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(verify_auth)])


def _redact(value: str | None) -> str | None:
    """Return a fingerprint suitable for the UI -- never the raw key."""
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"...{value[-4:]}"


@router.get("/privacy", dependencies=[Depends(rate_limit_read)])
async def get_privacy_status(redis: aioredis.Redis = Depends(get_redis)):
    """Get current privacy mode status."""
    enabled = await redis.get(Keys.PRIVACY_MODE) == "1"
    return {"privacy_mode": enabled}


@router.post("/privacy", dependencies=[Depends(rate_limit_write)])
async def toggle_privacy(enabled: bool, redis: aioredis.Redis = Depends(get_redis)):
    """Toggle privacy mode on/off."""
    await redis.set(Keys.PRIVACY_MODE, "1" if enabled else "0")
    event = PrivacyModeEvent(enabled=enabled, source="web")
    await publish(redis, Channels.SYSTEM_PRIVACY, event)
    return {"privacy_mode": enabled}


@router.get("/config", dependencies=[Depends(rate_limit_read)])
async def get_public_config(db: sqlite3.Connection = Depends(get_db)):
    """Return non-secret config values the UI can display."""
    from palantir.config import load_config

    cfg = load_config()
    # DB-stored settings override env vars -- so the dashboard shows the
    # truth about which key the brain will actually use after reload.
    db_anthropic = get_setting(db, "anthropic_api_key")
    db_groq = get_setting(db, "groq_api_key")
    anthropic_key = db_anthropic or cfg.anthropic_api_key
    groq_key = db_groq or cfg.groq_api_key

    llm_provider = (
        "anthropic" if anthropic_key else ("groq" if groq_key else "none")
    )

    # Capability probe: without insightface installed the face-enrollment
    # endpoint returns 503, so the wizard can warn the operator up front.
    try:
        from palantir.vision.face_detector import _INSIGHTFACE_AVAILABLE
        face_detection_available = bool(_INSIGHTFACE_AVAILABLE)
    except Exception:
        face_detection_available = False

    return {
        "retention_days": cfg.privacy.data_retention_days,
        "auto_delete_on_unenroll": cfg.privacy.auto_delete_on_unenroll,
        "auth_configured": bool(cfg.auth_token),
        "anthropic_configured": bool(anthropic_key),
        "groq_configured": bool(groq_key),
        "anthropic_source": "db" if db_anthropic else ("env" if cfg.anthropic_api_key else None),
        "groq_source": "db" if db_groq else ("env" if cfg.groq_api_key else None),
        "anthropic_hint": _redact(anthropic_key),
        "groq_hint": _redact(groq_key),
        "llm_provider": llm_provider,
        "automation_enabled": cfg.automation.enabled,
        "allow_shell_commands": cfg.automation.allow_shell_commands,
        "face_detection_available": face_detection_available,
        "camera": {
            "width": cfg.camera.width,
            "height": cfg.camera.height,
            "fps": cfg.camera.fps,
        },
        "engagement": {
            "smoothing_window_seconds": cfg.engagement.smoothing_window_seconds,
        },
    }


# ---------- API key management --------------------------------------------


class ApiKeysRequest(BaseModel):
    """Set or clear API keys.  Omit a field to leave it unchanged.
    Pass an empty string to delete it."""

    anthropic_api_key: str | None = Field(default=None, max_length=512)
    groq_api_key: str | None = Field(default=None, max_length=512)


@router.post("/api_keys", dependencies=[Depends(rate_limit_write)])
async def set_api_keys(
    req: ApiKeysRequest,
    db: sqlite3.Connection = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Persist Anthropic / Groq API keys to the SQLite settings table.

    Sends a SYSTEM_RELOAD signal so the brain service rebuilds its
    LLMClient with the new keys (no service restart needed).
    """
    changed: list[str] = []
    for field in ("anthropic_api_key", "groq_api_key"):
        if field not in KNOWN_SETTINGS:
            continue
        value = getattr(req, field)
        if value is None:
            continue  # not in the request body -> leave alone
        # Strip whitespace -- pasting from a website often grabs trailing
        # newlines that confuse the SDK auth headers.
        value = value.strip()
        set_setting(db, field, value or None)
        changed.append(field)

    if not changed:
        raise HTTPException(
            status_code=400,
            detail="No recognised keys in request body. "
            f"Allowed: {sorted(KNOWN_SETTINGS)}",
        )

    # Tell the brain service to re-read the keys.  Targeted reload --
    # other services don't care about API keys.  Must match the shape
    # `handle_reload_request` expects: {reload_id, services}.  Anything
    # else and the receiving service silently ignores the message.
    reload_id = _secrets.token_hex(8)
    await publish(
        redis,
        Channels.SYSTEM_RELOAD,
        {
            "reload_id": reload_id,
            "services": ["brain"],
            "reason": "api_keys_updated",
        },
    )

    return {"updated": changed, "reload_id": reload_id}
