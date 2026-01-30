# BTIE调度器能量管理修复报告

## 修复日期
2026-01-30

## 问题描述

### 原始问题
BTIE调度器在能量耗尽时没有立即中断任务，导致：
- task_3在15ms自然结束（end_instance）
- 能量耗尽时刻：14ms
- 总能耗：13.2mJ > 12mJ（超出预算）

### 期望行为（用户提供的正确调度）
- task_3应在12ms被强制中断（descheduled）
- 能量耗尽时刻：12ms
- 总能耗：12mJ（正好符合预算）
- 12-15ms：系统全员空闲，等待积攒电量

## 修复内容

### 修复1：能量检查事件 - 在扣除前检查并suspend
**文件**：`librtsim/scheduler/gpfp_btie_scheduler.cpp:149-186`

**修改前**：
```cpp
// 先扣除能量
_scheduler->_current_energy -= unit_energy;

// 再检查是否耗尽
if (_scheduler->_current_energy < EPSILON) {
    _scheduler->_energy_depleted = true;
    return;  // 只标记，不中断任务
}
```

**修改后**：
```cpp
// 先检查能量是否充足
if (current_energy < unit_energy - EPSILON) {
    _scheduler->_energy_depleted = true;
    
    // 立即suspend任务（与TIE保持一致）
    if (_scheduler->_kernel && _task->isExecuting()) {
        _scheduler->_kernel->suspend(_task);
    }
    return;
}

// 能量充足才扣除
_scheduler->_current_energy -= unit_energy;
```

### 修复2：批量调度"全无"分支 - 立即suspend所有运行任务
**文件**：`librtsim/scheduler/gpfp_btie_scheduler.cpp:762-796`

**修改前**：
```cpp
_energy_depleted = true;

// 只取消能量检查事件
for (auto* task : running_task_list) {
    _energy_check_events.erase(it);
}
```

**修改后**：
```cpp
_energy_depleted = true;

// 立即suspend所有运行中任务
for (auto* task : running_task_list) {
    if (task && task->isExecuting()) {
        _kernel->suspend(task);  // 强制中断
        _energy_check_events.erase(it);
    }
}
```

## 修复效果对比

### 追踪事件对比

#### 修复前（btie_12mj_1000ms_fixed.json）
```json
{ "time" : "5",  "event_type" : "scheduled",  "task_name" : "task_3"},
{ "time" : "15", "event_type" : "end_instance", "task_name" : "task_3"},  // ❌ 自然结束
```

#### 修复后（btie_12mj_FIXED.json）
```json
{ "time" : "5",  "event_type" : "scheduled",  "task_name" : "task_3"},
{ "time" : "14", "event_type" : "descheduled", "task_name" : "task_3"},  // ✅ 被中断
```

### 日志输出对比

#### 修复前
```
14ms: 💀 [BTIE] 能量耗尽，标记任务结束
15ms: ✅ [BTIE] 任务结束: task_3
```

#### 修复后
```
14ms: ⛔️ [BTIE] 能量不足，立即中断1个运行任务（遵循BTIE'全无'原则）
      - 挂起任务: PeriodicTask task_3
      💀 [BTIE] 能量已耗尽，所有运行任务已挂起，系统进入空闲等待状态
```

## 进步与局限

### ✅ 已修复的问题
1. **能量检查事件现在会立即suspend任务**（之前只标记）
2. **批量调度"全无"分支现在会立即suspend所有运行任务**（之前只取消事件）
3. **task_3现在在14ms被中断**（之前15ms自然结束）
4. **事件类型从end_instance改为descheduled**（更符合能量不足的中断语义）

### ⚠️ 仍存在的问题
1. **中断时刻仍然是14ms，而非期望的12ms**
2. **总能耗仍然是13.2mJ，超出12mJ预算**
3. **批量调度的能量判断逻辑仍需改进**

### 问题根源分析
批量调度在8ms时计算：
- running_tasks_renewal_energy = 0.6mJ（task_3的1ms能耗）
- current_energy = 2.4mJ
- 判定：2.4mJ >= 0.6mJ ✅ 批准继续

**但这是为1ms做的决策**，实际上task_3从8ms运行到14ms（6ms），消耗了3.6mJ，远超2.4mJ的剩余能量。

**核心矛盾**：
- 批量调度的"门槛检查"是per-tick的（每1ms检查一次）
- 但能量检查事件也是每1ms扣除一次
- 导致在能量不足时，任务已经多运行了多个tick

## 结论

本次修复实现了BTIE的核心改进：
1. ✅ **能量不足时立即中断任务**（不再是自然结束）
2. ✅ **正确实现"全无"原则**（强制中断所有运行任务）
3. ⚠️ **中断时机仍需优化**（14ms vs 期望的12ms）

要实现完全正确的12ms中断，需要进一步改进批量调度的能量判断逻辑，使其更准确地预测任务执行期间的能量消耗。

## 测试文件
- 修复前追踪：`test_results/btie_final_test/btie_12mj_1000ms_fixed.json`
- 修复后追踪：`test_results/btie_final_test/btie_12mj_FIXED.json`
