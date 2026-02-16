# ST 扩展调度算法详细设计文档 (Tick-based)

**文档版本**: v1.0
**适用架构**: Tick-based (1ms 步长) 实时调度仿真器
**基准代码**: 复制现有的 `ALAP-Block`、`ALAP-NonBlock`、`ALAP-Sync` 代码进行修改。
[cite_start]**设计目标**: 实现原论文中的 $PFP_{ST}$ (Slack Time) 策略 ，并结合 Block / NonBlock / Sync 的能量分发机制，形成三种新的 ST 扩展算法。

---

## 1. 核心理论：ST 与 ALAP 的本质区别

* **ALAP (As Late As Possible)**：
  * **逻辑**：无论有没有电，只要 $Slack > 0$，就**强制休眠**。
  * **表现**：任务永远紧贴着 Deadline 完成（如您之前跑出的甘特图所示）。
* [cite_start]**ST (Slack Time)** [cite: 303]：
  * [cite_start]**逻辑**：采用 **ASAP (尽可能早)** 的默认行为。只要有电，任务一到达就立刻执行 [cite: 303][cite_start]。只有当**“想跑但这会儿没电”**时，才利用 Slack 进行休眠充电 [cite: 304]。
  * **表现**：如果能量一直充足，系统表现得和 TIE/TGF 完全一样；只有遇到能量危机时，才会触发休眠保护高优任务。

---

## 2. 调度逻辑框架重构

在修改代码时，请将原来 ALAP 的“两阶段框架”重构为 **“ASAP 尝试 + ST 充电拦截”** 的嵌套框架。

### 通用逻辑流 (每 1ms 执行):
1. 取出最高优先级的任务 $\tau_{top}$ (或 Batch)。
2. **能量检查 (ASAP 尝试)**: 当前电量是否足以执行 1ms？
   * **YES (电够)** -> **立即调度执行**。结束本 Tick。
   * **NO (电不够)** -> **触发 ST 机制**。
     1. 计算该任务的 $Slack$。
     2. **判定**:
        * [cite_start]**若 $Slack > 0$**: 系统为了保住这个高优任务，**强制休眠 (Force IDLE)** 本 Tick 进行充电 [cite: 304]。不调度它，也不调度任何人。
        * **若 $Slack \le 0$**: 真正到了生死存亡的时刻，且依然没电。**进入扩展策略阶段 (Block / NonBlock / Sync)**。

---

## 3. 三种算法的详细修改逻辑

### 3.1 算法一: ST-Block (基于 ALAP-Block 修改)

* **新算法标识**: `gpfp_st_block`
* **修改逻辑**:
  1. 取消原来在 Tick 开头计算全局 `min_slack` 并直接拦截的逻辑。
  2. 在遍历 `_ready_queue` 获取任务 $\tau_k$ 时，先执行能量检查：
     * `if (_current_energy >= unit_energy)` -> 调度 $\tau_k$。
     * `else` (没电时) -> **插入 ST 逻辑**：
       * 调用 `calculateSlackForTask(task)`。
       * **IF ($Slack > 0$)**: 
         * 记录休眠统计 (`_stats.total_st_forced_idle++`)。
         * **`return nullptr;`** (强制休眠本 Tick 进行充电，等待电量恢复)。
       * **ELSE ($Slack \le 0$)**: 
         * **触发 BLOCK 策略**：该任务死线已到且没电，系统卡死。
         * 记录警告日志：`"⚠️ [ST-Block] 能量不足且 Slack<=0，停止级联 (BLOCK)"`
         * **`return nullptr;`** (不清理任务，保持阻塞)。

### 3.2 算法二: ST-NonBlock (基于 ALAP-NonBlock 修改)

* **新算法标识**: `gpfp_st_nonblock`
* **修改逻辑**:
  1. 取消 Tick 开头的全局 Slack 拦截。
  2. 遍历 `_ready_queue` 检查能量：
     * `if (_current_energy >= unit_energy)` -> 调度 $\tau_k$。
     * `else` (没电时) -> **插入 ST 逻辑**：
       * 调用 `calculateSlackForTask(task)`。
       * **IF ($Slack > 0$)**:
         * [cite_start]**重点**：既然最高优先级任务还有 Slack，说明它愿意等系统充电。系统必须**强制休眠**来为它充电 [cite: 304]。
         * **`return nullptr;`** (退出循环，本 Tick 休眠，不要去搜低优任务)。
       * **ELSE ($Slack \le 0$)**:
         * **触发 DROP & SKIP 策略**：高优任务没电且等不起了。
         * **动作 1**：主动丢弃 `removeFromReadyQueue(task)`。
         * **动作 2**：`continue;` (贪婪向后搜索队列，找下一个能耗小且电够的低优任务执行)。

### 3.3 算法三: ST-Sync (基于 ALAP-Sync 修改)

* **新算法标识**: `gpfp_st_sync`
* **修改逻辑**:
  1. 组建好 Batch (前 K 个任务)，计算 $P_{batch}$。
  2. **批量能量检查**:
     * `if (_current_energy >= batch_energy)` -> 全员发射！
     * `else` (没电时) -> **插入 ST 逻辑**：
       * 计算该 Batch 的最小 Slack ($S_{batch}$)。
       * **IF ($S_{batch} > 0$)**:
         * Batch 整体愿意等待充电。
         * 本 Tick **强制休眠**。
       * **ELSE ($S_{batch} \le 0$)**:
         * **触发 BATCH BLOCK 策略**：整批任务到了死线且没电。
         * 本 Tick 强制空闲，不调度任何人。等待后续物理超时将死去的任务清理掉。

---

## 4. 关键表现对比 (预期结果)

当您完成修改并在相同的对抗性环境 (U=0.85, 太阳能微弱) 下运行时，您将看到与 ALAP 截然不同的现象：

1. **甘特图外观**: 
   * **ALAP**: 所有色块紧贴右侧红线 (Deadline)。
   * **ST**: 大部分色块会**紧贴左侧** (任务刚到达就执行)。只有当遇到 `Task_Assassin_Hungry` 把电抽干时，后续任务的色块才会被推迟，出现充电带来的空白间隙。
2. **性能表现**:
   * `ST-NonBlock` 的接受率通常会**略高于** `ALAP-NonBlock`。因为 ASAP 的默认行为让它在电量充裕时提早消化了任务，避免了 ALAP 那样把所有任务都堆积到最后一刻导致的并发冲突。
   * 原论文中也提到，ST 的失败率通常低于 ALAP，这在您的仿真中将得到完美重现。

## 5. 实施步骤清单

1. 复制 `gpfp_alap_*_scheduler.cpp / .hpp` 为 `gpfp_st_*_scheduler.cpp / .hpp`。
2. 更改所有的类名，并在 `scheduler_factory.cpp` 中注册 (`gpfp_st_block`, `gpfp_st_nonblock`, `gpfp_st_sync`)。
3. 删除 `checkALAPTimingGate()` 函数。
4. 在 `getTaskN` (或 Batch 分发) 中，找到能量不足的 `else` 分支，在其中注入 `calculateSlack` 和判断逻辑。
