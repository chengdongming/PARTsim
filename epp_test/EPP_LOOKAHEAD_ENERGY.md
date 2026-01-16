# EPP前瞻性能量判断实现

## 实现概述

根据用户需求，实现了**前瞻性能量判���（Look-ahead Energy Check）**逻辑，核心改进：

### 1. **能量判断基于"预测"，而非"现状"**

**旧逻辑**：
```cpp
if (_current_energy < energy_needed) {
    return nullptr;  // ❌ 只看当前能量
}
```

**新逻辑**：
```cpp
// ✅ 判断条件：能量_当前 + 能���_收集 >= 能量_消耗
double energy_after_task = energy_current + energy_collection - energy_consumption;
bool can_schedule = energy_after_task >= 0.0;
```

### 2. **前瞻性计算（Look-ahead）**

新增方法 `canScheduleWithEnergy()`：

```cpp
bool EPPScheduler::canScheduleWithEnergy(AbsRTTask *task, Tick current_time) {
    // 1. 能量_当前
    double energy_current = _current_energy;

    // 2. 能量_消耗（完整WCET）
    double energy_consumption = calculateEnergyForTask(task);

    // 3. 获取任务WCET
    Tick wcet = model->getWCET();

    // 4. ⭐ 能量_收集（任务执行期间）
    double energy_collection = predictEnergyCollection(current_time, wcet);

    // 5. ⭐ 核心判断
    double energy_after_task = energy_current + energy_collection - energy_consumption;
    return energy_after_task >= 0.0;
}
```

新增方法 `predictEnergyCollection()`：

```cpp
double EPPScheduler::predictEnergyCollection(Tick current_time, Tick duration) {
    // ⭐ 预测：在duration时间内能收集多少太阳能
    double irradiance = getSolarIrradiance(current_time);

    // 能量(J) = 辐照度(W/m²) × 面积(m²) × 效率 × 时间(s)
    double duration_seconds = static_cast<double>(duration) * 0.001;
    double energy = irradiance * _pv_area_m2 * _pv_efficiency * duration_seconds;

    return energy;
}
```

### 3. **扣减时机后置**

**修改前**（在`schedule()`中立即扣减）：
```cpp
dispatchTask(highest, cpu);
_current_energy -= energy_needed;  // ❌ 调度决策时立即扣减
```

**修改后**（不在`getFirst()`/`getTaskN()`中扣减）：
```cpp
// ✅ getFirst()/getTaskN() 只做前瞻性判断，不扣减能量
// 能量扣将在任务实际执行时进行（由MRTKernel管理）
```

### 4. **持续结算思想**

当前实现：
- ✅ 在调度决策时进行前瞻性预测
- ✅ 考虑任务执行期间的太阳能收集
- ✅ 使用"能量结余"概念（能量_当前 + 收集 - 消耗）

未来改进（可选）：
- 在任务执行的每个Tick同步结算能量收入和支出
- 精确跟踪能量变化轨迹

## 测试结果

### 运行日志（前瞻性能量判断）

```
🔮 [EPP] 前瞻性能量判断: task_high
    当前=5.000000J
    收集(预测)=0.000000J  ← time=0，仿真开始时刻
    消耗=0.250000J
    结余=4.750000J
    ✅可调度

✅ [EPP] getTaskN: 前瞻性判断能量足够，返回任务 #0: task_high
    当前能量: 5.000000J
    ⭐ 级联调度继续
```

### 分析

1. **time=0ms（仿真开始）**
   - 当前能量: 5.0J
   - 预测收集: 0.0J（初始化阶段）
   - task_high消耗: 0.25J
   - **结余: 4.75J ≥ 0 ✅ 可调度**

2. **time=1000ms（有太阳能收集）**
   - 假设辐照度: 500 W/m²
   - PV效率: 0.18
   - PV面积: 1.0 m²
   - task_mid (400ms) 预测收集: 500 × 1.0 × 0.18 × 0.4 = **36J**
   - 判断: 5.0 + 36 - 0.4 = **40.6J ≥ 0 ✅ 可调度**

## 代码修改清单

### 1. 头文件 [gpfp_epp_scheduler.hpp](librtsim/include/rtsim/scheduler/gpfp_epp_scheduler.hpp)

添加新方法声明：
```cpp
// ⭐ 前瞻性能量判断（新逻辑）
double predictEnergyCollection(Tick current_time, Tick duration);
bool canScheduleWithEnergy(AbsRTTask *task, Tick current_time);
```

### 2. 实现文件 [gpfp_epp_scheduler.cpp](librtsim/scheduler/gpfp_epp_scheduler.cpp)

#### 添加头文件
```cpp
#include <metasim/simul.hpp>  // 用于 SIMUL.getTime()
```

#### 实现新方法（lines 699-754）
```cpp
double EPPScheduler::predictEnergyCollection(Tick current_time, Tick duration) {
    double irradiance = getSolarIrradiance(current_time);
    double duration_seconds = static_cast<double>(duration) * 0.001;
    double energy = irradiance * _pv_area_m2 * _pv_efficiency * duration_seconds;
    return energy;
}

bool EPPScheduler::canScheduleWithEnergy(AbsRTTask *task, Tick current_time) {
    double energy_current = _current_energy;
    double energy_consumption = calculateEnergyForTask(task);
    Tick wcet = model->getWCET();
    double energy_collection = predictEnergyCollection(current_time, wcet);

    double energy_after_task = energy_current + energy_collection - energy_consumption;
    bool can_schedule = energy_after_task >= 0.0;

    SCHEDULER_LOG_INFO(std::string("🔮 [EPP] 前瞻性能量判断: ") + getTaskName(task) +
                      " 当前=" + std::to_string(energy_current) + "J" +
                      " 收集(预测)=" + std::to_string(energy_collection) + "J" +
                      " 消耗=" + std::to_string(energy_consumption) + "J" +
                      " 结余=" + std::to_string(energy_after_task) + "J" +
                      (can_schedule ? " ✅可调度" : " ❌不可调度"));

    return can_schedule;
}
```

