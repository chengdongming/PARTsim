# 三种 NonBlock 调度器生命周期重构方案

> 日期：2026-03-06
> 目标：给出 **正确、可实现、最小但足够彻底** 的方案，统一解决三种 NonBlock 调度器的生命周期问题：
> - `ST-NonBlock`
> - `ALAP-NonBlock`
> - `ASAP-NonBlock`
>
> 同时明确：本文 **只先详细覆盖 3 种 NonBlock**。其余 6 种算法（ST/ALAP/ASAP 的 Block 与 Sync 变体）放在文末作为后续处理范围，不在本文中展开重构细节。

---

## 1. 结论先行

### 1.1 ST-NonBlock

**有问题，而且是最严重的结构性问题。**

ST-NonBlock 当前的 1ms 抖动，不是单个 if 判断或黑名单漏清理能彻底解决的，而是因为“休眠 / 唤醒 / 重新调度”的生命周期被拆散到了多个位置共同修改：

- tick 边界续期检查
- `STNonBlockWakeEvent::doit()`
- `getTaskN()` 的贪心搜索分支
- `_skipped_tasks`
- `_skip_wake_events`
- `_pending_wake_task`
- ready queue / running map / kernel suspend-insert 流程

结果就是：

1. 任务被 suspend 后，仍可能通过 ready queue 重新参与选择；
2. wake event 不只是“通知可唤醒”，还直接触发 `dispatch()`；
3. `Slack<=0` 时 wake event 会每 1ms 重试；
4. `_pending_wake_task` 又反过来影响下一 tick 的能量预留；
5. 同一个任务在“睡眠态 / 就绪态 / 待唤醒态 / 运行态”之间没有唯一真相来源。

因此 ST-NonBlock **必须做生命周期重构**，不能继续靠补黑名单修。

关键代码位置：

- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:72-195](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L72-L195)
- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:724-812](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L724-L812)
- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:973-1233](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L973-L1233)

### 1.2 ALAP-NonBlock

**也有生命周期问题，但不是 ST 那种“wake-event 风暴型 1ms 抖动”。**

ALAP-NonBlock 没有 ST 的 per-task wake event 体系，因此**不会复制 ST 那种 “唤醒事件 -> dispatch -> 再挂起 -> 再唤醒事件” 的 1ms 事件风暴**。

但它仍然存在较弱版本的 NonBlock 生命周期问题：

- 运行中任务在 tick 续期时能量不足，会被 `_kernel->suspend(task)`；
- kernel 会把任务重新插回 ready queue；
- 系统之后继续依赖 `getTaskN()` + `_energy_depleted` + Slack/贪心过滤来避免再次上核；
- 所以它仍有“**挂起后仍停留在 ready 生命周期**”的结构性缺陷，只是没有额外的 wake event 放大器。

因此 ALAP-NonBlock **也需要重构**，但改动量会明显小于 ST。

关键代码位置：

- [librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp:495-555](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L495-L555)
- [librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp:711-915](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L711-L915)

### 1.3 ASAP-NonBlock

**同样需要重构，但根因更接近“挂起后仍留在 ready 生命周期 + `getTaskN()` 兼任状态机”。**

ASAP-NonBlock 没有 ST 的 wake event，所以**不会出现 ST 那种 wake-event 驱动的 1ms 连续震荡**；但从实现看，它依然把“谁可运行”分散在多个容器和多个阶段现场推理：

- tick 中检查运行任务续期能量，不足就 `_kernel->suspend(task)`；
- suspend 后仍依赖 ready queue 重新参与调度流程；
- `getTaskN()` 一边选任务，一边做能量门控、贪心后继搜索、跳过被抢占任务、维护 `_counted_tasks_in_dispatch` / `_newly_dispatched_this_tick`；
- `_energy_depleted`、`_energy_deducted_tasks`、`_counted_tasks_in_dispatch`、`_newly_dispatched_this_tick` 共同决定一个任务本 tick 到底算“可调度 / 已调度 / 已扣能量 / 仍在运行”，但没有显式 blocked state 作为统一事实来源。

