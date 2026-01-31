# TIE/TGF 纯Tick边界调度重构设计文档

## 文档版本信息
- **版本**: v1.1 (修正版)
- **日期**: 2026-01-31
- **作者**: Claude Code
- **目标**: 将TIE/TGF重构为纯tick边界调度，所有能量操作集中在1ms tick边界
- **状态**: 已审查并修正初始设计中的问题

## ⭐ v1.1 重要修正

本设计文档已根据实际代码审查进行了修正。主要修正包括：

1. **`_counted_tasks_in_dispatch` 填充时机**: 在 `getTaskN` 中标记，而不是在 `onEndDispatchMulti` 中
2. **`getCurrentExecutingTasks()` 返回类型**: 返回 `std::map<CPU*, AbsRTTask*>`，需要迭代键值对
3. **MRTKernel 修改**: 不需要修改 MRTKernel，保持其通用性
4. **BTIE 批量调度**: 需要单独处理，保留其"全有或全无"逻辑
5. **能量检查事件删除**: 续期能量扣除由 `performTickScheduling` 接管

详细修正说明见**附录B：设计文档审查发现的问题与修复**。

---

## 1. 设计目标

### 1.1 核心原则
- **时间粒度**: 1 tick = 1 ms，这是最基本的调度单位
- **边界集中**: 所有能量操作（收集、扣除、检查）都在tick边界完成
- **逻辑清晰**: 代码结构与设计逻��完全一致

### 1.2 调度逻辑（用户提供的原始设计）

#### TIE (Tick-based Instant Energy-aware)
在多核全局调度场景下，每个 1ms Tick 开始并完成能量收集后，TIE 调度器面对的是一个单一的全局就绪队列和多个空闲核心。调度器严格遵循优先级顺序进行分配：它锁定全局队列中优先级最高的任务，判断当前电量是否满足该任务运行 1ms。如果满足，则将该任务分发给第一个空闲核心，扣除对应的虚拟能量配额，然后继续对队列中次高优先级的任务进行同样的判断，试图分发给下一个核心。关键约束在于，一旦遇到任意一个高优先级任务因电量不足无法运行，TIE 会立即停止当前的所有调度尝试，即使后续还有空闲核心且队列后方有低功耗任务，系统也会强制剩余核心保持空闲。

**关键特征**:
- ✅ 严格优先级顺序（从高到低）
- ✅ 立即扣除能量配额
- ✅ 遇到能量���足立即停止级联
- ✅ 强制剩余核心保持空闲

#### TGF (Tick-based Greedy First)
在 1ms Tick 的决策阶段，TGF 旨在榨干每一分可用能量以最大化多核并行度。面对全局就绪队列，调度器启动全队列贪婪扫描。它从高优先级向低优先级遍历任务，寻找能耗需求小于当前剩余可用电量的"候选者"。一旦找到一个合适的任务，就立即将其分发给当前的一个空闲核心，并从暂存电量中扣除相应份额，然后继续扫描剩余队列以填充下一个核心。这个过程会一直持续，直到所有核心都被填满，或者遍历完整个队列仍无法找到任何可运行的任务。在多核视角下，TGF 能够通过"跳过"那些电量不够的大任务，将多个小任务同时调度到不同的核心上运行，极大地减少了核心的空转时间，实现了碎片化能量的高效并行利用。

**关键特征**:
- ✅ 全队列贪婪扫描
- ✅ 跳过高耗能任务
- ✅ 最大化填充空闲核心
- ✅ 碎片化能量利用

---

## 2. 当前实现的问题分析

### 2.1 能量操作分散在三个位置

```cpp
// 位置1: Tick边界收集能量 (performTickScheduling)
performTickScheduling() {
    double harvested = collectSolarEnergy(current_time);
    _current_energy += harvested;  // ✅ 收集能量
    dispatch();
}

// 位置2: 调度时预扣能量 (getTaskN)
getTaskN(n) {
    if (_current_energy < unit_energy) {
        return nullptr;
    }
    _current_energy -= unit_energy;  // ❌ 在这里扣除
    _counted_tasks_in_dispatch.insert(task);
    return task;
}

// 位置3: 运行时扣除续期能量 (TIEEnergyCheckEvent)
TIEEnergyCheckEvent::doit() {
    if (current_energy <= unit_energy) {
        suspend();
        return;
    }
    _current_energy -= unit_energy;  // ❌ 在这里扣除
    post(SIMUL.getTime() + 1);
}
```

### 2.2 事件优先级冲突

