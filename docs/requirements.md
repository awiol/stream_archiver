# Stream Archiver Requirements

**Document version:** `0.5.0-beta.3`
**Target package development version:** `0.5.0b3`
**Date:** 2026-10-01
**Status:** implementation contract for the first 0.5 beta candidate
**Repository intent:** repository requirements; supersedes document version 0.4.0-alpha.5

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

An **alias symlink** is a symlink whose resolved pathname is an exact discovered regular-file pathname under the same source root and whose resolved target identity matches that discovered regular file. Filesystem identity alone is insufficient because distinct hardlink pathnames can share one inode. Alias classification occurs across the complete discovery domain before stream segmentation.

### 3.6 Boundary entry

A **boundary entry** is a source entry whose timestamp participates in stream segmentation.

Alias symlinks do not participate in stream segmentation. A symlink that the selected symlink policy ignores or skips does not participate in stream segmentation.

### 3.7 Stream

A **stream** is a maximal ordered sequence of boundary entries inside one configured stream-partition domain in which every adjacent modification-time gap is less than `stream_gap`. A partition domain never spans configured source roots.

A gap greater than or equal to `stream_gap` starts a new stream.

### 3.8 Eligible stream

A stream is **eligible** when its newest boundary entry is at least `minimum_age` old at the planning reference time.

### 3.9 Archive plan

An **archive plan** is the deterministic mapping from one archive unit of an eligible logical stream to archive payload actions and cleanup-only actions. One logical stream may therefore have one or more archive plans.

### 3.10 Archive directory

An **archive directory** is one committed destination directory for one archive plan.

### 3.11 Cleanup recovery

**Cleanup recovery** is continuation of an archival operation that has already committed its archive directory but has not completed source cleanup and final completion evidence.

### 3.12 Restore

**Restore** means copying or reconstructing archived content back into operational source/use locations. Restore is outside the current product scope.

### 3.13 Run report

A **run report** is the final invocation-level result for newly selected archival work. It records exact execution outcomes after successful selected work and keeps recovery of previously committed archives separate. Human, JSON, and Markdown forms are renderings of the same result model.

### 3.14 Archive unit

An **archive unit** is one persistent archive transaction derived from one logical
stream. Schemas 1–4 produce one archive unit per logical stream. Schema 5 may
subdivide one logical stream into multiple deterministic archive units. Persistent
identity and naming do not depend on directory representation or invocation-local
stream order.

### 3.15 Archive-unit identity

An **archive-unit identity** is a deterministic format-neutral identifier derived
from the logical archive plan plus a deterministic zero-based unit index. For a
multi-unit logical stream, indexes cover the complete range from zero through
`archive_unit_count - 1`. The identity must not use `stream_index / stream_total`
or another invocation-local ordinal.

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

Each effective policy must define:

- a stable policy name;
- an ordered list of unique source roots;
- one destination root;
- `minimum_age`;
- `stream_gap`;
- one symlink rule;
- recursive-discovery behavior;
- one stream-partition mode;
- an archive-name policy appropriate to its schema;
- zero or more compression rules; and
- for schema 5, optional archive-unit source-byte and regular-file-count targets.

### R-CONFIG-002 — Multiple sources share policy settings

A policy may contain multiple source roots. Shared policy settings must not merge streams, plans, manifests, cleanup state, or archive directories across source roots.

### R-CONFIG-003 — Shared exact destinations

Two or more policies may use the same exact destination root.

The configuration validator must reject source/destination relationships that create ownership ambiguity, recursive archival, or nested destination ambiguity.

### R-CONFIG-004 — Canonical paths

The system must canonicalize configured roots for overlap, ownership, and lock-domain decisions. A symlink alias to another configured root must not bypass these checks.

### R-CONFIG-005 — Configuration schema evolution

Schema versions 1 and 2 must remain readable with their historical discovery behavior: recursive traversal and source-root-wide stream partitioning.

Schema version 3 must expose `recursive` and `stream_partition`. Schema version 4 must additionally expose `archive_name_template` and opt into the current stable-suffix archive namespace. Schema version 5 must additionally expose `archive_unit_max_source_bytes` and `archive_unit_max_regular_files` for deterministic archive-unit subdivision. A configuration that uses a field introduced by a later schema must declare that schema or newer so an older binary fails on the schema version rather than silently assigning different semantics.

### R-CONFIG-006 — Compression codecs

The only supported compression codecs are `gzip` and `bz2` unless a later requirements version adds another codec.

Compression level must be fixed at 9 and must not be configurable.

## 6. Discovery and stream requirements

### R-DISC-001 — Non-following discovery

Discovery must use non-following filesystem inspection. Directory symlinks must not be traversed.

### R-DISC-002 — Discovery failure visibility

A missing, inaccessible, symlinked, or non-directory source root must cause an actionable failed operation. A configured root symlink must be rejected before canonicalization hides the symlink object. Discovery must not silently omit an inaccessible subtree that is required by the policy.

### R-DISC-003 — Explicit recursive discovery

Schema-3 policies must expose `recursive = true|false`. Recursive discovery must traverse real descendant directories without following directory symlinks. Non-recursive discovery must consider entries directly under the source root and must not descend into real child directories.

### R-STREAM-001 — Configurable source-local partitioning

