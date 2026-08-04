"""Filesystem tests protect safe movement, compression, evidence, and recovery."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stream_archiver.config import (
    CompressionCodec,
    CompressionRule,
    Policy,
    SymlinkRule,
)
from stream_archiver.errors import ExecutionError, PlanningError, RecoveryError
from stream_archiver.executor import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    SUCCESS_NAME,
    recover_pending_archives,
    verify_archive,
)
from stream_archiver.model import ActionKind
from stream_archiver.service import plan_policy, run_policy
from tests.helpers import symlink_at, write_at

NOW = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


def test_run_policy_safely_moves_and_compresses_with_success_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion removes sources only after level-9 output and hashes verify."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(source / "session/data.json", b'{"value": 1}\n', old)
    write_at(source / "session/report.HTML", b"<h1>report</h1>\n", old)
    write_at(source / "session/plain.txt", b"plain\n", old)
    symlink_at(source / "session/alias.json", "data.json", old)
    symlink_at(source / "session/dangling", "missing.txt", old)
    absolute = symlink_at(source / "session/absolute", "/etc/hosts", old)

    gzip_levels: list[int] = []
    bz2_levels: list[int] = []
    original_gzip = gzip.GzipFile
    original_bz2 = bz2.BZ2Compressor

    def recording_gzip(*args: object, **kwargs: object) -> gzip.GzipFile:
        mode = kwargs.get("mode", args[1] if len(args) > 1 else None)
        if mode == "wb":
            gzip_levels.append(int(kwargs["compresslevel"]))
        return original_gzip(*args, **kwargs)

    def recording_bz2(level: int = 9) -> bz2.BZ2Compressor:
        bz2_levels.append(level)
        return original_bz2(level)

    monkeypatch.setattr("stream_archiver.executor.gzip.GzipFile", recording_gzip)
    monkeypatch.setattr("stream_archiver.executor.bz2.BZ2Compressor", recording_bz2)

    result = run_policy(_policy((source,), destination), now=NOW)

    assert gzip_levels == [9]
    assert bz2_levels == [9]
    assert len(result.archives) == 1
    execution = result.archives[0]
    archive = execution.archive_directory
    assert execution.moved_files == 3
    assert execution.uncompressed_files == 1
    assert execution.gzip_files == 1
    assert execution.bz2_files == 1
    assert gzip.decompress((archive / "session/data.json.gz").read_bytes()) == b'{"value": 1}\n'
    compressed_html = archive / "session/report.HTML.bz2"
    assert bz2.decompress(compressed_html.read_bytes()) == b"<h1>report</h1>\n"
    assert (archive / "session/plain.txt").read_bytes() == b"plain\n"
    assert (archive / "session/dangling").is_symlink()
    assert os.readlink(archive / "session/dangling") == "missing.txt"
    assert not os.path.lexists(source / "session/alias.json")
    assert not os.path.lexists(source / "session/dangling")
    assert absolute.is_symlink()

    manifest_path = archive / MANIFEST_NAME
    checksums_path = archive / CHECKSUMS_NAME
    success_path = archive / SUCCESS_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    success = json.loads(success_path.read_text(encoding="utf-8"))
    assert manifest["cleanup_complete"] is True
    assert manifest["compression_level"] == 9
    assert success["status"] == "completed"
    assert success["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert success["checksums_sha256"] == hashlib.sha256(checksums_path.read_bytes()).hexdigest()
    assert {item["compression"] for item in checksums["files"]} == {None, "gzip", "bz2"}
    assert verify_archive(archive).success_evidence == success_path


def test_default_regular_file_action_is_move(tmp_path: Path) -> None:
    """A regular file without a compression match is presented as a move."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))

    plan = plan_policy(_policy((source,), destination), now=NOW)

    assert plan.archive_plans[0].actions[0].kind is ActionKind.MOVE


