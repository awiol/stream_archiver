# Changelog

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
