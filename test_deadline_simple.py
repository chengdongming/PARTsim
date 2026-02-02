#!/usr/bin/env python3
"""
简化的约束截止期测试
使用现有的任务生成器，然后修改deadline进行测试
"""
import subprocess
import yaml
import json
import os
import re
from pathlib import Path
from collections import defaultdict

# 配置
TEST_DIR = Path('test_results/deadline_test')
TEST_DIR.mkdir(parents=True, exist_ok=True)

TASK_GENERATOR = './global_task_generator.py'
SIMULATOR = './build/rtsim/rtsim'
CONFIG_TEMPLATE = 'system_config_unified_template.yml'
SIMULATION_TIME = 10000
SYSTEM_CORES = 4

def generate_base_taskset():
    """生成基础任务集"""
    task_file = TEST_DIR / 'base_taskset.yml'

    cmd = [
        'python3', TASK_GENERATOR,
        '-n', '8', '-u', '3.5',  # 提高利用率到3.5
        '-p', '20', '-P', '100',
        '-c', str(SYSTEM_CORES),
        '--seed', '12345',
        '-o', str(task_file)
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"✅ 基础任务集已生成: {task_file}")
        return task_file
    except subprocess.CalledProcessError as e:
        print(f"❌ 生成任务集失败")
        print(f"   stdout: {e.stdout[:200]}")
        print(f"   stderr: {e.stderr[:200]}")
        return None

def modify_deadlines(base_file: Path, d_t_ratio: float) -> Path:
    """修改任务集的deadline - 使用文本替换保持原始格式"""
    with open(base_file, 'r') as f:
        content = f.read()

    # 使用正则表达式替换deadline
    # 匹配 "deadline: 数字" 并根据iat计算新的deadline
    lines = content.split('\n')
    new_lines = []
    current_iat = None

    for line in lines:
        # 检测iat行
        iat_match = re.search(r'iat:\s*(\d+)', line)
        if iat_match:
            current_iat = int(iat_match.group(1))
            new_lines.append(line)
            continue

        # 检测deadline行并修改
        deadline_match = re.search(r'(deadline:)\s*(\d+)', line)
        if deadline_match and current_iat:
            new_deadline = int(current_iat * d_t_ratio)
            new_line = re.sub(r'deadline:\s*\d+', f'deadline: {new_deadline}', line)
            new_lines.append(new_line)
            print(f"  修改: iat={current_iat}, deadline={deadline_match.group(2)} -> {new_deadline} (D/T={d_t_ratio})")
            continue

        new_lines.append(line)

    # 保存修改后的任务集
    output_file = TEST_DIR / f'taskset_dt{d_t_ratio}.yml'
    with open(output_file, 'w') as f:
        f.write('\n'.join(new_lines))

    return output_file

def modify_config(algorithm: str, battery: float) -> Path:
    """修改配置文件"""
    with open(CONFIG_TEMPLATE, 'r') as f:
        content = f.read()

    content = re.sub(r'scheduler:\s*\w+', f'scheduler: {algorithm}', content)
    content = re.sub(r'max_energy:\s*[\d.]+', f'max_energy: {battery}', content)
    content = re.sub(r'use_real_solar_data:\s*\w+', 'use_real_solar_data: false', content)

    if 'initial_energy_ratio:' in content:
        content = re.sub(r'initial_energy_ratio:\s*[\d.]+', 'initial_energy_ratio: 0.5', content)
    elif 'initial_energy:' in content:
        content = re.sub(r'initial_energy:\s*[\d.]+', f'initial_energy: {battery * 0.5}', content)

    config_file = TEST_DIR / f'config_{algorithm}_{battery}.yml'
    with open(config_file, 'w') as f:
        f.write(content)

    return config_file

