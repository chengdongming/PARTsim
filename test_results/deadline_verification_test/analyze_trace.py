#!/usr/bin/env python3
"""
分析追踪文件，验证调度逻辑
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

def load_trace(trace_file):
    """加载追踪文件"""
    with open(trace_file, 'r') as f:
        return json.load(f)

def analyze_trace(trace_file, task_config):
    """分析追踪文件"""
    print(f"\n{'='*80}")
    print(f"分析追踪文件: {trace_file.name}")
    print(f"{'='*80}")

    data = load_trace(trace_file)
    events = data.get('events', [])

    # 统计信息
    stats = {
        'arrivals': defaultdict(int),
        'scheduled': defaultdict(int),
        'completions': defaultdict(int),
        'deadline_misses': defaultdict(int),
    }

    # 任务实例追踪
    instances = defaultdict(list)  # task_name -> [(arrival, deadline, completion)]

    for event in events:
        etype = event.get('event_type')
        task_name = event.get('task_name')
        time = int(event.get('time'))
        arrival_time = int(event.get('arrival_time', 0))

        if etype == 'arrival':
            stats['arrivals'][task_name] += 1
            # 计算deadline
            period = task_config[task_name]['period']
            relative_deadline = task_config[task_name]['deadline']
            absolute_deadline = arrival_time + relative_deadline
            instances[task_name].append({
                'arrival': arrival_time,
                'deadline': absolute_deadline,
                'completion': None,
                'scheduled_times': []
            })

        elif etype == 'scheduled':
            stats['scheduled'][task_name] += 1
            # 记录调度时间
            for inst in reversed(instances[task_name]):
                if inst['arrival'] == arrival_time and inst['completion'] is None:
                    inst['scheduled_times'].append(time)
                    break

        elif etype == 'end_instance':
            stats['completions'][task_name] += 1
            # 记录完成时间
            for inst in reversed(instances[task_name]):
                if inst['arrival'] == arrival_time and inst['completion'] is None:
                    inst['completion'] = time
                    break

        elif etype == 'dline_miss':
            stats['deadline_misses'][task_name] += 1

    # 打印统计信息
    print(f"\n📊 事件统计:")
    print(f"{'任务':<12} {'到达':<8} {'调度':<8} {'完成':<8} {'Miss':<8}")
    print("-" * 50)
    for task_name in sorted(task_config.keys()):
        print(f"{task_name:<12} {stats['arrivals'][task_name]:<8} "
              f"{stats['scheduled'][task_name]:<8} {stats['completions'][task_name]:<8} "
              f"{stats['deadline_misses'][task_name]:<8}")

    # 检查deadline miss
    print(f"\n🔍 Deadline验证:")
    total_instances = 0
    total_misses = 0

    for task_name in sorted(task_config.keys()):
        task_instances = instances[task_name]
        task_misses = 0

        for inst in task_instances:
            total_instances += 1
            if inst['completion'] is not None:
                if inst['completion'] > inst['deadline']:
                    task_misses += 1
                    total_misses += 1
                    if task_misses <= 3:  # 只显示前3个miss
                        print(f"  ❌ {task_name}: 到达={inst['arrival']}, "
                              f"截止={inst['deadline']}, 完成={inst['completion']} "
                              f"(超时{inst['completion'] - inst['deadline']}ms)")

        if task_misses == 0:
            print(f"  ✅ {task_name}: 所有{len(task_instances)}个实例都在deadline内完成")
        else:
            print(f"  ⚠️  {task_name}: {task_misses}/{len(task_instances)} 个实例超时")

    print(f"\n总计: {total_misses}/{total_instances} 个实例超时 "
          f"({100*total_misses/total_instances if total_instances > 0 else 0:.1f}%)")

    # 验证前几个调度决策
    print(f"\n🔍 前10个调度决策验证:")
    scheduled_events = [e for e in events if e.get('event_type') == 'scheduled'][:10]
    for i, event in enumerate(scheduled_events, 1):
        task_name = event.get('task_name')
        time = event.get('time')
        arrival = event.get('arrival_time')
        energy_ok = event.get('energy_sufficient', True)
        print(f"  {i}. t={time}ms: {task_name} (到达={arrival}, 能量={'✓' if energy_ok else '✗'})")

    return stats, instances

def main():
    # 任务配置
    task_config = {
        'task_0': {'period': 51, 'deadline': 36, 'wcet': 30, 'workload': 'bzip2'},
        'task_1': {'period': 31, 'deadline': 22, 'wcet': 18, 'workload': 'bzip2'},
        'task_2': {'period': 37, 'deadline': 26, 'wcet': 22, 'workload': 'bzip2'},
        'task_3': {'period': 47, 'deadline': 33, 'wcet': 28, 'workload': 'control'},
    }

    print("\n" + "="*80)
    print("约束截止期调度逻辑验证")
    print("="*80)
    print(f"\n任务配置 (D/T=0.7):")
    print(f"{'任务':<12} {'周期T':<8} {'截止期D':<10} {'WCET':<8} {'D/T':<8} {'RM优先级'}")
    print("-" * 70)

    # 按RM优先级排序（周期从小到大）
    sorted_tasks = sorted(task_config.items(), key=lambda x: x[1]['period'])
    for priority, (task_name, config) in enumerate(sorted_tasks, 1):
        d_t_ratio = config['deadline'] / config['period']
        print(f"{task_name:<12} {config['period']:<8} {config['deadline']:<10} "
              f"{config['wcet']:<8} {d_t_ratio:<8.2f} {priority}")

    # 分析三个算法的追踪文件
    test_dir = Path(__file__).parent
    algorithms = ['tie', 'tgf', 'btie']

    all_results = {}
    for algo in algorithms:
        trace_file = test_dir / f'trace_{algo}.json'
        if trace_file.exists():
            stats, instances = analyze_trace(trace_file, task_config)
            all_results[algo] = (stats, instances)
        else:
            print(f"\n⚠️  追踪文件不存在: {trace_file}")

    # 对比总结
    print(f"\n{'='*80}")
    print("📊 算法对比总结")
    print(f"{'='*80}")
    print(f"{'算法':<10} {'总到达':<10} {'总完成':<10} {'总Miss':<10} {'Miss率':<10}")
    print("-" * 60)

    for algo in algorithms:
        if algo in all_results:
            stats, instances = all_results[algo]
            total_arrivals = sum(stats['arrivals'].values())
            total_completions = sum(stats['completions'].values())
            total_misses = sum(stats['deadline_misses'].values())

            # 从实例中计算实际miss
            actual_misses = 0
            for task_instances in instances.values():
                for inst in task_instances:
                    if inst['completion'] and inst['completion'] > inst['deadline']:
                        actual_misses += 1

            miss_rate = actual_misses / total_arrivals if total_arrivals > 0 else 0
            print(f"{algo.upper():<10} {total_arrivals:<10} {total_completions:<10} "
                  f"{actual_misses:<10} {miss_rate:<10.2%}")

if __name__ == '__main__':
    main()
