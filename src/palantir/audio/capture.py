"""Audio capture from USB microphone using sounddevice."""

from __future__ import annotations

import asyncio
import queue
from typing import Callable

import numpy as np
import sounddevice as sd
import structlog

from palantir.config import AudioConfig

logger = structlog.get_logger()


class AudioCapture:
    """Captures audio from microphone in a background thread.

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
        logger.info("audio_capture_stopped")

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
