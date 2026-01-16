# ASAP调度器 vs 抢占式调度器（EDF/FP/RM）对比

## 核心区别总结

| 特性 | ASAP调度器 | 抢占式调度器（EDF/FP/RM） |
|-----|-----------|------------------------|
| **调度目标** | 能量感知，尽可能早调度 | 基于优先级（deadline/周期/静态） |
| **能量约束** | ✅ **硬约束**：能量不足时等待 | ❌ 无能量约束 |
| **抢占机制** | ⚠️ **有限抢占**：只在分片边界抢占 | ✅ **完全抢占**：随时可抢占 |
| **任务分片** | ✅ 支持（unit_time=50ms） | ❌ FixedInstr原子执行 |
| **等待队列** | ✅ 有（能量不足时任务等待） | ❌ 无（就绪队列） |
| **能量收集** | ✅ 周期性收集太阳能 | ❌ 无 |
| **优先级** | 基于RM（周期越短优先级越高） | EDF/FP/RM各有优先级策略 |

---

## 1. 任务集配置区别

### ASAP调度器配置

```yaml
# config_asap_V28.13_8am.yml
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 3
    kernel:
      scheduler: gpfp_asap                    # ⭐ ASAP调度器
      scheduler_params:
        - "strict_priority=true"
        - "energy_stop_policy=true"           # ⭐ 能量不足停止策略
        - "expected_task_count=5"
        - "cascade_mode=true"

energy_management:
  initial_energy: 0.0                         # ⭐ 零初始能量
  max_energy: 1000.0

  # ⭐ 时间配置影响太阳能
  time_of_day_ms: 28800000                   # 上午8点
  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"

  unit_time: 50                               # ⭐ 任务分片时间（50ms）
  enable_energy_recovery: true                # ⭐ 启用能量恢复

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi                   # ⭐ 复杂功率模型
    params:
      - workload: bzip2
        power_params: [0.00775587, 33.376, 1.54585, 9.53439e-10]
        speed_params: [0.0256054, 2.9809e+6, 0.602631, 8.13712e+9]
        energy_coefficient: 1.2
```

### 抢占式调度器配置

```yaml
# config_rm.yml
simulation:
  duration: 500                               # 简单仿真时长

scheduler:
  type: "RMScheduler"                         # ⭐ RM调度器
  name: "RM"

kernel:
  type: "MRTKernel"
  cpus:
    - name: "CPU0"
      frequency: 8100                         # ⭐ 固定频率
      base_speed: 1.0

tasks:
  - name: "task_high"
    type: "PeriodicTask"
    period: 50
    deadline: 50
    instructions:
      - type: "fixed"                         # ⭐ FixedInstr（原子执行）
        duration: 10                          # ⭐ 不分片
        workload: "bzip2"

energy:
  enabled: false                              # ⭐ 不关心能量
  initial_energy: 1000.0
```

---

## 2. 任务定义区别

### ASAP调度器任务

```cpp
// ASAP使用GPFPASAPTaskModel（内部模型）
class GPFPASAPTaskModel : public TaskModel {
    int _period;                              // 周期
    int _wcet;                                // 最坏执行时间
    std::string _workload_type;               // 工作负载类型
    double _base_energy_consumption;          // ⭐ 能量消耗
    MetaSim::Tick _rm_priority;               // ⭐ RM优先级
};

// 任务分片执行（每50ms检查一次能量）
void ASAPSlicingEvent::doit() {
    // ⭐ 任务被切成多个unit_time片
    // 每片执行前检查能量是否足够
    // 能量不足则暂停，进入等待队列
}
```

### 抢占式调度器任务

```cpp
// 抢占式使用PeriodicTask + FixedInstr
PeriodicTask* task = new PeriodicTask(50, 50, 0, "task_high");
task->addInstr(new FixedInstr(task, 10, "bzip2"));

// FixedInstr原子执行（不可分片）
class FixedInstr : public ExecInstr {
    // ⭐ 一旦开始，必须执行完10ms
    // 可以被高优先级任务抢占，但不会被分片
    void deschedule() {
        // 保存执行进度
        actCycles += ((t - lastTime)) * currentSpeed;
        _endEvt.drop();                       // 停止执行
    }
};
```

---

## 3. 调度行为区别

### ASAP调度器

