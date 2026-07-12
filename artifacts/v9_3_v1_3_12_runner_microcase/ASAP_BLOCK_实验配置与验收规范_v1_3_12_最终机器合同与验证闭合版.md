# ASAP-BLOCK 实验配置与验收规范（v1.3.12——最终机器合同与验证闭合版，正式参数待 pilot）

> 理论基线：`asap_block_rta_multicore_complete_and_local_paper_ready_v9_3_fixed_carry_in_interface(1).md`（SHA-256 `524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`）
> 适用对象：多核、全局固定优先级、可抢占、共享储能、离散时间、ASAP-BLOCK
> 文档目的：冻结正式论文实验的主结构、实现合同、状态语义、统计口径、机器可读 schema 与验收门槛。
> 当前状态：**实验结构、状态机、依赖语义和数据接口按 v1.3.12 与 v9.3 第 9.5 节对齐；正式数值与运行参数待 pilot 后填入 formal contract**。固定 carry-in 推论已集成于 v9.3；CORE-0A、pilot、正式合同冻结与 CORE-0B 仍须在正式运行前完成。

> 进入 CORE-0A 前必须预先提交：`energy_numeric_mode`、`formal_master_seed` 来源、schema/version/hash、实现版本和候选数值合同。pilot 后允许冻结的仅是 scale、整数类型、范围、容差、horizon、timeout、正式请求数和正式参数网格；不得利用 pilot 改变公式、状态机、整数化语义或 seed 来源。

---


## 0A. v1.3.12 机器合同闭合裁决

`MACHINE_INTERFACE_AUTHORITY_V1_3_12`

实验研究问题、两套正式 RTA、五配置消融、CORE-0～CORE-5 和统计主线保持不变。本版只闭合机器接口、不可变合同和验证器。规范权威分工如下：

1. 本 Markdown 规定理论对应、实验问题、放行流程和统计解释；
2. `ASAP_BLOCK_experiment_schema_v1_3_12.yaml` 规定表、字段分类、键、结构化条件规则和状态机；
3. `ASAP_BLOCK_data_dictionary_v1_3_12.yaml` 规定字段类型、单位、格式和 nullability；
4. `ASAP_BLOCK_canonical_serialization_v1_3_12.yaml` 规定 Unicode、数值、有理数、集合、CSV、哈希预像和所有稳定 ID；
5. `ASAP_BLOCK_artifact_validator_v1_3_12.py` 验证构件哈希、重复 YAML key、sidecar、跨文件字段一致性和机器合同结构；
6. `ASAP_BLOCK_acceptance_report_validator_v1_3_12.py` 验证 CORE-0A/CORE-0B gate、formal-bound evidence replay、证据哈希、批准 build 和总体放行结论；
7. `ASAP_BLOCK_result_validator_v1_3_12.py` 验证正式结果数据、条件字段、主外键、请求状态机、theorem-check 配对和 release gate。

Markdown 中字段代码块不再构成独立 schema。机器字段的完整集合必须同时出现在 schema 与 data dictionary，且由 artifact validator 检查精确相等。

本版只修复 v9.3 固定 carry-in 联合认证语义：LOC-$\Theta^{\mathrm{cw}}$ 的完整兼容向量可以获得任务集级证书，但方法角色仍为辅助消融；成功前缀保持 provisional。既有实验结构、统计主线与 run-plan 设计不变。

## 0. 最终审计结论

此前实验方案的总体方向正确。v1.3.12 不改变两套正式 RTA、五配置消融或 CORE-0～CORE-5 主线，在既有实验主结构上，进一步修复正式合同哈希预像、计划/执行状态分离、跨文档 schema 口径、复合外键谱系和验证结论范围。关键结论如下：

1. **论文正式提出的 RTA 仍然只有两套**：

   $$
   \mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
   \qquad\text{和}\qquad
   \mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}.
   $$

2. 五个配置仅用于受控消融，不能包装成五套论文主方法，也不能退回简单的三阶段线性消融。

3. **$E_0=0$ 是无条件主轨道**；任何 $E_0>0$ 的结果都必须明确标记为 `certificate-conditioned RTA`，不能把仿真初始电量、电池容量或平均电量解释为逐作业释放时证书。

4. RTA 的 soundness 与 theorem-backed tightness 对照必须使用与主定理一致的无溢出保守能量账户，或使用已认证的 usable-energy service curve。有限容量电池经验实验必须独立放置。

5. 所有旧 v20.4/v21 的正式证明率、响应时间上界、紧致度和运行时间都必须使用 v9.3 重新运行。旧数据只能作为开发历史或预实验参考。

6. v9.3 是唯一 RTA 理论基线；本合同不重复声明 RTA 代码验收结果。runtime 集成与 CORE-0 验收仍属于后续执行项。

7. **任务级求解成功不等于任务级定理认证。** 在整个正式递归任务集通过前，已找到的值只是 provisional candidates；只有任务集级 `analysis_certification_status=CERTIFIED_TASKSET` 后，相关任务才升级为 `CERTIFIED`。

8. LOC-$\Theta^{\mathrm{cw}}$ 的正式安全消融只能读取一个**完整通过且已联合认证**的 CW 任务集候选向量。CW 源运行未通过时，可以保留数值诊断，但必须标记为 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`，不得进入正式安全消融结论。

9. 对 $E_0>0$，正式 soundness 与 theorem-backed tightness 必须要求整条被报告轨迹满足 `trace_e0_certificate_status=SATISFIED_ALL`；只检查目标作业自身证书不足以进入任务集级定理覆盖域。

10. 原始数值比较与定理审计结论必须分离：`raw_bound_comparison` 只描述观测值和 candidate 的大小关系；只有 `bound_theorem_applicability=APPLICABLE` 时，超界才是 `CERTIFIED_BOUND_VIOLATION`。

11. 定理适用性必须同时检查：candidate 已认证、服务轨迹有效、$E_0$ 轨迹证书成立、储能模型匹配、调度与事件语义一致、数值合同有效。适用域外的超界只能记为诊断性 `EXCEEDS_CANDIDATE`，不能称为 RTA soundness 反例。

12. 生成、求解、联合认证、依赖来源、仿真、定理适用性、证书和轨迹有效性必须使用正交状态字段，不能压缩成一个平面 `status` 枚举。

13. 候选边界和截止期边界必须在该边界的 completion 事件处理后判定；恰好在边界完成不构成违反。

14. 能量数值实现必须采用精确有理数或统一定点有向舍入合同；定点模式必须分别记录 `actual_rho_e_raw` 与 `actual_rho_e_analysis`，参数格验收以分析实际使用值为准。

15. 正式主方法的 proven ratio 分母必须包含全部生成成功任务集；ordinary no-candidate 与 timeout 不得被静默排除。numeric error 必须单独报告，并在正式参数格中触发 `INVALID_NUMERIC_COVERAGE`，而不是伪装成普通分析未证明。

16. LOC-$\Theta^{\mathrm{cw}}$ 必须记录依赖的 CW 分析运行和完整 carry-in 向量；仿真必须记录 scheduler、事件语义、初始电量和电池参数，避免再次混淆 `initial_energy` 与 $E_0$。

17. sustainability 必须区分公式层面的 RTA-input monotonicity 与重新运行物理系统后的经验/证书结论；改变功耗后，正 $E_0$ 证书不会自动继承。

18. CORE-0 必须拆成 `CORE-0A -> pilot -> 正式合同冻结 -> CORE-0B`。pilot 后冻结的 scale、整数类型、horizon 等必须在 CORE-0B 中重新验收；若改变 fixed-point integerization 语义，则必须升级规范版本并重新执行 CORE-0A 和 CORE-0B。

19. `simulation_run_id` 只标识实际执行轨迹；服务曲线、$E_0$ 证书和分析—仿真模型匹配必须通过独立检查表按 `analysis_run_id` 关联，不能写成一条仿真轨迹的唯一固有状态。

20. 任务集级求解与任务集级认证必须正交保存；LOC-$\Theta^{\mathrm{cw}}$ 的 source 未认证时允许 diagnostic run，但不能伪装成普通分析失败或正式 taskset result。

21. 正式 CORE-1/CORE-2 要求 `NUMERIC_ERROR=0`。numeric error 是数值合同覆盖失败，不是分析保守性；非零时对应参数格作废并返回 pilot。

22. `CERTIFIED_TASKSET` 表示任务集级充分性证书，而不是“论文主方法”标签。CW-D、LOC-D，以及满足 v9.3 推论 3B 完整条件的 LOC-$\Theta^{\mathrm{cw}}$ 均可获得该状态；论文主方法与安全消融通过 `analysis_method_role` 区分。

23. 所有正式生成请求必须写入 `generation_requests.csv`；生成失败没有 `analysis_run_id`，不得伪造空分析记录。正式 seeds 必须由无循环依赖的确定性规则生成，不能人工挑选或替换。

24. `formal_contract_hash` 必须覆盖 generator contract、numerical contract、正式网格、horizon、timeout 和确定性 seed 集。固定点模式的服务曲线整数化语义冻结为 `POINTWISE_FLOOR`，pilot 不能把它当作普通数值参数改动。

25. LOC-$\Theta^{\mathrm{cw}}$ 的运行级 solver 完成度与任务级认证数量必须分别保存；依赖检查必须覆盖同一 taskset、优先级、$E_0$、服务曲线、功耗向量、数值合同和 formal contract。

26. 规范闭合搜索必须逐点执行完整的 $w/q/h$ 三层有限扫描：$w=C_k,\ldots,D_k$，$h=0,\ldots,w-A_k^\Theta(w)$，$q=1,\ldots,A_k^\Theta(w)$。除已证明安全的当前循环内提前停止外，不得跳跃、二分或依赖未经证明的 $w/h$ 判定单调性。

27. CW-D 与 LOC-D 也采用任务集级联合认证：任务集未全部闭合或固定 carry-in 推论未激活时，任何前缀 candidate 都不能成为 theorem-backed bound。

28. 定点模式必须区分目标 $E_0$ 与分析实际使用的 $E_0$。正目标值若被舍入为 0，参数格无效；论文和表格必须以 `E0_analysis_effective` 与 `realized_epsilon_0_analysis` 表述实际条件。

29. 正 $E_0$ 的正式证书集合固定为 $[0,H_{\mathrm{gen}})$ 内释放的全部作业。部分作业证书只能作为诊断，不能进入任务集级 certificate-conditioned soundness 或 theorem-backed tightness。

30. completion censoring 是纯仿真事实；candidate boundary 是否到达属于具体 `analysis_run_id` 的 bound audit。正式 CORE-3 对已认证 candidate 必须满足 `NOT_OBSERVED=0`，且 deadline boundary 必须全部可判定。

31. 在完整认证 CW 源、有效固定 carry-in 推论和一致依赖输入下，LOC-$\Theta^{\mathrm{cw}}$ 返回 `NO_CANDIDATE` 是支配性/实现一致性失败，不是普通未证明。

32. 服务曲线自身必须通过 $\beta_l(0)=0$、非负、单调不减检查；分析—仿真兼容性必须逐项覆盖初始 pending jobs、整数边界事件、自挂起、任务串行性、执行量、功耗上界、系统开销和采能因果性。

33. 所有正式样本量均以“生成请求数”定义；生成失败不得补 seed。计划中的分析、仿真和审计请求必须预先写入不可变的 `run_plan_definition.csv`；执行状态和实际输出另写入 `run_execution_log.csv`，并满足 `N_planned=N_accounted`。

34. 配对敏感性实验使用 `base_generation_cell_id` 和确定性 transformation lineage；被变形参数不得进入基础 seed 的派生键。

35. `ASAP_BLOCK_experiment_schema_v1_3_12.yaml` 是机器可读的规范索引，集中定义状态枚举、字段分类、主键、复合唯一约束和外键谱系；完整可执行约束由冻结版本的 validator 实现。Markdown 中的字段代码块是说明性摘要，不再与 YAML 的 `required` 列表构成双重权威。任何经 validator 识别的跨文档语义不一致均为 `INTERNAL_CONFORMANCE_FAILURE`。

完成本文规定的理论接口补充、实现合同、CORE-0A、pilot、正式合同冻结与 CORE-0B 后，才允许启动正式大规模实验。

## 1. 分析版本与正确的消融结构

### 1.1 两套正式 RTA

#### 正式完整窗口分析

$$
\boxed{\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}}
$$

分析任务 $\tau_k$ 时，对高优先级任务使用完整窗口分析已经得到的候选：

$$
\Theta_i=\widehat R_i^{\mathrm{cw}}.
$$

能量包络中的工作量覆盖长度使用完整候选窗口：

$$
W_i^{\Theta}(w).
$$

#### 正式局部窗口分析

$$
\boxed{\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}}
$$

分析任务 $\tau_k$ 时，对高优先级任务使用局部窗口分析已经得到的候选：

$$
\Theta_i=\widehat R_i^{\mathrm{loc}}.
$$

第 $q$ 个 processor-progress tick 的能量包络使用局部覆盖长度：

$$
W_i^{\Theta}(q+h).
$$

论文主理论证明：

$$
\widehat R_k^{\mathrm{loc}}
\le
\widehat R_k^{\mathrm{cw}}.
$$

### 1.2 五个实验配置

为分离 certified carry-in、局部窗口以及递归 carry-in 反馈的贡献，使用下列五个配置：

| 配置 | 能量窗口 | 高优先级 carry-in | 定位 |
|---|---|---|---|
| CW-D | $w$ | $D_i$ | deadline-carry-in 完整窗口基线 |
| LOC-D | $q+h$ | $D_i$ | 只加入局部窗口 |
| CW-$\Theta^{\mathrm{cw}}$ | $w$ | 完整窗口递归候选 | 正式完整窗口 RTA |
| LOC-$\Theta^{\mathrm{cw}}$ | $q+h$ | 固定使用完整窗口候选 | 纯局部窗口受控对照 |
| LOC-$\Theta^{\mathrm{loc}}$ | $q+h$ | 局部窗口递归候选 | 正式局部窗口 RTA |

#### 这五个配置不是一条简单线性链

正确关系是一个偏序结构：

$$
\mathrm{LOC}\text{-}D
\preceq
\mathrm{CW}\text{-}D,
$$

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{CW}\text{-}D,
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}},
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{LOC}\text{-}D,
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}
\preceq
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}},
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}
\preceq
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}.
$$

其中 $X\preceq Y$ 表示：在共同适用的输入上，$X$ 的候选响应时间不大于 $Y$，且 $Y$ 通过时 $X$ 不应失败。

需要特别注意：

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\quad\text{与}\quad
\mathrm{LOC}\text{-}D
$$

一般不可直接排序，因为前者只收紧 carry-in，后者只收紧能量窗口。

### 1.3 各比较对应的独立贡献

#### certified carry-in 的贡献

$$
\mathrm{CW}\text{-}D
\quad\text{vs}\quad
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}.
$$

#### 纯局部窗口贡献（deadline carry-in 不变）

$$
\mathrm{CW}\text{-}D
\quad\text{vs}\quad
\mathrm{LOC}\text{-}D.
$$

#### 纯局部窗口贡献（certified carry-in 固定不变）

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\quad\text{vs}\quad
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}.
$$

#### 局部候选递归反馈的额外贡献

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}
\quad\text{vs}\quad
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}.
$$

#### 端到端正式方法比较

$$
\boxed{
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\quad\text{vs}\quad
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}
}
$$

这是论文正文的主比较。

### 1.4 固定 carry-in 接口推论（v9.3 已集成）

v9.3 第 9.5 节已通过定理 3A、推论 3A 和推论 3B 冻结以下接口：

1. 对全部任务给定固定向量

   $$
   C_i\le \Gamma_i\le D_i,
   $$

   若固定 carry-in 分析为全部任务得到有限候选，且逐任务同时满足

   $$
   \widehat R_i^{\star\leftarrow\Gamma}\le D_i,
   \qquad
   \widehat R_i^{\star\leftarrow\Gamma}\le\Gamma_i,
   $$

   则定理 3A 为该完整候选向量提供任务集级联合认证；不存在单个成功候选的独立提前升级。

2. 在 earliest-first-violation 证明中统一取

   $$
   \Theta_i=D_i
   $$

   时，CW-D 与 LOC-D 是安全的 deadline-carry-in 消融配置。

3. LOC-$\Theta^{\mathrm{cw}}$ 的正式安全接口必须读取一个 `source_analysis_certification_status=CERTIFIED_TASKSET` 的完整 CW 分析运行。该源运行中的整个候选向量已经由 v9.3 任务集级定理联合认证；LOC-$\Theta^{\mathrm{cw}}$ 仅读取该冻结向量，其自身产生的 local candidate 不得反馈为后续任务 carry-in。

4. 若 CW 源运行没有完整通过，当前理论下不能把其中的 provisional candidates 当作正式外部认证输入。实现可以继续计算数值诊断，但必须标记：

   ```text
   DIAGNOSTIC_ONLY_NOT_CERTIFIED
   ```

   并排除出 theorem-backed 消融、soundness 和 tightness 分母。

5. 缺失完整源依赖或依赖哈希不一致时，结果记为：

   ```text
   NOT_APPLICABLE_DEPENDENCY
   ```

   该软件状态属于实验接口规范，不写入数学定理陈述。

该推论不修改 complete/local 主公式，不新增第三套正式 RTA；它已经集成于 v9.3 第 9.5 节。CORE-2 只需实现并验证本合同规定的联合认证状态机。

## 2. 所有正式实验必须遵守的 P0 条件

### 2.1 $E_0$ 的语义

理论中的

$$
E(r_J)\ge E_0
$$

必须对每个被分析作业释放时刻成立。

#### 无条件轨道

$$
\boxed{E_0=0}
$$

这一轨道必须报告，即使 proven count 为 0。

#### 条件证书轨道

$$
E_0>0
$$

必须标记为：

```text
certificate-conditioned RTA
```

必须同时保存：

```text
theorem_conditioning_mode = UNCONDITIONAL_E0_ZERO | CONDITIONAL_E0_POSITIVE
```

禁止将以下量直接解释成 $E_0$：

- 系统在 $t=0$ 的一次性初始电量；
- 电池容量；
- 某条有限轨迹中的平均电量；
- 仿真开始时的 `initial_energy`。

有限轨迹上的逐作业检查只能证明该轨迹满足证书，不能自动推广到所有合法运行。

### 2.2 无溢出模型与有限电池必须分离

RTA 主定理采用

$$
E(t+1)=E(t)-P(S_t)+H(t),
$$

即无溢出保守能量账户。

用于 RTA soundness 与 tightness 对照的仿真必须满足以下之一：

1. 使用无上限能量账户；
2. 使用足够大的容量，并验证整个仿真中从未发生饱和；
3. 使用已证明安全的 usable-energy service curve。

有限容量电池若发生溢出，会丢失历史能量，不能直接与无溢出 RTA 对照。

### 2.3 执行量和功耗必须与理论一致

正式 RTA 对照仿真采用

$$
c_J=C_i,
\qquad
p_J(t)=\hat P_i.
$$

若仿真使用 $c_J<C_i$ 或 $p_J(t)<\hat P_i$，可以用于经验性能研究，但不得用于解释最坏情况 tightness。

### 2.4 v9.3 数学主体与完整 $w/q/h$ 搜索合同

代码必须逐项实现：

- complete-window envelope；
- local-window envelope；
- complete/local 共用完全相同的
  $$
  A_k^\Theta(w);
  $$
- local workload 长度使用
  $$
  q+h;
  $$
- 当前第 $q$ 个 progress tick 可用服务长度使用
  $$
  h+q-1;
  $$
- 能量需求包含
  $$
  y_k\hat P_k.
  $$

规范候选必须按 v9.3 第 9.1、12.2 节执行完整有限扫描：

$$
\widehat R_k^\star
=
\min\left\{
 w\in[C_k,D_k]\cap\mathbb Z
 \mid
 \mathsf{Close}_k^\star(w)
\right\}.
$$

权威伪代码为：

```text
for w = C_k, C_k + 1, ..., D_k:
    A = A_k^Theta(w)
    if A > w:
        continue

    closing_witness_found = false

    for h = 0, 1, ..., w - A:
        h_is_feasible = true

        for q = 1, 2, ..., A:
            evaluate the exact envelope and the required inequality
            if the q-th inequality fails:
                h_is_feasible = false
                break  # only stop the current h

        if h_is_feasible:
            closing_witness_found = true
            record witness_h and all required witnesses
            break  # existential h found for this w

    if closing_witness_found:
        return w  # the first closing w is the normative candidate

return NO_CANDIDATE
```

允许的提前停止仅包括：

1. 某个 $h$ 的某个 $q$ 失败后，停止该 $h$ 剩余的 $q$ 检查；
2. 某个 $h$ 满足全部 $q$ 约束后，停止当前 $w$ 的其余 $h$ 检查；
3. 找到首个闭合 $w$ 后返回候选。

明确禁止：

- 跳过任何尚未检查的 $w$；
- 对 $w$ 使用跳跃扫描、二分或未证明安全的 fixed-point shortcut；
- 跳过任何尚未检查的 $h$，或假设 closure 关于 $h$ 单调；
- 只抽查部分 $q$；
- 使用旧 v21 的内层 fixed point、empty-set 语义或未经证明的 jump rule。

CORE-0A、CORE-0B、微案例、操作计数和冻结检查表必须分别验证：

```text
w_values_checked
h_values_checked
q_values_checked
first_closing_w
witness_h
critical_q
full_w_scan_conformance
full_h_scan_conformance
full_q_scan_conformance
```

### 2.5 五配置统一接口

建议使用两个正交参数：

```text
window_mode   = complete | local
carry_in_mode = deadline | cw_candidate | loc_candidate
```

正式支持的实验组合及其方法角色：

```text
complete + deadline      -> CW-D             -> AUXILIARY_ABLATION
local    + deadline      -> LOC-D            -> AUXILIARY_ABLATION
complete + cw_candidate  -> CW-Theta^cw      -> MAIN_METHOD
local    + cw_candidate  -> LOC-Theta^cw     -> AUXILIARY_ABLATION
local    + loc_candidate -> LOC-Theta^loc    -> MAIN_METHOD
```

```text
  analysis_method_role =
    MAIN_METHOD
  | AUXILIARY_ABLATION
  | DIAGNOSTIC