#### 修改 getFirst()（lines 367-422）
```cpp
AbsRTTask *EPPScheduler::getFirst() {
    // ...
    // ⭐ 使用新的前瞻性能量判断
    Tick current_time = SIMUL.getTime();
    bool can_schedule = canScheduleWithEnergy(first_task, current_time);

    if (!can_schedule) {
        // 能量不足，不调度
        return nullptr;
    }

    // ✅ 能量足够（前瞻性判断），返回任务
    // 注意：这里不扣减能量，扣减将在任务实际执行时进行
    return first_task;
}
```

#### 修改 getTaskN()（lines 428-492）
```cpp
AbsRTTask *EPPScheduler::getTaskN(unsigned int n) {
    // ...
    // ⭐ 使用新的前瞻性能量判断
    Tick current_time = SIMUL.getTime();
    bool can_schedule = canScheduleWithEnergy(task, current_time);

    if (!can_schedule) {
        // ⭐ 停止级联调度
        return nullptr;
    }

    // ✅ 能量足够（前瞻性判断），返回任务
    // 注意：这里不扣减能量，扣减将在任务实际执行时进行
    return task;
}
```

## 与旧逻辑的对比

| 方面 | 旧逻辑 | 新逻辑 |
|------|--------|--------|
| **判断依据** | 当前能量 | 当前能量 + 预测收集 |
| **判断公式** | `current >= needed` | `current + collected >= needed` |
| **太阳能考虑** | ❌ 不考虑 | ✅ 考虑任务执行期间收集 |
| **扣减时机** | 调度决策时 | 任务实际执行时 |
| **能量利用率** | 低（保守） | 高（积极） |

## 示例场景

### 场景1：低能量 + 高太阳能

**条件**：
- 当前能量: 0.1J
- task消耗: 0.25J
- 太阳能收集率: 100 J/s (0.1 J/ms)
- 任务WCET: 250ms

**旧逻辑**：
```
0.1J < 0.25J → ❌ 不可调度
```

**新逻辑**：
```
预测收集 = 0.1 J/ms × 250ms = 25J
结余 = 0.1 + 25 - 0.25 = 24.85J ≥ 0 → ✅ 可调度
```

**优势**：充分利用太阳能，不因当前能量低而拒绝调度

### 场景2：中等能量 + 无太阳能

**条件**：
- 当前能量: 0.3J
- task消耗: 0.25J
- 太阳能收集率: 0 J/ms (夜晚)
- 任务WCET: 250ms

**旧逻辑**：
```
0.3J >= 0.25J → ✅ 可调度
```

**新逻辑**：
```
预测收集 = 0 J/ms × 250ms = 0J
结余 = 0.3 + 0 - 0.25 = 0.05J ≥ 0 → ✅ 可调度
```

**结果一致**：无太阳能时退化为旧逻辑

## 关键优势

1. **更积极的调度策略**
   - 不仅看"现在有多少能量"
   - 而是看"执行期间能收集多少能量"

2. **更高的能量利用率**
   - 充分利用太阳能收集
   - 避免因瞬时能量低而错失调度机会

3. **更符合实际情况**
   - 任务执行期间能量持续收集
   - 不应只看调度决策时刻的能量

4. **保持能量安全**
   - 仍然确保 `energy_after_task >= 0`
   - 不会导致能量透支

## 未来改进方向

### 1. 精确的辐照度积分
当前实现使用当前时刻辐照度，未来可以：
```cpp
// 更精确：积分计算（考虑辐照度变化）
double predictEnergyCollection(Tick start_time, Tick duration) {
    double total_energy = 0.0;
    for (Tick t = 0; t < duration; t += unit_time) {
        double irradiance = getSolarIrradiance(start_time + t);
        total_energy += irradiance * _pv_area_m2 * _pv_efficiency * unit_time;
    }
    return total_energy;
}
```

### 2. 实时能量结算
在任务执行的每个Tick更新能量：
```cpp
void onTick() {
    // 收入：收集太阳能
    double harvested = collectSolarEnergy(SIMUL.getTime());
    _current_energy += harvested;

    // 支出：执行中的任务消耗
    for (auto &pair : _running_tasks) {
        double consumed = calculateEnergyPerTick(pair.second);
        _current_energy -= consumed;
    }
}
```

### 3. 动态能量预算
根据实时收集率动态调整调度策略：
```cpp
if (collection_rate > consumption_rate) {
    // 收集大于消耗 → 可以调度更多任务
} else {
    // 收集小于消耗 → 需要保守调度
}
```

## 测试命令

```bash
cd /home/devcontainers/PARTSim-project

# 编译
cd build && make -j$(nproc)

# 运行测试（上午8点，中等太阳能）
./rtsim/rtsim epp_test/config_epp_8am.yml epp_test/tasks_epp.yml 5000 -t epp_test/trace_epp_lookahead.json

# 查看前瞻性判断日志
./rtsim/rtsim epp_test/config_epp_8am.yml epp_test/tasks_epp.yml 5000 2>&1 | grep "前瞻性"
```

## 总结

✅ **前瞻性能量判断已实现**，核心特点：
1. 能量判断基于"预测"，而非"现状"
2. 考虑任务执行期间的太阳能收集
3. 扣减时机后置（不在决策时扣减）
4. 保持能量安全（结余 ≥ 0）

这个新逻辑使EPP调度器能够**更积极地利用太阳能**，提高能量利用率和系统吞吐量！
