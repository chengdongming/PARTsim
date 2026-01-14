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
      scheduler: truefifo
      task_placement: partitioned
    volts:
      [ 1 ]
    freqs:
      [ 1000 ]
    base_freq: 1000
    power_model: cpu_psm
    speed_model: cpu_psm
power_models:
  - name: cpu_psm
    type: table_based
    filename: /tmp/hw_psm.csv
EOF

cat > /tmp/hw_psm.csv <<EOF
model,freq,workload,power,speed
cpu_psm,1000,bzip2,0.504400,1.000000
cpu_psm,1000,idle,0.099480,1.000000
EOF

cat > /tmp/sw.yml <<EOF
taskset:
  - name: task0
    iat: 1000
    deadline: 1000
    startcpu: 0
    code:
      - fixed(10,bzip2)
      - lock(queue)
      - fixed(10,bzip2)
  - name: task1
    iat: 1000
    deadline: 1000
    startcpu: 0
    code:
      - fixed(10,bzip2)
      - unlock(queue)
  - name: task2
    iat: 1000
    deadline: 1000
    startcpu: 0
    code:
      - fixed(10,bzip2)
resources:
  - name: queue
    initial_state: locked
EOF

$rtsim -d -t trace.txt /tmp/hw.yml /tmp/sw.yml 200 >/dev/null

cat trace.txt | grep 'task1 ended' | head -1 | grep '\[Time:20\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task2 ended' | head -1 | grep '\[Time:30\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task0 ended' | head -1 | grep '\[Time:40\]' || (echo "Fail"; exit 1)

echo "Success"

)
