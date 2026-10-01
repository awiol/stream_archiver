"""Run-level archival progress accounting independent of log presentation."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from stream_archiver.model import ActionKind, ArchivePlan, EntryKind

_REGULAR_PAYLOAD_ACTIONS = frozenset(
    {ActionKind.MOVE, ActionKind.COPY, ActionKind.GZIP, ActionKind.BZ2}
)


@dataclass(frozen=True, slots=True)
class RunTotals:
    """Immutable denominator for newly selected archive work in one invocation."""

    streams: int
    regular_files: int
    source_bytes: int

    @classmethod
    def from_plans(cls, plans: Sequence[ArchivePlan]) -> RunTotals:
        """Build exact selected-work totals from immutable archive plans."""

        regular_files = 0
        source_bytes = 0
        for plan in plans:
            files, byte_count = archive_regular_work(plan)
            regular_files += files
            source_bytes += byte_count
        logical_streams = len({(str(plan.source_root), plan.plan_id) for plan in plans})
        return cls(streams=logical_streams, regular_files=regular_files, source_bytes=source_bytes)


@dataclass(frozen=True, slots=True)
class RunProgressSnapshot:
    """Immutable observation of top-level progress for newly selected work."""

    stream_index: int
    stream_total: int
    files_completed: int
    files_total: int
    source_bytes_processed: int
    source_bytes_total: int
    written_payload_bytes: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ExecutionProgressDelta:
    """Low-level executor observation consumed by the run-level tracker."""

    source_bytes_processed: int = 0
    written_payload_bytes: int = 0

    def __post_init__(self) -> None:
        """Reject negative deltas because progress counters are monotonic."""

        if self.source_bytes_processed < 0 or self.written_payload_bytes < 0:
            raise ValueError("progress deltas must be non-negative")


ProgressSink = Callable[[ExecutionProgressDelta], None]


class RunProgressTracker:
    """Maintain invocation-level counters and monotonic elapsed time.

    The tracker owns only newly selected archival work. Recovery of a committed
    archive from an earlier invocation remains a separate orchestration result.
    """

    def __init__(
        self,
        totals: RunTotals,
        *,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
    ) -> None:
        """Create a tracker with an injectable monotonic clock for verification."""

        self._totals = totals
        self._clock = clock
        self._started_at = clock() if started_at is None else started_at
        self._stream_index = 0
        self._files_completed = 0
        self._source_bytes_processed = 0
        self._written_payload_bytes = 0
        self._current_stream_source_bytes = 0
        self._last_elapsed_seconds = 0.0
        self._next_source_info_percent = 10

    @property
    def totals(self) -> RunTotals:
        """Return the immutable selected-work denominator."""

        return self._totals

    def begin_stream(self, index: int) -> RunProgressSnapshot:
        """Select the current stream/archive ordinal and return a snapshot."""

        if index < 1 or index > self._totals.streams:
            raise ValueError("stream index is outside the selected run totals")
        if index < self._stream_index:
            raise ValueError("stream index must not move backwards")
        self._stream_index = index
        self._current_stream_source_bytes = 0
        return self.snapshot()

    def begin_archive_unit(self) -> RunProgressSnapshot:
        """Reset per-unit byte reconciliation while retaining the logical stream ordinal."""

        self._current_stream_source_bytes = 0
        return self.snapshot()

    def record_delta(self, delta: ExecutionProgressDelta) -> RunProgressSnapshot | None:
        """Apply executor byte observations and return a source milestone snapshot.

        A snapshot is returned only when processed source bytes cross a new
        ten-percent work boundary below 100%. This is work-triggered; elapsed
        time never causes an event.
        """

        if delta.source_bytes_processed:
            next_total = self._source_bytes_processed + delta.source_bytes_processed
            if next_total > self._totals.source_bytes:
                raise ValueError("processed source bytes exceed the selected total")
            self._source_bytes_processed = next_total
            self._current_stream_source_bytes += delta.source_bytes_processed
        if delta.written_payload_bytes:
            self._written_payload_bytes += delta.written_payload_bytes

        if self._totals.source_bytes <= 0:
            return None
        percent = self._source_bytes_processed * 100 // self._totals.source_bytes
        if percent >= self._next_source_info_percent and percent < 100:
            self._next_source_info_percent = ((percent // 10) + 1) * 10
            return self.snapshot()
        return None

    def complete_archive_unit(self, plan: ArchivePlan) -> RunProgressSnapshot:
        """Mark one successfully completed archive-unit transaction as complete.

        Successful reconciliation can complete a selected archive without
        copying every source byte during this invocation. In that case, account
        the remaining selected bytes at transaction completion so final run
        progress reflects completed selected work rather than copy mechanics.
        """

        files, source_bytes = archive_regular_work(plan)
        missing_source_bytes = source_bytes - self._current_stream_source_bytes
        if missing_source_bytes < 0:
            raise ValueError("stream processed more source bytes than its plan contains")
        self._source_bytes_processed += missing_source_bytes
        self._files_completed += files
        if self._files_completed > self._totals.regular_files:
            raise ValueError("completed files exceed the selected total")
        if self._source_bytes_processed > self._totals.source_bytes:
            raise ValueError("processed source bytes exceed the selected total")
        self._current_stream_source_bytes = source_bytes
        return self.snapshot()

    def complete_stream(self, plan: ArchivePlan) -> RunProgressSnapshot:
        """Compatibility alias for one-unit logical streams."""

        return self.complete_archive_unit(plan)

    def snapshot(self) -> RunProgressSnapshot:
        """Return current counters with non-decreasing monotonic elapsed time."""

        observed = max(0.0, self._clock() - self._started_at)
        elapsed = max(self._last_elapsed_seconds, observed)
        self._last_elapsed_seconds = elapsed
        return RunProgressSnapshot(
            stream_index=self._stream_index,
            stream_total=self._totals.streams,
            files_completed=self._files_completed,
            files_total=self._totals.regular_files,
            source_bytes_processed=self._source_bytes_processed,
            source_bytes_total=self._totals.source_bytes,
            written_payload_bytes=self._written_payload_bytes,
            elapsed_seconds=elapsed,
        )


def archive_regular_work(plan: ArchivePlan) -> tuple[int, int]:
    """Return selected regular payload-file count and source bytes for one plan."""

    actions = tuple(
        action
        for action in plan.actions
        if action.source.kind is EntryKind.REGULAR and action.kind in _REGULAR_PAYLOAD_ACTIONS
    )
    return len(actions), sum(action.source.identity.size for action in actions)
