#!/usr/bin/env bash
# Install stream-archiver into a stable system path and generate units from a
# user-owned configuration file. Package-tracked examples are never edited.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  sudo ./tools/install-systemd.sh --config PATH [options]

Required:
  --config PATH              Valid policy file to install.

Options:
  --install-root PATH        Stable application root (default: /opt/stream-archiver).
  --config-destination PATH  Installed policy path
                             (default: /etc/stream-archiver/policies.toml).
  --service-name NAME        Unit base name (default: stream-archiver).
  --service-user NAME        Service account (default: stream-archiver).
  --service-group NAME       Service group (default: stream-archiver).
  --on-calendar VALUE        systemd calendar schedule (default: daily).
  --randomized-delay VALUE   Randomized delay (default: 6h).
  --accuracy VALUE           Timer accuracy (default: 1h).
  --service-log-level LEVEL  DEBUG, INFO, WARNING, ERROR, or CRITICAL
                             (default: INFO).
  --service-log-format NAME  text or json (default: text).
  --replace-config           Replace an existing installed policy file.
  --help                     Show this help.

The script installs and validates the package and units. It does not start the mover or enable the timer; those operations can move files and require a
reviewed plan and a successful manual service run.
EOF
}

CONFIG_SOURCE=
INSTALL_ROOT=/opt/stream-archiver
CONFIG_DESTINATION=/etc/stream-archiver/policies.toml
SERVICE_NAME=stream-archiver
SERVICE_USER=stream-archiver
SERVICE_GROUP=stream-archiver
ON_CALENDAR=daily
RANDOMIZED_DELAY=6h
ACCURACY=1h
SERVICE_LOG_LEVEL=INFO
SERVICE_LOG_FORMAT=text
REPLACE_CONFIG=0
PYTHON_VERSION=3.14

while (($#)); do
    case "$1" in
        --config)
            CONFIG_SOURCE=${2:?--config requires a path}
            shift 2
            ;;
        --install-root)
            INSTALL_ROOT=${2:?--install-root requires a path}
            shift 2
            ;;
        --config-destination)
            CONFIG_DESTINATION=${2:?--config-destination requires a path}
            shift 2
            ;;
        --service-name)
            SERVICE_NAME=${2:?--service-name requires a name}
            shift 2
            ;;
        --service-user)
            SERVICE_USER=${2:?--service-user requires a name}
            shift 2
            ;;
        --service-group)
            SERVICE_GROUP=${2:?--service-group requires a name}
            shift 2
            ;;
        --on-calendar)
            ON_CALENDAR=${2:?--on-calendar requires a value}
            shift 2
            ;;
        --randomized-delay)
            RANDOMIZED_DELAY=${2:?--randomized-delay requires a value}
            shift 2
            ;;
        --accuracy)
            ACCURACY=${2:?--accuracy requires a value}
            shift 2
            ;;
        --service-log-level)
            SERVICE_LOG_LEVEL=${2:?--service-log-level requires a value}
            shift 2
            ;;
        --service-log-format)
            SERVICE_LOG_FORMAT=${2:?--service-log-format requires a value}
            shift 2
            ;;
        --replace-config)
            REPLACE_CONFIG=1
            shift
            ;;
        --python-version)
            PYTHON_VERSION=${2:?--python-version requires a version}
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    printf 'Run this installer as root, for example with sudo.\n' >&2
    exit 2
fi
if [[ -z $CONFIG_SOURCE || ! -f $CONFIG_SOURCE ]]; then
    printf -- '--config must name an existing regular file.\n' >&2
    exit 2
