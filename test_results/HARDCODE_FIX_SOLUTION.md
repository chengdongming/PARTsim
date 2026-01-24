# 中高级硬编码问题修复方案

**文档版本：** 1.0
**创建时间：** 2026-01-24
**针对问题：** 白天时间、太阳能辐照度、CPU名称硬编码

---

## 🔴 问题1：白天时间和太阳能辐照度硬编码

### 问题描述

**当前代码（所有三个算法）：**
```cpp
// TIE: gpfp_tie_scheduler.cpp:1228
// TGF: gpfp_tgf_scheduler.cpp:1091
// BTIE: gpfp_btie_scheduler.cpp:1012

if (hour_of_day >= 6 && hour_of_day <= 18) {
    return 500.0;  // ❌ 硬编码：6-18点，500W/m²
}
```

### 修复方案

#### 方案A：最小侵入式修改（推荐）

**1. 在TIEScheduler类中添加配置成员变量**

**��件：** `librtsim/include/rtsim/scheduler/gpfp_tie_scheduler.hpp`

**在私有成员变量区域（太阳能配置部分）添加：**
```cpp
// ========== 太阳能配置 ==========
std::string _solar_data_file;
double _pv_efficiency;
double _pv_area_m2;
bool _use_real_solar_data;
MetaSim::Tick _start_time_offset;

// ⭐ 新增：白天时间配置
int _solar_day_start_hour = 6;      // 默认6点开始
int _solar_day_end_hour = 18;        // 默认18点结束
double _max_solar_irradiance = 500.0; // 默认500W/m²
```

**2. 添加配置读取方法**

**文件：** `librtsim/include/rtsim/scheduler/gpfp_tie_scheduler.hpp`

**在公共方法区域添加：**
```cpp
// ========== 太阳能配置方法 ==========
void setSolarDayTime(int start_hour, int end_hour);
void setMaxSolarIrradiance(double max_irradiance);
int getSolarDayStartHour() const { return _solar_day_start_hour; }
int getSolarDayEndHour() const { return _solar_day_end_hour; }
double getMaxSolarIrradiance() const { return _max_solar_irradiance; }
```

**3. 实现配置方法**

**文件：** `librtsim/scheduler/gpfp_tie_scheduler.cpp`

**添加实现：**
```cpp
void TIEScheduler::setSolarDayTime(int start_hour, int end_hour) {
    if (start_hour < 0 || start_hour > 23) {
        SCHEDULER_LOG_WARNING("⚠️ [TIE] 无效的白天开始时间: " +
                            std::to_string(start_hour) + "，使用默认值6");
        _solar_day_start_hour = 6;
    } else {
        _solar_day_start_hour = start_hour;
    }

    if (end_hour < 0 || end_hour > 23 || end_hour <= start_hour) {
        SCHEDULER_LOG_WARNING("⚠️ [TIE] 无效的白天结束时间: " +
                            std::to_string(end_hour) + "，使用默认值18");
        _solar_day_end_hour = 18;
    } else {
        _solar_day_end_hour = end_hour;
    }

    SCHEDULER_LOG_INFO(std::string("☀️ [TIE] 太阳能白天时间设置为: ") +
                      std::to_string(_solar_day_start_hour) + ":00 - " +
                      std::to_string(_solar_day_end_hour) + ":00");
}

void TIEScheduler::setMaxSolarIrradiance(double max_irradiance) {
    if (max_irradiance <= 0.0) {
        SCHEDULER_LOG_WARNING("⚠️ [TIE] 无效的太阳能辐照度: " +
                            std::to_string(max_irradiance) + "，使用默认值500");
        _max_solar_irradiance = 500.0;
    } else {
        _max_solar_irradiance = max_irradiance;
    }

    SCHEDULER_LOG_INFO(std::string("☀️ [TIE] 最大太阳能辐照度设置为: ") +
                      std::to_string(_max_solar_irradiance) + " W/m²");
}
```

**4. 修改getSolarIrradiance()方法**

**文件：** `librtsim/scheduler/gpfp_tie_scheduler.cpp:1222-1232`

**修改前：**
```cpp
double TIEScheduler::getSolarIrradiance(int64_t time_ms) {
    if (!_use_real_solar_data) {
        int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
        int64_t hour_of_day = (actual_time_ms % 86400000) / 3600000;

        if (hour_of_day >= 6 && hour_of_day <= 18) {
            return 500.0;  // ❌ 硬编码
        } else {
            return 0.0;
        }
    }
    // ...
}
```

**修改后：**
```cpp
double TIEScheduler::getSolarIrradiance(int64_t time_ms) {
    if (!_use_real_solar_data) {
        int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
        int64_t hour_of_day = (actual_time_ms % 86400000) / 3600000;

        // ✅ 使用配置成员变量
        if (hour_of_day >= _solar_day_start_hour &&
            hour_of_day <= _solar_day_end_hour) {
            return _max_solar_irradiance;  // ✅ 从配置读取
        } else {
            return 0.0;
        }
    }
    // ...
}
```

