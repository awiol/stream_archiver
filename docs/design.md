# Stream Archiver Design

**Document version:** `0.5.0-beta.3`
**Target package development version:** `0.5.0b3`
**Date:** 2026-10-01
**Status:** implementation design for the first 0.5 beta candidate
**Requirements baseline:** `docs/requirements.md`, document version `0.5.0-beta.3`

## 1. Design objective

The 0.4 design repaired transaction and behavioral contracts while preserving the high-level architecture. The 0.5 line separates format-neutral archive-unit identity from configurable human archive names and adds deterministic subdivision of one logical stream into archive units. The current beta preserves the directory representation, transaction safety, reporting, and cleanup/recovery ordering while closing review and source-distribution defects.

The design retains:

- TOML policies;
- multiple source roots per policy;
- shared exact destination roots;
- deterministic source-local planning;
- gzip and bzip2 compression at fixed level 9;
- destination-side staging;
- atomic final-directory rename;
- manifest/hash/success evidence;
- cleanup recovery;
- structured logs; and
- generated systemd deployment.

The design changes the safety and state boundaries around these mechanisms.

## 2. Primary invariants

### INV-01 — Source isolation

One archive plan and one archive directory contain payload from one source root only.

### INV-02 — No destructive planning clock override

A user-supplied planning reference time cannot reach a destructive execution path.

### INV-03 — Committed archive before cleanup

No source cleanup begins until the required archive representation exists in the final committed archive directory.

### INV-04 — Verify committed state before cleanup

No source cleanup begins or resumes until the committed archive state has passed the required integrity checks.

### INV-05 — Source still matches plan

A regular source file is removed only if its current identity and content still match the plan record immediately before removal.

### INV-06 — Durable state ordering

The durable transaction state never claims a later stage until the filesystem state required by that stage has been synchronized according to the supported crash model.

### INV-07 — Alias follows selected target

An alias symlink cannot remain behind solely because its own modification time places it in another stream when its target regular file is removed.

### INV-08 — One destructive lock domain

Two invocations that can mutate the same canonical source or destination root cannot execute destructive transactions concurrently.

## 3. Revised planning model

### 3.1 Discovery output

Discovery produces one source-root snapshot containing:

```text
RegularEntry
    relative_path
    lstat identity
    mtime_ns
    size

SymlinkEntry
    relative_path
    lstat identity
    mtime_ns
    link_text
    classification
    resolved_regular_path?      # exact safely resolved path within source root
    resolved_regular_identity?  # must match the discovered entry at that path
```

Discovery never follows directory symlinks during traversal.

### 3.2 Symlink classification before stream segmentation

After discovery, classify symlinks against the complete source-root regular-file path-and-identity map.

Classes:

- `alias-to-source-regular`;
- `relative-non-alias`;
- `absolute`;
- `ignored-by-policy`;
- `unsupported-or-unresolvable` when required by implementation diagnostics.

Alias classification is independent of stream membership.

### 3.3 Boundary entries

Boundary entries are:

- regular files; and
- relative non-alias symlinks only when the selected symlink rule archives them.

Alias symlinks, absolute symlinks, and ignored/skipped symlinks do not influence stream boundaries.

This preserves the user's file-stream intent while preventing alias timestamps from blocking or splitting the selected target stream.

### 3.4 Stream segmentation

Sort boundary entries by:

```text
(mtime_ns, relative_path)
```

Start a new stream when the adjacent gap is at least `stream_gap`.

Eligibility uses the newest boundary-entry timestamp.

All comparisons use integer nanoseconds.

### 3.5 Alias attachment

After eligible streams are selected, construct the set of selected regular-file **paths** across the source-root plan.

For each candidate alias symlink:

- resolve the symlink without treating inode equality as pathname ownership;
- require the resolved pathname to be under the same source root;
- require an exact discovered regular entry at that relative path;
- require the resolved filesystem identity to match that discovered entry; and
- only if that exact target path is selected, attach a cleanup-only alias action to the archive plan that owns the target.

A hardlink outside the source root, or a different in-source hardlink pathname that is not the selected target path, does not authorize alias cleanup.

The alias modification time does not affect plan eligibility or archive display bounds.

### 3.6 Non-alias relative symlink decision

A relative non-alias symlink is a normal boundary entry when the configured rule preserves relative symlinks. It can therefore be archived in the stream selected by its own modification time.

This preserves the earlier behavior for non-alias symlinks while removing alias-induced stream distortion.

**Limitation:** an archived relative symlink can be dangling when its target is not included in the same archive. The manifest must record the original link text and the documentation must state this behavior.

## 4. Archive plan and naming

An archive plan contains two distinct sets:

```text
payload_actions
cleanup_only_actions
```

Payload actions create regular files or preserved symlinks in the archive.

Cleanup-only actions remove alias symlinks whose selected target moved successfully.

The plan records two timestamp ranges:

- `selection_range`: oldest/newest boundary entries used to define the stream;
- `payload_range`: oldest/newest source entries that produce archive payload objects.

The archive directory name uses `payload_range`.

The manifest records both ranges when they differ.

## 5. Revised transaction state machine

### 5.1 Logical states

```text
S0  planned
S1  staging-written
S2  staging-verified
S3  committed-pending-cleanup
S4  committed-reverified
S5  cleanup-in-progress
S6  cleanup-durable
S7  final-archive-verified
S8  completed
```

The state names describe logical design states. They do not require one separate on-disk file per state.

### 5.2 Normal execution

#### Step 1 — Plan

Create a deterministic archive plan from the source snapshot.

No mutation occurs.

#### Step 2 — Create staging payload

Create all payload objects under destination-local staging.

For regular files:

- copy or compress from the source;
- record source-content SHA-256;
- record archive-payload SHA-256;
- apply contract metadata; and
- synchronize file content and contract metadata.

For preserved symlinks:

- recreate the symlink using original link text;
- never dereference it to create payload bytes.

#### Step 3 — Make staging namespace durable

Synchronize every staging subdirectory whose entry set changed, from leaves toward the staging root, according to the supported Linux durability model.

Write the pending manifest and payload hash index durably.

#### Step 4 — Verify staging

Verify every staged regular payload.

