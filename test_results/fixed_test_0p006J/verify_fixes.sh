#!/bin/bash
# 三种调度算法修复验证脚本
# 用途: 快速验证TIE/BTIE/TGF能量管理修复是否正常

set -e

echo "========================================"
echo "三种调度算法修复验证测试"
echo "初始能量: 0.006J (6mJ)"
echo "========================================"
echo

# 配置
BUILD_DIR="../../build"
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKS_FILE="$TEST_DIR/tasks_3core_4task.yml"
SIMULATION_TIME=200

# 检查构建目录
if [ ! -d "$BUILD_DIR" ]; then
    echo "❌ 错误: 构建目录不存在: $BUILD_DIR"
    echo "请先运行: cd build && make -j4"
    exit 1
fi

# 检查rtsim可执行文件
RTSIM="$BUILD_DIR/rtsim/rtsim"
if [ ! -f "$RTSIM" ]; then
    echo "❌ 错误: rtsim可执行文件不存在: $RTSIM"
    echo "请先运行: cd build && make -j4"
    exit 1
fi

# 检查配置文件
if [ ! -f "$TASKS_FILE" ]; then
    echo "❌ 错误: 任务配置文件不存在: $TASKS_FILE"
    exit 1
fi

# 测试函数
test_scheduler() {
    local scheduler=$1
    local config_file=$2
    local trace_file=$3
    
    echo "----------------------------------------"
    echo "测试 $scheduler 调度器"
    echo "----------------------------------------"
    
    if [ ! -f "$config_file" ]; then
        echo "❌ 配置文件不存在: $config_file"
        return 1
    fi
    
    # 运行仿真
    echo "🚀 启动仿真..."
    if "$RTSIM" "$config_file" "$TASKS_FILE" $SIMULATION_TIME -t "$trace_file" > /dev/null 2>&1; then
        echo "✅ 仿真成功完成"
    else
        echo "❌ 仿真失败"
        return 1
    fi
    
    # 检查trace文件
    if [ ! -f "$trace_file" ]; then
        echo "❌ Trace文件未生成: $trace_file"
        return 1
    fi
    
    # 分析trace文件
    local scheduled_count=$(grep -c "scheduled" "$trace_file" || echo "0")
    local descheduled_count=$(grep -c "descheduled" "$trace_file" || echo "0")
    local end_count=$(grep -c "end_instance" "$trace_file" || echo "0")
    local last_time=$(grep '"time" :' "$trace_file" | tail -1 | grep -oP '\d+$' || echo "0")
    
    echo "📊 调度事件统计:"
    echo "   - scheduled:   $scheduled_count"
    echo "   - descheduled: $descheduled_count"
    echo "   - end_instance: $end_count"
    echo "   - 最后事件时间: ${last_time}ms"
    
    # 验证关键指标
    local errors=0
    
    # 检查1: 应该有scheduled事件
    if [ "$scheduled_count" -lt 100 ]; then
        echo "❌ 错误: scheduled事件数量异常 ($scheduled_count < 100)"
        ((errors++))
    fi
    
    # 检查2: 最后事件时间应该接近仿真时间
    if [ "$last_time" -lt 150 ] || [ "$last_time" -gt 200 ]; then
        echo "⚠️  警告: 最后事件时间异常 (${last_time}ms，预期150-200ms)"
        ((errors++))
    fi
    
    # 检查3: 不应该有无限循环 (最后几秒不应该有大量事件)
    local events_last_10ms=$(grep "\"time\" : \"19[0-9]\"" "$trace_file" | wc -l)
    if [ "$events_last_10ms" -gt 100 ]; then
        echo "❌ 错误: 最后10ms事件过多，可能存在无限循环"
        ((errors++))
    fi
    
    if [ $errors -eq 0 ]; then
        echo "✅ $scheduler 测试通过"
    else
        echo "❌ $scheduler 测试失败 ($errors 个错误)"
    fi
    
    echo
    return $errors
}

# 运行所有测试
total_errors=0

test_scheduler "TIE" \
    "$TEST_DIR/system_3core_tie_0p006J.yml" \
    "$TEST_DIR/tie_0p006J_trace.json" || ((total_errors++))

test_scheduler "BTIE" \
    "$TEST_DIR/system_3core_btie_0p006J.yml" \
    "$TEST_DIR/btie_0p006J_trace.json" || ((total_errors++))

test_scheduler "TGF" \
    "$TEST_DIR/system_3core_tgf_0p006J.yml" \
    "$TEST_DIR/tgf_0p006J_trace.json" || ((total_errors++))

# 总结
echo "========================================"
echo "测试总结"
echo "========================================"
if [ $total_errors -eq 0 ]; then
    echo "✅ 所有测试通过! 修复验证成功"
    echo
    echo "关键修复:"
    echo "  1. TIE/TGF: 能量不足时停止调度 (避免无限循环)"
    echo "  2. BTIE: 精确能量计算 (避免双重扣除)"
    echo
    echo "下一步建议:"
    echo "  - 查看详细分析: cat scheduler_comparison_analysis.md"
    echo "  - 查看事件时间线: cat event_timeline_summary.txt"
    echo "  - 测试不同能量级别: 修改yml配置中的initial_energy"
    exit 0
else
    echo "❌ 测试失败 ($total_errors 个调度器有问题)"
    echo
    echo "故障排查建议:"
    echo "  1. 确认代码修复已正确应用"
    echo "  2. 重新编译: cd build && make clean && make -j4"
    echo "  3. 检查日志文件中的错误信息"
    exit 1
fi
