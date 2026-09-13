"""CLI tests cover the installed command's scheduled-run contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stream_archiver.cli import main
from tests.helpers import write_at

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_run_if_due_archives_once_then_reports_not_due(tmp_path: Path, capsys: object) -> None:
    """Frequent invocations perform the first due run and suppress the next early run."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    state = tmp_path / "state.json"
    lock = tmp_path / "run.lock"
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)

    first_status = main(
        [
            "--config",
            str(config),
            "run-if-due",
            "--state",
            str(state),
            "--lock-file",
            str(lock),
        ]
    )
    first_output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    second_status = main(
        [
            "--config",
            str(config),
            "run-if-due",
            "--state",
            str(state),
            "--lock-file",
            str(lock),
        ]
    )
    second_output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert first_status == 0
    assert first_output[0]["status"] == "completed"
    assert second_status == 0
    assert second_output == [{"policy": "reports", "status": "not-due"}]


def _write_config(tmp_path: Path, source: Path, destination: Path) -> Path:
    path = tmp_path / "policies.toml"
    path.write_text(
        f"""
schema_version = 2
run_interval = "30d"

[[policies]]
name = "reports"
sources = ["{source}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "drop-aliases-preserve-relative"

[[policies.compression_rules]]
suffixes = [".json", ".html"]
compression = "gzip"
""",
        encoding="utf-8",
    )
    return path


def test_verify_command_reports_hash_evidence(tmp_path: Path, capsys: object) -> None:
    """Operators can recheck committed archives through the installed CLI surface."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)
    assert main(["--config", str(config), "run"]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]

    status = main(["--config", str(config), "verify"])
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert status == 0
    assert len(output) == 1
    assert output[0]["payload_files"] == 1
    assert len(output[0]["manifest_sha256"]) == 64
    assert output[0]["success_evidence"].endswith("SUCCESS.json")


def test_config_can_be_selected_from_environment(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    """Routine CLI use can avoid repeating a full policy-file path."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)
    monkeypatch.setenv("STREAM_ARCHIVER_CONFIG", str(config))  # type: ignore[attr-defined]

    status = main(["check"])
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert status == 0
    assert output["policies"][0]["name"] == "reports"


def test_explicit_config_overrides_environment(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    """An explicit policy file always wins over the convenience environment setting."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "STREAM_ARCHIVER_CONFIG", str(tmp_path / "missing.toml")
    )

    status = main(["--config", str(config), "check"])
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert status == 0
    assert output["policies"][0]["name"] == "reports"


def test_legacy_lock_file_is_ignored_in_favor_of_resource_locks(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    """The 0.3 lock-file option cannot redefine the 0.4 safety lock domain."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    legacy_lock = tmp_path / "legacy.lock"
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)

    status = main(["--config", str(config), "run", "--lock-file", str(legacy_lock)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]

    assert status == 0
    assert not legacy_lock.exists()
    assert "legacy_lock_file_ignored" in captured.err


def test_plan_at_is_read_only_and_destructive_commands_reject_global_now(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Artificial time is available only on the read-only planning surface."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    config = _write_config(tmp_path, source, destination)

    assert main(["--config", str(config), "plan", "--at", NOW.isoformat()]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]

    import pytest

    with pytest.raises(SystemExit):
        main(["--config", str(config), "--now", NOW.isoformat(), "run"])


def test_run_if_due_state_parent_is_in_resource_lock_domain(
    tmp_path: Path,
    capsys: object,
) -> None:
    """R-CONC/SCHED: due-state read/modify/write participates in serialization.

    A competing invocation that otherwise owns disjoint source/destination
    resources must still fail to acquire its destructive lock set while the
    shared scheduling-state directory is exclusively owned.
    """

    from stream_archiver.locking import resource_locks

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    state = tmp_path / "state" / "due.json"
    source.mkdir()
    destination.mkdir()
    state.parent.mkdir()
    config = _write_config(tmp_path, source, destination)

    with resource_locks((state.parent,), exclusive=True):
        status = main(
            [
                "--config",
                str(config),
                "run-if-due",
                "--state",
                str(state),
            ]
        )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert status == 2
    assert "conflicting exclusive resource lock" in captured.err
