"""Command-line interface for validation, planning, execution, and deployment."""

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
from stream_archiver.observability import configure_logging, log_event
from stream_archiver.service import (
    PolicyPlan,
    PolicyRunResult,
    plan_policy,
    run_policy,
    verify_destination,
)
from stream_archiver.state import load_state, save_state
from stream_archiver.systemd import render_systemd_bundle, resolve_current_executable

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return a process exit status."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    log_level = "DEBUG" if arguments.verbose else arguments.log_level
    configure_logging(level=log_level, format_name=arguments.log_format)
    log_event(
        LOGGER,
        logging.DEBUG,
        "cli_started",
        "command-line invocation started",
        command=arguments.command,
        config=arguments.config,
        selected_policies=arguments.policy,
    )

    try:
        config = load_config(arguments.config)
        policies = _select_policies(config, arguments.policy)
        now = _parse_now(arguments.now)
        log_event(
            LOGGER,
            logging.INFO,
            "configuration_ready",
            "configuration loaded and policy selection completed",
            config=arguments.config,
            policy_count=len(policies),
            policies=[policy.name for policy in policies],
        )

        if arguments.command == "check":
            _print_json(_config_summary(config))
            log_event(
                LOGGER,
                logging.INFO,
                "configuration_valid",
                "configuration validation completed",
                policy_count=len(config.policies),
            )
            return 0
        if arguments.command == "plan":
            plans = [plan_policy(policy, now=now) for policy in policies]
            _print_json([_plan_summary(plan) for plan in plans])
            log_event(
                LOGGER,
                logging.INFO,
                "planning_completed",
                "read-only planning completed",
                policy_count=len(plans),
                eligible_streams=sum(plan.eligible_streams for plan in plans),
                planned_archives=sum(len(plan.archive_plans) for plan in plans),
            )
            return 0
        if arguments.command == "verify":
            destinations = sorted({policy.destination for policy in policies})
            results = [
                result
                for destination in destinations
                for result in verify_destination(destination)
            ]
            _print_json([_verification_summary(result) for result in results])
            log_event(
                LOGGER,
                logging.INFO,
                "verification_completed",
                "destination verification completed",
                destination_count=len(destinations),
                archive_count=len(results),
            )
            return 0
        if arguments.command == "render-systemd":
            result = render_systemd_bundle(
                config,
                config_path=arguments.config,
                executable=resolve_current_executable(arguments.executable),
                output_directory=arguments.output_directory,
                service_name=arguments.service_name,
                service_user=arguments.service_user,
                service_group=arguments.service_group,
                on_calendar=arguments.on_calendar,
                randomized_delay=arguments.randomized_delay,
                accuracy=arguments.accuracy,
                log_level=arguments.service_log_level,
                log_format=arguments.service_log_format,
                force=arguments.force,
            )
            _print_json(
                {
                    "service": str(result.service_path),
                    "timer": str(result.timer_path),
                    "instructions": str(result.instructions_path),
                }
            )
            log_event(
                LOGGER,
                logging.INFO,
                "systemd_bundle_rendered",
                "deployment-specific systemd bundle generated",
                output_directory=arguments.output_directory,
                service=result.service_path,
                timer=result.timer_path,
            )
            return 0

        lock_file = arguments.lock_file or _default_lock_file(arguments)
        with execution_lock(lock_file):
            if arguments.command == "run":
                results = [run_policy(policy, now=now) for policy in policies]
                _print_json([_run_summary(result) for result in results])
                log_event(
                    LOGGER,
                    logging.INFO,
                    "run_completed",
                    "immediate policy run completed",
                    policy_count=len(results),
                    archive_count=sum(len(result.archives) for result in results),
                )
                return 0
            if arguments.command == "run-if-due":
                return _run_if_due(config, policies, arguments.state, now)
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            "interrupted",
            "operation interrupted by the user",
            action="rerun the same command; committed archives are recovered safely",
        )
        return 130
    except StreamArchiverError as exc:
        log_event(
            LOGGER,
            logging.ERROR,
            "operation_failed",
            str(exc),
            command=arguments.command,
            action="inspect preceding logs, correct the reported condition, and rerun",
        )
        return 2
    except Exception:
        LOGGER.exception(
            "unexpected internal failure; preserve the traceback and report the defect",
            extra={"event": "unexpected_failure"},
        )
        return 1

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
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
        help="minimum operational log level written to stderr",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="operational log format; command results remain JSON on stdout",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="deprecated alias for --log-level DEBUG",
    )

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

    systemd = subparsers.add_parser(
        "render-systemd",
        help="generate deployment-specific service, timer, and installation instructions",
    )
    systemd.add_argument("--output-directory", type=Path, required=True)
    systemd.add_argument(
        "--executable",
        type=Path,
        help="absolute installed stream-archiver executable; auto-detected when omitted",
    )
    systemd.add_argument("--service-name", default="stream-archiver")
    systemd.add_argument("--service-user", default="stream-archiver")
    systemd.add_argument("--service-group", default="stream-archiver")
    systemd.add_argument("--on-calendar", default="daily")
    systemd.add_argument("--randomized-delay", default="6h")
    systemd.add_argument("--accuracy", default="1h")
    systemd.add_argument(
        "--service-log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
    )
    systemd.add_argument(
        "--service-log-format",
        choices=("text", "json"),
        default="text",
    )
    systemd.add_argument("--force", action="store_true")
    return parser


def _run_if_due(
    config: AppConfig,
    policies: tuple[Policy, ...],
    state_path: Path,
    now: datetime,
) -> int:
    state = load_state(state_path)
    summaries: list[dict[str, object]] = []
    for index, policy in enumerate(policies, start=1):
        due = state.is_due(policy.name, now=now, interval=config.run_interval)
        log_event(
            LOGGER,
            logging.INFO,
            "due_policy_evaluated",
            "scheduled policy due state evaluated",
            policy=policy.name,
            policy_progress=f"{index}/{len(policies)}",
            due=due,
        )
        if not due:
            summaries.append({"policy": policy.name, "status": "not-due"})
            continue
        result = run_policy(policy, now=now)
        state = state.with_success(policy.name, when=now)
        save_state(state_path, state)
        summary = _run_summary(result)
        summary["status"] = "completed"
        summaries.append(summary)
        log_event(
            LOGGER,
            logging.INFO,
            "due_policy_completed",
            "scheduled policy completed and due state was persisted",
            policy=policy.name,
            archives=len(result.archives),
            state=state_path,
        )
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


def _verification_summary(result: object) -> dict[str, object]:
    return {
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
