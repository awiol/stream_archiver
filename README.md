# stream-archiver 0.2.0

`stream-archiver` safely moves complete, old filesystem streams into period-named
archive directories. It is designed for unattended Ubuntu services where a
simple age cutoff would otherwise split a continuous run of files.

The scheduler can check daily at a randomized time. A persistent state file
makes the actual archival work occur only after the configured elapsed interval,
for example every 30 days. Failed runs are not marked successful and are retried
at a later check.

## Main behavior

Each policy defines:

- one or more independent source roots;
- one destination root;
- the minimum age of the newest file in an eligible stream;
- the minimum adjacent timestamp gap that splits streams;
- a symlink rule; and
- zero or more suffix-based gzip or bzip2 rules.

Sources in one policy share settings, but they are discovered, grouped, planned,
and archived independently. Files from different source roots are never written
to the same final archive directory. Different policies may use the same exact
destination root.

A regular file with no matching compression rule has the public action `move`.
The safe implementation is:

1. copy to destination-side staging;
2. calculate source and archived SHA-256 hashes;
3. verify exact bytes, or decompress and verify compressed bytes;
4. atomically commit the complete archive directory;
5. validate the source again;
6. remove the original; and
7. write completion evidence only after cleanup succeeds.

This copy-verify-commit-delete sequence works across filesystems and avoids
removing a source before durable, verified destination data exists.

## Requirements

- Ubuntu or another Linux system; the process lock uses `fcntl.flock`;
- Python 3.11 or newer;
- no runtime dependencies outside the Python standard library;
- `systemd` only when using the included service and timer examples.

## Install

```bash
cd /opt
sudo unzip stream-archiver-0.2.0.zip
cd stream-archiver-0.2.0

python3 -m venv .venv
.venv/bin/pip install --no-index --no-deps dist/stream_archiver-0.2.0-py3-none-any.whl
```

For development and tests:

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Configure policies

Start from `examples/config/policies.toml`:

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

[[policies.compression_rules]]
suffixes = [".html"]
compression = "bz2"
```

Supported duration units are seconds (`s`), minutes (`m`), hours (`h`), days
(`d`), and weeks (`w`). Calendar months are intentionally unsupported.

Compression rules accept only `gzip` and `bz2`. Both always use compression
level 9. A level field is not supported. Rules match complete filenames
case-insensitively by suffix and append `.gz` or `.bz2` respectively.

The old singular `source = "/path"` form and schema version 1 remain accepted
for migration, but new configuration should use schema version 2 and `sources`.

### Path ownership rules

Paths are expanded and resolved to canonical absolute paths before ownership
checks, including existing symlinked parents.

- Source roots must be pairwise disjoint, including sources in the same policy.
- No source may equal, contain, or be contained by any destination.
- Destination roots may be exactly equal across policies.
- Distinct destination roots must not be nested.

### Symlink rules

- `drop-aliases-preserve-relative`: do not archive a symlink that resolves to a
  selected regular file; remove that alias after commit. Preserve other
  relative links. Leave absolute links in the source.
- `preserve-relative`: preserve relative links and leave absolute links.
- `ignore`: leave every symlink in the source.

## Validate, preview, run, and verify

```bash
stream-archiver --config /etc/stream-archiver/policies.toml check
stream-archiver --config /etc/stream-archiver/policies.toml plan
```

`plan` is read-only. Review it before the first real run.

Run immediately:

```bash
stream-archiver \
  --config /etc/stream-archiver/policies.toml \
  run \
  --lock-file /run/stream-archiver/execution.lock
```

Run only when due:

```bash
stream-archiver \
  --config /etc/stream-archiver/policies.toml \
  run-if-due \
  --state /var/lib/stream-archiver/state.json \
  --lock-file /run/stream-archiver/execution.lock
```

Recompute hashes and validate every archive under the selected policies'
destination roots:

```bash
stream-archiver \
  --config /etc/stream-archiver/policies.toml \
  verify
```

Use `--policy NAME` before the subcommand to select policies. Verification is
performed at destination scope because several policies may share a destination.

## Completion evidence

Every version-2 archive contains:

- `MANIFEST.json`: source identities, actions, source hashes, archive hashes,
  compression metadata, stream times, and cleanup state;
- `SHA256SUMS.json`: a safe JSON index of every regular archived payload and its
  SHA-256 hash; and
- `SUCCESS.json`: written only after source cleanup and a fresh archive
  verification. It contains the final manifest hash and checksum-index hash.

`SUCCESS.json` is evidence that the local transaction completed and verified at
that time. It is not a cryptographic signature and does not protect against an
attacker who can replace the archive and all evidence files together. Use
separate immutable storage, signatures, or backup tooling when authenticity is
required.

A committed archive with `cleanup_complete = false` has no valid success marker.
A later run validates unchanged sources, resumes cleanup, verifies payloads, and
then creates the marker.

## Install the systemd example

Review and edit all paths in:

- `examples/config/policies.toml`;
- `examples/systemd/stream-archiver.service`; and
- `examples/systemd/stream-archiver.timer`.

Then use the example installer or copy the files manually. The installer creates
an isolated virtual environment from the bundled wheel without network access,
validates the configuration and units, and enables the timer:

```bash
sudo ./examples/systemd/install-example.sh /opt/stream-archiver-0.2.0
sudo systemctl edit --full stream-archiver.service
sudo systemctl daemon-reload
sudo systemctl restart stream-archiver.timer
```

Inspect operation with:

```bash
systemctl list-timers stream-archiver.timer
systemctl status stream-archiver.service
journalctl -u stream-archiver.service
```

## Safety limits

The stream boundary is a heuristic based on the current filesystem snapshot. A
producer can still create a backdated file later. Producers should publish
completed files by atomic rename and avoid modifying eligible old files while
the archiver runs.

Identity and SHA-256 checks detect ordinary changes before deletion. They do not
make path deletion unconditionally race-free against a hostile concurrent
writer without a shared producer lock or completion protocol.

This tool is an archival mover with recovery and integrity evidence. It is not
an independently verified backup or automatic restore system.

See `docs/design.md`, `docs/operations.md`, and `docs/verification.md`.
