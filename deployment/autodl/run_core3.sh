#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment core3 scripts/run_v9_3_core3.py configs/v9_3_core3_smoke.yaml CORE3_FORMAL_CONFIG
