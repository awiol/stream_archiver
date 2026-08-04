"""Public orchestration for planning, running, and verifying archival policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stream_archiver.config import Policy
from stream_archiver.discovery import discover_entries
from stream_archiver.executor import (
    MANIFEST_NAME,
    ArchiveExecutionResult,
    ArchiveVerificationResult,
    execute_plan,
    recover_pending_archives,
    verify_archive,
)
from stream_archiver.model import ArchivePlan
from stream_archiver.planning import (
    build_archive_plan,
    select_eligible_streams,
    split_streams,
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
            plan
            for source_plan in self.source_plans
            for plan in source_plan.archive_plans
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

    source_plans: list[SourcePlan] = []
    for source in policy.sources:
        entries = discover_entries(source)
        streams = split_streams(entries, minimum_gap=policy.stream_gap)
        eligible = select_eligible_streams(
            streams,
            now=now,
            minimum_age=policy.minimum_age,
        )
        plans = tuple(
            plan
            for stream in eligible
            if (plan := build_archive_plan(policy, source, stream)) is not None
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
    return PolicyPlan(policy_name=policy.name, source_plans=tuple(source_plans))


def run_policy(policy: Policy, *, now: datetime) -> PolicyRunResult:
    """Recover pending cleanup, then execute every source-local eligible stream."""

    recovered = tuple(
        archive
        for source in policy.sources
        for archive in recover_pending_archives(policy.destination, source, now=now)
    )
    plan = plan_policy(policy, now=now)
    archives = tuple(execute_plan(item, now=now) for item in plan.archive_plans)
    return PolicyRunResult(
        policy_name=policy.name,
        recovered_archives=recovered,
        plan=plan,
        archives=archives,
    )


def verify_destination(destination: Path) -> tuple[ArchiveVerificationResult, ...]:
    """Verify every archive directory under one destination root.

    This function intentionally operates at destination scope because several
    policies may share that root. Directories without a manifest and the
    internal staging directory are ignored.
    """

    if not destination.exists():
        return ()
    results: list[ArchiveVerificationResult] = []
    for child in sorted(destination.iterdir()):
        if (
            child.name == ".stream-archiver-staging"
            or child.is_symlink()
            or not child.is_dir()
        ):
            continue
        if not (child / MANIFEST_NAME).is_file():
            continue
        results.append(verify_archive(child))
    return tuple(results)