For compressed payloads, decompress and compare with the recorded source-content hash.

#### Step 5 — Commit archive directory

Atomically rename the staging directory to the final archive directory on the destination filesystem.

Synchronize the destination directory containing the renamed archive directory.

The archive is now `committed-pending-cleanup`.

#### Step 6 — Re-verify committed archive

Read and verify the final committed archive representation.

This check must operate on the final path and must validate:

- manifest structure/ownership;
- expected payload presence;
- payload hashes;
- compressed round trips;
- payload hash index consistency; and
- plan/evidence consistency needed for cleanup.

If this step fails, stop without deleting a source entry.

#### Step 7 — Revalidate and remove sources

For each regular source file selected for cleanup:

1. revalidate identity and source-content hash;
2. remove the source entry only after successful revalidation; and
3. record cleanup progress in memory/logging.

For each cleanup-only alias:

1. confirm that its target action belongs to the completed archive plan;
2. confirm the alias itself still matches the planned symlink identity/link text; and
3. remove the alias.

If a source changed, stop cleanup for the affected transaction and report the preserved state.

Cleanup does not recursively remove empty source directories. Directories are not selected cleanup entries in the current contract, so even a directory made empty by selected-file removal remains present.

#### Step 8 — Synchronize cleanup

Synchronize every source directory whose entry set changed before marking cleanup complete durably.

#### Step 9 — Write final manifest

Write the manifest with `cleanup_complete=true` and cleanup completion time.

Synchronize the manifest and archive directory state.

#### Step 10 — Final verification

Re-run final archive verification against the final manifest/hash index.

#### Step 11 — Write completion evidence

Write `SUCCESS.json` only after final verification succeeds.

Synchronize `SUCCESS.json` and its containing archive directory.

The transaction is now complete.

## 6. Cleanup recovery design

Recovery is a reconciliation workflow, not blind continuation.

### 6.1 Candidate discovery

A committed archive with a valid pending manifest and no valid completion evidence is a cleanup-recovery candidate.

Recovery must not depend on source state alone.

### 6.2 Recovery order

For each candidate:

1. validate archive ownership and manifest schema;
2. verify the committed archive payload and hash index;
3. identify cleanup actions already complete by authoritative source inspection;
4. validate every remaining source entry against its plan record;
5. if any archive verification fails, stop and preserve every remaining source entry;
6. if any remaining source validation fails, stop and preserve that source entry;
7. perform only remaining idempotent cleanup actions;
8. synchronize affected source directories;
9. write the final cleanup-complete manifest;
10. perform final archive verification; and
11. write completion evidence.

### 6.3 Recovery corruption result

If committed archive corruption is detected before cleanup resumes, report:

```text
operation = cleanup_recovery
outcome = blocked
preserved_state = remaining source entries unchanged
retry_safe = no, until archive state is repaired or operator chooses another recovery action
```

The application must never repair archive corruption by deleting the source.

## 7. Durability design

### 7.1 Supported claim

The 0.4 design makes a bounded Linux-local-filesystem claim rather than an absolute persistence claim.

Contract wording:

> On supported Linux local filesystems, the transaction explicitly synchronizes payload files and every directory entry required by its recorded durability boundaries. This reduces crash-ordering ambiguity but does not replace filesystem-, storage-, or hardware-specific guarantees.

### 7.2 Directory synchronization helper

Introduce one internal durability abstraction that can synchronize:

- a regular file after final metadata changes;
- one directory after entry creation/removal/rename; and
- a path hierarchy in deterministic leaf-to-root order where needed.

The implementation must not scatter ad-hoc `fsync` calls without one documented ordering model.

### 7.3 Fault-oriented tests

Implementation verification must inject or simulate failure at material boundaries, including at least after:

- payload file creation;
- nested-directory creation;
- pending-manifest write;
- staging verification;
- final rename;
- committed re-verification;
- first source unlink;
- partial source cleanup;
- cleanup-directory synchronization;
- final manifest write; and
- final verification before completion evidence.

The acceptance property is recovery to either:

```text
valid source retained
```

or:

```text
valid committed archive retained with recoverable cleanup state
```

but never loss of both valid representations due to application ordering.

## 8. Time model

Use three separate concepts:

### 8.1 Planning reference time

Used only to evaluate age eligibility in `plan`.

CLI:

```text
stream-archiver plan [--at <ISO-8601>] [--json]
```

The normal `plan` command uses the observed current clock. The exported destructive `run_policy()` function also owns an observed clock and exposes no artificial-time parameter. A private deterministic helper is retained for behavior tests only.

### 8.2 Execution observation time

Used by destructive commands and operational state transitions.

No public destructive command accepts `--at` or `--now`.

### 8.3 Evidence event time

Manifest creation, cleanup completion, completion evidence, and scheduling success timestamps are captured when those events occur.

Tests inject a clock through the Python boundary rather than changing public destructive CLI semantics.

## 9. Verification model

### 9.1 Policy-scoped verification

Default `verify` behavior honors selected policies.

For each selected policy/source root, verify only archive directories whose manifest ownership matches that policy/source root.

### 9.2 Destination-wide audit

CLI:

```text
stream-archiver verify --all-in-destination
```

This verifies all recognized archive directories under destination roots reached by the selected configuration.

Alternative command name `audit-destination` may be considered, but one operation with an explicit scope flag is currently preferred to avoid command proliferation.

### 9.3 Archive-shaped directory recognition

The archive naming grammar becomes part of the design contract.

During destination-wide audit:

- a directory matching the archive naming grammar is an archive candidate even when `MANIFEST.json` is missing;
- missing required evidence is an error;
- unrelated directories that do not match the grammar are ignored.

### 9.4 Ownership validation

Policy-scoped verification validates manifest ownership fields before payload verification. An archive whose naming/manifest ownership conflicts is reported, not reassigned heuristically.

## 10. Concurrency model

### 10.1 Resource-based locking

Do not use invocation-specific lock-file paths as the primary concurrency contract.

Linux cooperative-lock design:

