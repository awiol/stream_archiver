"""Atomic per-policy due-state used by frequent systemd checks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from stream_archiver.errors import ExecutionError

STATE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class DueState:
    """Last successful completion times keyed by stable policy name."""

    last_success: dict[str, datetime]

    def is_due(self, policy_name: str, *, now: datetime, interval: timedelta) -> bool:
        """Return whether a policy has never succeeded or its interval has elapsed."""

        now_utc = _require_aware_utc(now)
        previous = self.last_success.get(policy_name)
        return previous is None or now_utc - previous >= interval

    def with_success(self, policy_name: str, *, when: datetime) -> "DueState":
        """Return a new state recording one successful policy completion."""

        updated = dict(self.last_success)
        updated[policy_name] = _require_aware_utc(when)
        return DueState(updated)


def load_state(path: Path) -> DueState:
    """Load state, treating a missing file as a never-run installation."""

    if not path.exists():
        return DueState({})
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionError(f"cannot read due state {path}: {exc}") from exc
    if raw.get("state_format_version") != STATE_FORMAT_VERSION:
        raise ExecutionError(f"unsupported due-state version in {path}")
    last_success_raw = raw.get("last_success")
    if not isinstance(last_success_raw, dict):
        raise ExecutionError(f"invalid due state in {path}: last_success must be an object")

    parsed: dict[str, datetime] = {}
    for name, timestamp in last_success_raw.items():
        if not isinstance(name, str) or not isinstance(timestamp, str):
            raise ExecutionError(f"invalid due state entry in {path}")
        try:
            moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExecutionError(f"invalid timestamp for policy {name!r} in {path}") from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ExecutionError(f"timestamp for policy {name!r} in {path} must include an offset")
        parsed[name] = moment.astimezone(timezone.utc)
    return DueState(parsed)


def save_state(path: Path, state: DueState) -> None:
    """Atomically persist due state after successful policy completion."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "state_format_version": STATE_FORMAT_VERSION,
        "last_success": {
            name: moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            for name, moment in sorted(state.last_success.items())
        },
    }
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError("due-state timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
