"""Cooperative resource-lock tests cover overlapping and disjoint filesystem roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from stream_archiver.errors import LockUnavailableError
from stream_archiver.locking import resource_locks


def test_nested_resource_root_conflicts_with_owned_ancestor(tmp_path: Path) -> None:
    """R-CONC-001: `/a` ownership conflicts with an independent `/a/b` request.

    Each ``resource_locks`` call opens its own directory descriptors.  Linux
    ``flock`` associates locks with open file descriptions, so a second call in
    the same process exercises the same conflict relation without coupling this
    regression oracle to child-process startup or teardown.
    """

    parent = tmp_path / "a"
    child = parent / "b"
    child.mkdir(parents=True)

    with (
        resource_locks((parent,), exclusive=True),
        pytest.raises(LockUnavailableError, match="conflicting shared resource lock"),
        resource_locks((child,), exclusive=True),
    ):
        pytest.fail("nested resource unexpectedly acquired")


def test_disjoint_sibling_roots_can_be_owned_concurrently(tmp_path: Path) -> None:
    """R-CONC-001: independent sibling resource locks can coexist."""

    first = tmp_path / "a" / "x"
    second = tmp_path / "a" / "y"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    with resource_locks((first,), exclusive=True), resource_locks((second,), exclusive=True):
        pass


def test_missing_lock_resource_is_visible_failure(tmp_path: Path) -> None:
    """Operational prerequisites fail visibly instead of becoming a skipped service run."""

    with (
        pytest.raises(LockUnavailableError, match="unavailable"),
        resource_locks((tmp_path / "missing",), exclusive=True),
    ):
        pass
