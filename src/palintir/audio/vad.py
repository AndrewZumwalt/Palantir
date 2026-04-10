"""Voice Activity Detection using Silero VAD.

Segments continuous audio into utterances by detecting speech start/end.
Used after wake word detection to capture the user's spoken command.
"""

from __future__ import annotations

import numpy as np
import structlog
import torch

logger = structlog.get_logger()


class VoiceActivityDetector:
    """Detects speech boundaries in streaming audio using Silero VAD.

    After the wake word fires, this module accumulates audio chunks and
    determines when the speaker has finished their utterance based on
    a configurable silence timeout.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        silence_timeout_ms: int = 1500,
        speech_threshold: float = 0.5,
        max_duration_seconds: int = 30,
    ):
        self._sample_rate = sample_rate
        self._silence_timeout_samples = int(sample_rate * silence_timeout_ms / 1000)
        self._speech_threshold = speech_threshold
        self._max_samples = sample_rate * max_duration_seconds

        # Load Silero VAD model
        self._model, self._utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._model.eval()

        # State
        self._recording = False
        self._audio_buffer: list[np.ndarray] = []
        self._total_samples = 0
        self._silence_samples = 0
        self._speech_detected = False

        logger.info("vad_initialized", sample_rate=sample_rate, silence_timeout_ms=silence_timeout_ms)

    def start_recording(self) -> None:
        """Begin capturing an utterance (called after wake word detection)."""
        self._recording = True
        self._audio_buffer.clear()
        self._total_samples = 0
        self._silence_samples = 0
        self._speech_detected = False
        self._model.reset_states()
        logger.debug("vad_recording_started")

    def process_audio(self, audio_chunk: np.ndarray) -> np.ndarray | None:
        """Feed audio and return the complete utterance when speech ends.

        Args:
            audio_chunk: Audio samples as int16 numpy array (16kHz mono).

        Returns:
            Complete utterance as int16 numpy array when speech ends, or None
            if still recording.
        """
        if not self._recording:
            return None

        self._audio_buffer.append(audio_chunk)
        self._total_samples += len(audio_chunk)

        # Silero VAD expects float32 tensor, 512 samples per chunk at 16kHz
        audio_float = audio_chunk.astype(np.float32) / 32768.0
        tensor = torch.from_numpy(audio_float)

        # Process in 512-sample windows
        window_size = 512
        is_speech = False
        for i in range(0, len(tensor) - window_size + 1, window_size):
            window = tensor[i : i + window_size]
            prob = self._model(window, self._sample_rate).item()
            if prob >= self._speech_threshold:
                is_speech = True
                break

        if is_speech:
            self._speech_detected = True
            self._silence_samples = 0
        else:
            self._silence_samples += len(audio_chunk)

        # End conditions
        should_end = False

        # Condition 1: Speech was detected and silence timeout reached
        if self._speech_detected and self._silence_samples >= self._silence_timeout_samples:
            logger.debug("vad_silence_timeout", total_samples=self._total_samples)
            should_end = True

        # Condition 2: Max duration reached
        if self._total_samples >= self._max_samples:
            logger.debug("vad_max_duration", total_samples=self._total_samples)
            should_end = True

        # Condition 3: Extended silence without any speech (false wake word trigger)
        no_speech_timeout = self._sample_rate * 5  # 5 seconds
        if not self._speech_detected and self._total_samples >= no_speech_timeout:
            logger.debug("vad_no_speech_detected")
            self._recording = False
            self._audio_buffer.clear()
            return None

        if should_end:
            self._recording = False
            utterance = np.concatenate(self._audio_buffer)
            self._audio_buffer.clear()
            duration = len(utterance) / self._sample_rate
            logger.info("vad_utterance_complete", duration_seconds=round(duration, 2))
            return utterance

        return None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def cancel(self) -> None:
        """Cancel current recording."""
        self._recording = False
        self._audio_buffer.clear()