```
时刻 t:
  1. TIEEnergyCheckEvent (优先级 -5) 先触发
     → 扣除运行任务的续期能量
     → post(t+1)

  2. TIETickEvent (优先级 +10) 后触发
     → performTickScheduling()
     → 收集能量
     → getTaskN() 扣除新任务能量
```

### 2.3 时序混乱导致的问题

| 问题 | 影响 | 根本原因 |
|------|------|---------|
| BTIE line 184检查错误 | BTIE比TIE早1ms终止 | 检查全局能量而非预扣能量 |
| TIE/TGF descheduled时间差1ms | 时序不一致 | 能量检查事件和tick事件时序冲突 |
| 能量扣除重复或遗漏 | 能量记账错误 | 预扣和续期扣除逻辑复杂 |

---

## 3. 重构方案：纯Tick边界调度

### 3.1 核心设计思想

```
每个Tick (1ms) 的完整流程：

┌─────────────────────────────────────────────────┐
│  Tick时刻 t (例如 10ms)                          │
└─────────────────────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ performTickScheduling  │
        └───────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
   ┌─────────┐ ┌──────────┐ ┌─────────────┐
   │1.收集  │ │2.续期    │ │3.调度新任务  │
   │ 太阳能  │ │运行任务  │ │(getTaskN)   │
   │+能量   │ │-能量     │ │-能量        │
   └─────────┘ └──────────┘ └─────────────┘
        │           │           │
        └───────────┴───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 任务执行 1ms          │
        │ (不触发能量检查事件)   │
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ Tick时刻 t+1          │
        │ 重复上述流程           │
        └───────────────────────┘
```

### 3.2 关键设计决策

#### 决策1: 移除能量检查事件
```cpp
// ❌ 删除 TIEEnergyCheckEvent
// ❌ 删除 startEnergyCheckForTask()
// ❌ 删除 stopEnergyCheckForTask()

// ✅ 理由：所有能量操作在performTickScheduling中完成
```

#### 决策2: getTaskN只做决策，不扣除能量
```cpp
AbsRTTask *TIEScheduler::getTaskN(unsigned int n) {
    // ⭐ 只检查能量是否足够，不扣除能量
    if (_current_energy < unit_energy - EPSILON) {
        return nullptr;
    }

    // ⭐ 返回任务，不扣除能量
    return task;
}
```

#### 决策3: 统一在performTickScheduling中扣除所有能量
```cpp
void TIEScheduler::performTickScheduling() {
    // 1. 收集太阳能
    _current_energy += collectSolarEnergy(current_time);

    // 2. 扣除运行任务的续期能量
    for (running_task : running_tasks) {
        _current_energy -= unit_energy;
    }

    // 3. 调度新任务（getTaskN只做决策）
    dispatch();

    // 4. 扣除新任务的初始能量
    for (new_task : newly_scheduled_tasks) {
        _current_energy -= unit_energy;
    }
}
```

---

## 4. 详细实现方案

### 4.1 performTickScheduling 重构（修正版）

