"""Behavioral evidence for 0.4.0a3 run progress and capacity observations."""

from __future__ import annotations

import errno
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_archiver import executor
from stream_archiver.capacity import (
    MATERIAL_ABSOLUTE_BYTES,
    CapacitySnapshot,
    CapacityUnavailable,
    CapacityWarningTracker,
    allocated_tree_bytes,
    assess_unexpected_capacity_consumption,
    materiality_threshold,
    observe_capacity,
)
from stream_archiver.cli import main
from stream_archiver.config import CompressionCodec, CompressionRule, Policy, SymlinkRule
from stream_archiver.errors import ExecutionError
from stream_archiver.observability import configure_logging
from stream_archiver.presentation import format_bytes
from stream_archiver.progress import ExecutionProgressDelta, RunProgressTracker, RunTotals
from stream_archiver.service import _run_policies_at, _run_policy_at, plan_policy
from tests.helpers import write_at

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _capacity(destination: Path, free_bytes: int) -> CapacitySnapshot:
    """Build one deterministic capacity observation for a test destination."""

    return CapacitySnapshot(
        destination=destination,
        total_bytes=10 * 1024**3,
        free_bytes=free_bytes,
        observed_at=NOW,
    )


def _policy(
    source: Path,
    destination: Path,
    *,
    name: str = "reports",
    compression_rules: tuple[CompressionRule, ...] = (),
) -> Policy:
    """Create a deterministic policy used by a3 accounting tests."""

    return Policy(
        name=name,
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=compression_rules,
    )


def test_human_size_formatter_uses_decimal_si_units() -> None:
    """Human volume labels match the selected SI convention without changing bytes."""

    assert format_bytes(999) == "999 B"
    assert format_bytes(1_000) == "1.00 kB"
    assert format_bytes(9_990_000) == "9.99 MB"
    assert format_bytes(47_300_000_000) == "47.3 GB"


def test_progress_tracker_uses_monotonic_elapsed_and_exact_totals(tmp_path: Path) -> None:
    """Run progress keeps exact byte totals and non-decreasing injected elapsed time."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "a.bin", b"a" * 100, NOW - timedelta(days=40))
    plan = plan_policy(_policy(source, destination), now=NOW).archive_plans[0]
    clock_values = iter((100.0, 101.25, 100.5, 102.0, 103.0))
    tracker = RunProgressTracker(
        RunTotals.from_plans((plan,)),
        clock=lambda: next(clock_values),
    )

    tracker.begin_stream(1)
    first = tracker.record_delta(ExecutionProgressDelta(source_bytes_processed=50))
    second = tracker.snapshot()
    completed = tracker.complete_stream(plan)

    assert first is not None
    assert first.source_bytes_processed == 50
    assert second.elapsed_seconds >= first.elapsed_seconds
    assert completed.files_completed == 1
    assert completed.source_bytes_processed == 100
    assert completed.source_bytes_total == 100


def test_top_level_progress_aggregates_multiple_policies(tmp_path: Path) -> None:
    """One destructive invocation establishes one denominator across selected policies."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="json", stream=output)
    destination = tmp_path / "archive"
    destination.mkdir()
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    write_at(source_a / "a.bin", b"a" * 100, NOW - timedelta(days=40))
    write_at(source_b / "b.bin", b"b" * 200, NOW - timedelta(days=40))
    policies = (
        _policy(source_a, destination, name="a"),
        _policy(source_b, destination, name="b"),
    )

    results = _run_policies_at(
        policies,
        planning_time=NOW,
        capacity_observer=lambda path: _capacity(path, 10 * 1024**3),
    )

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    started = next(event for event in events if event["event"] == "run_progress_started")
    completed = next(event for event in events if event["event"] == "run_progress_completed")
    assert len(results) == 2
    assert started["stream_total"] == 2
    assert started["files_total"] == 2
    assert started["source_bytes_total"] == 300
    assert completed["stream_index"] == 2
    assert completed["files_completed"] == 2
    assert completed["source_bytes_processed"] == 300


