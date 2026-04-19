"""Vision service: camera capture, face detection/recognition, object detection, engagement.

This is the main entry point for the palantir-vision systemd service.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time

import structlog

from palantir.config import load_config
from palantir.db import init_db
from palantir.logging import setup_logging
from palantir.models import (
    DetectedFace,
    Event,
    EventType,
    PrivacyModeEvent,
    ServiceStatus,
    VisiblePerson,
)
from palantir.preflight import log_and_check, validate_for
from palantir.redis_client import Channels, Keys, Subscriber, create_redis, publish
from palantir.reload import handle_reload_request

from .capture import CameraCapture

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
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._db = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()
        self._last_frame_count = 0

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

    async def start(self) -> None:
        preflight = validate_for("vision", self._config)
        if not log_and_check(preflight, fatal_on_error=False):
            raise RuntimeError("vision preflight failed")

        self._loop = asyncio.get_running_loop()
        self._redis = await create_redis(self._config)
        self._db = init_db(self._config)

        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

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
        await self._subscriber.start()

        # Start camera
        self._camera = CameraCapture(self._config.camera)
        if not self._privacy_mode:
            self._camera.start()

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

        # Tier 1: Face detection (every frame)
        if frame_num % cam_cfg.face_detection_interval == 0:
            await self._detect_faces(frame)

        # Tier 2: Engagement analysis (every 5th frame)
        if frame_num % cam_cfg.engagement_interval == 0:
            await self._analyze_engagement(frame)

        # Tier 3: Object detection (every 30th frame) + store frame for cloud vision
        if frame_num % cam_cfg.object_detection_interval == 0:
            # Store latest frame as JPEG in Redis for cloud vision queries
            import cv2
            _, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            await self._redis.set(Keys.LATEST_FRAME, jpeg_buf.tobytes(), ex=30)
            await self._detect_objects(frame)

        # Periodic exit check for attendance (every 30 seconds)
        now = time.monotonic()
        if now - self._last_exit_check >= 30:
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

        if not detections:
            return

        detected_faces = []
        for det in detections:
            face_msg = DetectedFace(
                bbox=det.bbox,
                confidence=det.det_score,
            )

            # Try to recognize the face
            if det.embedding is not None and self._face_recognizer:
                result = self._face_recognizer.recognize(det.embedding)
                if result.matched:
                    face_msg.person_id = result.person_id
                    face_msg.name = result.name
                    face_msg.confidence = result.confidence

                    # Update attendance
                    if self._attendance_tracker and result.person_id:
                        is_new = self._attendance_tracker.person_seen(result.person_id)
                        if is_new:
                            # Publish entry event
                            event = Event(
                                type=EventType.PERSON_ENTERED,
                                person_id=result.person_id,
                                data={"name": result.name},
                            )
                            await publish(self._redis, Channels.EVENTS_LOG, event)

                    # Update visible persons in Redis
                    visible = VisiblePerson(
                        person_id=result.person_id,
                        name=result.name,
                        role=result.role or "student",
                        bbox=det.bbox,
                    )
                    await self._redis.hset(
                        Keys.VISIBLE_PERSONS,
                        result.person_id,
                        visible.model_dump_json(),
                    )
                    # Add to present set
                    await self._redis.sadd(Keys.PRESENT_PERSONS, result.person_id)

            detected_faces.append(face_msg)

        # Publish detected faces
        faces_data = [f.model_dump() for f in detected_faces]
        await publish(self._redis, Channels.VISION_FACES, {"faces": faces_data})

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
        for eng in engagements:
            if eng.person_id in visible_raw:
                try:
                    vp = json.loads(visible_raw[eng.person_id])
                    eng.name = vp.get("name")
                except (json.JSONDecodeError, KeyError):
                    pass

        # Publish engagement data to Redis for dashboard + eventlog
        engagement_data = [e.model_dump(mode="json") for e in engagements]
        await publish(self._redis, Channels.VISION_ENGAGEMENT, {"engagements": engagement_data})

        logger.debug(
            "engagement_analyzed",
            count=len(engagements),
            states=[e.state.value for e in engagements],
        )

    async def _detect_objects(self, frame) -> None:
        """Run YOLO object detection and cache results in Redis."""
        if not self._object_detector or not self._object_detector.is_available:
            return

        # Run detection in executor (CPU-bound)
        objects = await self._loop.run_in_executor(
            None, self._object_detector.detect, frame
        )

        if not objects:
            return

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
            # Clear visible state
            await self._redis.delete(Keys.VISIBLE_PERSONS)
            logger.info("vision_privacy_mode_enabled")
        else:
            if self._camera and not self._camera.is_running:
                self._camera.start()
            logger.info("vision_privacy_mode_disabled")

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
                self._camera = CameraCapture(self._config.camera)
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
                "fps": self._camera.fps if self._camera else 0.0,
                "frames_processed": self._last_frame_count,
                "face_detection": _FACE_AVAILABLE,
                "engagement_active": _ENGAGEMENT_AVAILABLE and self._engagement_classifier is not None,
                "enrolled_faces": self._face_recognizer.enrolled_count if self._face_recognizer else 0,
                "present_count": self._attendance_tracker.present_count if self._attendance_tracker else 0,
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
        logger.info("vision_service_stopped")


def main() -> None:
    setup_logging("vision")
    service = VisionService()
    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
