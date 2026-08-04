"""Discovery tests ensure traversal failures cannot silently omit source data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stream_archiver.discovery import discover_entries
from stream_archiver.errors import ExecutionError


def test_walk_permission_error_fails_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable subtree is reported rather than treated as an empty directory."""

    source = tmp_path / "source"
    source.mkdir()

    def failing_walk(*args: Any, **kwargs: Any) -> list[object]:
        callback = kwargs["onerror"]
        callback(PermissionError("denied"))
        return []

    monkeypatch.setattr("stream_archiver.discovery.os.walk", failing_walk)

    with pytest.raises(ExecutionError, match="cannot traverse source directory"):
        discover_entries(source)
