#!/bin/bash

(
    set -e

    PROJ_HOME_PATH=$(realpath "$(git rev-parse --show-toplevel)")
    rtsim="$PROJ_HOME_PATH/build/rtsim/rtsim"

cat > /tmp/hw.yml <<EOF
cpu_islands:
  - name: cpu
    numcpus: 1
    kernel:
      scheduler: edf
      task_placement: partitioned
    volts:
      [ 1 ]
    freqs:
      [ 1000 ]
    base_freq: 1000
    power_model: cpu_psm
    speed_model: cpu_psm
  - name: gpu
    numcpus: 1
    kernel:
      scheduler: truefifo
      task_placement: partitioned
    volts:
      [ 1 ]
    freqs:
      [ 1000 ]
    base_freq: 1000
    power_model: gpu_psm
    speed_model: gpu_psm
power_models:
  - name: cpu_psm
    type: table_based
    filename: /tmp/hw_psm.csv
  - name: gpu_psm
    type: table_based
    filename: /tmp/hw_psm.csv
EOF

cat > /tmp/hw_psm.csv <<EOF
model,freq,workload,power,speed
cpu_psm,1000,bzip2,0.504400,1.000000
cpu_psm,1000,idle,0.099480,1.000000
gpu_psm,1000,bzip2,0.504400,1.000000
gpu_psm,1000,idle,0.099480,1.000000
EOF

cat > /tmp/sw.yml <<EOF
taskset:
  - name: task0
    iat: 4
    startcpu: 0
    code:
      - fixed(1,bzip2)
      - unlock(task0_gpu_begin)
      - lock(task0_gpu_end)
      - fixed(1,bzip2)
  - name: task0_gpu
    iat: 4
    startcpu: 1
    code:
      - lock(task0_gpu_begin)
      - fixed(2,bzip2)
      - unlock(task0_gpu_end)
  - name: task1
    iat: 10
    startcpu: 0
    code:
      - fixed(1,bzip2)
      - unlock(task1_gpu_begin)
      - lock(task1_gpu_end)
      - fixed(1,bzip2)
  - name: task1_gpu
    iat: 10
    startcpu: 1
    code:
      - lock(task1_gpu_begin)
      - fixed(3,bzip2)
      - unlock(task1_gpu_end)
resources:
  - name: task0_gpu_begin
    initial_state: locked
  - name: task0_gpu_end
    initial_state: locked
  - name: task1_gpu_begin
    initial_state: locked
  - name: task1_gpu_end
    initial_state: locked
EOF

$rtsim -d -t trace.txt /tmp/hw.yml /tmp/sw.yml 10 >/dev/null

function failure() {
  echo "Failure"
  false
}

function success() {
  echo "Success"
}

cat trace.txt | grep 'task0_gpu ended' | head -1 | grep '\[Time:3\]' || failure
cat trace.txt | grep 'task0 ended' | head -1 | grep '\[Time:4\]' || failure

cat trace.txt | grep 'task1_gpu ended' | head -1 | grep '\[Time:6\]' || failure
cat trace.txt | grep 'task1 ended' | head -1 | grep '\[Time:7\]' || failure

cat trace.txt | grep 'task0 missed' | head -1 | grep '\[Time:8\]' || failure
cat trace.txt | grep 'task0_gpu ended' | head -2 | tail -1 | grep '\[Time:8\]' || failure
cat trace.txt | grep 'task0 ended' | head -2 | tail -1 | grep '\[Time:9\]' || failure

success
)
