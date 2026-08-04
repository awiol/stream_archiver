"""Transactional move execution, integrity evidence, and cleanup recovery."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from stream_archiver.errors import ExecutionError, RecoveryError
from stream_archiver.model import (
    ActionKind,
    ArchivePlan,
    Entry,
    EntryKind,
    PlannedAction,
)

COMPRESSION_LEVEL = 9
MANIFEST_NAME = "MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS.json"
SUCCESS_NAME = "SUCCESS.json"
MANIFEST_FORMAT_VERSION = 2
LEGACY_MANIFEST_FORMAT_VERSION = 1
EVIDENCE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ArchiveExecutionResult:
    """Observed result of committing, verifying, and cleaning up one plan."""

    archive_directory: Path
    moved_files: int
    gzip_files: int
    bz2_files: int
    preserved_symlinks: int
    dropped_alias_symlinks: int
    skipped_symlinks: int
    success_evidence: Path | None

    @property
    def compressed_files(self) -> int:
        """Return the number of moved files transformed with compression."""

        return self.gzip_files + self.bz2_files

    @property
    def uncompressed_files(self) -> int:
        """Return the number of moved regular files stored without compression."""

        return self.moved_files - self.compressed_files


@dataclass(frozen=True, slots=True)
class ArchiveVerificationResult:
    """Evidence observed while re-verifying one committed archive."""

    archive_directory: Path
    payload_files: int
    preserved_symlinks: int
    manifest_sha256: str
    checksums_sha256: str | None
    success_evidence: Path | None


def execute_plan(plan: ArchivePlan, *, now: datetime) -> ArchiveExecutionResult:
    """Safely move one source stream into a committed archive directory.

    An uncompressed move is implemented as a verified copy into destination
    staging, an atomic destination-side commit, source revalidation, and source
    deletion. Compression follows the same transaction. Source deletion never
    starts before every payload and its SHA-256 evidence are durable in the
    committed archive.
    """

    now_utc = _require_aware_utc(now)
    staging_directory: Path | None = None
    try:
        _ensure_real_directory(plan.destination_root)
        final_directory = plan.final_directory
        if os.path.lexists(final_directory):
            if final_directory.is_symlink() or not final_directory.is_dir():
                raise ExecutionError(
                    f"archive path is not a real directory: {final_directory}"
                )
            manifest = _read_manifest(final_directory / MANIFEST_NAME, RecoveryError)
            _validate_plan_manifest_match(plan, manifest, final_directory)
            if manifest.get("cleanup_complete") is not True:
                _recover_manifest(final_directory, manifest, completed_at=now_utc)
            elif manifest["manifest_format_version"] == MANIFEST_FORMAT_VERSION:
                _ensure_success_evidence(final_directory, manifest)
            return _result_from_manifest(final_directory, manifest)

        staging_root = plan.destination_root / ".stream-archiver-staging"
        _ensure_real_directory(staging_root)
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f"{plan.archive_name}.", suffix=".partial", dir=staging_root
            )
        )

        manifest = _stage_plan(plan, staging_directory, now_utc)
        _write_manifest(staging_directory / MANIFEST_NAME, manifest)
        _fsync_directory(staging_directory)
        os.replace(staging_directory, final_directory)
        staging_directory = None
        _fsync_directory(plan.destination_root)

        _validate_all_sources(manifest, plan.source_root)
        _remove_sources(manifest, plan.source_root)
        manifest["cleanup_complete"] = True
        manifest["cleanup_completed_at"] = _format_utc(now_utc)
        _write_manifest(final_directory / MANIFEST_NAME, manifest)
        _remove_empty_source_directories(plan.source_root)
        _ensure_success_evidence(final_directory, manifest)
        return _result_from_manifest(final_directory, manifest)
    except (ExecutionError, RecoveryError):
        if staging_directory is not None and staging_directory.exists():
            shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    except OSError as exc:
        if staging_directory is not None and staging_directory.exists():
            shutil.rmtree(staging_directory, ignore_errors=True)
        raise ExecutionError(
            f"filesystem operation failed for plan {plan.plan_id}: {exc}"
        ) from exc


def recover_pending_archives(
    destination_root: Path,
    source_root: Path,
    *,
    now: datetime | None = None,
) -> tuple[Path, ...]:
    """Resume cleanup and repair missing success evidence for one source root."""

    completed_at = _require_aware_utc(now or datetime.now(timezone.utc))
    try:
        if not destination_root.exists():
            return ()
        if destination_root.is_symlink() or not destination_root.is_dir():
            raise RecoveryError(
                f"archive destination is not a real directory: {destination_root}"
            )
        recovered: list[Path] = []
        for child in sorted(destination_root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            if child.name == ".stream-archiver-staging":
                continue
            manifest_path = child / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            manifest = _read_manifest(manifest_path, RecoveryError)
            if manifest.get("source_root") != str(source_root):
                continue
            if manifest.get("destination_root") != str(destination_root):
                raise RecoveryError(f"archive manifest destination mismatch: {child}")
            if manifest.get("archive_name") != child.name:
                raise RecoveryError(f"archive manifest name mismatch: {child}")
            if manifest.get("cleanup_complete") is not True:
                _recover_manifest(child, manifest, completed_at=completed_at)
                recovered.append(child)
            elif manifest["manifest_format_version"] == MANIFEST_FORMAT_VERSION:
                _ensure_success_evidence(child, manifest)
        return tuple(recovered)
    except RecoveryError:
        raise
    except OSError as exc:
        raise RecoveryError(
            f"cannot inspect archive destination {destination_root}: {exc}"
        ) from exc


def verify_archive(directory: Path) -> ArchiveVerificationResult:
    """Recompute archive hashes and validate completion evidence.

    Version-2 archives require a completed manifest, a matching SHA-256 index,
    and a ``SUCCESS.json`` marker whose manifest hash matches the final manifest.
    Legacy version-1 archives can still be payload-verified but have no separate
    success marker.
    """

    if directory.is_symlink() or not directory.is_dir():
        raise RecoveryError(f"archive is not a real directory: {directory}")
    manifest_path = directory / MANIFEST_NAME
    manifest = _read_manifest(manifest_path, RecoveryError)
    if manifest.get("archive_name") != directory.name:
        raise RecoveryError(f"archive manifest name mismatch: {directory}")
    if manifest.get("destination_root") != str(directory.parent):
        raise RecoveryError(f"archive manifest destination mismatch: {directory}")
    if manifest.get("cleanup_complete") is not True:
        raise RecoveryError(f"archive cleanup is incomplete: {directory}")

    payload_files, preserved_symlinks = _verify_archive_payloads(directory, manifest)
    manifest_digest = _sha256_regular_file(manifest_path)

    checksums_digest: str | None = None
    success_path: Path | None = None
    if manifest["manifest_format_version"] == MANIFEST_FORMAT_VERSION:
        checksums_digest = _verify_checksums_file(directory, manifest)
        success_path = directory / SUCCESS_NAME
        success = _read_json(success_path, RecoveryError, "success evidence")
        _validate_success(success, success_path)
        expected = {
            "plan_id": manifest["plan_id"],
            "policy_name": manifest["policy_name"],
            "source_root": manifest["source_root"],
            "destination_root": manifest["destination_root"],
            "archive_name": manifest["archive_name"],
            "manifest_sha256": manifest_digest,
            "checksums_sha256": checksums_digest,
        }
        for key, value in expected.items():
            if success.get(key) != value:
                raise RecoveryError(f"success evidence {key} mismatch: {success_path}")

    return ArchiveVerificationResult(
        archive_directory=directory,
        payload_files=payload_files,
        preserved_symlinks=preserved_symlinks,
        manifest_sha256=manifest_digest,
        checksums_sha256=checksums_digest,
        success_evidence=success_path,
    )


def _validate_plan_manifest_match(
    plan: ArchivePlan,
    manifest: dict[str, Any],
    final_directory: Path,
) -> None:
    if manifest.get("plan_id") != plan.plan_id:
        raise ExecutionError(f"archive directory collision: {final_directory}")
    if manifest.get("source_root") != str(plan.source_root):
        raise ExecutionError(f"archive manifest source mismatch: {final_directory}")
    if manifest.get("destination_root") != str(plan.destination_root):
        raise ExecutionError(
            f"archive manifest destination mismatch: {final_directory}"
        )
    if manifest.get("archive_name") != plan.archive_name:
        raise ExecutionError(f"archive manifest name mismatch: {final_directory}")


def _ensure_real_directory(path: Path) -> None:
    """Create a directory and reject a symlink or non-directory at that path."""

    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ExecutionError(f"expected a real directory: {path}")


def _stage_plan(plan: ArchivePlan, staging: Path, now: datetime) -> dict[str, Any]:
    records = [_stage_action(action, staging) for action in plan.actions]
    checksums = _checksums_document(records)
    checksums_path = staging / CHECKSUMS_NAME
    _write_json(checksums_path, checksums)

    return {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "plan_id": plan.plan_id,
        "policy_name": plan.policy_name,
        "source_root": str(plan.source_root),
        "destination_root": str(plan.destination_root),
        "archive_name": plan.archive_name,
        "stream_oldest_mtime_ns": plan.stream.oldest_mtime_ns,
        "stream_newest_mtime_ns": plan.stream.newest_mtime_ns,
        "created_at": _format_utc(now),
        "compression_level": COMPRESSION_LEVEL,
        "checksums_file": CHECKSUMS_NAME,
        "checksums_sha256": _sha256_regular_file(checksums_path),
        "cleanup_complete": False,
        "cleanup_completed_at": None,
        "entries": records,
    }


def _stage_action(action: PlannedAction, staging: Path) -> dict[str, Any]:
    entry = action.source
    compression = _compression_for_action(action.kind)
    record: dict[str, Any] = {
        "source_path": entry.relative_path.as_posix(),
        "source_kind": entry.kind.value,
        "action": action.kind.value,
        "archive_path": action.archive_path.as_posix() if action.archive_path else None,
        "identity": {
            "device": entry.identity.device,
            "inode": entry.identity.inode,
            "mode": entry.identity.mode,
            "size": entry.identity.size,
            "mtime_ns": entry.identity.mtime_ns,
        },
        "link_target": entry.link_target,
        "compression": compression,
        "source_sha256": None,
        "archive_sha256": None,
        "archive_size": None,
    }

    if action.kind in {ActionKind.DROP_ALIAS_SYMLINK, ActionKind.SKIP_SYMLINK}:
        return record

    assert action.archive_path is not None
    destination = staging / action.archive_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    if action.kind is ActionKind.PRESERVE_SYMLINK:
        assert entry.link_target is not None
        os.symlink(entry.link_target, destination)
        _set_symlink_mtime(destination, entry.identity.mtime_ns)
        return record

    source_digest, archive_digest, archive_size = _write_regular_payload(
        entry,
        destination,
        compression=compression,
    )
    record["source_sha256"] = source_digest
    record["archive_sha256"] = archive_digest
    record["archive_size"] = archive_size
    return record


def _write_regular_payload(
    entry: Entry,
    destination: Path,
    *,
    compression: str | None,
) -> tuple[str, str, int]:
    source_hasher = hashlib.sha256()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        source_fd = os.open(entry.absolute_path, flags)
    except OSError as exc:
        raise ExecutionError(
            f"cannot open source safely: {entry.absolute_path}: {exc}"
        ) from exc

    try:
        metadata = os.fstat(source_fd)
        _assert_regular_identity(entry, metadata)
        with os.fdopen(source_fd, "rb", closefd=False) as source_stream:
            with destination.open("xb") as raw_destination:
                if compression == "gzip":
                    with gzip.GzipFile(
                        filename="",
                        mode="wb",
                        compresslevel=COMPRESSION_LEVEL,
                        fileobj=raw_destination,
                        mtime=0,
                    ) as output:
                        _copy_and_hash(source_stream, output, source_hasher)
                elif compression == "bz2":
                    _copy_bz2_and_hash(source_stream, raw_destination, source_hasher)
                elif compression is None:
                    _copy_and_hash(source_stream, raw_destination, source_hasher)
                else:  # pragma: no cover - planner and manifest validation constrain codecs.
                    raise ExecutionError(
                        f"unsupported compression codec: {compression}"
                    )
                raw_destination.flush()
                os.fsync(raw_destination.fileno())
    finally:
        os.close(source_fd)

    after = entry.absolute_path.lstat()
    _assert_regular_identity(entry, after)
    os.chmod(destination, stat.S_IMODE(entry.identity.mode))
    os.utime(destination, ns=(entry.identity.mtime_ns, entry.identity.mtime_ns))

    archive_digest = _sha256_regular_file(destination)
    source_digest = source_hasher.hexdigest()
    if compression == "gzip":
        decompressed_digest = _sha256_gzip_payload(destination)
        if decompressed_digest != source_digest:
            raise ExecutionError(f"gzip round-trip verification failed: {destination}")
    elif compression == "bz2":
        decompressed_digest = _sha256_bz2_payload(destination)
        if decompressed_digest != source_digest:
            raise ExecutionError(f"bzip2 round-trip verification failed: {destination}")
    elif archive_digest != source_digest:
        raise ExecutionError(f"move staging verification failed: {destination}")
    return source_digest, archive_digest, destination.stat().st_size


def _copy_bz2_and_hash(
    source: BinaryIO,
    destination: BinaryIO,
    hasher: Any,
) -> None:
    compressor = bz2.BZ2Compressor(COMPRESSION_LEVEL)
    while chunk := source.read(1024 * 1024):
        hasher.update(chunk)
        compressed = compressor.compress(chunk)
        if compressed:
            destination.write(compressed)
    tail = compressor.flush()
    if tail:
        destination.write(tail)


def _copy_and_hash(source: BinaryIO, destination: BinaryIO, hasher: Any) -> None:
    while chunk := source.read(1024 * 1024):
        hasher.update(chunk)
        destination.write(chunk)


def _checksums_document(records: list[dict[str, Any]]) -> dict[str, Any]:
    files = [
        {
            "source_path": record["source_path"],
            "archive_path": record["archive_path"],
            "sha256": record["archive_sha256"],
            "size": record["archive_size"],
            "compression": record["compression"],
        }
        for record in records
        if record["source_kind"] == EntryKind.REGULAR.value
    ]
    files.sort(key=lambda item: item["archive_path"])
    return {
        "format_version": EVIDENCE_FORMAT_VERSION,
        "algorithm": "sha256",
        "files": files,
    }


def _validate_all_sources(manifest: dict[str, Any], source_root: Path) -> None:
    for record in manifest["entries"]:
        action = ActionKind(record["action"])
        if action is ActionKind.SKIP_SYMLINK:
            continue
        source = source_root / Path(record["source_path"])
        if not os.path.lexists(source):
            raise ExecutionError(
                f"selected source disappeared before cleanup: {source}"
            )
        _validate_source_record(source, record, error_type=ExecutionError)


def _remove_sources(
    manifest: dict[str, Any],
    source_root: Path,
    *,
    error_type: type[ExecutionError] | type[RecoveryError] = ExecutionError,
) -> None:
    records = [
        record
        for record in manifest["entries"]
        if ActionKind(record["action"]) is not ActionKind.SKIP_SYMLINK
    ]
    records.sort(
        key=lambda record: (
            0 if record["source_kind"] == EntryKind.SYMLINK.value else 1,
            record["source_path"],
        )
    )
    for record in records:
        path = source_root / Path(record["source_path"])
        if not os.path.lexists(path):
            continue
        _validate_source_record(path, record, error_type=error_type)
        try:
            path.unlink()
        except OSError as exc:
            raise error_type(
                f"archive committed but source cleanup failed for {path}: {exc}"
            ) from exc


def _recover_manifest(
    directory: Path,
    manifest: dict[str, Any],
    *,
    completed_at: datetime,
) -> None:
    source_root = Path(manifest["source_root"])
    for record in manifest["entries"]:
        action = ActionKind(record["action"])
        if action is ActionKind.SKIP_SYMLINK:
            continue
        source = source_root / Path(record["source_path"])
        if not os.path.lexists(source):
            continue
        _validate_source_record(source, record, error_type=RecoveryError)

    _remove_sources(manifest, source_root, error_type=RecoveryError)
    manifest["cleanup_complete"] = True
    if manifest["manifest_format_version"] == MANIFEST_FORMAT_VERSION:
        manifest["cleanup_completed_at"] = _format_utc(completed_at)
    _write_manifest(directory / MANIFEST_NAME, manifest)
    _remove_empty_source_directories(source_root)
    if manifest["manifest_format_version"] == MANIFEST_FORMAT_VERSION:
        _ensure_success_evidence(directory, manifest)


def _validate_source_record(
    source: Path,
    record: dict[str, Any],
    *,
    error_type: type[ExecutionError] | type[RecoveryError],
) -> None:
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise error_type(
            f"cannot inspect source during cleanup: {source}: {exc}"
        ) from exc

    expected = record["identity"]
    actual = {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
    }
    if actual != expected:
        raise error_type(f"source changed after planning; refusing cleanup: {source}")

    if record["source_kind"] == EntryKind.SYMLINK.value:
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or os.readlink(source) != record["link_target"]
        ):
            raise error_type(
                f"symlink changed after planning; refusing cleanup: {source}"
            )
        return

    if not stat.S_ISREG(metadata.st_mode):
        raise error_type(f"source is no longer a regular file: {source}")
    expected_digest = record.get("source_sha256")
    if expected_digest is not None and _sha256_regular_file(source) != expected_digest:
        raise error_type(
            f"source content changed after planning; refusing cleanup: {source}"
        )


def _assert_regular_identity(entry: Entry, metadata: os.stat_result) -> None:
    expected = entry.identity
    actual = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    wanted = (
        expected.device,
        expected.inode,
        expected.mode,
        expected.size,
        expected.mtime_ns,
    )
    if actual != wanted or not stat.S_ISREG(metadata.st_mode):
        raise ExecutionError(f"source changed after planning: {entry.absolute_path}")


def _verify_archive_payloads(
    directory: Path,
    manifest: dict[str, Any],
) -> tuple[int, int]:
    payload_files = 0
    preserved_symlinks = 0
    for record in manifest["entries"]:
        action = ActionKind(record["action"])
        if action in {ActionKind.DROP_ALIAS_SYMLINK, ActionKind.SKIP_SYMLINK}:
            continue
        archive_path = directory / Path(record["archive_path"])
        if action is ActionKind.PRESERVE_SYMLINK:
            if (
                not archive_path.is_symlink()
                or os.readlink(archive_path) != record["link_target"]
            ):
                raise RecoveryError(
                    f"archived symlink does not match manifest: {archive_path}"
                )
            preserved_symlinks += 1
            continue

        if archive_path.is_symlink() or not archive_path.is_file():
            raise RecoveryError(
                f"archived payload is not a regular file: {archive_path}"
            )
        if archive_path.stat().st_size != record.get(
            "archive_size", archive_path.stat().st_size
        ):
            raise RecoveryError(f"archived payload size mismatch: {archive_path}")
        archive_digest = _sha256_regular_file(archive_path)
        if archive_digest != record["archive_sha256"]:
            raise RecoveryError(f"archived payload SHA-256 mismatch: {archive_path}")
        compression = record.get("compression") or _compression_for_action(action)
        if compression == "gzip":
            source_digest = _sha256_gzip_payload(archive_path)
        elif compression == "bz2":
            source_digest = _sha256_bz2_payload(archive_path)
        else:
            source_digest = archive_digest
        if source_digest != record["source_sha256"]:
            raise RecoveryError(f"archived payload round-trip mismatch: {archive_path}")
        payload_files += 1
    return payload_files, preserved_symlinks


def _verify_checksums_file(directory: Path, manifest: dict[str, Any]) -> str:
    checksums_path = directory / manifest["checksums_file"]
    if checksums_path.is_symlink() or not checksums_path.is_file():
        raise RecoveryError(f"missing SHA-256 index: {checksums_path}")
    digest = _sha256_regular_file(checksums_path)
    if digest != manifest["checksums_sha256"]:
        raise RecoveryError(f"SHA-256 index digest mismatch: {checksums_path}")
    actual = _read_json(checksums_path, RecoveryError, "SHA-256 index")
    expected = _checksums_document(manifest["entries"])
    if actual != expected:
        raise RecoveryError(f"SHA-256 index content mismatch: {checksums_path}")
    return digest


def _ensure_success_evidence(directory: Path, manifest: dict[str, Any]) -> None:
    """Verify a completed archive and create its final success marker once."""

    success_path = directory / SUCCESS_NAME
    if os.path.lexists(success_path):
        verify_archive(directory)
        return

    _verify_archive_payloads(directory, manifest)
    checksums_digest = _verify_checksums_file(directory, manifest)
    manifest_digest = _sha256_regular_file(directory / MANIFEST_NAME)
    success = {
        "evidence_format_version": EVIDENCE_FORMAT_VERSION,
        "status": "completed",
        "plan_id": manifest["plan_id"],
        "policy_name": manifest["policy_name"],
        "source_root": manifest["source_root"],
        "destination_root": manifest["destination_root"],
        "archive_name": manifest["archive_name"],
        "completed_at": manifest["cleanup_completed_at"],
        "manifest_sha256": manifest_digest,
        "checksums_sha256": checksums_digest,
    }
    _write_json(success_path, success)
    _fsync_directory(directory)
    verify_archive(directory)


def _validate_success(success: Any, path: Path) -> None:
    if not isinstance(success, dict):
        raise RecoveryError(f"success evidence must be an object: {path}")
    if success.get("evidence_format_version") != EVIDENCE_FORMAT_VERSION:
        raise RecoveryError(f"unsupported success evidence version: {path}")
    if success.get("status") != "completed":
        raise RecoveryError(f"success evidence is not completed: {path}")
    for key in (
        "plan_id",
        "policy_name",
        "source_root",
        "destination_root",
        "archive_name",
        "completed_at",
    ):
        if not isinstance(success.get(key), str) or not success[key]:
            raise RecoveryError(f"invalid {key} in success evidence: {path}")
    for key in ("manifest_sha256", "checksums_sha256"):
        if not _is_sha256(success.get(key)):
            raise RecoveryError(f"invalid {key} in success evidence: {path}")


def _sha256_regular_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecoveryError(
            f"cannot open regular file for hashing {path}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RecoveryError(f"cannot hash non-regular file: {path}")
        hasher = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


def _sha256_gzip_payload(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with gzip.open(path, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except (OSError, EOFError) as exc:
        raise RecoveryError(f"cannot verify gzip payload {path}: {exc}") from exc
    return hasher.hexdigest()


def _sha256_bz2_payload(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with bz2.open(path, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except (OSError, EOFError) as exc:
        raise RecoveryError(f"cannot verify bzip2 payload {path}: {exc}") from exc
    return hasher.hexdigest()


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _write_json(path, manifest)


def _write_json(path: Path, value: Any) -> None:
    """Atomically replace one JSON file without depending on a reusable temp path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_manifest(
    path: Path,
    error_type: type[ExecutionError] | type[RecoveryError],
) -> dict[str, Any]:
    manifest = _read_json(path, error_type, "archive manifest")
    _validate_manifest(manifest, path, error_type)
    return manifest