```

`analysis_method_role` 只描述论文定位，不决定安全性。任务集级安全性由 `analysis_certification_status` 表示；因此，在固定 carry-in 推论已生效且全部任务候选闭合时，CW-D、LOC-D 和满足推论 3B 全部依赖与兼容条件的 LOC-$\Theta^{\mathrm{cw}}$ 可以获得 `CERTIFIED_TASKSET`，但其角色仍是 `AUXILIARY_ABLATION`。

`complete + loc_candidate` 在数学上并非不安全，只是不属于本文需要的主方法或受控消融。实现可将其标记为：

```text
UNSUPPORTED_EXPERIMENT_VARIANT
```

不得把它描述为“理论非法”。

### 2.6 精确能量包络与内部一致性

正式主结果必须使用 v9.3 第 12.1 节的确定性 exact algorithm。

必须满足：

- 能量需求不得低估；
- 能量服务不得高估；
- 使用 checked integer、精确有理数，或具有证明的保守定点语义；
- 保存 energy scale、分母、舍入方向和溢出检查信息。

以下求解终止原因必须区分：

```text
NO_CANDIDATE
TIMEOUT
NUMERIC_ERROR
INTERNAL_CONFORMANCE_FAILURE
```

其中 `NO_CANDIDATE` 表示分析公式在给定截止期内未闭合；`NUMERIC_ERROR` 表示数值合同覆盖失败；`INTERNAL_CONFORMANCE_FAILURE` 表示 fast exact algorithm 与 brute force、定义式或内部断言不一致。后两者都不能伪装成普通分析未闭合。

### 2.7 事件顺序与调度语义

仿真和 RTA 必须共享：

1. 完成上一 tick 的执行；
2. 计入上一 tick 收能；
3. 释放新作业；
4. 读取边界能量；
5. 调度；
6. 执行当前 tick。

ASAP-BLOCK 必须只扫描每个任务的 eligible HOL 作业，并在首个不可支付作业处停止。

### 2.8 优先级必须完全一致

主实验采用 Deadline Monotonic，并冻结完整全序：

```text
(D_i, T_i, task_id) ascending
```

同一任务集的所有 RTA 变体和仿真必须读取同一 `priority_rank`。优先级 rank 必须写入任务集文件和 semantic hash。论文不得声称 DM 对当前模型全局最优。

## 3. 统一任务集与能量参数生成器

### 3.1 任务模型与硬后置条件

每个任务

$$
\tau_i=(C_i,T_i,D_i,\hat P_i)
$$

必须满足

$$
1\le C_i\le D_i\le T_i,
\qquad
0<\frac{C_i}{T_i}\le1.
$$

任务为 constrained-deadline sporadic sequential tasks，同任务作业不并行。

每个生成任务集还必须满足

$$
|U_{\mathrm{actual}}-U_{\mathrm{target}}|\le \varepsilon_U.
$$

其中 $\varepsilon_U$ 的最终值、单任务利用率上下界和最大重采样次数必须在 pilot 前冻结；当前 `0.01` 仅作为候选值，不是最终冻结参数。

### 3.2 处理器负载

定义归一化处理器负载

$$
\rho_P
=
\frac{1}{M}
\sum_i\frac{C_i}{T_i}.
$$

主扫描候选：

$$
\rho_P\in\{0.1,0.2,\ldots,0.9\}.
$$

边界控制点可增加 $0.95$ 和 $1.0$。生成方法优先使用 RandFixedSum。所有方法共享同一任务集，不得为不同方法重新采样。

### 3.3 周期与 WCET

主实验候选：

$$
T_i\sim\mathrm{UniformInteger}[40,200].
$$

先生成连续利用率 $u_i$，再使用 compensated rounding 得到 $C_i$。生成失败时记录：

```text
GENERATION_FAILURE
```

禁止自动修改 `task_util_min`、任务数、周期范围或其他分布参数来“救活”某个实验点。

鲁棒性附录可采用

$$
T_i\sim\mathrm{LogUniform}[10,1000].
$$

### 3.4 截止期

使用

$$
D_i
=
C_i+
\left\lfloor
\delta_i(T_i-C_i)
\right\rfloor.
$$

主实验候选为 $\delta_i=0.75$；敏感性候选为

$$
\delta\in\{0.25,0.5,0.75,1.0\}.
$$

改变 $D_i$ 后重新执行 DM 得到系统级效果；附录另做冻结 `priority_rank` 的纯 deadline 参数对照。

### 3.5 功耗异质性定义

本文统一采用：$\kappa$ 是功耗 latent 支持上界，而不是每个任务集中必然实现的实际比值。

先生成

$$
v_i\in\{1,\ldots,\kappa\},
$$

再按目标 $\rho_E$ 对整组功耗做共同缩放，得到 $\hat P_i$。每个任务集必须同时保存：

```text
kappa_support
realized_power_ratio_raw
realized_power_ratio_analysis
```

其中 raw 与 analysis-account 分别定义为

$$
\mathrm{realized\_power\_ratio}_{\mathrm{raw}}
=
\frac{\max_i\hat P_i}{\min_i\hat P_i}.
$$

$$
\mathrm{realized\_power\_ratio}_{\mathrm{analysis}}
=
\frac{\max_i P_i^{\mathrm{analysis}}}{\min_i P_i^{\mathrm{analysis}}}.
$$

有限电池归一化必须明确使用 raw 物理功耗或 analysis-account 功耗，不能混用单位。

主分布在 pilot 前固定为一种，不得在不同实验中随意切换。

为保证 $\kappa$ 敏感性是严格配对实验，每个基础任务集只生成一次冻结的 latent quantile 向量

$$
z_i\in[0,1),
$$

随后对任意 $\kappa$ 使用同一确定性映射，例如

$$
v_i^{(\kappa)}
=
1+\left\lfloor \kappa z_i\right\rfloor.
$$

该映射保持任务之间的 latent 顺序，仅允许因离散化产生并列；不得为每个 $\kappa$ 独立重新随机生成 $v_i$。实现必须冻结并保存：

```text
power_latent_seed
power_latent_vector_hash
power_latent_mapping_version
```

若采用其他映射，必须满足同一 latent 向量、确定性、跨 $\kappa$ 可追溯，并在配置中给出版本号。

### 3.6 能量负载、服务率与功耗尺度的唯一生成合同

长期能量需求率定义为

$$
P_{\mathrm{dem}}
=
\sum_i\frac{C_i}{T_i}\hat P_i,
$$

对 latency-rate 服务曲线

$$
\beta_l(\ell)=r[\ell-L]^+,
$$

定义

$$
\rho_E=\frac{P_{\mathrm{dem}}}{r}.
$$

由于同时缩放 $(\hat P_i,r)$ 不改变 $\rho_E$，仅规定“达到目标 $\rho_E$”不足以唯一确定实验输入。正式主网格必须先冻结服务率基准

$$
r=r_{\mathrm{ref}}>0,
$$

再仅缩放功耗。令

$$
u_i=\frac{C_i}{T_i},
$$

生成正 latent 功耗形状 $v_i$，并计算

$$
P_{\mathrm{dem}}^{0}
=
\sum_i u_i v_i.
$$

对目标 $\rho_E$，定义

$$
P_{\mathrm{dem}}^{\mathrm{target}}
=
\rho_E r_{\mathrm{ref}},
$$

$$
\alpha
=
\frac{P_{\mathrm{dem}}^{\mathrm{target}}}
{P_{\mathrm{dem}}^{0}},
\qquad
\hat P_i=\alpha v_i.
$$

从而在数值舍入前严格有

$$
\frac{
\sum_i(C_i/T_i)\hat P_i
}{r_{\mathrm{ref}}}
=
\rho_E.
$$

采用定点有向舍入后，必须分别重新计算并保存 `actual_rho_e_raw` 与 `actual_rho_e_analysis`；参数格验收以 `actual_rho_e_analysis` 为准，不得用目标值替代实现后的实际值。

正式参数格还必须满足预先冻结的能量负载偏差合同。精确有理数模式下，应在精确算术中满足

$$
\rho_E^{\mathrm{raw}}
=
\rho_E^{\mathrm{analysis}}
=
\rho_E^{\mathrm{target}}.
$$

定点模式下，必须冻结容差 $\varepsilon_{\rho_E}$ 及其口径，并以分析实际使用值验收：

$$
\left|
\rho_E^{\mathrm{analysis}}
-
\rho_E^{\mathrm{target}}
\right|
\le
\varepsilon_{\rho_E}.
$$

同时保存 $\rho_E^{\mathrm{raw}}$，用于量化从连续参数化到定点分析输入的偏差；不得用 $\rho_E^{\mathrm{raw}}$ 代替分析值执行参数格验收。

建议默认使用绝对容差；若使用相对容差，必须在配置中明确分母与零值处理。每个输入保存：

```text
rho_e_tolerance
rho_e_tolerance_mode = EXACT | ABSOLUTE | RELATIVE
rho_e_parameterization_status = ACCEPTED | OUT_OF_TOLERANCE | NUMERIC_ERROR
```

只有 `ACCEPTED` 才能进入对应目标 $\rho_E$ 参数格。`OUT_OF_TOLERANCE` 必须通过增大 scale、重新执行冻结的功耗参数化步骤或返回 pilot 修订数值合同解决，不能仅记录实际值后仍按目标格统计。

对 $\kappa$ 敏感性，为只改变任务之间的功耗分布而保持总能量需求和服务率不变，应从同一基础任务集构造各 $v_i^{(\kappa)}$，并进行加权归一化：

$$
\bar v_i^{(\kappa)}
=
\frac{v_i^{(\kappa)}}
{\sum_j (C_j/T_j)v_j^{(\kappa)}},
$$

$$
\hat P_i^{(\kappa)}
=
P_{\mathrm{dem}}^{\mathrm{target}}
\bar v_i^{(\kappa)}.
$$

因此不同 $\kappa$ 配置保持相同的

$$
P_{\mathrm{dem}}^{\mathrm{target}}
\quad\text{和}\quad
r_{\mathrm{ref}},
$$

只改变功耗在任务之间的分布。每个任务集必须记录：

```text
service_rate_reference
power_scale_alpha
target_power_demand
actual_power_demand_raw
actual_power_demand_analysis
target_rho_e
actual_rho_e_raw
actual_rho_e_analysis
rho_e_tolerance
rho_e_tolerance_mode
rho_e_parameterization_status
```

主扫描候选：

$$
\rho_E\in\{0.25,0.50,0.75,0.90,1.00\},
$$

另加 $1.10$ 作为长期能源过载 negative control。$r_{\mathrm{ref}}$ 的具体数值必须在 pilot 后、正式 seeds 运行前冻结，且同一配对实验中不得改变。

### 3.7 服务时延及离散整数化

主实验候选为

$$
L=4\text{ ticks}.
$$

服务时延敏感性使用目标比值

$$
\lambda_L\in\{0,0.05,0.1,0.2,0.4\}.
$$

对每个任务集定义其实际最小周期

$$
T_{\min}^{\mathrm{set}}=\min_i T_i,
$$

并将离散服务时延冻结为

$$
L(\lambda_L)
=
\left\lceil
\lambda_L T_{\min}^{\mathrm{set}}
\right\rceil.
$$

采用上取整是因为更大的 $L$ 表示不更强的服务保证，避免离散化后比声明的目标服务更乐观。必须记录：

```text
target_service_latency_ratio
realized_service_latency_L
realized_service_latency_ratio
```

其中

$$
\lambda_L^{\mathrm{actual}}
=
\frac{L(\lambda_L)}{T_{\min}^{\mathrm{set}}}.
$$

仅报告 $\rho_E$ 不足以描述服务曲线，必须同时报告 $r_{\mathrm{ref}}$、目标时延比、整数 $L$ 和实际时延比。

### 3.8 数值表示、保守舍入与实际能量负载口径

在进入 CORE-0A 前，必须预先提交并冻结 `energy_numeric_mode`，从以下两种方案中选择一种。pilot 不负责在两种模式之间挑选；若 CORE-0A 后改变模式，必须升级规范/合同版本并重新执行 CORE-0A。不得使用“只把全部能量乘以服务率 $r$ 的分母”作为一般规则。因为 $\hat P_i$、$E_0$、$r$ 与 $\beta_l$ 可能具有不同分母，仅乘单一分母通常不能将全部能量量值整数化；直接取所有分母的最小公倍数也可能导致整数膨胀和溢出。

#### 方案 A：精确有理数

所有

$$
\hat P_i,\quad E_0,\quad r,\quad \beta_l(\ell)
$$

均使用约分后的精确有理数表示，并满足：

- 分子、分母使用 checked big integer，或使用已经给出全范围证明的整数类型；
- 比较、加法和乘法不得经过普通二进制浮点近似；
- 任意溢出、分母为零或内部不一致均触发 `NUMERIC_ERROR` 或 `INTERNAL_CONFORMANCE_FAILURE`，不得返回 schedulable。

精确有理数模式下定义：

$$
P_{\mathrm{dem}}^{\mathrm{raw}}
=
\sum_i \frac{C_i}{T_i}\hat P_i,
\qquad
\rho_E^{\mathrm{raw}}
=
\frac{P_{\mathrm{dem}}^{\mathrm{raw}}}{r}.
$$

此时

$$
\rho_E^{\mathrm{analysis}}
=
\rho_E^{\mathrm{raw}}.
$$

#### 方案 B：统一定点 scale

冻结统一正整数 scale $S$，并采用有向舍入：

$$
P_i^{\mathrm{int}}
=
\left\lceil S\hat P_i\right\rceil,
$$

$$
E_{0,\mathrm{analysis}}^{\mathrm{scaled}}
=
\left\lfloor S E_{0,\mathrm{target}}^{\mathrm{raw}}\right\rfloor,
$$

$$
E_{0,\mathrm{analysis}}^{\mathrm{effective}}
=
\frac{E_{0,\mathrm{analysis}}^{\mathrm{scaled}}}{S}
\le
E_{0,\mathrm{target}}^{\mathrm{raw}}.
$$

定点分析、证书检查和论文条件参数必须使用 `E0_analysis_scaled` / `E0_analysis_effective`，不能把目标 raw 值冒充分析实际值。必须保存：

```text
E0_target_raw
E0_analysis_scaled
E0_analysis_effective
E0_rounding_error
target_epsilon_0
realized_epsilon_0_analysis
e0_parameterization_policy = EXACT_GRID | EFFECTIVE_VALUE_REPORTED
e0_parameterization_status = ACCEPTED | ROUNDED_TO_ZERO | OUT_OF_TOLERANCE | NUMERIC_ERROR
theorem_conditioning_mode = UNCONDITIONAL_E0_ZERO | CONDITIONAL_E0_POSITIVE
```

其中：

$$
E0\_rounding\_error
=
E_{0,\mathrm{target}}^{\mathrm{raw}}
-
E_{0,\mathrm{analysis}}^{\mathrm{effective}},
$$

$$
\epsilon_{0,\mathrm{target}}
=
\frac{E_{0,\mathrm{target}}^{\mathrm{raw}}}{\max_i\hat P_i},
\qquad
\epsilon_{0,\mathrm{analysis}}
=
\frac{E_{0,\mathrm{analysis}}^{\mathrm{scaled}}}{\max_i P_i^{\mathrm{int}}}.
$$

正式条件实验必须满足以下之一：

1. `EXACT_GRID`：$S E_{0,\mathrm{target}}^{\mathrm{raw}}\in\mathbb Z$；
2. `EFFECTIVE_VALUE_REPORTED`：以 $E_{0,\mathrm{analysis}}^{\mathrm{effective}}$ 和 $\epsilon_{0,\mathrm{analysis}}$ 作为论文实际参数，并满足预先冻结的 $E_0$ 舍入容差。

若 $E_{0,\mathrm{target}}^{\mathrm{raw}}>0$ 但 $E_{0,\mathrm{analysis}}^{\mathrm{scaled}}=0$，必须标记 `ROUNDED_TO_ZERO`，该请求不能进入正 $E_0$ 条件轨道，也不能伪装成 `certificate-conditioned` 结果。

$$
\beta_l^{\mathrm{int}}(\ell)
=
\left\lfloor S\beta_l(\ell)\right\rfloor.
$$

固定点模式的结构语义冻结为：

```text
energy_numeric_mode = FIXED_POINT_DIRECTED
-> service_curve_integerization_mode = POINTWISE_FLOOR
```

精确有理数模式对应：

```text
energy_numeric_mode = EXACT_RATIONAL
-> service_curve_integerization_mode = EXACT
```

因此，`energy_numeric_mode` 必须在 CORE-0A 前固定；pilot 只允许冻结 scale、整数类型、范围和容差。fixed-point 下的 `service_curve_integerization_mode=POINTWISE_FLOOR` 是结构合同，不是 pilot 参数。若改变数值模式或从 `POINTWISE_FLOOR` 改为其他整数化语义，必须升级实验规范版本并重新执行完整 CORE-0A。

在 `POINTWISE_FLOOR` 下，分析直接使用逐长度的 $\beta_l^{\mathrm{int}}(\ell)$，而不是擅自替换成未声明的整数率 $r^{\mathrm{int}}[\ell-L]^+$。对 latency-rate 原曲线，其缩放后渐近服务率为精确有理数

$$
r_{\mathrm{analysis}}^{\mathrm{scaled}}=Sr,
$$

不是默认的 $\lfloor Sr\rfloor$。若未来实现改用

$$
\beta_l^{\mathrm{int}}(\ell)=r^{\mathrm{int}}[\ell-L]^+,
\qquad
r^{\mathrm{int}}=\lfloor Sr\rfloor,
$$

则必须升级配置版本、修改 integerization mode，并按 $r^{\mathrm{int}}$ 重新计算全部分析负载指标，不能与 `POINTWISE_FLOOR` 数据混用。

定点模式分别定义：

$$
P_{\mathrm{dem}}^{\mathrm{raw}}
=
\sum_i \frac{C_i}{T_i}\hat P_i,
\qquad
\rho_E^{\mathrm{raw}}
=
\frac{P_{\mathrm{dem}}^{\mathrm{raw}}}{r},
$$

以及分析实际使用的缩放需求率

$$
P_{\mathrm{dem}}^{\mathrm{analysis}}
=
\sum_i \frac{C_i}{T_i}P_i^{\mathrm{int}},
$$

$$
\rho_E^{\mathrm{analysis}}
=
\frac{P_{\mathrm{dem}}^{\mathrm{analysis}}}
{r_{\mathrm{analysis}}^{\mathrm{scaled}}}
=
\frac{
\sum_i (C_i/T_i)P_i^{\mathrm{int}}
}{Sr}.
$$

`rho_e_parameterization_status` 和参数格容差验收必须以 $\rho_E^{\mathrm{analysis}}$ 为准；$\rho_E^{\mathrm{raw}}$ 只用于报告生成前的物理/连续参数。若 pointwise floor 在短窗口上造成额外离散损失，应通过完整 $\beta_l^{\mathrm{int}}(\ell)$ 和 energy-slack 指标体现，不能把短窗口损失伪装成单一整数率。

两种模式都必须满足：

- 能量需求向上舍入或精确表示；
- 初始供给和服务供给向下舍入或精确表示；
- 所有运算使用 checked integer / checked rational；
- 保存原始值、缩放值和每个字段的舍入方向；
- 若缩放后出现溢出风险、精度合同无法证明或实现使用了反向舍入，则返回 `NUMERIC_ERROR`，不得返回 schedulable。

正式结果必须记录：

```text
energy_numeric_mode = EXACT_RATIONAL | FIXED_POINT_DIRECTED
energy_numeric_scale
E0_target_raw
E0_analysis_scaled
E0_analysis_effective
E0_rounding_error
target_epsilon_0
realized_epsilon_0_analysis
e0_parameterization_policy
e0_parameterization_status
theorem_conditioning_mode
service_curve_integerization_mode
energy_demand_rounding = UP | EXACT
energy_supply_rounding = DOWN | EXACT
service_rate_r_raw
service_rate_r_scaled_exact
actual_rho_e_raw
actual_rho_e_analysis
numeric_integer_type
numeric_range_check_status
```

在 numerical mode 与 $S$ 真正冻结前，本规范不指定唯一数值实现，但任何实现都必须保证：**需求不低估、供给不高估、异常保守失败、参数格以分析实际值验收**。

### 3.9 生成请求事实、覆盖率与确定性 seed 合同

任务集生成是独立于 RTA 分析的阶段。每一个正式请求，无论成功还是失败，都必须写入 `generation_requests.csv`。生成失败时不存在有效 taskset，也不应伪造 `analysis_run_id` 或空的 `per_taskset_results.csv` 行。

每个生成请求至少保存：

```text
generation_request_id
run_phase
formal_contract_hash
generator_contract_hash
parameter_cell_id
base_generation_cell_id
paired_family_id
seed_scope_id
replicate_index
requested_seed
generation_status
generation_attempts
max_resampling_reached
generation_failure_reason
target_total_utilization
target_rho_p
target_rho_e
actual_total_utilization
actual_rho_p
actual_rho_e_raw
actual_rho_e_analysis
rho_e_tolerance
rho_e_tolerance_mode
rho_e_parameterization_status
taskset_id
taskset_semantic_hash
```

字段约束：

- `generation_status=GENERATION_FAILURE` 时，`taskset_id` 与 `taskset_semantic_hash` 必须为 null；
- `generation_status=SUCCESS` 时，`taskset_id` 与 `taskset_semantic_hash` 必须非空且唯一指向冻结任务集；
- `per_taskset_results.csv` 只能引用 `generation_status=SUCCESS` 的 `generation_request_id`；
- generation success rate、failure rate 和重采样统计只能从 `generation_requests.csv` 计算。

`generation_status=SUCCESS` 仅在下列条件同时成立时允许出现：

1. 任务参数满足第 3.1--3.4 节的全部后置条件；
2. `rho_e_parameterization_status=ACCEPTED`；
3. 数值范围检查通过；
4. 没有达到不可恢复的重采样或参数化失败。

若 `rho_e_parameterization_status` 为 `OUT_OF_TOLERANCE` 或 `NUMERIC_ERROR`，必须令：

```text
generation_status = GENERATION_FAILURE
```

并在 `generation_failure_reason` 中保存具体原因。此类请求计入 generation-failure coverage，不能静默删除或转入相邻 $\rho_E$ 参数格。

每个参数格必须报告：

$$
\mathrm{generation\ success\ rate}
=
\frac{N_{\mathrm{generation\ success}}}
{N_{\mathrm{generation\ requested}}},
$$

以及请求数、成功数、失败数、`generation_attempts` 分布和达到最大重采样次数的请求数。

proven ratio 仍以生成成功任务集为分析分母，但 generation coverage 必须并列报告。pilot 后、正式运行前冻结最大生成失败率 $\gamma_{\mathrm{gen}}$。若某参数格超过该门槛，则相关正式批次标记：

```text
INVALID_GENERATION_COVERAGE
```

该批结果不得进入正式结论。必须返回 pilot，形成新的配置版本和新的 formal contract，并使用新的正式 seed 集从头运行；原 seed 和输出只能保留作诊断。

#### 正式 seed 的无选择性派生

正式 seeds 不允许人工逐个挑选，也不允许因生成困难、timeout 或结果不理想而替换。为避免 `formal_contract_hash` 与 seed 集之间形成循环依赖，先计算不包含派生 seed 集和 `formal_seed_set_hash` 的：

```text
seed_derivation_context_hash
```

其覆盖 generator contract、numerical contract、分析网格、horizon、timeout、样本数、实验配置版本以及其他先验冻结字段。在 CORE-0A 前预先提交且不可根据 pilot 结果更换：

```text
formal_master_seed
formal_master_seed_source = PRECOMMITTED_CONFIG | PUBLIC_CONSTANT | PUBLIC_RANDOM_BEACON
formal_master_seed_commitment_hash
formal_seed_derivation_algorithm = SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12
```

定义 `seed_scope_id`：独立网格使用 `parameter_cell_id`，配对敏感性家族使用不包含被变形参数的 `base_generation_cell_id`。对 `seed_scope_id` 和重复编号 $j$，定义：

$$
\mathrm{seed}_{j}
=
\mathrm{UInt64BE}\!\left(
\mathrm{first8bytes}\!\left(
\mathrm{SHA256}\!\left(
\mathrm{canonical}(\mathrm{seed\_derivation\_context\_hash},
\mathrm{seed\_scope\_id},j,
\mathrm{formal\_master\_seed})
\right)
\right)
\right).
$$

其中 `UInt64BE(first8bytes(...))` 的字节序和截断规则必须进入 `formal_seed_derivation_algorithm` 版本；若具体 RNG 只接受有符号整数，则使用固定的无偏映射规则并写入数据字典。派生全部 seeds 后计算 `formal_seed_set_hash`，最后再计算包含该 seed 集哈希的 `formal_contract_hash`。这样不存在自引用循环。

必须保存：

```text
formal_master_seed
formal_master_seed_source
formal_master_seed_commitment_hash
formal_seed_derivation_algorithm
seed_derivation_context_hash
formal_seed_set_hash
```

并满足：

- `formal_master_seed` 在 CORE-0A 前预先提交，不得观察 pilot 后尝试多个 master seed；
- 不根据 pilot 的 proven ratio 或 runtime 挑选正式 seeds；
- 不替换任何生成失败的正式 seed；
- 生成失败仍保留在 `generation_requests.csv`；
- seed 派生算法、canonical serialization version 和所有哈希进入 manifest。

正式运行前还必须冻结：单任务利用率上下界、$\varepsilon_U$、最大重采样次数、$\gamma_{\mathrm{gen}}$、主功耗 latent 分布、$r_{\mathrm{ref}}$、$\rho_E$ 容差、能量数值 scale 及其对应 generator contract。


### 3.10 配对任务集、变换谱系、请求计划与独立随机子流

#### 配对敏感性任务集

CORE-4 中被变形的参数（$E_0$、$L$、$\delta$、$\kappa$ 等）不得进入基础任务集 seed 的派生键。每个配对家族必须保存：

```text
paired_family_id
base_generation_cell_id
base_taskset_id
parent_taskset_id
transformation_id
transformation_type
transformation_parameter
transformation_hash
parameter_cell_id
```

规则：

- 基础任务集只由 `base_generation_cell_id` 和 replicate index 派生；
- 各参数点从同一 `base_taskset_id` 确定性变换；
- `parameter_cell_id` 是变换后规范参数向量的 canonical hash；
- 任何随机变换必须使用冻结的 transformation substream，不能重新独立采样基础任务集；
- 变换后的任务集必须保存 `parent_taskset_id` 和 `transformation_hash`。


#### 请求计划定义、依赖 DAG 与执行日志

正式批次在任务集生成前冻结 `run_plan_definition.csv` 和 `run_plan_dependencies.csv`。下游请求不得依赖尚未知的 `taskset_id`；它们通过稳定的 generation/parent `request_id` 形成不可变 DAG。

`run_plan_definition.csv` 的规范列由 schema 冻结，核心列包括：

```text
request_id
request_type
run_phase
plan_context_hash
parameter_cell_id
replicate_index
parent_request_id
generation_request_id
analysis_variant_or_scenario
request_payload_hash
expected_output_id
expected_output_type
```

`run_plan_dependencies.csv` 以 `(request_id, dependency_request_id, dependency_role)` 为主键。两文件按 canonical CSV 规则分别哈希，再形成 `run_plan_bundle_hash`。计划预像不得包含 `formal_contract_hash`、execution status、时间戳、`actual_output_id` 或生成后才产生的 taskset 内容。

执行状态另写 `run_execution_log.csv`，主键为 `(request_id, attempt_index, execution_event_index)`。每个 attempt 必须恰有一个 `STARTED`，至多一个 terminal event；只有冻结 retry policy 允许的基础设施终止状态可以产生下一 attempt。每个 request 必须最终拥有唯一 accounting outcome。`run_execution_log.csv` 不进入 formal-contract hash。

正式批次必须满足：

$$
N_{\mathrm{planned}}=N_{\mathrm{accounted}},
$$

且所有请求均由不可变计划计数，不能从已存在结果行反推请求总数。

#### 不可变对象的自哈希预像

所有自哈希对象统一采用以下规则：

1. 先按冻结的 canonical serialization 生成预像；
2. 计算某对象的 hash 时，将该对象自身的 `*_hash` 字段排除，或规范化为 `null`；
3. 不得把执行后状态、实际输出 ID、验收结果或时间戳放入不可变合同预像；
4. 该规则适用于 `formal_contract_hash`、`generator_contract_hash`、`simulation_contract_hash`、`trace_generator_contract_hash`、`run_plan_bundle_hash` 和其他 child-contract hash；
5. hash 预像字段清单和排除字段必须进入 `hashing_contract`，由 validator 检查。

#### 随机流合同

随机流必须按标签和索引独立派生：

```text
TASK_UTILIZATION
PERIODS
POWER_LATENT
RELEASE_TRACE
HARVEST_TRACE
ADVERSARIAL_SEARCH
```

每条随机流保存 `stream_label`、`stream_index`、`derived_seed`、`generator_version`。修改某个生成步骤不得通过共享 RNG 状态改变其他随机流。

任务集生成规则进入 `generator_contract.yaml`；release/harvest/adversarial 场景规则分别进入 `trace_generator_contract.yaml` 和 `simulation_contract.yaml`，其哈希全部纳入最终 `formal_contract_hash`。


## 4. CORE-0A、pilot、CORE-0B 两级验收

# 4. CORE-0A / CORE-0B：两级理论—实现一致性验收

数值合同、服务曲线整数化、整数类型、scale、horizon、timeout 与容差会直接影响分析输入、溢出范围和验证结果。因此 CORE-0 必须拆成两个放行阶段，避免出现“pilot 后改变正式合同，但仍沿用 pilot 前验收结果”的闭环。

- **CORE-0A（pilot 前结构验收）**：在已经预先提交的 `energy_numeric_mode`、master-seed 来源和候选数值合同下验证公式、完整 $w/q/h$ 扫描、索引、状态机、exact solver 逻辑、依赖来源、事件顺序和 schema 结构。CORE-0A 通过后才允许运行 pilot。
- **pilot**：只用于估计运行成本和冻结正式参数，不得修改理论公式、状态语义或依赖规则。
- **正式合同冻结**：保持 CORE-0A 前已经固定的 numerical mode、integerization mode 和 master-seed 来源不变，冻结 scale、整数类型、$r_{\mathrm{ref}}$、$\rho_E/E_0$ 容差、horizon、timeout、正式网格、正式生成请求数和派生 seed 集，并生成唯一的 `formal_contract_hash`。
- **CORE-0B（正式参数验收）**：使用最终冻结合同重新执行所有受正式参数影响的正确性和范围检查。CORE-0B 通过后才允许进入 CORE-1～CORE-5 正式运行。

若 pilot 触发的不只是数值值变化，而是改变了 exact algorithm、integerization 语义、状态机、表结构或理论接口，则必须升级配置版本并重新执行完整 CORE-0A，不能只运行 CORE-0B。

### 4.1 公式级与调度语义微案例

至少覆盖：

1. completion / harvest / release / dispatch 顺序；
2. 当前 tick 收能不能支付当前 tick；
3. eligible HOL；
4. 同任务每 tick 至多一个作业；
5. 首个不可支付作业立即停止；
6. 低优先级不能绕过；
7. processor-progress tick；
8. energy-blocked tick；
9. 服务长度 $h+q-1$；
10. workload 长度 $q+h$；
11. 目标能量 $y_k\hat P_k$；
12. 目标完成边界恰等于候选边界时不构成违反；
13. complete/local 共用相同 $A_k^\Theta(w)$；
14. timeout、overflow 和数值异常不返回 schedulable；
15. 前缀 candidate 在任务集失败时保持 `PROVISIONAL_NOT_CERTIFIED`；
16. 定理适用域外超界只记 raw diagnostic，不触发 certified soundness violation；
17. $w=C_k,\ldots,D_k$ 逐点扫描，不发生 jump/binary search；
18. 每个 $h$ 检查全部 $q=1,\ldots,A_k^\Theta(w)$；
19. CW-D/LOC-D 的 provisional-to-certified 联合认证状态机；
20. 正目标 $E_0$ 被舍入为 0 时拒绝条件参数格；
21. 完整认证 CW 源下 LOC-$\Theta^{\mathrm{cw}}$ 的 `NO_CANDIDATE` 触发支配性失败；
22. 服务曲线合同和全部系统模型兼容性位检查。

每个案例输出逐 tick 时间线，包括能量、eligible 序列、选择前缀、执行集合、完成事件和 tick 分类。

### 4.2 exact envelope 与 brute force

对小参数实例同时运行：

1. v9.3 第 12.1 节专用 exact algorithm；
2. 直接枚举所有合法整数向量 $\mathbf y$。

要求

$$
\mathcal E_{\mathrm{fast}}
=
\mathcal E_{\mathrm{bruteforce}}.
$$

正式门槛：

- 一个预先冻结、可穷尽的有限小域，要求全部实例零差异；
- 在穷尽域之外再增加至少 $10^4$ 个随机/边界实例；
- 覆盖 $M\in\{1,2,3\}$；
- 覆盖无 hp、无 lp、$y_k=0$、$y_k>0$、总容量约束饱和、低优先级约束饱和；
- 允许差异数为 0。

### 4.3 processor term 对照

快速实现必须与定义式直接枚举完全一致：

$$
D_k^{P,\Theta}(w)
=
\max\left\{
 d\ge0
 \mid
 \sum_{i\in hp(k)}
 \min\{\bar W_{i,k}^{P,\Theta}(w),d\}
 \ge Md
\right\}.
$$

### 4.4 有限小状态反例搜索

冻结有限搜索空间，而不是笼统声明“枚举全部 sporadic 释放”。候选范围：

$$
M\le2,
\quad n\le3,
\quad C_i\le2,
\quad T_i\le5,
\quad C_i\le D_i\le T_i.
$$

必须分别定义：

$$
H_{\mathrm{gen}}^{\mathrm{enum}}
\quad\text{和}\quad
H_{\mathrm{obs}}^{\mathrm{enum}}.
$$

在 $[0,H_{\mathrm{gen}}^{\mathrm{enum}})$ 内枚举合法释放；之后停止新释放并继续执行，直到所有需要检查的候选边界与截止期均得到确定判定，或达到明确的 drain cap。

每个枚举实例必须保存：

```text
enum_status =
    COMPLETE
  | INCONCLUSIVE_DRAIN_CAP
  | INTERNAL_ERROR
