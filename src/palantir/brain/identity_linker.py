"""Cross-modal identity linking: connects voice to face.

The core differentiator of Palantir. When someone speaks, this module
resolves who they are (via voice) and where they are in the camera
frame (via face), enabling questions like "what am I wearing?"
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import redis.asyncio as aioredis
import structlog

from palantir.models import BoundingBox, VisiblePerson
from palantir.redis_client import Keys

logger = structlog.get_logger()


@dataclass
class LinkedIdentity:
    """A fully resolved identity: who is speaking and where they are."""
    person_id: str | None = None
    name: str | None = None
    role: str | None = None

    # Voice identification
    voice_confidence: float = 0.0
    voice_matched: bool = False

    # Visual location
    bbox: BoundingBox | None = None
    visually_located: bool = False
    location_source: str = ""  # "live", "last_known", "inferred"

    @property
    def fully_linked(self) -> bool:
        """True if both voice and visual identity are resolved."""
        return self.voice_matched and self.visually_located


@dataclass
class LastKnownPosition:
    """Cached position of a person in the camera frame."""
    person_id: str
    bbox: BoundingBox
    timestamp: float = field(default_factory=time.monotonic)


class IdentityLinker:
    """Links voice identity to visual identity in real-time.

    Resolution strategy:
    1. Voice embedding -> match to enrolled voice profile -> person_id
    2. Check if person_id is currently visible in camera
    3. If visible: exact match (strong link)
    4. If not visible: use last-known position (with staleness timeout)
    5. If voice ambiguous: use visible faces to narrow candidates
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        staleness_timeout: float = 10.0,
    ):
        self._redis = redis
        self._staleness_timeout = staleness_timeout

        # Cache of last known positions
        self._last_known: dict[str, LastKnownPosition] = {}

    async def link(
        self,
        speaker_person_id: str | None,
        speaker_name: str | None,
        speaker_confidence: float = 0.0,
    ) -> LinkedIdentity:
        """Resolve a speaker's full identity including visual location.

        Args:
            speaker_person_id: Person ID from voice recognition (or None).
            speaker_name: Name from voice recognition (or None).
            speaker_confidence: Voice match confidence score.

        Returns:
            LinkedIdentity with both voice and visual information.
        """
        identity = LinkedIdentity(
            person_id=speaker_person_id,
            name=speaker_name,
            voice_confidence=speaker_confidence,
            voice_matched=speaker_person_id is not None,
        )

        if not speaker_person_id:
            # Voice didn't match anyone - try to infer from visible faces
            identity = await self._infer_from_visible(identity)
            return identity

        # We know who's speaking. Find them in the camera.
        identity = await self._locate_in_camera(identity)
        return identity

    async def _locate_in_camera(self, identity: LinkedIdentity) -> LinkedIdentity:
        """Find a known speaker's visual position in the camera frame."""
        person_id = identity.person_id

        # Strategy 1: Check if currently visible
        visible_data = await self._redis.hget(Keys.VISIBLE_PERSONS, person_id)
        if visible_data:
            try:
                visible = VisiblePerson.model_validate_json(visible_data)
                identity.bbox = visible.bbox
                identity.role = visible.role
                identity.visually_located = True
                identity.location_source = "live"

                # Update last known position
                self._last_known[person_id] = LastKnownPosition(
                    person_id=person_id,
                    bbox=visible.bbox,
                )

                logger.debug(
                    "identity_linked_live",
                    person_id=person_id,
                    name=identity.name,
                )
                return identity
            except Exception:
                pass

        # Strategy 2: Use last known position (if not too stale)
        last = self._last_known.get(person_id)
        if last and (time.monotonic() - last.timestamp) < self._staleness_timeout:
            identity.bbox = last.bbox
            identity.visually_located = True
            identity.location_source = "last_known"

            logger.debug(
                "identity_linked_last_known",
                person_id=person_id,
                age_seconds=round(time.monotonic() - last.timestamp, 1),
            )
            return identity

        # Strategy 3: Person is known by voice but not currently visible
        logger.debug(
            "identity_voice_only",
            person_id=person_id,
            name=identity.name,
        )
        return identity

    async def _infer_from_visible(self, identity: LinkedIdentity) -> LinkedIdentity:
        """Try to infer speaker identity from currently visible faces.

        If only one unmatched person is visible and the voice didn't match
        anyone, they might be the speaker (useful for unenrolled people).
        """
        visible_data = await self._redis.hgetall(Keys.VISIBLE_PERSONS)
        if not visible_data:
            return identity

        # If exactly one person is visible, they're probably the speaker
        if len(visible_data) == 1:
            person_id, data_str = next(iter(visible_data.items()))
            try:
                visible = VisiblePerson.model_validate_json(data_str)
                identity.person_id = visible.person_id
                identity.name = visible.name
                identity.role = visible.role
                identity.bbox = visible.bbox
                identity.visually_located = True
                identity.location_source = "inferred"

                logger.debug(
                    "identity_inferred_single_visible",
                    person_id=person_id,
                    name=visible.name,
                )
            except Exception:
                pass

        return identity

    def update_position(self, person_id: str, bbox: BoundingBox) -> None:
        """Update the last known position of a person (called by vision service)."""
        self._last_known[person_id] = LastKnownPosition(
            person_id=person_id,
            bbox=bbox,
        )

    def clear_stale_positions(self) -> None:
        """Remove positions older than the staleness timeout."""
        now = time.monotonic()
        stale = [
            pid for pid, pos in self._last_known.items()
            if now - pos.timestamp > self._staleness_timeout
        ]
        for pid in stale:
            del self._last_known[pid]
