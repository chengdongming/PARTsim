# TIE vs TGF 调度算法差异测试

## 🔴 重要更新：Suspend Bug修复（2026-01-24）

**发现并修复了MRTKernel::suspend()的严重bug：**

- **问题：** `suspend()` 错误地调用了 `onTaskEnd()`，导致被中断的任务被永久终止
- **影响：** 任务因能量不足中断后，剩余执行时间被丢弃，无法等待能量恢复后继续执行
- **修复：** 改为调用 `insert()` 将任务重新插入到就绪队列，保留剩余执行时间
- **详情：** [SUSPEND_BUG_FIX.md](./SUSPEND_BUG_FIX.md)

**修复前后的行为变化：**
- **修复前（Bug）：** task_1,2中断后被终止 → task_3,4被调度 → 4个任务被错误"完成"
- **修复后（正确）：** task_1,2中断后重新插入队列 → 保留剩余执行时间 → 等待能量恢复

**注意：** 修复后，以下测试结果基于修复后的正确行为。

---

## 📊 最新测试结果（2026-01-24更新）

### 测试1：高优先级任务从未执行（展示算法差异）

**配置：** 初始能量0.15mJ，4个任务（2个高能耗encrypt + 2个低能耗control）

**结果：**
- **TIE:** 0个任务完成（高能耗任务能量不足，立即停止级联）
- **TGF:** 2个任务完成（跳过高能耗任务，调度低能耗task_3和task_4）

**测试文件：**
- [tasks_4tasks_difference.yml](./tasks_4tasks_difference.yml) - 任务配置
- [TIE_4tasks_trace.json](./TIE_4tasks_trace.json) - TIE追踪
- [TGF_4tasks_trace_fixed.json](./TGF_4tasks_trace_fixed.json) - TGF追踪（修复后）

### 测试2：高优先级任务执行后被中断（算法行为相同）

**配置：** 初始能量1.6mJ，4个任务（高��耗任务执行1ms后中断）

**结果：**
- **TIE:** 4个任务完成（高能耗任务中断后，低能耗任务被调度）
- **TGF:** 4个任务完成（相同行为）

**为什么相同？** 关键发现：当任务被中断时，`suspend()` → `extract()` 会将任务从就绪队列中移除，使得低优先级任务可以被TIE访问到。

**测试文件：**
- [tasks_execute_then_interrupt.yml](./tasks_execute_then_interrupt.yml) - 任务配置
- [TIE_interrupt_trace.json](./TIE_interrupt_trace.json) - TIE追踪
- [TGF_interrupt_trace.json](./TGF_interrupt_trace.json) - TGF追踪

### 详细分析文档

- [ALGORITHM_DIFFERENCE_SUMMARY.md](./ALGORITHM_DIFFERENCE_SUMMARY.md) - 算法差异详细分析
- [INTERRUPT_TEST_ANALYSIS.md](./INTERRUPT_TEST_ANALYSIS.md) - 中断场景分析

---

## ⚠️ 重要说明（历史问题已修复）

### Suspend Bug修复（2026-01-24）

**严重bug：** `MRTKernel::suspend()` 错误地调用了 `onTaskEnd()`，导致被中断的任务被永久终止。

**问题：**
- 任务因能量不足被中断时，`suspend()` → `onTaskEnd()` 会永久移除任务
- 剩余执行时间被丢弃，能量账户被清理
- 任务无法等待能量恢复后继续执行

**修复：**
- 改为 `suspend()` → `insert()`，将任务重新插入到就绪队列
- 保留剩余执行时间，等待能量恢复
- **详细文档：** [SUSPEND_BUG_FIX.md](./SUSPEND_BUG_FIX.md)

**影响：**
- 修复前：任务中断后被终止，错误的"完成"统计
- 修复后：任务中断后保留在队列中，等待能量恢复（正确行为）

### 历史问题已修复

之前测试中观察到的"TGF能完成4个任务，TIE只能完成3个"的差异是由于**TGF能量会计bug**导致的，不是算法设计上的差异。

