# ASAP-BLOCK 新 B4 统一扩展实验方案
## 统一任务族、九算法公平比较与 ASAP-BLOCK 条件优势验证
### v5.2：图形形式、单位与离散收能边界最终冻结版

## 0. 文档状态

本方案只讨论新版 B4，不处理 B1、B2、B3。

本版本在 v4 基础上完成以下关键修订：

1. 统一功率、时间和能量单位；
2. 区分“结果可判定作业”和“horizon 内实际释放作业”；
3. 明确 \(\rho_E=1\) 负对照与 \(\rho_E=2\) 主实验共用电池和逐单位时间收能轨迹；
4. 冻结机制事件和机会分母；
5. 明确 NONBLOCK 绕过机会以多核优先级扫描过程中“第一个因剩余能量不足而不可支付的候选”为基准；
6. 明确 CPU-only 门禁只是有限 horizon 过滤，不是永久可调度性证明；
7. 使用按利用率分层的基础任务集聚类 bootstrap；
8. 修正 \(q_0\) 的量纲定义；
9. 冻结三阶段 offered-harvest 的离散单位时间边界和可用时刻；
10. 保留必要设计，删除与核心研究问题关系较弱的额外场景和参数。

本方案可作为实现与 Pilot 的冻结合同。只有实现、单元测试、非干扰审计和中立 Pilot 门禁全部通过后，才能启动 formal。

---

# 1. 实验动机

旧扩展实验 A 已完成普通随机任务集上的九算法比较。其结果表明：

- ASAP-BLOCK、ASAP-NONBLOCK 和 ASAP-SYNC 的 acceptance 曲线完全或近乎完全重合；
- ST 系列与 ASAP 系列高度接近；
- ALAP 系列内部重合；
- 调整电池容量、初始能量和整体收能倍率，仍未稳定产生 BLOCK、NONBLOCK 和 SYNC 的差异。

这说明旧实验主要改变了“总共有多少能量”，但没有稳定控制：

\[
\text{任务优先级}
\times
\text{单位执行能耗}
\times
\text{截止期紧度}
\times
\text{收能时间结构}.
\]

因此，新 B4 使用一个统一的优先级关键型共享能量任务族：

> 4 个高优先级关键任务与 6 个低优先级后台任务共享 4 个处理器和一个储能设备。

九种算法始终运行在完全相同的任务集、释放序列、供能轨迹和电池条件下。旧实验 A 作为普通随机负对照保留，不重跑。

---

# 2. 研究问题

## RQ1：完整九算法在统一任务族中的综合表现是否不同

比较：

\[
\{\mathrm{ASAP},\mathrm{ALAP},\mathrm{ST}\}
\times
\{\mathrm{BLOCK},\mathrm{NONBLOCK},\mathrm{SYNC}\}.
\]

主要观察：

- 严格全任务集通过率 WholePass；
- 高优先级前缀通过率 HPPass；
- 九算法在预注册负载—供能网格上的平均表现。

## RQ2：固定 ASAP 后，BLOCK 相对 NONBLOCK 和 SYNC 的优势位于哪里

比较：

- ASAP-BLOCK；
- ASAP-NONBLOCK；
- ASAP-SYNC。

## RQ3：固定 BLOCK 后，ASAP 相对 ALAP 和 ST 的优势位于哪里

比较：

- ASAP-BLOCK；
- ALAP-BLOCK；
- ST-BLOCK。

## RQ4：ASAP-BLOCK 相对四个直接对照的总体效应有多大

四个直接比较为：

1. ASAP-BLOCK vs ASAP-NONBLOCK；
2. ASAP-BLOCK vs ASAP-SYNC；
3. ASAP-BLOCK vs ALAP-BLOCK；
4. ASAP-BLOCK vs ST-BLOCK。

## RQ5：ASAP-BLOCK 为什么领先，以及代价是什么

同时报告：

- 高优先级保护；
- 低优先级漏期；
- 总体完成率；
- 绕过、批次拒绝、主动推迟和 slack 充能等待；
- 电池空、满和收能截断。

---

# 3. 结论边界

本实验不试图证明：

> ASAP-BLOCK 在所有任务集、所有供能条件和所有指标上全局最优。

实验最多支持：

> 在统一的优先级关键型共享能量任务族中，ASAP-BLOCK 在临界供能区域获得更高的高优先级可调度能力，并在部分区域将该优势转化为严格全任务集成功率提升。

若 formal 只复现 HPPass 或 Top-M JMR 优势，而 WholePass 没有明显提高，则结论必须限定为“高优先级保护优势”。

若九算法仍高度重合，不得继续无边界调参。

---

# 4. 平台和任务模型

## 4.1 基础平台

- 处理器数：\(M=4\)；
- 每任务集任务数：\(n=10\)；
- 全局固定优先级调度；
- RM 优先级；
- 可抢占、可迁移；
- 单一共享储能；
- 离散单位时间：

\[
\Delta t=1\ \mathrm{ms}=10^{-3}\ \mathrm{s};
\]

- formal horizon：

\[
H=30{,}000\ \mathrm{ms};
\]

- 技术 timeout：300 s；
- 单次重试 timeout：600 s；
- 九种算法对同一任务集完全配对。

## 4.2 利用率

归一化利用率定义为：

\[
U_{\mathrm{norm}}
=
\frac{1}{M}
\sum_{i=1}^{n}\frac{C_i}{T_i}.
\]

正式主网格：

