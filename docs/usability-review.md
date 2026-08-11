# Installation and operations usability review

Release reviewed: 0.2.0  
Resulting release: 0.3.0

## Observed problems

### Tracked examples were deployment configuration

The previous workflow told the operator to edit a package-tracked service file
that contained version-specific executable paths and an example list of
`ReadWritePaths`. This created several failure modes:

- package updates could replace or conflict with local edits;
- adding a policy source could leave the service sandbox stale;
- the installation path embedded the release version;
- the installer copied example configuration before the user had a clean
  ownership boundary for local configuration; and
- enabling the timer was mixed into installation rather than gated by a manual
  plan and run.

### Logs did not support unattended diagnosis

Only terminal errors were logged. An operator could not distinguish discovery,
planning, staging, verification, commit, cleanup, or recovery, and could not see
whether a large file or multi-file archive was progressing.

## Design changes

### Generated deployment artifacts

`render-systemd` now derives the executable path, config path, source and
destination sandbox paths, service identity, schedule, and log options. It
creates units plus exact installation instructions in a separate output
directory. Generated files are replaced only with explicit `--force`.

This makes the policy the source of truth and removes the need to edit tracked
unit examples.

### Stable and conservative installer

`tools/install-systemd.sh` accepts a required user-owned policy path and installs
into `/opt/stream-archiver/venv`, which does not contain a release number. It
validates configuration and planning as the service user, generates and verifies
units, and reloads systemd. It does not start the mover or enable the timer.

Existing installed policy is checked before account or package changes and is
protected unless `--replace-config` is explicit. The installer verifies the
bundled wheel checksum when release evidence is present and exposes service
identity, timer schedule, and service logging as command-line options. The first
destructive run therefore remains a separate operator decision.

### Layered logs

Named structured events provide:

- lifecycle visibility at `INFO`;
- detailed decisions at `DEBUG`;
- bounded progress by items, source bytes, and percentages;
- distinct expected-failure, unexpected-failure, and interruption outcomes; and
- stdout/stderr separation for scripting and journal collection.

## Remaining usability limits

- The installer cannot decide correct Unix ownership or ACLs for arbitrary
  source trees. It validates access through the read-only plan and reports
  failures, but the operator owns permission design.
- The generated service requires configured source and destination directories
  to exist before start.
- Unit generation does not install or enable units by itself.
- Package upgrades are not managed by an Ubuntu `.deb`; the wheel and stable
  virtual environment remain an administrator-managed deployment.
- Log volume at `DEBUG` can be significant for large archives. It is intended
  for diagnosis, not permanent default operation.

## 0.3.1 follow-up review

### Path diagnostics were visually ambiguous

Text logs previously emitted structured fields without quoting simple strings,
and final exception text embedded paths directly in free-form messages. A path
containing spaces could therefore be difficult to distinguish from neighboring
fields. Version 0.3.1 quotes every text-log string field and emits source-root
failures with separate `source`, `reason`, and corrective-action fields.

### Service-user validation did not reproduce the systemd sandbox

The installer ran `plan` as the service account before installation, but that
check occurred outside the generated service mount namespace. A policy under
`/home`, `/root`, or `/run/user` could pass that check and later be hidden by
`ProtectHome=true` when systemd started the service. Unit generation now keeps
`ProtectHome=true` only when none of the required configuration/source/
destination paths are in those trees. Otherwise it uses `ProtectHome=false`
while retaining `ProtectSystem=strict` and the generated writable-path allow
list.

### Routine CLI invocation repeated deployment details

The documented workflow repeated a full `--config` path and an explicit manual
lock path for nearly every command. The CLI now resolves configuration from an
explicit option, `STREAM_ARCHIVER_CONFIG`, a standard per-user XDG location, or
the installed `/etc/stream-archiver/policies.toml`. Manual state and locking use
the XDG state directory by default. The review workflow is also shorter:
`plan` already validates configuration, and a successful `run` already performs
transactional payload/evidence verification.

### Compression output collisions were safe but unnecessarily fatal

A valid source can contain both `data.json` and `data.json.gz`. Compressing the
first file prefers the same archive path used by the second. The previous
planner rejected the complete stream. Version 0.3.1 preserves fixed source paths
and deterministically disambiguates only generated gzip/bzip2 paths, with the
chosen path recorded in the plan and manifest. Fixed-path and reserved-evidence
collisions remain hard failures.
