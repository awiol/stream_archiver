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
    config = _write_config(tmp_path, source, destination)

    first_status = main(
        [
            "--config",
            str(config),
            "--now",
            NOW.isoformat(),
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
            "--now",
            (NOW + timedelta(days=1)).isoformat(),
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
    config = _write_config(tmp_path, source, destination)
    assert main(["--config", str(config), "--now", NOW.isoformat(), "run"]) == 0
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


def test_manual_run_uses_xdg_state_directory_for_default_lock(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    """A default system config does not imply an unwritable lock beside that file."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    state_home = tmp_path / "state home"
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    config = _write_config(tmp_path, source, destination)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))  # type: ignore[attr-defined]

    status = main(["--config", str(config), "--now", NOW.isoformat(), "run"])
    capsys.readouterr()  # type: ignore[attr-defined]

    assert status == 0
    assert (state_home / "stream-archiver" / "execution.lock").is_file()
