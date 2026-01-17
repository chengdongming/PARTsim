#!/usr/bin/env python3
"""
分析arrival_offset修复后的结果
"""

import json

with open('/home/devcontainers/PARTSim-project/arrival_offset_test/trace_final.json', 'r') as f:
    data = json.load(f)
    events = data['events']

print("="*80)
print("arrival_offset 修复后测试结果")
print("="*80)
print()

time_offset = 43200000

# 统计每个任务的所有到达事件
arrivals = {}
for e in events:
    if e['event_type'] == 'arrival':
        task = e['task_name']
        time = int(e['time']) - time_offset

        if task not in arrivals:
            arrivals[task] = []
        arrivals[task].append(time)

print("【所有任务实例的到达时间】")
print("任务        | 实例1 | 实例2 | 实例3 | 周期")
print("-"*60)

for task in ['task_high', 'task_mid', 'task_low']:
    if task in arrivals:
        times = arrivals[task]
        times_str = " | ".join([f"{t:>6}" for t in times])

        # 计算实际周期
        if len(times) >= 2:
            actual_period = times[1] - times[0]
        else:
            actual_period = -1

        print(f"{task:<12} | {times_str} | {actual_period}")

print()

# 对比预期
expected_arrivals = {
    'task_high': [0, 500, 1000, 1500],      # period=500, offset=0
    'task_mid':  [200, 1200],               # period=1000, offset=200
    'task_low':  [100, 1600]                # period=1500, offset=100
}

print("【预期 vs 实际对比】")
print(f"{'任务':<12} | {'预期到达':<30} | {'实际到达':<30} | {'状态':<10}")
print("-"*85)

for task in ['task_high', 'task_mid', 'task_low']:
    expected = expected_arrivals[task]
    actual = arrivals.get(task, [])

    # 只对比前N个实例
    min_len = min(len(expected), len(actual))
    exp_str = ", ".join([f"{t}" for t in expected[:min_len]])
    act_str = ", ".join([f"{t}" for t in actual[:min_len]])

    # 检查是否匹配
    match = expected[:min_len] == actual[:min_len]
    status = "✅ 正确" if match else "❌ 错误"

    print(f"{task:<12} | {exp_str:<30} | {act_str:<30} | {status:<10}")

print()

# 详细事件时间线（前30个）
print("【详细事件时间线】(前30个事件)")
print(f"{'相对时间':<8} | {'事件类型':<15} | {'任务':<12}")
print("-"*50)

for i, e in enumerate(events[:30]):
    time = int(e['time']) - time_offset
    etype = e['event_type']
    task = e['task_name']
    print(f"{time:<8} | {etype:<15} | {task:<12}")

print()

# 抢占式调度验证
print("【抢占式调度验证】")
print()

# 分析事件序列
preemptions = []
for i in range(1, len(events)):
    prev = events[i-1]
    curr = events[i]

    # 查找抢占：一个任务还没完成，另一个任务就开始了
    if prev['event_type'] == 'scheduled' and curr['event_type'] == 'scheduled':
        prev_time = int(prev['time']) - time_offset
        curr_time = int(curr['time']) - time_offset

        if prev_time == curr_time and prev['task_name'] != curr['task_name']:
            preemptions.append({
                'time': curr_time,
                'from': prev['task_name'],
                'to': curr['task_name']
            })

if preemptions:
    print(f"✅ 发现 {len(preemptions)} 次调度:")
    for p in preemptions[:10]:
        print(f"   时间{p['time']}ms: {p['from']} → {p['to']}")
else:
    print("⚠️ 未发现明显的调度切换（可能是因为arrival_offset错开了任务到达）")

print()
print("="*80)
