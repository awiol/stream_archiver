"""Pure stream grouping, age selection, and archive action planning."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stream_archiver.config import CompressionCodec, Policy, StreamPartition, SymlinkRule
from stream_archiver.errors import PlanningError
from stream_archiver.model import (
    ActionKind,
    ArchivePlan,
    Entry,
    EntryKind,
    PlannedAction,
    Stream,
)
from stream_archiver.naming import (
    archive_unit_id,
    render_archive_name,
    validate_archive_name_length,
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


def split_partitioned_streams(
    entries: tuple[Entry, ...],
    *,
    minimum_gap: timedelta,
    partition: StreamPartition,
) -> tuple[Stream, ...]:
    """Split boundary entries within the configured grouping domain.

    ``source-root`` preserves the historical behavior. ``parent-directory``
    prevents timestamp adjacency in one directory from merging a stream in a
    different exact relative parent directory.
    """

    if partition is StreamPartition.SOURCE_ROOT:
        return split_streams(entries, minimum_gap=minimum_gap)
    if partition is not StreamPartition.PARENT_DIRECTORY:
        raise PlanningError(f"unsupported stream partition: {partition}")

    grouped: dict[Path, list[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.relative_path.parent, []).append(entry)
    streams = [
        stream
        for parent in sorted(grouped, key=lambda item: item.as_posix())
        for stream in split_streams(tuple(grouped[parent]), minimum_gap=minimum_gap)
    ]
    return tuple(
        sorted(
            streams,
            key=lambda stream: (
                stream.oldest_mtime_ns,
                stream.newest_mtime_ns,
                stream.entries[0].relative_path.as_posix(),
            ),
        )
    )


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
    *,
    source_entries: tuple[Entry, ...] | None = None,
) -> ArchivePlan | None:
    """Build the single archive plan for an unpartitioned logical stream.

    Call :func:`build_archive_plans` when archive-unit subdivision is enabled.
    This compatibility wrapper rejects a multi-unit result rather than silently
    dropping later units.
    """

    plans = build_archive_plans(
        policy,
        source_root,
        stream,
        source_entries=source_entries,
    )
    if not plans:
        return None
    if len(plans) != 1:
        raise PlanningError(
            "logical stream was subdivided into multiple archive units; use build_archive_plans()"
        )
    return plans[0]


def build_archive_plans(
    policy: Policy,
    source_root: Path,
    stream: Stream,
    *,
    source_entries: tuple[Entry, ...] | None = None,
) -> tuple[ArchivePlan, ...]:
    """Build deterministic archive-unit plans for one eligible logical stream.

    Regular files are greedily assigned in deterministic stream order. A new
    unit starts before a regular file when adding it would exceed either enabled
    limit and the current unit already contains a regular file. A single file
    larger than the byte limit remains intact and occupies one oversized unit.

    Cleanup-only aliases follow their selected regular target. Other symlink
    actions are assigned to the first unit whose last regular entry is not older
    than the symlink; links after all regular files follow the final unit. This
    keeps zero-byte link actions deterministic without making them consume the
    regular-file limits.
    """

    if source_root not in policy.sources:
        raise PlanningError(f"source is not owned by policy {policy.name!r}: {source_root}")

    all_entries = source_entries if source_entries is not None else stream.entries
    regular_entries = {
        entry.relative_path: entry for entry in all_entries if entry.kind is EntryKind.REGULAR
    }
    selected_regular_paths = {
        entry.relative_path for entry in stream.entries if entry.kind is EntryKind.REGULAR
    }
    actions = tuple(
        _plan_entry(policy, source_root, entry, regular_entries) for entry in stream.entries
    )
    if policy.symlink_rule is SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE:
        selected_paths = {entry.relative_path for entry in stream.entries}
        alias_actions = tuple(
            PlannedAction(entry, ActionKind.DROP_ALIAS_SYMLINK, None)
            for entry in all_entries
            if entry.kind is EntryKind.SYMLINK
            and entry.relative_path not in selected_paths
            and _resolved_source_regular_target(entry.absolute_path, source_root, regular_entries)
            in selected_regular_paths
        )
        actions += alias_actions
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
        return ()

    plan_id = _plan_id(policy, source_root, stream, actions)
    units = _partition_archive_actions(policy, source_root, actions, regular_entries)
    unit_count = len(units)
    plans: list[ArchivePlan] = []
    for unit_index, unit_actions in enumerate(units):
        unit_payload = tuple(
            action
            for action in unit_actions
            if action.kind
            in {
                ActionKind.MOVE,
                ActionKind.GZIP,
                ActionKind.BZ2,
                ActionKind.PRESERVE_SYMLINK,
            }
        )
        if not unit_payload:
            raise PlanningError("archive-unit partition produced a unit without payload")
        payload_oldest = min(action.source.mtime_ns for action in unit_payload)
        payload_newest = max(action.source.mtime_ns for action in unit_payload)
        if policy.archive_name_template is None:
            archive_name = (
                f"{_format_timestamp(payload_oldest)}--{_format_timestamp(payload_newest)}--"
                f"{plan_id[:10]}"
            )
        else:
            archive_name = render_archive_name(
                policy.archive_name_template,
                start_mtime_ns=payload_oldest,
                end_mtime_ns=payload_newest,
                plan_id=plan_id,
                unit_index=unit_index,
            )
            validate_archive_name_length(archive_name, policy.destination)
        plans.append(
            ArchivePlan(
                policy_name=policy.name,
                source_root=source_root,
                destination_root=policy.destination,
                plan_id=plan_id,
                archive_name=archive_name,
                stream=stream,
                actions=unit_actions,
                archive_unit_index=unit_index,
                archive_unit_count=unit_count,
            )
        )
        log_event(
            LOGGER,
            logging.DEBUG,
            "archive_plan_built",
            "deterministic archive-unit plan created",
            policy=policy.name,
            source=source_root,
            archive=archive_name,
            plan_id=plan_id,
            archive_unit_id=archive_unit_id(plan_id, unit_index=unit_index),
            archive_unit_index=unit_index,
            archive_unit_count=unit_count,
            actions=len(unit_actions),
            payload_actions=len(unit_payload),
        )
    return tuple(plans)


def _partition_archive_actions(
    policy: Policy,
    source_root: Path,
    actions: tuple[PlannedAction, ...],
    regular_entries: dict[Path, Entry],
) -> tuple[tuple[PlannedAction, ...], ...]:
    """Partition one logical stream's actions by regular-file limits."""

    max_bytes = policy.archive_unit_max_source_bytes
    max_files = policy.archive_unit_max_regular_files
    if max_bytes is None and max_files is None:
        return (actions,)

    regular_actions = tuple(
        sorted(
            (
                action
                for action in actions
                if action.source.kind is EntryKind.REGULAR
                and action.kind in {ActionKind.MOVE, ActionKind.GZIP, ActionKind.BZ2}
            ),
            key=lambda action: (action.source.mtime_ns, action.source.relative_path.as_posix()),
        )
    )
    if not regular_actions:
        return (actions,)

    regular_units: list[list[PlannedAction]] = [[]]
    unit_bytes = 0
    for action in regular_actions:
        size = action.source.identity.size
        current = regular_units[-1]
        exceeds_files = max_files is not None and len(current) >= max_files
        exceeds_bytes = max_bytes is not None and current and unit_bytes + size > max_bytes
        if current and (exceeds_files or exceeds_bytes):
            regular_units.append([])
            current = regular_units[-1]
            unit_bytes = 0
        current.append(action)
        unit_bytes += size

    assignment: dict[Path, int] = {}
    regular_keys: list[tuple[tuple[int, str], int]] = []
    for unit_index, unit in enumerate(regular_units):
        for action in unit:
            assignment[action.source.relative_path] = unit_index
        last = unit[-1].source
        regular_keys.append(((last.mtime_ns, last.relative_path.as_posix()), unit_index))

    unit_actions: list[list[PlannedAction]] = [[] for _ in regular_units]
    for action in actions:
        if action.source.kind is EntryKind.REGULAR:
            unit_index = assignment[action.source.relative_path]
        elif action.kind is ActionKind.DROP_ALIAS_SYMLINK:
            target = _resolved_source_regular_target(
                action.source.absolute_path, source_root, regular_entries
            )
            if target is None or target not in assignment:
                raise PlanningError(
                    "cleanup-only alias lost its selected regular target during subdivision"
                )
            unit_index = assignment[target]
        else:
            link_key = (action.source.mtime_ns, action.source.relative_path.as_posix())
            unit_index = regular_keys[-1][1]
            for last_key, candidate_index in regular_keys:
                if link_key <= last_key:
                    unit_index = candidate_index
                    break
        unit_actions[unit_index].append(action)

    return tuple(tuple(unit) for unit in unit_actions)


