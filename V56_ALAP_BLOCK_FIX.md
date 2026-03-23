# V56 ALAP-Block 捉鬼记：拔除全局断头台

## Bug 发现

V55 重构后的追踪文件与 V53 **完全一致**（MD5: `3933d2f2...`）！这说明尽管 V55 重构了续期能量检查逻辑，但 `task_high` 在 t=924 依然被群体挂起！

### 根因：两个"全局断头台"

**病灶 1**：`scheduleEnergyDepletionEvent()` 在续期循环末尾被调用
- 使用 `calculateTotalPowerConsumption()`（总功耗）预测全局耗尽时间
- 注册闹钟事件，在预测耗尽时刻触发 `onEnergyDepleted()`
- **问题**：即使逐级剥夺逻辑正确执行，只要能量足够运行高优先级任务，全局闹钟仍会在未来的某时刻触发

**病灶 2**：`onEnergyDepleted()` 无脑群体挂起所有任务
- 遍历所有运行任务，全部挂起
- **问题**：破坏了逐级剥夺逻辑的成果

### 捉鬼操作

**1. 废除 `scheduleEnergyDepletionEvent()`**

修改位置：续期循环末尾（第 658-673 行）

```cpp
// 修改前：
cancelEnergyDepletionEvent();
double total_power = calculateTotalPowerConsumption();
if (total_power > 0.0 && _current_energy > 0.0) {
    MetaSim::Tick time_to_deplete = predictTimeToDepletion(_current_energy, total_power);
    scheduleEnergyDepletionEvent(time_to_deplete);
}

// 修改后：
cancelEnergyDepletionEvent();
// ⭐ V56捉鬼：废除全局能量耗尽预测闹钟！
```

同时将 `scheduleEnergyDepletionEvent()` 函数体改为空操作（打印废除警告）。

**2. 废除 `onEnergyDepleted()`**

```cpp
// 修改前：
_energy_depleted = true;
// 遍历所有任务，群体挂起
for (AbsRTTask *task : tasks_to_suspend) {
    _kernel->suspend(task);
}

// 修改后：
// ⚠️ 绝对不设置 _energy_depleted = true
// ⚠️ 绝对不调用任何 _kernel->suspend()
// ⚠️ 绝对不调用 dispatch()
// → Block壁垒由逐级剥夺逻辑自然建立
```

---

## 测试结果

### 核心验证

| 指标 | V53 | V56 |
|------|------|-----|
| task_high descheduled | 924ms | **925ms** ✅ |
| 总消耗能量 | 0.499000J | **0.499600J** ✅ |
| 剩余能量 | 0.001000J | 0.000400J |
| 追踪文件 MD5 | `3933d2f2...` | `05fd8f49...` ✅ |

**task_high 从 t=924 多活到了 t=925！多消耗了 0.6mJ = 1ms × 0.6mJ/ms！**

### Tick 924 逐级剥夺过程

```
Tick 924: 剩余能量 = 1.0mJ
  排序: task_high(500ms) → task_mid1(1000ms) → task_mid2(1000ms)

  task_high: 1.0 ≥ 0.6 ✅ 续期成功，剩余 0.4mJ
  task_mid1: 0.4 < 0.6 🚨 BLOCK壁垒触发！挂起 task_mid1
  task_mid2: trigger_block=true 🛑 连坐挂起

Tick 925: 剩余能量 = 0.4mJ
  task_high: 0.4 < 0.6 🚨 BLOCK壁垒触发！挂起 task_high

Tick 926+: 剩余能量 = 0.4mJ
  所有任务 Block 壁垒生效，getTaskN() 返回 nullptr
```

### dline_miss 原因对比

V53: `insufficient_time`（错误，应该是 energy_depleted）
V56: `energy_depleted`（正确，准确反映根因）

---

## 核心原理

### V56 捉鬼的核心洞察

```
旧的错误逻辑：
  1. Tick 边界扣能量
  2. 续期检查（独立判断）→ 可能挂起一些任务
  3. 注册全局耗尽闹钟
  4. 全局闹钟触发 → onEnergyDepleted() → 群体挂起所有任务
  ❌ 全局闹钟覆盖了续期检查的成果

V56 正确逻辑：
  1. Tick 边界扣能量
  2. 逐级剥夺 + Block壁垒 → 正确挂起
  3. 不注册任何闹钟
  4. Block壁垒自然生效
  ✅ Block壁垒完全由 Tick 边界逻辑建立
```

---

## Block vs NonBlock 最终对比

| 特性 | Block (V56) | NonBlock |
|------|-------------|----------|
| 高优先级任务能量不足 | 连坐挂起低优先级任务 | 只挂起当前任务 |
| Block 壁垒建立 | Tick 边界的逐级剥夺逻辑 | 不建立壁垒 |
| 低优先级任务接管 | ❌ 不允许 | ✅ 允许 |
| 全局能量耗尽闹钟 | ❌ 废除 | ❌ 不使用 |

---

## 结论

V56 通过拔除两个"全局断头台"实现了真正的 Block 语义：

1. **废除全局耗尽闹钟**：不再使用 `scheduleEnergyDepletionEvent()`，Block 壁垒完全由逐级剥夺逻辑建立
2. **废除群体挂起**：不再在 `onEnergyDepleted()` 中群体挂起所有任务
3. **task_high 正确多活**：从 t=924 多活到 t=925（+1ms）
4. **dline_miss 原因准确**：从 "insufficient_time" 变为 "energy_depleted"
