"""Wake word detection using openWakeWord.

Listens for "Hey Palintir" (or a configured wake word) and triggers
the voice pipeline when detected.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import structlog

logger = structlog.get_logger()

try:
    from openwakeword.model import Model as OWWModel

    _OWW_AVAILABLE = True
except ImportError:
    _OWW_AVAILABLE = False


class WakeWordDetector:
    """Detects wake words in streaming audio using openWakeWord.

    Uses the built-in "hey_jarvis" model as a starting point.
    A custom "Hey Palintir" model can be trained later using
    openwakeword's training pipeline.
    """

    # Built-in models that work well as stand-ins until custom training
    DEFAULT_MODEL = "hey_jarvis"

    def __init__(
        self,
        threshold: float = 0.7,
        model_name: str | None = None,
        custom_model_path: str | None = None,
    ):
        self._threshold = threshold
        self._model_name = model_name or self.DEFAULT_MODEL
        self._callbacks: list[Callable[[float], None]] = []
        self._model: OWWModel | None = None
        self._active = False

        if not _OWW_AVAILABLE:
            logger.warning("openwakeword_not_installed", hint="pip install openwakeword")
            return

        try:
            if custom_model_path:
                self._model = OWWModel(
                    wakeword_models=[custom_model_path],
                    inference_framework="onnx",
                )
                logger.info("wake_word_loaded", model="custom", path=custom_model_path)
            else:
                self._model = OWWModel(
                    wakeword_models=[self._model_name],
                    inference_framework="onnx",
                )
                logger.info("wake_word_loaded", model=self._model_name)
            self._active = True
        except Exception:
            logger.exception("wake_word_init_failed")

    def on_wake(self, callback: Callable[[float], None]) -> None:
        """Register a callback for wake word detection.

        Args:
            callback: Called with the detection confidence score (0.0-1.0).
        """
        self._callbacks.append(callback)

    def process_audio(self, audio_chunk: np.ndarray) -> None:
        """Feed an audio chunk (16kHz int16 mono) to the wake word detector.

        Args:
            audio_chunk: Audio samples as int16 numpy array.
        """
        if not self._active or not self._model:
            return

        # openWakeWord expects int16 audio
        self._model.predict(audio_chunk)

        # Check all model predictions
        for model_name, score in self._model.prediction_buffer.items():
            # score is a list of recent predictions; check the latest
            if score and score[-1] >= self._threshold:
                logger.info(
                    "wake_word_detected",
                    model=model_name,
                    confidence=round(score[-1], 3),
                )
                for callback in self._callbacks:
                    try:
                        callback(score[-1])
                    except Exception:
                        logger.exception("wake_callback_error")

                # Reset the prediction buffer to avoid repeated triggers
                self._model.reset()
                break

    def reset(self) -> None:
        """Reset the detection state (e.g., after processing an utterance)."""
        if self._model:
            self._model.reset()

    @property
    def is_active(self) -> bool:
        return self._active
