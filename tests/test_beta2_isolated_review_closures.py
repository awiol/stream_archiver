"""Regressions that close the isolated 0.5.0b1 review findings.

These tests bind the three review findings to their accepted contracts rather than
hard-coding the beta.2 candidate version.  They remain useful after later release
advancement because their oracles are policy ownership, logical-stream accounting,
and the declared supported-Python range.
"""

from __future__ import annotations

import io
import json
import subprocess
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_archiver.capacity import CapacitySnapshot
from stream_archiver.config import Policy, SymlinkRule
from stream_archiver.errors import RecoveryError
from stream_archiver.observability import configure_logging
from stream_archiver.service import _run_policy_at, verify_policies
from tests.helpers import write_at

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
_PROJECT_ROOT = Path(__file__).parents[1]


def _policy(
    source: Path, destination: Path, *, name: str = "reports", max_files: int | None = None
) -> Policy:
    """Return one deterministic policy for verification and capacity regressions."""

    return Policy(
        name=name,
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
        archive_name_template="{start:%Y%m%dT%H%M%SZ}--{end:%Y%m%dT%H%M%SZ}",
        archive_unit_max_regular_files=max_files,
    )


def _archive_shaped_directory(destination: Path) -> Path:
    """Create a legacy-name archive candidate accepted by verification discovery."""

    candidate = destination / "20260101T000000.000000Z--20260101T010000.000000Z--0123456789"
    candidate.mkdir()
    return candidate


@pytest.mark.parametrize(
    "manifest_text",
    (
        "{}",
        '{"source_root": "$SOURCE"}',
        '{"policy_name": "reports"}',
        '{"policy_name": 7, "source_root": "$SOURCE"}',
        '{"policy_name": "reports", "source_root": "relative/path"}',
        "{not-json",
    ),
    ids=(
        "empty",
        "missing-policy",
        "missing-source",
        "wrong-types",
        "relative-source",
        "malformed-json",
    ),
)
def test_policy_scoped_verify_rejects_malformed_ownership_evidence(
    tmp_path: Path,
    manifest_text: str,
) -> None:
    """R-VERIFY-005: ambiguous ownership is corruption, not a foreign-owner skip."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    candidate = _archive_shaped_directory(destination)
    (candidate / "MANIFEST.json").write_text(
        manifest_text.replace("$SOURCE", str(source)),
        encoding="utf-8",
    )

    with pytest.raises(RecoveryError):
        verify_policies((_policy(source, destination),))


def test_policy_scoped_verify_rejects_symlinked_manifest(tmp_path: Path) -> None:
    """R-VERIFY-005: symlinked ownership evidence cannot establish archive ownership."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    candidate = _archive_shaped_directory(destination)
    target = tmp_path / "manifest-target.json"
    target.write_text(
        json.dumps({"policy_name": "reports", "source_root": str(source)}),
        encoding="utf-8",
    )
    (candidate / "MANIFEST.json").symlink_to(target)

    with pytest.raises(RecoveryError, match="real regular file"):
        verify_policies((_policy(source, destination),))


def test_policy_scoped_verify_skips_valid_foreign_owner_before_payload_verification(
    tmp_path: Path,
) -> None:
    """R-VERIFY-001/005: valid foreign ownership still prevents payload verification."""

    destination = tmp_path / "archive"
    destination.mkdir()
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    old = NOW - timedelta(days=40)
    write_at(first_source / "first.bin", b"first", old)
    write_at(second_source / "second.bin", b"second", old)
    first = _policy(first_source, destination, name="first")
    second = _policy(second_source, destination, name="second")
    first_result = _run_policy_at(first, planning_time=NOW)
    second_result = _run_policy_at(second, planning_time=NOW)
    foreign_payload = second_result.archives[0].archive_directory / "second.bin"
    foreign_payload.write_bytes(b"corrupt-foreign-payload")

    results = verify_policies((first,))

    assert [item.archive_directory for item in results] == [
        first_result.archives[0].archive_directory
    ]


def test_policy_scoped_verify_fully_verifies_selected_owner(tmp_path: Path) -> None:
    """R-VERIFY-001: selected-owner archives are not accepted from ownership alone."""

    destination = tmp_path / "archive"
    destination.mkdir()
    source = tmp_path / "source"
    write_at(source / "data.bin", b"payload", NOW - timedelta(days=40))
    policy = _policy(source, destination)
    result = _run_policy_at(policy, planning_time=NOW)
    (result.archives[0].archive_directory / "data.bin").write_bytes(b"corrupt")

    with pytest.raises(RecoveryError):
        verify_policies((policy,))


def test_partitioned_capacity_events_report_logical_stream_bytes(tmp_path: Path) -> None:
    """R-CAP-003: every unit precheck retains the logical stream byte denominator."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "a.bin", b"a" * 100, old)
    write_at(source / "b.bin", b"b" * 100, old + timedelta(seconds=1))
    output = io.StringIO()
    configure_logging(level="DEBUG", format_name="json", stream=output)

    def capacity(path: Path) -> CapacitySnapshot:
        return CapacitySnapshot(path, total_bytes=20_000, free_bytes=10_000, observed_at=NOW)

    _run_policy_at(
        _policy(source, destination, max_files=1),
        planning_time=NOW,
        capacity_observer=capacity,
    )

    events = [
        json.loads(line)
        for line in output.getvalue().splitlines()
        if json.loads(line)["event"] == "destination_capacity_checked"
    ]
    assert [event["current_stream_source_bytes"] for event in events] == [200, 200]
    assert [event["remaining_source_bytes"] for event in events] == [200, 100]


def test_supported_python_range_is_consistent_across_release_surfaces() -> None:
    """R-PY-001: metadata, docs, classifiers, and installer encode Python 3.11-3.13."""

    project = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    classifiers = set(project["classifiers"])
    readme = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    verification = (_PROJECT_ROOT / "docs" / "verification.md").read_text(encoding="utf-8")
    installer = (_PROJECT_ROOT / "tools" / "install-systemd.sh").read_text(encoding="utf-8")

    assert project["requires-python"] == ">=3.11,<3.14"
    assert {
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    } <= classifiers
    assert "Programming Language :: Python :: 3.14" not in classifiers
    assert "Python 3.11–3.13" in readme
    assert "Python 3.11, 3.12, and 3.13" in verification
    assert "3.11|3.11.*|3.12|3.12.*|3.13|3.13.*" in installer


def test_installer_accepts_patch_selector_within_supported_minor(tmp_path: Path) -> None:
    """R-PY-001: a patch selector inside a supported minor passes the support gate."""

    config = tmp_path / "policies.toml"
    config.write_text("placeholder\n", encoding="utf-8")
    completed = subprocess.run(
        [
            "bash",
            str(_PROJECT_ROOT / "tools" / "install-systemd.sh"),
            "--config",
            str(config),
            "--python-version",
            "3.13.7",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert "supports Python 3.11, 3.12, and 3.13" not in completed.stderr


def test_installer_rejects_unsupported_python_before_mutation(tmp_path: Path) -> None:
    """R-PY-001: an unsupported interpreter selector is rejected before root/system changes."""

    config = tmp_path / "policies.toml"
    config.write_text("placeholder\n", encoding="utf-8")
    completed = subprocess.run(
        [
            "bash",
            str(_PROJECT_ROOT / "tools" / "install-systemd.sh"),
            "--config",
            str(config),
            "--python-version",
            "3.14",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "supports Python 3.11, 3.12, and 3.13" in completed.stderr
