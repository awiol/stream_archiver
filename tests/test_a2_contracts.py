"""Regression and contract tests added by the 0.4.0a2 whole-package review."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_archiver.cli import main
from stream_archiver.config import AppConfig, Policy, StreamPartition, SymlinkRule, load_config
from stream_archiver.discovery import discover_entries
from stream_archiver.errors import ConfigurationError, LockUnavailableError
from stream_archiver.executor import _discard_staging_directory
from stream_archiver.locking import resource_locks
from stream_archiver.observability import configure_logging
from stream_archiver.service import _run_policy_at, plan_policy, run_policy, verify_destination
from stream_archiver.systemd import render_systemd_bundle
from tests.helpers import write_at

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _policy(
    source: Path,
    destination: Path,
    *,
    recursive: bool = True,
    partition: StreamPartition = StreamPartition.SOURCE_ROOT,
    symlink_rule: SymlinkRule = SymlinkRule.IGNORE,
) -> Policy:
    return Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=symlink_rule,
        compression_rules=(),
        recursive=recursive,
        stream_partition=partition,
    )


def test_public_run_policy_has_no_artificial_planning_clock(tmp_path: Path) -> None:
    """SA-REV-001: destructive callers cannot supply a future eligibility clock."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()

    with pytest.raises(TypeError, match="unexpected keyword argument 'now'"):
        run_policy(_policy(source, destination), now=NOW)  # type: ignore[call-arg]


def test_cleanup_leaves_unselected_empty_directories_unchanged(tmp_path: Path) -> None:
    """SA-REV-002: cleanup mutates selected entries, not unrelated empty namespace."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    unrelated = source / "keep-empty"
    unrelated.mkdir(parents=True)
    destination.mkdir()
    write_at(source / "selected" / "data.txt", b"payload", NOW - timedelta(days=40))

    _run_policy_at(_policy(source, destination), planning_time=NOW)

    assert unrelated.is_dir()
    assert (source / "selected").is_dir()


def test_external_hardlink_identity_does_not_make_symlink_an_alias(tmp_path: Path) -> None:
    """SA-REV-003: alias cleanup follows an exact selected in-source target path.

    The external file is a hardlink to the selected source file and therefore has
    the same device/inode identity.  Identity equality alone must not authorize
    removal of a symlink whose resolved pathname is outside the source root.
    """

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    outside = tmp_path / "outside.txt"
    destination.mkdir()
    selected = write_at(source / "selected.txt", b"payload", NOW - timedelta(days=40))
    outside.hardlink_to(selected)
    alias = source / "external-alias"
    alias.symlink_to(outside)

    result = _run_policy_at(
        _policy(
            source,
            destination,
            symlink_rule=SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE,
        ),
        planning_time=NOW,
    )

    assert result.archives[0].dropped_alias_symlinks == 0
    assert alias.is_symlink()


def test_unselected_internal_hardlink_target_does_not_authorize_alias_cleanup(
    tmp_path: Path,
) -> None:
    """SA-REV-003: alias cleanup requires the exact target path to be selected.

    Non-recursive discovery selects the top-level hardlink but does not discover
    the nested sibling hardlink targeted by the top-level symlink. Equal inode
    identity must not make the symlink cleanup-only.
    """

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    selected = write_at(source / "selected.txt", b"payload", NOW - timedelta(days=40))
    nested = source / "nested" / "sibling.txt"
    nested.parent.mkdir()
    nested.hardlink_to(selected)
    alias = source / "alias"
    alias.symlink_to(Path("nested") / "sibling.txt")

    result = _run_policy_at(
        _policy(
            source,
            destination,
            recursive=False,
            symlink_rule=SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE,
        ),
        planning_time=NOW,
    )

    assert result.archives[0].dropped_alias_symlinks == 0
    assert alias.is_symlink()
    assert nested.is_file()


def test_configured_symlink_root_is_rejected_before_canonicalization(tmp_path: Path) -> None:
    """SA-REV-004: configuration cannot hide a source-root symlink by resolving it."""

    real_source = tmp_path / "real-source"
    real_source.mkdir()
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(real_source, target_is_directory=True)
    destination = tmp_path / "archive"
    config = tmp_path / "policies.toml"
    config.write_text(
        f'''schema_version = 2\nrun_interval = "30d"\n\n[[policies]]\nname = "reports"\nsources = ["{source_alias}"]\ndestination = "{destination}"\nminimum_age = "30d"\nstream_gap = "8h"\nsymlink_rule = "ignore"\n''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="source root must not be a symbolic link"):
        load_config(config)


def test_configured_symlink_destination_is_rejected_before_canonicalization(tmp_path: Path) -> None:
    """SA-REV-004: a configured destination symlink remains visible to validation."""

    source = tmp_path / "source"
    source.mkdir()
    real_destination = tmp_path / "real-archive"
    real_destination.mkdir()
    destination_alias = tmp_path / "archive-alias"
    destination_alias.symlink_to(real_destination, target_is_directory=True)
    config = tmp_path / "policies.toml"
    config.write_text(
        "\n".join(
            (
                "schema_version = 2",
                'run_interval = "30d"',
                "",
                "[[policies]]",
                'name = "reports"',
                f'sources = ["{source}"]',
                f'destination = "{destination_alias}"',
                'minimum_age = "30d"',
                'stream_gap = "8h"',
                'symlink_rule = "ignore"',
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="destination root must not be a symbolic link"):
        load_config(config)


def test_schema_three_exposes_discovery_and_partition_controls(tmp_path: Path) -> None:
    """The new discovery controls are explicit schema-3 behavior with safe defaults."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    config = tmp_path / "policies.toml"
    config.write_text(
        f'''schema_version = 3\nrun_interval = "30d"\n\n[[policies]]\nname = "reports"\nsources = ["{source}"]\ndestination = "{destination}"\nminimum_age = "30d"\nstream_gap = "8h"\nsymlink_rule = "ignore"\nrecursive = false\nstream_partition = "parent-directory"\n''',
        encoding="utf-8",
    )

    policy = load_config(config).policies[0]
    assert policy.recursive is False
    assert policy.stream_partition is StreamPartition.PARENT_DIRECTORY


