#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLED_ROOT="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies"

if command -v node >/dev/null 2>&1; then
  NODE_DIR="$(dirname "$(command -v node)")"
elif [[ -x "${BUNDLED_ROOT}/node/bin/node" ]]; then
  NODE_DIR="${BUNDLED_ROOT}/node/bin"
else
  echo "未找到 Node.js 运行时。" >&2
  exit 1
fi

if command -v pnpm >/dev/null 2>&1; then
  PNPM_BIN="$(command -v pnpm)"
elif [[ -x "${BUNDLED_ROOT}/bin/fallback/pnpm" ]]; then
  PNPM_BIN="${BUNDLED_ROOT}/bin/fallback/pnpm"
else
  echo "未找到 pnpm。" >&2
  exit 1
fi

export PATH="${NODE_DIR}:${PATH}"
cd "${ROOT_DIR}/frontend"
"${PNPM_BIN}" install
"${PNPM_BIN}" build

