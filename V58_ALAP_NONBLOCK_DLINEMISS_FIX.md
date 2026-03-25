# V58 逻辑补漏：ALAP-NonBlock Early Abort 必须记录 dline_miss

## 问题描述

V57 重构后，ALAP-NonBlock 使用 `dropHopelessTask()` 主动终止能量不足的任务，但只输出 `kill` 事件，没有输出 `dline_miss` 事件。这导致 Python 评估脚本无法统计到这些失败的任务。

**根本原因**：`dropHopelessTask()` 调用 `concrete_task->killInstance()`，该方法会调用 `deadEvt.drop()`，从而阻止 `DeadEvt::doit()` 触发。JSONTrace 依赖 `DeadEvt::probe()` 来输出 `dline_miss`，因此这些 Early Abort 场景完全缺失 `dline_miss` 记录。

### V57 vs V58 事件对比

| 版本 | dline_miss 数量 | kill 数量 | Python 脚本统计 |
|------|----------------|-----------|----------------|
| V57  | 0              | 12        | **无法统计**    |
| V58  | 12             | 12        | **全部统计**    |

---

## 修改操作

### 1. JSONTrace 新增 `forceLogDlineMiss()` 方法

**文件**：[librtsim/json_trace.cpp](librtsim/json_trace.cpp) 和 [librtsim/include/rtsim/json_trace.hpp](librtsim/include/rtsim/json_trace.hpp)

```cpp
// json_trace.hpp: 新增方法声明
void forceLogDlineMiss(AbsRTTask *task, const std::string &reason = "early_abort_energy_depleted");

// json_trace.cpp: 新增方法实现
void JSONTrace::forceLogDlineMiss(AbsRTTask *task, const std::string &reason) {
    if (!task) return;
    if (max_time >= 0 && SIMUL.getTime() >= max_time) return;

    Task *tt = dynamic_cast<Task*>(task);
    if (!tt) return;

    // 将任务添加到deadline miss集合（防止重复记录descheduled）
    _deadline_missed_tasks.insert(task);

    // 清除任务开始时间记录
    _task_start_times.erase(task);
    _task_start_consumed.erase(task);

    // 输出 dline_miss JSON 事件
    fd << "{ ";
    fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
    fd << "\"event_type\": \"dline_miss\", ";
    fd << "\"task_name\": \"" << tt->getName() << "\", ";
    fd << "\"arrival_time\": \"" << tt->getLastArrival() << "\"";
    // 计算deadline信息
    MetaSim::Tick arrival_time = tt->getLastArrival();
    MetaSim::Tick relative_deadline = tt->getRelDline();
    MetaSim::Tick absolute_deadline = arrival_time + relative_deadline;
    fd << ", \"deadline\": \"" << absolute_deadline << "\"";
    fd << ", \"miss_amount\": \"" << (MetaSim::SIMUL.getTime() - absolute_deadline) << "\"";
    writeEnergyInfo();
    fd << ", \"reason\": \"" << reason << "\"";
    fd << "}";
}
```

### 2. EnergyInfoProvider 接口新增 `logDlineMiss()` 和 `setTraceLogger()`

**文件**：[librtsim/include/rtsim/energy_info_provider.hpp](librtsim/include/rtsim/energy_info_provider.hpp)

```cpp
// 新增方法1：强制记录dline_miss事件
virtual void logDlineMiss(AbsRTTask *task, const std::string &reason = "early_abort") {
    (void)task;
    (void)reason;
}

// 新增方法2：设置JSONTrace指针
virtual void setTraceLogger(void *trace) {
    (void)trace;
}
```

### 3. NonBlock 调度器注册 JSONTrace 并调用 `logDlineMiss()`

**文件**：[librtsim/include/rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp](librtsim/include/rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp)

```cpp
// 新增成员变量
JSONTrace *_trace_logger = nullptr;

// 新增方法声明
void logDlineMiss(AbsRTTask *task, const std::string &reason = "early_abort") override;
void setTraceLogger(void *trace) override {
    _trace_logger = static_cast<JSONTrace *>(trace);
}
```

**文件**：[librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp)

