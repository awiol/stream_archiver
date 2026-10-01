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

The project declares Python 3.11, 3.12, and 3.13. Test every claimed version for
a release when those interpreters are available.

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
- rerun after that interruption reuses deterministic unit identities and completes the group before cleanup; and
- changing partition identity while such a group is incomplete stops before new mutation or cleanup.

Where practical, execute the discriminating regression against the exact prior
baseline and record that it fails in the expected way.

## Installed-artifact verification

```bash
./tools/verify-local.sh --release
```

The release route builds a wheel without dependency resolution, installs it into
an isolated target directory, copies the tests outside the source checkout, runs
them with imports resolved from the installed package, and smoke-tests
`python -m stream_archiver --help`.

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

Assert that generated 0.4 services:

- do not contain operational `ConditionPath...` gates;
- do not pass `--lock-file`;
- use `ProtectHome=read-only` with explicit `ReadWritePaths`; and
- do not start work or enable the timer during installation.

## Compatibility verification

For the 0.4 line, preserve executable evidence that:

- completed supported 0.3 and previous 0.4-alpha archives remain verifiable;
- pending supported 0.3 archives enter corrected verify-before-delete recovery;
- valid v1 scheduling state is readable and conservatively due;
- schema-1/2 configurations retain recursive/source-root historical defaults;
- schema-3 configurations expose the new discovery/partition controls; and
- upgraded systemd deployments require regenerated units.

For the human plan surface, verify representative summary arithmetic and inspect line density as a usability signal. The approximate 40-line summary target and 1–10 second INFO-density preference are not correctness gates.

## Release packaging verification

Before handoff:

1. build the complete source archive from the final repository tree, excluding
   VCS internals/caches/build products/release evidence;
2. extract and inspect it in a clean directory;
3. generate a binary-capable full-index patch from the exact stated baseline;
4. replay that patch on a clean exact baseline;
5. compare replay and target source trees byte-for-byte and by relevant modes;
6. run source and installed-package checks on the final target;
7. record unavailable gates and limitations explicitly;
8. write the delivery manifest; and
9. compute final SHA-256 values after every artifact is final.

## Claim limits

Coverage is an omission signal, not proof of safety. Fault injection verifies
specified intermediate-state behavior but does not simulate every real
power-loss/storage-controller failure. Self-review is not independent review.
