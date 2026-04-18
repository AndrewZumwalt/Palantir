"""Startup preflight checks.

Validates configuration and environment before a service begins accepting
work. Services can call `validate_for(service_name)` early in `start()`
and abort cleanly if critical preconditions aren't met.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from palantir.config import PalantirConfig

logger = structlog.get_logger()


@dataclass
class PreflightResult:
    service: str
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _check_common(config: PalantirConfig, result: PreflightResult) -> None:
    """Preconditions shared by every service."""
    # Database directory must exist and be writable
    db_dir = Path(config.db_path).parent
    if not db_dir.exists():
        try:
            db_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            result.error(f"Cannot create database directory {db_dir}: {e}")
            return

    if not os.access(db_dir, os.W_OK):
        result.error(f"Database directory {db_dir} is not writable")


def _check_llm_access(config: PalantirConfig, result: PreflightResult) -> None:
    """Services that call Claude should warn loudly if the API key is missing."""
    if not config.anthropic_api_key:
        result.warn(
            "ANTHROPIC_API_KEY is not set; service will run in offline mode only"
        )


def _check_audio_deps(result: PreflightResult) -> None:
    """Audio service needs sounddevice + optional ML packages."""
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        result.error("sounddevice not installed; run `pip install -e .[voice]`")

    try:
        import openwakeword  # noqa: F401
    except ImportError:
        result.warn("openwakeword missing; wake word detection disabled")

    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        result.warn("faster-whisper missing; STT disabled")


def _check_vision_deps(result: PreflightResult) -> None:
    """Vision service depends on OpenCV."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        result.error("opencv-python not installed")

    try:
        import insightface  # noqa: F401
    except ImportError:
        result.warn("insightface missing; face recognition disabled")

    try:
        import ultralytics  # noqa: F401
    except ImportError:
        result.warn("ultralytics missing; object detection and engagement disabled")


def _check_web_deps(config: PalantirConfig, result: PreflightResult) -> None:
    """Web service should have a clean port and its auth state is worth warning about."""
    if not config.auth_token:
        result.warn(
            "No auth_token configured; API is open to anyone on the network"
        )

    # Frontend dist directory existence is informational
    frontend_dist = (
        Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    )
    if not frontend_dist.exists():
        result.warn(
            f"Frontend dist not found at {frontend_dist}; "
            "build with `cd frontend && npm run build`"
        )

    # TLS: if configured, make sure we can actually produce certs
    if config.web.tls_cert_file or config.web.tls_key_file:
        if not (config.web.tls_cert_file and config.web.tls_key_file):
            result.warn(
                "TLS partially configured; both tls_cert_file and "
                "tls_key_file must be set for HTTPS to activate"
            )
        else:
            cert_exists = Path(config.web.tls_cert_file).is_file()
            key_exists = Path(config.web.tls_key_file).is_file()
            if not (cert_exists and key_exists):
                try:
                    import cryptography  # noqa: F401
                except ImportError:
                    result.warn(
                        "TLS paths configured but cert missing and "
                        "cryptography package not installed; HTTPS disabled"
                    )


def _check_redis(config: PalantirConfig, result: PreflightResult) -> None:
    """Warn if Redis socket is missing on Pi deployments."""
    if config.redis.url.startswith("unix://"):
        sock_path = config.redis.url.replace("unix://", "")
        if not Path(sock_path).exists():
            result.warn(
                f"Redis unix socket {sock_path} not found; "
                f"will fall back to {config.redis.fallback_url}"
            )


def validate_for(service_name: str, config: PalantirConfig) -> PreflightResult:
    """Validate startup preconditions for a specific service.

    Returns a PreflightResult. Callers should log errors and abort if
    `result.ok` is False, or log warnings and continue otherwise.
    """
    result = PreflightResult(service=service_name)
    _check_common(config, result)
    _check_redis(config, result)

    if service_name == "audio":
        _check_audio_deps(result)
    elif service_name == "vision":
        _check_vision_deps(result)
    elif service_name == "brain":
        _check_llm_access(config, result)
    elif service_name == "web":
        _check_web_deps(config, result)
    elif service_name in ("tts", "eventlog"):
        pass  # no extra checks

    return result


def log_and_check(result: PreflightResult, fatal_on_error: bool = True) -> bool:
    """Log the result. Returns True if the service may proceed, False if it must abort."""
    for warn in result.warnings:
        logger.warning("preflight_warning", service=result.service, issue=warn)
    for err in result.errors:
        logger.error("preflight_error", service=result.service, issue=err)

    if not result.ok:
        if fatal_on_error:
            logger.error(
                "preflight_failed_aborting",
                service=result.service,
                errors=len(result.errors),
            )
            return False
        logger.warning(
            "preflight_failed_continuing",
            service=result.service,
            errors=len(result.errors),
        )
    else:
        logger.info(
            "preflight_ok",
            service=result.service,
            warnings=len(result.warnings),
        )
    return True