\[
U_{\mathrm{norm}}
\in
\{0.2,0.3,0.4,0.5,0.6\}.
\]

任务利用率通过 UUniFast-Discard 生成，并限制：

\[
0.01
\le
\frac{C_i}{T_i}
\le
0.45.
\]

## 4.3 周期、WCET 与 RM 次序

- 周期：

\[
T_i\in[40,200]\ \mathrm{ms};
\]

- WCET 使用补偿取整；
- 实际总利用率误差不超过 0.01；
- RM 顺序按 \((T_i,\mathrm{task\_id})\) 升序确定；
- RM 前 4 个任务定义为 Top-4；
- 其余 6 个任务定义为 Bottom-6。

## 4.4 约束截止期

所有任务满足：

\[
1\le C_i\le D_i\le T_i.
\]

Top-4：

\[
D_i/T_i\in[0.50,0.70].
\]

Bottom-6：

\[
D_i/T_i\in[0.85,1.00].
\]

若整数取整后不满足 \(C_i\le D_i\le T_i\)，则重新生成整个基础任务集。

## 4.5 异步释放

每个任务具有固定初始偏移：

\[
O_i\in\{0,1,\ldots,T_i-1\}.
\]

作业释放时刻：

\[
r_{i,j}=O_i+jT_i,
\qquad j=0,1,2,\ldots
\]

偏移必须：

- 由任务集 seed 确定性生成；
- 写入任务集语义哈希；
- 九算法完全复用；
- 不根据算法或结果调整。

## 4.6 CPU-only 有限 horizon 门禁

每个基础任务集先在以下参考配置中运行：

- 相同 GFP-RM 优先级；
- 相同释放偏移；
- 不受能量限制；
- 相同 \(H=30{,}000\) ms。

只有该参考运行中全部可判定作业零漏期的任务集才进入 B4。

该门禁只是排除在本实验 horizon 内已经由纯 CPU 竞争导致失败的任务集，不构成永久可调度性或理论可调度性证明。必须报告生成尝试数、接受数和淘汰比例。

---

# 5. 计算负载与任务能耗解耦

## 5.1 固定基础计算负载

所有任务使用相同基础 workload 和相同频率：

```text
base_workload = hash
frequency = 9000
```

具体配置身份写入语义哈希。

这样，任务的计算需求由 \(C_i\) 决定，任务间的能耗差异只由独立能量因子决定。

## 5.2 统一能量单位

令 \(p_0\) 为基础 workload 在固定频率下的功率，单位为瓦特：

\[
[p_0]=\mathrm{W}=\mathrm{J/s}.
\]

定义基础 workload 每毫秒执行所消耗的能量：

\[
q_0
\triangleq
p_0\times10^{-3}\ \mathrm{J/ms}.
\]

因此：

\[
[q_0]=\mathrm{J/ms}.
\]

一个 WCET 为 \(C_i\) ms、能量因子为 \(e_i\) 的作业，其名义 WCET 能量为：

\[
E_i^{\mathrm{job}}
=
C_i q_0 e_i
=
C_i p_0 e_i\times10^{-3}\ \mathrm{J}.
\]

另有单个仿真单位时间对应的基础能量：

\[
\varepsilon_0
=
q_0\Delta t
=
p_0\times10^{-3}\ \mathrm{J},
\]

其中 \(\Delta t=1\ \mathrm{ms}\)。实现中的每单位时间能量扣除应与 \(\varepsilon_0 e_i\) 一致。

后续所有能量公式统一使用 \(q_0\)，不得直接把 \(C_i p_0\) 当作焦耳。

## 5.3 独立任务能量因子

每个任务增加：

```text
task_energy_factor
```

有效功率：

\[
p_i^{\mathrm{eff}}
=
p_0e_i.
\]

该字段只改变能量消耗，不改变：

- \(C_i\)；
- \(T_i\)；
- \(D_i\)；
- \(O_i\)；
- workload 执行速度；
- 频率。

## 5.4 单位一致性审计

正式实现前必须增加测试：

1. 固定一个 \(C=1\) ms 的任务；
2. 令 \(e=1\)；
3. 模拟器记录的执行能量必须等于 \(q_0\)；
4. 对任意整数 \(C\)，记录能量必须等于 \(Cq_0\)，仅允许预先冻结的浮点容差；
5. Python 生成器、C++ 调度器、日志和 analyzer 必须使用同一能量单位。

---

# 6. 作业集合与能量归一化

结果判定与物理能量归一化使用不同作业集合。

## 6.1 结果可判定作业集合

\[
\mathcal J_H^{\mathrm{obs}}
=
\left\{
J_{i,j}
\mid
r_{i,j}+D_i\le H
\right\}.
\]

WholePass、HPPass、JMR 和完成率的 deadline 判定只使用该集合。

## 6.2 horizon 内释放作业集合

\[
\mathcal J_H^{\mathrm{rel}}
=
\left\{
J_{i,j}
\mid
r_{i,j}<H
\right\}.
\]

这些作业都可能在 \(H\) 前执行并消耗能量，因此名义物理需求和供能比例使用该集合。

这样既保留严格的 horizon censoring，又不漏计在 horizon 尾部释放但截止期超过 \(H\) 的作业能量。

## 6.3 Top-4 与 Bottom-6 基础名义能量

未施加优先级能量因子时：

\[
W_H
=
\sum_{\substack{
J_{i,j}\in\mathcal J_H^{\mathrm{rel}}\\
i\in\mathrm{Top4}
}}
C_iq_0,
\]

