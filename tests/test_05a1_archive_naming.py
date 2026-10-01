"""0.5.0a1 tests protect archive-unit identity and configurable naming."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_archiver.cli import _plan_summary
from stream_archiver.config import Policy, SymlinkRule, load_config
from stream_archiver.errors import ConfigurationError, PlanningError, RecoveryError
from stream_archiver.naming import (
    ARCHIVE_UNIT_SUFFIX_HEX_LENGTH,
    DEFAULT_ARCHIVE_NAME_TEMPLATE,
    archive_unit_id,
    archive_unit_suffix,
    render_archive_name,
)
from stream_archiver.service import _run_policy_at, plan_policy, verify_destination
from stream_archiver.state import policy_fingerprint
from tests.helpers import write_at

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def test_archive_unit_identity_is_stable_and_unit_index_is_identity_input() -> None:
    """Future subdivision can distinguish units without invocation-local ordinals."""

    plan_id = "a" * 64

    first = archive_unit_id(plan_id, unit_index=0)
    same = archive_unit_id(plan_id, unit_index=0)
    second = archive_unit_id(plan_id, unit_index=1)

    assert first == same
    assert first != second
    assert len(first) == 64
    assert archive_unit_suffix(plan_id) == first[:ARCHIVE_UNIT_SUFFIX_HEX_LENGTH]


def test_default_current_name_omits_subseconds_and_carries_stable_suffix() -> None:
    """Human default precision is seconds while exact identity remains in the suffix."""

    start_ns = 1_759_147_200_123_456_789
    end_ns = start_ns + 3_661_987_654_321
    plan_id = "b" * 64

    name = render_archive_name(
        DEFAULT_ARCHIVE_NAME_TEMPLATE,
        start_mtime_ns=start_ns,
        end_mtime_ns=end_ns,
        plan_id=plan_id,
    )

    assert ".123456" not in name
    assert name.endswith(f"--sa-{archive_unit_suffix(plan_id)}")


def test_custom_template_uses_fixed_english_month_weekday_and_span() -> None:
    """Human calendar tokens do not depend on process locale."""

    start = datetime(2026, 9, 29, 21, 15, 16, tzinfo=UTC)
    end = start + timedelta(days=1, hours=2, minutes=3, seconds=4)
    template = "{start:%a-%d-%b-%Y_%H%M%S}--{end:%a-%d-%b-%Y}--{span:%Dd%Hh%Mm%Ss}"

    name = render_archive_name(
        template,
        start_mtime_ns=int(start.timestamp() * 1_000_000_000),
        end_mtime_ns=int(end.timestamp() * 1_000_000_000),
        plan_id="c" * 64,
    )

    assert name.startswith("Tue-29-Sep-2026_211516--Wed-30-Sep-2026--1d02h03m04s--sa-")


def test_subsecond_fields_are_explicit_template_opt_in() -> None:
    """Microsecond and nanosecond text appears only when the template requests it."""

    name = render_archive_name(
        "{start:%Y%m%dT%H%M%S.%fZ}--{end:%N}",
        start_mtime_ns=1_759_147_200_123_456_789,
        end_mtime_ns=1_759_147_200_987_654_321,
        plan_id="d" * 64,
    )

    assert ".123456Z" in name
    assert "987654321" in name


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("../{start}", "path separators"),
        ("{unknown}", "unsupported"),
        ("{start:%Z}", "directive"),
        ("{start!r}", "conversions"),
        ("prefix--sa-{start}", "reserved"),
    ],
)
def test_schema4_rejects_unsafe_or_ambiguous_templates(
    tmp_path: Path,
    template: str,
    message: str,
) -> None:
    """Configuration rejects template constructs outside the controlled grammar."""

    path = _write_config(tmp_path, schema_version=4, archive_name_template=template)

    with pytest.raises(ConfigurationError, match=message):
        load_config(path)


def test_schema4_supplies_current_default_while_schema3_preserves_legacy_mode(
    tmp_path: Path,
) -> None:
    """Schema migration is explicit; old schemas retain old persistent names."""

    current = load_config(_write_config(tmp_path / "current", schema_version=4))
    legacy = load_config(_write_config(tmp_path / "legacy", schema_version=3))

    assert current.policies[0].archive_name_template == DEFAULT_ARCHIVE_NAME_TEMPLATE
    assert legacy.policies[0].archive_name_template is None


def test_schema3_rejects_archive_name_template(tmp_path: Path) -> None:
    """A schema-3 file cannot silently opt into schema-4 namespace behavior."""

    path = _write_config(
        tmp_path,
        schema_version=3,
        archive_name_template="{start:%Y-%m-%d}",
    )

    with pytest.raises(ConfigurationError, match="unknown keys: archive_name_template"):
        load_config(path)


def test_planning_uses_custom_human_prefix_and_exposes_full_archive_unit_identity(
    tmp_path: Path,
) -> None:
    """One current logical stream receives a format-neutral unit identity and stable suffix."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    policy = _policy(
        source,
        destination,
        archive_name_template="{start:%a-%d-%b-%Y}--{span:%Dd%Hh%Mm%Ss}",
    )

    plan = plan_policy(policy, now=NOW).archive_plans[0]

    assert plan.archive_unit_index == 0
    assert plan.archive_unit_id == archive_unit_id(plan.plan_id)
    assert plan.archive_name.startswith("Thu-20-Aug-2026--0d00h00m00s--sa-")
    assert plan.archive_name.endswith(archive_unit_suffix(plan.plan_id))


