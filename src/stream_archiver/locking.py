"""Cooperative hierarchical locking for Stream Archiver filesystem resources."""

from __future__ import annotations

import fcntl
import logging
import os
import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from stream_archiver.errors import LockUnavailableError
from stream_archiver.observability import log_event

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResourceLockRequest:
    """One canonical directory and the cooperative lock mode requested for it."""

    path: Path
    exclusive: bool


def _canonical_existing_directory(path: Path) -> Path:
    """Return one real canonical directory or fail with an actionable lock error."""

    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise LockUnavailableError(f"required lock resource is unavailable: {path}: {exc}") from exc
    if not canonical.is_dir():
        raise LockUnavailableError(f"required lock resource is not a real directory: {path}")
    return canonical


def _ancestor_paths(path: Path) -> tuple[Path, ...]:
    """Return real directory ancestors from filesystem root through ``path``."""

    chain = [path, *path.parents]
    chain.reverse()
    return tuple(chain)


def build_resource_lock_requests(
    roots: Iterable[Path],
    *,
    exclusive: bool,
) -> tuple[ResourceLockRequest, ...]:
    """Build a deterministic hierarchical lock set for canonical resource roots.

    Ancestors are shared so an exclusive lock on ``/a`` conflicts with a later
    request for ``/a/b`` while disjoint sibling roots can coexist.  Requests for
    the same path are merged and exclusive mode wins.
    """

    modes: dict[Path, bool] = {}
    for root in roots:
        canonical = _canonical_existing_directory(root)
        for ancestor in _ancestor_paths(canonical):
            requested_exclusive = exclusive and ancestor == canonical
            modes[ancestor] = modes.get(ancestor, False) or requested_exclusive
    return tuple(
        ResourceLockRequest(path, modes[path])
        for path in sorted(modes, key=lambda item: (len(item.parts), item.as_posix()))
    )


@contextmanager
def resource_locks(
    roots: Iterable[Path],
    *,
    exclusive: bool,
) -> Iterator[None]:
    """Acquire one non-blocking cooperative hierarchical lock set.

    The locks coordinate Stream Archiver processes only.  They are advisory and
    do not constrain unrelated producers or other programs.
    """

    requests = build_resource_lock_requests(roots, exclusive=exclusive)
    owned: list[tuple[int, ResourceLockRequest]] = []
    try:
        for request in requests:
            flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            descriptor = os.open(request.path, flags)
            operation = fcntl.LOCK_EX if request.exclusive else fcntl.LOCK_SH
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(descriptor)
                mode = "exclusive" if request.exclusive else "shared"
                raise LockUnavailableError(
                    f"another Stream Archiver process owns a conflicting {mode} "
                    f"resource lock: {request.path}"
                ) from exc
            except BaseException:
                os.close(descriptor)
                raise
            owned.append((descriptor, request))
        log_event(
            LOGGER,
            logging.INFO,
            "resource_locks_acquired",
            "cooperative filesystem resource locks acquired",
            operation="lock",
            outcome="acquired",
            resources=[str(request.path) for request in requests],
            exclusive=exclusive,
        )
        yield
    finally:
        for descriptor, _request in reversed(owned):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def execution_lock(path: Path) -> Iterator[None]:
    """Provide the deprecated 0.3 lock-file API without making it authoritative.

    New CLI execution uses :func:`resource_locks`.  This compatibility helper is
    retained for third-party callers during the 0.4 alpha migration only.
    """

    warnings.warn(
        "execution_lock(path) is deprecated; lock-file paths do not define the 0.4 safety domain",
        DeprecationWarning,
        stacklevel=2,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockUnavailableError(f"another process owns legacy lock file {path}") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
