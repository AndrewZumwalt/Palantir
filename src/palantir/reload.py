"""Soft-reload protocol shared by all services.

The web API publishes a `SYSTEM_RELOAD` message with a `reload_id` and a list
of target service names. Each service subscribes and, if its name is in the
target list, invokes a service-specific reload coroutine that rebuilds its
stateful resources (models, caches, hardware handles) in-place. Progress is
streamed back via `SYSTEM_RELOAD_PROGRESS` messages which the web service
bridges to WebSocket clients.

This module is deliberately tiny — the real work lives in each service's
`_reload` method. Keeping the protocol in one place means the UI can trust a
consistent `{reload_id, service, status, message}` shape from every service.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import structlog

from palantir.redis_client import Channels, publish

logger = structlog.get_logger()

ReloadFn = Callable[[], Awaitable[None]]


async def handle_reload_request(
    redis,
    service_name: str,
    data: dict,
    reload_fn: ReloadFn,
) -> None:
    """Wire a service's reload coroutine into the SYSTEM_RELOAD protocol.

    Call this from a service's Redis subscription handler. It checks whether
    the reload request targets this service, runs `reload_fn`, and publishes
    start/success/error progress messages keyed by `reload_id`.
    """
    reload_id = data.get("reload_id", "")
    targets = data.get("services") or []
    if service_name not in targets:
        return

    async def _progress(status: str, message: str) -> None:
        await publish(
            redis,
            Channels.SYSTEM_RELOAD_PROGRESS,
            {
                "reload_id": reload_id,
                "service": service_name,
                "status": status,
                "message": message,
            },
        )

    await _progress("running", "reloading…")
    try:
        await reload_fn()
    except Exception as exc:  # pragma: no cover - reported to UI
        logger.exception("reload_failed", service=service_name)
        await _progress("error", f"{type(exc).__name__}: {exc}")
        return
    await _progress("ok", "reload complete")
