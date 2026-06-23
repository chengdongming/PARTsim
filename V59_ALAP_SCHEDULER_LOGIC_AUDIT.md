# V59 ALAP 三种调度算法逻辑核对总结

## 背景

本文档用于核对当前仓库中的三种 ALAP 调度算法实现，是否符合如下目标语义：

- **ALAP-Block**：严格 ALAP 门控 + 高优缺电后形成绝对阻塞墙，不允许低优任务旁路。
- **ALAP-NonBlock**：严格 ALAP 门控 + 高优缺电后允许有限度的贪心旁路，但旁路候选也必须满足自身 `Slack <= 0`。
- **ALAP-Sync**：多核同步成组准入；在准入阶段做组级供电核验；若组级能量不足则整组不上机；运行中不做违反物理直觉的“强制连坐踢出”。

本次核对是**语义级审计**，结论关注“当前代码是否符合理论定义”，而不是只看某个测试 case 是否通过。

---

## 总结结论

| 算法 | 结论 | 说明 |
|------|------|------|
| ALAP-Block | **基本符合** | 已实现严格 ALAP 门控、严格阻塞墙、禁止低优旁路 |
| ALAP-NonBlock | **大体符合，但有偏差** | 核心贪心旁路逻辑成立，但 arrival 阶段仍有能量门槛，且旁路 deadline 约束比目标语义更严格 |
| ALAP-Sync | **部分符合，不完全符合** | 具备 batch/sync 准入框架，但当前实现不是纯粹的原子组级验资，并且仍存在运行时主动挂起与 arrival 能量门槛 |

---

## 1. ALAP-Block 核对结果

### 1.1 符合点

#### A. 严格 ALAP 全局时序门控

当前实现会遍历 ready queue 与 running tasks，计算全局最小 Slack；当 `min_slack > 0` 时直接休眠，并设置精确唤醒闹钟。

参考代码：
- [gpfp_alap_block_scheduler.cpp:2214-2291](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L2214-L2291)

这与目标语义中的“只有当 Slack 归零、到达最迟启动时刻才允许申请上机执行”一致。

#### B. 运行中任务按优先级逐级续期

Block 当前会在 tick 边界对运行中任务按 RM 优先级排序，然后逐个检查是否能续期一个 tick 的能量。

参考代码：
- [gpfp_alap_block_scheduler.cpp:586-669](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L586-L669)

其中核心语义是：
- 高优先级任务先检查；
- 一旦更高优任务续不起，就触发阻塞壁垒；
- 后续低优任务不再有机会获得 CPU。

#### C. 高优缺电后形成绝对阻塞墙

在运行中任务续期逻辑中，一旦某个高优任务缺电：
- 设置 `_alap_blocking = true`
- 记录 `_blocking_task`
- 对该任务标记 `insufficient_energy`
- 对后续低优任务执行 `alap_blocking` 连坐挂起

参考代码：
- [gpfp_alap_block_scheduler.cpp:622-655](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L622-L655)

这与目标语义中的“阻塞墙（Blocking Barrier）”是吻合的。

#### D. 阻塞后禁止任何低优旁路

`getTaskN()` 中只要 `_alap_blocking` 已被置位，就直接拒绝调度任何后续任务。

参考代码：
- [gpfp_alap_block_scheduler.cpp:803-810](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L803-L810)

此外，当候选任务本身能量不足时，也会立即停止级联搜索，不会继续向后扫描更低优任务。

参考代码：
- [gpfp_alap_block_scheduler.cpp:943-951](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L943-L951)

这与“即使低优任务耗电更低、系统残余能量足够它跑，也绝对不能越过高优阻塞任务”一致。

#### E. 缺电不提前销毁，保留到 deadline miss 路径

任务到达时会加入 ready queue，而不是因为当前能量不足而直接丢弃实例。

