#!/usr/bin/env python3
"""
深入分析arrival_offset问题
检查所有任务实例的到达时间
"""

import json

with open('/home/devcontainers/PARTSim-project/arrival_offset_test/arrival_offset_trace_fixed.json', 'r') as f:
    data = json.load(f)
    events = data['events']

print("="*80)
print("深入分析 arrival_offset 问题")
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
print("任务        | 实例1 | 实例2 | 实例3 | 预期周期")
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

# 分析问题
print("【问题分析】")
print()

# 预期的到达时间（基于arrival_offset）
expected_arrivals = {
    'task_high': [0, 500, 1000],       # period=500, offset=0
    'task_mid':  [200, 1200, 2200],    # period=1000, offset=200
    'task_low':  [100, 1600, 3100]     # period=1500, offset=100
}

print("预期 vs 实际:")
print(f"{'任务':<12} | {'预期到达时间':<30} | {'实际到达时间':<30}")
print("-"*75)

for task in ['task_high', 'task_mid', 'task_low']:
    expected = expected_arrivals[task]
    actual = arrivals.get(task, [])

    exp_str = ", ".join([f"{t}" for t in expected[:len(actual)]])
    act_str = ", ".join([f"{t}" for t in actual])

    print(f"{task:<12} | {exp_str:<30} | {act_str:<30}")

print()
print("❌ 问题: 所有任务都在0ms首次到达，arrival_offset被忽略！")
print()
print("🔍 根本原因分析:")
print("   1. YAML解析器创建PeriodicTask时，没有传递arrival_offset作为phase参数")
print("   2. PeriodicTask构造函数: PeriodicTask(iat, deadline, phase, name, qs)")
print("   3. phase参数就是第一次到达的时间偏移")
print("   4. 我们需要在解析YAML时，将arrival_offset传递给phase参数")
print()

print("="*80)
