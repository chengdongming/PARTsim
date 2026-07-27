# B4-PE-I4A-0 协议补充：阶段算法范围与确定性身份

## 1. 背景与边界

本补充解决 B4-PE-I4A 前置检查发现的两个阻断项：Pilot 的算法范围，以及 taskset、source、case 的确定性 seed/ID 派生规则。原冻结文档 `ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md` 和公共系统模板保持不变。

本补充不实现 manifest，不改变任何数值实验参数、调度语义、公共任务生成器、公共 runner、scheduler、RTA、CMake 或输出 schema。与并行 RTA 分支的隔离边界是：本补充只新增 `experiments/b4_priority_energy/` 内的协议与协议测试，不读取或修改 RTA 实现。

`protocol_resolution_v1.json` 是 canonicalization、seed、ID、阶段算法、计数和复用键的机器合同来源；本 Markdown 给出同一合同的人类可读说明。参考派生与测试必须读取 JSON 中的域、摘要切片、字节序、掩码、前缀和长度，不得维护另一套独立派生常量。

## 2. 三阶段算法范围

Pilot 严格使用冻结文档 §10.3 列出的五个算法，不补成九算法：

1. `ASAP-BLOCK`
2. `ASAP-NONBLOCK`
3. `ASAP-SYNC`
4. `ALAP-BLOCK`
5. `ST-BLOCK`

Formal Main 和 Negative Control 使用冻结文档对完整九算法的固定顺序：

1. `ASAP-BLOCK`
2. `ASAP-NONBLOCK`
3. `ASAP-SYNC`
4. `ALAP-BLOCK`
5. `ALAP-NONBLOCK`
6. `ALAP-SYNC`
7. `ST-BLOCK`
8. `ST-NONBLOCK`
9. `ST-SYNC`

## 3. 精确计数公式

- Pilot：`3 utilization levels × 4 lambda_E levels × 2 rho_E levels × 20 tasksets per utilization × 5 algorithms = 2400 cases`。
- Formal Main：`5 utilization levels × 4 lambda_E levels × 100 tasksets per utilization × 9 algorithms = 18000 cases`；`rho_E="2"` 为固定值，不形成乘数。
- Negative Control：`3 utilization levels × 2 lambda_E levels × 100 reused formal tasksets per utilization × 9 algorithms = 5400 cases`；`rho_E="1"` 为固定值，不形成乘数。

Pilot 的 20 个重复在每个 utilization 层内编号 `1..20`。Formal 的 100 个重复在每个 utilization 层内编号 `1..100`。Negative Control 使用对应 Formal taskset 的相同重复编号，不建立新的 taskset pool。

## 4. 复用键

稳定身份使用 `identity_protocol="B4-PE-v5.2/I4A-0-v1"`。`taskset_pool` 只有 `pilot` 和 `formal`：Pilot 使用独立的 `pilot` pool；Formal Main 与 Negative Control 使用同一个 `formal` pool。这实现 Pilot/formal seed 分离，并使 Negative Control 复用对应 Formal taskset。

| 字段 | taskset_key | source_key | case_key | 依据 |
|---|---|---|---|---|
| `identity_protocol` | 直接 | 直接 | 直接 | 版本化身份域 |
| `taskset_pool` | 直接 | 经 `taskset_id` | 经 `taskset_id` | Pilot seed 与 formal seed 分离；Negative 复用 formal pool |
| `utilization` (`U_norm`) | 直接 | 经 `taskset_id` | 经 `taskset_id` | U 定义基础计算任务集 |
| `replicate_index` | 直接 | 经 `taskset_id` | 经 `taskset_id` | utilization 层内独立重复编号 |
| `phase` | 否 | 否 | 直接 | 阶段属于请求身份；taskset 由 pool 管理复用 |
| `lambda_E` | 否 | 直接 | 经 `source_id` | 定义 offered source 的缩放条件 |
| `source_profile`、`horizon_ms` | 否 | 直接 | 经 `source_id` | 定义三阶段确定性 source |
| `rho_reference="2"` | 否 | 直接 | 经 `source_id` | 电池和 source 统一以主实验 rho 为参考 |
| `E0` | 否 | 由 `taskset_id` 与 `E0_rule` 确定 | 经 `source_id` | 同一 taskset 跨 rho 冻结 |
| `Emax` | 否 | 由 `taskset_id` 与 `Emax_rule` 确定 | 经 `source_id` | 同一 taskset 跨 rho 冻结 |
| `alpha` | 否 | 由 `taskset_id`、`lambda_E` 与 `alpha_rule` 确定 | 经 `source_id` | 同一 taskset/lambda 跨 rho 冻结 |
| 实验 `rho_E` | 否 | 否 | 直接 | 只改变 task energy factor 分布，不改变 taskset/source |
| `algorithm` | 否 | 否 | 直接 | 算法只改变 case |
| 绝对路径、时间戳、PID、运行/算法顺序、临时目录 | 否 | 否 | 否 | 非语义运行环境，不得影响稳定身份 |

