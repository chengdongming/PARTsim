#!/bin/bash

# 全面测试脚本
# 测试TIE、TGF、BTIE三种算法

BASE_DIR="/home/devcontainers/PARTSim-project"
TEST_DIR="$BASE_DIR/test_results/comprehensive_test"
BINARY="$BASE_DIR/build/rtsim/rtsim"

mkdir -p "$TEST_DIR/results"

# 测试列表
declare -a TESTS=(
    "test1_energy_accounting"
    "test2_single_task"
    "test3_multicore"
    "test4_energy_recovery"
)

# 算法列表
declare -a ALGOS=(
    "gpfp_tie"
    "gpfp_tgf"
    "gpfp_btie"
)

echo "========================================"
echo "    全面测试 TIE/TGF/BTIE 算法"
echo "========================================"
echo ""

for TEST in "${TESTS[@]}"; do
    echo "=========================================="
    echo "测试: $TEST"
    echo "=========================================="

    for ALGO in "${ALGOS[@]}"; do
        echo ""
        echo "--- 算法: $ALGO ---"

        TASK_FILE="$TEST_DIR/${TEST}.yml"
        OUTPUT_FILE="$TEST_DIR/results/${TEST}_${ALGO}.log"
        TRACE_FILE="$TEST_DIR/results/${TEST}_${ALGO}_trace.json"

        # 创建临时系统配置文件
        SYSTEM_FILE="$TEST_DIR/system_${ALGO}_${TEST}.yml"

        # 复制任务文件并修改调度器
        sed "s/scheduler: .*/scheduler: $ALGO/" "$TASK_FILE" > "$SYSTEM_FILE"

        # 运行测试
        echo "运行: $BINARY $SYSTEM_FILE $TASK_FILE 10"
        "$BINARY" "$SYSTEM_FILE" "$TASK_FILE" 10 -t "$TRACE_FILE" > "$OUTPUT_FILE" 2>&1

        # 提取关键信息
        echo "结果摘要:"
        grep -E "(任务完成数|能量不足|Deadline|总消耗能量|剩余能量)" "$OUTPUT_FILE" | head -20

        # 检查错误
        if grep -q "ERROR\|FATAL\|Segmentation\|Assertion" "$OUTPUT_FILE"; then
            echo "⚠️ 发现错误！"
            grep -E "ERROR|FATAL|Segmentation|Assertion" "$OUTPUT_FILE"
        fi
    done
    echo ""
done

echo ""
echo "========================================"
echo "          测试完成"
echo "========================================"
echo "结果保存在: $TEST_DIR/results/"
echo ""
