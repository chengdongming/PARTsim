# ASAP-BLOCK v9.3 统一实验代码正确性复查

## 1. 结论与边界

- 基线：`9e791b7d80de452d4fa041a0a323a4d5fcec1061`
- 生产代码冻结候选：`1beda24d0f3ab1c58379a8f9e503df21613997b5`
- 分支：`audit-freeze-v9.3-experiments`
- 复查范围：CORE-1～CORE-5、EXT-1、EXT-2 框架、EXT-4，以及公共配置、seed、taskset store、RTA/simulation engine、checkpoint/resume、aggregation、plot-data。
- 明确排除：CORE-0A、EXT-3、正式大规模实验、正式论文图、authoritative pointer、build identity、多层 evidence/ZIP。

结论：发现的结果相关 P0/P1 已用最小修改关闭，未发现未解决且可能改变正式实验结果的 P0/P1。代码冻结条件在干净 clone 有界演练后满足。EXT-2 仍因真实数据来源、许可和预处理证据不齐而保持正式禁用；本报告不宣称论文正式实验已经完成。

## 2. 基线快照与入口清单

复查开始时 tracked tree 干净；工作区原有的 CORE-0A、pointer、旧合同文档和 ZIP 等 untracked 文件均未被 import、build、打包或提交。

环境：

- Python 3.8.10
- CMake 3.16.3
- GCC/G++ 9.4.0
- simulator：`build/rtsim/rtsim`
- C++ test binary：`build/test/test_librtsim`

正式生产 runner：

- `scripts/run_v9_3_core1.py`
- `scripts/run_v9_3_core2.py`
- `scripts/run_v9_3_core3.py`
- `scripts/run_v9_3_core4.py`
- `scripts/run_v9_3_core5.py`
- `scripts/run_v9_3_ext1.py`
- `scripts/run_v9_3_ext2.py`
- `scripts/run_v9_3_ext4.py`

配置清单：

- CORE-1/2：`*_template.yaml`、`*_smoke.yaml`、`*_formal_candidate.yaml`
- CORE-3/4/5、EXT-1/2/4：`*_template.yaml`、`*_smoke.yaml`
- CORE-3 另有受限 pass micro 配置；它不是正式候选配置。
- 当前只有 CORE-1/2 文件被标为 formal candidate；其余实验须在下一阶段完成参数冻结。EXT-2 formal 必须继续拒绝。

基线测试：全仓 pytest 为 852 passed / 62 skipped；CTest 为 163/166，已知失败固定为 `Scheduler.FIFO`、`Scheduler.TrueFIFO`、`Scheduler.RM`。

## 3. 公共依赖图与数据流

```text
YAML
  -> experiments/v9_3/config.py
  -> cell_model.py / 各扩展 cell builder
  -> canonical SHA-256 identity + deterministic seed
  -> taskset_store.py
       -> global_task_generator.py
       -> frozen canonical task payload + semantic hash
  -> analysis/simulation request CSV
       -> execution_engine.py -> asap_block_v9_3_runner.py
          -> asap_block_rta_v9_3_taskset.py -> asap_block_rta_v9_3.py
       -> simulation_engine.py -> build/rtsim/rtsim
          -> scheduler_registry.py -> 9 个独立 C++ GFP implementation
  -> atomic terminal/result CSV + checkpoint
  -> per-experiment aggregation
  -> frozen plot-data CSV
```

审查生产 import graph 与源码字符串后确认：

- solver 输入不读取既有 result/artifact 数值；
- pilot、v20.4、v21 不可由正式 runner 回退调用；
- 没有硬编码 candidate、认证状态或 summary 计数；
- smoke fixture 不会由 formal 路径自动选择；
- plotting 入口只读取冻结 CSV，不调用 RTA、simulator 或 subprocess，也不重新分类终态；
- canonical identity 使用 SHA-256/canonical JSON，不使用进程随机化的 Python `hash()`。

## 4. 配置到最终 CSV 的逐实验审查

### CORE-1