```

其中：

- `COMPLETE`：所有具有数值 candidate 的目标作业均已得到确定的 `WITHIN_CANDIDATE` 或 `EXCEEDS_CANDIDATE` raw comparison，并且所有纳入截止期检查的目标作业均已得到 `MET_DEADLINE` 或 `DEADLINE_MISS` 判定；作业提前完成也可以使相应判定提前确定；
- `INCONCLUSIVE_DRAIN_CAP`：到达 drain cap 时仍存在 `raw_bound_comparison=NOT_OBSERVED` 或 `deadline_check_status=DEADLINE_NOT_REACHED`；
- `INTERNAL_ERROR`：状态转移、枚举器或 analyzer 出现内部错误。

`INCONCLUSIVE_DRAIN_CAP` 不能计入“已验证无反例”的实例。出现该状态时必须增大观察范围，或缩小并重新冻结枚举域。

搜索合同还必须冻结：

- priority 全序；
- $\hat P_i$ 的有限字母表；
- $E_0$ 的有限字母表；
- $H(t)$ 的有限字母表；
- 服务曲线家族；
- 初始账户；
- 边界 0 是否允许释放；
- sporadic 间隔枚举规则。

对具有 `task_certification_status=CERTIFIED` 且定理前提适用的作业，必须逐作业检查

$$
R_J^{\mathrm{obs}}
>
\widehat R_{\tau(J)}.
$$

禁止只检查 deadline miss。硬门槛：

$$
N_{\mathrm{CERTIFIED\_BOUND\_VIOLATION}}=0,
\qquad
N_{\mathrm{inconclusive}}=0.
$$

并且不得出现 `INTERNAL_ERROR`。

### 4.5 完整偏序与 sustainability 不变量

在共同适用域上，所有以下 violation 必须为 0：

$$
\mathrm{LOC}\text{-}D
\preceq
\mathrm{CW}\text{-}D,
$$

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{CW}\text{-}D,
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}},
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}}
\preceq
\mathrm{LOC}\text{-}D,
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}
\preceq
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}},
$$

$$
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}
\preceq
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}.
$$

仍然不设置

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}}
\quad\text{与}\quad
\mathrm{LOC}\text{-}D
$$

之间的排序门槛。

同时检查正确方向的 sustainability 变形：

- 增大 $E_0$；
- 增强 $\beta_l$；
- 降低 $\hat P_i$；
- 缩小有效 $\Theta_i$。

还必须检查相同输入、相同 seed、相同 numerical scale 下的 deterministic repeatability。


### 4.5A CORE-0 非空覆盖门槛

“零 violation”不能由空集合真空满足。CORE-0A/B 必须报告覆盖计数，并至少满足：

```text
N_exact_exhaustive_instances > 0
N_exact_random_boundary_instances >= 10000
N_certified_tasksets > 0
N_energy_blocking_cases > 0
N_positive_processor_interference_cases > 0
N_complete_local_common_cases > 0
```

若正式论文包含正 $E_0$ 条件轨道，还必须满足：

```text
N_positive_E0_satisfied_traces > 0
```

若某项因当前阶段不适用，必须在 `coverage_gate_status` 中显式标记 `NOT_APPLICABLE_WITH_JUSTIFICATION`，不能默认为通过。


### 4.6 CORE-0A 放行标准

下列任一项失败，禁止进入 pilot：

- exact envelope 与 brute force 不一致；
- processor term 不一致；
- 任一偏序反例；
- 任一 `CERTIFIED_BOUND_VIOLATION`；
- 任一 `INCONCLUSIVE_DRAIN_CAP` 或枚举 `INTERNAL_ERROR`；
- timeout 被误报为通过；
- 当前 tick 误用当前收能；
- complete/local processor term 不同；
- deterministic repeatability 失败；
- 完整 $w/q/h$ 扫描一致性失败；
- 任一非空覆盖门槛未满足；
- 服务曲线合同或系统模型兼容性检查失败；
- 出现 `INTERNAL_CONFORMANCE_FAILURE`。

### 4.7 pilot 后 generator contract 与 formal contract 冻结

pilot 结束后必须生成两个规范化合同。

#### `generator_contract.yaml`

至少冻结：

```text
generator_contract_version
generator_contract_hash
task_util_min
task_util_max
utilization_tolerance
period_distribution
period_min
period_max
deadline_delta_main
deadline_generation_rule
power_latent_distribution
power_latent_mapping_version
max_resampling_attempts
generation_failure_threshold
priority_policy
priority_tiebreak
rho_e_parameterization_rule
```

任何任务分布、后置条件、舍入、重采样或优先级生成规则变化都必须产生新的 `generator_contract_version` 和 `generator_contract_hash`。

#### `simulation_contract.yaml` 与 `trace_generator_contract.yaml`

必须冻结 scheduler/scenario 选择、事件顺序、初始能量、账户模式、release/harvest trace 生成器、随机子流、启发式搜索目标/预算/停止条件、场景请求数和版本哈希。对抗搜索的目标函数优先基于实际响应时间或 deadline stress，不得专门针对 CW 或 LOC 的某一 candidate。


#### `run_plan_definition.csv` 与 `run_execution_log.csv`

正式运行前生成全部预期请求定义并计算 `run_plan_bundle_hash`。定义文件只含不可变的请求身份、阶段、输入引用和预期输出，不含 `formal_contract_hash`、执行状态或实际输出。执行状态使用独立的 `run_execution_log.csv`，不得重新计算或改变 formal contract。

#### `formal_contract.yaml` 与 `acceptance_report.yaml`



`formal_contract_hash` 定义为完整不可变合同预像的 SHA-256。预像至少覆盖：

```text
formal_contract_version
markdown_spec_sha256
experiment_schema_sha256
validator_sha256
theory_document_sha256
fixed_carry_in_corollary_sha256
pre_core0a_commitments
generator_contract_hash
simulation_contract_hash
trace_generator_contract_hash
numeric_contract
analysis_contract
pairing_contract
sample_request_contract
seed_contract
statistics_contract
runtime_environment_contract
build_identity_requirement
formal_grid_hash
run_plan_bundle_hash
formal_seed_set_hash
```

其中 `seed_contract` 必须显式包含：

```text
formal_master_seed
formal_master_seed_source
formal_master_seed_commitment_hash
seed_derivation_algorithm
seed_derivation_context_hash
formal_seed_set_hash
```

计算预像时必须排除或置空：

```text
formal_contract_hash
所有 child contract 自身的 *_hash 字段（在计算对应 child hash 时）
CORE-0A/CORE-0B 实际验收结果
run_execution_log.csv 的内容或 hash
正式运行后产生的状态、时间戳和 actual_output_id
acceptance_report.yaml 的结果字段
```

CORE-0A/CORE-0B 的实际验收结果必须写入独立的 `acceptance_report.yaml`，由其引用不可变 `formal_contract_hash`；填写验收结果不得改变 formal contract。

在 fixed-point 模式下，`service_curve_integerization_mode` 必须是结构冻结的 `POINTWISE_FLOOR`；`energy_numeric_mode` 与整数化语义均在 CORE-0A 前预先提交。pilot 只能冻结 scale、整数类型、数值范围和容差，不能选择或切换 numerical mode，也不能无版本升级地改成另一种整数化语义。

pilot 输出不能并入正式结果。任何正式合同字段变化都必须生成新的 `formal_contract_version` 和 `formal_contract_hash`。若变化涉及算法、fixed-point integerization 语义、状态机、schema 或理论接口，还必须升级实验配置版本并重新执行完整 CORE-0A。

### 4.8 CORE-0B：正式参数冻结后的最终验收

CORE-0B 必须在最终 `formal_contract_hash` 下重新执行至少以下检查：

1. exact envelope 与 brute force 零差异；
2. processor term 与定义式扫描零差异；
3. 所有受 scale、数值 mode 或整数类型影响的公式级微案例；
4. checked integer / rational 的全范围检查和溢出边界测试；
5. `actual_rho_e_analysis` 参数化、容差与格点归属检查；
6. 服务曲线合同合法性、`POINTWISE_FLOOR` 整数化、analysis-account harvest trace 和完整有限域验证；
7. theorem-backed 仿真的能量账户与分析数值合同一致性；
8. deterministic repeatability；
9. generator contract、seed 派生和生成请求事实表一致性；
10. formal contract 的规范化可追溯性；
11. 所有正式配置下 `NUMERIC_ERROR=0` 和 `INTERNAL_CONFORMANCE_FAILURE=0`。

正式合同可追溯性采用规范化规则：

- 顶层事实/运行表 `generation_requests.csv`、`per_taskset_results.csv` 和 `simulation_taskset_summary.csv` 必须直接保存 `formal_contract_hash`；
- 子表可以不重复保存该字段，但必须通过非空强外键唯一追溯到一个顶层记录；
- 同时引用 `analysis_run_id` 和 `simulation_run_id` 的检查表必须验证二者指向同一 `formal_contract_hash`；不一致时兼容性检查失败并触发 `INTERNAL_CONFORMANCE_FAILURE`；
- manifest 必须保存所有表的外键完整性检查结果和 formal-contract lineage hash。

CORE-0B 放行门槛：

```text
core0b_status = PASSED
formal_contract_hash_match = TRUE
generator_contract_hash_match = TRUE
seed_derivation_check_status = VALID
foreign_key_lineage_status = VALID
numeric_coverage_status = VALID
```

任何失败都必须返回 pilot 或配置修订阶段。不得在 CORE-0B 失败后继续生成正式主结果。

## 5. CORE-1：两套正式 RTA 的主比较

### 5.1 核心研究问题

1. LOC 是否比 CW 证明更多任务集？
2. 两者都通过时，LOC 上界能收紧多少？
3. 收益主要出现在哪些处理器负载和能量负载区域？
4. LOC 的额外运行成本是否可接受？

### 5.2 主配置候选

主平台候选：

$$
M=4,
\qquad n=20.
$$

固定候选：

- $T_i\in[40,200]$；
- $\delta=0.75$；
- $\kappa=5$；
- $L=4$；
- DM 全序 `(D_i,T_i,task_id)`。

二维网格候选：

$$
\rho_P\in\{0.1,0.2,\ldots,0.9\},
$$

$$
\rho_E\in\{0.25,0.50,0.75,0.90,1.00\},
$$

另加 $\rho_E=1.10$ negative control。

### 5.3 轨道顺序

先运行：

$$
E_0=0.
$$

确认生成链、分析链、状态记录和输出文件非空，且分析真实执行。不得要求 $E_0=0$ 的 proven count 必须非零。

随后再运行一个或多个 $E_0>0$ 的条件证书轨道。两类轨道必须分表、分图或明确分面，不能混合计算总体 proven ratio。

### 5.4 pilot 与正式样本量

- `pilot_generation_requests_per_cell = 50`：每格固定 50 个生成请求，而不是凑满 50 个生成成功任务集；
- pilot 仅用于冻结 transition region、p95 runtime、timeout、正式样本数、正式 seeds、正式网格和运行预算；
- 正式样本量定义为 `formal_generation_requests_per_cell=N`。不得在生成失败后追加 seed 以凑满成功样本；同时报告 `requested_sample_size`、`realized_generation_success_count` 和 `realized_analysis_pair_count`；
- pilot 与正式运行使用不同 seeds。

主网格只运行两套正式方法：

$$
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}},
\qquad
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}.
$$

### 5.5 指标、分母、方法角色、求解状态与联合认证语义

正式结果必须把方法角色、任务集级求解状态和任务集级认证状态分开。

```text
  analysis_method_role =
    MAIN_METHOD
  | AUXILIARY_ABLATION
  | DIAGNOSTIC
```

```text
analysis_solver_status =
    COMPLETED
  | NO_CANDIDATE
  | TIMEOUT
  | NUMERIC_ERROR
  | NOT_APPLICABLE_DEPENDENCY
  | INTERNAL_CONFORMANCE_FAILURE
  | UNSUPPORTED_EXPERIMENT_VARIANT
```

```text
  analysis_certification_status =
    CERTIFIED_TASKSET
  | DIAGNOSTIC_ONLY_NOT_CERTIFIED
  | NOT_CERTIFIED
  | NOT_APPLICABLE
```

认证规则：

- CW-$\Theta^{\mathrm{cw}}$ 与 LOC-$\Theta^{\mathrm{loc}}$ 属于 `MAIN_METHOD`；全部任务闭合且任务集级定理适用时为 `COMPLETED + CERTIFIED_TASKSET`；
- CW-D 与 LOC-D 属于 `AUXILIARY_ABLATION`；只有固定 carry-in 推论 `ACTIVE` 且全部任务候选闭合时才为 `COMPLETED + CERTIFIED_TASKSET`；推论未生效时，即使数值 candidates 全部找到，也只能是 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`；
- LOC-$\Theta^{\mathrm{cw}}$ 属于 `AUXILIARY_ABLATION`；完整源 CW 已联合认证、依赖全部有效、全部 local candidates 找到且逐任务不大于源 CW candidate 时，为 `COMPLETED + CERTIFIED_TASKSET`。该证书只使完整向量可参与 theorem-backed paired bound comparison，不形成第三套主方法或 CW 失败时的新增证明；
- 普通闭合失败对应 `NO_CANDIDATE + NOT_CERTIFIED`；
- timeout 对应 `TIMEOUT + NOT_CERTIFIED`；
- numeric error 对应 `NUMERIC_ERROR + NOT_CERTIFIED`；
- 缺失依赖或变体不适用时为 `NOT_APPLICABLE`。

`CERTIFIED_TASKSET` 的语义是“该运行获得了有效的任务集级充分性证书”，不是“论文主方法”。主方法与安全消融由 `analysis_method_role` 区分。

#### taskset proven ratio

两套正式主方法的主图固定采用下式；固定 carry-in 推论生效后的 CW-D/LOC-D 在 CORE-2 中也使用同一分母定义：

$$
\mathrm{taskset\ proven\ ratio}
=
\frac{
N_{\mathrm{analysis\_certification\_status=CERTIFIED\_TASKSET}}
}{
N_{\mathrm{generation\ success}}
}.
$$

`taskset_proven` 若保留，必须满足硬不变量：

```text
taskset_proven ==
    (analysis_certification_status == CERTIFIED_TASKSET)
```

任何不一致均为 `INTERNAL_CONFORMANCE_FAILURE`。

pilot 阶段可以把 `NO_CANDIDATE`、`TIMEOUT` 和 `NUMERIC_ERROR` 作为诊断性未证明样本保守计入分母；但正式 CORE-1/CORE-2 参数格必须满足：

$$
N_{\mathrm{NUMERIC\_ERROR}}=0.
$$

若正式参数格出现任何 `NUMERIC_ERROR`，该参数格状态为：

```text
numeric_coverage_status = INVALID_NUMERIC_COVERAGE
```

该格不得进入正式 proven-ratio、local-only 或运行时间主结论，必须返回 pilot/配置修订后重新运行。timeout 可以作为 operational failure 保守计入正式分母并单独报告，但 numeric error 不能解释为正常分析保守性。

#### task-level 求解覆盖与认证覆盖

```text
task_solver_status =
    CANDIDATE_FOUND
  | NO_CANDIDATE
  | TIMEOUT
  | NUMERIC_ERROR
  | NOT_EVALUATED_AFTER_PREFIX_FAILURE
  | NOT_APPLICABLE_DEPENDENCY
  | INTERNAL_CONFORMANCE_FAILURE
```

```text
task_certification_status =
    CERTIFIED
  | PROVISIONAL_NOT_CERTIFIED
  | DIAGNOSTIC_ONLY_NOT_CERTIFIED
  | NOT_CERTIFIED
  | NOT_APPLICABLE
```

对所有任务集级分析（正式递归 CW/LOC，以及固定 carry-in 的 CW-D/LOC-D）统一执行联合认证：

- 若 `analysis_solver_status=COMPLETED`，全部任务均为 `CANDIDATE_FOUND`，且对应任务集级定理/推论有效，则 `analysis_certification_status=CERTIFIED_TASKSET`，所有任务升级为 `CERTIFIED`；
- 若在任务 $\tau_j$ 处出现 `NO_CANDIDATE` / `TIMEOUT` / `NUMERIC_ERROR`，此前候选均为 `PROVISIONAL_NOT_CERTIFIED`，失败任务为 `NOT_CERTIFIED`，后续任务为 `NOT_EVALUATED_AFTER_PREFIX_FAILURE + NOT_APPLICABLE`；
- CW-D/LOC-D 若全部数值闭合但 `fixed_carry_in_corollary_status!=ACTIVE`，运行级为 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`，所有候选任务为 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`；
- 不得把任何 provisional/diagnostic candidate 用于 theorem-backed soundness 或 tightness。

令 $n_s$ 为生成成功任务集 $s$ 的任务数，必须报告：

$$
\mathrm{evaluated\ prefix\ coverage}
=
\frac{N_{\mathrm{solver\ invoked\ task\ records}}}
{\sum_{s\in\mathcal S_{\mathrm{gen\ success}}}n_s},
$$

$$
\mathrm{provisional\ candidate\ coverage}
=
\frac{N_{\mathrm{CANDIDATE\_FOUND}}}
{\sum_{s\in\mathcal S_{\mathrm{gen\ success}}}n_s},
$$

$$
\mathrm{certified\ task\ coverage}
=
\frac{N_{\mathrm{CERTIFIED}}}
{\sum_{s\in\mathcal S_{\mathrm{gen\ success}}}n_s}.
$$

`provisional candidate coverage` 是求解诊断，不是 task-level proven ratio。

#### local-only 必须分解

对同一生成成功任务集，分别报告：

```text
local_only_analytical
local_only_due_to_timeout
local_only_due_to_numeric_failure
```

定义为：

- `local_only_analytical`：
  ```text
  LOC.analysis_certification_status = CERTIFIED_TASKSET
  CW.analysis_solver_status = NO_CANDIDATE
  CW.analysis_certification_status = NOT_CERTIFIED
  ```
