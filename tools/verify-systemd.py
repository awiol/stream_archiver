"""Generate disposable systemd units and verify their 0.4 deployment contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stream_archiver.config import AppConfig, Policy, SymlinkRule
from stream_archiver.systemd import render_systemd_bundle


def main() -> int:
    """Run a non-destructive generated-unit smoke test with real systemd tooling."""

    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        print("NOTICE: systemd-analyze unavailable; generated-unit verification not executed.")
        return 0

    with tempfile.TemporaryDirectory(prefix="stream-archiver-systemd-") as raw_root:
        root = Path(raw_root)
        source = root / "source with spaces"
        destination = root / "archive"
        source.mkdir()
        destination.mkdir()
        config_path = root / "policies.toml"
        config_path.write_text("schema_version = 2\n", encoding="utf-8")
        executable = root / "stream-archiver"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        config = AppConfig(
            run_interval=timedelta(days=30),
            policies=(
                Policy(
                    name="smoke",
                    sources=(source,),
                    destination=destination,
                    minimum_age=timedelta(days=30),
                    stream_gap=timedelta(hours=8),
                    symlink_rule=SymlinkRule.IGNORE,
                    compression_rules=(),
                ),
            ),
        )
        result = render_systemd_bundle(
            config,
            config_path=config_path,
            executable=executable,
            output_directory=root / "generated",
        )
        service = result.service_path.read_text(encoding="utf-8")
        if "ConditionPath" in service:
            raise SystemExit("generated service contains an operational ConditionPath gate")
        if "--lock-file" in service:
            raise SystemExit("generated service contains the obsolete --lock-file option")
        if "ProtectHome=read-only" not in service:
            raise SystemExit("generated service does not use ProtectHome=read-only")
        subprocess.run(
            [analyzer, "verify", str(result.service_path), str(result.timer_path)],
            check=True,
        )
    print("Generated systemd unit verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
