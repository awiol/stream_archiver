# Operations guide

## Pre-deployment checks

1. Ensure the destination has free space for every complete eligible stream.
   Safe movement temporarily retains both source and destination copies.
2. Give the service account read, traversal, and deletion permissions on every
   source and create/write permissions on each destination.
3. Ensure source roots do not overlap one another or any destination.
4. Equal destination roots are permitted; nested destination roots are not.
5. Validate configuration and inspect the read-only plan:

```bash
/opt/stream-archiver-0.2.0/.venv/bin/stream-archiver \
  --config /etc/stream-archiver/policies.toml check

/opt/stream-archiver-0.2.0/.venv/bin/stream-archiver \
  --config /etc/stream-archiver/policies.toml plan
```

6. Run and verify manually before enabling the timer:

```bash
sudo systemctl start stream-archiver.service
sudo systemctl status stream-archiver.service
journalctl -u stream-archiver.service --since today

/opt/stream-archiver-0.2.0/.venv/bin/stream-archiver \
  --config /etc/stream-archiver/policies.toml verify
```

## Completion criteria

A version-2 archive is complete only when all conditions hold:

- `MANIFEST.json` has `cleanup_complete: true`;
- every regular payload matches its recorded archive SHA-256;
- every compressed payload decompresses to its source SHA-256;
- `SHA256SUMS.json` matches the manifest and its recorded hash; and
- `SUCCESS.json` has status `completed` and references the current final
  manifest and checksum-index hashes.

The `verify` command checks these conditions without changing files.

## Diagnosing failures

Exit status 2 means configuration, planning, locking, execution, recovery, or
verification failed. Common causes include:

- source path missing or inaccessible;
- source roots overlap;
- a source overlaps a destination;
- a generated `.gz` or `.bz2` name collides with another payload path;
- a payload collides with a reserved evidence filename;
- source identity or content changed between planning and cleanup;
- a pending cleanup contains a changed source path;
- a payload, checksum index, manifest, or success marker was corrupted;
- insufficient destination space or permissions; or
- another invocation owns the process lock.

A committed archive with `cleanup_complete: false` is not complete and should
not have a valid `SUCCESS.json`. Correct the source or permission problem and
run the policy again. Recovery validates remaining sources before deletion.

If the source was intentionally replaced after a pending commit, do not edit the
manifest to force deletion. Inspect the committed archive, preserve both copies,
and resolve the conflict manually.

## Shared destination operation

Several policies can share one destination. Archive directory hashes include
policy and source ownership, so equal time ranges do not collide. Recovery scans
that destination and acts only on manifests for the current source root.
Verification is destination-scoped and therefore checks all archives in a
selected shared destination.

The systemd service must list every source and destination under
`ReadWritePaths`, including all sources in a policy's `sources` array.

## Restore

The tool does not implement automatic restore. A completed manifest records
original relative paths, modes, mtimes, actions, link text, hashes, and codec.

- Restore `move` payloads directly.
- Decompress `.gz` and `.bz2` payloads to the recorded source path without the
  appended archive suffix.
- Recreate preserved symlinks from `link_target`.
- Verify hashes before placing restored files into use.

Test restore procedures on non-production data. This mover is not a substitute
for an independent backup with tested recovery objectives.
