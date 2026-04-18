"""Conversation state management and persistence.

Stores conversation history in SQLite and provides retrieval
for building LLM context.
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger()


class ConversationManager:
    """Manages conversation history storage and retrieval."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def save_turn(
        self,
        user_text: str,
        assistant_text: str,
        person_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Save a conversation turn to the database."""
        self._db.execute(
            "INSERT INTO conversations (person_id, session_id, user_text, assistant_text) "
            "VALUES (?, ?, ?, ?)",
            (person_id, session_id, user_text, assistant_text),
        )
        self._db.commit()
        logger.debug(
            "conversation_saved",
            person_id=person_id,
            user_preview=user_text[:50],
        )

    def save_memory(
        self,
        fact: str,
        person_id: str | None = None,
        source: str = "conversation",
    ) -> None:
        """Store a persistent memory/fact about a person or topic."""
        self._db.execute(
            "INSERT INTO memory (person_id, fact, source) VALUES (?, ?, ?)",
            (person_id, fact, source),
        )
        self._db.commit()
        logger.debug("memory_saved", fact=fact[:80], person_id=person_id)

    def get_recent_turns(
        self, person_id: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Get recent conversation turns, optionally filtered by person."""
        if person_id:
            rows = self._db.execute(
                "SELECT user_text, assistant_text, created_at FROM conversations "
                "WHERE person_id = ? ORDER BY created_at DESC LIMIT ?",
                (person_id, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT user_text, assistant_text, created_at FROM conversations "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        # Return in chronological order
        turns = []
        for row in reversed(rows):
            turns.append({"role": "user", "content": row["user_text"]})
            turns.append({"role": "assistant", "content": row["assistant_text"]})
        return turns
