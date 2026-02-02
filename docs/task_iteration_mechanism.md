# 任务周期迭代机制说明

## 📋 概述

在PARTSim中，周期性任务通过事件驱动机制自动生成多个实例。每个任务实例都有自己的到达时间和截止期。

## 🔄 任务实例生成流程

### 1. 任务初始化

```cpp
// BTIETaskModel构造函数 (gpfp_btie_scheduler.cpp:222-236)
BTIETaskModel::BTIETaskModel(AbsRTTask *t, int period, int wcet,
                           const std::string &workload,
                           double energy_coefficient,
                           MetaSim::Tick arrival_offset)
    : _task(t),
      _period(period),              // 周期 T
      _wcet(wcet),                  // 最坏执行时间 C
      _workload(workload),
      _energy_coefficient(energy_coefficient),
      _rm_priority(period),         // RM优先级 = 周期
      _arrival_offset(arrival_offset),  // 初始偏移
      _next_release(arrival_offset),    // 下次到达时间 = 初始偏移
      _total_energy(0.0),
      _unit_energy(0.0)
```

**关键参数：**
- `period`: 任务周期 T
- `arrival_offset`: 第一个实例的到达时间偏移
- `_next_release`: 下一个实例的到达时间

### 2. 任务到达事件 (onArrival)

```cpp
// task.cpp:310-360
void Task::onArrival(Event *e) {
    if (!isActive()) {
        // 1. 处理新实例到达
        handleArrival(SIMUL.getTime());

        // 2. 通知内核
        _kernel->onArrival(this);
    } else {
        // 任务已激活，缓冲到达事件
        buffArrival();
    }

    // 3. 安排下一次到达
    reactivate();
}
```

### 3. 处理到达 (handleArrival)

```cpp
// task.cpp:180-220
void Task::handleArrival(Tick arr) {
    // 1. 设置到达时间
    arrival = arr;
    lastArrival = arr;

    // 2. 重置执行时间和指令
    execdTime = 0;
    actInstr = instrQueue.begin();

    // 3. 计算绝对截止期
    _dl = getArrival() + _rdl;  // 绝对截止期 = 到达时间 + 相对截止期

    // 4. 安排截止期检查事件
    if (_dl >= SIMUL.getTime()) {
        deadEvt.post(_dl);
    }

    // 5. 设置任务状态为就绪
    state = TSK_READY;
}
```

**关键计算：**
```
绝对截止期 = 到达时间 + 相对截止期
_dl = arrival + _rdl
```

### 4. 重新激活 (reactivate)

```cpp
// task.cpp:170-178
void Task::reactivate() {
    Tick v;

    if (int_time != nullptr) {
        // 获取下一次到达的间隔时间（周期）
        v = (Tick) int_time->get();

        if (v > 0) {
            // 安排下一次到达事件
            arrEvt.post(SIMUL.getTime() + v);
        }
    }
}
```

**周期性任务的int_time：**
- 对于周期性任务，`int_time`是一个返回固定周期值的随机变量
- 每次调用`int_time->get()`返回周期T
- 下一次到达时间 = 当前时间 + 周期

### 5. 实例结束 (onEndInstance)

```cpp
// task.cpp:371-400
void Task::onEndInstance(Event *) {
    // 1. 取消截止期检查事件
    deadEvt.drop();

    // 2. 重置指令队列
    actInstr = instrQueue.begin();
    lastArrival = arrival;

    // 3. 设置任务状态为非激活
    state = TSK_IDLE;

    // 4. 通知内核任务结束
    _kernel->onEnd(this);

    // 5. 处理缓冲的到达事件（如果有）
    if (!arrQueue.empty()) {
        // 处理下一个缓冲的实例
        fakeArrEvt.post(SIMUL.getTime());
    }
}
```

## 📊 任务实例迭代示例

假设有一个任务：
- 周期 T = 50ms
- 相对截止期 D = 40ms (约束截止期，D < T)
- 到达偏移 = 0ms

### 时间线：

```
时间轴:  0ms    40ms   50ms   90ms   100ms  140ms  150ms
         |      |      |      |      |      |      |
实例1:   到达   截止   ✓

实例2:          到达   截止   ✓

实例3:                 到达   截止   ✓
```

### 每个实例的计算：

**实例1:**
- 到达时间: 0ms
- 绝对截止期: 0 + 40 = 40ms
- 下次到达: 0 + 50 = 50ms

**实例2:**
- 到达时间: 50ms
- 绝对截止期: 50 + 40 = 90ms
- 下次到达: 50 + 50 = 100ms

**实例3:**
- 到达时间: 100ms
- 绝对截止期: 100 + 40 = 140ms
- 下次到达: 100 + 50 = 150ms

## 🔍 关键代码位置

### 任务模型定义
- **文件**: `librtsim/scheduler/gpfp_btie_scheduler.cpp`
- **行号**: 222-236 (BTIETaskModel构造函数)

### 到达事件处理
- **文件**: `librtsim/task.cpp`
- **函数**:
  - `Task::onArrival()` (行310-360)
  - `Task::handleArrival()` (行180-220)
  - `Task::reactivate()` (行170-178)

### 实例结束处理
- **文件**: `librtsim/task.cpp`
- **函数**: `Task::onEndInstance()` (行371-400)

## 📝 重要概念

### 1. 相对截止期 vs 绝对截止期

- **相对截止期 (_rdl)**: 从到达时间开始计算的截止期长度
  - 对于隐式截止期: `_rdl = period`
  - 对于约束截止期: `_rdl < period`

- **绝对截止期 (_dl)**: 实际的截止时间点
  - 计算公式: `_dl = arrival + _rdl`

### 2. 周期性任务的自动迭代

周期性任务通过以下机制自动生成实例：

1. **到达事件触发** → `onArrival()`
2. **处理当前实例** → `handleArrival()`
3. **安排下次到达** → `reactivate()` → `arrEvt.post(当前时间 + 周期)`
4. **实例完成** → `onEndInstance()`
5. **循环回到步骤1**

### 3. 约束截止期的实现

在我们的测试中，通过修改任务文件中的`deadline`字段实现：

```yaml
taskset:
  - name: task_0
    iat: 50          # 周期 T = 50ms
    deadline: 40     # 相对截止期 D = 40ms (D/T = 0.8)
    runtime: 10      # WCET C = 10ms
```

调度器会：
1. 读取`deadline`字段作为相对截止期`_rdl`
2. 每次实例到达时计算: `绝对截止期 = 到达时间 + deadline`
3. 在绝对截止期时检查任务是否完成
4. 如果未完成，触发`deadline miss`事件

## ✅ 总结

任务实例的迭代是通过**事件驱动**机制实现的：

1. 每个任务实例到达时触发`arrEvt`事件
2. 处理到达，计算截止期，设置任务为就绪状态
3. 自动安排下一个周期的到达事件
4. 实例完成后，如果有缓冲的到达事件，继续处理
5. 周而复始，直到仿真结束

这种机制确保了：
- ✅ 周期性任务自动生成多个实例
- ✅ 每个实例有独立的到达时间和截止期
- ✅ 支持约束截止期 (D < T)
- ✅ 正确处理任务重叠（通过缓冲机制）
