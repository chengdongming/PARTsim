#!/usr/bin/env bash
set -euo pipefail
AUTODL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${PARTSIM_FORMAL_CONFIRM:-}" != "RUN_V9_3_FORMAL" ]]; then
  printf '%s\n' 'Refusing formal execution. Set PARTSIM_FORMAL_CONFIRM=RUN_V9_3_FORMAL after final configuration review.' >&2
  exit 2
fi
export PARTSIM_RUN_MODE=formal
for name in core1 core2 core3 core4 core5 ext1; do
  "$AUTODL_DIR/run_${name}.sh"
done
if "$AUTODL_DIR/run_ext2.sh"; then
  printf '%s\n' 'EXT-2 formal refusal was expected but did not occur.' >&2
  exit 2
else
  status=$?
  if [[ "$status" -ne 3 ]]; then
    exit "$status"
  fi
fi
"$AUTODL_DIR/run_ext4.sh"
"$AUTODL_DIR/verify_results.sh"