Stream segmentation must never combine configured source roots. Schema-3 policies must support `stream_partition = "source-root"` and `stream_partition = "parent-directory"`. `source-root` groups all boundary entries under one source root by time. `parent-directory` applies the same time-gap rule independently within each entry's exact relative parent directory.

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

### R-SAFE-009 — Bounded source namespace mutation

Automatic cleanup must remove only source entries represented by selected cleanup actions. It must not recursively remove unrelated or pre-existing empty directories. A directory that becomes empty because its selected files were removed remains in place unless a later explicit directory-cleanup contract is added.

### R-SAFE-010 — Cleanup dependency order

Within one transaction, selected regular-file payload sources should be removed before cleanup-only alias symlinks. This ordering reduces the partial-failure state in which an alias has been removed while its selected target remains. Each entry must still pass its own final revalidation immediately before unlink.

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

The 0.4.0a2 fingerprint adds the effective recursive-discovery and stream-partition settings. A fingerprint written by 0.4.0a1 therefore mismatches conservatively on first a2 evaluation even when a schema-1/2 policy retains the historical defaults. The new fingerprint must be persisted only after successful completion.

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

### R-COMPAT-007 — Immediate-run output migration

`stream-archiver run` changes from implicit JSON output in `0.4.0a3` to a human-readable final report by default in `0.4.0a4`. Automation that consumes the immediate-run result must use `run --json`. This is an intentional incompatible pre-1.0 CLI presentation change authorized for the 0.4 alpha line.

`run-if-due` retains its scheduled machine-oriented JSON result contract. The immediate-run change must not silently alter scheduled due-state output.

## 16. Planning and operator-output requirements

### R-PLAN-001 — Human-readable default plan

`stream-archiver plan` must produce a concise human-readable read-only summary by default. The summary must identify the planning reference time, snapshot limitation, source and stream counts, eligible streams, planned archives, selected regular-file count and input bytes, symlink/alias dispositions, compression/move dispositions, a bounded file-extension distribution, source/destination context, and an explicit no-mutation statement.

The design target is approximately 40 lines for a representative one-policy configuration. This is an operator-usability target, not a correctness gate.

### R-PLAN-002 — Explicit machine-readable detail

`stream-archiver plan --json` must expose the complete action-level plan in machine-readable JSON. The 0.4.0a1 JSON structure should remain stable where the new functionality does not require an additive field.

### R-PLAN-003 — Relative archive layout

Archive payload paths must preserve source-relative structure. Recursive discovery must not flatten files from different source directories. Separate configured source roots must continue to produce separate archive plans and archive directories even when they share a destination root.

## 17. Logging and diagnostic requirements

### R-LOG-001 — Output separation

Command results and human command presentations must remain on stdout. Operational logs must remain on stderr/journald. A machine-readable output mode must not be mixed with unrelated human prose on stdout.

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

When known and safe to disclose, an operational failure must identify the failed operation, affected object, observed condition, preserved state, safe next action, and whether retry is safe. Remediation guidance must not be attached mechanically to ordinary successful events.

### R-LOG-007 — Level semantics

INFO must describe important run, policy, source, stream/archive, recovery, and naturally occurring aggregate-progress events. Normal per-entry discovery, staging, hashing, revalidation, cleanup, and lock-detail events must be DEBUG unless the entry itself fails or requires operator attention.

The project may measure representative active-workload INFO density against the operator preference of approximately one useful INFO event per 1–10 seconds. That range is a soft diagnostic heuristic only. The implementation must not add timer-driven messages, heartbeat logs, cadence suppression, rate limiting, or a release gate solely to satisfy the range.

### R-LOG-008 — Stable semantic fields

A field that identifies an archival operation must use a distinct concept such as `archive_action`. A field that tells an operator what to do after a failure must use `next_action`. The implementation must not overload one `action` field for both meanings.

### R-LOG-009 — Human path context

Machine-readable JSON logs must retain complete path values required for diagnosis. Human text logs should prefer relative entry paths once the applicable source/archive root is already identified by the surrounding event context. Failure diagnostics may include the full affected path when it materially improves remediation.

## 18. systemd deployment requirements

### R-SYSTEMD-001 — Generated deployment

Deployment-specific systemd units must be generated from validated configuration. Operators must not be required to edit source-controlled unit templates.

### R-SYSTEMD-002 — Visible prerequisite failure

A missing required config/source/destination path must result in a visible failed operation. Generated unit conditions must not silently skip the service for a state that the application treats as operational failure.

### R-SYSTEMD-003 — Least privilege

Generated units must grant only required filesystem access. When home-tree access is required, the generator should prefer a narrower read-only protection plus explicit writable exceptions when that configuration is valid.

### R-SYSTEMD-004 — No automatic destructive enablement

Installation must not start archival movement or enable the recurring timer without a separate operator decision after plan review and a successful manual validation run.

### R-SYSTEMD-005 — Safe generated-file replacement

`render-systemd --force` must replace a generated output pathname itself. It must not follow an existing output symlink and overwrite the symlink target.

### R-INSTALL-001 — Exact wheel selection

The guided installer must install a wheel whose normalized project version matches the source tree's declared project version. If no exact-version wheel is available, installation must stop with an actionable error rather than selecting another wheel by modification time.

### R-API-001 — Public destructive clock ownership

The exported `run_policy()` API must use an observed execution clock and must not accept an artificial eligibility time. Deterministic artificial clocks used by tests must remain on private/internal surfaces.

