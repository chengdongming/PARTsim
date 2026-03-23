# V57 ALAP-NonBlock 重构：Greedy Bypass 逐级剥夺

## 目标

基于 V56 ALAP-Block 的成功经验，重构 ALAP-NonBlock：
- 废除"全局断头台"（`scheduleEnergyDepletionEvent()` + `onEnergyDepleted()`）
- 实现 Greedy Bypass（贪婪绕行）续期能量检查
- 核心语义：**大哥能量不足被挂起，小弟可以绕过大哥，独占剩余能量**

## Bug 发现

### 问题 1：全局能量耗尽闹钟（Global Guillotine）

V56 中发现 `scheduleEnergyDepletionEvent()` 在续期循环末尾被调用，使用总功耗预测全局耗尽时间，注册闹钟事件。即使逐级剥夺逻辑正确执行，闹钟触发时 `onEnergyDepleted()` 仍会群体挂起所有任务，覆盖续期检查的成果。

NonBlock 中同样存在此问题：`_energy_depleted_event` 在每个 tick 末尾被注册，在未来某时刻触发群体挂起。

### 问题 2：非贪婪的续期检查

旧代码逐个检查运行中任务能量，某个任务能量不足时加入挂起列表。但没有按优先级排序，也没有 Greedy Bypass 逻辑——低优先级任务无法"捡漏"大哥挂起后剩余的能量。

---

## 修改操作

### 1. 废除 `scheduleEnergyDepletionEvent()`

**文件**：[gpfp_alap_nonblock_scheduler.cpp:2453](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L2453)

```cpp
// 修改前：
void ALAPNonBlockScheduler::scheduleEnergyDepletionEvent(MetaSim::Tick time_until_depletion) {
    if (!_energy_depleted_event) return;
    Tick current_time = SIMUL.getTime();
    Tick depletion_time = current_time + time_until_depletion;
    if (time_until_depletion <= 0) {
        SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] 能量已耗尽，立即触发耗尽处理");
        onEnergyDepleted();
        return;
    }
    _energy_depleted_event->drop();
    _energy_depleted_event->_scheduled_depletion_time = depletion_time;
    _energy_depleted_event->_energy_at_prediction = _current_energy;
    _energy_depleted_event->post(depletion_time);
    SCHEDULER_LOG_INFO("⚡ [ALAP-NonBlock] ⭐ 注册能量耗尽预测事件: ...");
}

// 修改后：
void ALAPNonBlockScheduler::scheduleEnergyDepletionEvent(MetaSim::Tick time_until_depletion) {
    // ⭐ V57捉鬼：废除"全局能量耗尽预测闹钟"！
    // NonBlock语义：每个任务独立判断，不存在"全局断头台"
    // Block壁垒由逐级剥夺逻辑建立，NonBlock不建立任何壁垒
    SCHEDULER_LOG_DEBUG("⚡ [ALAP-NonBlock] scheduleEnergyDepletionEvent()已被废除，不执行任何操作！");
}
```

### 2. 废除 `onEnergyDepleted()`

**文件**：[gpfp_alap_nonblock_scheduler.cpp:2466](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L2466)

```cpp
// 修改前：
void ALAPNonBlockScheduler::onEnergyDepleted() {
    Tick current_time = SIMUL.getTime();
    SCHEDULER_LOG_WARNING("💀💀💀 [ALAP-NonBlock] ⭐ 能量耗尽事件触发！...");
    if (_current_energy < 0.0) {
        SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] 检测到能量透支！强制归零");
    }
    _energy_depleted = true;  // ❌ 全局断头台标志

    // 群体挂起所有任务
    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> tasks_to_suspend;
        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            tasks_to_suspend.push_back(task);
        }
        for (AbsRTTask *task : tasks_to_suspend) {
            setSuspendReason(task, "energy_depleted");
            _kernel->suspend(task);  // ❌ 群体挂起
            SCHEDULER_LOG_WARNING("💀 [ALAP-NonBlock] 强制挂起任务(能量耗尽): " + getTaskName(task));
        }
    }
    cancelEnergyDepletionEvent();
    SCHEDULER_LOG_WARNING("💀💀💀 [ALAP-NonBlock] 能量耗尽处理完成，跳后续续调度");
}

// 修改后：
void ALAPNonBlockScheduler::onEnergyDepleted() {
    // ⭐ V57捉鬼：废除"全局断头台"！
    // NonBlock语义：每个任务独立判断，不存在"全局断头台"
    // 当能量不足时，只挂起能量不足的任务，不影响其他任务
    Tick current_time = SIMUL.getTime();
    SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] onEnergyDepleted()已被废除，本函数不再执行任何操作！"
                         " 时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms");
    // ⚠️ 绝对不设置 _energy_depleted = true
    // ⚠️ 绝对不调用任何 _kernel->suspend()
    // ⚠️ 绝对不调用 dispatch()
    // → 逐级剥夺逻辑在renewal check中已处理
}
```

