"""Behavioral tests for structured logs and progress reporting."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stream_archiver.config import Policy, SymlinkRule
from stream_archiver.observability import configure_logging, log_event
from stream_archiver.service import run_policy
from tests.helpers import write_at

NOW = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


def test_json_logging_preserves_native_event_fields() -> None:
    """JSON logging emits one parseable event with structured scalar fields."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="json", stream=output)

    import logging

    logger = logging.getLogger("stream_archiver.test")
    log_event(logger, logging.INFO, "example", "completed", count=3, ok=True)

    payload = json.loads(output.getvalue())
    assert payload["event"] == "example"
    assert payload["message"] == "completed"
    assert payload["count"] == 3
    assert payload["ok"] is True


def test_policy_run_logs_action_and_byte_progress(tmp_path: Path) -> None:
    """A multi-chunk file produces actionable action and byte progress events."""

    output = io.StringIO()
    configure_logging(level="DEBUG", format_name="json", stream=output)
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "large.bin", b"x" * (3 * 1024 * 1024), NOW - timedelta(days=40))
    policy = Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
    )

    result = run_policy(policy, now=NOW)

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    names = [event["event"] for event in events]
    assert result.archives[0].moved_files == 1
    assert "archive_action_started" in names
    assert "archive_action_progress" in names
    assert "archive_action_progress_debug" in names
    assert "source_cleanup_progress" in names
    assert "archive_execution_completed" in names
    progress = next(
        event for event in events if event["event"] == "archive_action_progress"
    )
    assert progress["action_progress"] == "1/1"
    assert progress["file_bytes"].endswith(f"/{3 * 1024 * 1024}")
    assert progress["overall_bytes"].endswith(f"/{3 * 1024 * 1024}")
    assert 0 < progress["overall_percent"] < 100
    completed = next(
        event for event in events if event["event"] == "archive_action_completed"
    )
    assert completed["percent"] == 100
    assert completed["overall_percent"] == 100


def test_cli_failure_log_is_actionable_and_stdout_remains_empty(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Expected CLI failures emit structured corrective context only on stderr."""

    from stream_archiver.cli import main

    missing = tmp_path / "missing.toml"
    status = main(
        [
            "--config",
            str(missing),
            "--log-format",
            "json",
            "check",
        ]
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    events = [json.loads(line) for line in captured.err.splitlines()]
    assert status == 2
    assert captured.out == ""
    assert events[-1]["event"] == "operation_failed"
    assert "action" in events[-1]