- derive a lock identity from the canonical filesystem resource path;
- acquire shared locks for the relevant existing ancestor directories and an exclusive lock for each source/destination root that the invocation can mutate;
- promote a resource to exclusive when the same resource is requested in both modes;
- acquire ancestor resources before descendants, then canonical lexical order within a depth;
- hold the complete lock set through destructive recovery, planning, execution, and due-state update;
- acquire shared lock sets for `verify` so a cooperating destructive process cannot mutate the audited resource hierarchy concurrently.

The implementation uses advisory locks. It coordinates Stream Archiver processes; it does not prevent non-cooperating producer processes from writing source files or replacing a pathname after final validation. Nested roots therefore contend within the Stream Archiver lock protocol, while content and namespace quiescence remain explicit operating preconditions.

### 10.2 Multiple processes/configurations

Two processes whose canonical source/destination hierarchies overlap contend on at least one common lock. This includes an ancestor/descendant pair such as `/a` and `/a/b`.

The implementation must fail or wait according to an explicit CLI/service policy. The current design preference is non-blocking failure with an actionable diagnostic for manual invocation and a retryable service failure for scheduled invocation.

### 10.3 Read-only commands

`plan` does not require exclusive locks by default, but it must label the result as a snapshot that can become stale before execution.

`verify` acquires the shared hierarchical cooperative lock set for destination resources in scope. The destination lock is the common resource with a cooperating commit and does not make archive verification depend on the continued existence of an old source root. `plan` remains unlocked and explicitly produces a potentially stale snapshot.

## 11. Scheduling-state model

### 11.1 State schema v2

State record:

```json
{
  "schema_version": 2,
  "policies": {
    "policy-name": {
      "last_success": "...Z",
      "policy_fingerprint": "sha256:..."
    }
  }
}
```

### 11.2 Fingerprint inputs

The fingerprint includes every field whose change must trigger immediate reevaluation:

- canonical source roots and order;
- canonical destination root;
- `minimum_age`;
- `stream_gap`;
- symlink rule; and
- compression rules.

It excludes logging and timer-presentation settings that cannot change archive selection or representation. The ordered canonical source roots, canonical destination, `minimum_age`, `stream_gap`, symlink rule, and ordered compression rules are all included.

### 11.3 Migration

When a v1 state record is read:

- preserve the old timestamp for diagnostic/migration evidence;
- treat the policy as due because no trusted matching fingerprint exists;
- write v2 only after a successful policy completion.

## 12. Logging and progress design

### 12.1 Run correlation

Create one `run_id` per CLI invocation.

Every operational event contains it.

### 12.2 Stable field vocabulary

Use:

```text
run_id
policy_name
source_root
source_path
destination_root
archive_name
archive_directory
plan_id
phase
operation
outcome
retry_safe
```

Do not use `source` for both root and individual path.

### 12.3 Transaction phases

Canonical phase names:

```text
discovery
planning
staging
staging_verification
commit
committed_verification
source_revalidation
source_cleanup
cleanup_persistence
final_verification
completion_evidence
recovery
```

### 12.4 Progress

Each phase may expose phase-specific item and byte progress.

A top-level transaction state may expose ordinal phase progress, but must not report `100%` until the completed state.

For large source revalidation and final verification, emit bounded byte milestones as is already done for staging.

### 12.5 Transition wording

Log state transitions after they occur, or use explicit intent wording before the action.

For example:

```text
event=source_revalidation_started
...
event=source_revalidation_succeeded
...
event=source_removed
```

Do not log `removing verified source entry` before verification has completed.

## 13. systemd design

### 13.1 Remove operational `ConditionPath...` gates

The generated unit starts the application even when a required policy/source/destination path is missing so the application can report the structured failure and exit non-zero.

### 13.2 Filesystem sandbox

Continue deriving writable paths from validated configuration.

When required paths are under a home tree, evaluate this order:

1. `ProtectHome=read-only` plus explicit writable exceptions;
2. if that cannot satisfy the required access contract, use `ProtectHome=false` and record the reason in generated deployment instructions.

### 13.3 Mount behavior

If mounted storage is a supported operational target, evaluate generated `RequiresMountsFor=` dependencies as an optional deployment mechanism. Application-level path validation remains mandatory.

### 13.4 Installation gate

The installer continues to stop before:

- first destructive run; and
- timer enablement.

The generated instructions require:

1. config review;
2. read-only plan;
3. one manual service execution;
4. archive verification; and
5. explicit timer enablement.

## 14. Logical move metadata contract

The product uses **logical move** to mean:

> create the required archive representation, verify it, and remove the source according to the transaction contract.

For regular files, preserve:

- content;
- relative path subject to archive transformation rules;
- permission mode bits; and
- modification time.

Do not imply preservation of:

- inode identity;
- owner/group identity;
- ACLs;
- extended attributes;
- sparse allocation;
- creation time; or
- filesystem-specific metadata.

If future use cases require these properties, add them as explicit requirements before implementation.

## 15. Documentation architecture

Repository documentation contains:

```text
README.md
    concise purpose, safety boundary, installation, common commands

docs/requirements.md
    stable behavioral and operational requirements

docs/design.md
    current mechanisms, state machines, invariants, trade-offs

docs/operations.md
    deployment, first-run, recurring operation, failure and recovery procedures

docs/verification.md
    evergreen maintainer verification procedure
```

Per-release review records and exact verification results remain external release evidence.

### 15.1 README assurance wording

Until the 0.4 implementation passes SAFE-DELETE and durability gates, avoid headings or claims such as:

```text
safe move
crash-safe
independently verified
```

Prefer precise descriptions of the mechanism and its verified boundary.

## 16. Requirement-to-design traceability