调用链为通用 config/cell/taskset store → `ExecutionEngine` → production RTA → `per_taskset_results.csv`/`per_task_results.csv` → CORE-1 comparison/plot CSV。计划只包含 `CW_THETA_CW`、`LOC_THETA_LOC`，两个 variant 共享 generation/taskset identity；variant 不进入 seed。summary 分开记录 requested/terminal、completed-only 与 common-candidate 口径。

### CORE-2

五个 variant 顺序为 `CW_D`、`CW_THETA_CW`、`LOC_D`、`LOC_THETA_CW`、`LOC_THETA_LOC`。`LOC_THETA_CW` 的 source 是同一 taskset 上已经冻结的 CW 向量；source 不可用时生成 `NOT_APPLICABLE_DEPENDENCY`，没有 fallback。dependency CSV 保留 source/target vector hash，同源检查通过。

### CORE-3

CW、LOC 与 simulation 共享 taskset hash。RTA release-time E0 前提和 simulator 初始电量为两个独立字段；`release_e0_valid` 有独立计数。`SIM_HORIZON_INSUFFICIENT` 不计 pass，partial candidates 不进入 jointly-certified taskset tightness。修复后 raw `RTA_PASS_SIM_FAIL` 不再被无效 E0 前提改写为 timeout/error；有效前提分母仍独立保留。

### CORE-4

sweep cell 的 unchanged-field guard 验证每一条曲线只改变目标轴。E0、service、power 的预期单调方向由显式 ordering/paired CSV 给出；timeout 不计 monotonicity violation。completed runtime 和 censored count 已分离。

### CORE-5

scaling cell 每次只改变 task count 或 worker count 中的一个轴。runtime 使用 event/censored 口径，peak RSS 作用域与单位显式记录，输出明确声明 parallel throughput 不是算法复杂度。resume 只物化已有终态时保留首轮 cell wall time 与 throughput。

### EXT-1

一个冻结 taskset 展开为九个 simulation request。除 scheduler ID/implementation 外，taskset hash、trace hash、input config hash、seed、horizon、初始电量、容量、release/deadline/power 完全一致。scheduler ID 不进入 taskset 或 trace seed。内部 simulator 异常映射为 `SIM_INTERNAL_ERROR`，不会伪装成 deadline miss。

### EXT-2

正式状态为 `REAL_TRACE_DATA_UNAVAILABLE`；smoke 明确标为 `SYNTHETIC_TEST_FIXTURE`。缺少 certified service lower bound 时 RTA 为 `NOT_APPLICABLE_NO_CERTIFIED_SERVICE_BOUND`。resampling、scale 和单位换算使用 `Fraction` exact arithmetic，segment 选择由配置记录而不是自动挑选有利片段。

### EXT-4

generator family、power mode、priority policy、deadline mode 都经过注册表/校验；不支持值 fail-closed，无静默 fallback。priority 轴由 Python `priority_policy.py` 生成，不调用 C++ `RMScheduler`。paired/unpaired 标签来自 unchanged-field 检查；requested denominator 来自 sample plan，不再错误等于已经到达的 terminal 行数。

## 5. RTA variant、seed 与输入身份

新增跨实验 identity test 使用真实 `TasksetStore`，在 CORE-1～CORE-5 中用相同 generation 配置与 base seed 生成输入，并验证：

- generation ID、derived seed、semantic hash 完全相同；
- canonical task payload 逐字段相同；
- E0 不进入需要跨 E0 配对的 generation identity；
- variant、scheduler、timeout、retry 和 worker count 不进入数学输入 seed。

CORE-1/2 受限演练共享 `taskset_store_core12`；EXT-1 九调度器的 scheduler-neutral input hash 电池全部闭合。CORE-4/5 和 EXT-4 的 one-axis/unchanged-field 测试通过。

## 6. 终态、timeout、删失和 soundness

RTA 终态保持独立：`COMPLETED`、`NO_CANDIDATE`、`TIMEOUT`、`NOT_APPLICABLE_DEPENDENCY`、`NUMERIC_ERROR`、`INTERNAL_CONFORMANCE_FAILURE`。simulation 终态保持独立：`SIM_PASS_OBSERVED`、`SIM_DEADLINE_MISS`、`SIM_HORIZON_INSUFFICIENT`、`SIM_RUNTIME_TIMEOUT`、`SIM_INTERNAL_ERROR`。

