#!/bin/bash

# 测试时间点：0, 8, 10, 12, 14, 15, 17点
TIMES=(0 8 10 12 14 15 17)
TIME_MS=(0 28800000 36000000 43200000 50400000 54000000 61200000)

echo "=========================================="
echo "测试不同时间点的能量收集和消耗"
echo "=========================================="

for i in "${!TIMES[@]}"; do
    HOUR=${TIMES[$i]}
    MS=${TIME_MS[$i]}
    
    echo ""
    echo "=========================================="
    echo "测试时间: ${HOUR}:00"
    echo "=========================================="
    
    # 创建临时目录
    TEST_DIR="epp_test_${HOUR}h"
    mkdir -p $TEST_DIR
    
    # 复制任务集
    cp epp_test_0j_0ms/tasks.yml $TEST_DIR/
    
    # 创建配置文件
    cat > $TEST_DIR/config.yml << YAML
# EPP调度器测试 - ${HOUR}:00，初始能量0
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 2

    kernel:
      scheduler: gpfp_epp
      task_placement: global

      scheduler_params:
        - "strict_priority=true"
        - "enable_energy_recovery=true"

    volts: [0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, 1.08, 1.10, 1.12, 1.14]
    freqs: [7000, 7500, 8000, 8100, 8200, 8300, 8400, 8500, 9000, 9500, 10000, 10500]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 0.0
  max_energy: 1000.0

  day_of_year: 187
  time_of_day_ms: ${MS}  # ${HOUR}:00:00

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0

  unit_time: 50
  periodic_collection_interval_ms: 1
  enable_energy_recovery: true
  max_recovery_wait_time_ms: 10000

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi

    params:
      - workload: idle
        power_params: [0.00134845, 1.76307e-5, 124.535, 1.00399e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1
YAML

    # 运行仿真
    bash run_sim.sh -s $TEST_DIR/config.yml -t $TEST_DIR/tasks.yml -d 1000 -o $TEST_DIR/trace.json 2>&1 | grep -E "(收集能量|消耗能量|剩余能量|Deadline|仿真)" | tail -10
    
    # 提取能量统计
    if [ -f "$TEST_DIR/trace.json" ]; then
        echo "✓ 追踪文件已生成: $TEST_DIR/trace.json"
    fi
done

echo ""
echo "=========================================="
echo "所有测试完成"
echo "=========================================="