| Requirement family | Primary design section |
|---|---|
| R-SCOPE | §1 Design objective; §2 Primary invariants; §7.1 Supported durability claim |
| R-CONFIG | §3 Revised planning model; §20.1 Configuration schema 3; §23.5 and §24.1 schema-4/5 boundaries |
| R-DISC | §3.1 Discovery output; §3.3 Boundary entries |
| R-STREAM / R-SYM | §3 Revised planning model |
| R-NAME | §4 Archive plan and naming |
| R-MOVE | §14 Logical move metadata contract |
| R-SAFE | §5 Transaction state machine; §6 Cleanup recovery |
| R-DUR | §7 Durability design |
| R-EVID | §5 Transaction state machine; §9 Verification model |
| R-VERIFY | §9 Verification model |
| R-TIME | §8 Time model |
| R-SCHED | §11 Scheduling-state model |
| R-CONC | §10 Concurrency model |
| R-COMPAT | §§6, 9–13; §18 retained compatibility decisions; §22.8 output migration |
| R-PLAN | §20.3 Planning presentation boundary; §21.5 capacity observation |
| R-LOG | §12 Logging and progress design; §21.3 work-triggered progress |
| R-SYSTEMD | §13 systemd design; §20.7 safe generated deployment files |
| R-INSTALL | §13.4 Installation gate |
| R-API | §8.2 Execution observation time; §10 concurrency model; §20.5 public lock ownership |
| R-DOC | §15 Documentation architecture; this traceability section |
| R-PROG / R-CAP | §21 Runtime accounting and capacity design |
| R-REPORT | §22 Final reporting design |
| R-ANAME | §23 Archive-unit identity and configurable naming design |
| R-PART | §24 Archive-unit subdivision design; §25.4 pending group recovery |
| R-DIST / R-REL / R-TEST | §25 Review-closure and source-distribution design; §26.4 beta.2 closure |
| R-PY | §26.3 Supported Python policy |

## 17. Retained 0.4.0a1 implementation sequence

This sequence records the dependency order used for the 0.4.0a1 implementation.

1. Add requirements and state-machine regression tests for SAFE-DELETE.
2. Repair normal and recovery verification ordering.
3. Add durability abstraction and fault-oriented checks.
4. Refactor symlink classification/planning.
5. Separate planning time from execution clocks.
6. Redesign verification scopes and malformed archive discovery.
7. Replace lock-file contract with resource-based locking.
8. Migrate scheduling state to fingerprinted schema v2.
9. Update progress/logging vocabulary and phases.
10. Update systemd generation and operational docs.
11. Run full source, installed-wheel, crash/fault, and deployment verification before beta promotion.

## 18. Retained 0.4.0a1 foundation decisions

The earlier draft uncertainties are closed as follows:

1. Preserved non-alias relative symlinks remain boundary entries and use their own modification times.
2. The durability target is the bounded local-Linux-filesystem contract in §7.1; no unqualified NFS/SMB/FUSE/overlay guarantee is made.
3. `verify` acquires shared hierarchical cooperative locks; `plan` remains an unlocked read-only snapshot.
4. Destination-wide audit uses `verify --all-in-destination`.
5. The due-state fingerprint includes every selection or representation field listed in §11.2, including destination and compression rules.

Additional compatibility decisions:

- completed supported 0.3 archives remain verifiable;
- supported 0.3 pending-cleanup archives use the corrected verify-before-delete recovery order;
- state format v1 remains readable but is conservatively due until a successful v2 write;
- the destructive global `--now` option is removed and read-only `plan --at` replaces it;
- legacy `--lock-file` may be parsed during migration but does not define the safety lock domain; and
- generated 0.3 systemd units must be regenerated after upgrade.

## 19. Self-review additions from current CPS and Software Quality guidance

The 2026-09-13 review applies CPS 0.5.0-alpha.1 and Software Quality 0.4.0-alpha.4. It adds these implementation controls:

- distinguish executed steps, observed intermediate state, and intended outcome when fault tests fail;
- use test oracles that can discriminate the named defect and demonstrate regression sensitivity for critical fixes;
- evaluate persistent intermediate states and retry identity explicitly;
- keep correctness, compatibility, durability, and operator usability as separate verification claims;
- preserve exact unfinished payload and first resume action if delivery is interrupted; and
- treat source-tree tests, installed-package tests, systemd verification, and patch replay as different evidence surfaces.

These controls do not change the product's archival scope. They change how the 0.4.0a1 implementation is structured and verified.


## 20. 0.4.0a2 operator and boundary consolidation

### 20.1 Configuration schema 3

Schema 3 adds two orthogonal policy controls:

```toml
recursive = true
stream_partition = "source-root"  # or "parent-directory"
```

`recursive` controls which filesystem entries are discovered. `stream_partition` controls which discovered boundary entries can be adjacent in one time stream. The two mechanisms remain independent. Schemas 1 and 2 retain `recursive=true` and `source-root` partitioning.

Configured source and destination root paths are checked for a direct symlink object before canonicalization. After that check, canonical paths remain authoritative for overlap and lock-domain calculations.

### 20.2 Archive layout remains relative-tree preserving

Recursive discovery does not flatten output. Each payload object keeps its source-relative path, subject only to the existing documented compression suffix transformation/disambiguation. Multiple configured source roots remain separate archive plans even when they share one destination. No new destination namespace/layout mode is introduced in this candidate.

### 20.3 Planning presentation boundary

Planning state and rendering are separate responsibilities. `PolicyPlan` remains the immutable planning result. A presentation layer derives bounded aggregate statistics without changing selection:

- policy/source/stream/archive counts;
- selected regular-file count and input bytes;
- move/gzip/bzip2 disposition;
- preserved-link and cleanup-only-alias counts;
- top file extensions with a bounded `other` bucket; and
- per-source summary context.

`plan` renders this human summary by default. `plan --json` retains the action-level machine representation. The approximate 40-line target is a reader-task design target, not a correctness threshold.

### 20.4 Event-driven logging levels

INFO is reserved for meaningful run/policy/source and eligible-stream/archive lifecycle transitions plus aggregate byte progress that arises naturally from staged work. Normal entry discovery, per-entry staging, hashing, revalidation, cleanup, and lock acquisition are DEBUG. WARNING and ERROR remain for degraded/failed states requiring operator awareness.

No timer, heartbeat, rate limiter, suppression interval, or synthetic progress event exists to satisfy a logging cadence. Representative runs may measure the operator preference of roughly one useful INFO event per 1–10 seconds as a post-hoc usability heuristic only.

Structured machine logs retain complete paths. Entry-level text events use relative paths where the source/archive root is already identified by surrounding lifecycle context. Failure diagnostics can include full paths when needed. `archive_action` identifies an archival action; `next_action` is reserved for remediation.

