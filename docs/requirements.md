# Stream Archiver Requirements

**Document version:** `0.4.0-alpha.2`
**Target package development version:** `0.4.0a1`
**Date:** 2026-09-13
**Status:** implementation contract for the first 0.4 alpha candidate
**Repository intent:** repository requirements; supersedes the 0.4.0-alpha.1 draft

## 1. Purpose

Stream Archiver must move complete, old filesystem output streams from one or more source roots to configured archive destinations without deleting a source entry before the committed archive representation satisfies the defined integrity and recovery requirements.

The system is intended for generated or operational files on Linux. It is not a general backup system, filesystem snapshot system, or metadata-preserving replacement for `rename(2)`.

## 2. Normative vocabulary

- **must** states a requirement.
- **must not** states a prohibition.
- **should** states the default recommendation; a justified exception is permitted.
- **may** states permission.
- **can** states capability or possibility.

## 3. Project terminology

### 3.1 Source root

A **source root** is one configured canonical directory from which eligible source entries can be removed.

### 3.2 Source path

A **source path** is one relative path under a source root.

### 3.3 Source entry

A **source entry** is a discovered regular file or symlink that is relevant to policy evaluation. Directory symlinks are never traversed.

### 3.4 Regular file

A **regular file** is a filesystem regular file discovered with `lstat()` semantics.

### 3.5 Alias symlink

An **alias symlink** is a symlink whose resolved target identifies a regular file under the same source root by filesystem identity. Alias classification is source-root-wide and occurs before stream segmentation.

### 3.6 Boundary entry

A **boundary entry** is a source entry whose timestamp participates in stream segmentation.

Alias symlinks do not participate in stream segmentation. A symlink that the selected symlink policy ignores or skips does not participate in stream segmentation.

### 3.7 Stream

A **stream** is a maximal ordered sequence of boundary entries for one source root in which every adjacent modification-time gap is less than `stream_gap`.

A gap greater than or equal to `stream_gap` starts a new stream.

### 3.8 Eligible stream

A stream is **eligible** when its newest boundary entry is at least `minimum_age` old at the planning reference time.

### 3.9 Archive plan

An **archive plan** is the deterministic mapping from one eligible source-root stream to archive payload actions and cleanup-only actions.

### 3.10 Archive directory

An **archive directory** is one committed destination directory for one archive plan.

### 3.11 Cleanup recovery

**Cleanup recovery** is continuation of an archival operation that has already committed its archive directory but has not completed source cleanup and final completion evidence.

### 3.12 Restore

**Restore** means copying or reconstructing archived content back into operational source/use locations. Restore is outside the current product scope.

## 4. Scope and non-goals

### R-SCOPE-001 — Linux filesystem scope

The system must target Linux filesystems and may rely on Linux/POSIX filesystem primitives that are documented as implementation requirements.

**Acceptance evidence:** supported-platform declaration and platform-specific tests.

### R-SCOPE-002 — No general backup claim

The system must not claim to provide independent backup, remote redundancy, cryptographic authenticity, or automatic restore.

### R-SCOPE-003 — No producer-completion protocol

The system may use timestamp-gap heuristics. It must document that the heuristic cannot prove that a producer will not later create or modify backdated data.

### R-SCOPE-004 — Producer-quiescence boundary

The safe-deletion contract applies only when a selected source entry is quiescent at cleanup. A non-cooperating producer must not continue writing the selected inode through an already-open file descriptor, and it must not replace, rename over, or recreate the selected pathname between Stream Archiver's final source validation and unlink.

Stream Archiver must detect observable source identity or content changes before deletion and fail closed. It must not claim that path validation or advisory locking can prevent writes or namespace replacement by an unrelated process that does not participate in the project protocol.

## 5. Configuration requirements

### R-CONFIG-001 — Policy fields

Each policy must define:

- a stable policy name;
- an ordered list of unique source roots;
- one destination root;
- `minimum_age`;
- `stream_gap`;
- one symlink rule; and
- zero or more compression rules.

### R-CONFIG-002 — Multiple sources share policy settings

A policy may contain multiple source roots. Shared policy settings must not merge streams, plans, manifests, cleanup state, or archive directories across source roots.

