# ASAP-BLOCK v9.3 独立科学验证器设计与可行性分析

状态：仅设计，未实现。

本文限定于 CORE-1/CORE-2 的五个 v9.3 分析变体、冻结的整数窗口规则和
`EXACT_RATIONAL` 数值模式。本文不修改生产 RTA 数学、搜索器、taskset
orchestration、aggregation 或正式实验参数，也不把现有 state 或由它派生的
hash 当作科学真值。

## 1. 威胁与错误模型

需要分开的错误层如下。把这些层混为“产物不一致”会高估现有关系闭包能够给出的
保证。

| 层 | 典型错误 | 负责机制 | 不能保证的内容 |
|---|---|---|---|
| A. 产物生成后被一致修改 | 同时改 source/target state、terminal、CSV、vector hash、dependency 和 dominance 行 | 在可信验收后产生并外部保存的 hash/attestation 用于发现后续修改；独立 verifier 从冻结输入重算，用于发现验收前的科学篡改 | 如果攻击者同时控制 verifier、attestation 的信任根和冻结输入，则仓库内自校验无法解决 |
| B. serializer/aggregation 错误 | 字段错位、遗漏、把同一个错误值复制到所有视图 | schema、跨产物 closure、mutation tests，以及 verifier 输出与最终产物的绑定 | 关系闭包本身不能判断一致的错误候选是否正确 |
| C. taskset orchestration 错误 | 优先级顺序、前缀停止、fixed/recursive carry-in、source dependency 错误 | 独立 taskset replay 加 orchestration mutation tests | 仅重验单个候选闭合条件不足以发现此层错误 |
| D. canonical search 实现错误 | 跳过更早的 `w`/`h`、错误接受 `q`、错误报告 NO_CANDIDATE | 不调用生产 search 的独立有限枚举器；golden/mutation/differential tests | 若 verifier 复用 canonical search，就不能声称覆盖此层 |
| E. 低层 envelope/energy 数学实现错误 | workload、processor delay、complete/local envelope、service index 或 exact 比较实现错误 | 小型、直接、独立的 reference primitives；理论导出的 microcases；与生产实现的差分仅作补充 | 若 verifier 复用相应生产 primitive，该 primitive 必须进入 TCB，覆盖范围随之缩小 |
| F. 理论公式本身错误 | 代码忠实实现了一个不安全的充分条件 | 论文证明、同行审查、模型与实现映射审计 | 独立重算同一冻结公式不能证明公式本身正确 |

因此，hash/attestation 解决的是“可信验收之后是否变化”，跨产物 closure 解决的是“各视图
是否一致”，独立 verifier 解决的是“实现是否按冻结公式和 canonical 规则计算”，
测试解决已知错误类和回归，理论证明负责公式的安全性。它们不能互相替代。

### 1.1 信任模型与当前冻结决策

本文明确区分两个信任模型。

**T1：普通科研软件信任模型。** T1 假设 RTA 理论由论文证明支撑，production
analyzer 已经代码审查，关键科学 primitive 有独立 oracle 和 CORE-0，正式输入、输出、
resume、aggregation、plot、package 和 archive 已完成闭合，不考虑主动篡改，主要关注
无意实现错误。

**T2：强独立复核模型。** T2 要求每个正式 analysis 都由第二套科学实现完整重算，且
production analyzer 同步生成的、自洽的错误数值必须被独立实现发现。T2 支持
“independently verified”声明；T1 不支持该声明。

当前项目正式采用 **T1**。P1-3 coherent scientific mutation risk 真实存在，但在 T1
下分类为 **P2 / post-formal enhancement**，不属于当前正式 CORE-1/CORE-2 的阻止项。
当前分支不实现、不运行 independent verifier，也不得声称全部结果已由第二实现独立
重算。若未来采用 T2，必须在作出独立验证声明之前实现并全量运行本文规定的 verifier。

### 1.2 Current Formal Acceptance Policy（规范性）

当前 CORE-1/CORE-2 T1 正式 campaign 必须按以下启动门槛顺序执行；全部条件满足前
不得开始正式运行：

1. 当前修复分支形成一个本地 commit；
2. 该 commit 通过最终独立 diff/CI 审查；
3. 该 commit 合并到目标正式分支；
4. 只从合并后的唯一 commit 重新生成并冻结 canonical input/formal config，同时绑定
   production analyzer 和源码身份；
5. 为随后运行分配全新的、未被历史运行或 canary 使用过的 formal output root；
6. CORE-0 和要求的组件 oracle 全部通过；
7. 最终全量回归测试通过；
8. 在正式运行前完成最终 **non-formal production-chain canary**；
9. 该 canary 覆盖 CORE-1 和 CORE-2 各自的正式分析链，而不是只探测 CLI 或单个
   primitive；
10. canary 的科学字段验证、run closure、derived-output closure、plot closure、
    package closure 和 archive closure 全部通过；其中 plot closure 指当前冻结的
    canonical plot schema、plot input 及合法展示产物生成链，且 canary 必须实际经过
    正式生产链使用的 plot validator 和 plot CLI；
11. canary 的 soundness、dependency、dominance 和 contract violation 计数均为 0；
12. 只有上述 1--11 项全部通过后，才允许启动正式 CORE-1/CORE-2 campaign。

这里的 canary 是**当前 T1 正式 campaign 之前的生产链 canary**，不是未来 T2 verifier
的开发 canary，也不是用抽样替代正式实验。它必须使用明确标记的 non-formal 配置和
独立 canary output root，不得伪装、汇总或发布为正式结果。canary 失败即关闭正式启动
门槛；plot closure 失败同样不得启动正式 CORE-1/CORE-2 campaign。上述内容只明确
现有 production-chain canary 的验收范围，不新增验收层或实现要求。修复后必须形成新的
commit，重新完成独立审查、merge、formal config freeze 和
全量回归，并在新的 canary output root 上重新执行完整 canary，不得从失败 canary
直接继续或授权正式 campaign。

正式 campaign 完成后，当前 T1 正式科学结果还只有同时满足以下验收条件才能接受：

1. 实际运行身份精确绑定上述启动门槛冻结的 commit、canonical input/formal config、
   production analyzer 和全新 formal output root；
2. 正式 run closure 验证通过；
3. 正式 derived-output 验证通过；
4. 正式 package/archive closure 验证通过；
5. 正式产物不存在 soundness、dependency、dominance 或 contract violation；
6. 论文和发布说明明确采用 T1，而不是 T2。

P1-3 independent verifier 不属于当前 T1 acceptance gate；它只属于未来 T2 gate。它
不得改变当前 formal authorization、resume、aggregation、output inventory、package 或
archive schema。上述政策仅冻结验收条件，不在本文中授权、启动或执行正式实验。

### 1.3 非目标

