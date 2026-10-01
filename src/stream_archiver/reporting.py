"""Final run-result aggregation and human, JSON, and Markdown presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stream_archiver.executor import RegularPayloadGroupResult
from stream_archiver.model import ActionKind
from stream_archiver.presentation import format_bytes, format_elapsed

if TYPE_CHECKING:
    from stream_archiver.service import PolicyRunResult

RUN_REPORT_SCHEMA_VERSION = 1
_EXTENSION_NONE = "<none>"


class PayloadTransformation(StrEnum):
    """Transformation applied to a regular source file before archive storage."""

    UNCOMPRESSED = "uncompressed"
    GZIP = "gzip"
    BZIP2 = "bzip2"


@dataclass(frozen=True, slots=True)
class PayloadBreakdown:
    """Counts and byte totals for one transformation or source-extension group."""

    files: int
    source_bytes: int
    written_payload_bytes: int

    @property
    def saved_payload_bytes(self) -> int:
        """Return source bytes minus written transformed payload bytes."""

        return self.source_bytes - self.written_payload_bytes

    @property
    def payload_size_ratio(self) -> float | None:
        """Return written/source ratio, or ``None`` when source bytes are zero."""

        if self.source_bytes == 0:
            return None
        return self.written_payload_bytes / self.source_bytes

    @property
    def payload_savings_percent(self) -> float | None:
        """Return payload bytes saved as a percentage of source bytes."""

        if self.source_bytes == 0:
            return None
        return self.saved_payload_bytes / self.source_bytes * 100.0


@dataclass(frozen=True, slots=True)
class TransformationBreakdown:
    """One transformation label and its exact regular-file payload totals."""

    transformation: PayloadTransformation
    totals: PayloadBreakdown


@dataclass(frozen=True, slots=True)
class ExtensionBreakdown:
    """One original source extension with total and per-transformation outcomes."""

    extension: str
    totals: PayloadBreakdown
    transformations: tuple[TransformationBreakdown, ...]


@dataclass(frozen=True, slots=True)
class RunReport:
    """Immutable final result for newly selected work in one successful invocation.

    Recovery completed before the invocation's new-work plan remains explicitly
    separate through ``recovered_archives``. Byte totals describe regular payload
    content only; they do not imply filesystem allocation or a future container
    size such as ZIP bytes.
    """

    elapsed_seconds: float
    policies: tuple[str, ...]
    logical_streams_selected: int
    archive_units_completed: int
    archive_directories: tuple[Path, ...]
    recovered_archives: tuple[Path, ...]
    regular_files: int
    preserved_symlinks: int
    dropped_alias_symlinks: int
    skipped_symlinks: int
    source_bytes: int
    written_payload_bytes: int
    transformations: tuple[TransformationBreakdown, ...]
    extensions: tuple[ExtensionBreakdown, ...]
    archive_container_bytes: int | None = None
    filesystem_allocation_bytes: int | None = None

    @property
    def total_links(self) -> int:
        """Return all observed selected symlink dispositions."""

        return self.preserved_symlinks + self.dropped_alias_symlinks + self.skipped_symlinks

    @property
    def saved_payload_bytes(self) -> int:
        """Return source bytes minus written transformed payload bytes."""

        return self.source_bytes - self.written_payload_bytes

    @property
    def payload_size_ratio(self) -> float | None:
        """Return written/source ratio, or ``None`` when source bytes are zero."""

        if self.source_bytes == 0:
            return None
        return self.written_payload_bytes / self.source_bytes

    @property
    def payload_savings_percent(self) -> float | None:
        """Return payload bytes saved as a percentage of source bytes."""

        if self.source_bytes == 0:
            return None
        return self.saved_payload_bytes / self.source_bytes * 100.0


def build_run_report(
    results: tuple[PolicyRunResult, ...],
    *,
    elapsed_seconds: float,
) -> RunReport:
    """Aggregate completed policy results into one invocation-level result model."""

    payload_groups = tuple(
        group
        for result in results
        for archive in result.archives
        for group in archive.regular_payload_groups
    )
    transformations = _transformation_breakdowns(payload_groups)
    extensions = _extension_breakdowns(payload_groups)
    return RunReport(
        elapsed_seconds=max(0.0, elapsed_seconds),
        policies=tuple(result.policy_name for result in results),
        logical_streams_selected=sum(
            len({(str(plan.source_root), plan.plan_id) for plan in result.plan.archive_plans})
            for result in results
        ),
        archive_units_completed=sum(len(result.archives) for result in results),
        archive_directories=tuple(
            archive.archive_directory for result in results for archive in result.archives
        ),
        recovered_archives=tuple(path for result in results for path in result.recovered_archives),
        regular_files=sum(group.files for group in payload_groups),
        preserved_symlinks=sum(
            archive.preserved_symlinks for result in results for archive in result.archives
        ),
        dropped_alias_symlinks=sum(
            archive.dropped_alias_symlinks for result in results for archive in result.archives
        ),
        skipped_symlinks=sum(
            archive.skipped_symlinks for result in results for archive in result.archives
        ),
        source_bytes=sum(group.source_bytes for group in payload_groups),
        written_payload_bytes=sum(group.written_payload_bytes for group in payload_groups),
        transformations=transformations,
        extensions=extensions,
    )


def run_report_data(report: RunReport) -> dict[str, Any]:
    """Return the complete machine-readable representation of one run report."""

    return {
        "schema_version": RUN_REPORT_SCHEMA_VERSION,
        "elapsed_seconds": report.elapsed_seconds,
        "policies": list(report.policies),
        "logical_streams_selected": report.logical_streams_selected,
        "archive_units_completed": report.archive_units_completed,
        "archive_directories": [str(path) for path in report.archive_directories],
        "recovered_archives": [str(path) for path in report.recovered_archives],
        "regular_files": report.regular_files,
        "links": {
            "total": report.total_links,
            "preserved": report.preserved_symlinks,
            "dropped_aliases": report.dropped_alias_symlinks,
            "skipped": report.skipped_symlinks,
        },
        "source_bytes": report.source_bytes,
        "written_payload_bytes": report.written_payload_bytes,
        "saved_payload_bytes": report.saved_payload_bytes,
        "payload_size_ratio": report.payload_size_ratio,
        "payload_savings_percent": report.payload_savings_percent,
        "archive_container_bytes": report.archive_container_bytes,
        "filesystem_allocation_bytes": report.filesystem_allocation_bytes,
        "transformations": [_transformation_data(item) for item in report.transformations],
        "extensions": [_extension_data(item) for item in report.extensions],
    }


def render_run_summary(report: RunReport, *, maximum_extensions: int = 8) -> str:
    """Render a bounded human-oriented console summary from one run report."""

    if maximum_extensions < 1:
        raise ValueError("maximum_extensions must be at least 1")
    savings_percent = _format_percent(report.payload_savings_percent)
    ratio_percent = _format_ratio_percent(report.payload_size_ratio)
    lines = [
        "Stream Archiver run summary",
        f"  Runtime: {format_elapsed(report.elapsed_seconds)}",
        f"  Policies: {len(report.policies)} ({', '.join(report.policies) or 'none'})",
        (
            f"  Streams: {report.logical_streams_selected} selected; "
            f"{_count_phrase(report.archive_units_completed, 'archive unit')} completed"
        ),
        f"  Prior archives recovered: {len(report.recovered_archives)}",
        f"  Regular files: {report.regular_files}",
        (
            f"  Links: {report.total_links} total; {report.preserved_symlinks} preserved; "
            f"{report.dropped_alias_symlinks} aliases removed; "
            f"{report.skipped_symlinks} skipped"
        ),
        f"  Source payload: {format_bytes(report.source_bytes)}",
        f"  Written payload: {format_bytes(report.written_payload_bytes)}",
        (
            f"  Payload savings: {_format_signed_bytes(report.saved_payload_bytes)} "
            f"({savings_percent}); payload-size ratio {ratio_percent} of source"
        ),
        "",
        "Transformation disposition:",
    ]
    for item in report.transformations:
        lines.append(_human_breakdown_line(item.transformation.value, item.totals))
    if not report.transformations:
        lines.append("  none")

    lines.extend(["", "Original extensions:"])
    extensions = report.extensions[:maximum_extensions]
    for item in extensions:
        transformations = ", ".join(
            f"{part.transformation.value} {part.totals.files}"
            for part in item.transformations
            if part.totals.files
        )
        lines.append(
            f"  {item.extension}: {_count_phrase(item.totals.files, 'file')}; "
            f"source {format_bytes(item.totals.source_bytes)}; "
            f"written {format_bytes(item.totals.written_payload_bytes)}; "
            f"{transformations or 'no regular payload'}"
        )
    if len(report.extensions) > maximum_extensions:
        omitted = report.extensions[maximum_extensions:]
        lines.append(
            f"  other: {_count_phrase(sum(item.totals.files for item in omitted), 'file')} across "
            f"{len(omitted)} extensions; source "
            f"{format_bytes(sum(item.totals.source_bytes for item in omitted))}"
        )
    if not report.extensions:
        lines.append("  none")

    lines.extend(
        [
            "",
            (
                "Byte boundary: written payload excludes archive metadata, filesystem allocation, "
                "and future container overhead."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def render_run_markdown(report: RunReport) -> str:
    """Render a complete human-readable Markdown report from one run result."""

    savings_percent = _format_percent(report.payload_savings_percent)
    ratio_percent = _format_ratio_percent(report.payload_size_ratio)
    lines = [
        "# Stream Archiver run report",
        "",
        "## Summary",
        "",
        f"- Runtime: **{format_elapsed(report.elapsed_seconds)}**",
        f"- Policies: **{len(report.policies)}** — {', '.join(report.policies) or 'none'}",
        f"- Logical streams selected: **{report.logical_streams_selected}**",
        f"- Archive units completed: **{report.archive_units_completed}**",
        f"- Prior archives recovered: **{len(report.recovered_archives)}**",
        f"- Regular files: **{report.regular_files}**",
        (
            f"- Links: **{report.total_links}** total; {report.preserved_symlinks} preserved; "
            f"{report.dropped_alias_symlinks} aliases removed; {report.skipped_symlinks} skipped"
        ),
        f"- Source payload: **{format_bytes(report.source_bytes)}** ({report.source_bytes} bytes)",
        (
            f"- Written payload: **{format_bytes(report.written_payload_bytes)}** "
            f"({report.written_payload_bytes} bytes)"
        ),
        (
            f"- Payload savings: **{_format_signed_bytes(report.saved_payload_bytes)}** "
            f"({savings_percent}); payload-size ratio **{ratio_percent} of source**"
        ),
        "",
        "## Transformation disposition",
        "",
        (
            "| Transformation | Files | Source bytes | Written payload bytes | "
            "Saved payload bytes | Savings |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in report.transformations:
        lines.append(_markdown_breakdown_row(item.transformation.value, item.totals))
    if not report.transformations:
        lines.append("| none | 0 | 0 | 0 | 0 | n/a |")

    lines.extend(
        [
            "",
            "## Original extension × transformation",
            "",
            (
                "| Extension | Transformation | Files | Source bytes | "
                "Written payload bytes | Saved payload bytes |"
            ),
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for extension in report.extensions:
        for transformation in extension.transformations:
            totals = transformation.totals
            lines.append(
                f"| {_escape_markdown(extension.extension)} | "
                f"{transformation.transformation.value} | "
                f"{totals.files} | {totals.source_bytes} | {totals.written_payload_bytes} | "
                f"{totals.saved_payload_bytes} |"
            )
    if not report.extensions:
        lines.append("| none | none | 0 | 0 | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Archive outputs",
            "",
        ]
    )
    if report.archive_directories:
        lines.extend(f"- {_markdown_inline_code(str(path))}" for path in report.archive_directories)
    else:
        lines.append("- No new archive units were selected.")
    if report.recovered_archives:
        lines.extend(["", "## Recovered prior archives", ""])
        lines.extend(f"- {_markdown_inline_code(str(path))}" for path in report.recovered_archives)

    lines.extend(
        [
            "",
            "## Metric boundary",
            "",
            (
                "`written_payload_bytes` measures transformed regular-file payload bytes. It does "
                "not include archive metadata, filesystem allocation, or future archive-container "
                "overhead. Exact integer bytes in this report are authoritative; human units use "
                "decimal SI formatting."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _transformation_breakdowns(
    payload_groups: tuple[RegularPayloadGroupResult, ...],
) -> tuple[TransformationBreakdown, ...]:
    totals: dict[PayloadTransformation, list[int]] = {
        transformation: [0, 0, 0] for transformation in PayloadTransformation
    }
    for group in payload_groups:
        transformation = _transformation(group.action)
        bucket = totals[transformation]
        bucket[0] += group.files
        bucket[1] += group.source_bytes
        bucket[2] += group.written_payload_bytes
    return tuple(
        TransformationBreakdown(
            transformation=transformation,
            totals=PayloadBreakdown(*totals[transformation]),
        )
        for transformation in PayloadTransformation
        if totals[transformation][0] > 0
    )


def _extension_breakdowns(
    payload_groups: tuple[RegularPayloadGroupResult, ...],
) -> tuple[ExtensionBreakdown, ...]:
    raw: dict[str, dict[PayloadTransformation, list[int]]] = {}
    for group in payload_groups:
        extension = group.source_suffix or _EXTENSION_NONE
        transformation = _transformation(group.action)
        by_transformation = raw.setdefault(
            extension,
            {item: [0, 0, 0] for item in PayloadTransformation},
        )
        bucket = by_transformation[transformation]
        bucket[0] += group.files
        bucket[1] += group.source_bytes
        bucket[2] += group.written_payload_bytes

    results: list[ExtensionBreakdown] = []
    for extension, by_transformation in raw.items():
        parts = tuple(
            TransformationBreakdown(
                transformation=transformation,
                totals=PayloadBreakdown(*values),
            )
            for transformation, values in by_transformation.items()
            if values[0] > 0
        )
        results.append(
            ExtensionBreakdown(
                extension=extension,
                totals=PayloadBreakdown(
                    files=sum(item.totals.files for item in parts),
                    source_bytes=sum(item.totals.source_bytes for item in parts),
                    written_payload_bytes=sum(item.totals.written_payload_bytes for item in parts),
                ),
                transformations=parts,
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda item: (-item.totals.source_bytes, -item.totals.files, item.extension),
        )
    )


def _transformation(action: ActionKind) -> PayloadTransformation:
    if action in {ActionKind.MOVE, ActionKind.COPY}:
        return PayloadTransformation.UNCOMPRESSED
    if action is ActionKind.GZIP:
        return PayloadTransformation.GZIP
    if action is ActionKind.BZ2:
        return PayloadTransformation.BZIP2
    raise ValueError(f"regular payload has unsupported action: {action.value}")


def _transformation_data(item: TransformationBreakdown) -> dict[str, Any]:
    return {
        "transformation": item.transformation.value,
        **_breakdown_data(item.totals),
    }


def _extension_data(item: ExtensionBreakdown) -> dict[str, Any]:
    return {
        "extension": item.extension,
        **_breakdown_data(item.totals),
        "transformations": [_transformation_data(part) for part in item.transformations],
    }


def _breakdown_data(item: PayloadBreakdown) -> dict[str, Any]:
    return {
        "files": item.files,
        "source_bytes": item.source_bytes,
        "written_payload_bytes": item.written_payload_bytes,
        "saved_payload_bytes": item.saved_payload_bytes,
        "payload_size_ratio": item.payload_size_ratio,
        "payload_savings_percent": item.payload_savings_percent,
    }


def _human_breakdown_line(label: str, totals: PayloadBreakdown) -> str:
    return (
        f"  {label}: {_count_phrase(totals.files, 'file')}; "
        f"source {format_bytes(totals.source_bytes)}; "
        f"written {format_bytes(totals.written_payload_bytes)}; "
        f"saved {_format_signed_bytes(totals.saved_payload_bytes)} "
        f"({_format_percent(totals.payload_savings_percent)})"
    )


def _markdown_breakdown_row(label: str, totals: PayloadBreakdown) -> str:
    return (
        f"| {label} | {totals.files} | {totals.source_bytes} | "
        f"{totals.written_payload_bytes} | {totals.saved_payload_bytes} | "
        f"{_format_percent(totals.payload_savings_percent)} |"
    )


def _count_phrase(count: int, singular: str) -> str:
    """Return a count with a simple singular/plural English noun."""

    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


def _format_signed_bytes(value: int) -> str:
    if value < 0:
        return f"-{format_bytes(-value)}"
    return format_bytes(value)


def _format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _format_ratio_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "\\`")


def _markdown_inline_code(value: str) -> str:
    """Return Markdown inline code with a delimiter safe for embedded backticks."""

    longest = 0
    current = 0
    for character in value:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    delimiter = "`" * (longest + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"
