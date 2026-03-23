# V52 NonBlock 纯正性修复

## 问题背景

在 ST-NonBlock 和 ALAP-NonBlock 调度器中，错误地引入了 Block 算法的全局锁机制，导致低功耗任务无法进行贪心旁路（Greedy Bypass），破坏了 NonBlock 的纯正性。

### 原始错误行为
- 能量耗尽时强制执行 `_current_energy = 0.0`
- 设置全局锁 `_energy_depleted = true`
- 无脑 `suspend` 所有任务
- 阻止低功耗任务（如 `task_idle`）继续抢占 CPU 消耗剩余能量

### 期望的正确行为
- 保留真实的残血电量（如 0.8 mJ）
- 不设置全局锁
- 不主动挂起所有任务，而是让 `dispatch()` 自己评估谁能跑
- 低功耗任务发现残血电量够用，就能抢占 CPU 继续运行，直到真正榨干能量

---

## 修改文件

1. `librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp`
2. `librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp`

---

## 修改内容详解

### 1. `onEnergyDepleted()` 函数重写

**修改前（错误）：**
```cpp
void onEnergyDepleted() {
    _current_energy = 0.0;        // 强制归零
    _energy_depleted = true;       // 设置全局锁
    cancelEnergyDepletionEvent();

    // 无脑挂起所有任务
    if (_kernel) {
        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            setSuspendReason(task, "energy_depleted");
            _kernel->suspend(task);
        }
    }
}
```

**修改后（正确）：**
```cpp
void onEnergyDepleted() {
    // 保留残血电量，不强制归零！
    double residual_energy = _current_energy;
    if (residual_energy < 0.0) {
        residual_energy = 0.0;
        _current_energy = 0.0;
    }

    // 严禁设置全局锁！
    // _energy_depleted = true;

    cancelEnergyDepletionEvent();

    SCHEDULER_LOG_WARNING("💡 [Xxx-NonBlock] NonBlock模式：保留残血电量 " +
                         std::to_string(residual_energy * 1000) + "mJ，重新派发让低功耗任务继续");

    // 核心：直接调用dispatch，让底层调度逻辑评估谁能跑
    if (_kernel) {
        _kernel->dispatch();
    }

    // NonBlock核心：不再预调度能量耗尽闹钟
    // EnergyCheckEvent会在每个任务运行1ms后自动检查能量
    // 直到真正耗尽为止
}
```

**关键点：**
- 不强制 `_current_energy = 0.0`，保留真实残血电量
- 不设置 `_energy_depleted = true` 全局锁
- 不无脑挂起所有任务
- 直接调用 `_kernel->dispatch()` 让调度逻辑评估谁能跑
- 不再预调度能量耗尽闹钟，由 EnergyCheckEvent 自动检查

---

### 2. `getTaskN()` 函数修复

**修改前：**
```cpp
AbsRTTask *getTaskN(unsigned int n) {
    // 全局能量锁阻止所有任务调度
    if (_energy_depleted) {
        SCHEDULER_LOG_DEBUG("💀 getTaskN: 能量已耗尽，拒绝调度");
        return nullptr;
    }
    // ... 后续调度逻辑
}
```

**修改后：**
```cpp
AbsRTTask *getTaskN(unsigned int n) {
    // NonBlock纯正性：不使用全局能量锁！
    // 即使能量很少，也让低功耗任务有机会尝试调度
    // 每个任务会自己评估能量是否足够（在下面的循环中）

    SCHEDULER_LOG_DEBUG(std::string("🔍 getTaskN(") + std::to_string(n) + ") 被调用" +
                       " 当前能量: " + std::to_string(_current_energy) + "J");
    // ... 后续调度逻辑
}
```

**关键点：**
- 移除 `_energy_depleted` 全局检查
- 让每个任务在循环中自己评估能量是否足够

---

### 3. Tick 处理中的 `_energy_depleted` 检查移除

**修改前：**
```cpp
_last_tick_time = current_time;

// 能量不足时设置全局锁
if (_energy_depleted && _current_energy > 0.000001) {
    _energy_depleted = false;
    SCHEDULER_LOG_INFO("🔋 检测到能量恢复，解除历史energy_depleted标志");
}

if (_energy_depleted && _current_energy < 0.000001) {
    SCHEDULER_LOG_INFO("💀 当前tick能量为0，跳过任务调度");
    return;  // 直接返回，阻止所有任务调度
}
```

**修改后：**
```cpp
_last_tick_time = current_time;

// NonBlock纯正性：不使用全局能量锁！
// 即使能量很少，也让低功耗任务有机会尝试调度
// 如果能量真的为0且没有任务能运行，getTaskN会返回nullptr

// 确保能量不超过最大容量
if (_current_energy > _max_energy) {
    _current_energy = _max_energy;
}
```

**关键点：**
- 移除 `_energy_depleted` 标志的检查和设置
- 不再因为能量低而直接返回跳过调度

---

### 4. 续期能量检查中的全局锁移除

**修改前（ALAP-NonBlock）：**
```cpp
if (_current_energy < unit_energy - EPSILON) {
    // 设置全局锁
    if (!_energy_depleted) {
        _energy_depleted = true;
        _current_energy = 0.0;  // 强制归零
        SCHEDULER_LOG_WARNING("💀 能量耗尽，设置_energy_depleted标志");
    }

    // 挂起任务
    tasks_to_suspend.push_back(task);
}
```

