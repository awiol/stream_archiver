# stream-archiver

`stream-archiver` is a policy-driven Linux archival mover for filesystem output
that arrives as timestamped streams. It groups source entries by modification
time, waits until a complete stream is old enough, commits a verified archive
representation, and then removes the selected source entries.

The package is not a backup, snapshot, authenticity, or automatic-restore
system. Its deletion and durability guarantees are bounded by the requirements
in `docs/requirements.md`, including producer-quiescence and local-filesystem
preconditions.

## What 0.4 changes

The first 0.4 alpha corrects transaction and operator contracts that were unsafe
or ambiguous in the 0.3 line:

- the **final committed archive** is verified before normal source cleanup;
- cleanup recovery verifies the committed archive before deleting any remaining
  source entry;
- alias symlinks are classified across the complete source root before stream
  segmentation, so an alias follows its selected target across timestamp gaps;
- artificial time exists only as read-only `plan --at`; destructive commands use
  the observed execution clock;
- `verify` is policy-scoped by default and `verify --all-in-destination` is the
  explicit destination-wide audit;
- archive-shaped directories with missing required evidence are errors;
- cooperating Stream Archiver processes use hierarchical filesystem-resource
  locks rather than invocation-specific lock files;
- scheduled state format v2 records a policy fingerprint; valid v1 state is
  conservatively due until a successful v2 write;
- generated systemd units no longer use operational `ConditionPath...` gates;
  prerequisite failures are reported by the application; and
- staging progress is distinguished from transaction completion.

## Archival model

Each policy defines an ordered list of source roots, one destination root,
`minimum_age`, `stream_gap`, one symlink rule, and optional compression rules.
Sources share policy settings but never share one stream, plan, manifest, or
archive directory.

A **stream** is a maximal source-local sequence of boundary entries where every
adjacent modification-time gap is less than `stream_gap`. A stream is eligible
only when its newest boundary entry is at least `minimum_age` old at the planning
reference time.

For an uncompressed regular file, the logical `move` operation is implemented as
this staged transaction:

1. copy to destination-side staging and calculate source/archive SHA-256 hashes;
2. verify bytes, including decompression round trips for gzip/bzip2 payloads;
3. synchronize required payload metadata and staging namespace state;
4. atomically rename the staging directory to the final archive directory;
5. synchronize the destination namespace;
6. re-verify the payload and checksum evidence at the **final committed path**;
7. revalidate selected source identity/content and remove selected entries;
8. synchronize affected source directories;
9. write the final cleanup-complete manifest;
10. perform final archive verification; and
11. write and synchronize `SUCCESS.json`.

If final-path verification fails, source cleanup does not begin. During recovery,
a corrupt committed archive likewise preserves every remaining source entry.

### Durability boundary

The durability contract targets local Linux filesystems where successful
regular-file `fsync`, directory `fsync`, same-filesystem rename/unlink, and
advisory locking provide their documented semantics. Do not apply the same
unqualified durability claim to NFS, SMB, FUSE, overlay, or other filesystems
without separate verification.

### Producer-quiescence boundary

Stream Archiver checks filesystem identity, size, mtime, type, symlink text, and
regular-file content before deletion. It fails closed on observable changes.
These checks cannot prevent a non-cooperating producer that already holds an
open descriptor from writing the same inode after validation, or a producer
from replacing the selected pathname between validation and unlink. Producers
must therefore quiesce both content and namespace changes for selected entries
during cleanup, or participate in a stronger external coordination protocol.

## Compression and symlinks

Compression supports gzip and bzip2 at fixed level 9. If a generated compressed
name collides with an existing payload path, fixed source paths stay unchanged
and only the generated compressed path receives a deterministic suffix recorded
in the manifest.

Symlink rules are:

- `drop-aliases-preserve-relative`: an alias resolving to a selected regular file
  is cleanup-only and follows that target; other relative symlinks are preserved;
  absolute links remain in source;
- `preserve-relative`: relative symlinks are archived with their link text;
  absolute links remain in source; and
- `ignore`: symlinks remain in source and do not define stream boundaries.

A preserved non-alias relative symlink is a boundary entry and uses its own
modification time.

## Requirements

- Linux;
- Python 3.11, 3.12, or 3.13;
- no third-party runtime Python dependencies;
- `uv` for the recommended installer/development workflow; and
- systemd only for the scheduled-service workflow.

Source and destination roots for destructive/scheduled operation must already
exist as real directories. Missing operational paths are visible failures.

## Development setup

The repository supplies explicit environment and verification entry points:

```bash
./tools/bootstrap-dev.sh --python-version 3.11
./tools/verify-local.sh
```

`bootstrap-dev.sh` runs `uv sync --frozen --extra dev`. Use `--offline` only when
the required Python and package artifacts are already cached. `verify-local.sh`
does not download dependencies: it uses `.venv/bin/python` when available,
otherwise `python3`. It reports Ruff as unavailable rather than treating a
missing linter as a passing lint result.

