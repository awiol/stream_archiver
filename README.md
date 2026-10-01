# stream-archiver

`stream-archiver` is a policy-driven Linux mover for generated filesystem output
that arrives in timestamp-related streams. It discovers candidate entries,
groups them into streams, waits until a complete stream is old enough, commits
and verifies an archive representation, and only then removes selected source
entries.

It is not a backup, snapshot, authenticity, or automatic-restore system. Its
delete and crash-persistence claims are bounded by `docs/requirements.md`,
including local-filesystem and producer-quiescence preconditions.

## Quick start

Requirements: Linux and Python 3.11–3.13. `uv` is the recommended environment
and installer tool.

```bash
cp examples/config/policies.toml ~/stream-archiver-policies.toml
$EDITOR ~/stream-archiver-policies.toml
stream-archiver --config ~/stream-archiver-policies.toml check
stream-archiver --config ~/stream-archiver-policies.toml plan
```

`plan` is read-only and human-readable by default. Review it before destructive
execution. Use `plan --json` for the complete action-level machine representation.

```bash
stream-archiver --config ~/stream-archiver-policies.toml run
stream-archiver --config ~/stream-archiver-policies.toml verify
```

## Policy example

Configuration schema 5 makes discovery, stream grouping, archive naming, and optional archive-unit subdivision explicit:

```toml
schema_version = 5
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
recursive = true
stream_partition = "source-root"
archive_name_template = "{start:%Y-%b-%d_%H%M%S}--{end:%Y-%b-%d_%H%M%S}"
archive_unit_max_source_bytes = 10000000000
archive_unit_max_regular_files = 10000

[[policies.compression_rules]]
suffixes = [".json"]
compression = "gzip"
```

Schemas 1 and 2 remain readable and retain their historical discovery behavior; schema 3 retains explicit recursion/grouping and the historical archive-name shape. Schema 4 opts into configurable human archive names. Schema 5 additionally enables optional archive-unit byte/file targets. Current names always retain the mandatory stable `--sa-<identity>` suffix.

### Recursion, grouping, and archive layout

These are separate concepts:

- `recursive = true` discovers regular files and symlinks below real descendant
  directories. Directory symlinks are recorded as symlinks but never traversed.
- `recursive = false` considers only entries directly under each source root.
- `stream_partition = "source-root"` lets timestamp-adjacent entries anywhere
  below one source root belong to the same stream.
- `stream_partition = "parent-directory"` applies the same gap rule separately
  within each entry's exact relative parent directory.
- different configured source roots never form one stream or one archive plan;
- archived payloads preserve their source-relative directory paths. Recursive
  discovery does not flatten output.
- schema-5 `archive_unit_max_source_bytes` and `archive_unit_max_regular_files` may
  subdivide one logical stream into several archive units after grouping; they do
  not change which files belong to the logical stream.

See [the user guide](docs/user-guide.md) for diagrams, examples, and configuration
rationale.

## Stream selection

Within one configured partition, entries are ordered by modification time. A gap
of at least `stream_gap` starts a new stream. A stream is eligible only when its
newest boundary entry is at least `minimum_age` old at the planning reference
time.

The timestamp-gap rule is a heuristic. It is not a producer-completion protocol.
A producer must not keep modifying or replacing selected paths during cleanup.

## Transaction model

For each eligible stream, Stream Archiver:

1. writes payload and integrity evidence to destination-side staging;
2. verifies staged content;
3. synchronizes the required staging state;
4. atomically commits the archive directory and synchronizes its namespace;
5. re-verifies the **committed** archive;
6. revalidates selected sources and removes only selected cleanup entries;
7. synchronizes affected source directories;
8. records cleanup completion;
9. performs final verification; and
10. writes synchronized `SUCCESS.json` completion evidence.

If committed verification fails, source cleanup does not start. Pending cleanup
recovery applies the same verify-before-delete ordering.

Automatic cleanup does **not** recursively remove empty source directories.
Directories are not selected cleanup entries in the current contract.

## Symlinks

Supported rules are:

- `drop-aliases-preserve-relative`: a symlink resolving to the exact selected
  regular-file pathname under the same source root is cleanup-only; other
  relative symlinks are preserved and absolute non-alias symlinks remain in
  source;
- `preserve-relative`: relative symlinks are archived by link text; absolute
  symlinks remain in source; and