```cpp
void TIEScheduler::performTickScheduling() {
    Tick current_time = SIMUL.getTime();
    _stats.total_tick_count++;

    SCHEDULER_LOG_INFO("🔄 [TIE] ===== Tick " +
                       std::to_string(static_cast<int64_t>(current_time)) + "ms =====");
    SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

    // ========== 第1步：收集太阳能 ==========
    Tick elapsed = current_time - _last_tick_time;
    if (elapsed > 0) {
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.000001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
            SCHEDULER_LOG_INFO("☀️ 收集太阳能: +" +
                               std::to_string(harvested * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        }
    }
    _last_tick_time = current_time;

    // 确保能量不超过最大容量
    if (_current_energy > _max_energy) {
        _current_energy = _max_energy;
    }

    // ========== 第2步：处理运行中任务的续期能量 ==========
    // ⭐ 修正：getCurrentExecutingTasks() 返回 std::map<CPU*, AbsRTTask*>
    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> tasks_to_suspend;

        SCHEDULER_LOG_INFO("🏃 检查运行任务: " +
                           std::to_string(running_tasks_map.size()) + " 个");

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;

            double unit_energy = calculateUnitEnergyForTask(task);

            // 检查是否有足够能量续期1ms
            const double EPSILON = 1e-9;
            if (_current_energy < unit_energy - EPSILON) {
                // 能量不足，加入挂起列表
                tasks_to_suspend.push_back(task);
                SCHEDULER_LOG_WARNING("⚠️ 续期能量不足，将挂起: " +
                                     getTaskName(task) +
                                     " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                     " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
            } else {
                // 扣除续期能量
                double old_energy = _current_energy;
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;

                SCHEDULER_LOG_INFO("⚡ 扣除续期能量: " +
                                   getTaskName(task) +
                                   " -" + std::to_string(unit_energy * 1000) + " mJ " +
                                   std::to_string(old_energy * 1000) + " → " +
                                   std::to_string(_current_energy * 1000) + " mJ");
            }
        }

        // 挂起能量不足的任务
        for (AbsRTTask *task : tasks_to_suspend) {
            _kernel->suspend(task);
            SCHEDULER_LOG_INFO("🛑 挂起任务: " + getTaskName(task));
        }
    }

    // ========== 第3步：检查抢占 ==========
    checkAndPreempt();

    // ========== 第4步：调度新任务 ==========
    if (_kernel) {
        SCHEDULER_LOG_INFO("🔔 开始调度新任务");

        // 记录调度前的能量
        double energy_before_scheduling = _current_energy;

        // ⭐ 关键：清空本次tick的调度记录
        // getTaskN会填充这个集合，但不扣除能量
        _counted_tasks_in_dispatch.clear();
        _dispatching_tasks_total_energy = 0.0;

        // 调度任务（getTaskN只做决策和标记，不扣除能量）
        _kernel->dispatch();

        // ⭐ 关键：在dispatch后，统一扣除所有已标记任务的能量
        for (AbsRTTask *task : _counted_tasks_in_dispatch) {
            double unit_energy = calculateUnitEnergyForTask(task);
            _current_energy -= unit_energy;
            _stats.total_energy_consumed += unit_energy;
            _dispatching_tasks_total_energy += unit_energy;

            SCHEDULER_LOG_INFO("✅ 新任务扣除初始能量: " +
                               getTaskName(task) +
                               " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        }

        SCHEDULER_LOG_INFO("📊 调度完成: 新任务=" +
                           std::to_string(_counted_tasks_in_dispatch.size()) +
                           " 扣除能量=" + std::to_string(_dispatching_tasks_total_energy * 1000) + " mJ " +
                           std::to_string(energy_before_scheduling * 1000) + " → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    SCHEDULER_LOG_INFO("✅ Tick " +
                       std::to_string(static_cast<int64_t>(current_time)) +
                       "ms 完成, 剩余能量: " +
                       std::to_string(_current_energy * 1000) + " mJ");
}
```

### 4.2 getTaskN重构（只做决策和标记，不扣除能量）

```cpp
AbsRTTask *TIEScheduler::getTaskN(unsigned int n) {
    SCHEDULER_LOG_DEBUG("🔍 getTaskN(" + std::to_string(n) + ")");

    if (_ready_queue.empty()) {
        SCHEDULER_LOG_DEBUG("📭 就绪队列为空");
        return nullptr;
    }

    // 遍历就绪队列
    unsigned int ready_index = 0;
    for (size_t i = 0; i < _ready_queue.size(); ++i) {
        AbsRTTask *task = _ready_queue[i];
        if (!task) continue;

        // 检查是否已在运行
        bool is_running = false;
        if (_kernel) {
            CPU *proc = _kernel->getProcessor(task);
            is_running = (proc != nullptr);
        }

        if (is_running) {
            // 运行中任务：直接返回
            if (ready_index == n) {
                SCHEDULER_LOG_DEBUG("♻️ 返回运行中任务: " + getTaskName(task));
                return task;
            }
            ready_index++;
            continue;
        }

        // 新任务：检查能量是否足够
        if (ready_index == n) {
            double unit_energy = calculateUnitEnergyForTask(task);
            const double EPSILON = 1e-9;

            // ⭐ 只检查能量，不扣除能量
            if (_current_energy < unit_energy - EPSILON) {
                SCHEDULER_LOG_INFO("⚠️ 能量不足，停止级联: " +
                                  getTaskName(task) +
                                  " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                  " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
                return nullptr;  // TIE: 立即停止级联
            }

            // ⭐ 关键修改：只标记任务，不扣除能量
            // 能量将在performTickScheduling的dispatch后统一扣除
            _counted_tasks_in_dispatch.insert(task);
            SCHEDULER_LOG_INFO("✅ 决定调度任务（已标记，暂不扣能量）: " + getTaskName(task));
            return task;
        }

        ready_index++;
    }

    return nullptr;
}
```

### 4.3 MRTKernel::onEndDispatchMulti 不需要修改

**原设计文档问题:**
建议修改 `MRTKernel::onEndDispatchMulti` 来标记任务。

