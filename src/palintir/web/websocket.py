"""WebSocket manager for real-time dashboard updates."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class WebSocketManager:
    """Manages WebSocket connections and broadcasts updates to all connected clients."""

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        logger.info("ws_client_connected", total=len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
        logger.info("ws_client_disconnected", total=len(self._connections))

    async def broadcast(self, channel: str, data: dict) -> None:
        """Send a message to all connected WebSocket clients."""
        message = json.dumps({"channel": channel, "data": data})
        async with self._lock:
            stale = []
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception:
                    stale.append(ws)
            for ws in stale:
                self._connections.remove(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)