规范键字段精确为：

- `taskset_key`：`identity_protocol`、`taskset_pool`、`utilization`、`replicate_index`。
- `source_key`：`identity_protocol`、`taskset_id`、`lambda_E`、`source_profile`、`horizon_ms`、`rho_reference`、`E0_rule`、`Emax_rule`、`alpha_rule`。
- `case_key`：`identity_protocol`、`phase`、`taskset_id`、`source_id`、`rho_E`、`algorithm`。

当前三阶段 source 是由 taskset 和冻结三阶段配置完全决定的确定性 source，因此 `source_seed` 必须为 `null`。若未来协议显式引入真正随机的 source，才使用随机 source seed 规则；该变化需要新协议版本。

`unresolved_fields` 为空。

## 5. Canonical JSON

每个稳定 key 必须先验证字段集合精确匹配其 schema，禁止未知字段和 Python/JSON 浮点数，然后按以下规则序列化：

- UTF-8；
- object key 字典序；
- separators 为 `,` 和 `:`；
- 无空格；
- `ensure_ascii=false`；
- 十进制参数使用冻结词法的字符串（例如 `"0.70"`）；
- 不使用 binary64 默认字符串化；
- `utilization`、`lambda_E`、`rho_E` 和 `rho_reference` 只接受十进制字符串，拒绝 float、int 和 bool；
- `replicate_index` 和 `horizon_ms` 只接受真正的 Python/JSON integer，明确拒绝 bool；
- 不包含绝对路径、时间戳、PID、运行顺序、算法执行顺序或临时目录。

Python 参考形式：

```python
json.dumps(
    key,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
```

## 6. Seed 派生

Taskset seed：

```text
material = b"B4-PE/TASKSET-SEED/v1\n" + canonical_json(taskset_key)
digest = SHA-256(material)
taskset_seed = int.from_bytes(digest[0:8], "big") & 0x7fffffffffffffff
```

这里取 SHA-256 digest 的前 8 bytes（`start_byte=0`、`length_bytes=8`），不是前 8 个 hex 字符；按 big-endian 转换并施加 63 位掩码 `0x7fffffffffffffff`。结果 0 合法。碰撞、duplicate 或非法输入一律 fail closed，不重试、不加一、不换 digest 片段。

真正随机的 source seed：

```text
material = b"B4-PE/SOURCE-SEED/v1\n" + canonical_json(source_key)
digest = SHA-256(material)
source_seed = int.from_bytes(digest[0:8], "big") & 0x7fffffffffffffff
```

随机 source 同样取 digest 的前 8 bytes（不是前 8 个 hex 字符）、使用 big-endian 和 `0x7fffffffffffffff`，结果为 63 位非负整数并允许 0。确定性 source 的 `source_seed` 为 `null`。两类 seed 都不做隐式替换、加一或重试。

## 7. 稳定 ID 派生

```text
taskset_id = "ts-" + SHA256(
  b"B4-PE/TASKSET-ID/v1\n" + canonical_json(taskset_key)
).hexdigest()

source_id = "src-" + SHA256(
  b"B4-PE/SOURCE-ID/v1\n" + canonical_json(source_key)
).hexdigest()

case_id = "case-" + SHA256(
  b"B4-PE/CASE-ID/v1\n" + canonical_json(case_key)
).hexdigest()
```

