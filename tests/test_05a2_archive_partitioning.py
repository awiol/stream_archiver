"""0.5.0a2 tests protect deterministic archive-unit subdivision and recovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_archiver import service
from stream_archiver.config import Policy, SymlinkRule, load_config
from stream_archiver.errors import ConfigurationError, ExecutionError, RecoveryError
from stream_archiver.executor import MANIFEST_NAME, SUCCESS_NAME
from stream_archiver.model import ActionKind
from stream_archiver.reporting import build_run_report
from stream_archiver.service import _run_policy_at, plan_policy
from tests.helpers import symlink_at, write_at

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def test_schema5_accepts_partition_limits_and_schema4_rejects_them(tmp_path: Path) -> None:
    """Archive-unit limits enter configuration through an explicit schema boundary."""

    current = _write_config(tmp_path / "current", schema=5, max_bytes=100, max_files=2)
    policy = load_config(current).policies[0]

    assert policy.archive_unit_max_source_bytes == 100
    assert policy.archive_unit_max_regular_files == 2

    legacy = _write_config(tmp_path / "legacy", schema=4, max_bytes=100)
    with pytest.raises(ConfigurationError, match="unknown keys: archive_unit_max_source_bytes"):
        load_config(legacy)


def test_partitioning_applies_byte_and_file_limits_in_deterministic_order(tmp_path: Path) -> None:
    """Greedy subdivision starts a new unit before the first limit would be exceeded."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "a.bin", b"a" * 4, old)
    write_at(source / "b.bin", b"b" * 4, old + timedelta(seconds=1))
    write_at(source / "c.bin", b"c" * 4, old + timedelta(seconds=2))
    write_at(source / "d.bin", b"d" * 1, old + timedelta(seconds=3))

    policy = _policy(source, destination, max_bytes=8, max_files=2)
    first = plan_policy(policy, now=NOW).archive_plans
    second = plan_policy(policy, now=NOW).archive_plans

    assert [
        [a.source.relative_path.name for a in p.actions if a.kind is ActionKind.MOVE] for p in first
    ] == [
        ["a.bin", "b.bin"],
        ["c.bin", "d.bin"],
    ]
    assert [p.archive_unit_index for p in first] == [0, 1]
    assert all(p.archive_unit_count == 2 for p in first)
    assert [p.plan_id for p in first] == [first[0].plan_id, first[0].plan_id]
    assert [p.archive_unit_id for p in first] == [p.archive_unit_id for p in second]
    assert [p.archive_name for p in first] == [p.archive_name for p in second]


def test_oversized_single_file_is_not_split(tmp_path: Path) -> None:
    """One file may exceed the byte target but remains one intact archive-unit payload."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "large.bin", b"x" * 11, old)
    write_at(source / "small.bin", b"y" * 2, old + timedelta(seconds=1))

    plans = plan_policy(_policy(source, destination, max_bytes=8), now=NOW).archive_plans

    assert len(plans) == 2
    assert [
        sum(a.source.identity.size for a in p.actions if a.source.kind.value == "regular")
        for p in plans
    ] == [11, 2]


def test_cleanup_alias_follows_target_archive_unit(tmp_path: Path) -> None:
    """Cleanup-only alias deletion stays coupled to the unit that owns its target file."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "first.bin", b"a" * 4, old)
    write_at(source / "second.bin", b"b" * 4, old + timedelta(seconds=1))
    symlink_at(source / "alias-second", "second.bin", old - timedelta(days=1))
    policy = Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE,
        compression_rules=(),
        archive_name_template="{start:%Y%m%dT%H%M%SZ}",
        archive_unit_max_regular_files=1,
    )

    plans = plan_policy(policy, now=NOW).archive_plans
    alias_unit = next(
        plan.archive_unit_index
        for plan in plans
        if any(action.kind is ActionKind.DROP_ALIAS_SYMLINK for action in plan.actions)
    )
    target_unit = next(
        plan.archive_unit_index
        for plan in plans
        if any(action.source.relative_path == Path("second.bin") for action in plan.actions)
    )

    assert alias_unit == target_unit


