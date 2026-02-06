# TGF调度器能量消耗Bug修复报告

## 问题描述

在对比TIE、TGF、BTIE三种调度算法的追踪文件时，发现TGF调度器存在严重bug：**在任务到达（arrival）事件时就错误地消耗了能量**。

### Bug表现

**TIE（正确）：**
```json
{ "time": "0", "event_type": "arrival", "task_name": "task_1", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }
{ "time": "0", "event_type": "arrival", "task_name": "task_2", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }
{ "time": "0", "event_type": "arrival", "task_name": "task_3", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }
```

**TGF（修复前）：**
```json
{ "time": "0", "event_type": "arrival", "task_name": "task_1", "current_energy_mJ": 11.442, "total_consumed_mJ": 0.558 }  ❌
{ "time": "0", "event_type": "arrival", "task_name": "task_2", "current_energy_mJ": 10.884, "total_consumed_mJ": 1.116 }  ❌
{ "time": "0", "event_type": "arrival", "task_name": "task_3", "current_energy_mJ": 10.884, "total_consumed_mJ": 1.116 }  ❌
```

TGF在任务到达时就消耗了能量（每个任务0.558 mJ），这是不正确的。任务到达不应该消耗能量，只有任务执行时才应该消耗能量。

## 根本原因

### 事件执行顺序

在MetaSim框架中，事件的执行顺序是：
1. `Event::action()` 调用 `doit()`
2. `ArrEvt::doit()` 调用 `Task::onArrival()`
3. `Task::onArrival()` 调用 `Kernel::onArrival()` 触发调度
4. `Kernel::dispatch()` 调用 `Scheduler::getTaskN()`
5. **TGF的getTaskN()立即扣除能量**
6. `Event::action()` 调用所有监听器的 `probe()`
7. 追踪监听器记录arrival事件（此时能量已被扣除）

### TIE vs TGF的实现差异

**TIE的实现（正确）：**
```cpp
// getTaskN(): 只标记任务，不扣除能量
if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
    _counted_tasks_in_dispatch.insert(task);  // 只标记
}

// performTickScheduling(): dispatch后统一扣除能量
_kernel->dispatch();
for (AbsRTTask *task : _counted_tasks_in_dispatch) {
    double unit_energy = calculateUnitEnergyForTask(task);
    _current_energy -= unit_energy;  // 统一扣除
}
```

**TGF的实现（修复前）：**
```cpp
// getTaskN(): 立即扣除能量 ❌
if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
    _current_energy -= unit_energy;  // 立即扣除 ❌
    _counted_tasks_in_dispatch.insert(task);
}
```

## 修复方案

让TGF采用与TIE相同的能量扣除策略：
1. 在`getTaskN()`中只标记任务，不扣除能量
2. 在`performTickScheduling()`的`dispatch()`后统一扣除能量

### 修改的代码位置

**文件：** `librtsim/scheduler/gpfp_tgf_scheduler.cpp`

**修改1：getTaskN()方法（第736-746行）**
```cpp
// 修复前：
_current_energy -= next_unit_energy;
_stats.total_energy_consumed += next_unit_energy;
_counted_tasks_in_dispatch.insert(next_task);

// 修复后：
_counted_tasks_in_dispatch.insert(next_task);  // 只标记，不扣除
```

**修改2：getTaskN()方法（第758-770行）**
```cpp
// 修复前：
_current_energy -= unit_energy;
_stats.total_energy_consumed += unit_energy;
_counted_tasks_in_dispatch.insert(task);

// 修复后：
_counted_tasks_in_dispatch.insert(task);  // 只标记，不扣除
```

**修改3：performTickScheduling()方法（第521-527行）**
```cpp
// 添加：清空调度记录
_counted_tasks_in_dispatch.clear();
_dispatching_tasks_total_energy = 0.0;
```

**修改4：performTickScheduling()方法（第568-590行）**
```cpp
// 添加：dispatch后统一扣除能量
for (AbsRTTask *task : _counted_tasks_in_dispatch) {
    double unit_energy = calculateUnitEnergyForTask(task);
    _current_energy -= unit_energy;
    _stats.total_energy_consumed += unit_energy;
    _dispatching_tasks_total_energy += unit_energy;
}
```

## 修复验证

### 测试结果

运行所有测试（24个测试：12个午夜 + 12个中午）：
- ✅ 所有测试通过
- ✅ TGF的arrival事件能量正确（current_energy_mJ=12, total_consumed_mJ=0）
- ✅ 能量消耗逻辑正确

### 修复后的追踪文件

**TGF（修复后）：**
```json
{ "time": "0", "event_type": "arrival", "task_name": "task_1", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }  ✓
{ "time": "0", "event_type": "arrival", "task_name": "task_2", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }  ✓
{ "time": "0", "event_type": "arrival", "task_name": "task_3", "current_energy_mJ": 12.000, "total_consumed_mJ": 0.000 }  ✓
```

现在TGF的arrival事件能量与TIE完全一致！

## 影响分析

### 修复前的影响

1. **追踪文件不准确**：arrival事件记录的能量状态不正确
2. **能量消耗提前**：能量在任务到达时就被扣除，而不是在调度时扣除
3. **算法对比失真**：TGF与TIE/BTIE的追踪文件无法直接对比

### 修复后的改进

1. **追踪文件准确**：arrival事件正确反映任务到达时的能量状态
2. **能量扣除时机正确**：能量在dispatch后统一扣除
3. **算法对比准确**：三种算法的追踪文件可以准确对比

## 总结

这是一个关键的bug修复，确保了TGF调度器的能量消耗逻辑与TIE/BTIE一致。修复后，三种算法的追踪文件可以准确对比，为算法性能分析提供了可靠的数据基础。

**修复日期：** 2026-02-06
**修复人员：** Claude Sonnet 4.5
**测试状态：** ✅ 所有测试通过（24/24）
