"""Filesystem discovery that never follows symbolic links during traversal."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from stream_archiver.errors import ExecutionError
from stream_archiver.model import Entry, EntryKind, FileIdentity
from stream_archiver.observability import log_event

LOGGER = logging.getLogger(__name__)


def discover_entries(source: Path) -> tuple[Entry, ...]:
    """Return regular files and symlinks below ``source`` in deterministic order.

    Directories, sockets, devices, and FIFOs are not archival candidates. A
    disappearing entry is ignored because discovery is only a snapshot; any
    selected entry is validated again during execution.
    """

    log_event(
        LOGGER,
        logging.INFO,
        "discovery_started",
        "scanning source without following symlinks",
        source=source,
    )
    if source.is_symlink() or not source.is_dir():
        raise ExecutionError(f"source is not a real directory: {source}")

    entries: list[Entry] = []
    skipped_special = 0
    disappeared = 0
    for root_text, directory_names, file_names in os.walk(
        source, followlinks=False, onerror=_raise_walk_error
    ):
        directory_names.sort()
        file_names.sort()
        root = Path(root_text)

        # os.walk places symlinked directories in directory_names. Record them
        # as links and remove them so traversal never follows them.
        retained_directories: list[str] = []
        for name in directory_names:
            path = root / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                disappeared += 1
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "discovery_entry_disappeared",
                    "entry disappeared during discovery and was omitted",
                    path=path,
                )
                continue
            except OSError as exc:
                raise ExecutionError(
                    f"cannot inspect source entry {path}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                entries.append(_entry_from_stat(source, path, metadata))
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in file_names:
            path = root / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                disappeared += 1
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "discovery_entry_disappeared",
                    "entry disappeared during discovery and was omitted",
                    path=path,
                )
                continue
            if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                entries.append(_entry_from_stat(source, path, metadata))
            else:
                skipped_special += 1
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "discovery_entry_ignored",
                    "unsupported filesystem entry was not considered",
                    path=path,
                    mode=oct(metadata.st_mode),
                )

    ordered = tuple(
        sorted(entries, key=lambda item: (item.mtime_ns, item.relative_path.as_posix()))
    )
    regular_files = sum(entry.kind is EntryKind.REGULAR for entry in ordered)
    symlinks = len(ordered) - regular_files
    log_event(
        LOGGER,
        logging.INFO,
        "discovery_completed",
        "source discovery completed",
        source=source,
        entries=len(ordered),
        regular_files=regular_files,
        symlinks=symlinks,
        disappeared=disappeared,
        skipped_special=skipped_special,
    )
    return ordered


def _entry_from_stat(source: Path, path: Path, metadata: os.stat_result) -> Entry:
    kind = EntryKind.SYMLINK if stat.S_ISLNK(metadata.st_mode) else EntryKind.REGULAR
    try:
        link_target = os.readlink(path) if kind is EntryKind.SYMLINK else None
    except OSError as exc:
        raise ExecutionError(f"cannot read source symlink {path}: {exc}") from exc
    return Entry(
        relative_path=path.relative_to(source),
        absolute_path=path,
        kind=kind,
        identity=FileIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
        ),
        link_target=link_target,
    )


def _raise_walk_error(error: OSError) -> None:
    """Convert an os.walk traversal error into a visible policy failure."""

    raise ExecutionError(f"cannot traverse source directory: {error}") from error