def run_simulation(algorithm: str, battery: float, task_file: Path) -> Path:
    """运行仿真"""
    config_file = modify_config(algorithm, battery)
    trace_file = TEST_DIR / f'trace_{algorithm}_{battery}_{task_file.stem}.json'

    env = os.environ.copy()
    lib_path = os.path.abspath('./build/librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [SIMULATOR, str(config_file), str(task_file), str(SIMULATION_TIME), '-t', str(trace_file)]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=60)
        return trace_file
    except Exception as e:
        print(f"  ⚠️ 仿真失败: {e}")
        return None

def analyze_trace(trace_file: Path):
    """分析trace文件"""
    if not trace_file or not trace_file.exists():
        return None

    with open(trace_file, 'r') as f:
        data = json.load(f)

    events = data.get('events', [])
    if not events:
        return None

    stats = {
        'arrivals': 0,
        'completions': 0,
        'deadline_misses': 0
    }

    for event in events:
        etype = event.get('event_type')
        if etype == 'arrival':
            stats['arrivals'] += 1
        elif etype == 'end_instance':  # 修正：使用end_instance而不是completion
            stats['completions'] += 1
        elif etype == 'dline_miss':  # 修正：使用dline_miss而不是deadline_miss
            stats['deadline_misses'] += 1

    if stats['arrivals'] > 0:
        stats['failure_rate'] = stats['deadline_misses'] / stats['arrivals']
        stats['completion_rate'] = stats['completions'] / stats['arrivals']
    else:
        stats['failure_rate'] = 0
        stats['completion_rate'] = 0

    return stats

def main():
    print("=" * 80)
    print("🧪 约束截止期测试 (简化版)")
    print("=" * 80)

    # 1. 生成基础任务集
    print("\n📦 步骤1: 生成基础任务集")
    base_file = generate_base_taskset()
    if not base_file:
        print("❌ 无法生成基础任务集，测试终止")
        return

    # 2. 创建不同D/T比例的任务集
    print("\n📦 步骤2: 创建不同D/T比例的任务集")
    d_t_ratios = [1.0, 0.8, 0.6]
    task_files = {}

    for ratio in d_t_ratios:
        print(f"\n  创建 D/T={ratio} 的任务集:")
        task_files[ratio] = modify_deadlines(base_file, ratio)

    # 3. 运行测试
    print("\n📦 步骤3: 运行仿真测试")
    algorithms = ['gpfp_tie', 'gpfp_tgf', 'gpfp_btie']
    batteries = [10.0, 15.0, 25.0]  # 测试多个电池容量

    results = []

    for ratio, task_file in task_files.items():
        print(f"\n  测试 D/T={ratio}:")
        for algo in algorithms:
            for battery in batteries:
                print(f"    {algo} @ {battery}J ... ", end='', flush=True)
                trace_file = run_simulation(algo, battery, task_file)
                stats = analyze_trace(trace_file)

                if stats:
                    print(f"✅ (失败率: {stats['failure_rate']:.2%}, miss: {stats['deadline_misses']})")
                    results.append({
                        'algorithm': algo,
                        'battery': battery,
                        'd_t_ratio': ratio,
                        **stats
                    })
                else:
                    print("⚠️ (无数据)")

    # 4. 生成报告
    print("\n" + "=" * 80)
    print("📊 测试结果")
    print("=" * 80)

    if results:
        print(f"\n{'D/T比例':<10} {'算法':<15} {'失败率':<12} {'Deadline Miss':<15} {'完成率':<12}")
        print("-" * 80)
        for r in results:
            print(f"{r['d_t_ratio']:<10.1f} {r['algorithm']:<15} {r['failure_rate']:<12.2%} "
                  f"{r['deadline_misses']:<15} {r['completion_rate']:<12.2%}")

        # 保存结果
        results_file = TEST_DIR / 'results.json'
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n💾 结果已保存: {results_file}")
    else:
        print("\n❌ 没有有效的测试结果")

    print("\n" + "=" * 80)
    print("✅ 测试完成")
    print("=" * 80)

if __name__ == '__main__':
    main()
