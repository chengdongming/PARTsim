# 三个调度器能量不足主动中断测试报告（修复版）

## 测试概述

**测试时间**：2026-01-23
**测试配置**：
- 系统配置：energy_interrupt_test/system_*.yml（三个调度器的配置）
- 任务配置：energy_interrupt_test/tasks.yml（2个高能耗任务）
- 初始能量：0.0015J（非常低的能量，确保快速触发中断）
- 能量恢复：禁用（enable_energy_recovery: false）
- 太阳能数据：禁用（use_real_solar_data: false）
- 测试持续时间：10ms

## 关键问题与修复

### 问题1：ConfigManager配置解析问题
**问题**：ConfigManager只解析了`scheduler_energy_model`部分，没有解析`energy_management`部分的配置（如初始能量、能量恢复设置）和调度器类型。

**修复**：在`librtsim/scheduler/config_manager.cpp`中添加了对`energy_management`和调度器类型的解析逻辑。

### 问题2：notify方法在任务到达时扣减能量
**问题**：TIE和BTIE调度器在`notify`方法中**在任务到达时就立即扣减1ms的能耗**，但此时任务甚至还没有被调度，导致能量在调度前就已经不足。

**修复**：修改TIE和BTIE调度器的`notify`方法，让它们在任务到达时只检查能量，不扣减能量。能量��该在任务调度时通过`getTaskN()`方法扣减。

### 问题3：TIE调度器的getTaskN()重复计数问题
**问题**：`_counted_tasks_in_dispatch`集合只在tick边界时重置，但调度器在同一个tick内会多次调用`getTaskN()`方法（比如在`onBeginDispatchMulti`阶段），导致能量被重复检查。

**修复**：在`getTaskN()`方法中添加逻辑，当n==0时（新的调度周期开始），重置`_dispatching_tasks_total_energy`和`_counted_tasks_in_dispatch`。

### 问题4：BTIE调度器使用任务总能耗而不是1ms能耗
**问题**：BTIE调度器使用`calculateTotalEnergyForTask(task)`计算任务的**总能耗**（整个任务WCET的能耗），而不是只计算1ms的能耗。例如，一个WCET=10ms的任务，总能耗是0.006J，但1ms的能耗只有0.0006J。

**修复**：修改BTIE调度器的批量能量检查逻辑，让它只检查1ms的能量需求，使用`calculateUnitEnergyForTask(task)`代替`calculateTotalEnergyForTask(task)`。

## 测试结果（修复后）

### 1. TIE调度器（gpfp_tie）

**跟踪文件**：[trace_tie_fixed2_raw.json](trace_tie_fixed2_raw.json)

**事件统计**：
- 任务到达：2个（task_high_energy_1、task_high_energy_2）
- 任务调度：2个
- 任务解调度：2个
- 总消耗能量：0.0012J
- 剩余能量：0.0003J

**执行时间**：2ms（每个任务执行1ms后能量耗尽）

**行为分析**：
TIE调度器成功调度并执行了两个任务，每个任务执行1ms后，由于能量不足触发了中断机制。能量不足主动中断功能正常工作！

### 2. BTIE调度器（gpfp_btie）

**跟踪文件**：[trace_btie_fixed_raw.json](trace_btie_fixed_raw.json)

**事件统计**：
- 任务到达：2个（task_high_energy_1、task_high_energy_2）
- 任务调度：2个
- 任务解调度：2个
- 总消耗能量：0.0012J
- 剩余能量：0.0003J

**执行时间**：2ms（每个任务执行1ms后能量耗尽）

**行为分析**：
BTIE调度器成功调度并执行了两个任务，每个任务执行1ms后，由于能量不足触发了中断机制。能量不足主动中断功能正常工作！

### 3. TGF调度器（gpfp_tgf）

**跟踪文件**：[trace_tgf.json](trace_tgf.json)

**事件统计**：
- 任务到达：2个（task_high_energy_1、task_high_energy_2）
- 任务调度：2个
- 任务解调度：2个
- 总消耗能量：0.0012J
- 剩余能量：0.0003J

**执行时间**：1ms（两个任务都只执行了1ms）

**行为分析**：
TGF调度器成功调度并执行了两个任务，两个任务在1ms后同时解调度，表明能量不足中断机制正常工作。

## 测试结果总结

| 调度器 | 任务调度数 | 任务中断数 | 总消耗能量 | 剩余能量 | 执行时间 | 中断行为 |
|--------|------------|------------|------------|----------|----------|----------|
| TIE    | 2          | 2          | 0.0012J   | 0.0003J  | 2ms      | 能量耗尽，任务中断 |
| BTIE   | 2          | 2          | 0.0012J   | 0.0003J  | 2ms      | 能量耗尽，任务中断 |
| TGF    | 2          | 2          | 0.0012J   | 0.0003J  | 1ms      | 能量耗尽，任务中断 |

## 结论

**三个调度器的能量不足主动中断机制都能正常工作！** ✅

修复后的测试结果证明：
1. TIE调度器：任务执行2ms后能量耗尽，触发中断
2. BTIE调度器：任务执行2ms后能量耗尽，触发中断
3. TGF调度器：任务执行1ms后能量耗尽，触发中断

所有调度器都能在运行时检查能量状态，能量不足时主动中断任务，避免了任务异常终止或系统崩溃。

## 测试文件列表

1. **系统配置文件**：
   - [system_tie.yml](system_tie.yml) - TIE调度器配置
   - [system_btie.yml](system_btie.yml) - BTIE调度器配置
   - [system_tgf.yml](system_tgf.yml) - TGF调度器配置

2. **任务配置文件**：
   - [tasks.yml](tasks.yml) - 测试任务集配置

3. **测试跟踪文件（修复后）**：
   - [trace_tie_fixed2_raw.json](trace_tie_fixed2_raw.json) - TIE调度器跟踪文件
   - [trace_btie_fixed_raw.json](trace_btie_fixed_raw.json) - BTIE调度器跟踪文件
   - [trace_tgf.json](trace_tgf.json) - TGF调度器跟踪文件

4. **修复文件**：
   - [librtsim/scheduler/config_manager.cpp](../librtsim/scheduler/config_manager.cpp) - 添加了对energy_management和调度器类型的解析
   - [librtsim/scheduler/gpfp_tie_scheduler.cpp](../librtsim/scheduler/gpfp_tie_scheduler.cpp) - 修复了notify方法和getTaskN()方法
   - [librtsim/scheduler/gpfp_btie_scheduler.cpp](../librtsim/scheduler/gpfp_btie_scheduler.cpp) - 修复了notify方法和批量能量检查逻辑

## 建议

1. **能量配置建议**：根据任务特性和能量约束调整初始能量和能量恢复设置
2. **调度器选择建议**：
   - TIE调度器：适合需要精确能量控制的场景
   - BTIE调度器：适合批量调度和能量感知的场景
   - TGF调度器：适合需要快速响应和能量高效利用的场景
3. **测试建议**：在实际部署前，使用不同的能量配置进行充分测试
