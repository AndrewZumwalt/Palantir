"""Audio output: pluggable sink (local speaker vs. network relay).

Mirrors the capture-side split: the local sink plays via sounddevice on
whatever machine runs the TTS service, the relay sink publishes
synthesized PCM to Redis so the web service can forward it to a Pi over
the relay WebSocket.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import numpy as np
import sounddevice as sd
import structlog

from palantir.redis_client import Channels

logger = structlog.get_logger()


class AudioOutput(ABC):
    """Common interface for both local and relay audio output."""

    @abstractmethod
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def is_playing(self) -> bool: ...


class LocalAudioOutput(AudioOutput):
    """Plays audio through the system speaker via sounddevice."""

    def __init__(self, device: str | int | None = None):
        self._device = device
        self._playing = False

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        """Play audio samples synchronously (blocks until complete)."""
        if len(audio) == 0:
            return

        self._playing = True
        try:
            audio_float = audio.astype(np.float32) / 32768.0
            sd.play(audio_float, samplerate=sample_rate, device=self._device)
            sd.wait()
            logger.debug(
                "audio_played",
                sink="local",
                duration=round(len(audio) / sample_rate, 2),
                sample_rate=sample_rate,
            )
        except Exception:
            logger.exception("audio_playback_error")
        finally:
            self._playing = False

    def stop(self) -> None:
        sd.stop()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing


class RelayAudioOutput(AudioOutput):
    """Publishes synthesized PCM to Redis; the web service ships it to the Pi.

    Each `play()` call emits one Redis message containing int16 LE PCM
    bytes prefixed with a tiny 8-byte header so the receiver knows the
    sample rate without a separate metadata channel.

    Header (8 bytes, little-endian):
        bytes 0..3: ASCII "PCM\\x01"  (magic + version)
        bytes 4..7: uint32 sample_rate

    The Pi's relay client decodes this and pushes to its local
    sounddevice output.

    `play()` is called by `TTSService` inside `loop.run_in_executor(...)`,
    i.e. on a worker thread.  But the Redis client we publish through is
    bound to the main asyncio loop.  We therefore capture that loop at
    construction time and dispatch via `run_coroutine_threadsafe`.
    """

    _MAGIC = b"PCM\x01"

    def __init__(self, redis, *, loop: asyncio.AbstractEventLoop | None = None):
        self._redis = redis
        self._playing = False
        self._loop = loop or asyncio.get_event_loop()

    def _publish(self, payload: bytes) -> None:
        coro = self._redis.publish(Channels.RELAY_AUDIO_OUT, payload)
        if self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            # Block until the publish completes so play() preserves the
            # synchronous-completion semantics of LocalAudioOutput.play().
            fut.result(timeout=10.0)
        else:
            # Loop not running — most likely a unit test invoking play()
            # synchronously.  Run on the captured loop directly.
            self._loop.run_until_complete(coro)

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        if audio.size == 0:
            return
        self._playing = True
        try:
            # Coerce to int16 if the engine produced float32 in [-1, 1].
            if audio.dtype != np.int16:
                if np.issubdtype(audio.dtype, np.floating):
                    audio = (audio * 32767).clip(-32768, 32767).astype(np.int16)
                else:
                    audio = audio.astype(np.int16)
            payload = self._MAGIC + int(sample_rate).to_bytes(4, "little") + audio.tobytes()
            self._publish(payload)
            logger.debug(
                "audio_played",
                sink="relay",
                duration=round(len(audio) / sample_rate, 2),
                sample_rate=sample_rate,
            )
        except Exception:
            logger.exception("relay_audio_publish_failed")
        finally:
            self._playing = False

    def stop(self) -> None:
        # Tell the Pi to drop its playback queue with a zero-length frame.
        try:
            self._publish(self._MAGIC + (0).to_bytes(4, "little"))
        except Exception:
            logger.debug("relay_audio_stop_failed", exc_info=True)
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing


def create_audio_output(
    *,
    relay_mode: bool = False,
    redis=None,
    device: str | int | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> AudioOutput:
    """Factory: local speaker vs. relay-to-Pi.

    `loop` is required for relay mode and must be the asyncio loop the
    Redis client is bound to (typically the TTS service's main loop).
    """
    if relay_mode:
        if redis is None:
            raise ValueError("RelayAudioOutput requires a Redis client")
        return RelayAudioOutput(redis, loop=loop)
    return LocalAudioOutput(device)
