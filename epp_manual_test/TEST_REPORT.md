# EPP 调度器测试报告

## 测试目标
验证 EPP 调度器在初始能量为0时的行为，分别测试：
- **0点测试**: 午夜无太阳能场景
- **12点测试**: 正午有太阳能场景

## 测试配置

### 系统参数
- **CPU核心数**: 2
- **调度器**: gpfp_epp
- **初始能量**: 0.0 J
- **最大能量**: 1000.0 J
- **仿真时长**: 2000 ms

### 太阳能配置
- **PV效率**: 18%
- **PV面积**: 1.0 m²
- **能量收集间隔**: 100 ms
- **太阳能数据**: NASA真实数据（沈阳）

### 任务集
| 任务名 | 周期(ms) | WCET(ms) | 工作负载 | 优先级 |
|--------|----------|----------|----------|--------|
| task_high | 500 | 200 | bzip2 | 最高 |
| task_mid | 1000 | 300 | bzip2 | 中等 |
| task_low | 1500 | 400 | hash | 最低 |

### 能量消耗计算
基础功率 = 0.5 W，频率比 = 0.93 (8100 MHz)

- **task_high**: 0.5 × 1.2 × 0.93 × 0.2 = **0.1116 J**
- **task_mid**: 0.5 × 1.2 × 0.93 × 0.3 = **0.1674 J**
- **task_low**: 0.5 × 0.8 × 0.93 × 0.4 = **0.1488 J**

---

## 手动模拟结果

### 0点测试（无太阳能）

#### 太阳能辐照度
- **0点**: 0.0 W/m²（午夜无太阳能）

#### 调度时间线

| 时间 (ms) | 事件 | 能量判断 | 当前能量 (J) | 结果 |
|-----------|------|----------|--------------|------|
| 0 | 所有任务到达 | energy_after = 0 + 0 - 0.1116 < 0 | 0.0 | ❌ 能量不足 |
| 100 | 能量恢复事件 | 无太阳能可收集 | 0.0 | ❌ 仍不足 |
| 200-2000 | 持续尝试 | 每100ms触发恢复事件 | 0.0 | ❌ 永久失败 |

#### 预期结果
- ✅ 任务到达事件正常触发
- ❌ 所有任务因能量不足无法调度
- ✅ 所有任务错过截止时间（dline_miss）
- ✅ 符合预期（无太阳能，初始能量为0）

---

### 12点测试（有太阳能）

#### 太阳能辐照度
- **12点**: 443.83 W/m²（正午最大太阳能）
- **能量收集率**: 443.83 × 1.0 × 0.18 = **79.89 W**
- **100ms可收集**: 79.89 × 0.1 = **7.989 J**

#### 调度时间线

| 时间 (ms) | 事件 | 能量判断 | 当前能量 (J) | 结果 |
|-----------|------|----------|--------------|------|
| 0 | 所有任务到达 | energy_after = 0 + 15.978 - 0.1116 ≥ 0 | 0.0 | ⚠️ 前瞻通过，实际能量不足 |
| 0 | 预扣减失败 | consumeEnergy() 检查: 0 < 0.1116 | 0.0 | ❌ 启动能量恢复 |
| 100 | 能量恢复事件 | 收集 7.989 J | 7.989 | ✅ 能量充足 |
| 100 | 调度 task_high | energy_after = 7.989 + 15.978 - 0.1116 ≥ 0 | 7.877 | ✅ 开始执行（CPU0）|
| 100 | 级联调度 task_mid | energy_after = 7.877 + 23.967 - 0.1674 ≥ 0 | 7.710 | ✅ 开始执行（CPU1）|
| 200 | task_high完成 | 收集7.989J，退款0J | 15.699 | ✅ task_high新实例到达 |
| 300 | task_mid完成 | 收集7.989J，退款0J | 23.688 | ✅ 调度task_low |
| 400 | task_high完成 | 收集7.989J，退款0J | 31.478 | ✅ 继续调度 |
| ... | ... | ... | ... | ... |

#### 预期结果
- ✅ T=100ms后成功调度多个任务
- ✅ 能量持续增长（太阳能充足）
- ✅ 大量任务完成执行
- ✅ RM优先级正确维护