def test_written_payload_bytes_use_compressed_object_size(tmp_path: Path) -> None:
    """Written-payload accounting records final gzip bytes instead of source bytes."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="json", stream=output)
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    source_size = 2 * 1024 * 1024
    write_at(source / "data.txt", b"x" * source_size, NOW - timedelta(days=40))
    policy = _policy(
        source,
        destination,
        compression_rules=(CompressionRule((".txt",), CompressionCodec.GZIP),),
    )

    result = _run_policy_at(
        policy,
        planning_time=NOW,
        capacity_observer=lambda path: _capacity(path, 10 * 1024**3),
    )

    payload = next(result.archives[0].archive_directory.glob("*.gz"))
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    completed = next(event for event in events if event["event"] == "run_progress_completed")
    assert completed["source_bytes_total"] == source_size
    assert completed["written_payload_bytes"] == payload.stat().st_size
    assert completed["written_payload_bytes"] < source_size


def test_observe_capacity_uses_nearest_existing_ancestor_and_available_blocks(
    tmp_path: Path,
) -> None:
    """A missing destination is observed through its existing filesystem ancestor."""

    destination = tmp_path / "missing" / "archive"
    probes: list[Path] = []

    def statvfs(path: object) -> SimpleNamespace:
        probes.append(Path(path))
        return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=100, f_bavail=25)

    observation = observe_capacity(destination, statvfs=statvfs, clock=lambda: NOW)

    assert isinstance(observation, CapacitySnapshot)
    assert probes == [tmp_path]
    assert observation.total_bytes == 409_600
    assert observation.free_bytes == 102_400


def test_observe_capacity_failure_is_explicit(tmp_path: Path) -> None:
    """A failed statvfs observation remains unavailable rather than zero capacity."""

    destination = tmp_path / "archive"

    def statvfs(path: object) -> object:
        raise PermissionError(errno.EACCES, "permission denied", path)

    observation = observe_capacity(destination, statvfs=statvfs, clock=lambda: NOW)

    assert isinstance(observation, CapacityUnavailable)
    assert observation.destination == destination
    assert "PermissionError" in observation.reason
    assert "permission denied" in observation.reason


def test_capacity_warning_tracker_deduplicates_and_reenters() -> None:
    """Low-capacity warnings follow semantic state rather than time or stream count."""

    destination = Path("/archive")
    tracker = CapacityWarningTracker()
    remaining = 2 * 1024**3

    first = tracker.evaluate(
        destination, _capacity(destination, 1 * 1024**3), remaining_source_bytes=remaining
    )
    duplicate = tracker.evaluate(
        destination, _capacity(destination, 1 * 1024**3), remaining_source_bytes=remaining
    )
    sufficient = tracker.evaluate(
        destination, _capacity(destination, 3 * 1024**3), remaining_source_bytes=remaining
    )
    reentered = tracker.evaluate(
        destination, _capacity(destination, 1 * 1024**3), remaining_source_bytes=remaining
    )

    assert first.warn is True
    assert first.reason == "insufficient-capacity"
    assert duplicate.warn is False
    assert sufficient.warn is False
    assert reentered.warn is True
    assert reentered.reason == "insufficient-capacity"


def test_capacity_warning_tracker_rewarns_only_after_material_worsening() -> None:
    """An insufficient state warns again only after the documented dual threshold."""

    destination = Path("/archive")
    tracker = CapacityWarningTracker()
    remaining = 4 * 1024**3
    first_free = 3 * 1024**3
    first = tracker.evaluate(
        destination, _capacity(destination, first_free), remaining_source_bytes=remaining
    )
    small_loss = tracker.evaluate(
        destination,
        _capacity(destination, first_free - MATERIAL_ABSOLUTE_BYTES // 2),
        remaining_source_bytes=remaining,
    )
    large_loss = tracker.evaluate(
        destination,
        _capacity(destination, first_free - 128 * 1024**2),
        remaining_source_bytes=remaining,
    )

    assert first.warn is True
    assert small_loss.warn is False
    assert large_loss.warn is True
    assert large_loss.reason == "materially-worse-capacity"


def test_capacity_unavailable_warns_once_until_observation_recovers() -> None:
    """Unavailable advisory capacity is explicit without repeated identical warnings."""

    destination = Path("/archive")
    tracker = CapacityWarningTracker()
    unavailable = CapacityUnavailable(destination, NOW, "OSError: unavailable")

    first = tracker.evaluate(destination, unavailable, remaining_source_bytes=100)
    duplicate = tracker.evaluate(destination, unavailable, remaining_source_bytes=100)
    tracker.evaluate(destination, _capacity(destination, 1000), remaining_source_bytes=100)
    again = tracker.evaluate(destination, unavailable, remaining_source_bytes=100)

    assert first.warn is True
    assert first.reason == "capacity-unavailable"
    assert duplicate.warn is False
    assert again.warn is True


def test_allocation_measurement_failure_is_advisory(tmp_path: Path) -> None:
    """Missing diagnostic state returns unavailable instead of failing execution logic."""

    assert allocated_tree_bytes(tmp_path / "missing-archive") is None


def test_unexpected_capacity_consumption_uses_absolute_and_relative_threshold() -> None:
    """Residual loss is diagnostic only when it clears both noise controls."""

    known = 2 * 1024**3
    threshold = materiality_threshold(known)
    below = assess_unexpected_capacity_consumption(
        free_before_bytes=5 * 1024**3,
        free_after_bytes=5 * 1024**3 - known - threshold + 1,
        known_allocation_bytes=known,
    )
    at = assess_unexpected_capacity_consumption(
        free_before_bytes=5 * 1024**3,
        free_after_bytes=5 * 1024**3 - known - threshold,
        known_allocation_bytes=known,
    )

    assert threshold >= MATERIAL_ABSOLUTE_BYTES
    assert threshold >= known // 20
    assert below.material is False
    assert at.material is True
    assert at.residual_bytes == threshold


def test_plan_human_and_json_capacity_warning_preserve_exact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plan output shows a low-space warning while JSON retains exact byte values."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.bin", b"x" * 2_000, NOW - timedelta(days=40))
    config = tmp_path / "policies.toml"
    config.write_text(
        "\n".join(
            (
                "schema_version = 2",
                "[[policies]]",
                'name = "reports"',
                f'sources = ["{source}"]',
                f'destination = "{destination}"',
                'minimum_age = "30d"',
                'stream_gap = "8h"',
                'symlink_rule = "ignore"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "stream_archiver.cli.observe_capacity",
        lambda path: _capacity(path, 1_000),
    )

    assert main(["--config", str(config), "plan", "--at", NOW.isoformat()]) == 0
    human = capsys.readouterr()
    assert "WARNING" in human.out
    assert "2.00 kB" in human.out
    assert "1.00 kB" in human.out

    assert main(["--config", str(config), "plan", "--at", NOW.isoformat(), "--json"]) == 0
    machine = capsys.readouterr()
    payload = json.loads(machine.out)
    capacity = payload[0]["destination_capacity"]
    assert capacity["selected_source_bytes"] == 2_000
    assert capacity["free_bytes"] == 1_000
    assert capacity["insufficient"] is True


def test_execution_rechecks_capacity_before_every_stream_without_duplicate_warning(
    tmp_path: Path,
) -> None:
    """Execution samples each stream and does not repeat one unchanged low-space warning."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="json", stream=output)
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "a.bin", b"a" * 100, NOW - timedelta(days=42))
    write_at(source / "b.bin", b"b" * 100, NOW - timedelta(days=40))
    observations: list[int] = []

    def observer(path: Path) -> CapacitySnapshot:
        observations.append(1)
        return _capacity(path, 50)

    _run_policy_at(
        _policy(source, destination),
        planning_time=NOW,
        capacity_observer=observer,
    )

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    warnings = [event for event in events if event["event"] == "destination_capacity_low"]
    assert len(observations) == 4  # before and after each of two streams
    assert len(warnings) == 1
    assert warnings[0]["remaining_source_bytes"] == 200


