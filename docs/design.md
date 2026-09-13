# Stream Archiver Design

**Document version:** `0.4.0-alpha.2`
**Target package development version:** `0.4.0a1`
**Date:** 2026-09-13
**Status:** implementation design for the first 0.4 alpha candidate
**Requirements baseline:** `docs/requirements.md`, document version `0.4.0-alpha.2`

## 1. Design objective

The 0.4 design repairs the transaction and behavioral contracts without replacing the successful high-level architecture.

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
    resolved_regular_identity?  # only when safely resolvable within source root
```

Discovery never follows directory symlinks during traversal.

### 3.2 Symlink classification before stream segmentation

After discovery, classify symlinks against the complete source-root regular-file identity map.

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

After eligible streams are selected, construct the set of selected regular-file identities across the source-root plan.

For each alias symlink:

- if its target regular identity is selected, attach a cleanup-only alias action to the archive plan that owns the target;
- if the target is not selected, leave the alias unchanged.

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

CLI proposal:

```text
stream-archiver plan --at <ISO-8601>
```

The normal `plan` command uses the observed current clock.

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
| R-STREAM / R-SYM | §3 Revised planning model |
| R-NAME | §4 Archive plan and naming |
| R-SAFE | §5 Transaction state machine; §6 Cleanup recovery |
| R-DUR | §7 Durability design |
| R-TIME | §8 Time model |
| R-VERIFY | §9 Verification model |
| R-CONC | §10 Concurrency model |
| R-SCHED | §11 Scheduling-state model |
| R-LOG | §12 Logging and progress design |
| R-SYSTEMD | §13 systemd design |
| R-MOVE | §14 Logical move metadata contract |
| R-DOC | §15 Documentation architecture |

## 17. Implementation sequence used for 0.4.0a1

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

## 18. Resolved decisions for 0.4.0a1

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
