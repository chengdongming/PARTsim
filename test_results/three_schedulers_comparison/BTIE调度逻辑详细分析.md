# BTIE调度逻辑详细分析

## BTIE = Batch Tick-based Instant Energy-aware

BTIE的核心思想是**批量决策**，但能量扣除仍采用**后扣方式**（只扣除运行任务的能量）。

---

## 完整调度流程

### 1. Tick事件触发（每1ms）

```cpp
void BTIEScheduler::onTick() {
    performTickScheduling();  // 批量调度决策
}
```

---

### 2. performTickScheduling() - 批量决策核心

#### 第1步：扣除运行中任务的能量（后扣）

```cpp
// 收集运行中任务
const auto& running_tasks = _kernel->getCurrentExecutingTasks();
std::vector<AbsRTTask *> running_task_list;
double energy_to_deduct = 0.0;

for (const auto& map_pair : running_tasks) {
    AbsRTTask* task = map_pair.second;
    if (task) {
        running_task_list.push_back(task);
        double unit_energy = calculateUnitEnergyForTask(task);
        energy_to_deduct += unit_energy;
    }
}

// 扣除运行中任务的能量（它们已经执行了1ms）
if (_current_energy >= energy_to_deduct) {
    _current_energy -= energy_to_deduct;  // ✅ 后扣
} else {
    // ⚠️ 能量不足：记录警告，但不扣除
    SCHEDULER_LOG_WARNING("⚠️ 能量不足无法扣除运行任务能耗");
}
```

**关键点**：
- ✅ **只扣除运行中任务的能量**（后扣方式）
- ✅ 新任务不扣能量
- ❌ 能量不足时不扣除（这是已知问题）

---

#### 第2步：计算K（选择几个新任务）

```cpp
size_t running_count = running_task_list.size();      // 运行中任务数
size_t total_cpus = running_tasks.size();              // CPU总数
size_t free_cpus = total_cpus - running_count;         // 空闲CPU数
size_t K = std::min(free_cpus, _ready_queue.size());    // ⭐ K = min(空闲CPU, 等待任务数)
```

**示例**：
- 3核CPU，运行中2个任务 → K = min(1, 等待任务数) = 1
- 3核CPU，运行中0个任务 → K = min(3, 4) = 3

---

#### 第3步：按优先级排序并选择K个新任务

```cpp
std::vector<AbsRTTask *> new_tasks_to_schedule;
if (K > 0) {
    // 按RM优先级排序（deadline越小优先级越高）
    std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
    std::sort(sorted_ready.begin(), sorted_ready.end(),
        [](AbsRTTask* a, AbsRTTask* b) { return a->getDeadline() < b->getDeadline(); });

    // 选择前K个最高优先级任务
    for (size_t j = 0; j < K && j < sorted_ready.size(); ++j) {
        new_tasks_to_schedule.push_back(sorted_ready[j]);
    }
}
```

**关键点**：
- ✅ **只选择K个新任务**（不是全部）
- ✅ **按RM优先级排序**（deadline最小的优先）

---

#### 第4步：批量能量判断（"全有或全无"）

```cpp
// 计算新任务的总能耗
double new_tasks_energy = 0.0;
for (auto* task : new_tasks_to_schedule) {
    new_tasks_energy += calculateUnitEnergyForTask(task);
}

// ⭐ BTIE核心：批量判断
if (_current_energy >= new_tasks_energy) {
    // 能量充足：调度所有K个新任务
    _batch_scheduled_this_tick = true;

    // _current_batch_tasks包含：运行中任务 + 新任务
    std::vector<AbsRTTask *> all_tasks_to_dispatch;
    for (auto* task : running_task_list) {
        all_tasks_to_dispatch.push_back(task);  // 运行中的任务
    }
    for (auto* task : new_tasks_to_schedule) {
        all_tasks_to_dispatch.push_back(task);  // 新任务
    }

    _current_batch_tasks = all_tasks_to_dispatch;  // ✅ 设置批量任务

} else {
    // 能量不足：不调度新任务
    _batch_scheduled_this_tick = false;
    _current_batch_tasks.clear();  // ✅ 清空批量任务
}
```

**关键点**：
- ✅ **"全有或全无"策略**：能量充足时全部调度，不足时只影响新任务
- ✅ 运行中任务不受影响（继续在_current_batch_tasks中）
- ✅ 只检查新任务的能量，不扣除

---

### 3. dispatch()调用getTaskN()

```cpp
AbsRTTask *BTIEScheduler::getTaskN(unsigned int n) {
    // ⭐ 关键修复：使用_current_batch_tasks
    if (_current_batch_tasks.empty()) {
        return nullptr;  // 能量不足或没有任务
    }

    if (n >= _current_batch_tasks.size()) {
        return nullptr;  // 索引超出范围
    }

    AbsRTTask *task = _current_batch_tasks[n];
    return task;  // 直接返回，能量已检查过
}
```

