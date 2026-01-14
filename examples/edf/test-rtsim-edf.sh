#!/bin/bash

cat > /tmp/hw.yml <<EOF
cpu_islands:
  - name: island0
    numcpus: 1
    kernel:
      scheduler: edf
      task_placement: partitioned
    volts:
      [1.000000]
    freqs:
      [1000]
    base_freq: 1000
    power_model: island0_psm
    speed_model: island0_psm

power_models:
  - name: island0_psm
    type: table_based
    filename: /tmp/hw_psm.csv
EOF

cat > /tmp/hw_psm.csv <<EOF
model,freq,workload,power,speed
island0_psm,1000,bzip2,0.504400,1.000000
island0_psm,1000,idle,0.099480,1.000000
EOF

cat > /tmp/sw.yml <<EOF
taskset:
  - name: task_0_0
    iat: 40
    deadline: 40
    startcpu: 0
    code:
      - fixed(10,bzip2)
  - name: task_0_1
    iat: 100
    deadline: 100
    startcpu: 0
    code:
      - fixed(60,bzip2)
EOF

rtsim -d -t trace.txt /tmp/hw.yml /tmp/sw.yml 200

cat trace.txt | grep 'task_0_0 ended' | head -1 | grep '\[Time:10\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task_0_0 ended' | head -2 | tail -1 | grep '\[Time:50\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task_0_0 ended' | head -3 | tail -1 | grep '\[Time:90\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task_0_0 ended' | head -4 | tail -1 | grep '\[Time:130\]' || (echo "Fail"; exit 1)

# 5th instance of task_0_0 and 2nd of task_0_1 have the same deadline of 200
cat trace.txt | grep 'task_0_0 ended' | head -5 | tail -1 | grep '\[Time:1[78]0\]' || (echo "Fail"; exit 1)

cat trace.txt | grep 'task_0_1 ended' | head -1 | grep '\[Time:80\]' || (echo "Fail"; exit 1)
cat trace.txt | grep 'task_0_1 ended' | head -2 | tail -1 | grep '\[Time:1[78]0\]' || (echo "Fail"; exit 1)
