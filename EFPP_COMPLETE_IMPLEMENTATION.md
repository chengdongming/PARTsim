# EFPP调度器完整实现指南
## Elastic Flexible Priority-based Power-aware Scheduler - 弹性优先级能量感知调度器

---

## 📋 目录

1. [算法概述](#算法概述)
2. [EPP vs EFPP 核心区别](#epp-vs-efpp-核心区别)
3. [EPP所有已修复问题汇总](#epp所有已修复问题汇总)
4. [EFPP算法设计](#efpp算法设计)
5. [实现方案](#实现方案)
6. [测试验证方案](#测试验证方案)

---

## 算法概述

### 定���
**EFPP调度器**（Elastic Flexible Priority-based Power-aware Scheduler）是一个继承EPP能量管理框架的弹性优先级能量感知调度算法，专为**能量供应不稳定但对实时性要求相对宽松的混合关键性系统**设计。

### 核心特性
- ✅ **能量硬约束**：能量不足时绝不调度任务
- ✅ **弹性优先级**：能量不足时允许调度低优先级任务，提高能量利用率
- ✅ **抢占��调度**：Tick级（1ms）抢占粒度
- ✅ **前瞻性预扣减**：继承EPP的能量预测和预扣减机制
- ✅ **主动能量管理**：预测能量恢复时间，定时器主动唤醒
- ✅ **多核支持**：支持多CPU并行调度

### 适用场景
- 太阳能供电的混合关键性实时系统
- 能量供应不稳定的环境
- 对实时性要求相对宽松的系统
- 需要最大化能量利用率和系统吞吐量的场景

---

## EPP vs EFPP 核心区别

### 最重要的区别：能量不足时的处理策略

| 场景 | EPP | EFPP |
|------|-----|------|
| **能量不足判断** | 当前能量 + 预测收集 - 任务能耗 < 0 | 当前能量 + 预测收集 - 任务能耗 < 0 |
| **最高优先级任务无法调度** | ⛔ **立即停止**，不检查后续任务 | ✅ **继续检查**次优先级任务 |
| **调度策略** | 优先级绝对优先（刚性） | 弹性优先级（灵活） |
| **能量利用** | 可能浪费（有能量但不使用） | 最大化利用（能调度的都调度） |
| **实时性保证** | 严格（高优先级必须先执行） | 相对宽松（高优先级可等待） |

### 伪代码对比

#### **EPP调度逻辑**：
```python
def epp_schedule(ready_queue):
    # 按优先级从高到低排序
    sorted_tasks = sort_by_priority(ready_queue)

    # 只检查最高优先级任务
    for task in sorted_tasks:
        if can_schedule(task):  # 能量足够
            schedule(task)
            break  # ⭐ 立即停止，不检查后续任务
        else:
            return None  # ⭐ 能量不足，立即返回
```

#### **EFPP调度逻辑**：
```python
def efpp_schedule(ready_queue):
    # 按优先级从高到低排序
    sorted_tasks = sort_by_priority(ready_queue)

    # 顺序检查所有任务
    for task in sorted_tasks:
        if can_schedule(task):  # 能量足够
            schedule(task)
            return  # ⭐ 找到第一个可调度的任务后返回
        else:
            continue  # ⭐ 继续检查次优先级任务

    # 所有任务都无法调度
    return None
```

### 实际场景对比

**场景**：
- 就绪队列：task_high(0.5J), task_mid(0.2J), task_low(0.1J)
- 当前能量：0.15J
- 预测收集：0.0J

**EPP行为**：
1. 检查task_high：需要0.5J，能量不足
2. 立即停止 ⛔
3. 结果：**无任务被调度**，0.15J能量闲置

**EFPP行为**：
1. 检查task_high：需要0.5J，能量不足 → 继续
2. 检查task_mid：需要0.2J，能量不足 → 继续
3. 检查task_low：需要0.1J，能量足够 ✅
4. 调度task_low
5. 结果：**task_low被调度**，能量被利用

---

## EPP所有已修复问题汇总

### 1. 硬编码问题 ✅
**问题**：能量模型参数硬编码在代码中
**修复**：
- `power_model`, `base_freq`, `max_energy`, `periodic_collection_interval`改为从YAML读取
- 通过ConfigManager统一管理
- 提交：`7231350 硬编码问题解决`

### 2. 初始能量为0时的调度问题 ✅
**问题**：系统初始能量为0时，EPP无法启动调度
**修复**：
- 修复能量恢复时间计算
- 修复初始化逻辑
- 提交：`e1b468f`, `ff369cd`, `8f2f3e4`

### 3. 初始能量收集问题 ✅
**问题**：初始阶段能量收集不正常
**修复**：
- 修复周期性能量收集逻辑
- 提交：`d3b8ece`

### 4. 能量消耗统计问题 ✅
**问题**：能量消耗统计不准确
**修复**：
- 提交：`f4deda5`

### 5. 周期性任务多实例调度问题 ✅
**问题**：周期性任务的多个实例调度不正确
**修复**：
- 提交：`72e7ea5`

### 6. 调度记录问题 ✅
**问题**：调度事件记录不完整或错误
**修复**：
- 提交：`53b0eff`, `1fa6961`

### 7. arrival_offset不工作 ✅
**问题**：所有任务都在0ms到达，arrival_offset参数被忽略
**修复**：
- `rtsim/main.cpp`：解析arrival_offset并传递给PeriodicTask的phase参数
- `librtsim/scheduler/gpfp_epp_scheduler.cpp`：解析并存储arrival_offset
- `librtsim/include/rtsim/scheduler/gpfp_epp_scheduler.hpp`：更新构造函数
- **这是框架级别的修复，适用于所有调度算法**

### 8. 能量管理桥接问题 ✅
**问题**：C++调度器和Python能量管理器之间的通信
**修复**：
- 增强energy_bridge.cpp
- 提交：`7231350`

---

## EFPP算法设计

### 核心设计原则

#### P1: 能量硬约束（继承EPP）
```
能量_当前 + 能量_预测收集 >= 任务能耗  → 可以调度
能量_当前 + 能量_预测收集 <  任务能耗  → 不能调度
```

#### P2: 弹性优先级调度（EFPP核心创新）⭐
```
检查顺序：就绪队列最高优先级 → 次优先级 → ...
停止条件：找到第一个能量足够的任务 → 调度并停止
特殊处理：所有任务均无法调度 → 启动能量恢复
```

**与EPP的区别**：
- **EPP**：最高优先级任务不能调度 → 立即停止
- **EFPP**：最高优先级任务不能调度 → 继续检查次优先级任务

#### P3: 抢占式调度（继承EPP）
```
抢占时机：任务到达的任意Tick时刻（1ms粒度）
抢占条件：(优先级更高) AND (能量足够)
```

#### P4: 前瞻性能量判断与预扣减（继承EPP）
```
调度时刻：前瞻性判断（能量_当前 + 能量_预测收集 >= 能量_消耗）
扣减时机：调度决策时立即扣减
判断公式：energy_after_task = energy_current + energy_predicted - energy_consumption
可调度条件：energy_after_task >= 0
```

#### P5: 主动能量管理（继承EPP）
```
能量不足：计算最高优先级任务的恢复时间 → 设置定时器 → 唤醒调度器
唤醒时机：能量足够调度最高优先级任务
```

### 关键算法流程

#### 1. 弹性调度决策（getTaskN）
```cpp
AbsRTTask* EFPFPScheduler::getTaskN(unsigned int n) {
    // 1. 调用基类获取第n个任务（按RM优先级排序）
    AbsRTTask* task = Scheduler::getTaskN(n);
    if (!task) return nullptr;

    // 2. 获取任务参数
    auto wcet_it = _task_wcets.find(task);
    if (wcet_it == _task_wcets.end()) {
        return nullptr;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    // 3. 前瞻性能量预测（继承EPP）
    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(task, wcet);

    // 4. 前瞻性判断
    double current_energy = getCurrentEnergy();
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        // ⭐ EFPP关键：返回nullptr，让内核继续检查下一个任务
        SCHEDULER_LOG_INFO("⏸️ 任务" + getTaskShortName(task) +
                          "能量不足，检查下一个任务");
        return nullptr;
    }

    // 5. 能量足够，预扣减并调度
    if (!consumeEnergy(total_energy, getTaskShortName(task))) {
        return nullptr;
    }

    // 6. 创建能量账目（继承EPP）
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    _energy_accounts[task] = account;

    SCHEDULER_LOG_INFO("✅ 弹性调度: " + getTaskShortName(task) +
                      " 预扣减: " + std::to_string(total_energy) + "J");

    return task;
}
```

#### 2. 能量恢复计算（基于最高优先级任务）
```cpp
void EFPFPScheduler::startEnergyRecovery() {
    if (_recovery_in_progress) {
        return;
    }

    // 1. 获取就绪队列中的最高优先级任务
    AbsRTTask* highest_task = nullptr;
    for (auto it = begin(); it != end(); ++it) {
        AbsRTTask* task = *it;
        if (!highest_task || getPriority(task) < getPriority(highest_task)) {
            highest_task = task;
        }
    }

    if (!highest_task) {
        SCHEDULER_LOG_WARNING("⚠️ 无任务需要恢复能量");
        return;
    }

    // 2. 计算最高优先级任务的能量需求
    auto wcet_it = _task_wcets.find(highest_task);
    if (wcet_it == _task_wcets.end()) {
        return;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    double total_energy = calculateTaskEnergy(highest_task, wcet);
    double current_energy = getCurrentEnergy();
    double energy_gap = total_energy - current_energy;

    // 3. 基于当前太阳能功率预测恢复时间
    TimeMs adjusted_time = getAdjustedTime(SIMUL.getTime());
    double current_power = EnergyBridge::getInstance().getHarvestingRate(adjusted_time);

    Tick recovery_time;
    if (current_power > 0.001) {
        // 计算需要的时���
        double seconds_needed = energy_gap / current_power;
        recovery_time = static_cast<Tick>(seconds_needed * 1000.0);

        // 添加安全边界（10%）
        recovery_time = static_cast<Tick>(recovery_time * 1.1);
    } else {
        // 夜晚，等待到明天早上6点
        int64_t current_ms = static_cast<int64_t>(adjusted_time);
        int64_t day_ms = current_ms / (24 * 3600 * 1000);
        int64_t next_morning = (day_ms + 1) * (24 * 3600 * 1000) + 6 * 3600 * 1000;
        recovery_time = static_cast<Tick>(next_morning - current_ms);
    }

    // 4. 设置定时器
    MetaSim::Tick current_tick = SIMUL.getTime();
    MetaSim::Tick wake_time = current_tick + recovery_time;

    if (_recovery_event) {
        delete _recovery_event;
    }

    _recovery_event = new EFPEnergyRecoveryEvent(this, highest_task);
    _recovery_event->post(wake_time);

    _recovery_in_progress = true;

    SCHEDULER_LOG_INFO("⏳ 能量恢复启动: 目标任务=" + getTaskShortName(highest_task) +
                      " 缺口=" + std::to_string(energy_gap) + "J" +
                      " 预计恢复时间=" + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");
}
```

#### 3. 抢占机制（继承EPP，增强能量检查）
```cpp
void EFPFPScheduler::insert(AbsRTTask *new_task) {
    if (!new_task) return;

    std::string new_task_name = getTaskShortName(new_task);
    int new_prio = getPriority(new_task);

    // 1. 前瞻性能量判断
    double current_energy = getCurrentEnergy();
    auto wcet_it = _task_wcets.find(new_task);
    if (wcet_it == _task_wcets.end()) {
        _waiting_queue.push_back(new_task);
        return;
    }

    Tick wcet = static_cast<Tick>(wcet_it->second);
    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(new_task, wcet);
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        // ⭐ EFPP：新任务能量不足，加入等待队列
        // 但不阻止其他任务调度（已在getTaskN中实现）
        _waiting_queue.push_back(new_task);
        SCHEDULER_LOG_INFO("⏸️ 新任务" + new_task_name + "能量不足，加入等待队列");
        return;
    }

    // 2. 检查是否需要抢占（继承EPP）
    bool need_preempt = false;
    AbsRTTask* victim_task = nullptr;

    for (AbsRTTask* running_task : _running_tasks) {
        int running_prio = getPriority(running_task);

        if (new_prio < running_prio) {  // 新任务优先级更高
            double preempt_energy = total_energy;

            // 计算被抢占任务的剩余能量（需要回退）
            auto account_it = _energy_accounts.find(running_task);
            if (account_it != _energy_accounts.end()) {
                double remaining_prepaid = account_it->second.prepaid -
                                           account_it->second.consumed;
                preempt_energy -= remaining_prepaid;
            }

            double energy_after_preempt = current_energy + predicted - preempt_energy;

            if (energy_after_preempt >= 0.0) {
                need_preempt = true;
                victim_task = running_task;
                break;
            }
        }
    }

    if (need_preempt && victim_task) {
        SCHEDULER_LOG_INFO("⚡ 抢占: " + getTaskShortName(victim_task) +
                          " 被 " + new_task_name + " 抢占");
        preemptTask(victim_task);
        Scheduler::insert(new_task);

        MRTKernel* kernel = getKernel();
        if (kernel) {
            kernel->dispatch();
        }
    } else {
        Scheduler::insert(new_task);
    }
}
```

---

## 实现方案

### 方案选择：基于EPP继承扩展

**推荐方案**：继承 `EPPScheduler`，重写调度决策相关方法

**理由**：
1. EFPP是EPP的**直接扩展**，只修改能量不足时的处理逻辑
2. 复用EPP的**能量管理框架**（预扣减、记账、结算）
3. 复用EPP的**能量预测**、**能量恢复**、**抢占机制**
4. 代码量小（EPP仅1509行），易于维护
5. 修改点明确，风险可控

### 架构设计

```
AbsScheduler (抽象调度器)
    ↓
Scheduler (通用调度器)
    ↓
EPPScheduler (能量感知调度器)
    ├── 能量硬约束
    ├── 前瞻性预测与预扣减
    ├── 能量记账与结算机制
    ├── 抢占机制
    └── 级联调度
    ↓
EFPFPScheduler (弹性优先级能量感知调度器)
    ├── 继承EPP所有能量管理机制
    └── 重写：能量不足时的处理逻辑
```

### 需要修改的方法

#### 1. getTaskN(unsigned int n) - 核心修改
```cpp
// 文件：librtsim/scheduler/gpfp_efpp_scheduler.cpp
AbsRTTask* EFPFPScheduler::getTaskN(unsigned int n) {
    // 1. 调用基类获取任务
    AbsRTTask* task = EPPScheduler::getTaskN(n);
    if (!task) return nullptr;

    // 2. 前瞻性判断（继承EPP逻辑）
    auto wcet_it = _task_wcets.find(task);
    if (wcet_it == _task_wcets.end()) {
        return nullptr;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(task, wcet);

    double current_energy = getCurrentEnergy();
    double energy_after = current_energy + predicted - total_energy;

    // ⭐ EFPP关键修改：能量不足时返回nullptr，让内核继续检查下一个任务
    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("⏸️ [EFPP] 任务" + getTaskShortName(task) +
                          "能量不足，继续检查下一个任务");
        return nullptr;  // 返回nullptr，内核会调用getTaskN(n+1)
    }

    // 3. 能量足够，执行EPP的预扣减和记账逻辑
    if (!consumeEnergy(total_energy, getTaskShortName(task))) {
        return nullptr;
    }

    // 4. 创建能量账目（继承EPP）
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    _energy_accounts[task] = account;

    SCHEDULER_LOG_INFO("✅ [EFPP] 弹性调度任务: " + getTaskShortName(task) +
                      " 优先级: " + std::to_string(getPriority(task)) +
                      " 预扣减能量: " + std::to_string(total_energy) + "J");

    return task;
}
```

#### 2. startEnergyRecovery() - 基于最高优先级任务
```cpp
// 文件：librtsim/scheduler/gpfp_efpp_scheduler.cpp
void EFPFPScheduler::startEnergyRecovery() {
    if (_recovery_in_progress) {
        return;
    }

    // 1. 获取就绪队列中的最高优先级任务
    AbsRTTask* highest_task = getHighestPriorityTask();
    if (!highest_task) {
        SCHEDULER_LOG_WARNING("⚠️ [EFPP] 就绪队列为空，无需能量恢复");
        return;
    }

    // 2. 计算最高优先级任务的能量需求
    auto wcet_it = _task_wcets.find(highest_task);
    if (wcet_it == _task_wcets.end()) {
        SCHEDULER_LOG_ERROR("❌ [EFPP] 未找到任务WCET");
        return;
    }

    Tick wcet = static_cast<Tick>(wcet_it->second);
    double total_energy = calculateTaskEnergy(highest_task, wcet);

    // 3. 考虑执行期间收集的能量
    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);

    double current_energy = getCurrentEnergy();
    double energy_gap = total_energy - predicted - current_energy;

    if (energy_gap <= 0.0) {
        // 能量其实足够，可能预测不准，直接触发调度
        SCHEDULER_LOG_INFO("⚠️ [EFPP] 能量实际上足够，触发调度");
        MRTKernel* kernel = getKernel();
        if (kernel) {
            kernel->dispatch();
        }
        return;
    }

    // 4. 计算恢复时间
    TimeMs adjusted_time = getAdjustedTime(current_time);
    double current_power = EnergyBridge::getInstance().getHarvestingRate(adjusted_time);

    Tick recovery_time;
    if (current_power > 0.001) {
        double seconds_needed = energy_gap / current_power;
        recovery_time = static_cast<Tick>(seconds_needed * 1000.0);
        // 添加10%安全边界
        recovery_time = static_cast<Tick>(recovery_time * 1.1);
    } else {
        // 夜晚，等待到明天早上6点
        int64_t current_ms = static_cast<int64_t>(adjusted_time);
        int64_t day_ms = current_ms / (24 * 3600 * 1000);
        int64_t next_morning = (day_ms + 1) * (24 * 3600 * 1000) + 6 * 3600 * 1000;
        recovery_time = static_cast<Tick>(next_morning - current_ms);
    }

    // 5. 设置定时器
    MetaSim::Tick wake_time = current_time + recovery_time;

    if (_recovery_event) {
        delete _recovery_event;
    }

    _recovery_event = new EFPEnergyRecoveryEvent(this, highest_task);
    _recovery_event->post(wake_time);

    _recovery_in_progress = true;

    SCHEDULER_LOG_INFO("⏳ [EFPP] 能量恢复启动");
    SCHEDULER_LOG_INFO("  目标任务: " + getTaskShortName(highest_task));
    SCHEDULER_LOG_INFO("  优先级: " + std::to_string(getPriority(highest_task)));
    SCHEDULER_LOG_INFO("  能量缺口: " + std::to_string(energy_gap) + "J");
    SCHEDULER_LOG_INFO("  当前功率: " + std::to_string(current_power) + "W");
    SCHEDULER_LOG_INFO("  预计恢复: " + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");
}
```

#### 3. 辅助方法：getHighestPriorityTask()
```cpp
// 文件：librtsim/scheduler/gpfp_efpp_scheduler.cpp
AbsRTTask* EFPFPScheduler::getHighestPriorityTask() {
    AbsRTTask* highest_task = nullptr;
    int highest_priority = INT_MAX;

    for (auto it = begin(); it != end(); ++it) {
        AbsRTTask* task = *it;
        int prio = getPriority(task);

        if (prio < highest_priority) {
            highest_priority = prio;
            highest_task = task;
        }
    }

    return highest_task;
}
```

### 文件结构

```
librtsim/
├── scheduler/
│   ├── gpfp_epp_scheduler.hpp         # EPP头文件（已存在）
│   ├── gpfp_epp_scheduler.cpp         # EPP实现（已存在）
│   ├── gpfp_efpp_scheduler.hpp        # EFPP头文件（新增）
│   └── gpfp_efpp_scheduler.cpp        # EFPP实现（新增）
└── include/rtsim/scheduler/
    └── gpfp_efpp_scheduler.hpp        # EFPP头文件链接（新增）
```

### 头文件定义

```cpp
// 文件：librtsim/include/rtsim/scheduler/gpfp_efpp_scheduler.hpp

#ifndef RTSIM_GPFP_EFPP_SCHEDULER_HPP
#define RTSIM_GPFP_EFPP_SCHEDULER_HPP

#include <rtsim/scheduler/gpfp_epp_scheduler.hpp>

namespace RTSim {

class EFPFPScheduler : public EPPScheduler {
public:
    EFPFPScheduler(std::string name = "EFPP");
    virtual ~EFPFPScheduler();

    // 重写getTaskN实现弹性调度
    virtual AbsRTTask* getTaskN(unsigned int n) override;

    // 重写能量恢复（基于最高优先级任务）
    virtual void startEnergyRecovery() override;

private:
    // 获取就绪队列中最高优先级任务
    AbsRTTask* getHighestPriorityTask();

    // EFPP特有的能量恢复事件
    class EFPEnergyRecoveryEvent : public MetaSim::Event {
    private:
        EFPFPScheduler* _scheduler;
        AbsRTask* _target_task;
    public:
        EFPEnergyRecoveryEvent(EFPFPScheduler* sched, AbsRTask* task);
        virtual void doit() override;
    };

    EFPEnergyRecoveryEvent* _recovery_event;
    bool _recovery_in_progress;
};

} // namespace RTSim

#endif // RTSIM_GPFP_EFPP_SCHEDULER_HPP
```

---

## 测试验证方案

### 测试场景1：弹性调度验证 ⭐

**配置**：
```yaml
tasks:
  - name: task_high
    period: 400
    wcet: 100
    arrival_offset: 0
    workload: bzip2
    energy_coefficient: 1.2

  - name: task_mid
    period: 600
    wcet: 150
    arrival_offset: 0
    workload: bzip2
    energy_coefficient: 1.2

  - name: task_low
    period: 800
    wcet: 200
    arrival_offset: 0
    workload: bzip2
    energy_coefficient: 1.2

system:
  initial_energy: 0.15J  # 只够执行task_low
  solar_power: 0.0W     # 无能量收集
```

**预期行为（EFPP）**：
1. 检查task_high：需要0.12J，能量不足（0.15J < 0.12J）→ 继续
2. 检查task_mid：需要0.18J，能量不足（0.15J < 0.18J）→ 继续
3. 检查task_low：需要0.16J，能量不足（0.15J < 0.16J）→ 继续
4. 所有任务都无法调度 → 启动能量恢复 ⏳

**对比EPP行为**：
1. 检查task_high：能量不足 → **立即停止** ⛔

### 测试场景2：弹性调度成功案例 ⭐

**配置**：
```yaml
tasks: (同上)
system:
  initial_energy: 0.20J  # 够执行task_mid和task_low
  solar_power: 0.0W
```

**预期行为（EFPP）**：
1. 检查task_high：需要0.12J，能量足够 → ✅ 调度task_high
2. 级联调度：task_high执行完成后，继续检查其他任务

**如果初始能量只有0.15J**：
1. 检查task_high：需要0.12J，能量不足 → 继续
2. 检查task_mid：需要0.18J，能量不足 → 继续
3. 检查task_low：需要0.16J，能量不足 → 继续
4. 启动能量恢复 ⏳

### 测试场景3：抢占场景下的弹性调度

**配置**：
```yaml
system:
  initial_energy: 0.3J
  solar_power: 0.0W

timeline:
  T=0ms: task_low开始执行（wcet=200ms, 能量=0.16J）
  T=50ms: task_high到达（wcet=100ms, 能量=0.12J, 优先级更高）
```

**预期行为（EFPP）**：
1. T=0ms：task_low被调度，预扣减0.16J，剩余0.14J
2. T=50ms：task_high到达
3. 检查抢占：
   - task_high优先级更高 ✅
   - 检查能量：需要0.12J，当前0.14J，能量足够 ✅
4. 执行抢占：
   - 结算task_low账目：已执行50ms（0.04J），回退0.12J
   - 系统能量：0.14 + 0.12 = 0.26J
   - 调度task_high：预扣减0.12J，剩余0.14J
5. task_high执行完成后，task_low继续执行

### 测试场景4：能量恢复验证

**配置**：
```yaml
system:
  initial_energy: 0.05J
  solar_power: 0.5W  # 稳定太阳能输入

tasks:
  - name: task_high
    wcet: 100
    energy_coefficient: 1.2
    # 能量需求: 100ms × 1.0W × 1.2 = 0.12J
```

**预期行为（EFPP）**：
1. 检查task_high：需要0.12J，当前0.05J，能量不足
2. 计算能量缺口：0.12 - 0.05 = 0.07J
3. 预测恢复时间：0.07J ÷ 0.5W = 0.14s = 140ms
4. 设置定时器：140ms后唤醒
5. T=140ms：定时器触发，能量收集完成
6. 重新调度task_high ✅

### 性能对比测试

**测试目标**：对比EPP和EFPP在能量不足时的吞吐量

**配置**：
```yaml
system:
  initial_energy: 0.2J
  solar_power: 0.1W  # 持续但低功率的能量收集

tasks:
  - task_high: wcet=150ms, energy=0.18J, period=500ms
  - task_mid: wcet=100ms, energy=0.12J, period=600ms
  - task_low: wcet=50ms, energy=0.06J, period=800ms
```

**预期结果**：
- **EPP**：
  - T=0ms：task_high能量不足 → 停止，无任务执行
  - 吞吐量：0个任务

- **EFPP**：
  - T=0ms：task_high能量不足 → 检查task_mid
  - task_mid能量不足 → 检查task_low
  - task_low能量足够 ✅ → 执行task_low
  - 吞吐量：1个任务完成
  - T=50ms：task_low完成，能量收集0.005J，总能量0.145J
  - 继续检查其他任务...

---

## 总结

### EFPP vs EPP 核心区别总结

| 特性 | EPP | EFPP |
|------|-----|------|
| **能量不足策略** | 立即停止 | 继续检查低优先级任务 |
| **优先级策略** | 刚性（绝对优先） | 弹性（能量足够者优先） |
| **能量利用** | 可能浪费 | 最大化利用 |
| **实时性保证** | 严格 | 相对宽松 |
| **适用场景** | 严格实时系统 | 混合关键性系统 |
| **吞吐量** | 较低 | 较高 |
| **代码复用** | - | 完全继承EPP框架 |

### EFPP优势

1. **更高的能量利用率**：不浪费可用能量
2. **更高的系统吞吐量**：能量不足时仍能执行低优先级任务
3. **继承EPP所有优点**：能量硬约束、前瞻性预测、预扣减机制
4. **实现简单**：只需修改getTaskN()的逻辑
5. **代码复用率高**：95%以上代码复用EPP

### EFPP劣势

1. **实时性保证较弱**：高优先级任务可能被低优先级任务延迟
2. **不适用严格实时系统**：可能违反截止期限
3. **需要careful priority设计**：优先级分配更复杂

### 实现工作量估算

- **新增文件**：2个（.hpp + .cpp）
- **代码量**：约300-400行（EPP有1509行）
- **开发时间**：2-3天（包括测试）
- **风险等级**：低（大部分逻辑继承EPP）

---

**文档版本**: 1.0
**创建时间**: 2026-01-18
**作者**: Claude + 用户协作
**项目**: PARTSim - 多核实时系统模拟器
