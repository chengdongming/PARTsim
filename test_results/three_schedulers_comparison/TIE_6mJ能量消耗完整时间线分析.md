# TIE 6mJ场景 - 能量消耗完整时间线分析

## 📋 测试配置

### 初始能量
```
初始能量: 0.006000 J = 6.0 mJ
```

### 任务参数
```
task_1: WCET=5ms,  周期=20ms, 每ms能耗=0.6mJ, 总能耗=3.0mJ
task_2: WCET=8ms,  周期=30ms, 每ms能耗=0.6mJ, 总能耗=4.8mJ
task_3: WCET=10ms, 周期=40ms, 每ms能耗=0.6mJ, 总能耗=6.0mJ
task_4: WCET=12ms, 周期=50ms, 每ms能耗=0.6mJ, 总能耗=7.2mJ

CPU配置: 3核 (可同时执行3个任务)
每ms能耗(3任务并行): 3 × 0.6mJ = 1.8mJ
```

---

## ⏱️ 完整时间线

### Time=0ms: 任务到达和调度

**Trace事件**:
```json
{"time": "0", "event_type": "arrival", "task_name": "task_1"}
{"time": "0", "event_type": "arrival", "task_name": "task_2"}
{"time": "0", "event_type": "arrival", "task_name": "task_3"}
{"time": "0", "event_type": "arrival", "task_name": "task_4"}
{"time": "0", "event_type": "scheduled", "task_name": "task_1"}  ← 调度到CPU
{"time": "0", "event_type": "scheduled", "task_name": "task_2"}  ← 调度到CPU
{"time": "0", "event_type": "scheduled", "task_name": "task_3"}  ← 调度到CPU
```

**能量状态**:
```
当前能量: 6.0 mJ
运行中任务: task_1, task_2, task_3 (3个)
```

**分析**:
- ✅ 能量充足，3个任务成功调度
- task_4留在就绪队列（没有空闲CPU）

---

### Time=1ms: 第1次Tick事件

**能量扣除日志**:
```
⚡ [TIE] Tick事件: 扣除运行中任务能量 1.800000 mJ，6.000000 mJ → 4.200000 mJ (3 个任务)
```

**能量计算**:
```
扣除能量 = 3任务 × 0.6mJ/ms × 1ms = 1.8mJ
剩余能量 = 6.0mJ - 1.8mJ = 4.2mJ ✅
```

**任务执行状态**:
```
task_1: 已执行 1ms (还需 4ms)
task_2: 已执行 1ms (还需 7ms)
task_3: 已执行 1ms (还需 9ms)
```

**累计能量消耗**:
```
已消耗: 1.8mJ
理论完成task_1需要: 3.0mJ (已消耗 60%)
理论完成task_2需要: 4.8mJ (已消耗 38%)
理论完成task_3需要: 6.0mJ (已消耗 30%)
```

---

### Time=2ms: 第2次Tick事件

**能量扣除日志**:
```
⚡ [TIE] Tick事件: 扣除运行中任务能量 1.800000 mJ，4.200000 mJ → 2.400000 mJ (3 个任务)
```

**能量计算**:
```
扣除能量 = 3任务 × 0.6mJ/ms × 1ms = 1.8mJ
剩余能量 = 4.2mJ - 1.8mJ = 2.4mJ ✅
```

**任务执行状态**:
```
task_1: 已执行 2ms (还需 3ms)
task_2: 已执行 2ms (还需 6ms)
task_3: 已执行 2ms (还需 8ms)
```

**累计能量消耗**:
```
已消耗: 1.8mJ + 1.8mJ = 3.6mJ
理论完成task_1需要: 3.0mJ (已消耗超过需求!) ⚠️
理论完成task_2需要: 4.8mJ (已消耗 75%)
理论完成task_3需要: 6.0mJ (已消耗 60%)
```

**关键问题**:
- ❌ 已经消耗了 3.6mJ，但task_1只需要 3.0mJ
- ❌ 能量管理没有检查单个任务的总能耗
- ❌ 继续执行task_1会浪费能量

---

### Time=3ms: 第3次Tick事件

**能量扣除日志**:
```
⚡ [TIE] Tick事件: 扣除运行中任务能量 1.800000 mJ，2.400000 mJ → 0.600000 mJ (3 个任务)
```

