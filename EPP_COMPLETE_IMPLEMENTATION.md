# EPP调度器完整实现指南
## Energy-aware Preemptive Priority Scheduler - 全部实现细节

---

## 📋 目录

1. [算法概述](#算法概述)
2. [核心设计原则](#核心设计原则)
3. [能量记账机制（方案3）](#能量记账机制方案3)
4. [保守预测策略（方案A）](#保守预测策略方案a)
5. [级联调度控制](#级联调度控制)
6. [抢占机制设计](#抢占机制设计)
7. [数据结构定义](#数据结构定义)
8. [完整代码修改方案](#完整代码修改方案)
9. [测试验证方案](#测试验证方案)

---

## 算法概述

### 定义
**EPP调度器**（Energy-aware Preemptive Priority Scheduler）是一个能量感知的抢占式优先级调度算法，专为**太阳能供电的多核实时系统**设计。

### 核心特性
- ✅ **能量硬约束**：能量不足时绝对不调度任务
- ✅ **抢占式调度**：Tick级（1ms）抢占粒度，无分片边界限制
- ✅ **优先级驱动**：基于RM策略（周期越短优先级越高）
- ✅ **级联调度**：能量充足时连续调度多个任务到多个CPU
- ✅ **主动能量管理**：预测能量恢复时间，定时器主动唤醒
- ✅ **多核支持**：支持多CPU并行调度

### 适用场景
- 太阳能供电的实时嵌入式系统
- 能量采集系统（Energy Harvesting Systems）
- 电池供电的实时任务调度
- 需要严格实时性保证的能量受限环境

---

## 核心设计原则

### P1: 能量硬约束（Energy Hard Constraint）
```
能量_当前 + 能量_预测收集 >= 任务能耗  → 可以调度
能量_当前 + 能量_预测收集 <  任务能耗  → 不能调度
```
**含义**：能量是绝对的硬约束，不存在任何"借用"或"透支"机制。使用前瞻性判断，考虑任务执行期间能收集的能量。

### P2: 优先级绝对优先（Priority First）
```
检查顺序：就绪队列最高优先级 → 次优先级 → ...
停止条件：最高优先级任务不能调度 → 立即停止
```
**含义**：当最高优先级任务因能量不足无法调度时，即使能量足够调度低优先级任务，也不调度。

### P3: 级联调度（Cascading Scheduling）
```
调度成功 → 扣减能量 → 继续检查下一个任务
停止条件：(就绪队列空) OR (能量不足) OR (无空闲CPU)
```
**含义**：一次调度过程中，尽可能多地调度任务，充分利用能量和CPU资源。

### P4: Tick级抢占（Tick-level Preemption）
```
抢占时机：任务到达的任意Tick时刻（1ms粒度）
抢占条件：(优先级更高) AND (能量足够)
```
**含义**：不等待分片边界，高优先级任务到达立即检查抢占。

### P5: 前瞻性能量判断与预扣减（Look-ahead Energy Check & Pre-deduction）
```
调度时刻：前瞻性判断（能量_当前 + 能量_预测收集 >= 能量_消耗）
扣减时机：调度决策时立即扣减（在getTaskN()/getFirst()中）
判断公式：energy_after_task = energy_current + energy_predicted - energy_consumption
可调度条件：energy_after_task >= 0
```
**含义**：
- 调度决策时进行前瞻性预测
- 预测通过后立即扣减能量（预扣减策略）
- 考虑任务执行期间能收集的太阳能
- 扣减位置：getTaskN()/getFirst()方法中，在返回任务前
- 与CASCADE/ASAP的区别：CASCADE/ASAP使用延迟扣减（任务执行时扣减）

**扣减触发点明确说明**：
- ✅ 在`getTaskN()`中调用`consumeEnergy()`（级联调度时）
- ✅ 在`getFirst()`中调用`consumeEnergy()`（获取第一个任务时）
- ⚠️ 不在`notify()`中扣减能量（避免重复扣减）
- ⚠️ 不在`dispatchTask()`中扣减（只负责分配任务到CPU）

### P6: 主动能量管理（Proactive Energy Management）
```
能量不足：计算恢复时间 → 设置定时器 → 定时器唤醒调度器
唤醒时机：能量正好够调度最高优先级任务
```
**含义**：主动预测能量恢复时间，被动等待转为主动唤醒。

---

## 能量记账机制（方案3）

### 问题背景

**原始设计的缺陷**：
```
如果预扣除完整的WCET能量，被抢占时能量账目会不一致：

时刻 T=0ms:
  - task_high (wcet=100ms, 能量=1.0J) 被调度
  - getTaskN(0) 预扣减 1.0J
  - 开始执行

时刻 T=50ms:
  - task_urgent 到达，抢占 task_high
  - 问题：task_high 只执行了 50ms(0.5J)，但已扣减 1.0J
  - 能量账目：多扣减了 0.5J ❌
```

### 解决方案：能量记账 + 结算机制

#### 1. 能量账目数据结构

```cpp
struct TaskEnergyAccount {
    double prepaid;         // 预扣减能量（完整WCET）
    double consumed;        // 实际消耗能量（累计）
    double harvested;       // 执行期间实际收集能量
    double predicted;       // 预测收集能量（用于对比）
    MetaSim::Tick start_time;  // 任务开始时间
    MetaSim::Tick last_unit_time;  // 上次单位时间扣减时间
};

class EPPScheduler : public Scheduler {
private:
    // 能量账目映射
    std::map<AbsRTTask*, TaskEnergyAccount> _energy_accounts;

    // 系统能量状态
    double _current_energy;         // 当前可用能量
    double _initial_energy;         // 初始能量
    double _max_energy;             // 最大能量容量
};
```

#### 2. getTaskN()：预扣减并记账

```cpp
AbsRTTask* EPPScheduler::getTaskN(unsigned int n) {
    // 1. 调用基类获取任务
    AbsRTTask* task = Scheduler::getTaskN(n);
    if (!task) return nullptr;

    // 2. 获取任务参数
    auto wcet_it = _task_wcets.find(task);
    if (wcet_it == _task_wcets.end()) {
        SCHEDULER_LOG_WARNING("未找到任务WCET: " + getTaskShortName(task));
        return nullptr;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    // 3. 保守预测能量收集
    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);

    // 4. 计算任务总能量
    double total_energy = calculateTaskEnergy(task, wcet);

    // 5. 前瞻性判断
    double current_energy = getCurrentEnergy();
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("🚫 级联停止：前瞻性能量不足");
        SCHEDULER_LOG_INFO("   当前: " + std::to_string(current_energy) + "J" +
                          " 预测: " + std::to_string(predicted) + "J" +
                          " 需要: " + std::to_string(total_energy) + "J" +
                          " 缺口: " + std::to_string(-energy_after) + "J");
        return nullptr; // 停止级联调度
    }

    // 6. 预扣减能量
    if (!consumeEnergy(total_energy, getTaskShortName(task) + "_prepaid")) {
        SCHEDULER_LOG_ERROR("❌ 预扣减能量失败");
        return nullptr;
    }

    // 7. 创建能量账目
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    account.last_unit_time = current_time;

    _energy_accounts[task] = account;

    SCHEDULER_LOG_INFO("✅ 预扣减成功: " + getTaskShortName(task) +
                      " 预扣减: " + std::to_string(total_energy) + "J" +
                      " 当前能量: " + std::to_string(getCurrentEnergy()) + "J");

    return task;
}
```

#### 3. notify()：记录实际消耗（不扣减）

```cpp
void EPPScheduler::notify(AbsRTTask *task) {
    if (!task) return;

    std::string task_name = getTaskShortName(task);
    MetaSim::Tick current_time = SIMUL.getTime();

    // 1. ⭐ 关键修复：收集能量但不扣减（已预扣减）
    double harvested = updateEnergyContinuously(current_time);
    if (harvested > 0.001) {
        SCHEDULER_LOG_INFO("🔋 notify()收集能量: " + std::to_string(harvested) + "J");
    }

    // 2. 记录单位时间消耗
    double unit_energy = getUnitTimeEnergy(task);

    auto account_it = _energy_accounts.find(task);
    if (account_it != _energy_accounts.end()) {
        // 记录实际消耗
        account_it->second.consumed += unit_energy;

        // 记录收集的能量
        account_it->second.harvested += harvested;

        SCHEDULER_LOG_DEBUG("📊 能量账目: " + task_name +
                          " 已消耗: " + std::to_string(account_it->second.consumed) + "J" +
                          " 已收集: " + std::to_string(account_it->second.harvested) + "J" +
                          " 预扣减: " + std::to_string(account_it->second.prepaid) + "J");
    }

    // 3. 更新任务状态
    if (std::find(_running_tasks.begin(), _running_tasks.end(), task) == _running_tasks.end()) {
        _running_tasks.push_back(task);
    }

    // 4. 调用父类notify让任务开始执行
    Scheduler::notify(task);
}
```

#### 4. 结算能量账目（任务完成或被抢占）

```cpp
void EPPScheduler::settleEnergyAccount(AbsRTask* task) {
    auto account_it = _energy_accounts.find(task);
    if (account_it == _energy_accounts.end()) {
        SCHEDULER_LOG_WARNING("⚠️ 能量账目不存在: " + getTaskShortName(task));
        return;
    }

    TaskEnergyAccount& account = account_it->second;
    std::string task_name = getTaskShortName(task);

    // 1. 计算能量差额
    double actual_consumed = account.consumed;
    double actual_harvested = account.harvested;
    double prepaid = account.prepaid;
    double predicted = account.predicted;

    // 2. 能量回退：如果实际消耗 < 预扣减
    double refund = 0.0;
    if (actual_consumed < prepaid) {
        refund = prepaid - actual_consumed;

        // 回退能量到系统
        std::lock_guard<std::recursive_mutex> lock(_energy_mutex);
        if (_use_local_energy) {
            _local_energy += refund;
        } else {
            EnergyBridge::getInstance().addEnergy(refund);
        }

        SCHEDULER_LOG_INFO("💰 能量回退: " + task_name +
                          " 预扣减: " + std::to_string(prepaid) + "J" +
                          " 实际消耗: " + std::to_string(actual_consumed) + "J" +
                          " 回退: " + std::to_string(refund) + "J");
    }

    // 3. 考虑实际收集能量
    double net_harvest = actual_harvested - predicted;
    if (net_harvest > 0.001) {
        SCHEDULER_LOG_INFO("☀️ 净能量收益: " + task_name +
                          " 实际收集: " + std::to_string(actual_harvested) + "J" +
                          " 预测收集: " + std::to_string(predicted) + "J" +
                          " 净收益: " + std::to_string(net_harvest) + "J");
    }

    // 4. 打印能量账目总结
    SCHEDULER_LOG_INFO("📊 能量账目结算: " + task_name);
    SCHEDULER_LOG_INFO("  预扣减: " + std::to_string(prepaid) + "J");
    SCHEDULER_LOG_INFO("  实际消耗: " + std::to_string(actual_consumed) + "J");
    SCHEDULER_LOG_INFO("  实际收集: " + std::to_string(actual_harvested) + "J");
    SCHEDULER_LOG_INFO("  预测收集: " + std::to_string(predicted) + "J");
    SCHEDULER_LOG_INFO("  能量回退: " + std::to_string(refund) + "J");
    SCHEDULER_LOG_INFO("  净收益: " + std::to_string(net_harvest) + "J");

    // 5. 清除账目
    _energy_accounts.erase(account_it);
}
```

#### 5. 任务完成时结算

```cpp
void EPPScheduler::completeTaskExecution(AbsRTTask *task) {
    if (!task) return;

    std::string task_name = getTaskShortName(task);

    // 1. 结算能量账目
    settleEnergyAccount(task);

    // 2. 从运行队列移除
    auto running_it = std::find(_running_tasks.begin(), _running_tasks.end(), task);
    if (running_it != _running_tasks.end()) {
        _running_tasks.erase(running_it);
    }

    // 3. 释放核心
    for (auto &pair : _core_assignments) {
        if (pair.second == task) {
            pair.second = nullptr;
        }
    }

    // 4. 从基类队列移除
    extract(task);

    // 5. 更新统计
    _stats.total_task_completions++;

    // 6. 处理周期性任务
    auto period_it = _task_periods.find(task);
    if (period_it != _task_periods.end() && period_it->second > 0) {
        // 安排下一次激活
        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t next_activation = static_cast<int64_t>(current_time) + period_it->second;
        schedulePreciseActivationEvent(task, next_activation);

        SCHEDULER_LOG_INFO("周期性任务 " + task_name + " 下次激活: " +
                          std::to_string(next_activation) + "ms");
    } else {
        // 非周期性任务，标记完成
        _completed_tasks.insert(task);
        _active_tasks.erase(task);
    }

    // 7. 检查等待队列并触发调度
    double current_energy = getCurrentEnergy();
    int restored = requeueWaitingTasks(current_energy, nullptr);

    if (restored > 0) {
        MRTKernel *kernel = getKernel();
        if (kernel) {
            SCHEDULER_LOG_INFO("🚀 任务完成，触发dispatch");
            kernel->dispatch();
        }
    }
}
```

---

## 保守预测策略（方案A - 基于NASA太阳能数据）

### 问题背景

**线性预测的问题**：
```cpp
// 简单的线性预测
double predict = current_power * (wcet / 1000.0);
```

**缺陷**：
- 太阳能功率在WCET期间可能波动
- 过于乐观：预测0.6J，实际只收集0.3J → 能量不足 ❌

### 解决方案：保守预测 + 简化安全���数

**⭐ 重要说明**：本项目使用NASA真实太阳能数据，已包含：
- ✅ 时间因素（太阳角度、日照时间）
- ✅ 季节因素（按日期/季节提供）
- ✅ 地理位置（项目所在地的真实数据）

因此**不需要**额外的：
- ❌ 时间段修正（早晚、中午）
- ❌ 季节修正（春、夏、秋、冬）
- ❌ 天气修正（云量数据）

只需要一个**简化的安全系数**来应对：
- 云层遮挡（NASA数据可能是晴空模型）
- 短期波动（WCET期间的功率变化）
- 测量误差（实际采集与理论值的差异）

### 代码实现

```cpp
double EPPScheduler::predictEnergyHarvestConservative(
    MetaSim::Tick wcet,
    MetaSim::Tick current_time) {

    if (wcet <= 0) return 0.0;

    // 1. 获取当前太阳能功率（基于NASA真实数据）
    TimeMs adjusted_time = getAdjustedTime(current_time);
    double current_power = EnergyBridge::getInstance().getHarvestingRate(adjusted_time);

    if (current_power <= 0.0) {
        // 夜晚或无太阳能
        return 0.0;
    }

    // 2. ⭐ 保守安全系数（简化版）
    // 原因：NASA数据已经是真实的太阳能功率，但为考虑：
    // - 云层遮挡（NASA数据可能是晴空条件）
    // - 短期波动（WCET期间的功率变化）
    // - 测量误差
    // 使用0.85的保守系数
    double safety_factor = 0.85;  // 保守估计为85%

    // 3. 计算保守预测
    double conservative_power = current_power * safety_factor;
    double predicted_energy = conservative_power * (wcet / 1000.0);

    SCHEDULER_LOG_INFO("🔮 保守能量预测（基于NASA数据）:");
    SCHEDULER_LOG_INFO("  WCET: " + std::to_string(static_cast<int64_t>(wcet)) + "ms");
    SCHEDULER_LOG_INFO("  当前功率(NASA): " + std::to_string(current_power) + "W");
    SCHEDULER_LOG_INFO("  安全系数: " + std::to_string(safety_factor));
    SCHEDULER_LOG_INFO("  保守功率: " + std::to_string(conservative_power) + "W");
    SCHEDULER_LOG_INFO("  预测能量: " + std::to_string(predicted_energy) + "J");

    return predicted_energy;
}
```

### 为什么只需要0.85的安全系数？

**原因分析**：

1. **NASA数据已包含时间因素**：
   - NASA数据本身就考虑了太阳角度、日照时间
   - 不需要额外的时间段修正（早晚、中午）

2. **NASA数据已包含季节因素**：
   - NASA数据本身就是按日期/季节提供的
   - 不需要额外的季节修正

3. **只需要应对短期不确定性**：
   - **云层遮挡**：NASA数据可能是晴空模型，实际可能有云
   - **短期波动**：100ms-500ms的WCET期间，功率可能波动
   - **测量误差**：实际采集系统与理论值的差异

### 安全系数选择

- **0.85** 是一个经验值
- 可以根据实际测试结果调整：
  - 如果频繁能量不足 → 降低到 0.8
  - 如果太保守浪费机会 → 提高到 0.9

    Tick wcet = static_cast<Tick>(wcet_it->second);

    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(first_task, wcet);
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("❌ getFirst: 前瞻性能量不足");
        return nullptr;
    }

    // 预扣减
    if (!consumeEnergy(total_energy, getTaskShortName(first_task))) {
        return nullptr;
    }

    // 创建能量账目
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    _energy_accounts[first_task] = account;

    return first_task;
}

// getTaskN() - 第n个任务（n>=1）
AbsRTTask* EPPScheduler::getTaskN(unsigned int n) {
    AbsRTTask* task = Scheduler::getTaskN(n);
    if (!task) return nullptr;

    // 前瞻性能量判断
    auto wcet_it = _task_wcets.find(task);
    if (wcet_it == _task_wcets.end()) {
        return nullptr;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(task, wcet);

    double current_energy = getCurrentEnergy();
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("❌ getTaskN(#" + std::to_string(n) + "): 前瞻性能量不足");
        SCHEDULER_LOG_INFO("   停止级联调度");
        return nullptr;  // ⭐ 停止级联
    }

    // 预扣减
    if (!consumeEnergy(total_energy, getTaskShortName(task))) {
        return nullptr;
    }

    // 创建能量账目
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    _energy_accounts[task] = account;

    SCHEDULER_LOG_INFO("✅ getTaskN(#" + std::to_string(n) + "): " +
                      getTaskShortName(task) +
                      " 当前能量: " + std::to_string(getCurrentEnergy()) + "J" +
                      " ⭐ 级联继续");

    return task;
}
```

### schedule()简化实现

```cpp
void EPPScheduler::schedule() {
    MetaSim::Tick current_time = SIMUL.getTime();
    int64_t current_ms = static_cast<int64_t>(current_time);

    SCHEDULER_LOG_INFO("🔄 [EPP] ===== 开始调度 ===== @ " + std::to_string(current_ms) + "ms");

    // 1. 收集太阳能
    double harvested = updateEnergyContinuously(current_time);
    if (harvested > 0.001) {
        SCHEDULER_LOG_INFO("☀️ 收集能量: " + std::to_string(harvested) + "J");
    }

    // 2. 检查等待队列（能量恢复）
    double current_energy = getCurrentEnergy();
    int restored = requeueWaitingTasks(current_energy, nullptr);
    if (restored > 0) {
        SCHEDULER_LOG_INFO("✅ 恢复了 " + std::to_string(restored) + " 个任务到就绪队列");
    }

    // 3. 触发内核dispatch（级联调度）
    MRTKernel* kernel = getKernel();
    if (kernel) {
        SCHEDULER_LOG_INFO("🚀 触发dispatch（级联调度）");
        kernel->dispatch();  // 内核会循环调用getTaskN(0), getTaskN(1), ...
    }

    // 4. 如果没有调度任何任务且就绪队列非空，启动能量恢复
    if (getSize() > 0 && getCurrentEnergy() <= 0.001) {
        SCHEDULER_LOG_INFO("⏳ 能量不足，启动能量恢复");
        startEnergyRecovery();
    }

    SCHEDULER_LOG_INFO("🏁 [EPP] ===== 调度结束 =====");
}
```

---

## 抢占机制设计

### 抢占触发时机

**内核负责触发，调度器负责判断逻辑**：

1. **新任务到达**：内核 `onArrival()` → 调度器 `insert()` → 检查抢占
2. **任务结束**：内核 `onEnd()` → 调度器 `extract()` → 触发dispatch
3. **能量恢复**：恢复事件 → 调度器 → 触发dispatch

### 抢占判断逻辑

```cpp
// 在insert()中：新任务到达时检查抢占
void EPPScheduler::insert(AbsRTTask *new_task) {
    if (!new_task) return;

    std::string new_task_name = getTaskShortName(new_task);
    int new_prio = getPriority(new_task);

    SCHEDULER_LOG_INFO("📬 新任务到达: " + new_task_name +
                      " 优先级: " + std::to_string(new_prio));

    // 1. 前瞻性能量判断
    double current_energy = getCurrentEnergy();

    auto wcet_it = _task_wcets.find(new_task);
    if (wcet_it == _task_wcets.end()) {
        SCHEDULER_LOG_WARNING("未找到WCET，加入等待队列");
        _waiting_queue.push_back(new_task);
        return;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);
    double total_energy = calculateTaskEnergy(new_task, wcet);
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("❌ 新任务能量不足，加入等待队列");
        _waiting_queue.push_back(new_task);
        return;
    }

    // 2. 检查是否需要抢占
    bool need_preempt = false;
    AbsRTTask* victim_task = nullptr;

    for (AbsRTTask* running_task : _running_tasks) {
        int running_prio = getPriority(running_task);

        if (new_prio < running_prio) {  // 新任务优先级更高
            // 检查抢占的能量成本
            double preempt_energy = total_energy;

            // 计算被抢占任务的剩余能量（需要回退）
            auto account_it = _energy_accounts.find(running_task);
            if (account_it != _energy_accounts.end()) {
                double remaining_prepaid = account_it->second.prepaid -
                                           account_it->second.consumed;
                // 抢占成本 = 新任务能量 - 被抢占任务剩余能量
                preempt_energy -= remaining_prepaid;
            }

            double energy_after_preempt = current_energy + predicted - preempt_energy;

            if (energy_after_preempt >= 0.0) {
                // ✅ 可以抢占
                need_preempt = true;
                victim_task = running_task;
                break;  // 找到第一个可抢占的
            }
        }
    }

    if (need_preempt && victim_task) {
        SCHEDULER_LOG_INFO("⚡ 抢占: " + getTaskShortName(victim_task) +
                          " 被 " + new_task_name + " 抢占");

        // 1. 挂起被抢占任务
        preemptTask(victim_task);

        // 2. 插入新任务到就绪队列
        Scheduler::insert(new_task);

        // 3. 触发dispatch立即调度新任务
        MRTKernel* kernel = getKernel();
        if (kernel) {
            kernel->dispatch();
        }
    } else {
        // 不需要抢占，直接插入
        Scheduler::insert(new_task);
    }
}
```

### 抢占执行

```cpp
void EPPScheduler::preemptTask(AbsRTTask* task) {
    if (!task) return;

    std::string task_name = getTaskShortName(task);

    // 1. 结算能量账目（回退剩余能量）
    settleEnergyAccount(task);

    // 2. 从运行队列移除
    auto running_it = std::find(_running_tasks.begin(), _running_tasks.end(), task);
    if (running_it != _running_tasks.end()) {
        _running_tasks.erase(running_it);
    }

    // 3. 释放核心
    for (auto& pair : _core_assignments) {
        if (pair.second == task) {
            pair.second = nullptr;
            break;
        }
    }

    // 4. 挂起任务（不删除，保持活跃）
    // 任务会自动在下次调度时继续执行

    SCHEDULER_LOG_INFO("⏸️ 任务已挂起: " + task_name);
}
```

---

## 数据结构定义

### 头文件修改

```cpp
// gpfp_epp_scheduler.hpp

class EPPScheduler : public Scheduler {
private:
    // ========== 能量账目数据结构 ==========
    struct TaskEnergyAccount {
        double prepaid;              // 预扣减能量（完整WCET）
        double consumed;             // 实际消耗能量（累计）
        double harvested;            // 执行期间实际收集能量
        double predicted;            // 预测收集能量
        MetaSim::Tick start_time;    // 任务开始时间
        MetaSim::Tick last_unit_time; // 上次单位时间时间
    };

    // ========== 能量管理 ==========
    std::map<AbsRTTask*, TaskEnergyAccount> _energy_accounts;
    mutable std::recursive_mutex _energy_mutex;

    // ========== 任务队列 ==========
    std::vector<AbsRTTask*> _waiting_queue;

    // ========== 多核管理 ==========
    std::map<int, AbsRTTask*> _core_assignments;
    std::vector<AbsRTTask*> _running_tasks;

    // ========== 太阳能收集 ==========
    MetaSim::Tick _last_collection_time;

    // ========== 能量恢复事件 ==========
    EPPEnergyRecoveryEvent* _recovery_event;
    bool _recovery_in_progress;

public:
    // ========== 能量记账方法 ==========
    void settleEnergyAccount(AbsRTTask* task);

    // ========== 保守预测方法 ==========
    double predictEnergyHarvestConservative(
        MetaSim::Tick wcet,
        MetaSim::Tick current_time);

    // ========== 抢占方法 ==========
    void preemptTask(AbsRTTask* task);
    bool checkPreemption(AbsRTTask* new_task, AbsRTTask* running_task);

    // ========== 辅助方法 ==========
    int getHourOfDay(TimeMs time_ms);
    int getDayOfYear(TimeMs time_ms);
    double getCloudCover(TimeMs time_ms);
};
```

---

## 完整代码修改方案

### 修改1：移除notify()中的能量扣减

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: L2574-2657

```cpp
void EPPScheduler::notify(AbsRTTask *task) {
    if (!task) return;

    std::string task_name = getTaskShortName(task);
    MetaSim::Tick current_time = SIMUL.getTime();

    SCHEDULER_LOG_INFO("🔔 notify() 被调用: " + task_name);

    // ⭐ 关键修复1：收集能量
    double harvested = updateEnergyContinuously(current_time);
    if (harvested > 0.001) {
        SCHEDULER_LOG_INFO("🔋 notify()收集能量: " + std::to_string(harvested) + "J");
    }

    // ⭐ 关键修复2：记录单位时间消耗（不扣减）
    double unit_energy = getUnitTimeEnergy(task);

    auto account_it = _energy_accounts.find(task);
    if (account_it != _energy_accounts.end()) {
        // 记录实际消耗
        account_it->second.consumed += unit_energy;
        account_it->second.harvested += harvested;

        SCHEDULER_LOG_DEBUG("📊 能量账目更新: " + task_name +
                          " 已消耗: " + std::to_string(account_it->second.consumed) + "J" +
                          " 已收集: " + std::to_string(account_it->second.harvested) + "J");
    }

    // ⭐ 关键修复3：移除consumeEnergy()调用
    // ❌ 删除：consumeEnergy(unit_energy, task_name + "_timeslice");

    // 更新任务状态
    if (std::find(_running_tasks.begin(), _running_tasks.end(), task) == _running_tasks.end()) {
        _running_tasks.push_back(task);
    }

    // 调用父类notify
    Scheduler::notify(task);

    SCHEDULER_LOG_DEBUG("EPP Tick级抢占: " + task_name);
}
```

### 修改2：实现getTaskN()的级联控制

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: L2774-2828

```cpp
AbsRTTask* EPPScheduler::getTaskN(unsigned int n) {
    // 1. 调用基类获取任务
    AbsRTTask* task = Scheduler::getTaskN(n);
    if (!task) return nullptr;

    // 2. 获取任务WCET
    auto wcet_it = _task_wcets.find(task);
    if (wcet_it == _task_wcets.end()) {
        SCHEDULER_LOG_WARNING("⚠️ [EPP] getTaskN: 未找到任务WCET");
        return nullptr;
    }
    Tick wcet = static_cast<Tick>(wcet_it->second);

    // 3. 保守预测能量收集
    Tick current_time = SIMUL.getTime();
    double predicted = predictEnergyHarvestConservative(wcet, current_time);

    // 4. 计算任务总能量
    double total_energy = calculateTaskEnergy(task, wcet);

    // 5. 前瞻性判断
    double current_energy = getCurrentEnergy();
    double energy_after = current_energy + predicted - total_energy;

    if (energy_after < 0.0) {
        SCHEDULER_LOG_INFO("❌ [EPP] getTaskN(#" + std::to_string(n) + "): 前瞻性能量不足");
        SCHEDULER_LOG_INFO("   当前: " + std::to_string(current_energy) + "J" +
                          " 预测: " + std::to_string(predicted) + "J" +
                          " 需要: " + std::to_string(total_energy) + "J");
        return nullptr;  // ⭐ 停止级联调度
    }

    // 6. 预扣减能量
    if (!consumeEnergy(total_energy, getTaskShortName(task))) {
        SCHEDULER_LOG_ERROR("❌ [EPP] getTaskN: consumeEnergy失败");
        return nullptr;
    }

    // 7. 创建能量账目
    TaskEnergyAccount account;
    account.prepaid = total_energy;
    account.consumed = 0.0;
    account.harvested = 0.0;
    account.predicted = predicted;
    account.start_time = current_time;
    account.last_unit_time = current_time;
    _energy_accounts[task] = account;

    SCHEDULER_LOG_INFO("✅ [EPP] getTaskN(#" + std::to_string(n) + "): " +
                      getTaskShortName(task) +
                      " 预扣减: " + std::to_string(total_energy) + "J" +
                      " 当前能量: " + std::to_string(getCurrentEnergy()) + "J" +
                      " ⭐ 级联继续");

    return task;
}
```

### 修改3：实现能量结算方法

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: 新增方法

```cpp
void EPPScheduler::settleEnergyAccount(AbsRTask* task) {
    auto account_it = _energy_accounts.find(task);
    if (account_it == _energy_accounts.end()) {
        SCHEDULER_LOG_WARNING("⚠️ 能量账目不存在: " + getTaskShortName(task));
        return;
    }

    TaskEnergyAccount& account = account_it->second;
    std::string task_name = getTaskShortName(task);

    // 1. 计算能量差额
    double actual_consumed = account.consumed;
    double actual_harvested = account.harvested;
    double prepaid = account.prepaid;
    double predicted = account.predicted;

    // 2. 能量回退
    double refund = 0.0;
    if (actual_consumed < prepaid) {
        refund = prepaid - actual_consumed;

        // 回退能量
        std::lock_guard<std::recursive_mutex> lock(_energy_mutex);
        if (_use_local_energy) {
            _local_energy += refund;
        } else {
            EnergyBridge::getInstance().addEnergy(refund);
        }

        SCHEDULER_LOG_INFO("💰 能量回退: " + task_name +
                          " 预扣减: " + std::to_string(prepaid) + "J" +
                          " 实际消耗: " + std::to_string(actual_consumed) + "J" +
                          " 回退: " + std::to_string(refund) + "J");
    }

    // 3. 净收益
    double net_harvest = actual_harvested - predicted;
    if (std::abs(net_harvest) > 0.001) {
        SCHEDULER_LOG_INFO("净能量: " + task_name +
                          " 实际收集: " + std::to_string(actual_harvested) + "J" +
                          " 预测收集: " + std::to_string(predicted) + "J" +
                          " 净收益: " + std::to_string(net_harvest) + "J");
    }

    // 4. 打印总结
    SCHEDULER_LOG_INFO("📊 能量账目结算: " + task_name);
    SCHEDULER_LOG_INFO("  预扣减: " + std::to_string(prepaid) + "J");
    SCHEDULER_LOG_INFO("  实际消耗: " + std::to_string(actual_consumed) + "J");
    SCHEDULER_LOG_INFO("  实际收集: " + std::to_string(actual_harvested) + "J");
    SCHEDULER_LOG_INFO("  能量回退: " + std::to_string(refund) + "J");

    // 5. 清除账目
    _energy_accounts.erase(account_it);
}
```

### 修改4：在completeTaskExecution()中结算

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: L1355-1537

在`completeTaskExecution()`开头添加：

```cpp
void EPPScheduler::completeTaskExecution(AbsRTTask *task) {
    if (!task) return;

    std::string task_name = getTaskShortName(task);

    // ⭐ 关键修复：首先结算能量账目
    settleEnergyAccount(task);

    // ... 原有的任务完成逻辑
}
```

### 修改5：实现保守预测

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: 新增方法

```cpp
double EPPScheduler::predictEnergyHarvestConservative(
    MetaSim::Tick wcet,
    MetaSim::Tick current_time) {

    if (wcet <= 0) return 0.0;

    // 1. 获取当前太阳能功率
    TimeMs adjusted_time = getAdjustedTime(current_time);
    double current_power = EnergyBridge::getInstance().getHarvestingRate(adjusted_time);

    if (current_power <= 0.0) {
        return 0.0;
    }

    // 2. 基础安全系数
    double safety_factor = 0.8;

    // 3. 时间段修正
    int hour = getHourOfDay(adjusted_time);
    if (hour >= 6 && hour <= 9) {
        safety_factor = 0.7;  // 早晨
    } else if (hour >= 11 && hour <= 14) {
        safety_factor = 0.85; // 中午
    } else if (hour >= 16 && hour <= 19) {
        safety_factor = 0.6;  // 傍晚
    } else if (hour >= 20 || hour <= 5) {
        safety_factor = 0.0;  // 夜晚
    }

    // 4. 计算保守预测
    double predicted = current_power * safety_factor * (wcet / 1000.0);

    SCHEDULER_LOG_INFO("🔮 保守预测: WCET=" + std::to_string(static_cast<int64_t>(wcet)) + "ms" +
                      " 功率=" + std::to_string(current_power) + "W" +
                      " 系数=" + std::to_string(safety_factor) +
                      " 预测=" + std::to_string(predicted) + "J");

    return predicted;
}

int EPPScheduler::getHourOfDay(TimeMs time_ms) {
    int64_t seconds = time_ms / 1000;
    int64_t hours = (seconds / 3600) % 24;
    return static_cast<int>(hours);
}

int EPPScheduler::getDayOfYear(TimeMs time_ms) {
    int64_t days = time_ms / (24 * 3600 * 1000);
    return static_cast<int>((days % 365) + 1);
}
```

### 修改6：简化schedule()

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: L893-980

```cpp
void EPPScheduler::schedule() {
    MetaSim::Tick current_time = SIMUL.getTime();
    int64_t current_ms = static_cast<int64_t>(current_time);

    SCHEDULER_LOG_INFO("🔄 [EPP] ===== 开始调度 ===== @ " + std::to_string(current_ms) + "ms");

    // 1. 收集太阳能
    double harvested = updateEnergyContinuously(current_time);
    if (harvested > 0.001) {
        SCHEDULER_LOG_INFO("☀️ 收集能量: " + std::to_string(harvested) + "J");
    }

    // 2. 检查等待队列
    double current_energy = getCurrentEnergy();
    int restored = requeueWaitingTasks(current_energy, nullptr);
    if (restored > 0) {
        SCHEDULER_LOG_INFO("✅ 恢复了 " + std::to_string(restored) + " 个任务");
    }

    // 3. 触发dispatch（级联调度）
    MRTKernel* kernel = getKernel();
    if (kernel) {
        SCHEDULER_LOG_INFO("🚀 触发dispatch");
        kernel->dispatch();
    }

    // 4. 能量恢复
    if (getSize() > 0 && getCurrentEnergy() <= 0.001) {
        startEnergyRecovery();
    }

    SCHEDULER_LOG_INFO("🏁 [EPP] ===== 调度结束 =====");
}
```

### 修改7：在insert()中添加抢占检查

**文件**: `librtsim/scheduler/gpfp_epp_scheduler.cpp`
**位置**: L2106-2451

在`insert()`方法的能量判断之后、插入队列之前，添加抢占检查逻辑。

---

## 测试验证方案

### 测试场景1：正常级联调度

**配置**：
```
初始能量: 1.0J
任务: task_high(wcet=10ms, 0.1J), task_mid(wcet=20ms, 0.2J), task_low(wcet=30ms, 0.3J)
CPU: 3个空闲CPU
```

**预期行为**：
1. getTaskN(0) → task_high → 预扣减0.1J → 剩余0.9J
2. getTaskN(1) → task_mid → 预扣减0.2J → 剩余0.7J
3. getTaskN(2) → task_low → 预扣减0.3J → 剩余0.4J
4. 3个任务全部调度成功 ✅

### 测试场景2：能量不足停止级联

**配置**：
```
初始能量: 0.15J
任务: task_high(0.2J), task_mid(0.1J), task_low(0.05J)
```

**预期行为**：
1. getTaskN(0) → task_high → 前瞻性判断失败 → 返回nullptr
2. 级联停止，task_mid和task_low不被调度 ✅

### 测试场景3：抢占场景

**配置**：
```
初始能量: 1.0J
T=0ms: task_mid(wcet=100ms, 0.5J) 开始执行
T=50ms: task_high(wcet=10ms, 0.1J, 优先级更高) 到达
```

**预期行为**：
1. T=0ms: task_mid预扣减0.5J，开始执行
2. T=50ms: task_high到达
3. 检查抢占：优先级更高，能量足够
4. 抢占task_mid：
   - 结算task_mid账目：已消耗0.25J，回退0.25J
   - 系统能量：0.5 + 0.25 = 0.75J
5. 调度task_high：预扣减0.1J，剩余0.65J ✅

### 测试场景4：保守预测验证

**配置**：
```
时间: 傍晚18:00
太阳能功率: 0.5W（正在下降）
任务: task(wcet=100ms)
```

**预期行为**：
1. 简单预测：0.5W × 0.1s = 0.05J
2. 保守预测：0.5W × 0.6（傍晚系数）× 0.1s = 0.03J
3. 使用0.03J进行前瞻性判断 ✅

---

## 总结

### 核心修复点

1. **能量扣减**：只在`getTaskN()/getFirst()`中预扣减，`notify()`不扣减
2. **能量记账**：使用`TaskEnergyAccount`结构记录预扣减、实际消耗、实际收集
3. **能量结算**：任务完成或被抢占时，结算账目并回退多余能量
4. **保守预测**：使用安全系数（0.6-0.85）进行保守的能量预测
5. **级联控制**：`getTaskN()`能量不足时返回nullptr，内核自动停止级联
6. **抢占机制**：在`insert()`中检查抢占，抢占时结算被抢占任务的能量账目

### 优势

- ✅ **能量硬约束**：预扣减确保不会超支
- ✅ **抢占安全**：结算机制确保能量账目准确
- ✅ **保守预测**：安全系数降低能量不足风险
- ✅ **级联高效**：充分利用多核资源
- ✅ **实时保证**：优先级绝对优先，Tick级抢占

### 与其他调度器对比

| 特性 | ASAP | CASCADE | EPP |
|-----|------|---------|-----|
| 能量约束 | ✅ | ✅ | ✅ |
| 抢占粒度 | 50ms分片 | 50ms分片 | Tick级(1ms) |
| 能量扣减 | 延迟扣减 | 延迟扣减 | 预扣减+结算 |
| 抢占处理 | 简单 | 复杂 | 完善的结算机制 |
| 预测策略 | 线性 | 线性 | 保守+安全系数 |

---

**文档版本**: 1.0
**最后更新**: 2026-01-17
**作者**: Claude + 用户协作
**项目**: PARTSim - 多核实时系统模拟器
