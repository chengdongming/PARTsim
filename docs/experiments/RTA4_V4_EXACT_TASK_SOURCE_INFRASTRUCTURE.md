# RTA4 V4 精确任务输入基础设施说明

## 边界与授权状态

- 不可变 Stage A：commit `cee718db62b390e75ac0e7f79511972d04081140`，tree `218117db02073edda45ca2b4de9ddcacdd183550`。
- Stage A.5 审计提交：`9b437126`，其中 `stage_b_infrastructure_authorized=true`，但 `formal_t10_campaign_authorized=false`。
- Stage A 报告及 artifact 中原有的 `stage_b_authorized=false` 未修改。
- 本基础设施仅提供规范化、身份生成、dry-run、审计回放和测试入口；不会创建正式输出或 taskset-store 命名空间。
- T10 论文 campaign 文件仍包含 `UNAUTHORIZED_PARAMETER_PLACEHOLDER`，在新任务生成合同、新种子、样本数、E0 和统计方案另行冻结前无法通过 V4 配置解析。

## 通用 V4 输入合同

V4 使用独立 profile/schema/plan/config/store/source-closure 域，不接受 V1/V2/V3 身份。正式 campaign 必须显式提供 `task_source`、`energy_service`、`e0`、`methods`、`priority_policy` 和 `processors`；缺失、未知字段和科学浮点数均 fail closed。

`task_source` 注册两种模式：

1. `EXPLICIT_TASKSET_MANIFEST`：绑定原文件字节 SHA、规范化语义 SHA、任务顺序 SHA、逐任务集内容 SHA/身份及完整内容证书；运行前重新读取并防止 TOCTOU。
2. `GENERATED_FAMILY`：通过版本化 registry 选择 family，参数没有隐含默认值；已注册 `GENERAL_RANDOM_CONSTRAINED_V1` 与 `T10_BALANCED_V1`。

两种来源先规范化成相同的 `TasksetV4`，CW/LOC/PH/SEQ adapter 不读取或分派 `family_id`。任务来源模式、family、参数或 manifest 任一字节变化都会改变科学配置、plan、taskset-store 和 source-closure 身份。

energy-service registry 包含：

- `EXACT_LINEAR_SERVICE_V1`：论文主线显式配置 `rate="1/10"`，服务前缀由 `Fraction(length, 10)` 直接构造；源码中没有 `float`、`Fraction.from_float` 或 `Decimal.from_float` 决策路径。
- `VERIFIED_SHARED_ENERGY_MATERIAL_V1`：只接受显式注入且 service/beta/build 身份一致的既有 shared-energy material；不允许隐含 solar 回退。

`LEGACY_BINARY64_MATERIALIZED_LINEAR_SERVICE_V1` 未进入 V4 registry，仅保留在 Stage A.5 审计和历史回归工具中。

## 176 个只读回归任务集

只读 manifest：`artifacts/audit/rta4_t10_holdout_176_explicit_manifest_v4.json`

- 外部 holdout SHA-256：`aa1414482d6192135e04ddc166a68e106a7f5cfe889cbda6c63dfb5c7a2d8505`
- manifest 文件 SHA-256：`ae558a57688e784f58b39c5902185cc3fd0fb29767beffc82be6516e02e56f56`
- manifest 规范化语义 SHA-256：`3bb18538e0371a1a1ec0b879f8feaf72c51b0af04f53e7a3d280f2a52c8ee54e`
- explicit task-source identity：`8aaad7ed63ee58ea3954e83f7391432b31abdcf6c3772af74dafffa2e495eae0`
- content-certificate identity：`ad8b2025cd3c594bf8995bcc8b30a9331cecdfe2a6c6a437337aa30c03c6414f`
- recovered generated-family identity：`ceb0afe02df4eee27d72aa2aefebd62188faec7c46ac74557a3441e8653f8273`

重建脚本逐项核对 176 个外部证据任务集及种子后才写出 manifest；没有从认证汇总数字猜测或重建任务。

V4 parity harness 对同一 manifest 执行冻结 spotcheck 直接数学入口和 V4 unified adapter。完整回放覆盖 1408 个方法单元、14080 条逐任务记录；输入 mismatch、adapter mismatch、内部错误、脚本错误和支配违反均为 0。精确服务下认证数为：

| E0 | CW | LOC | PH | SEQ |
|---|---:|---:|---:|---:|
| `21/40` | 10 | 31 | 125 | 131 |
| `11/20` | 30 | 56 | 164 | 169 |

这些是 exact 合同的回归结果，不用于要求或调整 legacy 计数。

## 独立合并前阻塞项

现有 CORE-0A lineage 全套门禁的 `8 failed, 55 errors` 在基线 commit `c379cd53baee43466eccfa87bf018652d87c481e` 上同样存在。它属于独立的合并前阻塞项；本分支没有删除、跳过或弱化这些测试，也没有进行无关修复。
