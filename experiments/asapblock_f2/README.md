# ASAPBlock 无限容量 RTA 实验 F2

对应 `ASAPBlock_RTA_论文实验方案_F2_20261021.md`。方案 SHA-256：
`da72c1d8f7ba08cb177ab89e57a5890a9adaccc0dfa0a445a83835d0eae96f64`。

本目录新增实验编排、配对输入、统计和绘图，依赖相邻 `asapblock_unlimited/runtime` 的冻结源码，
不改变RTA、SEQ、九种调度算法或证明正文。`protocol.json` 是运行参数，
`expected_inputs.json` 固定结果无关的输入和选样身份。正式运行必须保留同一代码版本。

## 安装与前置核查

Linux / WSL2，Python 3.11以上。在仓库根目录运行：

```bash
python -m pip install -r experiments/asapblock_f2/requirements.txt
python experiments/asapblock_f2/test_f2.py
python experiments/asapblock_f2/run.py prepare
python experiments/asapblock_f2/run.py plan
python experiments/asapblock_f2/run.py check
```

`check` 检查10个已有方法入口和独立原版重放、正文构造例、生产与统一内核输出对齐、
相同X下的机制诊断，以及有限合法偶发释放/脉冲相位轨迹中的F/R界。
有限轨迹是错误检查，不是精确最坏响应计算或理论证明。
生产/统一内核数学对齐核查对双方统一使用120秒，独立于正式30秒分析请求。

## 预定试跑

```bash
python experiments/asapblock_f2/run.py run --suite pilot --workers 2
python experiments/asapblock_f2/run.py verify --suite pilot
python experiments/asapblock_f2/run.py summarize --suite pilot
python experiments/asapblock_f2/run.py plot --suite pilot
```

试跑固定20个骨架、192个输入、两方法384次30秒请求。图均标注PILOT，不能当作100骨架/配置的正式结果。
只有前置检查通过且源码身份一致，运行器才允许开始。`all --suite pilot`会依次准备、核查、运行、重放、统计和绘图。

## 正式实验

| suite | 输入 | 方法 | 墙钟预算 | 重复 | 备注 |
|---|---:|---|---:|---:|---|
| main | 9,600 | 生产AB、SEQ | 30秒 | 1 | CPU/能量/G交叉扫描，重叠格只生成一次 |
| mechanism | 600 | Full及四个删减版 | 60秒 | 1 | 同内核，50个主库骨架的相关变体 |
| scale | 1,100 | 生产AB、SEQ | 30秒 | 1 | 800原网格加300固定4核任务数点 |
| numeric_scale | 160 | 生产AB、SEQ | 120秒 | 3 | C/D/T/G共同放大，串行 |
| robustness | 400 | 生产AB、SEQ | 30秒 | 1 | 周期10至1,000，单独报告 |
| timing | 240 | 生产AB、SEQ | 120秒 | 3 | 主库192个不同骨架加原规模网格48个，串行 |
| mechanism_timing | 72 | Full/删除共享预算/CO | 60秒 | 3 | 同内核，串行；72输入来自38个骨架 |

核心请求共28,248次，时限总和325.8单核请求小时；这是预算上界，不是运行耗时预测。
每个请求4 GiB虚拟地址上限及一个宿主CPU；分析模型的M不等于宿主并行线程数。
默认覆盖率队列只用两个不同物理核，时序实验始终一队列串行；遵守宿主内存和CPU配额。
主认证与计时结果严格区分生产实现和审查内核。

对表中的每个suite分别运行以下命令。正式代码不提供任意修改预算/方法的开关：

```bash
python experiments/asapblock_f2/run.py run --suite main --workers 2
python experiments/asapblock_f2/run.py verify --suite main
python experiments/asapblock_f2/run.py summarize --suite main
python experiments/asapblock_f2/run.py plot --suite main
```

