# V51 能量耗尽预测修复方案 - ST 家族同步文档

## 1. 修复背景

### 1.1 Bug描述：虚空借电
在某些情况下，当电池即将耗尽时，任务可能会"惯性"跑完超出电池实际容量的时间，导致能量出现负值或"虚空借电"现象。

### 1.2 修复方案 V51
引入 `EnergyDepletedEvent` 能量耗尽预测事件：
- 当系统状态改变（dispatch/suspend）时，计算当前总功耗和剩余能量
- 预测能量耗尽的精确时刻
- 在事件队列中注册高优先级的能量耗尽事件
- 事件触发时强制挂起所有运行中任务

### 1.3 关键修复点
1. **能量底线是 0，不是 1.0**：`if (_current_energy < 0.0) _current_energy = 0.0;`
2. **只在功耗状态变化时预测**：不在每个tick的能量扣除循环中定闹钟
3. **每个tick只预测一次**：使用 `_last_prediction_tick` 防止同一tick内重复预测

## 2. ALAP 家族修复结果（已完成）

| 调度器 | 状态 | 事件类名 | 测试结果 |
|--------|------|----------|----------|
| ALAP-Block | ✅ 完成 | `ALAPBlockEnergyDepletedEvent` | 任务完成2, 能量归零 |
| ALAP-NonBlock | ✅ 完成 | `ALAPNonBlockEnergyDepletedEvent` | 任务完成2, 能量归零 |
| ALAP-Sync | ✅ 完成 | `ALAPSyncEnergyDepletedEvent` | 任务完成2, 能量归零 |

## 3. ST 家族修复方案

### 3.1 需要修改的文件

| 调度器 | .hpp 文件 | .cpp 文件 |
|--------|-----------|-----------|
| ST-Block | `gpfp_st_block_scheduler.hpp` | `gpfp_st_block_scheduler.cpp` |
| ST-NonBlock | `gpfp_st_nonblock_scheduler.hpp` | `gpfp_st_nonblock_scheduler.cpp` |
| ST-Sync | `gpfp_st_sync_scheduler.hpp` | `gpfp_st_sync_scheduler.cpp` |

### 3.2 .hpp 修改模板

```cpp
// 在被注释掉的 EnergyCheckEvent 之后添加：

// =====================================================
// ⭐ 能量耗尽预测事件（虚空借电Bug修复）
// 当系统预测到电池将在某时刻耗尽时，在事件队列中插入此事件
// 确保任务在电池真正耗尽时被正确中断，而不是"惯性"跑完
// =====================================================
class ST<Xxx>EnergyDepletedEvent : public MetaSim::Event {
private:
    ST<Xxx>Scheduler *_scheduler;

public:
    MetaSim::Tick _scheduled_depletion_time;  // 预测的耗尽时刻
    double _energy_at_prediction;               // 预测时的能量值

public:
    ST<Xxx>EnergyDepletedEvent(ST<Xxx>Scheduler *scheduler);
    void doit() override;

    MetaSim::Tick getScheduledDepletionTime() const { return _scheduled_depletion_time; }
    double getEnergyAtPrediction() const { return _energy_at_prediction; }
};
```

```cpp
// 在类成员变量中添加：
private:
    // ... 其他成员变量 ...

    // ⭐ 能量耗尽预测事件（Bug修复：防止虚空借电）
    ST<Xxx>EnergyDepletedEvent *_energy_depleted_event = nullptr;
    MetaSim::Tick _last_prediction_tick = -1;  // ⭐ 上次更新能量预测的tick

// 在私有方法区添加：
    // ⭐ 能量耗尽预测与事件注册（Bug修复）
    double calculateTotalPowerConsumption();                              // 计算当前总功耗
    MetaSim::Tick predictTimeToDepletion(double energy, double power);    // 预测能量耗尽时间
    void scheduleEnergyDepletionEvent(MetaSim::Tick depletion_time);     // 注册能量耗尽事件
    void cancelEnergyDepletionEvent();                                    // 取消能量耗尽事件

// 在public方法区添加：
public:
    // ⭐ 能量耗尽处理（public供ST<Xxx>EnergyDepletedEvent调用）
    void onEnergyDepleted();

// 在友元类声明中添加：
    friend class ST<Xxx>EnergyDepletedEvent;  // ⭐ Bug修复：能量耗尽预测事件
```

### 3.3 .cpp 修改模板

```cpp
// 在 Event 实现区域添加：

// =====================================================
// ⭐ EnergyDepletedEvent 实现（Bug修复：防止虚空借电）
// =====================================================

ST<Xxx>EnergyDepletedEvent::ST<Xxx>EnergyDepletedEvent(ST<Xxx>Scheduler *scheduler)
    : MetaSim::Event("ST<Xxx>EnergyDepletedEvent", MetaSim::Event::_DEFAULT_PRIORITY - 100),
      _scheduler(scheduler),
      _scheduled_depletion_time(0),
      _energy_at_prediction(0.0) {
    // ⭐ 最高优先级（_DEFAULT_PRIORITY - 100 确保在其他事件之前处理）
}

void ST<Xxx>EnergyDepletedEvent::doit() {
    if (!_scheduler) return;
    _scheduler->onEnergyDepleted();
}
```

```cpp
// 在构造函数中初始化：
_energy_depleted_event = new ST<Xxx>EnergyDepletedEvent(this);

// 在析构函数中清理：
if (_energy_depleted_event) {
    _energy_depleted_event->drop();
    delete _energy_depleted_event;
    _energy_depleted_event = nullptr;
}
```