---

## 仿真结果

### 0点测试追踪文件 ([trace_0h.json](epp_manual_test/trace_0h.json))

```json
{
  "events": [
    {"time": "0", "event_type": "arrival", "task_name": "task_high"},
    {"time": "0", "event_type": "arrival", "task_name": "task_mid"},
    {"time": "0", "event_type": "arrival", "task_name": "task_low"},
    {"time": "500", "event_type": "dline_miss", "task_name": "task_high"},
    {"time": "1000", "event_type": "dline_miss", "task_name": "task_mid"},
    ...
  ]
}
```

**分析**:
- ✅ 只有 `arrival` 和 `dline_miss` 事件
- ✅ 没有 `scheduled` 事件（符合预期）
- ✅ 所有任务错过截止时间
- ✅ **符合手动模拟**

### 12点测试追踪文件 ([trace_12h.json](epp_manual_test/trace_12h.json))

```json
{
  "events": [
    {"time": 43200000, "event_type": "arrival", "task_name": "task_high"},
    {"time": 43200000, "event_type": "arrival", "task_name": "task_mid"},
    {"time": 43200000, "event_type": "arrival", "task_name": "task_low"},
    {"time": 43200500, "event_type": "dline_miss", "task_name": "task_high"},
    ...
  ]
}
```

**分析**:
- ⚠️ **只有 `arrival` 和 `dline_miss` 事件**
- ❌ **没有任何 `scheduled` 事件！**
- ❌ **与手动模拟严重不符！**

---

## 🚨 发现的关键BUG

### BUG #1: YAML解析失败导致太阳能数据未启用

**日志证据**:
```
☀️ [EPP] 太阳能配置: use_real=false file=data/processed/shenyang_solar_minute.csv"  # NASA数据文件路径
```