Independent scientific verifier 不负责理论证明、证明任务模型正确、resume、worker
lifecycle、quarantine、aggregation、summary、plot、package/tar、文件系统发布、网络
服务、数据库、第二个调度仿真器或自动定理证明。其边界严格限定为：

```text
canonical scientific input
-> independent RTA replay
-> comparison report
-> optional T2 acceptance attestation
```

## 2. 最小可信计算基

建议 verifier 是独立入口、独立模块树和独立进程，不导入
`asap_block_rta_v9_3.py`、`asap_block_rta_v9_3_taskset.py` 或
`experiments.v9_3.aggregation`。最小 TCB 为：

- Python 运行时和标准库 `fractions.Fraction` 的精确有理数语义；
- verifier 自己的严格、只读 schema decoder；
- verifier 自己的 service-curve、envelope、closure 和 taskset replay 实现；
- 已冻结的 taskset/config/service-curve 原始内容，以及在 verifier 运行前由外部信任根
  固定的内容摘要；
- verifier 的版本/源码摘要和执行环境记录。

各类复用的具体决定如下。

| 组件 | 是否复用 | 理由 |
|---|---|---|
| exact arithmetic | 只复用标准库 `Fraction`，不复用生产数值包装器 | 避免复制大整数/有理数内核，同时保留独立的 canonical decoder 和范围检查 |
| task/parser | 不复用生产 task class/parser | 从冻结 JSON/CSV 重新解析，并拒绝 `bool`、非规范整数/分数和重复 task ID |
| service curve validation | 不调用生产 validator | 独立检查索引覆盖、非负、单调/冻结约束和 canonical fraction encoding |
| complete/local envelope primitive | 正式验收版不复用 | 独立按冻结公式写清晰但可较慢的 reference implementation，才能覆盖 E 层 |
| closure predicate | 不复用 | 独立计算 `A(w)`、允许的 `h/q` 域、service index 和逐点比较 |
| canonical search | 禁止复用 | 这是 D 层的主要验证对象 |
| taskset finalizer | 禁止复用 | 独立重建优先级前缀、状态矩阵、计数和联合认证 |

若早期原型为了测量成本而临时复用 complete/local envelope primitive，报告必须把该
primitive 明确列为 TCB，并将结论降级为“只覆盖 orchestration/search，不覆盖低层
envelope”。这种原型不能作为未来 T2 acceptance attestation 的科学验证证据。

## 3. 独立 verifier 的证明义务

verifier 从冻结输入重建 task 定义、RM 优先级、处理器数、`E0`、service curve 和分析
变体；state 只作为“待核对声明”，不能参与计算真值。

未来 T2 verifier 的搜索不得使用 production candidate、production status 或 production
state 中间量作为起点、终点、剪枝条件或科学真值。每个 task 都从冻结的 `C_k` 开始，
终止条件只能是 verifier 自己找到首个合法 candidate，或完整扫描至 `D_k`。production
candidate/status 只在独立搜索完成后作为待比较声明读取。

对每个实际求解的 task，完整义务为：

1. 重新建立 fixed 或 recursive carry-in。`CW_D/LOC_D` 使用全任务 deadline 向量；
   `CW_THETA_CW/LOC_THETA_LOC` 只使用 verifier 已经独立验证的高优先级候选前缀；
   `LOC_THETA_CW` 使用 verifier 已先独立验证为 jointly certified、且上下文身份一致的
   `CW_THETA_CW` source 向量。
2. 对 `w = C_k ... D_k` 按升序计算 `A_k(w)`。若 `A_k(w) > w`，该 `w` 没有合法
   `h`。
3. 对合法 `w`，按 `h = 0 ... w-A_k(w)` 升序扫描，并对
   `q = 1 ... A_k(w)` 检查冻结闭合不等式。service index 必须是
   `h+q-1`；complete envelope 的 coverage 是 `w`，local envelope 的 coverage 是
   `q+h`。
4. 报告的 candidate 必须是第一个存在全体 `q` 均通过的 `h` 的 `w`；报告的
   `witness_h` 必须是该 `w` 下第一个合法 `h`；`closing_w` 必须等于 candidate。
   verifier 既要重算成功点，也要证明所有更早的 `w` 和同一 `w` 下更早的 `h`
   失败。即使 production candidate 更早、更晚或不可行，verifier 仍按自己的搜索结果
   产生 independent candidate/status，不能在 production candidate 处停止。
5. 重算 `checked_w/h/q` 和 envelope-call counters 作为诊断证据。计数器不是科学证明
   本身；除非未来 acceptance policy 明确冻结精确控制流计数，counter 差异不能单独把
   数学上相同的 independent result 分类为 `SCIENTIFIC_MISMATCH`。
6. `NO_CANDIDATE` 只有在完整扫描到 `D_k` 后仍无候选时成立；不得从缺失 state、
   超时或异常推导。
7. `TIMEOUT` 是 operational/unproven 结果。verifier 只确认它没有携带认证声明，并按
   §5.3 标记为 `OPERATIONAL_UNVERIFIED`；离线重放可报告 production/independent
   结果，但不得静默把原结果改成 candidate 或 NO_CANDIDATE。
8. `NUMERIC_ERROR` 同样不得产生认证。精确参考实现若在合法冻结输入上成功，应报告
   `SCIENTIFIC_MISMATCH` 和稳定的 production-numeric-disagreement failure category；
   若输入本身非法则报告 `INVALID_INPUT_OR_CONTRACT`，不能接受生产认证。
9. complete/local 两种窗口必须走分别实现的 coverage/envelope 分支，不能只比较
   生产给出的 envelope 值。
10. taskset replay 必须按优先级逐项推进。第一个非 candidate task 确定
    `first_failed_priority`，后缀必须未求值；只有每个 task 的 candidate 都通过上述
    义务，才能给出 jointly certified。`LOC_THETA_CW` 还要检查 dependency
    applicable 和逐 task local candidate 不大于独立重算的 source candidate；N/A
    只能来自独立验证后的 dependency 不适用，而不是缺 state 的替代状态。

### 3.1 五变体规范矩阵（规范性）

下表冻结未来 T2 verifier 的五个实际分析变体。所有行按 §3.2 共享同一个 processor
expression；`CW_D/LOC_D` 的 deadline-fixed carry-in 与三个 theta 变体的
recursive/source-fixed carry-in 不同。complete/local 的 coverage 差异只进入 energy
envelope 的 workload input（`w` 或 `q+h`），不创造第二个 processor expression。

