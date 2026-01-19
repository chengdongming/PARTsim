#!/bin/bash

# 测试不同时间点的能量收集（2000ms）

echo "================================================================================"
echo "能量收集测试 - 不同时间点（2000ms）"
echo "================================================================================"

# 定义测试时间点
hours="0 8 10 12 14 15 17"

# 为每个时间点创建配置并运行测试
for hour in $hours; do
    echo ""
    echo "--------------------------------------------------------------------------------"
    echo "测试时间: ${hour}:00"
    echo "--------------------------------------------------------------------------------"
    
    # 计算time_of_day_ms
    time_of_day_ms=$((hour * 3600 * 1000))
    
    # 创建配置文件
    cat > tie_energy_test/test_energy_${hour}h_2000ms.yml << YAMLCONFIG
# 能量测试 - ${hour}:00 (2000ms)
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 1
    kernel:
      scheduler: gpfp_tie
      task_placement: global
    volts: [1.00]
    freqs: [8100]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 0.0
  max_energy: 10000.0
  day_of_year: 1
  time_of_day_ms: ${time_of_day_ms}
  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0
  unit_time: 1
  periodic_collection_interval_ms: 1
  enable_energy_recovery: true
  scheduler_energy_model:
    base_power: 0.5
    workload_coefficients:
      bzip2: 1.2
      hash: 0.8
      control: 0.1
      idle: 0.1

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi
    params:
      - workload: idle
        power_params: [0.00134845, 1.76307e-5, 124.535, 1.00399e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1
YAMLCONFIG

    # 创建任务文件（长周期任务，避免调度）
    cat > tie_energy_test/test_energy_${hour}h_tasks_2000ms.yml << YAMLTASKS
taskset:
  - name: idle_task
    iat: 100000
    runtime: 1
    deadline: 100000
    params: "period=100000,wcet=1,arrival_offset=0,workload=idle"
    code:
      - fixed(1, idle)
YAMLTASKS

    # 运行仿真（2000ms）
    ./build/rtsim/rtsim \
        "tie_energy_test/test_energy_${hour}h_2000ms.yml" \
        "tie_energy_test/test_energy_${hour}h_tasks_2000ms.yml" \
        "2000" 2>&1 | \
        grep "剩余能量:" | \
        tail -1
done

echo ""
echo "================================================================================"
echo "测试完成"
echo "================================================================================"