**Bug描述：**
- 旧版TGF在`getTaskN()`中跳过了运行中任务的能量计数
- 导致允许能量透支（总消耗1.2mJ > 可用能量0.8mJ）
- 这违反了能量安全约束

**修复内容：**
- TGF现在正确计入运行中任务的能量消耗（使用`_dispatching_tasks_total_energy`）
- 与TIE保持一致的能量会计机制
- 确保能量安全：不调度超出当前可用能量的任务

## 测试目标

通过设计能量临界场景，对比TIE（保守）和TGF（贪心）在能量不足时的不同决策行为。

## 核心差异

### TIE (Tick-based Instant Energy-aware) - 保守策略

```cpp
if (available_energy < unit_energy) {
    return nullptr;  // ⭐ 立即停止级联
}
```

**行为：** 能量不足时立即停止级联，不再检查后续任务

### TGF (Tick-based Greedy First) - 贪心策略

```cpp
if (available_energy < unit_energy) {
    // ⭐ 跳过当前任务，继续检查后续任务
    ready_index++;
    skipped_energy_insufficient = true;

    // 贪心策略：继续查找队列中是否有能量足够的后续任务
    for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
        AbsRTTask *next_task = _ready_queue[j];
        double next_unit_energy = calculateUnitEnergyForTask(next_task);
        double next_available = _current_energy - _dispatching_tasks_total_energy;

        if (next_available >= next_unit_energy - EPSILON) {
            // 找到能量足够的后续任务，调度它！
            return next_task;
        }
    }

    // 没有找到能量足够的任务
    return nullptr;
}
```

**行为：** 能量不足时跳过当前任务，继续检查后续任务，充分利用CPU

**关键点：**
- TGF的贪心策略体现在：**跳过能量不足的任务，继续检查后续任务**
- 这是在**就绪队列内部**的贪心搜索，不是能量透支

## 测试配置

**系统配置：**
- CPU核心：2核
- 初始能量：8mJ
- 时间：0:00（无太阳能）

**任务集：**
| 任务 | 周期 | WCET | 优先级 | 1ms能耗 |
|------|------|------|--------|---------|
| task_1 | 20ms | 15ms | 最高 | 0.6mJ |
| task_2 | 30ms | 3ms | 中 | 0.6mJ |
| task_3 | 40ms | 3ms | 中低 | 0.6mJ |
| task_4 | 50ms | 3ms | 最低 | 0.6mJ |

**能耗说明：**
- 所有任务的每ms能耗都是0.6mJ（在energy_config中统一定义）
- 能量检查是逐ms进行的，不是调度时检查总能量

## 修复后的测试结果

### 统计对比

| 指标 | TIE | TGF | 差异 |
|------|-----|-----|------|
| 任务完成数 | **3个** | **3个** | ✅ **相同** |
| 能量消耗 | 7.8mJ | 7.8mJ | 相同 |
| Deadline Miss | 1个(task_1) | 1个(task_1) | 相同 |

### 为什么现在相同？

**关键原因：** 所有任务的每ms能耗都是相同的（0.6mJ）

**6ms时的状态分析：**
```
当前能量：0.8mJ
运行中任务：task_1 (0.6mJ/ms, 已执行6ms，还需9ms)
就绪队列：[task_4] (task_3刚结束)

TIE getTaskN(1)：
  - 检查task_1（运行中）：续期，计入能量
  - 可用能量 = 0.8mJ - 0.6mJ = 0.2mJ
  - 检查task_4：需要0.6mJ，但只有0.2mJ
  - 能量不足，停止级联
  - 返回 nullptr ❌

TGF getTaskN(1)：
  - 检查task_1（运行中）：续期，计入能量
  - 可用能量 = 0.8mJ - 0.6mJ = 0.2mJ
  - 检查task_4：需要0.6mJ，但只有0.2mJ
  - 跳过task_4
  - 继续搜索后续任务：没有更多任务了
  - 返回 nullptr ❌

结果：两个算法都没有调度task_4（能量安全！）
```