| Variant | Carry-in mode | Energy workload window / coverage length | Processor term | Energy term | Recursive prefix | Source dependency / applicability | Invalid dependency status | Candidate search | Closing / witness | Final status |
|---|---|---|---|---|---|---|---|---|---|---|
| `CW_D` | deadline-fixed：全任务 `D_i` 向量；fixed-carry-in interface 必须有效 | complete；energy coverage=`w` | 按 §3.2 独立计算，processor workload length 固定为 `w`，使用本行 deadline-fixed carry-in | 独立 complete envelope；比较 `E <= E0 + beta(h+q-1)` | 无 | 无外部 source；只检查 fixed interface applicability | interface 无效时 `NOT_APPLICABLE_DEPENDENCY`，不得 fallback | 从 `C_k` 独立搜索至 verifier 首个 candidate 或 `D_k` | `closing_w=independent candidate`；`witness_h` 为首个成功 `h` | 全任务成功为 `COMPLETED/CERTIFIED_TASKSET`；否则按首个冻结失败状态终止并标记后缀未求值 |
| `LOC_D` | deadline-fixed：全任务 `D_i` 向量；fixed-carry-in interface 必须有效 | local；energy coverage=`q+h` | 按 §3.2 独立计算，processor workload length 仍为 `w`，使用本行 deadline-fixed carry-in | 独立 local envelope；比较 `E <= E0 + beta(h+q-1)` | 无 | 无外部 source；只检查 fixed interface applicability | interface 无效时 `NOT_APPLICABLE_DEPENDENCY`，不得 fallback | 同上 | 同上 | 同上 |
| `CW_THETA_CW` | recursive：只使用 verifier 已验证的高优先级 independent candidate 前缀 | complete；energy coverage=`w` | 按 §3.2 独立计算，processor workload length=`w`，使用本行 independent CW 前缀 carry-in | 独立 complete envelope；同一 service index | 有；按 RM 优先级逐项扩展 | 无外部 source；fixed interface 为 `NOT_APPLICABLE` | 不适用；不得读取 production source vector | 每个前缀 task 独立搜索至自身首个 candidate 或 `D_k` | 每个 task 独立产生 canonical closing/witness | 首个非 candidate 决定 taskset 状态和 `first_failed_priority`；后缀未求值 |
| `LOC_THETA_LOC` | recursive：只使用 verifier 已验证的高优先级 independent local candidate 前缀 | local；energy coverage=`q+h` | 按 §3.2 独立计算，processor workload length=`w`，使用本行 independent LOC 前缀 carry-in | 独立 local envelope；同一 service index | 有；按 RM 优先级逐项扩展 | 不携带外部 source；fixed interface 为 `NOT_APPLICABLE` | 不适用；出现 source 即 contract invalid | 每个前缀 task 独立搜索至自身首个 candidate 或 `D_k` | 每个 task 独立产生 canonical closing/witness | 与 `CW_THETA_CW` 相同的前缀停止和状态矩阵 |
| `LOC_THETA_CW` | source-fixed：使用 independently verified `CW_THETA_CW` candidate 全向量 | local；energy coverage=`q+h` | 按 §3.2 独立计算，processor workload length=`w`，使用本行 independently verified source-fixed carry-in | 独立 local envelope；同一 service index | 无；carry-in 固定为独立 source 向量 | source 必须为相同 context、相同 taskset/order、jointly certified，且 fixed interface 有效 | 任一条件不满足均为 `NOT_APPLICABLE_DEPENDENCY/NOT_APPLICABLE`；不得静默 fallback | source applicable 后仍从 `C_k` 独立搜索至自身首个 candidate 或 `D_k` | 独立产生 canonical closing/witness，并检查 local candidate 不大于 source candidate | 全部成功且满足 dominance 才为 `COMPLETED/CERTIFIED_TASKSET`；local `NO_CANDIDATE` 或 candidate 大于 source 为 `INTERNAL_CONFORMANCE_FAILURE`；TIMEOUT/numeric 按冻结状态传播；dependency N/A 与科学/资源失败严格分离 |

共同的失败状态保持现有冻结语义：task 级可为 `CANDIDATE_FOUND`、`NO_CANDIDATE`、
`TIMEOUT`、`NUMERIC_ERROR`、`NOT_EVALUATED_AFTER_PREFIX_FAILURE`、
`NOT_APPLICABLE_DEPENDENCY` 或 `INTERNAL_CONFORMANCE_FAILURE`；verifier 只重算并比较，
不得改写 production 状态。

最终科学比较对象包括 solver status、candidate、closing/witness、carry-in、任务前缀、
taskset certification、dependency 和 dominance；counter 另作诊断。任何科学差异都
生成 fail-closed 验收记录，不能修改原产物使其“匹配”。

### 3.2 Normative Processor-Term Interface（规范性）

本节的唯一公式权威身份是冻结理论文档
`asap_block_rta_multicore_complete_and_local_paper_ready_v9_3_fixed_carry_in_interface(1).md`
（SHA-256
`524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`）的第 3 节
carry-in workload 定义和第 4 节 processor reference progress 定义。v1.3.12 机器合同
§4.3 的 definition-scan 公式及当前 production core 的公式映射只作为一致性核对证据；
production 函数调用、优化扫描或函数名不是未来 verifier 的规范。未来 verifier 必须从
本节公式独立实现。

对目标任务 `tau_k`、RM 顺序中严格高于它的集合 `hp(k)`、候选窗口 `w` 和每个
`tau_i in hp(k)` 的冻结 carry-in 值 `theta_i`，先定义

$$
N_i^{\theta_i}(L)
=
\left\lfloor\frac{L+\theta_i-C_i}{T_i}\right\rfloor,
$$

$$
W_i^{\theta_i}(L)
=
N_i^{\theta_i}(L)C_i
+
\min\left\{
C_i,
L+\theta_i-C_i-N_i^{\theta_i}(L)T_i
\right\}.
$$

processor term 对所有五个变体都取 `L=w`，并逐个高优先级任务定义

$$
\bar W_{i,k}^{P,\Theta}(w)
=
\min\left\{
W_i^{\theta_i}(w),
[w-C_k+1]^+
\right\},
\qquad [x]^+=\max\{0,x\}.
$$

令 `W_k^{P,total}(w)` 为这些截断后 workload 的总和：

$$
W_k^{P,\mathrm{total}}(w)
=\sum_{\tau_i\in hp(k)}\bar W_{i,k}^{P,\Theta}(w).
$$

纯处理器排队延迟和最终 processor term 唯一定义为

$$
D_k^{P,\Theta}(w)
=
\max\left\{
d\in\mathbb Z_{\ge0}
\;\middle|\;
\sum_{\tau_i\in hp(k)}
\min\left\{\bar W_{i,k}^{P,\Theta}(w),d\right\}
\ge Md
\right\},
$$

$$
\boxed{A_k^\Theta(w)=C_k+D_k^{P,\Theta}(w).}
$$

这里的求和集合始终且仅为 `hp(k)`；目标任务和低优先级任务不进入 processor workload
求和。`M` 是正整数处理器数，在定义谓词右侧以 `M d` 使用。实施定义扫描时唯一需要的
有限域是

$$
d=0,1,\ldots,
\left\lfloor\frac{W_k^{P,\mathrm{total}}(w)}{M}\right\rfloor.
$$

