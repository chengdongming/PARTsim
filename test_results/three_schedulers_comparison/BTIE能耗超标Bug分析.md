# BTIE能耗超标Bug根本原因分析

## Bug确认

### 问题现象

```
BTIE批量调度: 收集3个任务，扣除1.674 mJ
实际调度:     只调用getTaskN(0)和getTaskN(1)，调度2个任务
能量浪费:     第3个任务的能量（0.558 mJ）被扣除但未执行
```

### 日志证据

```
[DEBUG] MRTKernel::dispatch() - 计算num_newtasks, ncpu=2
[DEBUG]   getTaskN(0)=task_1 getProcessor=energy_aware_cpus-1 freq 8100
[DEBUG]   getTaskN(1)=task_2 getProcessor=energy_aware_cpus-0 freq 8100
[DEBUG] MRTKernel::dispatch() - num_newtasks=0
```

**关键**: 只调用了getTaskN(0)和getTaskN(1)，因为ncpu=2

## Bug根因

### 1. BTIE的批量调度逻辑

```cpp
// gpfp_btie_scheduler.cpp:420-467
void BTIEScheduler::performTickScheduling() {
    // 1. 收集所有任务（运行中 + 就绪队列）
    std::vector<AbsRTTask *> all_tasks;
    all_tasks.insert(all_tasks.end(), running_tasks.begin(), running_tasks.end());
    for (auto* task : _ready_queue) {
        if (task && std::find(all_tasks.begin(), all_tasks.end(), task) == all_tasks.end()) {
            all_tasks.push_back(task);
        }
    }
    
    // 2. 计算所有任务的总能耗
    double total_energy = 0.0;
    for (auto* task : all_tasks) {
        total_energy += calculateUnitEnergyForTask(task);
    }
    
    // 3. ❌ Bug: 扣除所有任务的能量，不管实际能调度多少个
    if (_current_energy >= total_energy) {
        _current_energy -= total_energy;  // 扣除了all_tasks.size()个任务
        _batch_scheduled_this_tick = true;
    }
}
```

### 2. Dispatch的限制

```cpp
// MRTKernel::dispatch()
void MRTKernel::dispatch() {
    int ncpu = 2;  // 2个CPU
    
    // ⚠️ 只调用getTaskN(0)到getTaskN(ncpu-1)
    for (int i = 0; i < ncpu; i++) {
        AbsRTTask *task = getTaskN(i);  // 最多只调用getTaskN(0)和getTaskN(1)
        if (task) {
            // 分配到CPU
        }
    }
}
```

### 3. 问题流程

```
时刻X:
  CPU0: 运行 task_1 (高优先级)
  CPU1: 运行 task_2 (高优先级)
  就绪队列: [task_1, task_2, task_3]
  
BTIE::performTickScheduling():
  1. 收集: {task_1, task_2, task_3} = 3个任务
  2. 扣除: 3 × 0.558 = 1.674 mJ ❌ 多扣了！
  3. 设置: _batch_scheduled_this_tick = true

MRTKernel::dispatch():
  1. getTaskN(0) → task_1 ✅
  2. getTaskN(1) → task_2 ✅
  3. 没有调用getTaskN(2)，因为只有2个CPU ❌
  
结果:
  task_3的能量被扣除，但从未执行
```

## 能量浪费统计

### 那个"3个任务"的情况

```
BTIE有6次批量调度涉及3个任务:
  每次扣除: 3 × 0.558 = 1.674 mJ
  实际执行: 2 × 0.558 = 1.116 mJ
  每次浪费: 0.558 mJ
  
总浪费: 6 × 0.558 = 3.348 mJ
```

### 其他情况

```
BTIE有50次批量调度涉及1个任务:
  如果这1个任务在就绪队列等待（不在运行中）
  且它优先级较低，不会被调度
  能量也被浪费了
  
实际统计显示:
  总差异: 6.696 mJ
  其中6次3任务浪费: 3.348 mJ
  剩余: 3.348 mJ（可能来自其他情况的浪费）
```

## 修复方案

### 方案1: 只扣除能执行的任务能量（推荐）

```cpp
void BTIEScheduler::performTickScheduling() {
    // ... 收集all_tasks ...
    
    // ⭐ 修复: 只扣除min(CPU数量, 任务数量)个任务的能量
    int ncpu = getKernel()->getProcessorCount();
    int actual_tasks = std::min((int)all_tasks.size(), ncpu);
    
    double actual_energy = 0.0;
    for (int i = 0; i < actual_tasks; i++) {
        actual_energy += calculateUnitEnergyForTask(all_tasks[i]);
    }
    
    // 扣除实际能执行的任务能量
    if (_current_energy >= actual_energy) {
        _current_energy -= actual_energy;
        _stats.total_energy_consumed += actual_energy;
        _batch_scheduled_this_tick = true;
    }
}
```

### 方案2: 在getTaskN中扣除能量（像TIE）

```cpp
// 移除performTickScheduling中的能量扣除
// 在getTaskN中逐个扣除能量

AbsRTTask *BTIEScheduler::getTaskN(unsigned int n) {
    if (!_batch_scheduled_this_tick) {
        return nullptr;
    }
    
    // ... 返回第n个任务 ...
    
    // ⭐ 在这里扣除该任务的能量
    if (task) {
        double unit_energy = calculateUnitEnergyForTask(task);
        _current_energy -= unit_energy;
        _stats.total_energy_consumed += unit_energy;
    }
    
    return task;
}
```

### 方案3: 按优先级排序，只调度高优先级任务

```cpp
void BTIEScheduler::performTickScheduling() {
    // ... 收集all_tasks ...
    
    // ⭐ 按优先级排序
    std::sort(all_tasks.begin(), all_tasks.end(), 
              [](AbsRTTask* a, AbsRTTask* b) {
                  return getTaskPriority(a) > getTaskPriority(b);
              });
    
    // 只调度前ncpu个高优先级任务
    int ncpu = getKernel()->getProcessorCount();
    int actual_tasks = std::min((int)all_tasks.size(), ncpu);
    
    // 扣除实际调度的任务能量
    // ...
}
```

## 为什么TIE没有这个问题？

```cpp
// TIE: checkAndInterruptRunningTasks()
void TIEScheduler::checkAndInterruptRunningTasks() {
    // ✅ 只统计running_tasks，不包含就绪队列
    for (auto &map_pair : running_tasks) {
        AbsRTTask *task = map_pair.second;
        double unit_energy = calculateUnitEnergyForTask(task);
        total_energy_to_deduct += unit_energy;
    }
    
    // ✅ running_tasks的数量恰好等于CPU数量
    // 所以能量扣除是精确的
}
```

## 总结

### 这是一个真正的Bug！

- ❌ BTIE预先扣除了所有收集到的任务能量
- ❌ 但实际只能调度min(CPU数量, 任务数量)个任务
- ❌ 导致能量虚高10.9%

### 修复优先级

🔴 **高优先级**:
- 这个bug导致能耗统计不准确
- 在能量受限场景下可能影响任务完成率
- 建议优先修复

### 修复建议

推荐使用**方案1**，因为：
1. 保持批量调度逻辑不变
2. 只修改能量扣除的数量
3. 简单且不影响调度逻辑

