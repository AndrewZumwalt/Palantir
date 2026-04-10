"""Brain service: LLM orchestration, identity linking, automation.

This is the main entry point for the palintir-brain systemd service.
"""

from __future__ import annotations

import asyncio
import signal
import time

import structlog

from palintir.config import load_config
from palintir.db import init_db
from palintir.logging import setup_logging
from palintir.models import PrivacyModeEvent, ServiceStatus
from palintir.redis_client import Channels, Keys, Subscriber, create_redis, publish

logger = structlog.get_logger()


class BrainService:
    """Orchestrates AI reasoning: identity linking, LLM calls, automation rules."""

    def __init__(self):
        self._config = load_config()
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._db = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()

    async def start(self) -> None:
        self._redis = await create_redis(self._config)
        self._db = init_db(self._config)

        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        # Phase 2: subscribe to audio:utterance, audio:speaker_id
        # Phase 3: subscribe to vision:faces
        await self._subscriber.start()

        self._running = True
        logger.info("brain_service_started")
        await self._publish_status(healthy=True)

    async def _on_privacy_toggle(self, data: dict) -> None:
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled
        logger.info("brain_privacy_mode", enabled=event.enabled)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="brain",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={"privacy_mode": self._privacy_mode},
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        try:
            while self._running:
                await self._publish_status(healthy=True)
                await asyncio.sleep(10)
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
        logger.info("brain_service_stopped")


def main() -> None:
    setup_logging("brain")
    service = BrainService()
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
