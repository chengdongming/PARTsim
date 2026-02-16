#!/usr/bin/env python3
"""
严格验证：手动仿真 vs 真实仿真
"""
import json

print("="*80)
print("严格验证：手动仿真预测 vs 真实仿真结果")
print("="*80)

# 读取真实仿真结果
with open('test_kill_instance/traces/trace_kill_test.json') as f:
    data = json.load(f)
events = data['events']

print("\n📋 验证列表（逐项对比）\n")

# ============ 验证1: 0ms事件 ============
print("【验证1】0ms事件")
print("-" * 60)
manual_pred = """
手动预测:
- arrival事件（实例0）
- scheduled事件（实例0）
"""

# 真实结果
real_0ms = [e for e in events if int(e['time']) == 0]
print(manual_pred)
print("真实结果:")
for e in real_0ms[:5]:
    print(f"  {e['event_type']:15} | {e.get('task_name', 'N/A'):10} | arrival={e.get('arrival_time', 'N/A')}")

# 验证
has_arrival_0 = any(e['event_type'] == 'arrival' for e in real_0ms)
has_scheduled_0 = any(e['event_type'] == 'scheduled' for e in real_0ms)
print(f"\n✅ 通过" if (has_arrival_0 and has_scheduled_0) else "❌ 失败")

# ============ 验证2: 10ms关键事件 ============
print("\n【验证2】10ms关键事件（新实例到达，旧实例被杀）")
print("-" * 60)
manual_pred = """
手动预测:
- arrival事件（实例1，到达时间=10ms）
- dline_miss事件（实例0，到达时间=0ms）
- descheduled事件（实例0，executed_time_ms=10）
- kill事件（实例0，到达时间=0ms）
- 能量: 9995.8 mJ
"""

real_10ms = [e for e in events if int(e['time']) == 10]
print(manual_pred)
print("真实结果:")
for e in real_10ms[:8]:
    et = e['event_type']
    arr = e.get('arrival_time', 'N/A')
    exec_time = e.get('executed_time_ms', 'N/A')
    energy = e.get('current_energy_mJ', 'N/A')
    print(f"  {et:15} | arrival={arr:5} | executed={exec_time} | energy={energy}")

# 验证
has_arrival_10 = any(e['event_type'] == 'arrival' and e.get('arrival_time') == '10' for e in real_10ms)
has_miss_10 = any(e['event_type'] == 'dline_miss' and e.get('arrival_time') == '0' for e in real_10ms)
has_desched_10 = any(e['event_type'] == 'descheduled' and e.get('executed_time_ms') == 10 for e in real_10ms)
has_kill_10 = any(e['event_type'] == 'kill' and e.get('arrival_time') == '0' for e in real_10ms)
energy_10 = next((e['current_energy_mJ'] for e in real_10ms if 'current_energy_mJ' in e), None)

checks = [
    ("arrival(实例1)", has_arrival_10),
    ("dline_miss(实例0)", has_miss_10),
    ("descheduled(exec=10)", has_desched_10),
    ("kill(实例0)", has_kill_10),
    (f"energy=9995.8", energy_10 == 9995.8)
]

print("\n验证结果:")
all_pass = True
for check_name, result in checks:
    print(f"  {'✅' if result else '❌'} {check_name}")
    if not result:
        all_pass = False

# ============ 验证3: 实例执行时间 ============
print("\n【验证3】所有实例的执行时间（应该都是10ms）")
print("-" * 60)
print("手动预测: 每个实例都执行10ms（因为周期=10ms）")
print("真实结果:")

desched_events = [e for e in events if e['event_type'] == 'descheduled']
exec_times = [e['executed_time_ms'] for e in desched_events if 'executed_time_ms' in e]
print(f"  所有执行时间: {exec_times}")

all_10 = all(t == 10 for t in exec_times)
print(f"\n{'✅ 通过' if all_10 else '❌ 失败'}: 所有实例都执行了10ms")

# ============ 验证4: kill事件频率 ============
print("\n【验证4】kill事件频率（应该每10ms一次）")
print("-" * 60)
print("手动预测: 在10ms, 20ms, 30ms, 40ms, 50ms, ... 每个周期到达时发生kill")
print("真实结果:")

kill_events = [e for e in events if e['event_type'] == 'kill']
kill_times = sorted(set(int(e['time']) for e in kill_events))
print(f"  kill事件时间点: {kill_times}")

# 验证kill事件的时间间隔是否为10ms
intervals = [kill_times[i+1] - kill_times[i] for i in range(len(kill_times)-1)]
all_interval_10 = all(i == 10 for i in intervals)
print(f"  kill事件间隔: {intervals}")

kill_freq_correct = all_interval_10
print(f"\n{'✅ 通过' if kill_freq_correct else '❌ 失败'}: kill事件每10ms发生一次")

# ============ 验证5: 统计数据 ============
print("\n【验证5】统计数据")
print("-" * 60)

arrival_count = sum(1 for e in events if e['event_type'] == 'arrival')
kill_count = sum(1 for e in events if e['event_type'] == 'kill')

print(f"手动预测:")
print(f"  - 总到达事件: 10个")
print(f"  - 总kill事件: 9个（最后一个实例没被杀）")
print(f"\n真实结果:")
print(f"  - 总到达事件: {arrival_count}个")
print(f"  - 总kill事件: {kill_count}个")

stats_correct = (arrival_count == 10 and kill_count == 9)
print(f"\n{'✅ 通过' if stats_correct else '❌ 失败'}: 统计数据符合预期")

# ============ 总体验证结果 ============
print("\n" + "="*80)
print("总体验证结果")
print("="*80)

all_checks = [
    ("0ms事件", has_arrival_0 and has_scheduled_0),
    ("10ms关键事件", all(r for _, r in checks)),
    ("实例执行时间", all_10),
    ("kill事件频率", kill_freq_correct),
    ("统计数据", stats_correct)
]

for check_name, result in all_checks:
    print(f"{'✅' if result else '❌'} {check_name}")

final_result = all(r for _, r in all_checks)
print(f"\n{'='*80}")
print(f"{'✅ 所有验证通过！手动预测与真实仿真完全一致！' if final_result else '❌ 部分验证失败'}")
print(f"{'='*80}")
