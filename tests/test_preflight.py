"""Tests for startup preflight reporting."""

from __future__ import annotations

from palantir.config import PalantirConfig
from palantir.preflight import PreflightResult, log_and_check, validate_for


def test_log_and_check_returns_false_for_nonfatal_errors():
    result = PreflightResult(service="audio")
    result.error("missing dependency")

    assert log_and_check(result, fatal_on_error=False) is False


def test_web_preflight_requires_auth_token_in_production(tmp_path):
    cfg = PalantirConfig(environment="production")
    cfg.auth_token = ""
    cfg.db_path = str(tmp_path / "palantir.db")

    result = validate_for("web", cfg)

    assert result.ok is False
    assert "PALANTIR_AUTH_TOKEN must be set in production" in result.errors
