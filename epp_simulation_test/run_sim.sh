#!/bin/bash

# ============================================
# PARTSim 能量感知调度仿真脚本 - 完整修复版
# 现在从系统配置文件读取调度器类型
# ============================================

# 颜色设置
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    MAGENTA='\033[0;35m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; MAGENTA=''; NC=''
fi

# 默认配置
DEFAULT_SYSTEM="./simconf/systems/epp_test_config.yml"
DEFAULT_TASKSET="epp_test_tasks.yml"
DEFAULT_DURATION=60000
DEFAULT_TRACE="trace.json"

# 工具函数
print_section() {
    echo -e "${CYAN}"
    printf '═%.0s' {1..60}
    echo
    echo "  $1"
    printf '═%.0s' {1..60}
    echo -e "${NC}"
}

print_success() { 
    echo -e "${GREEN}✓ $1${NC}" 
}

print_warning() { 
    echo -e "${YELLOW}⚠ $1${NC}" 
}

print_error() { 
    echo -e "${RED}✗ $1${NC}" 
}

print_info() { 
    echo -e "${BLUE}ℹ $1${NC}" 
}

# 格式化时间（毫秒转为HH:MM:SS）
format_time() {
    local total_ms=$1
    local hours=$((total_ms / 3600000))
    local minutes=$(( (total_ms % 3600000) / 60000 ))
    local seconds=$(( (total_ms % 60000) / 1000 ))
    printf "%02d:%02d:%02d" $hours $minutes $seconds
}

# 从YAML文件读取调度器类型
get_scheduler_from_yaml() {
    local config_file="$1"
    local scheduler_type="gpfp_asap"  # 默认值
    
    if [ ! -f "$config_file" ]; then
        echo "$scheduler_type"
        return 1
    fi
    
    # 使用Python解析YAML文件
    local python_code="
import yaml
import sys

try:
    with open('$config_file', 'r') as f:
        config = yaml.safe_load(f)
    
    # 查找调度器设置
    if 'cpu_islands' in config and config['cpu_islands']:
        for island in config['cpu_islands']:
            if 'kernel' in island and 'scheduler' in island['kernel']:
                scheduler = island['kernel']['scheduler']
                print(scheduler)
                sys.exit(0)
    
    # 如果没有找到，返回默认值
    print('gpfp_asap')
    
except Exception as e:
    print('gpfp_asap')
    sys.exit(1)
"
    
    # 尝试使用python3
    if command -v python3 >/dev/null 2>&1; then
        local result=$(python3 -c "$python_code" 2>/dev/null)
        echo "$result"
    # 如果python3不可用，尝试使用grep简单匹配
    elif grep -q "scheduler:" "$config_file"; then
        local result=$(grep -A1 "scheduler:" "$config_file" | tail -1 | sed 's/[[:space:]]*//' | sed 's/["'\'']//g')
        echo "$result"
    else
        echo "$scheduler_type"
    fi
}
# 从YAML文件读取start_offset_minutes
get_start_offset_from_yaml() {
    local config_file="$1"
    local offset_minutes=0  # 默认值

    if [ ! -f "$config_file" ]; then
        echo "$offset_minutes"
        return 1
    fi

    # 使用Python解析YAML文件
    local python_code="
import yaml
import sys

try:
    with open('$config_file', 'r') as f:
        config = yaml.safe_load(f)

    offset_ms = 0

    # 方法1: 查找energy_management中的start_offset_minutes
    if 'energy_management' in config and 'start_offset_minutes' in config['energy_management']:
        offset = config['energy_management']['start_offset_minutes']
        offset_ms = offset * 60 * 1000
        print(int(offset_ms))
        sys.exit(0)

    # 方法2: 使用time_of_day_ms作为时间偏移（表示一天的什么时间）
    if 'energy_management' in config:
        em = config['energy_management']
        time_of_day_ms = em.get('time_of_day_ms', 0)

        if time_of_day_ms > 0:
            # 直接使用time_of_day_ms作为偏移
            offset_ms = time_of_day_ms
            print(int(offset_ms))
            sys.exit(0)

    # 如果没有找到，返回0
    print('0')
except Exception as e:
    print('0', file=sys.stderr)
    sys.exit(1)
"

    # 尝试使用python3
    if command -v python3 >/dev/null 2>&1; then
        local result=$(python3 -c "$python_code" 2>/dev/null)
        echo "${result:-0}"
    else
        echo "0"
    fi
}


