# PARTSim 调度器算法分析报告

本文档详细分析 PARTSim 项目中实现的 9 种调度器的算法逻辑，对比其预期设计与实际实现的一致性。

---

## 调度器分类

| 维度 | ASAP 系列 | ALAP 系列 | ST 系列 |
|------|----------|----------|---------|
| Block | ASAP-Block | ALAP-Block | ST-Block |
| NonBlock | ASAP-NonBlock | ALAP-NonBlock | ST-NonBlock |
| Sync | ASAP-Sync | ALAP-Sync | ST-Sync |

### 算法维度说明

- **ASAP (As Soon As Possible)**：尽可能早执行，无 Slack 门控
- **ALAP (As Late As Possible)**：尽可能晚执行，有 Slack 门控
- **ST (Slack Time)**：基于松弛时间的智能调度，结合 ASAP 和 ALAP 特点

- **Block**：能量不足时阻塞系统，宁缺毋滥
- **NonBlock**：能��不足时跳过缺电任务，贪心搜索低优任务
- **Sync**：批次调度，All-or-Nothing 机制

---

## 符合预期的调度器（7个）

| 调度器 | 核心特征 | 状态 |
|--------|---------|------|
| ASAP-Block | 贪婪 + 能量不足即阻塞 | ✅ |
| ASAP-NonBlock | 贪婪 + 贪心搜索低优 | ✅ |
| ASAP-Sync | 批次 + All-or-Nothing | ✅ |
| ALAP-Block | Slack门控 + 阻塞 | ✅ |
| ALAP-NonBlock | Slack门控 + 贪心���索 | ✅ |
| ALAP-Sync | Slack门控 + 批次 + All-or-Nothing | ✅ |
| ST-Sync | 批次 + 深度充电 + Slack唤醒 | ✅ |

---

## 存在问题的调度器（2个）

---

## 问题1：ST-Block 调度器 ✅ 已修复

### 预期逻辑

> ST-Block 调度逻辑的核心是**"智能妥协与宁缺毋滥的全局防守"**。
> - 能量充足时：像 ASAP 一样立即调度（不检查 Slack）
> - 能量不足时：检查 Slack 决定策略
>   - Slack > 0：任务有退让余地，触发全局阻塞，进入深度充电模式
>   - Slack ≤ 0：死线已至，退无可退，强制放行

### 修复前的问题

| 功能 | 预期 | 实际 | 状态 |
|------|------|------|------|
| 能量充足时 | ASAP 调度 | ASAP 调度 | ✅ |
| 能量不足时 | 检查 Slack | **直接阻塞** | ❌ |
| Slack > 0 | 阻塞等待充电 | **直接阻塞** | ✅ (但缺少判断) |
| Slack ≤ 0 | 强制执行 | **直接阻塞** | ❌ |

### 修复状态：✅ 已完成

**修改文件**：`librtsim/scheduler/gpfp_st_block_scheduler.cpp`
**修改位置**：`getTaskN()` 方法，第 1105-1135 行

