"""Behavioral evidence for 0.4.0a4 final run reporting and rendering."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stream_archiver import executor
from stream_archiver.cli import main
from stream_archiver.config import CompressionCodec, CompressionRule, Policy, SymlinkRule
from stream_archiver.errors import ExecutionError
from stream_archiver.reporting import (
    ExtensionBreakdown,
    PayloadBreakdown,
    PayloadTransformation,
    RunReport,
    TransformationBreakdown,
    build_run_report,
    render_run_markdown,
    render_run_summary,
    run_report_data,
)
from stream_archiver.service import _run_policy_at
from tests.helpers import symlink_at, write_at

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=40)


def _policy(source: Path, destination: Path) -> Policy:
    """Create a mixed-transformation policy for final-report tests."""

    destination.mkdir(parents=True, exist_ok=True)
    return Policy(
        name="reports",
        sources=(source,),
        destination=destination,
        minimum_age=timedelta(days=30),
        stream_gap=timedelta(hours=8),
        symlink_rule=SymlinkRule.DROP_ALIASES_PRESERVE_RELATIVE,
        compression_rules=(
            CompressionRule((".log",), CompressionCodec.GZIP),
            CompressionRule((".html",), CompressionCodec.BZ2),
        ),
    )


def _write_cli_config(tmp_path: Path, source: Path, destination: Path) -> Path:
    """Write one schema-3 CLI policy with deterministic old-file selection."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "policies.toml"
    path.write_text(
        f"""
schema_version = 3
run_interval = "30d"

[[policies]]
name = "reports"
sources = ["{source}"]
destination = "{destination}"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "drop-aliases-preserve-relative"
recursive = true
stream_partition = "source-root"

[[policies.compression_rules]]
suffixes = [".log"]
compression = "gzip"
""",
        encoding="utf-8",
    )
    return path


def test_run_report_aggregates_mixed_payload_and_link_dispositions(tmp_path: Path) -> None:
    """The result model reports exact source/written bytes and each link disposition."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "service.LOG", b"x" * 20_000, OLD)
    write_at(source / "page.html", b"<p>same</p>\n" * 800, OLD + timedelta(minutes=1))
    write_at(source / "README", b"plain payload", OLD + timedelta(minutes=2))
    symlink_at(source / "service-alias.log", "service.LOG", OLD + timedelta(minutes=3))
    symlink_at(source / "relative-link", "missing.txt", OLD + timedelta(minutes=4))
    symlink_at(source / "absolute-link", "/etc/hosts", OLD + timedelta(minutes=5))

    result = _run_policy_at(_policy(source, destination), planning_time=NOW)
    report = build_run_report((result,), elapsed_seconds=12.5)

    assert report.elapsed_seconds == 12.5
    assert report.logical_streams_selected == 1
    assert report.archive_units_completed == 1
    assert report.regular_files == 3
    assert report.total_links == 2
    assert report.preserved_symlinks == 1
    assert report.dropped_alias_symlinks == 1
    assert report.skipped_symlinks == 0
    assert report.source_bytes == 20_000 + len(b"<p>same</p>\n" * 800) + len(b"plain payload")
    assert report.written_payload_bytes == sum(
        group.written_payload_bytes for group in result.archives[0].regular_payload_groups
    )
    assert {item.transformation for item in report.transformations} == {
        PayloadTransformation.UNCOMPRESSED,
        PayloadTransformation.GZIP,
        PayloadTransformation.BZIP2,
    }
    assert {item.extension for item in report.extensions} == {".log", ".html", "<none>"}


def test_archive_result_groups_reporting_state_by_suffix_and_action(tmp_path: Path) -> None:
    """High file counts do not require one retained reporting object per regular file."""

    source = tmp_path / "source-grouped"
    destination = tmp_path / "archive-grouped"
    for index in range(20):
        write_at(source / f"item-{index}.log", f"payload-{index}".encode(), OLD)

    result = _run_policy_at(_policy(source, destination), planning_time=NOW)
    groups = result.archives[0].regular_payload_groups

    assert len(groups) == 1
    assert groups[0].source_suffix == ".log"
    assert groups[0].files == 20
    assert groups[0].source_bytes == sum(len(f"payload-{index}") for index in range(20))


def test_run_report_preserves_original_extension_and_transformation_crosstab(
    tmp_path: Path,
) -> None:
    """Case-normalized original extensions retain separate transformation outcomes."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "first.LOG", b"a" * 4000, OLD)
    write_at(source / "second.log", b"b" * 2000, OLD + timedelta(minutes=1))
    result = _run_policy_at(_policy(source, destination), planning_time=NOW)

    report = build_run_report((result,), elapsed_seconds=1.0)
    log_extension = next(item for item in report.extensions if item.extension == ".log")

    assert log_extension.totals.files == 2
    assert log_extension.totals.source_bytes == 6000
    assert len(log_extension.transformations) == 1
    assert log_extension.transformations[0].transformation is PayloadTransformation.GZIP
    assert log_extension.transformations[0].totals.files == 2


