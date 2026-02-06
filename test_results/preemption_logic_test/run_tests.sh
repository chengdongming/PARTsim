#!/bin/bash

# 抢占逻辑测试脚本
# 测试三种算法（TIE, TGF, BTIE）的抢占行为

echo "=========================================="
echo "抢占逻辑测试"
echo "=========================================="

# 设置路径
SIMULATOR="../../build/rtsim/rtsim"
TASKSET="taskset.yml"
SIMULATION_TIME=120000  # 120秒仿真时间
TEST_DIR="$(pwd)"

# 设置LD_LIBRARY_PATH
export LD_LIBRARY_PATH="../../build/librtsim:$LD_LIBRARY_PATH"

# 测试三种算法
algorithms=("tie" "tgf" "btie")

for algo in "${algorithms[@]}"; do
    echo ""
    echo "=========================================="
    echo "测试算法: ${algo^^}"
    echo "=========================================="

    CONFIG="configs/config_${algo}.yml"
    TRACE="traces/trace_${algo}.json"
    LOG="logs/${algo}_test.log"

    echo "配置文件: $CONFIG"
    echo "任务文件: $TASKSET"
    echo "仿真时长: ${SIMULATION_TIME}ms"
    echo "输出trace: $TRACE"

    # 运行仿真
    $SIMULATOR "$CONFIG" "$TASKSET" "$SIMULATION_TIME" -t "$TRACE" > "$LOG" 2>&1

    if [ $? -eq 0 ]; then
        echo "✅ ${algo^^} 测试完成"

        # 统计抢占次数
        if [ -f "$TRACE" ]; then
            preemptions=$(grep -o '"event_type":"preempt"' "$TRACE" | wc -l)
            echo "   抢占次数: $preemptions"
        fi
    else
        echo "❌ ${algo^^} 测试失败"
        echo "查看日志: $LOG"
    fi
done

echo ""
echo "=========================================="
echo "所有测试完成"
echo "=========================================="
echo "Trace文件位于: traces/"
echo "日志文件位于: logs/"