def test_schema_two_rejects_schema_three_discovery_fields(tmp_path: Path) -> None:
    """Old schema identity does not silently acquire semantics unknown to 0.4.0a1."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    config = tmp_path / "policies.toml"
    config.write_text(
        f'''schema_version = 2\nrun_interval = "30d"\n\n[[policies]]\nname = "reports"\nsources = ["{source}"]\ndestination = "{destination}"\nminimum_age = "30d"\nstream_gap = "8h"\nsymlink_rule = "ignore"\nrecursive = false\n''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unknown keys: recursive"):
        load_config(config)


def test_non_recursive_discovery_omits_real_descendants(tmp_path: Path) -> None:
    """Top-level discovery visits root entries but does not descend into real directories."""

    source = tmp_path / "source"
    write_at(source / "root.txt", b"root", NOW)
    write_at(source / "nested" / "child.txt", b"child", NOW)

    paths = {entry.relative_path for entry in discover_entries(source, recursive=False)}

    assert paths == {Path("root.txt")}


def test_parent_directory_partition_prevents_cross_directory_stream_merge(tmp_path: Path) -> None:
    """Recursive discovery and stream partitioning are independent policy decisions."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(source / "a" / "one.txt", b"a", old)
    write_at(source / "b" / "two.txt", b"b", old + timedelta(minutes=1))

    root_plan = plan_policy(_policy(source, destination), now=NOW)
    directory_plan = plan_policy(
        _policy(source, destination, partition=StreamPartition.PARENT_DIRECTORY),
        now=NOW,
    )

    assert root_plan.streams == 1
    assert directory_plan.streams == 2
    assert len(directory_plan.archive_plans) == 2


def test_verify_destination_direct_api_uses_shared_resource_lock(tmp_path: Path) -> None:
    """SA-REV-007: the exported verification API participates in lock coordination."""

    destination = tmp_path / "archive"
    destination.mkdir()

    with resource_locks((destination,), exclusive=True), pytest.raises(LockUnavailableError):
        verify_destination(destination)


def test_render_systemd_force_replaces_output_symlink_not_its_target(tmp_path: Path) -> None:
    """SA-REV-005: forced regeneration cannot overwrite an arbitrary symlink target."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    config = AppConfig(run_interval=timedelta(days=30), policies=(_policy(source, destination),))
    config_path = tmp_path / "policy.toml"
    config_path.write_text("schema_version = 2\n", encoding="utf-8")
    executable = tmp_path / "stream-archiver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    output = tmp_path / "generated"
    result = render_systemd_bundle(
        config, config_path=config_path, executable=executable, output_directory=output
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("do not overwrite\n", encoding="utf-8")
    result.service_path.unlink()
    result.service_path.symlink_to(victim)

    result = render_systemd_bundle(
        config,
        config_path=config_path,
        executable=executable,
        output_directory=output,
        force=True,
    )

    assert victim.read_text(encoding="utf-8") == "do not overwrite\n"
    assert result.service_path.is_file()
    assert not result.service_path.is_symlink()


def test_plan_defaults_to_human_summary_and_json_is_explicit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SA-REV-012: plan supports an operator summary and explicit action-level JSON."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.json", b"payload", NOW - timedelta(days=40))
    config = tmp_path / "policies.toml"
    config.write_text(
        f'''schema_version = 3\nrun_interval = "30d"\n\n[[policies]]\nname = "reports"\nsources = ["{source}"]\ndestination = "{destination}"\nminimum_age = "30d"\nstream_gap = "8h"\nsymlink_rule = "ignore"\nrecursive = true\nstream_partition = "source-root"\n''',
        encoding="utf-8",
    )

    assert main(["--config", str(config), "plan", "--at", NOW.isoformat()]) == 0
    human = capsys.readouterr().out
    assert "Stream Archiver plan" in human
    assert "1 streams, 1 eligible, 1 archives planned" in human
    assert "Nothing has been changed" in human
    assert len(human.splitlines()) <= 40

    assert main(["--config", str(config), "plan", "--at", NOW.isoformat(), "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert machine[0]["policy"] == "reports"
    assert machine[0]["sources"][0]["archives"][0]["actions"][0]["source"] == "data.json"


def test_failed_staging_cleanup_emits_operator_visible_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-REV-009: failed best-effort staging cleanup remains visible to operators."""

    output = io.StringIO()
    configure_logging(level="WARNING", format_name="json", stream=output)
    staging = tmp_path / "staging"
    staging.mkdir()

    def fail_rmtree(path: Path) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr("stream_archiver.executor.shutil.rmtree", fail_rmtree)
    _discard_staging_directory(staging, archive="archive-1")

    event = json.loads(output.getvalue())
    assert event["event"] == "staging_cleanup_failed"
    assert event["level"] == "WARNING"
    assert event["staging"] == str(staging)
    assert "next_action" in event
