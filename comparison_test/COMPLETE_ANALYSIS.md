# ASAP vs EPP 能量收集差异 - 完整分析

**更新日期**: 2026-01-17
**核心发现**: 两者都使用第187天的配置，但读取的数据行不同

---

## ✅ 两者都使用第187天配置

### 配置文件
```yaml
day_of_year: 187
time_of_day_ms: 43200000  # 12:00:00
```

### EPP配置读取
```
从配置文件读取到时间偏移: 43200000 ms (12:00:00) ✅
start_time_offset: 16113600000 ms (第187天12:00)
```

### ASAP配置读取
```
start_offset_minutes: 268560 分钟
计算方式: (187-1) × 1440 + 720 = 268560 ✅
simulation_start_time: 16113600000 ms (第187天12:00)
```

**结论**: ✅ 两者都正确读取了第187天12:00的配置

---

## 🔴 问题：读取的数据行不同

### NASA数据文件结构

```
文件: shenyang_solar_minute.csv
总行数: 532800行 = 365天 × 1440分钟/天

第1行: 标题 "irradiance_W_per_m2"
第2行: 第1天00:00的辐照度
...
第722行: 第1天12:00的辐照度 = 434.50 W/m²
...
第269280行: 第187天12:00的辐照度 = 901.40 W/m²
```

### EPP读取方式

```cpp
// gpfp_epp_scheduler.cpp
int64_t actual_time_ms = time_ms + _start_time_offset;
// time_ms = 0 (仿真开始)
// _start_time_offset = 16113600000 (第187天12:00)
// actual_time_ms = 16113600000

int64_t minute_of_day = (actual_time_ms % 86400000) / 60000;
// = (16113600000 % 86400000) / 60000
// = 43200000 / 60000
// = 720

// 读取第720行（当天的12:00）
辐照度 = 434.50 W/m² ✅ 正确！
```

**EPP关键**: 使用`% 86400000`取**当天的分钟数**，所以总是读取第720行。

### ASAP读取方式

```python
# solar_data_loader.py
total_minutes = absolute_time_ms // 60000
# absolute_time_ms = simulation_start_time + time_ms
#                   = 16113600000 + 43200000  # 注意：这里加了time_of_day_ms!
#                   = 16156800000

total_minutes = 16156800000 // 60000 = 269280

data_index = total_minutes % len(self.irradiance_data)
            = 269280 % 532800
            = 269280

# 读取第269280行（第187天的12:00）
辐照度 = 901.40 W/m² ❌ 错误！
```

**ASAP问题**:
1. 使用`simulation_start_time + time_of_day_ms`作为实际时间
2. 直接用总分钟数作为索引
3. 读取了第269280行（第187天），而不是第720行（当天）

---

## 💡 根本差异

| 项目 | EPP | ASAP |
|------|-----|------|
| **时间偏移** | 16113600000 ms | 16113600000 ms |
| **实际时间计算** | `time_ms + offset` | `sim_start + time_of_day + time_ms` |
| **索引方式** | `(actual % 86400000) / 60000` | `actual / 60000 % len(data)` |
| **读取位置** | 第720行 | 第269280行 |
| **辐照度** | **434.50** W/m² ✅ | **901.40** W/m² ❌ |
| **能量收集** | **78.21 J** ✅ | **169.137 J** ❌ |

---

## 🎯 为什么ASAP多了12小时？

从日志看：
```
ASAP simulation_start_time: 16113600000 ms
ASAP absolute_time: 16156800000 ms
差异: 43200000 ms = 12小时
```

**原因**: ASAP在计算绝对时间时，又加了一次`time_of_day_ms`！

让我检查ASAP的`get_harvesting_rate`调用：

```python
# energy_manager.py - EnergyHarvester.get_harvesting_rate()
def get_harvesting_rate(self, absolute_time_ms: int, ...):
    # absolute_time_ms 来自 EnergyManager.update_energy()
    #   = simulation_start_time + elapsed_time

    # 但是 elapsed_time 可能包含 time_of_day_ms！
```

---

## ✅ 正确的解决方案

### 方案1: 修改SolarDataLoader使用当天分钟数

```python
# solar_data_loader.py
def get_harvesting_rate(self, absolute_time_ms: int, pv_efficiency: float, pv_area_m2: float) -> float:
    # ⭐ 修复：使用当天分钟数，而不是总分钟数
    minute_of_day = (absolute_time_ms % 86400000) // 60000  # 0-1439

    current_irradiance = self.irradiance_data[minute_of_day]
    harvest_rate = current_irradiance * pv_efficiency * pv_area_m2 / 1000.0

    return max(harvest_rate, 0.0)
```

### 方案2: 统一使用EPP的读取方式

让ASAP也使用C++的`getSolarIrradiance()`，确保一致性。

---

## 📝 总结

### 你的理解是对的！

**是的，EPP和ASAP都选择了第187天的配置**！

但它们读取NASA数据的方式不同：

- **EPP**: 读取**第720行**（假设数据文件只包含当天的1440分钟）
  - 辐照度: 434.50 W/m²
  - 能量: 78.21 J ✅

- **ASAP**: 读取**第269280行**（使用累积总分钟数作为索引）
  - 辐照度: 901.40 W/m²
  - 能量: 169.137 J ❌

### NASA数据文件的结构问题

**实际情况**:
- NASA数据文件包含532800行 = 365天 × 1440分钟
- 第1-1440行: 第1天
- 第1441-2880行: 第2天
- ...
- 第269281-283200行: 第187天

**EPP假设**:
- 数据文件只有1440行（一天的数据）
- 总是读取第720行（当天12:00）
- 实际读取: 第720行 = 第1天12:00 = 434.50 W/m²

**ASAP读取**:
- 使用完整的数据文件（532800行）
- 读取第269280行 = 第187天12:00 = 901.40 W/m²

### 谁是对的？

**EPP读取的434.50** 是**第1天12:00**的辐照度，不是第187天！

**ASAP读取的901.40** 才是**第187天12:00**的辐照度！

**所以ASAP是对的，EPP错了！** 🎯

---

**结论**: EPP需要修复，让它也能读取正确的第187天的数据，而不是总是读取第1天的数据。