### 20.5 Public lock ownership

Public `run_policy`, `verify_policies`, `verify_destination`, and single-archive verification acquire the cooperative lock set required by their contract. Private helpers are used when the CLI already owns a broader multi-policy/state lock transaction. A public `resources_locked=True` assertion is not part of the supported API.

### 20.6 Planning collision index

Archive-path collision validation uses an index of exact payload objects and occupied parent prefixes. An ordinary conflict lookup is proportional to candidate path depth rather than to the number of previously selected payload objects. Fixed source-derived payload paths retain precedence; generated compression paths alone may be disambiguated.

### 20.7 Safe generated deployment files

`render-systemd --force` creates a temporary file in the output directory and replaces the output pathname with `os.replace`. This replaces an existing symlink object rather than opening and writing through its target.

The guided installer resolves the source tree's exact declared package version and refuses installation when no matching wheel exists. Modification time is not a version-selection fallback.

### 20.8 Documentation architecture

The maintained user-facing layers are:

- `README.md`: concise orientation, safety boundary, quick start, common commands;
- `docs/user-guide.md`: mental model, configuration decisions, discovery/partition/layout, planning, logging, examples, FAQ;
- `docs/operations.md`: deployment, permissions, systemd, recovery, upgrades;
- `docs/verification.md`: maintainer/release evidence procedure;
- `docs/requirements.md`: normative behavior; and
- `docs/design.md`: selected mechanisms and rationale.

Cross-links replace large duplicated explanations.

### 20.9 Whole-package review corrections

The second alpha incorporates the decision-ready findings from the 2026-09-27 CPS/SQG self-review. In addition to the operator changes, it removes unrelated empty-directory cleanup, strengthens exact-path alias classification, rejects root symlinks before canonicalization, closes public lock/clock bypasses, makes systemd output replacement symlink-safe, fails closed on mismatched installer wheels, and makes staging-cleanup failures visible.


## 21. 0.4.0a3 runtime accounting and capacity design

### 21.1 Invocation accounting boundary

`service._run_policies_at` owns the top-level selected-work boundary. It performs pending-archive recovery before establishing new-work totals. It then plans every selected policy at one common planning reference time and freezes the archive-plan tuple before destructive processing. This makes stream, regular-file, and source-byte denominators stable for the invocation. It also means later policies in one invocation no longer acquire a later eligibility reference time merely because earlier policies took time to execute.

The monotonic run clock starts when this orchestration starts. Recovery and planning therefore contribute to elapsed invocation runtime, while their work remains excluded from the newly selected stream/file/source-byte denominator.

`run` uses this multi-policy orchestration directly. `run-if-due` first identifies the due subset, then executes that subset as one accounting boundary. Its completion callback persists each successful policy's due-state immediately, preserving the established partial-success behavior if a later due policy fails.

### 21.2 Progress types and executor boundary

`progress.py` contains the accounting model rather than a general event framework:

- `RunTotals` is the immutable selected-work denominator;
- `RunProgressSnapshot` is the structured observation;
- `ExecutionProgressDelta` is the narrow executor-to-service byte observation; and
- `RunProgressTracker` owns monotonic counters and elapsed time.

The executor reports bytes that it actually reads from selected regular source files and the final logical payload size after each regular payload object is finalized. It does not decide log severity or invocation completion. The service marks selected files complete only after the archive transaction succeeds. A reconciliation path can finish selected work without rereading all bytes; transaction completion accounts any planned source-byte remainder while preserving the distinction between byte staging and file completion.

The existing archive-local ten-percent INFO staging event remains available to direct executor callers. When service-level accounting supplies a progress sink, that local INFO event is suppressed so one operation does not produce two competing aggregate INFO progress streams. Per-entry success detail remains DEBUG.

### 21.3 Work-triggered INFO progress

The tracker can return a progress snapshot when processed source bytes cross a new ten-percent boundary below 100%. This boundary is triggered only by observed work. It is not a timer and does not attempt to enforce a wall-clock cadence. Start, stream start, stream completion, and run completion are explicit INFO lifecycle observations.

### 21.4 Human size presentation

`presentation.format_bytes` is the shared human formatter. It uses decimal SI divisors: 1000 B = 1 kB, 1000 kB = 1 MB, and so on. Values below 10 scaled units normally use two decimal digits; larger scaled values use one. Bytes remain integers. Structured progress/capacity fields always carry exact integers in addition to any display strings.

This choice follows the requested `KB/MB/GB/TB`-style human convention while using the standards-consistent SI symbol `kB`. It avoids the previous proposal to display IEC units unless an explicit IEC contract is later adopted.

### 21.5 Planning capacity observation

`capacity.observe_capacity` uses `statvfs` and `f_bavail * f_frsize` (falling back to `f_bsize` when required) to observe bytes available to the process. If a destination does not yet exist, planning probes the nearest existing ancestor and does not create destination state. An `OSError` becomes a bounded `CapacityUnavailable` observation, not a synthetic zero-byte value.

Planning aggregates selected regular source bytes by exact destination root. Human output shows the advisory comparison; `plan --json` retains exact selected, free, and total byte fields. Compression is deliberately not estimated, so the selected-source comparison is conservative for compressible content but can still be insufficient for incompressible transformed output, metadata, staging overlap, or concurrent filesystem use.

### 21.6 Execution warning state

Execution observes capacity before every selected stream. The comparison uses remaining selected source bytes for that exact destination. `CapacityWarningTracker` stores only semantic warning state:

- first insufficient observation -> warning;
- repeated equivalent insufficient observation -> no new warning;
- return to sufficient -> reset;
- later insufficient observation -> warning again;
- first unavailable observation -> warning; repeated unavailable observations remain quiet until a successful observation resets that state; and
- an insufficient shortfall that worsens by at least `max(64 MiB, 5% of last warned shortfall)` -> another warning.

No wall-clock state participates in de-duplication. The threshold is an operator-noise policy, not a safe-space margin.

### 21.7 Post-stream unexpected-capacity diagnostic

For a newly created committed archive, the service can compare free bytes before and after the transaction with POSIX block allocation measured as `st_blocks * 512` over the committed archive tree using `lstat` without following symlinks. If block counts are not available, the diagnostic is omitted.