def test_multiple_sources_produce_separate_final_directories(tmp_path: Path) -> None:
    """Identically timed files from shared settings never form one cross-source stream."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(first / "same.txt", b"first", old)
    write_at(second / "same.txt", b"second", old)

    result = run_policy(_policy((first, second), destination), now=NOW)

    assert len(result.archives) == 2
    directories = {item.archive_directory for item in result.archives}
    assert len(directories) == 2
    archived_contents = {
        (directory / "same.txt").read_bytes() for directory in directories
    }
    assert archived_contents == {b"first", b"second"}
    manifests = [
        json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        for directory in directories
    ]
    assert {item["source_root"] for item in manifests} == {str(first), str(second)}


def test_policies_with_shared_destination_create_distinct_archives(tmp_path: Path) -> None:
    """Equal destination roots remain safe because plan identity includes source ownership."""

    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(first_source / "data.txt", b"first", old)
    write_at(second_source / "data.txt", b"second", old)
    first_policy = Policy(
        name="first-policy",
        sources=(first_source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
    )
    second_policy = Policy(
        name="second-policy",
        sources=(second_source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
    )

    first_result = run_policy(first_policy, now=NOW)
    second_result = run_policy(second_policy, now=NOW)

    directories = {
        first_result.archives[0].archive_directory,
        second_result.archives[0].archive_directory,
    }
    assert len(directories) == 2
    assert {
        json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))["policy_name"]
        for directory in directories
    } == {"first-policy", "second-policy"}


def test_verify_archive_rejects_tampered_success_marker(tmp_path: Path) -> None:
    """Completion evidence cannot be edited without making verification fail."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.txt", b"important", NOW - timedelta(days=40))
    archive = run_policy(_policy((source,), destination), now=NOW).archives[0].archive_directory
    success_path = archive / SUCCESS_NAME
    success = json.loads(success_path.read_text(encoding="utf-8"))
    success["manifest_sha256"] = "0" * 64
    success_path.write_text(json.dumps(success), encoding="utf-8")

    with pytest.raises(RecoveryError, match="manifest_sha256 mismatch"):
        verify_archive(archive)


def test_plan_rejects_compression_destination_collision(tmp_path: Path) -> None:
    """A generated compressed name cannot overwrite another source path."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(source / "data.json", b"source", old)
    write_at(source / "data.json.gz", b"already present", old + timedelta(hours=1))

    with pytest.raises(PlanningError, match="archive path collision"):
        plan_policy(_policy((source,), destination), now=NOW)


def test_plan_rejects_reserved_evidence_path(tmp_path: Path) -> None:
    """User payloads cannot replace the manifest, checksum index, or success marker."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / SUCCESS_NAME, b"payload", NOW - timedelta(days=40))

    policy = Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
    )
    with pytest.raises(PlanningError, match="archive path collision"):
        plan_policy(policy, now=NOW)


def test_staging_failure_leaves_sources_and_no_committed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure before commit removes partial output and never deletes the source."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    original = write_at(source / "data.json", b"important", NOW - timedelta(days=40))

    def fail_write(*args: object, **kwargs: object) -> tuple[str, str, int]:
        raise ExecutionError("injected write failure")

    monkeypatch.setattr("stream_archiver.executor._write_regular_payload", fail_write)

    with pytest.raises(ExecutionError, match="injected write failure"):
        run_policy(_policy((source,), destination), now=NOW)

    assert original.read_bytes() == b"important"
    committed = [path for path in destination.iterdir() if path.name != ".stream-archiver-staging"]
    assert committed == []