参考代码：
- [gpfp_alap_block_scheduler.cpp:991-1004](librtsim/scheduler/gpfp_alap_block_scheduler.cpp#L991-L1004)

这与“死等到能量恢复或 deadline miss 再由底层统一处理”一致。

### 1.2 Block 结论

**ALAP-Block 当前实现基本符合目标定义。**

它已经具备：
- 严格的 ALAP 时间门控；
- 高优优先的逐级续期；
- 缺电后形成绝对阻塞墙；
- 禁止低优任务绕过阻塞墙偷跑。

---

## 2. ALAP-NonBlock 核对结果

### 2.1 符合点

#### A. 仍坚持严格 ALAP 门控

NonBlock 同样先做全局最小 Slack 门控：
- 若 `min_slack > 0`，则系统休眠；
- 到唤醒点再重新评估。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:2153-2227](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L2153-L2227)

同时，在候选任务选择时，对 `slack > 0` 的任务不会提前调度。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:934-954](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L934-L954)

#### B. 高优缺电时允许继续向后扫描

NonBlock 的核心差异在于：当高优运行任务或 ready 队首候选缺电时，不会像 Block 那样把整个队列锁死，而是尝试继续寻找可运行的后续任务。

运行中任务续期逻辑：
- 能量不足的任务被记录到 `tasks_to_suspend`
- 但不会 `break`
- 后续低优任务仍继续尝试续期

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:541-609](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L541-L609)

ready queue 的旁路逻辑：
- 当当前候选缺电时，调用 `tryOpportunisticBypass(...)`
- 继续向后扫描是否存在满足约束的低优候选

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:788-879](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L788-L879)
- [gpfp_alap_nonblock_scheduler.cpp:959-973](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L959-L973)

#### C. 旁路候选也必须满足 `Slack <= 0`

`tryOpportunisticBypass(...)` 中明确过滤 `next_slack > 0` 的候选。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:852-858](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L852-L858)

这与“NonBlock 不是提前偷跑，而是在坚守 ALAP 时序底线下做机会主义捡漏”一致。

#### D. 对更高优 `Slack > 0` 的任务加了绕过保护

当前实现还加入了一条更细的约束：
- 如果更高优先级任务虽然 `Slack > 0`，但当前剩余能量连它一个 tick 都带不起来，
- 则本轮不允许继续往后搜索更低优任务。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:938-947](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L938-L947)

这有助于避免“拿残余能量去非法绕过更紧迫的上层约束”。

### 2.2 与目标语义不完全一致的地方

#### A. 旁路 deadline 约束比理论定义更严格

目标定义写的是：
- 旁路候选的 Deadline **通常应早于或等于** 被阻塞任务的 Deadline。

而当前代码实际要求：
- `next_deadline >= blocked_deadline` 则直接跳过；
- 也就是旁路候选 Deadline **必须严格早于** 被阻塞任务。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:842-848](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L842-L848)

因此，当前实现比目标语义更保守。

#### B. arrival 路径存在能量门槛

`notify()` 中如果当前能量不足以支撑该任务 1ms 能耗，则直接返回，不会把任务加入 ready queue。

参考代码：
- [gpfp_alap_nonblock_scheduler.cpp:1006-1027](librtsim/scheduler/gpfp_alap_nonblock_scheduler.cpp#L1006-L1027)

这与目标语义不完全一致。按你给出的定义：
- 高优任务缺电时，应该是“保留在系统中等待”，
- 再决定是否允许低优旁路；
- 不应该在 arrival 阶段因为当前缺电而直接不入队。

### 2.3 NonBlock 结论

**ALAP-NonBlock 当前实现的核心旁路思想是成立的，但还不是完全等价于目标定义。**

最主要的两点偏差是：
1. 旁路的 deadline 约束比目标语义更严格；
2. arrival 路径仍然带有能量门槛，导致部分缺电实例无法进入 ready queue 等待。

---

## 3. ALAP-Sync 核对结果

### 3.1 符合点

#### A. 具备严格 ALAP 门控

Sync 会先做全局 Slack 门控：
- 若所有任务都还未到最迟启动时刻，则休眠并设置闹钟；
- 一旦存在 `slack <= 0` 的任务，则允许进入调度阶段。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:2080-2140](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L2080-L2140)