因此最大值集合总含 `d=0` 且有界。这里没有 ceiling；floor 只出现在
`N_i^{theta_i}` 和上述等价有限扫描上界中。`C_i,D_i,T_i,theta_i,w,M`、raw workload、
截断 workload、`D_k^{P,Theta}` 和 `A_k^Theta` 全部属于整数域，不允许 float 或近似
比较。时间域是冻结的离散整数时间；`L` 和 `w` 表示半开窗口 `[t,t+L)`、`[t,t+w)`
的整数长度。能量值才使用 §5.2.1 的 canonical exact-rational encoding。

合法输入必须满足：任务 ID 唯一且 RM 顺序冻结；`C_i,D_i,T_i` 是严格整数并满足
`1 <= C_i <= D_i <= T_i`；`M>=1`；`C_k <= w <= D_k`；carry-in 向量对每个
`hp(k)` 恰有一个按 priority rank 排列的严格整数 `theta_i`，且
`C_i <= theta_i <= D_i`。`bool`、float、缺项、重复项、顺序/身份不一致、域外 `w`、
非法 `M` 或非法任务参数均为 `INVALID_INPUT_OR_CONTRACT`，不得截断、取整或 fallback。
若 `hp(k)` 为空，则两个和均为空和 0，唯一 delay 为 0，输出
`A_k^Theta(w)=C_k`。`w=C_k` 和 `w=D_k` 都是合法边界。

五变体只按 §3.1 冻结 `theta_i` 的来源：`CW_D/LOC_D` 使用 deadline-fixed `D_i`；
`CW_THETA_CW` 使用 verifier 已验证的 independent CW 前缀；`LOC_THETA_LOC` 使用
verifier 已验证的 independent local 前缀；`LOC_THETA_CW` 使用适用性已经独立验证的
source-fixed independent CW 全向量在 `hp(k)` 上的投影。complete/local 共享以上完整
processor term。二者差异只在 energy envelope 的 workload coverage 输入：complete
使用 `L_energy=w`，local 使用 `L_energy=q+h`；不得把 local 的 `q+h` 代入本节
processor workload。

未来 T2 的纯设计接口名冻结为 `normative_processor_term_v1`，不要求本轮创建代码：

- 输入：target task、按 RM 顺序的 higher-priority task set、`w`、carry-in
  mode/vector/source identity、energy workload coverage mode（用于交叉检查但不改变
  processor 的 `L=w`）、`M` 和完整冻结任务参数；
- 输出：按同一 priority rank 顺序的每任务
  `N_i`/edge/raw workload、截断 cap 与截断后 workload、总 processor workload
  `W_k^{P,total}(w)`、definition-scan 域、processor delay `D_k^{P,Theta}(w)` 和
  `A_k^Theta(w)`。

接口输出必须是不可歧义的规范 payload；未来 verifier 不得调用 production analyzer
取得其中任何值，也不得用 production candidate/status 限制其计算域。

## 4. 是否需要完整重算

### 方案 A：只验证 production candidate 的充分条件（非 T2）

优点是只需在报告的 `(w,h)` 上验证全部 `q`，成本较低，能证明该 candidate 是冻结
条件下的一个安全上界。它不能证明这是 canonical 的最早 candidate，不能验证
`witness_h` 的 canonicality、精确计数或 NO_CANDIDATE，也不能充分覆盖 taskset
前缀选择错误。它只适合作为未来开发期快速 canary，不是 T2 verifier，也不进入当前
T1 acceptance gate。

### 方案 B：独立重算单 task canonical result

不读取 production result 作为搜索边界，从 `C_k` 开始枚举每个 `w/h/q`。verifier 在
自己找到首个成功 `w/h` 时产生 independent candidate/closing/witness；若没有成功点，
则扫描至 `D_k` 并独立产生 `NO_CANDIDATE`。每个失败 `h` 至少要有一个独立失败的 `q`，
成功 `h` 必须验证全部 `q`。复杂度粗略上界为
`sum_w sum_h A_k(w)` 次 envelope 检查。

### 方案 C：独立重放整个 taskset

按五个变体各自的 fixed/recursive 规则，从最高优先级开始执行上述独立单-task 重算，
且每个 task 的终点只由 verifier 自己的结果决定；随后独立产生
taskset 状态矩阵。这是验证 source/target dependency、前缀停止、联合认证和 dominance
所必需的方案。代价接近再运行一次 RTA，且会产生有意的公式级代码重复；通过不同的
控制结构、禁止生产 import、microcase 和 mutation suite 降低同源错误风险。

当前 T1 不运行 A/B/C。未来若采用 T2，则必须对约定范围使用 C；A 只能作为开发期
canary，抽样 B/C 只能作为 shadow evidence，二者都不能支持“全部正式结果已独立验证”
的声明。

## 5. 独立性要求与现实架构

现实可行的架构是一个只读、批处理 verifier：输入为冻结 config、taskset store 中的
原始 task payload、service-curve material 和待核对结果；输出为追加式 verifier
report。它必须满足：

- 不调用 `analyze_taskset_v9_3`；
- 不调用 `solve_single_task_v9_3`；
- 不调用 `canonical_closure_search_v9_3`；
- 不调用生产 taskset finalizer、result validator 或 aggregation；
- 不从 state 构造 carry-in 真值；recursive/source carry-in 只能来自 verifier 自己已
  验证的前缀结果；
- 从冻结内容重建完整输入，并在计算前独立检查 canonical encoding 和 identity；
- 使用较直白的 reference envelope 实现，而不是复制生产搜索的缓存、短路或控制流；
- 通过静态 import allowlist 测试保证 verifier 模块树不能导入上述生产模块。

独立并不等于不同语言。以 Python + `Fraction` 实现一个小型 reference verifier 对当前
有限整数域是可行的；如果将来要抵抗 Python runtime 或供应链错误，再考虑第二语言
或 proof assistant，那属于更高威胁等级而非本轮最小范围。

### 5.1 未来 T2 input schema 草案（规范性）

本文冻结未来输入合同名称为
`ASAP_BLOCK_V9_3_INDEPENDENT_VERIFIER_INPUT_V1`，`schema_version=1`。这是未来 T2
实现的设计合同，不修改当前 production schema。每个 analysis 输入记录至少包含：

