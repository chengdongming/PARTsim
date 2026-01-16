# 抢占式调度测试示例

本目录包含抢占式调度算法的测试示例，演示EDF、FP和RM调度器的抢占行为。

## 调度器类型

### 1. EDF (Earliest Deadline First)
- **动态优先级**：基于绝对deadline，deadline越早优先级越高
- **抢占特性**：高优先级任务（deadline更早）会抢占低优先级任务
- **适用场景**：动态实时系统

### 2. FP (Fixed Priority)
- **静态优先级**：需要显式指定每个任务的优先级
- **抢占特性**：高优先级任务会抢占低优先级任务
- **适用场景**：静态实时系统

### 3. RM (Rate Monotonic)
- **静态优先级**：基于任务周期，周期越短优先级越高
- **抢占特性**：短周期任务会抢占长周期任务
- **适用场景**：周期性实时任务

## 任务集配置

### 抢占式测试任务集

| 任务名称 | 周期(ms) | Deadline(ms) | 执行时间(ms) | 优先级 |
|---------|---------|-------------|-------------|--------|
| task_high | 50 | 50 | 10 | 高 |
| task_mid | 100 | 100 | 20 | 中 |
| task_low | 200 | 200 | 30 | 低 |

### 预期抢占行为

**EDF调度器**：
```
t=0ms:   task_high(10ms) → 完成
t=10ms:  task_mid(20ms) → 执行中
t=30ms:  task_mid完成
t=30ms:  task_low(30ms) → 执行中
t=50ms:  task_high到达，抢占task_low → task_high执行10ms
t=60ms:  task_high完成 → task_low恢复执行（剩余20ms）
...
```

**RM调度器**：
- task_high (周期50ms) → 优先级最高
- task_mid (周期100ms) → 优先级中
- task_low (周期200ms) → 优先级最低

## 文件说明

- `config_edf.yml` - EDF调度器配置
- `config_fp.yml` - FP调度器配置
- `config_rm.yml` - RM调度器配置
- `test_preemptive.cpp` - 抢占式测试主程序
- `Makefile` - 编译脚本

## 使用方法

```bash
# 编译
make

# 运行EDF测试
./test_edf

# 运行FP测试
./test_fp

# 运行RM测试
./test_rm

# 查看trace文件
cat trace_edf.json | jq '.schedules[] | select(.preempted == true)'
```

## 预期输出

- **调度次数**：高优先级任务调度次数最多
- **抢占次数**：trace中应包含`preempted: true`的记录
- **deadline miss**：合理负载下应为0
