# Changelog

## 0.5.0b3 — 2026-10-01

- Integrate the beta2 review closures while preserving the committed 0.4 release history and repository-local ignore rules.
- Apply required Ruff normalization to the integrated beta source and verification tooling.

## 0.5.0b2 — 2026-09-30

- fix policy-scoped verification so malformed or ambiguous ownership evidence fails
  closed instead of being silently skipped as foreign-owned;
- preserve logical-stream byte semantics in capacity telemetry when one stream is
  subdivided into multiple archive units;
- make Python 3.11–3.13 the enforceable support range across Core Metadata, public
  documentation, classifiers, and the system installer; and
- add predecessor-sensitive regressions for all three isolated-review findings.

## 0.5.0b1 — 2026-09-30

- Promoted the reviewed 0.5 feature set to its first beta candidate after a package-wide Deep Consistency Review and review-bundle closure pass.
- Made the Python sdist a self-contained source-user/review artifact by including normative docs, changelog, examples, maintainer tools, complete pytest support, and the lockfile while excluding repository-local `local/`.
- Added a clean-sdist release gate that runs the complete shipped suite, builds the wheel from the sdist, reruns tests against the installed wheel, and verifies CLI/systemd surfaces without repository-only support files.
- Removed stale tracked `PKG-INFO`; generated metadata is now checked dynamically against the project version together with runtime, lockfile, requirements/design, and changelog identities.
- Replaced multiprocessing/subprocess-dependent locking regressions with a deterministic Linux `flock` oracle using independent open file descriptions, removing process startup, import-mode, queue, and teardown timing from release evidence.
- Fixed manifest-format-3 restart recovery to validate all pending archive-unit sources in a complete logical-stream group before the first unlink, while retaining per-unit revalidation immediately before cleanup.
- Removed duplicate requirement identifiers by assigning the configurable archive-name contract its own `R-ANAME-*` namespace and reconciled current schema-5/configuration terminology.
- Kept ZIP representation, hard final-container size, durable human sequence allocation, and storage-backend plugins deferred.

## 0.5.0a3 — 2026-10-01

- Applied behavior-preserving Ruff lint normalization to report text and restored the Python systemd verifier to non-executable mode.

## 0.5.0a2 — 2026-09-29

- Added configuration schema 5 with optional deterministic archive-unit source-byte and regular-file-count targets.
- Subdivided one logical stream greedily in stable modification-time/path order without splitting individual files; oversized single files remain intact.
- Bound subdivision policy into logical plan identity so different unit boundaries cannot reuse the same persistent unit identity, while unsplit 0.5.0a1 identity remains stable.
- Added manifest format 3 with archive-unit identity, index, and group count, plus explicit plan/manifest reconciliation.
- Changed subdivided-stream execution to commit and verify every unit before any source cleanup begins.
- Added group-aware pending recovery so an incomplete committed unit set cannot authorize cleanup.
- Kept logical-stream progress/report counts separate from archive-unit counts.
- Kept directory representation and local-filesystem durability semantics; ZIP and hard final-container-size semantics remain future work.

## 0.5.0a1 — 2026-09-29

- Added a format-neutral archive-unit identity, domain-separated from `plan_id` and parameterized by a deterministic unit index reserved for later stream subdivision.
- Added configuration schema 4 with per-policy `archive_name_template`; schemas 1–3 continue to use the historical persistent archive-name shape.
- Added mandatory `--sa-<20 hex>` stable identity suffixes for schema-4 archive names, keeping human naming separate from persistent identity.
- Added a controlled locale-independent formatter for UTC start/end and span fields, including fixed English month/weekday tokens and explicit microsecond/nanosecond opt-in.
- Added filename-safety and destination `PC_NAME_MAX` checks for rendered names.
- Added full archive-unit identity/index to machine planning output and included naming policy in scheduled-policy fingerprints.
- Extended destination audit and manifest validation to recognize current configurable names while retaining historical archive verification/recovery compatibility.
- Kept generic stream subdivision, ZIP representation, durable human sequence allocation, and storage-backend plugins outside this candidate.

## 0.4.0a5 — 2026-09-29

- Completed the 0.4.0a4 normalization by tracking the existing `local/.gitignore` unchanged and documenting that other `local/` contents remain excluded from commits.

## 0.4.0a4 — 2026-09-28

- Normalized the delivered alpha source with Ruff and resolved remaining Ruff diagnostics, including binding run-progress callbacks to their current stream.

## 0.4.0a3 — 2026-09-27