同时，在 batch 组装阶段，ready queue 中新增成员必须满足 `slack <= 0`。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:636-665](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L636-L665)

#### B. 具备同步组 / 批次准入视图

Sync 当前已经维护：
- `_current_batch_tasks`
- `_dispatch_selection_order`
- `_batch_scheduled_this_tick`

并通过 `getTaskN()` 返回稳定批次视图。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:1022-1061](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1022-L1061)

这说明它确实已经具备“同步组派发”的基本框架。

#### C. 对新增成员执行组级供电检查

当前实现会：
- 先保留已经续期成功的运行成员 `continued_running_tasks`
- 再从 ready queue 中补入新的 `slack <= 0` 任务
- 计算这些**新增成员**的总 1ms 能耗 `new_tasks_required_energy`
- 若当前能量不足，则整批新增失败，不允许本次 batch 建立

参考代码：
- [gpfp_alap_sync_scheduler.cpp:628-709](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L628-L709)

因此，Sync 当前的确有“组级准入检查”的语义。

### 3.2 与目标语义不一致的地方

#### A. 不是纯粹的原子性组级验资

目标定义中的 Sync 强调的是：
- 先选出 `K` 个满足 `Slack <= 0` 的任务；
- 然后对这 `K` 个任务整体计算下一个 1ms 的总耗电；
- 若总能量不足，则整个组一个都不上机。

但当前实现不是这样做的。它的顺序是：
1. 先对**正在运行的任务**逐个续期扣能量；
2. 再尝试为新增成员做组级检查。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:592-623](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L592-L623)
- [gpfp_alap_sync_scheduler.cpp:628-709](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L628-L709)

也就是说，当前 Sync 更接近：
- “旧成员先续期，新增成员再做同步准入”

而不是：
- “整个同步组作为一个不可分割实体做一次性验资”

#### B. 运行中仍有调度器主动挂起

目标定义强调：
- Sync 仅在“上机前”做强绑定核电；
- 运行中若电池耗尽，应由底层物理执行路径自然导致任务下机；
- 不应由调度器做违反物理直觉的“运行时连坐踢出”。

但当前代码中：
- 若运行中的任务续期失败，调度器会直接 `setSuspendReason(...); _kernel->suspend(task)`
- 若新增成员组级供电失败，还会把已经续期成功的 `continued_running_tasks` 主动挂起

参考代码：
- [gpfp_alap_sync_scheduler.cpp:611-622](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L611-L622)
- [gpfp_alap_sync_scheduler.cpp:689-705](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L689-L705)

因此，它与“运行中自然下机、不做运行时强制连坐干预”的目标定义并不完全一致。

#### C. arrival 路径仍然带有能量门槛

`notify()` 中如果当前能量不足，任务不会被加入 ready queue。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:1078-1110](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1078-L1110)

此外，`addTask()` 中如果 `_energy_depleted && _current_energy < epsilon`，还会直接拒绝新任务。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:1117-1127](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1117-L1127)

这与目标定义中的“队列中等待真实 deadline miss / 等待能量恢复”不一致。

### 3.3 Sync 结论

**ALAP-Sync 当前实现只部分符合目标定义。**

它现在已经具备：
- ALAP 门控；
- 稳定的 batch / dispatch 视图；
- 某种组级准入检查。

但它还不是目标定义中那种“纯同步成组准入型”实现，主要差异有三点：
1. 不是对整个同步组做原子的一次性验资；
2. 运行中仍存在调度器主动挂起；
3. arrival 阶段仍有能量门槛。

---

## 4. 总体结论

### 当前匹配度

- **ALAP-Block**：与目标定义**基本一致**。
- **ALAP-NonBlock**：与目标定义**大体一致，但仍有两处明显偏差**。
- **ALAP-Sync**：与目标定义**只部分一致，还没有完全收敛到纯同步组准入语义**。

### 如果后续要继续收敛，优先建议