fi
if [[ $CONFIG_SOURCE != /* || $INSTALL_ROOT != /* || $CONFIG_DESTINATION != /* ]]; then
    printf 'Configuration and installation paths must be absolute.\n' >&2
    exit 2
fi
if [[ -e $CONFIG_DESTINATION || -L $CONFIG_DESTINATION ]]; then
    if [[ $REPLACE_CONFIG -ne 1 ]]; then
        printf 'Refusing to replace %s without --replace-config.\n' \
            "$CONFIG_DESTINATION" >&2
        exit 2
    fi
fi

SCRIPT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PACKAGE_ROOT=$(cd -- "$SCRIPT_DIRECTORY/.." && pwd -P)
shopt -s nullglob
WHEELS=("$PACKAGE_ROOT"/dist/stream_archiver-*.whl)
shopt -u nullglob
if [[ ${#WHEELS[@]} -eq 0 ]]; then
    printf 'No stream-archiver wheel found under %s/dist.\n' \
        "$PACKAGE_ROOT" >&2
    exit 2
fi

PROJECT_VERSION=$(sed -n \
    '/^\[project\][[:space:]]*$/,/^\[/ {
        s/^[[:space:]]*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p
    }' \
    "$PACKAGE_ROOT/pyproject.toml" | head -n 1)

MATCHING_WHEELS=()
if [[ -n $PROJECT_VERSION ]]; then
    shopt -s nullglob
    MATCHING_WHEELS=(
        "$PACKAGE_ROOT"/dist/stream_archiver-"$PROJECT_VERSION"-*.whl
    )
    shopt -u nullglob
fi

if [[ ${#MATCHING_WHEELS[@]} -gt 0 ]]; then
    CANDIDATE_WHEELS=("${MATCHING_WHEELS[@]}")
else
    CANDIDATE_WHEELS=("${WHEELS[@]}")
    if [[ -n $PROJECT_VERSION ]]; then
        printf \
            'No wheel matches project version %s; selecting the newest available wheel.\n' \
            "$PROJECT_VERSION" >&2
    else
        printf \
            'Could not read the project version; selecting the newest available wheel.\n' \
            >&2
    fi
fi

WHEEL=${CANDIDATE_WHEELS[0]}
for CANDIDATE in "${CANDIDATE_WHEELS[@]:1}"; do
    if [[ $CANDIDATE -nt $WHEEL ]]; then
        WHEEL=$CANDIDATE
    fi
done
printf 'Selected wheel: %s\n' "$WHEEL"
if [[ -f $PACKAGE_ROOT/SHA256SUMS ]]; then
    WHEEL_RECORD=$(grep -F "  dist/$(basename -- "$WHEEL")" \
        "$PACKAGE_ROOT/SHA256SUMS" || true)
    if [[ -n $WHEEL_RECORD ]]; then
        (cd -- "$PACKAGE_ROOT" && printf '%s\n' "$WHEEL_RECORD" | sha256sum -c -)
    else
        printf \
            'Warning: SHA256SUMS does not contain the bundled wheel entry for %s; continuing without checksum verification.\n' \
            "$(basename -- "$WHEEL")" >&2
    fi
else
    printf \
        'Warning: %s/SHA256SUMS was not found; continuing without checksum verification.\n' \
        "$PACKAGE_ROOT" >&2
fi

if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_GROUP" --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -m 0755 -o root -g root "$INSTALL_ROOT"

UV_BIN=${UV_BIN:-$(command -v uv || true)}
PYTHON_VERSION=${PYTHON_VERSION:-3.11}

if [[ -z $UV_BIN || ! -x $UV_BIN ]]; then
    printf 'uv was not found. Set UV_BIN to the absolute path of uv.\n' >&2
    exit 2
fi

# Keep uv-managed Python outside /root so the systemd service account can
# execute the interpreter after installation.
UV_PYTHON_INSTALL_DIR="$INSTALL_ROOT/python" \
    "$UV_BIN" python install "$PYTHON_VERSION"

UV_PYTHON_INSTALL_DIR="$INSTALL_ROOT/python" \
    "$UV_BIN" venv \
        --clear \
        --python "$PYTHON_VERSION" \
        "$INSTALL_ROOT/venv"

"$UV_BIN" pip install \
    --python "$INSTALL_ROOT/venv/bin/python" \
    --no-index \
    --no-deps \
    --force-reinstall \
    "$WHEEL"

chmod -R a+rX "$INSTALL_ROOT/python"

install -d -m 0750 -o root -g "$SERVICE_GROUP" "$(dirname -- "$CONFIG_DESTINATION")"
install -m 0640 -o root -g "$SERVICE_GROUP" "$CONFIG_SOURCE" "$CONFIG_DESTINATION"

EXECUTABLE="$INSTALL_ROOT/venv/bin/stream-archiver"
runuser -u "$SERVICE_USER" -- "$EXECUTABLE" --config "$CONFIG_DESTINATION" check >/dev/null
runuser -u "$SERVICE_USER" -- "$EXECUTABLE" --config "$CONFIG_DESTINATION" plan >/dev/null

GENERATED=$(mktemp -d)
trap 'rm -rf -- "$GENERATED"' EXIT
"$EXECUTABLE" --config "$CONFIG_DESTINATION" render-systemd \
    --executable "$EXECUTABLE" \
    --output-directory "$GENERATED" \
    --service-name "$SERVICE_NAME" \
    --service-user "$SERVICE_USER" \
    --service-group "$SERVICE_GROUP" \
    --on-calendar "$ON_CALENDAR" \
    --randomized-delay "$RANDOMIZED_DELAY" \
    --accuracy "$ACCURACY" \
    --service-log-level "$SERVICE_LOG_LEVEL" \
    --service-log-format "$SERVICE_LOG_FORMAT"

SERVICE_UNIT="/etc/systemd/system/$SERVICE_NAME.service"
TIMER_UNIT="/etc/systemd/system/$SERVICE_NAME.timer"
install -m 0644 "$GENERATED/$SERVICE_NAME.service" "$SERVICE_UNIT"
install -m 0644 "$GENERATED/$SERVICE_NAME.timer" "$TIMER_UNIT"
systemd-analyze verify "$SERVICE_UNIT" "$TIMER_UNIT"
systemctl daemon-reload

cat <<EOF
Installation and validation completed.

No archival run was started and the timer was not enabled.

Review the plan again:
  sudo -u $SERVICE_USER $EXECUTABLE --config $CONFIG_DESTINATION plan

Run once and inspect logs:
  sudo systemctl start $SERVICE_NAME.service
  sudo systemctl status $SERVICE_NAME.service
  journalctl -u $SERVICE_NAME.service -n 200 --no-pager

After the manual run succeeds:
  sudo systemctl enable --now $SERVICE_NAME.timer
  systemctl list-timers $SERVICE_NAME.timer
EOF
