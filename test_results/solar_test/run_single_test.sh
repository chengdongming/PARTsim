#!/bin/bash
algo=$1
hour=$2

./build/rtsim/rtsim \
  test_results/solar_test/config_${algo}_${hour}h.yml \
  test_results/preemption_test/tasks_preemption_v3.yml \
  100 \
  -t test_results/solar_test/${hour}h/traces/${algo}_trace.json 2>&1 | \
  grep -E "Tick总次数:|任务完成数:|总消耗能量:|总收集能量:|剩余能量:"