#### 对 ALAP-NonBlock
1. 移除 `notify()` 中的 arrival 能量门槛，让缺电任务也能进入 ready queue；
2. 如果理论定义允许“deadline 相等也可旁路”，则把当前的严格 `<` 约束放宽到 `<=` 语义。

#### 对 ALAP-Sync
1. 把“运行中续期”与“新增成员准入”统一为真正的组级原子验资；
2. 尽量去掉运行时由调度器主动触发的连坐式 suspend；
3. 移除 `notify()` / `addTask()` 的 arrival 能量门槛，让实例先进入系统生命周期，再由准入逻辑决定是否上机。

---

## 5. 一句话结论

**当前代码里，Block 已基本符合你的理论定义；NonBlock 核心思想已对，但实现仍偏保守；Sync 已修到 same-config trace 与 Block 对齐，但从“理论调度语义”角度看，仍未完全等价于你给出的纯同步成组准入定义。**

---

## 6. ALAP-Sync 优化版 K 定义（保持当前每 1ms 能量检查）

### 6.1 推荐的 K 定义

如果继续优化 ALAP-Sync，但**保持当前“每 1ms tick 边界检查一次能量”**的实现框架不变，那么推荐把 `K` 明确定义为：

> **下一拍计划同时运行的整个同步组大小**

形式化地说：

```text
K = | continued_running_tasks ∪ newly_admitted_tasks |
```

其中：
- `continued_running_tasks`：本 tick 续期成功、下一拍仍将继续运行的任务；
- `newly_admitted_tasks`：本 tick 新通过 ALAP 门控与准入检查、准备在下一拍上机的任务；
- `K <= CPU核心总数`。

这意味着：
- `K` **不是**“本 tick 新准入任务数”；
- `K` **也不是** `min(ready_queue.size(), total_cpus)` 这种只看就绪队列的静态值；
- `K` 表示的是**完整同步组的人数**，也就是下一拍真正要一起占用处理器的总任务数。

### 6.2 这个定义与当前 ALAP 主路径的关系

当前 ALAP-Sync 主路径里，`performTickScheduling()` 已经比较接近这个定义：
- 先构造 `continued_running_tasks`
- 再把满足 `Slack <= 0` 的新任务补进 `sync_batch`
- 然后用 `sync_batch.size()` 作为本次同步组的实际大小

参考代码：
- [gpfp_alap_sync_scheduler.cpp:587-688](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L587-L688)

因此，从**主调度行为**上看，当前实现已经基本朝着“`K = 整个同步组大小`”这个方向收敛了。

### 6.3 保持不变的部分

本次推荐只调整 **K 的语义定义**，不改能量检查机制。

也就是说，仍然保持：
- 能量在 **每 1ms tick** 边界检查；
- 不额外引入“攒够 K 个任务总能量后再唤醒”的新事件机制；
- 继续由现有 tick 调度循环在每拍重新评估同步组。

换句话说，这次优化的重点是：
- **把 K 的含义定义清楚、统一清楚**；
- 而不是修改当前的能量恢复/唤醒机制。

### 6.4 如果按这个 K 定义继续清理代码，预计会改到哪些地方

如果后续要把代码与这一定义彻底对齐，改动范围主要集中在 **ALAP-Sync 本地实现**，不需要波及 Block / NonBlock / ASAP / JSONTrace / MRTKernel。

#### A. `calculateBatchSize()` 辅助函数需要改或删除

当前这个函数仍然返回：

```text
min(CPU核心总数, 就绪队列任务数)
```

参考代码：
- [gpfp_alap_sync_scheduler.cpp:373-385](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L373-L385)

这和推荐定义不一致，因为它：
- 没把继续运行的任务计入 K；
- 只是看 ready queue，而不是看完整同步组。

因此这里有两种处理方式：
1. **直接删除这个 helper**，避免它继续表达错误语义；
2. 或者把它改成基于 `sync_batch` / 完整同步组来计算 K。

#### B. `performTickScheduling()` 的日志与注释要统一成“整个同步组大小”