- `local_only_due_to_timeout`：
  ```text
  LOC.analysis_certification_status = CERTIFIED_TASKSET
  CW.analysis_solver_status = TIMEOUT
  ```
- `local_only_due_to_numeric_failure`：
  ```text
  LOC.analysis_certification_status = CERTIFIED_TASKSET
  CW.analysis_solver_status = NUMERIC_ERROR
  ```

论文正文中的“LOC 新增证明任务集”只能使用 `local_only_analytical`。`local_only_due_to_timeout` 只能解释为当前计算预算下的完成差异；`local_only_due_to_numeric_failure` 使对应参数格数值覆盖无效，不能作为 LOC 理论优势。

#### 其他指标

至少报告：

1. taskset proven ratio；
2. evaluated-prefix、provisional-candidate 和 certified-task coverage；
3. 三类 local-only；
4. CW-certified/LOC-not-certified violation；
5. common-certified coverage；
6. $\Delta R_i$ 与相对收紧；
7. `NO_CANDIDATE`；
8. `TIMEOUT`；
9. `NUMERIC_ERROR`；
10. `NOT_APPLICABLE_DEPENDENCY` 和 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`（辅助分析）。

LOC-$\Theta^{\mathrm{cw}}$ 可机械记录 `taskset_proven=true`，但不作为第三套主方法绘制独立 schedulability 图。它在有效源 CW 域中的 taskset proven coverage 必须与 source CW 一致；正式结果主要报告 applicable coverage、common-certified task coverage、paired tightening、绝对/相对收紧、runtime overhead 和 dominance violation count。其任务集级证书不表示能够在 CW 失败时额外证明任务集。

对两套正式方法均为 `CERTIFIED_TASKSET` 且对应任务均为 `CERTIFIED` 的任务：

$$
\Delta R_i
=
\widehat R_i^{\mathrm{cw}}
-
\widehat R_i^{\mathrm{loc}},
$$

$$
\Delta R_i^{\mathrm{rel}}
=
\frac{
\widehat R_i^{\mathrm{cw}}
-
\widehat R_i^{\mathrm{loc}}
}{
\widehat R_i^{\mathrm{cw}}
}.
$$

报告 mean、median、p90、p95、max 和 CDF，并同时报告 common-certified coverage，避免把 provisional candidate 混入正式上界比较。

### 5.6 正文图表

1. CW proven-ratio 热图；
2. LOC proven-ratio 热图；
3. LOC-CW 提升热图；
4. 固定 $\rho_E$ 的 proven-ratio 曲线；
5. $\Delta R_i^{\mathrm{rel}}$ CDF；
6. complete/local 运行时间与完成覆盖率对比表。

## 6. CORE-2：严格受控消融

### 6.1 目的

把收益拆分为：

- certified carry-in；
- local-window；
- local recursive feedback。

### 6.2 五配置

运行：

$$
\mathrm{CW}\text{-}D,
\quad
\mathrm{LOC}\text{-}D,
\quad
\mathrm{CW}\text{-}\Theta^{\mathrm{cw}},
\quad
\mathrm{LOC}\text{-}\Theta^{\mathrm{cw}},
\quad
\mathrm{LOC}\text{-}\Theta^{\mathrm{loc}}.
$$

五个配置必须使用：

- 相同 taskset；
- 相同 priority rank；
- 相同服务曲线；
- 相同 numerical scale；
- 相同 $E_0$。

不得为不同配置重新生成任务集。

### 6.3 正式执行顺序

先完成 $E_0=0$ 的五配置消融。只有在存在明确的正 $E_0$ 证书解释后，才运行条件轨道。

消融网格与样本量先由 pilot 决定，不在本候选规范中写死。

### 6.4 LOC-$\Theta^{\mathrm{cw}}$ 的完整源认证、输入一致性与联合升级

LOC-$\Theta^{\mathrm{cw}}$ 是 `AUXILIARY_ABLATION`，不是论文第三套主方法。其任务集级证书只证明完整 local 向量可合法进入 theorem-backed paired bound comparison；由于正式适用域要求 source CW 已认证，它不能在 CW 失败时新增证明任务集。

#### 固定 carry-in 推论状态

```text
fixed_carry_in_corollary_status =
    ACTIVE
  | HASH_MISMATCH
  | NOT_APPLICABLE
```

`ACTIVE` 表示 theory-document hash 精确匹配包含第 9.5 节的 v9.3 文档，且实现声明采用 `V9_3_SECTION_9_5_FIXED_CARRY_IN_INTERFACE`；文档或接口 hash 不一致时为 `HASH_MISMATCH`。CW-D、LOC-D 与 LOC-$\Theta^{\mathrm{cw}}$ 只有在 `ACTIVE` 时可认证；两套正式递归主方法使用 `NOT_APPLICABLE`。

#### 完整源认证

正式 theorem-backed 执行必须满足：

```text
source_variant = CW-Theta^cw
source_analysis_solver_status = COMPLETED
source_analysis_certification_status = CERTIFIED_TASKSET
fixed_carry_in_corollary_status = ACTIVE
dependency_vector_check_status = VALID
```

源运行的整个候选向量必须已由 v9.3 任务集级定理联合认证。目标运行只读取冻结源向量，其 local candidate 不得反馈为后续 carry-in。

#### 全部分析输入的一致性检查

`dependency_vector_check_status=VALID` 仅在下列条件全部满足时成立：

```text
source_taskset_semantic_hash == target_taskset_semantic_hash
source_priority_rank_hash    == target_priority_rank_hash
source_E0_scaled             == target_E0_scaled
source_service_curve_hash    == target_service_curve_hash
source_power_vector_hash     == target_power_vector_hash
source_energy_numeric_mode   == target_energy_numeric_mode
source_energy_numeric_scale  == target_energy_numeric_scale
source_formal_contract_hash  == target_formal_contract_hash
source_theory_document_sha256 == target_theory_document_sha256
source_analysis_E0_canonical_hash == target_analysis_E0_canonical_hash
source_analysis_power_vector_canonical_hash == target_analysis_power_vector_canonical_hash
source_analysis_service_curve_canonical_hash == target_analysis_service_curve_canonical_hash
source_analysis_energy_unit_hash == target_analysis_energy_unit_hash
source_fixed_carry_in_corollary_hash == target_fixed_carry_in_corollary_hash
```

还必须验证完整 `carry_in_vector_hash`、每个 hp 依赖记录和 fixed-carry-in corollary hash。除非另有明确的跨表示等价证明，任一不一致均导致：

```text
dependency_vector_check_status = INVALID
analysis_solver_status = NOT_APPLICABLE_DEPENDENCY
analysis_certification_status = NOT_APPLICABLE
```

并通过 `dependency_input_failure_mask` 保存全部不一致原因。

#### 任务级 provisional 与联合升级

```text
整个 LOC-Theta^cw 任务集完成前：
  task_solver_status = CANDIDATE_FOUND
  task_certification_status = PROVISIONAL_NOT_CERTIFIED

全部任务 candidate 找到且逐任务满足
  R_i^(loc<-cw) <= Gamma_i = R_i^cw
之后统一升级：
  analysis_solver_status = COMPLETED
  analysis_certification_status = CERTIFIED_TASKSET
  task_certification_status = CERTIFIED   # 对全部任务原子升级
```

任何成功前缀都不得提前标为 `CERTIFIED`。若 source CW 未认证，可以显式运行数值诊断，但所有 candidates 只能标为 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`，不得进入 soundness、tightness 或正式 bound audit。

#### 运行级 solver 聚合与认证聚合

LOC-$\Theta^{\mathrm{cw}}$ 对预先声明的全部目标任务执行求解。若 source 未认证，该运行只是 diagnostic，普通 `NO_CANDIDATE` 可以保留为诊断结果。若 source 已为 `CERTIFIED_TASKSET`、固定 carry-in 推论为 `ACTIVE` 且依赖向量为 `VALID`，完整窗口闭合蕴含相同 carry-in 下局部窗口闭合；此时任何 `NO_CANDIDATE` 必须升级为 `DOMINANCE_INVARIANT_VIOLATION` / `INTERNAL_CONFORMANCE_FAILURE` 并立即停止受影响正式运行。timeout 表示尚未算完，numeric error 表示数值覆盖无效。运行级字段冻结为：

```text
n_tasks_total
n_tasks_evaluated
n_tasks_candidate_found
n_tasks_certified
first_non_candidate_priority
dominance_invariant_status = NOT_CHECKED | SATISFIED | DOMINANCE_INVARIANT_VIOLATION | NOT_APPLICABLE
dominance_violation_count
```

`analysis_solver_status` 聚合规则：

- 所有目标任务已评估且全部找到 candidate：`COMPLETED`；
- source 未认证的 diagnostic 运行中，所有目标任务已评估但至少一个为 `NO_CANDIDATE`：`NO_CANDIDATE`；
- source 已认证、推论有效且依赖有效时出现任一 `NO_CANDIDATE`：`INTERNAL_CONFORMANCE_FAILURE`，并记录 `dominance_invariant_status=DOMINANCE_INVARIANT_VIOLATION` 和非零 `dominance_violation_count`；
- 未完成全部目标任务且发生 timeout：`TIMEOUT`；
- 发生 numeric error：`NUMERIC_ERROR`；
- 依赖不适用：`NOT_APPLICABLE_DEPENDENCY`；
- conformance failure：`INTERNAL_CONFORMANCE_FAILURE`。

`analysis_certification_status` 独立聚合：

- source 已认证、推论有效、依赖一致、全部 candidates 找到、全部兼容且 dominance violation count 为 0：`CERTIFIED_TASKSET`，并令 `n_tasks_certified=n_tasks_total`；
- timeout 或 numeric error：`NOT_CERTIFIED`，此前 candidates 保持 `PROVISIONAL_NOT_CERTIFIED`；
- source 未认证但完成诊断：`DIAGNOSTIC_ONLY_NOT_CERTIFIED`；
- 依赖缺失或输入不一致：`NOT_APPLICABLE`。

因此，部分任务成功后发生 timeout 或 numeric error 时，已有 candidates 仍是 provisional；不得保留任何正式 task-level certified 前缀。有效依赖域中的 `NO_CANDIDATE` 不是普通分析未证明，而是支配性/实现一致性失败。

必须报告：

```text
N_source_cw_certified
N_diagnostic_only
N_not_applicable
source_certified_coverage
applicable_coverage
common_certified_task_coverage
paired_bound_tightening_absolute
paired_bound_tightening_relative
runtime_overhead
dominance_violation_count
n_tasks_total
n_tasks_evaluated
n_tasks_candidate_found
n_tasks_certified
```

纯局部窗口的正式 tightening 只在 source CW 已认证、目标任务 local candidate 已认证且依赖输入完全一致的共同域上比较。该接口不用于宣称额外 taskset-level proven cases。

### 6.5 偏序失败为硬错误

偏序检查只在共同适用、数值合同有效且相关 solver 均完成的域上执行。timeout 不构成数学反例，但必须单独报告；numeric error 使参数格数值覆盖无效。

硬错误包括：

1. CW-D 为 `CERTIFIED_TASKSET`，而同一输入的 LOC-D 在 solver 完成后为 `NO_CANDIDATE`；
2. source CW 已为 `CERTIFIED_TASKSET`、固定 carry-in 推论有效且目标任务 CW bound 已认证，但 LOC-$\Theta^{\mathrm{cw}}$ 对同一目标任务在 solver 完成后找不到 local candidate；
3. LOC-$\Theta^{\mathrm{cw}}$ 的目标任务 bound 已认证，而正式 LOC-$\Theta^{\mathrm{loc}}$ 在同一目标任务处、共同适用域内完成 solver 后找不到 candidate；
4. 任一候选响应时间违反第 4.5 节的数值偏序。

仍然不在 CW-$\Theta^{\mathrm{cw}}$ 与 LOC-D 之间设置排序门槛。

### 6.6 消融指标

- CW-D、LOC-D、正式 CW、正式 LOC 的 taskset proven ratio；
- LOC-$\Theta^{\mathrm{cw}}$ 的 applicable coverage、common-certified task coverage、paired tightening 与 runtime overhead；
- 每一步新增证明数；
- 每一步响应时间收紧；
- 每一步运行时间增量；
- 五配置偏序 violation 数；
- `NOT_APPLICABLE_DEPENDENCY` 数量。

结果表述必须分别说明 carry-in、纯局部窗口、递归反馈和端到端总收益。

## 7. CORE-3：模型一致的仿真反例搜索与紧致度

CORE-3 是专项 P0。有限仿真不能证明 soundness，但必须能够发现模型或实现反例。

### 7.1 生成时域与观察时域

必须使用两个参数：

$$
H_{\mathrm{gen}}
\quad\text{和}\quad
H_{\mathrm{obs}}.
$$

规则：

1. 仅在 $[0,H_{\mathrm{gen}})$ 内释放作业；
2. 到达 $H_{\mathrm{gen}}$ 后停止新释放；
3. 继续采能并执行 pending jobs；
4. 至少满足

   $$
   H_{\mathrm{obs}}
   \ge
   H_{\mathrm{gen}}+D_{\max}.
   $$

这里 $H_{\mathrm{obs}}$ 表示**终止观察边界**：仿真执行全部区间 $[0,H_{\mathrm{obs}})$，并在边界 $H_{\mathrm{obs}}$ 先处理区间 $[H_{\mathrm{obs}}-1,H_{\mathrm{obs}})$ 的 completion 事件和完成状态更新，然后才结束观察。该定义保证当 $H_{\mathrm{obs}}\ge b_R$ 或 $H_{\mathrm{obs}}\ge b_D$ 时，对应边界已经按冻结事件顺序完成判定。

该条件足以覆盖所有已释放作业的候选边界与截止期边界，但不保证观察到每个作业的最终完成。

### 7.2 作业级完成、candidate 数值比较与截止期状态

对作业 $J$，若对应分析运行提供数值 candidate，则定义：

$$
b_R=r_J+\widehat R_{\tau(J)},
\qquad
b_D=r_J+D_{\tau(J)}.
$$

若没有数值 candidate，则 $b_R$ 为 null，但 deadline boundary $b_D$ 仍然存在。作业事实层至少保存：

```text
completion_observation_status = COMPLETED | CENSORED_HORIZON
deadline_check_status         = MET_DEADLINE | DEADLINE_MISS | DEADLINE_NOT_REACHED
```

candidate 比较在 `simulation_bound_checks.csv` 中按具体 `analysis_run_id` 保存：

```text
raw_bound_comparison =
    WITHIN_CANDIDATE
  | EXCEEDS_CANDIDATE
  | NOT_OBSERVED
  | NO_NUMERIC_CANDIDATE
```

判定必须遵守冻结的边界事件顺序。对边界 $t=b_R$ 或 $t=b_D$，analyzer 必须先完成区间 $[t-1,t)$ 内的执行并更新完成状态，随后才能比较。因而：

- 作业恰好在区间 $[b_R-1,b_R)$ 内取得最后一个执行单位，并在边界 $b_R$ 的 completion 事件中确认完成，raw comparison 为 `WITHIN_CANDIDATE`；
- 作业恰好在区间 $[b_D-1,b_D)$ 内完成，deadline 状态为 `MET_DEADLINE`；
- 禁止在 completion 事件之前执行 candidate 或 deadline 判定。

具体规则：

- 没有数值 candidate：`NO_NUMERIC_CANDIDATE`；
- $H_{\mathrm{obs}}<b_R$：`NOT_OBSERVED`；
- 已处理边界 $b_R$ 的 completion 事件且作业仍未完成：`EXCEEDS_CANDIDATE`；
- 作业不晚于 $b_R$ 完成：`WITHIN_CANDIDATE`；
- $H_{\mathrm{obs}}<b_D$：`DEADLINE_NOT_REACHED`；
- 已处理边界 $b_D$ 的 completion 事件且作业仍未完成：`DEADLINE_MISS`；
- 作业不晚于 $b_D$ 完成：`MET_DEADLINE`。

`EXCEEDS_CANDIDATE` 只是原始数值事实。只有第 7.8 节的 `bound_theorem_applicability=APPLICABLE` 时，它才升级为 `CERTIFIED_BOUND_VIOLATION`。`CENSORED_HORIZON` 只能阻止获得完整响应时间，不能掩盖已经到达边界后确定的 raw exceed 或 deadline miss。

强制 soundness 门槛：

$$
N_{\mathrm{CERTIFIED\_BOUND\_VIOLATION}}=0,
\qquad
N_{\mathrm{APPLICABLE+NOT\_OBSERVED}}=0,
\qquad
N_{\mathrm{DEADLINE\_NOT\_REACHED}}=0.
$$

在正式 CORE-3 中，$H_{\mathrm{obs}}\ge H_{\mathrm{gen}}+D_{\max}$ 且 certified candidate 满足 $R_i\le D_i$，因此后两项非零表示 horizon/analyzer 合同失败，不是可接受的普通 `INCONCLUSIVE`。

禁止只检查 `RTA pass ∧ deadline miss`，因为可能存在

$$
\widehat R_i
<
R_i^{\mathrm{obs}}
\le
D_i.
$$

### 7.3 theorem-backed 仿真的能量账户与模型一致性

正式 soundness/tightness 仿真必须明确使用哪一组数值。不得一边用 raw 功耗和 raw 采能轨迹调度，一边用定点向上/向下舍入后的分析参数宣称模型一致。

允许两种模式。

#### 模式 A：分析一致账户

```text
simulation_energy_account_mode = ANALYSIS_CONSISTENT_ACCOUNT
```

- `EXACT_RATIONAL` 模式：调度器、账户更新、服务验证和 $E_0$ 比较均使用同一组精确有理数；
- `FIXED_POINT_DIRECTED` 模式：
  $$
  P_i^{\mathrm{int}}=\lceil S\hat P_i\rceil,
  \qquad
  H^{\mathrm{int}}(t)=\lfloor S H^{\mathrm{raw}}(t)\rfloor,
  \qquad
  E_{0,\mathrm{analysis}}^{\mathrm{scaled}}=\lfloor S E_0\rfloor,
  $$
  调度器的支付判断、账户更新、服务轨迹检查和 release-time certificate 均在同一个整数保守账户上执行。

在该模式下，theorem-backed dispatch 必须使用分析账户；raw 物理量仅作为生成来源和报告字段，不能决定执行集合。

#### 模式 B：物理账户与保守影子账户

```text
simulation_energy_account_mode = PHYSICAL_WITH_CONSERVATIVE_SHADOW
```

同时维护：

$$
E_{\mathrm{physical}}(t)
\quad\text{和}\quad
E_{\mathrm{conservative}}(t).
$$

冻结：

$$
H^{\mathrm{int}}(t)=\lfloor S H^{\mathrm{raw}}(t)\rfloor,
\qquad
P_i^{\mathrm{int}}=\lceil S\hat P_i\rceil,
$$

并要求初始时满足

$$
E_{\mathrm{conservative}}(0)
\le
\lfloor S E_{\mathrm{physical}}(0)\rfloor.
$$

实现必须逐 tick 验证支配关系：

$$
E_{\mathrm{conservative}}(t)
\le
\lfloor S E_{\mathrm{physical}}(t)\rfloor.
$$

theorem-backed ASAP-BLOCK dispatch 必须基于保守影子账户。物理账户只用于验证所选执行集合在物理轨迹上同样可支付和记录实际能量行为；若实际调度器基于物理账户选择了保守账户不会选择的作业，则该轨迹只能作为经验物理仿真，不能进入 theorem-backed soundness/tightness。

两种模式均固定：

$$
c_J=C_i.
$$

并必须保证：

- 相同 priority rank；
- 相同 eligible HOL；
- 相同 BLOCK 语义；
- 相同事件顺序；
- 无溢出账户，或已认证的 usable-energy service curve；
- 当前 tick 收能下一 tick 才可用；
- release-time certificate 使用与 theorem-backed dispatch 相同的分析/保守账户。

至少保存：

```text
simulation_energy_account_mode
energy_account_semantics_version
dispatch_energy_account
harvest_trace_raw_hash
harvest_trace_scaled_hash
power_vector_raw_hash
power_vector_scaled_hash
release_time_energy_raw
release_time_energy_scaled
shadow_dominance_check_status
```

`simulation_run_id` 必须包含所有会改变实际调度轨迹的因素，包括 `simulation_energy_account_mode`、energy scale、scaled power vector、scaled harvest trace 和 account semantics version；但 $E_0$ 阈值和待验证服务曲线若只用于事后定理检查而不参与调度，不纳入 `simulation_run_id`，由独立检查表关联。

### 7.4 释放模式

每个任务集说明性字段摘要（完整字段分类以机器可读 schema 为准）：

1. 同步密集释放；
2. 周期最密释放加随机初始偏移；
3. 多个随机 offset seeds；
4. 多个随机 sporadic seeds；
5. 面向目标响应时间的启发式偏移搜索。

由于 global-FP 不存在简单同步关键时刻，不能只跑同步释放。

### 7.5 分析账户中的服务曲线合同与轨迹验证

在检查具体 harvest trace 前，必须先验证分析服务曲线本身满足：

$$
\beta_l^{\mathrm{analysis}}(0)=0,
\qquad
\beta_l^{\mathrm{analysis}}(\ell)\ge0,
\qquad
\beta_l^{\mathrm{analysis}}(\ell+1)\ge\beta_l^{\mathrm{analysis}}(\ell).
$$

```text
service_curve_contract_status = VALID | INVALID | NOT_CHECKED
```

该检查适用于构造的 latency-rate 曲线、外部导入曲线、真实轨迹拟合曲线和缩放后的离散曲线。合同无效时不得继续产生 theorem-backed applicability。

用于 theorem-backed 审计的轨迹检查必须在分析账户的数值和单位中执行，而不是混用 raw trace 与 scaled service curve。对长度为 $H_{\mathrm{obs}}$ 的轨迹定义：

$$
\mathcal D_H
=
\left\{
(t,\ell)\in\mathbb Z_{\ge0}^2
\;\middle|\;
 t+\ell\le H_{\mathrm{obs}}
\right\}.
$$

必须验证：

$$
\sum_{s=t}^{t+\ell-1}H^{\mathrm{analysis}}(s)
\ge
\beta_l^{\mathrm{analysis}}(\ell),
\qquad
\forall (t,\ell)\in\mathcal D_H.
$$

其中：

- 精确有理数模式：$H^{\mathrm{analysis}}=H^{\mathrm{raw}}$，$\beta_l^{\mathrm{analysis}}=\beta_l^{\mathrm{raw}}$，但比较必须使用精确有理数；
- fixed-point 分析一致模式：
  $$
  H^{\mathrm{analysis}}(s)=\lfloor S H^{\mathrm{raw}}(s)\rfloor,
  \qquad
  \beta_l^{\mathrm{analysis}}(\ell)=\lfloor S\beta_l^{\mathrm{raw}}(\ell)\rfloor;
  $$
- 保守影子账户模式：使用实际驱动 theorem-backed dispatch 的 shadow-harvest 序列作为 $H^{\mathrm{analysis}}$。

$\ell=0$ 时两侧均为 0。若使用比 $O(H_{\mathrm{obs}}^2)$ 更快的验证算法，必须通过证明或 exact cross-check 证明其与上述完整有限域定义等价，不能只抽查窗口。

不满足时：

```text
service_trace_status = INVALID_SERVICE_TRACE
```

该检查结果属于具体 `(simulation_run_id, analysis_run_id)`，写入 `service_trace_checks.csv`，并保存：

```text
harvest_trace_raw_hash
harvest_trace_analysis_hash
service_curve_raw_hash
service_curve_analysis_hash
service_curve_contract_status
analysis_energy_unit
service_verification_domain
service_verification_algorithm
service_verification_equivalence_status
```

有限域验证只支持相应观察域内的 trace-conditional 结论，不得扩展成无限时域或未来所有采能运行的统一保证。

### 7.6 $E_0$ 证书：作业级、轨迹级与非空覆盖域

对 $E_0>0$ 的正式 theorem-backed 轨道，证书覆盖集合固定为：

$$
\boxed{
\mathcal J_{\mathrm{cert}}^{\mathrm{formal}}
=
\{J\mid r_J\in[0,H_{\mathrm{gen}})\}.
}
$$

即纳入该 simulation run 中正式释放时域内释放的全部作业。不能只选择目标作业或任意预先声明的子集，因为未认证的其他高/低优先级作业仍可能改变被审计作业的调度历史和 first-violation 前提。

允许另建部分证书集合用于诊断，但必须标记：

```text
certificate_scope_mode = FULL_RELEASE_SET | DIAGNOSTIC_PARTIAL_CERTIFICATE_SCOPE
```

`DIAGNOSTIC_PARTIAL_CERTIFICATE_SCOPE` 不得进入 certificate-conditioned soundness、theorem-backed tightness 或任务集级轨迹证书覆盖率。证书集合不能在观察 $E(r_J)$ 后删改。下文未加上标时的 $\mathcal J_{\mathrm{cert}}$ 均指正式集合 $\mathcal J_{\mathrm{cert}}^{\mathrm{formal}}$。

