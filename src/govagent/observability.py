"""Structured JSON logs (structlog), one event per line on stderr.

Every log line emitted while an analysis runs carries its `run_id` (the analysis id), bound
once with `run_context` and merged in from a context variable, like SLF4J's MDC. Entry points
call `configure_logging` once: the CLI defaults to WARNING so its human output stays clean,
the MCP server to INFO. Secrets and prompts are never logged.
"""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, TextIO

import structlog


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        # Resolve sys.stderr when a logger is created, not now: tools such as test runners
        # swap it, and a captured stream may be closed by the time we log.
        logger_factory=(
            structlog.PrintLoggerFactory(file=stream)
            if stream is not None
            else lambda *_: structlog.PrintLogger(file=sys.stderr)
        ),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


@contextmanager
def run_context(run_id: str) -> Generator[None]:
    """Bind `run_id` to every log line emitted in this context (thread or task)."""
    with structlog.contextvars.bound_contextvars(run_id=run_id):
        yield
