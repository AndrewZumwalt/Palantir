"""Audio service: mic capture, wake word detection, VAD, STT, and speaker ID.

This is the main entry point for the palantir-audio systemd service.
Pipeline: mic -> wake word -> VAD -> STT -> publish utterance to Redis
"""

from __future__ import annotations

import asyncio
import json
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
        # The pump that drains audio chunks into wake-word/VAD.  Kept
        # on `self` (not a local in run()) so the soft-reload handler
        # can cancel + recreate it when the capture is swapped --
        # otherwise the new capture has no dispatcher attached.
        self._dispatch_task: asyncio.Task | None = None

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
        self._last_level_publish = 0.0
        self._last_audio_level = 0.0

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

        # Pick a custom .onnx path if the configured value looks like a
        # file path (slash, backslash, or an .onnx/.tflite extension);
        # otherwise treat it as a built-in model name.
        wake_id = audio_cfg.wake_word_model
        is_path = ("/" in wake_id or "\\" in wake_id
                   or wake_id.endswith((".onnx", ".tflite")))
        self._wake_word = WakeWordDetector(
            threshold=audio_cfg.wake_word_threshold,
            model_name=None if is_path else wake_id,
            custom_model_path=wake_id if is_path else None,
        )
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

    def _publish_level_from_audio(self, chunk: np.ndarray) -> None:
        """Publish a throttled mic level for the dashboard meter."""
        now = time.monotonic()
        if now - self._last_level_publish < 0.2:
            return
        self._last_level_publish = now

        if chunk.size == 0:
            rms = 0.0
            peak = 0.0
        else:
            audio = chunk.astype(np.float32)
            rms = float(np.sqrt(np.mean(np.square(audio))) / 32768.0)
            peak = float(np.max(np.abs(audio)) / 32768.0)
        rms = max(0.0, min(1.0, rms))
        peak = max(0.0, min(1.0, peak))
        self._last_audio_level = rms

        if not self._loop or not self._redis:
            return
        self._publish_audio_state_threadsafe(
            "level",
            channel=Channels.AUDIO_LEVEL,
            rms=rms,
            peak=peak,
            listening=self._listening_for_utterance,
            speech_detected=(
                self._vad.speech_detected
                if self._vad and self._listening_for_utterance
                else False
            ),
        )

    def _publish_audio_state_threadsafe(
        self,
        state: str,
        *,
        channel: str = Channels.AUDIO_STATE,
        **data,
    ) -> None:
        if not self._loop or not self._redis:
            return
        asyncio.run_coroutine_threadsafe(
            self._publish_audio_state(state, channel=channel, **data),
            self._loop,
        )

    async def _publish_audio_state(
        self,
        state: str,
        *,
        channel: str = Channels.AUDIO_STATE,
        **data,
    ) -> None:
        if not self._redis:
            return
        payload = {
            "state": state,
            "listening": self._listening_for_utterance,
            "level": self._last_audio_level,
            **data,
        }
        await publish(self._redis, channel, payload)

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """Handle a raw audio chunk from the microphone."""
        if self._privacy_mode:
            return

        self._publish_level_from_audio(chunk)

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
            elif not self._vad.is_recording:
                # VAD uses None for both "still recording" and "gave up
                # without speech".  When it has stopped recording, re-arm
                # wake-word detection instead of getting stuck forever.
                self._listening_for_utterance = False
                if self._wake_word:
                    self._wake_word.reset()
                self._publish_audio_state_threadsafe(
                    "empty",
                    message="No speech detected after wake word",
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
            self._publish_audio_state_threadsafe(
                "listening",
                confidence=confidence,
                message="Wake word heard",
            )

    async def _process_utterance(self, audio: np.ndarray) -> None:
        """Transcribe an utterance and publish the result."""
        try:
            if not self._stt:
                logger.warning("stt_not_available")
                await self._publish_audio_state(
                    "error",
                    message="Speech-to-text is not available",
                )
                return

            await self._publish_audio_state(
                "transcribing",
                duration_seconds=round(len(audio) / self._config.audio.sample_rate, 2),
            )

            # Run STT in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None, self._stt.transcribe, audio, self._config.audio.sample_rate
            )

            if not text:
                logger.debug("utterance_empty_transcription")
                await self._publish_audio_state(
                    "empty",
                    message="Speech-to-text returned no text",
                )
                return

            duration = len(audio) / self._config.audio.sample_rate
            await self._publish_audio_state(
                "heard",
                text=text,
                duration_seconds=round(duration, 2),
            )

            # Extract speaker embedding (in parallel concept, but sequentially for CPU)
            speaker_embedding = None
            if self._speaker_id and self._speaker_id.is_available:
                speaker_embedding = await loop.run_in_executor(
                    None,
                    self._speaker_id.extract_embedding,
                    audio,
                    self._config.audio.sample_rate,
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

            if speaker_person_id:
                # Store before publishing the utterance so the brain cannot
                # race ahead and read the previous speaker. The utterance also
                # carries these fields directly; this Redis key is now only a
                # short-lived compatibility fallback.
                await self._redis.set(
                    "state:last_speaker",
                    json.dumps(
                        {
                            "person_id": speaker_person_id,
                            "name": speaker_name,
                            "confidence": speaker_confidence,
                            "timestamp": time.time(),
                        }
                    ),
                    ex=30,  # Expires after 30 seconds
                )
            else:
                await self._redis.delete("state:last_speaker")

            # Build and publish the utterance with speaker info
            utterance = Utterance(
                text=text,
                speaker_embedding=(
                    speaker_embedding.tolist() if speaker_embedding is not None else None
                ),
                speaker_person_id=speaker_person_id,
                speaker_name=speaker_name,
                speaker_confidence=speaker_confidence,
                duration_seconds=round(duration, 2),
                source="voice",
            )

            logger.info(
                "utterance_published",
                text=text[:100],
                speaker=speaker_name or "unknown",
                duration=round(duration, 2),
            )
            await publish(self._redis, Channels.AUDIO_UTTERANCE, utterance)
        except Exception as exc:
            logger.exception("utterance_processing_failed")
            await self._publish_audio_state("error", message=str(exc))
        finally:
            self._listening_for_utterance = False
            if self._wake_word:
                self._wake_word.reset()
            if self._vad and self._vad.is_recording:
                self._vad.cancel()
            await self._publish_audio_state("idle")

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
            await self._publish_audio_state("idle", message="Privacy mode enabled")
            logger.info("audio_privacy_mode_enabled")
        else:
            if self._capture and not self._capture.is_running:
                self._capture.start()
            await self._publish_audio_state("idle", message="Privacy mode disabled")
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
                # Without this re-spawn the new capture has no
                # dispatcher attached: the OLD task was still pumping
                # the OLD (now-stopped) capture, which yields nothing
                # forever in relay mode.
                self._spawn_dispatch_task()
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
                "audio_level": round(self._last_audio_level, 3),
                "wake_word_active": self._wake_word.is_active if self._wake_word else False,
                "stt_available": self._stt.is_available if self._stt else False,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    def _spawn_dispatch_task(self) -> None:
        """(Re)spawn the dispatcher that drains the current capture into
        wake-word/VAD.  Called at start-up and whenever `_on_reload`
        swaps `self._capture` for a fresh instance."""
        # Cancel an in-flight dispatcher that's reading the OLD capture.
        old = self._dispatch_task
        if old is not None and not old.done():
            old.cancel()
        if self._capture is None:
            self._dispatch_task = None
            return
        self._dispatch_task = asyncio.create_task(
            self._capture.run_dispatch_loop()
        )

    async def run(self) -> None:
        await self.start()
        try:
            if self._capture:
                self._spawn_dispatch_task()
                while self._running:
                    await self._publish_status(healthy=True)
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            if self._dispatch_task is not None:
                self._dispatch_task.cancel()
                try:
                    await self._dispatch_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._dispatch_task = None
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
