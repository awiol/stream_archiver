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
--release:         additionally build a wheel, install it outside the checkout,
                  run the test suite against that installed package, and smoke
                  test the installed CLI.

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
  rm -rf .verification-dist .verification-install .verification-tests
}
trap cleanup_release_workspace EXIT

rm -rf .verification-dist .verification-install .verification-tests
mkdir -p \
  .verification-dist \
  .verification-install \
  .verification-tests/tests \
  .verification-tests/tools
"$PYTHON" -m pip wheel . --no-deps --no-build-isolation --wheel-dir .verification-dist
wheel=$(
  find .verification-dist -maxdepth 1 -type f -name 'stream_archiver-*.whl' -print \
    | sort \
    | tail -n 1
)
if [[ -z "$wheel" ]]; then
  printf 'No stream_archiver wheel was built.\n' >&2
  exit 2
fi
"$PYTHON" -m pip install --no-deps --no-compile --target .verification-install "$wheel"
cp -a tests/. .verification-tests/tests/
cp pyproject.toml .verification-tests/pyproject.toml
cp tools/install-systemd.sh .verification-tests/tools/install-systemd.sh
(
  cd .verification-tests
  PYTHONPATH="$ROOT/.verification-install" "$PYTHON" -m pytest -q tests
)
PYTHONPATH="$ROOT/.verification-install" "$PYTHON" -m stream_archiver --help >/dev/null
PYTHONPATH=src "$PYTHON" tools/verify-systemd.py
printf 'Installed-artifact verification passed: %s\n' "$wheel"
