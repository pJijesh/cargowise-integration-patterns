"""Structured JSON logging with a correlation id carried in a context variable."""

from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, TextIO

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)

_REDACT_MARKERS = ("token", "secret", "password", "authorization", "api_key", "apikey")
_RESERVED = {"ts", "level", "logger", "event", "correlation_id", "exception"}


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


@contextmanager
def correlation(cid: str) -> Iterator[str]:
    """Bind a correlation id for the duration of the block."""
    token = correlation_id_var.set(cid)
    try:
        yield cid
    finally:
        correlation_id_var.reset(token)


def _should_redact(key: str) -> bool:
    k = key.lower()
    return any(m in k for m in _REDACT_MARKERS)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc)
        payload: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        for key, value in (getattr(record, "fields", None) or {}).items():
            if key in _RESERVED:
                key = f"field_{key}"
            payload[key] = "[REDACTED]" if _should_redact(key) else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger("mini_middleware")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any
) -> None:
    logger.log(level, event, extra={"fields": fields})
