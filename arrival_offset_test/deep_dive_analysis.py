#!/usr/bin/env python3
import json

with open('arrival_offset_test/test2_trace.json', 'r') as f:
    data = json.load(f)
    events = data['events']

print('='*80)
print('task_C 执行时间深入分析')
print('='*80)
print()

print('【task_C 第1次实例详细时间线】')
print()

# 找出task_C第一次实例的所有事件
first_instance_arrival = None
for e in events:
    if e['task_name'] == 'task_C' and e['event_type'] == 'arrival':
        first_instance_arrival = int(e['time']) - 43200000
        break

scheduled_time = None
end_time = None

print('时间     | 事件类型            | 说明')
print('-'*60)

for e in events:
    time = int(e['time']) - 43200000
    if e['task_name'] == 'task_C':
        etype = e['event_type']

        if time > 500:  # 只看第一次实例（500ms之前）
            break

        if etype == 'arrival':
            print(f'{time:<8} | {etype:<20} | 任务C第1次实例到达')
        elif etype == 'scheduled':
            if scheduled_time is None:
                scheduled_time = time
                print(f'{time:<8} | {etype:<20} | 开始执行')
        elif etype == 'end_instance':
            if end_time is None:
                end_time = time
                execution_time = end_time - scheduled_time
                print(f'{time:<8} | {etype:<20} | 完成 (执行{execution_time}ms)')

print()
print('【关键时间点】')
print(f'到达时间: {first_instance_arrival}ms')
print(f'调度时间: {scheduled_time}ms')
print(f'完成时间: {end_time}ms')
print(f'执行时长: {end_time - scheduled_time}ms')
print()

print('【理论WCET】')
print('task_C wcet = 200ms')
print()

print('【差异分析】')
print(f'预期执行: 200ms')
print(f'实际执行: {end_time - scheduled_time}ms')
print(f'差异: {200 - (end_time - scheduled_time)}ms')
print()

print('【原因分析】')
print('✅ task_C从300ms开始执行')
print('✅ task_A在400ms到达（第2次实例: 0+400=400ms）')
print('✅ task_A优先级更高，立即抢占task_C')
print('✅ task_C被中断，只执行了: 400ms - 300ms = 100ms')
print(f'✅ 实际执行了{end_time - scheduled_time}ms（85ms），误差15ms')
print()
print('【误差来源】')
print('可能的误差来源:')
print('  1. Tick精度问题（可能不是整ms）')
print('  2. 调度开销（抢占、上下文切换）')
print('  3. 事件记录时间精度（385ms可能是384.999ms）')
print('  4. WCET计算方式（可能不是精确的200ms）')
print()

print('='*80)
print('【验证其他任务执行时间】')
print()

tasks_info = {
    'task_A': {'wcet': 100},
    'task_B': {'wcet': 150},
    'task_C': {'wcet': 200}
}

for task_name in ['task_A', 'task_B', 'task_C']:
    print(f'{task_name} (WCET={tasks_info[task_name]["wcet"]}ms):')

    scheduled_times = []
    end_times = []

    for e in events:
        if e['task_name'] == task_name:
            time = int(e['time']) - 43200000
            if e['event_type'] == 'scheduled':
                scheduled_times.append(time)
            elif e['event_type'] == 'end_instance':
                end_times.append(time)

    for i, (s, e) in enumerate(zip(scheduled_times, end_times)):
        exec_time = e - s
        print(f'  实例{i+1}: {s}ms -> {e}ms, 执行{exec_time}ms')
    print()

print('='*80)