def _read_json(
    path: Path,
    error_type: type[ExecutionError] | type[RecoveryError],
    description: str,
) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("path is not a real regular file")
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type(f"cannot read {description} {path}: {exc}") from exc


def _validate_manifest(
    manifest: Any,
    path: Path,
    error_type: type[ExecutionError] | type[RecoveryError],
) -> None:
    """Validate recovery-critical shape and reject path traversal."""

    if not isinstance(manifest, dict):
        raise error_type(f"archive manifest must be an object: {path}")
    version = manifest.get("manifest_format_version")
    if version not in {LEGACY_MANIFEST_FORMAT_VERSION, MANIFEST_FORMAT_VERSION}:
        raise error_type(f"unsupported manifest version in {path}")

    required_strings = (
        "plan_id",
        "policy_name",
        "source_root",
        "destination_root",
        "archive_name",
        "created_at",
    )
    for key in required_strings:
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise error_type(f"invalid {key} in archive manifest {path}")
    if not Path(manifest["source_root"]).is_absolute():
        raise error_type(f"source_root must be absolute in archive manifest {path}")
    if not Path(manifest["destination_root"]).is_absolute():
        raise error_type(
            f"destination_root must be absolute in archive manifest {path}"
        )
    if not isinstance(manifest.get("cleanup_complete"), bool):
        raise error_type(f"invalid cleanup_complete in archive manifest {path}")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise error_type(f"entries must be an array in archive manifest {path}")
    for index, record in enumerate(entries):
        _validate_manifest_record(record, path, index, version, error_type)

    if version == LEGACY_MANIFEST_FORMAT_VERSION:
        if manifest.get("gzip_compresslevel") != COMPRESSION_LEVEL:
            raise error_type(f"invalid gzip_compresslevel in archive manifest {path}")
        return

    if manifest.get("compression_level") != COMPRESSION_LEVEL:
        raise error_type(f"invalid compression_level in archive manifest {path}")
    if manifest.get("checksums_file") != CHECKSUMS_NAME:
        raise error_type(f"invalid checksums_file in archive manifest {path}")
    if not _is_sha256(manifest.get("checksums_sha256")):
        raise error_type(f"invalid checksums_sha256 in archive manifest {path}")
    completed_at = manifest.get("cleanup_completed_at")
    if manifest["cleanup_complete"]:
        if not isinstance(completed_at, str) or not completed_at:
            raise error_type(f"missing cleanup_completed_at in archive manifest {path}")
    elif completed_at is not None:
        raise error_type(f"pending manifest cannot have cleanup_completed_at: {path}")