\[
W_L
=
\sum_{\substack{
J_{i,j}\in\mathcal J_H^{\mathrm{rel}}\\
i\in\mathrm{Bottom6}
}}
C_iq_0.
\]

\(W_H\) 和 \(W_L\) 的单位均为焦耳。

---

# 7. 优先级—能耗耦合

## 7.1 耦合强度

定义：

\[
\rho_E
=
\frac{\text{Top-4 单位执行能耗}}
{\text{Bottom-6 单位执行能耗}}.
\]

主实验：

\[
\rho_E=2.
\]

中性负对照：

\[
\rho_E=1.
\]

不增加额外反向场景，以避免偏离“高优先级关键任务更耗能”的目标应用模型。

## 7.2 严格保持总名义能量需求不变

设 Top-4 能量因子为 \(a\)，Bottom-6 能量因子为 \(b\)，要求：

\[
\frac{a}{b}=\rho_E,
\]

\[
aW_H+bW_L=W_H+W_L.
\]

因此：

\[
b
=
\frac{W_H+W_L}
{\rho_EW_H+W_L},
\]

\[
a=\rho_Eb.
\]

最终：

- Top-4：\(e_i=a\)；
- Bottom-6：\(e_i=b\)。

因此 \(\rho_E=1\) 与 \(\rho_E=2\) 具有相同的 horizon 总名义能量需求：

\[
E_{\mathrm{dem}}^{\mathrm{nom}}(H)
=
aW_H+bW_L
=
W_H+W_L.
\]

它们只改变能量需求在优先级之间的分布。

---

# 8. 电池与收能轨迹

## 8.1 参考突发能量

为消除负对照歧义，电池和收能轨迹统一以主实验 \(\rho_E=2\) 为参考。

先计算 \(\rho_E=2\) 下的 Top-4 因子 \(a_{\mathrm{ref}}\)，再定义：

\[
E_{\mathrm{burst}}^{\mathrm{ref}}
=
\sum_{i\in\mathrm{Top4}}
C_iq_0a_{\mathrm{ref}}.
\]

## 8.2 跨 \(\rho_E\) 冻结的电池参数

对于同一基础任务集，无论 \(\rho_E=1\) 还是 \(\rho_E=2\)，均使用：

\[
E_0
=
E_{\mathrm{burst}}^{\mathrm{ref}},
\]

\[
E_{\max}
=
2E_{\mathrm{burst}}^{\mathrm{ref}}.
\]

因此负对照不会因为重新计算电池容量或初始电量而改变系统环境。

## 8.3 能量充足度

定义：

\[
\lambda_E
=
\frac{
E_0+
E_{\mathrm{harvest}}^{\mathrm{offered}}(H)
}{
E_{\mathrm{dem}}^{\mathrm{nom}}(H)
}.
\]

正式主网格：

\[
\lambda_E
\in
\{0.70,0.85,1.00,1.15\}.
\]

使用 offered harvest，而不是扣除 clipping 后的实际入电池能量。实际入电池能量是调度结果，不能反向用于参数定义。

## 8.4 统一三阶段 offered-harvest 轨迹

### 连续时间定义

基础无量纲轨迹：

\[
h_0(t)=
\begin{cases}
1.0, & 0\le t<5\ \mathrm{s},\\
0.2, & 5\le t<15\ \mathrm{s},\\
1.0, & 15\le t<30\ \mathrm{s}.
\end{cases}
\]

其积分为：

\[
\int_0^Hh_0(t)\,dt=22\ \mathrm{s}.
\]

缩放因子：

\[
\alpha
=
\frac{
\lambda_EE_{\mathrm{dem}}^{\mathrm{nom}}(H)-E_0
}{
22\ \mathrm{s}
}.
\]

因此：

\[
[\alpha]=\mathrm{W},
\]

且 offered-harvest power 为：

\[
h(t)=\alpha h_0(t).
\]

必须检查：

\[
\alpha\ge0.
\]

若不满足，任务集违反生成合同并重新生成，不得截断为 0。

### 离散单位时间实现合同

令离散索引：

\[
k\in\{0,1,\ldots,29{,}999\},
\]

其中索引 \(k\) 对应物理区间：

\[
[k,k+1)\ \mathrm{ms}.
\]

定义无量纲离散轨迹：

\[
g[k]=
\begin{cases}
1.0, & 0\le k<5000,\\
0.2, & 5000\le k<15000,\\
1.0, & 15000\le k<30000.
\end{cases}
\]

第 \(k\) 个物理区间产生的 offered-harvest energy 为：

\[
\Delta E_{\mathrm{harvest}}[k]
=
\alpha g[k]\times10^{-3}\ \mathrm{J}.
\]

该能量在物理区间 \([k,k+1)\) 结束后、时刻 \(k+1\) 的调度决策之前进入电池。时刻 \(0\) 的首次调度决策只使用初始能量 \(E_0\)，不预先获得第一个单位时间的收能。

因此，完整 horizon 内严格包含 30,000 个 offered-harvest 增量，并满足：

\[
\sum_{k=0}^{29999}
\Delta E_{\mathrm{harvest}}[k]
=
22\alpha.
\]

Python 生成器、C++ 仿真器、日志和 analyzer 必须使用完全相同的边界规则；该离散 offered-harvest trace 及其哈希必须写入实验身份。

## 8.5 负对照的逐单位时间冻结规则

对同一基础任务集和同一 \(\lambda_E\)：

