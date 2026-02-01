# TIE/TGF 纯Tick边界调度修复文档

## 📋 文档信息

- **版本**: v1.1
- **日期**: 2026-02-01
- **目标**: 将TIE/TGF重构为严格的1ms tick边界调度
- **原则**: 所有调度、能量、抢占操作必须在tick边界进行
- **更新日志**:
  - v1.1: 添加Bug #5（能量账户初始化缺失），完善边界情况检查
  - v1.0: 初始版本

---

## 🎯 设计意图

### TIE (Tick-based Instant Energy-aware) - 严格优先级阻��

**核心特征**：
1. **调度粒度**: 严格以1ms tick为基本单位
2. **调度时机**: 每个tick开始并完成能量收集后
3. **调度策略**:
   - 严格遵循优先级顺序（从高到低）
   - 锁定全局队列中优先级最高的任务
   - 判断当前电量是否满足该任务运行1ms
   - 如果满足，分发到空闲核心，扣除能量配额
   - 继续对次高优先级任务做同样判断
4. **关键约束**: **队头阻塞机制**
   - 一旦遇到任意高优先级任务因电量不足无法运行
   - **立即停止所有调度尝试**
   - 即使后续有低功耗任务，也不调度
   - 强制剩余核心保持空闲

**多核行为**:
```
Tick边界场景：
- 就绪队列: [Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
- 空闲核心: CPU0, CPU1, CPU2
- 当前能量: 只够运行Task2和Task3

TIE调度过程:
1. 检查Task1: 能量不足 → 立即停止！
2. CPU0, CPU1, CPU2全部保持空闲

结果: 全局队头阻塞，即使能量足够Task2/Task3也不调度
```

### TGF (Tick-based Greedy First) - 贪婪填充最大化并行

**核心特征**：
1. **调度粒度**: 严格以1ms tick为基本单位
2. **调度时机**: 每个tick开始并完成能量收集后
3. **调度策略**:
   - 全队列贪婪扫描（从高优先级向低优先级）
   - 寻找能耗需求小于当前剩余电量的"候选者"
   - 找到合适任务，立即分发到空闲核心，扣除能量
   - **继续扫描剩余队列**填充下一个核心
4. **关键约束**: **跳过大任务机制**
   - 遇到能量不足的任务，跳过继续寻找
   - 直到所有核心填满或遍历完整个队列

**多核行为**:
```
Tick边界场景：
- 就绪队列: [Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
- 空闲核心: CPU0, CPU1, CPU2
- 当前能量: 只够运行Task2和Task3

TGF调度过程:
1. 检查Task1: 能量不足 → 跳过
2. 检查Task2: 能量足够 → 调度到CPU0，扣除能量
3. 检查Task3: 能量足够 → 调度到CPU1，扣除能量
4. 继续扫描: 无更多可运行任务

结果: CPU2空闲，但CPU0/CPU1得到充分利用
```

---

## 🔍 问题分析

### 问题1: 非tick边界的调度决策 ❌

**位置**:
- `gpfp_tie_scheduler.cpp:1589-1600` - `onTaskEnd()`中调用`dispatch()`
- `gpfp_tgf_scheduler.cpp:1500-1513` - `onTaskEnd()`中调用`dispatch()`

**当前代码**:
```cpp
void TIEScheduler::onTaskEnd(AbsRTTask *task) {
    // ... 清理逻辑 ...

    // ❌ 错误：在非tick边界调度
    if (!_ready_queue.empty() && _kernel) {
        SCHEDULER_LOG_INFO("🔄 [TIE] 任务结束，触发立即调度");
        _kernel->dispatch();  // 违反了1ms tick边界原则
    }
}
```

**问题**:
1. ❌ 任务可能在tick=5.3ms结束，立即触发`dispatch()`
2. ❌ 调度决策发生在非tick边界
3. ❌ 能量扣除、抢占检查可能在tick之间进行
4. ❌ 违反了"Tick-based"的核心语义

**影响**:
- 破坏了tick边界的调度语义
- 能量管理时序混乱
- 与"1ms为基本单位"的设计目标冲突

---

### 问题2: 非tick边界的抢占检查 ❌

**位置**:
- `gpfp_tie_scheduler.cpp:925-936` - `onTaskArrival()`中调用`checkAndPreempt()`
- `gpfp_tgf_scheduler.cpp:926-937` - `onTaskArrival()`中调用`checkAndPreempt()`

**当前代码**:
```cpp
void TIEScheduler::onTaskArrival(AbsRTTask *task) {
    if (!task) return;

    SCHEDULER_LOG_INFO(std::string("📍 [TIE] 任务到达: ") + getTaskName(task));

    if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
        addToReadyQueue(task);
        checkAndPreempt();  // ❌ 错误：在非tick边界抢占
    }
}
```

**问题**:
1. ❌ 任务可能在tick=5.7ms到达，立即触发抢占检查
2. ❌ 抢占决策发生在非tick边界
3. ❌ 任务挂起可能在tick之间进行
4. ❌ 违反了"Tick-based抢占"的设计目标

**影响**:
- 抢占时机不可预测
- 能量检查时机不一致
- 破坏了tick边界的调度确定性

---

### 问题3: TGF非tick边界的能量扣除 ❌

**位置**:
- `gpfp_tgf_scheduler.cpp:688-747` - `getTaskN()`中立即扣除能量

