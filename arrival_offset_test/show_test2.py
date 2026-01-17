import json

with open('arrival_offset_test/test2_trace.json', 'r') as f:
    data = json.load(f)
    events = data['events']

print('='*80)
print('新测试场景：手动模拟 vs 实际结果')
print('='*80)
print()

print('【任务配置】')
print('task_A: period=400ms, wcet=100ms, arrival_offset=0ms (优先级最高)')
print('task_B: period=600ms, wcet=150ms, arrival_offset=150ms')
print('task_C: period=800ms, wcet=200ms, arrival_offset=300ms (优先级最低)')
print()

print('【实际事件时间线】(前30个)')
print('时间     | 事件类型            | 任务')
print('-'*50)

for e in events[:30]:
    time = int(e['time']) - 43200000
    etype = e['event_type']
    task = e['task_name']
    print(f'{time:<8} | {etype:<20} | {task}')

print()
