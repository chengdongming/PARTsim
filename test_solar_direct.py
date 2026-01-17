#!/usr/bin/env python3
"""
测试不同时间点的太阳能收集 - 直接运行并提取能量数据
"""

import subprocess
import re
import json

# 测试配置
TEST_HOURS = [0, 8, 10, 12, 14, 16, 18]
DAY_OF_YEAR = 187
SIMULATION_DURATION = 2000
PV_EFFICIENCY = 0.18
PV_AREA = 1.0

def get_irradiance_at_hour(hour):
    """获取指定小时的平均辐照度"""
    minute_idx = (DAY_OF_YEAR - 1) * 24 * 60 + hour * 60

    with open('data/processed/shenyang_solar_minute.csv', 'r') as f:
        next(f)  # 跳过表头
        data = []
        for i, line in enumerate(f):
            if i >= minute_idx and i < minute_idx + 60:
                try:
                    data.append(float(line.strip()))
                except:
                    pass
            if i >= minute_idx + 60:
                break

    avg_irradiance = sum(data) / len(data) if data else 0.0
    return avg_irradiance

def run_simulation_and_collect_energy(hour):
    """运行仿真并提取能量数据"""

    # 创建临时配置
    time_offset_ms = hour * 3600000
    config_content = f"""cpu_islands:
  - name: energy_aware_cpus
    numcpus: 3
    kernel:
      scheduler: gpfp_epp
      task_placement: global
      scheduler_params:
        - "strict_priority=true"
        - "enable_energy_recovery=true"
    volts: [0.8]
    freqs: [8000]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 5.0
  max_energy: 1000.0
  day_of_year: {DAY_OF_YEAR}
  time_of_day_ms: {time_offset_ms}
  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: {PV_EFFICIENCY}
  pv_area_m2: {PV_AREA}
  unit_time: 1
  enable_energy_recovery: true

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi
    params:
      - workload: idle
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1
      - workload: bzip2
        power_params: [0.007, 33.376, 1.545, 9.534e-10]
        speed_params: [0.025, 2.98e+6, 0.602, 8.137e+9]
        energy_coefficient: 1.2
      - workload: control
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1

trace:
  enabled: false
"""

    config_file = f'/tmp/solar_test_{hour}.yml'
    with open(config_file, 'w') as f:
        f.write(config_content)

    # 运行仿真并捕获输出
    result = subprocess.run(
        ['./build/rtsim/rtsim', config_file, 'epp_simulation_test/epp_test_tasks.yml',
         str(SIMULATION_DURATION)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd='/home/devcontainers/PARTSim-project'
    )

    # 提取能量收集信息
    output = result.stdout + result.stderr

    # 查找能量收集日志
    collected_pattern = r'\[Python\].*?收集能量.*?(\d+\.\d+)J.*?@(\d+)ms'
    collected_matches = re.findall(collected_pattern, output)

    total_collected = 0.0
    for energy_j, time_ms in collected_matches:
        total_collected += float(energy_j)

    # 查找初始能量和最终能量
    initial_energy = 5.0
    initial_match = re.search(r'初始能量.*?(\d+\.\d+)', output)
    if initial_match:
        initial_energy = float(initial_match.group(1))

    # 查找最终能量
    final_energy = initial_energy
    final_match = re.search(r'当前能量.*?(\d+\.\d+)', output.split('仿真结束')[-1] if '仿真结束' in output else output)
    if final_match:
        all_energy_matches = re.findall(r'当前能量.*?(\d+\.\d+)', output)
        if all_energy_matches:
            final_energy = float(all_energy_matches[-1])

    # 实际收集 = 最终能量 - 初始能量 + 消耗(粗略估计)
    # 更准确的方法是累加所有"收集能量"的日志

    # 计算消耗能量
    consumed_match = re.findall(r'consumeEnergy.*?(\d+\.\d+).*?→', output)
    total_consumed = sum([float(x) for x in consumed_match]) if consumed_match else 0.0

    # 实际收集 = 消耗 - (初始 - 最终)
    actual_collected = total_consumed - (initial_energy - final_energy)

    # 统计任务完成
    scheduled_count = output.count('scheduled')
    end_count = output.count('end_instance')

    return {
        'initial_energy': initial_energy,
        'final_energy': final_energy,
        'total_consumed': total_consumed,
        'actual_collected': actual_collected,
        'collected_from_log': total_collected,
        'scheduled_tasks': scheduled_count,
        'completed_tasks': end_count,
        'raw_output_sample': output[-500:] if len(output) > 500 else output
    }

def main():
    print('=' * 100)
    print('🌞 EPP太阳能收集测试 - NASA数据验证')
    print('=' * 100)
    print(f'📅 测试日期: 第{DAY_OF_YEAR}天 (年中)')
    print(f'⚡ 配置: PV效率={PV_EFFICIENCY}, 面积={PV_AREA}m²')
    print(f'⏱️  仿真时长: {SIMULATION_DURATION}ms = {SIMULATION_DURATION/1000:.1f}秒')
    print('=' * 100)

    print(f'\n{"时间点":<8} | {"辐照度":<12} | {"收集功率":<12} | {"理论能量":<12} | {"实际收集":<12} | {"误差":<10}')
    print('-' * 100)

    results = []

    for hour in TEST_HOURS:
        # 计算理论值
        irradiance_w_m2 = get_irradiance_at_hour(hour)
        irradiance_kw_m2 = irradiance_w_m2 / 1000.0
        collection_rate_w = irradiance_w_m2 * PV_EFFICIENCY * PV_AREA
        theoretical_energy_j = collection_rate_w * (SIMULATION_DURATION / 1000.0)

        # 运行仿真
        sim_result = run_simulation_and_collect_energy(hour)

        # 计算误差
        actual_energy = sim_result['actual_collected']
        error_pct = ((actual_energy - theoretical_energy_j) / theoretical_energy_j * 100) if theoretical_energy_j > 0 else 0

        results.append({
            'hour': hour,
            'irradiance_kw_m2': irradiance_kw_m2,
            'collection_rate_w': collection_rate_w,
            'theoretical_energy_j': theoretical_energy_j,
            'actual_energy_j': actual_energy,
            'error_pct': error_pct,
            'sim_result': sim_result
        })

        print(f'{hour:02d}:00   | {irradiance_kw_m2:10.4f}   | {collection_rate_w:10.2f}     | {theoretical_energy_j:10.4f}    | {actual_energy:10.4f}    | {error_pct:7.2f}%')

    print('=' * 100)
    print('\n📊 详细分析:')
    print('-' * 100)

    for r in results:
        print(f'\n⏰ 时间: {r["hour"]:02d}:00')
        print(f'   理论:')
        print(f'     - 辐照度: {r["irradiance_kw_m2"]:.4f} kW/m²')
        print(f'     - 收集功率: {r["collection_rate_w"]:.2f} W')
        print(f'     - 理论能量({SIMULATION_DURATION/1000:.1f}秒): {r["theoretical_energy_j"]:.4f} J')
        print(f'   实际:')
        print(f'     - 收集能量: {r["actual_energy_j"]:.4f} J')
        print(f'     - 调度任务: {r["sim_result"]["scheduled_tasks"]} 个')
        print(f'     - 完成任务: {r["sim_result"]["completed_tasks"]} 个')
        print(f'     - 初始能量: {r["sim_result"]["initial_energy"]:.4f} J')
        print(f'     - 最终能量: {r["sim_result"]["final_energy"]:.4f} J')
        print(f'     - 消耗能量: {r["sim_result"]["total_consumed"]:.4f} J')
        print(f'   误差: {r["error_pct"]:.2f}%')

    # 统计
    print('\n' + '=' * 100)
    print('📈 统计汇总:')
    print('-' * 100)

    avg_error = sum(abs(r['error_pct']) for r in results) / len(results)
    max_error = max(abs(r['error_pct']) for r in results)

    print(f'平均误差: {avg_error:.2f}%')
    print(f'最大误差: {max_error:.2f}%')
    print(f'测试点数: {len(results)}')

    print('\n✅ 测试完成!')

if __name__ == '__main__':
    main()
