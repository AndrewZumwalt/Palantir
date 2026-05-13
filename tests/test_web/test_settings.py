from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from palantir.redis_client import Channels
from palantir.settings_store import get_setting
from palantir.web.routers import settings


class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


async def test_set_tts_settings_persists_voice_and_reloads_tts(temp_db):
    redis = FakeRedis()

    result = await settings.set_tts_settings(
        settings.TTSSettingsRequest(voice="en-US-GuyNeural"),
        db=temp_db,
        redis=redis,
    )

    assert result["voice"] == "en-US-GuyNeural"
    assert get_setting(temp_db, "tts_voice") == "en-US-GuyNeural"
    channel, payload = redis.published[0]
    message = json.loads(payload)
    assert channel == Channels.SYSTEM_RELOAD
    assert message["services"] == ["tts"]
    assert message["reason"] == "tts_voice_updated"


async def test_set_tts_settings_rejects_unknown_voice(temp_db):
    with pytest.raises(HTTPException) as exc:
        await settings.set_tts_settings(
            settings.TTSSettingsRequest(voice="not-a-real-voice"),
            db=temp_db,
            redis=FakeRedis(),
        )

    assert exc.value.status_code == 400
