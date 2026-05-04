"""Tests for BrainService helpers."""

from __future__ import annotations

import cv2
import numpy as np

from palantir.brain.service import BrainService
from palantir.redis_client import Keys


class FakeRedis:
    def __init__(self, value):
        self.value = value
        self.keys: list[str] = []

    async def get(self, key: str):
        self.keys.append(key)
        return self.value


async def test_latest_frame_uses_binary_redis_client():
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok is True

    text_redis = FakeRedis("not binary")
    binary_redis = FakeRedis(jpeg.tobytes())
    service = BrainService()
    service._redis = text_redis
    service._binary_redis = binary_redis

    decoded = await service._get_latest_frame_async()

    assert decoded.shape[:2] == (2, 3)
    assert binary_redis.keys == [Keys.LATEST_FRAME]
    assert text_redis.keys == []
