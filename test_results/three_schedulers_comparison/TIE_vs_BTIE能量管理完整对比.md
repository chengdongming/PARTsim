# TIE vs BTIE 能量管理完整对比

## 测试场景
- 2个CPU，3个周期性任务
- 初始能量：100J（充足）
- 任务：task_1 (周期20ms, WCET 5ms), task_2 (30ms, 10ms), task_3 (40ms, 15ms)

## TIE的能量管理流程

### 初始化阶段（0ms）

```
0ms: task_1, task_2, task_3到达
  └─ insert()被调用3次
  └─ 第一个任务到达时，触发performTickScheduling()（特殊处理）

performTickScheduling() {
    running_tasks = {};  // 没有运行中任务
    energy_to_deduct = 0;  // 不扣能量

    // 准备调度
    return;
}

getTaskN(0) → task_1（检查能量，不扣）
getTaskN(1) → task_2（检查能量，不扣）
```

**能量变化**：100J → 100J（不变）

### Tick 1（1ms）

```
1ms: Tick事件触发
  ├─ performTickScheduling()被调用
  │
  ├─ 第1步：扣除运行中任务的能量
  │   running_tasks = {task_1, task_2}
  │   energy_to_deduct = 0.558 + 0.558 = 1.116mJ
  │   _current_energy -= 1.116mJ  ← 扣除！
  │
  ├─ 第2步：调度决策
  │   getTaskN(0) → task_1（续期，检查能量）
  │   getTaskN(1) → task_2（续期，检查能量）
  │
  └─ task_1, task_2继续执行
```

**能量变化**：100J → 99.998884J（扣除1.116mJ）

### Tick 5（5ms）

```
5ms: task_1完成（执行了5ms）
  ├─ performTickScheduling()被调用
  │
  ├─ 第1步：扣除运行中任务的能量
  │   running_tasks = {task_2}  // task_1已完成
  │   energy_to_deduct = 0.558mJ
  │   _current_energy -= 0.558mJ
  │
  ├─ 第2步：调度决策
  │   getTaskN(0) → task_2（续期）
  │   getTaskN(1) → task_3（新调度，检查能量但不扣）
  │
  └─ task_2继续，task_3开始执行
```

**能量变化**：99.xxxxJ → 99.xxxxJ（扣除0.558mJ）

## BTIE的能量管理流程（当前实现）

### 初始化阶段（0ms）

```
0ms: task_1, task_2, task_3到达
  └─ insert()被调用3次
  └─ 不触发performTickScheduling()（已修复）

0ms: Tick事件触发
  ├─ performTickScheduling()被调用
  │
  ├─ 第1步：收集所有任务
  │   running_tasks = {}
  │   _ready_queue = {task_1, task_2, task_3}
  │   K = min(2, 3) = 2
  │   all_tasks = {task_1, task_2}  // ← 只选择2个新任务
  │
  ├─ 第2步：计算总能耗
  │   total_energy = 0.558 + 0.558 = 1.116mJ
  │
  ├─ 第3步：批量判断并扣除
  │   _current_energy -= 1.116mJ  ← 预扣！
  │   _batch_scheduled_this_tick = true
  │   _current_batch_tasks = {task_1, task_2}
  │
  └─ dispatch()调用getTaskN()
      getTaskN(0) → task_1
      getTaskN(1) → task_2
```

**能量变化**：100J → 99.998884J（预扣1.116mJ）

### Tick 1（1ms）

```
1ms: Tick事件触发
  ├─ performTickScheduling()被调用
  │
  ├─ 第1步：收集所有任务
  │   running_tasks = {task_1, task_2}
  │   _ready_queue = {task_3}
  │   K = min(0, 1) = 0  // 没有空闲CPU
  │   all_tasks = {task_1, task_2}  // 只有运行中任务
  │
  ├─ 第2步：计算总能耗
  │   total_energy = 0.558 + 0.558 = 1.116mJ
  │
  ├─ 第3步：批量判断并扣除
  │   _current_energy -= 1.116mJ  ← 再次扣除！
  │   _batch_scheduled_this_tick = true
  │   _current_batch_tasks = {task_1, task_2}
  │
  └─ task_1, task_2继续执行
```

**能量变化**：99.998884J → 99.997768J（再扣1.116mJ）

## 问题分析

### BTIE的重复扣除问题

从Tick 0到Tick 1：
- Tick 0：预扣task_1, task_2的能量（它们还没执行！）
- Tick 1：又扣除task_1, task_2的能量（它们正在执行）

**这是重复扣除！**

正确的逻辑应该是：
- Tick 0：不扣能量（任务刚开始）
- Tick 1：扣除task_1, task_2的能量（它们在Tick 0→1期间执行了）

### 为什么测试结果还是对的？

因为：
- 总能量 = (运行时间 × 单位能耗)
- TIE：每个tick扣除运行中任务的能量
- BTIE：每个tick扣除所有任务的能量

虽然扣除时机不同，但**总量相同**：
```
TIE总能耗 = Σ(每个tick扣除的运行任务能量)
BTIE总能耗 = Σ(每个tick扣除的所有任务能量)
         = Σ(每个tick扣除的运行任务能量)  // 因为新任务最终会变成运行任务
```

所以在能量充足的场景下，结果一致。

## 能量受限场景的问题

假设初始能量只有1.674mJ（只能运行3个任务1ms）：

### TIE的行为

