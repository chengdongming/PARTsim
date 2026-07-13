#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
run_experiment ext4 scripts/run_v9_3_ext4.py configs/v9_3_ext4_smoke.yaml EXT4_FORMAL_CONFIG --max-tasksets 2
