# Systemd example workflow

Do not edit a package-tracked unit template. Generate deployment-specific files
from the installed executable and the policy at its intended final path:

```bash
stream-archiver \
  --config /etc/stream-archiver/policies.toml \
  render-systemd \
  --executable /opt/stream-archiver/venv/bin/stream-archiver \
  --output-directory ./generated-systemd
```

Review the generated service, timer, and `INSTALL.md`. The service sandbox paths
are derived from the validated policy, so adding a source or destination and
regenerating cannot silently leave a hardcoded permission list stale.

For a guided installation from this bundle, use
`tools/install-systemd.sh --config ABSOLUTE_PATH`. The installer does not start
archival work or enable the timer.