对每个 $J\in\mathcal J_{\mathrm{cert}}$，在其释放边界按冻结事件顺序读取保守账户并检查：

$$
E(r_J)\ge E_0.
$$

作业级状态：

```text
job_e0_certificate_status =
    NOT_REQUIRED
  | SATISFIED
  | NOT_SATISFIED
  | NOT_CHECKED
```

轨迹级状态：

```text
trace_e0_certificate_status =
    NOT_REQUIRED
  | SATISFIED_ALL
  | NOT_SATISFIED
  | EMPTY_E0_CERTIFICATE_SCOPE
  | NOT_CHECKED
```

状态语义必须满足：

- 当 $E_0=0$ 时，作业级与轨迹级状态均为 `NOT_REQUIRED`；
- 当 $E_0>0$ 且 $|\mathcal J_{\mathrm{cert}}|=0$ 时，必须记为 `EMPTY_E0_CERTIFICATE_SCOPE`，不得利用空集全称命题自动标记 `SATISFIED_ALL`；job satisfaction rate 记为 null / undefined；
- 当 $E_0>0$ 且覆盖集合非空时，`NOT_REQUIRED` 非法，必须逐作业得到 `SATISFIED` 或 `NOT_SATISFIED`，并据此聚合轨迹级状态；
- 只有

  $$
  |\mathcal J_{\mathrm{cert}}|>0
  \quad\land\quad
  \forall J\in\mathcal J_{\mathrm{cert}},\ E(r_J)\ge E_0
  $$

  时，轨迹级状态才是 `SATISFIED_ALL`。

同时报告：

$$
\mathrm{job\ certificate\ satisfaction\ rate}
=
\frac{
\#\{J\in\mathcal J_{\mathrm{cert}}:E(r_J)\ge E_0\}
}{
|\mathcal J_{\mathrm{cert}}|
},
$$

以及轨迹级状态和 $|\mathcal J_{\mathrm{cert}}|$。

若同一轨迹中任一 $J\in\mathcal J_{\mathrm{cert}}$ 不满足正 $E_0$，该轨迹已经离开 v9.3 任务集级定理覆盖的运行集合。此时可以保留逐作业观测和 raw candidate comparison，但整条轨迹不得进入正式 certificate-conditioned soundness 或 theorem-backed tightness 分母；不能只保留其中证书满足的目标作业并继续宣称任务集级保证。


$E_0$ 证书状态同样不是 `(simulation_run_id, job_id)` 的固有属性。相同释放时能量对不同 $E_0$ 可以得到不同结论。轨迹级结果必须写入 `e0_trace_certificate_checks.csv`，作业级结果必须写入 `e0_job_certificate_checks.csv`，并通过 `e0_certificate_check_id` 关联；`simulation_job_results.csv` 只保存释放时能量事实，不保存单一的证书判定。

### 7.7 theorem-backed tightness

正式 tightness 只对同时满足以下条件的作业计算：

1. 对应任务的

   ```text
   task_solver_status = CANDIDATE_FOUND
   task_certification_status = CERTIFIED
   ```

2. `bound_theorem_applicability=APPLICABLE`；
3. 作业最终完成已被观察；
4. 服务轨迹有效；
5. 储能账户、调度器语义、事件顺序与数值合同均满足定理前提；
6. $E_0$ 状态满足以下互斥规则：

   - $E_0=0$：

     ```text
     trace_e0_certificate_status = NOT_REQUIRED
     job_e0_certificate_status   = NOT_REQUIRED
     ```

   - $E_0>0$：

     ```text
     trace_e0_certificate_status = SATISFIED_ALL
     job_e0_certificate_status   = SATISFIED
     ```

对于 $E_0>0$，仅目标作业 `SATISFIED` 而轨迹级不是 `SATISFIED_ALL` 时，不得进入 theorem-backed tightness；该数据只能进入描述性诊断。

上述服务与证书状态必须来自 `simulation_bound_checks.csv` 引用的 `service_trace_check_id` 和 `e0_certificate_check_id`，不得从纯仿真事实表中的单一状态列读取。

随后计算：

$$
G_J^\star
=
\widehat R_{\tau(J)}^\star-R_J^{\mathrm{obs}},
$$

$$
Q_J^\star
=
\frac{\widehat R_{\tau(J)}^\star}{R_J^{\mathrm{obs}}}.
$$

必须同时报告：

- taskset certification coverage；
- certified task coverage；
- theorem-applicable job coverage；
- completion coverage；
- trace certificate coverage；
- absolute gap；
- ratio CDF；
- median、p90、p95、max；
- local 对 gap 的缩减。

可另行报告 provisional/diagnostic candidates 与观测响应时间的 raw gap，但必须明确标记为 `diagnostic-only`，不得与 theorem-backed tightness 混合。

### 7.8 定理适用域、原始数值比较与 soundness 审计

一个 candidate 是否能作为 v9.3 定理保证，不仅取决于求解器是否返回数值，还取决于联合认证和全部模型前提。必须把“是否适用”“为何不适用”“原始数值比较”和“最终 soundness 结论”分开。

#### 定理适用性

```text
bound_theorem_applicability =
    APPLICABLE
  | OUT_OF_THEOREM_SCOPE
  | NOT_CHECKED
```

当适用性不是 `APPLICABLE` 时，必须同时保存全部失败原因：

```text
applicability_failure_mask = {
    NO_CERTIFIED_CANDIDATE,
    E0_TRACE_CERTIFICATE_FAILED,
    EMPTY_E0_CERTIFICATE_SCOPE,
    INVALID_SERVICE_TRACE,
    BATTERY_MODEL_MISMATCH,
    SCHEDULER_SEMANTICS_MISMATCH,
    EVENT_ORDER_MISMATCH,
    NUMERIC_CONTRACT_INVALID,
    ANALYSIS_MODEL_MISMATCH,
    ENERGY_ACCOUNT_MISMATCH,
    DEPENDENCY_CERTIFICATION_MISMATCH,
    INITIAL_PENDING_JOBS_MISMATCH,
    NON_INTEGER_EVENT_MISMATCH,
    SELF_SUSPENSION_MISMATCH,
    TASK_SERIALITY_MISMATCH,
    EXECUTION_DEMAND_MISMATCH,
    POWER_UPPER_BOUND_MISMATCH,
    UNACCOUNTED_OVERHEAD,
    HARVEST_CAUSALITY_MISMATCH,
    SERVICE_CURVE_CONTRACT_INVALID
}
```

```text
applicability_pending_mask = {
    SERVICE_CURVE_CONTRACT_NOT_CHECKED,
    SERVICE_TRACE_NOT_CHECKED,
    E0_CERTIFICATE_NOT_CHECKED,
    COMPATIBILITY_NOT_CHECKED,
    DEPENDENCY_NOT_CHECKED
}
```

派生规则：若存在 pending check 且尚未确认任何 scope failure，则 `bound_theorem_applicability=NOT_CHECKED`、`soundness_check_status=INCONCLUSIVE`。`NOT_CHECKED` 不等于 `OUT_OF_THEOREM_SCOPE`。

`applicability_failure_mask` 是权威字段，允许同时包含多个原因。为了表格汇总可以另存：

```text
primary_applicability_failure_reason
```

其选择按上述枚举顺序的固定优先级生成，但主原因不能替代完整 mask。

`APPLICABLE` 仅在以下条件全部成立时允许：

- `task_certification_status=CERTIFIED`；
- 对 $E_0=0$，对应 `e0_certificate_check_id` 的轨迹证书为 `NOT_REQUIRED`；
- 对 $E_0>0$，对应 `e0_certificate_check_id` 的轨迹证书为 `SATISFIED_ALL`，且目标作业证书为 `SATISFIED`；
- 对应 `service_trace_check_id` 的服务曲线合同与轨迹状态均为 `VALID`；
- 对应 `compatibility_check_id` 的电池模型、scheduler semantics、event order、numeric contract、analysis model 和 energy-account match 全部通过；
- 系统从边界 0 开始且无模型外初始 pending jobs；
- 释放、抢占和迁移只在整数边界发生，无自挂起，同任务作业串行且不并行；
- 每个选中作业每 tick 执行一个原子单位，实际执行需求和单位能耗不超过分析值；
- 所有未建模开销已保守计入 $C_i$、$P_i$ 或从 $H(t)$ 扣除；
- 收能过程外生，或同一服务曲线对全部相关调度轨迹统一成立；
- 执行量、功耗向量、优先级和依赖向量与分析输入一致。

观察时域是否到达 candidate boundary 不是定理适用性，而是观测充分性，因此不写入 failure mask。

#### 原始数值比较

只要存在数值 candidate，即使它仍是 provisional 或定理适用域不成立，也可以做诊断性比较：

```text
raw_bound_comparison =
    WITHIN_CANDIDATE
  | EXCEEDS_CANDIDATE
  | NOT_OBSERVED
  | NO_NUMERIC_CANDIDATE
```

该字段只陈述数值事实，不自动形成 soundness 结论。

#### 定理审计结论

```text
soundness_check_status =
    WITHIN_CERTIFIED_BOUND
  | CERTIFIED_BOUND_VIOLATION
  | OUT_OF_THEOREM_SCOPE
  | INCONCLUSIVE
```

派生规则：

- `APPLICABLE` 且 `WITHIN_CANDIDATE`：`WITHIN_CERTIFIED_BOUND`；
- `APPLICABLE` 且 `EXCEEDS_CANDIDATE`：`CERTIFIED_BOUND_VIOLATION`，立即停止受影响正式运行；
- `APPLICABLE` 且 `NOT_OBSERVED`：`INCONCLUSIVE`；
- applicability 为 `OUT_OF_THEOREM_SCOPE`：`OUT_OF_THEOREM_SCOPE`，无论 raw comparison 是否超界；
- `NO_NUMERIC_CANDIDATE`：通常为 `OUT_OF_THEOREM_SCOPE`，不得执行正式 bound 审计。

强制门槛：

$$
N_{\mathrm{CERTIFIED\_BOUND\_VIOLATION}}=0.
$$

适用域外的 `EXCEEDS_CANDIDATE` 必须保留并调查，但不能称为 RTA soundness violation。

#### deadline 行为

无论定理是否适用，仍可独立报告：

```text
deadline_check_status = MET_DEADLINE | DEADLINE_MISS | DEADLINE_NOT_REACHED
```

`MET_DEADLINE` 只说明有限轨迹中的观测行为未超期，不能反向证明分析 soundness 或 candidate 已认证。

## 8. CORE-4：参数敏感性与 sustainability

所有敏感性实验必须采用配对变形：从同一基础任务集派生新输入，而不是每个参数点重新独立生成任务集。

### 8.1 $E_0$ 敏感性

仅改变证书值：

$$
\epsilon_0
=
\frac{E_0}{\max_i\hat P_i}.
$$

候选扫描：

$$
\epsilon_0\in\{0,0.25,0.5,1,2\}.
$$

除 0 外全部标记为 certificate-conditioned。

### 8.2 服务时延

保持任务集、功耗 latent vector、长期服务率 $r_{\mathrm{ref}}$ 和 $\rho_E$ 不变，仅改变目标服务时延比：

$$
\lambda_L\in\{0,0.05,0.1,0.2,0.4\}.
$$

对每个任务集使用第 3.7 节冻结的

$$
T_{\min}^{\mathrm{set}}=\min_i T_i,
\qquad
L(\lambda_L)=\lceil\lambda_L T_{\min}^{\mathrm{set}}\rceil,
$$

并同时报告目标比值、整数 $L$ 与实际比值 $L/T_{\min}^{\mathrm{set}}$。

### 8.3 截止期紧度

从相同 $C_i,T_i$ 派生不同 $D_i$：

$$
\delta\in\{0.25,0.5,0.75,1.0\}.
$$

必须分成两类：

1. 系统级敏感性：改变 $D_i$ 后重新计算 DM；
2. 固定优先级敏感性：先在预先冻结的参考截止期配置

   $$
   \delta_{\mathrm{ref}}=0.75
   $$

   下按 DM 生成 `priority_rank_reference`，随后改变 $D_i$ 但始终读取该参考排序。不得把当前参数点、最紧截止期点或运行时首次出现的排序解释为“原 `priority_rank`”。

必须保存：

```text
priority_reference_delta
priority_rank_reference_hash
```

正文可采用系统级结果，固定优先级结果用于因果归因。若 pilot 后改变 $\delta_{\mathrm{ref}}$，必须升级配置版本并重新生成全部配对结果。

### 8.4 功耗异质性

使用同一基础任务集和第 3.5 节冻结的同一 latent quantile 向量 $\mathbf z$ 构造不同 $\kappa$ 的 latent 形状；不得为各 $\kappa$ 独立抽样。随后严格使用第 3.6 节的加权归一化，使不同 $\kappa$ 下保持相同的

$$
P_{\mathrm{dem}}^{\mathrm{target}}
\quad\text{和}\quad
r_{\mathrm{ref}},
$$

而不是只笼统声明“保持相同 $\rho_E$”。候选：

$$
\kappa\in\{1,2,5,10\}.
$$

$\kappa$ 统一解释为分布支持上界，同时报告每个任务集的 `realized_power_ratio`、`actual_power_demand_raw`、`actual_power_demand_analysis`、`actual_rho_e_raw` 与 `actual_rho_e_analysis`。

### 8.5 sustainability：公式单调性与物理系统实验分离

必须区分两类完全不同的实验。

#### FORMULA_ONLY_MONOTONICITY

该模式只检查 RTA 数学映射的单调性，不声称变换后的 $E_0$、$\beta_l$、$\hat P_i$ 或 $\Theta_i$ 已获得新的物理/定理证书。theorem-backed sustainability 必须对变换后的每一项前提重新认证。

固定一个基准数值输入，分别在公式层面构造更大的 $E_0$、更强的 $\beta_l$、更小的 $\hat P_i$ 或更小的 $\Theta_i$，仅检查 RTA 数学映射的方向性。该测试**不声称变换后的输入已经获得新的物理或定理证书**：增大 $E_0$ 需要新的释放能量证书，增强 $\beta_l$ 需要新的服务保证，减小 $\hat P_i$ 需要重新确认其仍是实际功耗上界，减小 $\Theta_i$ 需要重新认证其仍是响应时间上界。

记录原闭合候选是否仍闭合、响应时间改善幅度和任何公式单调性违反；理论上公式层 violation 数必须为 0。只有在新输入的所有证书重新成立后，才允许将结果升级为 theorem-backed sustainability。

#### physical-system sustainability experiment

若要解释真实调度系统在降低功耗、增强采能或改变其他物理参数后的行为，必须：

1. 重新运行调度仿真；
2. 重新验证服务曲线；
3. 对 $E_0>0$ 重新或独立认证整条新轨迹的 release-time energy certificate；
4. 重新检查无溢出/usable-energy、调度器语义和事件顺序前提。

降低 $\hat P_i$ 可能使 ASAP-BLOCK 更早执行更多工作，从而改变后续电池状态。旧系统中的正 $E_0$ 证书不会自动继承到新轨迹。不得把 RTA-input monotonicity test 直接表述为“物理系统必然改善”。

### 8.6 可选：功耗—优先级相关性

附录可比较：

1. 功耗与优先级独立；
2. 高优先级功耗更高；
3. 高优先级功耗更低。

该实验解释 BLOCK 机制，不是主论文必需图。

## 9. CORE-5：运行时间、内存和可扩展性

运行时间实验必须分轴进行，不能同时改变 $M,n,D_{\max}$。

### 9.1 随任务数

固定 $M=4$ 与时间尺度，扫描：

$$
n\in\{10,20,40,80\}.
$$

### 9.2 随核心数

保持任务密度

$$
n=5M
$$

并扫描

$$
M\in\{2,4,8,16\}.
$$

保持归一化 $\rho_P$ 与 $\rho_E$ 不变。

### 9.3 随截止期数值或时间尺度

固定 $M=4,n=20$，分别测试不同 $D_{\max}$，或对基础任务集统一缩放：

$$
(C_i,T_i,D_i,L)\mapsto s(C_i,T_i,D_i,L),
\qquad
s\in\{1,2,4,8\}.
$$

利用率和能量比保持不变。

### 9.4 timeout、右删失与适用覆盖

不得从 runtime 样本中静默删除 timeout。必须同时报告：

- completion ratio；
- timeout ratio；
- timeout threshold；
- 每个方法的适用样本数；
- 双方法均完成的 paired runtime coverage。

若 timeout ratio 为 0，可以按通常方式报告经验 runtime 分位数。若 timeout 非零，只在完成实例上计算的 median/p90/p95 必须明确标记为 `completed-cases-only`，它们是条件于在 timeout 内完成的统计，不能代表无条件运行时间分布，也不能单独据此宣称某方法整体更快。

pilot 后、正式运行前应冻结一个需要启动右删失分析的 timeout-ratio 门槛。达到该门槛时，除完成样本分位数外，还应增加适用于行政性右删失的分析，例如 Kaplan--Meier 完成时间曲线，以及截至 timeout threshold 的 restricted mean runtime。只在双方法均完成样本上比较 paired runtime 时，必须同时报告共同覆盖率和 timeout 差异。

### 9.5 样本量与分位数

pilot 每点样本较少时，只报告 median、p90 或 p95、max 和 timeout。

正式 runtime 若每点仅 100–200 个任务集，报告 p50、p90、p95 和 max。只有单配置样本量接近或达到 1000 时，才将 p99 作为稳定主指标；否则只能标为经验 p99。

### 9.6 必须记录的运行指标

```text
envelope_call_count
h_values_checked
q_values_checked
closing_w
witness_h
critical_q
peak_rss
cpu_time
wall_time
```

还应按 `analysis_certification_status` 和 `analysis_solver_status` 分层报告运行时间，例如 certified、ordinary no-candidate、timeout 和 diagnostic-only 实例，并报告 LOC/CW runtime ratio。


### 9.6A runtime 环境合同

正式 runtime 运行必须冻结并记录：

```text
thread_count
cpu_affinity
cpu_governor
turbo_policy
warmup_runs
measurement_repetitions
run_order_randomization
cache_policy
rss_measurement_method
runtime_environment_hash
```

CW/LOC 的运行顺序必须按 taskset 配对并随机化或平衡；timeout 非零时的 survival/RMST 比较也保持 taskset 配对。


### 9.7 正常模式与完整扫描微基准

正常模式保留安全 early stop，用于实际运行时间。另增加少量强制完整扫描微基准，用于展示理论高阶复杂度与内部操作数量。

### 9.8 实现优化消融

可以比较：

1. 直接实现；
2. workload 预计算；
3. $\hat P_i$ 预排序和前缀缓存；
4. 重用 $q+h$ 的局部工作量；
5. complete/local 共享中间量；
6. 全部优化。

优化不得改变 exact envelope、完整 $h$ 搜索或数值保守性。

## 10. 外部参考线与基线

### 10.1 processor-only 参考

忽略能量约束，只计算多核 processor-progress 分析。

它是“能量无限充足时的乐观参考”，不是有效的 EH schedulability test。由于 processor-only 参考只依赖 $(C_i,T_i,D_i,M)$，不存在 raw/analysis 能量单位之分；raw/analysis 区分仅适用于能量必要条件。

### 10.2 必要条件参考

至少报告：

$$
\sum_i\frac{C_i}{T_i}\le M,
$$

$$
\sum_i\frac{C_i}{T_i}\hat P_i\le r
\qquad\text{(raw reference)},
$$

定点正式结果还必须报告与分析账户一致的必要条件：

$$
\sum_i\frac{C_i}{T_i}P_i^{\mathrm{int}}\le Sr
\qquad\text{(POINTWISE_FLOOR 的长期 analysis-account reference)}.
$$

这些条件不能证明任务集可调度，只用于标识明显不可行区域。

### 10.3 退化场景 sanity check

1. 能量极充足时，CW/LOC 应接近 processor-only 结果；
2. $M=1$ 时，与兼容的单核 EH-RTA 行为做退化检查；
3. 若任务模型和服务模型不完全一致，不得把该退化检查写成直接性能优劣比较。

目前没有与“多核 GFP＋共享储能＋ASAP-BLOCK”完全相同的现有 RTA，因此不应强行构造不公平的数值对比。

---

## 11. EXT-1：九种调度算法比较

该实验评价调度行为，不承担 RTA soundness 证明责任。

比较：

$$
\{\mathrm{ASAP},\mathrm{ALAP},\mathrm{ST}\}
\times
\{\mathrm{BLOCK},\mathrm{NONBLOCK},\mathrm{SYNC}\}.
$$

### 11.1 三个能量区域

#### abundant-energy

用于验证语义等价：

$$
\mathrm{ASAP\!\text{-}BLOCK}
=
\mathrm{ASAP\!\text{-}NONBLOCK}
=
\mathrm{ASAP\!\text{-}SYNC},
$$

$$
\mathrm{ST\ family}
=
\mathrm{ASAP\ family}.
$$

ALAP 的三个 conflict policy 在无能量不足时也应一致，但 ALAP 不必与 ASAP 相同。

#### transition region

主要比较区。必须有足够的：

- energy-block events；
- NONBLOCK bypass；
- SYNC batch stall；
- ST charge-hold。

#### severe starvation

用于观察算法退化和 deadline miss 行为。

### 11.2 指标

- taskset acceptance ratio；
- job deadline-miss ratio；
- 最大/平均响应时间；
- energy-blocked ticks；
- processor idle ticks；
- bypass 次数；
- sync stall 次数；
- ST hold 次数；
- preemption；
- migration；
- scheduler overhead；
- 平均/最低电池能量；
- overflow energy；
- harvested-energy utilization。

### 11.3 有限电池

九调度器经验比较可以使用有限电池，但必须与 RTA 主实验分开。

对多核系统，主归一化使用单个满载 tick 的最大保守扣能：

$$
P_{\mathrm{tick}}^{\max}
=
\max_{S\subseteq\tau,\ |S|\le M}
\sum_{\tau_i\in S}\hat P_i.
$$

由于 $\hat P_i>0$，该值等于功耗最大的 $\min\{M,n\}$ 个任务的 $\hat P_i$ 之和。主容量尺度报告：

$$
\frac{B_{\max}}{P_{\mathrm{tick}}^{\max}},
$$

其含义是电池最多可支付多少个“最坏满载多核 tick”。候选扫描值可在 pilot 后冻结，例如：

$$
\frac{B_{\max}}{P_{\mathrm{tick}}^{\max}}
\in
\{1,2,5,10\}.
$$

同时可以保留辅助单任务尺度：

$$
\frac{B_{\max}}{\max_i\hat P_i},
$$

但必须明确它只表示可容纳多少个最大单任务执行 tick，不能替代多核满载支付能力，也不宜单独用于不同 $M$ 或高异质性配置的横向解释。

旧九调度器曲线只有在最终语义和代码未改变且硬等价性重新通过后才能复用；更稳妥的做法是全部重跑。

---

## 12. EXT-2：真实能量轨迹案例

建议至少选择：

1. 稳定高供能；
2. 强波动/云遮挡；
3. 低供能。

### 12.1 服务曲线提取

对有限轨迹：

$$
\beta_{\mathrm{trace}}(\ell)
=
\min_t
\sum_{s=t}^{t+\ell-1}H(s).
$$

可进一步拟合保守 latency-rate 下界：

$$
\beta_l(\ell)=r[\ell-L]^+.
$$

建议将数据拆分为 calibration 和 held-out validation 两段。

### 12.2 结论边界

- 在同一轨迹上构造并使用下包络：`same-trace conditional`（同轨迹条件结果）；
- 在 held-out 轨迹验证：经验泛化；
- 未经物理或统计认证，不能宣称未来所有天气下的确定性硬实时保证。

### 12.3 输出

- 原始采能轨迹；
- 服务曲线；
- CW/LOC proven ratio；
- 观测响应时间与 bound ratio；
- 电量轨迹；
- processor-progress / energy-blocked tick；
- 一个代表任务集的完整时间线。

---

## 13. EXT-3：有限电池 RTA（可选）

只有在构造出 certified usable-energy service curve 后，有限电池结果才能作为正式 RTA 结论。

否则只能报告调度器经验结果：

- 容量；
- 溢出量；
- starvation；
- deadline miss；
- 平均电量；
- 九算法差异。

禁止把有限电池原始采能曲线直接代入无溢出定理。

---

## 14. EXT-4：生成器与优先级鲁棒性（附录）

### 14.1 生成器

比较：

- RandFixedSum；
- UUniFast-Discard。

### 14.2 周期分布

比较：

- Uniform $[40,200]$；
- LogUniform $[10,1000]$。

### 14.3 优先级

比较：

- DM；
- RM；
- 随机固定优先级；
- 可选的启发式优先级搜索。

