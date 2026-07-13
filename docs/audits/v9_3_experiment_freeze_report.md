# ASAP-BLOCK v9.3 实验代码冻结报告

## 冻结决定

**PASS — 形成代码冻结候选。**

- 基线：`9e791b7d80de452d4fa041a0a323a4d5fcec1061`
- 唯一生产代码冻结候选：`1beda24d0f3ab1c58379a8f9e503df21613997b5`
- 分支：`audit-freeze-v9.3-experiments`
- 未解决 P0：0
- 未解决、可能影响正式结果的 P1：0

本冻结覆盖 CORE-1～CORE-5、EXT-1、EXT-2 框架、EXT-4 及公共实验基础设施。不覆盖 CORE-0A、EXT-3、pointer/build identity/evidence ZIP，也没有运行正式大规模网格。

## 已关闭的结果风险

- completed runtime 与 right-censored timeout 分离；
- CORE-3 raw soundness 与 release-time E0 有效性分开；
- EXT-2/4 requested、terminal、valid 分母分开；
- duplicate attempt、冲突/计划外/缺失 terminal 和缺 analyzer state 全部 fail-closed；
- CORE-5 resume 保留首次实际 wall time/throughput；
- 增加跨实验 canonical taskset/seed/hash identity、正式 import graph、九工厂映射和 frozen plot-data 测试。

## 调用与公平性结论

- CORE-1 只调用 `CW_THETA_CW`/`LOC_THETA_LOC`；CORE-2 五 variant 顺序、依赖和 source freeze 正确。
- CORE-3 的 CW/LOC/simulation 使用同一 taskset；partial candidate 不进入 certified tightness。
- CORE-4/5 一次只改变一个轴；EXT-4 未支持模式 fail-closed。
- EXT-1 九调度器 factory 映射到九个独立 C++ 实现；scheduler-neutral 输入逐字段相同，scheduler ID 不进入 seed。
- 不存在 pilot 或 v20.4/v21 fallback，不读取既有实验结果作为 solver 输入。

## 已知非阻塞问题

- CTest 仍有 `Scheduler.FIFO`、`Scheduler.TrueFIFO`、`Scheduler.RM` 三项基线失败。影响分别为 `REACHABLE_BUT_SEMANTICALLY_IRRELEVANT`、`PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS`、`PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS`。九调度器语义与独立工厂映射测试全部通过。
- `tools/about.py` 与 `tools/taskset_generator/taskgen.py` 均为 `PROVEN_UNREACHABLE_FROM_FORMAL_EXPERIMENTS`；部署 compile scope 明确排除。
- EXT-2 为 `REAL_TRACE_DATA_UNAVAILABLE`，formal 禁用；synthetic fixture 只允许 smoke。

## 验证摘要

- v9.3 pytest：128 passed
- 全仓 pytest：863 passed、62 skipped、32 warnings
- CTest：163/166 passed；仅三项已知失败
- ASAP/ALAP/ST family + nine-factory identity：4/4 passed
- 正式 Python import/compile、shell syntax、deployment Python compile、`git diff --check`：passed
- 独立 clean clone Release build：passed
- clean bounded rehearsal：八组全部 terminal 闭合、summary/plot-data 可生成、八组 file hashes 通过
- clean rehearsal resume：八组通过，无重复/漏项；CORE-5 首轮 timing 文件哈希保持不变

受限计数：CORE-1 2；CORE-2 5；CORE-3 2 RTA + 1 sim；CORE-4 4；CORE-5 4；EXT-1 9 sim；EXT-2 1 fixture sim；EXT-4 4 RTA + 2 sim。

## 配置与下一阶段边界

CORE-1/2 已有 formal candidate YAML；CORE-3/4/5、EXT-1/4 只有 template/smoke，必须先完成最终参数冻结才能启动正式网格。EXT-2 在 provenance、许可、可复现预处理链及必要的 certified service lower bound 就绪前继续拒绝 formal。EXT-3 不在本候选中。

因此可以租用 AutoDL 进行部署验证和最终配置冻结；不应把“代码冻结通过”表述为“论文正式实验已经完成”。
