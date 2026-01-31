#!/bin/bash
# ============================================================================
# 三种算法（TIE/TGF/BTIE）在不同初始能量下的对比测试
# 测试配置：0J, 12mJ, 15mJ, 100J
# ============================================================================

BASE_DIR="/home/devcontainers/PARTSim-project"
TEST_DIR="$BASE_DIR/test_results/energy_comparison_v39"
BINARY="$BASE_DIR/build/rtsim/rtsim"
TASKS_FILE="$BASE_DIR/test_results/energy_2core_3task_test/tasks.yml"

# 测试参数
SCHEDULERS=("gpfp_tie" "gpfp_tgf" "gpfp_btie")
ENERGY_LEVELS=("0j" "12mj" "15mj" "100j")
ENERGY_VALUES=("0.0" "0.012" "0.015" "100.0")

echo "========================================"
echo "  三种算法能量对比测试 (V39)"
echo "========================================"
echo "测试配置:"
echo "  - 调度器: TIE, TGF, BTIE"
echo "  - 初始能量: 0J, 12mJ, 15mJ, 100J"
echo "  - 太阳能: 启用 (0点，无充电)"
echo "  - 仿真时长: 100ms"
echo ""

# 创建输出目录
mkdir -p "$TEST_DIR/traces"
mkdir -p "$TEST_DIR/logs"
mkdir -p "$TEST_DIR/configs"

# 生成配置文件
echo "生成配置文件..."
for i in "${!ENERGY_LEVELS[@]}"; do
    ENERGY="${ENERGY_LEVELS[$i]}"
    VALUE="${ENERGY_VALUES[$i]}"

    for SCHEDULER in "${SCHEDULERS[@]}"; do
        CONFIG_FILE="$TEST_DIR/configs/system_2core_${SCHEDULER}_${ENERGY}_v39.yml"

        case $SCHEDULER in
            gpfp_tie)
                SCHED_NAME="TIE"
                ;;
            gpfp_tgf)
                SCHED_NAME="TGF"
                ;;
            gpfp_btie)
                SCHED_NAME="BTIE"
                ;;
        esac

        cat > "$CONFIG_FILE" << EOF
# CPU集群配置
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 2

    kernel:
      scheduler: $SCHEDULER
      task_placement: global

    # 电压频率配置
    volts: [0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, 1.08, 1.10, 1.12, 1.14]
    freqs: [7000, 7500, 8000, 8100, 8200, 8300, 8400, 8500, 9000, 9500, 10000, 10500]
    base_freq: 8100

    # 功率和速度模型
    power_model: energy_aware_model
    speed_model: energy_aware_model

# =============================================
# 能量管理配置
# =============================================
energy_management:
  # 基本能量参数
  initial_energy: $VALUE
  max_energy: 1000.0

  # ⭐ 时间设置：0点（无太阳能充电）
  day_of_year: 182
  time_of_day_ms: 0

  # === NASA真实太阳能数据配置 ===
  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0
  battery_capacity_wh: 100.0
  battery_voltage: 3.7

  # 充电管理器配置
  charging_manager:
    type: solar
    enable_logging: true
    log_file: "$TEST_DIR/logs/charging_${SCHEDULER}_${ENERGY}.log"
EOF
        echo "  创建: $CONFIG_FILE"
    done
done

echo ""
echo "========================================"
echo "  开始运行测试"
echo "========================================"
echo ""

# 运行测试
TOTAL_TESTS=${#SCHEDULERS[@]}
TOTAL_TESTS=$((TOTAL_TESTS * ${#ENERGY_LEVELS[@]}))
CURRENT_TEST=0

for SCHEDULER in "${SCHEDULERS[@]}"; do
    for i in "${!ENERGY_LEVELS[@]}"; do
        ENERGY="${ENERGY_LEVELS[$i]}"
        CURRENT_TEST=$((CURRENT_TEST + 1))

        CONFIG_FILE="$TEST_DIR/configs/system_2core_${SCHEDULER}_${ENERGY}_v39.yml"
        OUTPUT_LOG="$TEST_DIR/logs/${SCHEDULER}_${ENERGY}_v39.log"
        TRACE_FILE="$TEST_DIR/traces/${SCHEDULER}_${ENERGY}_v39.json"

        echo "=========================================="
        echo "[$CURRENT_TEST/$TOTAL_TESTS] 测试: $SCHEDULER | 初始能量: $ENERGY"
        echo "=========================================="

        if [ ! -f "$BINARY" ]; then
            echo "❌ 错误: 找不到rtsim可执行文件: $BINARY"
            exit 1
        fi

        echo "运行测试..."
        "$BINARY" "$CONFIG_FILE" "$TASKS_FILE" 100 -t "$TRACE_FILE" > "$OUTPUT_LOG" 2>&1

        if [ $? -eq 0 ]; then
            echo "✅ 测试完成"

            # 提取关键结果
            echo "---------- 测试结果摘要 ----------"
            grep "任务完成数" "$OUTPUT_LOG" | tail -1
            grep "剩余能量" "$OUTPUT_LOG" | tail -1
            grep "Deadline Miss" "$OUTPUT_LOG" | tail -1 || echo "  (无deadline miss)"

            # 生成可视化
            echo "生成可视化..."
            python3 "$BASE_DIR/trace_visualizer.py" "$TRACE_FILE" \
                --output "$TEST_DIR/traces/${SCHEDULER}_${ENERGY}_v39.png" \
                --width 25 --height 8 \
                --title "${SCHEDULER} - ${ENERGY} - V39" \
                2>/dev/null

            if [ $? -eq 0 ]; then
                echo "✅ 可视化完成: ${SCHEDULER}_${ENERGY}_v39.png"
            else
                echo "⚠️ 可视化生成失败"
            fi
        else
            echo "❌ 测试失败！"
            grep -E "ERROR|FATAL|Segmentation|Assertion" "$OUTPUT_LOG" | head -5
        fi

        echo ""
    done
done

echo "========================================"
echo "  所有测试完成"
echo "========================================"
echo ""
echo "结果文件:"
echo "  追踪文件: $TEST_DIR/traces/"
echo "  日志文件: $TEST_DIR/logs/"
echo "  配置文件: $TEST_DIR/configs/"
echo "  可视化图表: $TEST_DIR/traces/*.png"
echo ""
