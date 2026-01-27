# BTIE批量调度的正确修复方案

## 当前问题

当前BTIE的实现：
1. ✅ 收集所有任务（运行中 + 就绪队列）
2. ❌ **没有按优先级排序**
3. ❌ **没有选择前K个任务**
4. ❌ 扣除所有任务的能量（包括不会被调度的）
5. ❌ 能量虚高10.9%

## 正确的批量调度逻辑

### 设计原则（用户描述）

```
K = min(CPU核心数, 候选任务数)

每个tick:
  1. 收集候选任务
     - 运行中的任务（加入候选）
     - 就绪队列的任务（加入候选）
     - 过滤即将完成的运行中任务（剩余时间<=1ms）
     
  2. 按优先级排序（RM: 周期短的优先级高）
     
  3. 选择前K个任务
     
  4. 批量能量判断
     - 计算这K个任务的总能耗
     - 能量充足 → 扣除能量，设置batch_scheduled=true
     - 能量不足 → 不扣除，设置batch_scheduled=false
     
  5. Dispatch
     - getTaskN(0)返回第1个任务
     - getTaskN(1)返回第2个任务
     - ... getTaskN(K-1)返回第K个任务
```

### 示例：2核，5个任务01234

```
时刻0ms:
  候选: [0,1,2,3,4] (全部新到达)
  过滤: [] (没有即将完成的)
  排序: [0,1,2,3,4] (假设0优先级最高)
  选择K=2: [0,1]
  能量判断: ✅ 能量充足
  扣除: 2个任务的能量
  调度: getTaskN(0)→0, getTaskN(1)→1

时刻1ms:
  候选: [0,1,2,3,4] (0,1运行中)
  过滤: [] (0,1都还需要继续执行)
  排序: [0,1,2,3,4]
  选择K=2: [0,1]
  能量判断: ✅ 能量充足
  扣除: 2个任务的能量
  调度: getTaskN(0)→0, getTaskN(1)→1

时刻5ms (假设0任务完成):
  候选: [1,2,3,4] (0已完成)
  过滤: [] 
  排序: [1,2,3,4]
  选择K=2: [1,2]
  能量判断: ✅ 能量充足
  扣除: 2个任务的能量
  调度: getTaskN(0)→1, getTaskN(1)→2
```

## 修复代码

### 修改 performTickScheduling()

