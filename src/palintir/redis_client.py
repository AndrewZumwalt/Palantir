"""Redis client factory and pub/sub helpers for inter-service communication."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

import redis.asyncio as aioredis
import structlog

from palintir.config import PalintirConfig

logger = structlog.get_logger()

# Process-wide shared fakeredis server so every service/connection in a dev
# process sees the same pub/sub + keyspace. Only used when
# PALINTIR_REDIS_FAKE=1 is set.
_fake_server = None


# Redis channel names
class Channels:
    AUDIO_WAKE = "audio:wake"
    AUDIO_UTTERANCE = "audio:utterance"
    AUDIO_SPEAKER_ID = "audio:speaker_id"
    VISION_FACES = "vision:faces"
    VISION_OBJECTS = "vision:objects"
    VISION_ENGAGEMENT = "vision:engagement"
    BRAIN_RESPONSE = "brain:response"
    BRAIN_ACTION = "brain:action"
    EVENTS_LOG = "events:log"
    SYSTEM_PRIVACY = "system:privacy"
    SYSTEM_STATUS = "system:status"


# Redis key names for ephemeral state
class Keys:
    VISIBLE_PERSONS = "state:visible_persons"
    PRESENT_PERSONS = "state:present_persons"
    ACTIVE_CONVERSATION = "state:active_conversation"
    PRIVACY_MODE = "state:privacy_mode"
    OBJECT_CACHE = "state:object_cache"
    LATEST_FRAME = "state:latest_frame"
    SERVICE_STATUS = "state:service_status"


async def create_redis(config: PalintirConfig) -> aioredis.Redis:
    """Create a Redis connection, trying Unix socket first then TCP fallback.

    Set `PALINTIR_REDIS_FAKE=1` to use an in-process fakeredis instead
    (development/testing on machines without a real redis-server).
    """
    if os.environ.get("PALINTIR_REDIS_FAKE") == "1":
        global _fake_server
        try:
            import fakeredis
            import fakeredis.aioredis
        except ImportError as e:
            raise RuntimeError(
                "PALINTIR_REDIS_FAKE=1 but fakeredis is not installed. "
                "Install dev extras: pip install -e '.[dev]'"
            ) from e
        if _fake_server is None:
            _fake_server = fakeredis.FakeServer()
        r = fakeredis.aioredis.FakeRedis(
            server=_fake_server, decode_responses=True
        )
        await r.ping()
        logger.info("redis_connected", url="fakeredis://in-process")
        return r

    try:
        r = aioredis.from_url(config.redis.url, decode_responses=True)
        await r.ping()
        logger.info("redis_connected", url=config.redis.url)
        return r
    except (ConnectionError, OSError):
        logger.warning("redis_unix_socket_failed", url=config.redis.url)

    r = aioredis.from_url(config.redis.fallback_url, decode_responses=True)
    await r.ping()
    logger.info("redis_connected", url=config.redis.fallback_url)
    return r


async def publish(redis: aioredis.Redis, channel: str, data: Any) -> None:
    """Publish a Pydantic model or dict to a Redis channel as JSON."""
    if hasattr(data, "model_dump_json"):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data, default=str)
    await redis.publish(channel, payload)


class Subscriber:
    """Async Redis Pub/Sub subscriber that routes messages to handlers."""

    def __init__(self, redis: aioredis.Redis):
        self._redis = redis
        self._pubsub = redis.pubsub()
        self._handlers: dict[str, list[Callable]] = {}
        self._task: asyncio.Task | None = None

    def on(self, channel: str, handler: Callable) -> None:
        """Register a handler for a channel. Handler receives parsed JSON data."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    async def start(self) -> None:
        """Subscribe to all registered channels and start listening."""
        if not self._handlers:
            return

        await self._pubsub.subscribe(*self._handlers.keys())
        self._task = asyncio.create_task(self._listen())
        logger.info("subscriber_started", channels=list(self._handlers.keys()))

    async def _listen(self) -> None:
        """Listen loop that dispatches messages to registered handlers."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue

                channel = message["channel"]
                handlers = self._handlers.get(channel, [])

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    data = message["data"]

                for handler in handlers:
                    try:
                        result = handler(data)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("handler_error", channel=channel)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Unsubscribe and stop listening."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._pubsub.unsubscribe()
        await self._pubsub.close()