**当前代码**:
```cpp
AbsRTTask *TGFScheduler::getTaskN(size_t n) {
    // ... 前面逻辑 ...

    if (next_available >= next_unit_energy - EPSILON) {
        // ❌ 错误：立即扣除能量，但getTaskN()可能在非tick边界被调用
        _current_energy -= next_unit_energy;
        _counted_tasks_in_dispatch.insert(next_task);
        return next_task;
    }
}
```

**问题**:
1. ❌ `onTaskEnd()`在非tick边界调用`dispatch()` → `getTaskN()`
2. ❌ 能量在非tick边界被扣除
3. ❌ 能量扣除可能发生在能量收集之前
4. ❌ 违反了"1ms来能量收集、调度、扣除能量"的原则

**影响**:
- 能量透支（扣除未来能量）
- 能量记账时序混乱
- 可能导致能量耗尽判断错误

**示例场景**:
```
时间线：
Tick=0ms: 收集能量+10mJ → 当前100mJ
Tick=0.5ms: 任务结束 → dispatch() → 扣除能量-50mJ → 当前50mJ
Tick=1ms: 收集能量+10mJ → 当前60mJ（但本应该在100mJ基础上扣除）

问题：能量扣除发生在能量收集之前，导致时序混乱
```

---

### 问题4: 续期能量扣除时间不匹配 ⚠️

**位置**:
- `gpfp_tie_scheduler.cpp:499-525` - `performTickScheduling()`中的续期能量扣除
- `gpfp_tgf_scheduler.cpp:464-497` - `performTickScheduling()`中的续期能量扣除

**当前代码**:
```cpp
void TIEScheduler::performTickScheduling() {
    // ... 收集太阳能 ...

    for (const auto& [cpu, task] : running_tasks_map) {
        if (!task || !task->isExecuting()) continue;

        double unit_energy = calculateUnitEnergyForTask(task);

        // ⚠️ 问题：每次都扣1ms能量，但任务实际执行时间可能不足1ms
        _current_energy -= unit_energy;  // ← 不精确！
    }
}
```

**问题**:
1. ⚠️ 每个tick固定扣除1ms能量
2. ⚠️ 未考虑任务实际开始/结束时间
3. ⚠️ 可能导致能量扣除不准确

**示例场景**:
```
时间线：
Tick=0.3ms: TaskA开始运行
Tick=1ms:   扣除1ms能量（实际执行了0.7ms）→ 多扣了0.3ms
Tick=1.5ms: TaskA结束
Tick=2ms:   TaskA已结束，但上次多扣的0.3ms能量无法退还

问题：能量扣除与实际执行时间不匹配
```

**影响**:
- 能量记账不精确
- 违反了"绝对精准的能量记账"目标
- 可能累积误差导致能量不足误判

---

## ✅ 修复方案

### 修复原则

1. **严格的tick边界**: 所有调度、能量、抢占操作必须在tick边界
2. **精确的能量记账**: 续期能量扣除必须考虑实际执行时间
3. **保持算法语义**: 修复后必须符合TIE/TGF的设计意图

### 修复1: 删除`onTaskEnd()`中的立即调度

**文件**: `gpfp_tie_scheduler.cpp`, `gpfp_tgf_scheduler.cpp`

**修复前**:
```cpp
void TIEScheduler::onTaskEnd(AbsRTTask *task) {
    // ... 清理逻辑 ...

    if (!_ready_queue.empty() && _kernel) {
        SCHEDULER_LOG_INFO("🔄 [TIE] 任务结束，触发立即调度");
        _kernel->dispatch();  // ❌ 必须删除
    }
}
```

**修复后**:
```cpp
void TIEScheduler::onTaskEnd(AbsRTTask *task) {
    if (!task) {
        return;
    }

    SCHEDULER_LOG_INFO(std::string("✅ [TIE] 任务结束: ") + getTaskName(task));

    // 从就绪队列移除
    removeFromReadyQueue(task);

    // 从运行任务映射中移除
    for (auto &pair : _running_tasks) {
        if (pair.second == task) {
            pair.second = nullptr;
            break;
        }
    }

    // 打印能量消耗统计
    auto it = _energy_accounts.find(task);
    if (it != _energy_accounts.end()) {
        SCHEDULER_LOG_INFO(std::string("📊 [TIE] 任务能量消耗: ") +
                          getTaskName(task) +
                          " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
        _energy_accounts.erase(it);
    }

    _stats.total_task_completions++;

    SCHEDULER_LOG_INFO(std::string("📊 [TIE] 当前能量: ") + std::to_string(_current_energy) + "J");

    // ✅ 修复：删除立即调度，严格遵守tick边界调度原则
    //
    // 原因：
    // 1. 调度粒度是1ms，所有调度决策必须在tick边界进行
    // 2. 任务可能在任意时刻结束，但调度决策必须等待下一个tick边界
    // 3. 下一个tick的performTickScheduling()会自动检测到_ready_queue不为空并调度
    //
    // 影响：
    // - 事件到达（任务结束）与调度决策之间最多有1ms延迟
    // - 但这符合"Tick-based"的设计语义，保证调度确定性
    // - 避免了非tick边界的能量扣除、抢占检查等问题
}
```

**TGF版本**: 相同修复

---

### 修复2: 删除`onTaskArrival()`中的立即抢占

**文件**: `gpfp_tie_scheduler.cpp`, `gpfp_tgf_scheduler.cpp`