用 `--output PATH` 指定独立目录。`--limit N`仅用于结果无关的哈希排序小样本检查，
永久标注custom_run，不能冒充完整F2。配置、源码、输入、硬件或CPU分配改变会拒绝续跑；
原始日志只追加，不挑最快重复。不要在串行计时同时运行其他耗CPU的工作。
每次请求与重放还保存独立、不可改写的`.records/`记录；JSONL索引缺条时可由独立记录追加恢复，
不用重算已保存请求。两份记录冲突会报错，结束前从磁盘核对全部预定请求，不能只依赖内存计数。
环境指纹包含Python与PyYAML、NumPy、Matplotlib版本，队列数受物理核及cgroup CPU配额共同限制。
计时对本运行器的覆盖率、核验、统计和绘图持互斥锁，避免本工具的并发任务干扰。
`request_wall_seconds`覆盖完整请求，`public_call_*`记录公开方法调用。
统一机制内核原有的求解/内置核验分时字段保留；生产入口没有分时字段时，`solve_clock_scope`
明确标为包含内置检查的公开调用。独立原版重放的时间始终单列，不伪造无法分离的内部耗时。

## 七张图与结果解释

main生成图1至3，mechanism生成图4，scale生成图5，numeric_scale生成图6，timing生成图7。
同一图导出PDF/PNG不重复计数。图3主图固定CPU扫描A的两档能量负载和G=1，
图2主图固定M=4、D=T；所有配置的原始统计同时提供。

认证分母含全部预选输入。只有对方完成未获证书时才记确定新增，超时保持未决。
独立重放未通过的输出不能计入认证或绘图。上界只比较双方整体认证的完整向量，
CO首单位基准用R−C+1。负改进不截断；图4的F/R对八个配置等权，配对覆盖不足时缺点并说明。
bootstrap使用5,000次配对分层骨架重采样，重复和相关变体不增加样本数。
图5耗时条件于完成；图6要求三次稳定完成；图7只用预定第一次运行。
任何超时都不伪装成30/60/120秒完成值。完成子集P95与无法确定的总体P95分开记录。
`main`/`pilot`等单次运行生成的速度配对只供流程诊断；论文正式速度比使用`timing`
及`mechanism_timing`中双方三次均稳定完成的配对，不把单次结果当成三次重复结果。

`tables/`包含状态计数、配对状态、确定新增、逐输入精度、区间、条件耗时、稳定重复、配对速度和预算覆盖。
完整配对、认证差及精度还按配置单列；精度表包含均值、中位数、绝对缩短、相同向量比例和变紧/变松数量。
`certificates/`及`verification.jsonl`保存证书与独立重放；`sources.json`及`plan.json`固定运行身份。
输出目录被git忽略，测量日志、证书、论文、PDF和研究压缩包不提交代码仓。
`generation_diagnostics.json`按骨架记录取整重试、UUniFast舍弃和生成失败；观察器读取冻结生成器的调用返回，
不改变随机数流或已固定输入。机制内核的证据类型计数覆盖实际访问的通过窗口，包含中间迭代与复核，
不能当作独立新增认证数。诊断结果不混入生产性能计时。
机制库完成并重放后，另运行`python experiments/asapblock_f2/run.py diagnose --suite mechanism`。
正式诊断每个配置按固定哈希预选一个输入，共24个；自定义小样本检查其全部选定输入。
在Full最终证书相同X处记录局部上界改变、供能阈值跨越、各删减版窗口是否通过及最终配对状态。
Full未认证的预选项保留跳过原因。诊断每项120秒，单列于核心分析预算和性能计时之外。

## 已有720组与构造材料

原720输入和180个构造/邻域输入按旧种子重新生成并核对原归档身份，始终独立于F2新随机总体。
可选择 `legacy_ablation` 或 `constructed`；不包含在28,248次核心预算中。
旧结果优先使用相邻复现目录的独立统计视图，不与F2的新硬件时间合并。
若必须重跑720组，`legacy_ablation`统一60秒；原300秒追加视图由旧复现入口按原规则保留，
不能覆盖F2的主预算认证率。此处不默认启动追加请求，也不启动w=D或analytic/flow实验。
