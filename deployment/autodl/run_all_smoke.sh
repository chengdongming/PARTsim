#!/usr/bin/env bash
set -euo pipefail
AUTODL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PARTSIM_RUN_MODE=smoke
for name in core1 core2 core3 core4 core5 ext1 ext2 ext4; do
  "$AUTODL_DIR/run_${name}.sh"
done
"$AUTODL_DIR/verify_results.sh"
