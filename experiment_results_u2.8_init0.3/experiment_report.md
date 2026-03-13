# 极限压测实验报告

## 实验配置

| 参数 | 值 | 说明 |
|------|-----|------|
| 任务数量 (TASK_N) | 10 | 每个任务集包含10个任务 |
| 利用率 (TASK_U) | 2.8 | 高利用率，制造能源赤字 |
| 周期范��� | 20-100 ms | 标准周期范围 |
| 任务集数量 (NUM_TASKSETS) | 20 | 每个配置点20个任务集 |
| 仿真时间 (SIMULATION_TIME) | 10000 ms | 10秒仿真 |
| 初始能量比例 | 30% | 电池容量的30% |
| 开始时间 (start_time_ms) | 14400000 | 04:00，涓流充电状态（极低太阳能输入）|
| 电池容量 | 1.0, 3.0, 5.0, 10.0, 15.0, 25.0, 40.0, 60.0 J | 8种容量 |
| 超时阈值 | 120秒 | 单次仿真最大运行时间 |

## 测试算法

共9种算法，分为3个家族：

### ASAP 家族 (尽快调度)
- `gpfp_asap_block` - 阻塞模式
- `gpfp_asap_nonblock` - 非阻塞模式
- `gpfp_asap_sync` - 同步模式

### ALAP 家族 (尽晚调度)
- `gpfp_alap_block` - 阻塞模式
- `gpfp_alap_nonblock` - 非阻塞模式
- `gpfp_alap_sync` - 同步模式

### ST 家族 (静态调度)
- `gpfp_st_block` - 阻塞模式
- `gpfp_st_nonblock` - 非阻塞模式
- `gpfp_st_sync` - 同步模式

## 实验规模

- **总仿真数**: 9 × 8 × 20 = **1440 次**
- **追踪文件数**: **1440 个**
- **并行进程数**: 12

## 实验结果

### 统计指标

1. **Failure Rate (Job-level)** - 任务失败率
2. **Preemptions (Count)** - 抢占次数
3. **Total Idle Time (ms)** - 总空闲时间
4. **Avg Exec Time (ms)** - 平均执行时间
5. **Scheduler Overhead** - 调度器开销
6. **Avg Energy Level (J)** - 平均能量水平

### 输出文件

| 文件 | 路径 |
|------|------|
| 原始数据 | `experiment_results_u2.8_init0.3/raw_data_diff.csv` |
| ASAP 图表 | `experiment_results_u2.8_init0.3/figure_asap_diff.png` |
| ALAP 图表 | `experiment_results_u2.8_init0.3/figure_alap_diff.png` |
| ST 图表 | `experiment_results_u2.8_init0.3/figure_st_diff.png` |
| 汇总表格 | `experiment_results_u2.8_init0.3/table1_diff.md` |

## 严重问题：大量死锁超时

### 问题描述

在极限压测条件下（高利用率 U=2.8 + 涓流充电 `start_time_ms=14400000`），**几乎所有算法**都发生了120秒超时。

### 受影响的算法

| 算法 | 超时情况 |
|------|----------|
| `gpfp_asap_block` | 所有电池容量均超时 |
| `gpfp_asap_nonblock` | 所有电池容量均超时 |
| `gpfp_asap_sync` | 所有电池容量均超时 |
| `gpfp_alap_block` | 所有电池容量均超时 |
| `gpfp_alap_nonblock` | 所有电池容量均超时 |
| `gpfp_alap_sync` | 所有电池容量均超时 |
| `gpfp_st_block` | 部分超时 |
| `gpfp_st_nonblock` | 所有电池容量均超时 |
| `gpfp_st_sync` | 所有电池容量均超时 |

### 根因分析

1. **极端能源赤字**: U=2.8 且太阳能输入极低（04:00时段仅1.42W），导致系统长期处于能源匮乏状态

2. **C++ 仿真引擎死锁**: 在能源极度匮乏时，调度器可能进入无限等待状态：
   - 任务在就绪队列中无限期挂起（饿死）
   - 同步机制可能造成死锁
   - 能量等待逻辑可能没有正确的超时处理

3. **缺少饿死检测**: C++ 引擎在任务饿死时不主动触发 `dline_miss` 事件

### 脚本修复

本次实验前对 Python 脚本进行了以下修复：

1. **新增饿死判定逻辑**:
   ```python
   # 边界免责期：最后 200ms 内到达的任务不算饿死
   starvation_threshold = last_time - 200.0
   for job_key, arrival_time in open_jobs:
       if arrival_time <= starvation_threshold:
           starved_count += 1
   stats['failed_instances'] += starved_count
   ```

2. **修复平均执行时间计算**:
   ```python
   # 使用 completed_instances 而非 total_instances 作为分母
   if stats['completed_instances'] > 0:
       stats['avg_execution_time'] = stats['busy_time'] / stats['completed_instances']
   ```

3. **增加超时报错信息**:
   ```python
   except subprocess.TimeoutExpired:
       print(f"\n❌ [致命错误] 算法 {algorithm} 在 Battery={battery} 时死锁卡住，超过120秒被强杀！请检查 C++ 代码！")
   ```

## 建议

### 短期修复

1. **检查 C++ 调度器死锁**: 重点检查 `gpfp_*_sync` 和 `gpfp_*_nonblock` 算法的等待逻辑

2. **添加饿死检测**: 在 C++ 引擎中添加任务饿死检测机制，当任务等待超过一定时间时触发 `dline_miss`

3. **增加仿真超时保护**: 考虑在 C++ 层面添加仿真步数上限或时间上限

### 长期改进

1. **能量感知调度优化**: 在极端能源匮乏时，需要有更智能的任务丢弃策略

2. **降级模式**: 当能量极低时，系统应进入降级模式，仅执行最关键的任务

3. **边界条件测试**: 增加更多极限条件下的单元测试

## 结论

本次极限压测实验成功暴露了 C++ 仿真引擎在高利用率 + 涓流充电条件下的严重死锁问题。虽然 Python 脚本的数据统计逻辑已修复，但需要先解决 C++ 层面的死锁问题才能获得有效的实验数据。

建议优先修复 C++ 调度器中的死锁问题，然后重新运行实验。

---

*报告生成时间: 2026-03-14*
*实验目录: `experiment_results_u2.8_init0.3/`*
