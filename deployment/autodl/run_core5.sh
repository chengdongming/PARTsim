#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment core5 scripts/run_v9_3_core5.py configs/v9_3_core5_smoke.yaml CORE5_FORMAL_CONFIG --max-cells 2 --max-tasksets 1
