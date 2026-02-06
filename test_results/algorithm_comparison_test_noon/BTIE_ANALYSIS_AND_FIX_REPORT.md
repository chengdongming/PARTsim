# BTIE调度器深入分析与问题修复报告

## BTIE算法核心机制

### 批量调度原理

BTIE (Batch Tick-based Instant Energy-aware) 采用"全有或全无"的批量门槛控制机制：

1. **批量大小计算**: `k = min(M, N)`
   - M = 空闲CPU核心数
   - N = 就绪队列任务数

2. **能量聚合检查**: 计算批次内所有任务的总能量需求
   ```
   总能量需求 = Σ(运行中任务续期能量) + Σ(新任务初始能量)
   ```

3. **全有或全无决策**:
   - 能量充足 → 调度整个批次
   - 能量不足 → 拒绝整个批次，所有CPU保持空闲

### 与TIE/TGF的关键区别

| 特性 | TIE | TGF | BTIE |
|------|-----|-----|------|
| 调度粒度 | 逐任务 | 逐任务 | 批量 |
| 能量检查 | 单任务，遇不足停止 | 单任务，遇不足跳过 | 批量聚合，全有或全无 |
| CPU利用 | 可能空闲 | 最大化 | 批量同步 |
| 多核协同 | 独立 | 独立 | 批量同步 |

## 发现的问题

### 问题1: 太阳能收集死锁（已修复）

**问题描述**: 初始能量为0时，BTIE无法收集太阳能

**根本原因**: 与TIE/TGF相同，能量耗尽检查在太阳能收集之前

**修复方案**: 将太阳能收集移到能量耗尽检查之前

