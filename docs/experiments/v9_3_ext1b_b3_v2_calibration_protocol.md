# EXT-1B/B3-v2 校准与正式候选冻结协议

## 1. 状态、基线与范围

本协议冻结于 PR #41 合并后的 `master`，基线提交为
`9a7cebc7`（`Merge pull request #41`）。本次变更只增加：

- 完整 B3-v2 校准配置；
- 校准结果的只读、fail-closed 验收脚本；
- 固定主候选的只读、fail-closed 决策脚本；
- 参数替换禁止规则及其单元测试；
- 本协议文档。

本次变更不修改调度器、RTA、B1、B2、B3-v1 或 PR #41 已冻结的机制语义，
也不新增或授权 FORMAL profile。本次变更不得运行完整校准或任何正式实验。

校准配置为
`configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml`。其
`parameter_status` 固定为 `CALIBRATION`。

## 2. 预先固定的唯一主候选

完整校准开始前，唯一主候选固定为：

| 参数 | 固定值 |
| --- | ---: |
| `recovery_margin_ticks` | `1` |
| `interpolation_rho` | `1/2` |
| `nominal_energy_supply_ratio` | `1/2` |

固定理由是保持 B3-v1 的 `rho/eta` 主参数可比，只新增最小正恢复裕量。

完整 `2 × 2 × 2` charging 网格中的其他 7 个组合是
`DIAGNOSTIC_ONLY`。它们必须完整运行并报告与主候选相同的指标，但没有参数选择权。

### 参数替换禁止规则

`scripts/decide_v9_3_ext1b3_b3_v2_candidate.py` 实施以下不可覆盖规则：

1. 只能读取预先固定主候选的门禁结果；
2. 不比较候选排名，不寻找最大值，也不读取“表现最好”的组合作为选择依据；
3. 主候选失败时，`selected_candidate` 必须为 `null`；
4. 即使一个或全部备选通过相同数值阈值，也不得替换主候选；
5. 主候选失败的校准证据必须原样保留；
6. 后续工作只能通过新 PR 和新协议重新设计，不得在当前协议内改参数重判；
7. 主候选通过也只允许后续独立 PR 提议 FORMAL profile，本决策不直接授权
   `parameter_status: FORMAL`。

任何缺失、未知字段、开放审计、报告篡改或候选身份变化均按主候选失败处理。

## 3. 完整校准规模

校准使用独立 seed space
`EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3`，并冻结：

- utilization：`1/5`、`2/5`；
- 每个 utilization 下 1 个 `POSITIVE_SLACK_ENERGY_AVAILABLE` control；
- 每个 utilization 下 8 个 `SLACK_LIMITED_CHARGING` 参数组合；
- 每个 utilization × scenario cell 接受 50 个任务集；
- `base_seed: 981301`；
- `structural_retry_limit: 24`；
- `resume: false`；
- fresh output root：`artifacts/v9_3_ext1b3_b3_v2_full_calibration`；
- fresh taskset store：
  `artifacts/v9_3_ext1b3_b3_v2_full_calibration_taskset_store`。

冻结规模为：

| 项目 | 数量 |
| --- | ---: |
| calibration units | 18 |
| accepted paired instances | 900 |
| schedulers per instance | 3 |
| scheduler requests | 2700 |

三个 scheduler 按固定顺序为 `gpfp_asap_block`、`gpfp_alap_block`、
`gpfp_st_block`。

任务集只可依据预先定义的结构谓词接受或拒绝。source index 固定为：

```text
source_taskset_index = logical_taskset_index * structural_retry_limit + attempt_index
```

所有 attempt 必须保留。不得依据调度结果重抽、替换或删除样本；不得复用旧 output
root 或旧 taskset store；不得以 `resume=true` 启动本校准。

## 4. 完整性门禁

主候选结果可被判定前，整个 18-unit 校准数据集必须闭合：

- 18 个 calibration rows、900 个 paired instances、2700 个唯一 requests 和
  2700 个唯一 terminal results 全部存在；