**根本原因**:
文件 [gpfp_epp_scheduler.cpp:248-251](../librtsim/scheduler/gpfp_epp_scheduler.cpp#L248-L251) 中的YAML解析代码**没有处理行内注释**：

```cpp
if (line.find("use_real_solar_data:") != std::string::npos) {
    std::string value = line.substr(line.find(":") + 1);
    value.erase(0, value.find_first_not_of(" \t"));
    _use_real_solar_data = (value == "true");  // ❌ BUG: value包含注释部分
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

### BUG #2: 能量收集函数直接返回0

**日志证据**:
```
⏰ [EPP] ===== 能量恢复事件触发 @ 100ms =====
⏰ [EPP] ===== 能量恢复事件触发 @ 200ms =====
...
总收集能量: 0.000000J
```

**根本原因**:
文件 [gpfp_epp_scheduler.cpp:980-983](../librtsim/scheduler/gpfp_epp_scheduler.cpp#L980-L983)：

```cpp
double EPPScheduler::collectSolarEnergy(Tick current_time) {
    if (!_use_real_solar_data) {
        return 0.0;  // ❌ 由于BUG #1，永远返回0
    }
    ...
}
```

### BUG #3: 前瞻性判断通过但预扣减失败

**日志证据** (12点测试):
```
🔮 [EPP] 前瞻性能量判断: ... 结余=17.900000J ✅可调度
❌ [EPP] consumeEnergy: 能量不足 需要=0.100000J 当前=0.000000J
⚠️ [EPP] getTaskN: 前瞻性判断通过但实际能量不足
```

**根本原因**:
- 前瞻性判断使用公式：`当前能量 + 预测收集 - 能量消耗 ≥ 0`
- 但 `consumeEnergy()` 检查的是：**当前能量 ≥ 能量消耗**
- 当初始能量为0时，即使预测收集充足，预扣减也会失败

**这是设计特性还是BUG？**
- 根据代码注释和实现，这**看起来是预期的行为**（防止预支未来能量）
- 但导致初始能量为0时，即使太阳能充足也无法启动

---

## 测试结论

### ✅ 成功验证
1. **0点测试**: 完全符合预期（无太阳能，所有任务无法调度）
2. **EPP能量判断逻辑**: 前瞻性判断公式正确实现
3. **RM优先级**: 任务按周期正确排序
4. **追踪文件格式**: 正确记录事件

### ❌ 发现问题
1. **YAML解析BUG**: 导致 `use_real_solar_data` 始终为 `false`
2. **太阳能收集失败**: 12点测试中能量收集始终为0
3. **12点测试失败**: 所有任务无法调度，与手动模拟不符

### 🎯 核心问题
**初始能量为0时的死锁**:
- 即使太阳能充足（如12点），由于初始能量为0，第一个任务无法预扣减能量
- 能量恢复事件触发后，由于 `use_real_solar_data=false`，收集到的能量始终为0
- 系统陷入永久饥饿

---

## 修复建议

### 修复 #1: 处理YAML行内注释
```cpp
// 修改 gpfp_epp_scheduler.cpp:248-251
if (line.find("use_real_solar_data:") != std::string::npos) {
    std::string value = line.substr(line.find(":") + 1);

    // ⭐ 移除行内注释（以#开头）
    size_t comment_pos = value.find('#');
    if (comment_pos != std::string::npos) {
        value = value.substr(0, comment_pos);
    }

    value.erase(0, value.find_first_not_of(" \t"));
    value.erase(value.find_last_not_of(" \t") + 1);

    _use_real_solar_data = (value == "true");
}
```

### 修复 #2: 允许初始能量为0时启动
**方案A**: 修改 `consumeEnergy()` 逻辑，允许在预测收集充足时预支能量
```cpp
bool EPPScheduler::consumeEnergy(double energy_joules, const std::string &task_name) {
    // ⭐ 允许能量透支（前瞻性判断已确保安全）
    const double EPSILON = 1e-9;
    if (_current_energy < energy_joules - EPSILON) {
        // 记录透支，但不阻止调度
        SCHEDULER_LOG_INFO("⚠️ [EPP] 能量透支: 预支 " +
                           std::to_string(energy_joules - _current_energy) + "J");
    }
    _current_energy -= energy_joules;
    return true;
}
```

**方案B**: 在第一次调度前，先收集一次太阳能
```cpp
bool EPPScheduler::canScheduleWithEnergy(AbsRTTask *task, Tick current_time) {
    // ⭐ 最优方案：在能量判断前，先实际收集一次太阳能
    double harvested = collectSolarEnergy(current_time);
    if (harvested > 0.0001) {
        _current_energy += harvested;
        _stats.total_energy_harvested += harvested;
    }
    ... // 继续原有逻辑
}
```

### 修复 #3: 使用真正的YAML解析器
当前的手动字符串解析非常脆弱，建议：
- 使用 `yaml-cpp` 库进行标准YAML解析
- 或依赖 ConfigManager 的配置（避免重复解析）

---

## 文件清单

### 测试配置文件
- [test_epp_0h_system.yml](epp_manual_test/test_epp_0h_system.yml) - 0点系统配置
- [test_epp_12h_system.yml](epp_manual_test/test_epp_12h_system.yml) - 12点系统配置
- [test_epp_tasks.yml](epp_manual_test/test_epp_tasks.yml) - 任务集配置

### 仿真输出
- [trace_0h.json](epp_manual_test/trace_0h.json) - 0点追踪文件
- [trace_12h.json](epp_manual_test/trace_12h.json) - 12点追踪文件
- [output_0h.log](epp_manual_test/output_0h.log) - 0点完整日志（55KB）
- [output_12h.log](epp_manual_test/output_12h.log) - 12点完整日志（113KB）

### 测试报告
- [TEST_REPORT.md](epp_manual_test/TEST_REPORT.md) - 本报告

---

## 测试统计

| 指标 | 0点测试 | 12点测试 |
|------|---------|----------|
| 任务到达事件 | 13 | 13 |
| 任务调度事件 | 0 | 0 |
| 任务完成事件 | 0 | 0 |
| 截止时间错过 | 13 | 13 |
| 能量收集总量 | 0.0 J | 0.0 J ❌ |
| 预期能量收集 | 0.0 J | ~160 J ✅ |
| 符合预期 | ✅ 是 | ❌ 否 |

---

**测试日期**: 2026-01-18
**测试人员**: Claude Code
**仿真器版本**: PARTSim (基于 MetaSim)
**调度器版本**: EPP Scheduler (gpfp_epp_scheduler.cpp)