```cpp
// dropHopelessTask() 中，killInstance() 之前注入 dline_miss：
if (count_deadline_miss) {
    _stats.total_deadline_misses++;
}

// ⭐ V58修复：在kill之前必须注入dline_miss记录！
if (count_deadline_miss) {
    logDlineMiss(task, "early_abort_" + reason);
}

concrete_task->killInstance();

// logDlineMiss() 实现：
void ALAPNonBlockScheduler::logDlineMiss(AbsRTTask *task, const std::string &reason) {
    if (_trace_logger && task) {
        _trace_logger->forceLogDlineMiss(task, reason);
    }
}
```

### 4. main.cpp 反向连接 JSONTrace 到调度器

**文件**：[rtsim/main.cpp](rtsim/main.cpp)

```cpp
// ⭐ V58新增：反向连接JSONTrace到调度器，用于Early Abort时注入dline_miss记录
energy_provider->setTraceLogger(tracer.jtrace.get());
```

---

## 测试结果

### V57 vs V58 对比（3c5t, 3000ms）

| 指标 | V57 | V58 |
|------|-----|-----|
| dline_miss 数量 | 0 | **12** |
| kill 数量 | 12 | 12 |
| Python 可统计 | ❌ | ✅ |

### V58 dline_miss 详情

```
[924ms] task_mid1 reason=early_abort_ready admission energy insufficient
[925ms] task_high reason=early_abort_ready admission energy insufficient
[1250ms] task_high reason=early_abort_ready admission energy insufficient
[1400ms] task_low reason=early_abort_ready admission energy insufficient
[1600ms] task_mid1 reason=early_abort_ready admission energy insufficient
[1700ms] task_mid2 reason=early_abort_ready admission energy insufficient
[1750ms] task_high reason=early_abort_ready admission energy insufficient
[2250ms] task_high reason=early_abort_ready admission energy insufficient
[2508ms] task_idle reason=early_abort_ready admission energy insufficient
[2600ms] task_mid1 reason=early_abort_ready admission energy insufficient
[2700ms] task_mid2 reason=early_abort_ready admission energy insufficient
[2750ms] task_high reason=early_abort_ready admission energy insufficient
```

每个 `kill` 事件前都有一个对应的 `dline_miss` 事件，reason 字段清晰标明 `early_abort`。

---

## Block vs NonBlock 语义差异

| 调度器 | Kill 机制 | dline_miss 来源 | V58 修复 |
|--------|-----------|----------------|---------|
| ALAP-Block | `killOnMiss(true)` → DeadEvt → dline_miss | DeadEvt probe | 不需要 |
| ALAP-NonBlock | `dropHopelessTask()` → killInstance() | **V58 新增** | **需要** |
| ALAP-Sync | `killOnMiss(true)` → DeadEvt → dline_miss | DeadEvt probe | 不需要 |

---

## 核心原理

### DeadEvt vs Early Abort 的区别

**正常 Deadline Miss（DeadEvt 路径）**：
1. Task 到达 → `deadEvt.post(deadline)`
2. 时间到达 deadline → `DeadEvt::doit()` → `killInstance()`
3. JSONTrace `probe(DeadEvt)` → 输出 `dline_miss`

**Early Abort（V57 路径）**：
1. Task 到达 → `deadEvt.post(deadline)`
2. 能量耗尽 → `dropHopelessTask()` → `killInstance()` → `deadEvt.drop()`
3. `DeadEvt::doit()` 被跳过 → **没有 dline_miss 输出**

**V58 修复**：
在 `killInstance()` 之前，主动调用 `forceLogDlineMiss()` 注入 `dline_miss` 记录。

### 双向连接机制

```
main.cpp:
  scheduler (EnergyInfoProvider) ←→ JSONTrace
         ↑                              ↑
         │ setEnergyProvider()    setTraceLogger()
         │______________________________│
```

---

## 结论

V58 通过在 `killInstance()` 之前主动注入 `dline_miss` 记录，修复了 Early Abort 场景下 Python 脚本无法统计 deadline miss 的问题。所有 12 个因能量耗尽被 kill 的任务现在都能被正确统计。
