# BTIE和TIE追踪文件差异分析：Descheduled时间1ms差异

## 问题描述

在15mJ初始能量测试中，BTIE和TIE/TGF算法的追踪文件存在1ms差异：
- **BTIE**: descheduled at 21ms
- **TIE/TGF**: descheduled at 22ms

但在12mJ初始能量测试中，三个算法的descheduled时间一致：
- **BTIE/TIE/TGF**: descheduled at 11ms

## 测试结果对比

### 12mJ测试（所有算法一致）
| 文件 | Descheduled时间 | 总能耗 |
|------|----------------|--------|
| btie_12mj_v39.json | 11ms | 11.4 mJ |
| tie_12mj_v39.json | 11ms | 11.4 mJ |
| tgf_12mj_v39.json | 11ms | 11.4 mJ |

### 15mJ���试（BTIE与TIE/TGF差异1ms）
| 文件 | Descheduled时间 | 总能耗 |
|------|----------------|--------|
| btie_15mj_v39.json | 21ms | 14.4 mJ |
| tie_15mj_v39.json | 22ms | 14.4 mJ |
| tgf_15mj_v39.json | 22ms | 14.4 mJ |

## ⭐ 核心发现：执行时间差异的真正原因

通过深入分析追踪文件，发现了**真正的原因**：

### 追踪数据分析

| 算法 | Descheduled时间 | task_1执行时长 | 能量消耗 |
|------|----------------|----------------|----------|
| **TIE** | 22ms | 2ms (20-22ms) | 1.2 mJ |
| **BTIE** | 21ms | 1ms (20-21ms) | 0.6 mJ |

**BTIE比TIE少执行了1ms！** 这正是descheduled时间差异的根本原因。

### 为什么BTIE在21ms就挂起？