不得宣称其中任何一种对当前模型最优，除非另有证明。

---

## 15. 统计方法与样本独立性

### 15.1 比例置信区间

proven ratio 和 acceptance ratio 使用 Wilson 95% confidence interval，不使用少量 seed 均值的简单正态区间。

### 15.2 配对比较：operational 与 analytical 分层

全部方法使用相同任务集，但必须区分两类研究问题。

#### operational comparison

回答“在冻结的正式 timeout 与资源预算下，哪种方法实际完成并认证了更多任务集”。分母包括全部生成成功且 numeric coverage 有效的配对任务集；timeout 按未认证保守计入。报告：

- `CERTIFIED_TASKSET` / 非认证的 McNemar test；
- timeout 差异与完成覆盖率；
- operational local-only。

#### analytical comparison

回答“在双方求解均正常结束时，哪种分析本身的证明能力更强”。仅纳入：

```text
numeric_coverage_status = VALID
双方 analysis_solver_status in {COMPLETED, NO_CANDIDATE}
无 dependency error
无 INTERNAL_CONFORMANCE_FAILURE
```

其中一方 `CERTIFIED_TASKSET`、另一方 `NO_CANDIDATE + NOT_CERTIFIED` 才表示 analytical-only。正文关于理论增强和“新增证明任务集”的结论以该比较为主。

响应时间差使用 taskset-level paired bootstrap；runtime 差使用配对中位数和 bootstrap CI，并同时报告共同完成覆盖率。不得把 timeout、numeric error 或 dependency failure 混入 analytical McNemar 结论。

### 15.3 禁止伪重复

同一任务集中的多个任务不能被当作完全独立样本计算显著性。bootstrap 的基本抽样单位是 taskset。


### 15.3A tightness 聚合与权重

一套任务集可能产生不同数量的作业和多条 release/harvest traces。必须同时区分：

1. `JOB_WEIGHTED_DESCRIPTIVE_CDF`：所有合格作业进入，仅作描述；
2. `TASK_BALANCED_WORST_TRACE`：对每个 `(taskset, task)` 先在冻结场景集合中取最大观测响应时间，再每个任务贡献一次；
3. `TASKSET_BALANCED_PRIMARY`：每个 taskset 对其 certified tasks 聚合成一个预先指定的 summary，作为主显著性推断单位。

正式 bootstrap 的一级抽样单位是 `paired_family_id` / `taskset_id`，不得把同一任务集内的作业或 traces 当作独立样本。场景数和最坏值聚合规则必须在 formal contract 中预先冻结。

### 15.3B 多重检验与样本量功效

主结论只对预先指定的少量全局/主切片比较进行确认性检验。若逐格报告多个 p 值，必须使用预先冻结的 Holm 或 Benjamini--Hochberg 校正，并同时报告效果量与置信区间。

正式请求数的选择至少考虑：

- Wilson 区间目标半宽；
- 预期 discordant-pair 比例与 McNemar 功效；
- paired bound-tightening 的目标置信区间宽度；
- generation success rate 和 timeout coverage。


### 15.4 状态与分母先验定义

每个图表必须在生成前固定：

- 适用样本；
- timeout 是否进入分母；
- dependency missing 如何处理；
- censored 作业如何处理；
- $E_0$ 证书不满足如何处理；
- invalid service trace 如何处理；
- numeric coverage invalid 如何处理；
- applicability failure mask 的多原因编码与主原因汇总规则。

任何状态均不得在观察结果后临时合并或删除。

### 15.5 pilot 与正式数据分离

- pilot seeds 只用于冻结运行预算和参数区间；
- 正式图使用新 seeds；
- 不得观察正式结果后再改变参数范围、样本数或 timeout。

## 16. 结果文件、机器接口与复现契约

# 16. 结果文件与复现契约

状态枚举、合法组合、派生规则、null 条件、主外键和 failure masks 的权威定义位于 `ASAP_BLOCK_experiment_schema_v1_3_12.yaml`。本文中的列表是可读说明，正式实现应由 schema 生成校验器；任何重复定义必须与 YAML 自动对照。结果 schema 必须采用正交状态字段，并把纯仿真事实、分析输入、证书检查、服务曲线检查、模型兼容性和作业上界审计分表保存。相同仿真轨迹可以对应多个 $E_0$、服务曲线和 RTA 分析运行；不得把这些分析相关状态固定绑定到 `simulation_run_id`。

### 16.1 状态字段

#### 请求执行状态与运行阶段

```text
run_phase = CORE0A | PILOT | CORE0B | FORMAL | DIAGNOSTIC
request_execution_status =
    PLANNED
  | STARTED
  | FINISHED
  | TIMEOUT
  | OUT_OF_MEMORY
  | INTERRUPTED
  | INFRASTRUCTURE_FAILURE
  | NOT_RUN
```

请求执行状态与数学 solver 状态必须分开。`OUT_OF_MEMORY`、`INTERRUPTED` 和 `INFRASTRUCTURE_FAILURE` 不得映射成 `NO_CANDIDATE`、`TIMEOUT` 或 `NUMERIC_ERROR`。

#### 生成状态

```text
generation_status = SUCCESS | GENERATION_FAILURE
```

#### 分析方法角色、任务集级求解与认证状态

```text
analysis_method_role =
    MAIN_METHOD
  | AUXILIARY_ABLATION
  | DIAGNOSTIC
```

```text
analysis_solver_status =
    COMPLETED
  | NO_CANDIDATE
  | TIMEOUT
  | NUMERIC_ERROR
  | NOT_APPLICABLE_DEPENDENCY
  | INTERNAL_CONFORMANCE_FAILURE
  | UNSUPPORTED_EXPERIMENT_VARIANT
```

```text
analysis_certification_status =
    CERTIFIED_TASKSET
  | DIAGNOSTIC_ONLY_NOT_CERTIFIED
  | NOT_CERTIFIED
  | NOT_APPLICABLE
```

`CERTIFIED_TASKSET` 用于所有已有正式充分性定理/推论支持、全部任务候选闭合且前提成立的任务集级分析，包括：

- `MAIN_METHOD`：CW-$\Theta^{\mathrm{cw}}$、LOC-$\Theta^{\mathrm{loc}}$；
- `AUXILIARY_ABLATION`：固定 carry-in 接口已 `ACTIVE` 时的 CW-D、LOC-D，以及满足完整 source/dependency/compatibility 条件的 LOC-$\Theta^{\mathrm{cw}}$。

若固定 carry-in 接口未生效，CW-D/LOC-D 即使数值闭合也只能是 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`。LOC-$\Theta^{\mathrm{cw}}$ 只有完整 source CW 已认证且完整 local 向量兼容时才使用 `CERTIFIED_TASKSET`；该状态不产生第三套主方法的独立 schedulability 结论。

允许组合必须满足：

```text
CERTIFIED_TASKSET              -> analysis_method_role in {MAIN_METHOD, AUXILIARY_ABLATION}
DIAGNOSTIC_ONLY_NOT_CERTIFIED  -> analysis_method_role in {AUXILIARY_ABLATION, DIAGNOSTIC}
```

任何非法角色—认证组合均为 schema conformance failure。

若保留兼容字段 `taskset_proven`，必须满足：

```text
taskset_proven ==
    (analysis_certification_status == CERTIFIED_TASKSET)
```

任何不一致均为 schema conformance failure。

#### 任务级求解与认证状态

```text
task_solver_status =
    CANDIDATE_FOUND
  | NO_CANDIDATE
  | TIMEOUT
  | NUMERIC_ERROR
  | NOT_EVALUATED_AFTER_PREFIX_FAILURE
  | NOT_APPLICABLE_DEPENDENCY
  | INTERNAL_CONFORMANCE_FAILURE
```

```text
task_certification_status =
    CERTIFIED
  | PROVISIONAL_NOT_CERTIFIED
  | DIAGNOSTIC_ONLY_NOT_CERTIFIED
  | NOT_CERTIFIED
  | NOT_APPLICABLE
```

#### LOC-$\Theta^{\mathrm{cw}}$ 源依赖状态

```text
source_analysis_certification_status = CERTIFIED_TASKSET
carry_in_source_certification_status = CERTIFIED_TASKSET

源任务集未联合认证或任一依赖不完整时：
  正式运行 -> NOT_APPLICABLE_DEPENDENCY + NOT_APPLICABLE
  显式诊断 -> DIAGNOSTIC_ONLY_NOT_CERTIFIED
```

```text
fixed_carry_in_corollary_status =
    ACTIVE
  | HASH_MISMATCH
  | NOT_APPLICABLE
```

```text
dependency_vector_check_status =
    VALID
  | INVALID
  | NOT_CHECKED
```

```text
dependency_input_failure_mask = {
    TASKSET_HASH_MISMATCH,
    PRIORITY_HASH_MISMATCH,
    E0_MISMATCH,
    SERVICE_CURVE_MISMATCH,
    POWER_VECTOR_MISMATCH,
    NUMERIC_MODE_MISMATCH,
    NUMERIC_SCALE_MISMATCH,
    FORMAL_CONTRACT_MISMATCH,
    THEORY_HASH_MISMATCH,
    CARRY_IN_VECTOR_HASH_MISMATCH,
    COROLLARY_INACTIVE,
    CANONICAL_E0_HASH_MISMATCH,
    CANONICAL_POWER_HASH_MISMATCH,
    CANONICAL_SERVICE_HASH_MISMATCH,
    ENERGY_UNIT_HASH_MISMATCH,
    DOMINANCE_INVARIANT_VIOLATION
}
```

#### 数值覆盖状态

```text
numeric_coverage_status =
    VALID
  | INVALID_NUMERIC_COVERAGE
  | NOT_CHECKED
```

正式参数格要求 `NUMERIC_ERROR=0` 且 `numeric_coverage_status=VALID`。

#### 有限枚举状态

```text
enum_status =
    COMPLETE
  | INCONCLUSIVE_DRAIN_CAP
  | INTERNAL_ERROR
```

只有 `COMPLETE` 可以进入“已验证无反例”的分母。

#### 仿真观察与删失状态

```text
simulation_observation_status =
    COMPLETE
  | PARTIALLY_CENSORED
  | INVALID_TRACE
```

纯仿真事实只保存 completion censoring：

```text
completion_censoring_status =
    NONE
  | COMPLETION_NOT_OBSERVED
```

candidate boundary 是否到达依赖具体 `analysis_run_id`，只在 `simulation_bound_checks.csv` 中保存：

```text
bound_observation_status =
    BOUNDARY_REACHED
  | BOUNDARY_NOT_REACHED
  | NO_NUMERIC_CANDIDATE
```

不得在 `simulation_taskset_summary.csv` 中保存分析相关的 `BOUNDARY_NOT_REACHED` 聚合状态。

#### 作业观察状态

```text
completion_observation_status =
    COMPLETED
  | CENSORED_HORIZON
```

```text
deadline_check_status =
    MET_DEADLINE
  | DEADLINE_MISS
  | DEADLINE_NOT_REACHED
```

#### 服务轨迹检查状态

```text
service_curve_contract_status = VALID | INVALID | NOT_CHECKED
service_trace_status =
    VALID
  | INVALID_SERVICE_TRACE
  | NOT_CHECKED
```

该状态属于 `service_trace_check_id`，不属于 `simulation_run_id` 本身。

#### $E_0$ 证书状态

```text
job_e0_certificate_status =
    NOT_REQUIRED
  | SATISFIED
  | NOT_SATISFIED
  | NOT_CHECKED
```

```text
trace_e0_certificate_status =
    NOT_REQUIRED
  | SATISFIED_ALL
  | NOT_SATISFIED
  | EMPTY_E0_CERTIFICATE_SCOPE
  | NOT_CHECKED
```

这些状态属于具体 `e0_certificate_check_id`。

#### 分析—仿真兼容性状态

```text
battery_model_theorem_status = MATCHES | MISMATCH | NOT_CHECKED
scheduler_semantics_match_status = MATCHES | MISMATCH | NOT_CHECKED
event_order_match_status = MATCHES | MISMATCH | NOT_CHECKED
numeric_contract_status = VALID | INVALID | NOT_CHECKED
analysis_model_match_status = MATCHES | MISMATCH | NOT_CHECKED
energy_account_match_status = MATCHES | MISMATCH | NOT_CHECKED
dependency_certification_match_status = MATCHES | MISMATCH | NOT_CHECKED
initial_pending_jobs_status = MATCHES | MISMATCH | NOT_CHECKED
integer_event_model_status = MATCHES | MISMATCH | NOT_CHECKED
self_suspension_status = MATCHES | MISMATCH | NOT_CHECKED
task_seriality_status = MATCHES | MISMATCH | NOT_CHECKED
execution_demand_status = MATCHES | MISMATCH | NOT_CHECKED
power_upper_bound_status = MATCHES | MISMATCH | NOT_CHECKED
overhead_accounting_status = MATCHES | MISMATCH | NOT_CHECKED
harvest_causality_status = MATCHES | MISMATCH | NOT_CHECKED
service_curve_contract_match_status = MATCHES | MISMATCH | NOT_CHECKED
```

这些状态属于 `(simulation_run_id, analysis_run_id)` 的兼容性检查，不是纯仿真事实。

#### 上界适用性与审计状态

```text
bound_theorem_applicability =
    APPLICABLE
  | OUT_OF_THEOREM_SCOPE
  | NOT_CHECKED
```

```text
applicability_failure_mask = {
    NO_CERTIFIED_CANDIDATE,
    E0_TRACE_CERTIFICATE_FAILED,
    EMPTY_E0_CERTIFICATE_SCOPE,
    INVALID_SERVICE_TRACE,
    BATTERY_MODEL_MISMATCH,
    SCHEDULER_SEMANTICS_MISMATCH,
    EVENT_ORDER_MISMATCH,
    NUMERIC_CONTRACT_INVALID,
    ANALYSIS_MODEL_MISMATCH,
    ENERGY_ACCOUNT_MISMATCH,
    DEPENDENCY_CERTIFICATION_MISMATCH,
    INITIAL_PENDING_JOBS_MISMATCH,
    NON_INTEGER_EVENT_MISMATCH,
    SELF_SUSPENSION_MISMATCH,
    TASK_SERIALITY_MISMATCH,
    EXECUTION_DEMAND_MISMATCH,
    POWER_UPPER_BOUND_MISMATCH,
    UNACCOUNTED_OVERHEAD,
    HARVEST_CAUSALITY_MISMATCH,
    SERVICE_CURVE_CONTRACT_INVALID
}
```

```text
applicability_pending_mask = {
    SERVICE_CURVE_CONTRACT_NOT_CHECKED,
    SERVICE_TRACE_NOT_CHECKED,
    E0_CERTIFICATE_NOT_CHECKED,
    COMPATIBILITY_NOT_CHECKED,
    DEPENDENCY_NOT_CHECKED
}
```

```text
raw_bound_comparison =
    WITHIN_CANDIDATE
  | EXCEEDS_CANDIDATE
  | NOT_OBSERVED
  | NO_NUMERIC_CANDIDATE
```

```text
soundness_check_status =
    WITHIN_CERTIFIED_BOUND
  | CERTIFIED_BOUND_VIOLATION
  | OUT_OF_THEOREM_SCOPE
  | INCONCLUSIVE
```

原单层 `bound_check_status` 不再作为规范主字段。旧数据若保留该列，必须通过迁移脚本拆分为 `raw_bound_comparison`、`bound_theorem_applicability`、`applicability_failure_mask` 和 `soundness_check_status`，并标明 schema version。`soundness_check_status` 本身仍是三层审计结构中的正式最终结论字段。

### 16.2 每次正式运行必须保存

规范构件与正式运行输出至少包括：

```text
ASAP_BLOCK_实验配置与验收规范_v1_3_12_最终机器合同与验证闭合版.md
ASAP_BLOCK_experiment_schema_v1_3_12.yaml
ASAP_BLOCK_data_dictionary_v1_3_12.yaml
ASAP_BLOCK_canonical_serialization_v1_3_12.yaml
ASAP_BLOCK_machine_interface_manifest_v1_3_12.yaml
ASAP_BLOCK_formal_contract_template_v1_3_12.yaml
ASAP_BLOCK_generator_contract_template_v1_3_12.yaml
ASAP_BLOCK_simulation_contract_template_v1_3_12.yaml
ASAP_BLOCK_trace_generator_contract_template_v1_3_12.yaml
ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml
ASAP_BLOCK_validation_common_v1_3_12.py
ASAP_BLOCK_artifact_validator_v1_3_12.py
ASAP_BLOCK_result_validator_v1_3_12.py
ASAP_BLOCK_acceptance_report_validator_v1_3_12.py
ASAP_BLOCK_run_plan_definition_template_v1_3_12.csv
ASAP_BLOCK_run_plan_dependencies_template_v1_3_12.csv
ASAP_BLOCK_run_execution_log_template_v1_3_12.csv
config.yaml
generator_contract.yaml
simulation_contract.yaml
trace_generator_contract.yaml
formal_contract.yaml
acceptance_report.yaml
manifest.json
git_commit.txt
git_status.txt
sha256sum.txt
run_plan_definition.csv
run_plan_dependencies.csv
run_execution_log.csv
generation_requests.csv
paired_transformations.csv
tasksets.csv
task_definitions.csv
release_trace_sets.csv
release_traces.csv
harvest_trace_sets.csv
harvest_traces.csv
per_taskset_results.csv
per_task_results.csv
rta_dependency_records.csv
simulation_taskset_summary.csv
simulation_job_results.csv
service_trace_checks.csv
e0_trace_certificate_checks.csv
e0_job_certificate_checks.csv
analysis_simulation_compatibility_checks.csv
simulation_bound_checks.csv
request_outputs.csv
bound_audit_runs.csv
```

`validation_report.json` 属于验证完成后生成的外部 release record，不进入待验证运行包的 formal hash 或 `sha256sum.txt`，避免验证结果对自身形成循环依赖。

### 16.2A 规范事实文件与请求计划

#### `tasksets.csv` / `task_definitions.csv`

保存可重新执行的完整任务集事实，而不是只保存哈希。`task_definitions.csv` 说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
taskset_id
task_id
C_i
T_i
D_i
P_raw
P_analysis
priority_rank
power_latent_value
```


#### `release_trace_sets.csv` / `harvest_trace_sets.csv`

轨迹头表提供可由顶层仿真事实表直接引用的稳定主键。

`release_trace_sets.csv` 至少保存：

```text
release_trace_id
taskset_id
scenario_id
trace_generator_contract_hash
release_trace_hash
formal_contract_hash
```

`harvest_trace_sets.csv` 至少保存：

```text
harvest_trace_id
scenario_id
trace_generator_contract_hash
harvest_trace_raw_hash
harvest_trace_analysis_hash
formal_contract_hash
```

`simulation_taskset_summary.csv.release_trace_id` 和 `harvest_trace_id` 必须分别外键到两个头表；明细表再以复合主键保存 job/tick 行。

#### `release_traces.csv` / `harvest_traces.csv`

```text
release_trace_id
job_id
task_id
release_time
execution_demand
```

```text
harvest_trace_id
tick
H_raw
H_analysis
```

大轨迹允许压缩分块存储，但索引、压缩格式、文件哈希和解压版本必须进入 manifest。

#### build identity

每个实际运行必须绑定：

```text
build_identity_hash
rta_implementation_hash
simulator_binary_hash
scheduler_binary_hash
compiler_and_flags_hash
```

formal contract 可以独立于代码 commit，但任何 result ID 必须可追溯到不可变 build identity。


### 16.3 生成请求与任务集级分析结果

#### `generation_requests.csv`

主键：

```text
generation_request_id
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
generation_request_id
formal_contract_hash
generator_contract_hash
parameter_cell_id
replicate_index
requested_seed
formal_master_seed
formal_master_seed_source
formal_master_seed_commitment_hash
formal_seed_derivation_algorithm
seed_derivation_context_hash
formal_seed_set_hash
generation_status
generation_attempts
max_resampling_reached
generation_failure_reason
target_total_utilization
target_rho_p
target_rho_e
actual_total_utilization
actual_rho_p
actual_rho_e_raw
actual_rho_e_analysis
rho_e_parameterization_status
taskset_id
taskset_semantic_hash
```

生成失败时 `taskset_id=null`；生成成功时 `taskset_id` 非空。任何分析运行只能引用成功请求。

#### `per_taskset_results.csv`

`analysis_run_id` 唯一标识一次固定任务集、固定 RTA 变体、固定服务曲线、固定 $E_0$ 和固定数值合同下的分析执行。主键：

```text
analysis_run_id
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
analysis_run_id
taskset_id
generation_request_id
formal_contract_version
formal_contract_hash
generator_contract_hash
experiment_config_version
experiment_config_hash
analysis_method_role
variant
window_mode
carry_in_mode
M
n
n_tasks_total
n_tasks_evaluated
n_tasks_candidate_found
n_tasks_certified
first_non_candidate_priority
target_total_utilization
actual_total_utilization
target_rho_p
actual_rho_p
target_rho_e
actual_rho_e_raw
actual_rho_e_analysis
rho_e_tolerance
rho_e_tolerance_mode
rho_e_parameterization_status
numeric_coverage_status
service_rate_reference
service_rate_r_raw
service_rate_r_scaled_exact
service_curve_integerization_mode
power_scale_alpha
target_power_demand
actual_power_demand_raw
actual_power_demand_analysis
target_service_latency_ratio
realized_service_latency_L
realized_service_latency_ratio
power_latent_seed
power_latent_vector_hash
power_latent_mapping_version
priority_reference_delta
priority_rank_reference_hash
E0_target_raw
E0_analysis_scaled
E0_analysis_effective
E0_rounding_error
target_epsilon_0
realized_epsilon_0_analysis
e0_parameterization_policy
e0_parameterization_status
theorem_conditioning_mode
service_latency_L
service_curve_raw_spec
service_curve_scaled_spec
analysis_solver_status
analysis_certification_status
taskset_proven
runtime_wall
runtime_cpu
rta_formula_version
theory_document_sha256
fixed_carry_in_corollary_status
fixed_carry_in_corollary_hash
taskset_semantic_hash
priority_rank_hash
power_vector_raw_hash
power_vector_scaled_hash
energy_numeric_mode
energy_numeric_scale
energy_demand_rounding
energy_supply_rounding
numeric_integer_type
numeric_range_check_status
service_curve_raw_hash
service_curve_scaled_hash
```

`taskset_proven` 必须等价于 `analysis_certification_status=CERTIFIED_TASKSET`。在 `POINTWISE_FLOOR` 模式下，`service_rate_r_scaled_exact=Sr`；不得擅自替换为 $\lfloor Sr\rfloor$。

### 16.4 任务级结果与 RTA 依赖记录

子表通过 `analysis_run_id` 继承 `run_phase`、formal contract 和 build identity；不要求重复列，但外键路径必须唯一。

#### `per_task_results.csv`

主键：

```text
(analysis_run_id, task_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
analysis_run_id
taskset_id
task_id
formal_contract_hash
analysis_method_role
variant
window_mode
carry_in_mode
priority_rank
C_i
T_i
D_i
P_hat_i_raw
P_hat_i_scaled
P_hat_i_rounding
candidate_response_time
task_solver_status
task_certification_status
source_analysis_run_id
carry_in_vector_hash
carry_in_source_variant
carry_in_source_certification_status
fixed_carry_in_corollary_status
dependency_vector_check_status
dependency_input_failure_mask
closing_w
witness_h
critical_q
minimum_energy_slack
processor_delay_Dp
envelope_call_count
h_values_checked
q_values_checked
```

#### `rta_dependency_records.csv`

主键：

```text
(analysis_run_id, target_task_id, hp_task_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
analysis_run_id
taskset_id
target_task_id
hp_task_id
theta_value
theta_source_mode
source_analysis_run_id
source_task_id
source_task_solver_status
source_task_certification_status
source_analysis_solver_status
source_analysis_certification_status
source_variant
source_theory_document_sha256
target_theory_document_sha256
source_taskset_semantic_hash
target_taskset_semantic_hash
source_priority_rank_hash
target_priority_rank_hash
source_E0_scaled
target_E0_scaled
source_service_curve_hash
target_service_curve_hash
source_power_vector_hash
target_power_vector_hash
source_energy_numeric_mode
target_energy_numeric_mode
source_energy_numeric_scale
target_energy_numeric_scale
source_formal_contract_hash
target_formal_contract_hash
fixed_carry_in_corollary_status
carry_in_vector_hash
dependency_vector_check_status
dependency_input_failure_mask
dependency_record_hash
```

LOC-$\Theta^{\mathrm{cw}}$ 的正式 theorem-backed 记录必须满足：

```text
source_variant = CW-Theta^cw
source_analysis_certification_status = CERTIFIED_TASKSET
source_task_certification_status = CERTIFIED
fixed_carry_in_corollary_status = ACTIVE
dependency_vector_check_status = VALID
dependency_input_failure_mask = EMPTY
```