核对结果：

- timeout retry 追加 attempt，不覆盖首个 attempt；
- TIMEOUT 不转为 NO_CANDIDATE；simulation timeout 不转为 deadline miss；horizon insufficient 不转为 pass；
- CORE-1/4 的普通 runtime mean 只使用 completed events，timeout 数进入 `runtime_censored_count`；
- CORE-5 使用 restricted mean/event-observed censoring，并在 resume 时保留首轮 cell timing；
- CORE-3 raw soundness class 与 `release_e0_valid` 分开，既不吞掉 `RTA_PASS_SIM_FAIL`，也不把无效前提混入有效分母；
- terminal 冲突、重复 attempt、计划外 terminal、成功 attempt 缺 analyzer state、非中断运行缺 terminal 均 fail-closed。

## 7. aggregation 与 plot-data 独立复算

微型人工 fixture 与真实 smoke 同时覆盖：

- requested denominator；
- terminal denominator；
- completed-only denominator；
- common-candidate denominator；
- sufficiently-observed simulation denominator；
- jointly-certified taskset tightness denominator；
- timeout/censored denominator。

关键定向测试位于 `test_v9_3_experiment_aggregation.py`、各 CORE pipeline test、`test_v9_3_ext1_pipeline.py`、`test_v9_3_ext2_pipeline.py`、`test_v9_3_ext4_pipeline.py`。EXT-2/4 人工制造 requested > terminal 的 fixture 后，summary 分别保持 requested、terminal、valid 三个口径。plot-data row count 由冻结终态/paired CSV 生成，无 solver 调用。

干净演练闭合计数：CORE-1 2/2；CORE-2 5/5；CORE-3 2 RTA + 1 simulation；CORE-4 4/4；CORE-5 4/4；EXT-1 9/9；EXT-2 1/1；EXT-4 4 RTA + 2 simulations。八个 `file_hashes.sha256` 均通过 verifier。

## 8. checkpoint/resume 破坏性测试

自动化测试覆盖：正常 run 后原子 terminal 跳过、attempt 后 terminal 前崩溃重建、成功 attempt 丢 state、配置 hash 改变、重复 attempt、冲突 terminal、计划外 terminal、删除 terminal 后恢复。结果为：

- 已完成 analysis/simulation 不重复执行；
- attempt 历史保留；
- 有 state 的 attempt 可重新物化 terminal；无 state 的成功 attempt 被检测并拒绝；
- config hash 不同拒绝 resume；重复/冲突/计划外数据拒绝；
- 非用户中断的运行必须达到每个 request 恰好一个 terminal。

此外，在独立 clone 的八组结果上执行了全量 `--resume`。resume 前后 CORE-5：

- `scalability_cells.csv`: `65cead96d08ceb599d9dbf36147374ca209c373e2489d97fa005ad9c10a85276`
- `scalability_summary.json`: `b5330a0c7c924bc44d076443ba8641dce5770ee08d327ed8a99df8f5418ab08f`

哈希逐字节不变，八目录再次通过完整性验证。

## 9. 已知 CTest 三项失败的影响判定

### `Scheduler.FIFO` — REACHABLE_BUT_SEMANTICALLY_IRRELEVANT

失败对应 `Scheduler::_queue` 的基础 comparator/iterator 顺序测试。九个正式 GFP 能量 scheduler 会调用基础 `Scheduler::insert` 做模型/队列 bookkeeping，因此不能声称整个 base path 不可达；但九个类均维护自己的 ready/current-tick selection state，并覆盖正式 dispatch 使用的 selection/get-first/get-task-N 路径。134 项 ASAP/ALAP/ST 语义测试、三个 family equivalence test 和九工厂独立映射测试全部通过。因此失败的基础 iterator 顺序不会决定正式九调度器的出队或调度结果。

### `Scheduler.TrueFIFO` — PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS

