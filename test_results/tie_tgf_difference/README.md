# TIE vs TGF 调度算法差异测试

## 测试目标

通过设计能量临界场景，测试TIE（保守）���TGF（贪心）在能量不足时的不同决策行为。

## 测试配置

**系统配置：**
- CPU核心：2核
- 初始能量：8mJ
- 时间：0:00（无太阳能）

**任务集：**
| 任务 | 周期 | WCET | 优先级 | 1ms能耗 |
|------|------|------|--------|---------|
| task_1 | 20ms | 15ms | 最高 | ~1.2mJ |
| task_2 | 30ms | 3ms | 中 | ~0.36mJ |
| task_3 | 40ms | 3ms | 中低 | ~0.36mJ |
| task_4 | 50ms | 3ms | 最低 | ~0.36mJ |

**能耗估算：**
- task_1: 15ms × 1.2W = 18mJ（超过初始能量8mJ）
- task_2: 3ms × 1.2W = 3.6mJ
- task_3: 3ms × 1.2W = 3.6mJ
- task_4: 3ms × 1.2W = 3.6mJ

## 核心差异

### TIE (Tick-based Instant Energy-aware) - 保���策略

```cpp
if (available_energy < unit_energy) {
    return nullptr;  // ⭐ 立即停止级联
}
```

**行为：** 能量不足时立即停止级联，不再检查后续任务

### TGF (Tick-based Greedy First) - 贪心策略

```cpp
if (_current_energy < unit_energy) {
    continue;  // ⭐ 跳过当前任务，继续检查后续任务
}
```

**行为：** 能量不足时跳过当前任务，继续检查后续任务，充分利用CPU

## 测试结果

### 统计对比

| 指标 | TIE | TGF | 差异 |
|------|-----|-----|------|
| 任务完成数 | **3个** | **4个** | ✅ **TGF多完成1个任务** |
| 能量消耗 | 7.8mJ | 7.8mJ | 相同 |
| 能量不足跳过 | 1次 | 1次 | 相同 |
| Deadline Miss | 0 | 0 | 相同 |

### 调度序列对比

**TIE调度序列：**
```
0ms:  task_1 (高优先级，大能耗) - 调度
      task_2 (中优先级，小能耗) - 调度
      ↓
3ms:   task_1被中断（能量耗尽）
       task_3 (低优先级，小能耗) - 调度
       ↓
6ms:   能量不足，停止级联 ❌
       task_4未调度
```

**TGF调度序列：**
```
0ms:  task_1 (高优先级，大能耗) - 调度
      task_2 (中优先级，小能耗) - 调度
      ↓
3ms:   task_1被中断（能量耗尽）
       task_3 (低优先级，小能耗) - 调度
       ↓
6ms:   task_3完成
       task_4 (低优先级，小能耗) - 调度 ✅
       （TGF贪心：跳过能量不足的任务，继续调度）
```

## 详细Trace分析

### TIE Trace（关键片段）
```json
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_1"}
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "scheduled", "task_name" : "task_3"}
// task_4未被调度
```

### TGF Trace（关键片段）
```json
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_1"}
{ "time" : "0", "event_type" : "scheduled", "task_name" : "task_2"}
{ "time" : "3", "event_type" : "scheduled", "task_name" : "task_3"}
{ "time" : "6", "event_type" : "scheduled", "task_name" : "task_4"}  // ✅ 多调度一个
```

## 结论

### 场景适用性

**TIE（保守）适用于：**
- 能量极其受限的系统
- 需要严格保证能量充足性的场景
- 不愿意冒能量中断风险

**TGF（贪心）适用于：**
- 需要最大化CPU利用率的场景
- 可以接受部分任务运行时中断
- 追求更高的任务吞吐量

### 性能对比

| 维度 | TIE | TGF |
|------|-----|-----|
| CPU利用率 | 较低（保守） | **更高（贪心）** |
| 任务吞吐量 | 较低 | **更高** |
| 能量保证性 | 强 | 中等 |
| 运行时中断 | 少 | 可能较多 |

## 测试文件

- [tasks_critical_energy.yml](tasks_critical_energy.yml) - 任务配置
- [system_TIE.yml](system_TIE.yml) - TIE系统配置
- [system_TGF.yml](system_TGF.yml) - TGF系统配置
- [TIE_trace.json](TIE_trace.json) - TIE追踪数据
- [TGF_trace.json](TGF_trace.json) - TGF追踪数据

## 生成命令

```bash
# TIE测试
./build/rtsim/rtsim system_TIE.yml tasks_critical_energy.yml 50 -t TIE_trace.json

# TGF测试
./build/rtsim/rtsim system_TGF.yml tasks_critical_energy.yml 50 -t TGF_trace.json
```