这说明 ASAP-NonBlock 虽然没有 ST 的“事件风暴”，但仍然属于典型的 **NonBlock 生命周期状态分裂**。

关键代码位置：

- [librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp:428-636](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L428-L636)
- [librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp:683-859](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L683-L859)
- [librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp:107-155](../librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp#L107-L155)

**结论：三种 NonBlock 都需要重构。区别只在于：**

- **ST-NonBlock：问题最重，必须优先处理；**
- **ALAP-NonBlock：没有 wake-event 风暴，但仍有 blocked/ready 生命周期混用；**
- **ASAP-NonBlock：没有 wake-event 风暴，但同样把 blocked 语义留在 ready + 现场判断里。**

---

## 2. 本次测试与代码证据

### 2.1 已有实测证据

- ST 配置： [traces/config_st_nonblock_test.yml](../traces/config_st_nonblock_test.yml)
- ALAP 配置： [test_alap_nonblock_config.yml](../test_alap_nonblock_config.yml)
- 任务集： [traces/taskset_v130_5task.yml](../traces/taskset_v130_5task.yml)
- 仿真时长：800ms

### 2.2 产物

- ST trace： [traces/st_nonblock_compare_800.json](../traces/st_nonblock_compare_800.json)
- ALAP trace： [traces/alap_nonblock_compare_800.json](../traces/alap_nonblock_compare_800.json)

### 2.3 已确认现象

#### ST-NonBlock

- 事件数：241
- 完成任务数：13
- 明显存在 1ms `scheduled/descheduled` 震荡
- 日志显示多个 wake event 在相邻毫秒连续触发，并不断重设唤醒定时器

#### ALAP-NonBlock

- 事件数：149
- 完成任务数：19
- 存在能量耗尽导致的挂起 / kill / miss
- **没有**观测到 ST 那种 wake-event 驱动的 1ms 连续震荡

#### ASAP-NonBlock

对 ASAP-NonBlock，本轮主要依据代码结构判断是否需要重构。结论是：

- 它没有 ST 那种 wake-event 放大器；
- 但它在 tick、ready queue、运行态、初始能量扣减、续期能量扣减之间存在明显状态耦合；
- 所以它同样属于需要重构的 NonBlock，而不是“可以不动”的那一类。

---

## 3. 根因分析

## 3.1 ST-NonBlock 的真正根因

### 根因 A：wake event 不是“状态转换通知”，而是在直接驱动调度

当前 [gpfp_st_nonblock_scheduler.cpp:72-145](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L72-L145) 中，`STNonBlockWakeEvent::doit()` 会：

- 判断能量 / slack
- 修改 `_skipped_tasks`
- 修改 `_pending_wake_task`
- 直接调用 `_kernel->dispatch()`

这意味着：

**wake event 已经不是纯事件，而是半个调度器。**

调度决策被分裂到了：

- tick 调度器
- wake event
- `getTaskN()`

这是 ST 抖动最核心的问题。

### 根因 B：sleeping task 没有真正离开 ready 生命周期

当前实现大量依赖“任务虽然还在 ready 相关结构中，但通过 `_skipped_tasks` 把它逻辑跳过”。

这会导致：

- ready queue 仍然保留它；
- kernel suspend 还会重新 insert；
- `getTaskN()` 还得不断判断“这个任务是不是虽然 ready 但其实不能选”；
- wake event 又去删 `_skipped_tasks`，让它重新可见。

本质上这是用 **黑名单补丁** 模拟状态机，而不是显式状态机。

### 根因 C：`Slack<=0` 被做成“每 1ms 重试一次”

在 [gpfp_st_nonblock_scheduler.cpp:158-193](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L158-L193) 和 [gpfp_st_nonblock_scheduler.cpp:1175-1233](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L1175-L1233) 中，`Slack<=0` 会把 wake time 设置为 `current+1`，于是：

- 到点唤醒；
- 能量仍不足；
- 再设一个 `+1ms`；
- 再次触发；
- 周而复始。

如果 wake event 同时还会触发 dispatch，就会把“deadline 紧急”放大成“事件风暴”。

### 根因 D：`_pending_wake_task` 把“唤醒意图”耦合进了 tick 能量记账

在 [gpfp_st_nonblock_scheduler.cpp:705-747](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L705-L747) 中，tick 续期能量检查会根据 `_pending_wake_task` 预留能量。

这让一个 wake event 的副作用跨越到下一 tick 的运行态能量检查，进一步加重状态耦合。

---

## 3.2 ALAP-NonBlock 的根因

ALAP-NonBlock 的问题比 ST 轻，但方向相同。

### 根因 A：挂起后任务仍通过 ready queue 生命周期回流

在 [gpfp_alap_nonblock_scheduler.cpp:551-555](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L551-L555)，任务能量不足时直接 `_kernel->suspend(task)`。

从日志和代码路径可见，suspend 后 kernel 会把任务重新插回 ready queue。于是 ALAP 仍在做：

- “任务其实被能量阻塞了，但结构上仍是 ready”；
- 然后靠 `_energy_depleted` / Slack / 贪心跳过把它挡回去。

### 根因 B：`getTaskN()` 承担了过多职责

在 [gpfp_alap_nonblock_scheduler.cpp:711-915](../librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L711-L915)，`getTaskN()` 同时在做：

- 过期过滤
- Slack 门控
- 能量判断
- 贪心后继搜索
- 对“被抢占的任务”做特殊跳过

这意味着“是否可运行”的判定仍是**临时计算出来的结果**，而不是来自显式状态。

---

## 3.3 ASAP-NonBlock 的根因

ASAP-NonBlock 的问题没有 ST 那么剧烈，但也不是“小修小补”能彻底解决。

### 根因 A：运行中任务能量不足后，被 suspend，但没有显式 blocked 生命周期

在 [gpfp_asap_nonblock_scheduler.cpp:485-539](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L485-L539)，tick 中会遍历运行任务：

- 若能量足够，则扣除续期能量；
- 若能量不足，则将任务加入 `tasks_to_suspend`，随后 `_kernel->suspend(task)`。

问题在于：

- 这里发生了真实的 `RUNNING -> 不能继续执行`；
- 但调度器并没有把它迁移到一个显式 `ENERGY_BLOCKED` 集合；
- 后续是否还能被选，仍然要靠 ready queue 当前内容 + `getTaskN()` 里的现场判断共同推导。

这和 ALAP-NonBlock 的结构性问题本质相同。

### 根因 B：`getTaskN()` 同时承担“选择器 + 状态过滤器 + 能量记账协调器”

在 [gpfp_asap_nonblock_scheduler.cpp:683-859](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L683-L859)，`getTaskN()` 同时在做：

- `_energy_depleted` 整体门控；
- 识别任务是否正在运行；
- 对目标任务做即时能量判断；
- 若目标任务能量不足，则向后贪心搜索更便宜的任务；
- 跳过已经调度过的任务；
- 跳过被抢占但仍有剩余执行时间的任务；
- 维护 `_counted_tasks_in_dispatch`；
- 维护 `_newly_dispatched_this_tick`。

这说明 `getTaskN()` 不是一个纯选择函数，而是在现场拼接生命周期和能量语义。

### 根因 C：一个任务的“本 tick 身份”被多个集合共同描述，但没有 single source of truth

从头文件 [gpfp_asap_nonblock_scheduler.hpp:107-155](../librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp#L107-L155) 可以看出，ASAP-NonBlock 当前状态分散在：

- `_ready_queue`
- `_running_tasks`
- `_counted_tasks_in_dispatch`
- `_energy_deducted_tasks`
- `_newly_dispatched_this_tick`
- `_energy_accounts`
- `_energy_depleted`

这些集合本身各有用途，但在当前实现里，它们共同参与了“任务是否可继续执行 / 是否可再次调度 / 是否已经扣过初始能量 / 是否应跳过续期扣除”的判定。

问题不在于集合数量多，而在于：

> 它们没有围绕一个明确的 blocked/ready 状态机组织起来。

### 根因 D：ASAP 的理论是“尽早 + 可回填”，但当前实现把 blocked 语义留在了 ready 选择阶段临时推理

ASAP-NonBlock 理论上允许：

- 高优先级或更靠前任务如果当前能量不够，可以让后面的可行任务先执行；
- 这是 NonBlock 的合理语义。

但工程上应当是：

- 先在 tick 仲裁点算出“当前哪些任务是 READY、哪些是 BLOCKED”；
- 然后 `getTaskN()` 仅在 READY 集合中做 ASAP 的回填选择。

而不是像现在这样：

- 把 blocked 任务留在 ready queue 里；
- 然后由 `getTaskN()` 在选择过程中一边试探、一边跳过、一边修正记账。

这就是 ASAP-NonBlock 也必须进入统一 NonBlock 重构范围的原因。

---

## 4. 最优方案：统一 NonBlock 生命周期，但三者保留各自理论策略

这里的“最优”指：

1. **不改变算法理论语义**：
   - ST 仍然允许高优任务个体等待并在时机到来时优先；
   - ALAP 仍然只允许 `Slack<=0` 的任务上核并做贪心回填；
   - ASAP 仍然允许在当前可行任务中尽早/贪心级联调度。
2. **去掉当前造成抖动、回流、状态分裂的工程结构**；
3. **尽量复用 NonBlock 公共生命周期框架**；
4. **不把 Block/Sync 的全局锁或批调度机制强行套到 NonBlock 上**。

### 核心原则

#### 原则 1：调度决策只能在 tick 边界发生

唯一允许决定“谁运行 / 谁继续 / 谁休眠 / 谁恢复可调度”的地方，应当是：

- `performTickScheduling()`

对于 ST，这意味着 **wake event 不能直接 dispatch**。

#### 原则 2：sleeping / blocked 必须是显式状态，不是 ready+过滤器

任务一旦因能量不足被阻塞，必须进入显式 blocked state：

- 不再参与 ready queue 选择；
- 不再依赖 `getTaskN()` 内部的“跳过 if”模拟睡眠；
- 恢复资格只改变状态，不直接调度。

#### 原则 3：`getTaskN()` 必须纯化

`getTaskN()` 只做：

- 从 **已可调度候选集** 中选第 n 个任务。

它不应该再兼任：

- 生命周期迁移
- wake event 创建/删除
- blocked 恢复
- 复杂副作用式能量协调

#### 原则 4：能量记账与生命周期迁移要分层

- 生命周期：任务是否 READY / RUNNING / BLOCKED，由 tick 仲裁维护；
- 能量记账：任务是否已扣初始能量、是否应扣续期能量，由专门记账结构维护；
- 记账结构不能反过来决定 blocked 生命周期。

#### 原则 5：保留各算法自己的候选规则，但候选集必须先被“状态机净化”

- ST：在 READY 集合上执行 ST 规则；
- ALAP：在 READY 集合上执行 `Slack<=0` + 回填规则；
- ASAP：在 READY 集合上执行尽早/贪心级联选择。

算法差异应该体现在“**如何从 READY 集合里选**”，而不是“**谁还在假 ready、谁其实 blocked**”。

---

## 5. 建议的显式状态机

建议为 NonBlock 家族引入统一的任务能量生命周期状态：

```text
READY
RUNNING
ENERGY_BLOCKED
WAKE_PENDING
DONE / KILLED / MISSED
```

### 状态含义

- `READY`：可被调度
- `RUNNING`：当前在 CPU 上执行
- `ENERGY_BLOCKED`：因能量不足被挂起，不参与 ready 选择
- `WAKE_PENDING`：ST 专用，唤醒时刻已到，但尚未到 tick 仲裁点
- `DONE / KILLED / MISSED`：终止态，必须清理全部附属状态

### 合法迁移

- `READY -> RUNNING`
- `RUNNING -> ENERGY_BLOCKED`
- `ENERGY_BLOCKED -> WAKE_PENDING`（仅 ST 需要）
- `ENERGY_BLOCKED -> READY`（ALAP / ASAP 在 tick 判断恢复资格时直接走这条）
- `WAKE_PENDING -> READY`
- `RUNNING / READY / ENERGY_BLOCKED / WAKE_PENDING -> DONE / KILLED / MISSED`

### 非法迁移

- `ENERGY_BLOCKED -> RUNNING`（不能绕过 tick）
- `WAKE_PENDING -> RUNNING`（不能由 wake event 直接 dispatch）
- `RUNNING -> READY` 但未经过 scheduler 仲裁

### 三种 NonBlock 的映射

#### ST-NonBlock

- 使用 `ENERGY_BLOCKED`
- 使用 `WAKE_PENDING`
- wake event 只负责 `ENERGY_BLOCKED -> WAKE_PENDING`

#### ALAP-NonBlock

- 使用 `ENERGY_BLOCKED`
- 一般不需要 `WAKE_PENDING`
- tick 中判断 `Slack<=0` 且能量足够时，`ENERGY_BLOCKED -> READY`

#### ASAP-NonBlock

- 使用 `ENERGY_BLOCKED`
- 不需要 `WAKE_PENDING`
- tick 中判断能量足够且任务重新可参与 ASAP 候选时，`ENERGY_BLOCKED -> READY`

---

## 6. 具体重构方案

## 6.1 ST-NonBlock：必须做的改动

### 改动 1：移除 wake event 中的直接调度副作用

目标文件：

- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:72-195](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L72-L195)

做法：

- 删掉 / 禁用 wake event 中的 `_kernel->dispatch()`；
- 删掉 wake event 中对 `_pending_wake_task` 的设置；
- wake event 只做：
  - 验证任务还活着；
  - 若仍处于 `ENERGY_BLOCKED`，则转为 `WAKE_PENDING`；
  - 记录一个 `needs_reschedule_check` 标志即可。

### 改动 2：去掉 `_pending_wake_task/_pending_wake_energy` 预留机制

目标文件：

- [librtsim/include/rtsim/scheduler/gpfp_st_nonblock_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_st_nonblock_scheduler.hpp)
- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:705-747](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L705-L747)