- 每个 paired instance 精确包含固定的三个 scheduler；
- `failures.csv` 为空；
- checkpoint 为 `requested=terminal=2700`、`pending=0` 且没有 stop request；
- terminal status 只能是 `SIM_PASS_OBSERVED` 或 `SIM_DEADLINE_MISS`；
- 不得存在 runner failure、timeout、internal error、horizon insufficient；
- timing transition 的 illegal、unclassifiable 和 audit error 计数均为零，全部
  timing audits 闭合；
- generation attempts 按
  `(scenario_cell_id, normalized_utilization, logical_taskset_index)` 分组后，
  恰好形成 `18 × 50 = 900` 个逻辑组；每个 unit 的 logical index 域精确为
  `0..49`；
- 每组 attempt index 唯一且从 0 连续到 accepted attempt；accepted 恰好一条且
  必须是最后一条，此前全部为 rejected；CSV 行序不影响排序后的验证，但任何缺口、
  重复、越界、全拒绝或 accepted 后追加 attempt 均 fail closed；
- 每行同时满足固定 retry limit、source-index 公式以及 `logical_index` /
  `source_index` 兼容字段一致性；
- 每个 accepted attempt 与 `generated_tasksets.csv`、`scenario_instances.csv` 和
  `simulation_requests.csv` 逐级闭合：accepted/source identity、attempt index、seed、
  final taskset identity、paired instance identity 以及固定三个 scheduler 必须形成
  双射；request 不得引用 rejected 或不存在的 attempt；
- hash、pairing、workload、source-index、taskset-store manifest 和 output-file
  audit 全部闭合；
- accepted capacity-infeasible task/taskset 计数为零；
- persisted `run_config.yaml`、metadata config hash、experiment ID、seed space 与
  冻结配置一致；
- metadata 的 selection policy 必须继续声明调度结果不参与样本选择。

任何一个 calibration unit 的完整性审计失败都会使完整校准 fail closed。备选组合
的机制指标不参与主候选选择，但备选证据缺失或不可审计会使整个预注册网格不完整。

`dataset_integrity` 必须显式报告 `logical_attempt_group_count`、
`complete_attempt_history_group_count`、`incomplete_attempt_history_group_count`、
`missing_attempt_index_count`、`duplicate_attempt_index_count`、
`accepted_not_last_count`、`multiple_accepted_count`、`no_accepted_count`，以及
`logical_index_domain_closed`、`attempt_sequence_audit_closed`、
`accepted_cross_table_identity_audit_closed`。完整性通过依赖这些计数达到冻结值且三个
closure 全部为 `true`。

`SIM_DEADLINE_MISS` 可原样保留为机制实验的合法终端状态，但不得解释为可调度性
证明或性能证据，也不得参与参数替换。

## 5. 主候选门禁

只有 fixed primary 的两个 utilization 单元及合并总体同时满足下表，且第 4 节的
完整性门禁通过，`calibration_passed` 才能为 `true`。

| 指标 | `U=1/5` | `U=2/5` | 总体 |
| --- | ---: | ---: | ---: |
| initial target job `target_wait_observed` | 100% | 100% | 100% |
| initial target job `target_positive_slack_transition` | ≥95% | ≥95% | ≥95% |
| `full_release_prefix_affordable` | 100% | 100% | 100% |
| `recovery_prefix_audit_closed` | 100% | 100% | 100% |
| `target_audit_closed` | 100% | 100% | 100% |
| target audit errors | 0 | 0 | 0 |
| prefix audit errors | 0 | 0 | 0 |
| later-target substitution | 0 | 0 | 0 |
| non-target substitution | 0 | 0 | 0 |
| transition after slack exhaustion | 0 | 0 | 0 |
| termination without transition | 0 | 0 | 0 |
| accepted capacity-infeasible tasks/tasksets | 0 | 0 | 0 |
| hash/pairing/workload/source/output audits | closed | closed | closed |

单 utilization 的 denominator 固定为 50，因此 transition 门槛的最小通过计数为
48；总体 denominator 固定为 100，最小通过计数为 95。脚本使用精确有理数
`19/20` 比较，不进行浮点舍入。

