"""Advisory destination-capacity observation and warning-state arithmetic."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stream_archiver.model import ArchivePlan
from stream_archiver.progress import archive_regular_work

MATERIAL_ABSOLUTE_BYTES = 64 * 1024 * 1024
MATERIAL_RELATIVE_DIVISOR = 20  # 5 percent.


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """One available-space observation for the filesystem containing a destination."""

    destination: Path
    total_bytes: int
    free_bytes: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CapacityUnavailable:
    """A failed advisory capacity observation with a bounded diagnostic reason."""

    destination: Path
    observed_at: datetime
    reason: str


CapacityObservation = CapacitySnapshot | CapacityUnavailable
CapacityObserver = Callable[[Path], CapacityObservation]


@dataclass(frozen=True, slots=True)
class CapacityWarningDecision:
    """State-based decision about whether an execution warning should be emitted."""

    warn: bool
    reason: str | None
    shortfall_bytes: int


@dataclass(frozen=True, slots=True)
class UnexpectedCapacityConsumption:
    """Arithmetic for free-space loss not explained by observed archive allocation."""

    free_before_bytes: int
    free_after_bytes: int
    observed_loss_bytes: int
    known_allocation_bytes: int
    residual_bytes: int
    threshold_bytes: int
    material: bool


@dataclass(slots=True)
class _DestinationWarningState:
    """Remember only warning state needed to suppress equivalent observations."""

    insufficient: bool = False
    unavailable: bool = False
    last_warned_shortfall_bytes: int = 0


class CapacityWarningTracker:
    """Suppress repeated low-capacity warnings by semantic state, never by time."""

    def __init__(self) -> None:
        """Create empty per-destination warning state for one run invocation."""

        self._states: dict[Path, _DestinationWarningState] = {}

    def evaluate(
        self,
        destination: Path,
        observation: CapacityObservation,
        *,
        remaining_source_bytes: int,
    ) -> CapacityWarningDecision:
        """Return whether a new unavailable/insufficient/materially-worse warning is due."""

        state = self._states.setdefault(destination, _DestinationWarningState())
        if isinstance(observation, CapacityUnavailable):
            warn = not state.unavailable
            state.unavailable = True
            return CapacityWarningDecision(
                warn=warn,
                reason="capacity-unavailable" if warn else None,
                shortfall_bytes=0,
            )

        state.unavailable = False
        shortfall = max(0, remaining_source_bytes - observation.free_bytes)
        if shortfall == 0:
            state.insufficient = False
            return CapacityWarningDecision(False, None, 0)

        if not state.insufficient:
            state.insufficient = True
            state.last_warned_shortfall_bytes = shortfall
            return CapacityWarningDecision(True, "insufficient-capacity", shortfall)

        worsening = shortfall - state.last_warned_shortfall_bytes
        threshold = materiality_threshold(state.last_warned_shortfall_bytes)
        if worsening >= threshold:
            state.last_warned_shortfall_bytes = shortfall
            return CapacityWarningDecision(True, "materially-worse-capacity", shortfall)
        return CapacityWarningDecision(False, None, shortfall)


def observe_capacity(
    destination: Path,
    *,
    statvfs: Callable[[os.PathLike[str] | str], os.statvfs_result] = os.statvfs,
    clock: Callable[[], datetime] | None = None,
) -> CapacityObservation:
    """Observe available bytes on the filesystem that will contain ``destination``.

    Planning does not create a missing destination. If it does not exist yet,
    inspect the nearest existing ancestor because that is where creation would
    initially allocate storage. Observation failure remains advisory and is
    represented explicitly instead of being converted to zero capacity.
    """

    observed_at = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    probe = _nearest_existing_path(destination)
    try:
        values = statvfs(probe)
    except OSError as exc:
        return CapacityUnavailable(destination, observed_at, _bounded_reason(exc))
    fragment_size = values.f_frsize or values.f_bsize
    total_bytes = int(values.f_blocks * fragment_size)
    free_bytes = int(values.f_bavail * fragment_size)
    return CapacitySnapshot(destination, total_bytes, free_bytes, observed_at)


def selected_source_bytes_by_destination(
    plans: Sequence[ArchivePlan],
) -> dict[Path, int]:
    """Aggregate selected regular source bytes by exact destination root."""

    totals: dict[Path, int] = {}
    for plan in plans:
        _, byte_count = archive_regular_work(plan)
        totals[plan.destination_root] = totals.get(plan.destination_root, 0) + byte_count
    return totals


def materiality_threshold(reference_bytes: int) -> int:
    """Return the 64 MiB-and-5% advisory noise threshold in exact bytes."""

    relative = max(0, reference_bytes) // MATERIAL_RELATIVE_DIVISOR
    return max(MATERIAL_ABSOLUTE_BYTES, relative)


def assess_unexpected_capacity_consumption(
    *,
    free_before_bytes: int,
    free_after_bytes: int,
    known_allocation_bytes: int,
) -> UnexpectedCapacityConsumption:
    """Compare observed capacity loss with Stream Archiver's measured allocation."""

    observed_loss = max(0, free_before_bytes - free_after_bytes)
    known = max(0, known_allocation_bytes)
    residual = max(0, observed_loss - known)
    threshold = materiality_threshold(known)
    return UnexpectedCapacityConsumption(
        free_before_bytes=free_before_bytes,
        free_after_bytes=free_after_bytes,
        observed_loss_bytes=observed_loss,
        known_allocation_bytes=known,
        residual_bytes=residual,
        threshold_bytes=threshold,
        material=residual >= threshold,
    )


def allocated_tree_bytes(root: Path) -> int | None:
    """Return POSIX allocated bytes for one committed archive tree when supported.

    The calculation uses ``st_blocks * 512`` from ``lstat`` and never follows
    symbolic links. ``None`` means the platform does not expose block counts.
    """

    total = 0
    stack = [root]
    try:
        while stack:
            path = stack.pop()
            metadata = path.lstat()
            blocks = getattr(metadata, "st_blocks", None)
            if blocks is None:
                return None
            total += int(blocks) * 512
            if stat.S_ISDIR(metadata.st_mode):
                stack.extend(path.iterdir())
    except OSError:
        return None
    return total


def _nearest_existing_path(path: Path) -> Path:
    """Return the nearest existing ancestor without creating destination state."""

    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent
    return candidate


def _bounded_reason(exc: OSError) -> str:
    """Return a concise observation failure reason without embedding target paths."""

    detail = exc.strerror or str(exc)
    return f"{type(exc).__name__}: {detail}"