**修正:**
不需要修改 MRTKernel。`_counted_tasks_in_dispatch` 的标记已经在 `getTaskN` 中完成：

```cpp
// ✅ 不需要修改 MRTKernel
// MRTKernel 保持通用性，不依赖具体调度器类型

// ✅ getTaskN 中已经标记任务
_counted_tasks_in_dispatch.insert(task);
```

**删除的能量检查事件调用:**

在 MRTKernel 中，需要删除对 `startEnergyCheckForTask` 的调用：

```cpp
void MRTKernel::onEndDispatchMulti(EndDispatchMultiEvt *e) {
    AbsRTTask *st = e->getTask();
    CPU *p = e->getCPU();

    _m_currExe[p] = st;

    if (st) {
        // ... 原有逻辑 ...

        // ❌ 删除：startEnergyCheckForTask(st, p);
        // 能量检查事件已被移除，续期能量由performTickScheduling处理
    }
}
```

### 4.4 TGF的getTaskN实现（贪婪策略）

```cpp
AbsRTTask *TGFScheduler::getTaskN(unsigned int n) {
    SCHEDULER_LOG_DEBUG("🔍 [TGF] getTaskN(" + std::to_string(n) + ")");

    if (_ready_queue.empty()) {
        return nullptr;
    }

    unsigned int ready_index = 0;
    const double EPSILON = 1e-9;

    // 遍历就绪队列
    for (size_t i = 0; i < _ready_queue.size(); ++i) {
        AbsRTTask *task = _ready_queue[i];
        if (!task) continue;

        // 检查是否已在运行
        bool is_running = false;
        if (_kernel) {
            CPU *proc = _kernel->getProcessor(task);
            is_running = (proc != nullptr);
        }

        if (is_running) {
            if (ready_index == n) return task;
            ready_index++;
            continue;
        }

        // 新任务：检查能量
        if (ready_index == n) {
            double unit_energy = calculateUnitEnergyForTask(task);

            // ⭐ TGF贪婪策略：能量不足时跳过，继续查找后续任务
            if (_current_energy < unit_energy - EPSILON) {
                SCHEDULER_LOG_INFO("⚠️ [TGF] 任务能量不足，尝试贪婪搜索: " +
                                  getTaskName(task));

                // 贪婪搜索：跳过当前任务，查找后续能量足够的任务
                for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
                    AbsRTTask *next_task = _ready_queue[j];
                    if (!next_task) continue;

                    // 检查是否已在运行
                    bool next_running = false;
                    if (_kernel) {
                        CPU *proc = _kernel->getProcessor(next_task);
                        next_running = (proc != nullptr);
                    }
                    if (next_running) continue;

                    double next_unit_energy = calculateUnitEnergyForTask(next_task);

                    if (_current_energy >= next_unit_energy - EPSILON) {
                        // ⭐ 找到能量足够的后续任务！
                        SCHEDULER_LOG_INFO("✅ [TGF] 贪婪策略：调度后续任务 " +
                                          getTaskName(task) + " → " +
                                          getTaskName(next_task));
                        // ⭐ 只标记任务，不扣除能量
                        _counted_tasks_in_dispatch.insert(next_task);
                        return next_task;
                    }
                }

                SCHEDULER_LOG_INFO("❌ [TGF] 未找到能量足够的任务");
                return nullptr;
            }

            // ⭐ 能量足够，返回任务（不扣除能量）
            SCHEDULER_LOG_DEBUG("✅ 决定调度任务: " + getTaskName(task));
            // ⭐ 只标记任务，不扣除能量
            _counted_tasks_in_dispatch.insert(task);
            return task;
        }

        ready_index++;
    }

    return nullptr;
}

### 4.5 BTIE的performTickScheduling重构（批量调度）

BTIE 有特殊的"全有或全无"批量调度逻辑，需要单独处理：

```cpp
void BTIEScheduler::performTickScheduling() {
    Tick current_time = SIMUL.getTime();
    _stats.total_tick_count++;

    SCHEDULER_LOG_INFO("🔄 [BTIE] ===== Tick " +
                       std::to_string(static_cast<int64_t>(current_time)) + "ms =====");

    // ========== 第1步：收集太阳能 ==========
    double harvested = collectSolarEnergy(current_time);
    if (harvested > 0.000001) {
        _current_energy += harvested;
        _stats.total_energy_harvested += harvested;
    }

    // ========== 第2步：计算所有任务的总能量需求 ==========
    const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();

    // 2.1 计算运行中任务的续期能量
    double running_tasks_renewal_energy = 0.0;
    std::vector<AbsRTTask *> running_task_list;
    for (const auto& [cpu, task] : running_tasks_map) {
        if (task && task->isExecuting()) {
            running_tasks_renewal_energy += calculateUnitEnergyForTask(task);
            running_task_list.push_back(task);
        }
    }

    // 2.2 计算新任务的能量
    double new_tasks_energy = 0.0;
    std::vector<AbsRTTask *> new_tasks_to_schedule;
    size_t K = std::min(static_cast<size_t>(free_cpus), _ready_queue.size());

    for (size_t i = 0; i < K; ++i) {
        if (i < _ready_queue.size()) {
            AbsRTTask *task = _ready_queue[i];
            new_tasks_energy += calculateUnitEnergyForTask(task);
            new_tasks_to_schedule.push_back(task);
        }
    }

    // ⭐ BTIE总能量需求 = 运行中任务续期 + 新任务
    double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;

    // ========== 第3步：BTIE"全有或全无"批量判断 ==========
    const double EPSILON = 1e-9;
    if (_current_energy > total_energy_needed - EPSILON) {
        // ✅ 能量充足：批量调度所有任务

        // ⭐ 一次性扣除所有能量（运行中续期 + 新任务初始）
        _current_energy -= total_energy_needed;
        _stats.total_energy_consumed += total_energy_needed;

        SCHEDULER_LOG_INFO("✅ [BTIE] 批量调度成功: " +
                          "运行任务数=" + std::to_string(running_task_list.size()) +
                          " 新任务数=" + std::to_string(new_tasks_to_schedule.size()) +
                          " 总能耗=" + std::to_string(total_energy_needed * 1000) + " mJ");

        // 继续调度新任务...
        _kernel->dispatch();
        // 注意：getTaskN不扣除能量，能量已在上面批量扣除

    } else {
        // ❌ 能量不足：BTIE"全无"原则
        _batch_scheduled_this_tick = false;
        _current_energy = 0.0;

        SCHEDULER_LOG_WARNING("❌ [BTIE] 能量不足，批量调度失败（全无原则）: " +
                             "总需要=" + std::to_string(total_energy_needed * 1000) + " mJ");

        // ⭐ 挂起所有运行中任务
        for (const auto& [cpu, task] : running_tasks_map) {
            if (task && task->isExecuting()) {
                _kernel->suspend(task);
            }
        }
    }

    // 检查抢占
    checkAndPreempt();

    SCHEDULER_LOG_INFO("✅ Tick 完成, 剩余能量: " +
                       std::to_string(_current_energy * 1000) + " mJ");
}
```

---

## 5. 代码删除清单

### 5.1 需要删除的类和方法

```cpp
// ❌ 删除整个类
class TIEEnergyCheckEvent;  // librtsim/scheduler/gpfp_tie_scheduler.hpp:43-56
class TGFEnergyCheckEvent;  // librtsim/scheduler/gpfp_tgf_scheduler.hpp:43-56
class BTIEEnergyCheckEvent; // librtsim/scheduler/gpfp_btie_scheduler.hpp:43-56

