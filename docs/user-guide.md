# Stream Archiver user guide

This guide explains the operating model behind Stream Archiver, why the main
policy settings exist, and how to review its behavior before allowing source
cleanup. For deployment/recovery procedures, use `operations.md`. For normative
behavior, use `requirements.md`.

## 1. Mental model

Stream Archiver is a **move-after-verified-copy** tool for timestamp-related
filesystem output. It does not move one file merely because that file is old.
It first decides which files belong to one logical stream, then waits until the
whole stream is old enough.

For one configured source root:

```text
source root
  ├── run-a/file-1   10:00
  ├── run-a/file-2   10:10
  └── run-a/file-3   18:30

stream_gap = 8h

stream 1: file-1, file-2
stream 2: file-3        # 8h20m gap starts a new stream
```

A stream becomes eligible when its newest boundary entry is at least
`minimum_age` old. This avoids archiving the old beginning of a stream while a
newer continuation is still nearby in time.

The heuristic does not prove producer completion. If the producer can continue
writing old paths, coordinate producer completion separately.

## 2. Source roots are independent

A policy can have several `sources`, but a stream never spans those roots.
Each eligible stream creates its own archive plan and archive directory.

```toml
sources = ["/data/source-a", "/data/source-b"]
destination = "/archive"
```

This does **not** merge `/data/source-a` and `/data/source-b` into one archive.
Both may write archive directories below `/archive`, but each archive manifest
records exactly one source root.

## 3. Recursive discovery

Schema 3 makes recursion explicit:

```toml
recursive = true
```

With `true`, Stream Archiver traverses real descendant directories. It never
traverses a directory symlink. With `false`, it considers only entries directly
under the configured source root.

Example:

```text
/source/root.txt
/source/day-1/a.dat
/source/day-2/b.dat
```

`recursive = false` discovers `root.txt` but not the two nested files.
`recursive = true` discovers all three regular files.

Schemas 1 and 2 remain supported and behave as `recursive = true`.

## 4. Stream partitioning

Recursion answers **what gets discovered**. Stream partitioning answers **which
discovered paths may be grouped together by time**. These settings are
independent.

### `source-root`

```toml
stream_partition = "source-root"
```

This preserves historical behavior. All boundary entries under one source root
are ordered together by modification time. Entries in different subdirectories
can therefore belong to one stream.

```text
/source/a/x.dat  10:00
/source/b/y.dat  10:02
```

With an 8-hour gap, these entries are one stream.

### `parent-directory`

```toml
stream_partition = "parent-directory"
```

The same time-gap algorithm runs independently in each entry's exact relative
parent directory. In the example above, `a/x.dat` and `b/y.dat` belong to
separate stream domains even though their timestamps are close.

Use `parent-directory` when directories already represent independent producer
batches or channels. Use `source-root` when directory boundaries are merely
organizational and the logical stream can span them.

Schemas 1 and 2 behave as `stream_partition = "source-root"`.

## 5. Relative directory structure is preserved

Recursive discovery does not flatten output. If an eligible payload contains:

```text
/source/day-1/a.dat
/source/day-1/meta.json
/source/day-2/b.dat
```

then one archive plan that contains those paths stores them as:

```text
/archive/<archive-name>/day-1/a.dat
/archive/<archive-name>/day-1/meta.json[.gz]
/archive/<archive-name>/day-2/b.dat
```

Compression can add its documented suffix/disambiguation, but the source-relative
path is otherwise retained. This avoids filename collisions and preserves useful
provenance.

There is no flatten-output mode in the current 0.4 line.

## 6. Policy fields

A schema-3 policy has this core shape:

```toml
schema_version = 3
run_interval = "30d"

[[policies]]
name = "reports"
sources = ["/srv/reports"]
destination = "/srv/archive/reports"
minimum_age = "30d"
stream_gap = "8h"
symlink_rule = "drop-aliases-preserve-relative"
recursive = true
stream_partition = "source-root"
```

### `minimum_age`

The age threshold applies to the **newest boundary entry of a stream**, not to a
file independently of its neighbors.

If one entry is 40 days old and another entry in the same stream is 20 days old,
the stream is not eligible for a 30-day policy.

### `stream_gap`

This controls continuity. A gap **greater than or equal to** `stream_gap` starts
a new stream. Smaller gaps do not.

Choose a value based on the producer's normal maximum quiet interval inside one
logical output stream, with enough margin for ordinary pauses. It is not a data
retention duration.

### `recursive`

Controls traversal depth only. See sections 3 and 4.

### `stream_partition`

Controls the grouping domain only. See section 4.

### `symlink_rule`

`ignore` leaves symlinks in source and excludes them from stream boundaries.

`preserve-relative` archives relative symlinks by link text. Absolute symlinks
remain in source.