- `ignore`: symlinks remain in source and do not define stream boundaries.

Inode equality alone does not make a symlink an alias. This avoids treating an
external or otherwise different hardlink pathname as the selected target.

## Planning output

The default plan summary is designed for pre-run review. It reports policy/source
counts, discovered entries, streams, eligible streams, planned archives, selected
regular-file count and input bytes, compression/move disposition, symlink/alias
disposition, common file extensions, and source/destination context.

```bash
stream-archiver plan
stream-archiver plan --at 2026-09-27T12:00:00Z
stream-archiver plan --json
```

`--at` is read-only. Destructive CLI and exported Python execution APIs do not
accept an artificial eligibility clock.

## Run result

Immediate `run` prints a bounded human summary after successful archival work. It
includes runtime, selected/completed stream counts, files and selected link
dispositions, source and transformed payload bytes, signed payload savings, and
transformation/extension disposition.

```bash
stream-archiver run
stream-archiver run --json
stream-archiver run --markdown
stream-archiver run --report ./run-report.md
```

Use `run --json` for the complete machine result. `--report` creates a new Markdown
auxiliary report and never overwrites an existing path. Scheduled `run-if-due` keeps
its JSON-oriented result contract.

## Logs

There is **no default log file**. Manual operation writes operational logs to
stderr. The generated systemd service leaves stderr under journald, so the normal
scheduled log interface is:

```bash
journalctl -u stream-archiver.service -f
journalctl -u stream-archiver.service -p warning..alert
```

INFO is event-driven and intended for important run/policy/source and
stream/archive lifecycle events plus naturally occurring aggregate progress. Run
progress includes stream/file counts, processed source bytes, written payload bytes,
and monotonic elapsed runtime.
Normal per-entry staging, hashing, revalidation, cleanup, and lock detail is
DEBUG. WARNING/ERROR entries carry remediation context when an operator action
is useful.

The project may inspect representative INFO density against an operator
preference of roughly one useful event per 1–10 seconds while work is active.
That is only a usability heuristic: there is no timer, heartbeat, rate limiter,
or acceptance gate implementing it.

```bash
stream-archiver --log-level DEBUG --log-format json plan --json
```

JSON logs retain complete structured paths. Human entry-level diagnostics prefer
relative paths when the applicable root is already established by context.

## Verification and scheduling

`verify` is policy/source scoped by default:

```bash
stream-archiver verify
```

Audit all recognized archives under selected destinations explicitly:

```bash
stream-archiver verify --all-in-destination
```

`run-if-due` records successful per-policy timestamps plus a policy fingerprint.
The fingerprint includes discovery and stream-partition settings because they can
change selection. Valid v1 state remains readable and is conservatively due until
successful v2 state is written.

## Cooperative locking

Public destructive and verification APIs acquire hierarchical advisory locks on
the filesystem resources they operate on. Nested roots contend across cooperating
Stream Archiver processes. The locks do not control unrelated producer programs.

## systemd installation

Build a wheel or use the exact release wheel, then run:

```bash
sudo UV_BIN="$(command -v uv)" \
  ./tools/install-systemd.sh \
  --config /absolute/path/to/policies.toml
```

The installer defaults to Python 3.11. It refuses to install a wheel whose
version does not match the source tree's declared project version. It validates
configuration and planning, generates and verifies deployment-specific units,
and reloads systemd. It does **not** start archival movement or enable the timer.

Generated units use `ProtectHome=read-only`, `ProtectSystem=strict`, and explicit
`ReadWritePaths`. Regenerate units after material configuration changes.

## Development

```bash
./tools/bootstrap-dev.sh --python-version 3.11
./tools/verify-local.sh
./tools/verify-local.sh --release
```

The release route builds and installs the wheel outside the source checkout and
runs the package/CLI verification surface. See `docs/verification.md` for the
exact evidence contract.

## Documentation map

- [User guide](docs/user-guide.md) — how the model works, configuration choices,
  examples, plan interpretation, logging, and FAQ.
- [Operations guide](docs/operations.md) — deployment, permissions, systemd,
  recovery, and upgrades.
- [Requirements](docs/requirements.md) — normative product contract.
- [Design](docs/design.md) — selected mechanisms and rationale.
- [Verification](docs/verification.md) — maintainer/release checks and evidence.
- [CHANGELOG](CHANGELOG.md) — user-visible candidate history.
