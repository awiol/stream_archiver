"""Pure stream grouping, age selection, and archive action planning."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
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
from stream_archiver.observability import log_event

LOGGER = logging.getLogger(__name__)

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
    result = tuple(Stream(tuple(group)) for group in groups)
    log_event(
        LOGGER,
        logging.DEBUG,
        "streams_split",
        "entries grouped into streams",
        entries=len(entries),
        streams=len(result),
        minimum_gap_seconds=int(minimum_gap.total_seconds()),
    )
    return result


def select_eligible_streams(
    streams: tuple[Stream, ...], *, now: datetime, minimum_age: timedelta
) -> tuple[Stream, ...]:
    """Return complete streams whose newest entry is at least ``minimum_age`` old."""

    if minimum_age <= timedelta(0):
        raise PlanningError("minimum_age must be positive")
    now_utc = _require_aware_utc(now)
    cutoff_ns = _datetime_to_ns(now_utc) - _timedelta_to_ns(minimum_age)
    eligible = tuple(stream for stream in streams if stream.newest_mtime_ns <= cutoff_ns)
    log_event(
        LOGGER,
        logging.DEBUG,
        "streams_selected",
        "stream age eligibility evaluated",
        streams=len(streams),
        eligible=len(eligible),
        minimum_age_seconds=int(minimum_age.total_seconds()),
        cutoff_ns=cutoff_ns,
    )
    return eligible


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
    actions = tuple(_plan_entry(policy, entry, regular_identities) for entry in stream.entries)
    actions = _resolve_archive_path_collisions(actions)
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

    plan_id = _plan_id(policy, source_root, stream, actions)
    archive_name = (
        f"{_format_timestamp(stream.oldest_mtime_ns)}--"
        f"{_format_timestamp(stream.newest_mtime_ns)}--{plan_id[:10]}"
    )
    log_event(
        LOGGER,
        logging.DEBUG,
        "archive_plan_built",
        "deterministic archive plan created",
        policy=policy.name,
        source=source_root,
        archive=archive_name,
        actions=len(actions),
        payload_actions=len(payload_actions),
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


def _resolve_archive_path_collisions(
    actions: tuple[PlannedAction, ...],
) -> tuple[PlannedAction, ...]:
    """Keep fixed payload paths stable and disambiguate generated compression paths.

    Compression appends ``.gz`` or ``.bz2``. A source tree can already contain
    that filename, for example both ``data.json`` and ``data.json.gz``. Aborting
    the complete stream is unnecessary because the manifest records the final
    payload path. Fixed move/symlink paths remain unchanged; only generated
    compressed paths may receive a deterministic disambiguation suffix.
    """

    payload_kinds = {
        ActionKind.MOVE,
        ActionKind.GZIP,
        ActionKind.BZ2,
        ActionKind.PRESERVE_SYMLINK,
    }
    fixed_kinds = {ActionKind.MOVE, ActionKind.PRESERVE_SYMLINK}
    owners: dict[Path, Path] = {
        path: Path(f"<reserved:{path.name}>") for path in _RESERVED_ARCHIVE_PATHS
    }

    for action in actions:
        if action.kind not in fixed_kinds:
            continue
        assert action.archive_path is not None
        conflict = _find_archive_path_conflict(action.archive_path, owners)
        if conflict is not None:
            previous_path, previous_source = conflict
            _raise_fixed_archive_collision(
                previous_source=previous_source,
                previous_path=previous_path,
                action=action,
            )
        owners[action.archive_path] = action.source.relative_path

    resolved: list[PlannedAction] = []
    for action in actions:
        if action.kind not in payload_kinds or action.kind in fixed_kinds:
            resolved.append(action)
            continue

        assert action.archive_path is not None
        desired = action.archive_path
        selected = desired
        if _find_archive_path_conflict(selected, owners) is not None:
            selected = _disambiguated_compression_path(action, owners)
            log_event(
                LOGGER,
                logging.INFO,
                "archive_path_disambiguated",
                "compressed payload path changed to avoid an archive collision",
                source_path=action.source.relative_path,
                desired_archive_path=desired,
                selected_archive_path=selected,
                action=action.kind.value,
            )
        owners[selected] = action.source.relative_path
        resolved.append(PlannedAction(action.source, action.kind, selected))

    return tuple(resolved)


def _find_archive_path_conflict(
    candidate: Path, owners: dict[Path, Path]
) -> tuple[Path, Path] | None:
    for previous_path, previous_source in owners.items():
        if (
            candidate == previous_path
            or candidate in previous_path.parents
            or previous_path in candidate.parents
        ):
            return previous_path, previous_source
    return None


def _disambiguated_compression_path(action: PlannedAction, owners: dict[Path, Path]) -> Path:
    assert action.archive_path is not None
    codec_suffix = ".gz" if action.kind is ActionKind.GZIP else ".bz2"
    source_name = action.source.relative_path.name
    digest = hashlib.sha256(
        f"{action.kind.value}\0{action.source.relative_path.as_posix()}".encode()
    ).hexdigest()[:12]
    base_name = f"{source_name}.stream-archiver-{digest}{codec_suffix}"
    parent = action.archive_path.parent

    candidate = parent / base_name
    counter = 1
    while _find_archive_path_conflict(candidate, owners) is not None:
        candidate = parent / (f"{source_name}.stream-archiver-{digest}-{counter}{codec_suffix}")
        counter += 1
    return candidate


def _raise_fixed_archive_collision(
    *, previous_source: Path, previous_path: Path, action: PlannedAction
) -> None:
    assert action.archive_path is not None
    log_event(
        LOGGER,
        logging.ERROR,
        "archive_path_collision",
        "two fixed payload paths cannot coexist in one archive",
        first_source_path=previous_source,
        first_archive_path=previous_path,
        second_source_path=action.source.relative_path,
        second_archive_path=action.archive_path,
        action=(
            "rename one source path or change the symlink policy so the archive tree "
            "has one filesystem object at each path"
        ),
    )
    raise PlanningError(
        "archive path collision between fixed payload paths; inspect the preceding "
        "archive_path_collision log event for the two source and archive paths"
    )


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
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    return f"{moment:%Y%m%dT%H%M%S}.{nanoseconds // 1_000:06d}Z"


def _timedelta_to_ns(value: timedelta) -> int:
    total_seconds = value.days * 86_400 + value.seconds
    return total_seconds * 1_000_000_000 + value.microseconds * 1_000


def _datetime_to_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return _timedelta_to_ns(value - epoch)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PlanningError("now must be timezone-aware")
    return value.astimezone(UTC)
