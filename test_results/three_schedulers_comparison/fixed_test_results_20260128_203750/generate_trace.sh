#!/bin/bash
# 从日志文件生成trace JSON文件

LOG_FILE="$1"
OUTPUT_FILE="$2"
SCHEDULER="${3:-UNKNOWN}"

if [ ! -f "$LOG_FILE" ]; then
    echo "[]"
    exit 0
fi

echo "["

first=true
# 提取关键事件并转换为JSON
grep -E "(Tick事件触发|任务到达|任务结束|能量|中断|批量调度)" "$LOG_FILE" | while IFS= read -r line; do
    # 提取时间戳
    time=$(echo "$line" | grep -oP '\d{2}:\d{2}:\d{2}\.\d{3}' | head -1 || echo "00:00:00.000")

    # 提取事件类型
    event_type="unknown"
    task_name=""

    if echo "$line" | grep -q "Tick事件触发"; then
        event_type="tick"
        tick_time=$(echo "$line" | grep -oP '@ \K[0-9]+' || echo "0")
        echo "  {\"time\": \"$tick_time\", \"event_type\": \"tick\", \"scheduler\": \"$SCHEDULER\"},"
    elif echo "$line" | grep -q "任务到达"; then
        task_name=$(echo "$line" | grep -oP '任务[^:]*' | sed 's/任务//' || echo "unknown")
        event_type="task_arrival"
        echo "  {\"time\": \"$time\", \"event_type\": \"task_arrival\", \"task_name\": \"$task_name\", \"scheduler\": \"$SCHEDULER\"},"
    elif echo "$line" | grep -q "任务结束"; then
        task_name=$(echo "$line" | grep -oP 'PeriodicTask [^ ]+' | sed 's/PeriodicTask //' || echo "unknown")
        event_type="end_instance"
        echo "  {\"time\": \"$time\", \"event_type\": \"end_instance\", \"task_name\": \"$task_name\", \"scheduler\": \"$SCHEDULER\"},"
    elif echo "$line" | grep -q "中断任务"; then
        task_name=$(echo "$line" | grep -oP 'PeriodicTask [^ ]+' | sed 's/PeriodicTask //' || echo "unknown")
        event_type="interrupted"
        echo "  {\"time\": \"$time\", \"event_type\": \"interrupted\", \"task_name\": \"$task_name\", \"scheduler\": \"$SCHEDULER\"},"
    elif echo "$line" | grep -q "能量已耗尽"; then
        event_type="energy_depleted"
        echo "  {\"time\": \"$time\", \"event_type\": \"energy_depleted\", \"scheduler\": \"$SCHEDULER\"},"
    elif echo "$line" | grep -q "批量调度成功"; then
        event_type="batch_scheduled"
        echo "  {\"time\": \"$time\", \"event_type\": \"batch_scheduled\", \"scheduler\": \"$SCHEDULER\"},"
    fi
done | sed '$ s/,$//'

echo ""
echo "]"