- \(\rho_E=1\) 与 \(\rho_E=2\) 使用相同 \(E_0\)；
- 使用相同 \(E_{\max}\)；
- 使用完全相同的 \(\alpha\)；
- 使用逐单位时间完全相同的 offered-harvest trace；
- offered-harvest trace hash 必须一致；
- 唯一变化是每个任务的 \(e_i\)。

由于两种 \(\rho_E\) 的 \(E_{\mathrm{dem}}^{\mathrm{nom}}(H)\) 严格相同，上述冻结规则与 \(\lambda_E\) 定义一致。

## 8.6 能量审计指标

必须记录：

- offered harvested energy；
- actual harvested energy；
- clipped harvested energy；
- consumed energy；
- battery-empty fraction；
- battery-full fraction；
- clipping ratio；
- minimum battery energy。

Pilot 不事后删除 clipping 较高的任务集。

---

# 9. 机制事件与机会分母

所有机制统计在“调度决策时刻”记录。一次调度决策时刻指调度器针对当前 ready/running/energy 状态进行一次正式 dispatch 选择；日志使用 \((t,\mathrm{decision\_sequence})\) 唯一标识，避免同一仿真时刻的多次调用被混淆。

下列事件每个调度决策时刻最多计 1 次。

## 9.1 NONBLOCK 绕过

### 优先级扫描状态

在每个调度决策时刻，NONBLOCK 按正式 RM 顺序从高到低扫描 ready 候选。设：

- \(\mathcal S\) 为当前扫描位置之前已经选入本次 dispatch 集合的更高优先级作业；
- \(m_{\mathrm{free}}\) 为当前仍可用于新 dispatch 的处理器位置数；
- \(E_{\mathrm{res}}\) 为扣除 \(\mathcal S\) 的预留能量后剩余的可用能量：

\[
E_{\mathrm{res}}
=
E(t)
-
\sum_{J\in\mathcal S} e_J^{\mathrm{tick}}.
\]

只有在 \(m_{\mathrm{free}}>0\) 时，后续候选才具有被 dispatch 的可能。

### 绕过机会

当且仅当：

1. 当前仍存在至少一个可用于新 dispatch 的处理器位置；
2. 按正式 RM 顺序扫描时，遇到第一个在当前剩余能量 \(E_{\mathrm{res}}\) 下不可支付的候选 \(J^\star\)；
3. 在 \(J^\star\) 之后，至少存在一个更低优先级 ready 候选 \(J^\ell\)，其单位时间能量需求能够由同一剩余能量支付：

\[
e_{J^\star}^{\mathrm{tick}}
>
E_{\mathrm{res}},
\qquad
e_{J^\ell}^{\mathrm{tick}}
\le
E_{\mathrm{res}}.
\]

则记：

```text
bypass_opportunity_count += 1
```

这里的 \(J^\star\) 不要求是整个 ready 队列中排名第一的作业；它是扣除已选择高优先级前缀的处理器位置和预留能量后，扫描过程中遇到的第一个不可支付候选。

无论 \(J^\star\) 之后存在几个可支付的低优先级候选，同一调度决策时刻最多只计一次绕过机会。

### 实际绕过

在上述绕过机会中，若调度器：

1. 跳过 \(J^\star\)；
2. 实际 dispatch 至少一个位于 \(J^\star\) 之后、优先级更低且可由 \(E_{\mathrm{res}}\) 支付的候选；
3. \(J^\star\) 在该次选择结束后仍处于 ready 且未被 dispatch；

则记：

```text
actual_bypass_count += 1
```

定义：

\[
\mathrm{BypassRate}
=
\frac{
\mathrm{actual\_bypass\_count}
}{
\mathrm{bypass\_opportunity\_count}
}.
\]

分母为 0 时报告 NA，不报告 0。

## 9.2 SYNC 批次拒绝

### 批次评估机会

当且仅当：

1. 存在至少一个可用于新 dispatch 的处理器位置；
2. SYNC 按正式语义构造出非空 canonical candidate batch。

记：

```text
sync_batch_evaluation_count += 1
```

### 批次拒绝

若该 canonical batch 整体不可支付，且因此没有按 SYNC 原子语义启动该批次，则：

```text
sync_batch_reject_count += 1
```

定义：

\[
\mathrm{SyncRejectRate}
=
\frac{
\mathrm{sync\_batch\_reject\_count}
}{
\mathrm{sync\_batch\_evaluation\_count}
}.
\]

## 9.3 ALAP 正松弛推迟

### 推迟机会

当且仅当：

1. 存在可用于新 dispatch 的处理器位置；
2. 当前最高优先级 ready 候选按能量合同可支付；
3. 该作业剩余 slack 为正；
4. 除 ALAP 时机规则外不存在阻止它 dispatch 的其他原因。

记：

```text
alap_deferral_opportunity_count += 1
```

### 实际推迟

若 ALAP 因正松弛规则未 dispatch 该作业，则：

```text
positive_slack_deferral_count += 1
```

定义：

\[
\mathrm{ALAPDeferralRate}
=
\frac{
\mathrm{positive\_slack\_deferral\_count}
}{
\mathrm{alap\_deferral\_opportunity\_count}
}.
\]

## 9.4 ST slack-charging 等待

### 等待机会

当且仅当：

1. 当前最高优先级 ready 候选能量不足；
2. 该作业剩余 slack 为正；
3. ST 规则进入“继续充能或启动”的判定；
4. CPU 可用性不是唯一阻塞原因。

