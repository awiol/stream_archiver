"""Command-line interface for validation, planning, execution, and verification."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from stream_archiver.config import AppConfig, Policy, load_config
from stream_archiver.errors import ConfigurationError, StreamArchiverError
from stream_archiver.locking import execution_lock
from stream_archiver.service import (
    PolicyPlan,
    PolicyRunResult,
    plan_policy,
    run_policy,
    verify_destination,
)
from stream_archiver.state import load_state, save_state

LOGGER = logging.getLogger("stream_archiver")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit status."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(arguments.config)
        policies = _select_policies(config, arguments.policy)
        now = _parse_now(arguments.now)

        if arguments.command == "check":
            _print_json(_config_summary(config))
            return 0
        if arguments.command == "plan":
            _print_json(
                [_plan_summary(plan_policy(policy, now=now)) for policy in policies]
            )
            return 0
        if arguments.command == "verify":
            destinations = sorted({policy.destination for policy in policies})
            results = [
                result
                for destination in destinations
                for result in verify_destination(destination)
            ]
            _print_json(
                [
                    {
                        "directory": str(result.archive_directory),
                        "payload_files": result.payload_files,
                        "preserved_symlinks": result.preserved_symlinks,
                        "manifest_sha256": result.manifest_sha256,
                        "checksums_sha256": result.checksums_sha256,
                        "success_evidence": (
                            str(result.success_evidence)
                            if result.success_evidence is not None
                            else None
                        ),
                    }
                    for result in results
                ]
            )
            return 0

        lock_file = arguments.lock_file or _default_lock_file(arguments)
        with execution_lock(lock_file):
            if arguments.command == "run":
                results = [run_policy(policy, now=now) for policy in policies]
                _print_json([_run_summary(result) for result in results])
                return 0
            if arguments.command == "run-if-due":
                return _run_if_due(config, policies, arguments.state, now)
    except StreamArchiverError as exc:
        LOGGER.error("%s", exc)
        return 2

    parser.error("unsupported command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stream-archiver",
        description="Safely move complete old filesystem streams using declarative policies.",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="TOML configuration path"
    )
    parser.add_argument(
        "--policy",
        action="append",
        default=[],
        help="run only this policy name; may be repeated",
    )
    parser.add_argument(
        "--now",
        help="explicit ISO-8601 time for reproducible planning; defaults to current UTC",
    )
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate configuration")
    subparsers.add_parser("plan", help="show eligible streams without modifying files")
    subparsers.add_parser(
        "verify", help="recompute archive hashes and success evidence"
    )

    run = subparsers.add_parser("run", help="run selected policies immediately")
    run.add_argument("--lock-file", type=Path)

    due = subparsers.add_parser(
        "run-if-due",
        help="run policies whose configured interval has elapsed",
    )
    due.add_argument("--state", type=Path, required=True)
    due.add_argument("--lock-file", type=Path)
    return parser


def _run_if_due(
    config: AppConfig,
    policies: tuple[Policy, ...],
    state_path: Path,
    now: datetime,
) -> int:
    state = load_state(state_path)
    summaries: list[dict[str, object]] = []
    for policy in policies:
        if not state.is_due(policy.name, now=now, interval=config.run_interval):
            summaries.append({"policy": policy.name, "status": "not-due"})
            continue
        result = run_policy(policy, now=now)
        state = state.with_success(policy.name, when=now)
        save_state(state_path, state)
        summary = _run_summary(result)
        summary["status"] = "completed"
        summaries.append(summary)
    _print_json(summaries)
    return 0


def _select_policies(config: AppConfig, selected: list[str]) -> tuple[Policy, ...]:
    if not selected:
        return config.policies
    requested = set(selected)
    policies = tuple(policy for policy in config.policies if policy.name in requested)
    missing = sorted(requested - {policy.name for policy in policies})
    if missing:
        raise ConfigurationError(f"unknown policy names: {', '.join(missing)}")
    return policies


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError("--now must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConfigurationError("--now must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _default_lock_file(arguments: argparse.Namespace) -> Path:
    if arguments.command == "run-if-due":
        return arguments.state.with_name(arguments.state.name + ".lock")
    return arguments.config.with_name(arguments.config.name + ".lock")


def _config_summary(config: AppConfig) -> dict[str, object]:
    return {
        "run_interval_seconds": int(config.run_interval.total_seconds()),
        "policies": [
            {
                "name": policy.name,
                "sources": [str(source) for source in policy.sources],
                "destination": str(policy.destination),
                "minimum_age_seconds": int(policy.minimum_age.total_seconds()),
                "stream_gap_seconds": int(policy.stream_gap.total_seconds()),
                "symlink_rule": policy.symlink_rule.value,
                "compression_rules": [
                    {
                        "suffixes": list(rule.suffixes),
                        "compression": rule.compression.value,
                        "compresslevel": 9,
                    }
                    for rule in policy.compression_rules
                ],
            }
            for policy in config.policies
        ],
    }


def _plan_summary(plan: PolicyPlan) -> dict[str, object]:
    return {
        "policy": plan.policy_name,
        "discovered_entries": plan.discovered_entries,
        "streams": plan.streams,
        "eligible_streams": plan.eligible_streams,
        "sources": [
            {
                "source": str(source_plan.source_root),
                "discovered_entries": source_plan.discovered_entries,
                "streams": source_plan.streams,
                "eligible_streams": source_plan.eligible_streams,
                "archives": [
                    {
                        "archive_name": item.archive_name,
                        "plan_id": item.plan_id,
                        "oldest_mtime_ns": item.stream.oldest_mtime_ns,
                        "newest_mtime_ns": item.stream.newest_mtime_ns,
                        "actions": [
                            {
                                "source": action.source.relative_path.as_posix(),
                                "action": action.kind.value,
                                "archive_path": (
                                    action.archive_path.as_posix()
                                    if action.archive_path is not None
                                    else None
                                ),
                            }
                            for action in item.actions
                        ],
                    }
                    for item in source_plan.archive_plans
                ],
            }
            for source_plan in plan.source_plans
        ],
    }


def _run_summary(result: PolicyRunResult) -> dict[str, object]:
    return {
        "policy": result.policy_name,
        "recovered_archives": [str(path) for path in result.recovered_archives],
        "planned_archives": len(result.plan.archive_plans),
        "archives": [
            {
                "directory": str(item.archive_directory),
                "moved_files": item.moved_files,
                "uncompressed_files": item.uncompressed_files,
                "gzip_files": item.gzip_files,
                "bz2_files": item.bz2_files,
                "compressed_files": item.compressed_files,
                "preserved_symlinks": item.preserved_symlinks,
                "dropped_alias_symlinks": item.dropped_alias_symlinks,
                "skipped_symlinks": item.skipped_symlinks,
                "success_evidence": (
                    str(item.success_evidence)
                    if item.success_evidence is not None
                    else None
                ),
            }
            for item in result.archives
        ],
    }


def _print_json(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