```cpp
void GPFPASAPScheduler::performASAPSchedule(Tick current_time) {
    // 1. 收集能量（太阳能）
    double harvested = updateEnergyContinuously(current_time);

    // 2. 检查等待队列
    if (current_energy >= unit_energy_needed) {
        // 能量足够 → 从等待队列恢复到就绪队列
        moveWaitingToReady();
    }

    // 3. 调度任务（按RM优先级）
    for (auto* task : ready_queue) {
        if (current_energy >= getUnitTimeEnergy(task)) {
            // ⭐ 调度一个unit_time（50ms）
            dispatch(task, unit_time);
            current_energy -= unit_energy;

            // 50ms后触发ASAPSlicingEvent再次检查
            scheduleSlicingEvent(task, 50);
            break;                            // ⭐ 只调度一个任务
        }
    }

    // 4. 安排下次能量收集
    scheduleEnergyRecovery();
}
```

**示例调度序列**（能量从0开始）：
```
t=0ms:    能量=0J → 所有任务进入等待队列
t=1ms:    能量=0.097J → 仍不足0.1J → 继续等待
t=2ms:    能量=0.195J → 调度task_high执行50ms
t=2-52ms: task_high执行（消耗0.1J）
t=52ms:   检查能量=0.095J → 不够，等待
t=55ms:   能量=0.191J → 调度task_mid执行50ms
```

### 抢占式调度器（RM）

```cpp
void RMScheduler::addTask(AbsRTTask *task) {
    // 自动根据周期分配优先级
    RMModel* model = new RMModel(task);
    // task_high (周期50ms)  → 优先级最高
    // task_mid (周期100ms)  → 优先级中
    // task_low (周期200ms)  → 优先级最低
    enqueueModel(model);
}

void MRTKernel::dispatch() {
    // ⭐ 总是调度最高优先级任务
    AbsRTTask* highest = _sched->getFirst();
    if (highest != current_executing) {
        // ⭐ 驱逐当前任务（抢占）
        current_executing->deschedule();
        highest->schedule();
    }
}
```

**示例调度序列**（无能量约束）：
```
t=0ms:    task_high(10ms), task_mid(20ms), task_low(30ms)同时到达
          → task_high优先级最高，执行10ms
t=10ms:   task_high完成 → task_mid执行20ms
t=30ms:   task_mid完成 → task_low执行30ms
t=50ms:   task_high再次到达 → ⭐ 抢占task_low → task_high执行10ms
t=60ms:   task_high完成 → task_low恢复执行（剩余20ms）
```

---

## 4. 抢占机制区别

### ASAP调度器的"抢占"

```cpp
// ASAP只在以下情况"抢占"：
// 1. 50ms分片结束时（ASAPSlicingEvent）
// 2. 任务完成时

void ASAPSlicingEvent::doit() {
    // ⭐ 只在分片边界检查
    // 不会在任务执行中间抢占
    double current_energy = getCurrentEnergy();
    if (current_energy < getUnitTimeEnergy(task)) {
        // 能量不足，暂停任务，进入等待队列
        addToWaitingQueue(task);
        task->deschedule();
    }
}
```

**特点**：
- ⚠️ **受限抢占**：只在50ms分片边界抢占
- ⚠️ **能量驱动**：抢占原因是能量不足
- ⚠️ **不检查优先级**：不会因为高优先级任务到达而抢占

### 抢占式调度器的抢占

```cpp
// MRTKernel在任务到达时立即检查是否需要抢占
void MRTKernel::onArrival(AbsRTTask *new_task) {
    _sched->insert(new_task);                 // 插入就绪队列

    // ⭐ 立即检查优先级
    AbsRTTask* highest = _sched->getFirst();
    if (highest != _m_currExe[cpu]) {
        // ⭐ 高优先级任务到达 → 立即抢占
        dispatch(cpu);
    }
}

void MRTKernel::onBeginDispatchMulti(BeginDispatchMultiEvt *e) {
    AbsRTTask* current_task = _m_currExe[cpu];
    if (current_task != nullptr) {
        // ⭐ 挂起当前任务（抢占）
        current_task->deschedule();
        _m_currExe[cpu] = nullptr;
    }

    // ⭐ 调度新任务
    AbsRTTask* new_task = _sched->getFirst();
    new_task->schedule();
}
```

**特点**：
- ✅ **完全抢占**：任务执行到任何时刻都可被抢占
- ✅ **优先级驱动**：高优先级任务到达立即抢占
- ✅ **保存进度**：FixedInstr保存执行进度，恢复后继续

