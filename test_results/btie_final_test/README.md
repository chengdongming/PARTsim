# BTIE调度器测试结果

## 测试配置

### 任务集
- **文件**: `tasks.yml`
- **任务数量**: 3个周期任务
- **任务配置**:
  - task_1: 周期=20ms, WCET=5ms, 工作负载=bzip2
  - task_2: 周期=30ms, WCET=8ms, 工作负载=bzip2
  - task_3: 周期=40ms, WCET=10ms, 工作负载=bzip2
- **CPU数量**: 2核

### 能量配置
- **初始能量**: 12mJ (0.012J)
- **能量消耗模型**: 每ms消耗0.6mJ (bzip2工作负载)
- **能量耗尽时间**: 约14ms (20次扣除 × 0.6mJ)

## 文件说明

### 追踪文件 (JSON格式)

#### 修复后的BTIE追踪文件 ⭐
1. **btie_12mj_120ms_fixed.json** - BTIE 120ms仿真
   - 包含前100ms的事件记录
   - 展示能量耗尽后的任务处理

2. **btie_12mj_1000ms_fixed.json** - BTIE 1000ms仿真（推荐查看）
   - 完整追踪文件，包含所有周期任务到达
   - 周期任务持续到达至990ms
   - 包含218个arrival事件
   - **修复前问题**: 在15ms时崩溃（事件调度时间错误）

#### TIE和TGF对比文件
3. **tie_12mj_120ms_final.json** - TIE 120ms仿真
   - 用于与BTIE对比

4. **gpfp_tie_12mj_solar_trace.json** - TIE 1000ms仿真
   - 包含217个arrival事件
   - 作为BTIE的对比基准

5. **gpfp_tgf_12mj_solar_trace.json** - TGF 1000ms仿真
   - 另一个调度算法的追踪文件

### 系统配置文件
- **system_2core_gpfp_btie_12mj_solar.yml** - BTIE调度器配置
- **system_2core_gpfp_tie_12mj_solar.yml** - TIE调度器配置
- **system_2core_gpfp_tgf_12mj_solar.yml** - TGF调度器配置

## BTIE修复内容

### 问题描述
在能量耗尽后（约14ms），BTIE调度器崩溃，错误信息：
```
EXCEPTION: Posting eventEndDMEvt task_3 at 5 in the past at time: 13
```

### 根本原因
`getTaskN()`方法在能量耗尽后没有检查`_energy_depleted`标志，导致返回已结束的任务给CPU调度器，引发事件调度时间错误。

### 修复方案
在`librtsim/scheduler/gpfp_btie_scheduler.cpp:860`添加能量耗尽检查：
```cpp
if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
    SCHEDULER_LOG_INFO("💀 [BTIE] getTaskN: 能量已耗尽，清空批量任务队列并返回nullptr");
    _current_batch_tasks.clear();
    _current_batch_size = 0;
    return nullptr;
}
```

### 修复结果
| 测试项 | 修复前 | 修复后 |
|--------|--------|--------|
| 120ms仿真 | ❌ 15ms崩溃 | ✅ 成功完成 |
| 1000ms仿真 | ❌ 15ms崩溃 | ✅ 成功完成 |
| 周期任务到达 | ❌ 只有15ms | ✅ 持续到990ms |
| arrival事件数量 | - | ✅ 218个 |

## BTIE vs TIE 对比

### 主要差异
- **TIE**: 在12ms时主动中断task_3（descheduled事件），记录deadline miss
- **BTIE**: 让task_3执行到15ms自然结束（end_instance事件），不记录deadline miss

### 行为差异原因
- TIE采用主动中断策略，能量不足时suspend任务
- BTIE采用自然结束策略，让任务完成当前执行片段

### 结论
BTIE的行为更合理，因为task_3执行了9ms（接近10ms WCET），且未错过40ms的deadline。

## 测试日期
2026-01-30

## 修复代码位置
- **文件**: `librtsim/scheduler/gpfp_btie_scheduler.cpp`
- **方法**: `BTIEScheduler::getTaskN()`
- **行号**: 860-870