记：

```text
st_charging_opportunity_count += 1
```

### 实际等待

若 ST 选择继续等待收能，则：

```text
st_slack_charging_wait_count += 1
```

定义：

\[
\mathrm{STChargingWaitRate}
=
\frac{
\mathrm{st\_slack\_charging\_wait\_count}
}{
\mathrm{st\_charging\_opportunity\_count}
}.
\]

## 9.5 高优先级能量阻塞比例

每个单位时间至多计一次：

- 分母：至少一个 Top-4 作业 ready，且在忽略能量约束时存在可 dispatch 位置；
- 分子：上述条件成立，并且至少一个最高优先级可 dispatch Top-4 作业仅因能量不足未能 dispatch。

定义：

\[
\mathrm{HPEnergyBlockedFraction}
=
\frac{
\mathrm{hp\_energy\_blocked\_ticks}
}{
\mathrm{hp\_dispatch\_demand\_ticks}
}.
\]

不同机制率使用不同机会分母，不能把原始计数直接横向比较。

---

# 10. Pilot

## 10.1 Pilot 目的

Pilot 只验证：

- 新功能实现正确；
- benchmark 进入非退化运行区；
- 五种直接相关调度逻辑确实被触发；
- clipping 未全面主导结果。

Pilot 不按 ASAP-BLOCK 是否领先选择参数。

## 10.2 Pilot 前置条件

下列功能和测试完成前禁止启动 Pilot：

1. `task_energy_factor` 已接入生成器、仿真器和结果身份；
2. 三阶段 offered-harvest 已实现；
3. 单位一致性测试通过；
4. CPU-only 门禁已实现；
5. HPPass 已实现；
6. 机制计数器已实现；
7. instrumentation 非干扰回归通过；
8. 五种 Pilot 算法的结果身份与配对审计通过。

## 10.3 Pilot 算法

- ASAP-BLOCK；
- ASAP-NONBLOCK；
- ASAP-SYNC；
- ALAP-BLOCK；
- ST-BLOCK。

## 10.4 Pilot 矩阵

\[
U_{\mathrm{norm}}
\in
\{0.3,0.4,0.5\},
\]

\[
\lambda_E
\in
\{0.70,0.85,1.00,1.15\},
\]

\[
\rho_E
\in
\{1,2\}.
\]

每单元 20 个基础任务集：

\[
3\times4\times2\times20\times5
=
2400
\]

次仿真。

Pilot seed 与 formal seed 完全独立，并在 Pilot 启动前写入冻结配置。

## 10.5 中立门禁

Pilot 通过必须满足：

1. 技术 error 和最终 timeout 为 0；
2. 结果配对完整；
3. CPU-only 门禁、单位审计和身份审计通过；
4. 至少一个 \(\rho_E=2\)、\(\lambda_E\in\{0.85,1.00\}\) 的单元中，五算法中位 HPPass 位于 \([0.15,0.85]\)；
5. 至少一个内部供能单元中：
   - 至少 20% 的任务集出现高优先级能量阻塞；
   - 至少 20% 出现 NONBLOCK 绕过机会；
   - 至少 20% 出现 SYNC 批次拒绝；
   - ALAP 推迟机会和实际推迟均非零；
   - ST 等待机会和实际等待均非零；
6. 至少一个内部供能单元的 clipping ratio 中位数不超过 10%；
7. instrumentation 非干扰测试全部通过。

这些门禁不包含 ASAP-BLOCK 的排名、领先幅度或显著性。

## 10.6 Pilot 失败处理

- 技术错误：修复后按原冻结配置重跑；
- clipping 全面主导：只允许将容量倍数从 \(2E_{\mathrm{burst}}^{\mathrm{ref}}\) 调整为 \(4E_{\mathrm{burst}}^{\mathrm{ref}}\)，完整保留原 Pilot；
- 关键机制几乎不发生：停止 formal，检查实现或预注册 \(\lambda_E\) 是否覆盖过渡区；
- 不允许根据 ASAP-BLOCK 的领先幅度调参。

---

# 11. Formal

## 11.1 主矩阵

固定：

\[
\rho_E=2.
\]

运行：

\[
5\ U
\times
4\ \lambda_E
\times
100\ \text{基础任务集}
\times
9\ \text{算法}
=
18{,}000
\]

次仿真。

每个 \(U\) 生成 100 个基础任务集。同一基础任务集复用于 4 个 \(\lambda_E\) 和 9 个算法。

## 11.2 中性负对照

固定：

\[
\rho_E=1.
\]

代表网格：

\[
U_{\mathrm{norm}}
\in
\{0.3,0.4,0.5\},
\]

\[
\lambda_E
\in
\{0.85,1.00\}.
\]

运行：

\[
3\times2\times100\times9
=
5400
\]

次仿真。

负对照复用对应的基础计算任务集、偏移、电池参数和逐单位时间 offered-harvest trace，只改变任务能量因子分布。

## 11.3 总规模

\[
2400+18000+5400
=
25{,}800
\]

次仿真。

单元级热力图主要用于描述，确认性统计基于 500 个基础任务集的聚类效应，因此固定每个利用率 100 个任务集，不引入结果驱动的样本量扩展。

---

# 12. 成功判定与性能指标

## 12.1 最低可判定作业数

每个任务至少需要 100 个 \(\mathcal J_H^{\mathrm{obs}}\) 作业。

不足 100 属于实验合同失败，不得记为算法漏期失败。

## 12.2 WholePass