### R-API-002 — Public lock ownership

Exported destructive and verification APIs must acquire their required cooperative resource locks themselves. Public callers must not be able to bypass the lock invariant through a `resources_locked` or equivalent assertion parameter.

## 19. Documentation requirements

### R-DOC-001 — Requirements/design separation

The repository must keep durable requirements and design decisions distinct from per-release review evidence.

### R-DOC-002 — Traceability

Each material design mechanism and each safety-critical test must identify the requirement or invariant it supports.

### R-DOC-003 — Terminology consistency

README, requirements, design, operations guidance, CLI help, and diagnostics must use the project terminology defined in this document unless a more specific technical term is required.

### R-DOC-004 — No unsupported assurance wording

Documentation must not describe the system as `safe`, `durable`, `independently verified`, or equivalent unless the applicable requirement and evidence are named.

### R-DOC-005 — Reader-task separation

The README must remain a concise orientation and quick-start surface. A maintained user guide must explain the operational mental model, configuration decisions, discovery/partition behavior, relative path preservation, plan interpretation, logging, worked examples, and frequently misunderstood behaviors. Operations and verification material must remain in their specialized documents rather than being copied wholesale into the README.

### R-DOC-006 — Executable examples

Canonical configuration and command examples must use supported interfaces and must be exercised by automated parsing or command tests where practical.

## 20. 0.4 alpha acceptance gate

A 0.4 alpha candidate must not be promoted beyond its declared maturity until executable evidence covers the applicable items below:

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
16. the exact delivery patch replays against the exact previous delivered source baseline and reproduces the target tree;
17. public destructive execution has no artificial-time or lock-bypass parameter;
18. unrelated empty source directories survive selected-file cleanup;
19. hardlink identity outside the exact selected in-source pathname cannot authorize alias cleanup;
20. schema-3 recursion and stream-partition behavior is executable and schema-1/2 compatibility defaults remain intact;
21. `render-systemd --force` does not follow an existing output symlink;
22. installer wheel selection fails closed when the exact package version is absent;
23. default `plan` output is human-readable and explicit `--json` retains action detail;
24. INFO-level verification demonstrates archive/stream lifecycle visibility without normal per-entry events;
25. invocation-level progress establishes stable stream/file/source-byte totals across selected policies;
26. written-payload accounting uses final transformed object sizes while source-byte accounting remains exact;
27. planning reports advisory destination capacity with exact machine-readable selected/free byte values;
28. execution rechecks capacity before every selected stream and suppresses repeated warnings by semantic state rather than time;
29. a low-space advisory does not become a hard execution gate and a later `ENOSPC` cannot produce false completion;
30. material unexpected-capacity diagnostics use neutral causal wording and exact arithmetic; and
31. source-tree and installed-wheel tests cover the `0.4.0a3` progress/capacity behavior on the final candidate;
32. final run-report totals match observed regular payload results for mixed uncompressed/gzip/bzip2 work;
33. zero-source-byte and compression-expansion cases preserve exact arithmetic without invented ratios;
34. original-extension and transformation cross-tabulation is complete in machine data while the console summary remains bounded;
35. recovered prior archives remain separate from newly selected run totals;
36. default `run` output is human-readable and explicit `run --json`/`run --markdown` render the same result model;
37. an existing Markdown report path blocks before destructive mutation; and
38. a post-commit Markdown export failure remains an auxiliary warning and does not relabel committed archival work as uncommitted or failed.

Regression tests for safety defects must demonstrate sensitivity to the corresponding pre-fix behavior when practical; a test that passes only on the corrected implementation is not by itself sensitivity evidence.

## 21. Foundation decisions resolved by 0.4.0a2

The following decisions close the open items in the 0.4.0-alpha.1 draft:

1. A preserved non-alias relative symlink is a boundary entry and participates in stream segmentation by its own modification time.
2. The durability claim is limited to local Linux filesystems on which required regular-file `fsync`, directory `fsync`, same-filesystem rename/unlink, and advisory locking operations have the documented semantics and return success. NFS, SMB, FUSE, overlay, and other filesystems are outside the unqualified durability claim unless separately verified.
3. `verify` uses shared hierarchical cooperative locks.
4. Destination-wide audit is spelled `stream-archiver verify --all-in-destination`.
5. The policy fingerprint includes ordered canonical sources, canonical destination, `minimum_age`, `stream_gap`, symlink rule, and ordered compression rules. Logging and timer-presentation settings are excluded.

These foundation decisions remain requirements through `0.4.0a4`; they are no longer open proposals.


## 22. 0.4.0a2 consolidation decisions

1. The exact candidate advances to `0.4.0a2`; the target release core remains `0.4.0`.
2. Configuration schema 3 owns `recursive` and `stream_partition`; schema 1/2 keep historical defaults.
3. Recursive discovery and stream partitioning are independent. Archive layout continues to preserve source-relative paths and does not gain a flatten mode.
4. Automatic cleanup no longer removes empty directories that are not explicit selected entries.
5. Alias classification requires an exact resolved in-source regular-file pathname plus matching identity; inode equality alone is not sufficient.
6. Public mutation/verification functions own their cooperative locking and do not expose a lock-bypass flag.
7. `plan` is human-readable by default; `plan --json` is the explicit detailed machine interface.
8. INFO is event-driven at meaningful archive/stream and aggregate-progress boundaries. The 1–10 second preference is only a measured operator heuristic, never a timer or gate.
9. The README is a short entry point. `docs/user-guide.md` is the detailed user-facing mental-model/configuration/usage reference.


