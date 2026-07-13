#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment core4 scripts/run_v9_3_core4.py configs/v9_3_core4_smoke.yaml CORE4_FORMAL_CONFIG --max-cells 2 --max-tasksets 1
