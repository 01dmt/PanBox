#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then PYTHON=python3; fi
exec "${PYTHON}" app.py serve "$@"