## 23. 0.4.0a3 run progress and destination-capacity contract

The following requirements adopt the corresponding provisional IDs from the
`0.4.0a3` planning delta. They add operator accounting without changing the
archive transaction, cleanup, or durability gates.

### R-PROG-001 — Selected-work totals

Before destructive processing of newly selected archive plans begins, a run must
establish totals for at least selected archive/stream count, selected regular-file
count, and selected regular source bytes. `run` and the due subset of
`run-if-due` must use one invocation-level denominator across the selected
policies. Recovery of a previously committed archive must remain separate from
newly selected work. All selected policies in that invocation use one planning
reference time.

### R-PROG-002 — Run-level progress state

During destructive execution, observable run progress must include, when
applicable, current selected stream/archive index and total, completed regular
files and selected total, processed regular source bytes and selected total,
logical archive payload bytes written after compression/transformation, and
elapsed runtime.

A counter must have a documented denominator and semantic boundary. A selected
regular file counts as completed only after its archive transaction completes
successfully. Source-byte progress may advance while staging reads a file.
Successful reconciliation of already-staged selected work may account the
remaining planned source bytes at transaction completion so final progress
reaches the selected denominator without claiming that those bytes were reread.

### R-PROG-003 — Runtime clock

Elapsed runtime must use a monotonic clock. Wall-clock changes must not make
elapsed runtime decrease or jump. The run elapsed clock begins when top-level
run orchestration starts, so recovery and planning performed before selected-work
totals are frozen remain part of invocation runtime even though they do not
increase the selected-work denominator.

### R-PROG-004 — Written-byte semantics

`written_payload_bytes` must mean logical bytes in finalized archive payload
objects for newly selected regular files. For compressed regular files it must
reflect compressed output bytes, not source bytes. Manifest, checksum, success
evidence, and filesystem metadata must not be silently included. Filesystem
allocated-space accounting used for capacity diagnosis must remain a different
concept.

### R-PROG-005 — Machine and human representations

Structured output/log fields must preserve exact non-negative integer byte
counts and numeric elapsed time. Human text may use compact units and duration
formatting derived from those exact values. Human rendering must not control
comparisons or machine semantics.

### R-PROG-006 — Human size format

The project must use one shared human-size formatter for plan summaries,
capacity warnings, progress logs, and other operator-facing volume fields. For
`0.4.0a3`, the formatter uses decimal SI divisors and the labels `B`, `kB`,
`MB`, `GB`, `TB`, and `PB`. Scaled values should normally use one or two decimal
digits. Exact bytes remain available in machine-readable fields.

### R-PROG-007 — INFO progress semantics

Important stream/archive lifecycle events and useful aggregate run progress may
be INFO. Per-entry normal-success work must remain DEBUG. Aggregate INFO progress
may be emitted when observed work crosses naturally occurring byte milestones.
The approximate one-useful-INFO-event-per-1-to-10-seconds preference is a
post-hoc usability heuristic only. It must not create a timer, heartbeat, sleep,
rate limiter, suppression interval, or release gate.

### R-CAP-001 — Planning capacity observation

Read-only planning must attempt to observe available bytes for each exact
destination with selected regular-file work when the platform can provide that
information. A missing destination must be probed through its nearest existing
ancestor without creating the destination.

The plan must compare available bytes with selected regular source bytes for all
selected work directed to that exact destination. If available bytes are less
than selected source bytes, the human plan must show an advisory warning
containing both values. Machine-readable data must retain both exact values.
Failure to obtain capacity information must be reported as unavailable capacity;
it must not silently become zero or sufficient capacity.

### R-CAP-002 — Capacity is advisory

The source-byte comparison is an advisory conservative check, not a reservation,
an output-size prediction, or a guarantee that the run fits or fails to fit.
Relevant uncertainty includes compression expansion/reduction, evidence files,
filesystem allocation, quotas/reservations, concurrent activity, metadata, and
allocation timing. Low or unavailable capacity must not authorize source
deletion, cancellation, a changed archive representation, or a hard execution
gate unless a later owner decision explicitly adds such a contract.

### R-CAP-003 — Per-stream execution recheck

Before each selected stream/archive begins staging, destructive execution must
observe destination free bytes again and compare them with remaining planned
regular source bytes for that exact destination. Structured observations must
identify at least destination, current free bytes, current-stream source bytes,
and remaining planned source bytes when capacity is available.

A successful or low-space precheck must not convert a later filesystem `ENOSPC`
or other write failure into success. Ordinary execution/recovery behavior remains
authoritative.

### R-CAP-004 — Warning state transitions

A low-capacity warning must not be emitted mechanically on every stream while the
same condition remains materially unchanged. Emit at least the first transition
into an insufficient state. A later warning may be emitted when the condition
materially worsens or after it returned to sufficient capacity and became
insufficient again. Unavailable-capacity warnings must likewise be state-based,
not time-based.

For `0.4.0a3`, material worsening means an increase in shortfall of at least the
greater of 64 MiB and 5% of the shortfall at the last insufficient-capacity
warning. This threshold controls operator noise only; it is not a storage-safety
boundary. Reassess it after representative operational evidence shows excessive
missed or duplicate warnings.