**5. 同样修改TGF和BTIE**

**文件：**
- `librtsim/scheduler/gpfp_tgf_scheduler.cpp`
- `librtsim/scheduler/gpfp_btie_scheduler.cpp

**相同的修改模式：**
- 添加成员变量
- 添加setter方法
- 修改getSolarIrradiance()方法

---

#### 方案B：从YAML配置文件读取（更灵活）

**1. 扩展配置文件格式**

**文件：** 系统配置YAML（如 `system_TIE.yml`）

**添加配置项：**
```yaml
energy_management:
  initial_energy: 8.0
  max_energy: 1000.0
  # ... 其他配置 ...

  # ⭐ 新增：太阳能模型配置
  solar_model:
    day_start_hour: 6          # 白天开始时间（小时）
    day_end_hour: 18            # 白天结束时间（小时）
    max_irradiance: 500.0       # 最大太阳能辐照度（W/m²）
    use_seasonal_adjustment: false  # 是否使用季节调整（可选）
```

**2. 在ConfigManager中添加读取逻辑**

**文件：** `librtsim/scheduler/config_manager.cpp`

**在parseEnergyConfig()方法中添加：**
```cpp
bool ConfigManager::parseEnergyConfig(const YAML::Node& energy_node) {
    // ... 现有代码 ...

    // ⭐ 新增：读取太阳能模型配置
    if (energy_node["solar_model"]) {
        auto solar_node = energy_node["solar_model"];

        // 读取白天时间
        if (solar_node["day_start_hour"]) {
            _solar_day_start_hour = solar_node["day_start_hour"].as<int>();
        }

        if (solar_node["day_end_hour"]) {
            _solar_day_end_hour = solar_node["day_end_hour"].as<int>();
        }

        // 读取最大辐照度
        if (solar_node["max_irradiance"]) {
            _max_solar_irradiance = solar_node["max_irradiance"].as<double>();
        }

        SCHEDULER_LOG_INFO(std::string("☀️ 太阳能模型配置: ") +
                          "白天=" + std::to_string(_solar_day_start_hour) + ":00 - " +
                          std::to_string(_solar_day_end_hour) + ":00, " +
                          "最大辐照度=" + std::to_string(_max_solar_irradiance) + " W/m²");
    }

    // ... 现有代码 ...
}
```

**3. 将配置传递给调度器**

**文件：** `librtsim/scheduler/config_manager.cpp`

**在创建调度器时传递配置：**
```cpp
// 在创建TIEScheduler后设置配置
TIEScheduler *tie_sched = dynamic_cast<TIEScheduler*>(_sched);
if (tie_sched) {
    tie_sched->setSolarDayTime(_solar_day_start_hour, _solar_day_end_hour);
    tie_sched->setMaxSolarIrradiance(_max_solar_irradiance);
}
```

---

### 配置示例

#### 示例1：温带地区（默认）
```yaml
solar_model:
  day_start_hour: 6
  day_end_hour: 18
  max_irradiance: 500.0
```

#### 示例2：高纬度地区（夏季日照长）
```yaml
solar_model:
  day_start_hour: 5
  day_end_hour: 21
  max_irradiance: 600.0  # 更高的辐照度
```

#### 示例3：赤道地区（全年日照稳定）
```yaml
solar_model:
  day_start_hour: 6
  day_end_hour: 18
  max_irradiance: 800.0  # 赤道地区更高
```

---

## 🔴 问题2：CPU名称硬编码（EnergyMRTKernel）

### 问题描述

**当前代码：**
```cpp
// librtsim/energyMRTKernel.cpp:757, 774
if (chosenCPU->getName().find("LITTLE_3") == string::npos ||
    iDeltaPows[i].cpu->getName().find("LITTLE_3") == string::npos) {
    chosenCPU = iDeltaPows[i].cpu;
}
```

### 修复方案

#### 方案A：使用CPU索引而非名称（推荐）

**文件：** `librtsim/energyMRTKernel.cpp`

**修改前：**
```cpp
// ❌ 硬编码CPU名称
if (chosenCPU->getName().find("LITTLE_3") == string::npos ||
    iDeltaPows[i].cpu->getName().find("LITTLE_3") == string::npos) {
    chosenCPU = iDeltaPows[i].cpu;
    chosenCPU->setOPP(iDeltaPows[i].opp);
    fitsInOtherCore = false;
    break;
}
```

**修改后（方案A1 - 使用CPU索引）：**
```cpp
// ✅ 使用CPU索引：选择第3个LITTLE核心
// 假设：第3个LITTLE核心（索引2）是性能最低的

