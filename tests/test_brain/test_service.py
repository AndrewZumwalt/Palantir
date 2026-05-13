"""Tests for BrainService helpers."""

from __future__ import annotations

import cv2
import numpy as np

from palantir.brain.identity_linker import IdentityLinker
from palantir.brain.service import BrainService
from palantir.models import BoundingBox, PersonRole, VisiblePerson
from palantir.redis_client import Keys


class FakeRedis:
    def __init__(self, value):
        self.value = value
        self.keys: list[str] = []

    async def get(self, key: str):
        self.keys.append(key)
        return self.value


class FakeVisibleRedis:
    async def hgetall(self, key: str):
        return {
            "p1": VisiblePerson(
                person_id="p1",
                name="Andrew",
                role=PersonRole.STUDENT,
                bbox=BoundingBox(x=1, y=2, width=3, height=4),
            ).model_dump_json()
        }


async def test_latest_frame_uses_binary_redis_client():
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok is True

    text_redis = FakeRedis(None)
    binary_redis = FakeRedis(jpeg.tobytes())
    service = BrainService()
    service._redis = text_redis
    service._binary_redis = binary_redis

    decoded = await service._get_latest_frame_async()

    assert decoded.shape[:2] == (2, 3)
    assert binary_redis.keys == [Keys.LATEST_FRAME]
    assert text_redis.keys == [Keys.LATEST_FRAME_META]


async def test_identity_linker_keeps_unknown_speaker_unknown_by_default():
    linker = IdentityLinker(FakeVisibleRedis())

    linked = await linker.link(None, None, 0.0)

    assert linked.person_id is None
    assert linked.name is None
    assert linked.voice_matched is False
    assert linked.visually_located is False


async def test_identity_linker_visible_fallback_is_explicit():
    linker = IdentityLinker(FakeVisibleRedis())

    linked = await linker.link(None, None, 0.0, allow_visible_fallback=True)

    assert linked.person_id == "p1"
    assert linked.name == "Andrew"
    assert linked.location_source == "inferred"


def test_last_speaker_fallback_requires_fresh_timestamp():
    assert BrainService._last_speaker_is_fresh({"timestamp": 1}) is False
    assert BrainService._last_speaker_is_fresh({"timestamp": "bad"}) is False
