"""Edge TTS engine: Microsoft's free neural voices via the public Edge endpoint.

Why this exists: Piper's `lessac-medium` voice sounds noticeably synthetic
("you can tell it's a robot"), and the `piper-tts` Python library doesn't
actually auto-download voice files like the comment in PiperEngine claims --
PiperVoice.load() expects a path to a local `.onnx` file, so the historical
TTS service was either silent or falling back to espeak (which isn't even
installed on Windows).

edge-tts hits the same neural voices Microsoft Edge uses for "Read Aloud" --
genuinely conversational, no API key, no per-character cost.  Trade-offs:
internet required (the synthesis call is to a Microsoft Azure endpoint),
~200-500 ms of network latency before audio starts.  For an in-class demo
this is fine; for fully-offline operation, fall back to Piper.

Decoding: edge-tts returns audio_24khz_48kbitrate_mono_mp3 (Microsoft's
endpoint doesn't expose PCM-formatted output the way the paid Azure API
does).  We decode the MP3 to int16 PCM with miniaudio so the existing
LocalAudioOutput / RelayAudioOutput sinks consume it unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import structlog

from palantir.config import TTSConfig

logger = structlog.get_logger()

try:
    import edge_tts

    _EDGE_AVAILABLE = True
except ImportError:
    _EDGE_AVAILABLE = False

try:
    import miniaudio

    _MINIAUDIO_AVAILABLE = True
except ImportError:
    _MINIAUDIO_AVAILABLE = False


# Default voice.  AriaNeural is Microsoft's flagship US English female
# voice -- warm + classroom-appropriate.  Other good options:
#   en-US-GuyNeural        (male, casual)
#   en-US-JennyNeural      (female, professional)
#   en-US-EricNeural       (male, neutral)
#   en-GB-RyanNeural       (British male)
#   en-GB-SoniaNeural      (British female)
# Full list: `edge-tts --list-voices` from the venv.
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"


class EdgeTTSEngine:
    """Synthesizes speech via Microsoft Edge TTS (neural voices, free).

    Matches the PiperEngine interface (`is_available`, `synthesize`) so
    the TTS service can swap between them without other changes.
    """

    def __init__(self, config: TTSConfig):
        self._config = config
        # Honor an explicit `tts.voice` from config if it looks like an
        # edge voice (contains a "Neural" suffix or matches the
        # `xx-XX-NameNeural` shape); otherwise use Aria.
        configured_voice = getattr(config, "voice", "") or ""
        if "Neural" in configured_voice and "-" in configured_voice:
            self._voice = configured_voice
        else:
            self._voice = DEFAULT_EDGE_VOICE
        self._available = _EDGE_AVAILABLE and _MINIAUDIO_AVAILABLE

        if not _EDGE_AVAILABLE:
            logger.warning("edge_tts_not_installed", hint="pip install edge-tts")
            return
        if not _MINIAUDIO_AVAILABLE:
            logger.warning(
                "miniaudio_not_installed",
                hint="pip install miniaudio (needed to decode the MP3 Edge returns)",
            )
            return

        logger.info("edge_tts_initialized", voice=self._voice)

    @property
    def is_available(self) -> bool:
        return self._available

    def synthesize(self, text: str) -> tuple[np.ndarray, int] | None:
        """Synthesize `text` and return (int16 PCM samples, sample_rate).

        Returns None if synthesis fails -- the TTS service will log and
        keep going so a transient network blip doesn't take the speaker
        offline.
        """
        if not self._available or not text.strip():
            return None
        try:
            # edge-tts is async-only.  We're called from a thread pool
            # executor (TTS service runs synthesize in run_in_executor),
            # so a fresh event loop here is fine -- the thread doesn't
            # have one yet.
            mp3_bytes = asyncio.run(self._fetch_mp3(text))
            if not mp3_bytes:
                logger.warning("edge_tts_empty_response", text_preview=text[:60])
                return None

            decoded = miniaudio.decode(
                mp3_bytes,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=24000,
            )
            # `decoded.samples` is a `bytearray` of int16 little-endian PCM.
            audio = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
            logger.debug(
                "edge_tts_synthesized",
                text_len=len(text),
                audio_samples=len(audio),
                sample_rate=decoded.sample_rate,
            )
            return audio, decoded.sample_rate
        except Exception:
            logger.exception("edge_tts_synthesis_failed", voice=self._voice)
            return None

    async def _fetch_mp3(self, text: str) -> bytes:
        """Collect every audio chunk from edge-tts into a single MP3 blob."""
        chunks: list[bytes] = []
        communicate = edge_tts.Communicate(text, voice=self._voice)
        async for evt in communicate.stream():
            # edge-tts streams a mix of audio + WordBoundary events; we
            # only want the audio frames.
            if evt.get("type") == "audio" and evt.get("data"):
                chunks.append(evt["data"])
        return b"".join(chunks)


def make_engine(config: TTSConfig) -> Any:
    """Return whichever TTS engine the config selects, falling back gracefully.

    Order of preference:
      1. config.engine == "edge"  -> EdgeTTSEngine if available
      2. config.engine == "piper" -> PiperEngine
      3. fallback -> EdgeTTSEngine if available else PiperEngine
    """
    requested = (getattr(config, "engine", "") or "").lower()

    if requested == "edge":
        engine = EdgeTTSEngine(config)
        if engine.is_available:
            return engine
        # Edge requested but unavailable -- fall through to Piper.
        logger.warning("edge_tts_unavailable_falling_back_to_piper")

    from .piper_engine import PiperEngine

    if requested == "piper":
        return PiperEngine(config)

    # Default: prefer Edge for naturalness, Piper as backup.
    engine = EdgeTTSEngine(config)
    if engine.is_available:
        return engine
    return PiperEngine(config)