### 16.5 纯仿真事实表

#### `simulation_taskset_summary.csv`

`simulation_run_id` 唯一标识一次实际执行轨迹。它必须覆盖所有会改变调度轨迹的因素，包括 taskset、scheduler、release trace、raw/scaled harvest trace、battery mode、event semantics、energy account mode、numeric scale、scaled power vector 和 account semantics version。它不包含仅用于事后检查且不改变调度的 $E_0$ 阈值或服务曲线声明。

主键：

```text
simulation_run_id
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
simulation_run_id
run_phase
formal_contract_hash
build_identity_hash
simulator_binary_hash
scheduler_binary_hash
taskset_id
scenario_id
scheduler_variant
scheduler_semantics_version
priority_policy
priority_rank_hash
event_order_version
simulation_energy_account_mode
energy_account_semantics_version
dispatch_energy_account
energy_numeric_mode
energy_numeric_scale
simulation_initial_energy_raw
simulation_initial_energy_scaled
battery_mode
battery_capacity_raw
battery_capacity_scaled
overflow_count
release_trace_hash
harvest_trace_raw_hash
harvest_trace_scaled_hash
power_vector_raw_hash
power_vector_scaled_hash
shadow_dominance_check_status
generation_horizon
observation_horizon
simulation_observation_status
completion_censoring_status
```

不得在该表保存单一 `service_trace_status`、`trace_e0_certificate_status` 或分析相关模型匹配状态。

#### `simulation_job_results.csv`

主键：

```text
(simulation_run_id, job_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
simulation_run_id
taskset_id
scenario_id
job_id
task_id
release_time
absolute_deadline
release_trace_hash
harvest_trace_raw_hash
harvest_trace_scaled_hash
release_time_energy_raw
release_time_energy_analysis
completion_time
observed_response_time
completion_observation_status
deadline_check_status
```

不得在该表保存单一 `job_e0_certificate_status`；证书结果写入独立检查表。

### 16.6 分析相关检查表

#### `service_trace_checks.csv`

每行表示一条仿真采能轨迹相对于一个具体分析服务曲线和数值合同的验证。主键：

```text
service_trace_check_id
```

唯一约束至少包括：

```text
(simulation_run_id, analysis_run_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
service_trace_check_id
simulation_run_id
analysis_run_id
harvest_trace_raw_hash
harvest_trace_scaled_hash
harvest_trace_analysis_hash
service_curve_raw_hash
service_curve_scaled_hash
service_curve_analysis_hash
analysis_energy_unit
energy_numeric_mode
energy_numeric_scale
service_curve_integerization_mode
service_trace_status
service_verification_domain
service_verification_algorithm
service_verification_equivalence_status
```

#### `e0_trace_certificate_checks.csv`

主键：

```text
e0_certificate_check_id
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
e0_certificate_check_id
simulation_run_id
analysis_run_id
E0_target_raw
E0_analysis_scaled
E0_analysis_effective
e0_parameterization_status
theorem_conditioning_mode
certificate_set_id
certificate_set_definition_hash
certificate_set_size
certificate_scope_mode
trace_e0_certificate_status
job_certificate_satisfaction_rate
```

#### `e0_job_certificate_checks.csv`

主键：

```text
(e0_certificate_check_id, job_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
e0_certificate_check_id
simulation_run_id
analysis_run_id
job_id
release_time_energy_raw
release_time_energy_analysis
job_e0_certificate_status
```

#### `analysis_simulation_compatibility_checks.csv`

该表保存 `(simulation_run_id, analysis_run_id)` 的模型匹配结论，避免在每个作业行重复计算。主键：

```text
compatibility_check_id
```

唯一约束：

```text
(simulation_run_id, analysis_run_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
compatibility_check_id
simulation_run_id
analysis_run_id
battery_model_theorem_status
scheduler_semantics_match_status
event_order_match_status
numeric_contract_status
analysis_model_match_status
energy_account_match_status
dependency_certification_match_status
initial_pending_jobs_status
integer_event_model_status
self_suspension_status
task_seriality_status
execution_demand_status
power_upper_bound_status
overhead_accounting_status
harvest_causality_status
service_curve_contract_match_status
compatibility_failure_mask
```

#### `simulation_bound_checks.csv`

每行表示一个作业相对于一个分析运行的 candidate 比较和定理审计。主键：

```text
(simulation_run_id, job_id, analysis_run_id)
```

说明性字段摘要（完整字段分类以机器可读 schema 为准）：

```text
simulation_run_id
job_id
taskset_id
task_id
analysis_run_id
analysis_variant
window_mode
carry_in_mode
analysis_solver_status
analysis_certification_status
task_solver_status
task_certification_status
candidate_response_time
candidate_boundary
deadline_boundary
service_trace_check_id
e0_certificate_check_id
compatibility_check_id
bound_observation_status
bound_theorem_applicability
applicability_pending_mask
applicability_failure_mask
primary_applicability_failure_reason
raw_bound_comparison
soundness_check_status
source_analysis_run_id
carry_in_vector_hash
```

必须满足：

$$
\mathrm{candidate\ boundary}
=
\mathrm{release\ time}
+
\mathrm{candidate\ response\ time},
$$

$$
\mathrm{deadline\ boundary}
=
\mathrm{release\ time}
+
D_i.
$$

字段规则：

- `task_solver_status=CANDIDATE_FOUND` 时 `candidate_response_time` 和 `candidate_boundary` 必须非 null；否则二者必须为 null，`raw_bound_comparison=NO_NUMERIC_CANDIDATE`；
- `EXACT_RATIONAL` 模式下，`*_scaled` 字段默认必须为 null，除非数据字典将其明确定义为规范化精确有理数表示；依赖一致性以 canonical analysis hash 为权威；
- `FIXED_POINT_DIRECTED` 模式下，`E0_analysis_scaled`、`energy_numeric_scale` 以及 source/target scaled 对照字段按数据字典强制非 null；
- provisional 或 diagnostic candidate 可以执行 raw comparison，但 applicability 必须为 `OUT_OF_THEOREM_SCOPE`，failure mask 包含 `NO_CERTIFIED_CANDIDATE`；
- 只有 `task_certification_status=CERTIFIED`，且 service/E0/compatibility 三个检查全部通过时，applicability 才可为 `APPLICABLE`；
- 只有 `APPLICABLE + EXCEEDS_CANDIDATE` 才产生 `CERTIFIED_BOUND_VIOLATION`；
- 观察边界未到达时，`bound_observation_status=BOUNDARY_NOT_REACHED`，raw comparison 为 `NOT_OBSERVED`。正式 CORE-3 中对 certified/applicable candidate 该状态必须为 0；非零属于 horizon/analyzer contract failure。

所有外键必须通过稳定 ID 关联，禁止仅依靠行顺序、浮点值或文件位置联结。

必须落实以下复合外键：

- `simulation_bound_checks.csv.(simulation_run_id, job_id)` -> `simulation_job_results.csv.(simulation_run_id, job_id)`；
- `simulation_bound_checks.csv.(analysis_run_id, task_id)` -> `per_task_results.csv.(analysis_run_id, task_id)`；
- `rta_dependency_records.csv.(source_analysis_run_id, source_task_id)` -> `per_task_results.csv.(analysis_run_id, task_id)`；
- `rta_dependency_records.csv.(analysis_run_id, target_task_id)` -> `per_task_results.csv.(analysis_run_id, task_id)`；
- `e0_job_certificate_checks.csv.(simulation_run_id, job_id)` -> `simulation_job_results.csv.(simulation_run_id, job_id)`；
- `e0_job_certificate_checks.csv.analysis_run_id` -> `per_taskset_results.csv.analysis_run_id`。

复合唯一约束必须写成显式 tuple/list 结构，例如：

```yaml
unique_constraints:
  - [simulation_run_id, analysis_run_id]
```

禁止使用可能被解释为两个单列唯一约束的扁平 `unique` 列表。


### 16.7 manifest、数据字典、合同哈希与外键谱系

`manifest.json` 必须记录：

- 代码 commit；
- RTA 公式版本与理论文档 SHA-256；
- 实验配置版本与哈希；
- `generator_contract_version` 与 `generator_contract_hash`；
- `formal_contract_version` 与 `formal_contract_hash`；
- `formal_master_seed`、seed derivation algorithm、context hash 与 seed-set hash；
- Python/C++/solver 版本；
- CPU、内存、操作系统；
- 单线程/多线程与编译选项；
- timeout、numerical mode、scale、integerization mode、舍入方向、整数类型和范围检查；
- simulation energy account mode；
- schema version/hash；
- 所有输出文件哈希；
- 外键完整性和 formal-contract lineage 检查结果。

合同哈希保存采用规范化规则：

1. 顶层请求/运行表直接保存 `formal_contract_hash`；
2. 子表通过非空强外键唯一继承 formal contract，不要求冗余复制 hash；
3. 每条子表记录必须能够通过唯一外键路径追溯到一个且仅一个 formal contract；
4. 同时关联 analysis 与 simulation 的检查表必须验证两侧 formal contract 相同；
5. 任一断链、多重归属或合同不一致均触发 `INTERNAL_CONFORMANCE_FAILURE`。

任何 schema 字段的 null 条件、适用范围、枚举映射、主键、外键、聚合规则和 failure-mask 编码必须在正式运行前写入数据字典。


### 16.8 机器可读 schema、data dictionary 与三类 validator

机器字段分为 `required`、`conditionally_required` 和 `optional_diagnostic`。schema 与 data dictionary 的表集合、字段集合、字段分类和 canonical column order 必须精确相等。

artifact validator 必须：

- 使用拒绝重复 key 的 YAML loader；
- 检查全部 sidecar；
- 将 formal-contract template 中嵌入的 Markdown、schema、data dictionary、canonical spec、三个 validator、child templates 和 acceptance template 哈希与实际字节逐一比较；
- 校验复合唯一约束、复合外键和 theorem-check pair linkage；
- 自测至少覆盖损坏嵌入 hash、重复 YAML key 和损坏 sidecar，且三者都必须返回非零退出码。

result validator 独立负责正式 CSV 结果：字段、类型、条件 null、主键、唯一约束、单列/复合外键、执行状态转移、`N_planned=N_accounted`、formal-primary check 唯一性和同一 `(simulation_run_id, analysis_run_id, job_id)` 的检查串接。artifact validator 通过不代表正式结果数据已经通过。

### 16.9 canonical serialization 与稳定 ID

所有合同、hash、ID 和不可变 CSV 使用 `ASAP_BLOCK_canonical_serialization_v1_3_12.yaml`。该文件冻结 UTF-8/NFC、LF、无 BOM、重复 key 拒绝、整数、有理数、null、集合排序、CSV header/row 排序、字节序、自哈希置空规则和 domain-separated ID 预像。禁止用普通浮点、YAML 隐式数值或人工字符串生成规范 ID。

### 16.10 theorem-applicability 检查的强配对

`simulation_bound_checks.csv` 对 service、$E_0$ 和 compatibility 的引用必须使用包含 `simulation_run_id` 与 `analysis_run_id` 的复合外键；$E_0$ 作业检查还必须包含 `job_id`。普通“ID 存在”不足以证明检查属于同一分析—仿真对。每个正式 pair 的 service、$E_0$ 和 compatibility 检查必须各有且仅有一个 `FORMAL_PRIMARY`，其他检查只能标记为 `DIAGNOSTIC_SECONDARY`。

### 16.11 acceptance report 与证据链

每个 CORE-0A/B gate 必须保存 status、证据文件、证据 hash、计数、validator name/version/hash 和备注。acceptance report 必须覆盖 processor term、有限状态搜索、inconclusive/violation 计数、事件顺序、联合认证、非空覆盖、最终 numeric/generation/run-plan/lineage/soundness 门槛。仅有 YAML 可解析和关键词存在不能写作总体 `PASSED`。


## 17. 旧实验的处理方式

### 可复用

- 任务生成器框架；
- 调度语义单元测试；
- 混淆矩阵分析脚本；
- runtime 记录框架；
- $E_0$ sensitivity 的实验思想；
- 九算法的事件计数基础设施。

### 必须重跑

- 所有正式 RTA proven ratio；
- complete/local 响应时间上界；
- 紧致度；
- soundness 对照；
- 消融；
- $E_0$ sensitivity；
- runtime/scalability；
- 与最新 RTA 相关的所有图表。

### 不再作为正式主结果

- v20.4 vs v21；
- 旧 v21-only proven 数量；
- 旧 A0–A4 中没有在 v9.3 下重新定义的变体；
- 把 `initial_energy` 当作 $E_0$ 的结果；
- 有限电池仿真与无溢出 RTA 直接组成的 soundness 混淆矩阵。

---

## 18. 唯一合理的执行顺序

### 步骤 1：冻结已集成的固定 carry-in 接口

固定 carry-in 联合认证已经写入 v9.3 第 9.5 节。实现与正式合同必须声明接口 `V9_3_SECTION_9_5_FIXED_CARRY_IN_INTERFACE`，并精确匹配理论文档 SHA-256；不再存在“投稿前待补推论”的阻塞项。

### 步骤 2：实现 v9.3 与五配置接口

完成 exact envelope、complete/local、冻结候选读取、求解/认证正交状态、规范化检查表和保守数值合同。

### 步骤 3：预提交数值模式、master seed 来源与 schema，完成 CORE-0A

在 CORE-0A 前固定 `energy_numeric_mode`、fixed-point integerization 语义、formal master seed 来源、schema/state-machine hash 和候选 build identity；随后完成结构验收。任一公式、状态机、依赖、exact solver 逻辑或 schema 检查失败，禁止进入 pilot。

### 步骤 4：每格 50 个生成请求的 pilot

pilot 用于冻结：

- scale、整数类型和范围；`energy_numeric_mode` 与 fixed-point 的 `POINTWISE_FLOOR` 已在 CORE-0A 前固定，不是 pilot 自由参数；
- $r_{\mathrm{ref}}$ 与 $\rho_E$ 容差；
- generation/observation horizon；
- timeout；
- 正式生成请求数；
- 确定性 seed 派生上下文和正式 seed 集；`formal_master_seed` 来源已在 CORE-0A 前预提交；
- 正式网格；
- transition region；
- 运行预算。

pilot 数据不得并入正式结果。

### 步骤 5：冻结正式合同并完成 CORE-0B

生成 `generator_contract.yaml`、`simulation_contract.yaml`、`trace_generator_contract.yaml`、`run_plan_definition.csv` 和 `formal_contract.yaml` 及其不可变哈希；执行状态另写入 `run_execution_log.csv`。按无循环依赖规则派生正式 seed 集，并使用最终合同重新执行参数敏感的 exact、范围、服务曲线、能量账户、$\rho_E$ 和重复性验收。CORE-0B 未通过时不得启动正式实验。

### 步骤 6：CORE-1 与 CORE-2

先运行 $E_0=0$，再运行有明确证书解释的条件轨道。正式参数格要求 `NUMERIC_ERROR=0`。

### 步骤 7：CORE-3

完成 service trace check、$E_0$ certificate check、analysis-simulation compatibility check 和 certified-bound audit。

### 步骤 8：CORE-4 与 CORE-5

完成配对敏感性、sustainability 分层实验和分轴复杂度实验。

### 步骤 9：扩展证据

最后运行九调度器、真实轨迹和有限电池扩展。

正式顺序冻结为：

$$
\boxed{
\mathrm{CORE\text{-}0A}
\rightarrow
\mathrm{pilot}
\rightarrow
\mathrm{formal\ contract\ freeze}
\rightarrow
\mathrm{CORE\text{-}0B}
\rightarrow
\mathrm{formal\ experiments}
}
$$

## 19. 最小论文实验包与高质量论文实验包

### 最小可发表包

$$
\boxed{
\mathrm{CORE\!\text{-}0}
+
\mathrm{CORE\!\text{-}1}
+
\mathrm{CORE\!\text{-}2}
+
\mathrm{CORE\!\text{-}3}
+
\mathrm{CORE\!\text{-}4}
+
\mathrm{CORE\!\text{-}5}
}
$$

这套实验能够回答：正确性、有效性、紧致度、消融、敏感性和复杂度。

### 高质量完整包

在上述基础上增加：

$$
\boxed{
\mathrm{EXT\!\text{-}1}
+
\mathrm{EXT\!\text{-}2}
}
$$

即九调度器与真实能量轨迹案例。

有限电池 RTA 和生成器鲁棒性可放附录。

---

## 20. 文献实验设计依据

本配置吸收了项目文献中的成熟做法，但没有机械照搬：

1. **Bertogna 与 Cirinei 2007**：以总利用率为横轴，比较不同多核可调度性测试的检测能力，并用大规模随机任务集验证 RTA。
2. **Sun 等，Improving the RTA of Global FP**：使用 RandFixedSum、配对任务集、精度和运行时间分开评价，并诚实报告极端实例的高运行时间。
3. **Abdeddaïm 等 2016**：同时改变处理器利用率、能量利用率、截止期和供能速率，使用 processor-only、必要条件、仿真参考和 weighted schedulability。
4. **Wang 等 2022**：比较两套 EH 响应时间上界，扫描处理器/能量利用率和截止期，并从真实太阳能轨迹构造服务曲线。
5. **Abdeddaïm 等 2013**：除失败率外，还比较抢占、调度开销、忙闲区间和平均能量，适合九调度器部分。
6. **Lin 等 2024**：同时报告 schedulability 和随任务数增长的运行时间，说明高复杂度分析必须给出实际扩展性。

因此，本项目的正式实验主线应是：

$$
\boxed{
\text{两套正式 RTA 主比较}
+
\text{受控消融}
+
\text{模型一致的紧致度审计}
+
\text{参数和复杂度分析}
}
$$

九调度器和真实轨迹作为增强证据，而不是替代 RTA 主实验。

---


## 20A. 机器可读权威文件

v1.3.12 发布 schema、data dictionary、canonical serialization、machine-interface manifest、三个 child-contract templates、formal-contract template、acceptance-report template、validation common、artifact validator、acceptance-report validator、result validator 和 run-plan CSV templates。

构件验证结论必须分层：

```text
Artifact syntax / duplicate-key validation
Sidecar hash validation
Embedded hash binding validation
Schema-data-dictionary equivalence
Cross-document semantic conformance
Runtime result conformance
CORE-0A
CORE-0B
```

前五项通过不代表 runtime result、CORE-0A 或 CORE-0B 通过。


## 21. 自检后的风险分类

本节区分“规范已经定义”与“代码/流程尚未实现或验收”。

### 全局 P0

1. v9.3 数学核心不由本机器合同包重新实现或验收；
2. task solver、task certification、analysis solver 和 analysis certification 尚未接入 runtime runner；
5. CORE-0A 尚未通过；
6. pilot 后正式合同与 `formal_contract_hash` 尚未冻结；
7. CORE-0B 尚未执行；
8. 旧正式 RTA 结果必须重跑；
9. 正规化 service/E0/compatibility/bound-check 表尚未由 runner、analyzer 和导出器实现；
10. theorem-backed 仿真的分析一致账户或保守影子账户尚未实现和验收；
11. 目标/分析 $E_0$ 参数化、全释放作业证书范围和服务曲线合同尚未实现；
12. run plan、请求事实、规范任务集/trace 文件和 build identity 尚未实现。

### CORE-2 专项 P0

1. `CERTIFIED_TASKSET` 必须统一表示任务集级充分性证书；CW-D/LOC-D 在固定 carry-in 推论生效且全部闭合时可认证，否则只能 diagnostic；
2. `analysis_method_role` 与 `analysis_certification_status` 尚未按新合同实现；
3. LOC-$\Theta^{\mathrm{cw}}$ 必须只读取 `source_analysis_certification_status=CERTIFIED_TASKSET` 的 CW 源运行；
4. 固定 carry-in 推论、源向量哈希、全部分析输入一致性和依赖记录必须有效，任务级 candidate 才能升级为 `CERTIFIED`；
5. source 未认证时，正式运行必须为 `NOT_APPLICABLE_DEPENDENCY + NOT_APPLICABLE`；只有显式 `DIAGNOSTIC` 运行才可使用 `DIAGNOSTIC_ONLY_NOT_CERTIFIED`；
6. LOC-$\Theta^{\mathrm{cw}}$ 的完整兼容向量必须联合升级为 `CERTIFIED_TASKSET`，但不得作为第三套主方法或 CW 失败时的新增证明；
7. 运行级 solver 完成度、任务认证计数、`rta_dependency_records.csv` 和依赖外键尚未实现；
8. 有效依赖域中的 `NO_CANDIDATE` 必须触发 dominance/conformance failure；
9. CW-D/LOC-D 的任务级联合认证状态机尚未实现。

### CORE-3 / CORE-4 数据语义 P0

1. `simulation_run_id` 必须只绑定实际轨迹和会改变轨迹的账户参数；
2. service trace、$E_0$ certificate 和 analysis-simulation compatibility 必须按 `analysis_run_id` 独立检查；
3. `service_trace_checks.csv`、`e0_trace_certificate_checks.csv`、`e0_job_certificate_checks.csv`、`analysis_simulation_compatibility_checks.csv` 尚未实现；
4. `simulation_bound_checks.csv` 必须引用上述检查 ID；
5. raw comparison、applicability failure mask 和 soundness status 尚未实现；
6. 只有 `APPLICABLE + EXCEEDS_CANDIDATE` 才是 `CERTIFIED_BOUND_VIOLATION`；
7. $E_0>0$ 必须要求非空证书域和 `SATISFIED_ALL`；
8. theorem-backed dispatch 必须使用分析一致账户或保守影子账户；
9. scaled harvest、scaled power、raw/scaled release-time energy 和 shadow dominance 尚未验收；
10. generation/observation horizon、completion 后边界判断和 drain-cap 零 inconclusive 门槛尚未实现；
11. completion censoring 与 analysis-bound observation 尚未彻底分表；
12. 完整系统模型兼容性 failure mask 与 pending mask 尚未实现。

### 正式数值覆盖 P0

1. pilot 可以出现 `NUMERIC_ERROR` 以暴露合同问题；
2. 正式 CORE-1/CORE-2 任一参数格必须满足 `NUMERIC_ERROR=0`；
3. 非零时参数格标记 `INVALID_NUMERIC_COVERAGE` 并返回 pilot；
4. `actual_rho_e_analysis`、范围证明、integerization mode 和正式 scale 必须在 CORE-0B 中重新验收。

### P1

1. `generation_requests.csv`、`generator_contract.yaml` 和生成失败事实链尚未实现；
2. 无循环依赖的确定性 seed 派生规则尚未实现；
3. formal contract 尚未覆盖完整 generator contract 与派生 seed 集；
4. LOC-$\Theta^{\mathrm{cw}}$ 的运行级 solver/认证聚合计数尚未实现；
5. DM 全序与 priority hash 一致性尚未验收；
6. 利用率范围、重采样次数与 generation-failure 门槛仍待 pilot 冻结；
7. $r_{\mathrm{ref}}$、$\rho_E$ 容差、horizon、timeout、正式网格和正式请求数待 pilot 冻结；formal master seed 来源必须在 CORE-0A 前预提交；
8. 三类 local-only 与 operational/analytical 统计分层尚未实现；
9. $\kappa$ latent coupling、deadline reference priority 与 runtime 右删失口径尚未落实；
10. sustainability 的公式单调性与物理系统重跑尚未在脚本和图表命名中分离；
11. schema 字段的 null 条件、failure/pending-mask 编码、formal-contract lineage 和数据字典尚未完成；
12. paired-family lineage、独立 RNG 子流、simulation/trace contracts 尚未实现；
13. tightness 权重、多重检验、功效和 runtime 环境合同尚未落实；
14. 机器可读 schema/data dictionary/formal-contract 与三类 validator 尚未接入 runner/analyzer。

### 条件性 P2

timeout 非零时，completed-only runtime 分位数必须标注为条件统计；达到预先冻结门槛时，应实现右删失分析和 restricted mean runtime。

## 22. 冻结前检查表

正式启动大规模运行前，逐项确认：

