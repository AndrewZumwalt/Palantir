from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from palantir.models import BoundingBox, DetectedObject
from palantir.vision.engagement import EngagementClassifier
from palantir.vision.person_tracker import PersonTracker
from palantir.vision.service import VisionService


class FakeArray:
    def __init__(self, values):
        self._values = np.array(values)

    def cpu(self):
        return self

    def numpy(self):
        return self._values

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        return self._values[index]


class FakeBoxes(list):
    def __init__(self, boxes, ids=None):
        super().__init__(boxes)
        self.id = ids


class FakeRedis:
    def __init__(self):
        self.hsets: list[tuple[str, str, str]] = []
        self.sadds: list[tuple[str, str]] = []

    async def hset(self, key, field, value):
        self.hsets.append((key, field, value))

    async def sadd(self, key, value):
        self.sadds.append((key, value))


def test_person_tracker_holds_identity_then_expires():
    tracker = PersonTracker(hold_seconds=10)
    face_box = BoundingBox(x=10, y=20, width=40, height=50)

    track = tracker.update_face(
        person_id="p1",
        name="Andrew",
        role="student",
        bbox=face_box,
        confidence=0.91,
        now=100.0,
    )

    assert track.source == "face"
    assert [t.person_id for t in tracker.active_tracks(now=105.0)] == ["p1"]
    assert tracker.active_tracks(now=111.0) == []
    assert tracker.expire(now=111.0) == ["p1"]


def test_person_tracker_body_detection_extends_identity():
    tracker = PersonTracker(hold_seconds=10)
    tracker.update_face(
        person_id="p1",
        name="Andrew",
        role="student",
        bbox=BoundingBox(x=10, y=20, width=40, height=50),
        confidence=0.91,
        now=100.0,
    )

    body = BoundingBox(x=5, y=15, width=120, height=300)
    track = tracker.update_body("p1", body, now=108.0)

    assert track is not None
    assert track.source == "body"
    assert track.bbox == body
    assert [t.person_id for t in tracker.active_tracks(now=117.0)] == ["p1"]
    assert tracker.active_tracks(now=119.0) == []


def test_person_tracker_resolves_persistent_body_track_after_movement():
    tracker = PersonTracker(hold_seconds=10)
    tracker.update_face(
        person_id="p1",
        name="Andrew",
        role="student",
        bbox=BoundingBox(x=10, y=20, width=40, height=50),
        confidence=0.91,
        now=100.0,
    )

    tracker.bind_body_track("p1", 42)
    assert tracker.person_for_body_track(42, now=105.0) == "p1"

    moved = BoundingBox(x=500, y=80, width=150, height=360)
    track = tracker.update_body("p1", moved, body_track_id=42, now=106.0)

    assert track is not None
    assert track.bbox == moved
    assert tracker.person_for_body_track(42, now=115.0) == "p1"


def test_person_tracker_single_active_body_fallback():
    tracker = PersonTracker(hold_seconds=10)
    tracker.update_face(
        person_id="p1",
        name="Andrew",
        role="student",
        bbox=BoundingBox(x=10, y=20, width=40, height=50),
        confidence=0.91,
        now=100.0,
    )

    moved = BoundingBox(x=500, y=80, width=150, height=360)

    assert tracker.match_body_bbox(moved, now=101.0) is None
    assert tracker.match_body_bbox(
        moved,
        now=101.0,
        allow_single_active=True,
    ) == "p1"


async def test_vision_updates_track_from_object_person_box():
    tracker = PersonTracker(hold_seconds=10)
    tracker.update_face(
        person_id="p1",
        name="Andrew",
        role="student",
        bbox=BoundingBox(x=10, y=20, width=40, height=50),
        confidence=0.91,
        now=100.0,
    )

    service = object.__new__(VisionService)
    service._redis = FakeRedis()
    service._attendance_tracker = None
    service._last_visible_at = {}
    service._person_tracker = tracker

    moved = BoundingBox(x=500, y=80, width=150, height=360)
    await service._update_tracks_from_person_objects(
        [
            DetectedObject(
                label="person",
                confidence=0.88,
                bbox=moved,
                location_description=None,
            )
        ],
        now=101.0,
    )

    track = tracker.get("p1")
    assert track is not None
    assert track.source == "body"
    assert track.bbox == moved
    assert service._last_visible_at["p1"] > 0


def test_pose_match_accepts_face_inside_body_box():
    classifier = object.__new__(EngagementClassifier)
    result = SimpleNamespace(
        boxes=FakeBoxes([
            SimpleNamespace(
                xyxy=[FakeArray([250, 90, 450, 700])],
            )
        ])
    )
    known = {"p1": BoundingBox(x=300, y=100, width=80, height=80)}

    assert classifier._match_to_person(result, 0, known) == "p1"


def test_pose_track_id_is_extracted_when_yolo_tracks():
    classifier = object.__new__(EngagementClassifier)
    result = SimpleNamespace(
        boxes=FakeBoxes(
            [
                SimpleNamespace(
                    xyxy=[FakeArray([250, 90, 450, 700])],
                )
            ],
            ids=FakeArray([42]),
        )
    )

    assert classifier._pose_track_id(result, 0) == 42
