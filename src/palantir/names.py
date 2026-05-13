"""Person-name display helpers."""

from __future__ import annotations

import re

_SPACES = re.compile(r"\s+")


def display_person_name(name: str | None) -> str | None:
    """Return a readable first-name-first display string.

    Student rosters often export names as "Last, First Middle". The app only
    has one name field, so normalize that format at every display boundary.
    """
    if name is None:
        return None

    cleaned = _SPACES.sub(" ", name.strip())
    if not cleaned or "," not in cleaned:
        return cleaned

    last, rest = cleaned.split(",", 1)
    first = _SPACES.sub(" ", rest.strip())
    last = _SPACES.sub(" ", last.strip())
    if not first or not last:
        return cleaned.replace(",", "")
    return f"{first} {last}"
