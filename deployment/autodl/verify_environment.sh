#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

min_mem_gib="${PARTSIM_MIN_MEMORY_GIB:-2}"
min_disk_gib="${PARTSIM_MIN_DISK_GIB:-5}"
cpu_count="$(getconf _NPROCESSORS_ONLN)"
memory_kib="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
disk_kib="$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
if (( memory_kib < min_mem_gib * 1024 * 1024 )); then
  printf 'Insufficient memory: %s KiB\n' "$memory_kib" >&2
  exit 2
fi
if (( disk_kib < min_disk_gib * 1024 * 1024 )); then
  printf 'Insufficient free disk: %s KiB\n' "$disk_kib" >&2
  exit 2
fi
python3 --version
cmake --version | head -1
c++ --version | head -1
printf 'CPU cores: %s; memory KiB: %s; free disk KiB: %s\n' "$cpu_count" "$memory_kib" "$disk_kib"
python3 -c 'import yaml; import experiments.v9_3; print("formal imports: OK")'
python3 -m compileall -q \
  "$PROJECT_ROOT/experiments/v9_3" \
  "$PROJECT_ROOT/asap_block_rta_v9_3.py" \
  "$PROJECT_ROOT/asap_block_rta_v9_3_taskset.py" \
  "$PROJECT_ROOT/asap_block_v9_3_runner.py" \
  "$PROJECT_ROOT/global_task_generator.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_core1.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_core2.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_core3.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_core4.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_core5.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_ext1.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_ext2.py" \
  "$PROJECT_ROOT/scripts/run_v9_3_ext4.py"
printf 'Environment verification passed. Legacy tools/about.py and tools/taskset_generator/taskgen.py were intentionally excluded.\n'