**修复后代码**：
```cpp
                if (projected_energy_after_dispatch < -EPSILON) {
                    // ⭐ ST-Block核心：能量不足时，检查Slack决定策略
                    Tick task_slack = calculateSlackForTask(task);
                    int64_t slack_ms = static_cast<int64_t>(task_slack);

                    if (task_slack > 0) {
                        // Slack > 0：任务有退让余地，全局阻塞等待充电
                        SCHEDULER_LOG_INFO(std::string("🔋 [ST-Block] 能量不足且Slack>0，全局阻塞等待充电") +
                                          " 任务=" + getTaskName(task) +
                                          " Slack=" + std::to_string(slack_ms) + "ms" +
                                          " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

                        // 激活全局深度休眠锁，禁止任何任务上核
                        _is_charging_sleep = true;
                        _energy_depleted = true;
                        _deep_charging = true;

                        return nullptr;  // 全局阻塞，进入深度充电模式
                    } else {
                        // Slack <= 0：死线已至，退无可退，强制放行
                        SCHEDULER_LOG_WARNING(std::string("🚨 [ST-Block] 能量不足但Slack<=0，死线已至强制放行") +
                                             " 任务=" + getTaskName(task) +
                                             " Slack=" + std::to_string(slack_ms) + "ms" +
                                             " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                             " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

                        // 继续执行，不阻塞（交由底层物理引擎处理能量耗尽）
                    }
                }
```
                //   - Slack > 0：任务还有时间，阻塞等待充电
                //   - Slack <= 0：死线已至，强制尝试执行

                // ⭐ 计算任务的1ms能耗
                double unit_energy = calculateUnitEnergyForTask(task);

                const double EPSILON = 1e-9;
                double projected_energy_after_dispatch =
                    _current_energy - _dispatching_tasks_total_energy - unit_energy;

                if (projected_energy_after_dispatch < -EPSILON) {
                    // ⭐ 能量不足，检查Slack决定策略
                    Tick task_slack = calculateSlackForTask(task);
                    int64_t slack_ms = static_cast<int64_t>(task_slack);

                    if (task_slack > 0) {
                        // ⭐ Slack > 0：任务还有等待充电的余地
                        SCHEDULER_LOG_INFO(std::string("🔋 [ST-Block] 能量不足但Slack>0，阻塞等待充电") +
                                          " 任务=" + getTaskName(task) +
                                          " Slack=" + std::to_string(slack_ms) + "ms" +
                                          " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

                        // ⭐ ST-Block核心：激活深度休眠锁，等待充电
                        _is_charging_sleep = true;
                        _energy_depleted = true;
                        _deep_charging = true;

                        return nullptr;  // 阻塞，进入充电模式
                    } else {
                        // ⭐ Slack <= 0：死线已至，必须强制执行
                        SCHEDULER_LOG_WARNING(std::string("🚨 [ST-Block] 能量不足但Slack<=0，强制执行") +
                                            " 任务=" + getTaskName(task) +
                                            " Slack=" + std::to_string(slack_ms) + "ms" +
                                            " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                            " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

                        // 继续执行（不阻塞），即使能量不足也要尝试
                        // 注意：任务可能无法完成，但必须尝试
                    }
                }
```

---

## 问题2：ST-NonBlock 调度器

### 预期逻辑

> ST-NonBlock 调度逻辑的核心是**"基于松弛时间的智能充电与低优先级的能量窃取"**。
> - 能量充足时：像 ASAP 一样立即调度
> - 能量不足时：检查 Slack
>   - Slack > 0：跳过该任务，继续寻找低优任务（但低优任务也需要检查 Slack）
>   - Slack ≤ 0：强制尝试执行
> - 贪心搜索时：只调度 Slack ≤ 0 的低优任务

### 实际实现问题

| 功能 | 预期 | 实际 | 状态 |
|------|------|------|------|
| 能量充足时 | ASAP 调度 | ASAP 调度 | ✅ |
| 能量不足时检查 Slack | 检查 | 有检查（V82） | ✅ |
| 贪心搜索检查 Slack | **检查** | **不检查（V130删除）** | ❌ |

**问题代码位置**：`librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp` 第 1148-1171 行

```cpp
// 当前错误实现（V130修复删除了Slack检查）：
// ⭐⭐⭐ V130修复：删除贪婪捡漏中的Slack判断！⭐⭐⭐
// ST-NonBlock核心逻辑：有电就跑，不管Slack
// 贪婪捡漏时，只要能量足够就调度，不再检查Slack

if (next_available >= next_unit_energy - EPSILON) {
    // 找到能量足够的后续任务，直接调度（没有检查Slack！）
    return next_task;
}
```

### 修复方案

**修改文件**：`librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp`
**修改位置**：`getTaskN()` 方法贪心搜索部分，第 1148-1171 行

**修改前（错误代码）**：
```cpp
                        // ⭐⭐⭐ V130修复：删除贪婪捡漏中的Slack判断！⭐⭐⭐
                        // ST-NonBlock核心逻辑：有电就跑，不管Slack
                        // 贪婪捡漏时，只要能量足够就调度，不再检查Slack
                        // Slack检查仅用于唤醒定时器，不用于正常调度派发
                        SCHEDULER_LOG_DEBUG(std::string("  [ST-NonBlock V130] 贪心搜索：检查任务 ") +
                                          getTaskName(next_task) +
                                          " 需要=" + std::to_string(next_unit_energy * 1000) + "mJ" +
                                          " 可用=" + std::to_string(next_available * 1000) + "mJ");

                        if (next_available >= next_unit_energy - EPSILON) {
                            // ⭐ 找到能量足够的后续任务，调度它！
                            // ⭐ 只标记任务，不扣除能量（能量将在dispatch后统一扣除）
                            if (_counted_tasks_in_dispatch.find(next_task) == _counted_tasks_in_dispatch.end()) {
                                _counted_tasks_in_dispatch.insert(next_task);
                                _newly_dispatched_this_tick.insert(next_task);

                                SCHEDULER_LOG_INFO(std::string("✅ [ST-NonBlock] 贪心策略：调度后续任务（已标记，暂不扣能量）") +
                                                  " 替换=" + getTaskName(task) +
                                                  " → " + getTaskName(next_task) +
                                                  " 1ms能耗=" + std::to_string(next_unit_energy * 1000) + " mJ");
                            }

                            return next_task;
                        }
```

**修改后（正确代码）**：
```cpp
                        // ⭐ ST-NonBlock核心逻辑：贪心搜索时也要检查Slack
                        // 只有Slack<=0的低优任务才能被调度
                        // 这样可以避免Slack>0的低优任务"偷电"导致高优任务饿死

                        // 检查候选任务的Slack
                        Tick next_slack = calculateSlackForTask(next_task);
                        int64_t next_slack_ms = static_cast<int64_t>(next_slack);

                        if (next_slack > 0) {
                            // Slack>0，跳过这个低优任务
                            SCHEDULER_LOG_DEBUG(std::string("  [ST-NonBlock] 贪心搜索：Slack>0，跳过低优任务 ") +
                                              getTaskName(next_task) +
                                              " Slack=" + std::to_string(next_slack_ms) + "ms");
                            continue;
                        }

                        SCHEDULER_LOG_DEBUG(std::string("  [ST-NonBlock] 贪心搜索：检查任务 ") +
                                          getTaskName(next_task) +
                                          " Slack=" + std::to_string(next_slack_ms) + "ms" +
                                          " 需要=" + std::to_string(next_unit_energy * 1000) + "mJ" +
                                          " 可用=" + std::to_string(next_available * 1000) + "mJ");

                        if (next_available >= next_unit_energy - EPSILON) {
                            // ⭐ 找到能量足够且Slack<=0的后续任务，调度它！
                            if (_counted_tasks_in_dispatch.find(next_task) == _counted_tasks_in_dispatch.end()) {
                                _counted_tasks_in_dispatch.insert(next_task);
                                _newly_dispatched_this_tick.insert(next_task);

                                SCHEDULER_LOG_INFO(std::string("✅ [ST-NonBlock] 贪心策略：调度Slack<=0的低优任务") +
                                                  " 替换=" + getTaskName(task) +
                                                  " → " + getTaskName(next_task) +
                                                  " Slack=" + std::to_string(next_slack_ms) + "ms" +
                                                  " 1ms能耗=" + std::to_string(next_unit_energy * 1000) + " mJ");
                            }

                            return next_task;
                        }
```

---

## ST 系列调度器对比

| 特性 | ST-Block | ST-NonBlock | ST-Sync |
|------|----------|-------------|---------|
| 调度策略 | ASAP + Slack智能门控 | ASAP + Slack智能门控 + 贪心 | ASAP + 批次 |
| 能量充足时 | 立即调度 | 立即调度 | 批量调度 |
| 能量不足时 | 检查Slack | 检查Slack + 贪心 | 检查批次Slack |
| Slack > 0 | 阻塞等待充电 | 跳过+贪心搜索 | 批量休眠 |
| Slack ≤ 0 | 强制执行 | 强制执行 | 批量强制 |
| 贪心搜索 | ❌ 无 | ✅ 有（需检查Slack） | ❌ 批次模式 |
| 深度充电 | ✅ 有 | ✅ 有 | ✅ 有 |

---

## 修复优先级

| 优先级 | 调度器 | 问题 | 影响 |
|--------|--------|------|------|
| **高** | ST-Block | 能量不足时不检查Slack直接阻塞 | 高优任务可能在有充电时间时被错误阻塞 |
| **高** | ST-NonBlock | 贪心搜索时不检查Slack | 低优任务可能在Slack>0时"偷电"导致高优任务饿死 |

---

## 测试验证

修复后应进行以下测试：

### ST-Block 测试用例

```
场景：高优任务能量不足，Slack > 0
预期：系统阻塞等待充电，而不是立即失败
验证：日志显示 "能量不足但Slack>0，阻塞等待充电"

场景：高优任务能量不足，Slack <= 0
预期：强制执行，即使能量不足
验证：日志显示 "能量不足但Slack<=0，强制执行"
```

### ST-NonBlock 测试用例

```
场景：高优任务能量不足，Slack > 0，低优任务 Slack > 0
预期：低优任务被跳过，系统等待充电
验证：日志显示 "贪心搜索：Slack>0，跳过低优任务"

场景：高优任务能量不足，Slack > 0，低优任务 Slack <= 0
预期：低优任务被调度
验证：日志显示 "调度Slack<=0的低优任务"
```

---

## 总结

### 需要修复的调度器

1. **ST-Block**：在 `getTaskN()` 中添加能量不足时的 Slack 检查逻辑
2. **ST-NonBlock**：在贪心搜索中恢复 Slack 检查逻辑

### 修复核心思想

ST 调度器的设计意图是：
- **能量充足时**：ASAP 调度（不管 Slack）
- **能量不足时**：智能门控（基于 Slack 决定策略）

当前实现缺失了"能量不足时的智能门控"，需要补全这个逻辑。

---

*报告生成时间：2026-03-18*
*分析基于 PARTSim 项目代码*