三个 ID 均保留完整 64 个十六进制摘要字符。`algorithm` 只进入 `case_key`；因此它影响 `case_id`，但不影响 `taskset_id` 或 `source_id`。输出根目录和算法执行顺序不进入任何 key。

## 8. Fail-closed 合同

- key 字段缺失、出现未知字段、未知 phase/algorithm、数值参数不是冻结十进制字符串时失败；
- Pilot 不是精确五算法、算法缺失、重复或顺序变化时失败；
- 三阶段计数与冻结期望不一致时失败；
- 任何两个不同 canonical taskset key 得到相同 taskset seed 或 ID 时失败；
- 真正随机 source 的不同 canonical source key 得到相同 source seed，或不同 source key 得到相同 source ID 时失败；
- 不得通过加一、重试、截断更多摘要位或删除对象静默修复碰撞；
- 确定性 source 若出现非 null seed 时失败；
- 绝对路径、时间戳、PID、运行顺序、算法执行顺序或临时目录进入稳定 key 时失败。

Duplicate 与 collision 必须明确区分并使用可区分的错误：

1. **Duplicate**：同一个 canonical key 重复出现，报告 `duplicate` 并 fail closed；不得静默去重或保留第一个对象。
2. **Seed collision**：两个不同 canonical key 得到同一 seed，报告 `seed collision` 并 fail closed；不得加一、加 salt、重试或换 digest 片段。
3. **ID collision**：两个不同 canonical key 得到同一 ID，报告 `ID collision` 并 fail closed；不得自动重命名、加 salt 或截换摘要。

Formal Main 与 Negative Control 对相同 `utilization` 和 `replicate_index` 都使用 `taskset_pool="formal"`，必须得到完全相同的 taskset key、taskset seed 和 taskset ID；`phase`、`rho_E`、`lambda_E` 和 `algorithm` 均不进入 taskset key。

## 9. 已授权的文档身份迁移

R2/master 整合仅迁移
`docs/experiments/ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md`
的字节身份。PR #58 删除第 2 行的两个行尾空格，使 SHA-256 从
`0fee308839f2097664a63a21f8806128c868b1016fab2712e67892356961be52`
变为
`5e168664d9ce2062bf2418d2280195124c08b1311d1de1e280d20822965c0581`。
该迁移由 B4-PE R2/master integration 明确授权，仅适用于本次整合：
`semantic_change=false`、`scientific_contract_change=false`，并继续强制
fail-closed 的 byte-exact SHA 身份检查。

## 10. JSON 规范化投影

以下区块由 `protocol_resolution_v1.json` 按 key 排序、紧凑 JSON 动态投影。测试从 JSON 重新生成这些行并逐行核对；它不是测试文件中的第二套合同。

