"""Tests for deployment-specific systemd generation and safe onboarding."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from stream_archiver.config import AppConfig, Policy, SymlinkRule
from stream_archiver.errors import ConfigurationError
from stream_archiver.systemd import render_systemd_bundle


def _config(tmp_path: Path) -> AppConfig:
    first = (tmp_path / "source one").resolve()
    second = (tmp_path / "source-two").resolve()
    destination = (tmp_path / "archive").resolve()
    for path in (first, second, destination):
        path.mkdir()
    return AppConfig(
        run_interval=timedelta(days=30),
        policies=(
            Policy(
                name="reports",
                sources=(first, second),
                destination=destination,
                minimum_age=timedelta(days=30),
                stream_gap=timedelta(hours=8),
                symlink_rule=SymlinkRule.IGNORE,
                compression_rules=(),
            ),
        ),
    )


def test_rendered_units_derive_paths_without_editable_placeholders(
    tmp_path: Path,
) -> None:
    """Generated units contain resolved deployment paths and exact review steps."""

    config = _config(tmp_path)
    config_path = tmp_path / "policies.toml"
    config_path.write_text("schema_version = 2\n", encoding="utf-8")
    executable = tmp_path / "bin" / "stream-archiver"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    output = tmp_path / "generated"

    result = render_systemd_bundle(
        config,
        config_path=config_path,
        executable=executable,
        output_directory=output,
    )

    service = result.service_path.read_text(encoding="utf-8")
    instructions = result.instructions_path.read_text(encoding="utf-8")
    assert str(executable) in service
    assert str(config_path) in service
    for policy in config.policies:
        assert str(policy.destination) in service
        for source in policy.sources:
            assert str(source) in service
    assert "ConditionPath" not in service
    assert "--lock-file" not in service
    assert "REPLACE" not in service
    assert "/opt/stream-archiver-0." not in service
    assert "Regenerate rather than editing" in instructions
    assert "successful manual service run" in instructions


def test_render_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """Regeneration requires an explicit overwrite decision."""

    config = _config(tmp_path)
    config_path = tmp_path / "policies.toml"
    config_path.write_text("schema_version = 2\n", encoding="utf-8")
    executable = tmp_path / "stream-archiver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    output = tmp_path / "generated"
    render_systemd_bundle(
        config,
        config_path=config_path,
        executable=executable,
        output_directory=output,
    )

    with pytest.raises(ConfigurationError, match="refusing to overwrite"):
        render_systemd_bundle(
            config,
            config_path=config_path,
            executable=executable,
            output_directory=output,
        )


def test_guided_installer_is_syntax_valid_and_version_independent() -> None:
    """The installer is invoked with options instead of requiring source edits."""

    import subprocess

    root = Path(__file__).parents[1]
    installer = root / "tools" / "install-systemd.sh"
    subprocess.run(["bash", "-n", str(installer)], check=True)
    text = installer.read_text(encoding="utf-8")
    assert "--config PATH" in text
    assert "--service-name NAME" in text
    assert "--on-calendar VALUE" in text
    assert "--service-log-level LEVEL" in text
    assert "/opt/stream-archiver-0." not in text
    assert "does not start the" in text
    assert "mover or enable the timer" in text
    assert "--python-version VERSION" in text
    assert "PYTHON_VERSION=3.11" in text
    assert text.index("Refusing to replace") < text.index('"$UV_BIN" python install')
    assert "SHA256SUMS has no bundled wheel entry" in text


def test_rendered_service_allows_configured_paths_under_home(tmp_path: Path) -> None:
    """ProtectHome cannot hide a source that the generated policy must access."""

    source = Path("/home/example/archive source")
    destination = Path("/srv/archive/stream-archiver")
    config = AppConfig(
        run_interval=timedelta(days=30),
        policies=(
            Policy(
                name="home-reports",
                sources=(source,),
                destination=destination,
                minimum_age=timedelta(days=30),
                stream_gap=timedelta(hours=8),
                symlink_rule=SymlinkRule.IGNORE,
                compression_rules=(),
            ),
        ),
    )
    config_path = tmp_path / "policies.toml"
    config_path.write_text("schema_version = 2\n", encoding="utf-8")
    executable = tmp_path / "stream-archiver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    result = render_systemd_bundle(
        config,
        config_path=config_path,
        executable=executable,
        output_directory=tmp_path / "generated",
    )

    service = result.service_path.read_text(encoding="utf-8")
    assert "ProtectHome=read-only" in service
    assert f'ReadWritePaths="{source}"' in service
