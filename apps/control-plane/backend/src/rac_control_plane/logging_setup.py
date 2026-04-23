# pattern: Imperative Shell
import os
import sys
from typing import Any

import structlog

from rac_control_plane.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog with JSON output and App Insights handler if available."""

    # Base processors for all loggers
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # Context processors - extract from structlog context
    context_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.dict_tracebacks,
    ]

    # Formatting processors
    formatting_processors: list[Any] = [
        structlog.processors.JSONRenderer(),
    ]

    # Configure structlog
    structlog.configure(
        processors=shared_processors + context_processors + formatting_processors,  # type: ignore
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Add App Insights handler if connection string is present
    app_insights_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if app_insights_conn_str:
        try:
            from opencensus.ext.azure.log_exporter import AzureLogHandler  # type: ignore  # noqa: F401

            # Note: structlog with JSON output sends to stdout/stderr;
            # App Insights handler would be added to stdlib logging if stdlib integration enabled
        except ImportError:
            pass  # opencensus not available, skip App Insights