**修改后：**
```cpp
if (_current_energy < unit_energy - EPSILON) {
    // NonBlock纯正性修复：不设置全局能量锁！
    // 只挂起当前这个能量不足的任务，保留残血电量给低功耗任务
    // _energy_depleted = true; // 严禁设置！
    // _current_energy = 0.0;   // 严禁归零！保留残血电量！

    // 只挂起这个高功耗任务
    tasks_to_suspend.push_back(task);
    SCHEDULER_LOG_WARNING("⚠️ 续期能量不足，将挂起: " + getTaskName(task) +
                         " (保留残血电量给低功耗任务)");
}
```

**关键点：**
- 不设置 `_energy_depleted = true`
- 不强制 `_current_energy = 0.0`
- 只挂起当前无法续期的高功耗任务

---

### 5. EnergyCheckEvent 中的全局锁移除

**修改前：**
```cpp
if (current_energy <= unit_energy + EPSILON) {
    // 标记能量耗尽
    _scheduler->_energy_depleted = true;
    _scheduler->_current_energy = 0.0;  // 强制归零

    // 挂起任务
    if (_cpu) {
        _scheduler->setSuspendReason(_task, "insufficient_energy");
        _scheduler->_kernel->suspend(_task);
    }
    return;
}
```

**修改后：**
```cpp
if (current_energy <= unit_energy + EPSILON) {
    // NonBlock纯正性修复：不设置全局能量耗尽标志！
    // _scheduler->_energy_depleted = true; // 严禁设置！

    // NonBlock纯正性修复：保留残血电量！
    // _scheduler->_current_energy = 0.0; // 严禁归零！
    SCHEDULER_LOG_INFO("💡 NonBlock模式：保留残血电量 " +
                       std::to_string(current_energy * 1000) + " mJ 给低功耗任务");

    // 只挂起这个高功耗任务
    if (_cpu) {
        _scheduler->setSuspendReason(_task, "insufficient_energy");
        _scheduler->_kernel->suspend(_task);
    }

    // NonBlock核心：挂起当前任务后，触发dispatch让低功耗任务有机会运行
    if (_scheduler->_kernel) {
        _scheduler->_kernel->dispatch();
    }
    return;
}
```

**关键点：**
- 不设置全局锁 `_energy_depleted`
- 保留残血电量
- 挂起高功耗任务后，立即调用 `dispatch()` 让低功耗任务有机会抢占 CPU

---

## NonBlock 纯正性核心原则

### 1. 保留真实残血电量
```
低功耗任务（如 idle）可能只需要 0.05mJ 就能运行
不能因为 "能量不足" 就强制归零，阻止低功耗任务继续运行
```

### 2. 不设置全局锁
```
_energy_depleted = true 会阻止所有任务调度
应该让每个任务自己评估能量是否足够
```

### 3. 不无脑挂起所有任务
```
正确做法：只挂起当前无法续期的高功耗任务
让低功耗任务有机会抢占 CPU，继续消耗剩余能量
```

### 4. 贪心旁路（Greedy Bypass）机制
```
能量耗尽时：
1. 不设置全局锁
2. 不强制归零能量
3. 只挂起高功耗任务
4. 调用 dispatch() 让低功耗任务评估是否能运行
5. 低功耗任务发现残血电量够用 → 成功抢占 CPU → 继续消耗能量
6. 直到能量真正被榨干
```

---

## 测试结果对比

### 测试配置
- 系统：3核
- 任务：5个（task_high, task_mid1, task_mid2, task_low, task_idle）
- 仿真时长：3000ms
- 初始能量：0.5J

### V51（修复前）
| 算法 | 任务完成数 | 总消耗能量 | 剩余能量 | 追踪文件大小 |
|------|-----------|-----------|---------|-------------|
| ST Block | 2 | 0.4992J | 0J | 8546 bytes |
| ST NonBlock | 2 | 0.4992J | 0J | 8546 bytes |
| ST Sync | 15 | 0J | 0.5J | 10547 bytes |

### V52（修复后）
| 算法 | 任务完成数 | 总消耗能量 | 剩余能量 | 追踪文件大小 |
|------|-----------|-----------|---------|-------------|
| ST Block | 2 | 0.4992J | 0J | 8546 bytes |
| **ST NonBlock** | **3** | **0.5J** | **0J** | **68359 bytes** |
| ST Sync | 15 | 0J | 0.5J | 10547 bytes |

### 修复效果
- NonBlock 任务完成数：**2 → 3**（提升 50%）
- 追踪文件大小：**8546 → 68359 bytes**（增长 8 倍）
- 能量完全耗尽：剩余能量从 0J 变为 0J（与 Block 一致）
- 证明低功耗任务现在能够继续运行，贪心旁路机制正常工作

---

## 总结

本次修复恢复了 NonBlock 调度器的"贪心旁路"纯正性：

1. **保留残血电量**：不强制归零，让低功耗任务有机会继续运行
2. **移除全局锁**：不让 `_energy_depleted` 阻止所有任务调度
3. **选择性挂起**：只挂起无法续期的高功耗任务
4. **自动派发**：调用 `dispatch()` 让调度逻辑评估谁能跑
5. **完全榨干**：低功耗任务持续运行，直到能量真正耗尽

修复后，NonBlock 算法相比 Block 算法展现了更好的能效：同样的能量消耗了更多任务。
