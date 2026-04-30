"""Audio capture: pluggable source (local mic vs. network relay).

Two implementations behind a small ABC so services can stay agnostic:

* `LocalAudioCapture` — the original sounddevice-based mic reader, used
  whenever the audio service runs on the same machine as the microphone.
* `RelayAudioCapture` — subscribes to Redis `relay:audio:in` (which the
  web service populates from a Pi's WebSocket).  Used when the laptop
  does processing and a Pi physically owns the mic.

The shape (`add_callback(cb)`, `start()`, `stop()`, `run_dispatch_loop()`,
`is_running`) is the public contract every consumer relies on.
"""

from __future__ import annotations

import asyncio
import queue
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np
import sounddevice as sd
import structlog

from palantir.config import AudioConfig
from palantir.redis_client import Channels

logger = structlog.get_logger()


class AudioCapture(ABC):
    """Common interface for both local and relay-sourced audio."""

    @abstractmethod
    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    async def run_dispatch_loop(self) -> None: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...


class LocalAudioCapture(AudioCapture):
    """Reads PCM chunks from a local microphone via sounddevice.

    Audio chunks are placed into an asyncio-safe queue for consumption
    by wake word detection and other audio processors.
    """

    def __init__(self, config: AudioConfig):
        self._config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._chunk_samples = int(config.sample_rate * config.chunk_duration_ms / 1000)
        self._stream: sd.InputStream | None = None
        self._running = False
        self._callbacks: list[Callable[[np.ndarray], None]] = []
        self._thread_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=100)

    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a callback that receives raw audio chunks (int16 numpy arrays)."""
        self._callbacks.append(callback)

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info: dict, status: sd.CallbackFlags
    ) -> None:
        """Called by sounddevice from the audio thread for each chunk."""
        if status:
            logger.warning("audio_capture_status", status=str(status))
        # Convert float32 to int16 for downstream processors
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
        try:
            self._thread_queue.put_nowait(audio_int16)
        except queue.Full:
            # Drop the oldest chunk so the most recent audio is preserved;
            # stale audio isn't useful for wake-word / STT.
            try:
                self._thread_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._thread_queue.put_nowait(audio_int16)
            except queue.Full:
                pass

    def start(self) -> None:
        """Start capturing audio from the microphone."""
        device = None if self._config.device == "default" else self._config.device

        self._stream = sd.InputStream(
            device=device,
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            blocksize=self._chunk_samples,
            callback=self._audio_callback,
        )
        self._stream.start()
        self._running = True
        logger.info(
            "audio_capture_started",
            source="local",
            sample_rate=self._sample_rate,
            chunk_samples=self._chunk_samples,
        )

    def stop(self) -> None:
        """Stop audio capture."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("audio_capture_stopped", source="local")

    async def run_dispatch_loop(self) -> None:
        """Async loop that pulls audio from the thread queue and dispatches to callbacks."""
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                # Non-blocking poll with short sleep to keep async loop responsive
                chunk = await loop.run_in_executor(
                    None, lambda: self._thread_queue.get(timeout=0.1)
                )
                for callback in self._callbacks:
                    try:
                        callback(chunk)
                    except Exception:
                        logger.exception("audio_callback_error")
            except queue.Empty:
                continue
            except asyncio.CancelledError:
                break

    @property
    def is_running(self) -> bool:
        return self._running


class RelayAudioCapture(AudioCapture):
    """Reads PCM chunks from Redis `relay:audio:in` instead of a local mic.

    The web service receives the Pi's WebSocket frames and republishes
    raw int16 LE PCM payloads on this channel.  We subscribe with a
    pubsub-style listener and dispatch each chunk to registered
    callbacks — exactly the same contract as `LocalAudioCapture` so the
    rest of the audio service is unchanged.
    """

    def __init__(self, config: AudioConfig, redis):
        self._config = config
        self._redis = redis
        self._callbacks: list[Callable[[np.ndarray], None]] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def add_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> None:
        """Mark the source as running.  The actual subscription is set up
        in `run_dispatch_loop` (which the service already kicks off as a
        background task)."""
        self._running = True
        logger.info(
            "audio_capture_started",
            source="relay",
            channel=Channels.RELAY_AUDIO_IN,
        )

    def stop(self) -> None:
        self._running = False
        logger.info("audio_capture_stopped", source="relay")

    async def run_dispatch_loop(self) -> None:
        """Subscribe to RELAY_AUDIO_IN and dispatch decoded chunks to callbacks."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(Channels.RELAY_AUDIO_IN)
        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not isinstance(data, (bytes, bytearray)):
                    continue
                # Decode int16 LE PCM into a numpy array.  Frames from the
                # Pi are already at config.sample_rate (we don't resample
                # mid-stream).
                chunk = np.frombuffer(bytes(data), dtype=np.int16)
                if chunk.size == 0:
                    continue
                for cb in self._callbacks:
                    try:
                        cb(chunk)
                    except Exception:
                        logger.exception("audio_callback_error")
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(Channels.RELAY_AUDIO_IN)
                await pubsub.close()
            except Exception:
                logger.debug("relay_audio_pubsub_close_failed", exc_info=True)

    @property
    def is_running(self) -> bool:
        return self._running


def create_audio_capture(
    audio_config: AudioConfig,
    *,
    relay_mode: bool = False,
    binary_redis=None,
) -> AudioCapture:
    """Factory: local mic vs. Pi relay over Redis.

    `binary_redis` is required when `relay_mode=True`: it must be a Redis
    client created with `decode_responses=False` (use
    `palantir.redis_client.create_binary_redis`).  Subscribing to PCM
    bytes through a text-mode client would corrupt the payload.
    """
    if relay_mode:
        if binary_redis is None:
            raise ValueError(
                "RelayAudioCapture requires a binary Redis client "
                "(create_binary_redis(config))"
            )
        return RelayAudioCapture(audio_config, binary_redis)
    return LocalAudioCapture(audio_config)
