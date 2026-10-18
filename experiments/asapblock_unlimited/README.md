# ASAPBlock V7.2 无限容量实验复现代码

这里包含论文实验所需的输入生成器、固定配置、分析内核、实验运行器和统计代码。
下载仓库即可重新生成输入并运行；不需要原工作区、历史结果压缩包或论文文件。
实验生成的输入、结果、证书和 CSV 均写入指定的输出目录。

## 环境与快速检查

使用 Linux 或 Windows 的 WSL2，Python 3.11 及以上。
已在 Python 3.12.14、PyYAML 6.0.3 下验证。实验使用 CPU 亲和性和进程资源限制。
在仓库根目录运行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r experiments/asapblock_unlimited/requirements.txt
python experiments/asapblock_unlimited/test_reproduction.py
python experiments/asapblock_unlimited/reproduce.py all --suite constructed --limit 1 --output experiments/asapblock_unlimited/runs/smoke
```

`--limit` 和自定义预算/方法会将运行明确标记为 `custom_run`，用于检查程序，不能替代全量结果。

## 完整复现

```bash
# 只生成输入、验证原始输入身份并查看计划，不启动分析。
python experiments/asapblock_unlimited/reproduce.py prepare
python experiments/asapblock_unlimited/reproduce.py plan

# 顺序运行全部实验、用原版代码核验证书、生成统计表。
python experiments/asapblock_unlimited/reproduce.py all

# 或只运行一个实验，之后核验和汇总。相同命令可断点续跑。
python experiments/asapblock_unlimited/reproduce.py run --suite main
python experiments/asapblock_unlimited/reproduce.py verify --suite main
python experiments/asapblock_unlimited/reproduce.py summarize --suite main
```

默认输出为本目录下 `runs/reproduction/`；可用 `--output PATH` 指定。
全量实验包含两万余次主请求及按规则追加的请求，耗时取决于机器和超时数量。
`--workers` 只影响覆盖率实验的并行队列；所有耗时比较实验始终串行运行。
程序按当前可用的 CPU 编号分配，不要求机器必须有 CPU 0。

| `--suite` | 输入与方法 | 预算和重复 |
|---|---|---|
| `main` | 1,920 输入，生产 analytic、历史审查 Full/删除共享预算、SEQ | 每请求 30 秒墙钟，1 次 |
| `ablation` | 同一批 720 输入，完整版本和全部 9 个对照 | 60 秒墙钟；每个超时追加一次 300 秒 |
| `constructed` | 18 个构造族 + 162 个邻域输入，7 种方法 | 每请求 30 秒墙钟 |
| `cost` | 原定 24 个 CPU 样本，Full、R-only、CO | 15 秒墙钟，2 次，串行 |
| `cost_branches` | 同一组 24 个输入，9 种方法 | 15 秒墙钟，2 次，串行 |
| `scale` | 原定 18 个规模/数值时间点 | 30 秒墙钟，每点 1 次，串行 |
| `quick_coverage` | 原 720 输入，生产 Full、固定截止期入口、SEQ | 每请求 15 秒墙钟 |
| `quick_timing` | 原定 24 输入，生产 Full、快速入口、快速入口后回退 | 30 秒墙钟，3 次，串行 |
| `flow` | 144 个既有长周期输入 + 432 个新增短周期输入 | 30 秒 CPU；按原分层规则选择至多 24 对，未完成方法追加至 120 秒 CPU |
| `flow_timing` | 原先选定的 19 输入，analytic/flow | 各输入 30/60/120 秒 CPU，3 次，串行 |

`flow_timing` 保留原条件样本的 8 个共同认证、8 个共同无证书及 3 个定向诊断输入身份，
不按新机器的结果重新筛样。其速度汇总分别使用前两类稳定完成样本，3 个定向样例单列。

## 方法对应关系

- `production_analytic`：理论正文对应的完整 V7.2，生产 analytic 路径。
- `production_flow`：同一个 RTA，用 flow 求解内部能量分配上界。
- `full`：与完整 RTA 相同判据的统一审查内核，用于公平消融和成本比较。
- `completion_only`：CO，取消独立首单位反馈；首单位精度比较使用推导值 `F=R-C+1`。
- `affine_completion`：R-only，迭代时由响应界推导 `F=R-C+1`。
- `no_first_cuts`、`no_first_work`、`no_shared_budget`：分别删除首单位历史约束、首单位工作量收紧、共享预算耦合。
- `last_block_only`、`progress_only`、`no_charging_gap`：阻塞/进展/充能间隔分支对照。
- `seq`：此前冻结的项目 SEQ-PH-LOC 基线；`seq_audit` 在同一 SEQ 调用后核验见证。
- `legacy_audit_full`、`legacy_audit_no_shared_budget`：主实验历史记录使用的审查调用，仅在相同调用族内解释成本。
- `deadline`：原已测的 `F=R=D` 快速入口；`fallback` 失败后进入完整搜索，共用一次总预算。

## 输入身份与代码固定

`configs/campaigns.json` 保存原始种子、生成参数和主实验元数据；
`configs/selections.json` 仅保存固定样本的选择身份与预算配置，不含实验测量结果。
程序重新生成全部 1,920、720、576、180、390 个归档输入，并检查每组排序后的
`(case_id, input_sha256)` 指纹，与 `configs/expected_inputs.json` 中的原归档指纹比较。
生成结果的文件头和来源描述记录本次复现，因此容器 JSON 文件本身的哈希可以不同；
任务模型哈希和包含元数据的样本身份必须一致。

`runtime/reproduction/` 中是原实验使用的生产/原版核验源码；
`runtime/tools/` 保留统一消融内核和独立首单位/分支对照。
`runtime/frozen_sources.json` 固定了 34 个原始依赖文件的字节哈希。
输入生成函数只调整导入位置，分析判据未修改。
该目录独立运行，不替换仓库原有九种调度算法的实现。

## 结果与解释

每个实验目录保存 `plan.json`、主请求 `primary.jsonl`、追加请求 `extension.jsonl`（如有）、
最终完成视图 `final.jsonl`、压缩证书及原版核验日志。追加视图只替换主请求中的未决项，
不会按最好结果挑选。输入、代码、环境或预算改变时，程序拒绝沿用已有日志。
锁文件阻止同一输出目录内同时启动多个实验；异常退出后，应确认旧进程已停止再移除遗留锁。

`summarize` 生成状态计数、确定新增认证、共同认证输入上的 F/R 精度、CPU 样本簇统计、
配置分层结果及串行耗时 CSV。`NO_CERTIFICATE` 表示未获得充分证书；`TIMEOUT` 保持未决。
耗时比只使用双方重复运行均稳定完成的输入，不把超时预算填成实际完成耗时。
精度统计先对任务取均值，再在同一 CPU 样本的可用变体内取均值，最后对样本簇取均值。
Bootstrap 使用 5,000 次分层重采样；固定算法和种子写入 CSV，属于条件性描述统计。
构造族不作为总体认证率证据；规模测试每点只有一次测量。

不同硬件和 Python 运行环境会改变耗时、超时数量及其条件统计分母。
本次整理验证了原始输入身份、冻结源码身份和小规模端到端运行，没有重跑或修改既有全量实验结果。
