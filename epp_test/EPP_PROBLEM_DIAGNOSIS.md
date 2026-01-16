# EPP调度器问题诊断报告

## 🔍 问题概述

用户反馈：**"现在的检查和调度都是tick（1ms），但是追踪文件我没感觉到"**

## 🚨 核心���题发现

### 问题1：能量从未被扣减 ❌

**现象**：
```bash
✅ [EPP] getTaskN: 前瞻性判断能量足够，返回任务 #0 当前能量: 100.000000J
✅ [EPP] getTaskN: 前瞻性判断能量足够，返回任务 #1 当前能量: 100.000000J
✅ [EPP] getTaskN: 前瞻性判断能量足够，返回任务 #2 当前能量: 100.000000J
```

**能量一直是100J，从未减少！**

### 问题2：schedule()方法不被调用

**发现**：
- `schedule()`方法（line 179）从未被MRTKernel调用
- MRTKernel只调用`getFirst()`和`getTaskN()`
- 第223行的`_current_energy -= energy_needed;`永远不会执行

**日志验证**：
```bash
grep "开始调度"  # ❌ 没有输出
grep "扣减能量后剩余"  # ❌ 没有输出
```

### 问题3：dispatchTask没有实际调度

**代码**（line 947-965）：
```cpp
void EPPScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
    // ...
    // 调度任务（通过MRTKernel）
    // 这里需要实际的调度逻辑
    // task->schedule();  // ❌ 被注释掉了
}
```

**dispatchTask只是记录了_running_tasks，但没有真正让任务执行！**

## 🔬 问题分析

### 架构理解错误

当前EPP实现假设：
```
getTaskN()返回任务 → MRTKernel调度任务 → 任务执行
```

实际情况应该是：
```
getTaskN()返回任务 → MRTKernel直接让任务运行 → 能量自动扣减（由谁？）
```

### 关键疑问

1. **能量应该由谁扣减？**
   - EPP调度器？❌ 目前没有
   - MRTKernel？❌ 没看到相关代码
   - EnergyBridge？❌ 只管理配置
   - CPU？❓ 没有检查

2. **能量约束如何真正生效？**
   - 如果能量永不扣减，约束就是摆设
   - 如果能量只在某处扣减，在哪里？

3. **追踪文件为什么看不出Tick级调度？**
   - 任务0ms到达并立即调度
   - 249ms, 399ms, 598ms结束
   - 看起来是连续执行，看不出Tick级行为

## 📊 对比CASCADE/ASAP

### CASCADE的行为
```bash
# CASCADE测试（0.35J初始能量）
初始能量: 0.300000 J
```

**需要检查**：CASCADE是否真的扣减能量？还是也只是做判断？

### 关键差异

| 调度器 | 能量判断 | 能量扣减 | 级联调度 |
|--------|---------|---------|---------|
| CASCADE | ✅ 有 | ❓ 未知 | ✅ 有 |
| ASAP | ✅ 有 | ❓ 未知 | ✅ 有 |
| EPP | ✅ 有 | ❌ 没有 | ✅ 有 |

## 🔧 需要的修复步骤

### 第一步：确认能量扣减机制

**问题**：能量应该在哪里扣减？

**选项A：在调度器中手动扣减**
```cpp
// 在某个地方（哪里？）
_current_energy -= task_energy;
```

**选项B：由底层系统自动扣减**
```cpp
// CPU/MRTKernel自动处理
// 需要找到这个机制
```

### 第二步：实现能量扣减

如果选择选项A（手动扣减），需要：

1. **找到正确的扣减时机**
   - 任务开始时？
   - 任务执行中？
   - 任务结束时？

2. **添加能量扣减代码**
   ```cpp
   void onTaskBegin(AbsRTTask *task) {
       double energy = calculateEnergyForTask(task);
       _current_energy -= energy;
       log("能量扣减: {} -> {}", _current_energy + energy, _current_energy);
   }
   ```

3. **持续更新能量（理想情况）**
   ```cpp
   void onTick() {
       // 每Tick更新能量
       for (auto running_task : _running_tasks) {
           double energy_per_tick = calculateEnergyPerTick(running_task);
           _current_energy -= energy_per_tick;
       }
   }
   ```

### 第三步：添加生命周期日志

```cpp
void onTaskArrival(AbsRTTask *task, Tick arrival_time) {
    log("时间[{}]: 任务 {} 实例(到达时间={}) 到达",
        SIMUL.getTime(), task->getName(), arrival_time);
}

void onTaskStart(AbsRTTask *task, Tick start_time) {
    log("时间[{}]: 任务 {} 实例(到达时间={}) 开始执行",
        start_time, task->getName(), task->getArrivalTime());
}

void onTaskEnd(AbsRTTask *task, Tick end_time) {
    log("时间[{}]: 任务 {} 实例(到达时间={}) 执行结束",
        end_time, task->getName(), task->getArrivalTime());
}
```

### 第四步：简化测试

创建最小测试用例：
```yaml
# 只有一个任务
taskset:
  - name: task_high
    iat: 500
    runtime: 250
    deadline: 500
    params: "period=500,wcet=250,workload=bzip2"
    code:
      - fixed(250, bzip2)
```

**预期**：
- 500ms释放新实例
- 立即调度
- 250ms完成
- 无截止错失

## 🎯 立即行动项

1. **检查CASCADE的能量扣减**
   - CASCADE真的扣减能量吗？
   - 还是在其他地方处理？

2. **找到能量扣减的真正位置**
   - 搜索`current_energy`的所有写操作
   - 搜索CPU能量管理

3. **实现EPP的能量扣减**
   - 要么手动实现
   - 要么找到并使用现有机制

4. **添加详细日志**
   - 任务生命周期
   - 能量变化
   - 调度决策

## 📝 待回答问题

1. **能量扣减在哪里发生？**（最关键）
2. **为什么能量一直是100J？**
3. **MRTKernel如何管理任务执行？**
4. **是否需要手动扣减能量？**

## 下一步

等待用户确认：
- 是否需要实现能量扣减？
- 还是有现有的机制我们没发现？
- 能量扣减应该在什么时机？