当且仅当：

- 仿真正常到达 \(H\)；
- 每个任务达到最低可判定作业数；
- 10 个任务的全部 \(\mathcal J_H^{\mathrm{obs}}\) 作业零漏期；

才有：

\[
\mathrm{WholePass}=1.
\]

任一可判定作业漏期：

\[
\mathrm{WholePass}=0.
\]

## 12.3 HPPass

当且仅当 Top-4 的全部 \(\mathcal J_H^{\mathrm{obs}}\) 作业零漏期：

\[
\mathrm{HPPass}=1.
\]

HPPass 是唯一确认性主终点。WholePass 是关键次要终点。

## 12.4 连续指标

正文核心：

- overall JMR；
- Top-M JMR；
- Bottom-6 JMR；
- completion ratio；
- priority-rank JMR。

辅助：

- 高优先级能量阻塞时间；
- 高优先级恢复等待时间；
- 未完成作业比例。

只统计完成作业的响应时间不得作为正文主指标，以避免未完成作业造成选择偏差。

---

# 13. 统计分析

## 13.1 统计单位

统计单位是基础任务集，不是作业，也不是单个 \(\lambda_E\) 运行。

同一基础任务集跨多个 \(\lambda_E\) 的结果必须作为同一 cluster 保留。

## 13.2 确认性主效应

对直接对照 \(c\) 和基础任务集 \(s\)，定义：

\[
d_{s,c}^{\mathrm{HP}}
=
\frac{1}{4}
\sum_{\lambda_E}
\left(
\mathrm{HPPass}_{s,\lambda_E}^{\mathrm{ASAP-BLOCK}}
-
\mathrm{HPPass}_{s,\lambda_E}^{c}
\right).
\]

总体效应：

\[
\Delta_c^{\mathrm{HP}}
=
\frac{1}{5}
\sum_{U}
\left(
\frac{1}{100}
\sum_{s\in U}
d_{s,c}^{\mathrm{HP}}
\right).
\]

每个利用率等权，每个利用率内 100 个基础任务集等权。

四个确认性比较：

1. ASAP-BLOCK vs ASAP-NONBLOCK；
2. ASAP-BLOCK vs ASAP-SYNC；
3. ASAP-BLOCK vs ALAP-BLOCK；
4. ASAP-BLOCK vs ST-BLOCK。

使用：

- 按利用率分层的基础任务集聚类 bootstrap 95% CI；
- 基础任务集级配对符号翻转随机化检验；
- Holm 校正。

## 13.3 分层聚类 bootstrap

每个 bootstrap replicate：

1. 在每个 \(U\) 内，从该层 100 个基础任务集中有放回抽取 100 个；
2. 对被抽中的基础任务集保留其全部 4 个 \(\lambda_E\) 和全部算法结果；
3. 计算各层效应；
4. 对 5 个利用率层等权平均。

分层不是一般 cluster bootstrap 正确性的必要条件，但与本实验固定等额网格的目标 estimand 更一致，且不增加实质复杂度。

## 13.4 WholePass

WholePass 使用相同的分层聚类方法报告：

- 网格平均风险差；
- 95% CI；
- ASAP-BLOCK-only 与 comparator-only 成功数量。

WholePass 不与 HPPass 混入同一个确认性检验族。

## 13.5 单元级结果

每个 \(U,\lambda_E\) 单元报告：

- 绝对通过率；
- 配对差；
- 聚类 bootstrap CI。

单元结果用于定位优势区域，不在每个单元堆叠显著性星号。

## 13.6 九算法网格平均通过率

对算法 \(S\)：

\[
\bar P_S^{\mathrm{Whole}}
=
\frac{1}{20}
\sum_{g\in\mathcal G}
P_S^{\mathrm{Whole}}(g),
\]

\[
\bar P_S^{\mathrm{HP}}
=
\frac{1}{20}
\sum_{g\in\mathcal G}
P_S^{\mathrm{HP}}(g),
\]

其中：

\[
\mathcal G
=
\{5\ U\}
\times
\{4\ \lambda_E\}.
\]

称为“预注册网格平均通过率”，不称为连续可调度体积。

---

# 14. 正文五张图

五张正文图全部来自 \(\rho_E=2\) 的同一份 formal 主矩阵。所有图统一使用论文级字体、线宽和符号规范；最终投稿版本同时输出矢量 PDF 和高分辨率 PNG。图中不得通过截断坐标、单独改变色标范围或选择性隐藏负值来夸大 ASAP-BLOCK 的优势。

## 图 1：完整九算法综合比较——双面板横向点图

图 1 使用双面板横向点图，不再使用 \(3\times3\) 颜色矩阵。

### 面板 A：HPPass

- 横轴：预注册网格平均 HPPass，单位为百分比；
- 纵轴：九种算法；
- 算法按 ASAP、ALAP、ST 三组排列，每组内部依次为 BLOCK、NONBLOCK、SYNC；
- 每个点表示该算法在 20 个预注册 \((U_{\mathrm{norm}},\lambda_E)\) 单元上的等权平均 HPPass；
- 水平误差线表示按利用率分层、以基础任务集为 cluster 的 95% bootstrap 置信区间。

算法顺序固定为：

1. ASAP-BLOCK；
2. ASAP-NONBLOCK；
3. ASAP-SYNC；
4. ALAP-BLOCK；
5. ALAP-NONBLOCK；
6. ALAP-SYNC；
7. ST-BLOCK；
8. ST-NONBLOCK；
9. ST-SYNC。