```cpp
// 在能量扣除位置添加软性守卫：
_current_energy -= unit_energy;
// ⭐ V51修复：软性能量守卫（不中断仿真）
if (_current_energy < 0.0) {
    SCHEDULER_LOG_WARNING("⚠️ [ST<Xxx>] 能量透支！强制归零: " +
                         getTaskName(task) + " 透支=" +
                         std::to_string(-_current_energy * 1000) + " mJ");
    _current_energy = 0.0;
}
```

```cpp
// 在文件末尾添加预测方法实现：

// =====================================================
// ⭐ 能量耗尽预测机制（Bug修复：防止虚空借电）
// =====================================================

double ST<Xxx>Scheduler::calculateTotalPowerConsumption() {
    if (!_kernel) {
        return 0.0;
    }

    const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
    double total_power = 0.0;

    for (const auto& [cpu, task] : running_tasks_map) {
        if (!task || !task->isExecuting()) continue;
        total_power += calculateUnitEnergyForTask(task);
    }

    return total_power;
}

MetaSim::Tick ST<Xxx>Scheduler::predictTimeToDepletion(double energy, double power) {
    if (power <= 0.0 || energy <= 0.0) {
        return MetaSim::Tick(-1);  // 无法预测
    }
    // time_to_deplete = energy / power (单位：ms)
    // 返回从当前时间算起，还能运行多少ms
    double time_ms = energy / power;
    return static_cast<MetaSim::Tick>(ceil(time_ms));
}

void ST<Xxx>Scheduler::scheduleEnergyDepletionEvent(MetaSim::Tick time_until_depletion) {
    if (!_energy_depleted_event) return;

    Tick current_time = SIMUL.getTime();
    Tick depletion_time = current_time + time_until_depletion;

    if (time_until_depletion <= 0) {
        // 能量已经耗尽，立即触发
        SCHEDULER_LOG_WARNING("⚠️ [ST<Xxx>] 能量已耗尽，立即触发耗尽处理");
        onEnergyDepleted();
        return;
    }

    // 取消旧的事件
    _energy_depleted_event->drop();

    // 设置耗尽时刻
    _energy_depleted_event->_scheduled_depletion_time = depletion_time;
    _energy_depleted_event->_energy_at_prediction = _current_energy;

    // 注册新事件
    _energy_depleted_event->post(depletion_time);

    SCHEDULER_LOG_INFO("⚡ [ST<Xxx>] ⭐ 注册能量耗尽预测事件: "
                      "当前=" + std::to_string(static_cast<int64_t>(current_time)) + "ms, "
                      "预测耗尽=" + std::to_string(static_cast<int64_t>(depletion_time)) + "ms, "
                      "剩余=" + std::to_string(static_cast<int64_t>(time_until_depletion)) + "ms, "
                      "剩余能量=" + std::to_string(_current_energy * 1000) + "mJ, "
                      "总功耗=" + std::to_string(calculateTotalPowerConsumption() * 1000) + "mJ/ms");
}

void ST<Xxx>Scheduler::cancelEnergyDepletionEvent() {
    if (_energy_depleted_event) {
        _energy_depleted_event->drop();
    }
}

void ST<Xxx>Scheduler::onEnergyDepleted() {
    Tick current_time = SIMUL.getTime();

    SCHEDULER_LOG_WARNING("💀💀💀 [ST<Xxx>] ⭐ 能量耗尽事件触发！时间=" +
                         std::to_string(static_cast<int64_t>(current_time)) + "ms, " +
                         "剩余能量=" + std::to_string(_current_energy * 1000) + "mJ");

    // ⭐ V51修复：软性守卫 - 不使用assert，直接强制归零
    if (_current_energy < 0.0) {
        SCHEDULER_LOG_WARNING("⚠️ [ST<Xxx>] 检测到能量透支！强制归零");
    }

    // 强制清零能量
    _current_energy = 0.0;
    _energy_depleted = true;

    // 挂起所有运行中的任务
    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> tasks_to_suspend;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            tasks_to_suspend.push_back(task);
        }

        for (AbsRTTask *task : tasks_to_suspend) {
            setSuspendReason(task, "energy_depleted");
            _kernel->suspend(task);
            SCHEDULER_LOG_WARNING("💀 [ST<Xxx>] 强制挂起任务(能量耗尽): " + getTaskName(task));
        }
    }

    // 取消能量耗尽事件
    cancelEnergyDepletionEvent();

    SCHEDULER_LOG_WARNING("💀 [ST<Xxx>] 能量耗尽处理完成，跳过后续调度");
}
```

### 3.4 预测调用位置

在 `performTickScheduling()` 函数末尾，tick 完成前统一调用：

```cpp
// ⭐ V51修复：能量耗尽预测 - 每个tick只更新一次
if (_last_prediction_tick == current_time) {
    SCHEDULER_LOG_DEBUG("⏭️ [ST<Xxx>] 跳过重复预测（本tick已更新）");
} else {
    _last_prediction_tick = current_time;
    cancelEnergyDepletionEvent();
    double total_power = calculateTotalPowerConsumption();
    if (total_power > 0.0 && _current_energy > 0.0) {
        MetaSim::Tick time_to_deplete = predictTimeToDepletion(_current_energy, total_power);
        scheduleEnergyDepletionEvent(time_to_deplete);
    }
}
```

## 4. 执行计划

1. [x] 修改 ST-Block .hpp 文件
2. [x] 修改 ST-Block .cpp 文件
3. [x] 修改 ST-NonBlock .hpp 文件
4. [x] 修改 ST-NonBlock .cpp 文件
5. [x] 修改 ST-Sync .hpp 文件
6. [x] 修改 ST-Sync .cpp 文件
7. [x] 编译测试 ST 家族

## 5. 验证标准

- 编译通过，无链接错误
- 0.5J 初始能量，2000ms 仿真，任务完成数与能量消耗合理
- 无能量透支警告（`_current_energy` 始终 >= 0）
- 能量耗尽预测事件正确注册和触发
