"""Tests for enrollment lifecycle behavior."""

from __future__ import annotations

import json
from pathlib import Path

from palantir.redis_client import Channels, Keys
from palantir.web.routers import enrollment


class FakeRedis:
    def __init__(self):
        self.hdel_calls: list[tuple[str, str]] = []
        self.srem_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, ...]] = []
        self.published: list[tuple[str, str]] = []

    async def hdel(self, key: str, field: str) -> None:
        self.hdel_calls.append((key, field))

    async def srem(self, key: str, member: str) -> None:
        self.srem_calls.append((key, member))

    async def delete(self, *keys: str) -> None:
        self.delete_calls.append(keys)

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def test_next_sample_index_uses_highest_existing_index(tmp_path):
    (tmp_path / "face_000.jpg").write_bytes(b"first")
    (tmp_path / "face_002.jpg").write_bytes(b"third")

    assert enrollment._next_sample_index(tmp_path, "face_*.jpg", "face_") == 3


async def test_unenroll_clears_caches_and_publishes_reload(temp_db, config, tmp_path):
    person_id = "person-1"
    config.enrollment_path = str(tmp_path / "enrollments")
    enrollment_dir = Path(config.enrollment_path) / person_id
    enrollment_dir.mkdir(parents=True)
    (enrollment_dir / "face_000.jpg").write_bytes(b"sample")

    temp_db.execute(
        "INSERT INTO persons (id, name, role) VALUES (?, ?, ?)",
        (person_id, "Ada", "student"),
    )
    temp_db.commit()

    enrollment._face_recognizer = object()
    enrollment._speaker_identifier = object()
    redis = FakeRedis()

    result = await enrollment.unenroll_person(
        person_id,
        db=temp_db,
        config=config,
        redis=redis,
    )

    assert result == {"status": "deleted", "person_id": person_id}
    assert not enrollment_dir.exists()
    assert temp_db.execute("SELECT id FROM persons WHERE id = ?", (person_id,)).fetchone() is None
    assert enrollment._face_recognizer is None
    assert enrollment._speaker_identifier is None
    assert redis.hdel_calls == [(Keys.VISIBLE_PERSONS, person_id)]
    assert redis.srem_calls == [(Keys.PRESENT_PERSONS, person_id)]
    assert redis.delete_calls == [("state:last_speaker",)]

    channel, payload = redis.published[0]
    message = json.loads(payload)
    assert channel == Channels.SYSTEM_RELOAD
    assert message["services"] == ["vision", "audio", "brain"]
    assert message["reason"] == "person_unenrolled"
    assert message["person_id"] == person_id
