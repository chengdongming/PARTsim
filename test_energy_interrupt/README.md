# 能量中断测试目录

本目录用于测试运行时能量检查和任务中断机制。

## 测试场景

### 夜间能量中断测试

这个测试场景模拟夜间无太阳能收集的环境，初始能量很小（0.1J），任务能耗较高。

**预期行为**：
- 任务开始执行后，能量很快耗尽
- `checkAndInterruptRunningTasks()` 方法会在每个tick检查运行中的任务
- 当能量不足以继续执行1ms时，任务会被中断
- 中断的任务会放回ready队列，等待能量恢复后继续执行

## 文件列表

### 任务集文件
- `test_interrupt_tasks.yml` - 测试任务集（3个高能耗任务）

### 系统配置文件
- `night_tie.yml` - TIE调度器夜间配置
- `night_btie.yml` - BTIE调度器夜间配置
- `night_tgf.yml` - TGF调度器夜间配置

## 运行测试

### 测试TIE调度器
```bash
cd /home/devcontainers/PARTSim-project
./run_sim.sh -s test_energy_interrupt/night_tie.yml -t test_energy_interrupt/test_interrupt_tasks.yml -d 2000 -o test_energy_interrupt/trace_tie_interrupt.json
```

### 测试BTIE调度器
```bash
./run_sim.sh -s test_energy_interrupt/night_btie.yml -t test_energy_interrupt/test_interrupt_tasks.yml -d 2000 -o test_energy_interrupt/trace_btie_interrupt.json
```

### 测试TGF调度器
```bash
./run_sim.sh -s test_energy_interrupt/night_tgf.yml -t test_energy_interrupt/test_interrupt_tasks.yml -d 2000 -o test_energy_interrupt/trace_tgf_interrupt.json
```

## 预期输出

在仿真日志中应该能看到类似以下的输出：

```
⚡ [TIE] 任务能量不足，将中断: PeriodicTask task_high_energy 需要1ms=0.000600J 当前能量=0.000100J
🛑 [TIE] 中断任务（能量不足）: PeriodicTask task_high_energy
⏸️ [TIE] 任务已中断，等待能量恢复: PeriodicTask task_high_energy
📊 [TIE] 本次tick中断了 2 个任务（能量不足）
```

在统计信息中，应该看到：
- `能量不足跳过: > 0` (表示触发了中断机制)
