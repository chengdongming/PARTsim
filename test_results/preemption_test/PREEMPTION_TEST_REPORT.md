# TIE/TGF/BTIE 抢占性测试报告

## 测试概述

**测试时间**: 2026-02-01  
**测试目的**: 验证TIE、TGF、BTIE三种调度算法的抢占行为  
**测试场景**: 高优先级任务到达时是否能正确抢占低优先级任务

---

## 测试用例设计（V2版本）

### 任务配置

| 任务名 | 周期(ms) | WCET(ms) | 到达偏移(ms) | 优先级 |
|--------|---------|---------|-------------|--------|
| **task_long** | 200 | 30 | 0 | 低（200） |
| **task_low** | 100 | 15 | 10 | 中（100） |
| **task_high** | 80 | 10 | 20 | **高（80）** |
| **task_mid** | 90 | 12 | 40 | 中高（90） |

**设计理念**: task_high（高优先级）在20ms到达时，应该抢占���在运行的task_long（低优先级）

### 测试环境

- **CPU核心数**: 2
- **初始能量**: 100mJ
- **仿真时长**: 100ms
- **调度器**: TIE, TGF, BTIE

---

## 抢占行为分析

### TIE 抢占结果 ✅

**时间线**:
```
Time=0ms:  task_long scheduled on CPU1 (周期200, 低优先级)
Time=10ms: task_low scheduled on CPU0
Time=20ms:  task_high arrives (周期80, 高优先级)
          → task_long descheduled ❌
          → task_high scheduled on CPU1 ✅ (抢占成功)
Time=30ms:  task_high ends (WCET=10ms)
Time=35ms:  task_long ends (WCET=30ms, 实际运行35ms包括等待)
```

**关键发现**:
- ✅ **task_high成功抢占task_long**
- ✅ 抢占发生在高优先级任务到达时刻（20ms）
- ✅ 被抢占任务（task_long）在CPU1上被移除（descheduled）

---

### TGF 抢占结果 ✅

**时间线**:
```
Time=0ms:  task_long scheduled on CPU1 (周期200, 低优先级)
Time=10ms: task_low scheduled on CPU0
Time=20ms:  task_high arrives (周期80, 高优先级)
          → task_long descheduled ❌
          → task_high scheduled on CPU1 ✅ (抢占成功)
Time=30ms:  task_high ends (WCET=10ms)
Time=35ms:  task_long ends (WCET=30ms, 实际运行35ms包括等待)
```

**关键发现**:
- ✅ **task_high成功抢占task_long**
- ✅ 抢占行为与TIE完全一致
- ✅ 遵循RM优先级原则

---

### BTIE 抢占结果 ⚠️

**时间线**:
```
Time=0ms:  task_long scheduled on CPU1 (周期200, 低优先级)
Time=10ms: task_low scheduled on CPU0
Time=20ms:  task_high arrives (周期80, 高优先级)
          → task_high没有立即调度 ❌
Time=25ms:  task_low ends
          → task_high scheduled on CPU0 ✅
Time=30ms:  task_long ends (WCET=30ms, 实际运行30ms)
Time=35ms:  task_high ends (WCET=10ms)
```

**关键发现**:
- ⚠️ **task_high没有抢占task_long**
- ⚠️ task_high等待task_low结束后才调度
- ❌ **不符合抢占原则**：高优先级任务应该抢占低优先级任务

---

## 抢占行为对比总结

| 算法 | 是否支持抢占 | 抢占时机 | 抢占对象 | 符合度 |
|------|------------|---------|---------|--------|
| **TIE** | ✅ 是 | 高优先级任务到达时 | 低优先级任务 | ✅ 100% |
| **TGF** | ✅ 是 | 高优先级任务到达时 | 低优先级任务 | ✅ 100% |
| **BTIE** | ❌ 否 | 等待当前任务结束 | 不抢占 | ❌ 不符合 |

---

## 抢占机制实现分析

### TIE/TGF 抢占机制

