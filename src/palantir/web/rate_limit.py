"""Simple in-memory rate limiting for the web API.

Uses a sliding-window counter keyed by client IP + route prefix. Good enough
for a single-process deployment on a Pi — if you scale out, swap the store
for Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    """Tracks request timestamps per key in a fixed-size window."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        bucket = self._buckets[key]

        # Drop timestamps older than window
        while bucket and bucket[0] <= now - self.window_seconds:
            bucket.popleft()

        if len(bucket) >= self.max_requests:
            retry_after = int(bucket[0] + self.window_seconds - now) + 1
            return False, max(1, retry_after)

        bucket.append(now)
        return True, 0


# Route-specific limiters
_write_limiter = SlidingWindowLimiter(max_requests=60, window_seconds=60.0)
_enroll_limiter = SlidingWindowLimiter(max_requests=20, window_seconds=60.0)
_read_limiter = SlidingWindowLimiter(max_requests=240, window_seconds=60.0)


def _client_key(request: Request) -> str:
    """Identify the client for rate limiting.

    Use the direct peer address only. X-Forwarded-For is client-controlled
    unless a trusted proxy strips and rewrites it, and Palantir does not
    currently have trusted-proxy configuration.
    """
    if not request.client:
        return "anonymous"
    return request.client.host


def rate_limit_read(request: Request) -> None:
    """Dependency for read-only endpoints."""
    key = f"read:{_client_key(request)}"
    ok, retry_after = _read_limiter.check(key)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_write(request: Request) -> None:
    """Dependency for write endpoints (POST/PUT/DELETE)."""
    key = f"write:{_client_key(request)}"
    ok, retry_after = _write_limiter.check(key)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_enroll(request: Request) -> None:
    """Stricter limiter for enrollment (uploads biometric data)."""
    key = f"enroll:{_client_key(request)}"
    ok, retry_after = _enroll_limiter.check(key)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Enrollment rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