# 显示使用方法
show_usage() {
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}               PARTSim 能量感知调度仿真系统                   ${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}用法:${NC} $0 [选项]"
    echo ""
    echo "  -s, --system FILE    系统配置文件（YAML格式）"
    echo "                       → 调度器类型将从该文件读取"
    echo "  -t, --taskset FILE   任务集文件（YAML格式）"
    echo "  -d, --duration MS    仿真持续时间（毫秒，默认: 60000）"
    echo "  -o, --output FILE    输出跟踪文件（默认: trace.json）"
    echo "  --scheduler NAME     调度器类型（可选，覆盖配置文件中的设置）"
    echo "  -h, --help           显示此帮助信息"
    echo ""
    echo -e "${CYAN}示例:${NC}"
    echo "  $0 -s epp_test_config.yml -t tasks.yml --scheduler gpfp_asap -d 60000"
    echo ""
    echo -e "${CYAN}注意:${NC}"
    echo "  * 调度器类型默认从系统配置文件(cpu_islands.kernel.scheduler)读取"
    echo "  * 使用 --scheduler 参数可以覆盖配置文件中的设置"
    echo ""
}

# 创建一个简单的Python处理脚本
create_simple_processor() {
    cat > /tmp/simple_trace_processor.py << 'EOF'
#!/usr/bin/env python3
"""
简单跟踪文件处理器 - 专门修复类型转换问题
"""
import json
import sys
from datetime import datetime

def safe_int(value):
    """安全转换为整数"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            # 移除可能的引号
            value = value.strip('"\'')
            if '.' in value:
                return int(float(value))
            else:
                return int(value)
        except:
            return 0
    return 0

def process_trace(input_file, output_file, time_offset, scheduler_type):
    print(f"处理跟踪文件: {input_file}")
    print(f"时间偏移: {time_offset} ms")
    
    try:
        # 读取原始文件
        with open(input_file, 'r', encoding='utf-8') as f:
            raw_data = f.read().strip()
        
        events = []
        
        # 尝试解析为JSON
        try:
            data = json.loads(raw_data)
            
            # 检查格式
            if isinstance(data, dict):
                if 'events' in data:
                    events = data['events']
                    print(f"检测到标准JSON格式，事件数: {len(events)}")
                else:
                    # 可能是其他结构的字典，尝试转换为事件
                    events = [data]
                    print(f"检测到单个事件字典")
            elif isinstance(data, list):
                events = data
                print(f"检测到JSON数组格式，事件数: {len(events)}")
            else:
                print(f"警告: 未知JSON格式类型: {type(data)}")
                
        except json.JSONDecodeError:
            # 尝试逐行解析
            print("JSON解析失败，尝试逐行解析...")
            lines = raw_data.split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('//') or line.startswith('#'):
                    continue
                
                # 清理尾部逗号
                if line.endswith(','):
                    line = line[:-1]
                
                if line.startswith('{') and line.endswith('}'):
                    try:
                        event = json.loads(line)
                        events.append(event)
                    except:
                        continue
            
            print(f"逐行解析，事件数: {len(events)}")
        
        if not events:
            print("警告: 未找到任何事件")
            return False
        
        print(f"成功解析 {len(events)} 个事件")
        
        # 应用时间偏移
        if time_offset > 0:
            print(f"应用时间偏移: {time_offset} ms")
            for event in events:
                # 确保事件是字典
                if not isinstance(event, dict):
                    continue
                
                # 处理时间字段
                for time_field in ['time', 'arrival_time', 'timestamp', 'start_time', 'end_time']:
                    if time_field in event:
                        # 保存原始值
                        if f'original_{time_field}' not in event:
                            event[f'original_{time_field}'] = event[time_field]
                        
                        # 安全转换为整数并添加偏移
                        original_val = event[time_field]
                        int_val = safe_int(original_val)
                        event[time_field] = int_val + time_offset
        
        # 按时间排序
        events.sort(key=lambda x: safe_int(x.get('time', 0)))
        
        # 计算统计信息
        if events:
            times = [safe_int(e.get('time', 0)) for e in events]
            min_time = min(times) if times else 0
            max_time = max(times) if times else 0
            duration_ms = max_time - min_time
            duration_s = duration_ms / 1000.0
        else:
            min_time = max_time = duration_ms = duration_s = 0
        
        # 创建输出结构
        output_data = {
            'events': events,
            'metadata': {
                'start_time_offset': time_offset,
                'total_events': len(events),
                'scheduler_type': scheduler_type,
                'processing_method': 'simple_processor',
                'processing_timestamp': datetime.now().isoformat(),
                'statistics': {
                    'min_time': min_time,
                    'max_time': max_time,
                    'duration_ms': duration_ms,
                    'duration_s': duration_s
                },
                'description': f'处理完成的跟踪文件，应用时间偏移 {time_offset} ms'
            }
        }
        
        # 写入输出文件
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        print(f"处理完成: 写入 {len(events)} 个事件到 {output_file}")
        print(f"时间范围: {min_time} - {max_time} ms ({duration_s:.1f} 秒)")
        
        return True
        
    except Exception as e:
        print(f"处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("用法: python3 simple_trace_processor.py <输入文件> <输出文件> <时间偏移> <调度器类型>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    time_offset = int(sys.argv[3])
    scheduler_type = sys.argv[4]
    
    success = process_trace(input_file, output_file, time_offset, scheduler_type)
    sys.exit(0 if success else 1)
EOF
}

# 主函数
main() {
    # 解析参数
    SYSTEM_CONFIG=""
    TASKSET_FILE=""
    SIM_DURATION=""
    OUTPUT_FILE=""
    
    OVERRIDE_SCHEDULER=""  # 用于覆盖配置文件的调度器
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -s|--system)
                SYSTEM_CONFIG="$2"
                shift 2
                ;;
            -t|--taskset)
                TASKSET_FILE="$2"
                shift 2
                ;;
            -d|--duration)
                SIM_DURATION="$2"
                shift 2
                ;;
            -o|--output)
                OUTPUT_FILE="$2"
                shift 2
                ;;
            --scheduler)
                OVERRIDE_SCHEDULER="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                print_error "未知参数: $1"
                show_usage
                exit 1
                ;;
        esac
    done
    
    # 设置默认值
    if [ -z "$SYSTEM_CONFIG" ]; then
        SYSTEM_CONFIG="$DEFAULT_SYSTEM"
    fi
    
    if [ -z "$TASKSET_FILE" ]; then
        TASKSET_FILE="$DEFAULT_TASKSET"
    fi
    
    if [ -z "$SIM_DURATION" ]; then
        SIM_DURATION="$DEFAULT_DURATION"
    fi
    
    if [ -z "$OUTPUT_FILE" ]; then
        OUTPUT_FILE="$DEFAULT_TRACE"
    fi
    

    
    # 1. 从配置文件读取调度器类型
    print_section "读取系统配置"
    SCHEDULER_TYPE=$(get_scheduler_from_yaml "$SYSTEM_CONFIG")
    
    if [ -z "$SCHEDULER_TYPE" ]; then
        SCHEDULER_TYPE="gpfp_asap"  # 备用默认值
        print_warning "无法从配置文件读取调度器类型，使用默认值: $SCHEDULER_TYPE"
    else
        print_info "从配置文件读取到调度器类型: $SCHEDULER_TYPE"
    fi
    
    # 2. 从配置文件读取时间偏移
    START_TIME_OFFSET=$(get_start_offset_from_yaml "$SYSTEM_CONFIG")
    if [ -z "$START_TIME_OFFSET" ] || [ "$START_TIME_OFFSET" = "0" ]; then
        START_TIME_OFFSET="0"
        print_info "未配置时间偏移，使用0（从年初开始）"
    else
        local time_str=$(format_time "$START_TIME_OFFSET")
        print_info "从配置文件读取到时间偏移: $START_TIME_OFFSET ms ($time_str)"
    fi
    
    # 3. 检查是否有命令行覆盖
    if [ -n "$OVERRIDE_SCHEDULER" ]; then
        print_info "命令行覆盖调度器类型: $OVERRIDE_SCHEDULER (原为: $SCHEDULER_TYPE)"
        SCHEDULER_TYPE="$OVERRIDE_SCHEDULER"
    fi
    
    # 显示配置摘要
    print_section "配置摘要"
    echo "  系统配置文件: $SYSTEM_CONFIG"
    echo "  任务集文件: $TASKSET_FILE"
    echo "  仿真持续时间: $SIM_DURATION ms"
    echo "  时间偏移将从配置文件读取"
    echo "  调度器类型: $SCHEDULER_TYPE (从配置文件读取)"
    echo "  输出跟踪文件: $OUTPUT_FILE"
    echo ""
    
    # 检查文件是否存在
    if [ ! -f "$SYSTEM_CONFIG" ]; then
        print_error "系统配置文件不存在: $SYSTEM_CONFIG"
        exit 1
    fi
    
    if [ ! -f "$TASKSET_FILE" ]; then
        print_error "任务集文件不存在: $TASKSET_FILE"
        exit 1
    fi
    
    # 检查仿真程序
    if [ ! -f "./build/rtsim/rtsim" ]; then
        print_error "仿真程序未找到: ./build/rtsim/rtsim"
        exit 1
    fi
    
    # 清理旧的跟踪文件
    RAW_OUTPUT_FILE="${OUTPUT_FILE%.json}_raw.json"
    if [ -f "$RAW_OUTPUT_FILE" ]; then
        rm -f "$RAW_OUTPUT_FILE"
        print_info "清理旧的原始跟踪文件: $RAW_OUTPUT_FILE"
    fi
    
    if [ -f "$OUTPUT_FILE" ]; then
        rm -f "$OUTPUT_FILE"
        print_info "清理旧的最终跟踪文件: $OUTPUT_FILE"
    fi
    
    # 设置环境变量
    print_section "设置环境变量"

    # === 修复：从YAML配置文件读取核心数 ===
    NUM_CPUS=$(python3 -c "
import yaml
try:
    with open('$SYSTEM_CONFIG', 'r') as f:
        config = yaml.safe_load(f)
    if 'cpu_islands' in config and config['cpu_islands']:
        print(config['cpu_islands'][0].get('numcpus', 4))
    else:
        print('4')
except:
    print('4')
" 2>/dev/null || echo "4")
    export RTSIM_NUM_CORES="$NUM_CPUS"

    # 直接设置，不进行unset
    export START_TIME_OFFSET=$(get_start_offset_from_yaml "$SYSTEM_CONFIG")
    export SCHEDULER_TYPE="$SCHEDULER_TYPE"
    export ENERGY_CONFIG_FILE="$SYSTEM_CONFIG"
    export TASKSET_CONFIG_PATH="$TASKSET_FILE"
    export RTSIM_ENERGY_DEBUG=1
    export RTSIM_VERBOSE=1

    echo "环境变量已设置:"
    echo "  START_TIME_OFFSET=$START_TIME_OFFSET"
    echo "  SCHEDULER_TYPE=$SCHEDULER_TYPE"
    echo "  ENERGY_CONFIG_FILE=$ENERGY_CONFIG_FILE"
    echo "  TASKSET_CONFIG_PATH=$TASKSET_CONFIG_PATH"
    echo "  RTSIM_NUM_CORES=$RTSIM_NUM_CORES (从YAML配置读取)"
    echo ""

    # 运行仿真
    print_section "运行仿真"
    CMD="./build/rtsim/rtsim \"$SYSTEM_CONFIG\" \"$TASKSET_FILE\" \"$SIM_DURATION\" -t \"$RAW_OUTPUT_FILE\""
    echo "执行命令: $CMD"
    echo ""
    
    if eval $CMD; then
        print_success "仿真完成"
        
        # 检查是否生成了跟踪文件
        if [ -f "$RAW_OUTPUT_FILE" ]; then
            local file_size=$(wc -c < "$RAW_OUTPUT_FILE" 2>/dev/null | awk '{print $1}' || echo "0")
            print_success "生成原始跟踪文件: $RAW_OUTPUT_FILE ($((file_size/1024))KB)"
            
            # 处理跟踪文件
            print_section "处理跟踪文件"
            
            # 创建简单的处理脚本
            create_simple_processor
            
            # 使用简单的处理脚本
            print_info "使用简单处理器处理跟踪文件"
            if python3 /tmp/simple_trace_processor.py "$RAW_OUTPUT_FILE" "$OUTPUT_FILE" "$START_TIME_OFFSET" "$SCHEDULER_TYPE"; then
                print_success "跟踪文件处理成功"
                
                # 检查输出文件
                if [ -f "$OUTPUT_FILE" ]; then
                    local output_size=$(wc -c < "$OUTPUT_FILE" 2>/dev/null | awk '{print $1}' || echo "0")
                    print_success "生成最终跟踪文件: $OUTPUT_FILE ($((output_size/1024))KB)"
                    
                    # 显示统计信息
                    local event_count=$(python3 -c "
import json
try:
    with open('$OUTPUT_FILE', 'r') as f:
        data = json.load(f)
    if 'events' in data:
        print(len(data['events']))
    elif isinstance(data, list):
        print(len(data))
    else:
        print('0')
except:
    print('0')
" 2>/dev/null || echo "0")
                    
                    if [ "$event_count" -gt 0 ]; then
                        print_info "跟踪文件包含 $event_count 个事件"
                    fi
                    
                    # 删除原始跟踪文件
                    if [ -f "$RAW_OUTPUT_FILE" ]; then
                        rm -f "$RAW_OUTPUT_FILE"
                        print_info "已自动删除原始跟踪文件: $RAW_OUTPUT_FILE"
                    fi
                else
                    print_warning "未生成最终跟踪文件，保留原始文件用于调试"
                fi
            else
                print_error "跟踪文件处理失败"
                print_info "尝试使用备用方法处理..."
                
                # 备用方法：直接复制原始文件
                if cp "$RAW_OUTPUT_FILE" "$OUTPUT_FILE" 2>/dev/null; then
                    print_warning "已复制原始文件作为输出（未应用时间偏移）"
                    
                    # 删除原始跟踪文件
                    if [ -f "$RAW_OUTPUT_FILE" ]; then
                        rm -f "$RAW_OUTPUT_FILE"
                        print_info "已自动删除原始跟踪文件: $RAW_OUTPUT_FILE"
                    fi
                else
                    print_error "无法创建输出文件，保留原始文件用于调试"
                fi
            fi
            
            # 清理临时文件
            rm -f /tmp/simple_trace_processor.py 2>/dev/null
        else
            print_warning "未生成原始跟踪文件"
        fi
    else
        print_error "仿真失败"
        exit 1
    fi
    
    # 显示最终结果
    print_section "仿真结果"
    echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}                    🎉 仿真成功完成!                         ${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "配置摘要:"
    echo "  系统配置: $(basename "$SYSTEM_CONFIG")"
    echo "  任务集: $(basename "$TASKSET_FILE")"
    echo "  仿真时间: $SIM_DURATION ms"
    echo "  开始时间: $time_str"
    echo "  调度器: $SCHEDULER_TYPE"
    
    if [ -f "$OUTPUT_FILE" ]; then
        echo ""
        echo "输出文件: $OUTPUT_FILE"
        echo "文件大小: $(du -h "$OUTPUT_FILE" 2>/dev/null | cut -f1 || echo "未知")"
    fi
    
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    
    return 0
}

# 脚本入口点
if [ "$0" = "$BASH_SOURCE" ]; then
    if [ $# -eq 0 ]; then
        show_usage
        exit 0
    fi
    
    if main "$@"; then
        exit 0
    else
        exit 1
    fi
fi