### R-CONFIG-003 — Shared exact destinations

Two or more policies may use the same exact destination root.

The configuration validator must reject source/destination relationships that create ownership ambiguity, recursive archival, or nested destination ambiguity.

### R-CONFIG-004 — Canonical paths

The system must canonicalize configured roots for overlap, ownership, and lock-domain decisions. A symlink alias to another configured root must not bypass these checks.

### R-CONFIG-005 — Compression codecs

The only supported compression codecs are `gzip` and `bz2` unless a later requirements version adds another codec.

Compression level must be fixed at 9 and must not be configurable.

## 6. Discovery and stream requirements

### R-DISC-001 — Non-following discovery

Discovery must use non-following filesystem inspection. Directory symlinks must not be traversed.

### R-DISC-002 — Discovery failure visibility

A missing, inaccessible, symlinked, or non-directory source root must cause an actionable failed operation. Discovery must not silently omit an inaccessible subtree that is required by the policy.

### R-STREAM-001 — Source-local segmentation

Stream segmentation must operate independently for each source root.

### R-STREAM-002 — Exact gap boundary

For adjacent boundary entries with modification times `t1` and `t2`, a new stream must begin when:

`(t2 - t1) >= stream_gap`.

The comparison must not use floating-point time arithmetic.

### R-STREAM-003 — Exact age boundary

A stream must be eligible when:

`planning_reference_time - newest_boundary_entry_time >= minimum_age`.

### R-STREAM-004 — Alias symlinks do not define boundaries

An alias symlink must not extend, merge, or split a stream. Its cleanup action is derived from the selected state of its target regular file.

### R-STREAM-005 — Skipped symlinks do not define boundaries

A symlink that the configured rule will leave in the source must not extend, merge, or split a stream.

## 7. Symlink requirements

### R-SYM-001 — Source-root-wide alias classification

The system must determine alias symlink identity across the complete discovered source root before stream segmentation.

### R-SYM-002 — Alias cleanup follows target selection

If an alias symlink points to a regular file selected for removal by an archive plan, the symlink must be treated as a cleanup-only entry in that same transaction, regardless of the symlink's own modification time.

If the target regular file is not selected, the alias symlink must remain in the source.

### R-SYM-003 — Preserve-relative rule

When the selected symlink rule preserves a non-alias relative symlink, the archive must store the symlink link text without dereferencing it.

The system must document that the archived symlink can be dangling when its target is outside the archived payload.

### R-SYM-004 — Absolute symlinks

Absolute symlinks must not be copied into the archive unless a later requirements version explicitly defines a safe supported behavior.

### R-SYM-005 — Ignored symlinks

Ignored or unsupported symlinks must remain unchanged in the source and must not affect stream eligibility.

## 8. Archive naming and ownership requirements

### R-NAME-001 — Source isolation

One archive directory must contain payload from exactly one source root and one archive plan.

### R-NAME-002 — Payload time range

The human-readable oldest/newest timestamps in an archive directory name must be derived from entries that actually produce archived payload objects.

Cleanup-only aliases and skipped entries must not change those displayed bounds.

### R-NAME-003 — Collision resistance

Archive identity must include deterministic ownership/context information sufficient to avoid collisions when policies share a destination or equal time ranges occur.

### R-NAME-004 — Generated compression-path collision handling

A generated compressed path may be deterministically disambiguated when it conflicts with a fixed source-derived archive path.

A fixed source-derived archive path must not be silently renamed to solve a generated-name collision.

## 9. Logical move and metadata requirements

### R-MOVE-001 — Default logical action

The default action for a selected regular file must be a logical move: archive the required representation and then remove the source only after the safe-deletion requirements are satisfied.

### R-MOVE-002 — Metadata contract

For regular files, the logical move must preserve:

- file content;
- source-relative path, subject only to documented compression suffix transformation/disambiguation;
- permission mode bits; and
- modification time.

The system does not guarantee preservation of inode identity, ownership, ACLs, extended attributes, creation time, sparse layout, or filesystem-specific metadata unless a later requirement adds them.

### R-MOVE-003 — Compression representation

For a compressed file, the archive must record both:

- the source content SHA-256; and
- the compressed archive-payload SHA-256.