ASAP-BLOCK 使用实心点突出，其余算法使用统一的空心点或中性符号。不得通过独立缩放或夸张颜色人为放大差异。

### 面板 B：WholePass

- 横轴：预注册网格平均 WholePass，单位为百分比；
- 纵轴、算法顺序、符号和置信区间计算方式与面板 A 完全一致；
- 面板 A 和面板 B 使用相同的横轴范围，便于直接比较高优先级保护与严格全任务集成功率。

图 1 回答：

> 九种算法在完整预注册主网格上的绝对表现如何，ASAP-BLOCK 的平均表现及其统计不确定性处于什么位置。

## 图 2：ASAP-BLOCK 相对 NONBLOCK 和 SYNC 的优势区域——双热力图

图 2 固定执行时机为 ASAP，比较不同能量冲突处理策略。

### 面板 A

\[
\Delta_{\mathrm{NB}}^{\mathrm{HP}}(U,\lambda_E)
=
P_{\mathrm{ASAP-BLOCK}}^{\mathrm{HP}}(U,\lambda_E)
-
P_{\mathrm{ASAP-NONBLOCK}}^{\mathrm{HP}}(U,\lambda_E).
\]

### 面板 B

\[
\Delta_{\mathrm{SYNC}}^{\mathrm{HP}}(U,\lambda_E)
=
P_{\mathrm{ASAP-BLOCK}}^{\mathrm{HP}}(U,\lambda_E)
-
P_{\mathrm{ASAP-SYNC}}^{\mathrm{HP}}(U,\lambda_E).
\]

两个面板统一采用：

- 横轴：能量充足度 \(\lambda_E\in\{0.70,0.85,1.00,1.15\}\)；
- 纵轴：归一化利用率 \(U_{\mathrm{norm}}\in\{0.2,0.3,0.4,0.5,0.6\}\)；
- 颜色：HPPass 配对风险差；
- 每个格子直接标注百分点差，例如 `+12 pp`；
- 正值表示 ASAP-BLOCK 更好，负值表示对照算法更好；
- 两个面板使用同一个以 0 为中心的对称发散色标。

图 2、图 3 的四张主热力图必须共享同一色标范围，避免因单独缩放造成视觉误导。WholePass 配对差不再占用主颜色，可在格内以较小文字补充，或放入紧邻附表及附录热力图。

图 2 回答：

> BLOCK 相对 NONBLOCK 和 SYNC 的高优先级保护优势出现在哪些利用率—供能区域。

## 图 3：ASAP-BLOCK 相对 ALAP-BLOCK 和 ST-BLOCK 的优势区域——双热力图

图 3 固定能量冲突处理策略为 BLOCK，比较不同执行时机策略。

### 面板 A

\[
\Delta_{\mathrm{ALAP}}^{\mathrm{HP}}(U,\lambda_E)
=
P_{\mathrm{ASAP-BLOCK}}^{\mathrm{HP}}(U,\lambda_E)
-
P_{\mathrm{ALAP-BLOCK}}^{\mathrm{HP}}(U,\lambda_E).
\]

### 面板 B

\[
\Delta_{\mathrm{ST}}^{\mathrm{HP}}(U,\lambda_E)
=
P_{\mathrm{ASAP-BLOCK}}^{\mathrm{HP}}(U,\lambda_E)
-
P_{\mathrm{ST-BLOCK}}^{\mathrm{HP}}(U,\lambda_E).
\]

两个面板的横轴、纵轴、格内标注和色标规则与图 2 完全一致。

图 3 回答：

> 在同样采用 BLOCK 规则时，ASAP 相对 ALAP 和 ST 的高优先级保护优势出现在哪些利用率—供能区域。

## 图 4：四个直接比较的总体正式效应——配对效应量点图

图 4 使用带 95% 置信区间的配对效应量点图，不在正文中称为“森林图”。

- 横轴：ASAP-BLOCK 相对对照算法的网格平均 HPPass 风险差，单位为百分点；
- 纵轴：四个预注册直接比较：
  1. ASAP-BLOCK − ASAP-NONBLOCK；
  2. ASAP-BLOCK − ASAP-SYNC；
  3. ASAP-BLOCK − ALAP-BLOCK；
  4. ASAP-BLOCK − ST-BLOCK；
- 实心点：\(\Delta_c^{\mathrm{HP}}\) 的点估计；
- 水平误差线：按利用率分层、以基础任务集为 cluster 的 95% bootstrap 置信区间；
- 竖直参考线：风险差为 0；
- 点位于 0 右侧表示 ASAP-BLOCK 更好，位于左侧表示对照算法更好。

WholePass 的网格平均风险差作为关键次要终点，可在每行使用较小的空心点表示，并通过图例与 HPPass 清楚区分。Holm 校正后的确认性检验结果放在图旁文字列或正文表格中，不使用大量显著性星号。

图 4 回答：

> 跨越完整主网格后，ASAP-BLOCK 相对四个直接对照的总体效应有多大，置信区间是否支持稳定正效应。

## 图 5：优势来源与代价——三面板组合图

### 面板 A：逐优先级 JMR 折线图

- 横轴：RM 优先级排名 1–10；
- 纵轴：对应优先级任务的 JMR；
- 曲线：ASAP-BLOCK 与四个直接对照；
- 在 rank 4 与 rank 5 之间增加竖直分界线，区分 Top-4 与 Bottom-6。

若五条曲线过于拥挤，可分成两个并列小面板：

