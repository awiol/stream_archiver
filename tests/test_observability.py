"""Behavioral tests for structured logs and progress reporting."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stream_archiver.config import Policy, SymlinkRule
from stream_archiver.observability import configure_logging, log_event
from stream_archiver.service import run_policy
from tests.helpers import write_at

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


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
    destination.mkdir()
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
    progress = next(event for event in events if event["event"] == "archive_action_progress")
    assert progress["action_progress"] == "1/1"
    assert progress["file_bytes"].endswith(f"/{3 * 1024 * 1024}")
    assert progress["staging_bytes"].endswith(f"/{3 * 1024 * 1024}")
    assert progress["staging_percent"] < 100
    completed = next(event for event in events if event["event"] == "archive_execution_completed")
    assert completed["transaction_percent"] == 100
    assert 0 < progress["staging_percent"] < 100
    staged = next(event for event in events if event["event"] == "archive_action_completed")
    assert staged["percent"] == 100
    assert staged["staging_percent"] == 100


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


def test_cli_json_events_share_one_run_id(tmp_path: Path, capsys: object) -> None:
    """R-LOG-002: one CLI invocation can be correlated across all emitted events."""

    from stream_archiver.cli import main

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    config = tmp_path / "policies.toml"
    config.write_text(
        (
            "schema_version = 2\n"
            "[[policies]]\n"
            'name = "reports"\n'
            f'sources = ["{source}"]\n'
            f'destination = "{destination}"\n'
            'minimum_age = "30d"\n'
            'stream_gap = "8h"\n'
            'symlink_rule = "ignore"\n'
        ),
        encoding="utf-8",
    )

    assert main(["--config", str(config), "--log-format", "json", "check"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    events = [json.loads(line) for line in captured.err.splitlines()]
    run_ids = {event["run_id"] for event in events}

    assert len(run_ids) == 1
    assert len(next(iter(run_ids))) == 32


def test_text_logging_quotes_paths_and_other_string_fields() -> None:
    """Whitespace in filesystem paths cannot visually merge with adjacent fields."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="text", stream=output)

    import logging

    logger = logging.getLogger("stream_archiver.test")
    log_event(
        logger,
        logging.INFO,
        "path_example",
        "testing path rendering",
        source=Path("/tmp/source with spaces/report.json"),
        operation="plan",
    )

    rendered = output.getvalue()
    assert 'source="/tmp/source with spaces/report.json"' in rendered
    assert 'operation="plan"' in rendered


def test_missing_source_log_has_structured_path_and_reason(tmp_path: Path, capsys: object) -> None:
    """A missing source is diagnosed separately from generic non-directory failure."""

    from stream_archiver.cli import main

    source = tmp_path / "missing source"
    destination = tmp_path / "archive"
    config = tmp_path / "policies.toml"
    config.write_text(
        (
            "schema_version = 2\n"
            "[[policies]]\n"
            'name = "reports"\n'
            f'sources = ["{source}"]\n'
            f'destination = "{destination}"\n'
            'minimum_age = "30d"\n'
            'stream_gap = "8h"\n'
            'symlink_rule = "ignore"\n'
        ),
        encoding="utf-8",
    )

    status = main(["--config", str(config), "--log-format", "json", "plan"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    events = [json.loads(line) for line in captured.err.splitlines()]

    assert status == 2
    unavailable = next(event for event in events if event["event"] == "source_unavailable")
    assert unavailable["source"] == str(source)
    assert unavailable["reason"] == "missing"
    assert events[-1]["detail"].startswith("source directory does not exist:")
