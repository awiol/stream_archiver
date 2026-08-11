# stream-archiver 0.3.1

`stream-archiver` safely moves complete, old filesystem streams into
period-named archive directories. It is intended for unattended Ubuntu systems
where a simple age cutoff could split a continuous sequence of files.

The scheduler may check daily at a randomized time. A persistent state file
makes actual archival work occur only after the configured elapsed interval,
for example every 30 days. A failed run is not marked successful and is retried
at a later check.

## Main behavior

Each policy defines:

- one or more independent source roots;
- one destination root;
- the minimum age of the newest file in an eligible stream;
- the adjacent timestamp gap that splits streams;
- a symlink rule; and
- zero or more suffix-based gzip or bzip2 rules.

Sources in one policy share settings, but discovery, stream grouping, archive
naming, movement, and recovery remain source-local. Files from separate source
roots never enter the same final archive directory. Separate policies may use
the same destination root.

A regular file with no compression rule has the public action `move`. The safe
cross-filesystem implementation is:

1. copy into destination-side staging;
2. calculate source and archived SHA-256 hashes;
3. verify exact bytes, or decompress and verify compressed bytes;
4. atomically commit the complete archive directory;
5. revalidate each source entry;
6. remove the original; and
7. write completion evidence after cleanup and fresh verification.

Compression supports gzip and bzip2. Both always use level 9. If a generated
compressed name conflicts with an existing payload path, for example
`data.json -> data.json.gz` when `data.json.gz` already exists, the fixed source
path is preserved and only the generated compressed path receives a
deterministic disambiguation suffix recorded in the manifest.

## Requirements

- Ubuntu or another Linux system; the process lock uses `fcntl.flock`;
- Python 3.11 or newer;
- no runtime dependencies outside the Python standard library;
- systemd only for the included scheduled-service workflow.

## Install for command-line use

The bundle contains a wheel and does not need network access:

```bash
python3 -m venv ~/.local/share/stream-archiver/venv
~/.local/share/stream-archiver/venv/bin/pip install \
  --no-index --no-deps \
  ./dist/stream_archiver-0.3.1-py3-none-any.whl
```

For development and tests:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Configure policies

Copy the example to a user-owned path. Do not edit an installed or Git-tracked
example in place.

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

[[policies.compression_rules]]
suffixes = [".html"]
compression = "bz2"
```

Duration units are seconds (`s`), minutes (`m`), hours (`h`), days (`d`), and
weeks (`w`). Calendar months are intentionally unsupported.

### Symlink rules

- `drop-aliases-preserve-relative`: drop a symlink that resolves to a selected
  regular file; preserve other relative links; leave absolute links in source.
- `preserve-relative`: preserve relative links and leave absolute links.
- `ignore`: leave all symlinks in source.

## Validate, preview, run, and verify

For a system installation, `/etc/stream-archiver/policies.toml` is discovered
automatically. For a user-managed policy, set it once in the shell environment:

```bash
export STREAM_ARCHIVER_CONFIG=~/stream-archiver-policies.toml
```

The resolution order is: explicit `--config`, `STREAM_ARCHIVER_CONFIG`,
`$XDG_CONFIG_HOME/stream-archiver/policies.toml` (or
`~/.config/stream-archiver/policies.toml`), then
`/etc/stream-archiver/policies.toml`.

A normal first review needs only:

```bash
stream-archiver plan
```

`plan` loads and validates the configuration before scanning sources, so a
separate `check` is not required before it. `check` remains useful when only
configuration syntax and policy validation should run.

Run immediately:

```bash
stream-archiver run
```

Manual runs place their default lock under `$XDG_STATE_HOME/stream-archiver` or
`~/.local/state/stream-archiver`; `--lock-file` remains available for explicit
overrides.

Verify every committed archive under the selected destination roots when an
independent audit is wanted:

```bash
stream-archiver verify
```

A successful `run` already verifies staged payload bytes and completion evidence
as part of the safe-move transaction. Use `--policy NAME` before the subcommand
to select policies.

## Logging and progress

Operational logs are written to stderr. JSON command results remain on stdout,
so scripts can consume them without mixing them with logs.

```bash
stream-archiver \
  --config ~/stream-archiver-policies.toml \
  --log-level DEBUG \
  --log-format text \
  plan
