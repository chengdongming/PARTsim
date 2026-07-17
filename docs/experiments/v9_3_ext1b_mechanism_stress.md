# v9.3 EXT-1B 九算法机制压力实验

## 目的与边界

EXT-1A 在一般随机输入上比较九个调度器。EXT-1B 使用独立 seed 空间和结果无关的结构谓词，主动构造能暴露调度机制差异的输入。EXT-1B 不替代 EXT-1A，也不改变 scheduler、RTA、CORE-1 或 CORE-2。

仿真通过只表示在给定 horizon 内观察到足够作业且没有 deadline miss，不能解释为可调度性证明。timeout、internal error 和 horizon insufficient 都是不可比较终态，既不是 pass，也不是 miss。

## 三个研究问题

### EXT-1B1：BYPASS_STRESS

研究最高优先级作业因能量不足时，NONBLOCK 对低优先级、低单位能耗作业的绕过是否真实发生，以及它怎样改变 deadline 和 top-M outcome。

任务按实际 RM priority rank 排序，并使用系统 workload 模型中的实际每 tick 能耗。构造 h 和 l，满足 priority(h) < priority(l) 且 e_l < e_h。

初始电量由精确有理数插值得到：

    epsilon_native = 1e-9 J
    E_init = e_l + rho * ((e_h - epsilon_native) - e_l), 0 < rho < 1

因此按 native affordability 判断满足 `E_init + epsilon_native >= e_l` 且 `E_init + epsilon_native < e_h`。`nonblock_bypass` 目前只由 `gpfp_asap_nonblock` 的 native 路径发出，所以 B1 runtime activation 只以该 scheduler 为可观测 anchor；其他 NONBLOCK scheduler 不是零，而是 UNAVAILABLE。

### EXT-1B2：SYNC_BATCH_STRESS

研究同一 release tick 上，BLOCK 可支付最高优先级前缀而 SYNC 无法原子支付 top-q batch 时，批量支付门槛是否真实阻塞执行。

q = min(M, ready_jobs)，默认 smoke/pilot 令 p = 1，并要求 1 <= p < q。按实际 priority rank 取 top-q：

    E_prefix = sum(e_i, i < p)
    E_batch = sum(e_i, i < q)
    E_init = E_prefix + rho * ((E_batch - epsilon_native) - E_prefix)

因此按相同 native epsilon 满足 prefix 可支付、完整 batch 不可支付。`sync_batch_block` 目前只由 `gpfp_asap_sync` 的 native 路径发出，所以 B2 runtime activation 只以该 scheduler 为可观测 anchor；其他 SYNC scheduler 记为 UNAVAILABLE。

### EXT-1B3：TIMING_STRESS

研究在相同 taskset、harvest trace、初始电量和 battery capacity 下，ASAP、ALAP 和 ST 的实际启动门控何时产生不同 first-execution timeline。

本实现按当前 scheduler 源码和测试取语义：

- ASAP 在作业可运行且能量门槛允许时立即选择；
- ALAP 只在当前绝对 deadline slack 不大于零时选择；
- ST 有能量时沿用 ASAP 路径；能量不足且 slack 为正时进入充电持有，直到 battery full 或 slack exhausted。

两个必需 timing cell 是：

- POSITIVE_SLACK_ENERGY_AVAILABLE：top-q 初始 slack 为正，并在 release 时具有一次选择所需能量；
- SLACK_LIMITED_CHARGING：目标作业初始不可支付，且精确构造 affordable_tick < battery_full_tick < initial_slack。

deadline 仅在 EXT-1B3 本地变换，始终满足 C <= D <= T 和配置的 D/T 区间。runtime activation 由 top-M first-execution vector 的 timing-family 差异或真实 ST charge-begin event 判定。ST 的 begin、hold、release 及 release reason 均直接来自当前 native trace。

## 名义收能率和实际 trace

任务集名义平均需求和收能率定义为：

    lambda_D = sum((C_i / T_i) * e_i)
    lambda_H = eta * lambda_D

系统使用 synthetic solar 峰值相位，base harvesting rate 使实际生成 trace 的峰值每 tick 收能等于 lambda_H。runner 从最终 system config 重新生成 trace 并记录 trace_hash，不以名义值代替实际输入。B1/B2 的有限 battery 不允许 overflow；B3 charging cell 为观察当前 ST 的 battery-full 门控，显式标记允许 clipping。

EXT-1B 的 `initial_battery` 只约束 t=0 机制输入，不是 CORE-3 proof-oriented 的“所有后续 release 电量下界”。trace admissibility 只要求 release 电量非负；初始电量、capacity 与实际 trace 仍在九 scheduler 间逐字配对。

## 公平配对与选择纪律

每个 accepted paired instance 恰有九个 scheduler，各一次。以下字段必须逐字一致，否则 P0 fail closed：

- taskset、harvest trace、simulation config 和 fair input hash；
- initial battery、battery capacity、horizon、maximum horizon；
- generation seed、M；
- priority、power、deadline、release、workload-vector hash；
- simulator build hash。

scheduler ID 只进入 request identity，不进入 taskset、deadline 或 harvest seed。

聚合与独立 analyzer 会重新验证九 request 完整性、request ID、所有公平字段和 terminal/request identity。不完整九 scheduler 组从 activation、paired outcome、priority summary、statistics 和 plot data 全部排除。只有 `comparison_eligible=true` 且终态为 pass 或 deadline miss 的请求能进入 outcome 与连续统计；失败、horizon insufficient 和不合格 trace 不会被当作 miss、pass、零或 tie。

