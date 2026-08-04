"""Configuration tests protect the declarative policy contract."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from stream_archiver.config import (
    CompressionCodec,
    CompressionRule,
    Policy,
    SymlinkRule,
    load_config,
)
from stream_archiver.errors import ConfigurationError


def test_load_config_shares_policy_settings_across_multiple_sources(tmp_path: Path) -> None:
    """One policy can reuse settings without merging the source roots."""

    destination = tmp_path / "archive"
    config_path = tmp_path / "policies.toml"
    config_path.write_text(
        f"""
schema_version = 2
run_interval = "30d"

[[policies]]
name = "reports"
sources = ["{tmp_path / 'reports-a'}", "{tmp_path / 'reports-b'}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "drop-aliases-preserve-relative"

[[policies.compression_rules]]
suffixes = [".json"]
compression = "gzip"

[[policies.compression_rules]]
suffixes = [".html"]
compression = "bz2"

[[policies]]
name = "logs"
sources = ["{tmp_path / 'logs'}"]
destination = "{destination}"
minimum_age = "6w"
stream_gap = "12h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.run_interval == timedelta(days=30)
    assert [policy.name for policy in config.policies] == ["reports", "logs"]
    reports = config.policies[0]
    assert reports.sources == (tmp_path / "reports-a", tmp_path / "reports-b")
    assert reports.destination == destination
    assert reports.compression_for(Path("nested/data.JSON")) is CompressionCodec.GZIP
    assert reports.compression_for(Path("nested/report.HTML")) is CompressionCodec.BZ2
    assert reports.compression_for(Path("image.png")) is None


def test_load_config_accepts_legacy_singular_source(tmp_path: Path) -> None:
    """Version-1 configuration remains loadable during migration to source lists."""

    path = tmp_path / "legacy.toml"
    path.write_text(
        f"""
schema_version = 1
[[policies]]
name = "legacy"
source = "{tmp_path / 'source'}"
destination = "{tmp_path / 'archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.policies[0].sources == (tmp_path / "source",)


def test_load_config_rejects_unsupported_compression(tmp_path: Path) -> None:
    """Only the explicitly implemented gzip and bzip2 codecs are accepted."""

    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "reports"
sources = ["{tmp_path / 'source'}"]
destination = "{tmp_path / 'archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
[[policies.compression_rules]]
suffixes = [".json"]
compression = "zstd"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="one of: gzip, bz2"):
        load_config(config_path)


def test_load_config_rejects_compression_level_override(tmp_path: Path) -> None:
    """Level 9 remains an invariant rather than mutable policy state."""

    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "reports"
sources = ["{tmp_path / 'source'}"]
destination = "{tmp_path / 'archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
[[policies.compression_rules]]
suffixes = [".json"]
compression = "gzip"
level = 1
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unknown keys: level"):
        load_config(config_path)


def test_load_config_requires_exactly_one_source_form(tmp_path: Path) -> None:
    """A policy cannot ambiguously define both singular and plural source keys."""

    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "reports"
source = "{tmp_path / 'source'}"
sources = ["{tmp_path / 'other'}"]
destination = "{tmp_path / 'archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="exactly one"):
        load_config(config_path)


def test_load_config_rejects_nested_source_and_destination(tmp_path: Path) -> None:
    """An archive cannot be placed below a source and recursively reselected."""

    source = tmp_path / "source"
    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "reports"
sources = ["{source}"]
destination = "{source / 'archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="disjoint"):
        load_config(config_path)


def test_load_config_rejects_overlapping_sources_across_policies(tmp_path: Path) -> None:
    """No two source roots may own the same file tree."""

    shared = tmp_path / "shared"
    config_path = tmp_path / "invalid-overlap.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "first"
sources = ["{shared}"]
destination = "{tmp_path / 'first-archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"

[[policies]]
name = "second"
sources = ["{shared / 'nested'}"]
destination = "{tmp_path / 'second-archive'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="pairwise disjoint"):
        load_config(config_path)


def test_load_config_allows_equal_destinations(tmp_path: Path) -> None:
    """Independent policies may intentionally write source-scoped archives together."""

    destination = tmp_path / "archive"
    config_path = tmp_path / "shared-destination.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "first"
sources = ["{tmp_path / 'first'}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"

[[policies]]
name = "second"
sources = ["{tmp_path / 'second'}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert {policy.destination for policy in config.policies} == {destination}


def test_load_config_rejects_nested_destination_roots(tmp_path: Path) -> None:
    """Shared destinations must be equal rather than invisibly nested."""

    destination = tmp_path / "archive"
    config_path = tmp_path / "nested-destination.toml"
    config_path.write_text(
        f"""
schema_version = 2
[[policies]]
name = "first"
sources = ["{tmp_path / 'first'}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"

[[policies]]
name = "second"
sources = ["{tmp_path / 'second'}"]
destination = "{destination / 'nested'}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="may be equal but must not be nested"):
        load_config(config_path)


def test_programmatic_compression_rule_rejects_unknown_codec() -> None:
    """Python callers receive the same codec restrictions as TOML callers."""

    with pytest.raises(ConfigurationError, match="one of: gzip, bz2"):
        CompressionRule((".json",), "zstd")


def test_programmatic_policy_rejects_untyped_symlink_rule(tmp_path: Path) -> None:
    """Programmatic construction cannot bypass the symlink-rule enum contract."""

    with pytest.raises(ConfigurationError, match="must be a SymlinkRule"):
        Policy(
            name="reports",
            sources=(tmp_path / "source",),
            destination=tmp_path / "archive",
            minimum_age=timedelta(days=30),
            stream_gap=timedelta(hours=8),
            symlink_rule="ignore",  # type: ignore[arg-type]
            compression_rules=(),
        )


def test_load_config_rejects_sources_that_alias_through_symlink(tmp_path: Path) -> None:
    """Canonical path resolution prevents two policies from owning one source tree."""

    source = tmp_path / "real-source"
    source.mkdir()
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    destination = tmp_path / "archive"
    config = tmp_path / "policies.toml"
    config.write_text(
        f'''\
schema_version = 2
run_interval = "30d"

[[policies]]
name = "first"
sources = ["{source}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"

[[policies]]
name = "second"
sources = ["{alias}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "ignore"
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="pairwise disjoint"):
        load_config(config)