**能量计算**:
```
扣除能量 = 3任务 × 0.6mJ/ms × 1ms = 1.8mJ
剩余能量 = 2.4mJ - 1.8mJ = 0.6mJ ✅
```

**任务执行状态**:
```
task_1: 已执行 3ms (还需 2ms)
task_2: 已执行 3ms (还需 5ms)
task_3: 已执行 3ms (还需 7ms)
```

**累计能量消耗**:
```
已消耗: 1.8mJ + 1.8mJ + 1.8mJ = 5.4mJ
剩余能量: 0.6mJ
```

**关键问题**:
- ❌ 已经消耗了 5.4mJ，超过task_1的 3.0mJ需求
- ❌ 剩余 0.6mJ 不够任何1ms的执行（需要 1.8mJ）
- ❌ 能量即将耗尽，但任务仍在执行

---

### Time=4ms: 第4次Tick事件 - 能量耗尽！

**能量扣除日志**:
```
⚡ [TIE] Tick事件: 扣除运行中任务能量 0.600000 mJ，0.600000 mJ → -0.000000 mJ (3 个任务)
```

**能量计算**:
```
扣除能量 = 3任务 × 0.6mJ/ms × 1ms = 1.8mJ
但剩余能量只有: 0.6mJ ❌

实际扣除: 0.6mJ (浮点数精度问题显示为 -0.000000 mJ)
剩余能量: -0.0mJ (违反物理定律!) ❌❌❌
```

**能量不足警告日志**:
```
⚡ [TIE] 任务能量不足，将中断: task_3 需要1ms=0.000600J 当前能量=-0.000000J
🛑 [TIE] 中断任务（能量不足）: task_3
```

**任务执行状态**:
```
task_1: 已执行 4ms (还需 1ms) ← 继续执行
task_2: 已执行 4ms (还需 4ms) ← 继续执行
task_3: 已执行 4ms (还需 6ms) ← 被中断 (descheduled)
```

**累计能量消耗**:
```
已消耗: 5.4mJ + 0.6mJ = 6.0mJ (全部初始能量!)
剩余能量: -0.0mJ (负数!) ❌
```

---

### Time=5ms: task_1完成

**Trace事件**:
```json
{"time": "5", "event_type": "end_instance", "task_name": "task_1"}
```

**任务执行状态**:
```
task_1: ✅ 完成 (执行了 5ms)
实际能耗: 5ms × 0.6mJ/ms = 3.0mJ

task_2: 继续执行中...
task_3: 已中断 (time=9ms会descheduled)
```

**关键问题**:
- ❌ task_1完成时已经消耗了全部 6.0mJ 能量
- ❌ 但能量管理没有阻止task_2继续执行
- ❌ task_2仍在CPU上执行，尽管能量已经耗尽

---

### Time=8ms: task_2完成

**Trace事件**:
```json
{"time": "8", "event_type": "end_instance", "task_name": "task_2"}
```

**任务执行状态**:
```
task_2: ✅ 完成 (执行了 8ms)
实际能耗: 8ms × 0.6mJ/ms = 4.8mJ

累计能耗: 3.0mJ (task_1) + 4.8mJ (task_2) = 7.8mJ
初始能量: 6.0mJ
能量超支: +1.8mJ (30%) ❌❌❌
```

**物理上不可能！**
- 任务执行了 8ms，消耗了 4.8mJ
- 但初始能量只有 6.0mJ
- task_1已经消耗了 3.0mJ
- 剩余能量应该是 3.0mJ，不够task_2的 4.8mJ
- **但task_2仍然执行完成了！**

---

### Time=9ms: task_3被解调度

**Trace事件**:
```json
{"time": "9", "event_type": "descheduled", "task_name": "task_3"}
```

**任务执行状态**:
```
task_3: ❌ 被解调度 (执行了 9ms，未完成)
实际能耗: 9ms × 0.6mJ/ms = 5.4mJ

如果完成需要: 10ms × 0.6mJ/ms = 6.0mJ
```

**统计结果**:
```
完成任务数: 2 (task_1, task_2)
总消耗能量: 6.0mJ (日志记录)
实际能耗: 3.0mJ + 4.8mJ + 5.4mJ = 13.2mJ
能量超支: +7.2mJ (120%) ❌❌❌
```

---

## 🔍 问题诊断

### 问题1: 能量管理是"事后会计"