```text
canonicalization={"booleans_as_integers":"forbidden","decimal_parameters_are_strings":true,"decimal_string_fields":["lambda_E","rho_E","rho_reference","utilization"],"decimal_string_pattern":"^(0|[1-9][0-9]*)(\\.[0-9]+)?$","encoding":"UTF-8","ensure_ascii":false,"forbid_binary64_numbers":true,"forbidden_identity_fields":["absolute_path","algorithm_order","execution_index","generated_at","output_path","output_root","pid","run_order","temporary_directory","timestamp"],"integer_fields":["horizon_ms","replicate_index"],"object_keys":"lexicographic","reject_unknown_key_fields":true,"separators":[",",":"]}
phase_algorithms={"formal_main":["ASAP-BLOCK","ASAP-NONBLOCK","ASAP-SYNC","ALAP-BLOCK","ALAP-NONBLOCK","ALAP-SYNC","ST-BLOCK","ST-NONBLOCK","ST-SYNC"],"negative_control":["ASAP-BLOCK","ASAP-NONBLOCK","ASAP-SYNC","ALAP-BLOCK","ALAP-NONBLOCK","ALAP-SYNC","ST-BLOCK","ST-NONBLOCK","ST-SYNC"],"pilot":["ASAP-BLOCK","ASAP-NONBLOCK","ASAP-SYNC","ALAP-BLOCK","ST-BLOCK"]}
phase_counts={"formal_main":{"expected":18000,"fixed_dimensions":{"rho_E":"2"},"formula":[{"dimension":"utilization_levels","factor":5,"values":["0.2","0.3","0.4","0.5","0.6"]},{"dimension":"lambda_E_levels","factor":4,"values":["0.70","0.85","1.00","1.15"]},{"dimension":"tasksets_per_utilization","factor":100,"replicate_index":"1..100"},{"dimension":"algorithms","factor":9}]},"negative_control":{"expected":5400,"fixed_dimensions":{"rho_E":"1","taskset_pool":"formal"},"formula":[{"dimension":"utilization_levels","factor":3,"values":["0.3","0.4","0.5"]},{"dimension":"lambda_E_levels","factor":2,"values":["0.85","1.00"]},{"dimension":"reused_formal_tasksets_per_utilization","factor":100,"replicate_index":"1..100"},{"dimension":"algorithms","factor":9}]},"pilot":{"expected":2400,"formula":[{"dimension":"utilization_levels","factor":3,"values":["0.3","0.4","0.5"]},{"dimension":"lambda_E_levels","factor":4,"values":["0.70","0.85","1.00","1.15"]},{"dimension":"rho_E_levels","factor":2,"values":["1","2"]},{"dimension":"tasksets_per_utilization","factor":20,"replicate_index":"1..20"},{"dimension":"algorithms","factor":5}]}}
key_schemas={"case_key":["identity_protocol","phase","taskset_id","source_id","rho_E","algorithm"],"source_key":["identity_protocol","taskset_id","lambda_E","source_profile","horizon_ms","rho_reference","E0_rule","Emax_rule","alpha_rule"],"taskset_key":["identity_protocol","taskset_pool","utilization","replicate_index"]}
phase_taskset_pool={"formal_main":"formal","negative_control":"formal","pilot":"pilot"}
seed_derivation={"source":{"byte_order":"big","collision_policy":"fail_closed","deterministic_source_seed":null,"digest_slice":{"length_bytes":8,"start_byte":0},"domain":"B4-PE/SOURCE-SEED/v1\n","hash":"SHA-256","mask_hex":"0x7fffffffffffffff","random_source_only":true,"result_bits":63,"zero_allowed":true},"taskset":{"byte_order":"big","collision_policy":"fail_closed","digest_slice":{"length_bytes":8,"start_byte":0},"domain":"B4-PE/TASKSET-SEED/v1\n","hash":"SHA-256","mask_hex":"0x7fffffffffffffff","result_bits":63,"zero_allowed":true}}
id_derivation={"case":{"collision_policy":"fail_closed","digest_hex_length":64,"domain":"B4-PE/CASE-ID/v1\n","hash":"SHA-256","id_prefix":"case-","use_full_hexdigest":true},"source":{"collision_policy":"fail_closed","digest_hex_length":64,"domain":"B4-PE/SOURCE-ID/v1\n","hash":"SHA-256","id_prefix":"src-","use_full_hexdigest":true},"taskset":{"collision_policy":"fail_closed","digest_hex_length":64,"domain":"B4-PE/TASKSET-ID/v1\n","hash":"SHA-256","id_prefix":"ts-","use_full_hexdigest":true}}
collision_policies={"different_canonical_keys_same_id":"error","different_canonical_keys_same_seed":"error","duplicate_canonical_key":"error"}
source_contract={"E0_rule":"E_burst_ref","Emax_rule":"2*E_burst_ref","alpha_rule":"(lambda_E*E_dem_nom(H)-E0)/22s","horizon_ms":30000,"rho_reference":"2","source_profile":"three-stage-offered-harvest-v1","source_type":"deterministic"}
```

本补充只冻结算法范围、计数和确定性身份，不能作为运行实验或修改调度语义的授权。
