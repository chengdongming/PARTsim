#!/usr/bin/env python3
"""
测试约束截止期 (Constrained Deadline: D < T)
验证调度器在截止期小于周期时的行为
"""
import json
import subprocess
import yaml
import os
import sys
from pathlib import Path
from typing import Dict, Any, List
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from collections import defaultdict

# 配置中文字体
font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
if os.path.exists(font_path):
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['font.family'] = font_prop.get_name()
plt.rcParams['axes.unicode_minus'] = False

# 测试配置
TEST_DIR = Path('test_results/constrained_deadline_test')
TEST_DIR.mkdir(parents=True, exist_ok=True)

SIMULATOR = './build/rtsim/rtsim'
CONFIG_TEMPLATE = 'system_config_unified_template.yml'
SIMULATION_TIME = 10000  # 10秒

class TaskSetGenerator:
    """任务集生成器"""

    @staticmethod
    def generate_task_set(scenario_name: str, d_t_ratio: float) -> Dict:
        """
        生成任务集

        参数:
            scenario_name: 场景名称
            d_t_ratio: D/T比例 (例如 1.0表示D=T, 0.8表示D=0.8T)
        """
        if scenario_name == "baseline":
            # 基准场景: D = T (隐式截止期)
            tasks = [
                {
                    'name': 'T1',
                    'period': 50,
                    'wcet': 10,
                    'deadline': 50,
                    'energy_per_cycle': 5.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T2',
                    'period': 100,
                    'wcet': 20,
                    'deadline': 100,
                    'energy_per_cycle': 8.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T3',
                    'period': 150,
                    'wcet': 25,
                    'deadline': 150,
                    'energy_per_cycle': 10.0,
                    'workload_type': 'fixed'
                }
            ]
        elif scenario_name == "constrained":
            # 约束截止期场景: D < T
            tasks = [
                {
                    'name': 'T1',
                    'period': 50,
                    'wcet': 10,
                    'deadline': int(50 * d_t_ratio),
                    'energy_per_cycle': 5.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T2',
                    'period': 100,
                    'wcet': 20,
                    'deadline': int(100 * d_t_ratio),
                    'energy_per_cycle': 8.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T3',
                    'period': 150,
                    'wcet': 25,
                    'deadline': int(150 * d_t_ratio),
                    'energy_per_cycle': 10.0,
                    'workload_type': 'fixed'
                }
            ]
        elif scenario_name == "mixed":
            # 混合场景: 部分任务D<T, 部分D=T
            tasks = [
                {
                    'name': 'T1',
                    'period': 50,
                    'wcet': 10,
                    'deadline': 50,  # D = T
                    'energy_per_cycle': 5.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T2',
                    'period': 100,
                    'wcet': 20,
                    'deadline': int(100 * d_t_ratio),  # D < T
                    'energy_per_cycle': 8.0,
                    'workload_type': 'fixed'
                },
                {
                    'name': 'T3',
                    'period': 150,
                    'wcet': 25,
                    'deadline': int(150 * d_t_ratio),  # D < T
                    'energy_per_cycle': 10.0,
                    'workload_type': 'fixed'
                }
            ]
        else:
            raise ValueError(f"Unknown scenario: {scenario_name}")

        return {'tasks': tasks}

    @staticmethod
    def calculate_utilization(task_set: Dict) -> float:
        """计算任务集利用率"""
        util = sum(task['wcet'] / task['period'] for task in task_set['tasks'])
        return util

def create_test_config(algorithm: str, battery_capacity: float, output_file: Path):
    """创建测试配置文件"""
    with open(CONFIG_TEMPLATE, 'r') as f:
        content = f.read()

    # 修改配置
    content = content.replace('scheduler: gpfp_tie', f'scheduler: gpfp_{algorithm.lower()}')
    content = content.replace('initial_energy: 100.0', f'initial_energy: {battery_capacity * 0.5}')
    content = content.replace('max_energy: 1000.0', f'max_energy: {battery_capacity}')
    content = content.replace('use_real_solar_data: true', 'use_real_solar_data: false')

    with open(output_file, 'w') as f:
        f.write(content)

