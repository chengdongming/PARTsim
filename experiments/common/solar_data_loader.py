"""
NASA太阳能数据加载器
将真实的辐照度数据转换为能量收集率
增强版：支持时间偏移，健壮的错误处理
依赖numpy进行高效数据处理
"""
import numpy as np
import os

class SolarDataLoader:
    def __init__(self, data_file_path: str, start_offset_minutes: int = 0):
        """
        加载预处理好的分钟级辐照度数据。
        
        参数:
            data_file_path: 数据文件路径
            start_offset_minutes: 时间偏移（分钟），用于调整仿真开始时间
        """
        if not os.path.exists(data_file_path):
            raise FileNotFoundError(f"太阳能数据文件不存在: {data_file_path}")
        
        # 使用numpy高效加载数据
        try:
            self.irradiance_data = np.loadtxt(data_file_path, delimiter=',', skiprows=1)
        except Exception as e:
            raise ValueError(f"无法加载数据文件 {data_file_path}: {e}")
        
        # 处理缺失值（-999表示缺失）
        self.irradiance_data = np.where(self.irradiance_data < 0, 0.0, self.irradiance_data)
        
        # 时间偏移
        self.start_offset_minutes = start_offset_minutes % len(self.irradiance_data)
        
        print(f"[SolarDataLoader] 已加载 {len(self.irradiance_data)} 分钟数据")
        print(f"[SolarDataLoader] 时间偏移: {self.start_offset_minutes} 分钟")
        print(f"[SolarDataLoader] 数据范围: {np.min(self.irradiance_data):.1f} - {np.max(self.irradiance_data):.1f} W/m²")
        print(f"[SolarDataLoader] 平均值: {np.mean(self.irradiance_data):.1f} W/m²")
        print(f"[SolarDataLoader] 标准差: {np.std(self.irradiance_data):.1f} W/m²")
    
    def get_harvesting_rate(self, absolute_time_ms: int, pv_efficiency: float, pv_area_m2: float) -> float:
        """
        根据仿真时间，计算当���的能量收集率 (J/ms)。

        核心公式：收集率 = 辐照度(W/m²) × 效率 × 面积(m²) × (1/1000)

        参数:
            absolute_time_ms: 绝对时间（毫秒），已包含时间偏移
            pv_efficiency: 光伏转换效率 (0.0-1.0)
            pv_area_m2: 光伏板面积（平方米）

        返回:
            能量收集率 (焦耳/毫秒)
        """
        # 1. 将仿真毫秒转换为分钟索引
        # 注意：absolute_time_ms 已经包含了时间偏移，不需要再加 start_offset_minutes
        total_minutes = absolute_time_ms // 60000  # 毫秒 -> 分钟

        # 2. 直接使用传入的时间（absolute_time_ms已包含偏移）
        # 修复：移除双重偏移问题
        data_index = total_minutes % len(self.irradiance_data)

        # 3. 获取当前分钟的辐照度
        current_irradiance = self.irradiance_data[data_index]

        # 4. 应用物理公式，转换为收集率
        harvest_rate = current_irradiance * pv_efficiency * pv_area_m2 / 1000.0

        # 5. 确保非负
        return max(harvest_rate, 0.0)
    
    def get_irradiance_at_time(self, absolute_time_ms: int) -> float:
        """
        直接获取指定时间的辐照度值（用于调试和验证）
        注意：absolute_time_ms 已包含时间偏移
        """
        total_minutes = absolute_time_ms // 60000
        # 修复：移除双重偏移
        data_index = total_minutes % len(self.irradiance_data)
        return self.irradiance_data[data_index]
    
    def get_irradiance_for_period(self, start_minute: int, duration_minutes: int) -> np.ndarray:
        """
        获取指定时间段的辐照度数据（用于验证）
        
        参数:
            start_minute: 开始时间（分钟）
            duration_minutes: 持续时间（分钟）
        
        返回:
            辐照度数组 (W/m²)
        """
        start_idx = (start_minute + self.start_offset_minutes) % len(self.irradiance_data)
        end_idx = (start_minute + duration_minutes + self.start_offset_minutes) % len(self.irradiance_data)
        
        if end_idx > start_idx:
            return self.irradiance_data[start_idx:end_idx]
        else:
            # 处理循环情况
            return np.concatenate([self.irradiance_data[start_idx:], 
                                   self.irradiance_data[:end_idx]])
    
    def get_data_summary(self) -> dict:
        """
        获取数据统计信息
        """
        return {
            "total_minutes": len(self.irradiance_data),
            "min_irradiance": float(np.min(self.irradiance_data)),
            "max_irradiance": float(np.max(self.irradiance_data)),
            "avg_irradiance": float(np.mean(self.irradiance_data)),
            "std_irradiance": float(np.std(self.irradiance_data)),
            "median_irradiance": float(np.median(self.irradiance_data)),
            "start_offset_minutes": self.start_offset_minutes
        }
    
    def get_daily_profile(self, day_of_year: int) -> np.ndarray:
        """
        获取指定日期的日辐照度剖面（24小时）
        
        参数:
            day_of_year: 一年中的第几天 (0-364)
        
        返回:
            24小时辐照度数组 (W/m²)
        """
        start_minute = day_of_year * 24 * 60
        return self.get_irradiance_for_period(start_minute, 24 * 60)
    
    def get_hourly_average(self, hour_of_day: int) -> float:
        """
        获取指定小时的全年龄平均辐照度
        
        参数:
            hour_of_day: 一天中的小时 (0-23)
        
        返回:
            平均辐照度 (W/m²)
        """
        # 获取所有该小时的数据
        hourly_data = []
        for day in range(365):
            start_minute = day * 24 * 60 + hour_of_day * 60
            hourly_data.extend(self.get_irradiance_for_period(start_minute, 60))
        
        return float(np.mean(hourly_data))
