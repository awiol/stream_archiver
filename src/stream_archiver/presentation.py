"""Human-readable planning, progress, and capacity presentation utilities."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stream_archiver.capacity import CapacityObservation, CapacitySnapshot, CapacityUnavailable
from stream_archiver.model import ActionKind, ArchivePlan, EntryKind
from stream_archiver.progress import RunProgressSnapshot, archive_regular_work

if TYPE_CHECKING:
    from stream_archiver.service import PolicyPlan

_PAYLOAD_REGULAR = frozenset({ActionKind.MOVE, ActionKind.GZIP, ActionKind.BZ2})


def render_plan_summary(
    plans: Sequence[PolicyPlan],
    *,
    reference_time: datetime,
    capacities: Mapping[Path, CapacityObservation] | None = None,
) -> str:
    """Render a bounded operator summary without enumerating individual entries.

    The summary reports source/input bytes. It does not predict compressed
    output size. Capacity comparisons are advisory and use exact selected source
    bytes against observed available destination bytes. The complete action-level
    representation remains available from the CLI's explicit JSON mode.
    """

    when = reference_time.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [
        "Stream Archiver plan",
        f"Reference time: {when}",
        "Read-only snapshot; filesystem state may change before execution.",
        "",
    ]

    totals = _totals(plans)
    lines.extend(
        [
            "Overall:",
            (
                f"  {len(plans)} policies, {totals['sources']} sources, "
                f"{totals['discovered']} entries discovered"
            ),
            (
                f"  {totals['streams']} streams, {totals['eligible']} eligible, "
                f"{totals['archives']} archives planned"
            ),
            (
                f"  {totals['regular']} regular files ({format_bytes(totals['bytes'])}), "
                f"{totals['preserved_links']} symlinks preserved, "
                f"{totals['aliases']} aliases removed"
            ),
        ]
    )

    if capacities is not None:
        lines.extend(["", "Destination capacity (advisory):"])
        destination_totals = _selected_bytes_by_destination(plans)
        for destination in sorted(destination_totals, key=str):
            selected_bytes = destination_totals[destination]
            observation = capacities.get(destination)
            if isinstance(observation, CapacitySnapshot):
                prefix = "WARNING" if observation.free_bytes < selected_bytes else "OK"
                lines.append(
                    f"  {prefix}: {destination} — selected source "
                    f"{format_bytes(selected_bytes)}; free {format_bytes(observation.free_bytes)}"
                )
            elif isinstance(observation, CapacityUnavailable):
                lines.append(
                    f"  WARNING: {destination} — selected source {format_bytes(selected_bytes)}; "
                    f"capacity unavailable ({observation.reason})"
                )
            else:
                lines.append(
                    f"  WARNING: {destination} — selected source {format_bytes(selected_bytes)}; "
                    "capacity unavailable"
                )

    for plan in plans:
        policy = _policy_stats(plan)
        lines.extend(
            [
                "",
                f"Policy: {plan.policy_name}",
                f"  Destination: {plan.destination_root}",
                (
                    f"  Discovery: {'recursive' if plan.recursive else 'top-level only'}; "
                    f"stream partition: {plan.stream_partition}"
                ),
                (
                    f"  {len(plan.source_plans)} sources; {plan.discovered_entries} entries; "
                    f"{plan.streams} streams; {plan.eligible_streams} eligible; "
                    f"{len(plan.archive_plans)} archives"
                ),
                "  Disposition: " + _format_dispositions(policy["dispositions"]),
                "  Extensions: " + _format_extensions(policy["extensions"]),
            ]
        )
        for index, source in enumerate(plan.source_plans[:6], start=1):
            selected = _source_selected_stats(source.archive_plans)
            lines.append(
                f"  Source {index}: {source.source_root} — "
                f"{source.eligible_streams}/{source.streams} eligible streams, "
                f"{len(source.archive_plans)} archives, {selected[0]} files, "
                f"{format_bytes(selected[1])}"
            )
        if len(plan.source_plans) > 6:
            omitted = len(plan.source_plans) - 6
            lines.append(f"  … {omitted} additional sources omitted from summary")

    lines.extend(["", "Nothing has been changed. Use `plan --json` for action-level detail."])
    return "\n".join(lines) + "\n"


def format_bytes(value: int) -> str:
    """Return a compact SI representation for a non-negative byte count.

    The public human convention uses decimal SI divisors and labels so ``kB``,
    ``MB``, ``GB``, and ``TB`` mean 10^3, 10^6, 10^9, and 10^12 bytes. Exact
    integer bytes remain the machine-readable authority.
    """

    size = float(max(0, value))
    units = ("B", "kB", "MB", "GB", "TB", "PB")
    for unit in units[:-1]:
        if size < 1000.0:
            if unit == "B":
                return f"{int(size)} {unit}"
            precision = 2 if size < 10.0 else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1000.0
    precision = 2 if size < 10.0 else 1
    return f"{size:.{precision}f} {units[-1]}"


def format_elapsed(seconds: float) -> str:
    """Return compact non-negative elapsed runtime for human operational text."""

    value = max(0.0, seconds)
    if value < 10.0:
        return f"{value:.1f}s"
    whole = int(value)
    if whole < 60:
        return f"{whole}s"
    minutes, sec = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h{minute:02d}m{sec:02d}s"


def progress_fields(snapshot: RunProgressSnapshot) -> dict[str, object]:
    """Return exact structured counters plus compact human progress strings."""

    return {
        "stream_index": snapshot.stream_index,
        "stream_total": snapshot.stream_total,
        "files_completed": snapshot.files_completed,
        "files_total": snapshot.files_total,
        "source_bytes_processed": snapshot.source_bytes_processed,
        "source_bytes_total": snapshot.source_bytes_total,
        "written_payload_bytes": snapshot.written_payload_bytes,
        "elapsed_seconds": snapshot.elapsed_seconds,
        "stream_progress": f"{snapshot.stream_index}/{snapshot.stream_total}",
        "files_progress": f"{snapshot.files_completed}/{snapshot.files_total}",
        "source_progress": (
            f"{format_bytes(snapshot.source_bytes_processed)}/"
            f"{format_bytes(snapshot.source_bytes_total)}"
        ),
        "written_payload": format_bytes(snapshot.written_payload_bytes),
        "elapsed": format_elapsed(snapshot.elapsed_seconds),
    }


def _totals(plans: Sequence[PolicyPlan]) -> dict[str, int]:
    """Aggregate bounded plan-summary totals across selected policies."""

    regular = preserved = aliases = byte_count = 0
    for plan in plans:
        for archive in plan.archive_plans:
            for action in archive.actions:
                if action.kind in _PAYLOAD_REGULAR:
                    regular += 1
                    byte_count += action.source.identity.size
                elif action.kind is ActionKind.PRESERVE_SYMLINK:
                    preserved += 1
                elif action.kind is ActionKind.DROP_ALIAS_SYMLINK:
                    aliases += 1
    return {
        "sources": sum(len(plan.source_plans) for plan in plans),
        "discovered": sum(plan.discovered_entries for plan in plans),
        "streams": sum(plan.streams for plan in plans),
        "eligible": sum(plan.eligible_streams for plan in plans),
        "archives": sum(len(plan.archive_plans) for plan in plans),
        "regular": regular,
        "bytes": byte_count,
        "preserved_links": preserved,
        "aliases": aliases,
    }


def _selected_bytes_by_destination(plans: Sequence[PolicyPlan]) -> dict[Path, int]:
    """Aggregate selected regular source bytes for each exact destination."""

    totals: dict[Path, int] = {}
    for policy_plan in plans:
        for archive in policy_plan.archive_plans:
            _, byte_count = archive_regular_work(archive)
            totals[archive.destination_root] = totals.get(archive.destination_root, 0) + byte_count
    return totals


def _policy_stats(plan: PolicyPlan) -> dict[str, object]:
    """Collect disposition and extension statistics for one policy summary."""

    dispositions: dict[ActionKind, list[int]] = defaultdict(lambda: [0, 0])
    extensions: Counter[str] = Counter()
    extension_bytes: Counter[str] = Counter()
    for archive in plan.archive_plans:
        for action in archive.actions:
            bucket = dispositions[action.kind]
            bucket[0] += 1
            if action.source.kind is EntryKind.REGULAR and action.kind in _PAYLOAD_REGULAR:
                bucket[1] += action.source.identity.size
                suffix = action.source.relative_path.suffix.casefold() or "<none>"
                extensions[suffix] += 1
                extension_bytes[suffix] += action.source.identity.size
    return {
        "dispositions": dispositions,
        "extensions": (extensions, extension_bytes),
    }


def _format_dispositions(dispositions: dict[ActionKind, list[int]]) -> str:
    """Render non-zero action disposition counts with payload input sizes."""

    labels = (
        (ActionKind.MOVE, "move"),
        (ActionKind.GZIP, "gzip"),
        (ActionKind.BZ2, "bzip2"),
        (ActionKind.PRESERVE_SYMLINK, "preserve-link"),
        (ActionKind.DROP_ALIAS_SYMLINK, "drop-alias"),
    )
    parts: list[str] = []
    for kind, label in labels:
        count, byte_count = dispositions.get(kind, [0, 0])
        if count == 0:
            continue
        if kind in _PAYLOAD_REGULAR:
            parts.append(f"{label} {count} ({format_bytes(byte_count)})")
        else:
            parts.append(f"{label} {count}")
    return "; ".join(parts) if parts else "no selected payload"


def _format_extensions(data: tuple[Counter[str], Counter[str]]) -> str:
    """Render the largest extension groups while bounding summary line growth."""

    counts, bytes_by_extension = data
    ranked = sorted(
        counts,
        key=lambda suffix: (-bytes_by_extension[suffix], -counts[suffix], suffix),
    )
    parts: list[str] = []
    for suffix in ranked[:5]:
        parts.append(f"{suffix} {counts[suffix]} ({format_bytes(bytes_by_extension[suffix])})")
    if len(ranked) > 5:
        remaining = ranked[5:]
        parts.append(
            "other "
            f"{sum(counts[item] for item in remaining)} "
            f"({format_bytes(sum(bytes_by_extension[item] for item in remaining))})"
        )
    return "; ".join(parts) if parts else "none"


def _source_selected_stats(archive_plans: Sequence[ArchivePlan]) -> tuple[int, int]:
    """Return selected regular-file count and source bytes for one source."""

    count = byte_count = 0
    for archive in archive_plans:
        for action in archive.actions:
            if action.kind in _PAYLOAD_REGULAR:
                count += 1
                byte_count += action.source.identity.size
    return count, byte_count
