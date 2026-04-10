"""Audio output management for playing synthesized speech.

Handles playback of audio through the system's default audio output
(speaker, 3.5mm jack, or USB audio device).
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd
import structlog

logger = structlog.get_logger()


class AudioOutput:
    """Plays audio through the system speaker."""

    def __init__(self, device: str | int | None = None):
        """Initialize audio output.

        Args:
            device: Audio output device name or index. None for system default.
        """
        self._device = device
        self._playing = False

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        """Play audio samples synchronously (blocks until complete).

        Args:
            audio: Audio samples as int16 numpy array.
            sample_rate: Sample rate of the audio.
        """
        if len(audio) == 0:
            return

        self._playing = True
        try:
            # sounddevice expects float32 in [-1, 1]
            audio_float = audio.astype(np.float32) / 32768.0
            sd.play(audio_float, samplerate=sample_rate, device=self._device)
            sd.wait()  # Block until playback completes
            logger.debug(
                "audio_played",
                duration=round(len(audio) / sample_rate, 2),
                sample_rate=sample_rate,
            )
        except Exception:
            logger.exception("audio_playback_error")
        finally:
            self._playing = False

    def stop(self) -> None:
        """Stop any currently playing audio."""
        sd.stop()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing
