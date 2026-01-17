#!/usr/bin/env python3
"""
测试太阳能收集功能 - 理论vs实际对比
测试时间点: 0, 8, 10, 12, 14, 16, 18点
"""

import subprocess
import json
import csv
import os
from pathlib import Path

# PV配置
PV_EFFICIENCY = 0.18
PV_AREA_M2 = 1.0
SIM_DURATION_MS = 2000  # 2秒仿真时长

# NASA太阳能数据文��
SOLAR_DATA_FILE = "data/processed/shenyang_solar_minute.csv"

# 测试时间点（小时）
TEST_HOURS = [0, 8, 10, 12, 14, 16, 18]

def get_irradiance_at_hour(hour):
    """从NASA数据中获取指定小时的辐照度（W/m²）"""
    try:
        with open(SOLAR_DATA_FILE, 'r') as f:
            reader = csv.DictReader(f)
            # 第187天，指定小时的数据
            # 格式: day,minute,irradiance
            target_day = 187
            target_minute = hour * 60  # 小时转换为分钟

            for row in reader:
                day = int(row['day'])
                minute = int(row['minute'])
                if day == target_day and minute == target_minute:
                    return float(row['irradiance'])
    except Exception as e:
        print(f"读取太阳能数据失败: {e}")

    # 如果读取失败，返回默认值
    if 6 <= hour <= 18:
        return 500.0  # 白天默认值
    return 0.0

def calculate_theoretical_energy(hour):
    """计算理论收集能量"""
    irradiance = get_irradiance_at_hour(hour)  # W/m²

    # 功率 (W) = 辐照度 × 效率 × 面积
    power_w = irradiance * PV_EFFICIENCY * PV_AREA_M2

    # 能量 (J) = 功率 × 时间
    time_s = SIM_DURATION_MS / 1000.0
    energy_j = power_w * time_s

    # 收集率 (%)
    collection_rate = (power_w / irradiance * 100) if irradiance > 0 else 0.0

    return {
        'irradiance': irradiance,
        'power_w': power_w,
        'energy_j': energy_j,
        'collection_rate': collection_rate
    }

def create_config_for_hour(hour):
    """为指定小时创建配置文件"""
    time_of_day_ms = hour * 3600000  # 小时转毫秒

    config_template = f"""# =============================================
# EPP调度器测试配置 - {hour:02d}:00
# =============================================

cpu_islands:
  - name: energy_aware_cpus
    numcpus: 3

    kernel:
      scheduler: gpfp_epp
      task_placement: global

      scheduler_params:
        - "strict_priority=true"
        - "enable_energy_recovery=true"

    volts: [0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, 1.08, 1.10, 1.12, 1.14]
    freqs: [7000, 7500, 8000, 8100, 8200, 8300, 8400, 8500, 9000, 9500, 10000, 10500]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 5.0
  max_energy: 1000.0

  day_of_year: 187
  time_of_day_ms: {time_of_day_ms}               # {hour:02d}:00:00

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: {PV_EFFICIENCY}
  pv_area_m2: {PV_AREA_M2}

  unit_time: 1
  enable_energy_recovery: true
  max_recovery_wait_time_ms: 10000

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi

    params:
      - workload: idle
        power_params: [0.00134845, 1.76307e-5, 124.535, 1.00399e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1

      - workload: bzip2
        power_params: [0.00775587, 33.376, 1.54585, 9.53439e-10]
        speed_params: [0.0256054, 2.9809e+6, 0.602631, 8.13712e+9]
        energy_coefficient: 1.2

      - workload: hash
        power_params: [0.00624673, 176.315, 1.72836, 1.77362e-10]
        speed_params: [0.00645628, 3.37134e+6, 7.83177, 93459]
        energy_coefficient: 0.8

      - workload: control
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1

trace:
  enabled: true
  trace_file: "trace_solar_{hour:02d}.json"
  trace_energy: true
  trace_scheduling: true
  trace_preemptions: true
"""

    config_file = f"epp_simulation_test/epp_test_{hour:02d}.yml"
    with open(config_file, 'w') as f:
        f.write(config_template)

    return config_file