int little_cpu_index = 0;
int target_little_index = 2;  // 可配置：选择第几个LITTLE核心

for (size_t i = 0; i < iDeltaPows.size(); i++) {
    if (iDeltaPows[i].cpu->getIsland()->type() == IslandType::LITTLE) {
        if (little_cpu_index == target_little_index) {
            chosenCPU = iDeltaPows[i].cpu;
            chosenCPU->setOPP(iDeltaPows[i].opp);
            fitsInOtherCore = false;
            break;
        }
        little_cpu_index++;
    }
}
```

**修改后（方案A2 - 使用频率选择）：**
```cpp
// ✅ 使用频率选择：选择最低频率的LITTLE核心
CPU *chosenCPU = nullptr;
double min_freq = std::numeric_limits<double>::max();

for (const auto& delta_pow : iDeltaPows) {
    CPU *cpu = delta_pow.cpu;

    // 只考虑LITTLE核心
    if (cpu->getIsland()->type() != IslandType::LITTLE) {
        continue;
    }

    // 检查是否是空闲的（未被选为chosenCPU）
    if (cpu == chosenCPU) {
        continue;
    }

    double cpu_freq = cpu->getFrequency();
    if (cpu_freq < min_freq) {
        min_freq = cpu_freq;
        chosenCPU = cpu;
        chosen_opp = delta_pow.opp;
    }
}

if (chosenCPU != nullptr) {
    chosenCPU->setOPP(chosen_opp);
}
```

---

#### 方案B：使用配置文件指定CPU

**1. 添加配置结构**

**文件：** 系统配置YAML

```yaml
dvfs:
  cpu_selection_policy: "frequency_based"  # 或 "index_based"
  little_cpu_index: 2                      # 用于index_based
  prefer_lowest_frequency: true            # 用于frequency_based
```

**2. 从配置读取选择策略**

**文件：** `librtsim/energyMRTKernel.cpp`

```cpp
// ✅ 根据配置策略选择CPU
std::string selection_policy = getConfigValue("cpu_selection_policy", "frequency_based");

if (selection_policy == "index_based") {
    // 使用索引选择
    int target_index = getConfigValue("little_cpu_index", 2);
    // ... 按索引选择逻辑

} else if (selection_policy == "frequency_based") {
    // 使用频率选择
    bool prefer_lowest = getConfigValue("prefer_lowest_frequency", true);
    // ... 按频率选择逻辑
}
```

---

### 推荐的修复策略

#### 对于问题1（白天时间硬编码）：

**推荐：方案A（最小侵入式）**

**理由：**
1. ✅ 修改量小，风险低
2. ✅ 保持向后兼容（默认值与硬编码相同）
3. ✅ 可以通过API调用设置
4. ✅ 不影响现有测试

**实施步骤：**
1. 添加成员变量（3个）
2. 添加setter方法（2个）
3. 修改getSolarIrradiance()（3处：TIE、TGF、BTIE）
4. 添加配置验证和日志

**预计影响范围：**
- 修改文件：3个（gpfp_tie_scheduler.cpp, gpfp_tgf_scheduler.cpp, gpfp_btie_scheduler.cpp）
- 修改行数：约30-40行
- 测试需求：验证默认行为不变

---

#### 对于问题2（CPU名称硬编码）：

**推荐：方案A2（使用频率选择）**

**理由：**
1. ✅ 更通用，适用于任何CPU命名
2. ✅ 基于实际硬件属性（频率）
3. ✅ 不依赖特定名称
4. ✅ 更符合DVF S的设计理念

**实施步骤：**
1. 修改CPU选择逻辑（2处）
2. 添加频率比较逻辑
3. 添加日志记录选择的CPU
4. 测试不同CPU配置

**预计影响范围：**
- 修改文件：1个（librtsim/energyMRTKernel.cpp）
- 修改行数：约20-30行
- 测试需求：验证CPU选择正确性

---

## 📋 完整修改清单

### 修改文件列表

1. **头文件修改（1个）**
   - `librtsim/include/rtsim/scheduler/gpfp_tie_scheduler.hpp`
     - 添加3个成员变量
     - 添加3个公共方法

2. **TIE实现修改（1个）**
   - `librtsim/scheduler/gpfp_tie_scheduler.cpp`
     - 添加2个setter方法实现
     - 修改getSolarIrradiance()方法

3. **TGF实现修改（1个）**
   - `librtsim/scheduler/gpfp_tgf_scheduler.cpp`
     - 添加相同的成员变量和方法
     - 修改getSolarIrradiance()方法

4. **BTIE实现修改（1个）**
   - `librtsim/scheduler/gpfp_btie_scheduler.cpp`
     - 添加相同的成员变量和方法
     - 修改getSolarIrradiance()方法

5. **EnergyMRTKernel修改（1个）**
   - `librtsim/energyMRTKernel.cpp`
     - 修改CPU选择逻辑（2处）
     - 添加频率比较逻辑

**总计：5个文件，约80-100行代码修改**

---

## 🧪 测试计划

### 测试1：验证默认行为不变

**目的：** 确保修改后默认行为与硬编码相���

**测试方法：**
```bash
# 运行现有测试，验证结果一致
./build/rtsim/rtsim system_TIE.yml tasks.yml 10
# 对比trace文件，确认调度行为不变
```

**预期结果：** ✅ 与修改前的行为完全一致

---

### 测试2：验证配置可修改

**目的：** 测试新配置项可以正确设置

**测试代码：**
```cpp
// 在调度器初始化后添加测试
TIEScheduler *sched = new TIEScheduler();
sched->setSolarDayTime(5, 20);  // 设置5:00-20:00
sched->setMaxSolarIrradiance(600.0);  // 设置600W/m²

