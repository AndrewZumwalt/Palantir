"""FastAPI web server: REST API, WebSocket, and static SPA serving.

This is the main entry point for the palantir-web systemd service.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from palantir.config import PalantirConfig, load_config
from palantir.db import init_db
from palantir.logging import setup_logging
from palantir.preflight import log_and_check, validate_for
from palantir.redis_client import (
    Channels,
    Subscriber,
    create_binary_redis,
    create_redis,
)
from palantir.relay.protocol import Frame, Op

from .routers import (
    attendance,
    automation,
    dashboard,
    engagement,
    enrollment,
    events,
    settings,
    system,
)
from .websocket import WebSocketManager

logger = structlog.get_logger()

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
MAX_RELAY_AUDIO_PAYLOAD_BYTES = 256 * 1024
MAX_RELAY_VIDEO_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_RELAY_FRAME_BYTES = MAX_RELAY_VIDEO_PAYLOAD_BYTES + 1
MAX_RELAY_AUDIO_FRAMES_PER_SECOND = 80
MAX_RELAY_VIDEO_FRAMES_PER_SECOND = 30


def _relay_payload_limit(op: Op) -> int | None:
    if op == Op.AUDIO_IN:
        return MAX_RELAY_AUDIO_PAYLOAD_BYTES
    if op == Op.VIDEO_FRAME:
        return MAX_RELAY_VIDEO_PAYLOAD_BYTES
    return None


def _relay_rate_limit(op: Op) -> int | None:
    if op == Op.AUDIO_IN:
        return MAX_RELAY_AUDIO_FRAMES_PER_SECOND
    if op == Op.VIDEO_FRAME:
        return MAX_RELAY_VIDEO_FRAMES_PER_SECOND
    return None


def _relay_payload_too_large(frame: Frame) -> bool:
    limit = _relay_payload_limit(frame.op)
    return limit is not None and len(frame.payload) > limit


class _RelayFrameLimiter:
    def __init__(self, *, now: float | None = None, window_seconds: float = 1.0):
        self._window_started = time.monotonic() if now is None else now
        self._window_seconds = window_seconds
        self._counts: dict[Op, int] = {}

    def check(self, op: Op, *, now: float | None = None) -> bool:
        limit = _relay_rate_limit(op)
        if limit is None:
            return True

        current = time.monotonic() if now is None else now
        if current - self._window_started >= self._window_seconds:
            self._window_started = current
            self._counts.clear()

        count = self._counts.get(op, 0) + 1
        self._counts[op] = count
        return count <= limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: setup and teardown shared resources."""
    config = load_config()
    preflight = validate_for("web", config)
    if not log_and_check(preflight, fatal_on_error=False):
        raise RuntimeError("web preflight failed")

    redis = await create_redis(config)
    # Second client for binary relay channels (PCM, JPEG); only used by
    # /relay/ws — but always created so the endpoint doesn't have to lazy-
    # initialise on first connect.
    binary_redis = await create_binary_redis(config)
    db = init_db(config)

    app.state.config = config
    app.state.redis = redis
    app.state.binary_redis = binary_redis
    app.state.db = db
    app.state.ws_manager = WebSocketManager()
    app.state.start_time = time.monotonic()
    # At most one Pi may be connected at a time.  Stored as a WebSocket
    # so a duplicate connection attempt can see the existing one.
    app.state.relay_pi: WebSocket | None = None

    # Bridge Redis Pub/Sub to WebSocket clients
    subscriber = Subscriber(redis)
    bridge_channels = [
        Channels.VISION_FACES,
        Channels.VISION_ENGAGEMENT,
        Channels.SYSTEM_STATUS,
        Channels.SYSTEM_PRIVACY,
        Channels.SYSTEM_RELOAD_PROGRESS,
        Channels.EVENTS_LOG,
    ]
    for channel in bridge_channels:

        async def _bridge(data: dict, ch: str = channel) -> None:
            await app.state.ws_manager.broadcast(ch, data)

        subscriber.on(channel, _bridge)

    # Cache latest ServiceStatus per service for the /api/system/status endpoint
    async def _cache_status(data: dict) -> None:
        name = data.get("name")
        if name:
            await redis.set(f"status:{name}", json.dumps(data), ex=60)

    subscriber.on(Channels.SYSTEM_STATUS, _cache_status)

    await subscriber.start()
    app.state.subscriber = subscriber

    logger.info("web_service_started", port=config.web.port)
    yield

    # Shutdown
    await subscriber.stop()
    db.close()
    await redis.close()
    try:
        await binary_redis.close()
    except Exception:
        logger.debug("binary_redis_close_failed", exc_info=True)
    logger.info("web_service_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Palantir",
        description="AI Classroom Assistant",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS for local development (frontend dev server)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(dashboard.router)
    app.include_router(settings.router)
    app.include_router(enrollment.router)
    app.include_router(attendance.router)
    app.include_router(engagement.router)
    app.include_router(events.router)
    app.include_router(automation.router)
    app.include_router(system.router)

    # Health check (no auth required)
    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "uptime_seconds": time.monotonic() - app.state.start_time,
            "ws_clients": app.state.ws_manager.client_count,
        }

    # WebSocket endpoint for real-time dashboard
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        manager: WebSocketManager = app.state.ws_manager

        # Optional auth check for WebSocket
        config: PalantirConfig = app.state.config
        if not config.auth_token:
            if config.is_production:
                await websocket.close(code=1011, reason="Authentication not configured")
                return
        else:
            token = websocket.query_params.get("token") or ""
            if not secrets.compare_digest(token, config.auth_token):
                await websocket.close(code=4001, reason="Unauthorized")
                return

        await manager.connect(websocket)
        try:
            # Drain any inbound frames to keep the connection alive; we don't
            # act on client->server messages today, but WebSocketDisconnect is
            # only raised when we try to read.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(websocket)

    # Pi <-> laptop relay endpoint.  The Pi opens this WebSocket and pumps
    # mic + camera frames; we publish them to Redis so the audio + vision
    # services pick them up unchanged.  TTS audio + hardware commands flow
    # the other direction.
    @app.websocket("/relay/ws")
    async def relay_endpoint(websocket: WebSocket):
        config: PalantirConfig = app.state.config

        # Auth: same bearer token as the dashboard WS
        if not config.auth_token:
            if config.is_production:
                await websocket.close(code=1011, reason="Authentication not configured")
                return
        else:
            token = websocket.query_params.get("token") or ""
            if not secrets.compare_digest(token, config.auth_token):
                await websocket.close(code=4001, reason="Unauthorized")
                return

        # One Pi at a time.  Any prior connection is kicked.
        prior = getattr(app.state, "relay_pi", None)
        if prior is not None:
            try:
                await prior.close(code=4000, reason="superseded")
            except Exception:
                pass

        await websocket.accept()
        app.state.relay_pi = websocket
        text_redis = app.state.redis
        bin_redis = app.state.binary_redis
        relay_limiter = _RelayFrameLimiter()

        # Announce that the Pi is online so the dashboard can react.
        try:
            await text_redis.publish(
                Channels.RELAY_STATUS, json.dumps({"connected": True})
            )
        except Exception:
            logger.debug("relay_status_publish_failed", exc_info=True)
        logger.info("relay_pi_connected", client=str(websocket.client))

        # Reverse direction: Redis -> Pi.  Two subscribers — one binary
        # for synthesized PCM, one text for hardware commands.
        async def forward_audio_out() -> None:
            pubsub = bin_redis.pubsub()
            await pubsub.subscribe(Channels.RELAY_AUDIO_OUT)
            try:
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    data = msg.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        await websocket.send_bytes(
                            Frame(Op.AUDIO_OUT, bytes(data)).encode()
                        )
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:
                logger.debug("relay_audio_out_forward_failed", exc_info=True)
            finally:
                try:
                    await pubsub.unsubscribe(Channels.RELAY_AUDIO_OUT)
                    await pubsub.close()
                except Exception:
                    logger.debug("audio_out_pubsub_close_failed", exc_info=True)

        async def forward_hardware_cmd() -> None:
            pubsub = text_redis.pubsub()
            await pubsub.subscribe(Channels.RELAY_HARDWARE_CMD)
            try:
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    raw = msg.get("data")
                    if not isinstance(raw, str):
                        continue
                    try:
                        cmd = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    kind = cmd.get("kind")
                    if kind == "led":
                        await websocket.send_bytes(
                            Frame.led(
                                float(cmd.get("r", 0.0)),
                                float(cmd.get("g", 0.0)),
                                float(cmd.get("b", 0.0)),
                            ).encode()
                        )
                    elif kind == "relay":
                        await websocket.send_bytes(
                            Frame.relay(
                                int(cmd["pin"]), bool(cmd.get("state", False))
                            ).encode()
                        )
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:
                logger.debug("relay_hardware_forward_failed", exc_info=True)
            finally:
                try:
                    await pubsub.unsubscribe(Channels.RELAY_HARDWARE_CMD)
                    await pubsub.close()
                except Exception:
                    logger.debug("hardware_pubsub_close_failed", exc_info=True)

        forward_tasks = [
            asyncio.create_task(forward_audio_out()),
            asyncio.create_task(forward_hardware_cmd()),
        ]

        # Forward direction: Pi -> Redis.  Loop until disconnect.
        try:
            while True:
                message = await websocket.receive_bytes()
                if len(message) > MAX_RELAY_FRAME_BYTES:
                    logger.warning(
                        "relay_frame_too_large",
                        size=len(message),
                        limit=MAX_RELAY_FRAME_BYTES,
                    )
                    continue

                try:
                    frame = Frame.decode(message)
                except ValueError:
                    logger.debug("relay_bad_frame", size=len(message))
                    continue
                if _relay_payload_too_large(frame):
                    logger.warning(
                        "relay_payload_too_large",
                        op=int(frame.op),
                        size=len(frame.payload),
                        limit=_relay_payload_limit(frame.op),
                    )
                    continue
                if not relay_limiter.check(frame.op):
                    logger.warning("relay_frame_rate_limited", op=int(frame.op))
                    continue

                if frame.op == Op.AUDIO_IN:
                    # PCM bytes — publish straight through.
                    await text_redis.publish(Channels.RELAY_AUDIO_IN, frame.payload)
                elif frame.op == Op.VIDEO_FRAME:
                    await text_redis.publish(
                        Channels.RELAY_VIDEO_FRAME, frame.payload
                    )
                elif frame.op == Op.GPIO_EVENT:
                    try:
                        evt = frame.json()
                    except Exception:
                        continue
                    # Privacy switch is the only GPIO event that today has
                    # a system-wide effect — fan it out on SYSTEM_PRIVACY
                    # so the audio/vision services react.  Everything else
                    # goes onto RELAY_GPIO for whoever wants it.
                    if evt.get("event") == "privacy" and "state" in evt:
                        enabled = bool(evt["state"])
                        await text_redis.set(
                            "state:privacy_mode", "1" if enabled else "0"
                        )
                        await text_redis.publish(
                            Channels.SYSTEM_PRIVACY,
                            json.dumps({"enabled": enabled}),
                        )
                    else:
                        await text_redis.publish(
                            Channels.RELAY_GPIO, json.dumps(evt)
                        )
                elif frame.op == Op.HELLO:
                    try:
                        info = frame.json()
                    except Exception:
                        info = {}
                    logger.info(
                        "relay_pi_hello",
                        version=info.get("version"),
                        hostname=info.get("hostname"),
                    )
                elif frame.op == Op.PING:
                    pass
                elif frame.op == Op.ERROR:
                    try:
                        info = frame.json()
                    except Exception:
                        info = {}
                    logger.warning("relay_pi_error", **info)
                else:
                    logger.debug("relay_unhandled_op", op=int(frame.op))
        except WebSocketDisconnect:
            logger.info("relay_pi_disconnected")
        except Exception:
            logger.exception("relay_endpoint_error")
        finally:
            for t in forward_tasks:
                t.cancel()
            for t in forward_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if app.state.relay_pi is websocket:
                app.state.relay_pi = None
            try:
                await text_redis.publish(
                    Channels.RELAY_STATUS, json.dumps({"connected": False})
                )
            except Exception:
                logger.debug("relay_status_publish_failed", exc_info=True)

    # Serve frontend SPA (production build)
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app


def main() -> None:
    """Entry point for the palantir-web service."""
    setup_logging("web")
    config = load_config()

    # Optional TLS. Self-sign on first run if the user configured paths but
    # hasn't provisioned a cert yet — typical home deployment.
    ssl_kwargs: dict = {}
    if config.web.tls_cert_file and config.web.tls_key_file:
        from .tls import ensure_tls_materials

        materials = ensure_tls_materials(
            config.web.tls_cert_file, config.web.tls_key_file
        )
        if materials:
            ssl_kwargs["ssl_certfile"], ssl_kwargs["ssl_keyfile"] = materials
            logger.info("web_tls_enabled", cert=ssl_kwargs["ssl_certfile"])
        else:
            logger.warning("web_tls_disabled_missing_materials")

    uvicorn.run(
        "palantir.web.main:create_app",
        factory=True,
        host=config.web.host,
        port=config.web.port,
        workers=1,
        log_level="info",
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
