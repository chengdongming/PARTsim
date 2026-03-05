# ST 系列调度算法代码检查报告

> 检查日期: 2026-03-04
> 对照文档: 《基于 1ms 离散时间的 ST 系列调度算法白皮书 (修订版)》

---

## 一、三大公理检查

| 公理 | 白皮书定义 | ST-Block | ST-NonBlock | ST-Sync |
|------|-----------|----------|-------------|---------|
| **ASAP 贪婪执行公理** | 正常调度不检查 Slack | ✅ 符合 | ✅ 符合 | ✅ 符合 |
| **1ms 查票公理** | 只检查 1ms 能量 | ✅ 符合 | ✅ 符合 | ✅ 符合 (V131修复) |
| **全局深度休眠锁** | 缺电挂起上锁 | ✅ 符合 (V130修复) | ✅ 符合 (V132修复: 不上锁) | ✅ 符合 (V131修复) |

---

## 二、ST-Block (严格阻塞式)

### 2.1 白皮书定义

```
能量不足 (current_energy < unit_energy(T1)):
1. T1 被挂起。调度器直接中止本 Tick 的后续分配 (return)
2. 上锁死睡：激活全局深度休眠锁 (_is_charging_sleep = true)
3. 定闹钟：计算 T1 的松弛时间 S(T1)，设定系统唤醒定时器
```

### 2.2 代码位置与修复

| 逻辑 | 代码位置 | 状态 |
|------|---------|------|
| 1ms 能量检查 | `getTaskN()` 第1071-1073行 | ✅ 符合 |
| 能量不足时中止调度 | `getTaskN()` 第1089行 `return nullptr` | ✅ 符合 |
| 激活深度休眠锁 | `getTaskN()` 第1082行 | ✅ **V130已修复** |
| 定闹钟 | `performTickScheduling()` 第722-740行 | ✅ 符合 |

### 2.3 修复内容

**文件**: `librtsim/scheduler/gpfp_st_block_scheduler.cpp`

```cpp
// 第1080-1086行 (V130修复)
// ⭐ ST-Block V130修复: 高优任务能量不足，立即激活全局深度休眠锁
// 符合白皮书定义: 在锁解开前，禁止任何任务上核
_is_charging_sleep = true;

// 设置能量耗尽标志，系统将进入充电模式
_energy_depleted = true;
_deep_charging = true;
```

---

## 三、ST-NonBlock (贪婪回填式)

### 3.1 白皮书定义

```
能量不足 (available_energy < unit_energy(Ti)):
1. Ti 被挂起。**不上全局锁**
2. 独立闹钟：为 Ti 单独计算松弛时间 S(Ti)，并为其注册专属的唤醒定时器
3. 贪婪搜索：跳过 Ti，带着剩余的 available_energy 继续检查后续的低优任务
```

### 3.2 代码位置与修复

| 逻辑 | 代码位置 | 状态 |
|------|---------|------|
| 1ms 能量检查 | `getTaskN()` 第1062-1063行 | ✅ 符合 |
| 能量不足时跳过任务 | `getTaskN()` 第1074行 | ✅ 符合 |
| **不上全局锁** | `getFirst()` 第949-952行 | ✅ **V132已修复** |
| 贪心搜索低优任务 | `getTaskN()` 第1082-1161行 | ✅ 符合 |
| 独立闹钟 | `performTickScheduling()` 第799-803行 | ✅ 符合 |

### 3.3 修复内容

**文件**: `librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp`

**问题**: 原代码在 `getFirst()` 中设置了 `_is_charging_sleep = true`，不符合白皮书定义

**修复** (V132):

```cpp
// 第949-952行 (V132修复)
// ⭐ ST-NonBlock V132修复：符合白皮书定义，**不上全局锁**
// 白皮书明确说 ST-NonBlock "不上全局锁"，允许低优任务捡漏
// 为被跳过的高优任务设置独立唤醒定时器（在贪心策略中处理）
SCHEDULER_LOG_INFO("🔓 [ST-NonBlock V132] 符合白皮书：不上全局锁，允许贪心捡漏");
```

---

## 四、ST-Sync (同步批量式)

### 4.1 白皮书定义

```
调度评估 (每 1ms):
1. 从就绪队列头部取出前 K 个任务（K ≤ M），组成一个同步执行组（Group）
2. 汇总组内能耗：计算这 K 个任务在下一毫秒的总功耗 E_batch = Σ unit_energy(Tj)

能量充足 (current_energy >= E_batch):
  - 全组 K 个任务同时派发上核，扣除 E_batch

能量不足 (current_energy < E_batch):
1. 全组挂起：绝不允许组内只有部分任务上核，K 个任务全员挂起
2. 上锁死睡：激活全局深度休眠锁 (_is_charging_sleep = true)
3. 定组闹钟：分别计算组内 K 个任务的松弛时间，取最小值 S_min，设定系统唤醒定时器
```

