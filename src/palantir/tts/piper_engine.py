"""Piper TTS engine for natural-sounding speech synthesis.

Piper runs entirely on-device and achieves faster-than-real-time
synthesis on Raspberry Pi 5 with VITS neural network models.
"""

from __future__ import annotations

import io
import subprocess
import wave
from pathlib import Path

import numpy as np
import structlog

from palantir.config import TTSConfig

logger = structlog.get_logger()

try:
    import piper

    _PIPER_AVAILABLE = True
except ImportError:
    _PIPER_AVAILABLE = False


class PiperEngine:
    """Synthesizes speech from text using Piper TTS.

    Falls back to espeak if Piper is not available (lower quality but
    always present on Raspberry Pi OS).
    """

    def __init__(self, config: TTSConfig):
        self._config = config
        self._voice = None
        self._use_espeak_fallback = False

        if not _PIPER_AVAILABLE:
            logger.warning("piper_not_installed", hint="pip install piper-tts")
            self._use_espeak_fallback = True
            return

        try:
            # Piper auto-downloads voice models on first use
            self._voice = piper.PiperVoice.load(config.voice)
            logger.info("piper_loaded", voice=config.voice)
        except Exception:
            logger.warning("piper_voice_load_failed", voice=config.voice)
            self._use_espeak_fallback = True

    def synthesize(self, text: str) -> tuple[np.ndarray, int] | None:
        """Convert text to audio.

        Args:
            text: Text to synthesize.

        Returns:
            Tuple of (audio samples as int16 numpy array, sample rate),
            or None on failure.
        """
        if not text.strip():
            return None

        if self._voice:
            return self._synthesize_piper(text)
        elif self._use_espeak_fallback:
            return self._synthesize_espeak(text)
        return None

    def _synthesize_piper(self, text: str) -> tuple[np.ndarray, int] | None:
        """Synthesize using Piper TTS."""
        try:
            # Piper outputs raw audio samples
            audio_buffer = io.BytesIO()
            with wave.open(audio_buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # 16-bit
                wav.setframerate(self._config.sample_rate)

                for audio_bytes in self._voice.synthesize_stream_raw(text):
                    wav.writeframes(audio_bytes)

            audio_buffer.seek(0)
            with wave.open(audio_buffer, "rb") as wav:
                frames = wav.readframes(wav.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16)

            logger.debug("piper_synthesized", text_len=len(text), audio_samples=len(audio))
            return audio, self._config.sample_rate

        except Exception:
            logger.exception("piper_synthesis_error")
            return self._synthesize_espeak(text)

    def _synthesize_espeak(self, text: str) -> tuple[np.ndarray, int] | None:
        """Fallback synthesis using espeak-ng (always available on Pi)."""
        try:
            result = subprocess.run(
                ["espeak-ng", "--stdout", "-s", "160", "-p", "50", text],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning("espeak_failed", stderr=result.stderr.decode()[:100])
                return None

            audio_buffer = io.BytesIO(result.stdout)
            with wave.open(audio_buffer, "rb") as wav:
                sample_rate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16)

            logger.debug("espeak_synthesized", text_len=len(text), audio_samples=len(audio))
            return audio, sample_rate

        except FileNotFoundError:
            logger.error("espeak_not_found", hint="apt install espeak-ng")
            return None
        except Exception:
            logger.exception("espeak_error")
            return None

    @property
    def is_available(self) -> bool:
        return self._voice is not None or self._use_espeak_fallback
