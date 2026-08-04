"""Typed TOML configuration and validation for archival policies."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from stream_archiver.durations import parse_duration
from stream_archiver.errors import ConfigurationError


class SymlinkRule(StrEnum):
    """Supported treatment of symbolic links selected with a file stream."""

    DROP_ALIASES_PRESERVE_RELATIVE = "drop-aliases-preserve-relative"
    PRESERVE_RELATIVE = "preserve-relative"
    IGNORE = "ignore"


class CompressionCodec(StrEnum):
    """Supported per-file compression formats."""

    GZIP = "gzip"
    BZ2 = "bz2"

    @property
    def archive_suffix(self) -> str:
        """Return the filename suffix appended to a compressed payload."""

        if self is CompressionCodec.GZIP:
            return ".gz"
        return ".bz2"


@dataclass(frozen=True, slots=True)
class CompressionRule:
    """Compress matching filename suffixes with gzip or bzip2 level 9.

    ``suffixes`` are matched case-insensitively against the complete filename.
    Compression level 9 is a package invariant and is not configurable.
    """

    suffixes: tuple[str, ...]
    compression: CompressionCodec | str = CompressionCodec.GZIP

    def __post_init__(self) -> None:
        """Normalize the codec and reject ambiguous or unsafe suffix rules."""

        try:
            codec = CompressionCodec(self.compression)
        except (TypeError, ValueError) as exc:
            choices = ", ".join(item.value for item in CompressionCodec)
            raise ConfigurationError(f"compression must be one of: {choices}") from exc
        object.__setattr__(self, "compression", codec)

        if not isinstance(self.suffixes, tuple) or not self.suffixes:
            raise ConfigurationError("compression suffixes must be a non-empty tuple")
        normalized: set[str] = set()
        for suffix in self.suffixes:
            if (
                not isinstance(suffix, str)
                or len(suffix) < 2
                or not suffix.startswith(".")
                or "/" in suffix
                or "\\" in suffix
            ):
                raise ConfigurationError(
                    "compression suffixes must begin with '.' and contain no path separator"
                )
            key = suffix.casefold()
            if key in normalized:
                raise ConfigurationError(f"compression suffix is repeated: {suffix!r}")
            normalized.add(key)

    def matches(self, path: Path) -> bool:
        """Return whether ``path`` must be compressed by this rule."""

        name = path.name.casefold()
        return any(name.endswith(suffix.casefold()) for suffix in self.suffixes)


@dataclass(frozen=True, slots=True)
class Policy:
    """Shared archival decisions applied independently to one or more sources.

    Sources share the destination, age, stream-gap, symlink, and compression
    settings, but discovery and stream planning are performed separately for
    every source. A stream can therefore never span source roots.
    """

    name: str
    sources: tuple[Path, ...]
    destination: Path
    minimum_age: timedelta
    stream_gap: timedelta
    symlink_rule: SymlinkRule
    compression_rules: tuple[CompressionRule, ...]

    def __post_init__(self) -> None:
        """Reject unsafe programmatic policies before planning or execution."""

        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise ConfigurationError(
                "policy name must be non-empty and have no surrounding whitespace"
            )
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ConfigurationError("policy sources must be a non-empty tuple")
        if not all(isinstance(source, Path) and source.is_absolute() for source in self.sources):
            raise ConfigurationError("every policy source must be an absolute Path")
        if not isinstance(self.destination, Path) or not self.destination.is_absolute():
            raise ConfigurationError("policy destination must be an absolute Path")

        normalized_sources = tuple(source.resolve(strict=False) for source in self.sources)
        normalized_destination = self.destination.resolve(strict=False)
        object.__setattr__(self, "sources", normalized_sources)
        object.__setattr__(self, "destination", normalized_destination)

        if len(normalized_sources) != len(set(normalized_sources)):
            raise ConfigurationError("policy sources must be unique after path resolution")
        for source in normalized_sources:
            _validate_disjoint_paths(source, normalized_destination, f"policy {self.name!r}")
        _validate_source_roots(
            tuple((self.name, source) for source in normalized_sources),
            context=f"policy {self.name!r}",
        )
        if not isinstance(self.minimum_age, timedelta) or self.minimum_age <= timedelta(0):
            raise ConfigurationError("policy minimum_age must be a positive timedelta")
        if not isinstance(self.stream_gap, timedelta) or self.stream_gap <= timedelta(0):
            raise ConfigurationError("policy stream_gap must be a positive timedelta")
        if not isinstance(self.symlink_rule, SymlinkRule):
            raise ConfigurationError("policy symlink_rule must be a SymlinkRule")
        if not isinstance(self.compression_rules, tuple) or not all(
            isinstance(rule, CompressionRule) for rule in self.compression_rules
        ):
            raise ConfigurationError(
                "policy compression_rules must be a tuple of CompressionRule objects"
            )
        _validate_suffix_ownership(self.compression_rules, f"policy {self.name!r}")

    def compression_for(self, relative_path: Path) -> CompressionCodec | None:
        """Return the configured codec for ``relative_path``, if any."""

        for rule in self.compression_rules:
            if rule.matches(relative_path):
                return CompressionCodec(rule.compression)
        return None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration shared by scheduled invocations."""

    run_interval: timedelta
    policies: tuple[Policy, ...]

    def __post_init__(self) -> None:
        """Validate scheduling and cross-policy path ownership for Python callers."""

        if not isinstance(self.run_interval, timedelta) or self.run_interval <= timedelta(0):
            raise ConfigurationError("run_interval must be a positive timedelta")
        if not isinstance(self.policies, tuple) or not self.policies:
            raise ConfigurationError("policies must be a non-empty tuple")
        if not all(isinstance(policy, Policy) for policy in self.policies):
            raise ConfigurationError("policies must contain only Policy objects")
        names = [policy.name for policy in self.policies]
        if len(names) != len(set(names)):
            raise ConfigurationError("policy names must be unique")
        _validate_policy_roots(self.policies)