// ❌ 删除方法
void TIEScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);
void TIEScheduler::stopEnergyCheckForTask(AbsRTTask *task);
void TGFScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);
void TGFScheduler::stopEnergyCheckForTask(AbsRTTask *task);
void BTIEScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);
void BTIEScheduler::stopEnergyCheckForTask(AbsRTTask *task);

// ❌ 删除成员变量
std::map<AbsRTTask *, TIEEnergyCheckEvent *> _energy_check_events;
std::map<AbsRTTask *, TGFEnergyCheckEvent *> _energy_check_events;
std::map<AbsRTTask *, BTIEEnergyCheckEvent *> _energy_check_events;
```

### 5.2 需要修改的方法签名

| 方法 | 当前签名 | 新签名 | 说明 |
|------|---------|--------|------|
| `getTaskN` | 可能扣除能量 | 只做决策，不扣除能量 | 核心修改 |
| `onEndDispatchMulti` | 启动能量检查事件 | 标记任务为已调度 | MRTKernel修改 |

---

## 6. 实现步骤（修正版）

### 第1步：备份现有代码
```bash
cp librtsim/scheduler/gpfp_tie_scheduler.cpp librtsim/scheduler/gpfp_tie_scheduler.cpp.pre_refactor
cp librtsim/scheduler/gpfp_tgf_scheduler.cpp librtsim/scheduler/gpfp_tgf_scheduler.cpp.pre_refactor
cp librtsim/scheduler/gpfp_btie_scheduler.cpp librtsim/scheduler/gpfp_btie_scheduler.cpp.pre_refactor
```

### 第2步：修改TIE/TGF的performTickScheduling
- 添加运行任务续期能量扣除逻辑（使用 `getCurrentExecutingTasks()`）
- 添加新任务初始能量扣除逻辑（遍历 `_counted_tasks_in_dispatch`）
- 能量不足时挂起任务

### 第3步：修改TIE/TGF的getTaskN
- 移除能量扣除逻辑（`_current_energy -= unit_energy`）
- 保留能量检查逻辑
- 保留任务标记逻辑（`_counted_tasks_in_dispatch.insert(task)`）
- TIE：能量不足时返回nullptr（停止级联）
- TGF：能量不足时贪婪搜索后续任务

### 第4步：修改BTIE的performTickScheduling
- 保留"全有或全无"批量调度逻辑
- 统一在 performTickScheduling 中扣除所有能量（运行中续期 + 新任务初始）
- 能量不足时挂起所有运行任务

### 第5步：修改MRTKernel::onEndDispatchMulti
- 移除 `startEnergyCheckForTask` 调用（TIE/TGF/BTIE）
- ⚠️ 不添加调度器特定的标记逻辑（保持 kernel 通用性）

### 第6步：删除能量检查事件相关代码
- 删除 `TIEEnergyCheckEvent` 类定义
- 删除 `TGFEnergyCheckEvent` 类定义
- 删除 `BTIEEnergyCheckEvent` 类定义
- 删除 `startEnergyCheckForTask` 方法
- 删除 `stopEnergyCheckForTask` 方法
- 删除 `_energy_check_events` 成员变量

### 第7步：更新头文件
- 移除能量检查事件相关声明
- 更新类文档注释

### 第8步：测试验证
- 运行12mJ测试，验证与TIE/TGF结果一致
- 运行15mJ测试，验证BTIE descheduled时间修正为22ms
- 对比重构前后的trace文件

---

## 7. 预期效果（修正版）

### 7.1 TIE行为保持不变
```
Tick 10ms:
  1. 收集太阳能: +0.0 mJ
  2. 扣除运行任务续期能量: -0.6mJ × 运行任务数
  3. getTaskN(0): 检查能量 → 标记task_1 (不扣除能量)
  4. dispatch后统一扣除task_1初始能量: -0.6mJ
  → 剩余能量正确