For release-oriented local checks:

```bash
./tools/verify-local.sh --release
```

That route additionally builds a wheel, installs it outside the source tree,
runs the test suite against the installed package, and smoke-tests the installed
CLI. See `docs/verification.md` for the evidence contract.

## Configure policies

Copy the example to a user-owned path:

```bash
cp examples/config/policies.toml ~/stream-archiver-policies.toml
$EDITOR ~/stream-archiver-policies.toml
```

Example:

```toml
schema_version = 2
run_interval = "30d"

[[policies]]
name = "generated-reports"
sources = [
  "/srv/application/reports-a",
  "/srv/application/reports-b",
]
destination = "/srv/archive/application-output"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "drop-aliases-preserve-relative"

[[policies.compression_rules]]
suffixes = [".json"]
compression = "gzip"
```

Duration units are seconds (`s`), minutes (`m`), hours (`h`), days (`d`), and
weeks (`w`). Calendar months are not supported.

## Validate, plan, run, and verify

Configuration resolution order is:

1. explicit `--config`;
2. `STREAM_ARCHIVER_CONFIG`;
3. `$XDG_CONFIG_HOME/stream-archiver/policies.toml` or
   `~/.config/stream-archiver/policies.toml`; and
4. `/etc/stream-archiver/policies.toml`.

Configuration-only validation:

```bash
stream-archiver check
```

Read-only planning with the current clock:

```bash
stream-archiver plan
```

For reproducible read-only planning only:

```bash
stream-archiver plan --at 2026-09-13T12:00:00Z
```

Run selected policies immediately:

```bash
stream-archiver run
```

The global 0.3 `--now` option is intentionally removed from destructive
execution. `--lock-file` remains accepted on `run` and `run-if-due` only as a
deprecated migration option; it is ignored and does not redefine the 0.4 lock
domain.

Verify archives owned by selected policies/source roots:

```bash
stream-archiver verify
```

Audit all recognized archive directories under the selected destination roots:

```bash
stream-archiver verify --all-in-destination
```

Use `--policy NAME` before the subcommand to select policies.

## Cooperative locking

Destructive operations acquire advisory hierarchical locks on canonical source
and destination resource paths. Relevant ancestors are shared and mutation roots
are exclusive. Consequently an operation on `/a` conflicts with one on `/a/b`,
while independent sibling roots can remain concurrent.

`verify` uses shared resource locks. `plan` remains an unlocked read-only
snapshot and can become stale immediately after it is produced.

The locks coordinate **Stream Archiver processes only**. They do not prevent
unrelated programs from changing source files.

## Scheduling state

`run-if-due` writes state format v2 only after successful policy completion. Each
record includes the successful time and a SHA-256 fingerprint of the ordered
canonical sources, destination, age/gap settings, symlink rule, and compression
rules. A changed or missing fingerprint makes the policy due.

Valid v1 state remains readable. Because v1 has no trustworthy matching
fingerprint, affected policies are conservatively due until they complete and a
v2 record is written.

## Logging and progress

Command results remain JSON on stdout. Operational logs go to stderr/journald.
Each CLI invocation has a `run_id`. Named phases distinguish staging,
committed verification, source revalidation/cleanup, cleanup persistence, final
verification, and completion.

Per-file `percent` and `staging_percent` may reach 100% when payload staging is
complete. Transaction completion is reported separately as
`transaction_percent=100` only after final verification/completion evidence.

```bash
stream-archiver --log-level DEBUG --log-format json plan
```

File contents are not written to logs.

## Systemd onboarding

Build a wheel or use a release wheel, then run the guided installer. If `uv` is
not in root's `PATH`, pass it explicitly:

```bash
sudo UV_BIN="$(command -v uv)" \
  ./tools/install-systemd.sh \
  --config /absolute/path/to/policies.toml
```

The installer defaults to Python 3.11; use `--python-version` to select another
declared supported interpreter. It installs to a stable application path,
validates the policy/plan as the service user, generates deployment-specific
units, runs unit verification, and reloads systemd. It does **not** run archival
movement or enable the timer.

Generated units use `ProtectHome=read-only`, `ProtectSystem=strict`, and explicit
`ReadWritePaths`. They do not use `ConditionPath...` to suppress application
failures and do not pass the obsolete `--lock-file` option. Regenerate units
after upgrading from 0.3.

## Archive evidence

A completed current archive contains:

- `MANIFEST.json`: ownership, source identity, actions, source/archive hashes,
  compression metadata, selection/payload time ranges, and cleanup state;
- `SHA256SUMS.json`: the machine-readable archived regular-payload hash index;
- `SUCCESS.json`: completion evidence written only after cleanup persistence and
  final verification.

`SUCCESS.json` is local transaction evidence, not a cryptographic signature.
An actor that can replace payload and all evidence files together is outside
this integrity model.

See `docs/requirements.md`, `docs/design.md`, `docs/operations.md`, and
`docs/verification.md`.