| 字段 | 冻结含义 |
|---|---|
| `schema` / `schema_version` | 上述固定名称和版本 |
| `analysis_id`, `taskset_id`, `variant` | production 声明的唯一身份和实际五变体枚举值 |
| `taskset_canonical_hash`, `canonical_task_payload` | 完整 canonical task 定义及其语义摘要 |
| `priority_policy`, `priority_order` | 冻结 RM policy 及唯一 task 顺序 |
| `processors_m`, `initial_energy_e0` | `M` 和 exact-rational `E0` |
| `service_curve_canonical_payload`, `service_curve_hash` | 完整 service curve、覆盖范围和摘要 |
| `config_semantic_hash` | 与该 analysis 有关的冻结 config 语义身份 |
| `dependency_plan`, `source_analysis_mapping`, `source_context` | source/target DAG、计划 source 和完整 dependency context；不适用时使用显式空值 |
| `production_terminal_claim`, `production_per_task_claim` | 只读待比较声明；不得参与 independent truth 计算 |
| `production_source_version_hash` | production analyzer 版本和源码摘要 |
| `verifier_source_version_hash` | 将执行的 verifier 版本和源码摘要 |
| `immutable_input_snapshot_hash` | 对上述 canonical 科学输入和 identity 的外部固定摘要 |

verifier 必须从 canonical task payload、config 语义、priority policy 和 service curve
重新建立全部科学输入。production state 中间向量，即使随 input bundle 提供，也只能
进入 `production_*_claim` 比较域，不能成为 carry-in、搜索边界或独立计算真值。

所有科学数值使用 canonical exact rational encoding。decoder 必须拒绝 `bool`、非规范
整数、非规范分数、重复 task ID、缺失 service index 以及任何 float fallback。

### 5.2 未来 T2 report schema 草案（规范性）

本文冻结未来报告合同名称为
`ASAP_BLOCK_V9_3_INDEPENDENT_VERIFIER_REPORT_V1`，`schema_version=1`。报告采用两层
结构：第一层是 report/analysis 级 `comparison_summary`；第二层是按 task、必要时按
candidate/point 保存的 `scientific_payloads`。summary 只便于定位和聚合，不能替代第二层
双方实际科学值。

#### 5.2.1 Canonical value、null 和 hash 规则

- 严格整数编码为 JSON integer；`bool` 不是整数。exact rational 统一编码为
  `{"numerator": p, "denominator": q}`，其中 `p` 是整数、`q>0`、`gcd(|p|,q)=1`；
  禁止 float、decimal 近似、未约分分数和字符串化科学对象。
- 所有下列必需字段始终存在。值不适用时使用显式 `null`，同时
  `field_applicability[field_path]` 必须为 `NOT_APPLICABLE`，并在
  `not_applicable_reason[field_path]` 使用冻结枚举；`APPLICABLE` 字段为 `null`、以缺字段
  代表 N/A，或把 `NOT_PRODUCED` 当作 N/A，均为 `INVALID_INPUT_OR_CONTRACT`。
- candidate 未找到时，candidate/closing/witness 可按上述规则为 `null`；最高优先级
  recursive carry-in 的空向量是一个适用的、长度为 0 的值，不是 `null` 或缺失。
- 规范对象按 key 字典序、array 原顺序、UTF-8、无额外空白、上述整数/分数/null 规则
  canonicalize，再计算 SHA-256 小写十六进制 hash。hash 用于压缩完整搜索摘要，但不得
  替代本节要求保存的双方最终值、关键分解和 mismatch 实值。

#### 5.2.2 两层字段

`comparison_summary` 至少记录完整 report identity、analysis/task 状态计数、每个 task 的
`verifier_status`、全部 `*_match` 汇总、`first_mismatch` 路径和 `report_content_hash`；后者
对省略自身字段后的 canonical 完整 report payload 计算，避免自引用。
每个 task 的第二层记录至少包含：

| 字段组 | 必需字段 |
|---|---|
| identity | `schema`, `schema_version`, `analysis_id`, `taskset_id`, `task_id`, `priority_rank`, `variant`, `input_snapshot_hash` |
| result values | `production_status`, `independent_status`, `production_candidate`, `independent_candidate` |
| closing/witness values | `production_closing_w`, `independent_closing_w`, `production_witness_h`, `independent_witness_h` |
| carry-in values | `production_carry_in_mode`, `independent_carry_in_mode`, `production_carry_in_vector`, `independent_carry_in_vector`, `production_carry_in_vector_hash`, `independent_carry_in_vector_hash` |
| processor values | `production_processor_term`, `independent_processor_term` |
| energy values | `production_energy_term`, `independent_energy_term` |
| closure values | `production_fixed_point_claim`, `independent_fixed_point_result` |
| source/dependency values | `production_source_context`, `independent_source_context`, `production_dependency_status`, `independent_dependency_status` |
| exact comparisons | `status_match`, `candidate_match`, `closing_w_match`, `witness_match`, `witness_h_match`, `carry_in_match`, `processor_term_match`, `energy_term_match`, `fixed_point_closure_match`, `source_applicability_match`, `dependency_context_match` |
| domain completion | `full_domain_checked`, `verified_search_w_min`, `verified_search_w_max`, `task_prefix_complete`, `search_domain_hash` |
| mismatch locator | `first_mismatch_field_path`, `first_mismatch_point`, `first_mismatch_production_value`, `first_mismatch_independent_value` |
| verifier execution | `verifier_numeric_mode`, `verifier_resource_status`, `verifier_status`, `failure_category`, `failure_reason` |
| provenance/policy | `verifier_version`, `verifier_hash`, `acceptance_policy`, `acceptance_policy_version` |

`witness_match` 是为兼容已冻结草案保留的字段；在 V1 witness 唯一规范值就是严格整数
`witness_h`，所以它必须与 `witness_h_match` 相同。若未来 witness 扩展为多字段对象，
必须升级 schema，并定义有字段名和类型的 payload；不得把结构体 `repr` 或自由文本放入
V1。`verifier_numeric_mode` 必须为 `EXACT_RATIONAL`。

当前 production schema 没有本节要求的逐项 processor/energy/closure 实值。因此当前
T1 产物不能被重新解释为已经满足此 V1 report，也不能以 independent 值回填
`production_*`。未来 T2 实施只有在一个另行审查、版本化、只读的 production claim
projection 能提供这些实际值后，才能签发 V1 report；projection 缺值属于合同失败。
这项未来前提不修改当前 production、worker、output、package 或 authorization schema。

#### 5.2.3 Normative scientific payload

**Carry-in payload.** `production_carry_in_vector` 和
`independent_carry_in_vector` 使用同一 schema：

```text
{
  mode,
  scope,                         # FULL_TASKSET_FIXED | VERIFIED_HP_PREFIX
  source_identity,               # source-dependent 时为规范对象，否则显式 null/N/A
  entries: [{task_id, priority_rank, value}, ...],
  processor_hp_projection: [{task_id, priority_rank, value}, ...]
}
```

