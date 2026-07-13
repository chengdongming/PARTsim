#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

venv="${PARTSIM_VENV:-$PROJECT_ROOT/.venv-autodl}"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install --requirement "$AUTODL_DIR/requirements.lock"
printf 'AutoDL Python environment ready: %s\n' "$venv"