def test_pending_cleanup_has_no_success_marker_and_recovery_creates_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success evidence is withheld until interrupted source cleanup completes."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    original = write_at(source / "data.txt", b"important", NOW - timedelta(days=40))

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise ExecutionError("injected cleanup failure")

    monkeypatch.setattr("stream_archiver.executor._remove_sources", fail_cleanup)
    with pytest.raises(ExecutionError, match="injected cleanup failure"):
        run_policy(_policy((source,), destination), now=NOW)

    archive = next(
        path
        for path in destination.iterdir()
        if path.name != ".stream-archiver-staging"
    )
    pending = json.loads((archive / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert pending["cleanup_complete"] is False
    assert not (archive / SUCCESS_NAME).exists()
    assert original.exists()

    monkeypatch.undo()
    recovered = recover_pending_archives(destination, source, now=NOW + timedelta(hours=1))

    assert recovered == (archive,)
    assert not original.exists()
    assert (archive / SUCCESS_NAME).is_file()
    assert verify_archive(archive).payload_files == 1


def test_source_change_after_commit_blocks_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery refuses to delete a path whose content no longer matches evidence."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    original = write_at(source / "data.txt", b"first", NOW - timedelta(days=40))

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise ExecutionError("injected cleanup failure")

    monkeypatch.setattr("stream_archiver.executor._remove_sources", fail_cleanup)
    with pytest.raises(ExecutionError):
        run_policy(_policy((source,), destination), now=NOW)
    monkeypatch.undo()

    original.write_bytes(b"changed")

    with pytest.raises(RecoveryError, match="source changed after planning"):
        recover_pending_archives(destination, source, now=NOW)
    assert original.read_bytes() == b"changed"


def test_verify_archive_detects_payload_corruption(tmp_path: Path) -> None:
    """A later hash verification reports changed archive bytes."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.txt", b"important", NOW - timedelta(days=40))
    archive = run_policy(_policy((source,), destination), now=NOW).archives[0].archive_directory
    (archive / "data.txt").write_bytes(b"corrupt")

    with pytest.raises(RecoveryError, match="(size|SHA-256) mismatch"):
        verify_archive(archive)


def test_recovery_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    """A modified archive manifest cannot direct cleanup outside the source root."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    archive = destination / "malicious"
    archive.mkdir()
    (archive / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "manifest_format_version": 1,
                "plan_id": "id",
                "policy_name": "reports",
                "source_root": str(source),
                "destination_root": str(destination),
                "archive_name": "malicious",
                "stream_oldest_mtime_ns": 0,
                "stream_newest_mtime_ns": 0,
                "created_at": "2026-08-02T12:00:00Z",
                "gzip_compresslevel": 9,
                "cleanup_complete": False,
                "entries": [
                    {
                        "source_path": "../victim.txt",
                        "source_kind": "regular",
                        "action": "copy",
                        "archive_path": "victim.txt",
                        "identity": {
                            "device": 0,
                            "inode": 0,
                            "mode": 0,
                            "size": 0,
                            "mtime_ns": 0,
                        },
                        "link_target": None,
                        "source_sha256": "0" * 64,
                        "archive_sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RecoveryError, match="unsafe path"):
        recover_pending_archives(destination, source, now=NOW)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_plan_rejects_file_directory_collision_created_by_compression(tmp_path: Path) -> None:
    """A generated `.bz2` file cannot replace a required directory path."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    old = NOW - timedelta(days=40)
    write_at(source / "report.html", b"report", old)
    write_at(source / "report.html.bz2/child.txt", b"child", old + timedelta(hours=1))

    with pytest.raises(PlanningError, match="archive path collision"):
        plan_policy(_policy((source,), destination), now=NOW)


def test_invalid_staging_path_is_reported_without_deleting_source(tmp_path: Path) -> None:
    """A non-directory staging path fails visibly and preserves selected files."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    original = write_at(source / "data.txt", b"important", NOW - timedelta(days=40))
    destination.mkdir()
    (destination / ".stream-archiver-staging").write_text("not a directory", encoding="utf-8")

    with pytest.raises(ExecutionError, match="filesystem operation failed"):
        run_policy(_policy((source,), destination), now=NOW)

    assert original.read_bytes() == b"important"


def _policy(sources: tuple[Path, ...], destination: Path) -> Policy:
    return Policy(
        name="reports",
        sources=sources,
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE,
        compression_rules=(
            CompressionRule((".json",), CompressionCodec.GZIP),
            CompressionRule((".html",), CompressionCodec.BZ2),
        ),
    )
