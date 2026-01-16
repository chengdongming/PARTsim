# EPP调度器测试

## 文件清单

### 配置文件
- [config_epp_0am.yml](config_epp_0am.yml) - 午夜0点配置（无太阳能）
- [config_epp_8am.yml](config_epp_8am.yml) - 上午8点配置（中等太阳能）
- [config_epp_12pm.yml](config_epp_12pm.yml) - 中午12点配置（最大太阳能）

### 任务文件
- [tasks_epp.yml](tasks_epp.yml) - EPP任务集（不需要suspend(0)）

### 参考文档
- [ENERGY_DATA.md](ENERGY_DATA.md) - 能量数据详细说明

## 快速开始

### 1. 编译项目

```bash
cd /home/devcontainers/PARTSim-project
mkdir -p build && cd build
cmake ..
make -j$(nproc)
```

### 2. 运行测试

```bash
# 午夜测试（无太阳能）
./build/rtsim-exe -c epp_test/config_epp_0am.yml -t epp_test/tasks_epp.yml

# 上午8点测试（中等太阳能）⭐ 推荐
./build/rtsim-exe -c epp_test/config_epp_8am.yml -t epp_test/tasks_epp.yml

# 中午12点测试（最大太阳能）⭐ 推荐
./build/rtsim-exe -c epp_test/config_epp_12pm.yml -t epp_test/tasks_epp.yml
```

## 能量数据总结

### 任务能耗（8.1 GHz）

| 任务 | WCET | 能耗 |
|------|------|------|
| task_high | 250 ms | 0.50 J |
| task_mid | 400 ms | 0.80 J |
| task_low | 600 ms | 1.20 J |
| task_background | 800 ms | 1.44 J |

**总能耗**: 3.94 J

### 太阳能收集

| 时间 | 收集速率 | 每秒收集 |
|------|----------|----------|
| 00:00 (午夜) | 0.000 J/ms | 0.0 J/s |
| 08:00 (上午) | 0.097 J/ms | 97.4 J/s |
| 12:00 (中午) | 0.153 J/ms | 153.1 J/s |

### 级联调度结果（初始能量5.0J）

所有时间点都能成功调度4个任务：

```
步骤1: task_high   (-0.50J) → 剩余4.50J ✅
步骤2: task_mid    (-0.80J) → 剩余3.70J ✅
步骤3: task_low    (-1.20J) → 剩余2.50J ✅
步骤4: task_background (-1.44J) → 剩余1.06J ✅
```

## 测试场景

### 1. 级联调度测试
- **目的**: 验证EPP能否连续调度多个任务
- **配置**: 初始能量5.0J
- **预期**: 成功调度4个任务

### 2. 能量恢复测试
- **目的**: 验证能量不足时的恢复机制
- **配置**: 初始能量1.0J
- **预期**: 启动能量恢复事件，3-30ms后恢复调度

### 3. 抢占测试
- **目的**: 验证Tick级抢占延迟
- **场景**: task_low执行时task_high到达
- **预期**: 抢占延迟 < 1ms

## 与CASCADE/ASAP的区别

| 特性 | CASCADE/ASAP | EPP |
|-----|-------------|-----|
| 任务分片 | 50ms | ❌ 不分片 |
| 抢占时机 | 分片边界 | ✅ 任意Tick |
| suspend(0) | ✅ 需要 | ❌ 不需要 |
| 抢占延迟 | 最多50ms | < 1ms |

**任务代码对比**:

CASCADE/ASAP:
```yaml
code:
  - fixed(50, bzip2)
  - suspend(0)      # 必须有
  - fixed(50, bzip2)
  - suspend(0)      # 必须有
```

EPP:
```yaml
code:
  - fixed(250, bzip2)  # 直接执行，无需suspend
```

## 相关文档

- [EPP_SCHEDULER_DESIGN.md](../EPP_SCHEDULER_DESIGN.md) - EPP算法设计文档
- [TASKSET_COMPARISON.md](../TASKSET_COMPARISON.md) - 任务集对比
- [EPP_SIMULATION.md](../EPP_SIMULATION.md) - 运行模拟
- [../preemptive_test/COMPARISON.md](../preemptive_test/COMPARISON.md) - ASAP vs 抢占式调度器对比

## 预期输出

### 调度日志示例

```
🚀 [EPP] EPP Scheduler 初始化
📁 [EPP] 配置文件: epp_test/config_epp_8am.yml
✅ [EPP] EnergyBridge 初始化成功
💰 [EPP] 初始能量: 5.000000J
✅ [EPP] EPP Scheduler 初始化完成

🔄 [EPP] ===== 开始调度 =====
☀️ [EPP] 收集能量: 0.097416J
📊 [EPP] 空闲CPU: 3 当前能量: 5.097416J

✅ [EPP] 能量足够，调度任务: task_high
⏬ [EPP] 扣减能量后剩余: 4.597416J
🔄 [EPP] 继续级联检查下一个任务

✅ [EPP] 能量足够，调度任务: task_mid
⏬ [EPP] 扣减能量后剩余: 3.797416J
🔄 [EPP] 继续级联检查下一个任务

✅ [EPP] 能量足够，调度任务: task_low
⏬ [EPP] 扣减能量后剩余: 2.597416J
🔄 [EPP] 继续级联检查下一个任务

✅ [EPP] 能量足够，调度任务: task_background
⏬ [EPP] 扣减能量后剩余: 1.157416J

✅ [EPP] 本轮调度成功: 4个任务
🏁 [EPP] ===== 调度结束 =====
```

## 故障排除

### 问题1：编译错误

```bash
# 确保已重新编译
cd build
make clean
cmake ..
make -j$(nproc)
```

### 问题2：能量不足

如果看到能量不足日志：
```
❌ [EPP] 能量不足，停止本轮调度
```

**解决方法**：
- 增加 `initial_energy` 到 5.0J 或更高
- 或使用有太阳能的时间（8am或12pm配置）

### 问题3：任务无法调度

检查任务参数是否正确：
```yaml
params: "period=500,wcet=250,workload=bzip2"
```

确保 `wcet` 参数与 `fixed()` 指令的时间匹配。

## 联系方式

如有问题，请查看：
- 设计文档：[EPP_SCHEDULER_DESIGN.md](../EPP_SCHEDULER_DESIGN.md)
- 模拟分析：[EPP_SIMULATION.md](../EPP_SIMULATION.md)

## 追踪文件

三个��试配置的追踪文件已生成：

1. **[trace_epp_0am.json](epp_test/trace_epp_0am.json)** - 午夜0点（无太阳能）
2. **[trace_epp_8am.json](epp_test/trace_epp_8am.json)** - 上午8点（中等太阳能）⭐
3. **[trace_epp_12pm.json](epp_test/trace_epp_12pm.json)** - 中午12点（最大太阳能）⭐

详细分析请查看：[TRACE_ANALYSIS.md](TRACE_ANALYSIS.md)

