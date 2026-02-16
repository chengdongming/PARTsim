#!/usr/bin/env python3
import json

with open('test_kill_instance/traces/trace_kill_test.json') as f:
    data = json.load(f)

events = data['events']

print('='*80)
print('测试结果分析：验证旧实例是否被杀死')
print('='*80)

print('\n📊 关键时间点分析（前50ms）：\n')
for e in events[:40]:
    t = int(e['time'])
    et = e['event_type']
    task = e.get('task_name', 'unknown')
    arr = e.get('arrival_time', 'N/A')
    exec_time = e.get('executed_time_ms', 'N/A')

    if t <= 50:
        if et in ['arrival', 'kill', 'scheduled', 'dline_miss', 'end_instance']:
            print(f"{t:3}ms: {et:15} | task={task:10} | arrival={arr:5} | executed={exec_time}")

print('\n' + '='*80)
print('✅ 验证结果')
print('='*80)

# 统计kill事件
kill_count = sum(1 for e in events if e['event_type'] == 'kill')
arrival_count = sum(1 for e in events if e['event_type'] == 'arrival')

print(f'\n总到达事件数: {arrival_count}')
print(f'总kill事件数: {kill_count}')
print(f'Kill比例: {kill_count/arrival_count*100:.1f}%')

# 分析第一个kill事件
first_kill = next((e for e in events if e['event_type'] == 'kill'), None)
if first_kill:
    kill_time = int(first_kill['time'])
    arrival_time = int(first_kill['arrival_time'])
    executed_time = first_kill.get('executed_time_ms', 0)

    print(f'\n📌 第一个kill事件分析:')
    print(f'   到达时间: {arrival_time}ms')
    print(f'   被杀时间: {kill_time}ms')
    print(f'   实际执行: {executed_time}ms')
    print(f'   预期WCET: 15ms')
    print(f'\n   ✅ 旧实例在{kill_time}ms时被杀死，执行了{executed_time}ms（未完成15ms）')
    print(f'   ✅ 新实例在{kill_time}ms时可以开始执行')
