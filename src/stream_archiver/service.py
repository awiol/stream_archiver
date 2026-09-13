"""Public orchestration for planning, running, and verifying archival policies."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stream_archiver.config import Policy
from stream_archiver.discovery import discover_entries
from stream_archiver.errors import RecoveryError
from stream_archiver.locking import resource_locks
from stream_archiver.executor import (
    MANIFEST_NAME,
    ArchiveExecutionResult,
    ArchiveVerificationResult,
    execute_plan,
    recover_pending_archives,
    verify_archive,
)
from stream_archiver.model import ArchivePlan
from stream_archiver.observability import log_event
from stream_archiver.planning import (
    boundary_entries_for_policy,
    build_archive_plan,
    select_eligible_streams,
    split_streams,
)

LOGGER = logging.getLogger(__name__)
_ARCHIVE_NAME_PATTERN = re.compile(
    r"^\d{8}T\d{6}\.\d{6}Z--\d{8}T\d{6}\.\d{6}Z--[0-9a-f]{10}$"
)


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
        entries = discover_entries(source)
        boundary_entries = boundary_entries_for_policy(policy, entries)
        streams = split_streams(boundary_entries, minimum_gap=policy.stream_gap)
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
    result = PolicyPlan(policy_name=policy.name, source_plans=tuple(source_plans))
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


def run_policy(
    policy: Policy,
    *,
    now: datetime,
    resources_locked: bool = False,
    event_clock: Callable[[], datetime] | None = None,
) -> PolicyRunResult:
    """Recover pending cleanup, then execute every source-local eligible stream.

    Direct library callers receive the same cooperative resource locking as the
    CLI. ``resources_locked`` is for the CLI scheduler, which already owns a
    superset lock while it updates due state.
    """

    if not resources_locked:
        with resource_locks((*policy.sources, policy.destination), exclusive=True):
            return run_policy(
                policy,
                now=now,
                resources_locked=True,
                event_clock=event_clock,
            )

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
    plan = plan_policy(policy, now=now)
    archives_list: list[ArchiveExecutionResult] = []
    for index, item in enumerate(plan.archive_plans, start=1):
        log_event(
            LOGGER,
            logging.INFO,
            "policy_archive_started",
            "executing planned archive",
            policy=policy.name,
            archive=item.archive_name,
            archive_progress=f"{index}/{len(plan.archive_plans)}",
        )
        archives_list.append(execute_plan(item, event_clock=event_clock))
    archives = tuple(archives_list)
    log_event(
        LOGGER,
        logging.INFO,
        "policy_run_completed",
        "policy execution completed",
        policy=policy.name,
        recovered_archives=len(recovered),
        archives=len(archives),
    )
    return PolicyRunResult(
        policy_name=policy.name,
        recovered_archives=recovered,
        plan=plan,
        archives=archives,
    )


def verify_policies(
    policies: tuple[Policy, ...],
    *,
    all_in_destination: bool = False,
    resources_locked: bool = False,
) -> tuple[ArchiveVerificationResult, ...]:
    """Verify archives for selected policy ownership or audit selected destinations."""

    if not resources_locked:
        resources = {policy.destination for policy in policies}
        with resource_locks(resources, exclusive=False):
            return verify_policies(
                policies,
                all_in_destination=all_in_destination,
                resources_locked=True,
            )

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
        results.extend(verify_destination(destination, owners=owners))
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
    ownership cannot be established safely.
    """

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
        results.append(verify_archive(child))
    log_event(
        LOGGER,
        logging.INFO,
        "destination_verification_completed",
        "destination verification completed",
        destination=destination,
        archives=len(results),
    )
    return tuple(results)
