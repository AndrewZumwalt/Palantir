"""Speech-to-text using faster-whisper.

Transcribes captured utterances into text on-device using the
CTranslate2-optimized Whisper implementation.
"""

from __future__ import annotations

import numpy as np
import structlog

logger = structlog.get_logger()

try:
    from faster_whisper import WhisperModel

    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False


class SpeechToText:
    """Transcribes audio to text using faster-whisper.

    Uses the base.en model with int8 quantization for optimal
    performance on Raspberry Pi 5.
    """

    def __init__(
        self,
        model_size: str = "base.en",
        compute_type: str = "int8",
        beam_size: int = 1,
        device: str = "cpu",
    ):
        self._beam_size = beam_size
        self._model: WhisperModel | None = None

        if not _WHISPER_AVAILABLE:
            logger.warning("faster_whisper_not_installed", hint="pip install faster-whisper")
            return

        try:
            logger.info("stt_loading_model", model=model_size, compute_type=compute_type)
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )
            logger.info("stt_model_loaded", model=model_size)
        except Exception:
            logger.exception("stt_init_failed")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str | None:
        """Transcribe an audio utterance to text.

        Args:
            audio: Audio samples as int16 numpy array.
            sample_rate: Sample rate of the audio (should be 16000).

        Returns:
            Transcribed text, or None if transcription failed.
        """
        if not self._model:
            logger.warning("stt_model_not_available")
            return None

        # faster-whisper expects float32 normalized to [-1, 1]
        audio_float = audio.astype(np.float32) / 32768.0

        try:
            segments, info = self._model.transcribe(
                audio_float,
                beam_size=self._beam_size,
                language="en",
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200,
                ),
            )

            # Collect all segment texts
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            full_text = " ".join(text_parts).strip()

            if full_text:
                logger.info(
                    "stt_transcribed",
                    text=full_text[:100],
                    language=info.language,
                    language_prob=round(info.language_probability, 3),
                    duration=round(info.duration, 2),
                )
            else:
                logger.debug("stt_empty_transcription")

            return full_text or None

        except Exception:
            logger.exception("stt_transcription_error")
            return None

    @property
    def is_available(self) -> bool:
        return self._model is not None
