"""Generate deployment-specific systemd units from validated configuration."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from stream_archiver.config import AppConfig
from stream_archiver.errors import ConfigurationError

_SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")
_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*[$]?")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class SystemdRenderResult:
    """Paths generated for a reviewable systemd deployment bundle."""

    service_path: Path
    timer_path: Path
    instructions_path: Path


def resolve_current_executable(explicit: Path | None = None) -> Path:
    """Resolve the console executable used in generated ``ExecStart``.

    An explicit path is preferred.  Otherwise the current console command is
    resolved through ``PATH``.  Running through ``python -m`` requires an
    explicit ``--executable`` because ``sys.argv[0]`` is not the installed
    console entry point.
    """

    if explicit is not None:
        candidate = explicit.expanduser().resolve(strict=False)
    else:
        resolved = shutil.which("stream-archiver")
        if resolved is None:
            raise ConfigurationError(
                "cannot locate the stream-archiver executable; pass --executable"
            )
        candidate = Path(resolved).resolve(strict=False)
    if not candidate.is_absolute():
        raise ConfigurationError("systemd executable path must be absolute")
    if not candidate.exists() or candidate.is_dir():
        raise ConfigurationError(f"systemd executable does not exist: {candidate}")
    if not os.access(candidate, os.X_OK):
        raise ConfigurationError(f"systemd executable is not executable: {candidate}")
    return candidate


def render_systemd_bundle(
    config: AppConfig,
    *,
    config_path: Path,
    executable: Path,
    output_directory: Path,
    service_name: str = "stream-archiver",
    service_user: str = "stream-archiver",
    service_group: str = "stream-archiver",
    on_calendar: str = "daily",
    randomized_delay: str = "6h",
    accuracy: str = "1h",
    log_level: str = "INFO",
    log_format: str = "text",
    force: bool = False,
) -> SystemdRenderResult:
    """Render units and exact installation instructions without installing them.

    Paths and sandbox permissions come from the validated policy file.  The
    generated files are deployment artifacts; users do not edit package-tracked
    templates or version-specific paths.
    """

    _validate_identifier(service_name, "service name", _SERVICE_NAME_PATTERN)
    _validate_identifier(service_user, "service user", _USER_PATTERN)
    _validate_identifier(service_group, "service group", _USER_PATTERN)
    normalized_level = log_level.upper()
    if normalized_level not in _LOG_LEVELS:
        choices = ", ".join(sorted(_LOG_LEVELS))
        raise ConfigurationError(f"log level must be one of: {choices}")
    if log_format not in {"text", "json"}:
        raise ConfigurationError("log format must be text or json")
    for value, description in (
        (on_calendar, "OnCalendar"),
        (randomized_delay, "RandomizedDelaySec"),
        (accuracy, "AccuracySec"),
    ):
        _reject_control_characters(value, description)

    config_path = config_path.expanduser().resolve(strict=False)
    executable = executable.expanduser().resolve(strict=False)
    output_directory = output_directory.expanduser().resolve(strict=False)
    if not config_path.is_absolute() or not executable.is_absolute():
        raise ConfigurationError("systemd config and executable paths must be absolute")

    output_directory.mkdir(parents=True, exist_ok=True)
    service_path = output_directory / f"{service_name}.service"
    timer_path = output_directory / f"{service_name}.timer"
    instructions_path = output_directory / "INSTALL.md"
    for path in (service_path, timer_path, instructions_path):
        if path.exists() and not force:
            raise ConfigurationError(
                f"refusing to overwrite {path}; use --force after reviewing existing files"
            )

    state_path = Path("/var/lib") / service_name / "state.json"
    lock_path = Path("/run") / service_name / "execution.lock"
    writable_paths = sorted(
        {
            *(source for policy in config.policies for source in policy.sources),
            *(policy.destination for policy in config.policies),
        },
        key=str,
    )

    service_text = _service_unit(
        config_path=config_path,
        executable=executable,
        service_name=service_name,
        service_user=service_user,
        service_group=service_group,
        state_path=state_path,
        lock_path=lock_path,
        writable_paths=writable_paths,
        log_level=normalized_level,
        log_format=log_format,
    )
    timer_text = _timer_unit(
        service_name=service_name,
        on_calendar=on_calendar,
        randomized_delay=randomized_delay,
        accuracy=accuracy,
    )
    instructions_text = _installation_instructions(
        service_name=service_name,
        service_user=service_user,
        service_group=service_group,
        config_path=config_path,
        executable=executable,
        generated_directory=output_directory,
        writable_paths=writable_paths,
    )
    _write_text(service_path, service_text)
    _write_text(timer_path, timer_text)
    _write_text(instructions_path, instructions_text)
    return SystemdRenderResult(service_path, timer_path, instructions_path)


def _service_unit(
    *,
    config_path: Path,
    executable: Path,
    service_name: str,
    service_user: str,
    service_group: str,
    state_path: Path,
    lock_path: Path,
    writable_paths: list[Path],
    log_level: str,
    log_format: str,
) -> str:
    command = " ".join(
        _quote_unit_argument(value)
        for value in (
            str(executable),
            "--config",
            str(config_path),
            "--log-level",
            log_level,
            "--log-format",
            log_format,
            "run-if-due",
            "--state",
            str(state_path),
            "--lock-file",
            str(lock_path),
        )
    )
    conditions = "\n".join(
        f"ConditionPathIsDirectory={_escape_unit_path(path)}" for path in writable_paths
    )
    permissions = "\n".join(
        f"ReadWritePaths={_quote_unit_argument(str(path))}" for path in writable_paths
    )
    protect_home = "false" if _requires_home_access([config_path, *writable_paths]) else "true"
    return f"""[Unit]
