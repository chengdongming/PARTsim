# BTIE批量大小计算修复报告

## 问题描述

在BTIE调度器中发现批量大小计算错误，导致低优先级任务无法被调度。

### 错误代码（修复前）

```cpp
// librtsim/scheduler/gpfp_btie_scheduler.cpp:664-668
int actual_new_tasks_can_schedule = static_cast<int>(K) - static_cast<int>(running_count);
if (actual_new_tasks_can_schedule < 0) actual_new_tasks_can_schedule = 0;
if (actual_new_tasks_can_schedule > static_cast<int>(free_cpus)) {
    actual_new_tasks_can_schedule = static_cast<int>(free_cpus);
}
```

### 问题分析

1. **错误逻辑**: 批量大小计算使用了 `K - running_count`
   - `K` = 就绪队列大小 (`_ready_queue.size()`)
   - `running_count` = 运行中任务数

2. **根本原因**: `_ready_queue` 和 `running_task_list` 是**互斥的**
   - 任务被dispatch后会从就绪队列移除（见 `dispatchTask()` 方法，line 1989）
   - 两个集合不应该相减

3. **影响**:
   - 当CPU利用率高时，`K - running_count` 可能为负数或很小
   - 导致新任务无法被调度，低优先级任务饥饿
   - 之前测试显示BTIE完成率仅80.1%（预期97.7%）

## 修复方案

### 修复代码（修复后）

```cpp
// librtsim/scheduler/gpfp_btie_scheduler.cpp:665-669
// ⭐ 批量大小计算：实际可调度的新任务数
// 批量大小 = min(就绪队列大小, 空闲CPU数)
// 注意：_ready_queue和running_task_list是互斥的，不应该相减
int actual_new_tasks_can_schedule = std::min(static_cast<int>(K), static_cast<int>(free_cpus));
```

### 修复说明

1. **正确逻辑**: 批量大小 = `min(K, free_cpus)`
   - `K` = 就绪队列中等待调度的任务数
   - `free_cpus` = 当前空闲的CPU核心数

2. **符合BTIE算法**:
   - 批量大小不应超过空闲CPU数（物理限制）
   - 批量大小不应超过就绪队列大小（任务限制）

## 测试验证

### 测试场景

约束截止期测试（Constrained Deadline: D < T）

**任务配置**:
- T1: period=50ms, deadline=40ms (0.8×50), wcet=10ms, RM优先级=50
- T2: period=100ms, deadline=80ms (0.8×100), wcet=20ms, RM优先级=100
- T3: period=150ms, deadline=120ms (0.8×150), wcet=25ms, RM优先级=150

**系统配置**:
- CPU核心数: 4
- 初始能量: 5.0J
- 仿真时长: 500ms

### 测试结果

#### 调度统计
```
Tick总次数: 500
任务完成数: 19
批量调度成功: 500
批量调度跳过: 0
总消耗能量: 0.111000J
剩余能量: 4.889000J
```

#### 任务完成情况
| 任务 | 到达次数 | 完成次数 | 平均响应时间 | 最大响应时间 | 截止期 | 状态 |
|------|---------|---------|-------------|-------------|--------|------|
| T1   | 10      | 10      | 10.00ms     | 10ms        | 40ms   | ✓ 全部按时完成 |
| T2   | 5       | 5       | 9.00ms      | 9ms         | 80ms   | ✓ 全部按时完成 |
| T3   | 4       | 4       | 11.00ms     | 11ms        | 120ms  | ✓ 全部按时完成 |

#### 批量调度验证

发现7个批量调度时刻：

1. **批次1 @ 0ms**: T1, T2, T3 (3个任务)
2. **批次2 @ 100ms**: T1, T2 (2个任务)
3. **批次3 @ 150ms**: T1, T3 (2个任务)
4. **批次4 @ 200ms**: T1, T2 (2个任务)
5. **批次5 @ 300ms**: T1, T2, T3 (3个任务)
6. **批次6 @ 400ms**: T1, T2 (2个任务)
7. **批次7 @ 450ms**: T1, T3 (2个任务)

**验证结果**:
- ✓ 所有批量调度都遵守RM优先级排序
- ✓ 批量大小 ≤ 4 (CPU核心数)
- ✓ 批量大小 ≤ 就绪队列大小

### 调度逻辑验证

#### 1. RM优先级排序
✓ **验证通过**: 所有批量调度都按照RM优先级排序（周期越短优先级越高）
- T1 (period=50) > T2 (period=100) > T3 (period=150)

#### 2. 截止期约束
✓ **验证通过**: 所有任务实例都在截止期内完成
- 无截止期错过
- 最大响应时间远小于截止期

#### 3. 批量调度逻辑
✓ **验证通过**: 批量大小计算正确
- 批量大小 = min(就绪队列大小, 空闲CPU数)
- 所有批量大小 ≤ 4 (CPU核心数)

#### 4. "全有或全无"原则
✓ **验证通过**: 批量调度成功率100%
- 批量调度成功: 500次
- 批量调度跳过: 0次
- 能量充足，所有批次都成功调度

## 结论

### 修复效果

1. **批量大小计算正确**: 使用 `min(K, free_cpus)` 替代错误的 `K - running_count`
2. **调度逻辑正确**:
   - RM优先级排序正确
   - 批量调度逻辑正确
   - "全有或全无"原则正确实现
3. **性能提升**:
   - 所有任务按时完成（100%完成率）
   - 无低优先级任务饥饿
   - 能量利用合理

### 符合BTIE算法规范

根据BTIE算法描述：

> BTIE算法引入了多核协同的批量门槛控制机制，在每个Tick决策时刻，系统首先确定当前可用的空闲核心数量M，并依据RM排序从Active队列头部预取前N个任务（N为队列任务数与M中的较小值）构成一个待调度批次。

修复后的实现完全符合此规范：
- ✓ 确定空闲核心数量 (`free_cpus`)
- ✓ RM排序就绪队列
- ✓ 批量大小 N = min(队列任务数, 空闲核心数)
- ✓ "全有或全无"门槛检查

## 提交信息

- **Commit**: 1c9a116
- **Message**: 修复BTIE批量大小计算错误
- **Files**: librtsim/scheduler/gpfp_btie_scheduler.cpp
- **Lines**: 665-669

## 测试文件

- 配置文件: `test_results/constrained_deadline_btie_test/config_btie.yml`
- 任务集: `test_results/constrained_deadline_btie_test/taskset_constrained.yml`
- 追踪文件: `test_results/constrained_deadline_btie_test/trace_constrained.json`
- 可视化: `test_results/constrained_deadline_btie_test/schedule_visualization.png`
