#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
python3 "$AUTODL_DIR/verify_outputs.py" \
  --output-root "$PARTSIM_OUTPUT_ROOT" --profile "$PARTSIM_RUN_MODE"
