"""Stream grouping tests protect cutoff and exact-gap boundary behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stream_archiver.model import Entry, EntryKind, FileIdentity
from stream_archiver.planning import select_eligible_streams, split_streams

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_cutoff_does_not_divide_one_continuous_stream() -> None:
    """Entries on both sides of the age cutoff stay together when their gap is small."""

    cutoff = NOW - timedelta(days=30)
    entries = (
        _entry("older.json", cutoff - timedelta(hours=1)),
        _entry("newer.json", cutoff + timedelta(hours=1)),
    )

    streams = split_streams(entries, minimum_gap=timedelta(hours=8))
    eligible = select_eligible_streams(streams, now=NOW, minimum_age=timedelta(days=30))

    assert len(streams) == 1
    assert eligible == ()


def test_exact_stream_gap_starts_a_new_eligible_stream() -> None:
    """A gap equal to the configured boundary separates old work from newer work."""

    cutoff = NOW - timedelta(days=30)
    entries = (
        _entry("old.json", cutoff - timedelta(hours=9)),
        _entry("new.json", cutoff - timedelta(hours=1)),
    )

    streams = split_streams(entries, minimum_gap=timedelta(hours=8))
    eligible = select_eligible_streams(streams, now=NOW, minimum_age=timedelta(days=30))

    assert len(streams) == 2
    assert len(eligible) == 2


def test_gap_one_nanosecond_below_boundary_does_not_split() -> None:
    """Nanosecond timestamps avoid accidental floating-point boundary changes."""

    first_ns = int(NOW.timestamp() * 1_000_000_000)
    gap_ns = 8 * 60 * 60 * 1_000_000_000
    entries = (
        _entry_ns("first", first_ns),
        _entry_ns("second", first_ns + gap_ns - 1),
    )

    streams = split_streams(entries, minimum_gap=timedelta(hours=8))

    assert len(streams) == 1


def _entry(name: str, when: datetime) -> Entry:
    return _entry_ns(name, int(when.timestamp() * 1_000_000_000))


def _entry_ns(name: str, mtime_ns: int) -> Entry:
    path = Path("/source") / name
    return Entry(
        relative_path=Path(name),
        absolute_path=path,
        kind=EntryKind.REGULAR,
        identity=FileIdentity(
            device=1,
            inode=abs(hash(name)),
            mode=0o100644,
            size=1,
            mtime_ns=mtime_ns,
        ),
    )


def test_age_cutoff_uses_exact_integer_nanoseconds() -> None:
    """An entry at the cutoff is eligible while one nanosecond newer is not."""

    cutoff_ns = int(NOW.timestamp()) * 1_000_000_000 - 30 * 24 * 60 * 60 * 1_000_000_000
    exact_stream = split_streams((_entry_ns("exact", cutoff_ns),), minimum_gap=timedelta(hours=8))
    newer_stream = split_streams(
        (_entry_ns("newer", cutoff_ns + 1),), minimum_gap=timedelta(hours=8)
    )

    assert (
        select_eligible_streams(exact_stream, now=NOW, minimum_age=timedelta(days=30))
        == exact_stream
    )
    assert select_eligible_streams(newer_stream, now=NOW, minimum_age=timedelta(days=30)) == ()


def test_stream_grouping_rejects_non_positive_gap() -> None:
    """Direct callers cannot turn every entry into an ambiguous zero-gap stream."""

    from stream_archiver.errors import PlanningError

    try:
        split_streams((_entry("one", NOW),), minimum_gap=timedelta(0))
    except PlanningError as exc:
        assert "must be positive" in str(exc)
    else:
        raise AssertionError("zero stream gap was accepted")