**旧版（有bug）的行为：**
```
TGF getTaskN(1)：
  - 检查task_1（运行中）：跳过！能量未计入 ❌ BUG
  - 当前能量 = 0.8mJ
  - 检查task_4：需要0.6mJ，有0.8mJ
  - 调度task_4 ✅
  - 总消耗 = 0.6mJ(task_1) + 0.6mJ(task_4) = 1.2mJ > 0.8mJ
  - 能量透支！❌ BUG
```

### Trace对比

**TIE Trace（修复后）：**
```json
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_1"}
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "end_instance", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "scheduled", "task_name" : "task_3"}
{ "time" : "6", "event_type" : "end_instance", "task_name" : "task_3"}
// task_4未被调度（能量不足）
{ "time" : "7", "event_type" : "descheduled", "task_name" : "task_1"}
```

**TGF Trace（修复后）：**
```json
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_1"}
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "end_instance", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "scheduled", "task_name" : "task_3"}
{ "time" : "6", "event_type" : "end_instance", "task_name" : "task_3"}
// task_4未被调度（能量不足）
{ "time" : "7", "event_type" : "descheduled", "task_name" : "task_1"}
```

**结果相同：** 两者都只完成了3个任务（task_1未完成，deadline miss）

## TGF的贪心策略何时生效？

TGF的贪心策略（跳过能量不足的任务，继续检查后续任务）在以下场景中会有优势：

**场景示例：**
```
就绪队列：[task_A(高优先级, 大能耗), task_B(中优先级, 小能耗), task_C(低优先级, 小能耗)]
当前能量：5mJ
task_A每ms能耗：3mJ
task_B每ms能耗：0.5mJ
task_C每ms能耗：0.5mJ

TIE行为：
  - 检查task_A：需要3mJ，有5mJ，调度 ✓
  - 能量剩余：5mJ - 3mJ = 2mJ
  - 检查task_B：需要0.5mJ，有2mJ，调度 ✓
  - 能量剩余：2mJ - 0.5mJ = 1.5mJ
  - 检查task_C：需要0.5mJ，有1.5mJ，调度 ✓
  - 结果：调度3个任务

TGF行为（相同）：
  - 检查task_A：需要3mJ，有5mJ，调度 ✓
  - 检查task_B：需要0.5mJ，有2mJ，调度 ✓
  - 检查task_C：需要0.5mJ，有1.5mJ，调度 ✓
  - 结果：调度3个任务
```

**关键差异场景（需要不同能耗）：**
```
就绪队列：[task_A(高优先级), task_B(中优先级)]
当前能量：1mJ
task_A每ms能耗：1.5mJ（能量不足）
task_B每ms能耗：0.5mJ（能量充足）

TIE行为：
  - 检查task_A：需要1.5mJ，只有1mJ，能量不足
  - 停止级联 ❌
  - 结果：0个任务被调度

TGF行为：
  - 检查task_A：需要1.5mJ，只有1mJ，能量不足
  - 跳过task_A
  - 检查task_B：需要0.5mJ，有1mJ，调度 ✓
  - 结果：1个任务被调度
```

**注意：** 当前系统中所有任务的每ms能耗都相同（0.6mJ），所以上述差异场景不会在现有测试中出现。要展示TGF的贪心优势，需要：
1. 不同任务有不同的每ms能耗（需要修改energy_config）
2. 或创建其他测试场景

## 结论

### 修复确认 ✅

1. **能量会计bug已修复：** TGF现在正确计入所有任务的能量消耗
2. **TGF的贪心策略已实现：** 跳过能量不足的任务，继续检查后续任务（代码级差异）
3. **测试结果正确：** 两个算法在能量安全的前提下表现一致

### 修复历史

- **commit 62ac384** (2026-01-24): "修复能量感知调度器的关键bug并改进功能"
  - 在TGF中添加`_dispatching_tasks_total_energy`和`_counted_tasks_in_dispatch`
  - 正确计入运行中任务的能量消耗
  - 实现与TIE一致的能量会计机制