def run_simulation(hour):
    """运行指定小时的仿真"""
    config_file = f"epp_simulation_test/epp_test_{hour:02d}.yml"
    task_file = "epp_simulation_test/epp_test_tasks.yml"
    trace_file = f"/tmp/trace_solar_{hour:02d}.json"

    cmd = [
        "./run_sim.sh",
        "-s", config_file,
        "-t", task_file,
        "-d", str(SIM_DURATION_MS),
        "-o", trace_file
    ]

    print(f"\n{'='*60}")
    print(f"运行仿真: {hour:02d}:00")
    print(f"{'='*60}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        # 读取trace文件获取实际收集能量
        try:
            with open(trace_file, 'r') as f:
                trace_data = json.load(f)

            # 从metadata中获取统计信息（如果有）
            actual_harvested = 0.0
            if 'statistics' in trace_data.get('metadata', {}):
                actual_harvested = trace_data['metadata']['statistics'].get('total_energy_harvested', 0.0)

            return {
                'success': True,
                'trace_file': trace_file,
                'actual_harvested': actual_harvested,
                'events': len(trace_data.get('events', []))
            }
        except Exception as e:
            print(f"读取trace文件失败: {e}")
            return {'success': False, 'error': str(e)}

    except subprocess.TimeoutExpired:
        return {'success': False, 'error': '仿真超时'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_energy_from_log(log_output):
    """从日志输出中提取能量收集信息"""
    lines = log_output.split('\n')
    harvested = 0.0

    for line in lines:
        if '收集能量:' in line or '总收集能量:' in line:
            try:
                # 提取数字
                parts = line.split()
                for i, part in enumerate(parts):
                    if 'J' in part and i > 0:
                        try:
                            harvested += float(parts[i-1])
                        except:
                            pass
            except:
                pass

    return harvested

def main():
    print("="*80)
    print("太阳能收集测试 - 理论vs实际对比")
    print("="*80)
    print(f"PV效率: {PV_EFFICIENCY*100}%")
    print(f"PV面积: {PV_AREA_M2} m²")
    print(f"仿真时长: {SIM_DURATION_MS}ms ({SIM_DURATION_MS/1000}秒)")
    print(f"测试时间点: {TEST_HOURS}")
    print("="*80)

    results = []

    for hour in TEST_HOURS:
        # 1. 创建配置文件
        config_file = create_config_for_hour(hour)
        print(f"\n✅ 创建配置文件: {config_file}")

        # 2. 计算理论值
        theory = calculate_theoretical_energy(hour)
        print(f"📐 理论计算:")
        print(f"   辐照度: {theory['irradiance']:.2f} W/m²")
        print(f"   功率: {theory['power_w']:.2f} W")
        print(f"   能量(2s): {theory['energy_j']:.2f} J")
        print(f"   收集率: {theory['collection_rate']:.2f}%")

        # 3. 运行仿真
        sim_result = run_simulation(hour)

        if sim_result['success']:
            # 4. 提取实际收集能量
            # 从trace文件中读取可能不够，我们还需要从日志中提取
            actual = sim_result.get('actual_harvested', 0.0)

            print(f"🔬 实际结果:")
            print(f"   收集能量: {actual:.4f} J")
            print(f"   事件数: {sim_result['events']}")

            # 5. 对比
            error = abs(actual - theory['energy_j'])
            error_pct = (error / theory['energy_j'] * 100) if theory['energy_j'] > 0 else 0

            print(f"📊 对比:")
            print(f"   理论能量: {theory['energy_j']:.4f} J")
            print(f"   实际能量: {actual:.4f} J")
            print(f"   绝对误差: {error:.4f} J")
            print(f"   相对误差: {error_pct:.2f}%")

            results.append({
                'hour': hour,
                'irradiance': theory['irradiance'],
                'theory_power': theory['power_w'],
                'theory_energy': theory['energy_j'],
                'actual_energy': actual,
                'error': error,
                'error_pct': error_pct,
                'events': sim_result['events']
            })
        else:
            print(f"❌ 仿真失败: {sim_result.get('error', '未知错误')}")
            results.append({
                'hour': hour,
                'irradiance': theory['irradiance'],
                'theory_power': theory['power_w'],
                'theory_energy': theory['energy_j'],
                'actual_energy': 0.0,
                'error': theory['energy_j'],
                'error_pct': 100.0,
                'events': 0,
                'error_msg': sim_result.get('error', '未知错误')
            })

    # 生成报告
    print("\n" + "="*80)
    print("测试总结")
    print("="*80)

    print(f"\n{'时间':<8} {'辐照度':<12} {'理论功率':<12} {'理论能量':<12} {'实际能量':<12} {'误差%':<10}")
    print("-" * 80)

    for r in results:
        print(f"{r['hour']:02d}:00   "
              f"{r['irradiance']:>8.2f}    "
              f"{r['theory_power']:>8.2f}    "
              f"{r['theory_energy']:>8.2f}    "
              f"{r['actual_energy']:>8.4f}    "
              f"{r['error_pct']:>6.2f}%  ")

    # 保存到CSV
    with open('solar_collection_results.csv', 'w', newline='') as f:
        fieldnames = ['hour', 'irradiance', 'theory_power', 'theory_energy',
                     'actual_energy', 'error', 'error_pct', 'events']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n✅ 结果已保存到: solar_collection_results.csv")

    # 生成Markdown报告
    with open('SOLAR_COLLECTION_COMPARISON.md', 'w') as f:
        f.write("# 太阳能收集对比报告 - 理论vs实际\n\n")
        f.write(f"**测试日期**: 2026-01-17\n")
        f.write(f"**PV配置**: 效率{PV_EFFICIENCY*100}%, 面积{PV_AREA_M2}m²\n")
        f.write(f"**仿真时长**: {SIM_DURATION_MS}ms ({SIM_DURATION_MS/1000}秒)\n\n")

        f.write("## 理论计算公式\n\n")
        f.write("```\n")
        f.write("功率(W) = 辐照度(W/m²) × PV效率 × PV面积\n")
        f.write("能量(J) = 功率(W) × 时间(s)\n")
        f.write("收集率(%) = (功率 / 辐照度) × 100 = 效率 × 面积 × 100\n")
        f.write("```\n\n")

        f.write("## 测试结果\n\n")
        f.write("| 时间 | 辐照度 (W/m²) | 理论功率 (W) | 理论能量 (J) | 实际能量 (J) | 误差 (%) |\n")
        f.write("|------|---------------|--------------|--------------|--------------|----------|\n")

        for r in results:
            f.write(f"| {r['hour']:02d}:00 | {r['irradiance']:.2f} | "
                   f"{r['theory_power']:.2f} | {r['theory_energy']:.2f} | "
                   f"{r['actual_energy']:.4f} | {r['error_pct']:.2f}% |\n")

        f.write("\n## 结论\n\n")
        avg_error = sum(r['error_pct'] for r in results) / len(results)
        f.write(f"- 平均误差: {avg_error:.2f}%\n")

        if avg_error < 5:
            f.write("- ✅ 理论与实际高度一致\n")
        elif avg_error < 15:
            f.write("- ⚠️ 理论与实际基本一致，存在一定偏差\n")
        else:
            f.write("- ❌ 理论与实际存在较大偏差，需要检查实现\n")

    print(f"✅ 报告已保存到: SOLAR_COLLECTION_COMPARISON.md")

if __name__ == '__main__':
    main()
