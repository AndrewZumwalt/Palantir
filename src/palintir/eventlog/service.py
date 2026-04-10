"""Event log service: event persistence, attendance tracking, score aggregation.

This is the main entry point for the palintir-eventlog systemd service.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time

import structlog

from palintir.config import load_config
from palintir.db import init_db
from palintir.logging import setup_logging
from palintir.models import Event, ServiceStatus
from palintir.redis_client import Channels, Subscriber, create_redis, publish

logger = structlog.get_logger()


class EventLogService:
    """Persists events to SQLite, manages attendance state, computes engagement scores."""

    def __init__(self):
        self._config = load_config()
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._db = None
        self._running = False
        self._start_time = time.monotonic()
        self._events_logged = 0

    async def start(self) -> None:
        self._redis = await create_redis(self._config)
        self._db = init_db(self._config)

        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.EVENTS_LOG, self._on_event)
        await self._subscriber.start()

        self._running = True
        logger.info("eventlog_service_started")
        await self._publish_status(healthy=True)

    async def _on_event(self, data: dict) -> None:
        """Persist an event to the database."""
        try:
            event = Event(**data)
            self._db.execute(
                "INSERT INTO events (type, person_id, data, created_at) VALUES (?, ?, ?, ?)",
                (event.type.value, event.person_id, json.dumps(event.data), event.timestamp.isoformat()),
            )
            self._db.commit()
            self._events_logged += 1
        except Exception:
            logger.exception("event_persist_error")

    async def _run_retention_cleanup(self) -> None:
        """Delete events older than the retention period."""
        retention_days = self._config.privacy.data_retention_days
        self._db.execute(
            "DELETE FROM events WHERE created_at < datetime('now', ?)",
            (f"-{retention_days} days",),
        )
        self._db.execute(
            "DELETE FROM engagement_samples WHERE sampled_at < datetime('now', '-30 days')"
        )
        self._db.commit()
        logger.info("retention_cleanup_complete", retention_days=retention_days)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="eventlog",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={"events_logged": self._events_logged},
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        cleanup_counter = 0
        try:
            while self._running:
                await self._publish_status(healthy=True)
                await asyncio.sleep(10)
                # Run retention cleanup once per hour
                cleanup_counter += 1
                if cleanup_counter >= 360:  # 360 * 10s = 1 hour
                    await self._run_retention_cleanup()
                    cleanup_counter = 0
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._subscriber:
            await self._subscriber.stop()
        if self._db:
            self._db.close()
        if self._redis:
            await self._redis.close()
        logger.info("eventlog_service_stopped")


def main() -> None:
    setup_logging("eventlog")
    service = EventLogService()
    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
