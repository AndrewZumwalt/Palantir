"""Structured logging setup for all Palantir services."""

from __future__ import annotations

import logging

import structlog


def setup_logging(service_name: str, *, debug: bool = False) -> None:
    """Configure structlog for a Palantir service."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.EventRenamer("msg"),
            (
                structlog.dev.ConsoleRenderer(colors=True)
                if debug
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bind service name to all log messages
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service_name)