def test_legacy_programmatic_policy_retains_historical_archive_name_shape(
    tmp_path: Path,
) -> None:
    """Programmatic callers keep legacy naming unless they opt into a template."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))

    plan = plan_policy(
        _policy(source, destination, archive_name_template=None), now=NOW
    ).archive_plans[0]

    assert "--sa-" not in plan.archive_name
    assert plan.archive_name.endswith(plan.plan_id[:10])


def test_policy_fingerprint_changes_when_archive_template_changes(tmp_path: Path) -> None:
    """Scheduled state becomes due when persistent archive naming policy changes."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    first = _policy(source, destination, archive_name_template="{start:%Y%m%d}")
    second = _policy(source, destination, archive_name_template="{start:%Y-%m-%d}")

    assert policy_fingerprint(first) != policy_fingerprint(second)


def test_current_archive_name_suffix_is_verified_against_manifest_plan_id(tmp_path: Path) -> None:
    """Renaming a current-format archive cannot detach its visible identity from its plan."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    result = _run_policy_at(
        _policy(source, destination, archive_name_template="{start:%Y%m%d}"),
        planning_time=NOW,
    )
    archive = result.archives[0].archive_directory
    wrong = archive.with_name(archive.name[:-1] + ("0" if archive.name[-1] != "0" else "1"))
    archive.rename(wrong)
    manifest_path = wrong / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_name"] = wrong.name
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(RecoveryError, match="identity suffix"):
        verify_destination(destination)


def test_malformed_current_manifest_plan_id_is_a_typed_recovery_failure(tmp_path: Path) -> None:
    """Malformed current identity metadata stays inside the recovery-error contract."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    result = _run_policy_at(
        _policy(source, destination, archive_name_template="{start:%Y%m%d}"),
        planning_time=NOW,
    )
    archive = result.archives[0].archive_directory
    manifest_path = archive / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plan_id"] = "not-a-sha256"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(RecoveryError, match="invalid plan_id"):
        verify_destination(destination)


def test_current_archive_shaped_directory_without_manifest_is_not_ignored(tmp_path: Path) -> None:
    """Destination audit recognizes configurable-name archives by their stable suffix shape."""

    destination = tmp_path / "archive"
    destination.mkdir()
    (destination / ("human-name--sa-" + "a" * ARCHIVE_UNIT_SUFFIX_HEX_LENGTH)).mkdir()

    with pytest.raises(RecoveryError, match="missing MANIFEST.json"):
        verify_destination(destination)


def test_machine_plan_exposes_archive_unit_identity_separately_from_name(tmp_path: Path) -> None:
    """R-ANAME-008: JSON-plan data carries full identity and deterministic unit index."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    planned = plan_policy(
        _policy(source, destination, archive_name_template="{start:%Y%m%d}"), now=NOW
    )

    data = _plan_summary(planned)
    archive = data["sources"][0]["archives"][0]  # type: ignore[index]

    assert archive["archive_unit_index"] == 0  # type: ignore[index]
    assert archive["archive_unit_id"] == planned.archive_plans[0].archive_unit_id  # type: ignore[index]
    assert archive["archive_name"] == planned.archive_plans[0].archive_name  # type: ignore[index]


def test_planning_rejects_rendered_name_over_destination_name_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-ANAME-004: destination filename limits are checked before execution."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.txt", b"payload", NOW - timedelta(days=40))
    monkeypatch.setattr("stream_archiver.naming.os.pathconf", lambda *_args: 30)

    with pytest.raises(PlanningError, match="filename limit"):
        plan_policy(
            _policy(
                source,
                destination,
                archive_name_template="human-prefix-that-is-intentionally-long-{start:%Y%m%d}",
            ),
            now=NOW,
        )


def _policy(
    source: Path,
    destination: Path,
    *,
    archive_name_template: str | None,
) -> Policy:
    return Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
        archive_name_template=archive_name_template,
    )


def _write_config(
    root: Path,
    *,
    schema_version: int,
    archive_name_template: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    destination = root / "archive"
    template_line = (
        f'archive_name_template = "{archive_name_template}"\n'
        if archive_name_template is not None
        else ""
    )
    path = root / "policies.toml"
    path.write_text(
        f'''schema_version = {schema_version}\nrun_interval = "30d"\n\n[[policies]]\nname = "reports"\nsources = ["{source}"]\ndestination = "{destination}"\nminimum_age = "30d"\nstream_gap = "8h"\nsymlink_rule = "ignore"\nrecursive = true\nstream_partition = "source-root"\n{template_line}''',
        encoding="utf-8",
    )
    return path