Verification must include decompression and comparison with the recorded source content hash.

## 10. Safe-deletion and transaction requirements

### R-SAFE-001 — Committed representation before deletion

Before any selected source entry is removed, its required archive representation must exist in the final committed archive directory, not only in staging.

### R-SAFE-002 — Committed archive verification before deletion

Before any selected source entry is removed, the system must perform a verification of the committed archive representation sufficient to establish the applicable payload and evidence requirements for that source entry.

### R-SAFE-003 — No deletion after failed archive verification

If committed-archive verification fails, the system must not remove any source entry whose safe-deletion requirement depends on the failed verification.

### R-SAFE-004 — Source revalidation before deletion

Immediately before removing a regular source file, the system must revalidate the source identity and content against the plan record.

A changed source must not be deleted automatically.

The implementation must bind final validation and removal as closely as the supported Linux filesystem interface permits. Validation must use the planned filesystem identity and content evidence. This requirement does not override the producer-quiescence boundary in R-SCOPE-004.

### R-SAFE-005 — Recovery ordering

During cleanup recovery, the system must verify the committed archive and pending transaction evidence before removing any remaining source entry.

### R-SAFE-006 — Recovery corruption behavior

If a committed archive is missing or corrupt during cleanup recovery, recovery must fail and preserve every remaining source entry.

### R-SAFE-007 — Partial cleanup reconciliation

Recovery must identify which cleanup effects already occurred. It must continue only with remaining idempotent cleanup actions whose source and archive preconditions are satisfied.

### R-SAFE-008 — Completion state

The system must not record an archive as complete until:

- required source cleanup has completed;
- cleanup durability requirements have been applied;
- final archive verification succeeds; and
- final completion evidence has been written durably.

## 11. Durability requirements

### R-DUR-001 — Declared durability model

The design must state the supported crash-persistence model and the filesystem assumptions under which it applies.

### R-DUR-002 — Durable archive namespace

Before source deletion begins, the implementation must synchronize every file and directory state required to ensure that the committed archive namespace and payload survive a supported crash model.

### R-DUR-003 — Metadata synchronization

File metadata changes that form part of the archive contract must be synchronized before the corresponding payload is treated as durable.

### R-DUR-004 — Source cleanup durability

Source-directory entry removals must be synchronized before durable transaction state records cleanup as complete.

### R-DUR-005 — Fault-oriented verification

Release verification must include fault-oriented tests or controlled simulations for interruption at material transaction boundaries.

### R-DUR-006 — Synchronization failure is not completion

If a required synchronization operation fails before source cleanup starts, the archival operation must fail without deleting dependent source entries.

If a required synchronization operation fails after cleanup has partially occurred, the archive must remain explicitly incomplete and recoverable. The implementation must not write completion evidence for that transaction.

## 12. Evidence and verification requirements

### R-EVID-001 — Manifest

Each committed archive must contain a manifest that records ownership, plan identity, source records, archive actions, hashes, compression metadata, stream/selection timestamps, and cleanup state.

### R-EVID-002 — Payload hash index

Each archive must contain a machine-readable hash index for archived regular payload objects.

### R-EVID-003 — Completion evidence

Completion evidence must be created only after the archive satisfies R-SAFE-008.

### R-VERIFY-001 — Policy-scoped verification

When the user selects one or more policies, `verify` must verify only archives owned by those selected policies/source roots unless the user explicitly requests destination-wide audit.

### R-VERIFY-002 — Destination-wide audit

The CLI must provide an explicit operation or option that audits all recognized archive directories under selected destination roots.

### R-VERIFY-003 — Malformed archive detection

An archive-shaped directory that lacks required evidence files must be reported as malformed/corrupt. Verification must not silently exclude it merely because its manifest is missing.

### R-VERIFY-004 — Unknown directories

Unrelated directories that do not match the archive ownership/naming contract may be ignored during destination-wide audit.

### R-VERIFY-005 — Malformed ownership ambiguity

If an archive-shaped directory lacks the evidence required to establish policy/source ownership, policy-scoped verification must report the malformed candidate rather than silently assign it to another policy or ignore it. This is a bounded exception to policy-only payload verification because ownership itself is the missing evidence.

