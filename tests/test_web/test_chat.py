from __future__ import annotations

from palantir.web.routers import chat


async def test_clear_history_deletes_conversation_rows(temp_db):
    temp_db.execute(
        "INSERT INTO conversations (user_text, assistant_text) VALUES (?, ?)",
        ("hello", "hi"),
    )
    temp_db.commit()

    result = await chat.clear_history(db=temp_db)

    assert result == {"cleared": True, "deleted": 1}
    count = temp_db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert count == 0
