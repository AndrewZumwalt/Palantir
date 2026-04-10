"""Vision service: camera capture, face detection/recognition, object detection, engagement.

This is the main entry point for the palintir-vision systemd service.
"""

from __future__ import annotations

import asyncio
import signal
import time

import structlog

from palintir.config import load_config
from palintir.logging import setup_logging
from palintir.models import PrivacyModeEvent, ServiceStatus
from palintir.redis_client import Channels, Keys, Subscriber, create_redis, publish

from .capture import CameraCapture

logger = structlog.get_logger()


class VisionService:
    """Orchestrates the vision pipeline with tiered frame processing.

    Tier 1 (every frame): Face detection
    Tier 2 (every 5th frame): Engagement analysis
    Tier 3 (every 30th frame): Object detection
    Tier 4 (on-demand): Claude Vision API
    """

    def __init__(self):
        self._config = load_config()
        self._camera: CameraCapture | None = None
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()
        self._last_frame_count = 0

    async def start(self) -> None:
        """Initialize and start all vision pipeline components."""
        self._redis = await create_redis(self._config)

        # Check if privacy mode is active
        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Set up Redis subscriber
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        await self._subscriber.start()

        # Start camera capture
        self._camera = CameraCapture(self._config.camera)
        if not self._privacy_mode:
            self._camera.start()

        self._running = True
        logger.info("vision_service_started", privacy_mode=self._privacy_mode)
        await self._publish_status(healthy=True)

    async def _process_frame(self) -> None:
        """Process a single frame through the tiered pipeline.

        Face detection, engagement, and object detection will be
        implemented in Phases 3, 5, and 6 respectively.
        """
        if not self._camera or self._privacy_mode:
            return

        frame, frame_num = self._camera.get_frame()
        if frame is None or frame_num == self._last_frame_count:
            return

        self._last_frame_count = frame_num
        cam_cfg = self._config.camera

        # Tier 1: Face detection (every frame) - Phase 3
        if frame_num % cam_cfg.face_detection_interval == 0:
            pass  # await self._detect_faces(frame)

        # Tier 2: Engagement analysis (every 5th frame) - Phase 6
        if frame_num % cam_cfg.engagement_interval == 0:
            pass  # await self._analyze_engagement(frame)

        # Tier 3: Object detection (every 30th frame) - Phase 5
        if frame_num % cam_cfg.object_detection_interval == 0:
            pass  # await self._detect_objects(frame)

    async def _on_privacy_toggle(self, data: dict) -> None:
        """Handle privacy mode toggle."""
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled

        if event.enabled:
            if self._camera and self._camera.is_running:
                self._camera.stop()
            logger.info("vision_privacy_mode_enabled")
        else:
            if self._camera and not self._camera.is_running:
                self._camera.start()
            logger.info("vision_privacy_mode_disabled")

    async def _publish_status(self, healthy: bool) -> None:
        """Publish service health status."""
        status = ServiceStatus(
            name="vision",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "capturing": self._camera.is_running if self._camera else False,
                "fps": self._camera.fps if self._camera else 0.0,
                "frames_processed": self._last_frame_count,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        """Main service loop."""
        await self.start()

        try:
            while self._running:
                await self._process_frame()
                # Status update every 10 seconds
                if int(time.monotonic()) % 10 == 0:
                    await self._publish_status(healthy=True)
                # Small sleep to prevent tight-looping when no new frames
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Shut down the vision service."""
        self._running = False
        if self._camera:
            self._camera.stop()
        if self._subscriber:
            await self._subscriber.stop()
        if self._redis:
            await self._redis.close()
        logger.info("vision_service_stopped")


def main() -> None:
    """Entry point for the palintir-vision service."""
    setup_logging("vision")
    service = VisionService()

    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
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
