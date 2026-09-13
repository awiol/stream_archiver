"""Due-state tests protect the non-calendar monthly scheduling behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stream_archiver.config import Policy, SymlinkRule
from stream_archiver.state import DueState, load_state, policy_fingerprint, save_state

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_policy_becomes_due_at_exact_interval() -> None:
    """The scheduler may check frequently but runs a policy only after 30 full days."""

    state = DueState({"reports": NOW - timedelta(days=30)})

    assert state.is_due("reports", now=NOW, interval=timedelta(days=30))
    assert not state.is_due(
        "reports",
        now=NOW - timedelta(microseconds=1),
        interval=timedelta(days=30),
    )


def test_state_round_trip_preserves_per_policy_success_times(tmp_path: Path) -> None:
    """Atomic JSON persistence retains independent timestamps for each policy."""

    path = tmp_path / "state.json"
    expected = DueState(
        {
            "reports": NOW,
            "logs": NOW - timedelta(days=2),
        }
    )

    save_state(path, expected)

    assert load_state(path) == expected


def test_load_state_rejects_naive_timestamp(tmp_path: Path) -> None:
    """State timestamps must not depend on the machine's local timezone."""

    path = tmp_path / "state.json"
    path.write_text(
        '{"state_format_version": 1, "last_success": {"reports": "2026-08-02T12:00:00"}}',
        encoding="utf-8",
    )

    from stream_archiver.errors import ExecutionError

    try:
        load_state(path)
    except ExecutionError as exc:
        assert "must include an offset" in str(exc)
    else:
        raise AssertionError("naive due-state timestamp was accepted")


def test_v1_state_is_readable_but_changed_policy_is_conservatively_due(tmp_path: Path) -> None:
    """Legacy timestamps do not suppress a run when no matching policy fingerprint exists."""

    path = tmp_path / "state.json"
    path.write_text(
        '{"state_format_version":1,"last_success":{"reports":"2026-08-02T12:00:00Z"}}',
        encoding="utf-8",
    )
    state = load_state(path)
    policy = _policy(tmp_path)

    assert state.is_due(
        "reports",
        now=NOW + timedelta(days=1),
        interval=timedelta(days=30),
        fingerprint=policy_fingerprint(policy),
    )


def test_policy_fingerprint_changes_when_archive_semantics_change(tmp_path: Path) -> None:
    """A selection/representation change cannot reuse a previous due-state identity."""

    first = _policy(tmp_path, stream_gap=timedelta(hours=8))
    second = _policy(tmp_path, stream_gap=timedelta(hours=9))

    assert policy_fingerprint(first) != policy_fingerprint(second)


def _policy(tmp_path: Path, *, stream_gap: timedelta = timedelta(hours=8)) -> Policy:
    source = (tmp_path / "source").resolve()
    destination = (tmp_path / "archive").resolve()
    return Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=stream_gap,
        symlink_rule=SymlinkRule.IGNORE,
        compression_rules=(),
    )