### 3. 移除预测事件调用

**文件**：[gpfp_alap_nonblock_scheduler.cpp:681](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L681)

```cpp
// 修改前：
// ⭐ V51修复：能量耗尽预测 - 每个tick只更新一次
if (_last_prediction_tick == current_time) {
    SCHEDULER_LOG_DEBUG("⏭️ [ALAP-NonBlock] 跳过重复预测（本tick已更新）");
} else {
    _last_prediction_tick = current_time;
    cancelEnergyDepletionEvent();
    double total_power = calculateTotalPowerConsumption();
    if (total_power > 0.0 && _current_energy > 0.0) {
        MetaSim::Tick time_to_deplete = predictTimeToDepletion(_current_energy, total_power);
        scheduleEnergyDepletionEvent(time_to_deplete);  // ❌ 调用全局闹钟
    }
}

// 修改后：
// ⭐ V57捉鬼：废除全局能量耗尽预测！
// NonBlock语义：不需要预测"何时全局耗尽"，每个任务独立判断
// Greedy Bypass已通过逐级剥夺逻辑处理所有情况
if (_last_prediction_tick == current_time) {
    SCHEDULER_LOG_DEBUG("⏭️ [ALAP-NonBlock] 跳过重复预测（本tick已更新）");
} else {
    _last_prediction_tick = current_time;
    cancelEnergyDepletionEvent();
    // ⭐ V57：不再注册任何全局耗尽事件！
}
```

### 4. 重构续期能量检查为 Greedy Bypass

**文件**：[gpfp_alap_nonblock_scheduler.cpp:541](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L541)

核心改动：将原来的"遍历运行任务、检查能量、挂起不足任务"的逻辑，改为：

1. **收集并按优先级排序**
2. **逐个检查能量，足够则续期，不足则 continue**（不 break！）
3. `available_energy` 累积递减，让低优先级任务看到大哥"省下"的能量

```cpp
// 修改前：第2步 续期能量检查
// 原代码遍历 running_tasks_map，对每个任务独立检查能量
// 问题：不按优先级排序，且无法让低优先级任务捡漏大哥挂起后的能量

// 修改后：
// ========== 第2步：处理运行中任务的续期能量（V57 Greedy Bypass） ==========
// ⭐ V57重构：优先级排序 + 贪婪绕行
// NonBlock语义：大哥能量不足被挂起，小弟可以绕过大哥，独占剩余能量
// 逻辑：按优先级排序，逐个检查能量，阻塞则continue（不break）
if (!_kernel) {
    _kernel = getKernel();
}

if (_kernel) {
    const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
    std::vector<AbsRTTask *> tasks_to_suspend;

    SCHEDULER_LOG_INFO("🏃 检查运行任务: " +
                       std::to_string(running_tasks_map.size()) + " 个");

    // ⭐ V57 Greedy Bypass: 收集并按优先级排序
    std::vector<AbsRTTask *> sorted_tasks;
    for (const auto& [cpu, task] : running_tasks_map) {
        if (!task || !task->isActive()) continue;
        // ⭐ V42修复：跳过当前tick中新调度的任务（能量已在getTaskN中扣除）
        if (_newly_dispatched_this_tick.find(task) != _newly_dispatched_this_tick.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⏭️ [ALAP-NonBlock] 跳过新任务的续期扣除: ") + getTaskName(task));
            continue;
        }
        sorted_tasks.push_back(task);
    }

    // ⭐ 按优先级排序（周期越小 = 优先级越高，排在前面的先检查）
    std::sort(sorted_tasks.begin(), sorted_tasks.end(),
        [this](AbsRTTask* a, AbsRTTask* b) {
            return a->getPeriod() < b->getPeriod();
        });

    const double EPSILON = 1e-9;
    double available_energy = _current_energy;

    for (AbsRTTask *task : sorted_tasks) {
        double unit_energy = calculateUnitEnergyForTask(task);

        if (available_energy >= unit_energy - EPSILON) {
            // 能量足够，续期成功
            double old_energy = _current_energy;
            _current_energy -= unit_energy;
            available_energy -= unit_energy;
            if (_current_energy < 0.0) _current_energy = 0.0;
            _stats.total_energy_consumed += unit_energy;

            SCHEDULER_LOG_INFO("⚡ [ALAP-NonBlock] 续期成功: " + getTaskName(task) +
                               " -" + std::to_string(unit_energy * 1000) + " mJ " +
                               std::to_string(old_energy * 1000) + " → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        } else {
            // ⭐ Greedy Bypass: 大哥能量不足被挂起，小弟继续尝试
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] 续期能量不足: " + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy * 1000) + " mJ " +
                                 " 剩余=" + std::to_string(available_energy * 1000) + " mJ");
            tasks_to_suspend.push_back(task);
            // ⭐ 关键：不break，continue让低优先级任务继续尝试（贪婪绕行）
        }
    }

    // 挂起能量不足的任务
    for (AbsRTTask *task : tasks_to_suspend) {
        clearTaskTickSelection(task);
        setSuspendReason(task, "insufficient_energy");
        _kernel->suspend(task);
        SCHEDULER_LOG_INFO("🛑 [ALAP-NonBlock] Greedy Bypass挂起: " + getTaskName(task));
    }
}
```

