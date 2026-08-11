# Design and behavioral contract

## Purpose

`stream-archiver` moves old filesystem output without cutting through a
continuous stream merely because the age cutoff falls inside it. Source removal
is consequential, so planning, data transfer, verification, commit, cleanup,
and evidence are explicit states.

## Policy model

Each policy owns:

- a non-empty ordered set of source roots;
- one destination root;
- minimum stream age;
- minimum stream-splitting gap;
- symlink handling; and
- suffix-to-codec compression rules.

Gzip and bzip2 are the supported codecs. Both always use level 9.

A source list is configuration reuse, not a combined data domain. Each source is
processed by a separate discovery and planning pass. `ArchivePlan.source_root`
is singular, the plan identifier includes that root, and every manifest records
it. Consequently, a stream and final directory cannot contain entries from two
sources.

Policy names are stable scheduling-state keys. A failure in any source prevents
the policy success time from advancing. A retry is idempotent because committed
plans are identified by deterministic hashes.

## Root validation

Configuration paths are expanded and resolved to canonical absolute paths before
ownership checks, including existing symlinked parents.

Source roots are exclusive ownership boundaries:

- source roots cannot equal or nest inside one another;
- a source cannot overlap any destination;
- policies may share one exact destination path; and
- distinct destination paths cannot nest.

Equal destinations are safe because final names contain a deterministic plan
hash derived from policy, source root, destination, source identities, actions,
and stream bounds.

## Stream semantics

Discovery recursively records regular files and symlinks with `lstat()` and
never follows directory symlinks. Entries are ordered by modification time in
nanoseconds, then relative path for deterministic ties. A gap greater than or
equal to `stream_gap` starts a new stream.

A stream is eligible only when its newest entry is at least `minimum_age` old.
A recent entry separated by less than the gap therefore holds back the complete
stream.

The rule is a snapshot heuristic. It cannot prove that a producer will not later
create a backdated file.

## Planned actions

- `move`: a regular file without matching compression;
- `gzip`: level-9 gzip plus source removal after commit;
- `bz2`: level-9 bzip2 plus source removal after commit;
- `preserve-symlink`: recreate a relative link without dereferencing it;
- `drop-alias-symlink`: remove an alias to a selected regular file after commit;
- `skip-symlink`: leave the link in the source.

`copy` remains accepted only in version-1 recovery manifests. New plans never
emit it.

## Compression semantics

Rules match complete filenames case-insensitively by suffix. Gzip appends `.gz`;
bzip2 appends `.bz2`. Gzip uses an empty stored filename and internal timestamp
zero. Bzip2 has no embedded source filename or modification time. The archived
filesystem mode and modification time are restored after writing.

For each compressed file, execution records:

- SHA-256 of original bytes;
- SHA-256 of compressed bytes;
- compressed size; and
- codec.

Execution decompresses the staged payload and requires its hash to equal the
source hash before commit.

Fixed payload paths must remain collision-free, including the reserved root-level
evidence names `MANIFEST.json`, `SHA256SUMS.json`, and `SUCCESS.json`. Generated
gzip/bzip2 names are different: if the preferred compressed path collides with a
fixed payload or required directory, the planner keeps all fixed source paths
unchanged and deterministically disambiguates only the generated compressed
path. The selected path becomes part of the plan identifier and manifest.

## Safe move transaction

For each eligible source-local stream:

1. Build a deterministic plan and plan identifier.
2. Create a unique staging directory on the destination filesystem.
3. Open each regular source without following a final symlink.
4. Validate source identity and write the staged payload.
5. Calculate source and archive SHA-256 values.
6. Verify direct bytes or a decompression round trip.
7. Write and sync `SHA256SUMS.json`.
8. Write and sync `MANIFEST.json` with `cleanup_complete = false`.
9. Atomically rename staging to the final period-named directory.
10. Revalidate all selected source identities and regular-file hashes.
11. Remove selected source paths.
12. Write the final manifest with `cleanup_complete = true`.
13. Reverify archive payloads and the checksum index.
14. Write `SUCCESS.json` containing hashes of the final manifest and checksum
    index.

Copying before deletion is intentional. A direct rename would be atomic only on
the same filesystem and would require rollback logic if a later entry or
compression operation failed. The selected sequence provides one consistent
contract across same- and cross-filesystem destinations.

## Evidence model

