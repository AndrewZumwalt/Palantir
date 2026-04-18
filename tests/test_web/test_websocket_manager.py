"""Tests for the WebSocketManager broadcast behavior."""

from __future__ import annotations

import asyncio

from palantir.web.websocket import WebSocketManager


class _FakeWebSocket:
    """Minimal stand-in for a Starlette WebSocket."""

    def __init__(self, *, slow: float = 0.0, fail: bool = False):
        self.sent: list[str] = []
        self.slow = slow
        self.fail = fail
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.fail:
            raise RuntimeError("simulated client failure")
        self.sent.append(message)


async def test_broadcast_fans_out_to_all_clients():
    mgr = WebSocketManager()
    a, b = _FakeWebSocket(), _FakeWebSocket()
    await mgr.connect(a)
    await mgr.connect(b)

    await mgr.broadcast("test", {"value": 1})

    assert len(a.sent) == 1
    assert len(b.sent) == 1
    assert '"channel": "test"' in a.sent[0]


async def test_broadcast_drops_failed_clients():
    mgr = WebSocketManager()
    good = _FakeWebSocket()
    bad = _FakeWebSocket(fail=True)
    await mgr.connect(good)
    await mgr.connect(bad)

    await mgr.broadcast("ch", {})

    # Good client still connected; bad one was pruned
    assert mgr.client_count == 1
    assert len(good.sent) == 1


async def test_broadcast_does_not_block_connects():
    """Connect/disconnect should not be held up by a slow send.

    Before the fix, the broadcast held the lock while awaiting each
    `send_text`, so connecting a new client serialized behind the slowest
    active client.
    """
    mgr = WebSocketManager()
    slow = _FakeWebSocket(slow=0.1)
    await mgr.connect(slow)

    async def do_broadcast() -> None:
        await mgr.broadcast("ch", {"big": True})

    async def do_connect() -> None:
        await asyncio.sleep(0.02)  # start mid-broadcast
        await mgr.connect(_FakeWebSocket())

    start = asyncio.get_event_loop().time()
    await asyncio.gather(do_broadcast(), do_connect())
    elapsed = asyncio.get_event_loop().time() - start

    # Connect should finish well before the slow broadcast; total time should
    # be dominated by the one slow send (~0.1s). Allow slack for Windows timing.
    assert elapsed < 0.5
    assert mgr.client_count == 2