Tick 11ms:
  1. 收集太阳能: +0.0 mJ
  2. 扣除运行任务续期能量: -0.6mJ
  3. getTaskN(0): task_1运行中，直接返回
  → 继续执行
```

**关键区别**:
- 重构前：能量扣除分散在 `getTaskN` 和 `TIEEnergyCheckEvent`
- 重构后：所有能量扣除集中在 `performTickScheduling`

### 7.2 TGF行为保持不变
```
Tick 10ms:
  1. 收集太阳能: +0.0 mJ
  2. getTaskN(0): task_1能量不足 → 贪婪搜索 → 标记task_4
  3. dispatch后统一扣除task_4初始能量: -0.3mJ
  → TGF贪婪策略正常工作
```

### 7.3 BTIE行为修正
```
15mJ测试:
  重构前: descheduled at 21ms (错误，line 184 bug)
  重构后: descheduled at 22ms (正确，与TIE一致)

  原因: 移除了能量检查事件中的错误检查逻辑
```

### 7.4 时序对比

| 方面 | 重构前 | 重构后 |
|------|--------|--------|
| **能量扣除位置** | `getTaskN` + `EnergyCheckEvent` | `performTickScheduling` |
| **能量检查事件** | 每任务1个，异步触发 | 无（同步在tick边界） |
| **续期能量扣除** | `TIEEnergyCheckEvent::doit()` | `performTickScheduling` |
| **BTIE批量判断** | 在performTickScheduling | 在performTickScheduling（不变） |
| **descheduled时间** | BTIE 21ms, TIE 22ms | BTIE 22ms, TIE 22ms（一致）|

---

## 8. 设计优势

### 8.1 逻辑清晰
- ✅ 所有能量操作在一个地方完成
- ✅ 时序简单，没有异步事件干扰
- ✅ 易于理解和维护

### 8.2 符合设计逻辑
- ✅ 代码结构与您提供的调度逻辑完全一致
- ✅ 1 tick = 1 ms 作为基本单位
- ✅ 所有操作在tick边界同步完成

### 8.3 消除bug
- ✅ 没有事件优先级冲突
- ✅ 没有重复扣除逻辑
- ✅ 没有BTIE line 184的误判

### 8.4 性能优化
- ✅ 减少事件调度开销
- ✅ 减少异步事件数量
- ✅ 简化调度器状态管理

---

## 9. 风险评估

### 9.1 潜在风险
| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 重构引入新bug | 高 | 充分测试，保留原代码备份 |
| 修改MRTKernel影响其他调度器 | 中 | 使用dynamic_cast只针对TIE/TGF修改 |
| 删除能量检查事件影响其他依赖 | 低 | 检查调用者，确保无外部依赖 |

### 9.2 回滚计划
如果重构后发现问题：
1. 保留原代码备份（`.pre_refactor`文件）
2. 使用git恢复到重构前版本
3. 逐步回滚修改

---

## 10. 总结

本重构方案将TIE/TGF从"分散式能量管理"重构为"纯tick边界能量管理"，实现了以下目标：

1. ✅ **时间粒度统一**: 1 tick = 1 ms作为基本调度单位
2. ✅ **边界集中**: 所有能量操作在performTickScheduling中完成
3. ✅ **逻辑一致**: 代码结构与设计逻辑完全一致
4. ✅ **消除bug**: 修复BTIE的line 184误判问题
5. ✅ **简化维护**: 减少事件复杂度，提高代码可读性

这个重构是TIE/TGF调度器的核心优化，将为后续的功能扩展和维护工作奠定坚实基础。

---

---

## 附录：参考资料

- 原始设计逻辑：用户提供的TIE/TGF/BTIE调度算法描述
- 当前代码位置：`librtsim/scheduler/gpfp_tie_scheduler.cpp`
- 问题分析文档：`docs/btie_tie_desched_timing_difference.md`

---

## 附录B：设计文档审查发现的问题与修复

### 问题1: `_counted_tasks_in_dispatch` 填充时机理解错误

**原文档问题:**
```cpp
// 4. 扣除新调度的任务的初始能量
for (AbsRTTask *task : _counted_tasks_in_dispatch) {
    _current_energy -= unit_energy;
}
```
假设 `_counted_tasks_in_dispatch` 在 dispatch 后填充。

**实际情况:**
查看当前代码 ([gpfp_tie_scheduler.cpp:699-705](librtsim/scheduler/gpfp_tie_scheduler.cpp#L699-L705))，`_counted_tasks_in_dispatch` 是在 `getTaskN()` 内部填充的：

```cpp
// ⭐ 当前代码：getTaskN中扣除能量
if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
    _current_energy -= unit_energy;
    _counted_tasks_in_dispatch.insert(task);  // ⭐ 在这里填充
    return task;
}
```

**修正方案:**
如果要在 `performTickScheduling` 中扣除能量，`getTaskN` 应该只做决策，不扣除能量，但需要标记任务：

```cpp
// ⭐ 修正后的getTaskN：只做决策，标记任务
AbsRTTask *TIEScheduler::getTaskN(unsigned int n) {
    // ... 检查能量 ...
    if (_current_energy < unit_energy - EPSILON) {
        return nullptr;
    }

    // ⭐ 只标记任务，不扣除能量
    _counted_tasks_in_dispatch.insert(task);
    SCHEDULER_LOG_INFO("✅ 决定调度任务: " + getTaskName(task));
    return task;
}
```

然后在 `performTickScheduling` 的 dispatch 后统一扣除：

```cpp
// dispatch(); // getTaskN只标记，不扣除

