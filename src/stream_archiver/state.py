"""Atomic fingerprinted per-policy due state used by scheduled checks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from stream_archiver.config import Policy
from stream_archiver.durability import sync_directory
from stream_archiver.errors import ExecutionError
from stream_archiver.observability import log_event

LOGGER = logging.getLogger(__name__)

STATE_FORMAT_VERSION = 2
LEGACY_STATE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class DueState:
    """Successful policy times plus the policy definition that produced them."""

    last_success: dict[str, datetime]
    policy_fingerprints: dict[str, str] = field(default_factory=dict)

    def is_due(
        self,
        policy_name: str,
        *,
        now: datetime,
        interval: timedelta,
        fingerprint: str | None = None,
    ) -> bool:
        """Return whether a policy is new, changed, or past its run interval."""

        now_utc = _require_aware_utc(now)
        previous = self.last_success.get(policy_name)
        if previous is None:
            return True
        if fingerprint is not None and self.policy_fingerprints.get(policy_name) != fingerprint:
            return True
        return now_utc - previous >= interval

    def with_success(
        self,
        policy_name: str,
        *,
        when: datetime,
        fingerprint: str | None = None,
    ) -> DueState:
        """Return new state recording one successful completion and fingerprint."""

        updated_times = dict(self.last_success)
        updated_times[policy_name] = _require_aware_utc(when)
        updated_fingerprints = dict(self.policy_fingerprints)
        if fingerprint is not None:
            updated_fingerprints[policy_name] = fingerprint
        else:
            updated_fingerprints.pop(policy_name, None)
        return DueState(updated_times, updated_fingerprints)


def policy_fingerprint(policy: Policy) -> str:
    """Return the v2 scheduling fingerprint for selection/representation fields."""

    payload = {
        "sources": [str(source) for source in policy.sources],
        "destination": str(policy.destination),
        "minimum_age_microseconds": _timedelta_microseconds(policy.minimum_age),
        "stream_gap_microseconds": _timedelta_microseconds(policy.stream_gap),
        "symlink_rule": policy.symlink_rule.value,
        "compression_rules": [
            {
                "suffixes": list(rule.suffixes),
                "compression": rule.compression.value,
            }
            for rule in policy.compression_rules
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def load_state(path: Path) -> DueState:
    """Load v2 state or conservatively migrate a valid v1 state in memory."""

    if not path.exists():
        log_event(
            LOGGER,
            logging.INFO,
            "due_state_missing",
            "no prior due state exists; policies are due",
            path=path,
        )
        return DueState({})
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionError(f"cannot read due state {path}: {exc}") from exc

    version = raw.get("state_format_version")
    if version == LEGACY_STATE_FORMAT_VERSION:
        parsed = _parse_v1_state(raw, path)
        log_event(
            LOGGER,
            logging.WARNING,
            "due_state_v1_loaded",
            "legacy due state loaded; policies are conservatively due until v2 success",
            path=path,
            policies=len(parsed),
        )
        return DueState(parsed, {})
    if version != STATE_FORMAT_VERSION:
        raise ExecutionError(f"unsupported due-state version in {path}")

    raw_policies = raw.get("policies")
    if not isinstance(raw_policies, dict):
        raise ExecutionError(f"invalid due state in {path}: policies must be an object")
    times: dict[str, datetime] = {}
    fingerprints: dict[str, str] = {}
    for name, record in raw_policies.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise ExecutionError(f"invalid due state entry in {path}")
        timestamp = record.get("last_success")
        fingerprint = record.get("policy_fingerprint")
        if not isinstance(timestamp, str) or (
            fingerprint is not None and not isinstance(fingerprint, str)
        ):
            raise ExecutionError(f"invalid due state entry for policy {name!r} in {path}")
        times[name] = _parse_timestamp(timestamp, name, path)
        if fingerprint is not None:
            if not fingerprint.startswith("sha256:") or len(fingerprint) != 71:
                raise ExecutionError(f"invalid policy fingerprint for {name!r} in {path}")
            fingerprints[name] = fingerprint
    return DueState(times, fingerprints)


def save_state(path: Path, state: DueState) -> None:
    """Atomically persist v2 due state after successful policy completion."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "state_format_version": STATE_FORMAT_VERSION,
        "policies": {
            name: {
                "last_success": state.last_success[name]
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "policy_fingerprint": state.policy_fingerprints.get(name),
            }
            for name in sorted(state.last_success)
        },
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    log_event(
        LOGGER,
        logging.INFO,
        "due_state_saved",
        "successful policy completion state persisted",
        path=path,
        policies=len(payload["policies"]),
    )


def _parse_v1_state(raw: dict[str, Any], path: Path) -> dict[str, datetime]:
    last_success_raw = raw.get("last_success")
    if not isinstance(last_success_raw, dict):
        raise ExecutionError(f"invalid due state in {path}: last_success must be an object")
    parsed: dict[str, datetime] = {}
    for name, timestamp in last_success_raw.items():
        if not isinstance(name, str) or not isinstance(timestamp, str):
            raise ExecutionError(f"invalid due state entry in {path}")
        parsed[name] = _parse_timestamp(timestamp, name, path)
    return parsed


def _parse_timestamp(value: str, name: str, path: Path) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionError(f"invalid timestamp for policy {name!r} in {path}") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ExecutionError(f"timestamp for policy {name!r} in {path} must include an offset")
    return moment.astimezone(UTC)


def _timedelta_microseconds(value: timedelta) -> int:
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError("due-state timestamps must be timezone-aware")
    return value.astimezone(UTC)