`entries` 必须按冻结 `priority_order` 的递增 rank 排列，长度、task ID 和 rank 都是科学值；
每个 `value` 在 v9.3 中是严格整数 `theta_i`，不是 rational 或字符串。deadline-fixed 和
source-fixed 模式保存完整固定向量；recursive 模式保存当时已经由同一侧验证的 hp
前缀。`processor_hp_projection` 恰好是 §3.2 实际求和集合上的投影。空向量只表示目标无
hp 且该 mode/scope 合法；它不表示未知或读取失败。`mode` 冻结枚举为
`DEADLINE_FIXED`、`RECURSIVE_CW_PREFIX`、`RECURSIVE_LOC_PREFIX`、
`SOURCE_FIXED_CW_VECTOR`。source-dependent payload 的 `source_identity` 至少包含 source
analysis/taskset/variant、source snapshot hash、source candidate-vector hash、context
identity 和 jointly-certified status。两个 carry-in vector hash 各自绑定上述整个规范
payload，而不是无序 map 或只绑定数值列表。

**Processor payload.** `production_processor_term` 和
`independent_processor_term` 都必须遵守 §3.2 的同一结构：

```text
{
  target_task_id, w, M, workload_length,
  hp_order,
  per_task_workload: [
    {task_id, priority_rank, theta_i, N_i, edge_workload, raw_workload,
     truncation_cap, truncated_workload}, ...
  ],
  total_processor_workload,
  delay_search_min, delay_search_max,
  processor_delay,
  A_k_w
}
```

其中 `workload_length` 必须为 `w`，`total_processor_workload` 是全部
`truncated_workload` 的整数和，delay search 上界是该总和除以 `M` 的 floor，
`A_k_w=C_k+processor_delay`。无 hp 时数组为空、总和和 delay 为 0、`A_k_w=C_k`。
payload 不得只保存 production 函数名、调用成功标志或 `A_k_w` 单值。

**Energy payload.** `production_energy_term` 和 `independent_energy_term` 使用相同的
point/list schema。每个保留的 point 至少记录：

```text
{
  w, h, q,
  coverage_mode, coverage_length,
  demand_envelope,
  initial_energy_e0,
  service_index, service_value,
  available_energy,
  exact_deficit,
  blocking_or_delay_h,
  closure_relation, closure_passed,
  envelope_evidence
}
```

`coverage_length` 对 complete 为 `w`、对 local 为 `q+h`；`service_index=h+q-1`；
`available_energy=E0+service_value`；
`exact_deficit=max(0,demand_envelope-available_energy)`，全部能量量使用 canonical exact
rational。`closure_relation` 固定为 `demand_envelope <= available_energy`，不能只写
“通过”。`envelope_evidence` 是带 discriminator 的规范对象：要么保存 maximizer 的
`y_k/z`、有序 per-task selection 和最大值，要么保存完整枚举域、枚举计数、最大值与
canonical search-summary hash；不得引入不存在且未冻结的 `critical_q` production 字段。
energy payload 至少保存最终 witness 下全部 `q=1..A_k(w)` 的 point、每个归并后的更早
失败区域的决定性失败 point，以及完整域 digest；发生 mismatch 时还必须保存首个差异
point 的双方上述实值。

**Fixed-point/closure payload.** 名称沿用 `fixed_point` 以兼容报告合同，但 V1 的实际
对象是冻结的 finite canonical closure，不得退化为“支持相同最终声明”：

```text
{
  candidate_w,
  predecessor_w,
  search_w_min, search_w_max,
  A_k_w,
  processor_term,
  energy_term_summary,
  witness_h,
  h_search_min, h_search_max,
  q_search_min, q_search_max,
  closure_predicate,
  closing_relation_summary,
  full_domain_checked,
  first_feasible_candidate,
  earlier_candidate_failure_summary,
  canonical_search_digest
}
```

`predecessor_w` 在 `candidate_w=C_k` 时按 §5.2.1 显式 N/A；否则为前一整数 candidate
window。`closure_predicate` 精确表示
`exists h in 0..w-A_k(w): forall q in 1..A_k(w), E(w,h,q) <= E0+beta(h+q-1)`。
`closing_relation_summary` 保存最终 witness 的全部 `q` 范围、exact pass/fail 和规范
digest；`earlier_candidate_failure_summary` 保存检查范围、失败类别计数、各归并区域的
首个决定性失败 `(w,h,q)` 及 digest，不要求复制每次内部迭代。candidate 结果的
`full_domain_checked=true` 表示 `C_k..candidate_w` 以及同一 `w` 的全部更早 `h` 已覆盖；
`NO_CANDIDATE` 则表示 `C_k..D_k` 全域已覆盖。`first_feasible_candidate` 必须等于
`candidate_w`，或在全域失败时显式为 N/A。双方 payload 都保存实际值，不能只保存一个
claim 字符串或总体 status。

**Dependency/source payload.** `production_source_context` 和
`independent_source_context` 使用相同 schema，至少包含 applicability、计划/实际 source
analysis identity、taskset/order/context identity、source variant、source input snapshot
hash、完整 source-vector hash、joint certification 和 fixed-interface status；无 source
的变体按 §5.2.1 显式 N/A。`production_dependency_status` 和
`independent_dependency_status` 必须是同一冻结 enum 的实际值，不能从 target status
反推。`source_applicability_match` 比较完整 applicability/source identity payload；
`dependency_context_match` 比较完整 dependency context 和 status payload。

#### 5.2.4 `*_match` 的精确语义

每个 `*_match` 都是在双方相应规范值通过 schema/canonical validation 后的精确比较：

- integer 与 canonical exact rational 必须精确相等，不设容差；
- vector、array 和结构必须 schema、字段集合、顺序、长度及每个元素全部精确相等；
- status、mode、applicability 和 reason 必须枚举值精确相等；
- `null` 只有双方字段都存在、双方 applicability/reason 均按 §5.2.1 精确相等时才能
  match；缺字段永不等价于 N/A；
- `processor_term_match`、`energy_term_match` 和 `fixed_point_closure_match` 比较上述
  双方实际 payload 及其完整域 digest，不能由 candidate/status 相同推导；
- `carry_in_match` 比较 mode、scope、source identity、完整有序向量、hp projection 和
  hash，不能只比较 hash；
- `witness_match` 与 `witness_h_match` 在 V1 都是双方 `witness_h` 的严格比较。

不得使用“支持相同结论”“语义一致”或“大致相同”。任一 match 为 false 时，report
仍保留双方完整规范值，并以 `first_mismatch_*` 给出可解析字段路径、`w/h/q` 等 point
identity 和双方实际差异；因此 `SCIENTIFIC_MISMATCH` 必须能从 report 本身定位首个具体
差异。外部 attestation 必须绑定 canonical 完整 report payload 的 hash，不得只绑定
总体 PASS/FAIL、summary 或 match 位。

#### 5.2.5 规模控制

V1 必需报告只保存最终值、关键分解、搜索范围、归并后的更早失败摘要、首个 mismatch
实值和规范 hashes。不要求保存每个内部 Python 对象、完整调试日志、全部 envelope
枚举行、production worker trace、文件系统清单或 package 内容。完整搜索的逐迭代细节
可作为独立、可选的 diagnostic attachment；它不是 V1 必需 schema，也不能替代 report
中的双方规范实值。这样既保留逐 task/candidate 的科学可审计性，也避免把 report 变成
生产 trace 或 package 的副本。

