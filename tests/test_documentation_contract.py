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
    for module_info in pkgutil.walk_packages(
        stream_archiver.__path__, prefix="stream_archiver."
    ):
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
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                if ast.get_docstring(node) is None:
                    missing.append(f"{path.name}:{node.name}")

    assert missing == []
