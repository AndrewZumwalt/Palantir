"""Offline fallback responder.

Provides deterministic, useful responses to common classroom requests when
the Claude API is unreachable. Keeps the assistant functional for:
  - Time / date queries
  - Attendance questions ("who is here")
  - Greetings
  - Visual queries (resolved from YOLO cache, not this module)
"""

from __future__ import annotations

import random
import re
from datetime import datetime

# Canned replies with slight variation to avoid sounding robotic
_GREETING_PATTERNS = [
    r"\bhello\b", r"\bhi\b", r"\bhey\b", r"\bgood morning\b",
    r"\bgood afternoon\b", r"\bgood evening\b",
]
_TIME_PATTERNS = [r"\btime\b", r"\bwhat time\b", r"\bo'?clock\b"]
_DATE_PATTERNS = [r"\bdate\b", r"\bwhat day\b", r"\btoday\b"]
_ATTENDANCE_PATTERNS = [
    r"\bwho('s| is)? here\b",
    r"\bwho('s| is)? in\b",
    r"\bhow many\b.*\b(people|students)\b",
    r"\battendance\b",
]
_HELP_PATTERNS = [r"\bhelp\b", r"\bwhat can you do\b"]
_GOODBYE_PATTERNS = [r"\bgoodbye\b", r"\bbye\b", r"\bsee you\b"]

_GREETINGS = [
    "Hello! I'm running in offline mode, but I can still help with basic questions.",
    "Hi there. My network's unavailable, but I can handle simple things.",
    "Hey! I'm here, though my full brain is offline right now.",
]

_HELP = (
    "I can tell you the time, the date, or who's in the classroom. "
    "My cloud connection is down, so I can't have deep conversations until it comes back."
)

_GOODBYES = ["Goodbye.", "See you later.", "Take care."]

_FALLBACK_APOLOGIES = [
    "I'm offline at the moment — I can't answer that right now.",
    "My connection is down, so I can't work that out. Try again in a minute?",
    "Network's out. I'll be back to normal as soon as it's restored.",
]


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def generate_offline_response(
    user_text: str,
    visible_person_names: list[str] | None = None,
    speaker_name: str | None = None,
) -> str:
    """Produce a best-effort response without the LLM.

    Args:
        user_text: the user's transcribed speech.
        visible_person_names: people the vision service currently sees.
        speaker_name: name of the speaker, if known.
    """
    text = user_text.lower().strip()
    visible_person_names = visible_person_names or []

    now = datetime.now()
    prefix = f"{speaker_name}, " if speaker_name else ""

    if _matches(text, _TIME_PATTERNS):
        return f"{prefix}it's {now.strftime('%I:%M %p').lstrip('0')}."

    if _matches(text, _DATE_PATTERNS):
        return f"{prefix}today is {now.strftime('%A, %B %d')}."

    if _matches(text, _ATTENDANCE_PATTERNS):
        if not visible_person_names:
            return "I don't see anyone in the classroom right now."
        if len(visible_person_names) == 1:
            return f"I see {visible_person_names[0]}."
        if len(visible_person_names) == 2:
            return f"I see {visible_person_names[0]} and {visible_person_names[1]}."
        names = ", ".join(visible_person_names[:-1])
        return (
            f"There are {len(visible_person_names)} people here: "
            f"{names}, and {visible_person_names[-1]}."
        )

    if _matches(text, _GREETING_PATTERNS):
        greeting = random.choice(_GREETINGS)
        return f"{speaker_name + '! ' if speaker_name else ''}{greeting}"

    if _matches(text, _GOODBYE_PATTERNS):
        return random.choice(_GOODBYES)

    if _matches(text, _HELP_PATTERNS):
        return _HELP

    return random.choice(_FALLBACK_APOLOGIES)
