"""Audio service: mic capture, wake word detection, STT, and speaker ID.

This is the main entry point for the palintir-audio systemd service.
"""

from __future__ import annotations

import asyncio
import signal
import time

import numpy as np
import structlog

from palintir.config import load_config
from palintir.logging import setup_logging
from palintir.models import PrivacyModeEvent, ServiceStatus, WakeWordEvent
from palintir.redis_client import Channels, Keys, Subscriber, create_redis, publish

from .capture import AudioCapture

logger = structlog.get_logger()


class AudioService:
    """Orchestrates the audio pipeline: capture -> wake word -> VAD -> STT -> speaker ID."""

    def __init__(self):
        self._config = load_config()
        self._capture: AudioCapture | None = None
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()

        # Audio buffer for wake word detection
        self._audio_buffer: list[np.ndarray] = []

        # State: are we recording an utterance after wake word?
        self._recording = False
        self._utterance_chunks: list[np.ndarray] = []

    async def start(self) -> None:
        """Initialize and start all audio pipeline components."""
        self._redis = await create_redis(self._config)

        # Check if privacy mode is active
        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Set up Redis subscriber for privacy mode toggle
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        await self._subscriber.start()

        # Start audio capture
        self._capture = AudioCapture(self._config.audio)
        self._capture.add_callback(self._on_audio_chunk)

        if not self._privacy_mode:
            self._capture.start()

        self._running = True
        logger.info("audio_service_started", privacy_mode=self._privacy_mode)

        # Publish service status
        await self._publish_status(healthy=True)

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """Handle a raw audio chunk from the microphone.

        In Phase 1, this just accumulates data. Wake word detection,
        VAD, STT, and speaker ID will be added in Phase 2.
        """
        if self._privacy_mode:
            return

        # Buffer audio for wake word processing (will be implemented in Phase 2)
        self._audio_buffer.append(chunk)

        # Keep buffer to last 5 seconds of audio
        max_chunks = int(
            5.0 * self._config.audio.sample_rate / (
                self._config.audio.sample_rate * self._config.audio.chunk_duration_ms / 1000
            )
        )
        if len(self._audio_buffer) > max_chunks:
            self._audio_buffer = self._audio_buffer[-max_chunks:]

    async def _on_privacy_toggle(self, data: dict) -> None:
        """Handle privacy mode toggle."""
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled

        if event.enabled:
            if self._capture and self._capture.is_running:
                self._capture.stop()
            self._audio_buffer.clear()
            self._recording = False
            self._utterance_chunks.clear()
            logger.info("audio_privacy_mode_enabled")
        else:
            if self._capture and not self._capture.is_running:
                self._capture.start()
            logger.info("audio_privacy_mode_disabled")

    async def _publish_status(self, healthy: bool) -> None:
        """Publish service health status."""
        status = ServiceStatus(
            name="audio",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "capturing": self._capture.is_running if self._capture else False,
                "recording_utterance": self._recording,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        """Main service loop."""
        await self.start()

        try:
            # Run the audio dispatch loop
            if self._capture:
                dispatch_task = asyncio.create_task(self._capture.run_dispatch_loop())

                # Periodic status publishing
                while self._running:
                    await self._publish_status(healthy=True)
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Shut down the audio service."""
        self._running = False
        if self._capture:
            self._capture.stop()
        if self._subscriber:
            await self._subscriber.stop()
        if self._redis:
            await self._redis.close()
        logger.info("audio_service_stopped")


def main() -> None:
    """Entry point for the palintir-audio service."""
    setup_logging("audio")
    service = AudioService()

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
