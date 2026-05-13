"""Curated TTS voice options exposed to the web UI."""

from __future__ import annotations

EDGE_VOICE_OPTIONS: tuple[dict[str, str], ...] = (
    {"id": "en-US-AriaNeural", "label": "Aria", "description": "US female, warm"},
    {"id": "en-US-JennyNeural", "label": "Jenny", "description": "US female, clear"},
    {"id": "en-US-GuyNeural", "label": "Guy", "description": "US male, casual"},
    {"id": "en-US-EricNeural", "label": "Eric", "description": "US male, neutral"},
    {"id": "en-US-MichelleNeural", "label": "Michelle", "description": "US female, steady"},
    {"id": "en-US-RogerNeural", "label": "Roger", "description": "US male, deeper"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia", "description": "British female"},
    {"id": "en-GB-RyanNeural", "label": "Ryan", "description": "British male"},
)


def is_known_voice(voice: str) -> bool:
    return voice in {option["id"] for option in EDGE_VOICE_OPTIONS}