```
Tick 0: 能量 = 1.674mJ
  ├─ 不扣能量（没有运行任务）
  ├─ 调度task_1, task_2
  └─ task_1, task_2开始执行

Tick 1: 能量 = 1.674mJ
  ├─ 扣除task_1, task_2: 1.674 - 1.116 = 0.558mJ
  ├─ 继续task_1, task_2
  └─ task_1, task_2继续执行

Tick 5: 能量 = 0.558mJ
  ├─ task_1完成
  ├─ 扣除task_2: 0.558 - 0.558 = 0.0mJ
  ├─ 尝试调度task_3
  └─ 能量不足，不调度task_3

Tick 6: 能量 = 0.0mJ
  ├─ task_2完成
  ├─ 无法调度任何任务
  └─ 系统停止
```

### BTIE的行为（当前实现）

```
Tick 0: 能量 = 1.674mJ
  ├─ 预扣task_1, task_2: 1.674 - 1.116 = 0.558mJ
  ├─ 调度task_1, task_2
  └─ task_1, task_2开始执行

Tick 1: 能量 = 0.558mJ
  ├─ 计算total_energy = 1.116mJ
  ├─ 能量不足（0.558 < 1.116）
  ├─ _batch_scheduled_this_tick = false
  ├─ suspend task_1, task_2  ← 不应该suspend！
  └─ 系统停止
```

**问题**：
1. BTIE预扣了能量，导致在Tick 1能量不足
2. BTIE suspend了运行中任务，这是错误的
3. 任务实际没有完成（它们才执行了1ms）

## 最优方案设计

### 核心原则

1. **能量后扣**：只扣除运行中任务的能量（它们已经执行了）
2. **批量决策**：一次性检查是否有足够能量运行新任务
3. **不预扣**：新任务的能量不预先扣除
4. **运行任务不受影响**：能量不足时不suspend运行中任务

### 修改方案

```cpp
void BTIEScheduler::performTickScheduling() {
    // 1. ⭐ 只扣除运行中任务的能量（后扣）
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

    // 2. ⭐ 选择K个新任务（不扣除能量）
    size_t running_count = running_task_list.size();
    size_t total_cpus = running_tasks.size();
    size_t free_cpus = total_cpus - running_count;
    size_t K = std::min(free_cpus, _ready_queue.size());

    std::vector<AbsRTTask *> new_tasks_to_schedule;
    if (K > 0) {
        std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
        std::sort(sorted_ready.begin(), sorted_ready.end(),
            [](AbsRTTask* a, AbsRTTask* b) { return a->getDeadline() < b->getDeadline(); });

        for (size_t j = 0; j < K && j < sorted_ready.size(); ++j) {
            new_tasks_to_schedule.push_back(sorted_ready[j]);
        }
    }

    // 3. ⭐ 只检查新任务的能量，不扣除
    double new_tasks_energy = 0.0;
    for (auto* task : new_tasks_to_schedule) {
        new_tasks_energy += calculateUnitEnergyForTask(task);
    }

    // 4. ⭐ 批量判断（"全有或全无"）
    if (_current_energy >= new_tasks_energy) {
        // 能量充足：调度所有新任务
        _batch_scheduled_this_tick = true;

        // _current_batch_tasks包含：运行中 + 新任务
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

        // ⭐ 关键：不suspend运行中任务
        // 它们会继续执行，因为它们的能量已经在第1步扣除过了
    }
}
```

### 修改后的能量流（能量受限场景）

```
Tick 0: 能量 = 1.674mJ
  ├─ 运行任务 = {}, energy_to_deduct = 0
  ├─ K = min(2, 3) = 2, 新任务 = {task_1, task_2}
  ├─ 新任务能量 = 1.116mJ <= 1.674mJ ✓
  ├─ 调度task_1, task_2
  └─ 能量 = 1.674mJ（不变）

Tick 1: 能量 = 1.674mJ
  ├─ 运行任务 = {task_1, task_2}, energy_to_deduct = 1.116mJ
  ├─ 扣除能量: 1.674 - 1.116 = 0.558mJ ✓
  ├─ K = 0（没有空闲CPU）
  ├─ 继续task_1, task_2
  └─ 能量 = 0.558mJ

Tick 5: 能量 = 0.558mJ
  ├─ 运行任务 = {task_2}, energy_to_deduct = 0.558mJ
  ├─ 扣除能量: 0.558 - 0.558 = 0.0mJ ✓
  ├─ K = 1, 新任务 = {task_3}
  ├─ 新任务能量 = 0.558mJ > 0.0mJ ✗
  ├─ 不调度task_3
  ├─ 继续task_2
  └─ 能量 = 0.0mJ

Tick 6: 能量 = 0.0mJ
  ├─ 运行任务 = {}, energy_to_deduct = 0
  ├─ K = 2, 新任务 = {task_3}
  ├─ 新任务能量 = 0.558mJ > 0.0mJ ✗
  ├─ 不调度task_3
  └─ 系统空闲
```

**与TIE完全一致！**

## 总结

最优方案的核心修改：
1. ✅ 只扣除运行中任务的能量（后扣）
2. ✅ 只检查新任务的能量（不预扣）
3. ✅ 能量不足时只影响新任务，运行中任务继续
4. ✅ 保持批量决策的特性

这样BTIE就真正是"批量TIE"了：
- 批量决策（一次性检查所有新任务）
- 能量后扣（与TIE一致）
- 行为正确（能量充足和受限场景都正确）
