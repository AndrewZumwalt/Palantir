"""Short-term person identity tracking for the vision pipeline.

This is intentionally small: face recognition gives us the identity, and
pose/body detections are only allowed to extend an already-known track.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from palantir.models import BoundingBox


@dataclass(slots=True)
class PersonTrack:
    person_id: str
    name: str
    role: str
    bbox: BoundingBox
    confidence: float
    last_face_at: float
    last_seen_at: float
    source: str = "face"
    body_track_id: int | None = None


class PersonTracker:
    """Keeps recognized identities alive through brief face loss."""

    def __init__(self, hold_seconds: float):
        self.hold_seconds = max(1.0, float(hold_seconds))
        self._tracks: dict[str, PersonTrack] = {}
        self._body_track_to_person: dict[int, str] = {}

    def clear(self) -> None:
        self._tracks.clear()
        self._body_track_to_person.clear()

    def update_face(
        self,
        *,
        person_id: str,
        name: str,
        role: str,
        bbox: BoundingBox,
        confidence: float,
        now: float | None = None,
    ) -> PersonTrack:
        now = time.monotonic() if now is None else now
        existing = self._tracks.get(person_id)
        track = PersonTrack(
            person_id=person_id,
            name=name,
            role=role,
            bbox=bbox,
            confidence=confidence,
            last_face_at=now,
            last_seen_at=now,
            source="face",
            body_track_id=existing.body_track_id if existing else None,
        )
        self._tracks[person_id] = track
        return track

    def update_body(
        self,
        person_id: str,
        bbox: BoundingBox,
        *,
        body_track_id: int | None = None,
        now: float | None = None,
    ) -> PersonTrack | None:
        now = time.monotonic() if now is None else now
        track = self._tracks.get(person_id)
        if track is None or self._is_expired(track, now):
            return None
        track.bbox = bbox
        track.last_seen_at = now
        track.source = "body"
        if body_track_id is not None:
            self.bind_body_track(person_id, body_track_id)
        return track

    def active_tracks(
        self,
        *,
        now: float | None = None,
        exclude: set[str] | None = None,
    ) -> list[PersonTrack]:
        now = time.monotonic() if now is None else now
        excluded = exclude or set()
        return [
            track
            for person_id, track in self._tracks.items()
            if person_id not in excluded and not self._is_expired(track, now)
        ]

    def expire(self, *, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        expired = [
            person_id
            for person_id, track in self._tracks.items()
            if self._is_expired(track, now)
        ]
        for person_id in expired:
            self._tracks.pop(person_id, None)
        return expired

    def get(self, person_id: str) -> PersonTrack | None:
        return self._tracks.get(person_id)

    def remove(self, person_id: str) -> None:
        track = self._tracks.pop(person_id, None)
        if track and track.body_track_id is not None:
            self._body_track_to_person.pop(track.body_track_id, None)

    def bind_body_track(self, person_id: str, body_track_id: int) -> None:
        track = self._tracks.get(person_id)
        if track is None:
            return
        if track.body_track_id is not None and track.body_track_id != body_track_id:
            self._body_track_to_person.pop(track.body_track_id, None)
        track.body_track_id = body_track_id
        self._body_track_to_person[body_track_id] = person_id

    def person_for_body_track(
        self,
        body_track_id: int,
        *,
        now: float | None = None,
    ) -> str | None:
        now = time.monotonic() if now is None else now
        person_id = self._body_track_to_person.get(body_track_id)
        if not person_id:
            return None
        track = self._tracks.get(person_id)
        if track is None or self._is_expired(track, now):
            self._body_track_to_person.pop(body_track_id, None)
            return None
        return person_id

    def match_body_bbox(
        self,
        bbox: BoundingBox,
        *,
        now: float | None = None,
        allow_single_active: bool = False,
    ) -> str | None:
        now = time.monotonic() if now is None else now
        active = [
            track
            for track in self._tracks.values()
            if not self._is_expired(track, now)
        ]
        if allow_single_active and len(active) == 1:
            return active[0].person_id

        best: tuple[float, str] | None = None
        for track in active:
            iou = _bbox_iou(track.bbox, bbox)
            dist = _center_distance(track.bbox, bbox)
            diag = max(_bbox_diag(track.bbox), _bbox_diag(bbox))
            if iou <= 0.05 and dist > max(180.0, diag * 0.75):
                continue
            score = iou * 2.0 + max(0.0, 1.0 - dist / max(1.0, diag))
            if best is None or score > best[0]:
                best = (score, track.person_id)

        return best[1] if best else None

    def confidence_for(self, track: PersonTrack, *, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        age = max(0.0, now - track.last_seen_at)
        decay = max(0.25, 1.0 - (age / self.hold_seconds))
        return round(max(0.15, track.confidence * decay), 4)

    def _is_expired(self, track: PersonTrack, now: float) -> bool:
        return now - track.last_seen_at > self.hold_seconds


def _bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    ax2 = a.x + a.width
    ay2 = a.y + a.height
    bx2 = b.x + b.width
    by2 = b.y + b.height
    ix1 = max(a.x, b.x)
    iy1 = max(a.y, b.y)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    area_a = max(1, a.width * a.height)
    area_b = max(1, b.width * b.height)
    return intersection / (area_a + area_b - intersection)


def _center_distance(a: BoundingBox, b: BoundingBox) -> float:
    ax = a.x + a.width / 2
    ay = a.y + a.height / 2
    bx = b.x + b.width / 2
    by = b.y + b.height / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _bbox_diag(bbox: BoundingBox) -> float:
    return (bbox.width**2 + bbox.height**2) ** 0.5
