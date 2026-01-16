# EPP调度器（Energy-aware Preemptive Priority Scheduler）完整设计文档

## 目录
1. [算法概述](#算法概述)
2. [核心设计原则](#核心设计原则)
3. [数据结构](#数据结构)
4. [完整算法逻辑](#完整算法逻辑)
5. [关键场景分析](#关键场景分析)
6. [抢占机制](#抢占机制)
7. [能量管理](#能量管理)
8. [多核调度](#多核调度)
9. [优先级策略](#优先级策略)
10. [与ASAP/CASCADE对比](#与asapcascade对比)
11. [实现细节](#实现细节)
12. [配置文件格式](#配置文件格式)

---

## 算法概述

### 定义
**EPP调度器**（Energy-aware Preemptive Priority Scheduler）是一个能量感知的抢占式优先级调度算法，专为**能量受限的实时系统**设计。

### 核心特性
- ✅ **能量硬约束**：能量不足时绝对不调度任务
- ✅ **抢占式调度**：Tick级（1ms）抢占粒度，无分片边界限制
- ✅ **优先级驱动**：基于RM策略（周期越短优先级越高）
- ✅ **级联调度**：能量充足时连续调度多个任务
- ✅ **主动能量管理**：预测能量恢复时间，定时器主动唤醒
- ✅ **多核支持**：支持多CPU并行调度

### 适用场景
- 太阳能供电的实时嵌入式系统
- 能量采集系统（Energy Harvesting Systems）
- 电池供电的实时任务调度
- 需要严格��时性保证的能量受限环境

---

## 核心设计原则

### P1: 能量硬约束（Energy Hard Constraint）
```
能量_当前 + 能量_收集 >= 任务能耗  → 可以调度
能量_当前 + 能量_收集 <  任务能耗  → 不能调度
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
调度时刻：前瞻性判断（能量_当前 + 能量_收集 >= 能量_消耗）
扣减时机：调度决策时立即扣减（在getTaskN()/getFirst()中）
判断公式：energy_after_task = energy_current + energy_collection - energy_consumption
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
- ⚠️ 不在`dispatchTask()`中扣减（只负责分配任务到CPU）
- ⚠️ 不在`schedule()`中扣减（只负责调度循环）

### P6: 主动能量管理（Proactive Energy Management）
```
能量不足：计算恢复时间 → 设置定时器 → 定时器唤醒调度器
唤醒时机：能量正好够调度最高优先级任务
```
**含义**：主动预测能量恢复时间，被动等待转为主动唤醒。

---

## 数据结构

### 核心数据结构

```cpp
class EPPScheduler : public Scheduler {
private:
    // ========== 能量管理 ==========
    double _current_energy;                          // 当前系统能量（J）
    double _initial_energy;                          // 初始能量（J）
    double _max_energy;                              // 最大能量容量（J）

    // ========== 任务队列 ==========
    std::deque<AbsRTTask*> _ready_queue;             // 就绪队列（按RM优先级排序）
    std::deque<AbsRTTask*> _waiting_queue;           // 等待队列（能量不足）

    // ========== 多核管理 ==========
    std::vector<CPU*> _cpus;                         // 所有CPU
    std::map<CPU*, AbsRTTask*> _running_tasks;       // 每个CPU当前运行的任务

    // ========== 能量恢复事件 ==========
    EPPEnergyRecoveryEvent* _recovery_event;        // 能量恢复事件

    // ========== 太阳能收集 ==========
    ConfigManager* _config_manager;                   // 配置管理器
    int64_t _last_collection_time;                   // 上次收集能量的时间

    // ========== 任务参数映射 ==========
    struct TaskParams {
        int period;                                  // 周期（ms）
        int wcet;                                    // 最坏执行时间（ms）
        std::string workload_type;                   // 工作负载类型
        double energy_coefficient;                   // 能量系数
    };
    std::map<AbsRTTask*, TaskParams> _task_params;
};
```

### EPPTaskModel（任务模型）

```cpp
class EPPTaskModel : public TaskModel {
private:
    int _period;                                     // 周期（ms）
    int _wcet;                                       // 最坏执行时间（ms）
    std::string _workload_type;                      // 工作负载类型
    double _energy_coefficient;                      // 能量系数

public:
    // RM优先级：周期越短优先级越高（数值越小）
    Tick getPriority() const override {
        return _period;  // 直接使用周期作为优先级
    }

    double calculateEnergyConsumption() {
        // 能量 = wcet × 工作负载功率系数 × CPU频率比例
        return _wcet * _energy_coefficient * frequency_ratio;
    }
};
```

---

## 完整算法逻辑

### 主调度函数 schedule()

```cpp
void EPPScheduler::schedule() {
    SCHEDULER_LOG_INFO("🔄 [EPP] ===== 开始调度 =====");

    // ========== 第1步：收集太阳能 ==========
    Tick current_time = SIMUL.getTime();
    double harvested = collectSolarEnergy(current_time);
    if (harvested > 0.001) {
        SCHEDULER_LOG_INFO("☀️ [EPP] 收集能量: " + std::to_string(harvested) + "J");
    }
    _current_energy += harvested;

    // ========== 第2步：检查等待队列（能量恢复）==========
    restoreWaitingQueueToReadyQueue();

    // ========== 第3步：级联调度 ==========
    int scheduled_count = 0;
    int free_cpus = countFreeCPUs();

    SCHEDULER_LOG_INFO("📊 [EPP] 空闲CPU: " + std::to_string(free_cpus) +
                       " 当前能量: " + std::to_string(_current_energy) + "J");

    for (int i = 0; i < free_cpus; i++) {
        // 3.1 从就绪队列获取最高优先级任务
        AbsRTTask* highest = getHighestPriorityTaskFromReadyQueue();

        if (highest == nullptr) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] 就绪队列为空，停止调度");
            break;  // 就绪队列为空，停止调度
        }

        // 3.2 计算任务能耗
        double energy_needed = calculateEnergyForTask(highest);

        SCHEDULER_LOG_DEBUG("🔍 [EPP] 检查任务: " + getTaskName(highest) +
                           " 需要" + std::to_string(energy_needed) + "J");

        // 3.3 ⭐ 核心判断：前瞻性能量检查
        // 预测任务执行期间能收集多少太阳能
        Tick wcet = getTaskWCET(highest);
        double energy_collection = predictEnergyCollection(current_time, wcet);
        double energy_after_task = _current_energy + energy_collection - energy_needed;

        SCHEDULER_LOG_INFO("🔮 [EPP] 前瞻性能量判断: " + getTaskName(highest) +
                          " 当前=" + std::to_string(_current_energy) + "J" +
                          " 收集(预测)=" + std::to_string(energy_collection) + "J" +
                          " 消耗=" + std::to_string(energy_needed) + "J" +
                          " 结余=" + std::to_string(energy_after_task) + "J");

        if (energy_after_task >= 0.0) {
            // ✅ 能量足够 → 调度任务
            SCHEDULER_LOG_INFO("✅ [EPP] 前瞻性判断能量足够，调度任务: " + getTaskName(highest));

            dispatchTask(highest);
            // ⭐ 注意：能量实际扣减发生在getTaskN()/getFirst()中（MRTKernel调用时）
            // 这里dispatchTask()只负责分配CPU，不扣减能量

            scheduled_count++;

            // ✅ 继续级联：检查下一个任务
            SCHEDULER_LOG_DEBUG("🔄 [EPP] 继续级联检查下一个任务");

        } else {
            // ❌ 能量不足 → 立即停止，不检查其他任务
            SCHEDULER_LOG_INFO("❌ [EPP] 前瞻性判断能量不足，停止本轮调度");
            SCHEDULER_LOG_INFO("   需要: " + std::to_string(energy_needed) + "J" +
                               " 当前: " + std::to_string(_current_energy) + "J" +
                               " 预测收集: " + std::to_string(energy_collection) + "J" +
                               " 缺口: " + std::to_string(energy_needed - _current_energy - energy_collection) + "J");
            break;
        }
    }

    // ========== 第4步：能量恢复管理 ==========
    if (scheduled_count == 0 && !_ready_queue.empty()) {
        // 本轮没有调度任何任务，但就绪队列非空
        // 说明能量不足以调度最高优先级任务

        AbsRTTask* highest = getHighestPriorityTaskFromReadyQueue();
        double energy_needed = calculateEnergyForTask(highest);

        // ⭐ 修复：energy_deficit必须与前瞻性判断的失败原因保持一致
        Tick current_time = SIMUL.getTime();
        Tick wcet = getTaskWCET(highest);
        double energy_collection = predictEnergyCollection(current_time, wcet);
        double energy_deficit = energy_needed - _current_energy - energy_collection;

        SCHEDULER_LOG_INFO("⏳ [EPP] 本轮未调度，启动能量恢复");
        SCHEDULER_LOG_INFO("   当前能量: " + std::to_string(_current_energy) + "J" +
                           " 需要: " + std::to_string(energy_needed) + "J" +
                           " 预测收集: " + std::to_string(energy_collection) + "J" +
                           " 缺口: " + std::to_string(energy_deficit) + "J");

        // 计算能量恢复时间
        Tick recovery_time = calculateEnergyRecoveryTime(energy_deficit);

        SCHEDULER_LOG_INFO("⏰ [EPP] 预计恢复时间: " + std::to_string(recovery_time) + "ms");

        // 设置能量恢复事件
        scheduleEnergyRecoveryEvent(recovery_time);

    } else if (scheduled_count > 0) {
        SCHEDULER_LOG_INFO("✅ [EPP] 本轮调度成功: " + std::to_string(scheduled_count) + "个任务");
    }

    SCHEDULER_LOG_INFO("🏁 [EPP] ===== 调度结束 =====");
}
```

---

## 关键场景分析

### 场景1：能量充足（完整级联调度）

**初始状态**：
```
就绪队列: [task_high(period=50, wcet=10, energy=0.1J),
           task_mid(period=100, wcet=20, energy=0.2J),
           task_low(period=200, wcet=30, energy=0.3J)]
当前能量: 1.0J
空闲CPU: 3个（CPU0, CPU1, CPU2）
```

**执行过程**：
```
第1次迭代:
  → 前瞻性检查task_high:
     当前能量=1.0J, 预测收集=0.0J, 消耗=0.1J
     结余=1.0+0.0-0.1=0.9J >= 0? ✅ YES
  → 调度task_high到CPU0
  → ⭐ 预扣减能量: 1.0 - 0.1 = 0.9J（在getTaskN()中扣减）
  → 安排结束事件: t = current + 10ms
  → ✅ 继续级联

第2次迭代:
  → 前瞻性检查task_mid:
     当前能量=0.9J, 预测收集=0.0J, 消耗=0.2J
     结余=0.9+0.0-0.2=0.7J >= 0? ✅ YES
  → 调度task_mid到CPU1
  → ⭐ 预扣减能量: 0.9 - 0.2 = 0.7J（在getTaskN()中扣减）
  → 安排结束事件: t = current + 20ms
  → ✅ 继续级联

第3次迭代:
  → 前瞻性检查task_low:
     当前能量=0.7J, 预测收集=0.0J, 消耗=0.3J
     结余=0.7+0.0-0.3=0.4J >= 0? ✅ YES
  → 调度task_low到CPU2
  → ⭐ 预扣减能量: 0.7 - 0.3 = 0.4J（在getTaskN()中扣减）
  → 安排结束事件: t = current + 30ms
  → ✅ 继续级联

第4次迭代:
  → 就绪队列为空
  → 停止调度

结果: 调度成功3个任务，剩余能量0.4J
```

### 场景2：能量不足（立即停止）

**初始状态**：
```
就绪队列: [task_high(0.2J), task_mid(0.1J), task_low(0.05J)]
当前能量: 0.15J
空闲CPU: 3个
```

**执行过程**：
```
第1次迭代:
  → 前瞻性检查task_high:
     当前能量=0.15J, 预测收集=0.0J, 消耗=0.2J
     结余=0.15+0.0-0.2=-0.05J >= 0? ❌ NO
  → ⭐ 立即停止，不检查task_mid和task_low
  → 即使0.15J >= 0.1J（task_mid）和0.15J >= 0.05J（task_low）
  → 也不调度次优先级任务

能量恢复管理:
  → 计算缺口: 0.2 - 0.15 - 0.0 = 0.05J
  → 计算恢复时间: 0.05J / 0.097(J/ms) ≈ 1ms
  → 设置能量恢复事件: t = current + 1ms

结果: 本轮调度0个任务，等待能量恢复
```

### 场景3：能量恢复后调度（级联）

**初始状态**（接场景2，1ms后）：
```
能量恢复事件触发:
  → 收集能量: 0.097J
  → 当前能量: 0.15 + 0.097 = 0.247J

就绪队列: [task_high(0.2J), task_mid(0.1J), task_low(0.05J)]
空闲CPU: 3个
```

**执行过程**：
```
第1次迭代:
  → 前瞻性检查task_high:
     当前能量=0.247J, 预测收集=0.0J, 消耗=0.2J
     结余=0.247+0.0-0.2=0.047J >= 0? ✅ YES
  → 调度task_high到CPU0
  → 扣减能量: 0.247 - 0.2 = 0.047J
  → 安排结束事件: t = current + wcet
  → ✅ 继续级联

第2次迭代:
  → 前瞻性检查task_mid:
     当前能量=0.047J, 预测收集=0.0J, 消耗=0.1J
     结余=0.047+0.0-0.1=-0.053J >= 0? ❌ NO
  → ⭐ 立即停止

能量恢复管理:
  → 计算缺口: 0.1 - 0.047 - 0.0 = 0.053J
  → 计算恢复时间: 0.053 / 0.097 ≈ 1ms
  → 设置能量恢复事件: t = current + 1ms

结果: 调度成功1个任务（最高优先级），继续等待能量恢复
```

### 场景4：无空闲CPU（立即停止）

**初始状态**：
```
就绪队列: [task_high(0.1J), task_mid(0.1J)]
当前能量: 1.0J
CPU状态: CPU0(busy), CPU1(busy), CPU2(idle)
空闲CPU: 1个
```

**执行过程**：
```
第1次迭代:
  → 前瞻性检查task_high:
     当前能量=1.0J, 预测收集=0.0J, 消耗=0.1J
     结余=1.0+0.0-0.1=0.9J >= 0? ✅ YES
  → 调度task_high到CPU2
  → 扣减能量: 1.0 - 0.1 = 0.9J

第2次迭代:
  → countFreeCPUs() = 0
  → 无空闲CPU，立即停止

结果: 调度成功1个任务，充分利用空闲CPU
```

---

## 抢占机制

### 抢占触发时机

**触发条件**：
1. 新任务到达（onTaskArrival）
2. 任务结束（onTaskEnd）
3. 能量恢复（onEnergyRecovery）

### 抢占判断逻辑

```cpp
void EPPScheduler::checkAndPreempt() {
    SCHEDULER_LOG_DEBUG("🔍 [EPP] ===== 检查抢占 =====");

    // 遍历所有CPU
    for (CPU* cpu : _cpus) {
        AbsRTTask* current_task = _running_tasks[cpu];
        AbsRTTask* highest_ready = getHighestPriorityTaskFromReadyQueue();

        if (highest_ready == nullptr) {
            // 就绪队列为空，无需抢占
            continue;
        }

        if (current_task == nullptr) {
            // CPU空闲，直接调度
            SCHEDULER_LOG_DEBUG("💤 [EPP] CPU" + std::to_string(cpu->getIndex()) +
                               "空闲，调度任务");
            schedule();
            return;
        }

        // ⭐ 抢占条件判断（使用前瞻性能量判断）
        Tick current_priority = getPriority(current_task);
        Tick highest_priority = getPriority(highest_ready);
        double energy_needed = calculateEnergyForTask(highest_ready);

        // ⭐ 修复：抢占检查必须使用与主调度循环相同的前瞻性公式
        Tick current_time = SIMUL.getTime();
        Tick wcet = getTaskWCET(highest_ready);
        double energy_collection = predictEnergyCollection(current_time, wcet);

        SCHEDULER_LOG_DEBUG("⚖️ [EPP] 抢占检查: " +
                           "当前任务=" + getTaskName(current_task) +
                           "(优先级=" + std::to_string(current_priority) + ") " +
                           "就绪任务=" + getTaskName(highest_ready) +
                           "(优先级=" + std::to_string(highest_priority) + ") " +
                           "能量(当前+预测): " + std::to_string(_current_energy + energy_collection) + "J" +
                           " 需要: " + std::to_string(energy_needed) + "J");

        if (highest_priority < current_priority) {
            // 优先级更高
            if (_current_energy + energy_collection >= energy_needed) {
                // ✅ 前瞻性判断能量足够 → 立即抢占（Tick级）
                SCHEDULER_LOG_INFO("⚡ [EPP] 抢占: " +
                                   getTaskName(current_task) + " 被 " +
                                   getTaskName(highest_ready) + " 抢占");

                // 1. 挂起当前任务
                current_task->deschedule();
                removeFromRunningTasks(cpu);

                // 2. 当前任务回到就绪队列
                insertToReadyQueue(current_task);

                // 3. 调度高优先级任务
                dispatchTaskOnCPU(cpu, highest_ready);
                removeFromReadyQueue(highest_ready);

                // 4. 扣减能量
                _current_energy -= energy_needed;

                SCHEDULER_LOG_INFO("⏬ [EPP] 抢占后能量: " +
                                   std::to_string(_current_energy) + "J");

            } else {
                // ❌ 能量不足，不抢占
                SCHEDULER_LOG_DEBUG("🔋 [EPP] 能量不足，无法抢占: " +
                                   "需要" + std::to_string(energy_needed) + "J " +
                                   "当前" + std::to_string(_current_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_DEBUG("✋ [EPP] 优先级不高，无需抢占");
        }
    }

    SCHEDULER_LOG_DEBUG("🏁 [EPP] ===== 抢占检查结束 =====");
}
```

### 抢占示例（Tick级）

**时间线**：
```
t=0ms:
  CPU0: task_mid执行中（周期100ms，优先级100）
  能量: 1.0J

t=5ms: task_high到达（周期50ms，优先级50）
  → 收集能量: 1.0 + 0.097 = 1.097J
  → 前瞻性能量检查: 1.097J + 0.0J(预测) >= 0.1J? ✅ YES
  → 插入就绪队列
  → ⭐ 立即检查抢占（Tick级）

抢占判断:
  → task_high优先级(50) < task_mid优先级(100)? ✅ YES
  → 前瞻性能量检查: 1.097J + 0.0J(预测) >= 0.1J? ✅ YES
  → ⭐ 立即抢占task_high（不等待分片边界）

抢占执行:
  → task_mid.deschedule()
  → task_mid回到就绪队列
  → task_high调度到CPU0
  → 扣减能量: 1.097 - 0.1 = 0.997J
  → 安排task_high结束: t = 5 + 10 = 15ms

t=15ms: task_high完成
  → 调用schedule()
  → task_mid重新调度（从就绪队列）
```

---

## 能量管理

### 太阳能收集

```cpp
double EPPScheduler::collectSolarEnergy(Tick current_time) {
    if (!_config_manager) {
        return 0.0;
    }

    // 获取当前时间对应的太阳能辐照度
    int64_t time_ms = static_cast<int64_t>(current_time);
    double irradiance = _config_manager->getSolarIrradiance(time_ms);

    // 计算能量收集功率（W）
    // P = 辐照度 × 光伏效率 × 面积
    double power = irradiance * _pv_efficiency * _pv_area_m2;

    // 计算自上次收集以来的时间差（ms）
    Tick time_delta = current_time - _last_collection_time;
    double time_seconds = time_delta / 1000.0;

    // 计算收集的能量（J）
    // E = P × t
    double energy = power * time_seconds;

    // 更新上次收集时间
    _last_collection_time = current_time;

    return energy;
}
```

### 能量恢复时间计算

```cpp
Tick EPPScheduler::calculateEnergyRecoveryTime(double energy_deficit) {
    // 获取当前太阳能辐照度
    int64_t current_time_ms = static_cast<int64_t>(SIMUL.getTime());
    double current_irradiance = _config_manager->getSolarIrradiance(current_time_ms);

    // 计算能量收集功率（W）
    double power = current_irradiance * _pv_efficiency * _pv_area_m2;

    if (power <= 0.0) {
        // 没有太阳能（夜晚或阴天）
        // 设置较长的检查间隔（1秒）
        SCHEDULER_LOG_WARNING("🌙 [EPP] 无太阳能，设置长间隔检查");
        return 1000;  // 1秒后再检查
    }

    // 计算需要的时间（ms）
    // 时间(s) = 能量(J) / 功率(W)
    double time_seconds = energy_deficit / power;
    Tick time_ms = static_cast<Tick>(ceil(time_seconds * 1000.0));

    // 至少1ms后检查
    Tick result = std::max(time_ms, 1);

    SCHEDULER_LOG_INFO("📐 [EPP] 能量恢复计算: " +
                       "缺口=" + std::to_string(energy_deficit) + "J " +
                       "功率=" + std::to_string(power) + "W " +
                       "时间=" + std::to_string(result) + "ms");

    return result;
}
```

### 能量恢复事件处理

```cpp
void EPPEnergyRecoveryEvent::doit() {
    SCHEDULER_LOG_INFO("⏰ [EPP] ===== 能量恢复事件触发 =====");

    // 1. 收集能量（此时应该已经收集到足够的能量）
    double harvested = _scheduler->collectSolarEnergy(SIMUL.getTime());
    SCHEDULER_LOG_INFO("☀️ [EPP] 收集能量: " + std::to_string(harvested) + "J");

    // 2. 检查等待队列
    _scheduler->restoreWaitingQueueToReadyQueue();

    // 3. ⭐ 主动唤醒调度器
    SCHEDULER_LOG_INFO("🔄 [EPP] 能量恢复，重新开始调度");
    _scheduler->schedule();

    SCHEDULER_LOG_INFO("🏁 [EPP] ===== 能量恢复事件结束 =====");
}
```

### 等待队列恢复（⭐ 修复：使用前瞻性判断）

```cpp
void EPPScheduler::restoreWaitingQueueToReadyQueue() {
    if (_waiting_queue.empty()) {
        return;
    }

    SCHEDULER_LOG_INFO("🔄 [EPP] 检查等待队列: " +
                       std::to_string(_waiting_queue.size()) + "个任务");

    int restored_count = 0;
    Tick current_time = SIMUL.getTime();

    // 遍历等待队列（按优先级排序）
    for (auto it = _waiting_queue.begin(); it != _waiting_queue.end();) {
        AbsRTTask* task = *it;
        double energy_needed = calculateEnergyForTask(task);

        // ⭐ 修复：使用前瞻性判断，与schedule()中的逻辑保持一致
        // 因为任务恢复后立即参与调度，调度判断是前瞻性的
        Tick wcet = getTaskWCET(task);
        double energy_collection = predictEnergyCollection(current_time, wcet);
        double energy_after_task = _current_energy + energy_collection - energy_needed;

        SCHEDULER_LOG_DEBUG("🔍 [EPP] 检查任务: " + getTaskName(task) +
                           " 需要" + std::to_string(energy_needed) + "J" +
                           " 预测收集: " + std::to_string(energy_collection) + "J" +
                           " 结余: " + std::to_string(energy_after_task) + "J");

        if (energy_after_task >= 0.0) {
            // ✅ 前瞻性判断能量足够，从等待队列移动到就绪队列
            SCHEDULER_LOG_INFO("✅ [EPP] 恢复任务: " + getTaskName(task) +
                               " (能量足够: " + std::to_string(_current_energy + energy_collection) + "J >= " +
                               std::to_string(energy_needed) + "J)");

            it = _waiting_queue.erase(it);
            insertToReadyQueue(task);
            restored_count++;

        } else {
            // ❌ 能量仍不足，继续等待
            SCHEDULER_LOG_DEBUG("❌ [EPP] 能量仍不足: " + getTaskName(task) +
                               " 缺口: " + std::to_string(-energy_after_task) + "J");
            ++it;
        }
    }

    if (restored_count > 0) {
        SCHEDULER_LOG_INFO("✅ [EPP] 恢复了" + std::to_string(restored_count) + "个任务到就绪队列");
    }
}
```

---

## 多核调度

### CPU状态管理

```cpp
int EPPScheduler::countFreeCPUs() {
    int count = 0;
    for (CPU* cpu : _cpus) {
        if (_running_tasks[cpu] == nullptr) {
            count++;
        }
    }
    return count;
}

CPU* EPPScheduler::getFreeCPU() {
    for (CPU* cpu : _cpus) {
        if (_running_tasks[cpu] == nullptr) {
            return cpu;
        }
    }
    return nullptr;  // 无空闲CPU
}

void EPPScheduler::assignTaskToCPU(CPU* cpu, AbsRTTask* task) {
    _running_tasks[cpu] = task;
    SCHEDULER_LOG_DEBUG("🖥️ [EPP] 分配任务" + getTaskName(task) +
                       "到CPU" + std::to_string(cpu->getIndex()));
}

void EPPScheduler::removeFromRunningTasks(CPU* cpu) {
    _running_tasks[cpu] = nullptr;
}
```

### 任务分发

```cpp
void EPPScheduler::dispatchTask(AbsRTTask* task) {
    // 1. 获取空闲CPU
    CPU* cpu = getFreeCPU();
    if (cpu == nullptr) {
        SCHEDULER_LOG_ERROR("❌ [EPP] 无空闲CPU，无法调度任务");
        return;
    }

    // 2. 分配任务到CPU
    dispatchTaskOnCPU(cpu, task);
}

void EPPScheduler::dispatchTaskOnCPU(CPU* cpu, AbsRTTask* task) {
    SCHEDULER_LOG_INFO("🚀 [EPP] 调度任务: " + getTaskName(task) +
                       " 到CPU" + std::to_string(cpu->getIndex()));

    // 1. 从就绪队列移除
    removeFromReadyQueue(task);

    // 2. 分配到CPU
    assignTaskToCPU(cpu, task);

    // 3. 计算WCET和结束时间
    Tick wcet = getTaskWCET(task);
    Tick current_time = SIMUL.getTime();
    Tick end_time = current_time + wcet;

    SCHEDULER_LOG_DEBUG("⏱️ [EPP] WCET=" + std::to_string(wcet) +
                       "ms 结束时间=" + std::to_string(end_time) + "ms");

    // 4. 调度任务执行
    task->schedule();

    // 5. 安排结束事件
    scheduleTaskEndEvent(task, cpu, end_time);
}
```

---

## 优先级策略

### RM优先级（Rate Monotonic）

```cpp
Tick EPPTaskModel::getPriority() const {
    // RM策略：周期越短，优先级越高（数值越小）
    return _period;
}

void EPPScheduler::insertToReadyQueue(AbsRTTask* task) {
    // 获取任务优先级（周期）
    Tick priority = getPriority(task);

    // 按优先级插入就绪队列（从小到大排序）
    auto it = _ready_queue.begin();
    while (it != _ready_queue.end()) {
        if (getPriority(*it) > priority) {
            // 找到插入位置（在第一个优先级更低的任务前）
            _ready_queue.insert(it, task);
            return;
        }
        ++it;
    }

    // 没找到位置，插入到末尾
    _ready_queue.push_back(task);
}

AbsRTTask* EPPScheduler::getHighestPriorityTaskFromReadyQueue() {
    if (_ready_queue.empty()) {
        return nullptr;
    }

    // 返回队列头部（最高优先级）
    return _ready_queue.front();
}

void EPPScheduler::removeFromReadyQueue(AbsRTTask* task) {
    _ready_queue.erase(
        std::remove(_ready_queue.begin(), _ready_queue.end(), task),
        _ready_queue.end()
    );
}
```

### 优先级示例

```
任务集:
  task_high: period=50ms  → 优先级=50
  task_mid:  period=100ms → 优先级=100
  task_low:  period=200ms → 优先级=200

就绪队列排序（从高到低）:
  [task_high(50), task_mid(100), task_low(200)]

调度顺序:
  1. task_high（优先级最高）
  2. task_mid（次优先级）
  3. task_low（优先级最低）
```

---

## 与ASAP/CASCADE对比

### 特性对比表

| 特性 | ASAP | CASCADE | **EPP** |
|-----|------|---------|--------|
| **能量约束** | ✅ 硬约束 | ✅ 硬约束 | ✅ 硬约束 |
| **抢占机制** | ⚠️ 50ms分片边界 | ⚠️ 50ms分片边界 | ✅ Tick级（1ms） |
| **抢占时机** | 分片结束 | 分片结束 | 任意时刻 |
| **优先级策略** | RM | RM | RM |
| **级联调度** | ✅ | ✅ | ✅ |
| **失败停止** | ✅ | ✅ | ✅ |
| **能量恢复** | ✅ 主动 | ✅ 主动 | ✅ 主动 |
| **多核支持** | ✅ | ✅ | ✅ |
| **等待队列** | ✅ | ✅ | ✅ |
| **任务分片** | ✅ 50ms | ✅ 50ms | ❌ 按WCET执行 |
| **能量预扣** | ❌ | ❌ | ✅ |
| **适用场景** | 能量受限非实时 | 能量受限实时 | **能量受限实时+抢占** |

### 调度行为对比

**场景**：3个任务，初始能量0.15J
```
task_high: energy=0.2J, period=50ms
task_mid:  energy=0.1J, period=100ms
task_low:  energy=0.05J, period=200ms
```

**ASAP行为**：
```
t=0ms: 能量0.15J
       检查task_high: 0.15J >= 0.2J? ❌ NO
       → 等待能量恢复

t=2ms: 能量0.34J
       检查task_high: 0.34J >= 0.2J? ✅ YES
       → 调度task_high执行50ms分片
       → 扣减能量: 0.34 - 0.1 = 0.24J

t=52ms: 分片结束
       → 继续执行或暂停（检查能量）
```

**EPP行为**：
```
t=0ms: 能量0.15J
       检查task_high: 0.15J >= 0.2J? ❌ NO
       → 等待能量恢复

t=2ms: 能量0.34J
       检查task_high: 0.34J >= 0.2J? ✅ YES
       → 调度task_high执行完整WCET（10ms）
       → 扣减能量: 0.34 - 0.2 = 0.14J

t=12ms: task_high完成
       → 检查task_mid: 0.14J >= 0.1J? ✅ YES
       → 调度task_mid
```

---

## 实现细节

### 文件结构

```
librtsim/
├── include/rtsim/scheduler/
│   ├── gpfp_epp_scheduler.hpp          # EPP调度器头文件
│   └── ...
├── scheduler/
│   ├── gpfp_epp_scheduler.cpp          # EPP调度器实现
│   └── ...
```

### 类定义

```cpp
// gpfp_epp_scheduler.hpp

#ifndef __GPFP_EPP_SCHEDULER_HPP__
#define __GPFP_EPP_SCHEDULER_HPP__

#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/energy_bridge.hpp>
#include <rtsim/config_manager.hpp>
#include <deque>
#include <map>

namespace RTSim {

    using namespace MetaSim;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // EPP能量恢复事件
    // =====================================================
    class EPPEnergyRecoveryEvent : public Event {
    private:
        EPPScheduler* _scheduler;

    public:
        EPPEnergyRecoveryEvent(EPPScheduler* scheduler);
        void doit() override;
    };

    // =====================================================
    // EPP任务模型
    // =====================================================
    class EPPTaskModel : public TaskModel {
    private:
        int _period;
        int _wcet;
        std::string _workload_type;
        double _energy_coefficient;

    public:
        EPPTaskModel(AbsRTTask* t, int period, int wcet,
                     const std::string& workload_type,
                     double energy_coefficient);

        Tick getPriority() const override;
        void changePriority(Tick p) override;

        // Getter方法
        int getPeriod() const { return _period; }
        int getWCET() const { return _wcet; }
        std::string getWorkloadType() const { return _workload_type; }
        double getEnergyCoefficient() const { return _energy_coefficient; }
    };

    // =====================================================
    // EPP调度器
    // =====================================================
    class EPPScheduler : public Scheduler {
    private:
        // 能量管理
        double _current_energy;
        double _initial_energy;
        double _max_energy;

        // 任务队列
        std::deque<AbsRTTask*> _ready_queue;
        std::deque<AbsRTTask*> _waiting_queue;

        // 多核管理
        std::vector<CPU*> _cpus;
        std::map<CPU*, AbsRTTask*> _running_tasks;

        // 能量恢复
        EPPEnergyRecoveryEvent* _recovery_event;

        // 太阳能收集
        ConfigManager* _config_manager;
        Tick _last_collection_time;
        double _pv_efficiency;
        double _pv_area_m2;

        // 任务参数
        struct TaskParams {
            int period;
            int wcet;
            std::string workload;
            double energy_coefficient;
        };
        std::map<AbsRTTask*, TaskParams> _task_params;

    public:
        EPPScheduler();
        virtual ~EPPScheduler();

        // ========== 核心调度函数 ==========
        void schedule() override;
        void addTask(AbsRTTask* task, const std::string& params) override;
        void removeTask(AbsRTTask* task) override;

        // ========== 任务事件处理 ==========
        void onTaskArrival(AbsRTTask* task);
        void onTaskEnd(AbsRTTask* task, CPU* cpu);

        // ========== 能量管理 ==========
        double collectSolarEnergy(Tick current_time);
        Tick calculateEnergyRecoveryTime(double energy_deficit);
        void scheduleEnergyRecoveryEvent(Tick delay);

        // ========== 队列管理 ==========
        void restoreWaitingQueueToReadyQueue();
        void insertToReadyQueue(AbsRTTask* task);
        void insertToWaitingQueue(AbsRTTask* task);
        void removeFromReadyQueue(AbsRTTask* task);
        AbsRTTask* getHighestPriorityTaskFromReadyQueue();

        // ========== 多核管理 ==========
        int countFreeCPUs();
        CPU* getFreeCPU();
        void assignTaskToCPU(CPU* cpu, AbsRTTask* task);
        void removeFromRunningTasks(CPU* cpu);

        // ========== 任务分发 ==========
        void dispatchTask(AbsRTTask* task);
        void dispatchTaskOnCPU(CPU* cpu, AbsRTTask* task);
        void scheduleTaskEndEvent(AbsRTTask* task, CPU* cpu, Tick end_time);

        // ========== 抢占检查 ==========
        void checkAndPreempt();

        // ========== 能量计算 ==========
        double calculateEnergyForTask(AbsRTTask* task);
        Tick getPriority(AbsRTTask* task);
        int getTaskWCET(AbsRTTask* task);
        std::string getTaskName(AbsRTTask* task);

        // ========== Getter/Setter ==========
        double getCurrentEnergy() const { return _current_energy; }
        void setCurrentEnergy(double energy) { _current_energy = energy; }

        static EPPScheduler* createInstance(vector<string>& par);

        friend class EPPEnergyRecoveryEvent;
    };

} // namespace RTSim

#endif
```

### 注册到工厂

```cpp
// gpfp_epp_scheduler.cpp

#include <rtsim/scheduler/gpfp_epp_scheduler.hpp>
#include <metasim/factory.hpp>

namespace RTSim {

    // 注册到工厂
    namespace {
        Factory<Scheduler, EPPScheduler, const std::vector<std::string>&>
            register_epp("gpfp_epp");
    }

    EPPScheduler* EPPScheduler::createInstance(vector<string>& par) {
        return new EPPScheduler();
    }

    // ... 其他实现

} // namespace RTSim
```

---

## 配置文件格式

### YAML配置示例

```yaml
# =============================================
# EPP调度器配置示例
# =============================================

cpu_islands:
  - name: energy_aware_cpus
    numcpus: 3

    kernel:
      scheduler: gpfp_epp                    # ⭐ 使用EPP调度器
      task_placement: global

    volts: [0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, 1.08, 1.10, 1.12, 1.14]
    freqs: [7000, 7500, 8000, 8100, 8200, 8300, 8400, 8500, 9000, 9500, 10000, 10500]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  initial_energy: 0.0                        # 初始能量
  max_energy: 1000.0                         # 最大能量容量

  # 时间配置
  day_of_year: 187
  time_of_day_ms: 28800000                   # 上午8点

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18                        # 光伏效率
  pv_area_m2: 1.0                            # 光伏面积

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi

    params:
      - workload: bzip2
        power_params: [0.00775587, 33.376, 1.54585, 9.53439e-10]
        speed_params: [0.0256054, 2.9809e+6, 0.602631, 8.13712e+9]
        energy_coefficient: 1.2              # ⭐ 能量系数

      - workload: crc32
        power_params: [0.00624673, 176.315, 1.72836, 1.77362e-10]
        speed_params: [0.00645628, 3.37134e+6, 7.83177, 93459]
        energy_coefficient: 0.8

      - workload: basicmath
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.5

# 任务配置（由global_task_generator.py生成）
tasks:
  - name: "task_0"
    type: "PeriodicTask"
    period: 50                               # ⭐ 周期决定优先级
    deadline: 50
    wcet: 10
    workload: "bzip2"

  - name: "task_1"
    type: "PeriodicTask"
    period: 100
    deadline: 100
    wcet: 20
    workload: "crc32"

  - name: "task_2"
    type: "PeriodicTask"
    period: 200
    deadline: 200
    wcet: 30
    workload: "basicmath"

# 追踪配置
trace:
  output_file: "trace_epp.json"
  log_schedules: true
  log_preemptions: true
  log_energy_management: true               # ⭐ 记录能量管理事件
  log_waiting_queue: true                   # ⭐ 记录等待队列
```

---

## 总结

### EPP调度器核心优势

1. **能量安全**：硬约束保证，不存在能量透支风险
2. **实时性保证**：Tick级抢占，优先级绝对优先
3. **资源高效**：级联调度充分利用能量和CPU
4. **主动管理**：预测能量恢复时间，主动唤醒调度器
5. **多核支持**：天然支持多CPU并行调度

### 适用场景总结

| 场景 | EPP是否适用 | 原因 |
|-----|-----------|------|
| 太阳能供电实时系统 | ✅ | 设计目标 |
| 能量采集系统 | ✅ | 能量感知 |
| 严格实时系统 | ✅ | Tick级抢占 |
| 多核并行系统 | ✅ | 多核支持 |
| 能量充足系统 | ⚠️ | 可用但非最优 |
| 非实时系统 | ⚠️ | 抢占开销可能不必要 |

### 与其他调度器选择建议

- **能量受限 + 实时性要求高** → EPP
- **能量受限 + 实时性要求中等** → ASAP
- **能量受限 + 复杂依赖** → CASCADE
- **能量充足 + 实时性要求高** → EDF/FP/RM