**修复前**:
```cpp
void TIEScheduler::onTaskArrival(AbsRTTask *task) {
    if (!task) return;

    SCHEDULER_LOG_INFO(std::string("📍 [TIE] 任务到达: ") + getTaskName(task));

    if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
        addToReadyQueue(task);
        checkAndPreempt();  // ❌ 必须删除
    }
}
```

**修复后**:
```cpp
void TIEScheduler::onTaskArrival(AbsRTTask *task) {
    if (!task) {
        return;
    }

    SCHEDULER_LOG_INFO(std::string("📍 [TIE] 任务到达: ") + getTaskName(task));

    if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
        addToReadyQueue(task);

        // ✅ 修复：删除立即抢占检查，严格遵守tick边界抢占原则
        //
        // 原因：
        // 1. 抢占检查涉及能量判断、任务挂起等操作，必须在tick边界进行
        // 2. 任务可能在任意时刻到达，但抢占决策必须等待下一个tick边界
        // 3. 下一个tick的performTickScheduling()会自动调用checkAndPreempt()
        //
        // 影响：
        // - 新任务到达与抢占检查之间最多有1ms延迟
        // - 但这符合"Tick-based"的设计语义，保证抢占确定性
        // - 避免了非tick边界的能量检查、任务挂起等问题
    }
}
```

**TGF版本**: 相同修复

---

### 修复3: TGF能量扣除时机重构

**文件**: `gpfp_tgf_scheduler.cpp`, `gpfp_tgf_scheduler.hpp`

#### 3.1 头文件修改

**修复前**:
```cpp
class TGFScheduler : public Scheduler {
    // ... 其他字段 ...
};
```

**修复后**:
```cpp
class TGFScheduler : public Scheduler {
private:
    // ✅ 新增：标记是否在tick边界调度中
    // 用于防御性编程，确保能量扣除只在tick边界进行
    bool _in_tick_boundary_dispatch = false;

public:
    // ✅ 新增：获取tick边界调度标记（用于测试）
    bool isInTickBoundaryDispatch() const { return _in_tick_boundary_dispatch; }

    // ... 其他成员 ...
};
```

#### 3.2 getTaskN()修改

**修复前**:
```cpp
AbsRTTask *TGFScheduler::getTaskN(size_t n) {
    // ... 前面逻辑 ...

    if (next_available >= next_unit_energy - EPSILON) {
        // ❌ 立即扣除能量
        _current_energy -= next_unit_energy;
        _counted_tasks_in_dispatch.insert(next_task);
        return next_task;
    }
}
```

**修复后**:
```cpp
AbsRTTask *TGFScheduler::getTaskN(size_t n) {
    // ... 前面逻辑（贪婪扫描、优先级检查等） ...

    if (next_available >= next_unit_energy - EPSILON) {
        // ✅ 修复：能量扣除延迟到tick边界统一进行
        //
        // 原因：
        // 1. getTaskN()可能在非tick边界被调用（虽然我们已经删除了onTaskEnd中的dispatch）
        // 2. 防御性编程：即使未来有代码错误地调用dispatch，也能保证能量扣除时机正确
        // 3. 与TIE保持一致：先做调度决策，tick边界统一扣除能量
        //
        // 实现：
        // - 不立即扣除能量
        // - 只标记任务到_counted_tasks_in_dispatch
        // - performTickScheduling()中统一扣除能量

        // ✅ 只在tick边界调度时才标记任务
        if (_in_tick_boundary_dispatch) {
            _counted_tasks_in_dispatch.insert(next_task);

            SCHEDULER_LOG_INFO("✅ [TGF] 决定调度任务（已标记，暂不扣能量）: " +
                               getTaskName(next_task) +
                               " 能量=" + std::to_string(next_unit_energy * 1000) + " mJ");
        } else {
            // ⚠️ 防御性编程：非tick边界调度（不应该发生）
            SCHEDULER_LOG_WARNING("⚠️ [TGF] getTaskN()在非tick边界被调用，拒绝调度");
            return nullptr;
        }

        return next_task;
    }

    // 能量不足，跳过（贪心策略）
    SCHEDULER_LOG_INFO("⏭️ [TGF] 任务能量不足，跳过（贪心策略）: " +
                       getTaskName(next_task) +
                       " 需要=" + std::to_string(next_unit_energy * 1000) + " mJ" +
                       " 剩余=" + std::to_string(next_available * 1000) + " mJ");

    return nullptr;
}
```

#### 3.3 performTickScheduling()修改

**修复前**:
```cpp
void TGFScheduler::performTickScheduling() {
    // ... 第1-3步 ...

    // 第4步：调度新任务
    if (_kernel) {
        _kernel->dispatch();  // getTaskN()中会扣除能量
    }
}
```