// 验证配置生效
assert(sched->getSolarDayStartHour() == 5);
assert(sched->getSolarDayEndHour() == 20);
assert(sched->getMaxSolarIrradiance() == 600.0);
```

---

### 测试3：验证不同地理配置

**目的：** 测试不同地区的太阳能配置

**测试配置：**
```yaml
# 高纬度地区
solar_model:
  day_start_hour: 5
  day_end_hour: 21
  max_irradiance: 400.0

# 赤道地区
solar_model:
  day_start_hour: 6
  day_end_hour: 18
  max_irradiance: 800.0
```

**预期结果：** ✅ 能量收集曲线符合配置

---

### 测试4：验证CPU选择正确性

**目的：** 测试新的CPU选择逻辑

**测试场景：**
- 4个LITTLE核心，不同频率
- 验证选择了最低频率的核心

**预期结果：** ✅ 选择了正确的CPU

---

## ⚠️ 注意事项和风险

### 风险1：向后兼容性

**问题：** 修改可能破坏现有测试

**缓解措施：**
- ✅ 默认值与硬编码值相同
- ✅ 保持API接口不变
- ✅ 添加详细日志

---

### 风险2：配置验证

**问题：** 无效配置值可能导致错误

**缓解措施：**
- ✅ 添加参数验证（0-23小时检查）
- ✅ 添加范围检查（辐照度>0）
- ✅ 使用默认值作为后备

---

### 风险3：性能影响

**问题：** 新增方法调用可能影响性能

**缓解措施：**
- ✅ 仅在配置时调用一次
- ✅ 运行时无额外开销
- ✅ 使用成员变量缓存

---

## 📊 修复效果评估

### 灵活性提升

| 指标 | 修复前 | 修复后 | 改进 |
|------|--------|--------|------|
| 可配置地理区域 | 1种 | 无限种 | ✅ 显著提升 |
| 可配置季节 | 无 | 支持 | ✅ 新功能 |
| CPU命名依赖 | 是 | 否 | ✅ 完全去除 |
| 代码可移植性 | 低 | 高 | ✅ 显著提升 |

### 代码质量提升

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 硬编码数量 | 5处 | 0处 |
| 配置灵活性 | 低 | 高 |
| 可维护性 | 中 | 高 |
| 可测试性 | 低 | 高 |

---

## 🎯 实施建议

### 阶段1：准备工作（1天）

1. ✅ 创建修复分支
2. ✅ 备份现有代码
3. ✅ 编写单元测试

### 阶段2：实施修复（2-3天）

1. ✅ 修改TIE（添加配置支持）
2. ✅ 修改TGF（添加配置支持）
3. ✅ 修改BTIE（添加配置支持）
4. ✅ 修改EnergyMRTKernel（CPU选择逻辑）

### 阶段3：测试验证（1-2天）

1. ✅ 运行回归测试
2. ✅ 验证默认行为
3. ✅ 测试新配置功能
4. ✅ 性能测试

### 阶段4：文档更新（0.5天）

1. ✅ 更新配置文件说明
2. ✅ 添加示例配置
3. ✅ 更新API文档

**总预计时间：4.5 - 6.5天**

---

## 📚 参考资料

### 相关配置文件

- 现有系统配置：`test_results/tie_tgf_difference/system_TIE.yml`
- 能量配置示例：`test_results/comprehensive_test/test1_energy_accounting.yml`

### 相关代码文件

- TIE调度器：`librtsim/scheduler/gpfp_tie_scheduler.cpp`
- TGF调度器：`librtsim/scheduler/gpfp_tgf_scheduler.cpp`
- BTIE调度器：`librtsim/scheduler/gpfp_btie_scheduler.cpp`
- EnergyMRTKernel：`librtsim/energyMRTKernel.cpp`

---

**修复方案版本：** 1.0
**创建时间：** 2026-01-24
**最后更新：** 2026-01-24

**注意：** 本文档仅提供修复方案，不包含实际代码修改。