**关键点**：
- ✅ 从`_current_batch_tasks`获取任务
- ✅ 不需要额外的能量检查（已在performTickScheduling中检查）
- ✅ 能量不足时返回nullptr

---

## 能量扣减逻辑

### 扣减对象：只扣除运行中任务的能量

```
时刻    运行任务               新任务        扣除能量
────────────────────────────────────────────────────────
0ms    []                   [task_1,2,3]   0 mJ
1ms    [task_1,2,3]         []              1.674 mJ ← 扣除3个运行任务
2ms    [task_1,2,3]         []              1.674 mJ ← 扣除3个运行任务
```

**关键点**：
- ✅ **后扣方式**：任务执行后才扣能量
- ✅ **只扣运行任务**：新任务不扣
- ✅ **新任务在下一tick变成运行任务后才开始扣能量**

---

## 批量调度示例

### 场景：3核CPU，4个任务，能量充足

```
就绪队列（按优先级排序）: [task_1(prio=20), task_2(prio=30), task_3(prio=40), task_4(prio=50)]

=== 0ms Tick ===
运行中任务: []
空闲CPU: 3
K = min(3, 4) = 3
选择新任务: [task_1, task_2, task_3]（前3个优先级最高的）
能量检查: 3个新任务能耗 = 1.674 mJ <= 8.0 mJ ✅
批量调度成功: _current_batch_tasks = [task_1, task_2, task_3]

=== 1ms Tick ===
运行中任务: [task_1, task_2, task_3]
扣除能量: 1.674 mJ
空闲CPU: 0
K = min(0, 1) = 0（没有空闲CPU）
选择新任务: []
能量检查: 0 mJ <= 6.126 mJ ✅
批量调度成功: _current_batch_tasks = [task_1, task_2, task_3]

=== 5ms Tick ===
运行中任务: [task_2, task_3]（task_1完成）
扣除能量: 1.116 mJ（只扣2个任务）
空闲CPU: 1
K = min(1, 2) = 1
选择新任务: [task_4]
能量检查: 0.558 mJ <= 4.464 mJ ✅
批量调度成功: _current_batch_tasks = [task_2, task_3, task_4]
```

---

## 能量不足场景

### 场景：3核CPU，初始能量1.0 mJ

```
=== 0ms Tick ===
运行中任务: []
K = min(3, 4) = 3
选择新任务: [task_1, task_2, task_3]
能量检查: 1.674 mJ > 1.0 mJ ❌
批量调度失败: _current_batch_tasks = []
结果: getTaskN(0) = nullptr，无任务调度 ✅
```

**关键点**：
- ✅ 能量不足时，`_current_batch_tasks`为空
- ✅ `getTaskN(0)`返回nullptr
- ✅ 无任务调度
- ✅ **这是正确的批量调度行为**

---

## 总结

### 1. 是批量调度吗？ ✅ 是的

**证据**：
- performTickScheduling()中**一次性决策**所有任务
- 批量判断能量：要么全部调度，要么不调度新任务
- 使用`_current_batch_tasks`保存批量决策结果

### 2. 选择几个任务？

**K = min(空闲CPU数, 等待任务数)**

- 空闲CPU = CPU总数 - 运行中任务数
- K = min(free_cpus, _ready_queue.size())
- 选择前K个优先级最高的任务

**示例**：
- 3核，运行0个，等待4个 → K = 3
- 3核，运行2个，等待4个 → K = 1
- 3核，运行3个，等待4个 → K = 0

### 3. 能量扣减几个任务？

**只扣除运行中任务的能量（后扣）**

```
运行任务数 = N
实际扣减 = N个任务的能量
新任务数 = K（不扣能量）
```

**示例**：
- 运行2个，新选1个 → 扣除2个任务能量
- 运行3个，新选0个 → 扣除3个任务能量
- 运行0个，新选3个 → 扣除0个任务能量

---

## BTIE vs TIE 对比

| 特性 | TIE | BTIE |
|------|-----|------|
| **调度方式** | 级联调度（逐个判断） | 批量调度（一次性判断） |
| **能量扣除** | 后扣（运行任务） | 后扣（运行任务） |
| **能量检查** | 逐个检查getTaskN() | 批量检查performTickScheduling() |
| **能量不足** | 立即停止级联 | 不调度新任务，运行任务继续 |
| **K值** | 不适用（逐个调度） | K = min(free_cpus, ready_queue.size()) |

---

## 当前BTIE的特性

✅ **已实现**：
1. 批量调度：一次性决策所有任务
2. 按优先级选择：K个最高优先级任务
3. 能量后扣：只扣除运行任务能量
4. "全有或全无"：能量充足全部调度，不足只影响新任务
5. 使用_current_batch_tasks：实现批量决策

⚠️ **已知问题**：
1. 能量不足时仍保留运行任务（可能导致任务完成数不一致）
2. 能量不足时没有中断机制（与TIE的差异）

**核心思想**：BTIE是"批量TIE" - 在tick边界批量决策，但能量管理采用后扣方式。