- Added invocation-level run progress with stable stream, regular-file, source-byte, written-payload-byte, and monotonic elapsed-runtime accounting across selected policies.
- Selected policies in one destructive invocation now share one planning reference time; a stream that becomes eligible only while earlier selected work is executing waits for a later invocation instead of changing the current progress denominator.
- Added advisory destination-capacity observations to `plan` and per-stream execution, including exact machine-readable byte fields and state-based warning de-duplication.
- Added neutral diagnostics for material unexplained free-space loss after a committed stream without attributing the observation to an external writer.
- Standardized human byte presentation on decimal SI units (`kB`, `MB`, `GB`, `TB`) while retaining exact integer bytes for machine data and comparisons.
- Kept capacity checks advisory: low or unavailable observed space does not bypass normal filesystem errors or weaken committed verification, source revalidation, cleanup, or durability ordering.
- Kept INFO work-triggered and stream/archive oriented; no timer, heartbeat, rate limiter, or cadence gate was introduced.

## 0.4.0a2 — 2026-09-27

- Added a concise human-readable `plan` summary and moved the complete action-level representation to explicit `plan --json`.
- Added configuration schema 3 with independent `recursive` discovery and `stream_partition` (`source-root` or `parent-directory`) controls; schema 1/2 remain compatible with historical defaults.
- Extended the v2 scheduling fingerprint with the new discovery controls; an existing 0.4.0a1 fingerprint therefore makes the policy conservatively due once after upgrade, then successful a2 execution stores the new fingerprint.
- Rebalanced logging so meaningful archive/stream lifecycle and aggregate staging progress remain INFO while routine per-entry and lock mechanics are DEBUG. The 1–10 second INFO preference is documented only as a post-hoc usability heuristic, not a timer or gate.
- Clarified that Stream Archiver creates no default log file: manual operational logs use stderr and generated systemd service logs are collected by journald.
- Bounded source cleanup to selected entries; automatic cleanup no longer removes unrelated or newly empty source directories. Regular payload sources are removed before cleanup-only aliases.
- Strengthened alias classification to require the exact resolved in-source target pathname plus matching identity, preventing external/different hardlinks from authorizing alias cleanup.
- Removed public destructive artificial-time and cooperative-lock bypass parameters; exported mutation and verification APIs own their required locks.
- Rejected configured root symlinks before canonicalization, made forced systemd regeneration replace output symlinks rather than write through them, and made the guided installer fail when no exact-version wheel exists.
- Replaced quadratic archive-path collision scanning with a path-prefix index; fixed source-derived paths still take precedence over generated compression paths.
- Added visible warnings when best-effort cleanup of failed staging directories cannot complete, and normalized failure diagnostics around `next_action`, preserved state, and retry safety.
- Added `docs/user-guide.md`, reduced README to an orientation/quick-start role, simplified `.gitignore`, and reconciled requirements/design/operations/verification documentation with the reviewed implementation.

## 0.4.0a1 — 2026-09-13

- Reordered normal and recovery cleanup so the final committed archive payload
  and checksum evidence are verified before any dependent source deletion.
- Added bounded durability helpers that synchronize final payload metadata,
  staging/destination namespaces, and source cleanup directories; synchronization
  failures cannot be converted into completed transactions.
- Moved symlink alias classification ahead of stream segmentation so an alias
  follows its selected target even when the alias timestamp is in another time
  stream; archive names now use payload-producing timestamp bounds.
- Removed the destructive global `--now` interface and added read-only
  `plan --at`; execution/evidence state uses observed execution time.
- Added policy-scoped verification, explicit `verify --all-in-destination`, and
  malformed archive-shaped directory detection.
- Replaced invocation lock-file coordination with cooperative hierarchical
  shared/exclusive filesystem-resource locks. Legacy `--lock-file` is accepted
  only as a deprecated, ignored migration option.
- Migrated due state to format v2 with policy fingerprints. Valid v1 state is
  readable but conservatively due until a successful v2 completion.
- Added invocation `run_id`, phase-aware completion/staging progress, and
  corrected source-cleanup log wording.
- Removed systemd operational `ConditionPath...` gates, changed home protection
  to `ProtectHome=read-only` with generated writable exceptions, and removed the
  obsolete generated lock-file argument.
- Added repository requirements, revised design, safety/compatibility/fault and
  hierarchical-lock regression tests, and local `uv` bootstrap/verification
  tooling. The installer now defaults to the declared Python 3.11 support floor.
- Scope the durability/safe-deletion claim to the documented local-Linux
  filesystem and producer-quiescence preconditions; the project is still not a
  backup, snapshot, authenticity, or automatic-restore system.

## 0.3.4b1 — 2026-08-13

- Promoted the 0.3.4 line to beta after the logging, deployment, path-diagnostic,
  compression-collision, and CLI usability work reached stabilization.
- Updated installer behavior accumulated during the 0.3.x fix series: source
  checkouts may contain multiple wheels, a current-version wheel is preferred,
  and the newest wheel is used as a warned fallback.
