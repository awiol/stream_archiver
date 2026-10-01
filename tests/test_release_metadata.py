"""Release metadata tests keep candidate identity and generated metadata aligned."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import stream_archiver

_PROJECT_ROOT = Path(__file__).parents[1]


def _project_version() -> str:
    """Return the authoritative package version from build metadata."""

    data = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _document_version(package_version: str) -> str:
    """Map the project's PEP 440 prerelease spelling to document SemVer spelling."""

    match = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", package_version)
    if match is None:
        return package_version
    core, stage, ordinal = match.groups()
    label = {"a": "alpha", "b": "beta", "rc": "rc"}[stage]
    return f"{core}-{label}.{ordinal}"


def _metadata_field(path: Path, field: str) -> str:
    """Read one field from generated Core Metadata text."""

    prefix = f"{field}: "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    raise AssertionError(f"missing {field} field in {path}")


def _normalized_requires_python(value: str) -> tuple[str, ...]:
    """Normalize equivalent comma-separated Python specifier ordering."""

    return tuple(sorted(part.strip() for part in value.split(",") if part.strip()))


def test_current_release_identity_is_consistent_across_authoritative_surfaces() -> None:
    """R-REL-001: all maintained current-version surfaces derive from pyproject identity."""

    version = _project_version()
    document_version = _document_version(version)

    assert stream_archiver.__version__ == version

    lock = tomllib.loads((_PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    local = [item for item in lock["package"] if item["name"] == "stream-archiver"]
    assert [item["version"] for item in local] == [version]

    requirements = (_PROJECT_ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    design = (_PROJECT_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    assert f"**Document version:** `{document_version}`" in requirements
    assert f"**Target package development version:** `{version}`" in requirements
    assert f"**Document version:** `{document_version}`" in design
    assert f"**Target package development version:** `{version}`" in design
    assert f"document version `{document_version}`" in design

    changelog = (_PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_release = next(line for line in changelog.splitlines() if line.startswith("## "))
    assert first_release.startswith(f"## {version} ")


def test_generated_core_metadata_matches_project_version_when_present() -> None:
    """R-REL-001: generated sdist/egg-info metadata cannot carry a stale candidate version."""

    version = _project_version()
    candidates = [
        _PROJECT_ROOT / "PKG-INFO",
        _PROJECT_ROOT / "src" / "stream_archiver.egg-info" / "PKG-INFO",
    ]
    present = [path for path in candidates if path.is_file()]

    project = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert all(_metadata_field(path, "Version") == version for path in present)
    expected_python = _normalized_requires_python(project["requires-python"])
    assert all(
        _normalized_requires_python(_metadata_field(path, "Requires-Python")) == expected_python
        for path in present
    )


def test_sdist_manifest_declares_the_source_user_contract() -> None:
    """R-DIST-001: packaging policy names every source-user artifact family."""

    manifest = (_PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    required_fragments = (
        "include CHANGELOG.md",
        "include uv.lock",
        "recursive-include docs *.md",
        "recursive-include examples *",
        "recursive-include tools *.sh *.py",
        "recursive-include tests *.py",
        "prune local",
    )

    assert all(fragment in manifest for fragment in required_fragments)
