"""Public orchestration for planning, running, and verifying archival policies."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stream_archiver.capacity import (
    CapacityObservation,
    CapacityObserver,
    CapacitySnapshot,
    CapacityUnavailable,
    CapacityWarningTracker,
    UnexpectedCapacityConsumption,
    allocated_tree_bytes,
    assess_unexpected_capacity_consumption,
    observe_capacity,
    selected_source_bytes_by_destination,
)
from stream_archiver.config import Policy
from stream_archiver.discovery import discover_entries
from stream_archiver.errors import RecoveryError
from stream_archiver.executor import (
    MANIFEST_NAME,
    ArchiveExecutionResult,
    ArchiveVerificationResult,
    _verify_archive_unlocked,
    execute_plan,
    recover_pending_archives,
)
from stream_archiver.locking import resource_locks
from stream_archiver.model import ArchivePlan
from stream_archiver.observability import log_event
from stream_archiver.planning import (
    boundary_entries_for_policy,
    build_archive_plan,
    select_eligible_streams,
    split_partitioned_streams,
)
from stream_archiver.presentation import format_bytes, progress_fields
from stream_archiver.progress import (
    ExecutionProgressDelta,
    RunProgressSnapshot,
    RunProgressTracker,
    RunTotals,
    archive_regular_work,
)

LOGGER = logging.getLogger(__name__)
_ARCHIVE_NAME_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{6}Z--\d{8}T\d{6}\.\d{6}Z--[0-9a-f]{10}$")


@dataclass(frozen=True, slots=True)
class SourcePlan:
    """Inspectable planning result for exactly one configured source root."""

    source_root: Path
    discovered_entries: int
    streams: int
    eligible_streams: int
    archive_plans: tuple[ArchivePlan, ...]


@dataclass(frozen=True, slots=True)
class PolicyPlan:
    """Inspectable result of evaluating all sources in one policy."""

    policy_name: str
    destination_root: Path
    recursive: bool
    stream_partition: str
    source_plans: tuple[SourcePlan, ...]

    @property
    def discovered_entries(self) -> int:
        """Return the number of entries discovered across independent sources."""

        return sum(item.discovered_entries for item in self.source_plans)

    @property
    def streams(self) -> int:
        """Return the number of source-local streams considered."""

        return sum(item.streams for item in self.source_plans)

    @property
    def eligible_streams(self) -> int:
        """Return the number of source-local streams old enough to archive."""

        return sum(item.eligible_streams for item in self.source_plans)

    @property
    def archive_plans(self) -> tuple[ArchivePlan, ...]:
        """Return all source-scoped plans without combining their entries."""

        return tuple(
            plan for source_plan in self.source_plans for plan in source_plan.archive_plans
        )


@dataclass(frozen=True, slots=True)
class PolicyRunResult:
    """Observed archives and recoveries produced by one policy run."""

    policy_name: str
    recovered_archives: tuple[Path, ...]
    plan: PolicyPlan
    archives: tuple[ArchiveExecutionResult, ...]


def plan_policy(policy: Policy, *, now: datetime) -> PolicyPlan:
    """Plan complete old streams independently for every configured source."""

    log_event(
        LOGGER,
        logging.INFO,
        "policy_planning_started",
        "planning policy sources",
        policy=policy.name,
        sources=len(policy.sources),
    )
    source_plans: list[SourcePlan] = []
    for source_index, source in enumerate(policy.sources, start=1):
        entries = discover_entries(source, recursive=policy.recursive)
        boundary_entries = boundary_entries_for_policy(policy, source, entries)
        streams = split_partitioned_streams(
            boundary_entries,
            minimum_gap=policy.stream_gap,
            partition=policy.stream_partition,
        )
        eligible = select_eligible_streams(
            streams,
            now=now,
            minimum_age=policy.minimum_age,
        )
        plans = tuple(
            plan
            for stream in eligible
            if (
                plan := build_archive_plan(
                    policy,
                    source,
                    stream,
                    source_entries=entries,
                )
            )
            is not None
        )
        log_event(
            LOGGER,
            logging.INFO,
            "source_planning_completed",
            "source planning completed",
            policy=policy.name,
            source=source,
            source_progress=f"{source_index}/{len(policy.sources)}",
            discovered_entries=len(entries),
            streams=len(streams),
            eligible_streams=len(eligible),
            planned_archives=len(plans),
        )
        source_plans.append(
            SourcePlan(
                source_root=source,
                discovered_entries=len(entries),
                streams=len(streams),
                eligible_streams=len(eligible),
                archive_plans=plans,
            )
        )
    result = PolicyPlan(
        policy_name=policy.name,
        destination_root=policy.destination,
        recursive=policy.recursive,
        stream_partition=policy.stream_partition.value,
        source_plans=tuple(source_plans),
    )
    log_event(
        LOGGER,
        logging.INFO,
        "policy_planning_completed",
        "policy planning completed",
        policy=policy.name,
        discovered_entries=result.discovered_entries,
        streams=result.streams,
        eligible_streams=result.eligible_streams,
        planned_archives=len(result.archive_plans),
    )
    return result


def run_policy(policy: Policy) -> PolicyRunResult:
    """Execute one policy with observed time and mandatory resource locking.

    Artificial reference times belong to :func:`plan_policy`. The exported
    destructive API has neither an artificial clock nor a lock-bypass flag.
    """

    with resource_locks((*policy.sources, policy.destination), exclusive=True):
        return _run_policy_locked(policy)


def _run_policy_locked(policy: Policy) -> PolicyRunResult:
    """Execute one policy when the caller already owns its mutation locks."""

    return _run_policies_at(
        (policy,),
        planning_time=datetime.now(UTC),
        resources_locked=True,
    )[0]


def _run_policy_at(
    policy: Policy,
    *,
    planning_time: datetime,
    resources_locked: bool = False,
    event_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    capacity_observer: CapacityObserver = observe_capacity,
) -> PolicyRunResult:
    """Internal deterministic execution helper used by behavior tests.

    Production callers use :func:`run_policy`. This helper exists so tests can
    reproduce age, evidence, progress, and capacity boundaries without
    reintroducing a public destructive artificial-time control.
    """

    return _run_policies_at(
        (policy,),
        planning_time=planning_time,
        resources_locked=resources_locked,
        event_clock=event_clock,
        monotonic_clock=monotonic_clock,
        capacity_observer=capacity_observer,
    )[0]


def _run_policies_locked(
    policies: tuple[Policy, ...],
    *,
    planning_time: datetime | None = None,
    on_policy_completed: Callable[[PolicyRunResult], None] | None = None,
) -> tuple[PolicyRunResult, ...]:
    """Execute selected policies as one top-level run under caller-owned locks."""

    return _run_policies_at(
        policies,
        planning_time=planning_time or datetime.now(UTC),
        resources_locked=True,
        on_policy_completed=on_policy_completed,
    )


@dataclass(frozen=True, slots=True)
class _PreparedPolicyRun:
    """Recovered state and immutable selected-work plan for one policy."""

    policy: Policy
    recovered_archives: tuple[Path, ...]
    plan: PolicyPlan


def _run_policies_at(
    policies: tuple[Policy, ...],
    *,
    planning_time: datetime,
    resources_locked: bool = False,
    event_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    capacity_observer: CapacityObserver = observe_capacity,
    on_policy_completed: Callable[[PolicyRunResult], None] | None = None,
) -> tuple[PolicyRunResult, ...]:
    """Execute selected policies with one progress/capacity accounting boundary.

    Recovery is completed before selected-work totals are established. All
    selected policies use one planning reference time so the invocation has one
    stable stream/file/byte denominator before new destructive processing starts.
    """

    if not policies:
        return ()
    if not resources_locked:
        resources = tuple(
            sorted(
                {
                    *(source for policy in policies for source in policy.sources),
                    *(policy.destination for policy in policies),
                },
                key=str,
            )
        )
        with resource_locks(resources, exclusive=True):
            return _run_policies_at(
                policies,
                planning_time=planning_time,
                resources_locked=True,
                event_clock=event_clock,
                monotonic_clock=monotonic_clock,
                capacity_observer=capacity_observer,
                on_policy_completed=on_policy_completed,
            )

    started_at = monotonic_clock()
    prepared = tuple(
        _prepare_policy_run(policy, planning_time=planning_time, event_clock=event_clock)
        for policy in policies
    )
    archive_plans = tuple(archive for item in prepared for archive in item.plan.archive_plans)
    progress = RunProgressTracker(
        RunTotals.from_plans(archive_plans),
        clock=monotonic_clock,
        started_at=started_at,
    )
    remaining_by_destination = selected_source_bytes_by_destination(archive_plans)
    warning_tracker = CapacityWarningTracker()
    _log_run_progress(
        "run_progress_started",
        "selected run totals established",
        progress.snapshot(),
    )

    results: list[PolicyRunResult] = []
    stream_index = 0
    for item in prepared:
        archives: list[ArchiveExecutionResult] = []
        for plan in item.plan.archive_plans:
            stream_index += 1
            start_snapshot = progress.begin_stream(stream_index)
            _log_run_progress(
                "run_stream_started",
                "selected stream/archive execution started",
                start_snapshot,
                policy=item.policy.name,
                archive=plan.archive_name,
                destination=plan.destination_root,
            )

            stream_files, stream_source_bytes = archive_regular_work(plan)
            remaining_source_bytes = remaining_by_destination[plan.destination_root]
            before = capacity_observer(plan.destination_root)
            decision = warning_tracker.evaluate(
                plan.destination_root,
                before,
                remaining_source_bytes=remaining_source_bytes,
            )
            _log_capacity_precheck(
                before,
                decision_reason=decision.reason if decision.warn else None,
                current_stream_source_bytes=stream_source_bytes,
                remaining_source_bytes=remaining_source_bytes,
                policy=item.policy.name,
                archive=plan.archive_name,
            )

            existed_before = os.path.lexists(plan.final_directory)

            def progress_sink(
                delta: ExecutionProgressDelta,
                policy_name: str = item.policy.name,
                archive_name: str = plan.archive_name,
                destination: Path = plan.destination_root,
            ) -> None:
                """Fold executor byte observations into the invocation tracker."""

                milestone = progress.record_delta(delta)
                if milestone is not None:
                    _log_run_progress(
                        "run_progress",
                        "selected source-byte progress crossed a work milestone",
                        milestone,
                        policy=policy_name,
                        archive=archive_name,
                        destination=destination,
                    )

            result = execute_plan(
                plan,
                event_clock=event_clock,
                progress_sink=progress_sink,
            )
            archives.append(result)

            remaining_after = max(0, remaining_source_bytes - stream_source_bytes)
            remaining_by_destination[plan.destination_root] = remaining_after
            after = capacity_observer(plan.destination_root)
            if (
                not existed_before
                and isinstance(before, CapacitySnapshot)
                and isinstance(after, CapacitySnapshot)
            ):
                allocation = allocated_tree_bytes(result.archive_directory)
                if allocation is not None:
                    assessment = assess_unexpected_capacity_consumption(
                        free_before_bytes=before.free_bytes,
                        free_after_bytes=after.free_bytes,
                        known_allocation_bytes=allocation,
                    )
                    _log_capacity_change(
                        plan.destination_root,
                        assessment=assessment,
                        remaining_source_bytes=remaining_after,
                        policy=item.policy.name,
                        archive=plan.archive_name,
                    )

            completed_snapshot = progress.complete_stream(plan)
            _log_run_progress(
                "run_stream_completed",
                "selected stream/archive transaction completed",
                completed_snapshot,
                policy=item.policy.name,
                archive=result.archive_directory,
                destination=plan.destination_root,
                stream_regular_files=stream_files,
                stream_source_bytes=stream_source_bytes,
            )

        policy_result = PolicyRunResult(
            policy_name=item.policy.name,
            recovered_archives=item.recovered_archives,
            plan=item.plan,
            archives=tuple(archives),
        )
        results.append(policy_result)
        log_event(
            LOGGER,
            logging.INFO,
            "policy_run_completed",
            "policy execution completed",
            policy=item.policy.name,
            recovered_archives=len(item.recovered_archives),
            archives=len(archives),
        )
        if on_policy_completed is not None:
            on_policy_completed(policy_result)

    _log_run_progress(
        "run_progress_completed",
        "selected run completed",
        progress.snapshot(),
        policy_count=len(policies),
    )
    return tuple(results)


def _prepare_policy_run(
    policy: Policy,
    *,
    planning_time: datetime,
    event_clock: Callable[[], datetime] | None,
) -> _PreparedPolicyRun:
    """Recover prior committed work and then plan new selected work for a policy."""

    log_event(
        LOGGER,
        logging.INFO,
        "policy_run_started",
        "policy execution started",
        policy=policy.name,
        sources=len(policy.sources),
        destination=policy.destination,
    )
    recovered = tuple(
        archive
        for source in policy.sources
        for archive in recover_pending_archives(
            policy.destination,
            source,
            event_clock=event_clock,
        )
    )
    plan = plan_policy(policy, now=planning_time)
    return _PreparedPolicyRun(policy, recovered, plan)


def _log_run_progress(
    event: str,
    message: str,
    snapshot: RunProgressSnapshot,
    **fields: object,
) -> None:
    """Emit one INFO run-progress observation with exact and human fields."""

    log_event(
        LOGGER,
        logging.INFO,
        event,
        message,
        **progress_fields(snapshot),
        **fields,
    )


def _log_capacity_precheck(
    observation: CapacityObservation,
    *,
    decision_reason: str | None,
    current_stream_source_bytes: int,
    remaining_source_bytes: int,
    policy: str,
    archive: str,
) -> None:
    """Emit one per-stream capacity observation and state-based warning."""

    if isinstance(observation, CapacitySnapshot):
        fields = {
            "policy": policy,
            "archive": archive,
            "destination": observation.destination,
            "free_bytes": observation.free_bytes,
            "total_bytes": observation.total_bytes,
            "current_stream_source_bytes": current_stream_source_bytes,
            "remaining_source_bytes": remaining_source_bytes,
            "free": format_bytes(observation.free_bytes),
            "current_stream_source": format_bytes(current_stream_source_bytes),
            "remaining_source": format_bytes(remaining_source_bytes),
            "observed_at": observation.observed_at,
        }
        log_event(
            LOGGER,
            logging.DEBUG,
            "destination_capacity_checked",
            "destination capacity observed before stream staging",
            **fields,
        )
        if decision_reason is not None:
            log_event(
                LOGGER,
                logging.WARNING,
                "destination_capacity_low",
                "free destination capacity is below remaining selected source volume",
                warning_reason=decision_reason,
                shortfall_bytes=max(0, remaining_source_bytes - observation.free_bytes),
                shortfall=format_bytes(max(0, remaining_source_bytes - observation.free_bytes)),
                **fields,
            )
        return

    assert isinstance(observation, CapacityUnavailable)
    if decision_reason is not None:
        log_event(
            LOGGER,
            logging.WARNING,
            "destination_capacity_unavailable",
            "destination capacity could not be observed before stream staging",
            policy=policy,
            archive=archive,
            destination=observation.destination,
            current_stream_source_bytes=current_stream_source_bytes,
            remaining_source_bytes=remaining_source_bytes,
            current_stream_source=format_bytes(current_stream_source_bytes),
            remaining_source=format_bytes(remaining_source_bytes),
            reason=observation.reason,
            observed_at=observation.observed_at,
        )


def _log_capacity_change(
    destination: Path,
    *,
    assessment: UnexpectedCapacityConsumption,
    remaining_source_bytes: int,
    policy: str,
    archive: str,
) -> None:
    """Report measured post-stream capacity change without asserting its cause."""

    fields = {
        "policy": policy,
        "archive": archive,
        "destination": destination,
        "free_before_bytes": assessment.free_before_bytes,
        "free_after_bytes": assessment.free_after_bytes,
        "observed_loss_bytes": assessment.observed_loss_bytes,
        "known_allocation_bytes": assessment.known_allocation_bytes,
        "unexpected_capacity_consumption_bytes": assessment.residual_bytes,
        "materiality_threshold_bytes": assessment.threshold_bytes,
        "remaining_source_bytes": remaining_source_bytes,
        "free_before": format_bytes(assessment.free_before_bytes),
        "free_after": format_bytes(assessment.free_after_bytes),
        "observed_loss": format_bytes(assessment.observed_loss_bytes),
        "known_allocation": format_bytes(assessment.known_allocation_bytes),
        "unexpected_capacity_consumption": format_bytes(assessment.residual_bytes),
        "materiality_threshold": format_bytes(assessment.threshold_bytes),
        "remaining_source": format_bytes(remaining_source_bytes),
    }
    if assessment.material:
        log_event(
            LOGGER,
            logging.WARNING,
            "unexpected_destination_capacity_consumption",
            "unexpected destination capacity consumption observed",
            **fields,
        )
    else:
        log_event(
            LOGGER,
            logging.DEBUG,
            "destination_capacity_change",
            "destination capacity change remained within the advisory noise threshold",
            **fields,
        )


def verify_policies(
    policies: tuple[Policy, ...],
    *,
    all_in_destination: bool = False,
) -> tuple[ArchiveVerificationResult, ...]:
    """Verify selected policy destinations under mandatory shared locks."""

    resources = {policy.destination for policy in policies}
    with resource_locks(resources, exclusive=False):
        return _verify_policies_locked(policies, all_in_destination=all_in_destination)


def _verify_policies_locked(
    policies: tuple[Policy, ...],
    *,
    all_in_destination: bool,
) -> tuple[ArchiveVerificationResult, ...]:
    """Verify selected policies when destination locks are already held."""

    destinations = sorted({policy.destination for policy in policies}, key=str)
    results: list[ArchiveVerificationResult] = []
    for destination in destinations:
        owners = None
        if not all_in_destination:
            owners = {
                (policy.name, str(source))
                for policy in policies
                if policy.destination == destination
                for source in policy.sources
            }
        results.extend(_verify_destination_locked(destination, owners=owners))
    return tuple(results)


def verify_destination(
    destination: Path,
    *,
    owners: set[tuple[str, str]] | None = None,
) -> tuple[ArchiveVerificationResult, ...]:
    """Verify every archive directory under one destination root.

    ``owners`` contains ``(policy_name, source_root)`` pairs for policy-scoped
    verification. ``None`` performs a destination-wide audit. Archive-shaped
    directories with missing manifests are always reported because their
    ownership cannot be established safely. Public calls acquire the shared
    cooperative destination lock by default.
    """

    with resource_locks((destination,), exclusive=False):
        return _verify_destination_locked(destination, owners=owners)


def _verify_destination_locked(
    destination: Path,
    *,
    owners: set[tuple[str, str]] | None,
) -> tuple[ArchiveVerificationResult, ...]:
    """Verify one destination while its shared cooperative lock is held."""

    log_event(
        LOGGER,
        logging.INFO,
        "destination_verification_started",
        "verifying archives under destination",
        destination=destination,
    )
    if not destination.exists():
        raise RecoveryError(f"archive destination does not exist: {destination}")
    if destination.is_symlink() or not destination.is_dir():
        raise RecoveryError(f"archive destination is not a real directory: {destination}")
    results: list[ArchiveVerificationResult] = []
    candidates: list[Path] = []
    for child in sorted(destination.iterdir()):
        if child.name == ".stream-archiver-staging" or child.is_symlink() or not child.is_dir():
            continue
        manifest_path = child / MANIFEST_NAME
        archive_shaped = _ARCHIVE_NAME_PATTERN.fullmatch(child.name) is not None
        if not manifest_path.is_file():
            if archive_shaped:
                raise RecoveryError(f"archive-shaped directory is missing {MANIFEST_NAME}: {child}")
            continue
        if owners is not None:
            import json

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RecoveryError(f"cannot read archive manifest {manifest_path}: {exc}") from exc
            owner = (manifest.get("policy_name"), manifest.get("source_root"))
            if owner not in owners:
                continue
        candidates.append(child)
    for index, child in enumerate(candidates, start=1):
        log_event(
            LOGGER,
            logging.INFO,
            "archive_verification_started",
            "verifying committed archive",
            destination=destination,
            archive=child,
            archive_progress=f"{index}/{len(candidates)}",
        )
        results.append(_verify_archive_unlocked(child))
    log_event(
        LOGGER,
        logging.INFO,
        "destination_verification_completed",
        "destination verification completed",
        destination=destination,
        archives=len(results),
    )
    return tuple(results)
