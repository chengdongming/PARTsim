# Suspend Bug 修复记录

## Bug 描述

**发现时间：** 2026-01-24
**严重程度：** 严重
**影响范围：** 所有能量感知调度器（TIE, TGF, BTIE）

### 问题描述

当任务因��量不足被中断时，`MRTKernel::suspend()` 错误地调用了 `onTaskEnd()`，导致：

1. ❌ 任务从就绪队列**永久移除**
2. ❌ 任务的剩余执行时间被丢弃
3. ❌ 能量账户被清理
4. ❌ 任务完成计数被错误增加
5. ❌ 任务实例被终止，无法等待能量恢复后继续执行

### 错误代码

**文件：** `librtsim/mrtkernel.cpp:205-224`

```cpp
void MRTKernel::suspend(AbsRTTask *task) {
    _sched->extract(task);
    CPU *p = getProcessor(task);
    if (p != nullptr) {
        task->deschedule();
        _m_currExe[p] = nullptr;
        _m_oldExe[task] = p;
        _m_dispatched[task] = nullptr;

        // ❌ BUG: onTaskEnd()会永久终止任务！
        _sched->onTaskEnd(task);

        dispatch(p);
    }
}
```

### 为什么这是Bug？

在能量采集的harvest-execution系统中：
- 任务因能量不足中断是**正常现象**
- 任务应该**保留剩余执行时间**
- 任务应该**等待能量恢复后继续执行**
- 不应该将中断误认为是任务完成

`onTaskEnd()` 的语义是"任务实例完成执行"，而不是"任务被暂时中断"。

## 修复方案

### 修复代码

```cpp
void MRTKernel::suspend(AbsRTTask *task) {
    _sched->extract(task);
    CPU *p = getProcessor(task);
    if (p != nullptr) {
        task->deschedule();
        _m_currExe[p] = nullptr;
        _m_oldExe[task] = p;
        _m_dispatched[task] = nullptr;

        // ⭐ BUG修复（2026-01-24）：suspend不应该调用onTaskEnd()
        // onTaskEnd()会永久移除任务、清理能量账户、增加完成计数
        // 对于能量不足的中断，任务应该保留剩余执行时间，等待能量恢复后继续执行
        // 正确做法：将任务重新插入到就绪队列，而不是终止它
        // _sched->onTaskEnd(task);  // ❌ 错误：这会终止任务实例

        // ✅ 修复：将任务重新插入到就绪队列
        // 这样任务会保留剩余执行时间，等待能量恢复后继续执行
        _sched->insert(task);

        std::cout << "[DEBUG] MRTKernel::suspend() - 任务已重新插入队列: "
                  << taskname(task) << " (剩余执行时间保留)" << std::endl;

        dispatch(p);
    }
}
```

### 修复说明

1. **移除 `onTaskEnd()` 调用**：避免任务被永久终止
2. **调用 `insert()`**：将任务重新插入到就绪队列
3. **保留剩余执行时间**：任务对象本身未被修改，剩余时间自动保留
4. **触发调度**：`dispatch(p)` 仍然会被调用，选择新任务运行

## 修复前后对比

### 测试场景

**配置：** 初始能量1.6mJ
- task_1, task_2：高能耗（0.75mJ/ms），运行5ms
- task_3, task_4：低能耗（0.05mJ/ms），运行3ms

### 修复前

**TIE：**
- 0ms：task_1, task_2 调度
- 1ms：task_1, task_2 能量不足 → suspend → **onTaskEnd()终止** ❌
- 1ms：task_3, task_4 被调度（task_1,2已不在队列）
- 2ms：task_3, task_4 能量耗尽
- **结果：** 4个任务被"完成"（task_1,2被错误终止）

**TGF：** 相同行为

### 修复后

**TIE：**
- 0ms：task_1, task_2 调度
- 1ms：task_1, task_2 能量不足 → suspend → **insert()重新插入队列** ✅
- 1ms：TIE检查task_1（队列首位）→ 能量不足 → **停止级联**
- **结果：** 0个任务完成，所有4个任务在队列中等待能量恢复

