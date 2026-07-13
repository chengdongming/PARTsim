#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment ext1 scripts/run_v9_3_ext1.py configs/v9_3_ext1_smoke.yaml EXT1_FORMAL_CONFIG --max-cells 1 --max-tasksets 1