`SHA256SUMS.json` uses JSON rather than the traditional text format so arbitrary
valid Linux filenames do not create escaping ambiguity. Its entries are sorted
by archive path and include path, size, compression, and SHA-256.

`SUCCESS.json` is absent until source cleanup and post-cleanup verification both
complete. It records:

- status `completed`;
- plan, policy, source, destination, and archive identities;
- completion time;
- final manifest SHA-256; and
- checksum-index SHA-256.

The `verify` command recomputes payload hashes, decompression hashes, checksum
index content and hash, final manifest hash, and success-marker references.
These files detect accidental corruption and incomplete local work. They are not
signed provenance against a writer who controls the destination.

## Recovery states

| State | Source | Manifest | Success marker | Next action |
|---|---|---|---|---|
| staging failed | unchanged | none | none | discard partial staging |
| committed, cleanup pending | present or partly removed | false | none | validate and resume cleanup |
| cleanup done, final manifest write failed | absent | false | none | treat missing sources as removed; finalize |
| final manifest done, marker write failed | absent | true | none | verify archive and create marker |
| completed | absent | true | valid | return idempotent result |

Changed source identity or content blocks recovery and is never deleted
automatically.

## CLI configuration and local state

Routine interactive commands do not require a repeated absolute policy path.
Configuration resolution is explicit `--config`, `STREAM_ARCHIVER_CONFIG`, the
XDG per-user policy path, then `/etc/stream-archiver/policies.toml`. The systemd
unit still embeds an explicit resolved path so scheduled behavior is independent
of service environment variables.

Manual `run` locking and default `run-if-due` state use the XDG user state
directory rather than the configuration directory. Explicit `--lock-file` and
`--state` values remain authoritative.

## Scheduling

The provided timer checks daily with a randomized delay. `run-if-due` stores one
last-success timestamp per policy and runs only when `run_interval` has elapsed.
A failed policy is not marked successful.

## Compatibility

- Configuration schema versions 1 and 2 are accepted.
- Singular `source` is converted to a one-item source tuple.
- Version-1 manifests can be read and pending cleanup can be recovered.
- Version-1 archives do not acquire version-2 success evidence automatically.

## Deliberate limits

- Linux is required for the process lock.
- Only regular files and symlinks are considered.
- Hard-linked regular paths are archived independently.
- Relative symlink text is preserved, including dangling and upward-relative
  targets.
- There is no producer lock, completion-marker protocol, signing key, restore
  command, or remote-storage integration.

## Observability contract

Command results are JSON on stdout. Operational logs are emitted on stderr so
interactive use, shell pipelines, and systemd journal collection do not corrupt
machine-readable results.

Logs use named events with structured fields. Text and JSON renderings contain
the same event names and context. `INFO` records lifecycle milestones and
bounded progress; `DEBUG` records planning and verification detail. Progress is
reported as:

- policy, source, archive, action, cleanup, and verification item counters;
- source bytes completed versus total archive-plan source bytes; and
- per-file percentage milestones for files read in multiple chunks.

Byte progress measures source bytes read. It does not claim destination bytes
written because compression changes size. Text-format string fields are always
JSON-quoted; paths are carried as structured fields where possible. Source-root
validation emits a dedicated event that distinguishes missing, inaccessible or
sandboxed, symlinked, and non-directory roots. File contents are never logged.
Unexpected exceptions retain a traceback and use a distinct exit status from
expected configuration or operational failures.

## Systemd generation contract

Package-tracked systemd units are not deployment inputs. `render-systemd` loads
a validated policy and generates:

- an `ExecStart` bound to the selected installed executable and final policy
  path;
- `ConditionPathIsDirectory` and `ReadWritePaths` entries derived from every
  configured source and destination;
- a persistent randomized timer;
- journal output configuration; and
- resolved installation and manual-validation instructions.

Generation refuses to overwrite existing output unless `--force` is explicit.
`ProtectHome=true` is retained only when neither the policy configuration nor a
configured source/destination is under `/home`, `/root`, or `/run/user`. When
such access is required, the generated unit sets `ProtectHome=false` instead of
hiding a path that the policy contract requires; `ProtectSystem=strict` and the
explicit `ReadWritePaths` allow-list remain.

The guided installer uses a stable version-independent virtual environment,
copies a user-supplied policy, validates `check` and `plan` as the service user,
and installs verified units. It intentionally does not start the mover or enable
the timer.
