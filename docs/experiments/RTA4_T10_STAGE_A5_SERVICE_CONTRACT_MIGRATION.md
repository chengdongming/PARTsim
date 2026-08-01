# RTA4 T10 阶段 A.5 服务合同迁移审计

## 结论

- `stage_b_infrastructure_authorized = true`
- `formal_t10_campaign_authorized = false`
- exact direct/adapter parity mismatch：0
- exact 输入 identity mismatch：0
- exact float 决策路径：0
- 预期 exact-input identity 变化：1408
- 数学/认证结果变化方法单元：24
- 支配违反：0
- 脚本失败：0
- 未分类内部错误：0

精确服务由 `Fraction(length, 10)` 直接构造。历史服务合同仅用于迁移审计，
不作为正式 campaign 选项，也不声明等价于精确 `beta(L)=L/10`。
配对审计两侧均使用 manifest 的精确有理功耗，故这里的 legacy 是服务合同
对照而不是完整历史 binary64 功耗回放；完整历史复现仍由 Stage A 产物记录。

## 身份与证据

- 仓库输入提交：`cee718db62b390e75ac0e7f79511972d04081140`
- 仓库输入 tree：`218117db02073edda45ca2b4de9ddcacdd183550`
- Stage A 提交：`cee718db62b390e75ac0e7f79511972d04081140`
- Stage A tree：`218117db02073edda45ca2b4de9ddcacdd183550`
- 外层证据归档：`8d77a94e0d0211dbbe3fa28eb940589cb6f865c07333ed275644bc41d8218759`
- 内层冻结归档：`cb46599fec4d0c362f888a0e96a16e65151cf5e84b12718f2e94fac95b2e3d4f`
- holdout：`aa1414482d6192135e04ddc166a68e106a7f5cfe889cbda6c63dfb5c7a2d8505`
- 冻结入口：`0584f64856fb5b0b7e7108013ed33b0942beab71a493ca0e372baf3f8a5ef729`
- 确认 runner：`3194bb4eb861c5d66f591779de44b2db357736f60fdb1f662905dee8f4d01268`

## 执行规模

- 任务集：176
- taskset/E0 单元：352
- 服务迁移方法级比较：1408
- 逐任务记录：14080
- exact 执行入口：直接数学入口与当前正式 unified adapter/worker 入口

## 配对迁移统计

| E0 | 方法 | Legacy | Exact | Both | Legacy only | Exact only | Neither | 认证变化 | 响应向量变化 | 最大绝对 R 变化 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 21/40 | CW | 10 | 10 | 10 | 0 | 0 | 166 | 0 | 3 | 1 |
| 21/40 | LOC | 31 | 31 | 31 | 0 | 0 | 145 | 0 | 4 | 1 |
| 21/40 | PH | 125 | 125 | 125 | 0 | 0 | 51 | 0 | 1 | 1 |
| 21/40 | SEQ | 131 | 131 | 131 | 0 | 0 | 45 | 0 | 1 | 1 |
| 11/20 | CW | 29 | 30 | 29 | 0 | 1 | 146 | 1 | 1 | 0 |
| 11/20 | LOC | 55 | 56 | 55 | 0 | 1 | 120 | 1 | 2 | 2 |
| 11/20 | PH | 164 | 164 | 164 | 0 | 0 | 12 | 0 | 4 | 1 |
| 11/20 | SEQ | 169 | 169 | 169 | 0 | 0 | 7 | 0 | 2 | 2 |

## 首个数学或认证变化

```json
{
  "cell_key": "T10_BALANCED|15|21/40",
  "diffs": [
    {
      "current": 7,
      "frozen": 8,
      "path": "$.carry_trace[4].theta_by_task[3][1]"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.carry_trace[5].theta_by_task[3][1]"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.response_vector[3]"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.task_results[3].candidate_response_time"
    },
    {
      "current": 21,
      "frozen": 28,
      "path": "$.task_results[3].checked_h_count"
    },
    {
      "current": 24,
      "frozen": 34,
      "path": "$.task_results[3].checked_q_count"
    },
    {
      "current": 6,
      "frozen": 7,
      "path": "$.task_results[3].checked_w_count"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.task_results[3].closing_w"
    },
    {
      "current": 24,
      "frozen": 34,
      "path": "$.task_results[3].envelope_call_count"
    },
    {
      "current": 5,
      "frozen": 6,
      "path": "$.task_results[3].witness_h"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.task_results[4].carry_in_values_used[3][1]"
    },
    {
      "current": 7,
      "frozen": 8,
      "path": "$.task_results[5].carry_in_values_used[3][1]"
    }
  ],
  "exact_certified": false,
  "legacy_certified": false,
  "method": "CW"
}
```

## 授权边界

基础设施授权只表示 exact direct/adapter 等价、精确输入无 float 决策路径、
身份完整、无内部错误且支配关系成立。正式 T10 campaign 仍保持未授权；
必须由用户另行冻结任务生成合同、新正式种子、样本数、E0 和统计方案。
