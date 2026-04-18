"""Resilience utilities: network monitoring, retries, circuit breakers.

Used by services that depend on external APIs (Claude, cloud vision) to
degrade gracefully when the internet drops or APIs are rate-limited.
"""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import structlog

logger = structlog.get_logger()


class CircuitState(str, Enum):
    CLOSED = "closed"      # normal operation
    OPEN = "open"          # failing, reject all requests
    HALF_OPEN = "half_open"  # tentative test


@dataclass
class CircuitBreaker:
    """Simple circuit breaker for external API calls.

    After `failure_threshold` consecutive failures, the breaker opens and
    all calls fail fast for `recovery_timeout` seconds. Then it moves to
    half-open and allows one test call; success closes the circuit.
    """

    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    name: str = "default"

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            logger.info("circuit_closed", name=self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.warning(
                    "circuit_opened",
                    name=self.name,
                    failures=self.failure_count,
                )
            self.state = CircuitState.OPEN

    def allow_request(self) -> bool:
        """Return True if a request is allowed through."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("circuit_half_open", name=self.name)
                return True
            return False
        # HALF_OPEN: allow one probe
        return True


class NetworkMonitor:
    """Periodically checks reachability of a DNS endpoint to detect outages.

    A background task keeps `online` up-to-date. Services can check this
    before attempting cloud calls and skip straight to local fallback if
    offline.
    """

    def __init__(
        self,
        probe_host: str = "1.1.1.1",
        probe_port: int = 443,
        check_interval_seconds: float = 30.0,
        timeout_seconds: float = 3.0,
    ):
        self._probe_host = probe_host
        self._probe_port = probe_port
        self._interval = check_interval_seconds
        self._timeout = timeout_seconds
        self._online = True  # optimistic start
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def online(self) -> bool:
        return self._online

    async def start(self) -> None:
        self._running = True
        await self._check_once()  # initial probe
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self._check_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("network_monitor_error")

    async def _check_once(self) -> None:
        previous = self._online
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self._probe_tcp),
                timeout=self._timeout,
            )
            self._online = True
        except (asyncio.TimeoutError, OSError, ConnectionError):
            self._online = False

        if previous != self._online:
            logger.info(
                "network_status_changed",
                online=self._online,
                host=self._probe_host,
            )

    def _probe_tcp(self) -> None:
        """Blocking TCP probe of probe_host:probe_port."""
        with socket.create_connection(
            (self._probe_host, self._probe_port), timeout=self._timeout
        ):
            pass


# --- Retry helpers ---

async def retry_async(
    fn: Callable,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 8.0,
    retry_on: tuple = (Exception,),
):
    """Call an async function with exponential backoff on failure."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except retry_on as e:
            last_error = e
            if attempt == max_attempts:
                break
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            logger.warning(
                "retry_backoff",
                attempt=attempt,
                max_attempts=max_attempts,
                delay_seconds=delay,
                error=str(e)[:100],
            )
            await asyncio.sleep(delay)
    if last_error:
        raise last_error
