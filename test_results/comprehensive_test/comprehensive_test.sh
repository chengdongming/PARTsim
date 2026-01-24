#!/bin/bash

echo "=========================================="
echo "  全面测试 TIE/TGF/BTIE - Bug��查"
echo "=========================================="
echo ""

RESULTS_DIR="/home/devcontainers/PARTSim-project/test_results/comprehensive_test/results"
mkdir -p "$RESULTS_DIR"

# 定义测试场景
cat > /tmp/scenario1.yml << 'EOF'
cpu_islands:
  - name: cpus
    numcpus: 2
    kernel:
      scheduler: gpfp_tie
      task_placement: global
    volts: [1.00]
    freqs: [8100]
    base_freq: 8100
    power_model: model
    speed_model: model

energy_management:
  initial_energy: 0.0015
  scheduler_energy_model:
    base_power: 0.5
    workload_coefficients:
      w1: 1.0
      w2: 0.3
    frequency_power_ratios:
      8100: 1.0

power_models:
  - name: model
    type: balsini_pannocchi
    params:
      - workload: w1
        power_params: [0, 0, 500, 0]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 1.0
      - workload: w2
        power_params: [0, 0, 500, 0]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.3

taskset:
  - name: high_priority
    iat: 100
    runtime: 10
    deadline: 20
    params: "period=100,wcet=10,workload=w1"
    code:
      - fixed(10, w1)
  - name: low_priority
    iat: 100
    runtime: 10
    deadline: 40
    params: "period=100,wcet=10,workload=w2"
    code:
      - fixed(10, w2)
EOF

# 测试三种算法
for ALGO in gpfp_tie gpfp_tgf gpfp_btie; do
    echo "==================== $ALGO ===================="

    # 创建系统配置
    SYSTEM_FILE="/tmp/system_${ALGO}.yml"
    sed "s/scheduler: .*/scheduler: $ALGO/" /tmp/scenario1.yml > "$SYSTEM_FILE"

    # 运行测试
    LOG_FILE="$RESULTS_DIR/${ALGO}_scenario1.log"
    /home/devcontainers/PARTSim-project/build/rtsim/rtsim "$SYSTEM_FILE" /tmp/scenario1.yml 10 > "$LOG_FILE" 2>&1

    # 提取关键统计
    echo "--- 统计信息 ---"
    grep -E "(任务完成数|总消耗能量|剩余能量|Deadline Miss)" "$LOG_FILE" | tail -10

    # 检查是否有错误
    if grep -qi "error\|fatal\|segmentation\|assertion" "$LOG_FILE"; then
        echo "⚠️ 发现错误！"
        grep -i "error\|fatal\|segmentation\|assertion" "$LOG_FILE" | head -5
    fi

    # 检查是否有重复调度
    echo "--- 检查重复调度 ---"
    DUPLICATES=$(grep "scheduled" "$LOG_FILE" | grep "high_priority" | wc -l)
    if [ $DUPLICATES -gt 2 ]; then
        echo "⚠️ 可能的重复调度：high_priority被调度$DUPLICATES次"
    fi

    # 检查能量透支
    echo "--- 检查能量会计 ---"
    INITIAL_ENERGY=$(grep "初始能量:" "$LOG_FILE" | head -1 | grep -oP '\d+\.\d+(?=\s*J)')
    FINAL_ENERGY=$(grep "剩余能量:" "$LOG_FILE" | grep -oP '\d+\.\d+(?=\s*J)' | tail -1)
    TOTAL_CONSUMED=$(grep "总消耗能量:" "$LOG_FILE" | grep -oP '\d+\.\d+(?=\s*J)' | tail -1)

    echo "初始能量: ${INITIAL_ENERGY:-未找到}"
    echo "最终能量: ${FINAL_ENERGY:-未找到}"
    echo "总消耗: ${TOTAL_CONSUMED:-未找到}"

    # 检查是否所有任务都被正确中断
    echo "--- 任务中断检查 ---"
    INTERRUPT_COUNT=$(grep -c "任务已中断" "$LOG_FILE")
    echo "中断次数: $INTERRUPT_COUNT"

    echo ""
done

echo "=========================================="
echo "  测试完成 - 详细日志保存在 $RESULTS_DIR"
echo "=========================================="
