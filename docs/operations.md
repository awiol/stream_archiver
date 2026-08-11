# Operations guide

## Pre-deployment checks

1. Create a user-owned policy file from `examples/config/policies.toml`.
2. Ensure every source and destination directory already exists.
3. Ensure the service account can traverse, read, and delete from sources and
   can create files in destinations.
4. Confirm destination free space. Safe movement temporarily retains both
   source and destination copies.
5. Review the read-only plan. `plan` also loads and validates the policy:

```bash
stream-archiver --config /path/to/policies.toml plan
```

After installation to the default `/etc/stream-archiver/policies.toml`, the
`--config` option can be omitted for manual commands.

## Recommended installation

Use the guided installer from an extracted release bundle:

```bash
sudo ./tools/install-systemd.sh \
  --config /absolute/path/to/policies.toml
```

The script checks configuration replacement before changing the system, verifies
the bundled wheel checksum when `SHA256SUMS` is present, installs into
`/opt/stream-archiver/venv`, installs the policy under `/etc/stream-archiver`,
generates units from actual policy paths, verifies them, and reloads systemd. It
does not start archival work or enable the timer. Use `--help` to set the service
name, account, schedule, randomized delay, timer accuracy, or log format/level.

For an existing installation, use `render-systemd` directly. The generated
`INSTALL.md` is the authoritative deployment checklist for those exact paths.

## First-run gate

Run the read-only plan as the service account, then perform one manual service
run:

```bash
sudo -u stream-archiver \
  /opt/stream-archiver/venv/bin/stream-archiver plan

sudo systemctl start stream-archiver.service
sudo systemctl status stream-archiver.service
journalctl -u stream-archiver.service -n 200 --no-pager
```

Only enable the timer after the service exits successfully and the resulting
archives pass verification:

```bash
/opt/stream-archiver/venv/bin/stream-archiver verify
sudo systemctl enable --now stream-archiver.timer
```

## Logs

Normal scheduled operation uses `INFO` text logs in journald. Follow progress:

```bash
journalctl -u stream-archiver.service -f
```

Show warnings and errors:

```bash
journalctl -u stream-archiver.service -p warning..alert
```

Temporarily use debug logging by regenerating the unit with
`--service-log-level DEBUG`, reviewing the diff, reinstalling it, and running
`systemctl daemon-reload`. Debug logs include more paths, plan decisions, and
verification milestones; they do not include file contents.

Important progress fields include:

- `policy_progress`, `source_progress`, `archive_progress`;
- `action_progress`, `cleanup_progress`, `payload_progress`;
- `file_bytes`, `overall_bytes`, `percent`, and `overall_percent`; and
- `archive`, `source`, `operation`, and evidence hashes.

## Completion criteria

A current archive is complete only when:

- `MANIFEST.json` has `cleanup_complete: true`;
- every regular payload matches its archived SHA-256;
- compressed payloads decompress to their source SHA-256;
- `SHA256SUMS.json` matches the manifest and recorded hash; and
- `SUCCESS.json` references the current final manifest and checksum-index
  hashes.

The `verify` command checks these conditions without changing files.

## Diagnosing failures

Exit status 2 indicates an expected configuration, planning, locking,
execution, recovery, or verification failure. The final `operation_failed` log
includes a corrective action. Exit status 1 indicates an unexpected internal
failure and retains a traceback for defect reporting. Exit status 130 indicates
operator interruption.

A committed archive with `cleanup_complete: false` is incomplete. Correct the
reported source or permission problem and rerun the policy. Recovery validates
remaining source identities before deletion.

Do not edit a manifest to force deletion after a source was intentionally
changed. Preserve both copies and resolve the conflict manually.

## Configuration or package upgrades

1. Install the new wheel into the stable virtual environment.
2. Run `plan` (which also validates configuration).
3. Regenerate systemd files with `--force`.
4. Review the generated-unit diff.
5. Install both units and run `systemctl daemon-reload`.
6. Perform a manual service run and verification before resuming the timer.

Regeneration is required when source paths, destinations, policy path,
executable path, service identity, schedule, or service log options change.

## Restore

Automatic restore is outside scope. The manifest records original relative
paths, modes, mtimes, actions, link text, hashes, and codec. Verify hashes before
placing restored files into use. Test restore procedures on non-production data.

## 0.3.1 command-line conveniences

For the standard system installation, the following are sufficient:

```bash
stream-archiver plan
stream-archiver run
```

The CLI automatically uses `/etc/stream-archiver/policies.toml` when no higher
priority policy path is configured. For a per-user policy, set
`STREAM_ARCHIVER_CONFIG` once or place the file at
`$XDG_CONFIG_HOME/stream-archiver/policies.toml` (defaulting to
`~/.config/stream-archiver/policies.toml`).

`plan` includes configuration validation, so `check` does not need to precede
it. A successful `run` already verifies the staged bytes and final completion
evidence; use `verify` later when an independent integrity audit is wanted.

If a service succeeds under `sudo -u stream-archiver ... plan` but reports that
a source is inaccessible when started by systemd, regenerate and reinstall the
unit with 0.3.1 or later. Generated units no longer hide required paths under
`/home`, `/root`, or `/run/user` with `ProtectHome=true`.
