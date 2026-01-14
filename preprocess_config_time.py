#!/usr/bin/env python3
"""
配置文件时间参数预处理器
将 day_of_year 和 time_of_day_ms 转换为 start_offset_minutes
"""

import sys
import yaml

def convert_time_parameters(config_file):
    """
    转换配置文件中的时间参数
    
    参数:
        config_file: YAML配置文件路径
        
    返回:
        转换后的 start_offset_minutes
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        energy_config = config.get('energy_management', {})
        
        # 检查是否有新参数
        day_of_year = energy_config.get('day_of_year')
        time_of_day_ms = energy_config.get('time_of_day_ms')
        
        if day_of_year is not None and time_of_day_ms is not None:
            # 计算start_offset_minutes
            # 公式: (day_of_year - 1) * 1440 + time_of_day_ms / 60000
            start_offset_minutes = (day_of_year - 1) * 1440 + int(time_of_day_ms / 60000)
            
            # 更新配置
            energy_config['start_offset_minutes'] = start_offset_minutes
            
            # 计算可读时间
            total_minutes = start_offset_minutes
            day = total_minutes // 1440 + 1  # 第几天
            minute_of_day = total_minutes % 1440
            hour = minute_of_day // 60
            minute = minute_of_day % 60
            
            print(f"✅ 时间参数转换成功:")
            print(f"   day_of_year: {day_of_year}")
            print(f"   time_of_day_ms: {time_of_day_ms} ({hour:02d}:{minute:02d})")
            print(f"   → start_offset_minutes: {start_offset_minutes}")
            print(f"   → 第{day}天 {hour:02d}:{minute:02d}")
            
            # 写回配置文件
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            
            return start_offset_minutes
            
        elif 'start_offset_minutes' in energy_config:
            # 已经有start_offset_minutes,不需要转换
            print(f"ℹ️  配置文件已包含 start_offset_minutes: {energy_config['start_offset_minutes']}")
            return energy_config['start_offset_minutes']
            
        else:
            print(f"⚠️  配置文件中既没有新参数(day_of_year/time_of_day_ms),也没有旧参数(start_offset_minutes)")
            print(f"   将使用默认值")
            return None
            
    except Exception as e:
        print(f"❌ 错误: {e}")
        return None

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python3 preprocess_config_time.py <config_file.yml>")
        sys.exit(1)
    
    config_file = sys.argv[1]
    print(f"处理配置文件: {config_file}")
    print("="*60)
    
    result = convert_time_parameters(config_file)
    
    if result is not None:
        print("="*60)
        print(f"✅ 处理完成, start_offset_minutes = {result}")
        sys.exit(0)
    else:
        print("="*60)
        print(f"⚠️  处理完成,但未设置时间参数")
        sys.exit(0)
