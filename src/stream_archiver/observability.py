"""Logging configuration and structured event helpers.

The command-line interface writes machine-readable command results to stdout and
operational logs to stderr.  This separation keeps JSON output scriptable while
allowing systemd to collect detailed progress and failure information.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

_DEFAULT_EVENT = "message"
_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class _DynamicStderrHandler(logging.StreamHandler):
    """Resolve ``sys.stderr`` at emission time for test and redirect safety."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


class TextEventFormatter(logging.Formatter):
    """Format one log event as compact key-value text in UTC."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a stable text representation suitable for journald."""

        timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        event = getattr(record, "event", _DEFAULT_EVENT)
        fields = _event_fields(record)
        prefix = (
            f"{timestamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z')} "
            f"{record.levelname} {record.name} event={event}"
        )
        message = record.getMessage()
        field_text = " ".join(
            f"{key}={_quote_text_value(value)}" for key, value in sorted(fields.items())
        )
        parts = [prefix]
        if message:
            parts.append(message)
        if field_text:
            parts.append(field_text)
        rendered = " ".join(parts)
        if record.exc_info:
            rendered += "\n" + self.formatException(record.exc_info)
        return rendered


class JsonEventFormatter(logging.Formatter):
    """Format one log event as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON line with stable core fields and event context."""

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", _DEFAULT_EVENT),
            "message": record.getMessage(),
        }
        payload.update(_event_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_logging(
    *,
    level: str,
    format_name: str,
    stream: TextIO | None = None,
) -> None:
    """Configure the package root logger without changing application stdout.

    Repeated calls replace only handlers owned by the ``stream_archiver``
    logger.  Third-party and test loggers are not modified.
    """

    logger = logging.getLogger("stream_archiver")
    logger.setLevel(_parse_log_level(level))
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream) if stream is not None else _DynamicStderrHandler()
    if format_name == "text":
        handler.setFormatter(TextEventFormatter())
    elif format_name == "json":
        handler.setFormatter(JsonEventFormatter())
    else:
        raise ValueError(f"unsupported log format: {format_name}")
    logger.addHandler(handler)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    /,
    **fields: object,
) -> None:
    """Emit one named event with structured fields.

    Values are retained as native JSON scalars for JSON output.  Paths and
    other objects are converted to strings by the formatters.
    """

    logger.log(
        level,
        message,
        extra={"event": event, "event_fields": fields},
        stacklevel=2,
    )


def _parse_log_level(value: str) -> int:
    normalized = value.upper()
    level = logging.getLevelNamesMapping().get(normalized)
    if not isinstance(level, int):
        raise ValueError(f"unsupported log level: {value}")
    return level


def _event_fields(record: logging.LogRecord) -> dict[str, Any]:
    explicit = getattr(record, "event_fields", {})
    fields: dict[str, Any] = dict(explicit) if isinstance(explicit, dict) else {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_FIELDS or key in {"event", "event_fields"}:
            continue
        fields.setdefault(key, value)
    return {key: _json_safe(value) for key, value in fields.items()}


def quote_path(path: os.PathLike[str] | str) -> str:
    """Return one filesystem path as an unambiguous JSON string literal.

    Human-readable log messages sometimes need to mention a path outside a
    structured field.  JSON quoting keeps whitespace, quotes, and backslashes
    visually bounded without changing the underlying path value.
    """

    return json.dumps(os.fspath(path))


def _json_safe(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _quote_text_value(value: object) -> str:
    """Render a structured field without visually ambiguous strings."""

    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))