**修复后**:
```cpp
void TGFScheduler::performTickScheduling() {
    SCHEDULER_LOG_INFO(std::string("🔄 [TGF] ===== Tick ") +
                       std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms =====");
    SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

    // Bug修复：能量耗尽时跳过调度
    if (_energy_depleted && _current_energy < 0.000001) {
        SCHEDULER_LOG_INFO(std::string("💀 [TGF] 能量已耗尽，跳过Tick调度"));
        return;
    }

    _stats.total_tick_count++;

    // V42修复：清空当前tick新调度任务标记
    _newly_dispatched_this_tick.clear();

    Tick current_time = SIMUL.getTime();

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
    if (!_kernel) {
        _kernel = getKernel();
    }

    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> tasks_to_suspend;

        SCHEDULER_LOG_INFO("🏃 检查运行任务: " +
                           std::to_string(running_tasks_map.size()) + " 个");

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isActive()) continue;

            // V42修复：跳过当前tick中新调度的任务
            if (_newly_dispatched_this_tick.find(task) != _newly_dispatched_this_tick.end()) {
                SCHEDULER_LOG_DEBUG(std::string("⏭️ [TGF] 跳过新任务的续期扣除: ") + getTaskName(task));
                continue;
            }

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

        // ✅ 修复：清空调度标记
        _counted_tasks_in_dispatch.clear();

        // ✅ 修复：标记进入tick边界调度
        _in_tick_boundary_dispatch = true;

        // ⭐ TGF关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
        int dispatch_attempts = 0;
        const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

        while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
            // 检查是否所有CPU都已填满
            bool all_cpus_full = true;
            for (auto &map_pair : _running_tasks) {
                if (map_pair.second == nullptr) {
                    all_cpus_full = false;
                    break;
                }
            }

            if (all_cpus_full) {
                SCHEDULER_LOG_DEBUG("✅ [TGF] 所有CPU已填满，停止调度");
                break;
            }

            // 记录调度前的任务数
            size_t tasks_before = _ready_queue.size() + _running_tasks.size();

            // 调用dispatch尝试调度更多任务
            _kernel->dispatch();
            dispatch_attempts++;

            // 记录调度后的任务数
            size_t tasks_after = _ready_queue.size() + _running_tasks.size();

            // 如果没有任务被调度（状态没变化），停止调度
            if (tasks_before == tasks_after) {
                SCHEDULER_LOG_DEBUG("⏹️ [TGF] 无更多任务可调度，停止dispatch循环");
                break;
            }

            SCHEDULER_LOG_DEBUG(std::string("🔄 [TGF] dispatch循环 #") + std::to_string(dispatch_attempts) +
                               " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                               " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
        }

        // ✅ 修复：标记退出tick边界调度
        _in_tick_boundary_dispatch = false;

        if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] dispatch循环达到最大迭代次数，可能存在bug");
        }

        // ✅ 修复：统一扣除所有已调度任务的能量
        double total_energy_deducted = 0.0;
        for (AbsRTTask *task : _counted_tasks_in_dispatch) {
            double unit_energy = calculateUnitEnergyForTask(task);
            _current_energy -= unit_energy;
            _stats.total_energy_consumed += unit_energy;
            total_energy_deducted += unit_energy;

            SCHEDULER_LOG_INFO("✅ 新任务扣除初始能量: " +
                               getTaskName(task) +
                               " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ");

            // 标记为新调度的任务（避免续期重复扣除）
            _newly_dispatched_this_tick.insert(task);
        }

        SCHEDULER_LOG_INFO("📊 调度完成: 新任务=" +
                           std::to_string(_counted_tasks_in_dispatch.size()) +
                           " 扣除能量=" + std::to_string(total_energy_deducted * 1000) + " mJ " +
                           std::to_string(energy_before_scheduling * 1000) + " → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    SCHEDULER_LOG_INFO("✅ Tick " +
                       std::to_string(static_cast<int64_t>(current_time)) +
                       "ms 完成, 剩余能量: " +
                       std::to_string(_current_energy * 1000) + " mJ");
}
```

---

### 修复4: 续期能量精确扣除

**文件**: `gpfp_tie_scheduler.cpp`, `gpfp_tgf_scheduler.cpp`
**文件**: `gpfp_tie_scheduler.hpp`, `gpfp_tgf_scheduler.hpp`

#### 4.1 头文件修改

**修复前**:
```cpp
struct TaskEnergyAccount {
    double total_consumed = 0.0;
    MetaSim::Tick start_time = 0;
    MetaSim::Tick last_unit_time = 0;
};
```

**修复后**:
```cpp
struct TaskEnergyAccount {
    double total_consumed = 0.0;           // 累计消耗能量
    MetaSim::Tick start_time = 0;           // 任务开始时间
    MetaSim::Tick last_deduct_time = 0;     // 上次扣除能量的时间（新增）
    MetaSim::Tick last_unit_time = 0;       // 上次单位时间
};
```

#### 4.2 performTickScheduling()修改

**修复前**:
```cpp
void TIEScheduler::performTickScheduling() {
    // ... 收集太阳能 ...

    for (const auto& [cpu, task] : running_tasks_map) {
        if (!task || !task->isExecuting()) continue;

        double unit_energy = calculateUnitEnergyForTask(task);

        // ❌ 固定扣除1ms能量
        _current_energy -= unit_energy;
    }
}
```