`critical_q` 不是当前 production schema 字段，因此 V1 不包含 `critical_q_match`，也
不得从 counter、witness、失败摘要或 optional trace 推测、伪造该字段。第 8 节所述
“首个失败 `q`”仍只是未来证书设计候选；本节 mismatch/failure point 的 `q` 是报告对已
实际比较点的索引，不声明 production 存在名为 `critical_q` 的字段。

### 5.3 失败、验收和重试状态矩阵（规范性）

| `verifier_status` | 条件 | T2 语义 | 重试规则 |
|---|---|---|---|
| `SCIENTIFIC_MATCH` | independent status/candidate/terms/closure、完整域、前缀和 dependency 均与 production 科学声明一致 | 对已完成的科学声明可计入 independently verified 范围 | 不需要重试 |
| `SCIENTIFIC_MISMATCH` | candidate、status、carry-in、processor、energy、closure、source 或完整域任一科学量不一致 | hard fail；不得签发通过 attestation | 不允许靠重复 verifier 重试变为 PASS；必须调查并形成新身份的修订结果 |
| `OPERATIONAL_UNVERIFIED` | verifier timeout、资源不足、进程失败、输入暂不可读，或 production 结果本身是 operational `TIMEOUT` | 不断言 production 错误，但不得标记 independently verified | 只有 verifier operational failure 可在同一 immutable snapshot 和同一 verifier 版本上单独重跑；production `TIMEOUT` 身份不被重写 |
| `INVALID_INPUT_OR_CONTRACT` | schema、hash、canonical encoding、identity 或 context 非法 | hard fail；不能进入科学比较 | 修复合同必须形成新的 snapshot/identity，不能原地放宽 |
| `NOT_APPLICABLE` | 仅当冻结的 source dependency/interface 语义确实不适用，且 independent replay 得出相同 applicability | 记录 N/A，不伪装成科学 PASS，也不替代资源失败 | 不因资源或进程失败产生；dependency 改变须形成新 analysis identity |

合法输入上 production `NUMERIC_ERROR` 而 independent exact replay 成功时，分类为
`SCIENTIFIC_MISMATCH`；输入自身非法时分类为 `INVALID_INPUT_OR_CONTRACT`。verifier 的
wall/CPU 时间和重试次数单独记录，不进入 production solver timeout、retry 或论文
runtime。重跑 production analyzer 必须产生新 campaign 或新 analysis identity。

## 6. 当前 T1 与未来 T2 的执行范围和离线 attestation

### 6.1 当前 T1

当前分支不运行 independent verifier。正式 CORE-1/CORE-2 依赖已冻结的代码审查、
CORE-0、最终全量回归、覆盖两条正式分析链的 non-formal production-chain canary 和
artifact closure，并按 §1.2 的 Current Formal Acceptance Policy 验收。P1-3 不参与当前
formal authorization、worker、resume、aggregation、summary、output inventory、package
或 archive；论文和发布材料不得使用“全部 analysis 已由第二实现独立重算”的表述。

### 6.2 未来 T2 的验证范围

未来 T2 必须验证所有正式 analysis，包括 `COMPLETED`、`NO_CANDIDATE`、`TIMEOUT` 和
首个失败前缀；不能只验证 certified 项，因为这会漏掉错误的 `NO_CANDIDATE`。固定比例
抽样只能形成 shadow/audit evidence，不能支持“全部正式结果已独立验证”的声明。

T2 verifier 可以在 production analyzer 完成并封存原始结果后离线运行。它不改变
production 科学结果、timeout、retry 或 runtime。只有 operational verifier failure
可以按 §5.3 单独重跑；scientific mismatch 不能通过重试消失。

### 6.3 首版离线边界（规范性）

首版 T2 数据流严格为：

```text
immutable canonical snapshot
-> offline independent replay
-> external verifier report
-> external acceptance attestation
```

首版不在 worker 返回后或 terminal 写入前运行 verifier，不介入 quarantine/resume，
不参与 aggregation、summary 或 package，也不修改当前 pre-run formal authorization
seal。当前 `ASAP_BLOCK_V9_3_OUTPUT_INVENTORY_V1` 不允许未知文件，因此 verifier
report 不得直接写入
原 formal output root；它必须位于独立 sibling directory 或形成独立 acceptance
artifact。历史 campaign 不得被原地修改。

外部 attestation 草案名称为
`ASAP_BLOCK_V9_3_INDEPENDENT_ACCEPTANCE_ATTESTATION_V1`，至少绑定：

- 原 campaign identity；
- immutable input snapshot hash；
- production output、package 和 archive hash（存在相应产物时）；
- verifier report hash；
- verifier source/version；
- acceptance policy 及版本；
- 验证范围，包括 analysis/taskset/task 数和状态覆盖；
- 最终状态及所有非 `SCIENTIFIC_MATCH` 记录的闭包。

其中 verifier report hash 必须对 §5.2 定义的 canonical 完整 report payload（包括双方
实际科学值、per-task payload 和 mismatch locator）计算；只绑定总体 PASS/FAIL、
`comparison_summary` 或一组 match 布尔值的 attestation 无效。

该 attestation 是未来 T2 的外部科学验收产物，不替换 pre-run authorization seal，不
进入当前 output/package schema，也不为历史 campaign 追写“已独立验证”标记。

## 7. 未来 T2 性能预算

当前 formal candidate 配置的规模是：10 tasks；3 utilization × 2 E0 × 50 tasksets，
即每个 core 300 个 tasksets；CORE-1 为 2 个变体（600 analyses），CORE-2 为 5 个变体
（1500 analyses），生产单次/重试预算为 30/60 秒。模板扩大到 100 tasksets/cell 时，
analysis 数量相应翻倍。

本节只用于未来 T2 容量规划，不是当前 T1 acceptance gate。方案 C 的 envelope 检查
数量与生产 RTA 同阶。无缓存、强调可读性的独立 reference
实现预计是约一次额外 RTA 到数倍 RTA，而不是常数级 CSV 检查。不能在实现前承诺固定
倍率；必须用上述 600/1500-analysis candidate grid 的只读 shadow benchmark 测量
p50/p95/max 和按状态分层的倍率。预算建议如下：

- 单 analysis verifier 设独立 wall/CPU 限额，初始容量规划按生产实际耗时的 2--5 倍，
  超限标记 `VERIFIER_RESOURCE_EXHAUSTED`，绝不视为 PASS；
- 全量成本按 2100 analyses 的实测分布求和，而不是用终态数量或 CSV 行数估算；
- taskset/variant 间可并行，`LOC_THETA_CW` 只需等待同 taskset 的 source verifier
  完成；进程数独立配置，避免与生产 worker 争用造成实验测量偏差；
