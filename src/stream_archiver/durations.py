"""Strict duration parsing for policy and scheduling configuration."""

from __future__ import annotations

import re
from datetime import timedelta

from stream_archiver.errors import ConfigurationError

_DURATION_RE = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>[smhdw])$")
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
}


def parse_duration(value: str, *, field: str) -> timedelta:
    """Parse a positive integer duration such as ``8h`` or ``30d``.

    Calendar months are deliberately unsupported because their length varies.
    Use days or weeks so eligibility and scheduling boundaries stay explicit.
    """

    if not isinstance(value, str):
        raise ConfigurationError(f"{field} must be a duration string")
    match = _DURATION_RE.fullmatch(value.strip())
    if match is None:
        raise ConfigurationError(
            f"{field} must use a positive integer followed by s, m, h, d, or w"
        )
    seconds = int(match.group("value")) * _UNIT_SECONDS[match.group("unit")]
    return timedelta(seconds=seconds)