## 13. Time and scheduling requirements

### R-TIME-001 — Planning reference time

A user-supplied artificial time may be used only by read-only planning commands.

### R-TIME-002 — Destructive execution time

Destructive commands must use an observed execution clock. They must not accept a normal CLI option that can make files artificially older.

### R-TIME-003 — Evidence timestamps

Manifest creation, cleanup completion, success evidence, and due-state success timestamps must represent observed execution events, not a user-supplied planning reference time.

### R-SCHED-001 — Due-state retry

A failed due policy must remain due. State must advance only after the policy completes successfully according to its scheduling contract.

### R-SCHED-002 — Policy fingerprint

Scheduling state must record a fingerprint of scheduling-relevant policy fields.

A missing or changed fingerprint must make the policy due on the next evaluation, unless a documented migration rule explicitly says otherwise.

### R-SCHED-003 — State migration

The implementation must define migration behavior for state files that predate the policy fingerprint. The conservative default is to treat the policy as due.

## 14. Concurrency requirements

### R-CONC-001 — Cooperative hierarchical resource lock domain

All Stream Archiver invocations that can operate on the same canonical source/destination resource hierarchy must participate in one cooperative lock domain, regardless of whether they are started manually or by systemd.

The lock domain must make nested overlaps contend. For example, an operation that owns `/a` for mutation must conflict with another Stream Archiver process that requests `/a/b`.

The lock contract is advisory and applies to cooperating Stream Archiver processes. It must not be described as preventing unrelated producers or other programs from modifying source files.

### R-CONC-002 — Stable hierarchical lock ordering

The implementation must acquire required ancestor resources before descendant resources. Within one depth, it must use deterministic canonical-path ordering. When one resource is requested in both shared and exclusive modes, exclusive mode wins.

### R-CONC-003 — Read-only commands

`plan` must remain read-only and need not acquire mutation locks; its output must be described as a snapshot that can become stale.

`verify` must acquire shared cooperative locks for the destination hierarchies it audits. A shared destination lock is sufficient to contend with a cooperating destructive commit; verification must not require a source root to remain present merely to verify a committed archive. Destructive operations must acquire exclusive locks on resources they can mutate and the required shared ancestor locks.

## 15. Compatibility and migration requirements

### R-COMPAT-001 — Completed 0.3 archives

The 0.4 implementation must continue to verify completed archives written by the supported 0.3 manifest/evidence format.

### R-COMPAT-002 — Pending 0.3 cleanup

A supported 0.3 committed archive with pending cleanup must use the corrected verify-before-delete recovery ordering. Legacy format compatibility must not restore the unsafe ordering.

### R-COMPAT-003 — Due-state v1 migration

A valid v1 due-state file must remain readable. Because it lacks a policy fingerprint, each affected policy must be treated as due until one successful 0.4 completion writes state format v2.

### R-COMPAT-004 — Artificial-time CLI migration

The global destructive `--now` interface is intentionally removed. Read-only planning must provide `plan --at <ISO-8601>` as its replacement.

### R-COMPAT-005 — Lock-file migration

A legacy `--lock-file` option may remain temporarily accepted for operator compatibility, but it must not define the safety lock domain. The CLI must warn that the option is deprecated or otherwise make the non-authoritative role explicit.

### R-COMPAT-006 — Generated systemd units

Operators upgrading from 0.3 must regenerate generated service units. Old units contain obsolete prerequisite and lock-file semantics and are not the 0.4 deployment contract.

## 16. Logging and diagnostic requirements

### R-LOG-001 — Output separation

Machine-readable command results must remain on stdout. Operational logs must remain on stderr/journald.

### R-LOG-002 — Stable structured fields

Operational logs must use stable concepts for at least:

- `run_id`;
- `event`;
- `phase`;
- `operation`;
- `outcome`;
- `policy_name` when applicable;
- `source_root` and `source_path` when applicable;
- `destination_root` when applicable;
- `archive_name` or `archive_directory` when applicable.

### R-LOG-003 — Path delimiting

Text-format paths, names, and user-controlled values must be safely delimited so whitespace cannot make field boundaries ambiguous.

### R-LOG-004 — Progress phases

Progress must distinguish at least:

