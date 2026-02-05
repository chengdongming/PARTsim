#!/usr/bin/env python3
"""
分析压力测试追踪文件
"""
import json
from pathlib import Path
from collections import defaultdict

def load_trace(trace_file):
    """加载追踪文件"""
    with open(trace_file, 'r') as f:
        return json.load(f)

def analyze_stress_trace(trace_file):
    """分析压力测试追踪"""
    print(f"\n{'='*80}")
    print(f"分析: {trace_file.name}")
    print(f"{'='*80}")

    data = load_trace(trace_file)
    events = data.get('events', [])

    stats = {
        'arrivals': 0,
        'completions': 0,
        'deadline_misses': 0,
        'scheduled': 0,
    }

    task_stats = defaultdict(lambda: {'arrivals': 0, 'completions': 0, 'misses': 0})

    # 能量追踪
    energy_samples = []

    for event in events:
        etype = event.get('event_type')
        task_name = event.get('task_name')
        time = int(event.get('time'))
        energy = event.get('current_energy_mJ', 0) / 1000  # 转换为J

        if etype == 'arrival':
            stats['arrivals'] += 1
            task_stats[task_name]['arrivals'] += 1
        elif etype == 'end_instance':
            stats['completions'] += 1
            task_stats[task_name]['completions'] += 1
        elif etype == 'dline_miss':
            stats['deadline_misses'] += 1
            task_stats[task_name]['misses'] += 1
        elif etype == 'scheduled':
            stats['scheduled'] += 1

        if time % 100 == 0:  # 每100ms采样一次能量
            energy_samples.append((time, energy))

    print(f"\n📊 总体统计:")
    print(f"  总到达: {stats['arrivals']}")
    print(f"  总完成: {stats['completions']}")
    print(f"  总调度: {stats['scheduled']}")
    print(f"  Deadline Miss: {stats['deadline_misses']}")
    print(f"  完成率: {stats['completions']/stats['arrivals']*100:.1f}%")
    print(f"  失败率: {stats['deadline_misses']/stats['arrivals']*100:.1f}%")

    print(f"\n📊 各任务统计:")
    print(f"{'任务':<12} {'到达':<8} {'完成':<8} {'Miss':<8} {'完成率':<10}")
    print("-" * 60)
    for task_name in sorted(task_stats.keys()):
        ts = task_stats[task_name]
        completion_rate = ts['completions'] / ts['arrivals'] * 100 if ts['arrivals'] > 0 else 0
        print(f"{task_name:<12} {ts['arrivals']:<8} {ts['completions']:<8} "
              f"{ts['misses']:<8} {completion_rate:<10.1f}%")

    print(f"\n⚡ 能量变化:")
    if energy_samples:
        print(f"  初始能量: {energy_samples[0][1]:.3f}J")
        print(f"  最终能量: {energy_samples[-1][1]:.3f}J")
        print(f"  能量消耗: {energy_samples[0][1] - energy_samples[-1][1]:.3f}J")

        # 显示能量变化趋势
        print(f"\n  能量变化趋势 (每100ms):")
        for time, energy in energy_samples[:10]:
            print(f"    t={time}ms: {energy:.3f}J")

    return stats, task_stats

def main():
    test_dir = Path(__file__).parent

    print("\n" + "="*80)
    print("压力测试结果对比")
    print("="*80)

    algorithms = ['tie', 'tgf', 'btie']
    all_results = {}

    for algo in algorithms:
        trace_file = test_dir / f'trace_{algo}_stress.json'
        if trace_file.exists():
            stats, task_stats = analyze_stress_trace(trace_file)
            all_results[algo] = (stats, task_stats)

    # 对比总结
    print(f"\n{'='*80}")
    print("📊 算法对比总结")
    print(f"{'='*80}")
    print(f"{'算法':<10} {'总到达':<10} {'总完成':<10} {'完成率':<12} {'Miss':<10} {'失败率':<10}")
    print("-" * 70)

    for algo in algorithms:
        if algo in all_results:
            stats, _ = all_results[algo]
            completion_rate = stats['completions'] / stats['arrivals'] * 100 if stats['arrivals'] > 0 else 0
            miss_rate = stats['deadline_misses'] / stats['arrivals'] * 100 if stats['arrivals'] > 0 else 0
            print(f"{algo.upper():<10} {stats['arrivals']:<10} {stats['completions']:<10} "
                  f"{completion_rate:<12.1f}% {stats['deadline_misses']:<10} {miss_rate:<10.1f}%")

if __name__ == '__main__':
    main()
