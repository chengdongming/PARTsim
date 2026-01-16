# EPP调度器架构问题分析

## 问题根源

**EPP调度器实现了自定义的schedule()方法，但这与MRTKernel的调度流程冲突！**

### MRTKernel的调度流程
```
MRTKernel::dispatch()
  └─> for (i = 0; ; i++)
       └─> task = scheduler->getTaskN(i)
           └─> if (task != nullptr)
               └─> assignToCPU(task, cpu)
                   └─> 记录"scheduled"事件
```

### EPP调度器的错误架构
```
EPPScheduler::schedule()  ❌ 自定义调度方法
  ├─ 调用 getFreeCPUCount() → 返回0（_running_tasks未初始化）
  ├─ 调用 dispatchTask() → 没有真正调度任务
  └─ 没有生成"scheduled"事件
```

### 正确的架构（应该像ASAP一样）
```
MRTKernel::dispatch()
  └─> scheduler->getTaskN(0) → EPP返回最高优先级任务
  └─> scheduler->getTaskN(1) → EPP返回第二高优先级任务（如果能量足够）
  └─> scheduler->getTaskN(2) → EPP返回nullptr（能量不足，停止级联）
      └─> MRTKernel记录"scheduled"事件 ✅
```

## 解决方案

### 选项A：移除EPP的schedule()方法
- 依赖MRTKernel的dispatch()
- 只实现getFirst()和getTaskN()
- **优点**：与架构一致，会生成scheduled事件
- **缺点**：需要确保能量检查逻辑正确

### 选项B：初始化_running_tasks映��
- 在构造函数中从ConfigManager获取CPU列表
- 填充_running_tasks映射
- **优点**：保留schedule()方法
- **缺点**：与MRTKernel的dispatch()冲突，可能重复调度

## 推荐：选项A

移除schedule()方法，只保留getFirst()/getTaskN()的能量检查逻辑。

### 关键修改

1. **删除或注释掉schedule()方法**
2. **确保getFirst()/getTaskN()正确实现能量检查**
3. **让MRTKernel负责实际的调度和事件记录**

### ASAP的示例（正确的实现）

```cpp
AbsRTTask *GPFPASAPScheduler::getFirst() {
    // 1. 检查能量
    if (current_energy <= 0) return nullptr;

    // 2. 获取最高优先级任务
    AbsRTTask *first_task = active_tasks[0];

    // 3. 检查能量是否足够
    if (current_energy < unit_energy) {
        return nullptr;  // ⭐ 关键：返回nullptr，不调度
    }

    // 4. ✅ 能量足够，返回任务
    // MRTKernel会记录"scheduled"事件
    return first_task;
}
```

## 立即行动

1. 检查EPP的getFirst()/getTaskN()实现
2. 确保它们在能量不足时返回nullptr
3. 移除或注释schedule()方法（避免与MRTKernel冲突）
4. 重新编译测试