```cpp
void BTIEScheduler::performTickScheduling() {
    _stats.total_tick_count++;
    
    // ... 能量收集 ...
    
    // 1. 收集候选任务
    std::vector<AbsRTTask *> candidate_tasks;
    const auto& running_tasks = _kernel->getCurrentExecutingTasks();
    
    // 1.1 添加运行中的任务（过滤即将完成的）
    for (const auto& map_pair : running_tasks) {
        AbsRTTask* task = map_pair.second;
        if (task) {
            // ⭐ 检查任务是否即将完成（剩余时间>1ms）
            auto* rt_task = dynamic_cast<RTTask*>(task);
            if (rt_task) {
                double remaining_time = rt_task->getRemainingTime();
                if (remaining_time > 1.0) {  // 剩余时间>1ms，需要继续执行
                    candidate_tasks.push_back(task);
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("🏁 [BTIE] 任务即将完成，不加入候选: ") + 
                                      getTaskName(task));
                }
            } else {
                candidate_tasks.push_back(task);
            }
        }
    }
    
    // 1.2 添加就绪队列中的任务
    for (auto* task : _ready_queue) {
        if (task && std::find(candidate_tasks.begin(), candidate_tasks.end(), task) == candidate_tasks.end()) {
            candidate_tasks.push_back(task);
        }
    }
    
    if (candidate_tasks.empty()) {
        SCHEDULER_LOG_INFO("📭 [BTIE] 无候选任务");
        _batch_scheduled_this_tick = false;
        _current_batch_tasks.clear();
        checkAndPreempt();
        return;
    }
    
    // 2. ⭐ 按优先级排序（RM: 周期短的优先级高）
    std::sort(candidate_tasks.begin(), candidate_tasks.end(),
              [this](AbsRTTask* a, AbsRTTask* b) {
                  // 获取任务周期（优先级）
                  auto* rt_a = dynamic_cast<RTTask*>(a);
                  auto* rt_b = dynamic_cast<RTTask*>(b);
                  if (rt_a && rt_b) {
                      // 周期短的优先级高
                      return rt_a->getPeriod() < rt_b->getPeriod();
                  }
                  return false;
              });
    
    // 3. ⭐ 选择前K个任务（K = min(CPU数, 候选任务数)）
    size_t ncpu = _kernel->getProcessorCount();
    size_t K = std::min(ncpu, candidate_tasks.size());
    
    std::vector<AbsRTTask *> selected_tasks(candidate_tasks.begin(), 
                                            candidate_tasks.begin() + K);
    
    // 4. 计算选中任务的总能耗
    double total_energy = 0.0;
    for (auto* task : selected_tasks) {
        double unit_energy = calculateUnitEnergyForTask(task);
        total_energy += unit_energy;
    }
    
    SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 批量调度决策: ") +
                      "候选任务=" + std::to_string(candidate_tasks.size()) +
                      " 选择K=" + std::to_string(K) +
                      " 总能耗=" + std::to_string(total_energy * 1000) + " mJ");
    
    // 5. ⭐ 批量能量判断（"全有或全无"）
    const double EPSILON = 1e-9;
    if (_current_energy >= total_energy - EPSILON) {
        // 能量充足：扣除选中任务的能耗
        double old_energy = _current_energy;
        _current_energy -= total_energy;
        _stats.total_energy_consumed += total_energy;
        
        // 将选中的任务标记为批量调度
        _batch_scheduled_this_tick = true;
        _current_batch_tasks = selected_tasks;  // ⭐ 只保存选中的K个任务
        _current_batch_size = K;
        _stats.total_batch_schedules++;
        
        SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 批量调度成功: ") +
                          "K=" + std::to_string(K) +
                          " 总能耗=" + std::to_string(total_energy * 1000) + " mJ");
    } else {
        // 能量不足：不调度任何任务
        _batch_scheduled_this_tick = false;
        _current_batch_tasks.clear();
        _current_batch_size = 0;
        _stats.total_batch_skipped++;
        
        SCHEDULER_LOG_INFO(std::string("❌ [BTIE] 能量不足，不调度: ") +
                          "需要=" + std::to_string(total_energy * 1000) + " mJ" +
                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
    }
    
    // ... checkAndPreempt() and dispatch() ...
}
```

### 修改 getTaskN()

```cpp
AbsRTTask *BTIEScheduler::getTaskN(unsigned int n) {
    // 检查本tick是否批量调度成功
    if (!_batch_scheduled_this_tick) {
        return nullptr;
    }
    
    // ⭐ 只返回选中的K个任务
    if (n >= _current_batch_size) {
        return nullptr;
    }
    
    // 从当前批量任务中返回第n个
    if (n < _current_batch_tasks.size()) {
        return _current_batch_tasks[n];
    }
    
    return nullptr;
}
```

## 修复效果

### 能量消耗

```
修复前:
  收集: 3个任务
  扣除: 3个任务的能量 (1.674 mJ)
  实际: 只调度2个任务
  浪费: 0.558 mJ

修复后:
  收集: 3个任务
  排序: 按优先级排序
  选择K=2: 只选择前2个任务
  扣除: 2个任务的能量 (1.116 mJ)
  实际: 调度2个任务
  浪费: 0 mJ ✅
```

### 预期能耗

```
修复前BTIE: 68.076 mJ
修复后BTIE: 61.380 mJ (与TIE相同) ✅
```

## 关键改进

1. ✅ **按优先级排序**：确保高优先级任务被优先调度
2. ✅ **选择前K个任务**：K = min(CPU数, 候选任务数)
3. ✅ **只扣除选中任务的能量**：精确匹配实际执行
4. ✅ **过滤即将完成的任务**：不浪费能量在即将完成的任务上
5. ✅ **保持"全有或全无"策略**：能量不足时完全不调度

## 这才是真正的批量调度！

符合用户描述的正确逻辑：
- 每个tick选择优先级最高的K个任务
- 考虑运行中任务的剩余执行时间
- 能量充足时全部调度，能量不足时全不调度
- 能量扣除精确匹配实际执行