**时间线分析**:
```
Time=0ms:  调度task_1, task_2, task_3
Time=1ms:  Tick事件，扣除1.8mJ ← 任务已经执行了1ms!
Time=2ms:  Tick事件，扣除1.8mJ ← 任务已经执行了1ms!
Time=3ms:  Tick事件，扣除1.8mJ ← 任务已经执行了1ms!
Time=4ms:  Tick事件，扣除0.6mJ ← 任务已经执行了1ms!
           能量耗尽，中断task_3 ← 但task_1, task_2继续执行!
Time=5ms:  task_1完成 ← 继续执行，尽管能量已耗尽
Time=8ms:  task_2完成 ← 继续执行，尽管能量已耗尽
Time=9ms:  task_3被解调度 ← 太晚了，已经执行了9ms
```

**根本问题**:
- 能量扣除在Tick事件中（事后）
- 任务执行在CPU上（事前）
- **能量扣除不控制任务执行！**

### 问题2: 只检查1ms能耗

**能量检查逻辑**:
```cpp
// ❌ 错误：只检查1ms能量
double unit_energy = calculateUnitEnergyForTask(task);  // 0.6mJ (1ms)
if (_current_energy >= unit_energy) {
    调度任务;  // 任务会执行10ms，但只检查了1ms！
}
```

**应该检查**:
```cpp
// ✅ 正确：检查全部WCET
double total_energy = unit_energy * wcet;  // 0.6mJ × 10ms = 6.0mJ
if (_current_energy >= total_energy) {
    调度任务;
} else {
    能量不足，不调度;
}
```

### 问题3: 能量变成负数

**日志证据**:
```
Time=4ms: 剩余能量 = -0.000000 mJ ❌
```

**原因**:
- EPSILON精度问题: `_current_energy >= energy - EPSILON`
- 允许能量略微超支
- 导致浮点数精度问题，显示为负数

---

## 📊 能量消耗总结

### 理论上应该发生什么

**初始能量**: 6.0mJ

**能量预算分配**:
```
选项1: 只调度task_1
  - 能量需求: 3.0mJ
  - 剩余能量: 3.0mJ
  - ✅ 可行

选项2: 调度task_1 + 部分task_2
  - task_1能量: 3.0mJ
  - task_2能量(5ms): 3.0mJ
  - 总计: 6.0mJ
  - ✅ 刚好可行

选项3: 调度task_1 + 完整task_2
  - task_1能量: 3.0mJ
  - task_2能量: 4.8mJ
  - 总计: 7.8mJ
  - ❌ 超出能量预算！
```

### 实际发生了什么

```
Time=0ms:  调度3个任务 (task_1, task_2, task_3)
Time=1ms:  消耗 1.8mJ，剩余 4.2mJ
Time=2ms:  消耗 1.8mJ，剩余 2.4mJ
Time=3ms:  消耗 1.8mJ，剩余 0.6mJ
Time=4ms:  消耗 0.6mJ，剩余 -0.0mJ ← 能量耗尽!
Time=5ms:  task_1完成 (消耗 3.0mJ)
Time=8ms:  task_2完成 (消耗 4.8mJ) ← 超支!
Time=9ms:  task_3中断 (消耗 5.4mJ) ← 超支!

总消耗: 3.0mJ + 4.8mJ + 5.4mJ = 13.2mJ
初始能量: 6.0mJ
能量超支: +7.2mJ (+120%) ❌❌❌
```

---

## 🎯 结论

### 核心问题

**能量管理完全不工作！**

1. ✅ 任务成功调度 (time=0ms)
2. ✅ 任务在CPU上执行 (time=0-9ms)
3. ✅ Tick事件扣除能量 (time=1-4ms)
4. ❌ 能量扣除不控制任务执行
5. ❌ 能量耗尽后任务仍继续执行
6. ❌ 最终能量超支120%

### 为什么BTIE比TIE多1个任务？

**不是设计优势！而是统计口径不同！**

- **TIE**: task_3被"解调度"(descheduled) → 不算完成 → 2个任务
- **BTIE**: task_3被"强制结束"但仍触发onTaskEnd → 算完成 → 3个任务

**实际上两者都执行了超过能量预算的工作量！**

---

**分析时间**: 2026-01-28
**数据来源**:
- Trace: tie_0.006J_trace.json
- 日志: tie_0.006J.log
- 配置: system_3core_tie_0.006J.yml
