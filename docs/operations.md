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
`--config` option can be omitted for manual commands. For per-user operation,
set `STREAM_ARCHIVER_CONFIG` once or place the policy under the XDG configuration
directory.

## Recommended installation

Use the guided installer from the source checkout or an extracted release
bundle:

```bash
sudo UV_BIN="$(command -v uv)" \
  ./tools/install-systemd.sh \
  --config /absolute/path/to/policies.toml
```

Passing `UV_BIN` explicitly avoids depending on root's `PATH` when `uv` is
installed only for the invoking user. The installer must use a Python version
compatible with the package requirement instead of assuming that the operating
system's `python3` command is sufficiently new.

A checkout can contain several wheels. The installer prefers a wheel matching
the current project version. If no matching wheel is present, it selects the
newest candidate and warns. When release checksum evidence contains an entry for
the selected wheel, the installer verifies it and rejects a mismatch. A missing
checksum file or missing wheel entry is a warning, not an installation blocker.

The installer protects an existing deployed policy before making system changes,
installs into a stable application path, validates the read-only plan as the
service user, generates units from the actual deployment paths, verifies the
units, and reloads systemd. It does not start archival work or enable the timer.
Use `--help` for the options supported by the installed revision.

For an existing installation, use `render-systemd` directly. The generated
`INSTALL.md` is the deployment checklist for those resolved paths.

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
`--service-log-level DEBUG`, reviewing the generated diff, reinstalling it, and
running `systemctl daemon-reload`. Debug logs include more paths, plan decisions,
and verification milestones; they do not include file contents.

Important progress fields include:

- `policy_progress`, `source_progress`, `archive_progress`;
- `action_progress`, `cleanup_progress`, `payload_progress`;
- `file_bytes`, `overall_bytes`, `percent`, and `overall_percent`; and
- archive, source, operation, and evidence-hash fields.

Text log string values are quoted. This keeps paths with whitespace visually
separate from adjacent fields.

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

Exit status 2 indicates an expected configuration, planning, locking, execution,
recovery, or verification failure. The final `operation_failed` log includes a
corrective action. Exit status 1 indicates an unexpected internal failure and
retains a traceback for defect reporting. Exit status 130 indicates operator
interruption.

A committed archive with `cleanup_complete: false` is incomplete. Correct the
reported source or permission problem and rerun the policy. Recovery validates
remaining source identities before deletion.

If a read-only plan succeeds for the service user but systemd reports that a
source is inaccessible, regenerate the unit from the current configuration and
inspect its sandbox. The generator disables `ProtectHome` when a required policy,
source, or destination path is under `/home`, `/root`, or `/run/user`; otherwise
it keeps that protection enabled.

Do not edit a manifest to force deletion after a source was intentionally
changed. Preserve both copies and resolve the conflict manually.

## Configuration or package upgrades

1. Install the new package into the stable virtual environment.
2. Run `plan` and review the result.
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
