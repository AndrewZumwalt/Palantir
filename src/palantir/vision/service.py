"""Vision service: camera capture, face detection/recognition, object detection, engagement.

This is the main entry point for the palantir-vision systemd service.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from dataclasses import replace

import structlog

from palantir.config import load_config
from palantir.db import init_db
from palantir.logging import setup_logging
from palantir.models import (
    DetectedFace,
    DetectedObject,
    Event,
    EventType,
    PrivacyModeEvent,
    ServiceStatus,
    VisiblePerson,
)
from palantir.preflight import log_and_check, validate_for
from palantir.redis_client import (
    Channels,
    Keys,
    Subscriber,
    create_binary_redis,
    create_redis,
    publish,
)
from palantir.reload import handle_reload_request

from .capture import CameraCapture, create_camera_capture
from .person_tracker import PersonTrack, PersonTracker

logger = structlog.get_logger()

# Conditional imports for ML components
try:
    from .face_detector import FaceDetector
    from .face_recognizer import FaceRecognizer

    _FACE_AVAILABLE = True
except ImportError:
    _FACE_AVAILABLE = False

try:
    from .object_detector import ObjectDetector

    _OBJECT_AVAILABLE = True
except ImportError:
    _OBJECT_AVAILABLE = False

try:
    from .engagement import EngagementClassifier

    _ENGAGEMENT_AVAILABLE = True
except ImportError:
    _ENGAGEMENT_AVAILABLE = False


class VisionService:
    """Orchestrates the vision pipeline with tiered frame processing."""

    def __init__(self):
        self._config = load_config()
        self._camera: CameraCapture | None = None
        self._current_camera_mode: str = "relay"  # updated by _reconfigure_camera
        self._current_camera_device: int = int(self._config.camera.device)
        self._redis = None
        self._binary_redis = None  # only created in relay mode
        self._subscriber: Subscriber | None = None
        self._db = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()
        self._last_frame_count = 0
        self._last_live_frame_publish = 0.0
        self._last_latest_frame_published_at: float | None = None

        # Face detection/recognition
        self._face_detector: FaceDetector | None = None
        self._face_recognizer: FaceRecognizer | None = None

        # Object detection
        self._object_detector: ObjectDetector | None = None

        # Engagement classification
        self._engagement_classifier: EngagementClassifier | None = None

        # Attendance tracking
        self._attendance_tracker = None
        self._last_exit_check = time.monotonic()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Recognition tags a face once; this tracker keeps that identity alive
        # through short face-loss gaps and lets pose boxes extend it when the
        # person turns away from the camera.
        self._person_tracker = PersonTracker(
            hold_seconds=max(8.0, self._config.identity.identity_staleness_seconds)
        )

        # Per-person "last tracked this frame or recently" timestamps. The
        # tracker handles recognized people; this map also lets us prune any
        # legacy/stale Redis entries that predate the current process.
        self._last_visible_at: dict[str, float] = {}
        self._visible_timeout_seconds: float = 3.0

    async def start(self) -> None:
        preflight = validate_for("vision", self._config)
        if not log_and_check(preflight, fatal_on_error=False):
            raise RuntimeError("vision preflight failed")

        self._loop = asyncio.get_running_loop()
        self._redis = await create_redis(self._config)
        self._db = init_db(self._config)

        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Clear visible/present state inherited from a previous launcher run.
        # Memurai keeps these keys across restarts, so without this wipe the
        # brain would keep saying "Andrew is here" after a fresh start even
        # before the camera sees a single frame.  The prune logic in
        # _detect_faces only removes entries that THIS process knows about
        # via _last_visible_at -- it can't tell the difference between a
        # stale entry left by a dead process and a legitimate one.
        try:
            await self._redis.delete(Keys.VISIBLE_PERSONS, Keys.PRESENT_PERSONS)
            self._person_tracker.clear()
            self._last_visible_at.clear()
            logger.info("visible_persons_cleared_on_startup")
        except Exception:
            logger.warning("visible_persons_clear_failed", exc_info=True)

        # Initialize face detection
        if _FACE_AVAILABLE:
            self._face_detector = FaceDetector()
            self._face_recognizer = FaceRecognizer(
                self._db,
                match_threshold=self._config.identity.face_match_threshold,
            )

            # Initialize attendance tracker
            from palantir.eventlog.attendance import AttendanceTracker
            self._attendance_tracker = AttendanceTracker(
                self._db,
                exit_timeout_seconds=self._config.attendance.exit_timeout_seconds,
            )
            # Auto-start a session
            self._attendance_tracker.start_session()

        # Initialize object detection
        if _OBJECT_AVAILABLE:
            self._object_detector = ObjectDetector(
                frame_width=self._config.camera.width,
                frame_height=self._config.camera.height,
            )

        # Initialize engagement classifier
        if _ENGAGEMENT_AVAILABLE:
            # Convert seconds to frame count: engagement runs every N frames at camera fps
            eng_fps = self._config.camera.fps / self._config.camera.engagement_interval
            window_frames = max(10, int(self._config.engagement.smoothing_window_seconds * eng_fps))
            self._engagement_classifier = EngagementClassifier(
                smoothing_window=window_frames,
                phone_threshold=self._config.engagement.phone_confidence_threshold,
                sleep_stillness_seconds=self._config.engagement.sleep_stillness_seconds,
                frame_height=self._config.camera.height,
            )

        # Subscribe to events
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        self._subscriber.on(Channels.SYSTEM_RELOAD, self._on_reload)
        self._subscriber.on(Channels.SYSTEM_CAMERA_MODE, self._on_camera_mode_change)
        await self._subscriber.start()

        # Start camera (local USB camera OR Pi relay over Redis).  The
        # mode persists in Redis so that subsequent restarts honor whatever
        # the operator last picked from the dashboard -- otherwise toggling
        # to "local" for room scanning would silently revert to "relay" on
        # the next launcher restart.
        startup_mode = await self._redis.get("state:camera_mode") or self._config.relay.mode
        await self._reconfigure_camera(startup_mode)

        self._running = True
        logger.info(
            "vision_service_started",
            privacy_mode=self._privacy_mode,
            face_detection=_FACE_AVAILABLE and self._face_detector is not None,
            enrolled_faces=self._face_recognizer.enrolled_count if self._face_recognizer else 0,
            engagement=_ENGAGEMENT_AVAILABLE and self._engagement_classifier is not None,
        )
        await self._publish_status(healthy=True)

    async def _process_frame(self) -> None:
        if not self._camera or self._privacy_mode:
            return

        frame, frame_num = self._camera.get_frame()
        if frame is None or frame_num == self._last_frame_count:
            return

        self._last_frame_count = frame_num
        cam_cfg = self._config.camera

        # Live feed: keep LATEST_FRAME warm for the troubleshooting view
        # at /api/vision/stream.  Capped at ~15 Hz so a 60 fps source
        # doesn't drown the Redis client in JPEG encodes.  In relay mode
        # RelayCameraCapture already updates this on every incoming
        # frame, so we skip here to avoid re-encoding what the Pi sent.
        # MUST read _current_camera_mode (not self._config.relay.mode) --
        # the latter is the launcher's startup value and never updates
        # after a runtime "START SCANNING" toggle, which would leave the
        # Camera page showing a black box forever.
        if self._current_camera_mode != "relay":
            now = time.monotonic()
            if now - self._last_live_frame_publish >= 1.0 / 15:
                self._last_live_frame_publish = now
                import cv2 as _cv2
                _, jpeg_buf = _cv2.imencode(
                    ".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 80]
                )
                await self._redis.set(
                    Keys.LATEST_FRAME, jpeg_buf.tobytes(), ex=30
                )
                published_at = time.time()
                self._last_latest_frame_published_at = published_at
                await self._redis.set(
                    Keys.LATEST_FRAME_META,
                    json.dumps(
                        {
                            "frame_number": frame_num,
                            "published_at": published_at,
                            "mode": self._current_camera_mode,
                            "device": self._camera_device(),
                        }
                    ),
                    ex=30,
                )
        else:
            self._last_latest_frame_published_at = time.time()

        # Tier 1: Face detection (every frame)
        if frame_num % cam_cfg.face_detection_interval == 0:
            await self._detect_faces(frame)

        # Tier 2: Engagement/body analysis. Run faster while a tagged person
        # is being tracked so the overlay follows movement after face loss.
        engagement_interval = max(1, cam_cfg.engagement_interval)
        if self._person_tracker.active_tracks(now=time.monotonic()):
            engagement_interval = min(engagement_interval, 2)
        if frame_num % engagement_interval == 0:
            await self._analyze_engagement(frame)

        # If pose tracking is unavailable or loses the body, normal YOLO
        # person boxes still give us a moving body rectangle to keep a
        # recognized identity attached while the face is covered.
        object_interval = max(1, cam_cfg.object_detection_interval)
        should_detect_objects = frame_num % object_interval == 0
        should_track_people_with_objects = (
            bool(self._person_tracker.active_tracks(now=time.monotonic()))
            and frame_num % 2 == 0
        )
        if should_track_people_with_objects and not should_detect_objects:
            await self._track_person_objects(frame)

        # Tier 3: Object detection (every 30th frame).  LATEST_FRAME is
        # already warm above for cloud-vision lookups, so we only run
        # the detector here.
        if should_detect_objects:
            await self._detect_objects(frame, update_person_tracks=True)

        # Periodic exit check for attendance.  The original 30s cadence
        # made the dashboard's Present panel feel broken: even with a 10s
        # exit_timeout, a person who walked out of frame stayed marked
        # present for up to 30 + 10 = 40 seconds before the next check
        # closed their record.  Polling every 2s drops worst-case
        # detection latency to ~12s, which feels live during a demo.
        now = time.monotonic()
        if now - self._last_exit_check >= 2:
            self._last_exit_check = now
            await self._check_attendance_exits()

    async def _detect_faces(self, frame) -> None:
        """Run face detection and recognition on a frame."""
        if not self._face_detector or not self._face_detector.is_available:
            return

        # Run detection in executor (CPU-bound)
        detections = await self._loop.run_in_executor(
            None, self._face_detector.detect, frame
        )

        # Always publish, even with zero face detections: the dashboard's
        # "Subjects in frame" count subscribes to this channel and stays
        # sticky on the last non-empty value if we skip empty frames.
        detected_faces: list[DetectedFace] = []
        recognized_ids: set[str] = set()
        detection_boxes = [det.bbox for det in detections]
        now = time.monotonic()

        for det in detections:
            face_msg = DetectedFace(
                bbox=det.bbox,
                confidence=det.det_score,
                source="face",
            )

            # Try to recognize the face
            if det.embedding is not None and self._face_recognizer:
                result = self._face_recognizer.recognize(det.embedding)
                if result.matched and result.person_id:
                    face_msg.person_id = result.person_id
                    face_msg.name = result.name
                    face_msg.confidence = result.confidence

                    track = self._person_tracker.update_face(
                        person_id=result.person_id,
                        name=result.name or result.person_id,
                        role=result.role or "student",
                        bbox=det.bbox,
                        confidence=result.confidence,
                        now=now,
                    )
                    recognized_ids.add(result.person_id)
                    await self._mark_track_visible(track, update_attendance=True)

            detected_faces.append(face_msg)

        # If the face is gone but we recently tagged the person, keep the
        # identity visible. Pose/body tracking refreshes the bbox in
        # _analyze_engagement; otherwise this decays out after hold_seconds.
        for track in self._person_tracker.active_tracks(
            now=now,
            exclude=recognized_ids,
        ):
            if any(self._bbox_iou(track.bbox, bbox) > 0.35 for bbox in detection_boxes):
                continue
            detected_faces.append(
                DetectedFace(
                    person_id=track.person_id,
                    name=track.name,
                    confidence=self._person_tracker.confidence_for(track, now=now),
                    bbox=track.bbox,
                    source="body" if track.source == "body" else "track",
                )
            )
            await self._mark_track_visible(track, update_attendance=True)

        expired_ids = self._person_tracker.expire(now=now)
        if expired_ids:
            await self._remove_visible_people(expired_ids)

        # Publish detected faces
        faces_data = [f.model_dump() for f in detected_faces]
        await publish(self._redis, Channels.VISION_FACES, {"faces": faces_data})

        # Prune visible_persons entries the camera hasn't actually seen
        # within VISIBLE_TIMEOUT.  Without this, every face we ever match
        # sticks in the hash forever and the brain's context keeps saying
        # "Andrew is here" long after Andrew walked out -- so it gives
        # wrong answers like "Andrew is wearing X" when someone else is
        # standing in frame.  Attendance records (the SQL side) keep
        # their own slower 5-minute exit timeout; that's deliberately
        # separate so a brief look-away doesn't log a phantom exit.
        await self._prune_stale_visible()

    async def _mark_track_visible(
        self,
        track: PersonTrack,
        *,
        update_attendance: bool,
    ) -> None:
        """Refresh Redis/dashboard state for a known tracked person."""
        if not self._redis:
            return

        if update_attendance and self._attendance_tracker:
            is_new = self._attendance_tracker.person_seen(track.person_id)
            if is_new:
                event = Event(
                    type=EventType.PERSON_ENTERED,
                    person_id=track.person_id,
                    data={"name": track.name},
                )
                await publish(self._redis, Channels.EVENTS_LOG, event)

        visible = VisiblePerson(
            person_id=track.person_id,
            name=track.name,
            role=track.role,
            bbox=track.bbox,
        )
        await self._redis.hset(
            Keys.VISIBLE_PERSONS,
            track.person_id,
            visible.model_dump_json(),
        )
        await self._redis.sadd(Keys.PRESENT_PERSONS, track.person_id)
        self._last_visible_at[track.person_id] = time.monotonic()

    async def _remove_visible_people(self, person_ids: list[str]) -> None:
        if not self._redis or not person_ids:
            return
        try:
            await self._redis.hdel(Keys.VISIBLE_PERSONS, *person_ids)
        except Exception:
            logger.debug("visible_remove_failed", ids=person_ids, exc_info=True)
        for person_id in person_ids:
            self._last_visible_at.pop(person_id, None)

    @staticmethod
    def _bbox_iou(a, b) -> float:
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

    async def _prune_stale_visible(self) -> None:
        """Remove visible_persons entries that haven't been re-detected recently."""
        if not self._redis:
            return
        now = time.monotonic()
        expired_ids = self._person_tracker.expire(now=now)
        if expired_ids:
            await self._remove_visible_people(expired_ids)
        cutoff = now - self._visible_timeout_seconds
        stale_ids: list[str] = []
        for person_id, last_at in list(self._last_visible_at.items()):
            if last_at < cutoff:
                stale_ids.append(person_id)
        if not stale_ids:
            return
        try:
            await self._redis.hdel(Keys.VISIBLE_PERSONS, *stale_ids)
        except Exception:
            logger.debug("visible_prune_failed", exc_info=True)
        for pid in stale_ids:
            self._last_visible_at.pop(pid, None)
        logger.debug("visible_pruned", ids=stale_ids)

    async def _analyze_engagement(self, frame) -> None:
        """Run pose-based engagement classification on the current frame."""
        if not self._engagement_classifier or not self._engagement_classifier.is_available:
            return

        # Build known_persons map from Redis visible persons
        known_persons = {}
        if self._redis:
            visible_raw = await self._redis.hgetall(Keys.VISIBLE_PERSONS)
            for person_id, json_str in visible_raw.items():
                try:
                    vp = json.loads(json_str)
                    from palantir.models import BoundingBox
                    known_persons[person_id] = BoundingBox(**vp["bbox"])
                except (json.JSONDecodeError, KeyError):
                    pass

        # Run classifier in executor (CPU-bound)
        engagements = await self._loop.run_in_executor(
            None,
            lambda: self._engagement_classifier.classify_frame(frame, known_persons),
        )

        if not engagements:
            return

        # Enrich with names from visible persons
        visible_raw = await self._redis.hgetall(Keys.VISIBLE_PERSONS) if self._redis else {}
        now = time.monotonic()
        for eng in engagements:
            resolved_person_id = self._resolve_engagement_identity(eng, now=now)
            if resolved_person_id:
                eng.person_id = resolved_person_id

            if eng.person_id in visible_raw:
                try:
                    vp = json.loads(visible_raw[eng.person_id])
                    eng.name = vp.get("name")
                except (json.JSONDecodeError, KeyError):
                    pass

            if eng.bbox and self._person_tracker.get(eng.person_id):
                track = self._person_tracker.update_body(
                    eng.person_id,
                    eng.bbox,
                    body_track_id=eng.track_id,
                    now=now,
                )
                if track:
                    eng.name = track.name
                    await self._mark_track_visible(track, update_attendance=True)

        # Publish engagement data to Redis for dashboard + eventlog
        engagement_data = [e.model_dump(mode="json") for e in engagements]
        await publish(self._redis, Channels.VISION_ENGAGEMENT, {"engagements": engagement_data})

        logger.debug(
            "engagement_analyzed",
            count=len(engagements),
            states=[e.state.value for e in engagements],
        )

    def _resolve_engagement_identity(self, eng, *, now: float) -> str | None:
        """Attach a pose/body result to an already-tagged person if possible."""
        if not eng.bbox:
            return None

        if not eng.person_id.startswith("unknown_") and self._person_tracker.get(eng.person_id):
            if eng.track_id is not None:
                self._person_tracker.bind_body_track(eng.person_id, eng.track_id)
            return eng.person_id

        if eng.track_id is not None:
            person_id = self._person_tracker.person_for_body_track(eng.track_id, now=now)
            if person_id:
                return person_id

        person_id = self._person_tracker.match_body_bbox(
            eng.bbox,
            now=now,
            allow_single_active=True,
        )
        if person_id and eng.track_id is not None:
            self._person_tracker.bind_body_track(person_id, eng.track_id)
        return person_id

    async def _track_person_objects(self, frame) -> None:
        """Use object-detected person boxes as a fallback body tracker."""
        if not self._object_detector or not self._object_detector.is_available:
            return

        objects = await self._loop.run_in_executor(
            None,
            self._object_detector.detect,
            frame,
        )
        await self._update_tracks_from_person_objects(objects)

    async def _update_tracks_from_person_objects(
        self,
        objects: list[DetectedObject],
        *,
        now: float | None = None,
    ) -> None:
        """Refresh known person tracks from generic YOLO person boxes."""
        if not objects:
            return
        now = time.monotonic() if now is None else now
        active = self._person_tracker.active_tracks(now=now)
        if not active:
            return

        person_objects = [
            obj
            for obj in objects
            if obj.label.lower() == "person" and obj.confidence >= 0.35
        ]
        if not person_objects:
            return

        person_objects.sort(
            key=lambda obj: obj.bbox.width * obj.bbox.height,
            reverse=True,
        )
        used_person_ids: set[str] = set()
        allow_single = len(active) == 1
        for obj in person_objects:
            person_id = self._person_tracker.match_body_bbox(
                obj.bbox,
                now=now,
                allow_single_active=allow_single,
            )
            if not person_id or person_id in used_person_ids:
                continue
            track = self._person_tracker.update_body(
                person_id,
                obj.bbox,
                now=now,
            )
            if track:
                used_person_ids.add(person_id)
                await self._mark_track_visible(track, update_attendance=True)

    async def _detect_objects(self, frame, *, update_person_tracks: bool = False) -> None:
        """Run YOLO object detection and cache results in Redis."""
        if not self._object_detector or not self._object_detector.is_available:
            return

        # Run detection in executor (CPU-bound)
        objects = await self._loop.run_in_executor(
            None, self._object_detector.detect, frame
        )

        if not objects:
            return

        if update_person_tracks:
            await self._update_tracks_from_person_objects(objects)

        # Cache object list in Redis for the brain to query
        objects_data = [obj.model_dump() for obj in objects]
        import json
        await self._redis.set(
            Keys.OBJECT_CACHE,
            json.dumps(objects_data, default=str),
            ex=60,  # Expires after 60 seconds
        )

        # Publish for dashboard
        await publish(self._redis, Channels.VISION_OBJECTS, {"objects": objects_data})

        logger.debug(
            "objects_detected",
            count=len(objects),
            labels=[o.label for o in objects[:10]],
        )

    async def _check_attendance_exits(self) -> None:
        """Check if anyone has left the room."""
        if not self._attendance_tracker:
            return

        exited = self._attendance_tracker.check_exits()
        for person_id in exited:
            # Remove from Redis state
            await self._redis.hdel(Keys.VISIBLE_PERSONS, person_id)
            await self._redis.srem(Keys.PRESENT_PERSONS, person_id)
            self._person_tracker.remove(person_id)
            self._last_visible_at.pop(person_id, None)

            # Publish exit event
            event = Event(
                type=EventType.PERSON_EXITED,
                person_id=person_id,
            )
            await publish(self._redis, Channels.EVENTS_LOG, event)

    async def _on_privacy_toggle(self, data: dict) -> None:
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled

        if event.enabled:
            if self._camera and self._camera.is_running:
                self._camera.stop()
            present_ids = set(await self._redis.smembers(Keys.PRESENT_PERSONS))
            if self._attendance_tracker:
                present_ids.update(self._attendance_tracker.clear_present())

            await self._redis.delete(Keys.VISIBLE_PERSONS, Keys.PRESENT_PERSONS)
            self._person_tracker.clear()
            self._last_visible_at.clear()
            for person_id in present_ids:
                exit_event = Event(
                    type=EventType.PERSON_EXITED,
                    person_id=person_id,
                    data={"reason": "privacy_mode"},
                )
                await publish(self._redis, Channels.EVENTS_LOG, exit_event)
            logger.info("vision_privacy_mode_enabled", present_cleared=len(present_ids))
        else:
            if self._camera and not self._camera.is_running:
                self._camera.start()
            logger.info("vision_privacy_mode_disabled")

    async def _on_camera_mode_change(self, data: dict) -> None:
        """Swap the camera capture between local cv2 and Redis relay at runtime.

        Triggered by POST /api/system/camera/scanning so the operator can
        flip the laptop webcam between "browser owns it for enrollment"
        (relay) and "vision service owns it for room tracking" (local)
        without restarting the launcher.
        """
        data = data or {}
        new_mode = data.get("mode") or self._current_camera_mode
        if new_mode not in ("local", "relay"):
            logger.warning("camera_mode_invalid", mode=new_mode)
            return
        try:
            if "device" in data:
                try:
                    await self._redis.set("state:camera_device", str(int(data["device"])))
                except (TypeError, ValueError):
                    logger.warning("camera_device_invalid", device=data.get("device"))
                    return
            await self._reconfigure_camera(new_mode)
            await self._redis.set("state:camera_mode", new_mode)
            logger.info("camera_mode_changed", mode=new_mode, device=self._camera_device())
        except Exception:
            logger.exception("camera_mode_change_failed", requested=new_mode)

    def _camera_device(self) -> int:
        try:
            # Runtime callers need the async Redis value; this helper is only
            # used for status/logging after _camera_config() has already read it.
            return int(getattr(self, "_current_camera_device", self._config.camera.device))
        except (TypeError, ValueError):
            return int(self._config.camera.device)

    async def _camera_config(self):
        raw = await self._redis.get("state:camera_device")
        try:
            device = int(raw) if raw is not None else int(self._config.camera.device)
        except (TypeError, ValueError):
            device = int(self._config.camera.device)
        self._current_camera_device = device
        return replace(self._config.camera, device=device)

    async def _reconfigure_camera(self, mode: str) -> None:
        """Stop the current camera (if any) and start a fresh one in `mode`.

        Used both at startup and by the runtime mode-change handler.  In
        privacy mode we still construct the new capture but leave it
        stopped, mirroring what _on_privacy_toggle would do.
        """
        relay_mode = mode == "relay"
        # Tear down whatever's running first so cv2.VideoCapture releases
        # the camera before we attempt to reopen it (or before the browser
        # tries to grab it during enrollment).
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:
                logger.debug("camera_stop_failed", exc_info=True)
            self._camera = None

        if relay_mode and self._binary_redis is None:
            self._binary_redis = await create_binary_redis(self._config)

        camera_config = await self._camera_config()
        self._camera = create_camera_capture(
            camera_config,
            relay_mode=relay_mode,
            binary_redis=self._binary_redis,
        )
        # Track the runtime mode so the live-frame publisher in
        # _process_frame knows whether to JPEG-encode and push to
        # LATEST_FRAME.  Reading self._config.relay.mode here would
        # always return the launcher's startup value -- the Camera
        # page would stay black after a runtime swap to local even
        # though detection overlays are firing.
        self._current_camera_mode = mode
        if not self._privacy_mode:
            self._camera.start()

    async def _on_reload(self, data: dict) -> None:
        """Rebuild model/camera state in-place when a reload is requested.

        This is the "soft-restart" path: we don't exit the process (systemd
        would drop pending work), we just drop and recreate the stateful
        resources that go stale — face enrollments, the camera handle, and
        the engagement classifier's sliding window. Callers drive this via
        POST /api/system/reload.
        """
        async def _do() -> None:
            # Re-load face enrollments from disk so newly-added persons are
            # picked up without a restart.
            if self._face_recognizer:
                self._face_recognizer.reload_profiles()
            self._person_tracker.clear()
            self._last_visible_at.clear()
            # Reset engagement smoothing window — stale pose history causes
            # "stuck" engagement states after misclassifications.
            if self._engagement_classifier and hasattr(
                self._engagement_classifier, "reset"
            ):
                self._engagement_classifier.reset()
            # Bounce the camera (cheapest way to clear a wedged v4l2 handle).
            if self._camera and not self._privacy_mode:
                try:
                    self._camera.stop()
                except Exception:
                    logger.debug("camera_stop_during_reload_failed", exc_info=True)
                relay_mode = self._current_camera_mode == "relay"
                if relay_mode and self._binary_redis is None:
                    self._binary_redis = await create_binary_redis(self._config)
                camera_config = await self._camera_config()
                self._camera = create_camera_capture(
                    camera_config,
                    relay_mode=relay_mode,
                    binary_redis=self._binary_redis,
                )
                self._camera.start()
            await self._publish_status(healthy=True)

        await handle_reload_request(self._redis, "vision", data, _do)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="vision",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "capturing": self._camera.is_running if self._camera else False,
                "mode": self._current_camera_mode,
                "device": self._camera_device(),
                "fps": self._camera.fps if self._camera else 0.0,
                "frames_processed": self._last_frame_count,
                "latest_frame_age_ms": (
                    int((time.time() - self._last_latest_frame_published_at) * 1000)
                    if self._last_latest_frame_published_at
                    else None
                ),
                "face_detection": _FACE_AVAILABLE,
                "engagement_active": (
                    _ENGAGEMENT_AVAILABLE and self._engagement_classifier is not None
                ),
                "enrolled_faces": (
                    self._face_recognizer.enrolled_count
                    if self._face_recognizer
                    else 0
                ),
                "present_count": (
                    self._attendance_tracker.present_count
                    if self._attendance_tracker
                    else 0
                ),
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        try:
            status_counter = 0
            while self._running:
                await self._process_frame()
                status_counter += 1
                if status_counter >= 1000:  # ~every 10s at 100 iterations/s
                    await self._publish_status(healthy=True)
                    status_counter = 0
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._attendance_tracker and self._attendance_tracker.session_active:
            self._attendance_tracker.end_session()
        if self._camera:
            self._camera.stop()
        if self._subscriber:
            await self._subscriber.stop()
        if self._db:
            self._db.close()
        if self._redis:
            await self._redis.close()
        if self._binary_redis:
            try:
                await self._binary_redis.close()
            except Exception:
                logger.debug("binary_redis_close_failed", exc_info=True)
        logger.info("vision_service_stopped")


def main() -> None:
    setup_logging("vision")
    service = VisionService()
    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown, sig)
        except NotImplementedError:
            # Windows: the proactor loop has no add_signal_handler.
            # Ctrl+C still surfaces via KeyboardInterrupt, and a SIGTERM
            # from the OS will tear the process down anyway.
            pass

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