The diagnostic computes:

`observed_loss = max(0, free_before - free_after)`

`residual = max(0, observed_loss - known_archive_allocation)`

A residual is material at `max(64 MiB, 5% of known_archive_allocation)`. The log event calls this residual **unexpected destination capacity consumption**. It does not identify an external writer or any other cause. The calculation is intentionally diagnostic because filesystem metadata, allocation timing, snapshots, quotas, concurrent activity, and other storage behavior can change free-space observations.

### 21.8 Failure and safety behavior

Capacity checks do not reserve blocks and do not stop a stream solely because the advisory comparison is low or unavailable. Normal filesystem failures, including `ENOSPC`, continue through the existing execution failure path. Source deletion remains behind committed verification and source revalidation. The progress and capacity code therefore adds observability but does not weaken the archive transaction.

### 21.9 Rejected alternatives

This candidate does not add a timer/heartbeat, INFO rate limiter, periodic status thread, capacity reservation, hard low-space execution gate, output-size predictor, causal external-writer warning, generalized event bus, or persistent invocation ordinal. These mechanisms would either exceed the accepted `0.4.0a3` scope or require evidence and contracts not currently available.


## 22. 0.4.0a4 final reporting design

### 22.1 Boundary and non-goals

The `0.4.0a4` change begins after successful archive execution evidence exists.
It does not alter selection, staging, atomic archive commit, source revalidation,
cleanup, completion evidence, capacity decisions, archive names, or due-state
fingerprints.

The design adds `reporting.py` as a presentation/accounting boundary. It is not
a general analytics framework or plugin system.

### 22.2 Grouped execution evidence

`ArchiveExecutionResult` now carries immutable `RegularPayloadGroupResult`
records grouped by case-folded original source suffix and archive action. Each
group contains regular-file count, original source bytes, and finalized
transformed payload bytes. This preserves the requested extension ×
transformation evidence without retaining another per-file object after the
archive plan already owns per-file source metadata. Report-state memory therefore
scales with distinct suffix/action groups rather than selected file count.

For supported manifest formats 2 and 3, finalized byte counts come directly from
validated `archive_size` records created during staging. Legacy manifest format 1
predates that field; when a supported legacy archive is reconciled, the already
verified final payload file supplies the size before it is folded into the group.
Reporting therefore does not revisit deleted source files.

### 22.3 Invocation aggregation

`build_run_report()` consumes the completed `PolicyRunResult` values and an
observed elapsed runtime. It aggregates only newly selected completed archive
results. `recovered_archives` from the pre-planning recovery phase is retained in
a separate collection.

The result contains exact integer bytes and immutable transformation/extension
breakdowns. Derived savings and ratio values are properties of those same exact
integers. Zero source bytes yields no ratio/percentage. Negative savings is
preserved when a codec expands input.

The current directory representation has no single authoritative container-byte
quantity. `archive_container_bytes` and `filesystem_allocation_bytes` therefore
remain `None`; later representation/storage work can populate distinct fields
without redefining transformed payload bytes.

### 22.4 Extension and transformation model

Reporting maps regular actions as follows:

| Archive action | Report transformation |
|---|---|
| `move` | `uncompressed` |
| legacy `copy` | `uncompressed` |
| `gzip` | `gzip` |
| `bz2` | `bzip2` |

Original extension is `Path(source_path).suffix.casefold()`. Empty suffixes use
`<none>`. The result stores a complete extension × transformation matrix.
Console rendering ranks extensions by source bytes, then file count and label,
and displays a bounded prefix plus one aggregate `other` row. JSON and Markdown
do not discard the omitted console groups.

### 22.5 CLI rendering contract

Immediate execution now mirrors planning's operator split:

```text
stream-archiver run             # bounded human summary
stream-archiver run --json      # complete machine result
stream-archiver run --markdown  # complete Markdown to stdout
```

The renderers consume the same `RunReport`. Operational logs remain on stderr.
The modes do not mix human prose into JSON stdout.

`run-if-due` remains JSON because it is the scheduled state-management surface
and its per-policy completion callback persists due-state immediately. Replacing
that surface with a human report is outside this candidate.

### 22.6 Optional Markdown export

`run --report PATH` is checked before destructive execution. An existing final
pathname is a configuration failure before mutation. The parent must already
exist and be a directory.

After successful archival execution, the complete Markdown text is written to a
temporary regular file in the target directory and `fsync`ed. `os.link()` then
publishes the requested pathname only if it is still unused. The temporary name
is removed after publication when cleanup succeeds. A temporary-name cleanup
failure after successful publication emits a separate warning and does not
misreport the already published final report as an export failure. This prevents
a race from overwriting a file created after preflight while preserving the
actual partial-success state.

The report file is auxiliary. If post-commit publication fails, the CLI emits
`run_report_export_failed` at WARNING with preserved-state/remediation fields and
continues to return archival success. This prevents an auxiliary presentation
failure from causing callers to treat committed archive transactions as though
they must be repeated. The operator can re-render from retained machine evidence
only when such evidence was separately captured; automatic historical report
regeneration is not claimed by this candidate.

### 22.7 Human report structure

The bounded console form reports runtime, policies, selected streams, completed
archive units, prior recoveries, regular files, link dispositions, source bytes,
written payload bytes, signed payload savings, payload-size ratio,
transformation disposition, and the highest-volume extension groups.

The Markdown form is intentionally more complete. It contains exact byte values,
all extension/transformation rows, archive output paths, recovered prior archive
paths, and an explicit metric-boundary note.

### 22.8 Compatibility decision

Changing immediate `run` from implicit JSON to human output is an intentional
pre-1.0 CLI incompatibility authorized by the operator request. The migration is
explicit `run --json`. No compatibility claim is made for consumers that parse
the old implicit JSON without adopting the flag.

The result schema starts at `schema_version = 1`. Future additive fields must
preserve the existing metric meanings; a semantic redefinition requires a schema
and compatibility decision.

### 22.9 Deferred work remains separate

This reporting boundary deliberately does not introduce archive-unit
subdivision, ZIP representation, configurable persistent archive naming, a
storage-backend protocol, durable stream sequence allocation, automatic report
history, or report-as-archive-provenance behavior. Those changes have different
persistent-state, extension-contract, or recovery boundaries.


