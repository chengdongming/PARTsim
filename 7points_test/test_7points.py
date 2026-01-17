# 7个时间点能量收集对比测试脚本

import subprocess
import json
import os

# 测试时间点（小时）
TEST_HOURS = [0, 8, 10, 12, 14, 15, 17]

# PV配置
PV_EFFICIENCY = 0.18
PV_AREA_M2 = 1.0
SIM_DURATION_MS = 1000  # 1秒仿真

# 获取指定小时的辐照度
def get_irradiance_at_hour(hour):
    """从NASA数据获取辐照度（第187天）"""
    import numpy as np

    data = np.loadtxt('data/processed/shenyang_solar_minute.csv', delimiter=',', skiprows=1)

    # 第187天指定小时的索引
    day_187 = 187
    target_minute = (day_187 - 1) * 1440 + hour * 60
    irradiance = data[target_minute]

    return irradiance

# 创建配置文件
def create_config(scheduler, hour):
    """创建配置文件"""
    time_of_day_ms = hour * 3600000

    config = f"""# {scheduler}调度器测试 - {hour:02d}:00
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 3

    kernel:
      scheduler: gpfp_{scheduler}
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
  time_of_day_ms: {time_of_day_ms}

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: {PV_EFFICIENCY}
  pv_area_m2: {PV_AREA_M2}

  unit_time: 50
  periodic_collection_interval_ms: 100
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
"""

    filename = f"7points_test/config_{scheduler}_{hour:02d}.yml"
    with open(filename, 'w') as f:
        f.write(config)

    return filename

# 运行仿真
def run_simulation(scheduler, hour):
    """运行仿真"""
    config_file = f"7points_test/config_{scheduler}_{hour:02d}.yml"
    trace_file = f"/tmp/trace_{scheduler}_{hour:02d}.json"

    cmd = [
        "./run_sim.sh",
        "-s", config_file,
        "-t", "epp_test_tasks.yml",
        "-d", str(SIM_DURATION_MS),
        "-o", trace_file
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # 增加到120秒

        # 提取收集能量 - 检查stdout和stderr
        harvested = 0.0
        all_output = result.stdout + '\n' + result.stderr

        for line in all_output.split('\n'):
            # 支持两种格式：
            # EPP: "总收集能量: 97.410600J"
            # ASAP: "仿真结束能量收集: 97.410600J"
            if '总收集能量' in line or '仿真结束能量收集' in line:
                try:
                    import re
                    match = re.search(r'(?:总收集能量|仿真结束能量收集):\s*([\d.]+)\s*J', line)
                    if match:
                        harvested = float(match.group(1))
                        break
                except:
                    pass

        return {
            'success': True,
            'harvested': harvested
        }
    except subprocess.TimeoutExpired as e:
        return {
            'success': False,
            'error': f'Timeout: {e}',
            'harvested': 0.0
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'harvested': 0.0
        }

# 主函数
def main():
    print("="*80)
    print("ASAP vs EPP 能量收集对比测试 - 7个时间点")
    print("="*80)
    print(f"PV效率: {PV_EFFICIENCY*100}%")
    print(f"PV面积: {PV_AREA_M2} m²")
    print(f"仿真时长: {SIM_DURATION_MS}ms ({SIM_DURATION_MS/1000}秒)")
    print(f"测试日期: 第187天")
    print("="*80)

    results = {'asap': [], 'epp': []}

    # 计算理论值
    print("\n📐 理论计算:")
    print(f"{'时间':<10} {'辐照度':<15} {'功率(W)':<12} {'理论能量(J)':<15}")
    print("-" * 60)

    for hour in TEST_HOURS:
        irradiance = get_irradiance_at_hour(hour)
        power = irradiance * PV_EFFICIENCY * PV_AREA_M2
        energy = power * (SIM_DURATION_MS / 1000.0)

        print(f"{hour:02d}:00    {irradiance:>10.2f}    {power:>10.2f}    {energy:>12.2f}")

    print("\n" + "="*80)

    # 测试ASAP
    print("\n🔬 测试ASAP调度器:")
    print("="*40)

    for hour in TEST_HOURS:
        # 创建配置
        create_config('asap', hour)

        # 运行仿真
        result = run_simulation('asap', hour)

        if result['success']:
            irradiance = get_irradiance_at_hour(hour)
            theory = irradiance * PV_EFFICIENCY * PV_AREA_M2 * 1.0
            actual = result['harvested']
            error = abs(actual - theory)
            error_pct = (error / theory * 100) if theory > 0 else 0

            print(f"{hour:02d}:00")
            print(f"  理论: {theory:.2f} J")
            print(f"  实际: {actual:.2f} J")
            print(f"  误差: {error_pct:.2f}%")

            results['asap'].append({
                'hour': hour,
                'irradiance': irradiance,
                'theory': theory,
                'actual': actual,
                'error_pct': error_pct
            })

    # 测试EPP
    print("\n🔬 测试EPP调度器:")
    print("="*40)

    for hour in TEST_HOURS:
        # 创建配置
        create_config('epp', hour)

        # 运行仿真
        result = run_simulation('epp', hour)

        if result['success']:
            irradiance = get_irradiance_at_hour(hour)
            theory = irradiance * PV_EFFICIENCY * PV_AREA_M2 * 1.0
            actual = result['harvested']
            error = abs(actual - theory)
            error_pct = (error / theory * 100) if theory > 0 else 0

            print(f"{hour:02d}:00")
            print(f"  理论: {theory:.2f} J")
            print(f"  实际: {actual:.2f} J")
            print(f"  误差: {error_pct:.2f}%")

            results['epp'].append({
                'hour': hour,
                'irradiance': irradiance,
                'theory': theory,
                'actual': actual,
                'error_pct': error_pct
            })

    # 生成对比表
    print("\n" + "="*80)
    print("📊 ASAP vs EPP 对比结果")
    print("="*80)
    print(f"{'时间':<10} {'辐照度':<12} {'ASAP能量':<12} {'EPP能量':<12} {'一致性':<10}")
    print("-" * 60)

    for i in range(len(TEST_HOURS)):
        asap_data = results['asap'][i]
        epp_data = results['epp'][i]

        diff = abs(asap_data['actual'] - epp_data['actual'])
        consistent = "✅" if diff < 0.01 else "❌"

        print(f"{asap_data['hour']:02d}:00    {asap_data['irradiance']:>8.2f}    "
              f"{asap_data['actual']:>8.2f}    {epp_data['actual']:>8.2f}    {consistent}")

    # 统计
    asap_avg_error = sum(r['error_pct'] for r in results['asap']) / len(results['asap'])
    epp_avg_error = sum(r['error_pct'] for r in results['epp']) / len(results['epp'])

    print("\n" + "="*80)
    print("📈 统计总结")
    print("="*80)
    print(f"ASAP平均准确度: {100 - asap_avg_error:.2f}%")
    print(f"EPP平均准确度: {100 - epp_avg_error:.2f}%")
    print(f"总理论能量: {sum(r['theory'] for r in results['asap']):.2f} J")
    print(f"ASAP总收集: {sum(r['actual'] for r in results['asap']):.2f} J")
    print(f"EPP总收集: {sum(r['actual'] for r in results['epp']):.2f} J")

if __name__ == '__main__':
    main()