def test_zero_source_bytes_have_no_ratio_and_can_expand(tmp_path: Path) -> None:
    """Zero-byte compressed sources avoid division by zero and preserve expansion bytes."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "empty.log", b"", OLD)
    result = _run_policy_at(_policy(source, destination), planning_time=NOW)

    report = build_run_report((result,), elapsed_seconds=0.5)
    data = run_report_data(report)

    assert report.source_bytes == 0
    assert report.written_payload_bytes > 0
    assert report.saved_payload_bytes == -report.written_payload_bytes
    assert report.payload_size_ratio is None
    assert report.payload_savings_percent is None
    assert data["payload_size_ratio"] is None
    assert data["payload_savings_percent"] is None


def test_recovered_prior_archive_is_separate_from_new_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery before planning is reported separately and does not inflate new-work totals."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.txt", b"important", OLD)

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise ExecutionError("injected cleanup failure")

    monkeypatch.setattr(executor, "_remove_sources", fail_cleanup)
    with pytest.raises(ExecutionError, match="injected cleanup failure"):
        _run_policy_at(_policy(source, destination), planning_time=NOW)
    monkeypatch.undo()

    result = _run_policy_at(_policy(source, destination), planning_time=NOW + timedelta(hours=1))
    report = build_run_report((result,), elapsed_seconds=2.0)

    assert len(report.recovered_archives) == 1
    assert report.logical_streams_selected == 0
    assert report.archive_units_completed == 0
    assert report.regular_files == 0
    assert report.source_bytes == 0
    assert report.written_payload_bytes == 0


def test_human_summary_bounds_extension_rows() -> None:
    """Console rendering limits extension cardinality without changing report data."""

    extensions = tuple(
        ExtensionBreakdown(
            extension=f".e{index}",
            totals=PayloadBreakdown(1, 100 - index, 50),
            transformations=(
                TransformationBreakdown(
                    PayloadTransformation.GZIP,
                    PayloadBreakdown(1, 100 - index, 50),
                ),
            ),
        )
        for index in range(12)
    )
    report = RunReport(
        elapsed_seconds=3.0,
        policies=("reports",),
        logical_streams_selected=1,
        archive_units_completed=1,
        archive_directories=(Path("/archive/unit"),),
        recovered_archives=(),
        regular_files=12,
        preserved_symlinks=0,
        dropped_alias_symlinks=0,
        skipped_symlinks=0,
        source_bytes=sum(item.totals.source_bytes for item in extensions),
        written_payload_bytes=sum(item.totals.written_payload_bytes for item in extensions),
        transformations=(
            TransformationBreakdown(
                PayloadTransformation.GZIP,
                PayloadBreakdown(
                    12,
                    sum(item.totals.source_bytes for item in extensions),
                    sum(item.totals.written_payload_bytes for item in extensions),
                ),
            ),
        ),
        extensions=extensions,
    )

    rendered = render_run_summary(report, maximum_extensions=3)

    assert ".e0:" in rendered
    assert ".e2:" in rendered
    assert ".e3:" not in rendered
    assert "other: 9 files across 9 extensions" in rendered
    assert len(run_report_data(report)["extensions"]) == 12


def test_markdown_and_json_render_from_same_exact_result_values(tmp_path: Path) -> None:
    """Markdown and JSON expose the same source, written, and savings byte values."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    write_at(source / "data.log", b"z" * 10_000, OLD)
    result = _run_policy_at(_policy(source, destination), planning_time=NOW)
    report = build_run_report((result,), elapsed_seconds=4.25)

    data = run_report_data(report)
    markdown = render_run_markdown(report)

    assert str(data["source_bytes"]) in markdown
    assert str(data["written_payload_bytes"]) in markdown
    assert str(data["saved_payload_bytes"]) in markdown
    assert "Original extension × transformation" in markdown
    assert "`.log`" not in markdown  # Table values are plain escaped text, not code spans.


