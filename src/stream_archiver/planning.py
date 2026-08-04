"""Pure stream grouping, age selection, and archive action planning."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stream_archiver.config import CompressionCodec, Policy, SymlinkRule
from stream_archiver.errors import PlanningError
from stream_archiver.model import (
    ActionKind,
    ArchivePlan,
    Entry,
    EntryKind,
    PlannedAction,
    Stream,
)

_RESERVED_ARCHIVE_PATHS = frozenset(
    {
        Path("MANIFEST.json"),
        Path("SHA256SUMS.json"),
        Path("SUCCESS.json"),
    }
)


def split_streams(entries: tuple[Entry, ...], *, minimum_gap: timedelta) -> tuple[Stream, ...]:
    """Split ordered entries whenever an adjacent gap is at least ``minimum_gap``."""

    if minimum_gap <= timedelta(0):
        raise PlanningError("minimum_gap must be positive")
    if not entries:
        return ()
    ordered = tuple(
        sorted(entries, key=lambda item: (item.mtime_ns, item.relative_path.as_posix()))
    )
    gap_ns = _timedelta_to_ns(minimum_gap)
    groups: list[list[Entry]] = [[ordered[0]]]
    for entry in ordered[1:]:
        if entry.mtime_ns - groups[-1][-1].mtime_ns >= gap_ns:
            groups.append([])
        groups[-1].append(entry)
    return tuple(Stream(tuple(group)) for group in groups)


def select_eligible_streams(
    streams: tuple[Stream, ...], *, now: datetime, minimum_age: timedelta
) -> tuple[Stream, ...]:
    """Return complete streams whose newest entry is at least ``minimum_age`` old."""

    if minimum_age <= timedelta(0):
        raise PlanningError("minimum_age must be positive")
    now_utc = _require_aware_utc(now)
    cutoff_ns = _datetime_to_ns(now_utc) - _timedelta_to_ns(minimum_age)
    return tuple(stream for stream in streams if stream.newest_mtime_ns <= cutoff_ns)


def build_archive_plan(
    policy: Policy,
    source_root: Path,
    stream: Stream,
) -> ArchivePlan | None:
    """Build one source-scoped deterministic plan.

    ``source_root`` must be one of the policy's configured sources. The caller
    invokes this function independently for each source, so streams and final
    directories never combine entries from different roots.
    """

    if source_root not in policy.sources:
        raise PlanningError(f"source is not owned by policy {policy.name!r}: {source_root}")

    regular_identities = {
        (entry.identity.device, entry.identity.inode)
        for entry in stream.entries
        if entry.kind is EntryKind.REGULAR
    }
    actions = tuple(
        _plan_entry(policy, entry, regular_identities) for entry in stream.entries
    )
    payload_actions = tuple(
        action
        for action in actions
        if action.kind
        in {
            ActionKind.MOVE,
            ActionKind.GZIP,
            ActionKind.BZ2,
            ActionKind.PRESERVE_SYMLINK,
        }
    )
    if not payload_actions:
        return None

    _reject_archive_path_collisions(payload_actions)
    plan_id = _plan_id(policy, source_root, stream, actions)
    archive_name = (
        f"{_format_timestamp(stream.oldest_mtime_ns)}--"
        f"{_format_timestamp(stream.newest_mtime_ns)}--{plan_id[:10]}"
    )
    return ArchivePlan(
        policy_name=policy.name,
        source_root=source_root,
        destination_root=policy.destination,
        plan_id=plan_id,
        archive_name=archive_name,
        stream=stream,
        actions=actions,
    )


def _plan_entry(
    policy: Policy,
    entry: Entry,
    regular_identities: set[tuple[int, int]],
) -> PlannedAction:
    if entry.kind is EntryKind.REGULAR:
        codec = policy.compression_for(entry.relative_path)
        if codec is CompressionCodec.GZIP:
            return PlannedAction(
                source=entry,
                kind=ActionKind.GZIP,
                archive_path=entry.relative_path.with_name(entry.relative_path.name + ".gz"),
            )
        if codec is CompressionCodec.BZ2:
            return PlannedAction(
                source=entry,
                kind=ActionKind.BZ2,
                archive_path=entry.relative_path.with_name(entry.relative_path.name + ".bz2"),
            )
        return PlannedAction(entry, ActionKind.MOVE, entry.relative_path)

    if policy.symlink_rule is SymlinkRule.IGNORE:
        return PlannedAction(entry, ActionKind.SKIP_SYMLINK, None)

    if policy.symlink_rule is SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE:
        target_identity = _resolved_target_identity(entry.absolute_path)
        if target_identity is not None and target_identity in regular_identities:
            return PlannedAction(entry, ActionKind.DROP_ALIAS_SYMLINK, None)

    if entry.link_target is not None and not os.path.isabs(entry.link_target):
        return PlannedAction(entry, ActionKind.PRESERVE_SYMLINK, entry.relative_path)
    return PlannedAction(entry, ActionKind.SKIP_SYMLINK, None)


def _resolved_target_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except (FileNotFoundError, OSError):
        return None
    return metadata.st_dev, metadata.st_ino


def _reject_archive_path_collisions(actions: tuple[PlannedAction, ...]) -> None:
    owners: dict[Path, Path] = {
        path: Path(f"<reserved:{path.name}>") for path in _RESERVED_ARCHIVE_PATHS
    }
    for action in actions:
        assert action.archive_path is not None
        archive_path = action.archive_path
        for previous_path, previous_source in owners.items():
            if (
                archive_path == previous_path
                or archive_path in previous_path.parents
                or previous_path in archive_path.parents
            ):
                raise PlanningError(
                    "archive path collision: "
                    f"{previous_source} maps to {previous_path}, while "
                    f"{action.source.relative_path} maps to {archive_path}"
                )
        owners[archive_path] = action.source.relative_path


def _plan_id(
    policy: Policy,
    source_root: Path,
    stream: Stream,
    actions: tuple[PlannedAction, ...],
) -> str:
    payload = {
        "policy": policy.name,
        "source": str(source_root),
        "destination": str(policy.destination),
        "entries": [
            {
                "path": action.source.relative_path.as_posix(),
                "kind": action.source.kind.value,
                "action": action.kind.value,
                "archive_path": (
                    action.archive_path.as_posix() if action.archive_path is not None else None
                ),
                "device": action.source.identity.device,
                "inode": action.source.identity.inode,
                "mode": action.source.identity.mode,
                "size": action.source.identity.size,
                "mtime_ns": action.source.identity.mtime_ns,
                "link_target": action.source.link_target,
            }
            for action in actions
        ],
        "oldest_mtime_ns": stream.oldest_mtime_ns,
        "newest_mtime_ns": stream.newest_mtime_ns,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _format_timestamp(mtime_ns: int) -> str:
    seconds, nanoseconds = divmod(mtime_ns, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{moment:%Y%m%dT%H%M%S}.{nanoseconds // 1_000:06d}Z"


def _timedelta_to_ns(value: timedelta) -> int:
    total_seconds = value.days * 86_400 + value.seconds
    return total_seconds * 1_000_000_000 + value.microseconds * 1_000


def _datetime_to_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return _timedelta_to_ns(value - epoch)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PlanningError("now must be timezone-aware")
    return value.astimezone(timezone.utc)