### R-CAP-005 — Unexpected capacity-consumption diagnostic

After a newly committed stream, when the platform exposes allocated block counts
and both pre/post free-space observations are available, the implementation may
compare observed free-space loss with measured allocation of the committed
archive tree. A positive residual is material when it is at least the greater of
64 MiB and 5% of measured archive allocation.

A material residual must be described as **unexpected destination capacity
consumption**. It must not be attributed to another process, filesystem behavior,
or another cause without separate evidence. Concurrent activity, delayed
allocation, filesystem metadata, journaling, quotas, snapshots, copy-on-write
behavior, and measurement noise remain credible explanations. Failure to obtain
the optional allocation measurement must remain advisory and must not fail an
otherwise completed archive operation.

### R-CAP-006 — Capacity units and exact values

Human capacity messages must use the shared human-size formatter. Structured
logs must retain exact integer byte values for available, remaining, written,
and residual quantities. Capacity arithmetic must use exact integer bytes.
Capacity observations and warnings must not weaken staged verification,
committed verification, source revalidation, cleanup ordering, or synchronization
requirements.

## 24. 0.4.0a3 candidate decisions

1. The exact candidate advances to `0.4.0a3`; target release core remains `0.4.0`.
2. The candidate is one coherent runtime-accounting increment: invocation progress, destination-capacity observations/warnings, and shared human byte presentation.
3. Decimal SI is the selected human-size convention for this candidate; IEC labels are not used with decimal divisors.
4. Capacity warnings remain advisory. No preflight reservation or hard low-space gate is introduced.
5. The 64 MiB / 5% dual thresholds are operator-noise heuristics with explicit review triggers, not safety guarantees.
6. Invocation-local stream ordinal is runtime state only and does not become persistent archive identity.


## 25. 0.4.0a4 final run-report contract

The fourth alpha extends the `0.4.0a3` accounting boundary into a durable result
model and presentation layer. It does not change archive directory identity,
archive transaction semantics, source cleanup, compression policy, storage
backend, or stream selection.

### R-REPORT-001 — One authoritative run-result model

After a successful immediate `run`, the implementation must construct one
invocation-level result model from completed execution evidence. Human console,
JSON, and Markdown output must render this model rather than independently
recalculate totals.

The model must identify at least elapsed run runtime, selected logical streams,
completed archive units, completed regular files, selected link dispositions,
source bytes, transformed written payload bytes, and prior recovered archives.
Recovery completed before new planning must remain separate from newly selected
work totals.

### R-REPORT-002 — Exact byte and savings definitions

For newly selected completed regular payloads:

- `source_bytes` is the sum of original regular source sizes represented by the
  completed selected work;
- `written_payload_bytes` is the sum of finalized transformed regular payload
  object sizes;
- `saved_payload_bytes = source_bytes - written_payload_bytes`;
- `payload_size_ratio = written_payload_bytes / source_bytes` when
  `source_bytes > 0`; and
- `payload_savings_percent = saved_payload_bytes / source_bytes * 100` when
  `source_bytes > 0`.

`payload_size_ratio` and `payload_savings_percent` must be absent/null rather
than invented when the denominator is zero. `saved_payload_bytes` may be
negative when transformation expands data. Human output must not clamp such an
observation to zero or describe it as positive savings.

### R-REPORT-003 — Transformation disposition

The result must report regular-file count, source bytes, written payload bytes,
and derived savings separately for `uncompressed`, `gzip`, and `bzip2`
transformations that occurred. Historical manifest action `copy` and current
`move` both belong to the `uncompressed` reporting category.

Content transformation is distinct from a future archive/container compression
mechanism. A future ZIP representation must not silently reuse these fields for
container compression.

### R-REPORT-004 — Original-extension disposition

The result must group regular payload outcomes by the original source filename's
final suffix, case-normalized. An extensionless source uses the explicit
`<none>` group. Machine-readable output and Markdown must preserve the complete
original-extension × transformation cross-tabulation.

The human console summary must bound extension rows and aggregate omitted groups
without changing the complete underlying result.

### R-REPORT-005 — Link disposition

The report must expose selected symlink dispositions available from completed
archive transactions, including preserved relative symlinks, removed alias
symlinks, and skipped symlinks when a completed plan contains them. It must not
infer a count for symlinks that discovery/policy rules excluded from selected
archive plans.

### R-REPORT-006 — Immediate-run rendering modes

`stream-archiver run` must render the bounded human summary by default.
`stream-archiver run --json` must emit the complete machine-readable result as
one valid JSON value. `stream-archiver run --markdown` must emit a complete
human-oriented Markdown report. These modes are mutually exclusive.

Structured JSON must retain exact integer byte fields. Human renderers use the
shared decimal-SI formatter. `run-if-due` remains machine-oriented JSON and is
not changed into a human report by this candidate.

### R-REPORT-007 — Optional Markdown report file

`stream-archiver run --report <path>` may additionally write the Markdown
renderer output as an auxiliary invocation artifact. The final path must be new.
If it already exists during preflight, the command must stop before destructive
archival work rather than overwrite it.

The exporter must first complete a same-directory temporary file and publish the
requested pathname without overwriting a concurrently created file. A report
export failure after successful archival commit must be logged as an actionable
warning. It must not roll back, invalidate, or relabel completed archives, and it
must not make the archival command return failure solely because this auxiliary
file could not be published.