def _validate_manifest_record(
    record: Any,
    path: Path,
    index: int,
    version: int,
    error_type: type[ExecutionError] | type[RecoveryError],
) -> None:
    context = f"{path}: entries[{index}]"
    if not isinstance(record, dict):
        raise error_type(f"{context} must be an object")
    _safe_manifest_relative_path(record.get("source_path"), context, error_type)
    try:
        action = ActionKind(record.get("action"))
        source_kind = EntryKind(record.get("source_kind"))
    except ValueError as exc:
        raise error_type(f"{context} has an unsupported action or source kind") from exc

    archive_path = record.get("archive_path")
    payload_actions = {
        ActionKind.MOVE,
        ActionKind.COPY,
        ActionKind.GZIP,
        ActionKind.BZ2,
        ActionKind.PRESERVE_SYMLINK,
    }
    if action in payload_actions:
        _safe_manifest_relative_path(archive_path, context, error_type)
    elif archive_path is not None:
        raise error_type(f"{context} must not define archive_path for {action.value}")

    regular_actions = {
        ActionKind.MOVE,
        ActionKind.COPY,
        ActionKind.GZIP,
        ActionKind.BZ2,
    }
    symlink_actions = {
        ActionKind.PRESERVE_SYMLINK,
        ActionKind.DROP_ALIAS_SYMLINK,
        ActionKind.SKIP_SYMLINK,
    }
    if source_kind is EntryKind.SYMLINK:
        if action not in symlink_actions:
            raise error_type(f"{context} has a regular-file action for a symlink")
        if not isinstance(record.get("link_target"), str):
            raise error_type(f"{context} has an invalid link_target")
    else:
        if action not in regular_actions:
            raise error_type(f"{context} has a symlink action for a regular file")
        if record.get("link_target") is not None:
            raise error_type(
                f"{context} must not define link_target for a regular file"
            )

    identity = record.get("identity")
    if not isinstance(identity, dict):
        raise error_type(f"{context} has an invalid identity")
    for key in ("device", "inode", "mode", "size", "mtime_ns"):
        if not isinstance(identity.get(key), int):
            raise error_type(f"{context}.identity.{key} must be an integer")

    source_digest = record.get("source_sha256")
    archive_digest = record.get("archive_sha256")
    if source_kind is EntryKind.REGULAR:
        if not _is_sha256(source_digest) or not _is_sha256(archive_digest):
            raise error_type(f"{context} has invalid regular-file digests")
        if version == MANIFEST_FORMAT_VERSION:
            if (
                not isinstance(record.get("archive_size"), int)
                or record["archive_size"] < 0
            ):
                raise error_type(f"{context} has invalid archive_size")
            expected_compression = _compression_for_action(action)
            if record.get("compression") != expected_compression:
                raise error_type(f"{context} has inconsistent compression metadata")
    elif source_digest is not None or archive_digest is not None:
        raise error_type(f"{context} must not define digests for a symlink")


