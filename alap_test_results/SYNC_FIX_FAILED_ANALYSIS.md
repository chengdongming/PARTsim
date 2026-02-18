# ALAP-Sync 修复失败分析

## 修复内容

应用了两个关键修复：
1. ✅ 启用批次级ALAP时序门控（第560-563行）
2. ✅ 删除个体Slack过滤（第809-815行）

## 测试结果对比

| 指标 | ALAP-Sync (原始) | ALAP-Sync (修复后) | 变化 |
|------|----------------|-----------------|------|
| 任务完成数 | 40 | 33 | ⬇️ -17.5% |
| Deadline Miss | 79 | 86 | ⬆️ +8.9% |
| Miss率 | 65% | 72% | ⬆️ +7% |
| 剩余能量 | 2.81 J | 4.03 J | ⬆️ +43% |

**结论**: 修复后性能更差了！

---

## 问题分析

### 根本原因：批次构建不完整

当前实现只在**新任务**的候选批次上计算Batch Slack：

```cpp
// 第549-563行
std::vector<AbsRTTask *> candidate_batch;
for (int i = 0; i < K && i < static_cast<int>(sorted_ready.size()); ++i) {
    candidate_batch.push_back(sorted_ready[i]);
}

// ⭐ 问题：只在候选批次（新任务）上检查Batch Slack
if (!candidate_batch.empty() && !checkALAPBatchTimingGate(candidate_batch)) {
    return;  // 批次休眠
}
```

这导致：
1. 如果新任务的Slack > 0，整个批次休眠
2. 但运行任务可能Slack ≤ 0，急需CPU时间
3. 结果：**运行任务继续执行，新任务等待，错过了最佳调度时机**

### 案例：t=30时刻

**原始实现**：
- Task_Assassin_Hungry (arrival=0) Slack = 30 - 30 = 0
- 批次门控通过，立即调度

**修复后**：
- Task_Assassin_Hungry (arrival=0) 可能已在运行中
- 新任务（如Task_Survivor_Eco）的Slack可能 > 0
- 批次门控失败，休眠
- Task_Assassin_Hungry继续运行，但Task_Survivor_Eco错过了调度时机

---

## 根本性问题

**当前实现的批次构建逻辑不符合"全员进退"原则**：

1. 批次只包含**新任务**（candidate_batch）
2. 不包括**运行任务**（running_task_list）
3. 批次级ALAP时序门控只检查新任务的Slack
4. 导致：新任务频繁休眠，错过调度时机

---

## 正确的实现方向

根据用户描述的调度逻辑，批次应该包括：

### 选项1：批次包括运行任务+新任务
```
batch = running_tasks + candidate_batch
batch_slack = min(slack_i for i in batch)
if batch_slack > 0:
    集体休眠
else:
    能量检查
```

### 选项2：批次只包括新任务，但不使用批次级门控
```
# 只在能量检查时考虑运行任务+新任务
if energy_sufficient:
    调度新任务
else:
    if batch_slack > 0:
        集体休眠
    else:
        整批阻塞
```

### 选项3：使用个体Slack过滤（原始实现）
```
# 不使用批次级门控，而是在任务选择时过滤Slack > 0的任务
for task in candidate_batch:
    if task.slack <= 0:
        schedule(task)
```

---

## 当前困境

三个选项都有问题：

**选项1**：
- 需要重构代码，将运行任务和新任务作为一个批次
- 复杂度高，可能引入新bug

**选项2**：
- 当前实现接近这个选项，但批次级门控逻辑有问题
- 需要调整门控触发时机

**选项3**：
- 原始实现就是这个选项，但性能最差（miss率65%）
- 用户明确要求实现"批次调度"，不是个体调度

---

## 建议

**暂时禁用批次级ALAP时序门控**，使用原始实现：
- 恢复注释掉第560-563行
- 恢复第809-815行的个体Slack过滤
- 重新测试

或者，**重新设计批次构建逻辑**：
- 修改第549-563行，让批次包括运行任务+新任务
- 实现真正的"全员进退"批次调度

需要用户明确选择哪个方向。
