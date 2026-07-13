#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment core2 scripts/run_v9_3_core2.py configs/v9_3_core2_smoke.yaml CORE2_FORMAL_CONFIG --max-cells 1 --max-tasksets 1
