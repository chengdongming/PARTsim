#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

cmake -S "$PROJECT_ROOT" -B "$PARTSIM_BUILD_DIR" \
  -DCMAKE_BUILD_TYPE="${PARTSIM_BUILD_TYPE:-Release}" -DBUILD_TESTING=OFF
cmake --build "$PARTSIM_BUILD_DIR" --parallel "${PARTSIM_BUILD_JOBS:-$PARTSIM_WORKERS}"
test -x "$PARTSIM_SIMULATOR_BIN"
printf 'Simulator ready: %s\n' "$PARTSIM_SIMULATOR_BIN"
