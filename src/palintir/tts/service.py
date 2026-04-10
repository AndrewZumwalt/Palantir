"""TTS service: text-to-speech synthesis and speaker output.

This is the main entry point for the palintir-tts systemd service.
Subscribes to brain responses and speaks them aloud.
"""

from __future__ import annotations

import asyncio
import signal
import time

import structlog

from palintir.config import load_config
from palintir.logging import setup_logging
from palintir.models import AssistantResponse, PrivacyModeEvent, ServiceStatus
from palintir.redis_client import Channels, Keys, Subscriber, create_redis, publish

from .audio_output import AudioOutput
from .piper_engine import PiperEngine

logger = structlog.get_logger()


class TTSService:
    """Synthesizes and plays speech from brain responses."""

    def __init__(self):
        self._config = load_config()
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()
        self._speech_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

        # TTS components
        self._engine: PiperEngine | None = None
        self._output: AudioOutput | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._redis = await create_redis(self._config)

        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Initialize TTS engine and audio output
        self._engine = PiperEngine(self._config.tts)
        self._output = AudioOutput()

        # Subscribe to events
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.BRAIN_RESPONSE, self._on_response)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        await self._subscriber.start()

        self._running = True
        logger.info(
            "tts_service_started",
            engine_available=self._engine.is_available,
        )
        await self._publish_status(healthy=True)

    async def _on_response(self, data: dict) -> None:
        """Queue a brain response for TTS synthesis."""
        if self._privacy_mode:
            return
        response = AssistantResponse(**data)
        await self._speech_queue.put(response.text)
        logger.debug("tts_queued", text_length=len(response.text))

    async def _synthesize_and_play(self, text: str) -> None:
        """Synthesize speech and play it through the speaker."""
        if not self._engine or not self._output:
            return

        # Run synthesis in executor (CPU-bound)
        result = await self._loop.run_in_executor(
            None, self._engine.synthesize, text
        )

        if result is None:
            logger.warning("tts_synthesis_failed", text=text[:50])
            return

        audio, sample_rate = result

        # Play audio (blocks in executor until done)
        await self._loop.run_in_executor(
            None, self._output.play, audio, sample_rate
        )

        logger.info("tts_spoken", text=text[:80], duration=round(len(audio) / sample_rate, 2))

    async def _on_privacy_toggle(self, data: dict) -> None:
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled
        if event.enabled:
            # Clear pending speech and stop current playback
            while not self._speech_queue.empty():
                self._speech_queue.get_nowait()
            if self._output:
                self._output.stop()
        logger.info("tts_privacy_mode", enabled=event.enabled)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="tts",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "queue_size": self._speech_queue.qsize(),
                "engine_available": self._engine.is_available if self._engine else False,
                "playing": self._output.is_playing if self._output else False,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        try:
            while self._running:
                try:
                    text = await asyncio.wait_for(self._speech_queue.get(), timeout=10.0)
                    await self._synthesize_and_play(text)
                except asyncio.TimeoutError:
                    await self._publish_status(healthy=True)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._output:
            self._output.stop()
        if self._subscriber:
            await self._subscriber.stop()
        if self._redis:
            await self._redis.close()
        logger.info("tts_service_stopped")


def main() -> None:
    setup_logging("tts")
    service = TTSService()
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