The Markdown file is not archive payload, archive provenance, or completion
evidence. Stream Archiver does not create it unless the operator requests
`--report`.

### R-REPORT-008 — Metric boundaries for future archive representations

`written_payload_bytes` excludes manifest/checksum/success evidence, directory
metadata, filesystem block allocation, and future archive-container overhead.
The run-result schema reserves separate `archive_container_bytes` and
`filesystem_allocation_bytes` concepts; they remain null when this candidate has
no applicable authoritative invocation-level measurement.

A later archive representation or storage backend must add its observations
without redefining the existing payload fields.

## 26. 0.4.0a4 candidate decisions

1. The exact candidate advances to `0.4.0a4`; target release core remains `0.4.0`.
2. The candidate is one coherent reporting increment over the accounting evidence introduced in `0.4.0a3`.
3. Immediate `run` becomes human-readable by default; automation migrates to explicit `run --json`.
4. `run-if-due` keeps its existing machine-oriented JSON result because scheduled execution is a separate operator/automation surface.
5. The Markdown report file is opt-in and auxiliary. Existing paths fail before mutation; post-commit export failures are warnings and do not change archive success.
6. Payload savings can be negative. Ratio/percentage values are undefined for zero source bytes and are represented as null/`n/a` rather than fabricated values.
7. Original-extension grouping uses the final source suffix, case-folded, with `<none>` for extensionless files. Compound-extension interpretation is not added in this candidate.
8. ZIP representation, archive-unit partitioning, configurable archive naming, storage-backend interfaces, and durable sequence allocation remain outside `0.4.0a4`.


## 27. 0.5.0a1 archive-unit identity and naming contract

The first 0.5 alpha changes the persistent archive namespace while preserving the
existing directory representation, transaction ordering, source selection, and
cleanup/recovery safety contracts.

### R-ANAME-001 — Format-neutral archive-unit identity

Each `ArchivePlan` must expose a full deterministic archive-unit identity that is
distinct from its human archive name. The identity must be domain-separated from
the plan identifier and must include a deterministic non-negative archive-unit
index. `0.5.0a1` uses index zero because subdivision is not yet implemented.

The archive-unit identity must not depend on invocation-local progress ordinals,
wall-clock execution time, locale, archive representation, or a human naming
template.

### R-ANAME-002 — Mandatory stable name suffix

A schema-4 archive name must end with `--sa-` followed by the first 20 lowercase
hexadecimal characters of the archive-unit identity. The human prefix may change
with configuration, but the mandatory suffix is not configurable.

If two different plans produce the same human prefix, the stable suffix must keep
the normal names distinct unless the truncated cryptographic identities collide.
An existing final path with mismatching manifest identity remains a hard collision;
the implementation must not overwrite or merge it.

### R-ANAME-003 — Controlled human template

Configuration schema 4 must support per-policy `archive_name_template`. The
template may contain literal text and the fields `start`, `end`, and `span`.
`start` and `end` describe payload-producing modification-time bounds in UTC;
`span` describes their non-negative difference.

Timestamp formats must support deterministic numeric fields plus fixed English
three-letter month (`%b`) and weekday (`%a`) names without depending on process
locale. `%f` (microseconds) and `%N` (nanoseconds) are explicit opt-in
sub-second directives. If the timestamp format is omitted, it must contain whole
seconds only.

The span formatter must support deterministic days/hours/minutes/seconds and
explicit opt-in microsecond/nanosecond fields. The human prefix is descriptive;
precision omitted from it does not weaken the stable archive-unit identity.

### R-ANAME-004 — Safe filename grammar and bounds

The naming template and rendered prefix must reject path separators, NUL, the
reserved `--sa-` marker, unsupported fields/directives, nested replacement
fields, and conversion operators. The rendered prefix must not be empty, `.` or
`..`. Planning must reject a rendered final name that exceeds the destination
filesystem's observed `PC_NAME_MAX`; when that query is unavailable, the
implementation uses the Linux-oriented 255-byte fallback and still relies on
normal filesystem errors as the final authority.

### R-ANAME-005 — Explicit schema migration

Schemas 1–3 must remain readable with their historical archive-name algorithm.
They must reject `archive_name_template` as an unknown key. Schema 4 must use
`archive_name_template` when supplied and otherwise use the current default
`{start:%Y%m%dT%H%M%SZ}--{end:%Y%m%dT%H%M%SZ}` before the mandatory stable
identity suffix.

Programmatic `Policy` construction must preserve the historical name algorithm
when `archive_name_template` is `None`; a non-null template explicitly opts into
the current naming contract.

### R-ANAME-006 — Recovery and verification compatibility

Verification and recovery must continue to accept supported historical archive
directories. Destination-wide verification must also recognize current
`--sa-<identity-suffix>` directory names as archive-shaped even when the human
prefix is arbitrary. A current-format archive name must be rejected when its
identity suffix does not match the manifest `plan_id` under the current
archive-unit identity derivation.

### R-ANAME-007 — Scheduling fingerprint

The policy fingerprint used by `run-if-due` must include the effective archive
name template. Changing persistent naming policy therefore makes a previously
successful policy conservatively due once, and the new fingerprint is stored
only after successful completion.

