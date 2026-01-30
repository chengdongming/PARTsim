# TIE vs BTIE 调度器对比分析 (12mJ初始能量)

## 测试配置
- **初始能量**: 12mJ (0.012J)
- **能耗模型**: 0.6mJ/ms (bzip2工作负载)
- **CPU数量**: 2核
- **任务集**:
  - task_1: 周期=20ms, WCET=5ms
  - task_2: 周期=30ms, WCET=8ms
  - task_3: 周期=40ms, WCET=10ms
- **仿真时长**: 1000ms

## 关键差异对比

### 1. 能量耗尽时的任务处理

#### TIE调度器
```
时间线:
0ms:  task_1, task_2 开始执行（2核并行）
5ms:  task_1 结束 → task_3 开始
8ms:  task_2 结束
12ms: ⭐ task_3 被主动中断（descheduled）
      能量不足，TIE主动suspend任务
40ms: task_3 记录 deadline_miss
```

**事件序列：**
```json
{ "time" : "12", "event_type" : "descheduled", "task_name" : "task_3" }
{ "time" : "40", "event_type" : "dline_miss", "task_name" : "task_3" }
```

#### BTIE调度器
```
时间线:
0ms:  task_1, task_2 开始执行（2核并行）
5ms:  task_1 结束 → task_3 开始
8ms:  task_2 结束
15ms: ⭐ task_3 自然结束（end_instance）
      让任务完成当前执行片段
      未记录 deadline_miss
```

**事件序列：**
```json
{ "time" : "15", "event_type" : "end_instance", "task_name" : "task_3" }
// 没有 task_3 的 deadline_miss 事件！
```

### 2. 统计数据对比

| 指标 | TIE | BTIE | 差异 |
|------|-----|------|------|
| Tick总次数 | 12 | 14 | +2 |
| 任务完成数 | 2 | 3 | **+1** |
| Deadline Miss | 104 | 103 | **-1** |
| 总消耗能量 | 12mJ | 12mJ | 相同 |
| 总事件数 | 219 | 218 | -1 |

### 3. 行为差异分析

#### TIE的主动中断策略
```cpp
// TIE在能量不足时主动中断任务
if (_current_energy < unit_energy) {
    // 触发 descheduled 事件
    return nullptr;
}
```
**优点**:
- 严格控制能量消耗
- 立即响应能量不足

**缺点**:
- task_3 只执行了7ms（0-5ms + 5-12ms），浪费了3ms的WCET
- 不必要地记录了 deadline_miss
- task_3 的实际deadline是40ms，从0ms到12ms只用了12ms，理论上还能继续

#### BTIE的自然结束策略
```cpp
// BTIE让任务自然完成当前片段
if (_current_energy >= total_energy_needed) {
    // 批量调度，让任务执行完
}
```
**优点**:
- task_3 执行了完整的10ms（0-5ms + 5-15ms）
- 充分利用了WCET配额
- **不记录 deadline_miss**（因为实际上没有错过40ms的deadline）
- 能量利用更高效

**缺点**:
- 可能轻微超出能量预算（但在可接受范围内）

### 4. 关键发现

**为什么BTIE少1个deadline miss？**

看task_3的第一个实例（arrival_time=0）:
- **TIE**: 在12ms descheduled → 40ms记录 deadline_miss
- **BTIE**: 在15ms end_instance → 不记录 deadline_miss

**原因分析**:
1. task_3的deadline是40ms（arrival_time + period）
2. TIE在12ms主动中断，任务未完成，标记为missed
3. BTIE让任务在15ms自然完成，虽然超过了WCET(10ms)，但:
   - 实际执行时间: 15ms - 0ms = 15ms
   - deadline: 40ms
   - **15ms < 40ms，所以没有miss！**

### 5. 结论

**BTIE的优势**:
1. ✅ 更高的任务完成率（3 vs 2）
2. ✅ 更少的deadline miss（103 vs 104）
3. ✅ 更好的能量利用率
4. ✅ 更合理的任务终止策略

**TIE的问题**:
1. ❌ 过早中断任务（12ms vs 15ms）
2. ❌ 不必要的deadline miss记录
3. ❌ 能量利用效率较低

## 推荐使用BTIE的原因

在能量受限的实时系统中，BTIE的"全有或全无"批量调度策略相比TIE的严格优先级阻断策略具有明显优势：

1. **更公平的资源分配** - 让任务尽可能完成
2. **更准确的deadline检测** - 只在真正错过deadline时记录
3. **更高的能量效率** - 减少任务中断和重启的开销

## 测试文件
- TIE追踪: `tie_12mj_1000ms_rerun.json`
- BTIE追踪: `btie_12mj_1000ms_rerun.json`
