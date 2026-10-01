# Verification guide

This document defines repeatable checks for the repository. Release-specific
results, hashes, reviewer findings, machine paths, and delivery metadata stay in
external release evidence.

## Evidence surfaces

Do not collapse these surfaces into one pass/fail claim:

1. requirements/design traceability;
2. source-tree behavior;
3. critical regression sensitivity and fault injection;
4. compatibility/migration behavior;
5. installed-wheel behavior;
6. generated systemd/installer behavior;
7. static/lint/format checks; and
8. source-bundle and patch-replay verification.

A passing unit suite is not by itself evidence for crash durability or a complete
release delivery.

## Development environment

Preferred setup:

```bash
./tools/bootstrap-dev.sh --python-version 3.11
```

This runs `uv sync --frozen --extra dev`. Add `--offline` only when all required
artifacts are cached. Network/cache failure is an environment result, not a
product test failure.

The project declares Python 3.11, 3.12, and 3.13, and package metadata enforces
`>=3.11,<3.14`. Test every claimed version for a release when those interpreters are
available.

## Source verification

```bash
./tools/verify-local.sh
```

The script:

- compiles `src` and `tests`;
- runs pytest with the src-layout package explicitly on `PYTHONPATH`;
- runs Ruff format/lint when Ruff is installed;
- validates repository shell scripts with `bash -n`; and
- runs `git diff --check`.

A missing Ruff executable is reported as **unavailable**, not passed.

## Regression sensitivity and fault tests

Critical fixes need an oracle that would detect the named pre-fix defect. At
minimum retain tests for:

- corrupt pending archive preserves the remaining source;
- committed final-path verification failure blocks normal cleanup;
- alias cleanup follows a target across timestamp stream boundaries;
- artificial time cannot reach destructive CLI commands;
- required synchronization failure cannot produce completion;
- nested resource locks conflict while disjoint siblings can coexist;
- policy-scoped and destination-wide verification differ;
- malformed archive-shaped directories are not silently skipped;
- v1 due-state migration is conservatively due; and
- policy fingerprint changes when archive selection/representation changes;
- public destructive execution rejects artificial eligibility time and lock-bypass assertions;
- cleanup preserves unrelated/empty source directories;
- exact-path alias classification rejects external/different hardlink identities;
- schema-3 recursion and partitioning remain independent;
- forced systemd output regeneration does not follow output symlinks; and
- INFO logs retain archive/stream lifecycle while normal per-entry actions remain DEBUG;
- invocation progress establishes stable multi-policy totals and reports exact source/written byte counters;
- compressed written-payload accounting uses the final transformed object size;
- planning and execution capacity comparisons preserve exact bytes and remain advisory;
- repeated low-space warnings follow semantic state rather than elapsed time;
- unexpected-capacity diagnostics use neutral causal wording; and
- an `ENOSPC` after a sufficient advisory observation still fails through the normal execution path without false source cleanup;
- final run reporting matches mixed uncompressed/gzip/bzip2 execution evidence;
- zero-byte and compression-expansion cases preserve signed savings and null ratio/percentage semantics;
- extension × transformation machine data remains complete while console output is bounded;
- recovered prior archives remain separate from newly selected work;
- immediate `run`, `run --json`, and `run --markdown` render one result model;
- an existing `--report` path prevents destructive execution; and
- a post-commit report-export failure warns without converting completed archive transactions into failed archival work;
- archive-unit identity is stable for the same plan and differs for different deterministic unit indexes;
- schema 4 uses controlled configurable names with a mandatory stable suffix while schemas 1–3 retain legacy naming;
- fixed English month/weekday tokens and opt-in sub-second directives are locale-independent;
- unsafe/unknown template syntax and overlong rendered names are rejected before archive mutation;
- policy fingerprints change when archive naming policy changes;
- current-format destination entries with missing manifests are not silently ignored; and
- a current archive name whose stable suffix disagrees with the manifest plan identity is rejected;
- schema 5 deterministically subdivides one logical stream by source-byte and/or regular-file targets;
- a single oversized regular file remains indivisible and may exceed the byte target alone;
- changing subdivision limits changes logical/unit identity while unpartitioned 0.5.0a1 identity remains stable;
- alias cleanup follows the archive unit containing its target;
- manifest-format-3 unit identity/index/count are reconciled against the selected plan;
- interruption after an earlier unit commit but before a later unit commit leaves all logical-stream sources intact; and
- rerun after that interruption reuses deterministic unit identities and completes the group before cleanup;
- changing partition identity while such a group is incomplete stops before new mutation or cleanup;
- restart recovery of a complete pending archive-unit group validates every remaining selected source across the group before the first unlink, so a later-unit mismatch cannot partially clean an earlier unit;
- malformed current-format `plan_id` metadata fails through the typed recovery/verification error contract rather than leaking a raw identity-helper exception;
- maintained requirement identifiers remain unique and every requirement family remains routed by the design traceability table; and
- hierarchical lock conflict/compatibility regressions use deterministic independent-open-file-description evidence without process-startup, queue, import-mode, or teardown timing as the oracle.