### R-ANAME-008 — Machine plan observability

The complete JSON planning surface must expose `archive_unit_id` and
`archive_unit_index` separately from `archive_name` and `plan_id`. The human
archive name remains presentation/persistent namespace; the full identity remains
machine-readable.

### R-ANAME-009 — Scope boundary

`0.5.0a1` must not subdivide a logical stream, create ZIP archives, add a durable
human sequence allocator, or introduce a destination-storage plugin contract.
Those remain separate planned/deferred changes.

## 28. 0.5.0a1 candidate decisions

1. Package, requirements, and design candidate identities align at `0.5.0-alpha.1`; historical mismatched document versions remain immutable.
2. Configuration schema 4 is the explicit opt-in boundary for the new persistent namespace; schemas 1–3 preserve historical names.
3. The full archive-unit identity is SHA-256 over a domain-separated canonical record containing `plan_id` and deterministic `unit_index`.
4. The persistent name suffix uses 20 hexadecimal identity characters (80 bits); the complete identity remains available in machine planning output.
5. Current one-stream/one-unit behavior uses unit index zero. Future subdivision must define deterministic unit ordering before using additional indexes.
6. Human names are configurable but never authoritative identity. Locale-dependent `strftime()` output is not used for `%a`/`%b`.
7. ZIP, generic subdivision, durable sequence allocation, and storage-backend abstraction remain outside this candidate.


## 29. 0.5.0a2 archive-unit subdivision contract

The second 0.5 alpha adds deterministic subdivision of one logical stream into
multiple directory archive units. It does not add ZIP representation or a
non-filesystem destination backend.

### R-PART-001 — Explicit schema-5 partition controls

Configuration schema 5 may set `archive_unit_max_source_bytes` and
`archive_unit_max_regular_files` per policy. Each supplied value must be a
positive non-boolean integer. Schemas 1–4 must reject these fields. Absence of
both limits preserves one archive unit per logical stream.

### R-PART-002 — Deterministic greedy subdivision

When at least one limit is configured, regular payload actions must be ordered
deterministically by source modification time and source-relative path. The
planner must start a new archive unit before adding a regular file when adding
it would exceed the byte target or when the current unit has already reached
the regular-file target.

`archive_unit_max_source_bytes` is based on original regular source bytes. It is
not a bound on transformed payload bytes, filesystem allocation, or a future
ZIP/container size.

### R-PART-003 — Files remain indivisible

Subdivision must not split one regular file. A single regular file larger than
the byte target must occupy one archive unit by itself. The resulting unit may
therefore exceed the configured source-byte target.

### R-PART-004 — Deterministic link placement

A cleanup-only alias symlink must be assigned to the archive unit that contains
its selected regular target. Other selected symlink actions do not consume the
regular-file or source-byte limits and must be assigned deterministically so a
replan of unchanged inputs yields the same unit allocation.

### R-PART-005 — Logical-plan and archive-unit identity

All units produced from one logical stream must share one `plan_id`. The
`plan_id` must include the effective partition limits when subdivision is
configured because those limits can change unit contents. Unpartitioned plans
must preserve the `0.5.0a1` plan/archive-unit identity derivation.

Each archive unit must expose a zero-based deterministic `archive_unit_index`,
the common positive `archive_unit_count`, and the archive-unit identity derived
from `plan_id` plus index. Machine planning output must expose all three values.

### R-PART-006 — Commit all units before source cleanup

For a subdivided logical stream, execution must commit and verify every archive
unit before cleanup of any source entry in that logical stream starts. A commit
failure in a later unit must therefore leave all source entries intact even if
earlier units already exist as verified pending archives.

Before cleanup begins, execution must revalidate source state for every pending
unit. Only after the complete unit set is committed and verified may cleanup
proceed in deterministic unit order.

### R-PART-007 — Group-aware pending recovery

Manifest format 3 must record `archive_unit_id`, `archive_unit_index`, and
`archive_unit_count`. Recovery must validate those fields. A pending format-3
unit set that is incomplete must not authorize source cleanup. Once all declared
units are present and valid, recovery may complete the group under the same
verify-before-delete contract. If current planning cannot reconstruct the same
logical `plan_id` for an incomplete pending group, execution must stop before new
archive mutation or source cleanup and require reconciliation. Historical
supported manifest formats remain readable under their prior single-archive
semantics.

### R-PART-008 — Stream and archive-unit accounting remain distinct

Progress and final reporting must count one logical stream once even when it is
represented by several archive units. `archive_units_completed` and unit-level
logs may count each committed unit separately. Invocation-local stream ordinals
must not become persistent archive identity.

### R-PART-009 — Existing representation remains unchanged

Every archive unit in `0.5.0a2` remains a local directory with the existing
staging, atomic namespace commit, payload verification, durability, and success
evidence. ZIP representation is a later feature.

### R-PART-010 — No hard final-container-size claim

The source-byte target is a deterministic planning target, not a guarantee that
a future archive container has a particular byte size. A hard ZIP-size limit,
if later required, must use representation-specific evidence and must not be
inferred from this requirement.

## 30. 0.5.0a2 candidate decisions