## 23. 0.5.0a1 archive-unit identity and configurable naming design

### 23.1 Reconciled implementation baseline

The user-supplied `0.4.0a5` sdist did not contain the issued `0.4.0a4` reporting
module or repository documentation. The implementation baseline for this change
is therefore a three-way reconciliation: issued `0.4.0a3` is the common source,
issued `0.4.0a4` supplies the accepted reporting delta, and the source files
present in the user `0.4.0a5` sdist supply the user-side changes. The reconciled
state passed the complete 117-test source suite before 0.5 implementation.

The sdist does not preserve repository-only `local/` or ignore configuration, so
no claim is made about the exact repository-level `local/` implementation beyond
not treating its absence from the sdist as a deletion instruction.

### 23.2 Identity model

`plan_id` remains the deterministic logical archive-plan digest over policy,
source/destination roots, selected entries, actions, identities, and selection
bounds. `ArchivePlan.archive_unit_index` was introduced in `0.5.0a1` with value
zero for its one-unit streams. `0.5.0a2` extends that field to the deterministic
zero-based range of a subdivided logical stream.

The full archive-unit identity is:

```text
SHA256(canonical JSON {
  domain: "stream-archiver/archive-unit/v1",
  plan_id: <64-hex plan id>,
  unit_index: <non-negative integer>
})
```

This keeps identity independent of archive representation and human name. The
`0.5.0a2` subdivision design below defines stable action ordering and unit allocation
before indexes above zero are assigned. Invocation progress indexes are unrelated.

### 23.3 Persistent archive name

For schema 4 the final directory name is:

```text
<rendered-human-prefix>--sa-<first-20-hex-of-archive-unit-id>
```

Twenty hexadecimal characters provide an 80-bit truncated namespace. The full
identity remains available through `ArchivePlan.archive_unit_id` and JSON plan
output. A truncated collision is not silently resolved: an occupied final path
whose manifest does not match the current plan follows the existing hard
collision/error path.

Schemas 1–3 and programmatic `Policy(..., archive_name_template=None)` retain the
historical microsecond-boundary plus `plan_id[:10]` name. This prevents a config
file from changing persistent namespace merely because the package is upgraded.

### 23.4 Controlled template formatter

`naming.py` owns the current controlled grammar. `string.Formatter` is used only
to parse replacement fields; Python `datetime.strftime()` is not used for human
month/weekday names because its `%a`/`%b` output depends on locale.

Allowed replacement fields are `start`, `end`, and `span`. Start/end directives:

- `%Y`, `%m`, `%d`, `%H`, `%M`, `%S` — zero-padded numeric UTC components;
- `%b`, `%a` — fixed English three-letter month/weekday tokens;
- `%f` — six-digit microseconds;
- `%N` — nine-digit nanoseconds; and
- `%%` — literal percent.

An omitted start/end format uses `%Y%m%dT%H%M%SZ`, so sub-second text is opt-in.
Span directives are `%D`, `%H`, `%M`, `%S`, `%f`, `%N`, and `%%`; omitted span
format uses `%Dd%Hh%Mm%Ss`. Omitted precision affects only the human prefix.

### 23.5 Configuration-schema boundary

Schema 4 adds `archive_name_template`. Its default is the current whole-second
start/end template. Older schemas reject the key and leave the Policy field as
`None`. This explicit schema boundary is preferable to silently changing names
for all existing configurations.

The policy scheduling fingerprint includes the effective template because a
change creates a different persistent destination namespace even though stream
selection is unchanged.

### 23.6 Filename safety and filesystem limits

Configuration validation rejects path separators, NUL, the reserved `--sa-`
marker, unknown fields/directives, conversions, and nested replacement fields.
Planning validates the rendered prefix and the complete encoded filename length.
`os.pathconf(destination, "PC_NAME_MAX")` is used when available; 255 bytes is
the local-Linux fallback if the limit cannot be queried. The actual filesystem
operation remains authoritative if the environment differs.

### 23.7 Historical discovery and verification

The existing strict legacy archive-name regular expression remains. Destination
verification additionally recognizes any real directory whose basename ends in
a syntactically valid current `--sa-<20 hex>` suffix. This permits arbitrary
human templates while retaining malformed-archive detection.

For current-format names, manifest validation recomputes the expected suffix from
`plan_id` and the manifest's validated deterministic unit index. Renaming a current
archive while changing only the manifest's `archive_name` therefore cannot detach
visible identity from the original plan/unit. Historical names do not receive this
new suffix check.

### 23.8 Deferred boundaries

No new storage protocol is introduced. The archive unit is still represented by
a local directory and uses the same staging, atomic commit, fsync, verification,
source-revalidation, cleanup, and recovery mechanisms. `0.5.0a2` builds generic
subdivision on `archive_unit_index`; ZIP remains a later representation; durable
human sequence allocation and non-filesystem destination backends remain
separate decisions.


## 24. 0.5.0a2 archive-unit subdivision design

### 24.1 Separation of concepts

`stream_partition` continues to choose the discovery/grouping domain before time
segmentation. The schema-5 archive-unit limits operate only after one logical
stream and its complete action set have been selected. The design therefore uses:

```text
source root -> grouping domain -> logical time stream -> archive units -> directory representation
```

This prevents the persistent grouping policy from being overloaded with a size
control and leaves archive representation independent for future ZIP work.

### 24.2 Deterministic partition algorithm

Planning first resolves the complete logical-stream action set and archive-path
collisions. It computes one logical `plan_id`. When subdivision is configured,
the plan-id input also records the effective maximum source bytes and maximum
regular files. This makes a partition-policy change an identity change while
leaving unpartitioned 0.5.0a1 identities unchanged.

Regular payload actions are sorted by `(mtime_ns, relative_path)`. A greedy pass
starts a new unit before adding the next regular file if the byte target would be
exceeded or if the current unit has already reached the file-count target. One
oversized file remains indivisible and occupies its own unit.

Cleanup-only aliases follow the unit that owns their selected regular target.
Other symlink actions are placed deterministically relative to regular-action
ordering and do not consume byte/file limits.