- verifier 的所有 wall/CPU 时间必须排除在 solver timeout、retry 决策和论文算法 runtime
  指标之外，另存 `verifier_wall_seconds`/`verifier_cpu_seconds`；
- production run 可以先完成并封存原始输出，再在只读副本上并行执行未来 T2 验收，
  因此不会改变 timeout 统计。

若全量 shadow benchmark 表明独立 envelope 重算不可接受，可研究可验证证书或分层
抽样，但在证明方案完成前不能把抽样结果等同于全量 formal 科学闭合。

## 8. 可独立验证的证书方案（未来增强）

生产 solver 可以输出证书来减少 verifier 的搜索控制开销，但证书内容仍须从冻结输入
独立核验。建议的 task 证书包含：

- canonical 输入摘要和 exact-rational canonical encoding；
- 每个访问 `w` 的 `A(w)`，以及 `A(w)>w` 的跳过理由；
- 每个失败 `h` 的首个失败 `q`、service index、coverage index、精确 envelope/service
  值；
- candidate `w` 上 witness `h` 的全部 `q` 检查；
- `closing_w`、访问域和四类 counter；
- fixed carry-in 全向量或 recursive carry-in 的已验证前缀引用；
- NO_CANDIDATE 时覆盖 `C..D` 全域的负证书。

这些字段不是当前 production 接口，也不属于首版 V1 report。特别是“首个失败 `q`”
不是现有 `critical_q` 字段；在新的 certificate schema 和 production interface 正式
冻结前，不得增加或声称存在 `critical_q_match`。

仅给出一个使 envelope 变大的 assignment 通常只能证明下界；要证明
`envelope <= service`，还需要 verifier 自己完整求最大值，或一个可核验的上界证书
（例如完整 DP frontier/对偶界及其逐步约束）。因此 envelope 证书能否显著降成本要先
单独设计和证明，不能默认一个“closure witness”已经证明最大值正确。

证书是非可信提示：缺失、重复、域不闭合或任一值重算不符即失败，verifier 不按证书
给出的值更新真值。由同一个错误 solver 对错误 state 计算的 hash 只证明字节一致，
不是独立科学证据。

## 9. 最小实施范围、测试与迁移

当前分支不创建 verifier 实现、测试或 schema 文件。若未来项目从 T1 切换到 T2，建议
后续单独评审的最小文件范围为：

- `tools/v9_3_independent_verifier/model.py`：严格独立 decoder 和不可变输入模型；
- `tools/v9_3_independent_verifier/service_curve.py`：独立 canonical/coverage 校验；
- `tools/v9_3_independent_verifier/envelope_reference.py`：complete/local reference
  envelope；
- `tools/v9_3_independent_verifier/closure_replay.py`：独立 `w/h/q` 枚举；
- `tools/v9_3_independent_verifier/taskset_replay.py`：五变体、前缀、dependency、
  dominance；
- `tools/v9_3_independent_verifier/report.py` 与 `cli.py`：追加式 report、资源统计和
  只读 CLI；
- §5 冻结的版本化 input/report schema，以及 §6.3 的 external attestation 绑定实现；
- `test/test_v9_3_independent_verifier_*.py`：公式 microcases、五变体 golden cases、
  production/verifier differential、coherent-mutation、NO_CANDIDATE 全域、timeout/numeric、
  source/target、certificate truncation 和 import-allowlist tests。

迁移分五步：

1. 对本文的 verifier 规范、输入/输出 schema、五变体矩阵和手工推导 microcases 进行
   限定设计复核；
2. 实现完整的 B/C；A 仅可选作开发 canary，并用 mutation suite 证明 C 能发现一致
   修改的错误 candidate/status；
3. 在非 formal canary 上与生产并行 shadow，调查所有差异，不影响生产结果；
4. 对 candidate grid 做完整性能测量和只读离线演练；
5. 独立审查通过且项目明确切换到 T2 后，才为新 campaign 生成独立 sibling report 和
   external acceptance attestation；不得修改当前 pre-run authorization seal 或原 output
   root。

旧 campaign 不被静默升级。若冻结输入完整，可产生单独的追溯 verifier report；否则
必须明确标记其 trust model 不覆盖 coherent scientific mutation。

## 10. CORE-0 与未来 T2 verifier 的关系

CORE-0 和 T2 verifier 的定位不同：

| 机制 | 范围 | 作用 |
|---|---|---|
| CORE-0 | reference primitives、小域 brute-force、随机/边界/mutation、开发期 oracle | 发现组件公式实现和已知边界错误，支撑当前 T1 |
| T2 verifier | 每个正式 analysis，从 canonical input 独立 replay 完整搜索域和最终声明 | 支持“independently verified”声明及未来 T2 acceptance |

强 CORE-0 足以作为当前 T1 的组成部分，但不能证明每个正式 analysis 已由第二实现重算。
未来 T2 verifier 应以 CORE-0 的手工、小域和 mutation evidence 验证自身 reference
implementation；它可以复用这些测试思想和冻结样例，但不能复用任何决定 production
candidate 的科学函数。完整域可验证证书将来可以减少控制开销，但仅有 witness 的轻量
检查不能替代 T2 full replay。

## 11. 两阶段最终决策

### 11.1 Current decision：采用 T1

- P1-3 风险真实，但分类为 post-formal P2 enhancement；
- independent verifier 不属于当前 acceptance gate，当前分支不实现它；
- 当前 formal campaign 只有在当前修复形成 commit、通过最终独立 diff/CI 审查、完成
  merge 和重新 freeze，并通过 §1.2 的全量回归及覆盖 CORE-1/CORE-2 生产链的最终
  non-formal canary 等所有启动门槛后才可开始；本文本身不执行或授权这些操作；
- 当前论文不得声称结果已经由第二科学实现独立重算。

### 11.2 Future decision：可选 T2

- full independent replay 在当前有限整数域和 exact-rational 模式下技术上可行；
- 必须先实现并审查本文冻结的 input/report schema、五变体矩阵、状态语义和离线
  attestation；
- 必须对所有正式 analysis 全量运行，并关闭所有 mismatch/contract/operational 未验证
  记录后，才允许声称相应范围 `independently verified`；
- T2 verifier 不能证明冻结理论公式本身，也不能抵抗 verifier 与外部信任根同时被控制。

**CURRENT_T1_ACCEPTED__FUTURE_T2_FEASIBLE_WITH_BOUNDED_SCOPE**

当前 T1 决策不会把真实 P1-3 风险描述为已消失，而是明确以代码审查、CORE-0、最终
全量回归、当前 production-chain canary 和 artifact closure 接受其剩余风险。未来只有
完成并全量运行 T2 verifier，才会改变独立重算声明的边界。
