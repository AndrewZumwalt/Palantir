"""Persistent operator-settings store backed by the `settings` SQLite table.

The motivation is small: we don't want operators to have to SSH into the
laptop to edit .env when they want to change an API key.  This module
exposes a tiny key/value API that:

  * Returns ``None`` for unknown keys so callers can transparently fall
    back to environment variables.
  * Treats empty strings as "delete" — the UI saves "" to clear a key.
  * Tracks ``updated_at`` so we can show a "last changed" timestamp.

Recognised keys (others are accepted but ignored by the rest of the
codebase) are listed in :data:`KNOWN_SETTINGS` so the API surface can
validate what the dashboard tries to write.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

# Settings the dashboard is allowed to read/write.  Everything else is
# rejected by the API layer to avoid the table becoming a junk drawer.
KNOWN_SETTINGS: frozenset[str] = frozenset(
    {
        "anthropic_api_key",
        "groq_api_key",
    }
)


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the stored value for `key`, or None if unset / empty."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    value = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    return value or None


def get_settings(conn: sqlite3.Connection, keys: Iterable[str]) -> dict[str, str]:
    """Bulk-fetch `keys`.  Missing or empty values are omitted."""
    keys = list(keys)
    if not keys:
        return {}
    placeholders = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        keys,
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        if isinstance(row, sqlite3.Row):
            k, v = row["key"], row["value"]
        else:
            k, v = row[0], row[1]
        if v:
            out[k] = v
    return out


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """Upsert `key`.  Empty/None deletes the row."""
    if not value:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()
        return
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET "
        "value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )
    conn.commit()


def resolved_api_keys(
    conn: sqlite3.Connection,
    *,
    env_anthropic: str = "",
    env_groq: str = "",
) -> tuple[str, str]:
    """Return (anthropic_key, groq_key), preferring the DB values.

    Used by the brain service at start and on reload so an operator who
    sets a key from the web UI sees it picked up without restarting the
    process.  Falls through to the env-var values from `load_config()`
    when the DB has nothing.
    """
    stored = get_settings(conn, ("anthropic_api_key", "groq_api_key"))
    return (
        stored.get("anthropic_api_key", env_anthropic),
        stored.get("groq_api_key", env_groq),
    )