**修复后**:
```cpp
void TIEScheduler::performTickScheduling() {
    SCHEDULER_LOG_INFO(std::string("🔄 [TIE] ===== Tick ") +
                       std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms =====");
    SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

    // Bug修复：能量耗尽时跳过调度
    if (_energy_depleted && _current_energy < 0.000001) {
        SCHEDULER_LOG_INFO(std::string("💀 [TIE] 能量已耗尽，跳过Tick调度"));
        return;
    }

    _stats.total_tick_count++;

    Tick current_time = SIMUL.getTime();

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

    // ========== 第2步：精确扣除运行任务的续期能量 ==========
    if (!_kernel) {
        _kernel = getKernel();
    }

    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> tasks_to_suspend;

        SCHEDULER_LOG_INFO("🏃 检查运行任务: " +
                           std::to_string(running_tasks_map.size()) + " 个");

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;

            double unit_energy = calculateUnitEnergyForTask(task);  // 1ms能量

            // ✅ 修复：精确计算实际执行时间
            auto& account = _energy_accounts[task];
            Tick actual_exec_time = current_time - account.last_deduct_time;

            // 根据实际执行时间计算实际能量消耗
            double actual_energy = unit_energy * static_cast<double>(actual_exec_time) / 1000.0;

            const double EPSILON = 1e-9;

            SCHEDULER_LOG_DEBUG("🔍 [TIE] 任务续期检查: " +
                               getTaskName(task) +
                               " 实际执行时间=" + std::to_string(actual_exec_time) + " ticks" +
                               " 实际能量=" + std::to_string(actual_energy * 1000) + " mJ" +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");

            if (_current_energy < actual_energy - EPSILON) {
                // 能量不足，加入挂起列表
                tasks_to_suspend.push_back(task);
                SCHEDULER_LOG_WARNING("⚠️ 续期能量不足，将挂起: " +
                                     getTaskName(task) +
                                     " 需要=" + std::to_string(actual_energy * 1000) + " mJ" +
                                     " 实际执行=" + std::to_string(actual_exec_time) + " ticks" +
                                     " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
            } else {
                // ✅ 扣除精确的续期能量
                double old_energy = _current_energy;
                _current_energy -= actual_energy;
                _stats.total_energy_consumed += actual_energy;
                account.total_consumed += actual_energy;
                account.last_deduct_time = current_time;  // ✅ 更新上次扣除时间

                SCHEDULER_LOG_INFO("⚡ 扣除续期能量: " +
                                   getTaskName(task) +
                                   " 执行时间=" + std::to_string(actual_exec_time) + " ticks" +
                                   " -" + std::to_string(actual_energy * 1000) + " mJ " +
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

        // 关键：清空本次tick的调度记录
        _counted_tasks_in_dispatch.clear();
        _dispatching_tasks_total_energy = 0.0;

        // 调度任务（getTaskN只做决策和标记，不扣除能量）
        _kernel->dispatch();

        // 关键：在dispatch后，统一扣除所有已标记任务的能量
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

#### 4.3 dispatchTask()修改

**修复前**:
```cpp
void TIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
    // ... 分发逻辑 ...
}
```

**修复后**:
```cpp
void TIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
    if (!task || !cpu) {
        return;
    }

    SCHEDULER_LOG_INFO(std::string("📤 [TIE] 分发任务: ") + getTaskName(task) +
                      " → CPU" + std::to_string(cpu->getIndex()));

    // ✅ 修复：初始化能量账户
    if (_energy_accounts.find(task) == _energy_accounts.end()) {
        _energy_accounts[task].start_time = SIMUL.getTime();
        _energy_accounts[task].last_deduct_time = SIMUL.getTime();  // ✅ 初始化扣除时间
        _energy_accounts[task].last_unit_time = SIMUL.getTime();
        SCHEDULER_LOG_DEBUG("✅ [TIE] 初始化任务能量账户: " + getTaskName(task));
    }

    // ... 其他分发逻辑 ...
}
```

---

### 修复5: dispatchTask()中能量账户初始化 🔴

**位置**:
- `gpfp_tie_scheduler.cpp:1460-1470` - `dispatchTask()`
- `gpfp_tgf_scheduler.cpp:1370-1380` - `dispatchTask()`

**当前代码**:
```cpp
void TIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
    if (!task || !cpu) {
        return;
    }

    SCHEDULER_LOG_INFO(std::string("📤 [TIE] 调度任务: ") + getTaskName(task) + " 到CPU");

    removeFromReadyQueue(task);
    _running_tasks[cpu] = task;
    // ❌ 缺失：没有初始化energy_accounts
}
```

**问题**:
1. ❌ 任务被调度时，`energy_accounts`没有被初始化
2. ❌ `performTickScheduling()`中访问`_energy_accounts[task]`时，会创建默认记录（last_deduct_time=0）
3. ❌ 导致续期能量扣除计算错误：
   ```cpp
   Tick actual_exec_time = current_time - account.last_deduct_time;
   // 如果last_deduct_time=0（未初始化），则计算错误！
   ```

**示例场景**:
```
时间线：
Tick=0ms: 任务A被调度，但energy_accounts未初始化
Tick=1ms: performTickScheduling()访问energy_accounts[A]
          → 创建默认记录，last_deduct_time=0
          → actual_exec_time = 1ms - 0ms = 1ms ✅ (碰巧正确)

Tick=2ms: performTickScheduling()
          → actual_exec_time = 2ms - 0ms = 2ms ❌ (错误！实际是1ms)
