#!/usr/bin/env python3
"""
V43修复实验测试 - 验证三种调度算法的能量管理
"""

import subprocess
import re
import sys
import os

def run_simulation(scheduler, taskset, duration=50000, timeout=300):
    """运行仿真并捕获输出"""
    config_file = f"/home/devcontainers/PARTSim-project/acceptance_ratio_experiment/config_gpfp_{scheduler}.yml"

    # 检查配置文件是否存在
    if not os.path.exists(config_file):
        print(f"❌ 配置文件不存在: {config_file}")
        return None

    cmd = [
        "/home/devcontainers/PARTSim-project/build/rtsim/rtsim",
        config_file,
        taskset,
        str(duration)
    ]

    print(f"\n{'='*70}")
    print(f"测试 {scheduler.upper()} 调度器")
    print(f"配置: {config_file}")
    print(f"任务集: {taskset}")
    print(f"仿真时长: {duration}ms")
    print(f"{'='*70}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr

        # 保存完整输出到文件
        output_file = f"/tmp/{scheduler}_output.txt"
        with open(output_file, 'w') as f:
            f.write(output)
        print(f"✅ 仿真完成，输出已保存到: {output_file}")

        return output
    except subprocess.TimeoutExpired:
        print(f"❌ {scheduler.upper()} 仿真超时（{timeout}秒）")
        return None
    except Exception as e:
        print(f"❌ 运行{scheduler}时出错: {e}")
        return None

def analyze_output(output, scheduler):
    """分析仿真输出"""
    if not output:
        return None

    results = {
        'scheduler': scheduler.upper(),
        'completion_rate': 0.0,
        'total_schedules': 0,
        'min_energy': float('inf'),
        'max_energy': 0.0,
        'energy_depletion_count': 0,
        'energy_recovery_count': 0,
        'negative_energy': False
    }

    # 提取完成率
    completion_match = re.search(r'完成率.*?(\d+\.?\d*)%', output)
    if completion_match:
        results['completion_rate'] = float(completion_match.group(1))

    # 提取总调度次数
    schedule_match = re.search(r'总调度次数.*?(\d+)', output)
    if schedule_match:
        results['total_schedules'] = int(schedule_match.group(1))

    # 查找所有能量值
    energy_values = re.findall(r'剩余能量.*?(-?\d+\.?\d*)\s*mJ', output)
    energy_values += re.findall(r'当前能量.*?(-?\d+\.?\d*)\s*mJ', output)
    energy_values += re.findall(r'能量.*?(-?\d+\.?\d*)\s*mJ', output)

    if energy_values:
        energy_floats = [float(e) for e in energy_values]
        results['min_energy'] = min(energy_floats)
        results['max_energy'] = max(energy_floats)
        results['negative_energy'] = results['min_energy'] < 0

    # 统计能量耗尽和恢复事件
    results['energy_depletion_count'] = len(re.findall(r'能量耗尽|能量已耗尽|Energy depleted', output))
    results['energy_recovery_count'] = len(re.findall(r'恢复调度|太阳能充电成功', output))

    return results

def print_results(results):
    """打印结果"""
    if not results:
        print("  ❌ 无结果数据")
        return

    print(f"\n{results['scheduler']} 实验结果:")
    print(f"  完成率: {results['completion_rate']:.1f}%")
    print(f"  总调度次数: {results['total_schedules']}")
    print(f"  最小能量: {results['min_energy']:.2f} mJ")
    print(f"  最大能量: {results['max_energy']:.2f} mJ")
    print(f"  能量耗尽事件: {results['energy_depletion_count']} 次")
    print(f"  能量恢复事件: {results['energy_recovery_count']} 次")

    # 判断是否通过
    if results['negative_energy']:
        print(f"  ❌ 失败: 能量出现负值 ({results['min_energy']:.2f} mJ)")
    else:
        print(f"  ✅ 通过: 能量未出现负值")

def main():
    taskset = "/home/devcontainers/PARTSim-project/acceptance_ratio_experiment/tasks/taskset_u0.10_001.yml"
    duration = 50000  # 50秒仿真
    timeout = 300  # 5分钟超时

    schedulers = ['tie', 'tgf', 'btie']
    all_results = []

    for scheduler in schedulers:
        output = run_simulation(scheduler, taskset, duration, timeout)
        results = analyze_output(output, scheduler)

        if results:
            all_results.append(results)
            print_results(results)
        else:
            print(f"\n{scheduler.upper()}: 无法获取结果")

    # 总结
    print(f"\n{'='*70}")
    print("实验总结")
    print(f"{'='*70}")

    if not all_results:
        print("❌ 所有测试都失败了")
        return 1

    all_passed = True
    for result in all_results:
        status = "✅ 通过" if not result['negative_energy'] else "❌ 失败"
        print(f"{result['scheduler']:6s}: {status} (最小能量={result['min_energy']:.2f} mJ, 完成率={result['completion_rate']:.1f}%)")
        if result['negative_energy']:
            all_passed = False

    if all_passed:
        print(f"\n✅ V43修复成功！所有调度器的能量都未出现负值。")
        return 0
    else:
        print(f"\n❌ V43修复不完整，部分调度器的能量仍出现负值。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
