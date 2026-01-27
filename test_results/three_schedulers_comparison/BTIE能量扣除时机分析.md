# BTIE能量扣除时机问题分析

## 当前实现对比

### TIE的能量扣除逻辑（正确）

```cpp
void TIEScheduler::performTickScheduling() {
    // 1. 先扣除运行中任务的能量（它们已经执行了1ms）
    for (const auto& map_pair : running_tasks) {
        AbsRTTask* task = map_pair.second;
        if (task) {
            total_energy_to_deduct += calculateUnitEnergyForTask(task);
        }
    }

    if (_current_energy >= total_energy_to_deduct) {
        _current_energy -= total_energy_to_deduct;  // 扣除
        _stats.total_energy_consumed += total_energy_to_deduct;
    }

    // 2. 然后进行新的调度决策
    // getTaskN()会逐个检查新任务的能量
}
```

**关键点**：
- ✅ **只扣除运行中任务的能量**（已经执行完了的）
- ✅ 新任务的能量在getTaskN()中**检查但不扣除**（让它们执行后在下个tick扣除）
- ✅ 符合"先执行后扣费"的逻辑

### BTIE的能量扣除逻辑（有问题）

```cpp
void BTIEScheduler::performTickScheduling() {
    // 1. 收集所有任务（运行中 + 新选择的K个）
    std::vector<AbsRTTask *> all_tasks;

    // 添加运行中任务
    for (const auto& map_pair : running_tasks) {
        if (task) all_tasks.push_back(task);
    }

    // 添加新任务（K个）
    size_t K = std::min(free_cpus, _ready_queue.size());
    for (size_t j = 0; j < K; ++j) {
        all_tasks.push_back(sorted_ready[j]);
    }

    // 2. 计算所有任务的总能耗（运行中 + 新任务）
    double total_energy = 0.0;
    for (auto* task : all_tasks) {
        total_energy += calculateUnitEnergyForTask(task);
    }

    // 3. 批量判断并一次性扣除所有任务的能量
    if (_current_energy >= total_energy) {
        _current_energy -= total_energy;  // ❌ 预扣了新任务的能量！
        _batch_scheduled_this_tick = true;
        _current_batch_tasks = all_tasks;
    }
}
```

**问题**：
- ❌ **预扣了新任务的能量**（它们还没有执行！）
- ❌ 如果有新任务加入但能量不足，会导致误判
- ❌ 不符合"先执行后扣费"的逻辑

## 时间线对比

### TIE的执行流程（正确）

```
Tick 0开始:
  ├─ 0ms: task_1, task_2到达，加入_ready_queue
  ├─ 0ms: 触发调度（第一次调度，特殊处理）
  │   └─ getTaskN(0) → task_1（检查能量但不扣）
  │   └─ getTaskN(1) → task_2（检查能量但不扣）
  └─ task_1, task_2开始执行

Tick 1开始:
  ├─ 1ms: performTickScheduling()被调用
  │   ├─ 扣除task_1, task_2的能量（它们已经执行了1ms）← 正确！
  │   ├─ 检查_ready_queue，决定是否调度新任务
  │   └─ getTaskN(0) → task_1（续期，检查能量）
  │   └─ getTaskN(1) → task_2（续期，检查能量）
  └─ task_1, task_2继续执行

Tick 5开始:
  ├─ 5ms: performTickScheduling()被调用
  │   ├─ 扣除task_2的能量（task_1已完成）
  │   ├─ getTaskN(0) → task_2（续期）
  │   └─ getTaskN(1) → task_3（新调度，检查能量但不扣）
  └─ task_2继续，task_3开始执行
```

### BTIE的执行流程（有问题）

```
Tick 0开始:
  ├─ 0ms: task_1, task_2, task_3到达，加入_ready_queue
  ├─ 0ms: performTickScheduling()被调用（tick事件）
  │   ├─ 收集all_tasks = [task_1, task_2]（K=2）
  │   ├─ 计算total_energy = task_1 + task_2
  │   ├─ 扣除total_energy ← 预扣了task_1, task_2的能量
  │   └─ 设置_batch_scheduled_this_tick = true
  ├─ dispatch()被调用
  │   ├─ getTaskN(0) → task_1（从_current_batch_tasks返回）
  │   └─ getTaskN(1) → task_2（从_current_batch_tasks返回）
  └─ task_1, task_2开始执行

Tick 1开始:
  ├─ 1ms: performTickScheduling()被调用
  │   ├─ 收集all_tasks = [task_1, task_2]（都在运行中）
  │   ├─ 计算total_energy = task_1 + task_2
  │   ├─ 扣除total_energy ← 再次扣除！
  │   └─ getTaskN(0), getTaskN(1) → task_1, task_2（续期）
  └─ task_1, task_2继续执行

问题：新任务的能量被预扣了！
```

