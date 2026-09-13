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

The timestamp-gap rule is a heuristic, not a producer-completion protocol.

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

Operational logs go to stderr/journald and machine-readable command results stay
on stdout. Every CLI invocation has a `run_id`.

Useful fields include `phase`, `operation`, `outcome`, `policy_name`,
`source_root`, `source_path`, `destination_root`, `archive_name`,
`archive_directory`, and `plan_id` where applicable. Older compatibility events
may retain additional fields.

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

Do not edit a manifest to force cleanup after a source conflict. Preserve the
available copies and resolve the conflict explicitly.

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

Completed supported 0.3 archives remain verifiable. Valid v1 scheduling state is
readable but affected policies are due until a successful v2 fingerprinted state
record is written. Pending supported 0.3 cleanup uses the corrected
verify-before-delete recovery ordering.

## Restore

Automatic restore is outside scope. Verify archive integrity before manually
placing archived content back into operational use. Test restoration procedures
on non-production data.
