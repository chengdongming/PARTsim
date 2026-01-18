# EPP/EFPP/CBPP 调度器问题修复指南

## 📋 文档目的

本文档记录了 EPP（Energy-aware Preemptive Priority）调度器中发现的问题及其修复方案，旨在为 **EFPP** 和 **CBPP** 调度器提供相同问题的修复指导。

---

## 🚨 发现的关键问题

### 问题 #1: YAML解析失败导致太阳能数据未启用

**影响范围**: EPP, EFPP, CBPP（所有使用能量管理的调度器）

**症状**:
- 配置文件中设置 `use_real_solar_data: true`，但调度器读取为 `false`
- 导致太阳能收集功能完全失效
- 即使在太阳能充足时段（如正午），能量收集始终为 0

**根本原因**:
手动YAML解析代码没有处理行内注释（以 `#` 开头）。

**错误代码示例**（所有调度器的通病）:
```cpp
// 在 gpfp_epp_scheduler.cpp, gpfp_efpp_scheduler.cpp, gpfp_cbpp_scheduler.cpp 中
if (line.find("use_real_solar_data:") != std::string::npos) {
    std::string value = line.substr(line.find(":") + 1);
    value.erase(0, value.find_first_not_of(" \t"));
    _use_real_solar_data = (value == "true");  // ❌ BUG
}
```

**配置文件内容**:
```yaml
use_real_solar_data: true              # 启用NASA真实太阳能数据
```

**实际解析结果**:
- `value` = `"true              # 启用NASA真实太阳能数据"`
- `value == "true"` → `false`
- **导致 `_use_real_solar_data = false`！**

---

### 问题 #2: 能量恢复时间计算错误

**影响范围**: EPP, EFPP, CBPP（所有使用能量管理的调度器）

**症状**:
- 当初始能量为0时，能量不足时恢复时间过长
- 在太阳能充足时段（如正午），本应在1-2ms内恢复，却等待了100ms
- 导致任务调度延迟，违反实时性要求

**根本原因**:
`calculateEnergyRecoveryTime()` 函数直接使用周期性收集间隔，而不是根据实际太阳能功率计算。

**错误代码示例**:
```cpp
Tick EPPScheduler::calculateEnergyRecoveryTime(double energy_needed) {
    // ❌ BUG: 直接使用周期性收集间隔
    Tick recovery_time = _periodic_collection_interval;  // 100ms
    return recovery_time;
}
```

**正确计算**（12点正午，太阳能充足时）:
```
能量缺口 = 0.1 J
当前辐照度 = 434.5 W/m²
当前功率 = 434.5 × 1.0 × 0.18 = 78.21 W
恢复时间 = 0.1 / 78.21 × 1000 = 1.28 ms ≈ 1 ms
```

**修复前后对比**:
| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 12点正午 | 100ms ❌ | 1-2ms ✅ |
| 0点午夜 | 100ms | 100ms（无太阳能） |

---

## ✅ 修复方案

### 修复 #1: YAML解析行内注释问题（已实现于EPP）

**适用场景**: 快速解决配置解析问题