def test_execution_writes_manifest_v3_and_report_distinguishes_streams_from_units(
    tmp_path: Path,
) -> None:
    """One logical stream can complete several archive units without inflating stream count."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "a.bin", b"a", old)
    write_at(source / "b.bin", b"b", old + timedelta(seconds=1))
    result = _run_policy_at(_policy(source, destination, max_files=1), planning_time=NOW)
    report = build_run_report((result,), elapsed_seconds=1.0)

    assert len(result.archives) == 2
    assert report.logical_streams_selected == 1
    assert report.archive_units_completed == 2
    for index, archive in enumerate(result.archives):
        manifest = json.loads(
            (archive.archive_directory / MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["manifest_format_version"] == 3
        assert manifest["archive_unit_index"] == index
        assert manifest["archive_unit_count"] == 2
        assert len(manifest["archive_unit_id"]) == 64
        assert (archive.archive_directory / SUCCESS_NAME).is_file()


def test_commit_phase_interruption_preserves_all_sources_and_rerun_reuses_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source cleanup starts until every unit in the logical stream is committed."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    first_source = write_at(source / "a.bin", b"a", old)
    second_source = write_at(source / "b.bin", b"b", old + timedelta(seconds=1))
    policy = _policy(source, destination, max_files=1)
    planned_names = [plan.archive_name for plan in plan_policy(policy, now=NOW).archive_plans]
    real_execute = service.execute_plan
    calls = 0

    def interrupt_second(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ExecutionError("injected second-unit commit interruption")
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(service, "execute_plan", interrupt_second)
    with pytest.raises(ExecutionError, match="second-unit commit interruption"):
        _run_policy_at(policy, planning_time=NOW)

    assert first_source.is_file()
    assert second_source.is_file()
    committed = [
        path for path in destination.iterdir() if path.is_dir() and not path.name.startswith(".")
    ]
    assert [path.name for path in committed] == [planned_names[0]]
    pending = json.loads((committed[0] / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert pending["cleanup_complete"] is False
    assert not (committed[0] / SUCCESS_NAME).exists()

    monkeypatch.setattr(service, "execute_plan", real_execute)
    result = _run_policy_at(policy, planning_time=NOW)

    assert [archive.archive_directory.name for archive in result.archives] == planned_names
    assert not first_source.exists()
    assert not second_source.exists()
    assert all((archive.archive_directory / SUCCESS_NAME).is_file() for archive in result.archives)


def _policy(
    source: Path,
    destination: Path,
    *,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> Policy:
    return Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
        archive_name_template="{start:%Y%m%dT%H%M%SZ}--{end:%Y%m%dT%H%M%SZ}",
        archive_unit_max_source_bytes=max_bytes,
        archive_unit_max_regular_files=max_files,
    )


def _write_config(
    root: Path,
    *,
    schema: int,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        f"schema_version = {schema}",
        'run_interval = "30d"',
        "",
        "[[policies]]",
        'name = "reports"',
        f'sources = ["{root / "source"}"]',
        f'destination = "{root / "archive"}"',
        'minimum_age = "30d"',
        'stream_gap = "8h"',
        'symlink_rule = "ignore"',
        "recursive = true",
        'stream_partition = "source-root"',
        'archive_name_template = "{start:%Y%m%d}"',
    ]
    if max_bytes is not None:
        lines.append(f"archive_unit_max_source_bytes = {max_bytes}")
    if max_files is not None:
        lines.append(f"archive_unit_max_regular_files = {max_files}")
    path = root / "policies.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_partition_policy_changes_logical_and_archive_unit_identity(tmp_path: Path) -> None:
    """Different subdivision limits cannot reuse a unit name for different contents."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    write_at(source / "a.bin", b"a", old)
    write_at(source / "b.bin", b"b", old + timedelta(seconds=1))

    one_per_unit = plan_policy(_policy(source, destination, max_files=1), now=NOW).archive_plans
    two_per_unit = plan_policy(_policy(source, destination, max_files=2), now=NOW).archive_plans

    assert one_per_unit[0].plan_id != two_per_unit[0].plan_id
    assert one_per_unit[0].archive_unit_id != two_per_unit[0].archive_unit_id
    assert one_per_unit[0].archive_name != two_per_unit[0].archive_name


def test_incomplete_group_rejects_changed_partition_identity_before_new_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending group cannot be abandoned by replanning under different limits."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    old = NOW - timedelta(days=40)
    first_source = write_at(source / "a.bin", b"a", old)
    second_source = write_at(source / "b.bin", b"b", old + timedelta(seconds=1))
    original = _policy(source, destination, max_files=1)
    real_execute = service.execute_plan
    calls = 0

    def interrupt_second(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ExecutionError("injected second-unit commit interruption")
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(service, "execute_plan", interrupt_second)
    with pytest.raises(ExecutionError, match="second-unit commit interruption"):
        _run_policy_at(original, planning_time=NOW)
    monkeypatch.setattr(service, "execute_plan", real_execute)

    changed = _policy(source, destination, max_files=2)
    with pytest.raises(RecoveryError, match="cannot be reconstructed by current planning"):
        _run_policy_at(changed, planning_time=NOW)

    assert first_source.is_file()
    assert second_source.is_file()
    manifests = [
        json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
        for path in destination.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(manifests) == 1
    assert manifests[0]["cleanup_complete"] is False