- Made release checksum evidence optional for installation when the checksum
  file or selected-wheel entry is absent. An explicitly supplied mismatching
  checksum still fails installation.
- Documented the `uv`-based Python workflow so installation does not depend on
  the operating system `python3` command satisfying the Python 3.11 minimum.
- Refreshed README and operations guidance to describe current config discovery,
  logging, systemd sandbox behavior, installer recovery gates, and routine CLI
  usage without historical version-specific sections.
- Replaced the release-specific verification record with an evergreen
  verification guide and removed the iteration-specific usability review from
  tracked product documentation.
- Removed third-party author attribution from package metadata and changed the
  package development-status classifier from Alpha to Beta.
- Updated package license metadata to the current SPDX string form and raised the
  setuptools build minimum accordingly.
- Added Ruff to development dependencies and recorded the project formatting
  target.
- Excluded generated wheels, root release checksum sidecars, Ruff caches, and
  temporary verification environments from source control.

## 0.3.1 — 2026-08-10

- Made text log string fields consistently JSON-quoted so paths containing
  whitespace remain visually bounded; expected failures now expose structured
  error type and detail fields.
- Added explicit source-root diagnostics for missing, inaccessible/sandboxed,
  symlinked, and non-directory paths.
- Fixed generated systemd hardening so configured paths under `/home`, `/root`,
  or `/run/user` are not hidden by `ProtectHome=true`.
- Made generated gzip/bzip2 output names collision-tolerant: fixed source paths
  remain unchanged while generated compressed names are deterministically
  disambiguated when necessary.
- Made `--config` optional for routine CLI use through `STREAM_ARCHIVER_CONFIG`,
  the XDG user policy location, or `/etc/stream-archiver/policies.toml`.
- Moved manual-run default lock/state locations under the user state directory
  instead of beside the policy file.
- Simplified first-run guidance: `plan` already validates configuration; a
  separate `check` is mainly useful for configuration-only validation.

## 0.3.0 — 2026-08-04

- Added structured text and JSON operational logging while keeping command JSON
  output isolated on stdout.
- Added INFO lifecycle and progress events for policy, source, archive, action,
  byte, cleanup, recovery, and verification work; added DEBUG planning and
  payload-verification detail.
- Added actionable expected-failure logs, traceback-preserving unexpected-failure
  logs, and explicit interruption handling.
- Added `render-systemd`, which generates service, timer, and installation
  instructions from the installed executable and validated policy paths.
- Replaced editable hardcoded unit examples with generated deployment artifacts.
- Added a stable-path guided installer that validates as the service user,
  verifies the bundled wheel checksum, rejects protected configuration before
  changing the system, and deliberately does not start work or enable the timer.
- Exposed service name, schedule, timer accuracy, randomized delay, and service
  log settings as installer options.
- Added operations guidance and behavioral tests for logging,
  item/byte/percentage progress, JSON events, systemd generation, path escaping,
  and overwrite protection.

## 0.2.0 — 2026-08-03

- Changed the default uncompressed action from `copy` to `move`; movement uses
  verified destination staging, atomic commit, source revalidation, and cleanup.
- Added `SUCCESS.json` and `SHA256SUMS.json` completion evidence and a `verify`
  command that recomputes payload, decompression, manifest, and evidence hashes.
- Added policy-level `sources` arrays. Each source is planned and archived
  independently and cannot share a final directory with another source. Paths
  are canonicalized before ownership checks to detect symlink aliases.
- Allowed policies to use the same exact destination while retaining source
  exclusivity and rejecting nested destinations.
- Added level-9 bzip2 compression alongside level-9 gzip.
- Added schema version 2 while retaining schema-1 and singular-source loading.
- Retained recovery support for version-1 archive manifests.
- Added behavioral tests for move actions, gzip and bzip2, multiple sources,
  shared destinations, success evidence, evidence tampering, corruption
  detection, and CLI verification.
- Updated systemd, configuration, design, operation, and migration examples.

## 0.1.0 — 2026-08-02

- Added declarative TOML policies with independent source, destination, age,
  stream-gap, symlink, and compression decisions.
- Added fixed gzip level-9 compression and rejection of other codecs or level
  overrides.
- Added deterministic stream grouping and full-stream age eligibility.
- Added staged archive commit, hashes, manifests, guarded source deletion, and
  interrupted-cleanup recovery.
- Added `check`, `plan`, `run`, and `run-if-due` commands.
- Added per-policy due state and non-blocking Linux process locking.
- Added pytest coverage for policy, filesystem, recovery, and CLI behavior.
- Added Ubuntu systemd service, timer, configuration, and installation examples.
