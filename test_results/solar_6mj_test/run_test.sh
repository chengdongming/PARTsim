#!/bin/bash

# ============================================================================
# TIE/TGF/BTIE 能量感知调度算法测试
# 初始能量: 6mJ + 太阳能收集
# ============================================================================

BASE_DIR="/home/devcontainers/PARTSim-project"
TEST_DIR="$BASE_DIR/test_results/solar_6mj_test"
BINARY="$BASE_DIR/build/rtsim/rtsim"

mkdir -p "$TEST_DIR/results"

# 测试配置
SCHEDULERS=("gpfp_tie" "gpfp_tgf" "gpfp_btie")
TASK_FILE="$TEST_DIR/tasks.yml"

echo "========================================"
echo "  太阳能+6mJ初始能量测试"
echo "========================================"
echo ""
echo "测试配置:"
echo "  - CPU数量: 3核"
echo "  - 初始能量: 6 mJ"
echo "  - 太阳能: 启用 (NASA数据, 夏至)"
echo "  - 时间范围: 0-1000ms"
echo "  - 任务数: 4个bzip2任务"
echo ""

for SCHEDULER in "${SCHEDULERS[@]}"; do
    echo "=========================================="
    echo "测试调度器: $SCHEDULER"
    echo "=========================================="

    SYSTEM_FILE="$TEST_DIR/system_3core_${SCHEDULER}_6mj_solar.yml"
    OUTPUT_FILE="$TEST_DIR/results/${SCHEDULER}_6mj_solar.log"
    TRACE_FILE="$TEST_DIR/results/${SCHEDULER}_6mj_solar_trace.json"

    echo "系统文件: $SYSTEM_FILE"
    echo "输出文件: $OUTPUT_FILE"
    echo "追踪文件: $TRACE_FILE"
    echo ""

    # 运行测试（仿真时长1000ms）
    echo "运行测试..."
    "$BINARY" "$SYSTEM_FILE" "$TASK_FILE" 1000 -t "$TRACE_FILE" > "$OUTPUT_FILE" 2>&1

    # 提取关键结果
    echo "========== 测试结果摘要 =========="
    echo "任务完成数:"
    grep "任务完成数" "$OUTPUT_FILE" | tail -1

    echo ""
    echo "能量消耗:"
    grep "剩余能量" "$OUTPUT_FILE" | tail -1

    echo ""
    echo "Deadline Miss:"
    grep "Deadline Miss" "$OUTPUT_FILE" | tail -1 || echo "  (无deadline miss)"

    echo ""
    echo "能量收集:"
    grep "收集能量" "$OUTPUT_FILE" | head -5 || echo "  (无能量收集)"

    echo ""

    # 检查错误
    if grep -q "ERROR\|FATAL\|Segmentation\|Assertion" "$OUTPUT_FILE"; then
        echo "⚠️ 发现错误！"
        grep -E "ERROR|FATAL|Segmentation|Assertion" "$OUTPUT_FILE" | head -10
    fi

    echo ""
done

echo "========================================"
echo "  测试完成"
echo "========================================"
echo ""
echo "结果文件:"
echo "  日志: $TEST_DIR/results/"
echo "  追踪: $TEST_DIR/results/*_6mj_solar_trace.json"
echo ""
