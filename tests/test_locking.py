"""Cooperative resource-lock tests cover overlapping and disjoint filesystem roots."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from stream_archiver.errors import LockUnavailableError
from stream_archiver.locking import resource_locks


def _try_exclusive_lock(path: str, queue: object) -> None:
    """Attempt one lock in a child process and report whether it was acquired."""

    result_queue = queue
    try:
        with resource_locks((Path(path),), exclusive=True):
            result_queue.put("acquired")  # type: ignore[attr-defined]
    except LockUnavailableError:
        result_queue.put("blocked")  # type: ignore[attr-defined]


def test_nested_resource_root_conflicts_with_owned_ancestor(tmp_path: Path) -> None:
    """R-CONC-001: `/a` ownership conflicts with another process requesting `/a/b`."""

    parent = tmp_path / "a"
    child = parent / "b"
    child.mkdir(parents=True)
    context = mp.get_context("spawn")
    queue = context.Queue()

    with resource_locks((parent,), exclusive=True):
        process = context.Process(target=_try_exclusive_lock, args=(str(child), queue))
        process.start()
        process.join(10)

    assert process.exitcode == 0
    assert queue.get(timeout=2) == "blocked"


def test_disjoint_sibling_roots_can_be_owned_concurrently(tmp_path: Path) -> None:
    """R-CONC-001: shared ancestors do not serialize independent sibling resources."""

    first = tmp_path / "a" / "x"
    second = tmp_path / "a" / "y"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    context = mp.get_context("spawn")
    queue = context.Queue()

    with resource_locks((first,), exclusive=True):
        process = context.Process(target=_try_exclusive_lock, args=(str(second), queue))
        process.start()
        process.join(10)

    assert process.exitcode == 0
    assert queue.get(timeout=2) == "acquired"


def test_missing_lock_resource_is_visible_failure(tmp_path: Path) -> None:
    """Operational prerequisites fail visibly instead of becoming a skipped service run."""

    with pytest.raises(LockUnavailableError, match="unavailable"):
        with resource_locks((tmp_path / "missing",), exclusive=True):
            pass
