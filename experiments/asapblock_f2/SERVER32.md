# 在32核Linux服务器运行F2

7组分析都可按任务集并发；证书重放和机制诊断也支持并发。
完整队列依次运行各实验组，在每组内部同时处理多个任务集，避免启动7个各占28核的程序造成超额占用。
RTA计算内部保持原来的单核顺序；同一任务集的不同方法及三次重复固定在同一核依次运行。
所有输入、方法、样本数、三次重复、30/60/120秒分析预算及600秒独立重放预算均保持不变，共28,248次核心请求。

## 安装与检查资源

在专用的Linux服务器或已分配的作业资源内运行。32核指允许使用的物理核；32逻辑CPU可能只有16个物理核。
服务器版本在`codex/asapblock-f2-server32`分支。使用独立目录，不覆盖已有研究目录或已运行的实验数据。

```bash
git clone --depth 1 --branch codex/asapblock-f2-server32 --single-branch https://github.com/chengdongming/PARTsim.git PARTsim-server32
cd PARTsim-server32
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r experiments/asapblock_f2/requirements.txt
python experiments/asapblock_f2/campaign.py --workers 28 --timing-workers 28 --resources-only
python experiments/asapblock_f2/test_f2.py
```

要求Python 3.11或以上。资源报告显示允许的CPU、物理核代表、CPU配额、内存上限和实际分配数量。
32物理核服务器可先使用28个并发请求，为协调进程、系统操作和检查点保留余量；实际并发会受CPU配额限制。
单请求4 GiB是虚拟地址上限，不是内存预分配。先观察试运行记录的`peak_rss_bytes`，再结合服务器内存选择并发数；
32核的核数本身不足以决定内存是否够用。

## 模式一：保持冻结方案的串行计时

覆盖率、规模、消融、稳健性、重放和诊断使用最多28核；三类重复计时阶段仍一次只运行一个请求。
适合保持原F2正式计时口径。

```bash
mkdir -p experiments/asapblock_f2/runs/f2_server_serial_timing
nohup python -u experiments/asapblock_f2/campaign.py \
  --output experiments/asapblock_f2/runs/f2_server_serial_timing \
  --workers 28 --timing-workers 1 --verification-workers 28 \
  > experiments/asapblock_f2/runs/f2_server_serial_timing/console.log 2>&1 &
```

## 模式二：7组分析均可并发

三类重复计时也按任务集分配到最多28个核，并发阶段仍排斥本工具的其他CPU工作。
相同任务集的两种或三种方法以及三次重复固定在同一核依次执行。
这是显式的**并发计时扩展配置**，论文应说明并发数、CPU绑定、硬件、预算和测量范围。
并发条件下的单请求墙钟耗时受到共享缓存、带宽和CPU频率影响，不能当作串行独占耗时，
也不能将不同机器或不同并发模式的耗时合并统计。

```bash
mkdir -p experiments/asapblock_f2/runs/f2_server_parallel_timing
nohup python -u experiments/asapblock_f2/campaign.py \
  --output experiments/asapblock_f2/runs/f2_server_parallel_timing \
  --workers 28 --timing-workers 28 --verification-workers 28 \
  > experiments/asapblock_f2/runs/f2_server_parallel_timing/console.log 2>&1 &
```

程序自动生成冻结输入并进行前置核验，只有通过后才分析正式输入。表格、摘要和图明确记录并发计时模式。
两种模式均使用完整28,248次核心请求。不要同时执行两个完整队列；选择一个模式和一个新目录。

## 查看进度与恢复

```bash
cat experiments/asapblock_f2/runs/f2_server_parallel_timing/campaign_status.json
tail -n 20 experiments/asapblock_f2/runs/f2_server_parallel_timing/console.log
```

输出`RUNNING`表示当前正在处理的组和阶段。`completed_requests`是主分析请求计数；
独立重放和诊断在其各自记录中，不增加28,248的分母。超时保留为未决，不计作完成未获证书。
每组随后进行证书独立重放、统计及绘图；机制组另做同X诊断。

中断后在相同代码、硬件、配置和目录重跑同一命令，已保存的请求、重放和诊断会跳过。
同一目录禁止更改并发度，禁止从这次4核运行的半成品目录直接续跑到32核服务器。
服务器使用相同输入从新目录启动，原机器的已有记录保留，不作为服务器计时样本混入。

每500次覆盖率请求和每组结束会保存数据检查点。重复计时阶段内不压缩或传输检查点，
其逐请求不可改写记录仍正常保存。统计、绘图、输入生成等协调步骤保留顺序执行，
这些步骤不需要32路同时运行。

## 单独运行一组

需要先准备输入并做检查，再按分析、核验、统计、绘图顺序执行。例如：

```bash
python experiments/asapblock_f2/run.py prepare --output experiments/asapblock_f2/runs/f2_one_suite
python experiments/asapblock_f2/run.py check --output experiments/asapblock_f2/runs/f2_one_suite
python experiments/asapblock_f2/run.py run --suite timing --output experiments/asapblock_f2/runs/f2_one_suite --workers 28 --timing-workers 28
python experiments/asapblock_f2/run.py verify --suite timing --output experiments/asapblock_f2/runs/f2_one_suite --verification-workers 28
python experiments/asapblock_f2/run.py summarize --suite timing --output experiments/asapblock_f2/runs/f2_one_suite
python experiments/asapblock_f2/run.py plot --suite timing --output experiments/asapblock_f2/runs/f2_one_suite
```

`--limit`仅用于固定哈希选取的小样本流程检查，会永久标为自定义试跑；正式运行不设置`--limit`。

## 交付前验证

33项回归测试通过，包括32物理核/CPU配额的分配、7组并发入口、保留同核重复顺序、
计时阶段的排他锁、并发重放与诊断、日志完整性、恢复时禁止更改并发度，以及并发计时的图表标记。
10个方法的独立重放、4组新输入的生产/机制内核对齐、15条有限轨迹和113个作业检查通过。

真实2路子进程试跑完成28次分析，6份证书全部独立重放通过，0程序错误。
进程生命周期记录确认分析阶段和重放阶段各有两个工作进程同时存在，分析绑定在两个不同核。
这验证了多进程执行和记录机制，不是32核机器的吞吐量测试。32核上的实际加速比、内存峰值和计时波动需在目标服务器测量。
