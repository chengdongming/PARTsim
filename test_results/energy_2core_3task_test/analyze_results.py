#!/usr/bin/env python3
"""
分析三种调度算法（TIE、BTIE、TGF）的仿真结果
"""

import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

def load_trace(json_file):
    """加载JSON追踪文件"""
    with open(json_file, 'r') as f:
        data = json.load(f)
    return data.get('events', [])

def analyze_trace(events):
    """分析追踪事件"""
    stats = {
        'total_tasks_completed': 0,
        'total_deadline_misses': 0,
        'total_scheduled': 0,
        'task_completion_times': defaultdict(list),
        'task_misses': defaultdict(int),
        'energy_events': 0,
        'first_miss_time': None,
        'schedule_events': 0,
        'deschedule_events': 0
    }

    for event in events:
        event_type = event.get('event_type', '')
        task_name = event.get('task_name', 'unknown')
        time_ms = int(event.get('time', 0))

        if event_type == 'end_instance':
            stats['total_tasks_completed'] += 1
            stats['task_completion_times'][task_name].append(time_ms)

        elif event_type == 'dline_miss':
            stats['total_deadline_misses'] += 1
            stats['task_misses'][task_name] += 1
            if stats['first_miss_time'] is None:
                stats['first_miss_time'] = time_ms

        elif event_type == 'scheduled':
            stats['total_scheduled'] += 1
            stats['schedule_events'] += 1

        elif event_type == 'descheduled':
            stats['deschedule_events'] += 1

        elif event_type in ['energy_harvested', 'energy_consumed']:
            stats['energy_events'] += 1

    # 计算最终能量（从最后的energy_consumed事件）
    final_energy = 0.015  # 默认15mJ
    for event in reversed(events):
        if event.get('event_type') == 'energy_consumed':
            final_energy = float(event.get('remaining_energy', 0))
            break

    stats['final_energy_mj'] = final_energy * 1000
    return stats

def print_stats(algorithm, stats):
    """打印统计信息"""
    print(f"\n{'='*60}")
    print(f"📊 {algorithm} 算法统计")
    print(f"{'='*60}")
    print(f"✅ 任务完成数: {stats['total_tasks_completed']}")
    print(f"❌ Deadline Miss数: {stats['total_deadline_misses']}")
    print(f"📅 调度次数: {stats['total_scheduled']}")
    print(f"⏰ 第一个Miss时间: {stats['first_miss_time']} ms" if stats['first_miss_time'] else "⏰ 第一个Miss时间: 无")
    print(f"🔋 最终能量: {stats['final_energy_mj']:.3f} mJ")

    if stats['task_completion_times']:
        print(f"\n各任务完成次数:")
        for task, times in sorted(stats['task_completion_times'].items()):
            print(f"  {task}: {len(times)} 次")

    if stats['task_misses']:
        print(f"\n各任务Miss次数:")
        for task, count in sorted(stats['task_misses'].items()):
            print(f"  {task}: {count} 次")

    # 计算成功率
    total_arrivals = stats['total_tasks_completed'] + stats['total_deadline_misses']
    if total_arrivals > 0:
        success_rate = (stats['total_tasks_completed'] / total_arrivals) * 100
        miss_rate = (stats['total_deadline_misses'] / total_arrivals) * 100
        print(f"\n📈 成功率: {success_rate:.1f}%")
        print(f"📉 Miss率: {miss_rate:.1f}%")

def main():
    result_dir = Path("test_results/energy_2core_3task_test/results_15mj_ARRIVAL_FIXED_NEW")

    algorithms = {
        'TIE': result_dir / "tie_15mj_fixed.json",
        'BTIE': result_dir / "btie_15mj_fixed.json",
        'TGF': result_dir / "tgf_15mj_fixed.json"
    }

    all_stats = {}

    for name, json_file in algorithms.items():
        if not json_file.exists():
            print(f"⚠️ 文件不存在: {json_file}")
            continue

        events = load_trace(json_file)
        stats = analyze_trace(events)
        all_stats[name] = stats
        print_stats(name, stats)

    # 对比总结
    print(f"\n{'='*60}")
    print("📋 三种算法对比总结")
    print(f"{'='*60}")
    print(f"{'算法':<8} {'完成数':<8} {'Miss数':<8} {'成功率':<10} {'首个Miss时间':<15}")
    print(f"{'-'*60}")

    for name in ['TIE', 'BTIE', 'TGF']:
        if name in all_stats:
            stats = all_stats[name]
            total = stats['total_tasks_completed'] + stats['total_deadline_misses']
            success_rate = (stats['total_tasks_completed'] / total * 100) if total > 0 else 0
            first_miss = stats['first_miss_time'] if stats['first_miss_time'] else 'N/A'
            print(f"{name:<8} {stats['total_tasks_completed']:<8} {stats['total_deadline_misses']:<8} {success_rate:<9.1f}% {str(first_miss)+' ms':<15}")

if __name__ == "__main__":
    main()