- ASAP-BLOCK、ASAP-NONBLOCK、ASAP-SYNC；
- ASAP-BLOCK、ALAP-BLOCK、ST-BLOCK。

该面板用于判断 ASAP-BLOCK 的收益是否集中在高优先级任务，以及是否伴随低优先级漏期增加。

### 面板 B：高优先级保护—总体吞吐权衡散点图

- 横轴：

\[
\Delta\mathrm{Completion}
=
\mathrm{CompletionRatio}_{\mathrm{ASAP-BLOCK}}
-
\mathrm{CompletionRatio}_{c};
\]

- 纵轴：

\[
\mathrm{TopM\ JMR\ Improvement}
=
\mathrm{JMR}_{c}^{\mathrm{TopM}}
-
\mathrm{JMR}_{\mathrm{ASAP-BLOCK}}^{\mathrm{TopM}};
\]

- 每个点表示一个 \((U,\lambda_E)\) 参数单元；
- 四个直接对照使用不同点形或拆成四个小面板；
- 横纵轴均绘制 0 参考线。

右上象限表示 ASAP-BLOCK 同时提高高优先级保护和总体完成率；左上象限表示提高高优先级保护但牺牲部分总体完成率。

### 面板 C：机制暴露分面图

机制指标不得使用同一根普通柱状图直接横向比较。面板 C 采用分面折线图或分面点图，分别展示：

- BypassRate；
- SyncRejectRate；
- ALAPDeferralRate；
- STChargingWaitRate；
- HPEnergyBlockedFraction；
- clipping ratio。

每个小面板：

- 横轴：\(\lambda_E\)；
- 纵轴：该机制自身定义的发生率或比例；
- 可对不同 \(U_{\mathrm{norm}}\) 使用不同线型或分别分面；
- 必须标明该指标自己的机会分母。

不同机制率具有不同分母，只用于确认相应机制是否发生以及随供能条件如何变化，不得根据数值大小直接比较“哪个机制更强”。

图 5 回答：

> ASAP-BLOCK 的优势集中在哪些优先级、是否存在总体吞吐代价，以及性能差异是否伴随预期调度机制的实际发生。

---

# 15. 负对照

\(\rho_E=1\) 不进入正文五张主图，可放附录或正文小表。

它回答：

> 当优先级与单位执行能耗不再系统耦合时，九算法差异是否缩小。

由于两种 \(\rho_E\)：

- 总名义能量需求相同；
- \(E_0\) 相同；
- \(E_{\max}\) 相同；
- offered-harvest trace 逐单位时间相同；

因此负对照只移除优先级—能耗耦合，不引入系统环境变化。

---

# 16. 审计合同

正式实验必须满足：

- 任务生成 seed、Pilot seed、formal seed 分离；
- 基础任务集、偏移和能量因子确定性；
- 九算法完全配对；
- \(\rho_E=1\) 与 \(\rho_E=2\) 的电池和 trace 冻结规则通过 hash 审计；
- semantic hash、配置 hash、Git commit 和二进制 hash 固定；
- 技术失败最终为 0；
- timeout 重试后仍失败则 formal 审计不通过；
- 结果行数与 manifest 一致；
- minimum adjudicable jobs 合同一致；
- checkpoint、日志和 stage seal 落盘；
- formal 数据生成后不得修改任务生成、参数、指标或图表定义。

## 16.1 Instrumentation 非干扰回归

固定测试集上，在计数器关闭和开启两种模式下比较：

- 每次调度决策的 selected set；
- 电池轨迹；
- 作业完成时刻；
- deadline miss；
- WholePass；
- HPPass。

必须完全一致。计数器只能观察，不能改变调度路径。

---

# 17. 实施顺序

1. 接入 `task_energy_factor`；
2. 统一 \(q_0\) 能量单位并完成单位测试；
3. 实现分层约束截止期和异步偏移；
4. 实现 CPU-only 有限 horizon 门禁；
5. 实现 \(\rho_E\) 严格归一化；
6. 实现跨 \(\rho_E\) 冻结的电池和 offered-harvest trace；
7. 实现 HPPass；
8. 实现机制事件与分母；
9. 完成单元测试和 instrumentation 非干扰回归；
10. 运行 2400 次 Pilot；
11. 按中立门禁审计；
12. Pilot 通过后运行 18,000 次主矩阵；
13. 运行 5,400 次中性负对照；
14. 完成统计、五张正文图和附录；
15. 独立审计全部输出。

---

# 18. 总规模

| 阶段 | 请求数 |
|---|---:|
| Pilot | 2,400 |
| \(\rho_E=2\) formal 主矩阵 | 18,000 |
| \(\rho_E=1\) 中性负对照 | 5,400 |
| **总计** | **25,800** |

不包含旧扩展实验 A。

---

# 19. 最终判断

该方案保留必要的公平性和论文可信度，同时避免无效复杂化：

- 一个统一任务族；
- 一套统一主网格；
- 一条统一 offered-harvest 轨迹；
- 九算法完全配对；
- 不为不同对照算法分别定制场景；
- WholePass 保持严格零漏期；
- HPPass 作为与算法目标一致的唯一确认性主终点；
- 负对照只改变任务间能量分布；
- 统计以基础任务集为聚类单位；
- 五张图各自回答不同问题。

本方案不会预先保证 ASAP-BLOCK 排名第一。它提供的是公平、可解释且可复现的验证条件，最终结论由独立 formal 数据决定。