### 4.2 代码位置与修复

| 逻辑 | 代码位置 | 状态 |
|------|---------|------|
| 取前 K 个任务 | `getTaskN()` 第1636-1638行 | ✅ 符合 |
| **1ms 总功耗计算** | `getTaskN()` 第1655-1660行 | ✅ **V131已修复** |
| 能量充足时全组派发 | `performTickScheduling()` 第1380-1403行 | ✅ 符合 |
| 能量不足时全组挂起 | `getTaskN()` 第1673-1679行 | ✅ 符合 |
| 激活深度休眠锁 | `getTaskN()` 第1677行 | ✅ **V131已修复** |
| 定组闹钟 (S_min) | `performTickScheduling()` 第1048-1070行 | ✅ 符合 |

### 4.3 修复内容

**文件**: `librtsim/scheduler/gpfp_st_sync_scheduler.cpp`

#### 修复1: 1ms 总功耗计算 (V131)

**原代码** (V113, 错误):
```cpp
// 第1656-1664行 - 计算的是 WCET 总能量，不是 1ms 总功耗
batch_energy_v108 += unit_energy * wcet_ms;  // ❌ 错误
```

**修复后** (V131):
```cpp
// 第1655-1660行 (V131修复)
// ⭐⭐⭐ V131修复：计算K个任务的1ms总功耗（符合白皮书"1ms查票公理"）
// 白皮书定义：E_batch = Σ unit_energy(T_j)，即下一毫秒的总功耗
double batch_energy_v108 = 0.0;
for (int i = 0; i < K_v108 && i < static_cast<int>(sorted_ready_v108.size()); ++i) {
    AbsRTTask* task = sorted_ready_v108[i];
    double unit_energy = calculateUnitEnergyForTask(task);
    batch_energy_v108 += unit_energy;  // ✅ 1ms总功耗（不是WCET总能量）
}
```

#### 修复2: 激活深度休眠锁 (V131)

**原代码** (缺少):
```cpp
// 第1677-1678行 - 没有设置 _is_charging_sleep
_energy_depleted = true;
return nullptr;
```

**修复后** (V131):
```cpp
// 第1676-1678行 (V131修复)
// ⭐ ST-Sync V131修复：全组挂起时激活深度休眠锁（符合白皮书定义）
_is_charging_sleep = true;
_energy_depleted = true;
return nullptr;
```

---

## 五、问题汇总与修复状态

| 问题 | 调度器 | 修复版本 | 状态 |
|------|--------|---------|------|
| getTaskN 能量不足时未设置深度休眠锁 | ST-Block | V130 | ✅ 已修复 |
| getTaskN 能量不足时未设置深度休眠锁 | ST-Sync | V131 | ✅ 已修复 |
| 错误地设置了全局锁 | ST-NonBlock | V132 | ✅ 已修复 |
| 使用 WCET 总能量而非 1ms 能量 | ST-Sync | V131 | ✅ 已修复 |

---

## 六、修复后的代码符合性总结

| 调度器 | 深度休眠锁 | 1ms 能量检查 | 贪心捡漏 | 全组挂起 | 状态 |
|--------|-----------|--------------|---------|---------|------|
| **ST-Block** | ✅ 上锁 | ✅ | N/A | N/A | **✅ 符合白皮书** |
| **ST-NonBlock** | ✅ 不上锁 | ✅ | ✅ | N/A | **✅ 符合白皮书** |
| **ST-Sync** | ✅ 上锁 | ✅ | N/A | ✅ | **✅ 符合白皮书** |

---

## 七、关键代码位置索引

### ST-Block
- 深度休眠锁检查: `performTickScheduling()` 第555-581行
- 1ms 能量检查: `getTaskN()` 第1071-1090行
- 唤醒定时器设置: `performTickScheduling()` 第722-740行

### ST-NonBlock
- 深度休眠锁检查: `performTickScheduling()` 第580-601行
- 1ms 能量检查 + 贪心捡漏: `getTaskN()` 第1062-1161行
- 独立唤醒定时器: `performTickScheduling()` 第799-803行

### ST-Sync
- 深度休眠锁检查: `performTickScheduling()` 第567-593行
- 批量 1ms 能量检查: `getTaskN()` 第1653-1689行
- 全组挂起 + 定组闹钟: `performTickScheduling()` 第1030-1073行
