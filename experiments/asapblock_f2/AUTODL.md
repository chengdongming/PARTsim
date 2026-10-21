# 全新AutoDL服务器：一个脚本配置并启动

使用已选的Python 3.12 / Ubuntu 22.04镜像。实验使用CPU，不需要安装额外CUDA或PyTorch。
默认28路并发，实际并发由允许的物理核和CPU配额限制；7组共28,248次核心请求。
默认将三类重复计时也按任务集并发，并在输出中明确标注并发测量条件。

## 1. 上传脚本并执行

下载仓库中的`tools/ab_rta_autodl.py`，上传到AutoDL的`/root/autodl-tmp/`。
打开AutoDL终端，执行：

```bash
cd /root/autodl-tmp
python ab_rta_autodl.py start
```

第一次会依次安装缺少的git/tmux、拉取固定代码、创建独立venv、安装固定依赖、检测资源、
运行33项代码自检，然后通过tmux在后台准备正式输入、执行前置核验并启动全部正式实验。
安装输出在`ab_rta_f2/logs/setup.log`，代码自检输出在`ab_rta_f2/logs/selftest.log`。
看到“已启动后台实验”后可以断开SSH/网页连接；机器必须保持开机。
不需要再次手动克隆仓库、安装依赖或启动第二份队列。

若要保留原F2的串行计时口径，在一个新目录首次执行：

```bash
python ab_rta_autodl.py start --serial-timing --root /root/autodl-tmp/ab_rta_f2_serial
```

该模式覆盖率和核验仍并发，三类重复计时单核串行。
同一批次的并发配置、代码、环境固定，不能中途切换模式。

## 2. 查看进度和日志

```bash
cd /root/autodl-tmp
python ab_rta_autodl.py status
python ab_rta_autodl.py logs
```

`status`同时检查真实后台会话和进度文件；不会仅凭旧的RUNNING文件声称队列仍在运行。
`logs`持续显示正式日志，按Ctrl+C只是退出看日志，不停止后台实验。
首次安装尚未完成时，可在另一个终端查看：

```bash
tail -n 30 /root/autodl-tmp/ab_rta_f2/logs/setup.log
tail -n 30 /root/autodl-tmp/ab_rta_f2/logs/selftest.log
```

## 3. 中断后恢复

```bash
cd /root/autodl-tmp
python ab_rta_autodl.py resume
```

机器重启后，使用相同命令恢复。保留已有记录，不重新计算已保存的请求；已存在后台会话时不重复启动。
若中断恰好留下不完整的最后一行JSONL索引，先备份原日志，再从不可改写的逐请求记录恢复；
中段损坏或两份记录冲突时拒绝自动处理。真正的程序错误也不会被悄悄忽略或改成未获证书。

首次安装失败尚未生成配置文件时，修好网络后重新执行`start`。
资源或镜像发生改变、或者想改变并发度时，使用新目录开始新批次。
可以用`--workers 16`在首次启动时将默认并发降为16路。

## 4. 打包和下载

确认`status`显示COMPLETE后执行：

```bash
cd /root/autodl-tmp
python ab_rta_autodl.py pack
```

脚本会输出压缩包路径，例如`/root/autodl-tmp/ab_rta_f2/F2_server_results_日期_时间.zip`。
在JupyterLab文件浏览器中找到该文件，右键Download即可。
压缩包包括结果、原始记录、证书、图表、输入、日志、固定代码版本和依赖清单。
代码使用Git的固定提交复现，不重复打包整个仓库或Python环境。未完成的中断批次也可打包备份，其状态会保留。
运行期间禁止额外压缩整个结果目录；可以下载程序已经生成的数据检查点。

## 固定版本和输出位置

- 核心实验代码固定为`1563ec7d63f46a059c41314d8ca1449ff0933e3f`。
- 源码集合SHA-256为`f0fb5bdc6f9f847d8fa70eeb39be77a6ec4cadbebdfee2850ba52dc1621c1014`。
- 依赖固定为PyYAML 6.0.3、NumPy 2.3.5、Matplotlib 3.10.8，和交付前试跑一致。
- 实验输入、RTA/SEQ逻辑、时间预算和样本数保持冻结方案。固定w=D与analytic/flow对比不启动。
- 默认项目、环境、日志及结果都在`/root/autodl-tmp/ab_rta_f2/`，与这边4核批次的数据分开。
- 安装后`install.json`记录配置；`resources.json`记录实际资源；`environment_freeze.txt`记录全部Python依赖。

如需自定义目录，每条后续命令都带相同的`--root /你的目录`。
GitHub连接失败时，按平台说明检查学术资源加速；脚本不切换到第三方代码镜像，也不覆盖已保存结果。

平台参考：[后台运行](https://www.autodl.com/docs/daemon/)、[实例数据与数据盘](https://www.autodl.com/docs/instance_data/)。

交付前已通过15项启动器检查，包括重复启动保护、固定版本校验、中断日志恢复、真实Git稀疏拉取和结果包CRC检查；
另从GitHub全新拉取了固定提交并通过源码/依赖核对。目标AutoDL实例的实际安装和运行需执行上述命令。
