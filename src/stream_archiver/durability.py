"""Linux filesystem synchronization helpers for archival transaction boundaries.

The helpers in this module implement the bounded durability contract documented
in ``docs/requirements.md``. They do not claim to make network, FUSE, overlay,
or storage-controller behavior equivalent to a verified local filesystem.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path


def sync_regular_file(path: Path) -> None:
    """Synchronize one regular file after its final metadata changes.

    The function rejects symlinks and non-regular files so callers cannot
    accidentally turn a namespace check into a following open operation.
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"cannot synchronize non-regular file: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_directory(path: Path) -> None:
    """Synchronize one directory namespace entry set."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_directories(paths: Iterable[Path]) -> None:
    """Synchronize unique directories deepest-first, then lexically.

    Deepest-first ordering persists leaf namespace changes before their parent
    relationships are relied on by a later transaction state.
    """

    unique = {path for path in paths}
    for path in sorted(unique, key=lambda item: (-len(item.parts), item.as_posix())):
        if path.exists() and path.is_dir() and not path.is_symlink():
            sync_directory(path)


def sync_tree_directories(root: Path) -> None:
    """Synchronize all real directories below ``root`` and then ``root`` itself."""

    directories = [
        path
        for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    ]
    directories.append(root)
    sync_directories(directories)
