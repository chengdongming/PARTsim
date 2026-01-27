# BTIE能量扣除最优方案

## 核心修改点

### 1. 能量扣除逻辑

**修改前（错误）**：
```cpp
// 计算所有任务的总能耗（运行中 + 新任务）
double total_energy = 0.0;
for (auto* task : all_tasks) {
    total_energy += calculateUnitEnergyForTask(task);
}

// 一次性扣除所有任务的能量
if (_current_energy >= total_energy) {
    _current_energy -= total_energy;  // ❌ 预扣了新任务
}
```

**修��后（正确）**：
```cpp
// 1. 只扣除运行中任务的能量
double energy_to_deduct = 0.0;
for (const auto& map_pair : running_tasks) {
    AbsRTTask* task = map_pair.second;
    if (task) {
        energy_to_deduct += calculateUnitEnergyForTask(task);
    }
}

// 扣除运行中任务的能量
if (energy_to_deduct > 0) {
    _current_energy -= energy_to_deduct;  // ✓ 只扣��行中
}

// 2. 选择K个新任务（不扣除能量）
// ...

// 3. 只检查新任务的能量，不扣除
double new_tasks_energy = 0.0;
for (auto* task : new_tasks_to_schedule) {
    new_tasks_energy += calculateUnitEnergyForTask(task);
}

// 4. 批量判断（只检查，不扣除）
if (_current_energy >= new_tasks_energy) {
    // 调度新任务
}
```

### 2. 能量不足时的处理

**修改前（错误）**：
```cpp
else {
    // 能量不足：不调度任何任务，并中断所有运行中的任务
    _batch_scheduled_this_tick = false;

    // ❌ suspend所有运行中任务
    for (AbsRTTask* task : tasks_to_interrupt) {
        _kernel->suspend(task);
    }
}
```

**修改后（正确）**：
```cpp
else {
    // 能量不足：不调度新任务，但运行中任务继续
    _batch_scheduled_this_tick = false;
    _current_batch_tasks.clear();

    // ✓ 不suspend运行中任务
    // 它们会继续执行，因为能量已经在第1步扣除过了
}
```

### 3. _current_batch_tasks的组成

**保持不变（正确）**：
```cpp
if (_current_energy >= new_tasks_energy) {
    _batch_scheduled_this_tick = true;

    // 包含运行中任务 + 新任务
    std::vector<AbsRTTask *> all_tasks_to_dispatch;
    for (auto* task : running_task_list) {
        all_tasks_to_dispatch.push_back(task);
    }
    for (auto* task : new_tasks_to_schedule) {
        all_tasks_to_dispatch.push_back(task);
    }

    _current_batch_tasks = all_tasks_to_dispatch;
}
```

## 关键改进

### 改进1：能量后扣
- ✓ 只扣除运行中任务的能量（它们已经执行了1ms）
- ✓ 新任务的能量不预先扣除
- ✓ 符合"先执行后扣费"的原则

### 改进2：精确的能量管理
- ✓ 避免重复扣除
- ✓ 能量计算准确
- ✓ 与TIE的逻辑一致

### 改进3：正确的"全有或全无"
- ✓ 能量充足：调度所有新任务
- ✓ 能量不足：只影响新任务，运行中任务继续
- ✓ 不suspend运行中任务

## 预期效果

### 能量充足场景（当前测试）
- Tick次数：100（不变）
- 总能耗：0.061380J（不变）
- 任务完成数：11（不变）

### 能量受限场景（新测试）
- 行为与TIE完全一致
- 能量管理精确
- 运行中任务不受影响

## 代码修改位置

文件：`librtsim/scheduler/gpfp_btie_scheduler.cpp`
函数：`performTickScheduling()`

行号范围：420-520（大约）