## 正确的BTIE能量扣除逻辑

### 修改方案

```cpp
void BTIEScheduler::performTickScheduling() {
    // 1. ⭐ 只扣除运行中任务的能量（它们已经执行了1ms）
    std::vector<AbsRTTask *> running_task_list;
    double energy_to_deduct = 0.0;

    for (const auto& map_pair : running_tasks) {
        AbsRTTask* task = map_pair.second;
        if (task) {
            running_task_list.push_back(task);
            double unit_energy = calculateUnitEnergyForTask(task);
            energy_to_deduct += unit_energy;
        }
    }

    // 扣除运行中任务的能量
    if (energy_to_deduct > 0) {
        if (_current_energy >= energy_to_deduct) {
            _current_energy -= energy_to_deduct;
            _stats.total_energy_consumed += energy_to_deduct;
        }
    }

    // 2. ⭐ 选择K个新任务（不扣除它们的能量，让它们执行后再扣除）
    size_t running_count = running_task_list.size();
    size_t total_cpus = running_tasks.size();
    size_t free_cpus = total_cpus - running_count;
    size_t K = std::min(free_cpus, _ready_queue.size());

    std::vector<AbsRTTask *> new_tasks_to_schedule;
    if (K > 0) {
        std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
        std::sort(sorted_ready.begin(), sorted_ready.end(),
            [](AbsRTTask* a, AbsRTTask* b) { return a->getDeadline() < b->getDeadline(); });

        size_t select_count = std::min(K, sorted_ready.size());
        for (size_t j = 0; j < select_count; ++j) {
            new_tasks_to_schedule.push_back(sorted_ready[j]);
        }
    }

    // 3. ⭐ BTIE核心：只检查新任务的能量，不扣除
    double new_tasks_energy = 0.0;
    for (auto* task : new_tasks_to_schedule) {
        new_tasks_energy += calculateUnitEnergyForTask(task);
    }

    // 4. 批量判断：能量是否足够运行新任务
    if (_current_energy >= new_tasks_energy) {
        _batch_scheduled_this_tick = true;

        // ⭐ _current_batch_tasks应该包含：运行中任务 + 新任务
        std::vector<AbsRTTask *> all_tasks_to_dispatch;
        for (auto* task : running_task_list) {
            all_tasks_to_dispatch.push_back(task);
        }
        for (auto* task : new_tasks_to_schedule) {
            all_tasks_to_dispatch.push_back(task);
        }

        _current_batch_tasks = all_tasks_to_dispatch;
        _current_batch_size = all_tasks_to_dispatch.size();
    } else {
        // 能量不足：不调度新任务，但运行中任务继续
        _batch_scheduled_this_tick = false;
        _current_batch_tasks.clear();
        _current_batch_size = 0;
    }
}
```

## "全有或全无"策略的正确理解

### BTIE的"全有或全无"

**含义**：
- 能量足够运行**所有新选择的K个任务** → 调度全部K个
- 能量不足以运行**所有K个新任务** → 不调度任何新任务
- **运行中任务不受影响**（它们已经执行了，继续执行）

**当前错误实现**：
- ❌ 能量不足时，会suspend所有运行中任务
- ❌ 这违反了"运行中任务继续"的原则

**正确实现**：
- ✅ 能量不足时，只影响新任务，运行中任务继续
- ✅ _batch_scheduled_this_tick = false 只影响getTaskN()返回新任务

## 修复总结

### 需要修改的地方

1. **能量扣除时机**
   - ❌ 当前：扣除all_tasks（运行中+新任务）
   - ✅ 正确：只扣除running_tasks（运行中）

2. **能量不足时的处理**
   - ❌ 当前：suspend所有运行中任务
   - ✅ 正确：只影响新任务，运行中任务继续

3. **_current_batch_tasks的组成**
   - ✅ 当前：包含运行中+新任务
   - ✅ 正确：保持不变（这样getTaskN可以正确续期）

4. **批量判断的对象**
   - ❌ 当前：判断all_tasks的能量
   - ✅ 正确：只判断new_tasks的能量
