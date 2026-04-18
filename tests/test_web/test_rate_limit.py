"""Tests for the sliding-window rate limiter."""

from __future__ import annotations

import time

from palantir.web.rate_limit import SlidingWindowLimiter


def test_limiter_allows_under_threshold():
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
    for _ in range(3):
        ok, _ = limiter.check("client")
        assert ok is True


def test_limiter_blocks_over_threshold():
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60.0)
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is True
    ok, retry = limiter.check("client")
    assert ok is False
    assert retry >= 1


def test_limiter_keys_are_independent():
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    # "a" is exhausted but "b" was only used once
    assert limiter.check("a")[0] is False
    assert limiter.check("b")[0] is False


def test_limiter_refills_after_window():
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=0.05)
    assert limiter.check("client")[0] is True
    assert limiter.check("client")[0] is False
    time.sleep(0.06)
    assert limiter.check("client")[0] is True