1. Package, requirements, and design candidate identities align at `0.5.0-alpha.2`.
2. Configuration schema 5 is the explicit opt-in boundary for subdivision.
3. Partition limits are included in `plan_id` only when subdivision is configured, preserving unsplit `0.5.0a1` identity.
4. Archive units are ordered deterministically and use zero-based persistent unit indexes.
5. The logical stream is the cleanup transaction group: all units commit and verify before any source cleanup.
6. Manifest format 3 carries unit-group cardinality and identity while supported historical manifests remain verifiable.
7. ZIP representation, a hard final-container-size mechanism, durable human sequence allocation, and storage-backend plugins remain outside this candidate.


## 31. 0.5.0 beta distribution, recovery, and release-consistency contract

### R-DIST-001 — Reviewable source distribution

The Python source distribution must contain the maintained normative requirements,
design, verification, operations and user documentation, `CHANGELOG.md`, canonical
examples, maintainer tools referenced by README, the maintained pytest modules and
their support modules, and the lockfile used by the documented development route.
Repository-local `local/` state must not be included.

### R-DIST-002 — Clean-sdist release verification

Release verification must build the source distribution, unpack it outside the
repository, and verify from that unpacked artifact that: the declared source-user
paths exist; the complete pytest suite collects and passes under the shipped pytest
configuration; a wheel can be built and installed from the sdist; the installed CLI
smoke check passes; and the documented systemd verification helper remains usable.
Repository-only files must not be used to satisfy this gate.

### R-REL-001 — One current release identity

The current package version must agree across build metadata, runtime
`__version__`, lockfile package metadata, the current requirements/design target,
and the leading changelog entry. Generated `PKG-INFO` present in a built sdist must
carry that same version. Tests must derive the expected identity from authoritative
build metadata instead of embedding the candidate version as an oracle.

### R-TEST-001 — Deterministic hierarchical lock evidence

The maintained hierarchical-lock regression must use a deterministic oracle for
Linux `flock` conflict and compatibility semantics. It must not depend on spawned
pytest-module importability, process-startup timing, queue delivery, or child-process
teardown. The primary regression may use separately opened file descriptions in
one process because `flock` ownership/conflict is defined on open file descriptions;
process lifecycle is not part of the behavior under test. A separate installed-artifact
process smoke may supplement this evidence but must not become the regression oracle.

### R-PART-011 — Recovery group-wide source prevalidation

When restart recovery sees a complete pending manifest-format-3 archive-unit group,
it must verify every pending committed unit and validate every remaining selected
source across the complete group before deleting any source entry from that group.
A mismatch in a later unit that exists before recovery begins must therefore leave
earlier-unit sources untouched. Per-unit revalidation immediately before unlink
remains required.

## 32. 0.5.0b1 beta entry decision

The first 0.5 beta candidate is permitted only after all of these conditions hold
on the exact final candidate:

1. SA-001, SA-002 and SA-003 from the isolated `0.4.0a5` sdist reviews are closed by a clean-sdist verification run;
2. every maintained requirement identifier is unique;
3. current package, requirements, design, lockfile, changelog and generated sdist metadata agree on the beta identity;
4. the archive-unit recovery regression for a changed later-unit source fails on the exact `0.5.0a2` predecessor and passes on the candidate;
5. the deterministic hierarchical-lock regressions pass repeatedly without process-startup, queue/import-mode, or teardown dependence;
6. source-tree, clean-sdist and installed-wheel verification pass on an available declared Python version;
7. Deep Consistency Review reaches `complete-for-declared-scope` for the declared beta scope with no unresolved release-blocking finding; and
8. unavailable supported runtimes, static tools, filesystem classes, power-loss tests, and specialist assurance remain explicit limitations rather than implied passes.

Promotion to beta records maturity of this source candidate only. It does not establish production approval, external publication, independent review, or unqualified crash durability beyond the documented local-filesystem assumptions.


## 33. 0.5.0b2 isolated-review closure contract

### R-VERIFY-006 — Ownership evidence is validated before policy filtering

Policy-scoped verification must validate the manifest evidence needed to establish
`policy_name` and `source_root` before using those fields to exclude a candidate as
foreign-owned. Malformed JSON, a non-regular or symlinked manifest, missing ownership
fields, invalid ownership field types, an empty ownership value, or a non-absolute
`source_root` must fail verification rather than become a foreign-owner skip. A valid
foreign-owned manifest may still be excluded before payload verification.

### R-CAP-007 — Subdivided stream capacity telemetry preserves stream semantics

When one logical stream is subdivided into several archive units, every per-unit
capacity precheck must report the complete logical stream's selected regular source
bytes as `current_stream_source_bytes`. `remaining_source_bytes` continues to decrease
as archive units complete. The existing field must not be silently redefined to mean
current archive-unit bytes.

### R-PY-001 — Supported Python runtime range

The supported Python runtime range for the 0.5 line is Python 3.11 through 3.13.
Package Core Metadata must encode `>=3.11,<3.14`; runtime-version classifiers must
name 3.11, 3.12, and 3.13; public support documentation must state the same range;
and the system installer must reject Python selectors outside those minor versions
before performing privileged or installation mutations. Patch-level selectors within
a supported minor version are permitted.

### R-TEST-002 — Isolated-review closure sensitivity

Maintained regressions must distinguish the `0.5.0b1` defects for malformed
policy-scoped ownership evidence, archive-unit/logical-stream capacity accounting,
and the open-ended Python support contract. Release verification must exercise the
source tree, clean source distribution, and installed wheel after these corrections.