---

## 5. 适用场景对比

### ASAP调度器

| 场景 | 是否适用 | 原因 |
|-----|---------|------|
| 太阳能供电系统 | ✅ | 设计目标 |
| 能量受限环境 | ✅ | 能量是硬约束 |
| 需要能量管理 | ✅ | 周期性收集太阳能 |
| 实时性要求极高 | ❌ | 能量不足时任务会等待 |
| 动态负载 | ⚠️ | 依赖太阳能数据 |

### 抢占式调度器

| 场景 | 是否适用 | 原因 |
|-----|---------|------|
| 实时系统 | ✅ | 优先级保证 |
| 软实时系统 | ✅ | deadline保证 |
| 能量不限环境 | ✅ | 无能量约束 |
| 能量受限环境 | ❌ | 不考虑能量 |
| 混合关键系统 | ✅ | 优先级映射关键性 |

---

## 6. 性能指标对比

### ASAP调度器

```python
# 能量指标
energy_consumption = solar_energy_collected - battery_drain
energy_efficiency = useful_work / energy_consumption

# 调度指标
scheduling_delay = waiting_time_in_energy_queue  # 能量等待时间
task_completion_rate = completed_tasks / total_tasks
deadline_miss_rate = missed_deadlines / total_tasks

# 示例（8am测试）
initial_energy: 0J
solar_irradiance: 541.2 W/m²
first_schedule: 2ms (从能量收集开始)
total_schedules: 58
deadline_misses: 0
```

### 抢占式调度器

```python
# 调度指标
preemption_count = number_of_preemptions       # 抢占次数
context_switch_overhead = preemptions * switch_cost
response_time = completion_time - arrival_time

# 可调度性分析
utilization = sum(wcet_i / period_i)
edf_schedulable = (utilization <= 1.0)
rm_schedulable = (utilization <= n * (2^(1/n) - 1))

# 示例
utilization: 55%
preemptions: 2-3次（高优先级任务到达时）
deadline_misses: 0
```

---

## 7. 代码层面区别

### ASAP调度器特殊机制

```cpp
// 1. 能量管理
double getCurrentEnergy() const;
void updateEnergyContinuously(TimeMs current_time);
bool checkEnergyAvailability(AbsRTTask* task);

// 2. 等待队列
std::deque<AbsRTTask*> _waiting_queue;
void addToWaitingQueue(AbsRTTask* task);
void restoreFromWaitingQueue();

// 3. 任务分片
void scheduleSlicingEvent(AbsRTTask* task, Tick slice_time);

// 4. 能量恢复
ASAPEnergyRecoveryEvent* _recovery_event;
void scheduleEnergyRecovery();

// 5. 太阳能数据
ConfigManager* _config_manager;
double getSolarIrradiance(TimeMs time_ms);
```

### 抢占式调度器标准机制

```cpp
// 1. 优先级比较
Tick EDFModel::getPriority() {
    return _rtTask->getDeadline();            // deadline越早优先级越高
}

Tick RMModel::getPriority() {
    return _rtTask->getRelDline();            // 周期越短优先级越高
}

Tick FPModel::getPriority() {
    return _prio;                             // 静态优先级
}

// 2. 抢占调度
void MRTKernel::dispatch();
void MRTKernel::onBeginDispatchMulti();
void MRTKernel::suspend(AbsRTTask* task);

// 3. 任务状态管理
void Task::schedule();
void Task::deschedule();
void ExecInstr::deschedule();                 // 保存进度
```

---

## 8. 总结：如何选择？

### 选择ASAP调度器当：
- ✅ 系统由太阳能供电
- ✅ 能量是关键约束
- ✅ 可以容忍调度延迟（等待能量收集）
- ✅ 任务可以分片执行（unit_time粒度）

### 选择抢占式调度器当：
- ✅ 需要严格实时性保证
- ✅ 任务有明确的deadline
- ✅ 能量不是约束（或能量充足）
- ✅ 需要任务在任意时刻可被抢占

### 混合方案：
可以考虑在ASAP调度器中添加基于优先级的抢占机制：
```cpp
// 在ASAPSlicingEvent中检查优先级
if (new_high_priority_task_arrived && current_task_priority < new_task_priority) {
    // ⭐ 结合能量和优先级的抢占
    preempt_current_task();
    schedule_new_task();
}
```
