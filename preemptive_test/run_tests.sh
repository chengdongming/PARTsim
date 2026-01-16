#!/bin/bash
# 抢占式调度测试脚本

set -e

echo "========================================"
echo "  抢占式调度测试"
echo "========================================"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查依赖
echo -e "${YELLOW}检查依赖...${NC}"
if ! command -v jq &> /dev/null; then
    echo -e "${RED}警告: jq未安装，将无法分析trace文件${NC}"
    echo "安装命令: sudo apt-get install jq"
fi

# 编译
echo -e "\n${YELLOW}编译测试程序...${NC}"
make clean
make

if [ ! -f test_edf ]; then
    echo -e "${RED}编译失败！${NC}"
    exit 1
fi

echo -e "${GREEN}编译成功！${NC}"

# 运行EDF测试
echo -e "\n${YELLOW}========================================${NC}"
echo -e "${GREEN}运行EDF测试...${NC}"
echo -e "${YELLOW}========================================${NC}"
./test_edf -s edf -o trace_edf.json

# 运行FP测试
echo -e "\n${YELLOW}========================================${NC}"
echo -e "${GREEN}运行FP测试...${NC}"
echo -e "${YELLOW}========================================${NC}"
./test_fp -s fp -o trace_fp.json

# 运行RM测试
echo -e "\n${YELLOW}========================================${NC}"
echo -e "${GREEN}运行RM测试...${NC}"
echo -e "${YELLOW}========================================${NC}"
./test_rm -s rm -o trace_rm.json

# 分析结果
echo -e "\n${YELLOW}========================================${NC}"
echo -e "${GREEN}分析结果...${NC}"
echo -e "${YELLOW}========================================${NC}"

if command -v jq &> /dev/null; then
    echo -e "\n${GREEN}=== EDF调度器 ===${NC}"
    edf_stats=$(jq '{schedules: .schedules | length, preemptions: [.schedules[] | select(.preempted == true)] | length, misses: .deadline_misses | length}' trace_edf.json)
    echo "调度次数: $(echo $edf_stats | jq -r '.schedules')"
    echo "抢占次数: $(echo $edf_stats | jq -r '.preemptions')"
    echo "截止时间错过: $(echo $edf_stats | jq -r '.misses')"

    echo -e "\n${GREEN}=== FP调度器 ===${NC}"
    fp_stats=$(jq '{schedules: .schedules | length, preemptions: [.schedules[] | select(.preempted == true)] | length, misses: .deadline_misses | length}' trace_fp.json)
    echo "调度次数: $(echo $fp_stats | jq -r '.schedules')"
    echo "抢占次数: $(echo $fp_stats | jq -r '.preemptions')"
    echo "截止时间错过: $(echo $fp_stats | jq -r '.misses')"

    echo -e "\n${GREEN}=== RM调度器 ===${NC}"
    rm_stats=$(jq '{schedules: .schedules | length, preemptions: [.schedules[] | select(.preempted == true)] | length, misses: .deadline_misses | length}' trace_rm.json)
    echo "调度次数: $(echo $rm_stats | jq -r '.schedules')"
    echo "抢占次数: $(echo $rm_stats | jq -r '.preemptions')"
    echo "截止时间错过: $(echo $rm_stats | jq -r '.misses')"

    # 显示抢占事件示例
    echo -e "\n${GREEN}=== 抢占事件示例 (EDF) ===${NC}"
    jq -r '.schedules[] | select(.preempted == true) | "\(.time)ms: \(.task) 被 \(.preempted_by) 抢占"' trace_edf.json | head -5
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}测试完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Trace文件:"
echo -e "  - trace_edf.json"
echo -e "  - trace_fp.json"
echo -e "  - trace_rm.json"
echo -e "\n查看详细trace:"
echo -e "  cat trace_edf.json | jq '.schedules[] | select(.preempted == true)'"