`drop-aliases-preserve-relative` additionally recognizes a symlink as an alias
only when it resolves to the **exact discovered regular-file pathname** under the
same source root and that target is selected. The alias is then cleanup-only and
is removed after its target payload source. Inode equality alone is insufficient:
an external or different hardlink pathname must not authorize cleanup.

A preserved relative symlink can be dangling after archival. Its link text is
what the contract preserves.

### compression rules

Compression rules match complete filename suffixes case-insensitively:

```toml
[[policies.compression_rules]]
suffixes = [".json", ".ndjson"]
compression = "gzip"
```

Supported codecs are `gzip` and `bz2`, fixed at level 9. Source content hash and
compressed payload hash are both recorded; verification decompresses compressed
payloads and rechecks the source-content hash.

### `run_interval`

This is the scheduled due interval. It is independent of `minimum_age`.
`minimum_age` controls stream eligibility; `run_interval` controls how frequently
a successfully completed policy becomes due again when using `run-if-due`.

## 7. Review a plan

Run:

```bash
stream-archiver --config /path/to/policies.toml plan
```

The default output is for human review. It shows:

- reference time and read-only snapshot warning;
- source, discovered-entry, stream and eligibility counts;
- planned archive count;
- selected regular-file count and **input** size;
- move/gzip/bzip2 disposition;
- preserved-symlink and cleanup-only-alias counts;
- common file extensions; and
- per-source destination context.

The byte figures are source/input bytes. The planner does not guess compressed
output size. If selected work exists, the plan also reports destination free space
as an advisory comparison. A warning means observed free bytes are below selected
source bytes; it does not reserve space or block execution.

Human sizes use decimal SI units (`kB`, `MB`, `GB`, `TB`). Structured JSON and log
fields retain exact integer byte values.

For action-level automation or inspection:

```bash
stream-archiver plan --json
```

For a reproducible read-only planning reference:

```bash
stream-archiver plan --at 2026-09-27T12:00:00Z
```

Artificial planning time is deliberately unavailable to destructive execution.
A plan can become stale immediately because it does not lock producers or the
filesystem.

## 8. Run and verify

After reviewing a plan:

```bash
stream-archiver run
```

The transaction stages and verifies destination content, commits it, verifies the
committed path again, then revalidates and removes selected source entries.
Completion evidence is written only after cleanup persistence and final
verification.

During a destructive invocation, INFO progress reports the current stream/archive
out of total, completed regular files out of total, processed source bytes out of
total, written payload bytes after transformation/compression, and monotonic elapsed
runtime. File completion advances only after its archive transaction completes.
All selected policies in that invocation use one planning reference time, so a stream
that becomes old enough only while earlier work is running waits until a later run.
This keeps the progress denominator stable.

Before each selected stream, Stream Archiver rechecks destination free space against
the remaining selected source bytes for that exact destination. Low or unavailable
capacity is advisory: it does not reserve space or bypass normal write failures.
Repeated warnings are de-duplicated by capacity state rather than time. A separate
**unexpected destination capacity consumption** diagnostic can report material
free-space loss that the measured archive allocation does not explain; it does not
identify the cause.

Human byte quantities use decimal SI units (`kB`, `MB`, `GB`, `TB`); JSON/log byte
fields remain exact integers.

Verify archives owned by selected policies/source roots:

```bash
stream-archiver verify
```

Audit every recognized archive under selected destination roots:

```bash
stream-archiver verify --all-in-destination
```

## 9. What source cleanup changes

Source cleanup removes only selected regular files and cleanup-only alias
symlinks. It does **not** recursively remove empty source directories.

For example, after moving:

```text
/source/run-1/data.bin
```

`/source/run-1/` remains as an empty directory. This bounds automatic namespace
mutation to explicit selected entries and avoids deleting pre-existing empty
directories that were never part of the plan.

## 10. Logs

Stream Archiver creates no log file by default.

Manual CLI:

```text
stdout  command result / plan presentation
stderr  operational logs
```

Generated systemd service:

```bash
journalctl -u stream-archiver.service -f
```

### INFO

INFO is event-driven. It includes important policy/source and stream/archive
lifecycle events such as planning summaries, archive start/commit/committed
verification/source-cleanup start/completion, recovery that actually acts, and
aggregate staging progress derived from work already being performed.

### DEBUG

DEBUG includes normal per-entry discovery, staging, hashing, source revalidation,
cleanup, lock details, and other diagnosis-oriented mechanics.

### WARNING and ERROR

These levels identify degraded or failed conditions that warrant operator
attention. When known, a failure diagnostic uses `next_action` for remediation
and states retry/preserved-state information rather than attaching suggestions to
all successful events.

The project may measure representative INFO output against a soft preference of
roughly one useful event every 1–10 seconds while active work is progressing.
This is only a post-hoc usability metric. It does not cause time-driven log
emission and is not a release gate.

## 11. Scheduling state

`run-if-due` records a version-2 due-state file after successful policy
completion. The policy fingerprint includes every setting that can change stream
selection or archive representation, including recursion and stream partitioning.