def test_run_cli_defaults_to_human_summary_and_json_is_explicit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The immediate run follows plan-style human default with explicit machine JSON."""

    first_source = tmp_path / "source-human"
    first_destination = tmp_path / "archive-human"
    write_at(first_source / "data.log", b"x" * 5000, OLD)
    first_destination.mkdir()
    first_config = _write_cli_config(tmp_path / "human", first_source, first_destination)

    assert main(["--config", str(first_config), "run"]) == 0
    human = capsys.readouterr().out

    second_source = tmp_path / "source-json"
    second_destination = tmp_path / "archive-json"
    write_at(second_source / "data.log", b"x" * 5000, OLD)
    second_destination.mkdir()
    second_config = _write_cli_config(tmp_path / "json", second_source, second_destination)

    assert main(["--config", str(second_config), "run", "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)

    assert human.startswith("Stream Archiver run summary\n")
    assert "Payload savings:" in human
    assert machine["schema_version"] == 1
    assert machine["regular_files"] == 1
    assert machine["extensions"][0]["extension"] == ".log"


def test_run_cli_can_emit_markdown_and_export_same_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit Markdown stdout and auxiliary report export use the same renderer."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    work = tmp_path / "config"
    work.mkdir()
    write_at(source / "data.log", b"x" * 5000, OLD)
    destination.mkdir()
    config = _write_cli_config(work, source, destination)
    report_path = tmp_path / "result.md"

    assert main(["--config", str(config), "run", "--markdown", "--report", str(report_path)]) == 0
    stdout = capsys.readouterr().out

    assert stdout.startswith("# Stream Archiver run report\n")
    assert report_path.read_text(encoding="utf-8") == stdout


def test_existing_report_path_blocks_run_before_source_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A deterministic report collision fails before archival can remove source data."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    work = tmp_path / "config"
    work.mkdir()
    original = write_at(source / "data.log", b"keep", OLD)
    destination.mkdir()
    config = _write_cli_config(work, source, destination)
    report_path = tmp_path / "result.md"
    report_path.write_text("existing", encoding="utf-8")

    status = main(["--config", str(config), "run", "--report", str(report_path)])
    captured = capsys.readouterr()

    assert status == 2
    assert original.read_bytes() == b"keep"
    assert report_path.read_text(encoding="utf-8") == "existing"
    assert "run report path already exists" in captured.err


def test_symlink_report_path_blocks_run_before_source_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A report-path symlink is an existing object and is never followed for output."""

    source = tmp_path / "source-symlink"
    destination = tmp_path / "archive-symlink"
    work = tmp_path / "config-symlink"
    work.mkdir()
    original = write_at(source / "data.log", b"keep", OLD)
    destination.mkdir()
    config = _write_cli_config(work, source, destination)
    target = tmp_path / "target.md"
    target.write_text("target", encoding="utf-8")
    report_path = tmp_path / "result-link.md"
    report_path.symlink_to(target)

    status = main(["--config", str(config), "run", "--report", str(report_path)])
    captured = capsys.readouterr()

    assert status == 2
    assert original.read_bytes() == b"keep"
    assert target.read_text(encoding="utf-8") == "target"
    assert report_path.is_symlink()
    assert "run report path already exists" in captured.err


