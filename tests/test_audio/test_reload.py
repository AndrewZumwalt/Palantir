"""Tests for audio service reload behavior."""

from __future__ import annotations

from palantir.audio.service import AudioService


class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


class DummySpeakerIdentifier:
    def __init__(self):
        self.reload_count = 0

    def reload_profiles(self) -> None:
        self.reload_count += 1


async def test_audio_reload_reloads_speaker_profiles():
    service = AudioService()
    speaker = DummySpeakerIdentifier()
    service._redis = FakeRedis()
    service._speaker_id = speaker

    async def publish_status(*, healthy: bool) -> None:
        assert healthy is True

    service._publish_status = publish_status

    await service._on_reload({"reload_id": "reload-1", "services": ["audio"]})

    assert speaker.reload_count == 1