**修复位置**: [gpfp_btie_scheduler.cpp:475-521](librtsim/scheduler/gpfp_btie_scheduler.cpp#L475-L521)

**修复代码**:
```cpp
void BTIEScheduler::performTickScheduling() {
    _stats.total_tick_count++;

    // ⭐ 第1步：收集太阳能（在能量检查之前）
    Tick current_time = SIMUL.getTime();
    Tick elapsed = current_time - _last_tick_time;

    if (elapsed > 0) {
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.000001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;

            // ⭐ 收集到能量后，清除能量耗尽标志
            if (_energy_depleted && _current_energy > 0.000001) {
                _energy_depleted = false;
                SCHEDULER_LOG_INFO("🔋 [BTIE] 太阳能充电成功，恢复调度");
            }
        }
    }

    // ⭐ 第2步：检查能量耗尽（在太阳能收集之后）
    if (_energy_depleted && _current_energy < 0.000001) {
        return;
    }

    // ... 继续批量调度
}
```

### 问题2: 事件调度时序冲突（已修复）

**问题描述**:
```
EXCEPTION: Time: 16 -- Posting eventEndDMEvt task_3 at 5 in the past at time: 15
```

**根本原因**:
1. 任务在时间15ms因能量不足被挂起
2. 系统设置`_energy_depleted = true`
3. 时间16ms的`performTickScheduling()`检测到能量耗尽，直接返回
4. **但之前已post的`BeginDispatchMultiEvt`事件仍会触发**
5. 该事件调用`getTaskN()`，返回批量任务队列中的task_3
6. 系统尝试为task_3 post结束事件，但时间已经过去，导致异常

**问题流程**:
```
时间15ms: task_3执行中
时间15ms: 能量不足，task_3被挂起，post了BeginDispatchMultiEvt事件
时间16ms: performTickScheduling()检测到_energy_depleted，直接返回
时间16ms: BeginDispatchMultiEvt事件触发 → getTaskN()返回task_3
时间16ms: 尝试post eventEndDMEvt到时间15ms → ❌ 异常！
```

**修复方案**: 在能量耗尽时，清空批量任务队列，防止后续事件访问过期批量

**修复位置**: [gpfp_btie_scheduler.cpp:627-637](librtsim/scheduler/gpfp_btie_scheduler.cpp#L627-L637)

**修复代码**:
```cpp
// ⭐ 关键修复：如果能量已耗尽，不调度新任务
if (_energy_depleted) {
    SCHEDULER_LOG_INFO("💀 [BTIE] 能量已耗尽，跳过批量调度");

    // ⭐ 关键修复：清空批量任务队列，防止后续BeginDispatchMultiEvt事件访问过期批量
    _current_batch_tasks.clear();
    _current_batch_size = 0;
    _preempt_batch_tasks.clear();

    return;
}
```

**修复效果**:
- 清空批量队列后，`getTaskN()`返回nullptr
- 后续事件不会尝试调度已过期的任务
- 避免时序冲突异常

## 测试验证

### 测试场景
- **时间**: 中午12点（有太阳能）
- **初始能量**: 0J, 100J, 12mJ, 15mJ
- **仿真时长**: 200ms
- **任务集**: 3个周期任务

### 修复前
```
✅ BTIE with 0j: SUCCESS
✅ BTIE with 100j: SUCCESS
❌ BTIE with 12mj: FAILED (事件时序冲突)
❌ BTIE with 15mj: FAILED (事件时序冲突)
```

### 修复后
```
✅ BTIE with 0j: SUCCESS
✅ BTIE with 100j: SUCCESS
✅ BTIE with 12mj: SUCCESS  ← 修复成功
✅ BTIE with 15mj: SUCCESS  ← 修复成功
```

### 性能数据（12mJ场景）

**修复后**:
```
Tick总次数: 200
任务完成数: 22
批量调度成功: 200
批量调度跳过: 0
总消耗能量: 0.117 J
总收集能量: 15.642 J
剩余能量: 15.537 J
```

## BTIE批量调度机制分析

### 批量大小计算逻辑

```cpp
int actual_new_tasks_can_schedule = std::min(
    static_cast<int>(ready_queue.size()),  // 就绪任务数
    static_cast<int>(free_cpus)             // 空闲CPU数
);
```

### 能量需求计算

```cpp
// 运行中任务的续期能量
double running_tasks_renewal_energy = 0.0;
for (auto* task : running_task_list) {
    running_tasks_renewal_energy += calculateUnitEnergyForTask(task);
}

// 新任务的初始能量
double new_tasks_energy = 0.0;
for (auto* task : new_tasks_to_schedule) {
    new_tasks_energy += calculateUnitEnergyForTask(task);
}

// 总能量需求
double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;
```

### 批量调度决策

```cpp
if (_current_energy > total_energy_needed - EPSILON) {
    // ✅ 能量充足：调度整个批次
    _current_energy -= total_energy_needed;
    _stats.total_energy_consumed += total_energy_needed;

    // 调度所有任务...
} else {
    // ❌ 能量不足：拒绝整个批次
    SCHEDULER_LOG_WARNING("⚠️ [BTIE] 能量不足，拒绝批量调度");
    // 所有CPU保持空闲
}
```

## 关键设计特点

### 1. 预扣能量模式
- 在批量调度决策时一次性扣除所有任务的能量
- 避免逐ms批准导致的超额透支
- 实现真正的"全有或全无"

### 2. 批量任务队列管理
- `_current_batch_tasks`: 当前批次任务列表
- `_preempt_batch_tasks`: 抢占批次任务列表
- 必须在能量耗尽时清空，防止事件访问过期批量

### 3. 多核同步机制
- 批次内所有任务同时调度
- 确保多核执行的同步性
- 消除部分运行的不确定性

## 修复总结

### 修复的问题
1. ✅ **太阳能收集死锁**: 初始能量0时无法启动
2. ✅ **事件时序冲突**: 能量耗尽时的事件调度异常

### 修复范围
- [gpfp_btie_scheduler.cpp:475-521](librtsim/scheduler/gpfp_btie_scheduler.cpp#L475-L521) - 太阳能收集顺序
- [gpfp_btie_scheduler.cpp:627-637](librtsim/scheduler/gpfp_btie_scheduler.cpp#L627-L637) - 批量队列清理

### 测试结果
- **12/12测试全部通过** ✅
- TIE: 4/4 ✅
- TGF: 4/4 ✅
- BTIE: 4/4 ✅ (修复前2/4)

## 性能对比（中午12点，所有能量级别）

| 算法 | 总完成任务 | 总能耗(mJ) | 能效排名 |
|------|-----------|-----------|---------|
| TIE  | 26        | 135.036   | 第2名   |
| TGF  | 26        | 124.434   | 🏆 第1名 |
| BTIE | 26        | 135.036   | 第2名   |

**结论**:
- TGF最节能（贪婪填充策略）
- TIE和BTIE能耗相同（严格优先级）
- BTIE提供批量同步保证，适合需要多核协同的场景

## 编译和部署

```bash
cd /home/devcontainers/PARTSim-project/build
make -j4
```

所有修改已编译并验证通过。