**TGF：**
- 0ms：task_1, task_2 调度
- 1ms：task_1, task_2 能量不足 → suspend → **insert()重新插入队列** ✅
- 1ms：TGF跳过task_1, task_2 → 调度task_3, task_4
- 2ms：task_3, task_4 能量耗尽 → suspend → **insert()重新插入队列** ✅
- **结果：** 所有4个任务在队列中，task_1,2执行了1ms，task_3,4执行了1ms

## 影响分析

### 正面影响

1. ✅ **正确的语义**：任务因能量不足中断时，保留剩余执行时间
2. ✅ **能量恢复可继续**：当能量恢复时，任务可以从断点继续执行
3. ✅ **正确的统计**：不会被错误地标记为"完成"
4. ✅ **符合harvest-execution模型**：任务应该等待能量恢复，而不是被终止

### 需要注意的变化

1. **TIE行为变化**：
   - 修复后，TIE在能量不足时会停止级联，所有任务在队列中等待
   - 这是**正确的保守行为**
   - 与修复前的"错误地调度低优先级任务"不同

2. **TGF行为保持**：
   - TGF的贪心策略仍然有效：跳过能量不足的任务，调度后续任务
   - 但现在所有被中断的任务都会保留在队列中

## 测试验证

### 测试1：基础中断场景

**命令：**
```bash
./build/rtsim/rtsim test_results/tie_tgf_difference/system_TIE_interrupt.yml \
                    test_results/tie_tgf_difference/tasks_execute_then_interrupt.yml \
                    10 -t TIE_interrupt_fixed_trace.json
```

**验证点：**
- ✅ 日志显示"任务已重新插入队列"
- ✅ task_1和task_2在队列中（未终止）
- ✅ 没有错误的"任务完成"日志

### 测试2：TGF贪心策略

**命令：**
```bash
./build/rtsim/rtsim test_results/tie_tgf_difference/system_TGF_interrupt.yml \
                    test_results/tie_tgf_difference/tasks_execute_then_interrupt.yml \
                    10 -t TGF_interrupt_fixed_trace.json
```

**验证点：**
- ✅ task_1,2被重新插入队列
- ✅ TGF跳过task_1,2，调度task_3,4
- ✅ task_3,4执行后被重新插入队列
- ✅ Trace显示正确的调度序列

## 相关文件

**修改的文件：**
- `librtsim/mrtkernel.cpp:205-224`

**测试文件：**
- `test_results/tie_tgf_difference/system_TIE_interrupt.yml`
- `test_results/tie_tgf_difference/system_TGF_interrupt.yml`
- `test_results/tie_tgf_difference/tasks_execute_then_interrupt.yml`
- `test_results/tie_tgf_difference/TIE_interrupt_fixed_trace.json`
- `test_results/tie_tgf_difference/TGF_interrupt_fixed_trace.json`

**分析文档：**
- `test_results/tie_tgf_difference/INTERRUPT_TEST_ANALYSIS.md`
- `test_results/tie_tgf_difference/ALGORITHM_DIFFERENCE_SUMMARY.md`

## 后续工作

1. ✅ 基础修复已完成
2. ⚠️ **需要考虑：** 如何处理长时间等待的任务？
   - 是否需要deadline检查？
   - 是否需要任务老化机制？
3. ⚠️ **需要测试：** 能量恢复场景
   - 当能量收集发生时，任务是否能正确恢复执行？
   - 剩余执行时间是否正确？

## 提交信息

```
fix: 修复suspend()错误调用onTaskEnd()导致任务被终止的bug

问题描述：
- 当任务因能量不足被中断时，suspend()调用onTaskEnd()
- onTaskEnd()会永久移除任务、清理能量账户、增加完成计数
- 这导致任务实例被终止，无法等待能量恢复后继续执行

修复方案：
- 移除suspend()中对onTaskEnd()的调用
- 改为调用insert()将任务重新插入到就绪队列
- 保留任务的剩余执行时间，等待能量恢复

影响：
- TIE: 能量不足时停止级联，所有任务在队列中等待
- TGF: 跳过能量不足的任务，调度后续任务
- 所有被中断的任务都保留在队列中，符合harvest-execution语义

测试：
- 修复后的trace显示任务被正确重新插入队列
- TIE和TGF的行为符合预期
```
