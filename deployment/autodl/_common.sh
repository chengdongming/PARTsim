#!/usr/bin/env bash
set -euo pipefail

AUTODL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PARTSIM_ROOT:-$(git -C "$AUTODL_DIR" rev-parse --show-toplevel)}"
PARTSIM_OUTPUT_ROOT="${PARTSIM_OUTPUT_ROOT:-$PROJECT_ROOT/autodl_runs/v9_3}"
PARTSIM_LOG_ROOT="${PARTSIM_LOG_ROOT:-$PARTSIM_OUTPUT_ROOT/logs}"
PARTSIM_CONFIG_ROOT="${PARTSIM_CONFIG_ROOT:-$PARTSIM_OUTPUT_ROOT/configs}"
PARTSIM_WORKERS="${PARTSIM_WORKERS:-1}"
PARTSIM_BUILD_DIR="${PARTSIM_BUILD_DIR:-$PROJECT_ROOT/build}"
PARTSIM_SIMULATOR_BIN="${PARTSIM_SIMULATOR_BIN:-$PARTSIM_BUILD_DIR/rtsim/rtsim}"
PARTSIM_RUN_MODE="${PARTSIM_RUN_MODE:-smoke}"
PARTSIM_ACTION="${PARTSIM_ACTION:-run}"

export PROJECT_ROOT PARTSIM_OUTPUT_ROOT PARTSIM_LOG_ROOT PARTSIM_CONFIG_ROOT
export PARTSIM_WORKERS PARTSIM_BUILD_DIR PARTSIM_SIMULATOR_BIN
export PARTSIM_RUN_MODE PARTSIM_ACTION

if [[ -x "${PARTSIM_VENV:-$PROJECT_ROOT/.venv-autodl}/bin/python" ]]; then
  PATH="${PARTSIM_VENV:-$PROJECT_ROOT/.venv-autodl}/bin:$PATH"
  export PATH
fi

mkdir -p "$PARTSIM_OUTPUT_ROOT" "$PARTSIM_LOG_ROOT" "$PARTSIM_CONFIG_ROOT"

run_logged() {
  local name="$1"
  shift
  local stamp log_file
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  log_file="$PARTSIM_LOG_ROOT/${name}_${PARTSIM_ACTION}_${stamp}.log"
  "$@" 2>&1 | tee "$log_file"
}

resolve_path() {
  local value="$1"
  if [[ "$value" = /* ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$PROJECT_ROOT/$value"
  fi
}

run_experiment() {
  local name="$1"
  local runner="$2"
  local smoke_config="$3"
  local formal_variable="$4"
  shift 4
  local source_config profile authorization_config
  profile="$PARTSIM_RUN_MODE"
  if [[ "$PARTSIM_RUN_MODE" == "formal" ]]; then
    if [[ "$name" == "ext2" ]]; then
      printf '%s\n' "EXT-2 formal mode is disabled: REAL_TRACE_DATA_UNAVAILABLE" >&2
      return 3
    fi
    if [[ "$name" != "core1" && "$name" != "core2" ]]; then
      printf '%s\n' "$name formal mode is disabled until it has a file-bound authorization contract." >&2
      return 2
    fi
    source_config="${!formal_variable:-}"
    if [[ -z "$source_config" ]]; then
      printf 'Set %s to an audited formal configuration.\n' "$formal_variable" >&2
      return 2
    fi
    local authorization_variable
    authorization_variable="${formal_variable%_CONFIG}_AUTHORIZATION"
    authorization_config="${!authorization_variable:-}"
    if [[ -z "$authorization_config" ]]; then
      printf 'Set %s to the exact formal authorization file.\n' "$authorization_variable" >&2
      return 2
    fi
    authorization_config="$(resolve_path "$authorization_config")"
  elif [[ "$PARTSIM_RUN_MODE" == "smoke" ]]; then
    source_config="$smoke_config"
  else
    printf 'Unknown PARTSIM_RUN_MODE: %s\n' "$PARTSIM_RUN_MODE" >&2
    return 2
  fi
  source_config="$(resolve_path "$source_config")"
  local run_root store_root prepared
  run_root="$PARTSIM_OUTPUT_ROOT/$name"
  if [[ "$name" == "core1" || "$name" == "core2" ]]; then
    store_root="$PARTSIM_OUTPUT_ROOT/taskset_store_core12"
  else
    store_root="$PARTSIM_OUTPUT_ROOT/taskset_stores/$name"
  fi
  prepared="$PARTSIM_CONFIG_ROOT/${name}_${PARTSIM_RUN_MODE}.yaml"
  python3 "$AUTODL_DIR/prepare_config.py" \
    --source "$source_config" --destination "$prepared" \
    --output-root "$run_root" --taskset-store "$store_root" \
    --worker-count "$PARTSIM_WORKERS" --simulator-bin "$PARTSIM_SIMULATOR_BIN" \
    --profile "$profile"
  local resume_args=()
  if [[ "$PARTSIM_ACTION" == "resume" ]]; then
    resume_args=(--resume)
  elif [[ "$PARTSIM_ACTION" != "run" ]]; then
    printf 'Unknown PARTSIM_ACTION: %s\n' "$PARTSIM_ACTION" >&2
    return 2
  fi
  local bounded_args=("$@")
  if [[ "$PARTSIM_RUN_MODE" == "formal" ]]; then
    bounded_args=()
  fi
  local authorization_args=()
  if [[ -n "${authorization_config:-}" ]]; then
    authorization_args=(
      --formal-authorization "$authorization_config"
      --source-freeze-config "$source_config"
    )
  fi
  run_logged "$name" python3 "$PROJECT_ROOT/$runner" \
    --config "$prepared" "${authorization_args[@]}" \
    "${resume_args[@]}" "${bounded_args[@]}"
}