主路径本身已经接近推荐定义，但日志和变量语义还需要统一说明：
- `sync_batch.size()` 应被明确视为当前 K；
- `continued_running_tasks.size()` 与新增成员数只是 K 的组成部分，不应再被当成不同层面的批量定义。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:625-688](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L625-L688)
- [gpfp_alap_sync_scheduler.cpp:693-705](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L693-L705)

这部分大概率只需要：
- 改注释；
- 改日志字段；
- 让调试信息明确打印 `K = sync_batch.size()`。

#### C. `_current_batch_size` 的维护要更严格

当前 `_current_batch_size` 在 batch 建立时会被写入，但在某些后续路径上没有同步更新。

例如：
- 任务结束时，会从 `_current_batch_tasks` 中移除任务，但没有同步重写 `_current_batch_size`
  - [gpfp_alap_sync_scheduler.cpp:1891-1896](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1891-L1896)
- 清理过期任务时，也会从 `_current_batch_tasks` 中移除任务，但没有同步重写 `_current_batch_size`
  - [gpfp_alap_sync_scheduler.cpp:2016-2035](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L2016-L2035)

如果要把 `K` 作为对外可解释的明确概念，那么 `_current_batch_size` 应始终与 `_current_batch_tasks.size()` 保持一致。

#### D. 头文件中的接口注释要改成“整个同步组大小”

当前头文件里：
- `_current_batch_tasks`
- `_current_batch_size`
- `getCurrentBatchTasks()`
- `getCurrentBatchSize()`

这些接口还只是泛泛写成“当前批量任务 / 当前批量大小”。

参考位置：
- [gpfp_alap_sync_scheduler.hpp:181-186](librtsim/include/rtsim/scheduler/gpfp_alap_sync_scheduler.hpp#L181-L186)
- [gpfp_alap_sync_scheduler.hpp:344-347](librtsim/include/rtsim/scheduler/gpfp_alap_sync_scheduler.hpp#L344-L347)

如果采用优化版 K 定义，建议把这些注释改成：
- “当前同步组任务集合”
- “当前同步组大小 K”

这样接口语义会更稳定，也方便后续分析工具引用。

#### E. `getTaskN()` 主体逻辑预计不用大改

`getTaskN()` 当前是基于 `_current_batch_tasks` 和 `_dispatch_selection_order` 返回稳定同步组视图。

参考代码：
- [gpfp_alap_sync_scheduler.cpp:1022-1061](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp#L1022-L1061)

因为 `_current_batch_tasks` 本来就承载“整个同步组”的快照，所以：
- 如果只是统一 K 的定义，
- `getTaskN()` 大概率**不需要重写核心逻辑**。

需要做的更多是：
- 确保它的注释、日志、调用语义都和“整个同步组大小 K”一致。

### 6.5 影响范围评估

如果只做这次 K 定义统一，而**不改能量机制**，那么改动范围总体是**中小型**：

#### 必改
- [gpfp_alap_sync_scheduler.cpp](librtsim/scheduler/gpfp_alap_sync_scheduler.cpp)
  - `calculateBatchSize()`
  - `performTickScheduling()` 的注释/日志
  - `_current_batch_size` 的维护点
- [gpfp_alap_sync_scheduler.hpp](librtsim/include/rtsim/scheduler/gpfp_alap_sync_scheduler.hpp)
  - batch/K 相关字段与 getter 注释
- [V59_ALAP_SCHEDULER_LOGIC_AUDIT.md](V59_ALAP_SCHEDULER_LOGIC_AUDIT.md)
  - 文档定义补充

#### 大概率不用改
- `getTaskN()` 的核心返回逻辑
- Block / NonBlock / ASAP 调度器
- `MRTKernel`
- `JSONTrace`
- 能量恢复与唤醒事件机制

### 6.6 一句话建议

如果你准备继续优化 ALAP-Sync，**最合适的做法不是去改“每 1ms 检查能量”这一层，而是先把 K 的语义统一成“下一拍完整同步组大小”，并把 `calculateBatchSize()`、`_current_batch_size`、接口注释和日志全部收敛到这个定义上。**
