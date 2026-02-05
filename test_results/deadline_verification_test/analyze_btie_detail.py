#!/usr/bin/env python3
"""
详细分析BTIE调度行为
"""
import json
from collections import defaultdict

def load_trace(trace_file):
    with open(trace_file, 'r') as f:
        return json.load(f)

def analyze_btie_scheduling():
    """分析BTIE的调度决策"""
    print("="*80)
    print("BTIE调度行为详细分析")
    print("="*80)

    # 任务配置（按RM优先级排序）
    tasks = {
        'task_5': {'period': 24, 'wcet': 14, 'deadline': 14, 'priority': 1},
        'task_4': {'period': 25, 'wcet': 15, 'deadline': 15, 'priority': 2},
        'task_1': {'period': 34, 'wcet': 20, 'deadline': 20, 'priority': 3},
        'task_2': {'period': 45, 'wcet': 27, 'deadline': 27, 'priority': 4},
        'task_3': {'period': 47, 'wcet': 28, 'deadline': 28, 'priority': 5},
        'task_0': {'period': 50, 'wcet': 30, 'deadline': 30, 'priority': 6},
    }

    print("\n任务配置（按RM优先级）：")
    print(f"{'任务':<10} {'周期':<8} {'WCET':<8} {'Deadline':<10} {'RM优先级'}")
    print("-" * 60)
    for task_name in sorted(tasks.keys(), key=lambda x: tasks[x]['priority']):
        t = tasks[task_name]
        print(f"{task_name:<10} {t['period']:<8} {t['wcet']:<8} {t['deadline']:<10} {t['priority']}")

    # 加载追踪文件
    data = load_trace('trace_btie_stress.json')
    events = data['events']

    # 分析前100个事件
    print(f"\n前100个事件：")
    print(f"{'时间':<8} {'事件':<15} {'任务':<10} {'到达':<8} {'能量(J)':<10}")
    print("-" * 70)

    for event in events[:100]:
        time = event.get('time')
        etype = event.get('event_type')
        task = event.get('task_name', '')
        arrival = event.get('arrival_time', '')
        energy = event.get('current_energy_mJ', 0) / 1000
        print(f"{time:<8} {etype:<15} {task:<10} {arrival:<8} {energy:<10.3f}")

    # 统计task_1的调度情况
    print(f"\n{'='*80}")
    print("task_1详细分析（高优先级任务，但90% miss）：")
    print(f"{'='*80}")

    task1_arrivals = []
    task1_scheduled = []
    task1_completions = []
    task1_misses = []

    for event in events:
        if event.get('task_name') == 'task_1':
            etype = event.get('event_type')
            time = int(event.get('time'))
            arrival = int(event.get('arrival_time', 0))

            if etype == 'arrival':
                task1_arrivals.append({'time': time, 'arrival': arrival})
            elif etype == 'scheduled':
                task1_scheduled.append({'time': time, 'arrival': arrival})
            elif etype == 'end_instance':
                task1_completions.append({'time': time, 'arrival': arrival})
            elif etype == 'dline_miss':
                task1_misses.append({'time': time, 'arrival': arrival})

    print(f"\ntask_1事件统计：")
    print(f"  到达次数: {len(task1_arrivals)}")
    print(f"  调度次数: {len(task1_scheduled)}")
    print(f"  完成次数: {len(task1_completions)}")
    print(f"  Miss次数: {len(task1_misses)}")

    print(f"\ntask_1前10个实例详情：")
    print(f"{'实例':<8} {'到达时间':<12} {'绝对Deadline':<15} {'是否调度':<12} {'完成时间':<12} {'结果'}")
    print("-" * 80)

    for i, arr_event in enumerate(task1_arrivals[:10]):
        arrival_time = arr_event['arrival']
        absolute_deadline = arrival_time + 20  # deadline = 20ms

        # 检查是否被调度
        scheduled = any(s['arrival'] == arrival_time for s in task1_scheduled)

        # 检查是否完成
        completion = next((c for c in task1_completions if c['arrival'] == arrival_time), None)
        completion_time = completion['time'] if completion else 'N/A'

        # 检查是否miss
        missed = any(m['arrival'] == arrival_time for m in task1_misses)

        result = '✓完成' if completion else ('✗Miss' if missed else '?未知')

        print(f"{i+1:<8} {arrival_time:<12} {absolute_deadline:<15} {'是' if scheduled else '否':<12} "
              f"{completion_time:<12} {result}")

    # 对比其他任务
    print(f"\n{'='*80}")
    print("所有任务的调度统计对比：")
    print(f"{'='*80}")

    task_stats = defaultdict(lambda: {'arrivals': 0, 'scheduled': 0, 'completions': 0, 'misses': 0})

    for event in events:
        task_name = event.get('task_name')
        etype = event.get('event_type')

        if etype == 'arrival':
            task_stats[task_name]['arrivals'] += 1
        elif etype == 'scheduled':
            task_stats[task_name]['scheduled'] += 1
        elif etype == 'end_instance':
            task_stats[task_name]['completions'] += 1
        elif etype == 'dline_miss':
            task_stats[task_name]['misses'] += 1

    print(f"{'任务':<10} {'RM优先级':<10} {'到达':<8} {'调度':<8} {'完成':<8} {'Miss':<8} {'调度率':<10} {'完成率'}")
    print("-" * 90)

    for task_name in sorted(tasks.keys(), key=lambda x: tasks[x]['priority']):
        stats = task_stats[task_name]
        priority = tasks[task_name]['priority']
        arrivals = stats['arrivals']
        scheduled = stats['scheduled']
        completions = stats['completions']
        misses = stats['misses']

        sched_rate = scheduled / arrivals * 100 if arrivals > 0 else 0
        comp_rate = completions / arrivals * 100 if arrivals > 0 else 0

        print(f"{task_name:<10} {priority:<10} {arrivals:<8} {scheduled:<8} {completions:<8} "
              f"{misses:<8} {sched_rate:<10.1f}% {comp_rate:.1f}%")

if __name__ == '__main__':
    analyze_btie_scheduling()