def _compression_for_action(action: ActionKind) -> str | None:
    if action is ActionKind.GZIP:
        return "gzip"
    if action is ActionKind.BZ2:
        return "bz2"
    return None


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _safe_manifest_relative_path(
    value: Any,
    context: str,
    error_type: type[ExecutionError] | type[RecoveryError],
) -> Path:
    if not isinstance(value, str) or not value:
        raise error_type(f"{context} has an invalid relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise error_type(f"{context} contains unsafe path {value!r}")
    return relative


def _result_from_manifest(
    directory: Path, manifest: dict[str, Any]
) -> ArchiveExecutionResult:
    counts = {kind: 0 for kind in ActionKind}
    for record in manifest["entries"]:
        counts[ActionKind(record["action"])] += 1
    success = directory / SUCCESS_NAME
    return ArchiveExecutionResult(
        archive_directory=directory,
        moved_files=(
            counts[ActionKind.MOVE]
            + counts[ActionKind.COPY]
            + counts[ActionKind.GZIP]
            + counts[ActionKind.BZ2]
        ),
        gzip_files=counts[ActionKind.GZIP],
        bz2_files=counts[ActionKind.BZ2],
        preserved_symlinks=counts[ActionKind.PRESERVE_SYMLINK],
        dropped_alias_symlinks=counts[ActionKind.DROP_ALIAS_SYMLINK],
        skipped_symlinks=counts[ActionKind.SKIP_SYMLINK],
        success_evidence=(
            success if success.is_file() and not success.is_symlink() else None
        ),
    )


def _set_symlink_mtime(path: Path, mtime_ns: int) -> None:
    try:
        os.utime(path, ns=(mtime_ns, mtime_ns), follow_symlinks=False)
    except (NotImplementedError, OSError):
        # Symlink timestamps are not part of the archive's behavioral contract.
        return


def _remove_empty_source_directories(source_root: Path) -> None:
    directories = sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            continue


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
