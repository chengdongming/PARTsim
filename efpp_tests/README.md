# EFPP调度器测试

## 测试文件说明

### 配置文件
- `efpp_test_config.yml` - 基础测试配置（上午8点，初始能量1.0J）
- `test1_0_12_zero_energy.yml` - 测试1：0点12点，初始能���为0
- `test2_energy_comparison.yml` - 测试2：7个时间点能量对比（中午12点，初始能量7.0J）

### 任务文件
- `efpp_test_tasks.yml` - 测试任务集

## 测试场景

### 测试1：0:00-12:00，初始能量为0
**目的**：验证EFPP在初始能量为0的情况下，能否通过收集太阳能启动调度

**配置**：
- 时间：00:00:00（凌晨）
- 初始能量：0.0J
- 仿真时长：12小时（43200000ms）
- 期望：从0开始收集能量，能量足够后开始调度任务

**运行命令**：
```bash
cd /home/devcontainers/PARTSim-project
./run_sim.sh -s efpp_tests/test1_0_12_zero_energy.yml -t efpp_tests/efpp_test_tasks.yml -d 43200000 -o efpp_tests/trace_test1.json
```

### 测试2：能量收集和消耗对比
**目的**：在7个不同时间点测试理论能量和实际能量的对比

**配置**：
- 时间：12:00:00（中午，最大辐照度）
- 初始能量：7.0J
- 期望：完成所有8个任务

**运行命令**：
```bash
cd /home/devcontainers/PARTSim-project
./run_sim.sh -s efpp_tests/test2_energy_comparison.yml -t efpp_tests/efpp_test_tasks.yml -d 2000 -o efpp_tests/trace_test2.json
```

## EFPP vs EPP 关键区别

在能量不足时的行为：

**EPP**：
```
检查task_high: 能量不足 → ⛔ 立即停止，不检查后续任务
```

**EFPP**：
```
检查task_high: 能量不足 → ✅ 继续检查task_mid
检查task_mid: 能量不足 → ✅ 继续检查task_low
检查task_low: 能量足够 → 🎯 调度task_low
```

## 预期结果

### 测试1预期：
- ✅ 能量从0J开始收集
- ✅ 天亮后（约6:00）能量逐渐增加
- ✅ 能量足够后开始调度任务
- ✅ EFPP会比EPP调度更多低优先级任务（弹性调度）

### 测试2预期：
- ✅ 初始7J能量
- ✅ 中午12点太阳能充足
- ✅ 完成所有8个任务
- ✅ 理论能量 ≈ 实际能量（误差<1%）