Where practical, execute the discriminating regression against the exact prior
baseline and record that it fails in the expected way.

## Clean-sdist and installed-artifact verification

```bash
./tools/verify-local.sh --release
```

The release route first builds and unpacks the Python source distribution. It
requires the normative docs, changelog, examples, maintainer tools, complete test
support graph, lockfile, and manifest policy to be present in that artifact, and
requires repository-local `local/` state to be absent. It then runs the complete
pytest suite and generated-systemd check from the unpacked sdist.

The route builds the wheel **from that sdist**, installs it into an isolated target
directory, reruns the sdist-shipped tests with imports resolved from the installed
package, verifies generated systemd output again, and smoke-tests
`python -m stream_archiver --help`. Repository-only files cannot satisfy this gate.

## Installer and systemd verification

Always run:

```bash
bash -n tools/install-systemd.sh tools/bootstrap-dev.sh tools/verify-local.sh
```

Generate units from disposable existing source/destination paths, including a
path containing whitespace and a home-tree case. Then run `systemd-analyze
verify` on the generated service/timer when the tool is available.

The release local-verification route automates a disposable generated-unit
check through `tools/verify-systemd.py` when `systemd-analyze` is installed.

Also verify that `render-systemd --force` replaces an output symlink itself and does not overwrite its target, and that the installer fails when no wheel matches the exact project version.

Assert that current generated services:

- do not contain operational `ConditionPath...` gates;
- do not pass `--lock-file`;
- use `ProtectHome=read-only` with explicit `ReadWritePaths`; and
- do not start work or enable the timer during installation.

## Compatibility verification

Preserve executable compatibility evidence that:

- completed supported 0.3 and previous 0.4-alpha archives remain verifiable;
- pending supported 0.3 archives enter corrected verify-before-delete recovery;
- valid v1 scheduling state is readable and conservatively due;
- schema-1/2 configurations retain recursive/source-root historical defaults;
- schema-3 configurations expose the discovery/partition controls;
- schema-4 configurations add stable-suffix configurable archive names while historical names remain discoverable;
- schema-5 configurations add deterministic archive-unit subdivision without redefining the logical stream; and
- upgraded systemd deployments require regenerated units.

For the human plan surface, verify representative summary arithmetic and inspect line density as a usability signal. The approximate 40-line summary target and 1–10 second INFO-density preference are not correctness gates.

## Release packaging verification

Before handoff:

1. run `./tools/verify-local.sh --release` so the Python sdist, sdist-only test
   graph, wheel-from-sdist, installed package, CLI, and systemd surfaces are checked;
2. confirm current release identity across build/runtime/docs/lock/changelog and the
   generated sdist `PKG-INFO`;
3. build the complete source archive from the final repository tree, excluding
   VCS internals/caches/build products/release evidence and repository-local `local/`;
4. extract and inspect it in a clean directory;
5. generate a binary-capable full-index patch from the exact stated baseline;
6. replay that patch on a clean exact baseline;
7. compare replay and target source trees byte-for-byte and by relevant modes;
8. record unavailable gates and limitations explicitly;
9. write the delivery manifest; and
10. compute final SHA-256 values after every artifact is final.

## Claim limits

Coverage is an omission signal, not proof of safety. Fault injection verifies
specified intermediate-state behavior but does not simulate every real
power-loss/storage-controller failure. Self-review is not independent review.


## 0.5.0b2 review-closure checks

The maintained release suite must retain focused checks that:

- policy-scoped verification rejects malformed, missing, wrongly typed, unreadable,
  or symlinked ownership evidence while still skipping a valid foreign owner before
  payload verification;
- a subdivided logical stream reports full logical-stream bytes in
  `current_stream_source_bytes` for every archive-unit precheck and separately reports
  decreasing `remaining_source_bytes`; and
- `requires-python`, Python classifiers, README/verification support wording, and the
  installer all encode the same Python 3.11–3.13 support policy.
