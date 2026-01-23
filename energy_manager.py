#!/usr/bin/env python3
"""
PARTSim 能量管理器 - 优化修复版
修复内容：
1. 能量恢复等待逻辑优化
2. 添加ASAP调度专用接口
3. 改进能量计算精度
4. 添加调试日志控制
5. 优化Python与C++交互
"""

import os
import sys
import json
import yaml
import math
import argparse
import time
import threading
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta

# 使用统一日志系统
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.unified_logger import get_energy_logger, LogLevel

# 获取能量管理器的日志记录器
logger = get_energy_logger()

class EnergyConfig:
    """能量配置管理器 - 从系统配置文件读取所有参数"""
    
    def __init__(self, config_file: str = None):
        self.config_file = config_file
        self.system_config = {}
        # 从环境变量获取调试级别
        debug_env = os.environ.get('RTSIM_ENERGY_DEBUG', '0')
        if debug_env == '2':
            self.debug_level = 2  # 详细调试
        elif debug_env == '1':
            self.debug_level = 1  # 基本调试
        else:
            self.debug_level = 0  # 关闭调试
        
        # 默认配置（如果配置文件不存在或没有相关配置）
        self._set_defaults()
        
        if config_file:
            self.load_from_file(config_file)
    
    def _set_defaults(self):
        """设置默认配置"""
        # 基本能量参数
        self.initial_energy = 200.0
        self.max_energy = 800.0
        
        # === 真实太阳能数据配置 ===
        self.use_real_solar_data = False
        self.solar_data_file = "data/processed/shenyang_solar_minute.csv"
        self.pv_efficiency = 0.18      # 18% 光伏效率
        self.pv_area_m2 = 1.0          # 1平方米光伏板
        self.start_offset_minutes = 0  # 时间偏移（分钟）
        
        # === 季节和时间配置 ===
        self.seasonal_factors = {
            "spring": 0.9,
            "summer": 1.2,
            "autumn": 0.8,
            "winter": 0.6
        }
        
        # 季节范围配置（一年中的第几天）
        self.season_ranges = {
            "spring": {"start_day": 60, "end_day": 151},   # 3月1日 - 5月31日
            "summer": {"start_day": 152, "end_day": 243},  # 6月1日 - 8月31日
            "autumn": {"start_day": 244, "end_day": 334},  # 9月1日 - 11月30日
            "winter": {"start_day": 335, "end_day": 365}   # 12月1日 - 2月28/29日
        }
        
        # 一天中的时间范围配置（24小时制）
        self.time_ranges = {
            "morning": {"start_hour": 6, "end_hour": 11},
            "noon": {"start_hour": 12, "end_hour": 13},    # 中午
            "afternoon": {"start_hour": 14, "end_hour": 17},
            "evening": {"start_hour": 18, "end_hour": 21},
            "night": {"start_hour": 22, "end_hour": 5}     # 跨夜
        }
        
        # 不同时间段的能量收集倍率
        self.time_multipliers = {
            "morning": 2.0,     # 上午
            "noon": 5.0,        # 中午最强
            "afternoon": 3.0,   # 下午
            "evening": 1.0,     # 傍晚
            "night": 0.5        # 晚上
        }
        
        # 季节能量收集倍率
        self.seasonal_multipliers = {
            "spring": 0.9,
            "summer": 1.2,
            "autumn": 0.8,
            "winter": 0.6
        }
        
        # 调度器单位时间（ms）
        self.unit_time = 50
        
        # 能量阈值（J）
        self.critical_energy = 50.0
        self.low_energy = 100.0
        self.normal_energy = 200.0
        self.high_energy = 400.0
        
        # 能量恢复配置
        self.enable_energy_recovery = True
        self.max_recovery_wait_time_ms = 10000

        # === 能量收集源配置 ===
        self.harvesting_sources = {
            'solar': {'enabled': True},
            'wind': {'enabled': True}
        }

        # 功率模型参数
        self.base_power = 0.5  # 基础功耗（W）
        
        # 工作负载功率系数（W）
        self.power_coefficients = {
            "bzip2": 1.2,
            "hash": 0.8,
            "encrypt": 1.5,
            "decrypt": 1.5,
            "control": 0.1
        }
        
        # 频率-功率比例系数（MHz）
        # ⭐ 修复：使用 MHz 单位，匹配配置文件中的频率范围（7000-10500 MHz）
        # 以 8100 MHz 为基准频率（比率=1.0），其他频率按线性比例调整
        self.frequency_power_ratios = {
            7000: 0.90,   # 7.0 GHz
            7500: 0.95,   # 7.5 GHz
            8000: 0.98,   # 8.0 GHz
            8100: 1.00,   # 8.1 GHz (基准频率)
            8200: 1.02,   # 8.2 GHz
            8300: 1.04,   # 8.3 GHz
            8400: 1.06,   # 8.4 GHz
            8500: 1.08,   # 8.5 GHz
            9000: 1.15,   # 9.0 GHz
            9500: 1.22,   # 9.5 GHz
            10000: 1.30,  # 10.0 GHz
            10500: 1.38   # 10.5 GHz
        }
        
        # 调试配置
        self.debug_mode = False
        self.log_energy_consumption = True
        
        # 基础收集率配置 - 修复：添加缺失的属性
        self.base_harvest_rate_per_ms = 0.00002  # 基础收集率 (J/ms) = 0.02 J/s
        self.solar_multiplier = 10.0  # 太阳能收集倍率
        self.wind_multiplier = 2.0    # 风能收集倍率
        
        # 真实太阳能数据配置 - 新增
        self.use_real_solar_data = False
        self.solar_data_file = "data/processed/shenyang_solar_minute.csv"
        self.pv_efficiency = 0.18      # 18% 光伏效率
        self.pv_area_m2 = 1.0          # 1平方米光伏板
        self.start_offset_minutes = 0  # 时间偏移（分钟）
    
    def load_from_file(self, config_file: str) -> bool:
        """从YAML配置文件加载能量参数"""
        try:
            if not os.path.exists(config_file):
                logger.error(f"Config file does not exist: {config_file}")
                return False
                
            with open(config_file, 'r') as f:
                self.system_config = yaml.safe_load(f)
            
            if not self.system_config:
                logger.warning(f"Config file is empty: {config_file}")
                return False
            
            # 提取能量管理配置
            energy_config = self.system_config.get('energy_management', {})
            
            # 基本能量参数
            self.initial_energy = float(energy_config.get('initial_energy', self.initial_energy))
            self.max_energy = float(energy_config.get('max_energy', self.max_energy))

            # 添加调试输出
            logger.info(f"[EnergyConfig] 加载配置文件: {config_file}")
            logger.info(f"[EnergyConfig] initial_energy from config: {energy_config.get('initial_energy', 'NOT SET')}")
            logger.info(f"[EnergyConfig] self.initial_energy after loading: {self.initial_energy}")

            # === 真实太阳能数据配置 ===
            # 修复：确保正确加载真实太阳能数据配置
            if 'use_real_solar_data' in energy_config:
                self.use_real_solar_data = bool(energy_config.get('use_real_solar_data', self.use_real_solar_data))
            else:
                self.use_real_solar_data = self.use_real_solar_data  # 保持默认值
            
            self.solar_data_file = str(energy_config.get('solar_data_file', self.solar_data_file))
            self.pv_efficiency = float(energy_config.get('pv_efficiency', self.pv_efficiency))
            self.pv_area_m2 = float(energy_config.get('pv_area_m2', self.pv_area_m2))

            # ⭐ 必需参数：day_of_year + time_of_day_ms
            day_of_year = energy_config.get('day_of_year', 187)
            time_of_day_ms = energy_config.get('time_of_day_ms', 0)

            # 计算公式: start_offset_minutes = (day_of_year - 1) * 1440 + time_of_day_ms / 60000
            self.start_offset_minutes = (day_of_year - 1) * 1440 + int(time_of_day_ms / 60000)
            logger.info(f"[EnergyConfig] 从 day_of_year={day_of_year}, time_of_day_ms={time_of_day_ms} 计算 start_offset_minutes={self.start_offset_minutes}")

            # === 关键修复：重新计算simulation_start_time ===
            self.simulation_start_time = int(self.start_offset_minutes * 60 * 1000)
            logger.info(f"[Python] 从配置文件更新start_offset_minutes={self.start_offset_minutes}, simulation_start_time={self.simulation_start_time}ms")

            # === 修复点：加载季节参数 ===
            season_ranges_config = energy_config.get('season_ranges', {})
            if season_ranges_config:
                for season, range_info in season_ranges_config.items():
                    if season in self.season_ranges:
                        self.season_ranges[season] = {
                            "start_day": int(range_info.get('start_day', self.season_ranges[season]["start_day"])),
                            "end_day": int(range_info.get('end_day', self.season_ranges[season]["end_day"]))
                        }
            
            # === 修复点：加载时间参数 ===
            time_ranges_config = energy_config.get('time_ranges', {})
            if time_ranges_config:
                for time_period, time_info in time_ranges_config.items():
                    if time_period in self.time_ranges:
                        self.time_ranges[time_period] = {
                            "start_hour": int(time_info.get('start_hour', self.time_ranges[time_period]["start_hour"])),
                            "end_hour": int(time_info.get('end_hour', self.time_ranges[time_period]["end_hour"]))
                        }
            
            # 加载时间倍率
            time_multipliers_config = energy_config.get('time_multipliers', {})
            if time_multipliers_config:
                for time_period, multiplier in time_multipliers_config.items():
                    if time_period in self.time_multipliers:
                        self.time_multipliers[time_period] = float(multiplier)
            
            # 加载季节倍率
            seasonal_multipliers_config = energy_config.get('seasonal_multipliers', {})
            if seasonal_multipliers_config:
                for season, multiplier in seasonal_multipliers_config.items():
                    if season in self.seasonal_multipliers:
                        self.seasonal_multipliers[season] = float(multiplier)
            
            # 加载季节因子（从environmental_awareness部分）
            environmental = self.system_config.get('environmental_awareness', {})
            if environmental:
                seasonal_factors_config = environmental.get('seasonal_factors', {})
                if seasonal_factors_config:
                    for season, factor in seasonal_factors_config.items():
                        self.seasonal_factors[season] = float(factor)
            
            # 单位时间
            self.unit_time = int(energy_config.get('unit_time', self.unit_time))
            
            # 能量阈值
            thresholds = energy_config.get('energy_thresholds', {})
            if thresholds:
                self.critical_energy = float(thresholds.get('critical', self.critical_energy))
                self.low_energy = float(thresholds.get('low', self.low_energy))
                self.normal_energy = float(thresholds.get('normal', self.normal_energy))
                self.high_energy = float(thresholds.get('high', self.high_energy))
            
            # 能量恢复
            self.enable_energy_recovery = bool(energy_config.get('enable_energy_recovery', self.enable_energy_recovery))
            self.max_recovery_wait_time_ms = int(energy_config.get('max_recovery_wait_time_ms', self.max_recovery_wait_time_ms))

            # ⭐ 新增：周期性能量收集间隔
            self.periodic_collection_interval = int(energy_config.get('periodic_collection_interval_ms', 100))

            # === 关键修复：加载能量收集源配置 ===
            harvesting_sources = energy_config.get('harvesting_sources', {})
            if harvesting_sources:
                self.harvesting_sources = harvesting_sources
                logger.info(f"[EnergyConfig] 加载能量收集源配置: {harvesting_sources}")
            else:
                self.harvesting_sources = {'solar': {'enabled': True}, 'wind': {'enabled': True}}
                logger.info(f"[EnergyConfig] 使用默认能量收集源配置")

            # 消耗模型
            consumption_model = energy_config.get('consumption_model', {})
            if consumption_model:
                self.base_power = float(consumption_model.get('base_power', self.base_power))
                
                workload_coeffs = consumption_model.get('workload_coefficients', {})
                if workload_coeffs:
                    for key, value in workload_coeffs.items():
                        self.power_coefficients[key] = float(value)
                
                freq_scaling = consumption_model.get('frequency_scaling', {})
                if freq_scaling:
                    for key, value in freq_scaling.items():
                        self.frequency_power_ratios[int(key)] = float(value)
            
            # 调试配置
            self.debug_mode = os.environ.get('RTSIM_ENERGY_DEBUG', '0') == '1'
            
            # === 新增：加载真实太阳能数据配置 ===
            # 使用.get()方法提供默认值，增强健壮性
            self.use_real_solar_data = energy_config.get('use_real_solar_data', self.use_real_solar_data)
            self.solar_data_file = energy_config.get('solar_data_file', self.solar_data_file)
            self.pv_efficiency = energy_config.get('pv_efficiency', self.pv_efficiency)
            self.pv_area_m2 = energy_config.get('pv_area_m2', self.pv_area_m2)
            self.start_offset_minutes = energy_config.get('start_offset_minutes', self.start_offset_minutes)
            
            logger.info(f"EnergyConfig loaded from {config_file}")
            logger.info(f"  Initial energy: {self.initial_energy} J")
            logger.info(f"  Max energy: {self.max_energy} J")
            logger.info(f"  Unit time: {self.unit_time} ms")
            logger.info(f"  Debug mode: {self.debug_mode}")
            
            # 打印真实太阳能数据配置
            if self.use_real_solar_data:
                logger.info("  === 真实太阳能数据配置 ===")
                logger.info(f"    使用真实数据: {self.use_real_solar_data}")
                logger.info(f"    数据文件: {self.solar_data_file}")
                logger.info(f"    光伏效率: {self.pv_efficiency}")
                logger.info(f"    光伏面积: {self.pv_area_m2} m²")
                logger.info(f"    时间偏移: {self.start_offset_minutes} 分钟")
            
            # 打印加载的季节和时间配置
            logger.info("加载的季节配置:")
            for season, range_info in self.season_ranges.items():
                logger.info(f"  {season}: 第{range_info['start_day']}天 - 第{range_info['end_day']}天")
            
            logger.info("加载的时间段配置:")
            for time_period, time_info in self.time_ranges.items():
                logger.info(f"  {time_period}: {time_info['start_hour']}:00 - {time_info['end_hour']}:00 (倍率: {self.time_multipliers.get(time_period, 1.0)})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error loading energy config: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def log_debug(self, level: int, message: str):
        """根据调试级别记录日志"""
        if self.debug_level >= level:
            if level == 2:
                logger.debug(f"[详细] {message}")
            else:
                logger.debug(message)

    def get_workload_power(self, workload_type: str) -> float:
        """获取工作负载功率系数"""
        return self.power_coefficients.get(workload_type, 1.0)
    
    def get_frequency_ratio(self, frequency_mhz: float) -> float:
        """获取频率-功率比例系数"""
        closest_freq = min(self.frequency_power_ratios.keys(), 
                          key=lambda f: abs(f - frequency_mhz))
        return self.frequency_power_ratios.get(closest_freq, 1.0)
    
    def get_unit_time_energy(self, workload_type: str, frequency_mhz: float = 1400.0) -> float:
        """计算单位时间的能量消耗"""
        return self.calculate_task_energy(workload_type, self.unit_time, frequency_mhz)
    
    def calculate_task_energy(self, workload_type: str, execution_time_ms: float, 
                            frequency_mhz: float = 1400.0) -> float:
        """计算任务能量消耗"""
        workload_power = self.get_workload_power(workload_type)
        frequency_ratio = self.get_frequency_ratio(frequency_mhz)
        
        # 总功率 (W) = 基础功率 + 工作负载功率 × 频率比例
        total_power_watts = self.base_power + workload_power * frequency_ratio
        
        # 能量 (J) = 功率 (W) × 时间 (s)
        execution_time_s = execution_time_ms / 1000.0
        energy_joules = total_power_watts * execution_time_s
        
        if self.debug_mode and execution_time_ms >= 10:
            logger.debug(f"能量计算: 工作负载={workload_type}, 时间={execution_time_ms}ms, "
                        f"功率={total_power_watts:.3f}W, 能量={energy_joules:.6f}J")
        
        return energy_joules

class EnergyHarvester:
    """能量收集器 - 基于绝对时间，修复了时间计算"""
    
    def __init__(self, config: EnergyConfig):
            self.config = config
            self.start_time_offset_ms = 0
            self.simulation_start_time = 0
            
            self.current_season = "summer"
            
            self.total_solar_harvested = 0.0
            self.total_wind_harvested = 0.0
            self.harvesting_history = []
            
            # 性能计数器
            self.harvest_count = 0
            self._last_log_time = 0
            
            # 修复：添加缓存机制提高性能
            self._harvest_rate_cache = {}
            self._cache_max_size = 1000
            self._cache_hit = 0
            self._cache_miss = 0
            
            # 修复：添加线程锁保证线程安全
            self._lock = threading.RLock()
            
            # === 新增：真实太阳能数据加载器 ===
            self.solar_loader = None
            if config.use_real_solar_data:
                try:
                    # 动态导入，避免循环依赖
                    from solar_data_loader import SolarDataLoader

                    # 智能路径处理：自动检测运行目录
                    import os
                    solar_file = config.solar_data_file
                    # 如果文件不存在且在build目录，尝试添加父目录前缀
                    if not os.path.exists(solar_file) and 'build' in os.getcwd().split(os.sep):
                        solar_file = os.path.join('..', solar_file)

                    self.solar_loader = SolarDataLoader(
                        solar_file,
                        start_offset_minutes=config.start_offset_minutes
                    )
                    logger.info(f"[EnergyHarvester] 成功初始化真实太阳能数据模型")
                    logger.info(f"[EnergyHarvester] 使用数据文件: {config.solar_data_file}")
                    logger.info(f"[EnergyHarvester] 光伏参数: 效率={config.pv_efficiency}, 面积={config.pv_area_m2}m²")
                    logger.info(f"[EnergyHarvester] 时间偏移: {config.start_offset_minutes} 分钟")
                except Exception as e:
                    logger.error(f"[EnergyHarvester] 错误: 无法初始化太阳能数据加载器: {e}")
                    logger.error(f"[EnergyHarvester] 将使用原有模型")
                    self.solar_loader = None


    def set_start_time_offset(self, offset_ms: int):
        """设置开始时间偏移"""
        with self._lock:
            self.start_time_offset_ms = offset_ms
            hour = (offset_ms // 3600000) % 24
            minute = (offset_ms % 3600000) // 60000
            second = (offset_ms % 60000) // 1000
            logger.info(f"EnergyHarvester: Start time offset set to {offset_ms} ms ({hour:02d}:{minute:02d}:{second:02d})")
            
            # 清空缓存，因为时间偏移改变了
            self._harvest_rate_cache.clear()
            
            # 修复：返回True，因为C++端期望布尔值
            return True
    
    def get_season_from_time(self, time_ms: int) -> str:
        """根据时间获取季节 - 使用可配置参数"""
        total_seconds = time_ms // 1000
        total_days = total_seconds // 86400
        day_of_year = total_days % 365
        
        # 使用配置的季节范围判断
        for season, range_info in self.config.season_ranges.items():
            start_day = range_info["start_day"]
            end_day = range_info["end_day"]
            
            # 处理跨年的情况（冬季）
            if season == "winter":
                if day_of_year >= start_day or day_of_year <= end_day:
                    return season
            elif start_day <= day_of_year <= end_day:
                return season
        
        # 默认返回夏季
        return "summer"
    
    def get_harvesting_rate(self, absolute_time_ms: int) -> float:
        """获取收集率 - 修复版，确保时间处理正确"""
        with self._lock:
            # 检查缓存
            cache_key = int(absolute_time_ms // 1000)  # 按秒缓存
            
            if cache_key in self._harvest_rate_cache:
                self._cache_hit += 1
                return self._harvest_rate_cache[cache_key]
            
            self._cache_miss += 1
            
            # === 关键修改：优先使用真实太阳能数据 ===
            if self.solar_loader is not None:
                # 使用真实太阳能数据
                total_rate = self.solar_loader.get_harvesting_rate(
                    absolute_time_ms,
                    self.config.pv_efficiency,
                    self.config.pv_area_m2
                )

                # === 关键修复：当使用真实NASA数据时，不应用时间倍率 ===
                # NASA数据已经是真实的辐照度值，应用时间倍率会导致收集率被错误放大
                # 只有在不使用真实数据时才应用时间倍率
                
                # 调试输出
                if self.config.debug_mode:
                    hour = int((absolute_time_ms // 3600000) % 24)
                    minute = int((absolute_time_ms % 3600000) // 60000)
                    second = int((absolute_time_ms % 60000) // 1000)
                    irradiance = self.solar_loader.get_irradiance_at_time(absolute_time_ms)
                    logger.debug(f"[EnergyHarvester] 真实数据模式:")
                    logger.debug(f"  绝对时间: {absolute_time_ms}ms")
                    logger.debug(f"  时间: {hour:02d}:{minute:02d}:{second:02d}")
                    logger.debug(f"  辐照度: {irradiance:.1f} W/m²")
                    logger.debug(f"  收集率: {total_rate:.6f} J/ms ({total_rate*1000:.2f} W)")
            else:
                # === 原有逻辑（作为临时后备） ===
                # 计算一天中的时间
                time_of_day = absolute_time_ms % 86400000
                hour = int((time_of_day // 3600000) % 24)
                minute = int((time_of_day % 3600000) // 60000)
                second = int((time_of_day % 60000) // 1000)
                
                # 基础收集率 (J/ms) - 使用默认值
                base_rate = 0.00002  # 默认基础收集率
                
                # 根据小时调整收集率
                if hour == 12:
                    multiplier = 10.0  # 中午最强
                elif 6 <= hour <= 18:
                    # 白天：线性变化
                    hour_offset = abs(hour - 12)
                    multiplier = 10.0 * (1.0 - hour_offset / 12.0 * 0.7)
                else:
                    # 晚上：使用风能
                    multiplier = 2.0 * 0.5
                
                # 确保最小倍数
                multiplier = max(multiplier, 0.1)
                
                # 计算总收集率
                total_rate = base_rate * multiplier
                
                # 季节调整
                season = self.get_season_from_time(absolute_time_ms)
                seasonal_factor = self.config.seasonal_factors.get(season, 1.0)
                total_rate *= seasonal_factor
                
                # 调试输出
                if self.config.debug_mode:
                    logger.debug(f"[Python] 收集率计算: {total_rate:.6f} J/ms ({total_rate*1000:.3f} J/s)")
                    logger.debug(f"  绝对时间: {absolute_time_ms}ms")
                    logger.debug(f"  时间: {hour:02d}:{minute:02d}:{second:02d}")
                    logger.debug(f"  倍数: {multiplier:.2f}, 季节: {season}")

            # ⭐ V28.11修复：只有在使用模拟数据时才确保最小收集率
            # 当使用真实NASA太阳能数据时，辐照度就是0（如午夜），收集率应该也是0
            # 强制设置最小收集率会导致理论值与实际值不匹配
            if self.solar_loader is None:
                # 只在不使用真实数据时应用最小收集率
                total_rate = max(total_rate, 0.000001)
            # 使用真实NASA数据时，保持total_rate不变（可能为0）

            # 缓存结果
            if len(self._harvest_rate_cache) >= self._cache_max_size:
                keys_to_remove = list(self._harvest_rate_cache.keys())[:self._cache_max_size//2]
                for key in keys_to_remove:
                    del self._harvest_rate_cache[key]
            
            self._harvest_rate_cache[cache_key] = total_rate
            
            return total_rate


    def harvest_energy(self, current_time_ms: int, duration_ms: int) -> float:
        """收集能量 - 完全修复版"""
        with self._lock:
            # 参数检查
            if duration_ms <= 0 or current_time_ms < 0:
                return 0.0
            
            # 计算收集率（使用绝对时间）
            harvest_rate = self.get_harvesting_rate(current_time_ms)
            
            # 理论收集量
            theoretical_harvest = harvest_rate * duration_ms
        
            # === 关键修复：移除所有限制，使用完整的NASA数据计算 ===
            # 直接使用理论收集量，不进行任何限制
            total_harvested = theoretical_harvest

            # 确保非负
            total_harvested = max(total_harvested, 0.0)
            
            # 统计
            self.harvest_count += 1
            
            # 调试输出
            if self.config.debug_mode and total_harvested > 0.001:
                hour = (current_time_ms // 3600000) % 24
                minute = (current_time_ms % 3600000) // 60000
                logger.debug(f"收集计算: {total_harvested:.6f}J, "
                            f"持续时间: {duration_ms}ms, "
                            f"收集率: {harvest_rate*1000:.3f}J/s, "
                            f"时间: {hour:02d}:{minute:02d}")
            
            return total_harvested
    

    def update_energy_continuously(self, current_time_ms: int) -> float:
        """连续更新能量收集 - 修复版，避免过度收集"""
        with self._lock:
            if self.last_update_time == 0:
                self.last_update_time = current_time_ms
                logger.info(f"首次更新能量收集，设置last_update_time为: {current_time_ms}ms")
                
                # 修复：对于初始化，只收集一小段时间，避免过度收集
                # 假设系统从空闲状态开始，收集1秒的能量
                initial_duration = min(1000, current_time_ms)  # 最多1秒
                if initial_duration > 0:
                    harvested = self.harvest_energy(0, initial_duration)
                    if harvested > 0:
                        # 确保不会超过最大能量
                        actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                        self.current_energy += actual_harvested
                        self.total_harvested += actual_harvested
                        
                        if self.config.debug_mode:
                            logger.debug(f"初始能量收集: {actual_harvested:.6f}J, 当前能量: {self.current_energy:.1f}J")
                    
                    return actual_harvested if 'actual_harvested' in locals() else 0.0
                else:
                    return 0.0
            
            time_elapsed = current_time_ms - self.last_update_time
            
            # 修复：避免过大的时间跳跃
            if time_elapsed <= 0:
                return 0.0
            
            # 如果时间跳跃超过5秒，限制收集时间
            if time_elapsed > 5000:  # 5秒
                logger.warning(f"时间跳跃过大: {time_elapsed}ms > 5000ms，限制收集时间")
                
                # 只收集最近5秒的能量
                harvest_duration = 5000
                
                # 使用平均收集率
                avg_rate = self.harvester.get_harvesting_rate(
                    self.last_update_time + self.simulation_start_time
                )
                
                # 计算收集的能量
                total_harvested = avg_rate * harvest_duration
                
                # 移除限制：直接使用NASA数据计算的收集量
                
                # 更新最后更新时间
                self.last_update_time = current_time_ms
                
                # 实际收集
                if total_harvested > 0:
                    actual_harvested = min(total_harvested, self.config.max_energy - self.current_energy)
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                    
                    if self.config.debug_mode:
                        logger.debug(f"大时间跳跃收集: {actual_harvested:.6f}J, 时间间隔: {time_elapsed}ms")
                    
                    return actual_harvested
                else:
                    return 0.0
            else:
                # 正常时间流逝
                harvested = self.harvest_energy(self.last_update_time, time_elapsed)
                
                if harvested > 0:
                    actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                
                self.last_update_time = current_time_ms
                
                if self.config.debug_mode and actual_harvested > 0.001:
                    logger.debug(f"正常收集: {actual_harvested:.6f}J, 时间间隔: {time_elapsed}ms")
                
                return actual_harvested if harvested > 0 else 0.0


class EnergyManager:
    """完整的能量管理器 - 优化修复版"""
    
    def __init__(self, config_file: str = None, verbose: bool = True):
            logger.info(f"[EnergyManager] __init__ called with config_file: {config_file}")
            self.config = EnergyConfig(config_file)
            self.harvester = EnergyHarvester(self.config)

            logger.info(f"[EnergyManager] self.config.initial_energy: {self.config.initial_energy}")
            self.current_energy = self.config.initial_energy
            logger.info(f"[EnergyManager] self.current_energy set to: {self.current_energy}")
            self.total_consumed = 0.0
            self.total_harvested = 0.0
            self.energy_level_history = []

            self.last_update_time = 0
            # 修复：从配���文件读取start_offset_minutes并转换为毫秒
            self.simulation_start_time = int(self.config.start_offset_minutes * 60 * 1000)
            self.task_energy_records = {}
            self.verbose = verbose
            
            # 修复：添加ASAP调度专用状态
            self.asap_recovery_target = None
            self.asap_recovery_start_time = 0
            self.asap_recovery_required_energy = 0.0
            
            # === 关键修复：添加恢复状态标志 ===
            self.recovery_in_progress = False
            self.recovery_start_time = 0
            self.recovery_end_time = 0
            
            # 修复：添加线程锁
            self._lock = threading.RLock()
            
            # 添加调试日志控制
            self._last_debug_log = 0
            
            if verbose:
                logger.info(f"[Python] EnergyManager初始化完成")
                logger.info(f"[Python] 初始能量: {self.current_energy:.1f}/{self.config.max_energy:.1f} J")
                logger.info(f"[Python] 仿真开始时间偏移: {self.simulation_start_time}ms")
                
                # 显示开始时间的格式化
                if self.simulation_start_time > 0:
                    hour = int((self.simulation_start_time // 3600000) % 24)
                    minute = int((self.simulation_start_time % 3600000) // 60000)
                    second = int((self.simulation_start_time % 60000) // 1000)
                    logger.info(f"[Python] 仿真开始时间: {hour:02d}:{minute:02d}:{second:02d}")


    def get_harvesting_rate_example(self, hour: int) -> float:
        """获取指定小时的收集率示例（用于显示）"""
        # 基础收集率
        base_rate = self.config.base_harvest_rate_per_ms
        
        # 根据小时调整收集率
        if hour == 12:
            multiplier = getattr(self.config, 'solar_multiplier', 10.0)
        elif 6 <= hour <= 18:
            hour_angle = abs(hour - 12) / 6.0
            solar_factor = math.cos(hour_angle * math.pi / 2.0)
            multiplier = getattr(self.config, 'solar_multiplier', 10.0) * max(solar_factor, 0.3)
        else:
            multiplier = getattr(self.config, 'wind_multiplier', 2.0) * 0.5
        
        multiplier = max(multiplier, 0.1)
        total_rate = base_rate * multiplier
        
        # 使用夏季系数
        seasonal_factor = getattr(self.config, 'seasonal_factors', {}).get("summer", 1.0)
        total_rate *= seasonal_factor
        
        return total_rate
    



    def set_start_time_offset(self, offset_ms: int):
        """设置开始时间偏移 - 修复版"""
        with self._lock:
            # 设置开始时间偏移
            self.simulation_start_time = offset_ms
            
            # 初始化 EnergyHarvester
            self.harvester.set_start_time_offset(offset_ms)
            
            # 修复：初始化 last_update_time 为0，表示还未开始收集
            self.last_update_time = 0
            
            hour = (offset_ms // 3600000) % 24
            minute = (offset_ms % 3600000) // 60000
            logger.info(f"EnergyManager: 仿真开始时间设置为: {hour:02d}:{minute:02d} (偏移: {offset_ms}ms)")
            
            # 修复：清空harvester的缓存
            if hasattr(self.harvester, '_harvest_rate_cache'):
                self.harvester._harvest_rate_cache.clear()
                logger.debug("EnergyHarvester 缓存已清空")
            
            # 修复：返回True，因为C++端期望布尔值
            return True
    
    def load_system_config(self, config_file: str) -> bool:
        """加载系统配置文件"""
        with self._lock:
            try:
                if not config_file or not isinstance(config_file, str):
                    logger.warning(f"警告: 无效的配置文件参数: {config_file}")
                    return True
                    
                # 保存旧的配置状态
                old_use_real_solar_data = getattr(self.config, 'use_real_solar_data', False)
                
                success = self.config.load_from_file(config_file)
                
                if success:
                    logger.info(f"成功重新加载配置文件: {config_file}")

                    # ========== 关键修复：更新仿真开始时间偏移 ==========
                    new_start_time_offset = int(self.config.start_offset_minutes * 60 * 1000)
                    if self.simulation_start_time != new_start_time_offset:
                        self.simulation_start_time = new_start_time_offset
                        hour = (self.simulation_start_time // 3600000) % 24
                        minute = (self.simulation_start_time % 3600000) // 60000
                        logger.info(f"更新仿真开始时间: {hour:02d}:{minute:02d} (偏移: {self.simulation_start_time}ms)")
                        # 同时更新harvester的时间偏移
                        if hasattr(self.harvester, 'set_start_time_offset'):
                            self.harvester.set_start_time_offset(self.simulation_start_time)

                    # ========== 关键修复：确保初始能量与配置一致 ==========
                    # 方案：如果配置文件中的initial_energy不同，则强制更新
                    # 注意：这会重置当前能量，适用于每次运行不同的测试场景
                    config_initial_energy = self.config.initial_energy
                    if abs(self.current_energy - config_initial_energy) > 0.01:  # 允许小的浮点误差
                        old_energy = self.current_energy
                        self.current_energy = config_initial_energy
                        logger.info(f"强制更新初始能量: {old_energy:.1f}J -> {self.current_energy:.1f}J")
                    else:
                        logger.info(f"初始能量已是配置值: {self.current_energy:.1f} J")

                    logger.info(f"当前能量: {self.current_energy:.1f}/{self.config.max_energy:.1f} J")
                    
                    # ========== 关键修复：如果启用了真实太阳能数据，重新初始化SolarDataLoader ==========
                    new_use_real_solar_data = getattr(self.config, 'use_real_solar_data', False)
                    if new_use_real_solar_data and (not old_use_real_solar_data or self.harvester.solar_loader is None):
                        try:
                            from solar_data_loader import SolarDataLoader

                            # 智能路径处理：自动检测运行目录
                            import os
                            solar_file = self.config.solar_data_file
                            if not os.path.exists(solar_file) and 'build' in os.getcwd().split(os.sep):
                                solar_file = os.path.join('..', solar_file)

                            self.harvester.solar_loader = SolarDataLoader(
                                solar_file,
                                start_offset_minutes=self.config.start_offset_minutes
                            )
                            logger.info(f"[EnergyManager] 重新初始化真实太阳能数据模型")
                            logger.info(f"[EnergyManager] 使用数据文件: {self.config.solar_data_file}")
                            logger.info(f"[EnergyManager] 光伏参数: 效率={self.config.pv_efficiency}, 面积={self.config.pv_area_m2}m²")
                        except Exception as e:
                            logger.error(f"[EnergyManager] 错误: 无法重新初始化太阳能数据加载器: {e}")
                            self.harvester.solar_loader = None
                
                return bool(success)
                
            except Exception as e:
                logger.error(f"加载配置文件异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return True
    def wait_for_energy_recovery_asap_simple(self, required_energy: float, 
                                       current_time_ms: int,
                                       max_wait_time_ms: int = 10000) -> bool:
        """ASAP调度专用：简化能量恢复 - 修复版"""
        with self._lock:
            logger.info(f"ASAP能量恢复开始: 需要{required_energy:.6f}J, 当前{self.current_energy:.1f}J")
            
            start_time = current_time_ms
            waited_ms = 0
            check_interval = 1000  # 每1秒检查一次
            
            while waited_ms < max_wait_time_ms:
                # 计算收集时间
                harvest_duration = min(check_interval, max_wait_time_ms - waited_ms)
                
                # 转换为绝对时间进行收集
                absolute_time = current_time_ms + waited_ms + self.simulation_start_time
                harvested = self.harvest_energy(absolute_time, harvest_duration)
                
                if harvested > 0:
                    # 实际收集
                    actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                    
                    # 检查是否满足需求
                    if self.current_energy >= required_energy:
                        logger.info(f"✅ ASAP能量恢复完成: {self.current_energy:.1f}J >= {required_energy:.6f}J")
                        return True
                    
                    logger.debug(f"恢复进度: +{actual_harvested:.6f}J, 当前{self.current_energy:.1f}J")
                
                waited_ms += harvest_duration
            
            logger.warning(f"⚠️ ASAP能量恢复超时: {self.current_energy:.1f}J < {required_energy:.6f}J")
            return False

    def wait_for_energy_recovery_wrapper(self, required_energy: float,
                                        current_time_ms: int,
                                        max_wait_time_ms: int = 10000) -> bool:
        """C++兼容性接口：等待能量恢复 - 最终修复版"""
        try:
            # === 关键修复：添加详细日志 ===
            logger.info(f"[Python] wait_for_energy_recovery_wrapper 调用:")
            logger.info(f"  所需能量: {required_energy:.6f} J")
            logger.info(f"  仿真时间: {current_time_ms} ms")
            logger.info(f"  开始偏移: {self.simulation_start_time} ms")
            logger.info(f"  绝对时间: {current_time_ms + self.simulation_start_time} ms")
            logger.info(f"  当前能量: {self.current_energy:.6f} J")
            
            # 转换时间为绝对时间（用于收集率计算）
            absolute_time_ms = current_time_ms  # 已经是绝对时间
            
            # === 关键修复：验证当前能量状态 ===
            # 获取当前收集率
            current_rate = self.harvester.get_harvesting_rate(absolute_time_ms)
            
            logger.info(f"[Python] 恢复前状态检查:")
            logger.info(f"  绝对时间: {absolute_time_ms} ms")
            logger.info(f"  当前收集率: {current_rate*1000:.3f} J/s")
            logger.info(f"  需要能量: {required_energy:.6f} J")
            logger.info(f"  当前能量: {self.current_energy:.6f} J")
            logger.info(f"  能量差: {required_energy - self.current_energy:.6f} J")
            
            # 计算理论恢复时间
            if current_rate > 0:
                needed_energy = max(0, required_energy - self.current_energy)
                estimated_time_ms = needed_energy / current_rate
                logger.info(f"  理论恢复时间: {estimated_time_ms:.0f} ms")
            
            # 调用ASAP恢复
            result = self.wait_for_energy_recovery_asap(required_energy, current_time_ms, max_wait_time_ms)
            
            logger.info(f"[Python] 恢复结果: {result}")
            return result
            
        except Exception as e:
            logger.error(f"能量恢复异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def calculate_task_energy_debug(self, workload_type: str, execution_time_ms: float, 
                              frequency_mhz: float = 1400.0) -> Dict[str, Any]:
        """详细的任务能量计算（用于调试）"""
        workload_power = self.config.get_workload_power(workload_type)
        frequency_ratio = self.config.get_frequency_ratio(frequency_mhz)
        
        # 总功率计算
        total_power = self.config.base_power + workload_power * frequency_ratio
        
        # 能量计算
        execution_time_s = execution_time_ms / 1000.0
        energy_joules = total_power * execution_time_s
        
        result = {
            "workload_type": workload_type,
            "workload_power": workload_power,
            "frequency_mhz": frequency_mhz,
            "frequency_ratio": frequency_ratio,
            "base_power": self.config.base_power,
            "total_power": total_power,
            "execution_time_ms": execution_time_ms,
            "energy_joules": energy_joules,
            "formula": f"{self.config.base_power} + {workload_power} * {frequency_ratio} = {total_power}W"
        }
        
        return result

    def get_config_for_cpp(self) -> Dict[str, Any]:
        """获取配置供C++使用"""
        with self._lock:
            # 从系统配置中读取核心数
            num_cores = 4  # 默认值
            base_frequency = 1400.0  # 默认值
            
            if hasattr(self.config, 'system_config') and self.config.system_config:
                # 尝试从cpu_islands配置中读取核心数
                cpu_islands = self.config.system_config.get('cpu_islands', [])
                if cpu_islands:
                    first_island = cpu_islands[0]
                    # 尝试读取numcpus
                    if 'numcpus' in first_island:
                        num_cores = int(first_island['numcpus'])
                    # 尝试读取base_freq
                    if 'base_freq' in first_island:
                        base_frequency = float(first_island['base_freq'])
                    
                    # 尝试从scheduler_params中读取num_cores
                    kernel = first_island.get('kernel', {})
                    scheduler_params = kernel.get('scheduler_params', [])
                    for param in scheduler_params:
                        if isinstance(param, str) and param.startswith('num_cores='):
                            try:
                                num_cores = int(param.split('=')[1])
                            except (ValueError, IndexError):
                                pass
                        elif isinstance(param, str) and param.startswith('base_frequency='):
                            try:
                                base_frequency = float(param.split('=')[1])
                            except (ValueError, IndexError):
                                pass

            # 智能路径处理：为C++提供太阳能数据文件路径
            import os
            solar_file = self.config.solar_data_file
            if not os.path.exists(solar_file) and 'rtsim' in os.getcwd().split(os.sep):
                solar_file = os.path.join('..', solar_file)

            config_dict = {
                "num_cores": num_cores,
                "base_frequency": base_frequency,
                "unit_time": int(self.config.unit_time),
                "expected_task_count": 12,
                "initial_energy": float(self.config.initial_energy),
                "max_energy": float(self.config.max_energy),
                "base_harvest_rate": float(self.config.base_harvest_rate_per_ms),
                "start_time_offset": int(self.simulation_start_time),
                "enable_energy_recovery": bool(self.config.enable_energy_recovery),
                "periodic_collection_interval": int(self.config.periodic_collection_interval),
                "base_power": float(self.config.base_power),
                "power_coefficients": dict(self.config.power_coefficients),
                "frequency_power_ratios": dict(self.config.frequency_power_ratios),
                # 太阳能相关配置 - C++需要
                "solar_data_file": solar_file,
                "use_real_solar_data": bool(self.config.use_real_solar_data),
                "pv_efficiency": float(self.config.pv_efficiency),
                "pv_area_m2": float(self.config.pv_area_m2)
            }
            
            logger.info(f"[Python] 返回C++配置: 核心数={config_dict['num_cores']}, "
                       f"基础频率={config_dict['base_frequency']}MHz, "
                       f"初始能量={config_dict['initial_energy']}J")
            return config_dict
    
    # 在 EnergyManager 类中修改以下函数

    def wait_for_energy_recovery_asap_fixed(self, required_energy: float, 
                                            current_time_ms: int,
                                            max_wait_time_ms: int = None) -> bool:
        """ASAP调度专用：等待能量恢复 - 修复版，解决恢复循环问题"""
        with self._lock:
            if max_wait_time_ms is None:
                max_wait_time_ms = self.config.max_recovery_wait_time_ms
            
            if not self.config.enable_energy_recovery:
                logger.warning("[Python] 能量恢复被禁用")
                return False
            
            # 检查微小能量缺口，避免恢复循环
            energy_gap = required_energy - self.current_energy
            
            # === 关键修复：忽略微小能量缺口 ===
            if energy_gap <= 0.001:  # 小于1mJ的缺口
                logger.info(f"[Python] 微小能量缺口({energy_gap:.6f}J) < 0.001J，视为足够")
                return True
            
            # 记录恢复开始状态
            start_energy = self.current_energy
            
            logger.info(f"[Python] ASAP能量恢复计算:")
            logger.info(f"  需要能量: {required_energy:.6f}J")
            logger.info(f"  当前能量: {start_energy:.6f}J")
            logger.info(f"  能量缺口: {energy_gap:.6f}J")
            logger.info(f"  绝对时间: {current_time_ms}ms")
            
            # 如果已经足够，直接返回
            if start_energy >= required_energy:
                logger.info("[Python] 能量已足够，无需恢复")
                return True
            
            # 获取当前收集率（使用绝对时间）
            harvest_rate = self.harvester.get_harvesting_rate(current_time_ms)
            
            logger.info(f"  收集率: {harvest_rate*1000:.3f} J/s")
            
            if harvest_rate <= 0:
                logger.warning("[Python] 收集率为0，无法恢复")
                return False
            
            # 计算理论恢复时间
            estimated_time_ms = energy_gap / harvest_rate
            logger.info(f"  理论恢复时间: {estimated_time_ms:.0f} ms")
            
            # 限制最大等待时间
            if estimated_time_ms > max_wait_time_ms:
                logger.warning(f"[Python] 恢复时间过长: {estimated_time_ms:.0f}ms > {max_wait_time_ms}ms")
                return False
            
            # === 关键修复：不进行虚拟等待，只返回理论时间 ===
            # 实际时间推进由C++调度器通过仿真时钟完成
            logger.info("[Python] ✅ 理论恢复可行")
            return True



    # energy_manager.py - EnergyManager类中添加以下方法
    def sync_energy_state_with_cpp(self, cpp_energy_value: float) -> float:
        """与C++端同步能量状态 - 关键修复函数"""
        with self._lock:
            logger.info(f"[Python] 同步能量状态: C++报告={cpp_energy_value:.6f}J, Python当前={self.current_energy:.6f}J")
            
            # 计算差异
            energy_diff = abs(cpp_energy_value - self.current_energy)
            
            if energy_diff > 0.1:  # 差异超过0.1J，进行修正
                logger.warning(f"[Python] 能量状态不一致! 差异={energy_diff:.6f}J")
                logger.warning(f"[Python] C++端: {cpp_energy_value:.6f}J")
                logger.warning(f"[Python] Python端: {self.current_energy:.6f}J")
                
                # 使用C++端的值作为权威值（因为C++调用Python）
                old_energy = self.current_energy
                self.current_energy = max(0.0, min(cpp_energy_value, self.config.max_energy))
                
                logger.info(f"[Python] 能量状态已同步: {old_energy:.6f}J -> {self.current_energy:.6f}J")
                
                # 重新计算消耗和收集的差值，确保总账平衡
                expected_total = self.config.initial_energy + self.total_harvested - self.total_consumed
                adjustment = self.current_energy - expected_total
                
                if abs(adjustment) > 0.01:
                    logger.debug(f"[Python] 调整记录: 期望={expected_total:.6f}J, 实际={self.current_energy:.6f}J")
                    # 将调整计入收集量（假设是收集/消耗记录有误）
                    if adjustment > 0:
                        self.total_harvested += adjustment
                    else:
                        self.total_consumed -= adjustment
            
            return self.current_energy

    def get_energy_state_for_cpp(self) -> Dict[str, Any]:
        """获取能量状态供C++端使用"""
        with self._lock:
            return {
                "current_energy": float(self.current_energy),
                "total_consumed": float(self.total_consumed),
                "total_harvested": float(self.total_harvested),
                "max_energy": float(self.config.max_energy),
                "simulation_start_time": int(self.simulation_start_time)
            }


    def validate_energy_calculations(self):
        """验证能量计算一致性"""
        logger.info("=== 能量计算验证 ===")
        
        # 1. 验证配置文件中的收集率
        base_rate_jps = self.config.base_harvest_rate_per_ms * 1000
        logger.info(f"配置收集率: {base_rate_jps:.3f} J/s")
        logger.info(f"太阳能倍率: {self.config.solar_multiplier}")
        logger.info(f"风能倍率: {self.config.wind_multiplier}")
        
        # 2. 计算中午12点的理论收集率
        noon_absolute_time = 12 * 3600000  # 12:00:00
        noon_rate = self.harvester.get_harvesting_rate(noon_absolute_time)
        logger.info(f"中午12点收集率: {noon_rate*1000:.3f} J/s")
        
        # 3. 验证单位时间能量计算
        logger.info("\n单位时间(50ms)能量计算验证:")
        
        test_workloads = [
            ("encrypt", 1.5, "加密任务"),
            ("decrypt", 1.5, "解密任务"),
            ("hash", 0.8, "哈希任务"),
            ("bzip2", 1.2, "压缩任务"),
            ("control", 0.1, "控制任务"),
        ]
        
        for workload_type, expected_power, description in test_workloads:
            # 使用配置函数计算
            unit_energy = self.config.get_unit_time_energy(workload_type, 1400.0)
            
            # 手动计算验证
            base_power = self.config.base_power
            workload_power = self.config.get_workload_power(workload_type)
            freq_ratio = self.config.get_frequency_ratio(1400.0)
            
            total_power = base_power + workload_power * freq_ratio
            manual_energy = total_power * (self.config.unit_time / 1000.0)
            
            logger.info(f"{description}: {unit_energy:.6f}J (函数) / {manual_energy:.6f}J (手动)")
            
            # 检查一致性
            if abs(unit_energy - manual_energy) > 0.0001:
                logger.warning(f"{workload_type} 能量计算不一致!")
        
        # 4. 根据你的日志计算实际收集率
        # 实际: 9706ms收集0.11196J
        actual_rate = 0.11196 / 9.706  # J/s
        logger.info(f"\n实际收集率(来自日志): {actual_rate:.3f} J/s")
        logger.info(f"理论中午收集率: {noon_rate*1000:.3f} J/s")
        logger.info(f"差异: {actual_rate - noon_rate*1000:.3f} J/s")
        
        logger.info("=== 验证完成 ===")


    def consume_energy(self, energy_joules: float, task_name: str = "unknown") -> bool:
        """消耗能量 - 修复版，带精确能量检查"""
        with self._lock:
            if energy_joules <= 0:
                return True
            
            # 修复：添加更精确的能量检查
            if energy_joules <= self.current_energy + 1e-10:  # 添加容差
                self.current_energy -= energy_joules
                self.total_consumed += energy_joules
                
                if task_name not in self.task_energy_records:
                    self.task_energy_records[task_name] = 0.0
                self.task_energy_records[task_name] += energy_joules
                
                self.record_energy_level()
                
                # 输出消耗日志
                if self.config.debug_mode and (energy_joules > 0.001 or task_name.endswith("_asap")):
                    logger.debug(f"消耗能量: {energy_joules:.6f}J, 任务: {task_name}, "
                                f"剩余: {self.current_energy:.1f}J")
                
                return True
            else:
                logger.warning(f"能量不足! 需要: {energy_joules:.6f}J, 可用: {self.current_energy:.1f}J")
                return False
    
    def harvest_energy(self, current_time_ms: int, duration_ms: int) -> float:
        """收集能量 - 修复版"""
        with self._lock:
            if duration_ms <= 0 or current_time_ms < 0:
                return 0.0
            
            # 使用绝对时间获取收集率
            harvest_rate = self.harvester.get_harvesting_rate(current_time_ms)
            
            # 计算收集的能量
            total_harvested = harvest_rate * duration_ms        # 移除限制：直接使用NASA数据计算的收集量，不进行人为限制
            
            # 确保非负
            total_harvested = max(total_harvested, 0.0)
            
            if self.config.debug_mode and total_harvested > 0.001:
                hour = (current_time_ms // 3600000) % 24
                minute = (current_time_ms % 3600000) // 60000
                logger.debug(f"收集计算: {total_harvested:.6f}J, 持续时间: {duration_ms}ms, "
                            f"时间: {hour:02d}:{minute:02d}, 收集率: {harvest_rate:.6f}J/ms")
            
            return total_harvested

    # energy_manager.py - 优化 update_energy_continuously_wrapper 函数
    def update_energy_continuously_wrapper(self, current_time_ms: int) -> float:
        """C++兼容性接口：连续更新能量收集 - 最终修复版"""
        try:
            # === 关键修复：current_time_ms已经是绝对时间 ===
            # C++端已经将仿真时间转换为绝对时间
            # 我们直接使用这个时间进行能量收集计算
            
            if current_time_ms < 0:
                logger.warning(f"[Python] 无效的绝对时间: {current_time_ms}ms")
                return 0.0
            
            # === 关键修复：根据ASAP算法，在恢复期间也应该收集能量 ===
            # 检查恢复状态，但继续收集能量
            if self.recovery_in_progress:
                # 检查恢复是否已完成
                if current_time_ms >= self.recovery_end_time:
                    self.recovery_in_progress = False
                    logger.info(f"[Python] 恢复完成，恢复期间收集的能量已计入")
                else:
                    # 恢复期间，继续收集能量
                    remaining_time = self.recovery_end_time - current_time_ms
                    logger.debug(f"[Python] 恢复期间，继续收集能量 (剩余恢复时间: {remaining_time}ms)")
            
            # 记录时间信息（调试用）
            hour = (current_time_ms // 3600000) % 24
            minute = (current_time_ms % 3600000) // 60000
            second = (current_time_ms % 60000) // 1000
            
            logger.debug(f"[Python] 能量收集: 绝对时间={current_time_ms}ms ({hour:02d}:{minute:02d}:{second:02d})")
            
            # 直接调用内部收集函数
            harvested = self._update_energy_with_absolute_time(current_time_ms)
            
            return float(harvested)
            
        except Exception as e:
            logger.error(f"update_energy_continuously_wrapper 异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0.0

    def _harvest_energy_duration(self, start_time_ms: int, duration_ms: int, current_absolute_time_ms: int) -> float:
        """内部函数：收集指定持续时间的能量"""
        if duration_ms <= 0:
            return 0.0
        
        # 使用平均收集率
        start_rate = self.harvester.get_harvesting_rate(start_time_ms)
        end_rate = self.harvester.get_harvesting_rate(start_time_ms + duration_ms)
        avg_rate = (start_rate + end_rate) / 2.0
        
        # 计算收集的能量
        harvested = avg_rate * duration_ms
        
        # 移除限制：直接使用NASA数据计算的收集量
        
        # 确保非负
        harvested = max(harvested, 0.0)
        
        return harvested



    def update_energy_continuously(self, current_time_ms: int) -> float:
        """连续更新能量收集 - 修复版，实时收集能量"""
        with self._lock:
            # === 关键修复：首次调用时，设置last_update_time为当前时间 ===
            # 这样下次调用时才能正确计算时间差
            is_first_call = (self.last_update_time == 0)
            if is_first_call:
                # 首次调用，标记但不收集能量（因为没有时间间隔）
                self.last_update_time = current_time_ms
                logger.info(f"EnergyManager: 初始化update_energy_continuously，起始时间: {current_time_ms}ms")
                return 0.0

            time_elapsed = current_time_ms - self.last_update_time

            # 修复：如果时间没有流逝或倒退，不收集能量
            if time_elapsed <= 0:
                logger.debug(f"EnergyManager: 时间未流逝，跳过收集 current={current_time_ms} last={self.last_update_time}")
                return 0.0

            # === 关键修复：移除时间跳跃限制，允许实时收集 ===
            # NASA太阳能数据是实时的，应该按实际时间流逝收集

            # 正常时间流逝，实时收集能量
            logger.debug(f"EnergyManager: 收集能量 start={self.last_update_time}ms elapsed={time_elapsed}ms")
            harvested = self.harvest_energy(self.last_update_time, time_elapsed)

            if harvested > 0:
                actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                if actual_harvested > 0:
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                    self.record_energy_level()

                    # 记录收集信息（每收集1J以上才记录）
                    if actual_harvested > 1.0:
                        hour = (self.last_update_time // 3600000) % 24
                        minute = (self.last_update_time % 3600000) // 60000
                        logger.info(f"[能量收集] 时间: {hour:02d}:{minute:02d}, 收集: {actual_harvested:.3f}J, "
                                   f"间隔: {time_elapsed}ms, 当前能量: {self.current_energy:.3f}J")

            self.last_update_time = current_time_ms

            return harvested if harvested > 0 else 0.0


    def record_energy_level(self):
        """记录能量水平 - 修复版，添加能量更新事件记录"""
        level = self.get_energy_level()
        
        # 记录到历史
        self.energy_level_history.append({
            "time": self.last_update_time,
            "energy": self.current_energy,
            "level": level
        })
        
        # 记录能量更新事件（用于追踪文件）
        if self.config.debug_mode and self.last_update_time > 0:
            # 转换为仿真时间（减去开始时间偏移）
            simulation_time = self.last_update_time - self.simulation_start_time
            
            # 记录能量更新事件
            logger.info(f"[ENERGY_UPDATE] time={simulation_time}ms, "
                       f"energy={self.current_energy:.3f}J, "
                       f"level={level}, "
                       f"consumed={self.total_consumed:.3f}J, "
                       f"harvested={self.total_harvested:.3f}J")
            
            # 如果能量变化显著，记录更详细的信息
            if len(self.energy_level_history) >= 2:
                prev_energy = self.energy_level_history[-2]["energy"]
                energy_change = self.current_energy - prev_energy
                if abs(energy_change) > 0.01:  # 能量变化超过0.01J
                    logger.debug(f"[ENERGY_CHANGE] Δ={energy_change:.3f}J, "
                                f"from={prev_energy:.3f}J, "
                                f"to={self.current_energy:.3f}J")
    
    def get_energy_level(self) -> str:
        """获取能量水平"""
        if self.current_energy <= self.config.critical_energy:
            return "CRITICAL"
        elif self.current_energy <= self.config.low_energy:
            return "LOW"
        elif self.current_energy <= self.config.normal_energy:
            return "NORMAL"
        else:
            return "HIGH"
    
    # ========== 修复：添加ASAP专用接口 ==========
    
    def check_asap_scheduling(self, required_energy: float) -> bool:
        """ASAP调度专用：检查是否有足够能量"""
        with self._lock:
            has_sufficient = self.current_energy >= required_energy
            
            if self.config.debug_mode and not has_sufficient:
                logger.debug(f"ASAP调度检查: 需要{required_energy:.6f}J, 可用{self.current_energy:.1f}J, 不足")
            
            return has_sufficient
    

    def sync_energy_state(self):
        """同步能量状态 - 用于调试和验证"""
        with self._lock:
            logger.info("[Python] === 能量状态同步 ===")
            logger.info(f"  Python端 current_energy: {self.current_energy:.6f} J")
            logger.info(f"  Python端 total_consumed: {self.total_consumed:.6f} J")
            logger.info(f"  Python端 total_harvested: {self.total_harvested:.6f} J")
            
            # 计算期望的能量值
            expected_energy = (self.config.initial_energy + 
                            self.total_harvested - 
                            self.total_consumed)
            logger.info(f"  期望能量: {expected_energy:.6f} J")
            logger.info(f"  实际能量: {self.current_energy:.6f} J")
            logger.info(f"  差异: {abs(expected_energy - self.current_energy):.6f} J")
            
            # 如果有差异，进行修正
            if abs(expected_energy - self.current_energy) > 0.1:  # 0.1J的容差
                logger.warning(f"  能量状态不一致！进行修正")
                self.current_energy = max(0.0, min(expected_energy, self.config.max_energy))
                logger.info(f"  修正后能量: {self.current_energy:.6f} J")
            
            logger.info("[Python] === 同步完成 ===")
            return self.current_energy

    def get_harvesting_rate_wrapper(self, current_time_ms: int) -> float:
        """C++兼容性接口：获取收集率 - 修复时间传递问题"""
        try:
            # === 关键修复：直接使用传入的绝对时间 ===
            if current_time_ms < 0:
                logger.warning(f"[Python] 无效的时间: {current_time_ms}ms")
                return self.config.base_harvest_rate_per_ms
            
            # 直接使用这个时间
            absolute_time_ms = current_time_ms
            
            # 调用harvester的获取收集率方法
            rate = self.harvester.get_harvesting_rate(absolute_time_ms)
            
            return float(rate)
            
        except Exception as e:
            logger.error(f"[Python] get_harvesting_rate_wrapper异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self.config.base_harvest_rate_per_ms

    def wait_for_energy_recovery_asap(self, required_energy: float, 
                                    current_time_ms: int,
                                    max_wait_time_ms: int = None) -> bool:
        """ASAP调度专用：等待能量恢复 - 修复版"""
        if max_wait_time_ms is None:
            max_wait_time_ms = self.config.max_recovery_wait_time_ms
        
        if not self.config.enable_energy_recovery:
            logger.warning("Energy recovery is disabled")
            return False
        
        with self._lock:
            # 保存恢复目标状态
            self.asap_recovery_target = "asap_task"
            self.asap_recovery_start_time = current_time_ms
            self.asap_recovery_required_energy = required_energy
            
            start_time = current_time_ms
            max_end_time = current_time_ms + max_wait_time_ms
            
            logger.info(f"ASAP能量恢复开始: 需要{required_energy:.6f}J, 当前{self.current_energy:.1f}J, "
                    f"最多等待{max_wait_time_ms}ms")
            
            # === 关键修复：使用绝对时间进行收集率计算 ===
            absolute_start_time = current_time_ms + self.simulation_start_time
            
            # 简化恢复逻辑：直接计算需要等待的时间
            current_rate = self.harvester.get_harvesting_rate(absolute_start_time)
            
            if current_rate <= 0:
                logger.warning("当前收集率为0，无法恢复能量")
                self.asap_recovery_target = None
                return False
            
            # 计算需要的收集时间
            energy_needed = max(0, required_energy - self.current_energy)
            if energy_needed <= 0:
                logger.info("能量已足够，无需恢复")
                self.asap_recovery_target = None
                return True
            
            estimated_wait_time = energy_needed / current_rate
            
            logger.info(f"恢复参数: 需要能量{energy_needed:.6f}J, "
                    f"当前收集率{current_rate*1000:.3f}J/s, "
                    f"预计恢复时间{estimated_wait_time:.0f}ms")
            
            if estimated_wait_time > max_wait_time_ms:
                logger.warning(f"预计恢复时间{estimated_wait_time:.0f}ms超过最大等待时间{max_wait_time_ms}ms")
                self.asap_recovery_target = None
                return False
            
            # 模拟等待过程（分段收集）
            wait_segments = int(estimated_wait_time / 1000) + 1
            segment_duration = estimated_wait_time / wait_segments
            
            logger.info(f"分段恢复: {wait_segments}段, 每段{segment_duration:.0f}ms")
            
            for i in range(wait_segments):
                segment_start_sim = start_time + i * segment_duration
                segment_start_absolute = segment_start_sim + self.simulation_start_time
                
                # 收集这段时间的能量（使用绝对时间）
                harvested = self.harvest_energy(segment_start_absolute, segment_duration)
                
                if harvested > 0:
                    # 实际收集
                    actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                    
                    logger.debug(f"恢复进度 {i+1}/{wait_segments}: +{actual_harvested:.6f}J, "
                            f"当前{self.current_energy:.1f}J")
                
                # 检查是否满足需求
                if self.current_energy >= required_energy:
                    logger.info(f"✅ ASAP能量恢复完成: {self.current_energy:.1f}J >= {required_energy:.6f}J")
                    self.asap_recovery_target = None
                    return True
            
            logger.warning(f"ASAP能量恢复未完成: {self.current_energy:.1f}J < {required_energy:.6f}J")
            self.asap_recovery_target = None
            return False
    # ========== 通用接口 ==========
    
    def get_energy_status_dict(self) -> Dict[str, Any]:
        """获取能量状态字典"""
        with self._lock:
            simulation_time = self.last_update_time - self.simulation_start_time
            current_hour = (self.last_update_time // 3600000) % 24
            current_minute = (self.last_update_time % 3600000) // 60000
            
            status = {
                "current_energy": float(self.current_energy),
                "max_energy": float(self.config.max_energy),
                "energy_level": str(self.get_energy_level()),
                "total_consumed": float(self.total_consumed),
                "total_harvested": float(self.total_harvested),
                "solar_harvested": float(self.harvester.total_solar_harvested),
                "wind_harvested": float(self.harvester.total_wind_harvested),
                "start_time_offset": int(self.simulation_start_time),
                "simulation_time": int(simulation_time),
                "absolute_time": int(self.last_update_time),
                "current_hour": int(current_hour),
                "current_minute": int(current_minute),
                "available_energy": float(self.current_energy),
                "unit_time_ms": int(self.config.unit_time),
                "asap_recovery_in_progress": self.asap_recovery_target is not None,
                "asap_recovery_required": float(self.asap_recovery_required_energy) if self.asap_recovery_target else 0.0
            }
            
            return status
    
    def get_detailed_energy_status(self) -> str:
        """获取详细能量状态字符串"""
        status = self.get_energy_status_dict()
        
        start_hour = (status['start_time_offset'] // 3600000) % 24
        start_minute = (status['start_time_offset'] % 3600000) // 60000
        
        detailed = f"""
=== Detailed Energy Status ===
Current Energy: {status['current_energy']:.1f} J
Max Capacity: {status['max_energy']:.1f} J
Energy Level: {status['energy_level']}
Total Consumed: {status['total_consumed']:.1f} J
Total Harvested: {status['total_harvested']:.1f} J
  - Solar: {status['solar_harvested']:.1f} J
  - Wind: {status['wind_harvested']:.1f} J
Available Energy: {status['available_energy']:.1f} J
Start Time: {status['start_time_offset']} ms ({start_hour:02d}:{start_minute:02d})
Current Time: {status['absolute_time']} ms ({status['current_hour']:02d}:{status['current_minute']:02d})
Unit Time: {status['unit_time_ms']} ms
ASAP Recovery: {'IN PROGRESS' if status['asap_recovery_in_progress'] else 'IDLE'}
"""
        
        if self.asap_recovery_target:
            detailed += f"Recovery Target: {self.asap_recovery_target}\n"
            detailed += f"Required Energy: {self.asap_recovery_required_energy:.6f} J\n"
        
        if self.task_energy_records:
            detailed += "\nTask Energy Consumption:\n"
            for task, energy in sorted(self.task_energy_records.items(), key=lambda x: x[1], reverse=True)[:10]:
                detailed += f"  {task}: {energy:.3f} J\n"
        
        detailed += "============================"
        return detailed
    
    def has_sufficient_energy(self, required_energy: float) -> bool:
        """检查是否有足够能量"""
        with self._lock:
            return required_energy <= self.current_energy
    
    def wait_for_energy_recovery(self, required_energy: float, current_time_ms: int, 
                               max_wait_time_ms: int = None) -> bool:
        """通用等待能量恢复"""
        # 默认使用ASAP版本的恢复
        return self.wait_for_energy_recovery_asap(required_energy, current_time_ms, max_wait_time_ms)
    
    def calculate_task_energy(self, workload_type: str, execution_time_ms: float, 
                            frequency_mhz: float = 1400.0) -> float:
        """计算任务能量消耗"""
        return self.config.calculate_task_energy(workload_type, execution_time_ms, frequency_mhz)
    
    def has_sufficient_energy_for_batch(self, task_workloads: List[str], 
                                       execution_time_ms: float, 
                                       frequency_mhz: float = 1400.0) -> bool:
        """检查是否有足够能量执行批量任务"""
        total_energy_required = 0.0
        
        for workload in task_workloads:
            task_energy = self.calculate_task_energy(workload, execution_time_ms, frequency_mhz)
            total_energy_required += task_energy
        
        current_energy = self.current_energy
        has_sufficient = current_energy >= total_energy_required
        
        if self.config.debug_mode and len(task_workloads) > 1:
            logger.debug(f"批量能量检查: {len(task_workloads)}个任务, "
                        f"需要{total_energy_required:.3f}J, 可用{current_energy:.1f}J, "
                        f"充足: {has_sufficient}")
        
        return has_sufficient
    
    # ========== C++兼容性接口 ==========
    
    def get_current_energy_value(self) -> float:
        with self._lock:
            return float(self.current_energy)
    
    def get_energy_status_string(self) -> str:
        status = self.get_energy_status_dict()
        return (f"Energy: {status['current_energy']:.1f}/{status['max_energy']:.1f} J "
                f"(Level: {status['energy_level']}, "
                f"Used: {status['total_consumed']:.1f} J, "
                f"Harvested: {status['total_harvested']:.1f} J)")


    # EnergyManager 类中的时间处理函数
    def update_energy_continuously_wrapper(self, current_time_ms: int) -> float:
        """C++兼容性接口：连续更新能量收集 - 最终修复版"""
        try:
            # === 关键修复：时间转换 ===
            # current_time_ms 是从C++传来的仿真时间
            # 需要转换为绝对时间进行能量收集计算
            
            # 检查时间有效性
            if current_time_ms < 0:
                logger.warning(f"无效的仿真时间: {current_time_ms}ms")
                return 0.0
            
            # 转换为绝对时间
            absolute_time_ms = current_time_ms  # 已经是绝对时间
            
            # 调用内部收集函数
            harvested = self._update_energy_with_absolute_time(absolute_time_ms)
            
            return float(harvested)
            
        except Exception as e:
            logger.error(f"update_energy_continuously_wrapper 异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0.0

    def _update_energy_with_absolute_time(self, absolute_time_ms: int) -> float:
        """使用绝对时间更新能量 - 修复版，避免初始化时收集过多能量"""
        with self._lock:
            # === 关键修复：检查能量收集是否启用 ===
            # 如果配置文件中禁用了能量收集，直接返回0
            harvesting_sources = self.config.__dict__.get('harvesting_sources', {})
            if isinstance(harvesting_sources, dict):
                solar_enabled = harvesting_sources.get('solar', {}).get('enabled', True)
                wind_enabled = harvesting_sources.get('wind', {}).get('enabled', True)
                if not solar_enabled and not wind_enabled:
                    # 只更新时间，不收集能量
                    if self.last_update_time == 0:
                        self.last_update_time = absolute_time_ms
                    else:
                        self.last_update_time = absolute_time_ms
                    return 0.0

            # 首次调用初始化
            # 首次调用初始化
            if self.last_update_time == 0:
                # === 关键修复：设置last_update_time为当前时间 ===
                # 这样下次调用时才能正确计算时间差并收集能量
                self.last_update_time = absolute_time_ms
                hour = int((absolute_time_ms // 3600000) % 24)
                minute = int((absolute_time_ms % 3600000) // 60000)
                logger.info(f"能量收集器初始化: 绝对时间={hour:02d}:{minute:02d}，起始时间={absolute_time_ms}ms")
                return 0.0  # 首次调用不收集能量（因为没有时间间隔）
            
            
            # 计算时间间隔
            time_elapsed = absolute_time_ms - self.last_update_time
            
            # 时间未前进或倒退
            if time_elapsed <= 0:
                return 0.0
            
            # === 修复：移除大时间跳跃的限制 ===
            # 原来的逻辑会跳过大时间跳跃的能量收集，这导致部分能量丢失
            # 现在所有时间间隔都应该正常收集能量
            # if time_elapsed > 5000 and self.last_update_time == self.simulation_start_time:
            #     logger.info(f"仿真开始时间跳跃: {time_elapsed}ms，不收集初始化能量")
            #     self.last_update_time = absolute_time_ms
            #     return 0.0
            
            # 正常时间流逝
            # 使用平均收集率计算这段时间收集的能量
            start_rate = self.harvester.get_harvesting_rate(self.last_update_time)
            end_rate = self.harvester.get_harvesting_rate(absolute_time_ms)
            avg_rate = (start_rate + end_rate) / 2.0
            harvested = avg_rate * time_elapsed
            
            # 确保非负
            harvested = max(harvested, 0.0)
            
            # 实际收集能量
            actual_harvested = 0.0
            if harvested > 0:
                actual_harvested = min(harvested, self.config.max_energy - self.current_energy)
                if actual_harvested > 0:
                    self.current_energy += actual_harvested
                    self.total_harvested += actual_harvested
                    
                    # 调试输出
                    if self.config.debug_mode and actual_harvested > 0.001:
                        hour = int((absolute_time_ms // 3600000) % 24)
                        minute = int((absolute_time_ms % 3600000) // 60000)
                        logger.debug(f"能量收集: {actual_harvested:.6f}J, "
                                    f"时间间隔: {time_elapsed}ms, "
                                    f"收集率: {avg_rate*1000:.3f}J/s, "
                                    f"当前时间: {hour:02d}:{minute:02d}")
            
            # 更新时间
            self.last_update_time = absolute_time_ms
            
            return actual_harvested

    def set_recovery_state(self, recovery_in_progress: bool, recovery_end_time_ms: int = 0):
        """设置恢复状态 - 供C++调度器调用"""
        with self._lock:
            self.recovery_in_progress = recovery_in_progress
            self.recovery_end_time = recovery_end_time_ms

            if recovery_in_progress:
                logger.info(f"[Python] 设置恢复状态: 恢复进行中，结束时间={recovery_end_time_ms}ms")
            else:
                logger.info(f"[Python] 设置恢复状态: 恢复结束")

    def set_recovery_state_wrapper(self, recovery_in_progress: bool, recovery_end_time_ms: int = 0) -> bool:
        """C++兼容性接口：设置恢复状态（实例方法）"""
        try:
            self.set_recovery_state(recovery_in_progress, recovery_end_time_ms)
            return True
        except Exception as e:
            logger.error(f"[Python] 设置恢复状态异常: {e}")
            return False


    # 添加新的内部函数
    def update_energy_continuously_wrapper_internal(self, current_time_ms: int) -> float:
        """内部实现：连续更新能量收集 - 修复时间跳跃问题"""
        with self._lock:
            # 修复：对于初始调用，只设置时间，不收集能量
            if self.last_update_time == 0:
                self.last_update_time = current_time_ms
                logger.debug(f"初始化能量收集器，设置last_update_time为: {current_time_ms}ms")
                return 0.0
            
            time_elapsed = current_time_ms - self.last_update_time
            
            # 修复：如果时间没有流逝或倒退，不收集能量
            if time_elapsed <= 0:
                return 0.0
            
            # 修复：避免过大的时间跳跃，特别是初始化时的跳跃
            max_time_jump = 1000  # 最大1秒跳跃
            if time_elapsed > max_time_jump:
                logger.warning(f"时间跳跃过大: {time_elapsed}ms > {max_time_jump}ms，进行安全处理")
                
                # 对于初始化跳跃，我们只收集一小段时间，避免过度收集
                # 假设系统从空闲状态开始，收集1秒的能量
                harvest_duration = min(time_elapsed, max_time_jump)
                harvested = self.harvest_energy(
                    int(self.last_update_time), 
                    int(harvest_duration)
                )
                
                # 更新最后更新时间
                self.last_update_time = current_time_ms
                
                if self.config.debug_mode:
                    logger.debug(f"大时间跳跃安全收集: {harvested:.6f}J, 时间间隔: {time_elapsed}ms, 实际收集时间: {harvest_duration}ms")
                
                return harvested
            else:
                # 正常时间流逝
                harvested = self.harvest_energy(
                    int(self.last_update_time), 
                    int(time_elapsed)
                )
                self.last_update_time = current_time_ms
                
                if self.config.debug_mode and harvested > 0.001:
                    logger.debug(f"正常能量收集: {harvested:.6f}J, 时间间隔: {time_elapsed}ms")
                
                return harvested



    def get_harvesting_rate(self, current_time_ms: int) -> float:
        """获取当前收集率 (焦耳/毫秒)"""
        with self._lock:
            # 使用缓存提高性能
            cache_key = current_time_ms // 1000  # 按秒缓存
            
            if cache_key in self._harvest_rate_cache:
                self._cache_hit += 1
                return self._harvest_rate_cache[cache_key]
            
            self._cache_miss += 1
            
            # 使用绝对时间（当前仿真时间 + 开始时间偏移）
            absolute_time_ms = current_time_ms  # 已经是绝对时间，不需要再加偏移
            
            # 确保时间为整数
            hour = int((absolute_time_ms // 3600000) % 24)
            minute = int((absolute_time_ms % 3600000) // 60000)
            
            # 基础收集率
            base_rate = self.config.base_harvest_rate_per_ms
            
            # 根据小时调整收集率
            # 中午12点最高，早晚较低
            if hour == 12:
                multiplier = self.config.solar_multiplier  # 中午最强
            elif 6 <= hour <= 18:
                # 白天：线性变化
                hour_offset = abs(hour - 12)
                multiplier = self.config.solar_multiplier * (1.0 - hour_offset / 12.0 * 0.7)
            else:
                # 晚上：使用风能
                multiplier = self.config.wind_multiplier * 0.5
            
            # 确保最小倍数
            multiplier = max(multiplier, 0.1)
            
            # 计算总收集率
            total_rate = base_rate * multiplier
            
            # 季节调整
            season = self.get_season_from_time(absolute_time_ms)
            seasonal_factor = self.config.seasonal_factors.get(season, 1.0)
            total_rate *= seasonal_factor
            
            # 确保最小收集率
            total_rate = max(total_rate, 0.000001)
            
            # 调试输出
            if self.config.debug_mode:
                logger.debug(f"收集率: {total_rate:.6f} J/ms ({total_rate*1000:.3f} J/s) "
                            f"at {hour:02d}:{minute:02d}, multiplier={multiplier:.2f}, "
                            f"season={season}")
            
            # 缓存结果
            if len(self._harvest_rate_cache) >= self._cache_max_size:
                keys_to_remove = list(self._harvest_rate_cache.keys())[:self._cache_max_size//2]
                for key in keys_to_remove:
                    del self._harvest_rate_cache[key]
            
            self._harvest_rate_cache[cache_key] = total_rate
            
            return total_rate
    
    def wait_for_energy_recovery_wrapper(self, required_energy: float, 
                                       current_time_ms: int, 
                                       max_wait_time_ms: int = 10000) -> bool:
        return bool(self.wait_for_energy_recovery(required_energy, current_time_ms, max_wait_time_ms))

# 全局实例管理
_global_energy_manager = None
_global_energy_manager_lock = threading.Lock()

def get_energy_manager(config_file: str = None, verbose: bool = True) -> EnergyManager:
    """获取全局能量管理器实例 - 修复版，确保配置文件正确加载"""
    global _global_energy_manager
    
    with _global_energy_manager_lock:
        # 如果已经存在全局实例，但需要重新加载配置
        if _global_energy_manager is not None and config_file:
            try:
                # 检查是否需要重新加载配置
                current_config_file = getattr(_global_energy_manager.config, 'config_file', None)
                logger.info(f"[get_energy_manager] config_file={config_file}, current_config_file={current_config_file}")
                if current_config_file != config_file:
                    logger.info(f"重新加载配置文件: {current_config_file} -> {config_file}")
                    success = _global_energy_manager.load_system_config(config_file)
                    if not success:
                        logger.warning(f"重新加载配置文件失败: {config_file}")
                else:
                    logger.info(f"配置文件相同，无需重新加载: {config_file}")
            except Exception as e:
                logger.error(f"重新加载配置文件异常: {e}")
        
        # 创建新的全局实例（如果不存在）
        if _global_energy_manager is None:
            try:
                _global_energy_manager = EnergyManager(config_file, verbose)
                logger.info("全局EnergyManager实例创建成功")
                
                # 确保配置文件已加载
                if config_file and not _global_energy_manager.config.system_config:
                    logger.info(f"确保配置文件加载: {config_file}")
                    success = _global_energy_manager.load_system_config(config_file)
                    if not success:
                        logger.warning(f"确保配置文件加载失败: {config_file}")
                        
            except Exception as e:
                logger.error(f"创建EnergyManager失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # 创建简单的后备管理器
                _global_energy_manager = create_fallback_manager()
        
        return _global_energy_manager

# C++兼容性接口 - 配置获取
def get_config_for_cpp() -> Dict[str, Any]:
    """C++兼容性接口：获取配置"""
    manager = get_energy_manager()
    try:
        if hasattr(manager, 'get_config_for_cpp'):
            return manager.get_config_for_cpp()
        else:
            logger.error("EnergyManager实例没有get_config_for_cpp方法")
            return {}
    except Exception as e:
        logger.error(f"获取配置异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {}

def create_fallback_manager():
    """创建简单的后备管理器"""
    class SimpleFallbackManager:
        def __init__(self):
            self.current_energy = 200.0
            self.max_energy = 600.0
            self.start_time_offset = 0
            self.last_update_time = 0
            self.total_consumed = 0.0
            self.total_harvested = 0.0
            logger.warning("使用简单的后备能量管理器")
        
        def set_start_time_offset(self, offset_ms):
            self.start_time_offset = offset_ms
            self.last_update_time = offset_ms
            # 修复：返回True，因为C++端期望布尔值
            return True
        
        def get_current_energy_value(self):
            return self.current_energy
        
        def consume_energy(self, energy, task_name):
            if energy <= self.current_energy:
                self.current_energy -= energy
                return True
            return False
        
        def update_energy_continuously_wrapper(self, current_time_ms):
            if self.last_update_time == 0:
                self.last_update_time = current_time_ms
                return 0.0
            
            time_elapsed = current_time_ms - self.last_update_time
            if time_elapsed > 0:
                # 简单收集：每秒收集0.02J
                harvested = 0.00002 * time_elapsed
                self.current_energy = min(self.max_energy, self.current_energy + harvested)
                self.last_update_time = current_time_ms
                return harvested
            return 0.0
        
        def get_harvesting_rate_wrapper(self, current_time_ms):
            return 0.00002  # 固定收集率
        
        def wait_for_energy_recovery_wrapper(self, current_time_ms, required_energy, max_wait=10000):
            # 简单实现：直接返回False，让调度器等待
            return False
    
    return SimpleFallbackManager()

# C++兼容性接口
def load_system_config(config_file: str) -> bool:
    manager = get_energy_manager()
    return bool(manager.load_system_config(config_file))

def set_start_time_offset(offset_ms: int):
    manager = get_energy_manager()
    result = manager.set_start_time_offset(int(offset_ms))
    # 修复：返回布尔值，因为C++端期望布尔值
    return bool(result) if result is not None else True

def get_current_energy() -> float:
    manager = get_energy_manager()
    return float(manager.current_energy)

def get_harvesting_rate(current_time_ms: int) -> float:
    manager = get_energy_manager()
    return float(manager.harvester.get_harvesting_rate(current_time_ms))

def consume_energy(energy_joules: float, task_name: str) -> bool:
    manager = get_energy_manager()
    return bool(manager.consume_energy(energy_joules, task_name))

def update_energy_harvesting(current_time_ms: int, duration_ms: int) -> float:
    manager = get_energy_manager()
    return float(manager.harvest_energy(current_time_ms, duration_ms))

def update_energy_continuously(current_time_ms: int) -> float:
    manager = get_energy_manager()
    return float(manager.update_energy_continuously(current_time_ms))

def wait_for_energy_recovery(required_energy: float, current_time_ms: int, 
                           max_wait_time_ms: int = 10000) -> bool:
    manager = get_energy_manager()
    return bool(manager.wait_for_energy_recovery(required_energy, current_time_ms, max_wait_time_ms))

def has_sufficient_energy_for_batch(task_workloads: List[str], 
                                   execution_time_ms: float) -> bool:
    manager = get_energy_manager()
    return bool(manager.has_sufficient_energy_for_batch(task_workloads, execution_time_ms))

# C++兼容性接口
def update_energy_continuously_wrapper(current_time_ms: int) -> float:
    """C++兼容性接口：连续更新能量收集（模块级）- 最终修复版"""
    manager = get_energy_manager()
    
    try:
        # === 关键修复：确保时间一致性 ===
        # current_time_ms 是C++传来的绝对时间，直接使用
        
        if current_time_ms < 0:
            logger.warning(f"[Python] 模块级：无效的绝对时间: {current_time_ms}ms")
            return 0.0
        
        # 直接调用实例方法
        result = float(manager.update_energy_continuously_wrapper(current_time_ms))
        
        return result
    except Exception as e:
        logger.error(f"[Python] 模块级 update_energy_continuously_wrapper 异常: {e}")
        return 0.0

def get_harvesting_rate_wrapper(current_time_ms: int) -> float:
    """C++兼容性接口：获取收集率（模块级）- 最终修复版"""
    manager = get_energy_manager()
    
    try:
        # === 关键修复：直接使用传入的时间 ===
        return float(manager.get_harvesting_rate_wrapper(current_time_ms))
    except Exception as e:
        logger.error(f"[Python] 模块级 get_harvesting_rate_wrapper 异常: {e}")
        return manager.config.base_harvest_rate_per_ms

def wait_for_energy_recovery_wrapper(current_time_ms: int, 
                                   required_energy: float,
                                   max_wait_time_ms: int = 10000) -> bool:
    """C++兼容性接口：等待能量恢复"""
    manager = get_energy_manager()
    return bool(manager.wait_for_energy_recovery(required_energy, current_time_ms, max_wait_time_ms))

def get_energy_status() -> str:
    manager = get_energy_manager()
    status = manager.get_energy_status_dict()
    return (f"Energy: {status['current_energy']:.1f}/{status['max_energy']:.1f} J "
            f"(Level: {status['energy_level']}, "
            f"Used: {status['total_consumed']:.1f} J, "
            f"Harvested: {status['total_harvested']:.1f} J)")

def get_detailed_energy_status() -> str:
    manager = get_energy_manager()
    return str(manager.get_detailed_energy_status())

def calculate_task_energy_cpp(workload_type: str, execution_time_ms: float, 
                            frequency_mhz: float = 1400.0) -> float:
    manager = get_energy_manager()
    return float(manager.calculate_task_energy(workload_type, execution_time_ms, frequency_mhz))

# ASAP专用接口
def check_asap_scheduling(required_energy: float) -> bool:
    """ASAP调度专用：检查是否有足够能量"""
    manager = get_energy_manager()
    return bool(manager.check_asap_scheduling(required_energy))

def wait_for_energy_recovery_asap(required_energy: float, current_time_ms: int,
                                max_wait_time_ms: int = 10000) -> bool:
    """ASAP调度专用：等待能量恢复"""
    manager = get_energy_manager()
    return bool(manager.wait_for_energy_recovery_asap(required_energy, current_time_ms, max_wait_time_ms))

def set_recovery_state_wrapper(recovery_in_progress: bool, recovery_end_time_ms: int = 0) -> bool:
    """C++兼容性接口：设置恢复状态"""
    manager = get_energy_manager()
    try:
        manager.set_recovery_state(recovery_in_progress, recovery_end_time_ms)
        return True
    except Exception as e:
        logger.error(f"[Python] 设置恢复状态异常: {e}")
        return False

if __name__ == "__main__":
    # 简单测试
    print("Energy Manager Test")
    manager = get_energy_manager()
    manager.set_start_time_offset(43200000)
    print(f"Current energy: {manager.get_current_energy_value()} J")
    print(f"Energy status: {manager.get_energy_status_string()}")
