# Operations guide

## Pre-deployment contract

Before destructive or scheduled operation:

1. Create and review a user-owned policy file.
2. Ensure every configured source and destination root already exists as a real
   directory.
3. Ensure the service account can traverse/read/delete from sources and create
   archive objects in destinations.
4. Confirm destination free space. A staged logical move temporarily retains
   both source and destination representations.
5. Ensure producers will not continue modifying entries selected for cleanup.
6. Review a read-only plan.

```bash
stream-archiver --config /path/to/policies.toml plan
```

The timestamp-gap rule is a heuristic, not a producer-completion protocol. Schema 3 also makes recursive discovery and stream partitioning explicit; review `docs/user-guide.md` before changing either setting.

## Development and local verification

For a locked `uv` development environment:

```bash
./tools/bootstrap-dev.sh --python-version 3.11
```

For source verification without downloads:

```bash
./tools/verify-local.sh
```

For the additional wheel/install surface:

```bash
./tools/verify-local.sh --release
```

If Ruff is unavailable, the verification script reports that fact. It does not
convert an unavailable lint gate into a pass.

## Recommended installation

```bash
sudo UV_BIN="$(command -v uv)" \
  ./tools/install-systemd.sh \
  --config /absolute/path/to/policies.toml
```

The installer defaults to Python 3.11 and accepts `--python-version` for another
declared supported version. It protects an existing installed policy, installs
into the stable application path, validates policy/plan as the service user,
generates deployment-specific units, verifies them, and reloads systemd. It does
not start the mover or enable the timer.

## First-run gate

Run the read-only plan as the service account:

```bash
sudo -u stream-archiver /opt/stream-archiver/venv/bin/stream-archiver plan
```

Then run the generated service once and inspect its status/logs:

```bash
sudo systemctl start stream-archiver.service
sudo systemctl status stream-archiver.service
journalctl -u stream-archiver.service -n 200 --no-pager
```

Verify the resulting archives before enabling recurrence:

```bash
/opt/stream-archiver/venv/bin/stream-archiver verify
sudo systemctl enable --now stream-archiver.timer
```

## Time and verification surfaces

`plan --at <ISO-8601>` is the only public artificial-time surface. `run` and
`run-if-due` use the observed execution clock.

`verify` checks only archives whose manifest ownership matches the selected
policy/source roots, except that archive-shaped candidates with missing
ownership evidence are reported as malformed. To audit every recognized archive
under selected destination roots, use:

```bash
stream-archiver verify --all-in-destination
```

## Locks and concurrent producers

Stream Archiver uses advisory hierarchical filesystem-resource locks. Nested
resources contend across cooperating Stream Archiver processes. `verify` uses
shared locks; destructive operation uses exclusive mutation-root locks.

The lock protocol does not control unrelated producer processes. A producer that
keeps writing an already-open inode after final validation, or replaces the
selected pathname between validation and unlink, is outside the safe-deletion
precondition. Use an external producer completion or coordination protocol when
that behavior is possible.

`--lock-file` is retained only as a deprecated 0.3 migration option and is
ignored by the 0.4 safety lock domain.

## Logs and progress

Stream Archiver creates no log file by default. Manual operational logs go to stderr. The generated systemd service leaves stderr under journald. Command results stay on stdout; `plan` is human-readable by default and `plan --json` provides its detailed machine representation. Every CLI invocation has a `run_id`.

Useful fields include `phase`, `operation`, `outcome`, `policy_name`, `source_root`, `source_path`, `destination_root`, `archive_name`, `archive_directory`, and `plan_id` where applicable. `archive_action` identifies an archival operation; `next_action` is reserved for remediation.

INFO is event-driven at meaningful run/policy/source and stream/archive lifecycle boundaries, with aggregate staging progress derived from actual work. Routine per-entry discovery/staging/hashing/revalidation/cleanup and lock details are DEBUG. The approximate 1–10 second INFO density preference is only a representative-workload usability heuristic; no timer, heartbeat, rate limiter, or pass/fail gate implements it.

`staging_percent=100` means payload staging is complete; it does not mean the
transaction is complete. `transaction_percent=100` is emitted only after source
cleanup persistence, final verification, and completion evidence.

```bash
journalctl -u stream-archiver.service -f
journalctl -u stream-archiver.service -p warning..alert
```

## Failure states and recovery

Exit status 2 is an expected configuration/planning/locking/execution/recovery
or verification failure. Exit status 1 is an unexpected internal failure. Exit
status 130 is operator interruption.

A committed archive with `cleanup_complete: false` is a pending transaction.
Recovery first verifies the committed payload and checksum evidence. If that
verification fails, recovery stops before deleting any remaining source entry.
If some cleanup occurred before interruption, recovery reconciles missing versus
still-present source entries and continues only when remaining preconditions
hold.

Do not edit a manifest to force cleanup after a source conflict. Preserve the available copies and resolve the conflict explicitly.

Automatic cleanup removes selected files/symlinks only. It does not recursively remove empty source directories, including directories made empty by selected-file cleanup.

## Durability boundary

Completion requires successful synchronization of the required payload metadata,
archive namespace, source cleanup directories, final manifest, and completion
evidence. A synchronization failure before cleanup preserves source entries. A
failure after partial cleanup leaves the archive incomplete; it must not receive
completion evidence.

This contract is bounded to the local-Linux-filesystem assumptions in
`docs/requirements.md`. Validate other filesystem/storage types separately.

## Upgrading from 0.3

1. Install the new package into the stable environment.
2. Run `plan` and inspect differences.
3. Regenerate systemd files; 0.3 units contain obsolete condition/lock-file
   semantics.
4. Review the generated unit diff and run `systemd-analyze verify`.
5. Perform one manual service run and `verify` before enabling the timer.

Completed supported 0.3 and 0.4.0a1 archives remain verifiable. Valid v1 scheduling state is readable but affected policies are due until a successful v2 fingerprinted state record is written. Pending supported 0.3 cleanup uses the corrected verify-before-delete recovery ordering.

Schema-1/2 configuration remains valid with recursive/source-root historical defaults. Use schema 3 when setting `recursive` or `stream_partition`. Because 0.4.0a2 adds those selection fields to the v2 policy fingerprint, a v2 fingerprint written by 0.4.0a1 intentionally mismatches once after upgrade; `run-if-due` therefore treats the policy as due and writes the a2 fingerprint only after successful completion. `plan` has been human-readable by default since 0.4.0a2; automation that consumed the 0.4.0a1 plan JSON must add `plan --json`.

## Capacity observations and low-space handling

`plan` compares selected regular source bytes with observed destination free bytes.
Execution repeats the observation before each selected stream. These checks are
advisory and do not reserve filesystem blocks. If capacity is low or cannot be
observed, investigate the destination before large runs, but do not interpret the
warning as a guarantee that the next write will fail or succeed.

Human quantities use decimal SI labels. JSON/log byte fields are exact integers.
A later `ENOSPC` follows the ordinary execution-failure path even when a precheck
reported sufficient space.

Repeated warnings are state-based. The implementation can warn again after a
material worsening of an already-low condition. The current noise threshold is the
greater of 64 MiB and 5% of the previous warned shortfall. This threshold is not a
safety margin.

After a newly committed stream, a warning named
`unexpected_destination_capacity_consumption` can report free-space loss not
explained by measured allocation of the committed archive tree. Treat it as a
diagnostic observation, not as evidence of an external writer or a filesystem fault.

## Restore

Automatic restore is outside scope. Verify archive integrity before manually
placing archived content back into operational use. Test restoration procedures
on non-production data.
