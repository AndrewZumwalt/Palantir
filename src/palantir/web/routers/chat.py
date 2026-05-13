"""Chat history + text-input endpoints.

Surfaces the brain's saved conversation turns to the dashboard, and lets
the operator type a message that flows through the same brain pipeline as
a transcribed voice utterance.

Design:
* GET /api/chat/history -- read-only list of recent conversations from the
  SQLite `conversations` table, joined to `persons` so the UI can display
  who Claude attributed each turn to.
* POST /api/chat/message -- publishes an Utterance on AUDIO_UTTERANCE so
  the brain's existing `_on_utterance` handler runs identity inference,
  context build, LLM call, and save_turn.  The endpoint returns
  immediately; the client polls /history for the response.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from palantir.models import Utterance
from palantir.names import display_person_name
from palantir.redis_client import Channels, publish
from palantir.web.dependencies import get_db, get_redis, verify_auth
from palantir.web.rate_limit import rate_limit_read, rate_limit_write

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(verify_auth)])


@router.get("/history", dependencies=[Depends(rate_limit_read)])
async def list_history(
    limit: int = 50,
    db: sqlite3.Connection = Depends(get_db),
):
    """Return recent conversation turns, newest first.

    Each turn is the user's text and the assistant's reply, plus the
    person Claude thought was speaking (if any).
    """
    limit = max(1, min(limit, 200))
    rows = db.execute(
        """
        SELECT c.id, c.created_at, c.user_text, c.assistant_text, c.person_id,
               p.name AS person_name
          FROM conversations c
          LEFT JOIN persons p ON p.id = c.person_id
         ORDER BY c.created_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return {
        "turns": [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "user_text": r["user_text"],
                "assistant_text": r["assistant_text"],
                "person_id": r["person_id"],
                "person_name": display_person_name(r["person_name"]),
            }
            for r in rows
        ],
    }


class TextMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/message", dependencies=[Depends(rate_limit_write)])
async def send_text_message(
    msg: TextMessage,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Publish a typed message as if it were a transcribed utterance.

    The brain's existing handler picks it up, runs identity inference
    (if exactly one person is visible they're attributed as the speaker;
    otherwise the turn is anonymous), calls the LLM, and saves the turn.
    Frontend polls /history to see the response land.
    """
    text = msg.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    utterance = Utterance(
        text=text,
        speaker_embedding=None,
        duration_seconds=0.0,
        source="typed",
        timestamp=datetime.now(),
    )
    await publish(redis, Channels.AUDIO_UTTERANCE, utterance)
    return {"queued": True, "text": text}


@router.delete("/history", dependencies=[Depends(rate_limit_write)])
async def clear_history(db: sqlite3.Connection = Depends(get_db)):
    """Delete all saved conversation turns."""
    cursor = db.execute("DELETE FROM conversations")
    db.commit()
    return {"cleared": True, "deleted": cursor.rowcount}