def test_report_publish_does_not_overwrite_path_created_after_preflight(
    tmp_path: Path,
) -> None:
    """The final hard-link publication fails closed if another actor creates the pathname."""

    from stream_archiver.cli import _prepare_report_path, _write_markdown_report

    report_path = tmp_path / "race.md"
    prepared = _prepare_report_path(report_path)
    report_path.write_text("other actor", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _write_markdown_report(prepared, "our report")

    assert report_path.read_text(encoding="utf-8") == "other actor"
    assert list(tmp_path.glob(".race.md.*.tmp")) == []


def test_report_export_failure_does_not_relabel_completed_archives_as_failed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-commit Markdown failure is advisory while completed archive success remains true."""

    source = tmp_path / "source"
    destination = tmp_path / "archive"
    work = tmp_path / "config"
    work.mkdir()
    original = write_at(source / "data.log", b"payload", OLD)
    destination.mkdir()
    config = _write_cli_config(work, source, destination)
    report_path = tmp_path / "result.md"

    def fail_report(path: Path, content: str) -> None:
        raise OSError("injected report write failure")

    monkeypatch.setattr("stream_archiver.cli._write_markdown_report", fail_report)
    status = main(["--config", str(config), "run", "--report", str(report_path)])
    captured = capsys.readouterr()

    assert status == 0
    assert not os.path.lexists(original)
    assert not report_path.exists()
    assert "run_report_export_failed" in captured.err
    assert "completed archives remain committed and verified" in captured.err
    assert "retry_safe=false" in captured.err
    assert "do not rerun archival solely to recreate this invocation report" in captured.err
    assert captured.out.startswith("Stream Archiver run summary\n")


def test_report_temporary_cleanup_failure_preserves_successful_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed temporary-name cleanup must not misreport a published report as failed."""

    source = tmp_path / "source-cleanup"
    destination = tmp_path / "archive-cleanup"
    work = tmp_path / "config-cleanup"
    work.mkdir()
    write_at(source / "data.log", b"payload", OLD)
    destination.mkdir()
    config = _write_cli_config(work, source, destination)
    report_path = tmp_path / "result-cleanup.md"
    original_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.parent == tmp_path and path.name.startswith(".result-cleanup.md."):
            raise OSError("injected temporary cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary_unlink)

    status = main(["--config", str(config), "run", "--report", str(report_path)])
    captured = capsys.readouterr()

    assert status == 0
    assert report_path.read_text(encoding="utf-8").startswith("# Stream Archiver run report\n")
    assert "run_report_exported" in captured.err
    assert "run_report_temporary_cleanup_failed" in captured.err
    assert "run_report_export_failed" not in captured.err
    assert "final Markdown report is published" in captured.err
    assert len(list(tmp_path.glob(".result-cleanup.md.*.tmp"))) == 1


def test_run_cli_reports_zero_selected_work_without_inventing_compression_ratio(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty successful run reports zero counts and leaves ratio metrics undefined."""

    source = tmp_path / "source-empty"
    destination = tmp_path / "archive-empty"
    work = tmp_path / "config-empty"
    source.mkdir()
    destination.mkdir()
    work.mkdir()
    config = _write_cli_config(work, source, destination)

    assert main(["--config", str(config), "run", "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)

    assert machine["logical_streams_selected"] == 0
    assert machine["archive_units_completed"] == 0
    assert machine["regular_files"] == 0
    assert machine["source_bytes"] == 0
    assert machine["written_payload_bytes"] == 0
    assert machine["saved_payload_bytes"] == 0
    assert machine["payload_size_ratio"] is None
    assert machine["payload_savings_percent"] is None