失败对应独立 `TrueFIFOScheduler` 类。它不在 v9.3 九调度器 registry/factory mapping 中，正式 simulation YAML 也不生成该 scheduler 名称。没有正式间接调用。

### `Scheduler.RM` — PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS

失败对应 C++ `RMScheduler` 的基础队列排序测试。九个能量 scheduler 是九个独立 factory implementation，不实例化 `RMScheduler`。EXT-4 的 RM priority generation 在 Python `experiments/v9_3/priority_policy.py` 中完成并写入 task payload，也不调用 C++ RM 类。正式任务优先级及九调度器决策不经过失败路径。

未修改九调度器冻结语义；这三项仍记录为非阻塞既有问题，不能泛化为“CTest 全通过”。

## 10. 两个遗留 Python 文件

- `tools/about.py`（Python 2 print）
- `tools/taskset_generator/taskgen.py`（TabError）

二者判定均为 `PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS`。正式 runner/import graph 不引用它们；生产 generator 是仓库根目录的 `global_task_generator.py`。AutoDL compile scope 只编译 `experiments/v9_3`、生产 RTA/taskset/runner、生产 generator 和八个正式 runner，不会全仓扫描这两个遗留文件。

## 11. P0/P1/P2 清单

已关闭 P0：

1. CORE-1/CORE-4 timeout 时长混入 completed runtime mean；已分离 censoring。
2. CORE-3 无效 release E0 前提吞掉 raw `RTA_PASS_SIM_FAIL`；已分开 raw/evaluable 与 E0 分母。
3. EXT-2 缺 terminal denominator；已补 requested/terminal/valid 三口径。
4. EXT-4 requested denominator 取 terminal 行数、缺失 method 时 method 集缩水；已改为 plan/sample 与 run-config 方法集。

已关闭 P1：

1. duplicate attempt、计划外/冲突 terminal、成功 attempt 缺 state、正常结束缺 terminal 未全部 fail-closed。
2. CORE-5 resume 用极短恢复耗时覆盖首轮 wall time/throughput。
3. 这些正式路径的定向测试覆盖不足；已补统一 identity/import/plot 与破坏性 resume 测试。

P2/非阻塞：

- 三项旧 CTest 仍失败，影响判定见第 9 节；不修改冻结 scheduler 语义。
- 两个遗留 Python 文件语法不兼容，正式路径不可达；不做范围外清理。
- EXT-2 真实数据未获准是明确禁用边界，不是用 fixture 替代的待办。

未解决 P0：0。未解决且可能影响正式结果的 P1：0。

## 12. 干净环境有界演练与测试结果

独立 clone：`/tmp/partsim_v93_audit_clean_20260714`，HEAD 为生产代码冻结候选。没有复制原工作区 build、artifact 或缓存；在 clone 内执行 `cmake -S . -B build -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release` 和 `cmake --build build --parallel 2`，从源码构建成功。

严格受限演练规模与第 7 节计数一致。新产物从干净 clone 回传至 `artifacts/v9_3_unified_rehearsal/`；原工作区预演未被当成干净证据。resume、summary、plot-data 和 file hash 全部通过。

最终回归：

- v9.3 定向 pytest：128 passed；
- 全仓 pytest：863 passed、62 skipped、32 warnings；
- CTest：163/166 passed，只有三项已知失败；
- family/identity 定向 GTest：4/4 passed；
- 其余九 scheduler/ASAP-BLOCK 语义测试在 CTest 中通过；
- 正式 Python import/compile scope：passed；
- deployment shell syntax/Python compile：passed；
- `git diff --check`：passed。

## 13. 冻结判定

无未解决 P0/结果相关 P1；输入公平、seed/taskset/trace 配对、终态、删失、分母、resume、遗留文件不可达性、CTest 影响和干净演练均有证据闭合。生产代码冻结候选唯一确定为 `1beda24d0f3ab1c58379a8f9e503df21613997b5`。

可以准备并验证 AutoDL 部署载体，也可以进入“最终配置冻结”阶段；不得把该结论解释为可以跳过参数冻结、直接宣称正式实验完成。EXT-2 formal 在边界条件满足前必须继续拒绝。