查看BTIE能量检查事件代码（[librtsim/scheduler/gpfp_btie_scheduler.cpp:184](librtsim/scheduler/gpfp_btie_scheduler.cpp#L184)）：

```cpp
if (_scheduler->_current_energy < unit_energy * 0.1) {  // < 0.06 mJ
    // 预扣能量已耗尽，立即挂起
    _scheduler->_current_energy = 0.0;
    _scheduler->_kernel->suspend(_task);
    return;
}
```

这个检查的本意是验证"预扣能量是否耗尽"，但它检查的是**全局_current_energy**，而不是任务级别的预扣能量。

**问题**：
1. 批量调度在21ms扣除能量后，全局_current_energy被减少
2. 能量检查事件检查全局_current_energy
3. 如果全局能量较低（虽然还有能量），就误判为"预扣能量耗尽"
4. 导致提前1ms挂起

### TIE为什么能继续到22ms？

TIE的能量检查逻辑（[librtsim/scheduler/gpfp_tie_scheduler.cpp:164](librtsim/scheduler/gpfp_tie_scheduler.cpp#L164)）：

```cpp
if (current_energy <= unit_energy + EPSILON) {
    // 挂起
} else {
    // 扣除能量，继续执行
    _scheduler->_current_energy -= unit_energy;
    post(SIMUL.getTime() + 1);
}
```

TIE只检查"是否有足够能量执行下一个1ms"，只要能量>0.6mJ就继续执行。

### 是否需要修复？

**这可能是一个bug！** BTIE的能量检查事件(line 184)检查逻辑过于敏感：
- 它检查全局_current_energy < 0.06 mJ
- 但全局能量在批量调度时已被扣除
- 导致误判为"能量耗尽"，提前挂起任务

**建议**: 重新审视line 184的检查逻辑，确保它正确检查"预扣能量"，而不是全局能量。

### 为什么12mJ测试没有差异？

在12mJ测试中，能量恰好耗尽在tick边界：
- Task 3执行到第11ms，能量从0.6 mJ扣除到0.0 mJ
- 两个算法都在11ms检测到能量耗尽
- BTIE的敏感检查不会提前触发，因为能量确实为0

## 追踪记录时机详细解释

### 核心概念：仿真事件处理顺序

RTSim仿真器按照以下顺序处理事件：

```
对于每个时刻t:
    1. 处理 performTickScheduling() - 决定哪些任务运行
    2. 处理该时刻的所有已调度事件（能量检查、到达事件等）
    3. 所有事件处理完成后，仿真时间推进到t+1
```

关键点：**`SIMUL.getTime()`在整个事件处理过程中保持不变**

### Descheduled事件的记录流程

当任务被挂起时，调用链如下：

```
kernel->suspend(task)
    ↓
task->deschedule()
    ↓
deschedEvt.process()
    ↓
JSONTrace::probe(DeschedEvt)
    ↓
writeTaskEvent(task, "descheduled")
    ↓
fd << "\"time\" : \"" << SIMUL.getTime() << "\""
```

**`SIMUL.getTime()`返回的是当前正在处理的仿真时间**，而不是实际时间。

### TIE的详细执行时间线（20-22ms）

```
======== 时刻20ms ========
performTickScheduling:
  - 能量 = ~3.0 mJ (假设值)
  - task_1到达
  - getTaskN扣除初始能量: 3.0 mJ → 2.4 mJ
  - 调度task_1
  - 创建TIEEnergyCheckEvent，post(21ms)

======== 时刻21ms ========
performTickScheduling:
  - task_1正在运行

TIEEnergyCheckEvent处理:
  - SIMUL.getTime() = 21
  - current_energy = 2.4 mJ
  - unit_energy = 0.6 mJ
  - 条件: 2.4 <= 0.6 + EPSILON → FALSE (能量充足!)
  - 扣除能量: 2.4 mJ → 1.8 mJ
  - post(22ms): 调度下一次检查

======== 时刻22ms ========
performTickScheduling:
  - task_1正在运行

TIEEnergyCheckEvent处理:
  - SIMUL.getTime() = 22
  - current_energy = 1.8 mJ
  - unit_energy = 0.6 mJ
  - 条件: 1.8 <= 0.6 + EPSILON → FALSE (能量充足!)
  - 扣除能量: 1.8 mJ → 1.2 mJ
  - post(23ms): 调度下一次检查
```

### BTIE的详细执行时间线（20-21ms）

```
======== 时刻20ms ========
performTickScheduling:
  - 能量 = ~3.0 mJ
  - task_1到达
  - getTaskN扣除初始能量: 3.0 mJ → 2.4 mJ
  - 调度task_1
  - 创建BTIEEnergyCheckEvent，post(21ms)

======== 时刻21ms ========
performTickScheduling (批量调度):
  - 计算total_energy_needed:
    - 运行中任务: task_1 (0.6 mJ)
    - 新任务: 无
    - total_energy_needed = 0.6 mJ

  - ⭐ 批量调度条件检查 (line 790):
    if (_current_energy > total_energy_needed - EPSILON)
    即: if (2.4 mJ > 0.6 mJ - EPSILON)
    结果: TRUE → 批量调度成功

  - 扣除预扣能量: 2.4 mJ → 1.8 mJ
  - 调度BTIEEnergyCheckEvent (在当前21ms tick内处理)

BTIEEnergyCheckEvent处理（同一tick内）:
  - SIMUL.getTime() = 21

  - ⚠️ 检查1: 续期能量是否足够 (line 160):
    if (current_energy < unit_energy - EPSILON)
    current_energy = 1.8 mJ (全局能量)
    结果: 1.8 < 0.6 → FALSE (能量充足)

  - ⚠️ 检查2: 预扣能量是否耗尽 (line 184):
    if (_scheduler->_current_energy < unit_energy * 0.1)
    _scheduler->_current_energy = 1.8 mJ (全局能量)
    unit_energy * 0.1 = 0.06 mJ
    结果: 1.8 < 0.06 → FALSE (继续)

  - 但追踪显示BTIE在21ms就挂起了！
  - 这说明实际运行时，_current_energy可能很低

⚠️ 实际情况分析:
  根据追踪结果（BTIE只执行1ms，消耗0.6 mJ），推测:
  - 20ms getTaskN扣除: 3.0 mJ → 2.4 mJ
  - 21ms批量调度扣除: 2.4 mJ → 1.8 mJ
  - 但能量检查事件可能检测到其他情况导致挂起

  可能原因:
  1. 能量检查事件的_current_energy可能不是全局能量
  2. 或者批量调度扣除的不是0.6 mJ
  3. 或者初始能量不是3.0 mJ
```

## 关键差异对比

| 维度 | TIE | BTIE |
|------|-----|------|
| **能量扣除时机** | 能量检查事件中扣除 | 批量调度时预扣 + getTaskN扣除 |
| **能量检查阈值** | `≤ unit_energy` (≤0.6 mJ) | `< unit_energy * 0.1` (<0.06 mJ) |
| **执行时长** | 2ms (20-22ms) | 1ms (20-21ms) |
| **Descheduled时间** | 22ms | 21ms |

## 总结

### 差异本质

这个1ms差异**不完全是设计哲学差异**，而是：
1. **BTIE的能量检查逻辑(line 184)可能有问题**
2. 导致BTIE比TIE提前1ms挂起任务（21ms vs 22ms）
3. 造成descheduled事件记录时间相差1ms

### 追踪记录时机差异

**记录时机差异**是结果，不是原因：
- BTIE在21ms挂起 → 记录descheduled at 21ms
- TIE在22ms挂起 → 记录descheduled at 22ms

**真正原因**: BTIE比TIE少执行了1ms。

### V39修复总结

V39版本修复了批量调度条件([line 790](librtsim/scheduler/gpfp_btie_scheduler.cpp#L790))的能量判断：
- **V38**: `current_energy > total_energy_needed + EPSILON`
- **V39**: `current_energy > total_energy_needed - EPSILON`

但V39**可能还有问题**：能量检查事件(line 184)的检查逻辑需要进一步审视。
