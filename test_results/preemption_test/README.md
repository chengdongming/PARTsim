# 三种能量感知调度算法抢占测试

## 测试目标

验证TIE、TGF、BTIE三种调度算法在多核环境下是否正确支持任务抢占功能。

## 测试场景

**抢占测试设计：**
- CPU核心：3核
- 任务数量：4个（确保会发生抢占）
- 初始能量：100J（充足能量，避免能量不足干扰）
- 测试时长：100ms

**任务集：**
| 任务 | 周期 | WCET | 到达偏移 | 优先级 |
|------|------|------|---------|--------|
| task_1 | 20ms | 5ms | 10ms | 最高（周期最短） |
| task_2 | 30ms | 20ms | 0ms | 中 |
| task_3 | 40ms | 20ms | 0ms | 中低 |
| task_4 | 50ms | 20ms | 0ms | 最低 |

**抢占机制：**
1. 0ms时，task_2、task_3、task_4到达，占满3个CPU核心
2. 10ms时，task_1到��（高优先级，周期20ms最短）
3. task_1抢占task_4（低优先级，周期50ms最长）

## 抢占验证

### 10ms时刻事件序列

**TIE调度算法：**
```json
{ "time" : "10", "event_type" : "arrival", "task_name" : "task_1"}
{ "time" : "10", "event_type" : "descheduled", "task_name" : "task_4"}
{ "time" : "10", "event_type" : "scheduled", "task_name" : "task_1"}
```
✅ **抢占成功**：task_4被抢占，task_1被调度

**TGF调度算法：**
```json
{ "time" : "10", "event_type" : "arrival", "task_name" : "task_1"}
{ "time" : "10", "event_type" : "descheduled", "task_name" : "task_4"}
{ "time" : "10", "event_type" : "scheduled", "task_name" : "task_1"}
```
✅ **抢占成功**：task_4被抢占，task_1被调度

**BTIE调度算法：**
```json
{ "time" : "10", "event_type" : "arrival", "task_name" : "task_1"}
{ "time" : "10", "event_type" : "descheduled", "task_name" : "task_4"}
{ "time" : "10", "event_type" : "scheduled", "task_name" : "task_1"}
```
✅ **抢占成功**：task_4被抢占，task_1被调度

## 测试结果统计

| 指标 | TIE | TGF | BTIE |
|------|-----|-----|------|
| 任务完成数 | 22 | 22 | 22 |
| Deadline Miss | 0 | 0 | 0 |
| 抢占事件 | ✅ 发生 | ✅ 发生 | ✅ 发生 |
| 能量消耗 | 117mJ | 117mJ | 123mJ |

## 结论

✅ **三种调度算法都正确支持抢占功能：**

1. **TIE**：正确实现Rate-Monotonic抢占，高优先级任务能够抢占低优先级任务
2. **TGF**：正确实现Rate-Monotonic抢占，贪心策略不影响抢占机制
3. **BTIE**：正确实现Rate-Monotonic抢占，批量调度不干扰抢占逻辑

**抢占机制验证：**
- ✅ 优先级判断正确（基于周期的Rate-Monotonic调度）
- ✅ 高优先级任务到达时能够立即抢占低优先级任务
- ✅ 被抢占任务正确暂停（descheduled）
- ✅ 抢占任务正确调度（scheduled）

## 测试文件

### 配置文件
- [tasks_preemption.yml](tasks_preemption.yml) - 抢占测试任务配置
- [system_TIE.yml](system_TIE.yml) - TIE系统配置
- [system_TGF.yml](system_TGF.yml) - TGF系统配置
- [system_BTIE.yml](system_BTIE.yml) - BTIE系统配置

### 追踪数据
- [TIE_preemption_trace.json](TIE_preemption_trace.json) - TIE抢占追踪
- [TGF_preemption_trace.json](TGF_preemption_trace.json) - TGF抢占追踪
- [BTIE_preemption_trace.json](BTIE_preemption_trace.json) - BTIE抢占追踪

### 可视化图表
- [TIE_preemption_gantt.png](TIE_preemption_gantt.png) - TIE抢占甘特图
- [TGF_preemption_gantt.png](TGF_preemption_gantt.png) - TGF抢占甘特图
- [BTIE_preemption_gantt.png](BTIE_preemption_gantt.png) - BTIE抢占甘特图

在甘特图中可以清晰看到：
- 0-10ms：task_2、task_3、task_4运行
- 10ms时刻：task_1到达，抢占task_4
- 10-15ms：task_1、task_2、task_3运行
- 15ms之后：task_1完成，task_4继续执行