原因：

- 它把唤醒意图硬耦合进续期能量记账；
- 是事件风暴和 tick 能量判断之间的桥；
- 去掉后，tick 只看当前真实 energy 和当前真实候选集。

### 改动 3：把 `_skipped_tasks` 升级为真正的 blocked state 容器

建议改成：

- `_energy_blocked_tasks`
- `_wake_pending_tasks`

语义拆开后：

- blocked：不能选
- wake pending：下一个 tick 可以重新进入候选

### 改动 4：`getTaskN()` 彻底纯化

目标文件：

- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:973-1233](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L973-L1233)

必须删除的职责：

- 在能量不足分支中创建 wake event；
- 在能量恢复分支中删除 wake event；
- 用 `_skipped_tasks` 做生命周期迁移。

保留职责：

- 从 `READY` 集合里，按 ST 规则选择第 n 个；
- 若能量不足，仅返回“本轮不能调度该任务”，**不产生副作用**。

### 改动 5：所有 sleep/wake 转移集中到 `performTickScheduling()`

目标文件：

- [librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp:575-926](../librtsim/scheduler/gpfp_st_nonblock_scheduler.cpp#L575-L926)

统一顺序应为：

1. 收集能量
2. 处理完成 / kill / miss 清理
3. 处理已到时的 `WAKE_PENDING -> READY`
4. 检查运行中任务续期，不能续期则 `RUNNING -> ENERGY_BLOCKED`
5. 为刚 blocked 的任务注册 / 更新 wake timer
6. 基于当前 `READY` 集合做调度
7. 记录本 tick 状态

### 改动 6：终止路径必须统一清理所有状态

目标文件：

- `onTaskEnd()`
- `cleanupExpiredTasks()`
- kill / miss 相关回调

要保证终止时统一清理：

- ready queue
- running map
- blocked / wake pending state
- wake event map
- 任何能量记账残留

---

## 6.2 ALAP-NonBlock：建议做的改动

ALAP 不需要复制 ST 的 wake event 机制，因此改动应更小。

### 改动 1：引入显式 `ENERGY_BLOCKED`

当运行中任务在 tick 续期时能量不足，不要只做 `_kernel->suspend(task)` 后就指望 ready queue + `_energy_depleted` 自己兜住。

应当：

- `RUNNING -> ENERGY_BLOCKED`
- 直到后续 tick 判断它重新满足 ALAP 约束（`Slack<=0` 且能量足够）后，才回到 `READY`

### 改动 2：把“恢复资格”逻辑前移到 tick，而不是留给 `getTaskN()` 现场推理

ALAP 的 `getTaskN()` 里现在混合了：

- Slack 过滤
- 能量过滤
- 贪心查找
- 被抢占残留过滤

建议改成：

- `performTickScheduling()` 先计算本 tick 可回到 READY 的任务；
- `getTaskN()` 只面对 READY 集合。

### 改动 3：保留 ALAP 理论，不引入 ST 的 per-task wake event

ALAP 的理论核心是 `Slack<=0` 才执行，因此它根本不需要 ST 的“专属唤醒定时器 + force execute + pending reserve”这一套。

### 改动 4：把被抢占 / 被能量阻塞的特判从 `getTaskN()` 挪回状态机

如果一个任务因为能量原因本 tick 不应参与 ALAP 候选，它应当在 tick 前置阶段就被排除，而不是在 `getTaskN()` 内部通过多个 if 临时跳过。

---

## 6.3 ASAP-NonBlock：建议做的改动

ASAP-NonBlock 不需要 ST 的 wake event，也不应照搬 ALAP 的 slack 恢复条件；但它仍然必须完成同一类生命周期收敛。

### 改动 1：为 suspend 后的任务建立显式 `ENERGY_BLOCKED`

目标文件：

- [librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp:485-539](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L485-L539)
- [librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp:107-155](../librtsim/include/rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp#L107-L155)

当前问题是：

- tick 中检查运行任务能量不足后直接 `_kernel->suspend(task)`；
- 但没有一个显式集合表明“这个任务现在因能量而 blocked”。

建议新增：

- `_energy_blocked_tasks`

并在 tick 中明确执行：

- `RUNNING -> ENERGY_BLOCKED`
- 同时把它从可参与选择的 READY 候选中移除

### 改动 2：把 blocked -> READY 的恢复判断放到 tick，而不是留给 `getTaskN()` 现场试探

ASAP-NonBlock 的理论不是“到 slack 时刻再上”，而是“当前能调就尽快调，调不了就允许后继回填”。

因此对 blocked task，tick 中应当先做：

1. 重新评估当前可用能量；
2. 判断该任务是否重新具有候选资格；
3. 若有资格，则 `ENERGY_BLOCKED -> READY`；
4. 然后再进入 ASAP 的候选排序 / 级联选择。

这样 `getTaskN()` 就不需要一边看到 ready queue 里的 blocked task，一边现场猜它是不是该跳过。

### 改动 3：纯化 `getTaskN()`，保留 ASAP 的贪心，但去掉生命周期副作用

目标文件：

- [librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp:683-859](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L683-L859)

应保留的语义：

- 如果第 n 个 READY 任务当前单位能耗太高，可以继续看后面的 READY 任务；
- 这是 ASAP-NonBlock 的合理“可行任务回填”语义。

应删除或前移的职责：

- 用 ready queue 中的假 ready 任务来做 blocked 语义；
- 在 `getTaskN()` 里兼任 blocked / preempted / energy bookkeeping 的混合判断；
- 通过 `_counted_tasks_in_dispatch` / `_newly_dispatched_this_tick` 来间接表达生命周期。

重构后，`getTaskN()` 应只做：

- 从已净化的 READY 候选集合中；
- 按 ASAP 非阻塞规则选择第 n 个可行任务；
- 只产生最小必要的“本轮准备 dispatch 哪些任务”的记账副作用。

### 改动 4：把“生命周期状态”与“能量记账状态”彻底拆开

ASAP-NonBlock 当前最容易越修越乱的点，就是把以下两类语义缠在一起：

- 生命周期：任务能否被选、是否 blocked、是否已恢复；
- 记账：任务是否已扣过初始能量、是否要跳过本 tick 的续期扣除。

建议明确分层：

#### 生命周期层

- `_ready_queue`
- `_running_tasks`
- `_energy_blocked_tasks`

#### 记账层

- `_energy_deducted_tasks`
- `_newly_dispatched_this_tick`
- `_energy_accounts`

这样 `_energy_deducted_tasks` 再也不应该承担“这个任务是不是还不该被调度”的职责。

### 改动 5：清理 `getTaskN()` 中已明显失控的补丁式逻辑

当前 [gpfp_asap_nonblock_scheduler.cpp:683-706](../librtsim/scheduler/gpfp_asap_nonblock_scheduler.cpp#L683-L706) 已经出现重复 `_energy_depleted` 判断和明显的补丁残留。这本身就是信号：

- 这个函数已经承载了过多临时修复；
- 应通过状态机收敛，而不是继续在里面叠加 if。

这一步不是“代码洁癖”，而是为了降低后续继续修坏生命周期的风险。

### 改动 6：终止路径统一清理 blocked 与记账残留

目标文件：

- `onTaskEnd()`
- `removeTask()`
- miss / kill 清理路径

要保证终止时清理：

- ready queue
- running map
- `_energy_blocked_tasks`
- `_energy_deducted_tasks`
- `_newly_dispatched_this_tick`
- `_energy_accounts`

否则 ASAP-NonBlock 很容易出现“任务结束了，但旧记账状态还影响下一实例”的隐性 bug。

---

## 7. 为什么这是“最优方案”

## 7.1 它修的是根，不是症状

如果继续在现有代码上补：

- 多加几个 blacklist
- 多拦几个 self-dispatch
- 多清几处 skipped set
- 多打一层 debounce
- 多修几处 `_energy_depleted` 判断

只能减少部分现象，**不能保证不会再次出现新的抖动回路或 ready 回流问题**。

真正的问题是：

> 同一个任务的生命周期被多个函数、多个集合同时控制。

本方案把控制权收敛到 tick 仲裁点，属于根因修复。

## 7.2 它不改变 ST / ALAP / ASAP 的理论语义

- ST 仍然是 NonBlock：高优不强行上全局锁，低优仍可捡漏；
- ALAP 仍然是 ALAP：只有 slack 到点的任务才可执行；
- ASAP 仍然是 ASAP：可行任务尽早执行，必要时允许后继回填；
- 改变的是工程实现的**状态管理方式**，不是算法定义。

## 7.3 它能统一解释三类现象

- 为什么 ST 有 1ms 连续抖动：因为多了 wake event 放大器；
- 为什么 ALAP 没有同样风暴，但也有挂起后残留问题：因为它仍把 blocked task 留在 ready 生命周期里；
- 为什么 ASAP 虽没有 wake event，但仍要重构：因为它同样把 blocked 与可调度资格留在 ready + `getTaskN()` 的现场推理里。

---

## 8. 推荐实施顺序

### Phase 1：先重构 ST-NonBlock

1. 引入显式 blocked / wake-pending state
2. 删除 wake event 中的 `_kernel->dispatch()`
3. 删除 `_pending_wake_task/_pending_wake_energy`
4. 把 `getTaskN()` 纯化
5. 把所有 sleep / wake 迁移集中到 tick
6. 回归测试 ST trace

### Phase 2：收敛 ALAP-NonBlock

1. 引入显式 blocked state
2. 把 blocked -> READY 恢复逻辑前移到 tick
3. 精简 `getTaskN()` 的副作用和特判
4. 保持无 wake event 设计
5. 回归测试 ALAP trace

### Phase 3：收敛 ASAP-NonBlock

1. 引入显式 blocked state
2. 把 blocked -> READY 恢复逻辑前移到 tick
3. 保留 ASAP 贪心回填，但只在 READY 候选集上执行
4. 将生命周期状态和能量记账状态彻底拆层
5. 清理 `getTaskN()` 中补丁式副作用逻辑
6. 回归测试 ASAP-NonBlock trace

### Phase 4：再考虑抽取 NonBlock 共性工具（可选）

如果前三阶段稳定，再考虑抽一层共用辅助逻辑：

- blocked state 容器
- 终止清理工具
- ready 恢复工具
- tick 中统一生命周期钩子

但**不要一开始就做抽象基类大重构**，否则风险过高。

---

## 9. 验收标准

### ST-NonBlock 验收

必须满足：

1. 不再出现同一任务连续 `scheduled/descheduled/scheduled/descheduled` 的 1ms 交替震荡；
2. wake event 不再直接触发 dispatch；
3. `getTaskN()` 不再创建 / 删除 wake event；
4. 被 blocked 的任务不会继续以 ready task 身份参与候选选择；
5. kill / dline miss / end_instance 后无残留 blocked / wake state。

### ALAP-NonBlock 验收

必须满足：

1. 能量不足后任务进入显式 blocked 状态；
2. blocked task 不在 ready 候选中反复被现场跳过；
3. `getTaskN()` 只做选择，不再扮演状态机；
4. trace 中 deschedule 仍可能存在，但不会演化为 ST 那种 1ms 连续风暴。

### ASAP-NonBlock 验收

必须满足：

1. 运行中任务因能量不足被 suspend 后，进入显式 blocked 状态；
2. blocked task 不再继续以 ready task 身份留在候选流里等待 `getTaskN()` 现场跳过；
3. `getTaskN()` 保留 ASAP 贪心回填语义，但不再兼任 blocked 生命周期迁移；
4. 生命周期状态与能量记账状态解耦；
5. 终止后无残留 `_energy_blocked_tasks` / `_energy_deducted_tasks` / `_newly_dispatched_this_tick` / `_energy_accounts` 污染下一实例。

---

## 10. 其余 6 种算法的处理边界

本文暂时**不详细展开**以下 6 种算法：

- `ST-Block`
- `ST-Sync`
- `ALAP-Block`
- `ALAP-Sync`
- `ASAP-Block`
- `ASAP-Sync`

当前判断是：

- 它们**不是这轮“必须立刻做生命周期大重构”的主目标**；
- 主要大问题集中在 **3 个 NonBlock**；
- Block / Sync 变体后续仍应做一轮单独审查，重点检查：
  - 是否存在 suspend 后 ready 回流；
  - 是否存在 event / batch / tick 多点共同控制生命周期；
  - 是否存在终止清理残留；
  - 是否存在能量记账与候选资格耦合过深。

也就是说，**当前可以先把 3 个 NonBlock 方案做对、做稳，再单独处理剩余 6 个算法。**

---

## 11. 最终判断

**最终判断如下：**

- **ST-NonBlock：有严重结构性问题，必须优先重构生命周期；**
- **ALAP-NonBlock：也有 NonBlock 生命周期设计缺陷，需要重构，但不需要 ST 的 wake-event 机制；**
- **ASAP-NonBlock：同样存在 blocked/ready 生命周期混用和 `getTaskN()` 职责过载，也需要重构；**
- **因此这轮真正需要详细重构方案的是 3 个 NonBlock，而不是全部 9 个算法一起大手术；**
- **最优正确方案不是继续补黑名单，而是：**
  - 以 `performTickScheduling()` 为唯一调度仲裁点；
  - 引入显式 blocked / wake-pending 状态；
  - 把 ST 的 wake event 降级为“资格恢复通知”；
  - 纯化 `getTaskN()`；
  - 把生命周期状态与能量记账状态拆开；
  - 在 3 个 NonBlock 稳定后，再处理剩余 6 个算法。

这是我基于现有代码结构、ST/ALAP trace、以及 ASAP 三个实现的仔细对比后认为**正确、可实现、且风险最低**的方案。