A changed fingerprint makes the policy due. Valid v1 state is readable but is
conservatively considered due until a successful v2 record exists.

## 12. Multiple policies and shared destinations

Policies can share the exact same destination root. Archive identity and manifest
ownership keep source plans separate. Nested but unequal destination roots are
rejected because their ownership and lock domains would be ambiguous.

Source roots must be pairwise disjoint and must not overlap destination roots.
Configured source/destination roots themselves must be real directories rather
than symlinks.

## 13. Cooperative concurrency

Stream Archiver acquires hierarchical advisory filesystem locks. `/a` conflicts
with a cooperating operation on `/a/b`; independent sibling resources can remain
concurrent.

These locks coordinate Stream Archiver processes only. They do not prevent an
unrelated producer from changing files or replacing a pathname. Producer content
and namespace quiescence is therefore an operating precondition at cleanup.

## 14. Recovery model

A committed archive with `cleanup_complete: false` is pending. On recovery,
Stream Archiver first verifies the committed archive. If that verification fails,
remaining source entries are preserved.

If cleanup partially happened before interruption, recovery reconciles already
missing entries with still-present entries, revalidates remaining entries, and
continues only the idempotent cleanup actions whose preconditions still hold.

Do not edit a manifest to force cleanup after a conflict.

## 15. Archive evidence

A completed current archive contains:

- `MANIFEST.json` — plan ownership, actions, identities, hashes, timestamps, and
  cleanup state;
- `SHA256SUMS.json` — archived regular-payload hash index; and
- `SUCCESS.json` — completion evidence written after cleanup persistence and
  final verification.

This evidence detects inconsistency within the package's local integrity model.
It is not a cryptographic signature and does not protect against an actor that
can replace payload and all evidence together.

## 16. systemd operation

The generated service leaves operational stderr to journald. The installer does
not enable the timer automatically. Review a plan, run the service manually,
verify resulting archives, then enable recurrence as described in
`operations.md`.

`render-systemd --force` replaces generated output pathnames atomically. An
existing output symlink is replaced rather than followed.

The guided installer refuses to choose a wheel with a version different from the
source tree's declared package version.

## 17. Frequently asked questions

### Does Stream Archiver scan subdirectories?

Yes for schema 1/2 and for schema 3 with `recursive = true`. Use
`recursive = false` for top-level-only discovery.

### Can one stream span multiple directories?

Yes with `stream_partition = "source-root"`. No across different exact parent
directories with `stream_partition = "parent-directory"`.

### Can one stream span multiple configured source roots?

No. Source roots are always independent planning domains.

### Is the original directory hierarchy preserved?

Yes. Archive payload paths preserve source-relative structure; output is not
flattened.

### Does `minimum_age = "30d"` mean each file is considered independently?

No. The newest boundary entry in the complete stream must be at least 30 days
old.

### What exactly does `stream_gap = "8h"` mean?

An adjacent modification-time gap of 8 hours or more starts a new stream. A gap
one nanosecond smaller does not.

### Why did an old file remain unarchived?

A newer boundary entry can keep the whole stream ineligible. Check the plan,
partition mode, and neighboring timestamps.

### Why can an alias link be removed even when its own timestamp is newer?

Alias links do not define stream eligibility. Under
`drop-aliases-preserve-relative`, cleanup follows the exact selected target path.
The link is removed only when that target participates in the archive plan.

### Are absolute symlinks archived?

Absolute **non-alias** symlinks are not archived. Under alias-dropping policy, a
symlink is cleanup-only only when it resolves to the exact selected in-source
regular-file pathname.

### Where is the default log file?

There is none. Manual logs use stderr. Scheduled service logs are normally read
from journald.

### Why is INFO quieter than DEBUG?

INFO is intended to show meaningful stream/archive-level progress. DEBUG carries
normal entry-level mechanics.

### What does `staging_percent=100` mean?

Only that source bytes have been staged. It is not transaction completion.
`transaction_percent=100` appears only after cleanup persistence, final
verification, and completion evidence.

### Are empty source directories removed?

No. The current cleanup contract removes selected entries only.

### Can Stream Archiver restore data automatically?

No. Automatic restore is outside current scope.

### What if a producer modifies a selected file during archival?

Observable identity/content changes cause cleanup to fail closed. The package
cannot stop a non-cooperating producer from writing an already-open inode or
replacing a pathname in the final validation/unlink race. Coordinate producer
quiescence separately.

### What happens after I change a policy used by `run-if-due`?

If the changed field affects selection or archive representation, the policy
fingerprint changes and the policy becomes due on its next evaluation.

## 18. Next references

- `operations.md` — installation, first-run gate, permissions, recovery, upgrade.
- `requirements.md` — normative requirements and supported boundaries.
- `design.md` — architecture and decision rationale.
- `verification.md` — verification and release evidence.
