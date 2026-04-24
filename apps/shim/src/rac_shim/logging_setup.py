# pattern: Imperative Shell
"""Structlog JSON logging setup for the shim.

Mirrors the control-plane pattern: structured JSON output, correlation_id
threaded via contextvars.
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO") -> None:
    """Configure structlog to emit JSON-formatted structured logs.

    Call once at application startup (e.g., in lifespan).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