Every resulting `ArchivePlan` carries the common logical `plan_id`, its
zero-based `archive_unit_index`, and common `archive_unit_count`. The existing
domain-separated archive-unit identity hashes `plan_id` plus the unit index.

### 24.3 Group transaction boundary

A logical stream is now a group transaction for cleanup. Service execution
commits each archive unit with `defer_cleanup=True`. Every committed unit is
fully staged, atomically published, and verified, but its sources remain. After
all units exist, `complete_committed_plans()` validates group cardinality and
identity, verifies every pending unit, and revalidates all source entries before
performing any cleanup.

This ordering addresses the crash case in which unit zero commits but a later
unit fails. Source state remains sufficient to deterministically reconstruct the
original complete logical plan and therefore the same unit identities on retry.
It deliberately trades temporary duplicate storage for deterministic recovery
and source safety.

### 24.4 Manifest and recovery

Manifest format 3 adds `archive_unit_id`, `archive_unit_index`, and
`archive_unit_count`. Plan/manifest reconciliation checks all three fields in
addition to plan, source, destination, and archive name. Manifest validation also
recomputes the unit identity from plan id and index.

Recovery groups pending format-3 archives by `plan_id`. It does not delete
sources for an incomplete declared group. After planning, orchestration compares
any deferred incomplete group identities with the freshly reconstructed logical
plans. A mismatch (for example because partition policy or source membership
changed) stops execution before new archival mutation; silently abandoning the
old pending group is not permitted. A complete pending group is verified before
cleanup proceeds. Formats 1 and 2 retain their historical single-archive recovery
semantics.

### 24.5 Progress and reporting

Invocation totals deduplicate logical streams by source root plus logical
`plan_id`. Unit staging can reset unit-local byte reconciliation without
advancing the stream ordinal. Final reporting therefore exposes one selected
logical stream and multiple completed archive units when subdivision occurs.

### 24.6 Scope boundary

The unit byte limit is measured from original regular source sizes and is only a
planning target. It does not predict compressed payload size, block allocation,
or future ZIP overhead. `0.5.0a2` keeps the directory representation and local
filesystem durability model. ZIP and any representation-specific hard maximum
container size remain future design work; a destination storage protocol remains
deferred until a concrete second backend justifies an extension contract.


## 25. 0.5.0b1 review-closure and source-distribution design

### 25.1 Distribution boundary

The wheel remains the runtime artifact. The sdist is also a supported source-user,
review, and verification artifact because README explicitly routes users and
maintainers to in-tree examples, tools, tests, and normative documents.
`MANIFEST.in` therefore owns the non-runtime source-distribution inclusion policy.
It includes the maintained documentation, changelog, examples, tools, tests/support
modules, lockfile, and `.gitignore`, while `local/` remains repository-local.

Generated root `PKG-INFO` is not maintained repository source. Setuptools generates
current metadata while building the sdist. Release verification checks any generated
metadata against the project version instead of keeping a copied `PKG-INFO` that can
drift.

### 25.2 Release identity oracle

`pyproject.toml` is the package-version authority for a candidate. Release tests
derive that value and compare runtime `__version__`, the lockfile's local package
record, requirements/design target identity, leading changelog identity, and built
sdist metadata. Candidate-specific literals are not used as expected test values.
This lets the same tests survive prerelease advancement while still detecting stale
release-bound artifacts.

### 25.3 Locking-test oracle

The hierarchical locking regressions use two independently opened lock sets in one
Python process. On Linux, `flock` conflict/compatibility is associated with open file
descriptions, so this directly exercises the kernel relation that cooperating
processes rely on without adding process startup, pytest import-mode, queue, or
interpreter-teardown behavior to the regression oracle. A bounded installed-artifact
process smoke can supplement this check when useful, but process lifecycle is not a
release-dependent pass/fail condition for the lock semantics themselves.

### 25.4 Pending group recovery

Normal multi-unit execution already commits every unit before group cleanup. Restart
recovery now mirrors that group boundary: after confirming that all expected units
exist, it verifies every pending archive and validates every remaining selected source
for every pending unit before calling any per-unit recovery cleanup. Per-unit recovery
then revalidates its own sources immediately before unlink, preserving the existing
source-race fail-closed check.

### 25.5 Beta review boundary

The beta transition is a review/closure candidate, not a new feature tranche. It
closes source-distribution completeness, release-identity drift, requirement-ID
ambiguity, lock-test evidence instability, current-schema documentation drift, and
group-recovery prevalidation. ZIP representation, final-container hard size limits,
durable human numbering, and storage-backend plugins remain deferred.


## 26. 0.5.0b2 isolated-review closure design

### 26.1 Policy-scoped ownership routing

Policy-scoped verification separates ownership routing from full archive verification.
Before an archive candidate can be skipped as foreign-owned, `_manifest_owner()` reads
the manifest through the same regular-file/symlink-safe JSON boundary used by archive
verification and validates only the two fields necessary to establish ownership:
`policy_name` and absolute `source_root`. This avoids payload work for valid foreign
archives while making ambiguous ownership fail closed. Selected archives still pass
through the complete manifest, payload, checksum, and completion-evidence verifier.

### 26.2 Capacity field semantics under subdivision

The service computes one `stream_source_bytes` value for the logical stream group
before iterating its archive units. Every unit's destination-capacity precheck receives
that group value for `current_stream_source_bytes`; `remaining_source_bytes` retains
its existing destination-wide decreasing-work meaning. No new telemetry field or
schema is required.

### 26.3 Supported Python policy

The 0.5 release line uses a closed supported-minor policy: Python 3.11, 3.12, and
3.13. `pyproject.toml` therefore declares `>=3.11,<3.14`, the existing classifiers
name those three minors, and `install-systemd.sh` rejects other minor selectors before
root/system mutation. Patch selectors such as `3.13.7` remain valid because they are
within a supported minor. This is a support/evidence boundary, not a claim that 3.14
would necessarily fail at runtime.

### 26.4 Candidate scope

`0.5.0b2` is a review-closure candidate only. It does not add ZIP representation,
new subdivision policy, storage backends, or durable human sequence allocation.
