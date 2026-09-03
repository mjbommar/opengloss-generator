"""Structured logging.

Console output is human-readable; the file sink is JSONL so a run is machine-queryable
afterwards. Every event carries the ``run_id`` via a context variable, so no call site
has to thread it through.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog

__all__ = ["bind_run", "configure_logging", "get_logger"]

TRACE = 5


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    run_id: str | None = None,
) -> None:
    """Install the structlog and stdlib logging configuration for a process.

    Args:
        level: Minimum level for the console sink.
        log_dir: Directory for the JSONL sink. No file sink is installed when ``None``.
        run_id: Run identifier used to name the log file.
    """
    logging.addLevelName(TRACE, "TRACE")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None and run_id is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / f"{run_id}.log.jsonl", encoding="utf-8")
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(processor=structlog.processors.JSONRenderer())
        )
        handlers.append(file_handler)

    handlers[0].setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        )
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        # Close what we opened; a long-lived process reconfiguring per run would
        # otherwise leak a file descriptor for every run.
        if isinstance(existing, logging.FileHandler):
            existing.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def bind_run(run_id: str, **extra: Any) -> None:  # noqa: ANN401
    """Bind a run identifier, and any other constants, to every subsequent log event."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=run_id, **extra)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for a module."""
    return structlog.stdlib.get_logger(name)
