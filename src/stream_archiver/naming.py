"""Deterministic archive-unit identity and configurable human archive names."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter

from stream_archiver.errors import ConfigurationError, PlanningError

DEFAULT_ARCHIVE_NAME_TEMPLATE = "{start:%Y%m%dT%H%M%SZ}--{end:%Y%m%dT%H%M%SZ}"
ARCHIVE_UNIT_SUFFIX_HEX_LENGTH = 20
_ARCHIVE_UNIT_ID_DOMAIN = "stream-archiver/archive-unit/v1"
_ALLOWED_FIELDS = frozenset({"start", "end", "span"})
_MONTH_ABBR = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIMESTAMP_DIRECTIVES = frozenset({"Y", "m", "b", "d", "a", "H", "M", "S", "f", "N"})
_SPAN_DIRECTIVES = frozenset({"D", "H", "M", "S", "f", "N"})


def archive_unit_id(plan_id: str, *, unit_index: int = 0) -> str:
    """Return the full format-neutral identity for one archive unit.

    ``unit_index`` is the deterministic zero-based position of this archive unit
    within its logical stream. It is part of persistent identity and is never an
    invocation-local stream ordinal.
    """

    if not _is_sha256(plan_id):
        raise ValueError("plan_id must be a lowercase SHA-256 hexadecimal string")
    if isinstance(unit_index, bool) or not isinstance(unit_index, int) or unit_index < 0:
        raise ValueError("unit_index must be a non-negative integer")
    payload = {
        "domain": _ARCHIVE_UNIT_ID_DOMAIN,
        "plan_id": plan_id,
        "unit_index": unit_index,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def archive_unit_suffix(plan_id: str, *, unit_index: int = 0) -> str:
    """Return the mandatory shortened identity suffix used in archive names."""

    return archive_unit_id(plan_id, unit_index=unit_index)[:ARCHIVE_UNIT_SUFFIX_HEX_LENGTH]


def validate_archive_name_template(template: str) -> None:
    """Validate the controlled archive-name template grammar.

    The template may contain literal text plus ``start``, ``end``, and ``span``
    fields. Timestamp directives are locale-independent. ``%f`` and ``%N`` are
    explicit microsecond and nanosecond fields; omitting them keeps sub-second
    information out of the human prefix while the stable identity remains exact.
    """

    if not isinstance(template, str) or not template:
        raise ConfigurationError("archive_name_template must be a non-empty string")
    if "\x00" in template or "/" in template or "\\" in template:
        raise ConfigurationError("archive_name_template must not contain path separators or NUL")
    if "--sa-" in template:
        raise ConfigurationError(
            "archive_name_template must not contain the reserved '--sa-' marker"
        )
    try:
        parsed = tuple(Formatter().parse(template))
    except ValueError as exc:
        raise ConfigurationError(f"invalid archive_name_template: {exc}") from exc
    if not parsed:
        raise ConfigurationError("archive_name_template must not be empty")
    has_field = False
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        has_field = True
        if field_name not in _ALLOWED_FIELDS:
            choices = ", ".join(sorted(_ALLOWED_FIELDS))
            raise ConfigurationError(
                f"archive_name_template field {field_name!r} is unsupported; use: {choices}"
            )
        if conversion is not None:
            raise ConfigurationError(
                "archive_name_template conversions such as !r are not supported"
            )
        if "{" in format_spec or "}" in format_spec:
            raise ConfigurationError(
                "archive_name_template nested replacement fields are not supported"
            )
        _validate_format_spec(field_name, format_spec)
    if not has_field and template in {".", ".."}:
        raise ConfigurationError("archive_name_template must not render '.' or '..'")


def render_archive_name(
    template: str,
    *,
    start_mtime_ns: int,
    end_mtime_ns: int,
    plan_id: str,
    unit_index: int = 0,
) -> str:
    """Render one safe archive directory name with a mandatory stable suffix."""

    validate_archive_name_template(template)
    if end_mtime_ns < start_mtime_ns:
        raise PlanningError("archive payload end time precedes start time")
    values = {
        "start": _TimestampValue(start_mtime_ns),
        "end": _TimestampValue(end_mtime_ns),
        "span": _SpanValue(end_mtime_ns - start_mtime_ns),
    }
    try:
        prefix = template.format_map(values)
    except (ValueError, KeyError) as exc:  # defensive; validation owns normal failures.
        raise PlanningError(f"cannot render archive_name_template: {exc}") from exc
    if not prefix or prefix in {".", ".."}:
        raise PlanningError("archive_name_template rendered an empty or reserved filename")
    if "\x00" in prefix or "/" in prefix or "\\" in prefix:
        raise PlanningError("archive_name_template rendered a path separator or NUL")
    if "--sa-" in prefix:
        raise PlanningError("archive_name_template rendered the reserved '--sa-' marker")
    return f"{prefix}--sa-{archive_unit_suffix(plan_id, unit_index=unit_index)}"


def validate_archive_name_length(name: str, destination: Path) -> None:
    """Reject a rendered archive name that exceeds the destination name limit."""

    try:
        name_max = os.pathconf(destination, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = 255
    encoded_length = len(os.fsencode(name))
    if encoded_length > name_max:
        raise PlanningError(
            "rendered archive name exceeds the destination filename limit: "
            f"{encoded_length} bytes > {name_max} bytes"
        )


def is_current_archive_name(name: str) -> bool:
    """Return whether ``name`` has the current stable archive-unit suffix shape."""

    marker = "--sa-"
    prefix, separator, suffix = name.rpartition(marker)
    return bool(
        separator
        and prefix
        and len(suffix) == ARCHIVE_UNIT_SUFFIX_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in suffix)
    )


def current_archive_name_matches_plan(name: str, plan_id: str, *, unit_index: int = 0) -> bool:
    """Return whether a current-format archive name carries the expected identity suffix."""

    if not is_current_archive_name(name):
        return False
    return name.endswith(f"--sa-{archive_unit_suffix(plan_id, unit_index=unit_index)}")


class _TimestampValue:
    """Format one UTC nanosecond timestamp with a controlled directive set."""

    def __init__(self, timestamp_ns: int) -> None:
        self.timestamp_ns = timestamp_ns

    def __format__(self, spec: str) -> str:
        selected = spec or "%Y%m%dT%H%M%SZ"
        return _format_timestamp_ns(self.timestamp_ns, selected)


class _SpanValue:
    """Format one non-negative nanosecond duration with a controlled directive set."""

    def __init__(self, span_ns: int) -> None:
        self.span_ns = span_ns

    def __format__(self, spec: str) -> str:
        selected = spec or "%Dd%Hh%Mm%Ss"
        return _format_span_ns(self.span_ns, selected)


def _validate_format_spec(field_name: str, spec: str) -> None:
    selected = spec or ("%Dd%Hh%Mm%Ss" if field_name == "span" else "%Y%m%dT%H%M%SZ")
    allowed = _SPAN_DIRECTIVES if field_name == "span" else _TIMESTAMP_DIRECTIVES
    _scan_directives(selected, allowed, field_name)


def _format_timestamp_ns(timestamp_ns: int, spec: str) -> str:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    values = {
        "Y": f"{moment.year:04d}",
        "m": f"{moment.month:02d}",
        "b": _MONTH_ABBR[moment.month],
        "d": f"{moment.day:02d}",
        "a": _WEEKDAY_ABBR[moment.weekday()],
        "H": f"{moment.hour:02d}",
        "M": f"{moment.minute:02d}",
        "S": f"{moment.second:02d}",
        "f": f"{nanoseconds // 1_000:06d}",
        "N": f"{nanoseconds:09d}",
    }
    return _substitute_directives(spec, values, _TIMESTAMP_DIRECTIVES, "timestamp")


def _format_span_ns(span_ns: int, spec: str) -> str:
    if span_ns < 0:
        raise PlanningError("archive span must not be negative")
    seconds, nanoseconds = divmod(span_ns, 1_000_000_000)
    days, day_seconds = divmod(seconds, 86_400)
    hours, hour_seconds = divmod(day_seconds, 3_600)
    minutes, second = divmod(hour_seconds, 60)
    values = {
        "D": str(days),
        "H": f"{hours:02d}",
        "M": f"{minutes:02d}",
        "S": f"{second:02d}",
        "f": f"{nanoseconds // 1_000:06d}",
        "N": f"{nanoseconds:09d}",
    }
    return _substitute_directives(spec, values, _SPAN_DIRECTIVES, "span")


def _scan_directives(spec: str, allowed: frozenset[str], field_name: str) -> None:
    index = 0
    while index < len(spec):
        if spec[index] != "%":
            index += 1
            continue
        index += 1
        if index >= len(spec):
            raise ConfigurationError(
                f"archive_name_template {field_name} format ends with an incomplete '%' directive"
            )
        directive = spec[index]
        if directive == "%":
            index += 1
            continue
        if directive not in allowed:
            choices = ", ".join(f"%{item}" for item in sorted(allowed))
            raise ConfigurationError(
                f"archive_name_template {field_name} directive %{directive} is unsupported; "
                f"use: {choices}, or %%"
            )
        index += 1


def _substitute_directives(
    spec: str,
    values: dict[str, str],
    allowed: frozenset[str],
    field_name: str,
) -> str:
    _scan_directives(spec, allowed, field_name)
    result: list[str] = []
    index = 0
    while index < len(spec):
        character = spec[index]
        if character != "%":
            result.append(character)
            index += 1
            continue
        directive = spec[index + 1]
        if directive == "%":
            result.append("%")
        else:
            result.append(values[directive])
        index += 2
    return "".join(result)


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