```

Supported log levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
`--log-format json` emits one structured JSON log event per line.

At `INFO`, unattended runs report:

- configuration and due-state decisions;
- policy, source, archive, and action counters;
- per-file and overall source-byte progress, including bounded percentages;
- staging, commit, cleanup, recovery, verification, and success-evidence stages;
- failures with a corrective action.

`DEBUG` adds discovery, stream-selection, plan, lock, per-chunk milestone, and
payload-verification details. Text-log string fields are always quoted, so a
path containing spaces cannot visually run into the following field. Source
access failures also report a structured `source` and `reason`. Paths and hashes
are logged, but file contents are not.

For systemd:

```bash
journalctl -u stream-archiver.service -f
journalctl -u stream-archiver.service -p warning..alert
```

## Systemd onboarding

There are two supported workflows. Neither requires editing a tracked service
file or a version-specific hardcoded path.

### Guided installer

1. Create and review a user-owned policy file.
2. Build or use the bundled wheel.
3. Run:

```bash
sudo ./tools/install-systemd.sh \
  --config /absolute/path/to/your-policies.toml
```

Common deployment options can be supplied directly, for example:

```bash
sudo ./tools/install-systemd.sh \
  --config /absolute/path/to/your-policies.toml \
  --on-calendar daily \
  --randomized-delay 6h \
  --service-log-level INFO
```

The installer:

- validates arguments and protects an existing installed policy before making
  account or package changes;
- verifies the bundled wheel against `SHA256SUMS` when that release evidence is
  present;
- installs into the stable path `/opt/stream-archiver/venv`;
- copies the supplied policy to `/etc/stream-archiver/policies.toml`;
- creates the service account when needed;
- validates the configuration and read-only plan as the service user;
- generates units from the actual executable, policy paths, sources, and
  destinations;
- verifies and installs the units; and
- does **not** run archival work or enable the timer.

It prints the exact manual-run and enablement commands. An existing installed
configuration is not replaced unless `--replace-config` is supplied. The
installer also accepts service name, user/group, calendar schedule, randomized
delay, timer accuracy, and service log options; run it with `--help` for the
complete interface.

### Review-first unit generation

Install the wheel, put the policy at its intended final path, then generate a
reviewable deployment bundle:

```bash
/opt/stream-archiver/venv/bin/stream-archiver \
  --config /etc/stream-archiver/policies.toml \
  render-systemd \
  --executable /opt/stream-archiver/venv/bin/stream-archiver \
  --output-directory ./generated-systemd
```

The output contains:

- `stream-archiver.service` with `ReadWritePaths` derived from the policy;
- `stream-archiver.timer`; and
- `INSTALL.md` with resolved validation, installation, test, and enablement
  commands.

Regenerate with `--force` after executable, policy-path, source, destination,
identity, schedule, or service-log changes. Review the diff rather than editing
the generated unit. The generator keeps `ProtectHome=true` when possible, but
disables that sandbox when the policy or configuration must access `/home`,
`/root`, or `/run/user`; `ProtectSystem=strict` and generated `ReadWritePaths`
remain in effect.

## Completion evidence

Every current archive contains:

- `MANIFEST.json`: source identities, actions, source and archive hashes,
  compression metadata, stream times, and cleanup state;
- `SHA256SUMS.json`: a JSON index of archived regular payload hashes; and
- `SUCCESS.json`: written only after source cleanup and fresh verification.

`SUCCESS.json` proves that the local transaction completed and verified at that
time. It is not a cryptographic signature against an actor able to replace the
payload and all evidence files together.

## Safety limits

The stream boundary is a heuristic based on the current filesystem snapshot. A
producer can still create a backdated file later. Producers should publish
completed files by atomic rename and avoid modifying eligible old files while
the archiver runs.

Identity and SHA-256 checks detect ordinary changes before deletion. They do not
make deletion fully race-free against a hostile concurrent writer without a
shared producer lock or completion protocol.

This tool is an archival mover with recovery and integrity evidence. It is not
an independently verified backup or automatic restore system.

See `docs/design.md`, `docs/operations.md`, `docs/usability-review.md`, and
`docs/verification.md`.
