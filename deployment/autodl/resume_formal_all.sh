#!/usr/bin/env bash
set -euo pipefail
AUTODL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PARTSIM_ACTION=resume
exec "$AUTODL_DIR/run_formal_all.sh"