def run_simulation(algorithm: str, battery_capacity: float, task_file: Path,
                   config_file: Path, trace_file: Path) -> bool:
    """运行仿真"""
    create_test_config(algorithm, battery_capacity, config_file)

    env = os.environ.copy()
    lib_path = os.path.abspath('./build/librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [SIMULATOR, str(config_file), str(task_file), str(SIMULATION_TIME), '-t', str(trace_file)]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=60)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  ⚠️ 仿真失败: {algorithm}, battery={battery_capacity}J")
        return False

def analyze_trace(trace_file: Path) -> Dict[str, Any]:
    """分析trace文件"""
    if not trace_file.exists():
        return None

    with open(trace_file, 'r') as f:
        data = json.load(f)

    events = data.get('events', [])
    if not events:
        return None

    # 统计信息
    stats = {
        'total_arrivals': 0,
        'total_completions': 0,
        'total_deadline_misses': 0,
        'task_stats': defaultdict(lambda: {
            'arrivals': 0,
            'completions': 0,
            'deadline_misses': 0,
            'response_times': []
        })
    }

    # 跟踪每个任务实例
    task_instances = {}  # (task_name, arrival_time) -> {'arrival': time, 'completion': time, 'deadline': time}

    for event in events:
        etype = event.get('event_type')
        task_name = event.get('task_name')
        time = float(event.get('time', 0))
        arrival_time = event.get('arrival_time', '0')

        instance_key = (task_name, arrival_time)

        if etype == 'arrival':
            stats['total_arrivals'] += 1
            stats['task_stats'][task_name]['arrivals'] += 1

            # 记录到达信息
            if instance_key not in task_instances:
                task_instances[instance_key] = {
                    'arrival': time,
                    'completion': None,
                    'deadline': None
                }

        elif etype == 'completion':
            stats['total_completions'] += 1
            stats['task_stats'][task_name]['completions'] += 1

            # 记录完成时间
            if instance_key in task_instances:
                task_instances[instance_key]['completion'] = time
                arrival = task_instances[instance_key]['arrival']
                response_time = time - arrival
                stats['task_stats'][task_name]['response_times'].append(response_time)

        elif etype == 'deadline_miss':
            stats['total_deadline_misses'] += 1
            stats['task_stats'][task_name]['deadline_misses'] += 1

    # 计算平均响应时间
    for task_name, task_stat in stats['task_stats'].items():
        if task_stat['response_times']:
            task_stat['avg_response_time'] = np.mean(task_stat['response_times'])
            task_stat['max_response_time'] = np.max(task_stat['response_times'])
        else:
            task_stat['avg_response_time'] = 0
            task_stat['max_response_time'] = 0

    # 计算失败率
    if stats['total_arrivals'] > 0:
        stats['failure_rate'] = stats['total_deadline_misses'] / stats['total_arrivals']
    else:
        stats['failure_rate'] = 0

    return stats

def run_test_suite():
    """运行完整测试套件"""
    print("=" * 80)
    print("🧪 约束截止期测试 (Constrained Deadline Test)")
    print("=" * 80)

    # 测试参数
    algorithms = ['TIE', 'TGF', 'BTIE']
    battery_capacities = [10.0, 25.0, 40.0]
    scenarios = [
        ('baseline', 1.0, 'D=T (隐式截止期)'),
        ('constrained', 0.8, 'D=0.8T (约束截止期)'),
        ('constrained', 0.6, 'D=0.6T (紧迫截止期)'),
        ('mixed', 0.7, '混合 (部分D<T)')
    ]

    results = []

    total_tests = len(algorithms) * len(battery_capacities) * len(scenarios)
    test_count = 0

    print(f"\n📋 测试计划: {total_tests} 个测试")
    print(f"   算法: {', '.join(algorithms)}")
    print(f"   电池容量: {', '.join(str(b)+'J' for b in battery_capacities)}")
    print(f"   场景: {len(scenarios)} 种")

    for scenario_name, d_t_ratio, scenario_desc in scenarios:
        print(f"\n{'='*80}")
        print(f"📦 场景: {scenario_desc}")
        print(f"{'='*80}")

        # 生成任务集
        task_set = TaskSetGenerator.generate_task_set(scenario_name, d_t_ratio)
        utilization = TaskSetGenerator.calculate_utilization(task_set)

        print(f"\n任务集信息:")
        print(f"  利用率: {utilization:.2f}")
        print(f"  任务数: {len(task_set['tasks'])}")
        for task in task_set['tasks']:
            d_t = task['deadline'] / task['period']
            print(f"    {task['name']}: T={task['period']}ms, D={task['deadline']}ms, "
                  f"C={task['wcet']}ms, D/T={d_t:.2f}")

        # 保存任务集
        task_file = TEST_DIR / f'tasks_{scenario_name}_{d_t_ratio}.yml'
        with open(task_file, 'w') as f:
            yaml.dump(task_set, f)

        # 运行测试
        for algorithm in algorithms:
            for battery in battery_capacities:
                test_count += 1
                print(f"\n  [{test_count}/{total_tests}] {algorithm} @ {battery}J ... ", end='', flush=True)

                config_file = TEST_DIR / f'config_{algorithm}_{battery}_{scenario_name}_{d_t_ratio}.yml'
                trace_file = TEST_DIR / f'trace_{algorithm}_{battery}_{scenario_name}_{d_t_ratio}.json'

                success = run_simulation(algorithm, battery, task_file, config_file, trace_file)

                if success:
                    stats = analyze_trace(trace_file)
                    if stats:
                        results.append({
                            'algorithm': algorithm,
                            'battery': battery,
                            'scenario': scenario_desc,
                            'scenario_name': scenario_name,
                            'd_t_ratio': d_t_ratio,
                            'utilization': utilization,
                            **stats
                        })
                        print(f"✅ (失败率: {stats['failure_rate']:.2%}, "
                              f"deadline miss: {stats['total_deadline_misses']})")
                    else:
                        print("⚠️ (无trace数据)")
                else:
                    print("❌")

    return results

def generate_report(results: List[Dict]):
    """生成测试报告"""
    print(f"\n{'='*80}")
    print("📊 测试结果分析")
    print(f"{'='*80}")

    if not results:
        print("❌ 没有测试结果")
        return

    # 按场景分组
    scenarios = {}
    for r in results:
        scenario = r['scenario']
        if scenario not in scenarios:
            scenarios[scenario] = []
        scenarios[scenario].append(r)

    # 为每个场景生成报告
    for scenario, scenario_results in scenarios.items():
        print(f"\n## {scenario}")
        print("-" * 80)

        # 按算法分组
        by_algo = defaultdict(list)
        for r in scenario_results:
            by_algo[r['algorithm']].append(r)

        # 打印表格
        print(f"\n{'算法':<8} {'电池(J)':<10} {'失败率':<12} {'Deadline Miss':<15} {'完成率':<12}")
        print("-" * 80)

        for algo in ['TIE', 'TGF', 'BTIE']:
            if algo in by_algo:
                for r in sorted(by_algo[algo], key=lambda x: x['battery']):
                    completion_rate = r['total_completions'] / r['total_arrivals'] if r['total_arrivals'] > 0 else 0
                    print(f"{algo:<8} {r['battery']:<10.1f} {r['failure_rate']:<12.2%} "
                          f"{r['total_deadline_misses']:<15} {completion_rate:<12.2%}")

        # 任务级别统计
        print(f"\n任务级别统计:")
        for r in scenario_results[:3]:  # 只显示第一个电池容量的结果
            if r['battery'] == scenario_results[0]['battery']:
                print(f"\n  {r['algorithm']} @ {r['battery']}J:")
                for task_name, task_stat in r['task_stats'].items():
                    print(f"    {task_name}: 到达={task_stat['arrivals']}, "
                          f"完成={task_stat['completions']}, "
                          f"miss={task_stat['deadline_misses']}, "
                          f"平均响应={task_stat['avg_response_time']:.2f}ms")

def plot_comparison(results: List[Dict]):
    """绘制对比图"""
    if not results:
        return

    print(f"\n📈 生成对比图表...")

    # 准备数据
    scenarios = sorted(set(r['scenario'] for r in results))
    algorithms = ['TIE', 'TGF', 'BTIE']

    fig, axes = plt.subplots(2, 2, figsize=(16, 12), dpi=150)

    # 子图1: 不同场景下的失败率对比
    ax1 = axes[0, 0]
    for algo in algorithms:
        scenario_failure_rates = []
        for scenario in scenarios:
            algo_results = [r for r in results if r['algorithm'] == algo and r['scenario'] == scenario]
            if algo_results:
                avg_failure = np.mean([r['failure_rate'] for r in algo_results])
                scenario_failure_rates.append(avg_failure)
            else:
                scenario_failure_rates.append(0)

        x = np.arange(len(scenarios))
        ax1.plot(x, scenario_failure_rates, marker='o', label=algo, linewidth=2, markersize=8)

    ax1.set_xlabel('场景', fontsize=11, fontweight='bold')
    ax1.set_ylabel('平均失败率', fontsize=11, fontweight='bold')
    ax1.set_title('不同场景下的失败率对比', fontsize=13, fontweight='bold', pad=10)
    ax1.set_xticks(range(len(scenarios)))
    ax1.set_xticklabels(scenarios, rotation=15, ha='right', fontsize=9)
    ax1.legend(loc='best', fontsize=10)
    ax1.grid(True, alpha=0.3, linestyle='--')

    # 子图2: 电池容量对失败率的影响
    ax2 = axes[0, 1]
    batteries = sorted(set(r['battery'] for r in results))

    # 选择一个有代表性的场景
    target_scenario = scenarios[1] if len(scenarios) > 1 else scenarios[0]

    for algo in algorithms:
        failure_rates = []
        for battery in batteries:
            algo_results = [r for r in results if r['algorithm'] == algo and
                          r['battery'] == battery and r['scenario'] == target_scenario]
            if algo_results:
                failure_rates.append(algo_results[0]['failure_rate'])
            else:
                failure_rates.append(0)

        ax2.plot(batteries, failure_rates, marker='s', label=algo, linewidth=2, markersize=8)

    ax2.set_xlabel('电池容量 (J)', fontsize=11, fontweight='bold')
    ax2.set_ylabel('失败率', fontsize=11, fontweight='bold')
    ax2.set_title(f'电池容量对失败率的影响\n({target_scenario})', fontsize=13, fontweight='bold', pad=10)
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3, linestyle='--')

    # 子图3: Deadline miss数量对比
    ax3 = axes[1, 0]
    width = 0.25
    x = np.arange(len(scenarios))

    for i, algo in enumerate(algorithms):
        miss_counts = []
        for scenario in scenarios:
            algo_results = [r for r in results if r['algorithm'] == algo and r['scenario'] == scenario]
            if algo_results:
                avg_miss = np.mean([r['total_deadline_misses'] for r in algo_results])
                miss_counts.append(avg_miss)
            else:
                miss_counts.append(0)

        ax3.bar(x + i*width, miss_counts, width, label=algo, alpha=0.8)

    ax3.set_xlabel('场景', fontsize=11, fontweight='bold')
    ax3.set_ylabel('平均Deadline Miss数量', fontsize=11, fontweight='bold')
    ax3.set_title('Deadline Miss数量对比', fontsize=13, fontweight='bold', pad=10)
    ax3.set_xticks(x + width)
    ax3.set_xticklabels(scenarios, rotation=15, ha='right', fontsize=9)
    ax3.legend(loc='best', fontsize=10)
    ax3.grid(True, alpha=0.3, linestyle='--', axis='y')

    # 子图4: D/T比例对失败率的影响
    ax4 = axes[1, 1]
    d_t_ratios = sorted(set(r['d_t_ratio'] for r in results))

    for algo in algorithms:
        failure_rates = []
        for ratio in d_t_ratios:
            algo_results = [r for r in results if r['algorithm'] == algo and r['d_t_ratio'] == ratio]
            if algo_results:
                avg_failure = np.mean([r['failure_rate'] for r in algo_results])
                failure_rates.append(avg_failure)
            else:
                failure_rates.append(0)

        ax4.plot(d_t_ratios, failure_rates, marker='^', label=algo, linewidth=2, markersize=8)

    ax4.set_xlabel('D/T 比例', fontsize=11, fontweight='bold')
    ax4.set_ylabel('平均失败率', fontsize=11, fontweight='bold')
    ax4.set_title('D/T比例对失败率的影响', fontsize=13, fontweight='bold', pad=10)
    ax4.legend(loc='best', fontsize=10)
    ax4.grid(True, alpha=0.3, linestyle='--')
    ax4.axvline(x=1.0, color='red', linestyle='--', linewidth=1, alpha=0.5, label='D=T')

    plt.tight_layout()
    output_file = TEST_DIR / 'comparison_results.png'
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"✅ 对比图已保存: {output_file}")

def main():
    # 运行测试
    results = run_test_suite()

    # 生成报告
    generate_report(results)

    # 绘制对比图
    plot_comparison(results)

    # 保存结果
    results_file = TEST_DIR / 'test_results.json'
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 结果已保存: {results_file}")

    print(f"\n{'='*80}")
    print("✅ 测试完成！")
    print(f"{'='*80}")

if __name__ == '__main__':
    main()