- [ ] 理论文档为 v9.3；
- [x] 固定 carry-in 安全推论已集成于 v9.3 第 9.5 节，并保存接口版本与理论文档哈希；
- [ ] v9.3 exact implementation 已完成；
- [ ] CORE-0A 已通过；
- [ ] `energy_numeric_mode`、formal master seed 来源、schema/state-machine hash 在 CORE-0A 前预提交；
- [ ] pilot 与正式 seeds 分离；
- [ ] numerical mode、scale、integerization mode、整数类型、$r_{\mathrm{ref}}$、$\rho_E$ 容差、horizon、timeout、网格和正式生成请求数已冻结；
- [ ] `formal_contract.yaml`、`formal_contract_version` 和 `formal_contract_hash` 已生成；
- [ ] `simulation_contract.yaml`、`trace_generator_contract.yaml`、`run_plan_definition.csv` 及其不可变哈希已生成，`run_execution_log.csv` 与正式合同预像分离；
- [ ] `generator_contract.yaml`、`generator_contract_version` 和 `generator_contract_hash` 已生成并被 formal contract 覆盖；
- [ ] `generation_requests.csv` 记录全部成功/失败请求，失败请求没有伪造 `analysis_run_id`；
- [ ] 正式 seeds 由 `seed_derivation_context_hash + seed_scope_id + replicate_index + formal_master_seed` 确定性派生，且无 seed 替换；
- [ ] pilot/formal 样本量均以 generation requests 定义，未为凑成功样本追加 seed；
- [ ] `run_plan_definition.csv`、`run_plan_dependencies.csv`、`request_outputs.csv` 与 `run_execution_log.csv` 联合满足 `N_planned=N_accounted`，基础设施失败与数学求解状态分离；
- [ ] CORE-0B 已在最终 formal contract 下通过；
- [ ] exact envelope 在冻结小域穷举与至少 $10^4$ 个随机/边界实例中均与 brute force 零差异；
- [ ] CORE-0 非空覆盖门槛均满足；
- [ ] $D_k^{P,\Theta}$ 与定义扫描零差异；
- [ ] $w=C_k,\ldots,D_k$ 逐点扫描，$h$ 完整枚举，每个 $h$ 检查全部 $q=1,\ldots,A_k^\Theta(w)$；
- [ ] $y_k\hat P_k$ 被计入，服务长度为 $h+q-1$，局部 workload 长度为 $q+h$；
- [ ] analysis solver/certification 与 task solver/certification 已正交实现；
- [ ] `taskset_proven == (analysis_certification_status == CERTIFIED_TASKSET)`；
- [ ] `analysis_method_role` 已实现；固定 carry-in 推论生效后，CW-D/LOC-D 可合法使用 `CERTIFIED_TASKSET`；推论未生效时只能 diagnostic；
- [ ] 正式递归任务集失败前的 candidates 不会被误标为 `CERTIFIED`；
- [ ] LOC-$\Theta^{\mathrm{cw}}$ 仅读取 `source_analysis_certification_status=CERTIFIED_TASKSET` 的 CW 源运行；
- [ ] source CW、固定 carry-in 推论、依赖向量和哈希均有效时，目标 local candidate 才升级为 `CERTIFIED`；
- [ ] LOC-$\Theta^{\mathrm{cw}}$ 的完整兼容向量统一升级为 `CERTIFIED_TASKSET`；成功前缀保持 `PROVISIONAL_NOT_CERTIFIED`；该辅助证书不作为第三套主方法独立 proven ratio；
- [ ] LOC-$\Theta^{\mathrm{cw}}$ 的 `analysis_solver_status`、`n_tasks_evaluated`、`n_tasks_candidate_found`、`n_tasks_certified`、`first_non_candidate_priority` 和 `dominance_invariant_status` 已按聚合规则记录；
- [ ] 完整认证依赖域中的 LOC-$\Theta^{\mathrm{cw}}$ `NO_CANDIDATE` 会触发 conformance failure；
- [ ] source/target 的 taskset、priority、canonical $E_0$/service/power/unit hash、numeric mode/scale、theory hash 与 formal contract 全部一致后，依赖才可为 `VALID`；
- [ ] `rta_dependency_records.csv` 的来源状态字段统一且外键完整；
- [ ] 三类 local-only 已分开，正文新增证明只使用 `local_only_analytical`；
- [ ] 正式 CORE-1/CORE-2 每格 `NUMERIC_ERROR=0`；
- [ ] 非零 numeric error 会标记 `INVALID_NUMERIC_COVERAGE` 并作废参数格；
- [ ] theorem-backed 仿真使用 `ANALYSIS_CONSISTENT_ACCOUNT` 或已验证的 `PHYSICAL_WITH_CONSERVATIVE_SHADOW`；
- [ ] fixed-point 模式调度支付判断使用 $P_i^{\mathrm{int}}$、scaled harvest 和同一保守账户；
- [ ] shadow 模式逐 tick 支配关系检查为通过；
- [ ] `simulation_run_id` 包含所有会改变轨迹的账户、scale、scaled power 和 scaled harvest 信息；
- [ ] `simulation_taskset_summary.csv` 和 `simulation_job_results.csv` 只保存纯仿真事实；
- [ ] `service_trace_checks.csv` 按 `(simulation_run_id, analysis_run_id)` 保存服务曲线检查；
- [ ] `e0_trace_certificate_checks.csv` 和 `e0_job_certificate_checks.csv` 支持同一轨迹对应多个 $E_0$；
- [ ] `analysis_simulation_compatibility_checks.csv` 保存模型匹配；
- [ ] `simulation_bound_checks.csv` 引用 `service_trace_check_id`、`e0_certificate_check_id` 和 `compatibility_check_id`；
- [ ] applicability failure mask 能保存多个同时失败原因；
- [ ] 原单层 `bound_check_status` 已迁移；`soundness_check_status` 保留为最终审计结论；
- [ ] 只有 `APPLICABLE + EXCEEDS_CANDIDATE` 产生 `CERTIFIED_BOUND_VIOLATION`；
- [ ] `CERTIFIED_BOUND_VIOLATION=0`；
- [ ] $E_0=0$ 时证书状态为 `NOT_REQUIRED`；
- [ ] $E_0>0$ 时正式证书集合等于 $[0,H_{\mathrm{gen}})$ 内释放的全部作业，非空且正式分母只接受 `SATISFIED_ALL`；
- [ ] 正目标 $E_0$ 未被舍入为 0，论文报告使用 `E0_analysis_effective`；
- [ ] 服务曲线满足零点、非负、单调不减合同，且轨迹在具体分析曲线和 scaled trace 下通过完整有限域验证；
- [ ] 服务曲线验证使用 $H^{\mathrm{analysis}}$ 与 $\beta_l^{\mathrm{analysis}}$，未混用 raw/scaled 单位；
- [ ] generation/observation horizon、completion 后边界判断和 drain-cap 状态已经验收；
- [ ] 正式 CORE-3 中 `APPLICABLE+NOT_OBSERVED=0` 且 `DEADLINE_NOT_REACHED=0`；
- [ ] 纯仿真表只保存 completion censoring，candidate boundary 状态只存在于分析相关 bound-check 表；
- [ ] `actual_rho_e_raw` 与 `actual_rho_e_analysis` 均已计算，参数格验收使用后者；
- [ ] `POINTWISE_FLOOR` 模式使用 `service_rate_r_scaled_exact=Sr`，未擅自替换为 $\lfloor Sr\rfloor$；
- [ ] generation success rate、失败门槛和重采样次数已冻结并报告；
- [ ] CORE-4 使用 `base_generation_cell_id`、paired-family lineage 和确定性 transformation；
- [ ] release/harvest/adversarial 随机子流按标签独立派生；
- [ ] timeout 非零时 completed-only runtime 已明确标注，必要时执行右删失分析；
- [ ] tightness 的 job/task/taskset 权重和 bootstrap 聚类层级已预先冻结；
- [ ] 多重检验校正、功效目标和 runtime 环境合同已冻结；
- [ ] operational 与 analytical 配对比较已分开；理论新增证明不含 timeout、numeric error 或 dependency failure；
- [ ] sustainability 图表区分 RTA-input monotonicity 与 physical-system rerun；
- [ ] `ASAP_BLOCK_experiment_schema_v1_3_12.yaml` 与状态机校验通过；
- [ ] schema/data dictionary 精确等价，canonical serialization、条件 null、主键、外键、theorem-check 配对和 failure-mask 编码完整；
- [ ] `tasksets.csv`、`task_definitions.csv`、`release_traces.csv`、`harvest_traces.csv` 可完整重放；
- [ ] 每个运行绑定 build identity 与二进制哈希；
- [ ] manifest、formal contract、代码 commit、理论哈希、schema hash 和文件哈希一致；
- [ ] 旧 v20.4/v21 结果未混入正式主表。

全部通过后，才允许按本规范启动正式大规模运行。任何 formal contract 变更都必须重新执行 CORE-0B；若变更涉及算法、integerization 语义、状态机或 schema，则还必须重新执行 CORE-0A。


# 23. v1.3.12 机器合同闭合修订

<!-- ASAP_BLOCK_MACHINE_INTERFACE_MANIFEST_FILE: ASAP_BLOCK_machine_interface_manifest_v1_3_12.yaml -->
<!-- ASAP_BLOCK_MACHINE_INTERFACE_MANIFEST_SHA256: 26423a18282652d559e1d842fd834666f59f88ca2fca3382f2fb3e06f9213764 -->
<!-- ASAP_BLOCK_CANONICAL_ALGORITHM: SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12 -->

本节是 v1.3.12 的机器接口规范性修订；本次主修复是把 `TaskAnalysisRecord.failure_reason` 映射为可无损 round-trip 的结构化正式语义。若前文的接口字段名称、哈希名称或验证范围与本节冲突，以本节和机器可读 schema/data dictionary/canonical specification 为准。实验理论主线、两套正式 RTA、五配置消融以及 CORE-0～CORE-5 不变。

## 23.1 验证器不再修改后破坏构件

artifact validator 默认只读。只有显式使用 `--write-report` 时才原子写入 `validation_report.json`，并在同一事务中同步更新 `validation_report.json.sha256`。验证器内置“两次连续验证”负向/回归测试，保证写报告后再次验证仍通过。

## 23.2 跨文档验证声明的边界

artifact validator 机械验证：文件字节与 sidecar、formal template 嵌入绑定、schema/data dictionary 字段等价、canonical preimage 名称、机器接口 manifest、Markdown 必需章节及嵌入 manifest 摘要。它不声称证明全部自然语言与机器 schema 的语义等价。`cross_document_semantic_conformance=PASSED` 被废止，规范字段改为 `machine_interface_manifest_conformance`。

## 23.3 无循环哈希与 ID 预像

所有哈希和稳定 ID 统一使用 `SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12`。权威预像位于 `ASAP_BLOCK_canonical_serialization_v1_3_12.yaml`，包括 `plan_context_hash`、`formal_grid_hash`、`seed_derivation_context_hash`、`formal_seed_set_hash`、`run_plan_bundle_hash`、`request_payload_hash`、`request_id`、`expected_output_id`、`carry_in_vector_hash`、`dependency_record_hash` 和 `task_result_hash`。任何对象的自哈希字段在预像中置为 JSON null；预像不得包含自身或其下游派生量。

## 23.4 结果验证必须非空并执行硬约束

`FORMAL_RELEASE` profile 要求：

- `N_planned = N_accounted > 0`；
- 正式 generation、analysis 和按计划要求的 simulation/audit 结果非空；
- CORE-0B acceptance report 已通过；
- 正式主结果 `NUMERIC_ERROR=0`；
- 所有正式结果使用 CORE-0B 批准的 build identity；
- 候选、认证、任务模型、依赖一致性、证书域、candidate boundary 和 soundness 状态派生规则全部机械检查。

空表包、依赖环和 terminal 后继续写事件均必须失败。

## 23.5 pre-formal 与 formal 谱系分离

CORE-0A/PILOT 行只绑定 `plan_context_hash`，`formal_contract_hash` 必须为 null；CORE-0B/FORMAL 行必须同时绑定最终 `formal_contract_hash`。禁止在正式合同生成后回填 pre-formal 历史行。

## 23.6 CORE-0B 批准 build

formal contract 冻结 `approved_rta_build_identity_hash`、`approved_simulator_build_identity_hash`、`approved_scheduler_build_identity_hash` 和 `approved_audit_build_identity_hash`。CORE-0B 报告必须针对这些 build；正式运行使用任何其他 build 均为 release-gate failure。

## 23.7 请求、输出和执行状态

每种 `request_type` 的 `request_payload_hash` 字段集合、输出类型和输出表由 canonical specification 冻结。`request_outputs.csv` 将不可变计划输出身份、真实输出 hash 和结果主键连接起来。执行 attempt/event 必须连续，terminal 是最后事件，terminal 后不得追加 HEARTBEAT；retry 仅允许发生在冻结的 retryable terminal 后。

## 23.8 result/acceptance validator 分工

- artifact validator：规范构件和嵌入绑定；
- acceptance-report validator：报告自哈希、证据文件/哈希、门槛计数、批准 build 和 overall gate；
- result validator：完整运行包、CSV 类型/条件、主外键、谱系、状态机、硬不变量、非空覆盖和正式 release gate。

## 23.9 统计与随机性

正式 master-seed 来源在 CORE-0A 前承诺。PUBLIC_RANDOM_BEACON 模式承诺 beacon source、精确 round/time 和 extraction rule，而不是在揭示前伪造最终 seed。所有任务/轨迹随机流使用独立标签子流。

## 23.10 验证结论表述

允许写：`Artifact syntax and machine-interface manifest validation: PASSED`。在没有正式运行包、acceptance report 和 CORE-0B 证据时，禁止写 `runtime conformance`、`formal release gate` 或 `theorem soundness` 已通过。

## 23.11 逐任务结构化失败来源

`per_task_results.csv` 固定在 `task_certification_status` 后加入非空枚举列 `task_failure_reason_code`，并在条件字段区首位加入 nullable canonical text 列 `task_failure_detail`。正式枚举仅包含实际任务记录生产域：

- `NONE`；
- `NO_CANDIDATE`；
- `SOLVER_TIMEOUT`；
- `NUMERIC_ERROR`；
- `UPSTREAM_PREFIX_FAILURE`；
- `DEPENDENCY_NOT_APPLICABLE`；
- `DOMINANCE_INVARIANT_VIOLATION`；
- `UNKNOWN_CORE_STATUS`；
- `INTERNAL_CONFORMANCE_FAILURE`。

输入验证和未归属任务的基础设施异常发生在任务记录形成前，保留为 execution-level 状态；`DIAGNOSTIC_ONLY_NOT_CERTIFIED` 是认证状态；依赖 `INVALID` 由依赖表表达。因此它们不是任务失败码。

## 23.12 detail 规范化语言

`task_failure_detail` 使用 UTF-8、NFC、LF，非 null 时长度为 1～256 Unicode scalar；CSV 空字段唯一表示 null，空字符串不是合法正式值。禁止 NUL、CR、绝对 POSIX/Windows 路径、内存地址、时间戳、原始 traceback、非有限浮点文本以及 dict/set repr。当前每个 code 的 detail 是固定 null 或固定字面量，不允许调用者自由选择：

| code | canonical detail |
|---|---|
| `NONE` | null |
| `NO_CANDIDATE` | `closure exhausted through task deadline` |
| `SOLVER_TIMEOUT` | null |
| `NUMERIC_ERROR` | `numeric guard rejected analysis` |
| `UPSTREAM_PREFIX_FAILURE` | null |
| `DEPENDENCY_NOT_APPLICABLE` | null |
| `DOMINANCE_INVARIANT_VIOLATION` | `local result violated frozen carry-in dominance` |
| `UNKNOWN_CORE_STATUS` | `unrecognized core solver status` |
| `INTERNAL_CONFORMANCE_FAILURE` | `internal analyzer conformance failure` |

原始 Python exception/debug message 不是正式语义。producer 必须调用显式 fail-closed normalizer；未知 raw/origin 组合被拒绝，且 raw 字符串永不复制到 CSV。consumer 以 code 和结构化状态字段为准，不得从 detail 反推 solver 状态。

## 23.13 状态—失败原因矩阵

| task solver status | 允许的 code |
|---|---|
| `CANDIDATE_FOUND` | `NONE`；若形成正式 dominance counterexample，则为 `DOMINANCE_INVARIANT_VIOLATION` |
| `NO_CANDIDATE` | `NO_CANDIDATE`；LOC-Theta-cw 有效依赖域内的反例为 `DOMINANCE_INVARIANT_VIOLATION` |
| `TIMEOUT` | `SOLVER_TIMEOUT` |
| `NUMERIC_ERROR` | `NUMERIC_ERROR` |
| `NOT_EVALUATED_AFTER_PREFIX_FAILURE` | `UPSTREAM_PREFIX_FAILURE` |
| `NOT_APPLICABLE_DEPENDENCY` | `DEPENDENCY_NOT_APPLICABLE` |
| `INTERNAL_CONFORMANCE_FAILURE` | `UNKNOWN_CORE_STATUS` 或 `INTERNAL_CONFORMANCE_FAILURE` |

任一 `DOMINANCE_INVARIANT_VIOLATION` code 必须同时具有任务级 dominance violation 状态及 `NOT_CERTIFIED`；反向亦然。成功任务不得携带普通失败 code，失败任务不得使用 `NONE`。

## 23.14 哈希与 identity 边界

`task_result_hash` 是完整记录完整性哈希；v1.3.12 域为 `ASAP_BLOCK:TASK_RESULT:v1.3.12`，预像包含 `task_failure_reason_code` 和规范化后的 `task_failure_detail`。null 使用 JSON null，和所有字符串均不同；空字符串被拒绝。改变正式 code/detail 必须改变行字节和 hash。

`request_id`、`request_payload_hash`、`taskset_semantic_hash` 等输入 identity 不包含失败 code/detail。不同原始 debug 字符串若通过同一批准来源映射成同一 code/detail，正式行和 hash 相同，这是刻意冻结的等价关系。

## 23.15 round-trip 与 P0 回归

result validator 自测覆盖 candidate、no-candidate、timeout、numeric、prefix、dependency 和 dominance/conformance 七类对象的 object → row → CSV → reload，检查 code、detail、null、status、hash 和重复序列化字节。P0 回归使用相同 solver/certification 状态但不同正式失败来源，要求行、hash 和 reload 对象均可区分；修改 code 而不重算 hash 必须失败。

## 23.16 schema 与模板闭合

`ASAP_BLOCK_per_task_results_template_v1_3_12.csv` 是 41 列权威空模板。header 必须精确等于 schema canonical column order；v1.3.11 的 39 列 header、任意附加列或只加列而不更新 `task_result_hash` 预像均由验证器拒绝。


## 23.11 请求图与物化谱系

正式请求类型包括任务集生成、配对变换、release-trace 生成、harvest-trace 生成、分析、仿真以及四类审计。变换后的任务集通过 `materialization_request_id` 绑定变换请求，并保留 `source_generation_request_id`；不得把派生任务集伪装成新的独立生成成功。`run_plan_definition.csv` 的每种请求 payload 字段均由 canonical specification 穷尽定义，缺列或缺值必须失败。

## 23.12 BOUND_AUDIT 的批次输出

BOUND_AUDIT 在计划阶段绑定 simulation request 与 analysis request，不要求预先知道运行后才产生的 job ID。一个 BOUND_AUDIT 请求产生一条 `bound_audit_runs.csv` 顶层记录和零到多条作业级 `simulation_bound_checks.csv`；正式非空门槛要求应审计的作业集合完整，输出 bundle hash 覆盖顶层记录及全部作业行。

## 23.13 执行完成与分析超时的区分

正式计划请求必须全部以 `FINISHED` 结束并产生与 `expected_output_id` 一致的输出 bundle。分析算法内部达到冻结 timeout 属于一次成功完成的请求，其 `analysis_solver_status=TIMEOUT`，而不是请求执行状态 `TIMEOUT`。执行级 `TIMEOUT`、`OUT_OF_MEMORY`、`INTERRUPTED` 或 `INFRASTRUCTURE_FAILURE` 均使正式批次不完整，不能被当作普通未证明样本。

## 23.14 审计状态必须由事实表派生

`applicability_failure_mask`、`applicability_pending_mask`、`bound_theorem_applicability` 和 `soundness_check_status` 必须由被引用的任务认证、服务曲线、完整释放集 $E_0$ 证书、模型兼容性及原始边界比较机械派生。禁止手工填写 failure mask 以把 `EXCEEDS_CANDIDATE` 降级为适用域外。

## 23.15 正式发布验证边界

本规范包通过只证明机器合同构件自身闭合。正式实验发布还必须由 result validator 和 acceptance-report validator 对完整运行包执行：非空计划、全部请求入账、批准 build、一致外键、输出 bundle hash、CORE-0B 全门槛以及零 certified-bound violation。规范包验证通过不等于 CORE-0A、pilot、CORE-0B 或正式实验已经完成。


<!-- full natural-language semantic equivalence is not mechanically claimed -->

## 23.16 CORE-0 验收证据的可重放绑定

每个 `PASSED` gate 不得只提交人工填写的计数和任意文件哈希。正式合同必须逐 gate 冻结 `validator_file`、`validator_version` 与 `validator_sha256`，并采用 `ASAP_BLOCK_GATE_REPLAY_V1`。验收报告的首个 evidence 文件必须是自哈希的结构化 gate-evidence bundle；其余 evidence 输入必须全部使用根目录安全文件名并逐文件哈希。acceptance-report validator 必须重新执行 formal-contract 绑定的 gate validator，且重放输出中的 gate identity、formal-contract hash、bundle hash 与 counts 必须和验收报告完全一致。路径穿越、符号链接、未绑定 validator、伪造 counts、replay 非零退出或 replay 输出不一致均必须失败。

## 23.17 正式合同的强类型与范围验证

`formal_contract.yaml`、`generator_contract.yaml`、`simulation_contract.yaml` 和 `trace_generator_contract.yaml` 的“非空”不是合法性的充分条件。result validator 必须机械检查规范数值、正整数、比例范围、`0<u_{min}<=u_{max}<=1`、周期上下界、deadline 参数范围、generation-failure threshold、numerical mode 与 integerization 的条件一致性、电池模式与容量的条件 null、horizon 顺序、RNG/优先级常量、64 位小写十六进制哈希、无占位符以及所有 child-contract 自哈希。非法字符串占位、反向范围、负能量、错误 mode、未解析 `${...}` 或非规范数值均不得进入正式合同。

## 23.18 运行包中的验证器闭合

正式运行包必须同时包含并哈希绑定 artifact validator、result validator、acceptance-report validator、validation common、Markdown、schema、data dictionary、canonical specification、machine-interface manifest 和全部合同模板。result validator 必须核验 formal artifact bindings 与批准的 validator hash；缺失、替换、符号链接或目录逃逸均为 release-gate failure。

## 23.19 空 failure mask 的唯一编码

CSV 空单元格表示 null，不能同时表示“已检查且没有失败原因”。所有 `enum_set` failure/pending mask 的空集合统一编码为字面量 `EMPTY`；`EMPTY` 必须单独出现，禁止与其他成员通过 `|` 组合。`primary_applicability_failure_reason` 在 failure mask 为 `EMPTY` 时固定为 `NONE`，否则按 schema 中冻结的 failure-mask 优先级取首个原因。该规则由 common scalar validator 和 result validator 共同执行。


## 23.20 BOUND_AUDIT 的全作业覆盖

每个正式 `BOUND_AUDIT` 请求的作业级审计集合必须与其引用的 `simulation_job_results.csv` 中该 `simulation_run_id` 的完整作业集合精确相等。即使某作业没有数值 candidate，也必须保留对应审计行，并使用 `NO_NUMERIC_CANDIDATE` / `OUT_OF_THEOREM_SCOPE` 等规范状态记账；不得通过漏掉未完成、无 candidate 或超界作业来降低审计分母。`bound_audit_runs.csv` 的全部聚合计数必须由该完整作业集合重新计算。

## 23.21 语义字段的强类型冻结

ID、状态、模式和舍入方向不得以 `canonical_number` 或任意自由字符串代替。`generation_request_id`、`base_generation_cell_id` 等稳定身份字段使用 `identifier`；`rho_e_tolerance_mode`、`battery_mode`、`theta_source_mode`、`numeric_integer_type`、功耗舍入方向以及全部 analysis-simulation match status 使用冻结枚举。artifact validator 与 result validator 共享一份关键字段语义类型合同，防止 schema/data dictionary 在“字段集合一致”但字段类型错误时产生假通过。

## 23.22 $E_0=0$ 轨道的证书比例为空

`job_certificate_satisfaction_rate` 只对正 $E_0$ 且证书集合非空、逐作业状态已解析的轨迹有定义。无条件 $E_0=0$ 轨道的作业/轨迹证书状态为 `NOT_REQUIRED`，该比例必须为 null；正 $E_0$ 的空证书域或尚未检查状态同样必须为 null。不得用数值 0 将“不适用”伪装成 0% 满足率。

## 23.23 正式请求必须执行完成

`FORMAL_RELEASE` 中的全部 `FORMAL` 请求以及 `CORE0B` profile 中的全部 `CORE0B` 请求必须以执行级 `FINISHED` 终止并物化唯一输出。执行级 `TIMEOUT`、`OUT_OF_MEMORY`、`INTERRUPTED`、`INFRASTRUCTURE_FAILURE`、`CANCELLED` 或 `NOT_RUN_DEPENDENCY` 均使对应发布批次失败；它们不得仅凭“已入账”或残留输出文件被接受。分析算法内部 timeout 仍记录为 `FINISHED + analysis_solver_status=TIMEOUT`。
