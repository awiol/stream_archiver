#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

PYTHON_VERSION=${PYTHON_VERSION:-3.11}
UV_BIN=${UV_BIN:-$(command -v uv || true)}
OFFLINE=0

usage() {
  cat <<'EOF'
Usage: ./tools/bootstrap-dev.sh [--python-version VERSION] [--offline]

Create/update the project development environment from uv.lock and install the
dev extra. --offline requires every needed Python and package artifact to exist
in the local uv cache.
EOF
}

while (($#)); do
  case "$1" in
    --python-version)
      PYTHON_VERSION=${2:?--python-version requires a version}
      shift 2
      ;;
    --offline)
      OFFLINE=1
      shift
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

if [[ -z "$UV_BIN" ]]; then
  printf 'uv is required. Install uv or set UV_BIN to its absolute path.\n' >&2
  exit 2
fi

args=(sync --frozen --extra dev --python "$PYTHON_VERSION")
if ((OFFLINE)); then
  args+=(--offline)
fi
"$UV_BIN" "${args[@]}"
printf 'Development environment ready: %s/.venv\n' "$ROOT"
