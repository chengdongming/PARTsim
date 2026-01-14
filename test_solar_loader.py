#!/usr/bin/env python3
"""测试太阳能数据加载器"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from energy_manager import EnergyManager

def main():
    print('=== 测试能量管理器初始化 ===')

    # 测试早上8点配置
    config_file = 'test_time_based/test_summer_morning.yml'
    print(f'加载配置: {config_file}')

    manager = EnergyManager(config_file, verbose=True)

    print(f'\n=== 配置检查 ===')
    print(f'use_real_solar_data: {manager.config.use_real_solar_data}')
    print(f'solar_data_file: {manager.config.solar_data_file}')
    print(f'pv_efficiency: {manager.config.pv_efficiency}')
    print(f'pv_area_m2: {manager.config.pv_area_m2}')
    print(f'start_offset_minutes: {manager.config.start_offset_minutes}')

    print(f'\n=== Solar Loader检查 ===')
    print(f'solar_loader is None: {manager.harvester.solar_loader is None}')

    if manager.harvester.solar_loader:
        print('✅ solar_loader已成功初始化!')

        # 测试三个时间点的收集率
        print(f'\n=== 测试三个时间点的收集率 ===')

        # 午夜 (0点)
        midnight_ms = 267840 * 60000
        rate_midnight = manager.harvester.get_harvesting_rate(midnight_ms)
        irradiance_midnight = manager.harvester.solar_loader.get_irradiance_at_time(midnight_ms)
        print(f'午夜 (offset={267840}分钟): 辐照度={irradiance_midnight:.2f} W/m², 收集率={rate_midnight:.6f} J/ms')

        # 早上8点
        morning_ms = 268320 * 60000
        rate_morning = manager.harvester.get_harvesting_rate(morning_ms)
        irradiance_morning = manager.harvester.solar_loader.get_irradiance_at_time(morning_ms)
        print(f'早上 (offset={268320}分钟): 辐照度={irradiance_morning:.2f} W/m², 收集率={rate_morning:.6f} J/ms')

        # 正午12点
        noon_ms = 268560 * 60000
        rate_noon = manager.harvester.get_harvesting_rate(noon_ms)
        irradiance_noon = manager.harvester.solar_loader.get_irradiance_at_time(noon_ms)
        print(f'正午 (offset={268560}分钟): 辐照度={irradiance_noon:.2f} W/m², 收集率={rate_noon:.6f} J/ms')

        # 2000ms的理论收集
        print(f'\n=== 2000ms的理论能量收集 ===')
        print(f'午夜: {rate_midnight * 2000:.2f} J')
        print(f'早上: {rate_morning * 2000:.2f} J')
        print(f'正午: {rate_noon * 2000:.2f} J')
    else:
        print('❌ solar_loader未初始化!')
        print('将使用固定收集率 0.00002 J/ms')

if __name__ == '__main__':
    main()
