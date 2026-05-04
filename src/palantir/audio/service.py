"""Audio service: mic capture, wake word detection, VAD, STT, and speaker ID.

This is the main entry point for the palantir-audio systemd service.
Pipeline: mic -> wake word -> VAD -> STT -> publish utterance to Redis
"""

from __future__ import annotations

import asyncio
import signal
import time

import numpy as np
import structlog

from palantir.config import load_config
from palantir.db import init_db
from palantir.logging import setup_logging
from palantir.models import (
    PrivacyModeEvent,
    ServiceStatus,
    SpeakerIdentification,
    Utterance,
    WakeWordEvent,
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

from .capture import AudioCapture, create_audio_capture
from .stt import SpeechToText
from .wake_word import WakeWordDetector

logger = structlog.get_logger()

# Import VAD conditionally (requires torch)
try:
    from .vad import VoiceActivityDetector

    _VAD_AVAILABLE = True
except ImportError:
    _VAD_AVAILABLE = False

# Import speaker ID conditionally
try:
    from .speaker_id import SpeakerIdentifier

    _SPEAKER_ID_AVAILABLE = True
except ImportError:
    _SPEAKER_ID_AVAILABLE = False


class AudioService:
    """Orchestrates the audio pipeline: capture -> wake word -> VAD -> STT."""

    def __init__(self):
        self._config = load_config()
        self._capture: AudioCapture | None = None
        self._redis = None
        self._binary_redis = None  # only created in relay mode
        self._subscriber: Subscriber | None = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()

        # Pipeline components
        self._wake_word: WakeWordDetector | None = None
        self._vad: VoiceActivityDetector | None = None
        self._stt: SpeechToText | None = None
        self._speaker_id: SpeakerIdentifier | None = None
        self._db = None

        # State
        self._listening_for_utterance = False
        self._utterance_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Initialize and start all audio pipeline components."""
        preflight = validate_for("audio", self._config)
        if not log_and_check(preflight, fatal_on_error=False):
            raise RuntimeError("audio preflight failed")

        self._loop = asyncio.get_running_loop()
        self._redis = await create_redis(self._config)

        # Check privacy mode
        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Redis subscriber
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        self._subscriber.on(Channels.SYSTEM_RELOAD, self._on_reload)
        await self._subscriber.start()

        # Initialize pipeline components
        audio_cfg = self._config.audio

        self._wake_word = WakeWordDetector(threshold=audio_cfg.wake_word_threshold)
        self._wake_word.on_wake(self._on_wake_word)

        if _VAD_AVAILABLE:
            self._vad = VoiceActivityDetector(
                sample_rate=audio_cfg.sample_rate,
                silence_timeout_ms=audio_cfg.silence_timeout_ms,
                max_duration_seconds=audio_cfg.max_utterance_seconds,
            )

        self._stt = SpeechToText(
            model_size=audio_cfg.stt_model,
            compute_type=audio_cfg.stt_compute_type,
            beam_size=audio_cfg.stt_beam_size,
        )

        # Initialize speaker identification
        if _SPEAKER_ID_AVAILABLE:
            self._db = init_db(self._config)
            self._speaker_id = SpeakerIdentifier(
                self._db,
                match_threshold=self._config.identity.voice_match_threshold,
            )

        # Start audio capture (local mic OR Pi relay over Redis)
        relay_mode = self._config.relay.mode == "relay"
        if relay_mode and self._binary_redis is None:
            self._binary_redis = await create_binary_redis(self._config)
        self._capture = create_audio_capture(
            self._config.audio,
            relay_mode=relay_mode,
            binary_redis=self._binary_redis,
        )
        self._capture.add_callback(self._on_audio_chunk)

        if not self._privacy_mode:
            self._capture.start()

        self._running = True
        logger.info(
            "audio_service_started",
            privacy_mode=self._privacy_mode,
            wake_word=self._wake_word.is_active,
            vad=_VAD_AVAILABLE,
            stt=self._stt.is_available if self._stt else False,
        )
        await self._publish_status(healthy=True)

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """Handle a raw audio chunk from the microphone."""
        if self._privacy_mode:
            return

        if self._listening_for_utterance and self._vad:
            # Feed audio to VAD to capture the utterance
            utterance = self._vad.process_audio(chunk)
            if utterance is not None:
                # Utterance complete - process it asynchronously
                self._listening_for_utterance = False
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._process_utterance(utterance), self._loop
                    )
        else:
            # Feed audio to wake word detector
            if self._wake_word:
                self._wake_word.process_audio(chunk)

    def _on_wake_word(self, confidence: float) -> None:
        """Called when the wake word is detected."""
        if self._listening_for_utterance:
            return  # Already listening

        logger.info("wake_word_triggered", confidence=round(confidence, 3))
        self._listening_for_utterance = True

        # Start VAD recording
        if self._vad:
            self._vad.start_recording()

        # Publish wake event
        if self._loop and self._redis:
            event = WakeWordEvent(confidence=confidence)
            asyncio.run_coroutine_threadsafe(
                publish(self._redis, Channels.AUDIO_WAKE, event), self._loop
            )

    async def _process_utterance(self, audio: np.ndarray) -> None:
        """Transcribe an utterance and publish the result."""
        if not self._stt:
            logger.warning("stt_not_available")
            return

        # Run STT in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None, self._stt.transcribe, audio, self._config.audio.sample_rate
        )

        if not text:
            logger.debug("utterance_empty_transcription")
            if self._wake_word:
                self._wake_word.reset()
            return

        duration = len(audio) / self._config.audio.sample_rate

        # Extract speaker embedding (in parallel concept, but sequentially for CPU)
        speaker_embedding = None
        if self._speaker_id and self._speaker_id.is_available:
            speaker_embedding = await loop.run_in_executor(
                None, self._speaker_id.extract_embedding, audio, self._config.audio.sample_rate
            )

        # Identify speaker
        speaker_person_id = None
        speaker_name = None
        speaker_confidence = 0.0

        if speaker_embedding is not None and self._speaker_id:
            match = self._speaker_id.identify(speaker_embedding)
            if match.matched:
                speaker_person_id = match.person_id
                speaker_name = match.name
                speaker_confidence = match.confidence
                logger.info(
                    "speaker_identified",
                    name=match.name,
                    confidence=match.confidence,
                )

                # Publish speaker identification
                speaker_msg = SpeakerIdentification(
                    person_id=match.person_id,
                    name=match.name,
                    confidence=match.confidence,
                )
                await publish(self._redis, Channels.AUDIO_SPEAKER_ID, speaker_msg)

        # Build and publish the utterance with speaker info
        utterance = Utterance(
            text=text,
            speaker_embedding=speaker_embedding.tolist() if speaker_embedding is not None else None,
            duration_seconds=round(duration, 2),
        )

        logger.info(
            "utterance_published",
            text=text[:100],
            speaker=speaker_name or "unknown",
            duration=round(duration, 2),
        )
        await publish(self._redis, Channels.AUDIO_UTTERANCE, utterance)

        # Also publish speaker ID separately so brain can correlate.
        # JSON-encode to stay robust against names that contain colons.
        if speaker_person_id:
            import json as _json
            await self._redis.set(
                "state:last_speaker",
                _json.dumps({
                    "person_id": speaker_person_id,
                    "name": speaker_name,
                    "confidence": speaker_confidence,
                }),
                ex=30,  # Expires after 30 seconds
            )

        if self._wake_word:
            self._wake_word.reset()

    async def _on_privacy_toggle(self, data: dict) -> None:
        """Handle privacy mode toggle."""
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled

        if event.enabled:
            if self._capture and self._capture.is_running:
                self._capture.stop()
            self._listening_for_utterance = False
            if self._vad:
                self._vad.cancel()
            logger.info("audio_privacy_mode_enabled")
        else:
            if self._capture and not self._capture.is_running:
                self._capture.start()
            logger.info("audio_privacy_mode_disabled")

    async def _on_reload(self, data: dict) -> None:
        """Soft-reload: reset wake-word detector, VAD, and bounce capture.

        The mic can get wedged into a no-wake-word state if background noise
        re-triggers detection partially; resetting the detector + cancelling
        any in-progress VAD recovers without a systemd restart.
        """
        async def _do() -> None:
            if self._wake_word:
                self._wake_word.reset()
            if self._vad:
                self._vad.cancel()
            if self._speaker_id:
                self._speaker_id.reload_profiles()
            self._listening_for_utterance = False
            if self._capture and not self._privacy_mode:
                try:
                    self._capture.stop()
                except Exception:
                    logger.debug("audio_stop_during_reload_failed", exc_info=True)
                relay_mode = self._config.relay.mode == "relay"
                if relay_mode and self._binary_redis is None:
                    self._binary_redis = await create_binary_redis(self._config)
                self._capture = create_audio_capture(
                    self._config.audio,
                    relay_mode=relay_mode,
                    binary_redis=self._binary_redis,
                )
                self._capture.add_callback(self._on_audio_chunk)
                self._capture.start()
            await self._publish_status(healthy=True)

        await handle_reload_request(self._redis, "audio", data, _do)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="audio",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "capturing": self._capture.is_running if self._capture else False,
                "listening": self._listening_for_utterance,
                "wake_word_active": self._wake_word.is_active if self._wake_word else False,
                "stt_available": self._stt.is_available if self._stt else False,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        dispatch_task: asyncio.Task | None = None
        try:
            if self._capture:
                dispatch_task = asyncio.create_task(self._capture.run_dispatch_loop())
                while self._running:
                    await self._publish_status(healthy=True)
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            if dispatch_task is not None:
                dispatch_task.cancel()
                try:
                    await dispatch_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._capture:
            self._capture.stop()
        if self._subscriber:
            await self._subscriber.stop()
        if self._redis:
            await self._redis.close()
        if self._binary_redis:
            try:
                await self._binary_redis.close()
            except Exception:
                logger.debug("binary_redis_close_failed", exc_info=True)
        logger.info("audio_service_stopped")


def main() -> None:
    setup_logging("audio")
    service = AudioService()
    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
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