---

## Greedy Bypass 机制详解

### Block vs NonBlock 语义对比

| 特性 | Block (V56) | NonBlock (V57) |
|------|-------------|----------------|
| 大哥能量不足 | 连坐挂起低优先级任务 | **只挂起大哥，小弟继续尝试** |
| 排序 | 按优先级排序 | 按优先级排序 |
| 不足时 | `trigger_block = true` → 后续全部挂起 | **continue** → 后续继续检查 |
| 壁垒建立 | `_alap_blocking = true` | **无壁垒** |
| `getTaskN` | `_alap_blocking` 全局拒绝 | **无 `_alap_blocking` 检查** |

### Greedy Bypass 执行示例

```
Tick 924: 剩余能量 = 1.0mJ

  排序: task_high(500ms, 0.6mJ) → task_mid1(1000ms, 0.6mJ) → task_mid2(1000ms, 0.4mJ)

  task_high:  1.0 ≥ 0.6 ✅ 续期成功，available_energy = 0.4mJ
  task_mid1:  0.4 < 0.6 🚨 挂起 task_mid1（continue，不 break）
  task_mid2:  0.4 ≥ 0.4 ✅ 续期成功！Greedy Bypass 成功！

Tick 925: 剩余能量 = 0.4mJ

  排序: task_high(500ms, 0.6mJ) → task_mid2(1000ms, 0.4mJ)

  task_high:  0.4 < 0.6 🚨 挂起 task_high（continue，不 break）
  task_mid2:  0.4 ≥ 0.4 ✅ 续期成功！task_mid2 独占 0.4mJ！
```

---

## 测试结果

### 核心验证

| 指标 | V56 NonBlock | V57 NonBlock |
|------|-------------|-------------|
| Deadline Miss | **2** | **0** ✅ |
| task_high 最后执行 | t=924ms | **t=925ms** ✅ (+1ms) |
| 总消耗能量 | 200.2mJ | 200.2mJ |
| task_mid1 descheduled | t=924 | t=924 |

### 关键差异

V57 比 V56 更好：
- **0 deadline misses**（vs V56's 2）
- **task_high 多活了 1ms**（+0.6mJ 消耗）
- 相同的总能量消耗

### 为什么 Greedy Bypass 在当前测试中未完全触发？

在 3c5t 测试场景中：
- `task_mid2` 在 t=827 已经完成了实例 0（提前 173ms 完成，节省了 69.2mJ）
- t=924 时，`task_mid2` 不在运行中，无法参与 Greedy Bypass
- 下一个 `task_mid2` 实例在 t=1000 才到达

**Greedy Bypass 在以下场景会生效**：
- 低功耗任务（如 task_idle, unit_energy ≈ 0.1mJ）在运行中
- 高功耗任务（如 task_high, unit_energy = 0.6mJ）能量耗尽挂起
- 低功耗任务可以使用剩余能量继续执行

---

## 核心原理

### V57 的核心洞察

```
旧的错误逻辑：
  1. Tick 边界扣能量
  2. 独立检查每个任务能量
  3. 挂起能量不足的任务
  4. 注册全局耗尽闹钟
  5. 全局闹钟触发 → onEnergyDepleted() → 群体挂起
  ❌ 群体挂起覆盖了独立检查的成果
  ❌ 低优先级任务无法捡漏

V57 正确逻辑：
  1. Tick 边界扣能量
  2. 优先级排序 + Greedy Bypass
  3. 不注册任何闹钟
  4. 每个任务独立判断
  ✅ 小弟可以绕过大哥，独占剩余能量
```

---

## 结论

V57 通过三个步骤实现了 ALAP-NonBlock 的 Greedy Bypass：

1. **废除全局耗尽闹钟**：`scheduleEnergyDepletionEvent()` 改为空操作
2. **废除群体挂起**：`onEnergyDepleted()` 改为空操作
3. **Greedy Bypass 续期检查**：优先级排序 + `continue` 不 break，让低优先级任务捡漏

结果：0 deadline misses（vs V56's 2），task_high 多活 1ms，相同的能量消耗。
