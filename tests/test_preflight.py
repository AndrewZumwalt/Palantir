"""Tests for startup preflight reporting."""

from __future__ import annotations

from palantir.preflight import PreflightResult, log_and_check


def test_log_and_check_returns_false_for_nonfatal_errors():
    result = PreflightResult(service="audio")
    result.error("missing dependency")

    assert log_and_check(result, fatal_on_error=False) is False