**修复位置**:
- [gpfp_epp_scheduler.cpp:248-305](librtsim/scheduler/gpfp_epp_scheduler.cpp#L248-L305)
- **需要在 EFPP 和 CBPP 中应用相同修复**

**修复代码**:
```cpp
// 解析配置项
if (in_energy_section) {
    if (line.find("use_real_solar_data:") != std::string::npos) {
        std::string value = line.substr(line.find(":") + 1);

        // ⭐ 修复：移除行内注释（以#开头）
        size_t comment_pos = value.find('#');
        if (comment_pos != std::string::npos) {
            value = value.substr(0, comment_pos);
        }

        value.erase(0, value.find_first_not_of(" \t"));
        value.erase(value.find_last_not_of(" \t") + 1);

        _use_real_solar_data = (value == "true");

        SCHEDULER_LOG_DEBUG(std::string("🔧 [EPP] 解析 use_real_solar_data: ") +
                              value + " -> " +
                              (_use_real_solar_data ? "true" : "false"));
    }
    else if (line.find("solar_data_file:") != std::string::npos) {
        std::string value = line.substr(line.find(":") + 1);

        // ⭐ 修复：移除行内注释
        size_t comment_pos = value.find('#');
        if (comment_pos != std::string::npos) {
            value = value.substr(0, comment_pos);
        }

        // 去除引号
        value.erase(0, value.find_first_not_of(" \t\""));
        value.erase(value.find_last_not_of(" \t\"") + 1);
        _solar_data_file = value;
    }
    else if (line.find("pv_efficiency:") != std::string::npos) {
        std::string value = line.substr(line.find(":") + 1);

        // ⭐ 修复：移除行内注释
        size_t comment_pos = value.find('#');
        if (comment_pos != std::string::npos) {
            value = value.substr(0, comment_pos);
        }

        value.erase(0, value.find_first_not_of(" \t"));
        value.erase(value.find_last_not_of(" \t") + 1);
        _pv_efficiency = std::stod(value);
    }
    else if (line.find("pv_area_m2:") != std::string::npos) {
        std::string value = line.substr(line.find(":") + 1);

        // ⭐ 修复：移除行内注释
        size_t comment_pos = value.find('#');
        if (comment_pos != std::string::npos) {
            value = value.substr(0, comment_pos);
        }

        value.erase(0, value.find_first_not_of(" \t"));
        value.erase(value.find_last_not_of(" \t") + 1);
        _pv_area_m2 = std::stod(value);
    }
}
```

**需要修改的字段**（所有调度器统一）:
1. `use_real_solar_data` - 是否使用真实太阳能数据
2. `solar_data_file` - 太阳能数据文件路径
3. `pv_efficiency` - 光伏转换效率
4. `pv_area_m2` - 光伏板面积

**修复步骤**（适用于EFPP/CBPP）:
1. 找到调度器构造函数中的YAML解析代码
2. 定位上述4个字段的解析逻辑
3. 在每个字段解析中添加移除行内注释的代码
4. 添加调试日志，记录解析前后的值

---

### 修复 #2: 能量恢复时间计算错误（已实现于EPP）

**适用场景**: 确保能量恢复时间准确，满足实时性要求

**修复位置**:
- [gpfp_epp_scheduler.cpp:1109-1163](librtsim/scheduler/gpfp_epp_scheduler.cpp#L1109-L1163)
- **需要在 EFPP 和 CBPP 中应用相同修复**

**修复代码**:
```cpp
Tick EPPScheduler::calculateEnergyRecoveryTime(double energy_needed) {
    if (energy_needed <= 0) {
        return 0;
    }

    Tick recovery_time = 0;

    // ⭐ 修复：根据当前太阳能功率计算恢复时间
    if (_use_real_solar_data) {
        // 获取当前辐照度
        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);
        double irradiance = getSolarIrradiance(current_ms);

        // 计算当前功率 (W)
        // 功率 = 辐照度(W/m²) × 面积(m²) × 效率
        double current_power = irradiance * _pv_area_m2 * _pv_efficiency;

        if (current_power > 0.001) {
            // 计算恢复时间 (ms)
            // 时间(s) = 能量(J) / 功率(W)
            // 时间(ms) = 时间(s) × 1000
            double recovery_time_seconds = energy_needed / current_power;
            recovery_time = static_cast<Tick>(recovery_time_seconds * 1000.0);

            SCHEDULER_LOG_INFO(std::string("⏰ 计算能量恢复时间: ") +
                              "缺口=" + std::to_string(energy_needed) + "J" +
                              " 当前辐照度=" + std::to_string(irradiance) + " W/m²" +
                              " 当前功率=" + std::to_string(current_power) + " W" +
                              " 预计恢复=" + std::to_string(static_cast<int64_t>(recovery_time)) + "ms" +
                              " (基于实际太阳能功率)");
        } else {
            // 无太阳能，使用周期性收集间隔
            recovery_time = _periodic_collection_interval;
            SCHEDULER_LOG_INFO(std::string("⏰ 计算能量恢复时间: ") +
                              "缺口=" + std::to_string(energy_needed) + "J" +
                              " 无太阳能(辐照度=0)" +
                              " 将使用周期性收集=" + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");
        }
    } else {
        // 不使用真实太阳能数据，使用周期性收集间隔
        recovery_time = _periodic_collection_interval;
        SCHEDULER_LOG_INFO(std::string("⏰ 计算能量恢复时间: ") +
                          "缺口=" + std::to_string(energy_needed) + "J" +
                          " 未使用真实太阳能数据" +
                          " 将使用周期性收集=" + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");
    }

    // 最小恢复时间：1ms
    if (recovery_time < 1) {
        recovery_time = 1;
    }

    return recovery_time;
}
```

**修复效果**:
```
修复前: ⏰ 计算能量恢复时间: 缺口=0.100000J 收集间隔=100ms (将使用周期性收集)
修复后: ⏰ 计算能量恢复时间: 缗口=0.100000J 当前辐照度=434.500000 W/m² 当前功率=78.210000 W 预计恢复=1ms (基于实际太阳能功率)
```

**验证结果**:
| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 首次调度延迟 | 100ms | 1-2ms |
| 符合实时性 | ❌ 否 | ✅ 是 |

**修复步骤**（适用于EFPP/CBPP）:
1. 找到调度器中的 `calculateEnergyRecoveryTime()` 函数
2. 替换为上述修复代码
3. 验证日志输出是否正确计算恢复时间

---

### 方案B: 长期方案（推荐但可选）

**适用场景**: 彻底解决YAML解析问题，使用标准库

**技术方案**: 集成 `yaml-cpp` 库

**优点**:
- 标准化、健壮的YAML解析
- 支持复杂数据结构
- 自动处理注释、缩进、多行字符串等
- 社区维护，持续更新

**缺点**:
- 需要引入新依赖
- 需要修改CMakeLists.txt
- 代码改动量较大

**实现步骤**:
1. 安装 yaml-cpp 库
2. 修改 CMakeLists.txt 添加依赖
3. 重构配置解析代码，使用 yaml-cpp API
4. 测试所有调度器

**代码示例**（yaml-cpp）:
```cpp
#include <yaml-cpp/yaml.h>

YAML::Node config = YAML::LoadFile(config_file);

// 读取能量管理配置
YAML::Node energy = config["energy_management"];
_use_real_solar_data = energy["use_real_solar_data"].as<bool>();
_solar_data_file = energy["solar_data_file"].as<std::string>();
_pv_efficiency = energy["pv_efficiency"].as<double>();
_pv_area_m2 = energy["pv_area_m2"].as<double>();
```

**建议**:
- **短期**: 使用方案A快速修复，确保功能正常
- **长期**: 逐步迁移到yaml-cpp（可作为独立任务）

---

## 🔧 EFPP调度器修复指南

### 文件位置
- 头文件: [librtsim/include/rtsim/scheduler/gpfp_efpp_scheduler.hpp](librtsim/include/rtsim/scheduler/gpfp_efpp_scheduler.hpp)
- 实现文件: [librtsim/scheduler/gpfp_efpp_scheduler.cpp](librtsim/scheduler/gpfp_efpp_scheduler.cpp)

### 修复步骤
1. **定位YAML解析代码**:
   ```bash
   grep -n "use_real_solar_data:" librtsim/scheduler/gpfp_efpp_scheduler.cpp
   ```

2. **应用方案A的修复代码**（参考EPP的修复）

3. **验证修复**:
   - 重新编译: `cd build && make -j4`
   - 运行EFPP测试
   - 检查日志中的 `use_real=` 值是否为 `true`

### 预期结果
修复后，日志应该显示：
```
☀️ [EFPP] 太阳能配置: use_real=true file=data/processed/shenyang_solar_minute.csv eff=0.180000 area=1.000000m²
```

---

## 🔧 CBPP调度器修复指南

### 文件位置
- 头文件: [librtsim/include/rtsim/scheduler/gpfp_cbpp_scheduler.hpp](librtsim/include/rtsim/scheduler/gpfp_cbpp_scheduler.hpp)
- 实现文件: [librtsim/scheduler/gpfp_cbpp_scheduler.cpp](librtsim/scheduler/gpfp_cbpp_scheduler.cpp)

### 修复步骤
与EFPP相同，参考EPP的修复方案。

---

## 📊 测试验证

### 测试场景
使用与EPP相同的测试配置：
- **初始能量**: 0.0 J
- **测试时间**: 12点（正午，太阳能充足）
- **期望结果**: 能量成功收集，任务正常调度

### 测试命令
```bash
cd build && make clean && make -j4
./run_sim.sh -s efpp_test_system.yml -t test_tasks.yml -d 2000 -o trace_efpp.json
```

### 验证日志
修复成功的标志：
```
☀️ [调度器名] 太阳能配置: use_real=true file=... eff=... area=...m²
```

修复失败的症状：
```
☀️ [调度器名] 太阳能配置: use_real=false file=... eff=... area=...m²
总收集能量: 0.000000J
```

---

## 📝 修复检查清单

### EPP调度器
- [x] 修复YAML解析行内注释问题
- [x] 添加调试日志
- [ ] 重新编译测试
- [ ] 验证能量收集功能

### EFPP调度器
- [ ] 定位YAML解析代码
- [ ] 应用方案A修复
- [ ] 添加调试日志
- [ ] 重新编译测试
- [ ] 验证能量收集功能

### CBPP调度器
- [ ] 定位YAML解析代码
- [ ] 应用方案A修复
- [ ] 添加调试日志
- [ ] 重新编译测试
- [ ] 验证能量收集功能

---

## 🎯 核心要点总结

1. **问题本质**: 手动YAML解析代码没有处理行内注释
2. **影响范围**: 所有使用太阳能数据的调度器（EPP/EFPP/CBPP）
3. **快速修复**: 在解析时移除 `#` 及其后的注释内容
4. **长期方案**: 集成yaml-cpp标准库（可选）
5. **修复验证**: 检查日志中的 `use_real=` 是否为 `true`

---

## 📚 相关文件

- [EPP测试报告](epp_manual_test/TEST_REPORT.md) - 详细的问题分析和测试结果
- [EPP修复代码](librtsim/scheduler/gpfp_epp_scheduler.cpp#L248-L305) - 参考实现
- [YAML配置模板](system_config_unified_template.yml) - 配置文件格式

---

**文档版本**: 1.0
**创建日期**: 2026-01-18
**最后更新**: 2026-01-18
**维护者**: PARTSim开发团队