Description=Archive complete old filesystem streams
ConditionPathExists={_escape_unit_path(config_path)}
{conditions}

[Service]
Type=oneshot
User={service_user}
Group={service_group}
ExecStart={command}

StateDirectory={service_name}
RuntimeDirectory={service_name}
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome={protect_home}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
{permissions}

Nice=10
IOSchedulingClass=idle
StandardOutput=journal
StandardError=journal
SyslogIdentifier={service_name}
"""


def _requires_home_access(paths: list[Path]) -> bool:
    """Return whether configured policy paths need systemd home-tree access.

    ``ProtectHome=true`` hides ``/home``, ``/root``, and ``/run/user`` inside
    the service mount namespace.  Disable that sandbox only when a configured
    source or destination is located below one of those trees; the generated
    ``ReadWritePaths`` list still limits writable locations under
    ``ProtectSystem=strict``.
    """

    protected_roots = (Path("/home"), Path("/root"), Path("/run/user"))
    return any(path == root or root in path.parents for path in paths for root in protected_roots)


def _escape_unit_path(path: Path) -> str:
    """Escape one absolute path for an unquoted systemd path directive.

    ``ConditionPath*`` treats surrounding quote characters as literal path
    characters.  Encode whitespace and backslashes instead of applying the
    command-line quoting used by ``ExecStart`` and ``ReadWritePaths``.
    Percent signs are doubled so that systemd does not interpret them as
    specifiers.
    """

    value = str(path)
    if not path.is_absolute():
        raise ConfigurationError(f"systemd condition path must be absolute: {path}")
    _reject_control_characters(value, "systemd path")
    return value.replace("%", "%%").replace("\\", r"\x5c").replace(" ", r"\x20")


def _timer_unit(
    *, service_name: str, on_calendar: str, randomized_delay: str, accuracy: str
) -> str:
    return f"""[Unit]
Description=Check whether stream archival is due

[Timer]
OnCalendar={on_calendar}
Persistent=true
RandomizedDelaySec={randomized_delay}
AccuracySec={accuracy}
Unit={service_name}.service

[Install]
WantedBy=timers.target
"""


def _installation_instructions(
    *,
    service_name: str,
    service_user: str,
    service_group: str,
    config_path: Path,
    executable: Path,
    generated_directory: Path,
    writable_paths: list[Path],
) -> str:
    paths = "\n".join(f"- `{path}`" for path in writable_paths)
    return f"""# Install generated systemd units

These files were generated from `{config_path}` for `{executable}`.
Regenerate rather than editing them when the executable, policy paths, service
identity, or schedule changes.

## 1. Review prerequisites

The service account is `{service_user}:{service_group}`.  Create it when absent:

```bash
getent group {service_group} >/dev/null || sudo groupadd --system {service_group}
id -u {service_user} >/dev/null 2>&1 || sudo useradd --system \\
  --gid {service_group} --home-dir /nonexistent --shell /usr/sbin/nologin \\
  {service_user}
```

Ensure that the account can traverse, read, and delete from each source and can
create files in each destination:

{paths}

The generated service deliberately refuses to start when any configured source
or destination directory is absent.

## 2. Validate before installation

```bash
{_shell_quote(str(executable))} --config {_shell_quote(str(config_path))} check
{_shell_quote(str(executable))} --config {_shell_quote(str(config_path))} plan
systemd-analyze verify \\
  {_shell_quote(str(generated_directory / f"{service_name}.service"))} \\
  {_shell_quote(str(generated_directory / f"{service_name}.timer"))}
```

## 3. Install and test

```bash
sudo install -m 0644 {_shell_quote(str(generated_directory / f"{service_name}.service"))} \\
  /etc/systemd/system/{service_name}.service
sudo install -m 0644 {_shell_quote(str(generated_directory / f"{service_name}.timer"))} \\
  /etc/systemd/system/{service_name}.timer
sudo systemctl daemon-reload
sudo systemctl start {service_name}.service
sudo systemctl status {service_name}.service
journalctl -u {service_name}.service -n 200 --no-pager
```

A successful manual service run is required before enabling the timer:

```bash
sudo systemctl enable --now {service_name}.timer
systemctl list-timers {service_name}.timer
```

## 4. Regenerate after configuration changes

Run `stream-archiver render-systemd` again with the same options and `--force`,
review the diff, reinstall both units, and run `systemctl daemon-reload`.
"""


def _quote_unit_argument(value: str) -> str:
    _reject_control_characters(value, "systemd argument")
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _validate_identifier(value: str, description: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise ConfigurationError(f"invalid {description}: {value!r}")


def _reject_control_characters(value: str, description: str) -> None:
    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise ConfigurationError(f"invalid {description}: control characters are not allowed")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
