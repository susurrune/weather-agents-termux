"""Structured logging for Weather Agents.

Usage:
    from weather_agents.core.logger import get_logger
    log = get_logger("fog")
    log.info("chat_request", user_message="hello", agent="fog")
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Module-level registry so logger config persists
_configured = False
_loggers: dict[str, logging.Logger] = {}


class StructuredFormatter(logging.Formatter):
    """JSON-lines formatter for machine-parseable logs."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            obj.update(record.extra_fields)
        return json.dumps(obj, ensure_ascii=False, default=str)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure root logger with structured output.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR
        log_file: Optional file path; if not set, logs go to stderr.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger("wa")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = StructuredFormatter()

    # Always log to stderr
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Optional file handler
    if log_file:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(p))
        fh.setFormatter(fmt)
        root.addHandler(fh)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a structured logger for the given component name."""
    if name not in _loggers:
        logger = logging.getLogger(f"wa.{name}")
        _loggers[name] = logger
    return _loggers[name]


class LoggerMixin:
    """Mixin adding a `log` attribute to any class that has a `name` property."""

    @property
    def log(self) -> logging.Logger:
        return get_logger(self.name if hasattr(self, "name") else type(self).__name__)


def log_event(logger: logging.Logger, event_type: str, **fields: Any) -> None:
    """Log a structured event with extra fields."""
    record = logger.makeRecord(
        logger.name, logging.INFO, "(unknown)", 0, event_type, (), None,
    )
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
