# ALAP-Sync 完成率低的原因分析

## 问题概述

ALAP-Sync的任务完成数仅为40个，而ALAP-Block和ALAP-NonBlock分别为77和76个，差距巨大。

通过追踪文件分析，发现ALAP-Sync有**79个deadline miss**（几乎是其他算法的2倍），而Block和NonBlock分别只有42和43个���

---

## 核心问题：批量调度导致的"调度延迟"和"任务饥饿"

### 1. 调度延迟问题

#### Task_Mid_B (arrival=0) 对比：

**ALAP-Block:**
- t=48ms: 首次调度
- t=120ms: 完成 (execution_time=72ms, 完整执行WCET)

**ALAP-Sync:**
- t=50ms: 首次调度 (延迟2ms)
- t=120ms: deadline miss (只执行了70ms，还差2ms)

**原因**: ALAP-Sync需要等待"批量调度决策"，在tick边界才触发调度，导致任务首次调度延迟。

---

### 2. 任务饥饿问题

#### Task_Assassin_Hungry (arrival=50) 对比：

**ALAP-Block:**
- t=80ms: 调度
- t=100ms: 完成 (execution_time=20ms)

**ALAP-NonBlock:**
- t=80ms: 调度
- t=100ms: 完成 (execution_time=20ms)

**ALAP-Sync:**
- ❌ **完全未调度**
- t=100ms: 直接 deadline miss + kill

**原因**: ALAP-Sync的"全有或全无"批量策略，如果当前批次能量不足，整个批次都被拒绝，导致高优先级任务也无法调度。

---

### 3. 批量阻塞问题

在ALAP-Sync中，当能量不足以支持整个批次时：
- **所有任务都被阻塞**，包括能量充足的低优先级任务
- 这导致CPU资源浪费
- 低优先级任务饥饿

而在ALAP-NonBlock中：
- 高优先级任务能量不足时，会跳过并继续调度低优先级任务
- 提高了资源利用率

---

## 具体案例：t=50时刻的调度差异

### ALAP-Block:
```
t=50ms:
├─ Task_Assassin_Hungry (arrival=50) 到达
├─ Task_Mid_A (arrival=0) 调度 ← 立即调度
└─ Task_Mid_B (arrival=0) 已在运行

结果: 高优先级任务立即抢占，充分利用CPU
```

### ALAP-Sync:
```
t=50ms:
├─ Task_Assassin_Hungry (arrival=50) 到达
├─ Task_Mid_A (arrival=0) 调度 ← 批量调度
└─ Task_Mid_B (arrival=0) 调度 ← 批量调度
但在t=50-100之间，Task_Assassin_Hungry (arrival=50) 未被调度

原因: 批量调度在tick边界决策，新到达的高优先级任务需要等待下一个tick
```

---

## 根本原因总结

### ALAP-Sync的设计缺陷：

1. **批量调度的tick边界限制**
   - 只在每个tick边界进行批量调度决策
   - 新到达的高优先级任务需要等待下一个tick
   - 导致调度延迟

2. **"全有或全无"的阻塞策略**
   - 能量不足时，整个批次都被拒绝
   - 无法像NonBlock那样跳过高优先级任务，调度低优先级任务
   - 导致资源浪费和任务饥饿

3. **任务完成数统计方式不同**
   - ALAP-Sync统计的是"新调度到CPU的任务数" (40)
   - 实际end_instance事件有43个
   - 但由于大量deadline miss，实际有效完成数远低于Block/NonBlock

---

## 数据对比

| 指标 | ALAP-Block | ALAP-NonBlock | ALAP-Sync |
|------|-----------|---------------|-----------|
| 任务完成数 | 77 | 76 | 40 |
| end_instance事件 | 77 | 76 | 43 |
| deadline miss | 42 | 43 | 79 |
| kill事件 | 42 | 43 | 79 |
| **miss率** | **35%** | **36%** | **65%** |

---

## 结论

**ALAP-Sync的低完成率是由其批量调度机制导致的：**

1. **调度延迟**: tick边界调度导致新任务调度延迟
2. **批量阻塞**: "全有或全无"策略导致资源浪费
3. **任务饥饿**: 高优先级任务阻塞时，低优先级任务也无法调度

这体现了"全员进退、同生共死"策略的**双刃剑特性**：
- ✅ 优点: 减少调度开销，批量决策效率高
- ❌ 缺点: 调度延迟高，任务饥饿严重

---

## 建议改进方向

1. **Mid-tick抢占**: 在tick之间允许高优先级任务抢占
2. **部分批量调度**: 允许批次中部分任务调度（类似NonBlock）
3. **动态批次大小**: 根据能量情况动态调整批次大小