```

**修复方案**:
```cpp
void TIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
    if (!task || !cpu) {
        return;
    }

    SCHEDULER_LOG_INFO(std::string("📤 [TIE] 调度任务: ") + getTaskName(task) + " 到CPU");

    // ✅ 修复：初始化能量账户
    if (_energy_accounts.find(task) == _energy_accounts.end()) {
        _energy_accounts[task].start_time = SIMUL.getTime();
        _energy_accounts[task].last_deduct_time = SIMUL.getTime();
        _energy_accounts[task].last_unit_time = SIMUL.getTime();
        SCHEDULER_LOG_DEBUG("✅ [TIE] 初始化任务能量账户: " + getTaskName(task));
    } else {
        // ✅ 任务重新调度：更新last_deduct_time（避免重复扣除）
        _energy_accounts[task].last_deduct_time = SIMUL.getTime();
        SCHEDULER_LOG_DEBUG("🔄 [TIE] 重新调度任务，更新last_deduct_time: " + getTaskName(task));
    }

    removeFromReadyQueue(task);
    _running_tasks[cpu] = task;
}
```

**TGF版本**: 相同修复

---

### 修复6: TGF调度逻辑根本性错误 🔴🔴🔴

**位置**:
- `gpfp_tgf_scheduler.cpp:647-780` - `getTaskN()`核心逻辑

**当前代码问题**:
```cpp
for (size_t i = 0; i < _ready_queue.size(); ++i) {
    AbsRTTask *task = _ready_queue[i];

    if (ready_index == n) {
        // 找到第n个任务，检查能量
        if (available_energy < unit_energy - EPSILON) {
            // 贪心搜索后续任务 ✅
            for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
                // 搜索...
            }
            return nullptr;
        }
        return task;
    } else {
        // ❌ 问题：只增加索引，不跳过能量不足的任务
        ready_index++;
    }
}
```

**问题描述**:
TGF存在**三个根本性错误**：

#### **错误1: 概念性错误** 🔴🔴🔴
- **问题**：`getTaskN(n)`的语义是"返回第n个未调度任务"，但TGF需要的是"返回任何一个能量足够的任务"
- **本质**：这是**语义冲突**！位置语义 vs 能量语义
- **后果**：无法实现真正的贪心策略

#### **错误2: 逻辑错误** 🔴🔴
- **问题**：在`ready_index != n`分支中，只增加索引，不检查/跳过能量不足的任务
- **代码**：`ready_index++;`（没有能量检查）
- **后果**：高优先级但能量不足的任务不会被跳过

#### **错误3: 优先级违反错误** 🔴
- **问题**：可能返回高优先级但能量不足的任务，而不是低优先级但能量足够的任务
- **后果**：违反了TGF的贪心填充原则

**示例场景**:
```
就绪队列: [Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
当前能量: 只够Task2和Task3

期望的TGF行为（贪心填充）：
1. getTaskN(0) → 跳过Task1 → 返回Task2 ✅
2. getTaskN(1) → 跳过Task1,Task2 → 返回Task3 ✅
3. getTaskN(2) → 跳过Task1,Task2,Task3 → 返回nullptr ✅

实际发生的TGF行为（依赖Task2是否已调度）：
场景A - Task2未调度：
1. getTaskN(0) → ready_index=0 → Task1能量不足 → 贪心搜索 → 返回Task2 ✅
2. getTaskN(1) → ready_index=1 → Task2已调度 → 返回Task3 ✅
3. getTaskN(2) → ready_index=2 → Task3已调度 → 返回nullptr ✅

场景B - Task2已调度：
1. getTaskN(1) → ready_index=0 → Task1能量不足 → 贪心搜索 → Task2已调度 → nullptr ❌
   问题：即使Task3可调度，也无法找到！

根本原因：只在ready_index == n时才贪心搜索，不是全队列扫描
```

**修复方案**:
```cpp
AbsRTTask *TGFScheduler::getTaskN(size_t n) {
    // ✅ 修复：真正的全队列贪婪扫描
    // 不再依赖"第n个任务"的语义，改为"返回第一个能量足够的任务"

    if (_ready_queue.empty()) {
        return nullptr;
    }

    const double EPSILON = 1e-9;
    std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - 全队列贪婪扫描开始" << std::endl;

    // ✅ 遍历整个就绪队列（从高优先级到低优先级）
    for (size_t i = 0; i < _ready_queue.size(); ++i) {
        AbsRTTask *task = _ready_queue[i];

        if (!task) {
            continue;
        }

        // ✅ 跳过已调度的任务
        if (_counted_tasks_in_dispatch.find(task) != _counted_tasks_in_dispatch.end()) {
            std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - 跳过已调度任务: "
                      << getTaskName(task) << std::endl;
            continue;
        }

        // ✅ 跳过运行中的任务
        bool is_running = false;
        if (_kernel) {
            CPU *proc = _kernel->getProcessor(task);
            is_running = (proc != nullptr);
        }

        if (is_running) {
            std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - 跳过运行中任务: "
                      << getTaskName(task) << std::endl;
            continue;
        }

        // ✅ 检查能量
        double unit_energy = calculateUnitEnergyForTask(task);
        double available_energy = _current_energy - _dispatching_tasks_total_energy;

        std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - 检查任务: "
                  << getTaskName(task)
                  << " 需要=" << (unit_energy * 1000) << " mJ"
                  << " 剩余=" << (available_energy * 1000) << " mJ" << std::endl;

        if (available_energy >= unit_energy - EPSILON) {
            // ✅ 找到第一个能量足够的任务！

            // ✅ 只在tick边界扣除能量（修复Bug #3）
            if (_in_tick_boundary_dispatch) {
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;

                std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - 扣除能量: "
                          << getTaskName(task)
                          << " -" << (unit_energy * 1000) << " mJ" << std::endl;
            }

            _counted_tasks_in_dispatch.insert(task);
            _newly_dispatched_this_tick.insert(task);

            SCHEDULER_LOG_INFO(std::string("✅ [TGF] 贪心策略：调度任务") +
                              " 任务=" + getTaskName(task) +
                              " 优先级=" + std::to_string(i) +
                              " 能量=" + std::to_string(unit_energy * 1000) + " mJ" +
                              " 剩余=" + std::to_string(available_energy * 1000) + " mJ");

            return task;
        }

        // ⭐ 能量不足：跳过，继续搜索（贪心策略）
        SCHEDULER_LOG_INFO(std::string("⏭️ [TGF] 能量不足，跳过（贪心策略）") +
                          " 任务=" + getTaskName(task) +
                          " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                          " 剩余=" + std::to_string(available_energy * 1000) + " mJ");
    }

    // 遍历完整个队列，没有找到能量足够的任务
    SCHEDULER_LOG_INFO(std::string("⚠️ [TGF] 全队列扫描完成，无能量足够的任务"));
    return nullptr;
}
```

**修复后的行为**:
```
就绪队列: [Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
当前能量: 只够Task2和Task3

getTaskN(0)调用：
→ 遍历Task1: 能量不足 → 跳过
→ 遍历Task2: 能量足够 ✅ → 返回Task2

getTaskN(1)调用：
→ 遍历Task1: 能量不足 → 跳过
→ 遍历Task2: 已调度 → 跳过
→ 遍历Task3: 能量足够 ✅ → 返回Task3

getTaskN(2)调用：
→ 遍历Task1: 能量不足 → 跳过
→ 遍历Task2: 已调度 → 跳过
→ 遍历Task3: 已调度 → 跳过
→ 返回nullptr

✅ 完全符合贪心策略！
```

**关键改进**:
1. ✅ **删除ready_index逻辑**：不再依赖"第n个任务"的语义
2. ✅ **全队列扫描**：从高到低遍历所有任务
3. ✅ **跳过机制**：跳过已调度/运行中/能量不足的任务
4. ✅ **能量语义**：返回"第一个能量足够的任务"，而不是"第n个任务"

---

## 📊 修复验证清单

### 修复1验证: 删除onTaskEnd()中的dispatch

- [ ] `gpfp_tie_scheduler.cpp:1589-1600` - 删除`_kernel->dispatch()`
- [ ] `gpfp_tgf_scheduler.cpp:1500-1513` - 删除`_kernel->dispatch()`
- [ ] 验证：任务结束后等待tick调度
- [ ] 验证：无非tick边界的dispatch调用

### 修复2验证: 删除onTaskArrival()中的checkAndPreempt

- [ ] `gpfp_tie_scheduler.cpp:925-936` - 删除`checkAndPreempt()`
- [ ] `gpfp_tgf_scheduler.cpp:926-937` - 删除`checkAndPreempt()`
- [ ] 验证：任务到达后等待tick抢占
- [ ] 验证：无非tick边界的抢占检查

### 修复3验证: TGF能量扣除时机

- [ ] `gpfp_tgf_scheduler.hpp` - 添加`_in_tick_boundary_dispatch`字段
- [ ] `gpfp_tgf_scheduler.cpp:getTaskN()` - 只在tick边界标记任务
- [ ] `gpfp_tgf_scheduler.cpp:performTickScheduling()` - 统一扣除能量
- [ ] 验证：能量只在tick边界扣除
- [ ] 验证：getTaskN()非tick边界拒绝调度

### 修复4验证: 续期能量精确扣除

- [ ] `gpfp_tie_scheduler.hpp` - 添加`last_deduct_time`字段
- [ ] `gpfp_tgf_scheduler.hpp` - 添加`last_deduct_time`字段
- [ ] `gpfp_tie_scheduler.cpp:performTickScheduling()` - 精确计算实际执行时间
- [ ] `gpfp_tgf_scheduler.cpp:performTickScheduling()` - 精确计算实际执行时间
- [ ] 验证：续期能量扣除与实际执行时间匹配

### 修复5验证: dispatchTask()能量账户初始化

- [ ] `gpfp_tie_scheduler.cpp:dispatchTask()` - 初始化energy_accounts
- [ ] `gpfp_tgf_scheduler.cpp:dispatchTask()` - 初始化energy_accounts
- [ ] 验证：新任务被调度时正确初始化
- [ ] ���证：重新调度的任务更新last_deduct_time

---

## 🎯 修复后的保证

### 时序保证

```
时间轴（修复后）：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tick=0ms边界：
  ✅ performTickScheduling()
  ✅ 收集太阳能
  ✅ 精确扣除续期能量（0→1ms的实际执行时间）
  ✅ checkAndPreempt()
  ✅ dispatch()（主调度）

Tick=0ms ~ 1ms之间：
  ✅ 任务在0.3ms到达 → addToReadyQueue()，不调度
  ✅ 任务在0.5ms结束 → 清理状态，不调度
  ✅ 无任何调度/能量/抢占操作

Tick=1ms边界：
  ✅ performTickScheduling()
  ✅ 收集太阳能
  ✅ 精确扣除续期能量（1→2ms的实际执行时间）
  ✅ checkAndPreempt()
  ✅ dispatch()（主调度）
```

### 算法语义保证

#### TIE算法语义（修复后）

✅ **严格优先级**:
```cpp
// getTaskN()中：从高到低遍历，遇到能量不足立即停止
for (size_t i = 0; i < _ready_queue.size(); ++i) {
    AbsRTTask *task = _ready_queue[i];

    if (ready_index == n) {
        double unit_energy = calculateUnitEnergyForTask(task);

        // ⭐ 队头阻塞：能量不足立即停止
        if (_current_energy < unit_energy - EPSILON) {
            return nullptr;  // 停止级联
        }

        // 调度任务
        _counted_tasks_in_dispatch.insert(task);
        return task;
    }
    ready_index++;
}
```

✅ **队头阻塞**:
```
场景：[Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
当前能量：只够Task2和Task3

TIE调度:
1. 检查Task1: 能量不足 → 立即停止！
2. CPU0, CPU1, CPU2全部保持空闲

结果：符合设计意图的队头阻塞
```

#### TGF算法语义（修复后）

✅ **贪婪扫描**:
```cpp
// getTaskN()中：全队列扫描，跳过能量不足的任务
if (available_energy < unit_energy - EPSILON) {
    // ⭐ 贪心策略：继续查找队列中是否有能量足够的后续任务
    for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
        AbsRTTask *next_task = _ready_queue[j];
        double next_unit_energy = calculateUnitEnergyForTask(next_task);

        if (next_available >= next_unit_energy - EPSILON) {
            // 找到能量足够的后续任务，调度它！
            _counted_tasks_in_dispatch.insert(next_task);
            return next_task;
        }
    }
}
```

✅ **跳过大任务**:
```
场景：[Task1(P1,大能耗), Task2(P2,小能耗), Task3(P3,小能耗)]
当前能量：只够Task2和Task3

TGF调度:
1. 检查Task1: 能量不足 → 跳过
2. 检查Task2: 能量足够 → 调度到CPU0
3. 检查Task3: 能量足够 → 调度到CPU1

结果：符合设计意图的贪婪填充
```

### 能量记账精确性保证

✅ **新任务能量扣除**:
```cpp
// performTickScheduling()第4步：统一扣除
for (AbsRTTask *task : _counted_tasks_in_dispatch) {
    double unit_energy = calculateUnitEnergyForTask(task);
    _current_energy -= unit_energy;  // 只扣除1ms能量
}
```

✅ **运行任务续期能量**:
```cpp
// performTickScheduling()第2步：精确扣除
Tick actual_exec_time = current_time - account.last_deduct_time;
double actual_energy = unit_energy * static_cast<double>(actual_exec_time) / 1000.0;
_current_energy -= actual_energy;  // 根据实际执行时间扣除
```

✅ **能量收集时机**:
```cpp
// performTickScheduling()第1步：tick边界收集
double harvested = collectSolarEnergy(current_time);
_current_energy += harvested;
```

---

## 🧪 测试计划

### 单元测试

1. **测试tick边界调度**
   - 验证：所有调度决策在tick边界
   - 验证：无tick之间的dispatch调用

2. **测试能量扣除时机**
   - 验证：能量只在tick边界扣除
   - 验证：能量收集在能量扣除之前

3. **测试续期能量精确性**
   - 验证：任务在tick=0.3ms开始，tick=1ms扣除0.7ms能量
   - 验证：任务在tick=1.5ms结束，不重复扣除

4. **测试TIE队头阻塞**
   - 验证：高优先级任务能量不足时立即停止
   - 验证：后续低优先级任务不被调度

5. **测试TGF贪婪填充**
   - 验证：跳过大任务，调度小任务
   - 验证：最大化多核并行度

### 集成测试

1. **多核调度测试**
   - 场景：3个CPU，5个任务，混合优先级和能耗
   - 验证：TIE和TGF的调度行为符合设计意图

2. **能量耗尽测试**
   - 场景：初始能量耗尽后的行为
   - 验证：能量不足时正确挂起任务

3. **任务到达/结束测试**
   - 场景：任务在tick之间到达/结束
   - 验证：等待tick边界处理

---

## 📝 总结

### 修复前后对比

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| **调度粒度** | 混合（tick边界 + 事件驱动） | 纯tick边界（1ms） |
| **调度时机** | tick边界 + 任务结束 | 只在tick边界 |
| **抢占时机** | tick边界 + 任务到达 | 只在tick边界 |
| **能量扣除** | dispatch时立即扣除 | tick边界统一扣除 |
| **续期能量** | 固定扣除1ms | 精确计算实际时间 |
| **能量账户** | 未初始化，可能导致计算错误 | dispatchTask()中正确初始化 |
| **算法语义** | 基本符合 | 完全符合 |

### 关键改进

1. ✅ **严格的tick边界调度**
   - 删除所有非tick边界的dispatch调用
   - 删除所有非tick边界的抢占检查

2. ✅ **绝对精准的能量记账**
   - 新任务能量：tick边界统一扣除
   - 续期能量：根据实际执行时间精确扣除
   - 能量账户：dispatchTask()中正确初始化

3. ✅ **保持算法语义**
   - TIE：严格优先级 + 队头阻塞
   - TGF：贪婪扫描 + 跳过大任务

### 设计目标达成

- ✅ 调度粒度：严格的1ms tick边界
- ✅ 能量管理：绝对精准的能量记账
- ✅ 算法语义：完全符合TIE/TGF设计意图

---

## 📌 注意事项

### 兼容性

- ✅ 修复后与MRTKernel完全兼容
- ✅ 修复后与EnergyBridge完全兼容
- ✅ 修复后不影响其他调度器

### 性能影响

- ⚠️ 响应延迟：任务到达/结束与调度之间最多1ms延迟
- ✅ 调度确定性：tick边界调度保证可预测性
- ✅ 能量精确性：消除了能量记账误差

### 后续优化

- [ ] 考虑添加tick边界调度性能监控
- [ ] 考虑添加能量记账精度验证
- [ ] 考虑添加算法语义验证测试

---

**文档版本**: v1.0
**最后更新**: 2026-02-01
**作者**: Claude Sonnet 4.5
**状态**: 待审查和测试
