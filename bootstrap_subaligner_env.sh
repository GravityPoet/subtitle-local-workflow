#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv-subaligner"
READY_MARKER="${SCRIPT_DIR}/.subaligner-ready"

if ! command -v uv >/dev/null 2>&1; then
  echo "[error] uv is required but not found on PATH" >&2
  exit 1
fi

rm -f "${READY_MARKER}"
uv venv "${VENV_DIR}" --python 3.11
uv pip install --python "${VENV_DIR}/bin/python" --upgrade pip setuptools wheel
uv pip install --python "${VENV_DIR}/bin/python" numpy torch webrtcvad
"${VENV_DIR}/bin/python" -m pip install --no-build-isolation "subaligner[stretch]"
touch "${READY_MARKER}"

echo "[done] subaligner env ready: ${VENV_DIR}"
echo "       python=${VENV_DIR}/bin/python"
