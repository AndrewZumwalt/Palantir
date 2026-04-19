"""FastAPI web server: REST API, WebSocket, and static SPA serving.

This is the main entry point for the palantir-web systemd service.
"""

from __future__ import annotations

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
from palantir.redis_client import Channels, Subscriber, create_redis

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: setup and teardown shared resources."""
    config = load_config()
    preflight = validate_for("web", config)
    log_and_check(preflight, fatal_on_error=False)

    redis = await create_redis(config)
    db = init_db(config)

    app.state.config = config
    app.state.redis = redis
    app.state.db = db
    app.state.ws_manager = WebSocketManager()
    app.state.start_time = time.monotonic()

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
        if config.auth_token:
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