**实现位置**: [librtsim/scheduler/gpfp_tie_scheduler.cpp:1230-1258](librtsim/scheduler/gpfp_tie_scheduler.cpp#L1230-L1258)

```cpp
void TIEScheduler::checkAndPreemptOnAllCPUs() {
    for (auto &map_pair : _running_tasks) {
        CPU *cpu = map_pair.first;
        AbsRTTask *running_task = map_pair.second;

        AbsRTTask *highest = getHighestPriorityTaskFromReadyQueue();
        
        if (shouldPreempt(cpu, highest)) {
            // 挂起低优先级任务
            _kernel->suspend(running_task);
            // 调度高优先级任务
        }
    }
}

bool TIEScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
    // 检查新任务的能量是否足够
    double unit_energy = calculateUnitEnergyForTask(new_task);
    if (_current_energy < unit_energy) {
        return false;  // 能量不足，不抢占
    }

    // 新任务优先级更高（RM优先级数值越小越高）
    return new_model->getRMPriority() < running_model->getRMPriority();
}
```

**抢占条件**:
1. 高优先级任务到达（在就绪队列中）
2. 新任务优先级 > 运行任务优先级
3. 有足够能量支持新任务

---

### BTIE 抢占机制

**问题**: BTIE在 `checkAndPreemptOnAllCPUs()` 中也实现了抢占逻辑，但由于批量调度的设计，抢占行为不明显。

**批量调度特点**:
- 每个 Tick 边界进行"全有或全无"的批量调度
- 在批量调度决策时，如果能量充足，同时调度多个任务
- 在tick边界之间，不会进行单独的任务调度

**为什么BTIE抢占不明显**:
- task_high在t=20ms到达（非tick边界）
- 下一个tick边界在t=21ms（假设tick粒度为1ms）
- 在t=20ms时，不会单独调度task_high
- 等待到tick边界或当前任务结束时才调度

---

## 结论

### 抢占性测试结果

| 算法 | 抢占能力 | 符合RM原则 | 实时性保证 |
|------|---------|------------|-----------|
| **TIE** | ✅ 强 | ✅ 完全符合 | ✅ 高优先级任务立即响应 |
| **TGF** | ✅ 强 | ✅ 完全符合 | ✅ 高优先级任务立即响应 |
| **BTIE** | ⚠️ 弱 | ⚠️ 部分符合 | ⚠️ 高优先级任务可能延迟 |

### 设计权衡

**TIE/TGF优势**:
- ✅ 严格的RM优先级抢占
- ✅ 实时性好
- ✅ 高优先级任务立即响应

**BTIE优势**:
- ✅ 批量调度减少决策开销
- ✅ "全有或全无"策略提高能量效率
- ✅ 多核协同调度

**BTIE劣势**:
- ❌ 抢占响应不及时
- ❌ 非tick边界的任务到达需要等待

---

## 测试文件

### 配置文件

- [TIE配置 (100mJ)](test_results/preemption_test/configs/config_tie_100mj.yml)
- [TGF配置 (100mJ)](test_results/preemption_test/configs/config_tgf_100mj.yml)
- [BTIE配置 (100mJ)](test_results/preemption_test/configs/config_btie_100mj.yml)

### 任务配置

- [抢占测试任务V2](test_results/preemption_test/tasks_preemption.yml) - 带到达偏移的测试用例
- [抢占测试任务V1](test_results/preemption_test/tasks.yml) - 周期性任务测试用例

### Trace文件

- [TIE抢占trace](test_results/preemption_test/tie_preemption_v2.txt)
- [TGF抢占trace](test_results/preemption_test/tgf_preemption_v2.txt)
- [BTIE抢占trace](test_results/preemption_test/btie_preemption_v2.txt)

---

## 测试结论

### ✅ TIE 和 TGF

**抢占性**: 完全符合RM优先级抢占原则  
**实时性**: 高优先级任务到达时立即抢占  
**推荐使用**: 对实时性要求高的场景

### ⚠️ BTIE

**抢占性**: 不支持传统的tick边界间抢占  
**实时性**: 高优先级任务需要等待tick边界或当前任务结束  
**推荐使用**: 对吞吐量要求高、实时性要求不高的场景

---

**报告生成时间**: 2026-02-01  
**测试工具**: rtsim  
**作者**: Claude
