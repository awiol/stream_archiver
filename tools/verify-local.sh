#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

MODE=source
PYTHON=${PYTHON:-}

usage() {
  cat <<'EOF'
Usage: ./tools/verify-local.sh [--release] [--python PATH]

source (default): compile, test the src-layout checkout, run Ruff when present,
                  check shell syntax and Git whitespace.
--release:         additionally build and unpack the sdist, run the complete
                  suite from that artifact, build/install its wheel, rerun the
                  suite against the installed package, and smoke-test the CLI.

The script does not download dependencies. Use tools/bootstrap-dev.sh first when
the required test/lint tools are not already available.
EOF
}

while (($#)); do
  case "$1" in
    --release)
      MODE=release
      shift
      ;;
    --python)
      PYTHON=${2:?--python requires a path}
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

if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
  else
    PYTHON=$(command -v python3)
  fi
fi

printf 'verify-local mode=%s python=%s\n' "$MODE" "$PYTHON"
"$PYTHON" -m compileall -q src tests tools
PYTHONPATH=.:src "$PYTHON" -m pytest -q

RUFF=
if [[ -x .venv/bin/ruff ]]; then
  RUFF=.venv/bin/ruff
elif command -v ruff >/dev/null 2>&1; then
  RUFF=$(command -v ruff)
fi
if [[ -n "$RUFF" ]]; then
  "$RUFF" format --check .
  "$RUFF" check .
else
  printf 'NOTICE: Ruff unavailable; lint/format verification not executed.\n' >&2
fi

bash -n tools/install-systemd.sh tools/bootstrap-dev.sh tools/verify-local.sh
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check
else
  printf 'NOTICE: no Git worktree; git diff --check not executed.\n' >&2
fi

if [[ "$MODE" != release ]]; then
  exit 0
fi

cleanup_release_workspace() {
  rm -rf .verification-dist .verification-install .verification-sdist
}
trap cleanup_release_workspace EXIT

UV_BIN=${UV_BIN:-$(command -v uv || true)}
if [[ -z "$UV_BIN" ]]; then
  printf 'uv is required for the clean-sdist release gate. Set UV_BIN or install uv.\n' >&2
  exit 2
fi

rm -rf .verification-dist .verification-install .verification-sdist
mkdir -p .verification-dist .verification-install .verification-sdist

# Build the supported source-user artifact without resolving/downloading build
# dependencies. The active verification environment must already contain the
# declared build backend.
"$UV_BIN" build \
  --sdist \
  --no-build-isolation \
  --offline \
  --out-dir .verification-dist \
  .
sdist=$(
  find .verification-dist -maxdepth 1 -type f -name 'stream_archiver-*.tar.gz' -print \
    | sort \
    | tail -n 1
)
if [[ -z "$sdist" ]]; then
  printf 'No stream_archiver source distribution was built.\n' >&2
  exit 2
fi

tar -xzf "$sdist" -C .verification-sdist
sdist_root=$(
  find .verification-sdist -mindepth 1 -maxdepth 1 -type d -name 'stream_archiver-*' -print \
    | sort \
    | tail -n 1
)
if [[ -z "$sdist_root" ]]; then
  printf 'Could not locate extracted stream_archiver sdist root.\n' >&2
  exit 2
fi

required_sdist_paths=(
  CHANGELOG.md
  uv.lock
  MANIFEST.in
  docs/requirements.md
  docs/design.md
  docs/verification.md
  docs/operations.md
  docs/user-guide.md
  examples/config/policies.toml
  tools/bootstrap-dev.sh
  tools/install-systemd.sh
  tools/verify-local.sh
  tools/verify-systemd.py
  tests/__init__.py
  tests/helpers.py
)
for relative in "${required_sdist_paths[@]}"; do
  if [[ ! -f "$sdist_root/$relative" ]]; then
    printf 'Required source-distribution path is missing: %s\n' "$relative" >&2
    exit 2
  fi
done
if [[ -e "$sdist_root/local" || -L "$sdist_root/local" ]]; then
  printf 'Repository-local local/ unexpectedly entered the source distribution.\n' >&2
  exit 2
fi

# Run the official test configuration using only files shipped by the sdist.
(
  cd "$sdist_root"
  PYTHONPATH=.:src "$PYTHON" -m pytest -q
  bash -n tools/install-systemd.sh tools/bootstrap-dev.sh tools/verify-local.sh
  PYTHONPATH=src "$PYTHON" tools/verify-systemd.py
)

# Build the runtime artifact from the sdist, not from the repository checkout.
"$PYTHON" -m pip wheel \
  "$sdist" \
  --no-deps \
  --no-build-isolation \
  --wheel-dir .verification-dist
wheel=$(
  find .verification-dist -maxdepth 1 -type f -name 'stream_archiver-*.whl' -print \
    | sort \
    | tail -n 1
)
if [[ -z "$wheel" ]]; then
  printf 'No stream_archiver wheel was built from the source distribution.\n' >&2
  exit 2
fi
"$PYTHON" -m pip install --no-deps --no-compile --target .verification-install "$wheel"

# The tests and their source-user fixtures come from the extracted sdist while
# imports resolve only from the installed wheel target.
(
  cd "$sdist_root"
  PYTHONPATH="$ROOT/.verification-install" "$PYTHON" -m pytest -q tests
  PYTHONPATH="$ROOT/.verification-install" "$PYTHON" tools/verify-systemd.py
)
PYTHONPATH="$ROOT/.verification-install" "$PYTHON" -m stream_archiver --help >/dev/null
printf 'Clean-sdist and installed-artifact verification passed: %s -> %s\n' "$sdist" "$wheel"
