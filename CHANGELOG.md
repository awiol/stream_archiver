# Changelog

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
