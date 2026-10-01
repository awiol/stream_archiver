"""Documentation checks preserve reviewable intent in public code and tests."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import stream_archiver


def test_public_package_objects_have_explanatory_docstrings() -> None:
    """Public classes and functions remain discoverable without reading internals."""

    missing: list[str] = []
    for module_info in pkgutil.walk_packages(stream_archiver.__path__, prefix="stream_archiver."):
        module = importlib.import_module(module_info.name)
        for name, value in vars(module).items():
            if name.startswith("_") or getattr(value, "__module__", None) != module.__name__:
                continue
            if (inspect.isclass(value) or inspect.isfunction(value)) and not inspect.getdoc(value):
                missing.append(f"{module.__name__}.{name}")

    assert missing == []


def test_behavior_tests_explain_setup_and_contract() -> None:
    """Every test function carries a concise explanation of the claim it protects."""

    tests_root = Path(__file__).parent
    missing: list[str] = []
    for path in sorted(tests_root.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                and ast.get_docstring(node) is None
            ):
                missing.append(f"{path.name}:{node.name}")

    assert missing == []


def test_canonical_current_example_parses_with_explicit_discovery_controls() -> None:
    """The user-facing example exercises the current configuration schema and controls."""

    from stream_archiver.config import StreamPartition, load_config

    root = Path(__file__).parents[1]
    config = load_config(root / "examples" / "config" / "policies.toml")

    assert all(policy.recursive for policy in config.policies)
    assert config.policies[0].stream_partition is StreamPartition.SOURCE_ROOT
    assert config.policies[1].stream_partition is StreamPartition.PARENT_DIRECTORY


def test_requirement_identifiers_are_unique() -> None:
    """DCR traceability requires one semantic meaning for every maintained requirement ID."""

    import re

    requirements = (Path(__file__).parents[1] / "docs" / "requirements.md").read_text(
        encoding="utf-8"
    )
    identifiers = re.findall(r"(?m)^###\s+(R-[A-Z0-9-]+)\b", requirements)

    assert len(identifiers) == len(set(identifiers))


def test_every_requirement_family_is_routed_by_design_traceability() -> None:
    """R-DOC-002: each maintained requirement family has a design route."""

    import re

    root = Path(__file__).parents[1]
    requirements = (root / "docs" / "requirements.md").read_text(encoding="utf-8")
    design = (root / "docs" / "design.md").read_text(encoding="utf-8")
    families = set(re.findall(r"(?m)^###\s+(R-[A-Z]+)-\d+\b", requirements))
    traceability = design.split("## 16. Requirement-to-design traceability", 1)[1].split(
        "## 17.", 1
    )[0]
    routed = set(re.findall(r"R-[A-Z]+", traceability))

    assert families <= routed


def test_source_user_artifacts_referenced_by_the_distribution_contract_exist() -> None:
    """R-DIST-001: a source-user checkout/sdist carries its local docs, tools, and examples."""

    root = Path(__file__).parents[1]
    required = (
        "CHANGELOG.md",
        "uv.lock",
        "docs/requirements.md",
        "docs/design.md",
        "docs/verification.md",
        "docs/operations.md",
        "docs/user-guide.md",
        "examples/config/policies.toml",
        "tools/bootstrap-dev.sh",
        "tools/install-systemd.sh",
        "tools/verify-local.sh",
        "tools/verify-systemd.py",
        "tests/helpers.py",
    )

    assert [relative for relative in required if not (root / relative).is_file()] == []
