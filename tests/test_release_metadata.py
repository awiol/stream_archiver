"""Release metadata tests keep the package and runtime version contract aligned."""

from __future__ import annotations

import tomllib
from pathlib import Path

import stream_archiver


def test_project_version_matches_runtime_version() -> None:
    """The build metadata and imported package expose the same release version."""

    project_root = Path(__file__).parents[1]
    data = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["version"] == stream_archiver.__version__