// ⭐ 扣除所有已标记任务的能量
for (AbsRTTask *task : _counted_tasks_in_dispatch) {
    double unit_energy = calculateUnitEnergyForTask(task);
    _current_energy -= unit_energy;
}
```

### 问题2: `getCurrentExecutingTasks()` 返回类型理解错误

**原文档问题:**
```cpp
const auto& running_tasks = _kernel->getCurrentExecutingTasks();
for (const auto& map_pair : running_tasks) {
    AbsRTTask *task = map_pair.second;
```

**实际情况:**
`getCurrentExecutingTasks()` 返回 `const std::map<CPU *, AbsRTTask *>&` (mrtkernel.hpp:526-528)，不是任务列表，而是 CPU 到任务的映射。

**修正:**
```cpp
const auto& running_tasks = _kernel->getCurrentExecutingTasks();
for (const auto& [cpu, task] : running_tasks) {
    if (!task || !task->isExecuting()) continue;
    // ...
}
```

### 问题3: MRTKernel::onEndDispatchMulti 修改建议不当

**原文档问题:**
建议在 `onEndDispatchMulti` 中标记任务：
```cpp
TIEScheduler *tie_sched = dynamic_cast<TIEScheduler*>(_sched);
if (tie_sched) {
    tie_sched->_counted_tasks_in_dispatch.insert(st);
}
```

**实际问题:**
1. `onEndDispatchMulti` 是 MRTKernel 的通用方法，修改它会影响其他调度器
2. `_counted_tasks_in_dispatch` 应该在 `getTaskN` 中填充，而不是在 kernel 中

**修正方案:**
不需要修改 MRTKernel，保持 `getTaskN` 负责标记任务：

```cpp
// ❌ 删除：MRTKernel::onEndDispatchMulti 中的修改
// ✅ 保持：getTaskN 中标记任务
```

### 问题4: BTIE 批量调度的特殊处理未考虑

**原文档问题:**
文档主要关注 TIE/TGF，没有详细说明 BTIE 如何重构。

**实际情况:**
BTIE 有完全不同的批量调度逻辑：
- `total_energy_needed = running_tasks_renewal_energy + new_tasks_energy`
- `_current_energy > total_energy_needed - EPSILON` 批量检查
- `_current_energy -= total_energy_needed` 一次性扣除全部能量
- 使用 `_current_batch_tasks` 而不是 `_counted_tasks_in_dispatch`

**修正方案:**
BTIE 应该有单独的重构方案：

```cpp
void BTIEScheduler::performTickScheduling() {
    // 1. 收集太阳能
    _current_energy += collectSolarEnergy(current_time);

    // 2. 计算所有任务的总能量需求
    double running_tasks_renewal_energy = 0.0;
    for (const auto& [cpu, task] : _kernel->getCurrentExecutingTasks()) {
        if (task && task->isExecuting()) {
            running_tasks_renewal_energy += calculateUnitEnergyForTask(task);
        }
    }

    double new_tasks_energy = 0.0;
    for (auto* task : new_tasks_to_schedule) {
        new_tasks_energy += calculateUnitEnergyForTask(task);
    }

    double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;

    // 3. BTIE "全有或全无"批量检查
    if (_current_energy > total_energy_needed - EPSILON) {
        // ⭐ 批量扣除所有能量
        _current_energy -= total_energy_needed;
        // 调度所有任务...
    } else {
        // ⭐ "全无"：挂起所有运行任务
        _current_energy = 0.0;
        for (const auto& [cpu, task] : _kernel->getCurrentExecutingTasks()) {
            if (task && task->isExecuting()) {
                _kernel->suspend(task);
            }
        }
    }
}
```

### 问题5: 能量检查事件删除的时机问题

**原文档问题:**
直接删除 `TIEEnergyCheckEvent`，但未说明如何处理运行中任务的续期能量扣除。

**修正方案:**
删除能量检查事件后，续期能量扣除必须在 `performTickScheduling` 中完成：

```cpp
void TIEScheduler::performTickScheduling() {
    // 1. 收集太阳能
    _current_energy += collectSolarEnergy(current_time);

    // 2. ⭐ 扣除运行中任务的续期能量（替代 TIEEnergyCheckEvent）
    std::vector<AbsRTTask *> tasks_to_suspend;
    for (const auto& [cpu, task] : _kernel->getCurrentExecutingTasks()) {
        if (!task || !task->isExecuting()) continue;

        double unit_energy = calculateUnitEnergyForTask(task);
        if (_current_energy < unit_energy - EPSILON) {
            tasks_to_suspend.push_back(task);
        } else {
            _current_energy -= unit_energy;
            _stats.total_energy_consumed += unit_energy;
        }
    }

    // 挂起能量不足的任务
    for (AbsRTTask *task : tasks_to_suspend) {
        _kernel->suspend(task);
    }

    // 3. 检查抢占...
    // 4. 调度新任务...
    // 5. 扣除新任务初始能量...
}
```

---

## 总结修正后的设计

### 核心修改点

1. **getTaskN**: 只做决策，标记任务，不扣除能量
2. **performTickScheduling**:
   - 收集太阳能
   - 扣除运行中任务续期能量
   - 调度新任务（getTaskN只标记）
   - 扣除新任务初始能量
3. **删除 TIEEnergyCheckEvent**: 续期能量扣除由 performTickScheduling 接管
4. **不修改 MRTKernel**: 保持 kernel 通用性，所有调度器特定逻辑在 scheduler 中
5. **BTIE 单独处理**: 保留其批量调度的"全有或全无"逻辑