_TOP_LEVEL_KEYS = frozenset({"schema_version", "run_interval", "policies"})
_POLICY_KEYS = frozenset(
    {
        "name",
        "source",
        "sources",
        "destination",
        "minimum_age",
        "stream_gap",
        "symlink_rule",
        "compression_rules",
    }
)
_COMPRESSION_KEYS = frozenset({"suffixes", "compression"})
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})


def load_config(path: Path) -> AppConfig:
    """Load and validate a TOML configuration file.

    Schema version 2 documents the preferred ``sources = [...]`` form and both
    gzip and bzip2. Version 1 and its singular ``source`` key remain accepted to
    ease upgrades from stream-archiver 0.1.0.
    """

    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc

    _reject_unknown(raw, _TOP_LEVEL_KEYS, context="configuration")
    schema_version = raw.get("schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        choices = ", ".join(str(item) for item in sorted(_SUPPORTED_SCHEMA_VERSIONS))
        raise ConfigurationError(f"schema_version must be one of: {choices}")

    run_interval = parse_duration(raw.get("run_interval", "30d"), field="run_interval")
    raw_policies = raw.get("policies")
    if not isinstance(raw_policies, list) or not raw_policies:
        raise ConfigurationError("configuration must contain at least one [[policies]] table")

    policies = tuple(_parse_policy(item, index) for index, item in enumerate(raw_policies))
    return AppConfig(run_interval=run_interval, policies=policies)


def _validate_policy_roots(policies: tuple[Policy, ...]) -> None:
    """Protect source ownership while permitting a shared destination root."""

    sources = tuple(
        (policy.name, source)
        for policy in policies
        for source in policy.sources
    )
    _validate_source_roots(sources, context="configuration")

    destinations = tuple((policy.name, policy.destination) for policy in policies)
    for source_name, source in sources:
        for destination_name, destination in destinations:
            if _paths_overlap(source, destination):
                raise ConfigurationError(
                    "source and destination roots must be disjoint: "
                    f"{source_name}.sources contains {source}, while "
                    f"{destination_name}.destination is {destination}"
                )

    for index, (first_name, first_path) in enumerate(destinations):
        for second_name, second_path in destinations[index + 1 :]:
            if first_path == second_path:
                continue
            if first_path in second_path.parents or second_path in first_path.parents:
                raise ConfigurationError(
                    "destination roots may be equal but must not be nested: "
                    f"{first_name}.destination={first_path}, "
                    f"{second_name}.destination={second_path}"
                )


def _validate_source_roots(
    sources: tuple[tuple[str, Path], ...], *, context: str
) -> None:
    for index, (first_name, first_path) in enumerate(sources):
        for second_name, second_path in sources[index + 1 :]:
            if _paths_overlap(first_path, second_path):
                raise ConfigurationError(
                    f"{context} source roots must be pairwise disjoint: "
                    f"{first_name}={first_path} overlaps {second_name}={second_path}"
                )


def _parse_policy(raw: Any, index: int) -> Policy:
    context = f"policies[{index}]"
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{context} must be a table")
    _reject_unknown(raw, _POLICY_KEYS, context=context)

    name = _required_nonempty_string(raw, "name", context)
    sources = _source_paths(raw, context)
    destination = _absolute_path(raw, "destination", context)

    minimum_age = parse_duration(
        _required_nonempty_string(raw, "minimum_age", context),
        field=f"{context}.minimum_age",
    )
    stream_gap = parse_duration(
        _required_nonempty_string(raw, "stream_gap", context),
        field=f"{context}.stream_gap",
    )

    symlink_value = _required_nonempty_string(raw, "symlink_rule", context)
    try:
        symlink_rule = SymlinkRule(symlink_value)
    except ValueError as exc:
        choices = ", ".join(rule.value for rule in SymlinkRule)
        raise ConfigurationError(
            f"{context}.symlink_rule must be one of: {choices}"
        ) from exc

    compression_rules = _parse_compression_rules(raw.get("compression_rules", []), context)
    return Policy(
        name=name,
        sources=sources,
        destination=destination,
        minimum_age=minimum_age,
        stream_gap=stream_gap,
        symlink_rule=symlink_rule,
        compression_rules=compression_rules,
    )


def _source_paths(raw: dict[str, Any], context: str) -> tuple[Path, ...]:
    has_source = "source" in raw
    has_sources = "sources" in raw
    if has_source == has_sources:
        raise ConfigurationError(
            f"{context} must define exactly one of 'source' or 'sources'"
        )
    if has_source:
        return (_absolute_path(raw, "source", context),)

    values = raw.get("sources")
    if not isinstance(values, list) or not values:
        raise ConfigurationError(f"{context}.sources must be a non-empty string array")
    paths: list[Path] = []
    for item_index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(
                f"{context}.sources[{item_index}] must be a non-empty string"
            )
        paths.append(_expanded_absolute_path(value, f"{context}.sources[{item_index}]"))
    return tuple(paths)


def _parse_compression_rules(raw: Any, policy_context: str) -> tuple[CompressionRule, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError(f"{policy_context}.compression_rules must be an array of tables")

    rules: list[CompressionRule] = []
    for index, item in enumerate(raw):
        context = f"{policy_context}.compression_rules[{index}]"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{context} must be a table")
        _reject_unknown(item, _COMPRESSION_KEYS, context=context)

        compression = item.get("compression")
        try:
            codec = CompressionCodec(compression)
        except (TypeError, ValueError) as exc:
            choices = ", ".join(codec.value for codec in CompressionCodec)
            raise ConfigurationError(f"{context}.compression must be one of: {choices}") from exc

        suffixes_raw = item.get("suffixes")
        if not isinstance(suffixes_raw, list) or not suffixes_raw:
            raise ConfigurationError(f"{context}.suffixes must be a non-empty string array")
        rules.append(CompressionRule(tuple(suffixes_raw), codec))

    result = tuple(rules)
    _validate_suffix_ownership(result, policy_context)
    return result


def _required_nonempty_string(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _absolute_path(raw: dict[str, Any], key: str, context: str) -> Path:
    value = _required_nonempty_string(raw, key, context)
    return _expanded_absolute_path(value, f"{context}.{key}")


def _expanded_absolute_path(value: str, context: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        raise ConfigurationError(f"{context} must be an absolute path")
    return path.resolve(strict=False)


def _validate_disjoint_paths(source: Path, destination: Path, context: str) -> None:
    if _paths_overlap(source, destination):
        raise ConfigurationError(
            f"{context} source and destination must be disjoint, non-nested paths"
        )


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _validate_suffix_ownership(
    rules: tuple[CompressionRule, ...], policy_context: str
) -> None:
    owners: dict[str, int] = {}
    for rule_index, rule in enumerate(rules):
        for suffix in rule.suffixes:
            key = suffix.casefold()
            if key in owners:
                raise ConfigurationError(
                    f"{policy_context}.compression_rules repeats suffix {suffix!r}"
                )
            owners[key] = rule_index


def _reject_unknown(raw: dict[str, Any], allowed: frozenset[str], *, context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigurationError(f"{context} contains unknown keys: {', '.join(unknown)}")
