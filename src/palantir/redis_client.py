"""Redis client factory and pub/sub helpers for inter-service communication."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

import redis.asyncio as aioredis
import structlog

from palantir.config import PalantirConfig

logger = structlog.get_logger()

# Process-wide shared fakeredis server so every service/connection in a dev
# process sees the same pub/sub + keyspace. Only used when
# PALANTIR_REDIS_FAKE=1 is set.
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
    SYSTEM_RELOAD = "system:reload"
    SYSTEM_RELOAD_PROGRESS = "system:reload:progress"
    # ----- Pi <-> laptop relay (only used when input.source = "relay") -----
    # Web service publishes raw sensor data here after decoding from the
    # Pi's WebSocket; audio + vision services subscribe instead of opening
    # local hardware.
    RELAY_AUDIO_IN = "relay:audio:in"        # bytes: int16 LE PCM, 16 kHz mono
    RELAY_VIDEO_FRAME = "relay:video:frame"  # bytes: JPEG-encoded BGR frame
    RELAY_GPIO = "relay:gpio"                # JSON GPIO event from Pi
    # Reverse direction: TTS service publishes synthesized PCM here, web
    # forwards each chunk to the Pi for playback.
    RELAY_AUDIO_OUT = "relay:audio:out"      # bytes: int16 LE PCM
    RELAY_HARDWARE_CMD = "relay:hardware"    # JSON LED/relay commands
    RELAY_STATUS = "relay:status"            # JSON connection state


# Redis key names for ephemeral state
class Keys:
    VISIBLE_PERSONS = "state:visible_persons"
    PRESENT_PERSONS = "state:present_persons"
    ACTIVE_CONVERSATION = "state:active_conversation"
    PRIVACY_MODE = "state:privacy_mode"
    OBJECT_CACHE = "state:object_cache"
    LATEST_FRAME = "state:latest_frame"
    SERVICE_STATUS = "state:service_status"


async def create_redis(
    config: PalantirConfig,
    *,
    decode_responses: bool = True,
) -> aioredis.Redis:
    """Create a Redis connection, trying Unix socket first then TCP fallback.

    Set `PALANTIR_REDIS_FAKE=1` to use an in-process fakeredis instead
    (development/testing on machines without a real redis-server).

    `decode_responses=True` (the default) returns str values — what most
    of the codebase expects.  Set `False` to get raw bytes back; needed
    for the relay binary channels (PCM, JPEG) where utf-8 decode would
    corrupt the payload.  Use `create_binary_redis()` for that case.
    """
    if os.environ.get("PALANTIR_REDIS_FAKE") == "1":
        global _fake_server
        try:
            import fakeredis
            import fakeredis.aioredis
        except ImportError as e:
            raise RuntimeError(
                "PALANTIR_REDIS_FAKE=1 but fakeredis is not installed. "
                "Install dev extras: pip install -e '.[dev]'"
            ) from e
        if _fake_server is None:
            _fake_server = fakeredis.FakeServer()
        r = fakeredis.aioredis.FakeRedis(
            server=_fake_server, decode_responses=decode_responses
        )
        await r.ping()
        logger.info(
            "redis_connected",
            url="fakeredis://in-process",
            decode_responses=decode_responses,
        )
        return r

    r = aioredis.from_url(config.redis.url, decode_responses=decode_responses)
    try:
        await r.ping()
        logger.info(
            "redis_connected", url=config.redis.url, decode_responses=decode_responses
        )
        return r
    except (ConnectionError, OSError):
        logger.warning("redis_unix_socket_failed", url=config.redis.url)
        # Free the half-open client before we try the fallback.
        try:
            await r.close()
        except Exception:
            logger.debug("redis_primary_close_failed", exc_info=True)

    r = aioredis.from_url(config.redis.fallback_url, decode_responses=decode_responses)
    await r.ping()
    logger.info(
        "redis_connected",
        url=config.redis.fallback_url,
        decode_responses=decode_responses,
    )
    return r


async def create_binary_redis(config: PalantirConfig) -> aioredis.Redis:
    """Convenience wrapper: a Redis client that returns raw bytes.

    Use this for the relay channels (`relay:audio:*`, `relay:video:*`)
    where payloads are PCM / JPEG and would be mangled by utf-8 decode.
    """
    return await create_redis(config, decode_responses=False)


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