def boundary_entries_for_policy(
    policy: Policy,
    source_root: Path,
    entries: tuple[Entry, ...],
) -> tuple[Entry, ...]:
    """Return source entries whose timestamps define stream boundaries.

    Alias classification is intentionally source-root-wide. Alias symlinks and
    symlinks that the policy will skip do not split or extend a stream.
    Preserved non-alias relative symlinks remain normal boundary entries and use
    their own modification times.
    """

    regular_entries = {
        entry.relative_path: entry for entry in entries if entry.kind is EntryKind.REGULAR
    }
    boundary: list[Entry] = []
    for entry in entries:
        if entry.kind is EntryKind.REGULAR:
            boundary.append(entry)
            continue
        if policy.symlink_rule is SymlinkRule.IGNORE:
            continue
        if (
            policy.symlink_rule is SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE
            and _resolved_source_regular_target(entry.absolute_path, source_root, regular_entries)
            is not None
        ):
            continue
        if entry.link_target is None or os.path.isabs(entry.link_target):
            continue
        boundary.append(entry)
    return tuple(sorted(boundary, key=lambda item: (item.mtime_ns, item.relative_path.as_posix())))


def _plan_entry(
    policy: Policy,
    source_root: Path,
    entry: Entry,
    regular_entries: dict[Path, Entry],
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

    if (
        policy.symlink_rule is SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE
        and _resolved_source_regular_target(entry.absolute_path, source_root, regular_entries)
        is not None
    ):
        return PlannedAction(entry, ActionKind.DROP_ALIAS_SYMLINK, None)

    if entry.link_target is not None and not os.path.isabs(entry.link_target):
        return PlannedAction(entry, ActionKind.PRESERVE_SYMLINK, entry.relative_path)
    return PlannedAction(entry, ActionKind.SKIP_SYMLINK, None)


def _resolved_source_regular_target(
    symlink_path: Path,
    source_root: Path,
    regular_entries: dict[Path, Entry],
) -> Path | None:
    """Return the exact in-source regular target path for one alias symlink.

    Filesystem identity alone is insufficient because different hardlink paths
    can share one inode while only one pathname is selected. The resolved target
    must be inside the source root and must match the discovered regular entry at
    that exact relative path.
    """

    try:
        canonical_root = source_root.resolve(strict=True)
        target = symlink_path.resolve(strict=True)
        relative = target.relative_to(canonical_root)
        metadata = target.stat()
    except (OSError, ValueError):
        return None
    entry = regular_entries.get(relative)
    if entry is None or not stat.S_ISREG(metadata.st_mode):
        return None
    if (metadata.st_dev, metadata.st_ino) != (entry.identity.device, entry.identity.inode):
        return None
    return relative


@dataclass(slots=True)
class _ArchivePathIndex:
    """Index archive objects and occupied directory prefixes by relative path."""

    objects: dict[Path, Path] = field(default_factory=dict)
    descendants: dict[Path, tuple[Path, Path]] = field(default_factory=dict)

    def conflict(self, candidate: Path) -> tuple[Path, Path] | None:
        """Return one existing object that conflicts with ``candidate``."""

        if candidate in self.objects:
            return candidate, self.objects[candidate]
        descendant = self.descendants.get(candidate)
        if descendant is not None:
            return descendant
        for parent in candidate.parents:
            source = self.objects.get(parent)
            if source is not None:
                return parent, source
        return None

    def add(self, path: Path, source: Path) -> None:
        """Record one payload object and the directory prefixes it occupies."""

        self.objects[path] = source
        for parent in path.parents:
            self.descendants.setdefault(parent, (path, source))


def _resolve_archive_path_collisions(
    actions: tuple[PlannedAction, ...],
) -> tuple[PlannedAction, ...]:
    """Keep fixed payload paths stable and disambiguate generated compression paths."""

    payload_kinds = {
        ActionKind.MOVE,
        ActionKind.GZIP,
        ActionKind.BZ2,
        ActionKind.PRESERVE_SYMLINK,
    }
    fixed_kinds = {ActionKind.MOVE, ActionKind.PRESERVE_SYMLINK}
    index = _ArchivePathIndex()
    for path in _RESERVED_ARCHIVE_PATHS:
        index.add(path, Path(f"<reserved:{path.name}>"))

    for action in actions:
        if action.kind not in fixed_kinds:
            continue
        assert action.archive_path is not None
        conflict = index.conflict(action.archive_path)
        if conflict is not None:
            previous_path, previous_source = conflict
            _raise_fixed_archive_collision(
                previous_source=previous_source,
                previous_path=previous_path,
                action=action,
            )
        index.add(action.archive_path, action.source.relative_path)

    resolved: list[PlannedAction] = []
    for action in actions:
        if action.kind not in payload_kinds or action.kind in fixed_kinds:
            resolved.append(action)
            continue

        assert action.archive_path is not None
        desired = action.archive_path
        selected = desired
        if index.conflict(selected) is not None:
            selected = _disambiguated_compression_path(action, index)
            log_event(
                LOGGER,
                logging.DEBUG,
                "archive_path_disambiguated",
                "compressed payload path changed to avoid an archive collision",
                source_path=action.source.relative_path,
                desired_archive_path=desired,
                selected_archive_path=selected,
                archive_action=action.kind.value,
            )
        index.add(selected, action.source.relative_path)
        resolved.append(PlannedAction(action.source, action.kind, selected))

    return tuple(resolved)


def _disambiguated_compression_path(action: PlannedAction, index: _ArchivePathIndex) -> Path:
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
    while index.conflict(candidate) is not None:
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
        next_action=(
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
        "selection_oldest_mtime_ns": stream.oldest_mtime_ns,
        "selection_newest_mtime_ns": stream.newest_mtime_ns,
    }
    if (
        policy.archive_unit_max_source_bytes is not None
        or policy.archive_unit_max_regular_files is not None
    ):
        # Partition policy changes archive-unit boundaries. Bind it into the
        # logical plan identity only when subdivision is configured so the
        # unsplit 0.5.0a1 identity remains stable.
        payload["archive_unit_partition"] = {
            "max_source_bytes": policy.archive_unit_max_source_bytes,
            "max_regular_files": policy.archive_unit_max_regular_files,
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