- discovery/planning;
- staging;
- committed-archive verification;
- source revalidation;
- source cleanup;
- final archive verification; and
- completion-evidence creation.

### R-LOG-005 — Completion percentage

A transaction-wide 100% completion indication must not be emitted before the archival transaction is actually complete.

### R-LOG-006 — Actionable failure

When known and safe to disclose, an operational failure must identify the failed operation, affected object, observed condition, preserved state, safe next action, and whether retry is safe.

## 17. systemd deployment requirements

### R-SYSTEMD-001 — Generated deployment

Deployment-specific systemd units must be generated from validated configuration. Operators must not be required to edit source-controlled unit templates.

### R-SYSTEMD-002 — Visible prerequisite failure

A missing required config/source/destination path must result in a visible failed operation. Generated unit conditions must not silently skip the service for a state that the application treats as operational failure.

### R-SYSTEMD-003 — Least privilege

Generated units must grant only required filesystem access. When home-tree access is required, the generator should prefer a narrower read-only protection plus explicit writable exceptions when that configuration is valid.

### R-SYSTEMD-004 — No automatic destructive enablement

Installation must not start archival movement or enable the recurring timer without a separate operator decision after plan review and a successful manual validation run.

## 18. Documentation requirements

### R-DOC-001 — Requirements/design separation

The repository must keep durable requirements and design decisions distinct from per-release review evidence.

### R-DOC-002 — Traceability

Each material design mechanism and each safety-critical test must identify the requirement or invariant it supports.

### R-DOC-003 — Terminology consistency

README, requirements, design, operations guidance, CLI help, and diagnostics must use the project terminology defined in this document unless a more specific technical term is required.

### R-DOC-004 — No unsupported assurance wording

Documentation must not describe the system as `safe`, `durable`, `independently verified`, or equivalent unless the applicable requirement and evidence are named.

## 19. 0.4 alpha acceptance gate

The first 0.4 alpha implementation must not be promoted until executable evidence covers at least:

1. corrupt committed archive during pending cleanup preserves remaining source;
2. committed archive verification precedes normal source cleanup;
3. alias symlinks follow target selection across stream boundaries;
4. destructive CLI execution cannot use an artificial planning time;
5. missing required systemd paths produce visible application failure;
6. malformed archive-shaped directories fail verification;
7. policy-scoped and destination-wide verification are distinct;
8. nested resource ownership contends across cooperating processes while intended disjoint resources remain independently usable;
9. v1 due state is readable and conservatively due; policy changes produce deterministic v2 fingerprint behavior;
10. progress remains semantically correct through final verification and completion;
11. observable source replacement/change at cleanup fails closed, with the non-cooperating open-writer limitation documented;
12. synchronization failures at each material durability boundary do not create false completion;
13. completed supported 0.3 archives remain verifiable and pending 0.3 cleanup uses corrected ordering;
14. source-tree and installed-wheel CLI tests pass on a declared supported Python version;
15. generated systemd units pass `systemd-analyze verify` where that tool is available; and
16. the exact delivery patch replays against the exact `0.3.4b1` source baseline and reproduces the target tree.

Regression tests for safety defects must demonstrate sensitivity to the corresponding pre-fix behavior when practical; a test that passes only on the corrected implementation is not by itself sensitivity evidence.

## 20. Resolved design decisions for 0.4.0a1

The following decisions close the open items in the 0.4.0-alpha.1 draft:

1. A preserved non-alias relative symlink is a boundary entry and participates in stream segmentation by its own modification time.
2. The durability claim is limited to local Linux filesystems on which required regular-file `fsync`, directory `fsync`, same-filesystem rename/unlink, and advisory locking operations have the documented semantics and return success. NFS, SMB, FUSE, overlay, and other filesystems are outside the unqualified durability claim unless separately verified.
3. `verify` uses shared hierarchical cooperative locks.
4. Destination-wide audit is spelled `stream-archiver verify --all-in-destination`.
5. The policy fingerprint includes ordered canonical sources, canonical destination, `minimum_age`, `stream_gap`, symlink rule, and ordered compression rules. Logging and timer-presentation settings are excluded.

These decisions are requirements for the `0.4.0a1` implementation; they are no longer open proposals.
