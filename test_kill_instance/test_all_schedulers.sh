#!/bin/bash
# 测试三个调度器是否都有kill逻辑

echo "========================================"
echo "测试三个调度器的kill逻辑"
echo "========================================"

scheds=("gpfp_tie" "gpfp_tgf" "gpfp_btie")

for sched in "${scheds[@]}"; do
    echo ""
    echo "========================================"
    echo "测试调度器: $sched"
    echo "========================================"

    # 创建配置文件
    cat > test_kill_instance/config_${sched}.yml << EOF
cpu_islands:
  - name: test_island
    numcpus: 1
    kernel:
      scheduler: ${sched}
      task_placement: global
    volts: [1.0]
    freqs: [1000]
    base_freq: 1000
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 10.0
  max_energy: 100.0
  use_real_solar_data: false
  pv_efficiency: 0.18
  pv_area_m2: 0.0
  periodic_collection_interval_ms: 1
  scheduler_energy_model:
    base_power: 1.0
    workload_coefficients:
      bzip2: 1.0
    frequency_power_ratios:
      1000: 1.0

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi
    params:
      - workload: bzip2
        power_params: [0.00775587, 33.376, 1.54585, 9.53439e-10]
        speed_params: [0.0256054, 2.9809e+6, 0.602631, 8.13712e+9]
        energy_coefficient: 1.0
EOF

    # 运行仿真
    export LD_LIBRARY_PATH=./build/librtsim:$LD_LIBRARY_PATH
    timeout 5 ./build/rtsim/rtsim \
        test_kill_instance/config_${sched}.yml \
        test_kill_instance/taskset.yml \
        100 \
        -t test_kill_instance/traces/trace_${sched}.json \
        2>&1 | tail -20

    # 统计kill事件
    kill_count=$(cat test_kill_instance/traces/trace_${sched}.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
kill_count = sum(1 for e in data['events'] if e['event_type'] == 'kill')
print(kill_count)
" 2>/dev/null)

    echo "  ✅ ${sched} kill事件数: ${kill_count}"
done

echo ""
echo "========================================"
echo "测试完成"
echo "========================================"