结构 retry 只检查结构谓词。禁止根据 ASAP-BLOCK 或任何 scheduler 的 pass、miss、胜率、response time 来接受、拒绝、重采样或选择参数。pilot eligibility 只能使用结构成立率、runtime activation ratio、valid terminal ratio、timeout/error/horizon-insufficient ratio、公平性和运行成本。建议门槛是 activation 20%–80%、valid terminal 至少 95%、timeout 加 internal error 不超过 1%、horizon insufficient 不超过 5%。

## 激活集合、指标和统计

mechanism_activation.csv 明确区分：

- A_STRUCTURAL_REJECTED：结构条件不成立；
- B_RUNTIME_UNOBSERVABLE：结构成立，但所需 native event/完整 timing vector 不可观测；
- B_STRUCTURAL_ONLY：结构成立但运行机制未激活；
- C1_RUNTIME_ACTIVATED_OUTCOME_SAME：激活且 paired deadline outcome 相同；
- C2_RUNTIME_ACTIVATED_OUTCOME_DIFFERENT：激活且 paired deadline outcome 不同。

主要二元指标是 overall taskset pass 和 top-M success。top-M success 要求最高 M 个 priority rank 的任务均无 miss、满足 minimum jobs，且请求终态可比较。first missed priority rank 是发生 miss 的最小 rank；无 miss 写 NONE，但其 numeric 统计列写 UNAVAILABLE，不用 task-count sentinel 伪装成普通数值；不可比较也写 UNAVAILABLE。

连续或有序指标包括 top-M maximum response time、first missed priority rank、energy-blocked ticks、processor-wait ticks、synchronization wait 和 bypass count。ASAP-BLOCK 是预先声明的主要算法，只和其余八个 scheduler 做 paired statistics。二元指标输出 paired bootstrap CI、exact McNemar 和按“同一 cell、同一二元 endpoint 的八个 comparator”定义的 Holm family；连续指标输出 paired effect、确定性 bootstrap CI 和 win/tie/loss，并以绝对容差 `1e-9` 定义 tie。

## 配置和命令

smoke、pilot、formal 分别使用 EXT1B_SMOKE、EXT1B_PILOT、EXT1B_FORMAL seed 空间。

    python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b1_smoke.yaml
    python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml
    python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b3_smoke.yaml
    python3 scripts/analyze_v9_3_ext1b.py --output-root artifacts/v9_3_ext1b1_smoke --verify-hashes
    python3 scripts/run_v9_3_ext1b.py --verify-hashes artifacts/v9_3_ext1b1_smoke

pilot 仅在单独授权的小规模校准中运行：

    python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b1_pilot.yaml

configs/v9_3_ext1b_formal_template.yaml 的状态是 UNFROZEN_FORMAL_TEMPLATE，runner 必然拒绝执行。即使只把状态改成 FROZEN_FOR_FORMAL_EXECUTION，当前 runner 仍会拒绝：本实现没有绑定正式运行授权，不能靠单字段切换绕过。正式运行前仍需评审并冻结 scenario 选择、grid、taskset count、horizon、timeout、battery/harvest 轴、bootstrap resamples 和 formal authorization；本次没有创建可执行正式配置。metadata/summary 明确写 `formal_large_scale_run=false` 和 `result_class=NON_FORMAL_SMOKE_OR_PILOT`。

--resume 复用按 request ID 原子写入的 terminal JSON，重建 CSV 时不会复制 request/result。--dry-run 只报告 cardinality。

## 固定输出

每次 run 写出 20 个顶层产物：

1. run_metadata.json
2. run_config.yaml
3. checkpoint.json
4. file_hashes.sha256
5. scheduler_registry.csv
6. generated_tasksets.csv
7. generation_attempts.csv
8. scenario_instances.csv
9. simulation_requests.csv
10. simulation_attempts.csv
11. simulation_results.csv
12. task_outcomes.csv
13. mechanism_activation.csv
14. paired_scheduler_outcomes.csv
15. scheduler_summary.csv
16. scenario_summary.csv
17. priority_rank_summary.csv
18. paired_statistics.csv
19. ext1b_plot_data.csv
20. failures.csv

summary CSV 显式携带 requested、terminal、valid terminal、sufficiently observed、structural activation、runtime-observable、runtime-activated 和 outcome-comparable 计数。`runtime_activation_denominator` 是“所属完整 paired mechanism scope 已由声明的 event anchor 或 timing vector 观测到”的 scheduler-request 关联数，`runtime_activation_count` 是其中所属 scope 确实激活的关联数；它们不是“每个 scheduler 都亲自发出 native event”的计数。因而 B1/B2 的一个完整九路 pair 在 anchor 可观测时，scenario 汇总贡献 9，单 scheduler 汇总各贡献 1。`outcome_comparable_denominator` 是完整九路作用域内可比较请求数。plot data 分 scenario/cell 输出 overall、top-M、三类 mechanism activation、paired risk/response difference、first-missed priority 和 battery trajectory；缺失值保留为 UNAVAILABLE，不会替换成零。

smoke 配置启用 retained semantic trace，使三个确定性 bounded case 可以从 native event 验证 B1 bypass、B2 sync wait，以及 B3 ASAP/ALAP/ST first-execution 和 ST charging timeline。trace 只用于 smoke 诊断，不应提交实验产物。