def test_material_unexpected_capacity_loss_logs_without_external_writer_claim(
    tmp_path: Path,
) -> None:
    """A material residual uses neutral unexpected-consumption wording and exact fields."""

    output = io.StringIO()
    configure_logging(level="INFO", format_name="json", stream=output)
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    write_at(source / "data.bin", b"x" * 1024, NOW - timedelta(days=40))
    free_values = iter((2 * 1024**3, 2 * 1024**3 - 256 * 1024**2))

    _run_policy_at(
        _policy(source, destination),
        planning_time=NOW,
        capacity_observer=lambda path: _capacity(path, next(free_values)),
    )

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    warning = next(
        event for event in events if event["event"] == "unexpected_destination_capacity_consumption"
    )
    assert warning["unexpected_capacity_consumption_bytes"] >= MATERIAL_ABSOLUTE_BYTES
    assert "external writer" not in warning["message"].lower()
    assert warning["message"] == "unexpected destination capacity consumption observed"


def test_enospc_after_sufficient_precheck_preserves_source_and_fails_normally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful advisory precheck cannot convert a later ENOSPC into completion."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    destination.mkdir()
    source_file = write_at(source / "data.bin", b"x" * 1024, NOW - timedelta(days=40))

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(executor, "_copy_and_hash", no_space)

    with pytest.raises(ExecutionError, match="filesystem operation failed"):
        _run_policy_at(
            _policy(source, destination),
            planning_time=NOW,
            capacity_observer=lambda path: _capacity(path, 10 * 1024**3),
        )

    assert source_file.exists()
    assert not any(path.name != ".stream-archiver-staging" for path in destination.iterdir())