“later-target substitution”和“non-target substitution”是指初始 target job 未产生
合格正 slack transition 时，由后续同 task job 或非 target job 的 transition 替代。
这两者必须为零。任意作业的 `timing_activation` 不能替代初始 target job 的
`target_positive_slack_transition`。

## 6. 备选与 positive-control 报告

7 个备选 charging 组合逐 utilization 及总体完整报告第 5 节的同一组指标，包括
denominator、计数、精确比例、错误计数、capacity 计数及全部 audit closure。
报告中的每个备选必须标为：

```text
role: DIAGNOSTIC_ONLY
formal_selection_eligible: false
```

两个 positive-control unit 也分别报告相同字段。由于它们不适用 charging target
recovery contract，其 target charging denominator 固定为 0，并且不得进入主候选
门禁的分子或分母。

## 7. 只读验收与决策

完整实验由后续授权流程运行；本 PR 不执行。实验完成后，只读验收命令为：

```bash
python3 scripts/audit_v9_3_ext1b3_b3_v2_calibration.py \
  --config configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml \
  --output-root artifacts/v9_3_ext1b3_b3_v2_full_calibration \
  > b3_v2_calibration_acceptance.json
```

该脚本只读实验目录和 taskset store，JSON 写到 stdout；重定向目标由调用者显式
选择。返回码 0 表示主候选和完整性门禁均通过，返回码 1 表示 fail closed。

候选决策命令为：

```bash
python3 scripts/decide_v9_3_ext1b3_b3_v2_candidate.py \
  --config configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml \
  --output-root artifacts/v9_3_ext1b3_b3_v2_full_calibration \
  --acceptance-report b3_v2_calibration_acceptance.json \
  > b3_v2_candidate_decision.json
```

`--acceptance-report` 可省略。决策器的唯一权威数据源是 `--config` 与
`--output-root`：它必须在内存中直接调用 `audit_calibration()` 重新审计原始证据，
并只基于该结果决策。若提供 acceptance report，它仅是一致性副本；决策器分别生成
canonical JSON 并要求两份对象逐字段完全相等。解析错误、重复 JSON key、缺失/未知
字段或任意差异都会拒绝，外部报告不能单独授权通过。

即使内部审计报告声称 `passed=true`，决策器仍以 strict schema 重新验证完整 8 候选
参数积、唯一 primary 身份、两个 utilization、每个 metric 的类型/denominator/精确
ratio、overall 的逐字段求和、全部 gate checks 与 failed-check list，并从计数独立重算
逐 utilization 和总体门禁。`dataset_integrity.passed`、`primary_gate.passed`、
`numeric_gate_passed` 与 `calibration_passed` 必须和重算结果完全一致，不能作为被信任
的快捷授权位。

两个脚本均不修改 output root、taskset store、run config、metadata 或 CSV，也不生成
FORMAL profile；它们只向 stdout 输出 JSON。无法读取原始证据同样返回结构化拒绝。
主候选失败、原始证据缺失/损坏、外部副本不一致或替换规则被篡改时，决策必须包含：

```text
decision: REJECTED
selected_candidate: null
formal_profile_pr_permitted: false
required_next_action: NEW_PR_AND_NEW_PROTOCOL_REDESIGN
```

## 8. 后续 FORMAL profile 的必要条件

只有本协议固定主候选通过全部门禁，后续独立 PR 才可以新增 FORMAL profile。
该 PR 必须同时新增并冻结：

- `parameter_status: FORMAL`；
- 新 `experiment_id`；
- 新 formal seed space；
- 新 base seed；
- 新 bootstrap seed；
- 新 output root 和新 taskset store；
- `tasksets_per_cell: 200`；
- 2 utilization × 2 timing cells；
- 800 paired instances；
- 2400 scheduler requests。

正式 ST 门禁必须读取 initial target job 的
`target_positive_slack_transition`，不得使用任意作业的 `timing_activation`。

校准通过不自动创建 FORMAL profile、不自动改变 `parameter_status`，也不授权正式
实验。若主候选失败，本节全部失效，必须保留失败证据并重新走新 PR/新协议。