### 算法特性对比

| 维度 | TIE | TGF |
|------|-----|-----|
| 能量会计 | ✅ 正确 | ✅ 正确（已修复）|
| 贪心搜索 | ❌ 保守 | ✅ 跳过不足任务，继续搜索 |
| CPU利用率 | 较低（保守） | 较高（贪心）|
| 代码级差异 | 立即停止级联 | 继续检查后续任务 |
| 能量安全性 | ✅ 强 | ✅ 强（修复后）|

## 测试文件

- [tasks_critical_energy.yml](tasks_critical_energy.yml) - 任务配置
- [system_TIE.yml](system_TIE.yml) - TIE系统配置
- [system_TGF.yml](system_TGF.yml) - TGF系统配置
- [TIE_trace.json](TIE_trace.json) - TIE追踪数据（修复后）
- [TGF_trace.json](TGF_trace.json) - TGF追踪数据（修复后）

## 生成命令

```bash
# TIE测试
./build/rtsim/rtsim system_TIE.yml tasks_critical_energy.yml 50 -t TIE_trace.json

# TGF测试
./build/rtsim/rtsim system_TGF.yml tasks_critical_energy.yml 50 -t TGF_trace.json
```

## 详细日志分析

查看TGF的贪心策略日志（6ms时）：
```bash
./build/rtsim/rtsim system_TGF.yml tasks_critical_energy.yml 10 2>&1 | grep -A5 "6ms.*getTaskN"
```

预期输出：
```
⚠️ [TGF] 任务能量不足，跳过（贪心策略） 任务=task_4 需要1ms=0.000600J 已调度能耗=0.000600J 剩余=0.000200J
⚠️ [TGF] 贪心策略：未找到能量足够的任务
getTaskN(1) = nullptr
```

这证明TGF的贪心策略（跳过task_4，继续搜索）正在工作，只是后续没有能量充足的任务了。

---

## 测试场景汇总

| 场景 | 初始能量 | TIE结果 | TGF结果 | 差异? | 说明 |
|------|---------|---------|---------|-------|------|
| **4tasks差异测试** | 0.15mJ | 0任务 | 2任务 | ✅ **是** | 高能耗任务从未运行，TIE停止级联，TGF跳过并调度低能耗任务 |
| **Execute-then-Interrupt** | 1.6mJ | 4任务 | 4任务 | ❌ 否 | 高能耗任务中断后被extract()，TIE也能访问低能耗任务 |
| **原始能量临界测试** | 8mJ | 3任务 | 3任务 | ❌ 否 | 所有任务能耗相同，算法行为一致 |

### 关键结论

1. **TGF的贪心优势场景：** 当高优先级任务能量不足且**从未执行**时，TGF能够跳过它们并调度低优先级任务，而TIE会停止级联。

2. **TGF和TIE行为相同场景：**
   - 所有任务能耗相同时
   - 高优先级任务执行后被中断（被extract()移出队列）
   - 能量充足可以调度所有任务时

3. **实现细节：** 任务中断时调用`suspend()` → `extract()`，会暂时将任务从就绪队列中移除，这使得TIE也能访问到后续的低优先级任务。

### 代码位置参考

- **TIE保守策略：** [librtsim/scheduler/gpfp_tie_scheduler.cpp:605-612](../../librtsim/scheduler/gpfp_tie_scheduler.cpp#L605-L612)
- **TGF贪心策略：** [librtsim/scheduler/gpfp_tgf_scheduler.cpp:542-579](../../librtsim/scheduler/gpfp_tgf_scheduler.cpp#L542-L579)
- **多核调度修复：** [librtsim/scheduler/gpfp_tgf_scheduler.cpp:552-560](../../librtsim/scheduler/gpfp_tgf_scheduler.cpp#L552-L560)（防止重复调度同一任务）
- **任务中断机制：** [librtsim/scheduler/gpfp_tie_scheduler.cpp:920-934](../../librtsim/scheduler/gpfp_tie_scheduler.cpp#L920-L934)
