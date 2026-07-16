#!/usr/bin/env bash
set -euo pipefail
AUTODL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PARTSIM_FORMAL_CONFIRM:-}" ]]; then
  printf '%s\n' 'Refusing legacy environment-token authorization; use per-core authorization files.' >&2
  exit 2
fi
if [[ -z "${CORE1_FORMAL_AUTHORIZATION:-}" || -z "${CORE2_FORMAL_AUTHORIZATION:-}" ]]; then
  printf '%s\n' 'CORE1_FORMAL_AUTHORIZATION and CORE2_FORMAL_AUTHORIZATION are required.' >&2
  exit 2
fi
export PARTSIM_RUN_MODE=formal
for name in core1 core2; do
  "$AUTODL_DIR/run_${name}.sh"
done
"$AUTODL_DIR/verify_results.sh"
