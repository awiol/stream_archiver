#!/usr/bin/env bash
# Install the bundled wheel and systemd examples. Review paths before use.
set -euo pipefail

PACKAGE_ROOT=${1:-/opt/stream-archiver-0.2.0}
WHEEL_GLOB=("$PACKAGE_ROOT"/dist/stream_archiver-*.whl)

if [[ ${#WHEEL_GLOB[@]} -ne 1 || ! -f ${WHEEL_GLOB[0]} ]]; then
    printf 'Expected exactly one bundled wheel under %s/dist\n' "$PACKAGE_ROOT" >&2
    exit 2
fi
WHEEL=${WHEEL_GLOB[0]}

sudo useradd \
    --system \
    --home-dir /nonexistent \
    --shell /usr/sbin/nologin \
    stream-archiver 2>/dev/null || true

# Install without network access. stream-archiver has no runtime dependencies.
sudo python3 -m venv "$PACKAGE_ROOT/.venv"
sudo "$PACKAGE_ROOT/.venv/bin/pip" install \
    --no-index \
    --no-deps \
    --force-reinstall \
    "$WHEEL"

sudo install -d -m 0750 -o root -g stream-archiver /etc/stream-archiver
sudo install -m 0640 -o root -g stream-archiver \
    "$PACKAGE_ROOT/examples/config/policies.toml" \
    /etc/stream-archiver/policies.toml

sudo install -m 0644 "$PACKAGE_ROOT/examples/systemd/stream-archiver.service" \
    /etc/systemd/system/stream-archiver.service
sudo install -m 0644 "$PACKAGE_ROOT/examples/systemd/stream-archiver.timer" \
    /etc/systemd/system/stream-archiver.timer

sudo "$PACKAGE_ROOT/.venv/bin/stream-archiver" \
    --config /etc/stream-archiver/policies.toml \
    check
sudo systemd-analyze verify \
    /etc/systemd/system/stream-archiver.service \
    /etc/systemd/system/stream-archiver.timer
sudo systemctl daemon-reload
sudo systemctl enable --now stream-archiver.timer
sudo systemctl list-timers stream-archiver.timer
