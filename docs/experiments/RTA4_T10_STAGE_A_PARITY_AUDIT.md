# RTA4 T10 阶段 A parity 审计

## 结论

- `stage_b_authorized = false`
- 数学公式修改：否
- 根因分类：CAMPAIGN_TASK_FAMILY_NOT_CONNECTED、ENERGY_SERVICE_MAPPING_MISMATCH、OLD_EVIDENCE_OR_CONTRACT_INCONSISTENT
- 入口结果 parity mismatch：0
- 冻结证据回放 mismatch：0
- 输入语义 mismatch：0
- 原生身份域诊断差异单元：352
- 支配违反：0
- 第一处不一致：`{"field":"energy_service.beta(1)","frozen_implemented":"3602879701896397/36028797018963968","required_exact_linear":"1/10"}`

阶段 B 未获授权的决定性原因是：冻结脚本虽然把服务配置记录为 `1/10`，
实际执行却先将其物化成 binary64
`3602879701896397/36028797018963968`，随后按 binary64 逐项累加；
因此其 beta 前缀不等于所要求的精确 `beta(L)=L/10`。这一差异属于科学输入，
不能作为纯诊断字段忽略，也不能根据汇总认证数反推或改写。

## 证据与源码

- 外层证据归档 SHA-256：`8d77a94e0d0211dbbe3fa28eb940589cb6f865c07333ed275644bc41d8218759`
- 内层冻结归档 SHA-256：`cb46599fec4d0c362f888a0e96a16e65151cf5e84b12718f2e94fac95b2e3d4f`
- holdout SHA-256：`aa1414482d6192135e04ddc166a68e106a7f5cfe889cbda6c63dfb5c7a2d8505`
- 冻结入口 SHA-256：`0584f64856fb5b0b7e7108013ed33b0942beab71a493ca0e372baf3f8a5ef729`
- 确认 runner SHA-256：`3194bb4eb861c5d66f591779de44b2db357736f60fdb1f662905dee8f4d01268`
- 冻结源码：`4a04e2afd88424b8ebe85500b0561d7203c64e4e` / tree `51ddb853c1e47244f0d1407ec665742c141dae48`
- 当前源码：`c379cd53baee43466eccfa87bf018652d87c481e` / tree `7763d6b81c695560ec522bfdbe2467ea55917b24`

## 完整比较计数

- taskset/E0 单元：352
- 方法级单元：1408
- 逐任务结果字段比较所覆盖的任务结果行：14080
- 规范化十任务任务集：176
- 脚本失败：0
- 未分类内部错误：0

| E0 | CW | LOC | PH | SEQ |
|---|---:|---:|---:|---:|
| 21/40 | 10 | 29 | 125 | 131 |
| 11/20 | 25 | 54 | 143 | 152 |

## 根因判定

1. `CAMPAIGN_TASK_FAMILY_NOT_CONNECTED`：V3 CORE-1 normalizer 固定使用
   `GENERAL_RANDOM_CONSTRAINED_DEADLINE`，现有 E1 YAML 没有 task-source、
   T10 背景任务或能量服务字段。因此既有 9600 请求是一般随机负对照。
2. `ENERGY_SERVICE_MAPPING_MISMATCH`：冻结确认配置写入 `service_rate=1/10`，
   但冻结脚本实际执行的是 binary64 物化常量轨迹及 binary64 区间累加，
   从 `L=1` 起就与精确 `L/10` 不同。
3. `OLD_EVIDENCE_OR_CONTRACT_INCONSISTENT`：旧认证计数与旧实现完全可复现，
   但不能同时把这些结果解释为精确 `beta(L)=L/10` 的结果。

未发现 task C/D/T/power、RM 顺序、方法分派或当前正式 adapter 数学结果差异；
入口 B 在接收入口 A 的实际物化服务前缀时，与冻结入口逐任务完全等价。
352 个单元的旧/正式原生 taskset、priority、service、power 身份字符串均不同，
原因是二者使用不同的身份域和证书 schema；对应规范化内容 SHA、服务前缀、
功耗向量及 `exact_input_identity` 全部一致，因此这些差异只列为诊断，不计入
科学输入或数学 parity mismatch。

## 门禁

阶段 A 的证据完整性、176 任务集规范化、旧统计复现和双入口数学 parity
均通过；但“精确 `beta(L)=L/10`”科学合同与冻结证据不一致。因此按失败关闭
规则，`stage_b_authorized=false`，不得继续实现 V4 或正式 campaign，直到用户
明确选择冻结旧 binary64 服务合同，或提供确由精确 `Fraction(L,10)` 产生的
176 个逐任务基准结果。
