#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLED_ROOT="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies"

if command -v node >/dev/null 2>&1; then
  NODE_DIR="$(dirname "$(command -v node)")"
else
  NODE_DIR="${BUNDLED_ROOT}/node/bin"
fi

if command -v pnpm >/dev/null 2>&1; then
  PNPM_BIN="$(command -v pnpm)"
else
  PNPM_BIN="${BUNDLED_ROOT}/bin/fallback/pnpm"
fi

export PATH="${NODE_DIR}:${PATH}"

cd "${ROOT_DIR}"
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then PYTHON=python3; fi
"${PYTHON}" app.py serve --port 8088 &
BACKEND_PID=$!
trap 'kill ${BACKEND_PID} 2>/dev/null || true' EXIT INT TERM

cd "${ROOT_DIR}/frontend"
exec "${PNPM_BIN}" dev
