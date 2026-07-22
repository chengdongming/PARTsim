# EXT-1B/B3-v2 正式确认协议 R1

## 1. 授权、基线与范围

本协议建立在提交
`b391ee6ad2854834ab1248ef713499350f195b9e`（合并 PR #44）上。B3-v2
校准的有效审计结论为 `CALIBRATION_PASS_PRIMARY_ONLY`，因此只授权独立 PR
预注册一个新的 FORMAL profile；校准本身没有创建或授权 `parameter_status:
FORMAL`。

授权来源是以下只读证据：

- `b3_v2_calibration_pr44_attempt3_20260722T043623Z.acceptance_report_effective_config.json`；
- `b3_v2_calibration_pr44_attempt3_20260722T043623Z.candidate_decision_effective_config.json`；
- `b3_v2_calibration_pr44_attempt3_20260722T043623Z.effective_config_reaudit.sha256`。

本 PR 只建立正式配置、profile 注册、正式结果文件路由、只读验收器、冻结测试和本
协议。它不修改 C++、ASAP/ALAP/ST-BLOCK 调度语义、原生 affordability comparator、
trace schema、timing event 语义、任务生成算法、B1/B2、校准审计器或校准决策器。

## 2. 唯一冻结参数

正式实验只确认校准前已经预注册且通过的主候选：

| 参数 | 冻结值 |
| --- | ---: |
| `recovery_margin_ticks` | `1` |
| `interpolation_rho` | `1/2` |
| `nominal_energy_supply_ratio` | `1/2` |

配置中的 `scenario.calibration_grid` 是 B3-v2 既有 scenario expander 的兼容字段；
在本 FORMAL profile 中三个维度都必须是长度为 1 的列表。它不是搜索网格，不产生
候选排名或参数选择。校准中的另外 7 个诊断组合不得出现在正式配置或正式样本中。

正式结果无论通过或失败都不能修改上述参数、替换 seed、删除证据或改用诊断候选。

## 3. 身份与确定性 seed 冻结

正式身份固定为：

- `experiment_id`：
  `asap-block-v9.3-ext1b3-b3-v2-formal-confirmation-r1`；
- `parameter_status`：`FORMAL`；
- `seed_space`：
  `EXT1B3_B3_V2_FORMAL_CONFIRMATION_R1_WORKLOAD_CONTRACT_V3`；
- `base_seed`：`524843528`；
- `bootstrap_seed`：`1070221135`；
- `bootstrap_resamples`：`2000`。

两个 seed 均在任何正式运行前由同一个确定性规则一次性生成：

```text
1 + big_endian_uint64(sha256(ASCII_LABEL)[0:8]) mod (2^31 - 1)
```

标签分别为：

```text
ASAP-BLOCK/v9.3/EXT-1B/B3/FORMAL-CONFIRMATION-R1/base-seed
ASAP-BLOCK/v9.3/EXT-1B/B3/FORMAL-CONFIRMATION-R1/bootstrap-seed
```

该规则不读取任何校准或正式结果，也不试跑多个 seed。两个结果与 B3-v2 校准 seed
`981301` / `9813903` 不同，也不与 B1/B2 正式 seed space 或仓库已登记 seed 重复。

## 4. 正式矩阵与隔离

配置文件为
`configs/v9_3_ext1b3_b3_v2_formal_confirmation_r1.yaml`，冻结：

- utilization：`1/5`、`2/5`；
- timing subtypes：`POSITIVE_SLACK_ENERGY_AVAILABLE`、
  `SLACK_LIMITED_CHARGING`；
- `tasksets_per_cell: 200`；
- `taskset_index_start: 0`；
- `structural_retry_limit: 24`；
- scheduler 顺序：`gpfp_asap_block`、`gpfp_alap_block`、
  `gpfp_st_block`；
- `resume: false`；
- simulation horizon / maximum horizon：`400` / `400`；
- timeout：`30` 秒；
- trace mode：`semantic`；
- `retain_trace: true`。

冻结规模为：

| 项目 | 数量 |
| --- | ---: |
| runner cells | 4 |
| paired instances | 800 |
| scheduler requests | 2400 |

正式 output root 为
`artifacts/v9_3_ext1b3_b3_v2_formal_confirmation_r1`，taskset store 为
`artifacts/v9_3_ext1b3_b3_v2_formal_confirmation_r1_taskset_store`。两者互不相同，
也不引用校准 output/store。正式任务集完全由新的 seed space 和 base seed 生成；校准
的 900 个 paired instances 不进入正式统计。

模拟器配置身份为 `./build/rtsim/rtsim`，正式验收冻结其 SHA-256 为：

```text
77240587c11ad151cd5beb216d7edcb4ac4f5285f9d44ada117e8c2245e5b089
```

## 5. 正式验收语义

runner 为 B3-v2 FORMAL 写入 `b3_formal_confirmation_summary.csv`，不会把正式证据
写成校准候选决策。只读验收器为：

```text
scripts/audit_v9_3_ext1b3_b3_v2_formal_confirmation.py
```

验收器直接读取原始 output 和 taskset store 证据，并 fail closed 核验：

- source/persisted config、metadata、simulator、seed 与 profile 身份；
- checkpoint `requested=terminal=2400`、`pending=0`、无 stop request；
- 800 个 generated/instance/accepted-attempt 双射；
- 2400 个唯一 request、attempt、result、terminal 和 B3 summary；
- 每个 paired instance 精确包含冻结顺序的三个 scheduler；
- attempt 序列、source-index 公式、workload、capacity、identity 和 pairing closure；
- 完整 output hash manifest；
- terminal 状态只允许 `SIM_PASS_OBSERVED` 或 `SIM_DEADLINE_MISS`；
- 4 个 formal units 只包含唯一冻结参数与两个预注册 timing subtypes。

数值门禁沿用已预注册主候选门禁。两个 charging utilization 分别以 200 为
denominator，合并以 400 为 denominator；initial target wait、prefix affordability
和相关 audit closure 必须为 100%，initial target positive-slack transition 必须至少
为 95%，所有 substitution、slack-exhaustion、termination-without-transition、audit
error 和 accepted capacity violation 必须为 0。

验收只输出：

- `FORMAL_CONFIRMATION_PASSED`；或
- `FORMAL_CONFIRMATION_FAILED`。

失败时必须保留全部证据，且 `required_next_action` 固定为
`RETAIN_EVIDENCE_AND_USE_NEW_PR_AND_NEW_PROTOCOL`。验收报告始终声明
`parameter_selection_permitted: false` 和
`parameters_may_be_adjusted_from_this_result: false`。

本 PR 不启动 2400 个正式请求。正式启动命令必须等本 PR 经确认后再冻结和提供。
