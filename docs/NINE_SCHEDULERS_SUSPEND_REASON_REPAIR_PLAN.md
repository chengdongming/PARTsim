# 九种调度算法的挂起原因修复方案

> 目标：**只修复 descheduled / suspend 的原因标注**，不修改任何调度逻辑、能量准入逻辑、批量语义、深度充电语义或抢占策略。
>
> 当前结论：**ST-Sync 已基本修复完成，可作为参考实现。**

---

## 1. 问题定义

当前 `JSONTrace` 在记录 `descheduled` 事件时，会读取调度器通过 `EnergyInfoProvider` 提供的挂起原因：

- `insufficient_energy`
- `preemption`
- `unknown`

接口定义见 [energy_info_provider.hpp](../librtsim/include/rtsim/energy_info_provider.hpp#L19-L26)。

其中默认实现是：

- `getSuspendReason(...)` 返回 `"unknown"`
- `clearSuspendReason(...)` 不做任何事

这意味着：**如果某个调度器没有自己实现 suspend reason 追踪，那么 trace 层无法知道这次 deschedule 到底是缺电还是抢占。**

现在 [json_trace.cpp](../librtsim/json_trace.cpp#L260-L276) 已改成：

- 明确 `insufficient_energy` → 输出 energy_insufficient
- 明确 `preemption` → 输出 higher_priority_task
- `unknown` → 默认按 `preemption` 输出

因此，后续若不补调度器侧原因记录，则：

- **事件时间不会变**
- **调度行为不会变**
- **能量数字不会变**
- **只会导致部分 trace 中的原因字段变错**

---

## 2. 本次修复必须遵守的边界

### 2.1 允许做的事

只允许做下面这类“纯标注”修改：

1. 在调度器头文件中增加 `_suspend_reasons`
2. 增加：
   - `setSuspendReason(...)`
   - `getSuspendReason(...) const override`
   - `clearSuspendReason(...) override`
3. 在 **调用 `_kernel->suspend(task)` 之前**，按真实原因先写入：
   - `"insufficient_energy"`
   - `"preemption"`
4. 让 `JSONTrace` 读取这些原因

### 2.2 明确禁止的事

以下都**不能改**：

- `getTaskN()` 的准入逻辑
- `performTickScheduling()` 的调度顺序
- 能量收集/扣减模型
- 批量调度人数 K
- ST 系列深度休眠 / 唤醒语义
- ALAP slack 计算与时序门控
- ASAP / ALAP / ST 的抢占策略
- `MRTKernel::suspend()` / `dispatch()` 行为
- trace 事件的时序、数量和触发条件

---

## 3. 统一修复模式

### 3.1 头文件统一增量模式

对还未支持原因追踪的 8 个调度器，在各自 `*.hpp` 中仿照 ST-Sync 增加：

```cpp
std::map<AbsRTTask *, std::string> _suspend_reasons;
void setSuspendReason(AbsRTTask *task, const std::string &reason);
std::string getSuspendReason(AbsRTTask *task) const override;
void clearSuspendReason(AbsRTTask *task) override;
```

参考实现位置：
- [gpfp_st_sync_scheduler.hpp:189-193](../librtsim/include/rtsim/scheduler/gpfp_st_sync_scheduler.hpp#L189-L193)

### 3.2 cpp 文件统一实现模式

在各自 `*.cpp` 中增加和 ST-Sync 等价的轻量实现：

```cpp
void XxxScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
    if (task) _suspend_reasons[task] = reason;
}

std::string XxxScheduler::getSuspendReason(AbsRTTask *task) const {
    if (!task) return "unknown";
    auto it = _suspend_reasons.find(task);
    if (it != _suspend_reasons.end()) return it->second;
    return "unknown";
}

void XxxScheduler::clearSuspendReason(AbsRTTask *task) {
    if (task) _suspend_reasons.erase(task);
}
```

参考实现位置：
- `librtsim/scheduler/gpfp_st_sync_scheduler.cpp` 中 `setSuspendReason/getSuspendReason/clearSuspendReason`

### 3.3 suspend 前打点原则

规则只有一条：

- **谁决定挂起，谁在 `_kernel->suspend(task)` 前写原因。**

映射原则：

- 因能量不足、能量耗尽、批量能量不足、运行时能量检查失败而挂起：
  - `setSuspendReason(task, "insufficient_energy")`
- 因高优任务到达、换入、腾核、替换低优运行任务而挂起：
  - `setSuspendReason(task, "preemption")`

### 3.4 不需要新增 trace 层推断

不建议再把 `JSONTrace` 改回“能量启发式猜测原因”。

原因：

1. 猜测会把真正的抢占误标成缺电
2. 同步批量/运行时中断场景下，trace 层缺少足够上下文
3. 正确归因应该由调度器在 suspend 决策点提供，而不是由 trace 事后猜

---

## 4. ST-Sync 当前状态

### 4.1 已具备完整参考结构

ST-Sync 已经有：

- 头文件中的 `_suspend_reasons` 和 override 接口：
  - [gpfp_st_sync_scheduler.hpp:189-193](../librtsim/include/rtsim/scheduler/gpfp_st_sync_scheduler.hpp#L189-L193)
- trace 读取后清理原因：
  - [json_trace.cpp:260-276](../librtsim/json_trace.cpp#L260-L276)
- 缺电批量挂起前设置原因：
  - [gpfp_st_sync_scheduler.cpp:1300-1302](../librtsim/scheduler/gpfp_st_sync_scheduler.cpp#L1300-L1302)
  - [gpfp_st_sync_scheduler.cpp:2704-2706](../librtsim/scheduler/gpfp_st_sync_scheduler.cpp#L2704-L2706)

### 4.2 当前判断

就“**只修复原因标注**”这个目标来说，**ST-Sync 可以视为已修好**。

后续若继续完善 ST-Sync，也应仅限于：

- 检查所有抢占型 `_kernel->suspend(lowest_priority_task)` 前是否都已标 `preemption`

这属于“补齐覆盖率”，不是当前主缺陷。

---

## 5. 九种算法逐个修复方案

---

## 5.1 ASAP-NonBlock

### 头文件
- [gpfp_asap_nonblock_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp)

当前状态：
- 已实现 EnergyInfoProvider 的能量接口
- **未实现 suspend reason 接口**

应新增：
- `_suspend_reasons`
- `setSuspendReason(...)`
- `getSuspendReason(...) const override`
- `clearSuspendReason(...) override`

### cpp 修复点
- [gpfp_asap_nonblock_scheduler.cpp:157](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L157)
  - 运行时能量检查事件触发挂起
  - 原因应标：`insufficient_energy`

- [gpfp_asap_nonblock_scheduler.cpp:533-536](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L533-L536)
  - `tasks_to_suspend` 批量挂起路径
  - 从日志语义看，这是能量不足导致
  - 原因应标：`insufficient_energy`

- [gpfp_asap_nonblock_scheduler.cpp:1001-1002](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L1001-L1002)
  - `running_task` 被挂起
  - 这里大概率是为了给更高优任务腾核
  - 原因应标：`preemption`

- [gpfp_asap_nonblock_scheduler.cpp:1805](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L1805)
  - 需要结合上下文核实
  - 若是清退低优先级运行任务让位，标 `preemption`
  - 若是能量不足收缩运行集，标 `insufficient_energy`

### 风险说明

ASAP-NonBlock 的重点不是逻辑改动，而是**区分“缺电跳过/挂起”与“高优抢占”**。不能把全部 `_kernel->suspend()` 都统一写成 `insufficient_energy`。

---

## 5.2 ASAP-Block

### 头文件
- [gpfp_asap_block_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_asap_block_scheduler.hpp)

当前状态：
- 仅有能量 getter
- **没有 suspend reason 追踪**

### cpp 修复点
- [gpfp_asap_block_scheduler.cpp:185-186](../librtsim/scheduler/gpfp_asap_block_scheduler.cpp#L185-L186)
  - 运行时能量检查挂起
  - 原因：`insufficient_energy`

- [gpfp_asap_block_scheduler.cpp:563-565](../librtsim/scheduler/gpfp_asap_block_scheduler.cpp#L563-L565)
  - `tasks_to_suspend` 批量挂起
  - 日志语义明确是缺电路径
  - 原因：`insufficient_energy`

- [gpfp_asap_block_scheduler.cpp:1012-1013](../librtsim/scheduler/gpfp_asap_block_scheduler.cpp#L1012-L1013)
  - `running_task` 被挂起
  - 从位置判断更像抢占/腾核
  - 原因：`preemption`

- [gpfp_asap_block_scheduler.cpp:1087](../librtsim/scheduler/gpfp_asap_block_scheduler.cpp#L1087)
  - 需结合上下文确认
  - 按决策动机填 `preemption` 或 `insufficient_energy`

### 特别注意

Block 变体经常在“遇到一个不能运行的更高优任务时整体阻断”，但这不代表所有 suspend 都是缺电；**为新任务腾核的 suspend 仍是 preemption。**

---

## 5.3 ASAP-Sync

### 头文件
- [gpfp_asap_sync_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_asap_sync_scheduler.hpp)

当前状态：
- 无 suspend reason 字段和 override

### cpp 修复点
- [gpfp_asap_sync_scheduler.cpp:173-174](../librtsim/scheduler/gpfp_asap_sync_scheduler.cpp#L173-L174)
  - 单任务运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_asap_sync_scheduler.cpp:197](../librtsim/scheduler/gpfp_asap_sync_scheduler.cpp#L197)
  - 第二个 energy check 相关挂起点
  - 原因：`insufficient_energy`

- [gpfp_asap_sync_scheduler.cpp:899-900](../librtsim/scheduler/gpfp_asap_sync_scheduler.cpp#L899-L900)
  - 批量挂起任务
  - 若该段对应整组/整批因能量不足撤销，则标 `insufficient_energy`

- [gpfp_asap_sync_scheduler.cpp:1440](../librtsim/scheduler/gpfp_asap_sync_scheduler.cpp#L1440)
  - `lowest_priority_task` 被挂起
  - 典型抢占腾核路径
  - 原因：`preemption`

- [gpfp_asap_sync_scheduler.cpp:1601](../librtsim/scheduler/gpfp_asap_sync_scheduler.cpp#L1601)
  - 再次挂起 `lowest_priority_task`
  - 原因：`preemption`

### 特别注意

Sync 变体最容易犯的错误是：
- 把“批量能量不足导致整组挂起”
- 和“高优任务导致移除最低优运行任务”

混成同一种 reason。这里必须严格区分。

---

## 5.4 ALAP-NonBlock

### 头文件
- [gpfp_alap_nonblock_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp)

当前状态：
- 无 suspend reason override

### cpp 修复点
- [gpfp_alap_nonblock_scheduler.cpp:157-158](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L157-L158)
  - 运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_alap_nonblock_scheduler.cpp:557-560](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L557-L560)
  - `tasks_to_suspend` 缺电挂起
  - 原因：`insufficient_energy`

- [gpfp_alap_nonblock_scheduler.cpp:1262](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L1262)
  - `worst_running` 被挂起
  - 这是典型“换出较差运行者”的抢占路径
  - 原因：`preemption`

- [gpfp_alap_nonblock_scheduler.cpp:2040](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L2040)
- [gpfp_alap_nonblock_scheduler.cpp:2283](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L2283)
  - 需要结合局部上下文区分
  - 原则同上：缺电标 `insufficient_energy`，换入更优任务标 `preemption`

### 特别注意

ALAP-NonBlock 有 slack/timing gate 语义，但**slack 导致的“延后调度”不等于 suspend reason**。只有真正调用 `_kernel->suspend()` 时才需要打点，而且原因应按当次 suspend 的直接动机填写。

---

## 5.5 ALAP-Block

### 头文件
- [gpfp_alap_block_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_alap_block_scheduler.hpp)

### cpp 修复点
- [gpfp_alap_block_scheduler.cpp:185-186](../librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L185-L186)
  - 运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_alap_block_scheduler.cpp:589-591](../librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L589-L591)
  - 批量缺电挂起
  - 原因：`insufficient_energy`

- [gpfp_alap_block_scheduler.cpp:1226](../librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L1226)
  - `worst_running` 被挂起
  - 原因：`preemption`

- [gpfp_alap_block_scheduler.cpp:1296](../librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L1296)
- [gpfp_alap_block_scheduler.cpp:2017](../librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L2017)
  - 需结合上下文确认

### 风险点

ALAP-Block 中“严格阻断”与“换出正在运行任务”可能共存。不要因为整体算法名叫 Block，就把所有 suspend 一律记成缺电。

---

## 5.6 ALAP-Sync

### 头文件
- [gpfp_alap_sync_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_alap_sync_scheduler.hpp)

### cpp 修复点
- [gpfp_alap_sync_scheduler.cpp:174-175](../librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L174-L175)
  - 运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_alap_sync_scheduler.cpp:1088-1089](../librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1088-L1089)
  - 批量 / 组内任务被挂起
  - 若上下文是整组能量不足，原因：`insufficient_energy`

- [gpfp_alap_sync_scheduler.cpp:1707](../librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1707)
  - `lowest_priority_task` 被挂起
  - 原因：`preemption`

- [gpfp_alap_sync_scheduler.cpp:1900](../librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1900)
  - `lowest_priority_task` 再次被挂起
  - 原因：`preemption`

- [gpfp_alap_sync_scheduler.cpp:2556](../librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L2556)
  - 需核实是缺电收缩还是抢占让位

### 特别注意

ALAP-Sync 和 ST-Sync 一样，通常同时存在：

1. 整组缺电挂起
2. 低优运行任务让位给高优任务

这两个原因必须分开，不然 trace 会继续出现“幽灵抢占”或“幽灵缺电”。

---

## 5.7 ST-NonBlock

### 头文件
- [gpfp_st_nonblock_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_st_nonblock_scheduler.hpp)

### cpp 修复点
- [gpfp_st_nonblock_scheduler.cpp:295-296](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L295-L296)
  - 运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_st_nonblock_scheduler.cpp:763-774](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L763-L774)
  - `tasks_to_suspend` 路径
  - 从日志看是缺电导致的挂起/跳过
  - 原因：`insufficient_energy`

- [gpfp_st_nonblock_scheduler.cpp:1568](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L1568)
  - `worst_running` 被挂起
  - 原因：`preemption`

- [gpfp_st_nonblock_scheduler.cpp:2242](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L2242)
- [gpfp_st_nonblock_scheduler.cpp:2494](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L2494)
  - 需结合局部上下文确认

### 特别注意

ST-NonBlock 已经有“缺电跳过但不上全局锁”的独特语义。补 reason 时只能在 suspend 前写标签，**不能顺手调整唤醒器、跳过策略或 charging lock。**

---

## 5.8 ST-Block

### 头文件
- [gpfp_st_block_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_st_block_scheduler.hpp)

当前状态：
- 仅有能量接口
- 没有 `_suspend_reasons`
- 这个调度器最近还在做能量不跌穿修复，但那是另一条线，不应和本方案混做

### cpp 修复点
- [gpfp_st_block_scheduler.cpp:224-225](../librtsim/scheduler/gpfp_st_block_scheduler.cpp#L224-L225)
  - 运行时能量不足挂起
  - 原因：`insufficient_energy`

- [gpfp_st_block_scheduler.cpp:776-778](../librtsim/scheduler/gpfp_st_block_scheduler.cpp#L776-L778)
  - `tasks_to_suspend` 缺电挂起
  - 原因：`insufficient_energy`

- [gpfp_st_block_scheduler.cpp:1486](../librtsim/scheduler/gpfp_st_block_scheduler.cpp#L1486)
  - `worst_running` 被挂起
  - 原因：`preemption`

- [gpfp_st_block_scheduler.cpp:1557](../librtsim/scheduler/gpfp_st_block_scheduler.cpp#L1557)
- [gpfp_st_block_scheduler.cpp:2244](../librtsim/scheduler/gpfp_st_block_scheduler.cpp#L2244)
  - 需结合上下文确认

### 特别注意

ST-Block 同时存在：

- 缺电进入深度休眠 / 深度充电
- 为更高优任务让位的运行时替换

补 reason 时，不要把深度充电状态机和 trace 标注改动耦合在一起。

---

## 5.9 ST-Sync

### 状态
- **已完成参考实现**

### 已覆盖关键点
- 头文件增加 `_suspend_reasons` 和接口 override
- cpp 中实现 `set/get/clear`
- 整组缺电挂起前写 `insufficient_energy`
- `JSONTrace` 已读取该标注

### 仍建议复查的点
- [gpfp_st_sync_scheduler.cpp:2126](../librtsim/scheduler/gpfp_st_sync_scheduler.cpp#L2126)
- [gpfp_st_sync_scheduler.cpp:2337](../librtsim/scheduler/gpfp_st_sync_scheduler.cpp#L2337)
- [gpfp_st_sync_scheduler.cpp:3185](../librtsim/scheduler/gpfp_st_sync_scheduler.cpp#L3185)

若这些路径是高优换入导致的让位挂起，应补：

```cpp
setSuspendReason(lowest_priority_task, "preemption");
```

如果这些点当前未影响用户关注场景，可放在“第二轮补齐覆盖率”处理。

---

## 6. 推荐实施顺序

### 第一阶段：先让 8 个未修调度器具备 reason 存储能力

对以下头文件统一补结构：

- `gpfp_asap_nonblock_scheduler.hpp`
- `gpfp_asap_block_scheduler.hpp`
- `gpfp_asap_sync_scheduler.hpp`
- `gpfp_alap_nonblock_scheduler.hpp`
- `gpfp_alap_block_scheduler.hpp`
- `gpfp_alap_sync_scheduler.hpp`
- `gpfp_st_nonblock_scheduler.hpp`
- `gpfp_st_block_scheduler.hpp`

### 第二阶段：先补所有“明显缺电 suspend”

优先补这些最确定的点：

- 运行时能量检查事件内的 `_kernel->suspend(...)`
- `tasks_to_suspend` / 整组挂起循环中的 `_kernel->suspend(...)`
- 日志已经写着“任务因能量不足被挂起”的路径

这是最安全的一批，因为几乎没有语义歧义。

### 第三阶段：再补所有“明显抢占 suspend”

重点补：

- `lowest_priority_task`
- `worst_running`
- `running_task`

这些名字本身已经很强地表明：它们通常是为了给更优任务腾核，不是缺电。

### 第四阶段：最后处理剩余模糊点

对少数单独的 `_kernel->suspend(task)`：

- 只读上下文
- 判断直接动机
- 填入正确 reason

如果直接动机仍不清晰，宁可先不改，也不要误标。

---

## 7. 关于 json_trace.cpp 的建议

### 当前建议

**保留现在的 `JSONTrace` 实现，不回退到能量启发式。**

原因：

1. ST-Sync 已经开始依赖“调度器显式提供真实原因”
2. 启发式会重新引入误判
3. 这次任务的正确方向本来就是“把原因修到调度器里”

### 但要接受的过渡期现象

在其余 8 个调度器补完前：

- 某些原本被启发式猜成 `insufficient_energy` 的 descheduled
- 现在会临时显示成 `preemption`

这属于**过渡期标签不完整**，不是调度行为错误。

---

## 8. 验证标准

每修完一个调度器，都只验证“原因是否正确”，不验证新的调度策略。

### 8.1 应保持不变的内容

以下结果必须完全不变或只允许浮点打印误差级别变化：

- scheduled / descheduled / end_instance 的时间
- arrival_time
- current_energy_mJ 的演化
- total_consumed_mJ / total_harvested_mJ
- 任务集合、CPU 占用、调度顺序
- deadline miss 数量

### 8.2 应变化的内容

应只变化：

- `descheduled.reason`
- `descheduled.preempted_by`

### 8.3 重点检查样例

对每个调度器至少验证两类场景：

1. **真实缺电挂起场景**
   - 期望：`reason = insufficient_energy`
2. **真实高优抢占场景**
   - 期望：`reason = preemption`

### 8.4 ST-Sync 额外结论

ST-Sync 当前验证重点已经基本满足：

- 不再依赖 trace 启发式猜原因
- 批量缺电挂起可被正确标成 `insufficient_energy`
- 剩余工作主要是补齐所有抢占型 suspend 的 `preemption` 标注覆盖率

---

## 9. 最小提交原则

如果后续开始真正动代码，建议每个调度器按以下粒度提交：

1. 头文件：增加 suspend reason 成员与 override
2. cpp：实现 `set/get/clear`
3. cpp：只在 suspend 前增加 `setSuspendReason(...)`

不要把以下内容混进同一次提交：

- 能量修复
- 深度充电修复
- Tick 逻辑重构
- 任务选择逻辑修改
- trace 格式新增字段

---

## 10. 总结

### 现状

- [json_trace.cpp](../librtsim/json_trace.cpp#L260-L276) 已转向“只信调度器显式原因”
- 9 个算法里，**只有 ST-Sync 已经具备显式 suspend reason 机制**
- 其余 8 个算法仍缺这层信息，因此 trace 原因会暂时不完整

### 最终建议

- **ST-Sync 视为已修好，可作为模板复制到其余 8 个调度器**
- 后续修复要严格限定在“原因标注”层
- 先补全部 `insufficient_energy` 路径，再补 `preemption` 路径
- 不要回退到 trace 层启发式猜测

### 一句话原则

> 不改调度，只改标签；
> 不让 trace 猜，让 scheduler 说真话。
