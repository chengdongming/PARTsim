# TIE和BTIE调度序列差异根本原因分析

## 问题现象

**能耗差异**：
- TIE: 30×2任务(1.116mJ) + 50×1任务(0.558mJ) = 61.380mJ
- BTIE: 27×2任务(1.116mJ) + 55×1任务(0.558mJ) = 60.822mJ
- 差异: 3次2任务运行少5次1任务运行 = 0.558mJ

## 根本原因

### BTIE的致命Bug

**问题代码位置**: `librtsim/scheduler/gpfp_btie_scheduler.cpp:509`

```cpp
// 7. BTIE核心：批量能量判断
if (_current_energy >= new_tasks_energy - EPSILON) {
    _batch_scheduled_this_tick = true;
    _current_batch_tasks = new_tasks_to_schedule;  // ❌ 只包含新任务！
    _current_batch_size = new_tasks_to_schedule.size();
}
```

**问题所在**：
1. `new_tasks_to_schedule`只包含从`_ready_queue`中选择的K个**新任务**
2. `_current_batch_tasks`设置为**只有新任务**，不包含运行中任务
3. `getTaskN()`从`_current_batch_tasks`中返回任务

**结果**：运行中任务无法通过getTaskN()续期！

### BTIE的getTaskN()逻辑

```cpp
AbsRTTask *BTIEScheduler::getTaskN(unsigned int n) {
    if (!_batch_scheduled_this_tick) {
        return nullptr;  // 批量调度失败
    }

    if (n >= _current_batch_size) {
        return nullptr;  // 超出K个任务
    }

    // 从_current_batch_tasks中返回第n个任务
    unsigned int ready_index = 0;
    for (size_t i = 0; i < _current_batch_tasks.size(); ++i) {
        AbsRTTask *task = _current_batch_tasks[i];

        // 检查任务是否已经在运行
        bool is_running = false;
        for (const auto &pair : _running_tasks) {
            if (pair.second == task) {
                is_running = true;
                break;
            }
        }

        if (is_running) {
            // 运行中任务续期
            if (ready_index == n) {
                return task;
            }
            ready_index++;
            continue;
        }

        // 未运行任务，返回（新调度）
        if (ready_index == n) {
            return task;
        }

        ready_index++;
    }

    return nullptr;
}
```

**问题**：`_current_batch_tasks`中只有新任务，没有运行中任务！所以`is_running`检查总是返回false，运行中任务无法续期。

### TIE的正确逻辑

TIE的getTaskN()遍历`_ready_queue`，对每个任务：
1. 检查是否在`_running_tasks`中
2. 如果是运行中任务，检查能量，能量足够就续期
3. 如果不是运行中任务，检查能量，能量足够就调度

关键差异：TIE的`_ready_queue`在调度后**不会**移除运行中任务！所以getTaskN()可以找到运行中任务并续期。

## 调度序列对比

### TIE的调度序列（从日志）

```
0ms: 调度task_1 (累计0.000558J)
0ms: 调度task_1 (累计0.000558J)
0ms: 调度task_1 (累计0.000558J)
0ms: 调度task_1, task_2 (累计0.001116J)  ← 2个任务
0ms: 调度task_1 (累计0.000558J)
0ms: 调度task_1, task_2 (累计0.001116J)  ← 2个任务
...
```

TIE在每次调度开始时重置累计能耗，所以可以看到1.116mJ（2任务）和0.558mJ（1任务）交替。

### BTIE的调度序列（从日志）

```
0ms: 运行中=0 空闲=2 选择K=2 新任务能耗=1.116mJ  ← 调度2个新任务
1ms: 运行中=2 空闲=0 选择K=0  ← 续期2个运行中任务
2ms: 运行中=2 空闲=0 选择K=0  ← 续期2个运行中任务
3ms: 运行中=2 空闲=0 选择K=0  ← 续期2个运行中任务
4ms: 运行中=2 空闲=0 选择K=0  ← 续期2个运行中任务
5ms: 运行中=1 空闲=1 选择K=1 新任务能耗=0.558mJ  ← task_1完成，调度1个新任务
```

BTIE在1-4ms时，有2个运行中任务，但扣除的是运行中任务的能耗（在performTickScheduling开始时扣除）。

## 修复方案

### 修复位置

文件: `librtsim/scheduler/gpfp_btie_scheduler.cpp`
行号: 509

### 修改前

```cpp
if (_current_energy >= new_tasks_energy - EPSILON) {
    _batch_scheduled_this_tick = true;
    _current_batch_tasks = new_tasks_to_schedule;  // ❌ 只有新任务
    _current_batch_size = new_tasks_to_schedule.size();
}
```

### 修改后

```cpp
if (_current_energy >= new_tasks_energy - EPSILON) {
    _batch_scheduled_this_tick = true;

    // ⭐ 关键修复：_current_batch_tasks应该包含：运行中任务 + 新任务
    // 这样getTaskN()才能正确返回运行中任务（续期）和新任务（调度）

    std::vector<AbsRTTask *> all_tasks_to_dispatch;

    // 1. 先添加运行中任务（它们需要续期）
    for (const auto& map_pair : running_tasks) {
        AbsRTTask* task = map_pair.second;
        if (task) {
            all_tasks_to_dispatch.push_back(task);
        }
    }

    // 2. 再添加新任务（按优先级排序）
    all_tasks_to_dispatch.insert(all_tasks_to_dispatch.end(),
                                 new_tasks_to_schedule.begin(),
                                 new_tasks_to_schedule.end());

    _current_batch_tasks = all_tasks_to_dispatch;
    _current_batch_size = all_tasks_to_dispatch.size();
}
```

## 预期修复效果

### 修复前
- 27×2任务 + 55×1任务 = 60.822mJ
- 运行中任务在某些tick无法续期

### 修复后（预期）
- 30×2任务 + 50×1任务 = 61.380mJ
- 与TIE完全一致

## 验证方法

修复后运行测试，检查：
1. 能耗应该与TIE一致：0.061380J
2. 追踪文件应该与TIE完全一致
3. 统计"运行中=2"的次数应该与TIE的"累计0.001116J"次数一致

## 总结

**根本原因**：BTIE的`_current_batch_tasks`只包含新任务，不包含运行中任务，导致getTaskN()无法返回运行中任务进行续期。

**修复方法**：`_current_batch_tasks`应该包含运行中任务+新任务，这样getTaskN()才能正确处理续期和调度。

**影响**：这个bug导致BTIE在某些tick只运行1个任务而不是2个，减少了3次2任务运行，增加了5次1任务运行。
