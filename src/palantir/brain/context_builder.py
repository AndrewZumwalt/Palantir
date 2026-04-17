"""Builds structured context for LLM calls from current room state.

Assembles information about who's speaking, who's visible, what objects
are detected, and recent conversation history into a formatted context
string for the Claude system prompt.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import redis.asyncio as aioredis
import structlog

from palantir.redis_client import Keys

logger = structlog.get_logger()


class ContextBuilder:
    """Assembles room context from Redis state and SQLite history."""

    def __init__(self, redis: aioredis.Redis, db: sqlite3.Connection):
        self._redis = redis
        self._db = db

    async def build(
        self,
        speaker_name: str | None = None,
        speaker_id: str | None = None,
    ) -> str:
        """Build a context string for the LLM.

        Args:
            speaker_name: Name of the person who just spoke.
            speaker_id: ID of the person who just spoke.

        Returns:
            Formatted context string for the system prompt.
        """
        sections = []

        # Who is speaking
        if speaker_name:
            sections.append(f"[SPEAKER] {speaker_name}")
        else:
            sections.append("[SPEAKER] Unknown person")

        # Who is visible in the room
        visible_data = await self._redis.hgetall(Keys.VISIBLE_PERSONS)
        if visible_data:
            names = []
            for person_id, data_str in visible_data.items():
                try:
                    data = json.loads(data_str)
                    names.append(f"  - {data.get('name', 'Unknown')} ({data.get('role', 'unknown')})")
                except (json.JSONDecodeError, TypeError):
                    names.append(f"  - Person {person_id}")
            sections.append("[VISIBLE PERSONS]\n" + "\n".join(names))
        else:
            sections.append("[VISIBLE PERSONS] None detected")

        # Present persons count
        present_count = await self._redis.scard(Keys.PRESENT_PERSONS)
        sections.append(f"[ATTENDANCE] {present_count} people present")

        # Cached detected objects
        object_cache = await self._redis.get(Keys.OBJECT_CACHE)
        if object_cache:
            try:
                objects = json.loads(object_cache)
                if objects:
                    obj_list = [f"  - {obj['label']}" for obj in objects[:15]]
                    sections.append("[VISIBLE OBJECTS]\n" + "\n".join(obj_list))
            except (json.JSONDecodeError, TypeError):
                pass

        # Recent conversation history with this person
        if speaker_id:
            history = self._get_recent_conversations(speaker_id, limit=5)
            if history:
                conv_lines = []
                for row in history:
                    conv_lines.append(f"  User: {row['user_text'][:100]}")
                    conv_lines.append(f"  Palantir: {row['assistant_text'][:100]}")
                sections.append("[RECENT CONVERSATION]\n" + "\n".join(conv_lines))

        # Key memories about this person
        if speaker_id:
            memories = self._get_person_memories(speaker_id, limit=5)
            if memories:
                mem_lines = [f"  - {row['fact']}" for row in memories]
                sections.append("[MEMORIES ABOUT SPEAKER]\n" + "\n".join(mem_lines))

        # Current time
        now = datetime.now()
        sections.append(f"[TIME] {now.strftime('%A, %B %d, %Y at %I:%M %p')}")

        return "\n\n".join(sections)

    def get_conversation_history(
        self, person_id: str | None, limit: int = 10
    ) -> list[dict]:
        """Get recent conversation turns formatted for the Claude messages array."""
        if not person_id:
            return []

        rows = self._get_recent_conversations(person_id, limit)
        history = []
        for row in rows:
            history.append({"role": "user", "content": row["user_text"]})
            history.append({"role": "assistant", "content": row["assistant_text"]})
        return history

    def _get_recent_conversations(self, person_id: str, limit: int = 5) -> list[sqlite3.Row]:
        """Fetch recent conversations for a person from the database."""
        return self._db.execute(
            "SELECT user_text, assistant_text FROM conversations "
            "WHERE person_id = ? ORDER BY created_at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()

    def _get_person_memories(self, person_id: str, limit: int = 5) -> list[sqlite3.Row]:
        """Fetch stored memories/facts about a person."""
        return self._db.execute(
            "SELECT fact FROM memory "
            "WHERE person_id = ? AND (expires_at IS NULL OR expires_at > datetime('now')) "
            "ORDER BY created_at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
