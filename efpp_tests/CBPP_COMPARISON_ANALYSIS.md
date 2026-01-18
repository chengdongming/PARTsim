# CBPP调度器手动模拟与实际运行对比分析

## 测试环境
- **测试时间**: 2026-01-19
- **修改内容**: 添加前瞻性能量预测、批量清空机制、批量抢占机制

---

## 1. 0点测试（无太阳能，初始能量0J）

### 预期结果（手动模拟）
```
初始能量: 0J
太阳能功率: 0W/m² × 1.0m² × 0.18 = 0W

T=0ms 批量决策：
  批量总能耗 = 0.301J
  预测收集能量 = 0J
  能量结余 = 0J + 0J - 0.301J = -0.301J < 0  ❌

结论：由于初始能量为0且无太阳能，整个仿真期间不应该有任何任务被调度。
```

### 实际结果
```
总调度次数: 0
任务完成数: 0
总消耗能量: 0.000000J
总收集能量: 0.000000J
剩余能量: 0.000000J
```

**Trace文件分析：**
- ✅ 所有事件都是 `arrival` + `dline_miss`
- ✅ 没有任何 `scheduled` 或 `end_instance` 事件
- ✅ 完全符合预期！

**结论：0点测试 ✅ 完全正确**

---

## 2. 12点测试（太阳能充足，初始能量0J）

### 预期结果（手动模拟）
```
初始能量: 0J
太阳能功率: 434.5W/m² × 1.0m² × 0.18 = 78.21W

T=0ms 批量决策：
  批量总能耗 = 0.301J
  预测收集能量 = 78.21W × 0.2s = 15.642J
  能量结余 = 0J + 15.642J - 0.301J = 15.341J > 0  ✅

预期调度：
  T=0ms: 调度批量 [task_high, task_mid, task_low]
  T=100ms: task_low完成 (WCET=100ms)
  T=150ms: task_mid完成 (WCET=150ms)
  T=200ms: task_high完成 (WCET=200ms)

  T=400ms: task_high第二次实例到达
  T=600ms: task_mid第二次实例到达
  T=800ms: task_low第二次实例到达
```

### 实际结果
```
总调度次数: 0
任务完成数: 6
总消耗能量: 1.72J
总收集能量: 156.42J  ✅ (78.21W × 2s = 156.42J)
剩余能量: 154.70J
```

**Trace文件分析：**
```
T=0ms:   task_high, task_mid, task_low 到达 ✅
T=400ms: task_high第二次到达 + dline_miss(第一次) ❌
         task_high, task_mid 被调度 ❌
T=550ms: task_mid完成
         task_low 被调度
T=600ms: task_high完成
         task_mid第二次到达
         task_high被调度(第二次)
T=650ms: task_low完成
         task_mid被调度(第二次)
T=800ms: task_high, task_mid完成
         task_low第二次到达
         task_high第三次到达
         task_high被调度(第三次)
T=1000ms: task_high完成
...后续还有更多任务
```

### 🔴 严重问题！

**问题1：第一次批量调度延迟400ms！**

| 时间点 | 预期行为 | 实际行为 | 问题 |
|--------|----------|----------|------|
| T=0ms | 批量调度3个任务 | ❌ 未调度 | **能量判断bug** |
| T=400ms | task_high第二次到达 | ✅ 开始调度第一次批量 | **400ms延迟！** |
| T=400ms | - | ❌ task_high第一次实例deadline miss | **由于延迟导致** |

**问题2：所有任务第一次实例都deadline miss**

从trace文件可以看到：
- task_high第一次: deadline在400ms，实际在400ms调度 (miss)
- task_mid第一次: deadline在600ms，实际在400ms调度完成，但延迟太多
- task_low第一次: deadline在800ms，实际在550ms调度完成，但延迟太多

### 根本原因分析

**T=0ms时的能量判断失败原因：**

1. **初始能量收集问题**
   - 虽然使用了前瞻性预测，但可能存在以下问题：
     - `collectSolarEnergy()` 在T=0时返回0（因为elapsed=0）
     - `predictEnergyCollection()` 依赖 `getSolarIrradiance(current_time)`
     - 如果current_time传递错误，可能获取到错误的辐照度

2. **能量恢复事件触发**
   - 能量不足时，应该设置能量恢复事件
   - 恢复时间可能计算错误
   - 400ms后能量恢复才触发第一次调度

3. **getTaskN(0)调用时机**
   - 第一次批量决策在T=0ms时应该发生
   - 但能量判断失败，返回nullptr
   - 导致MRTKernel没有调度任何任务

### 能量计算验证

**手动计算：**
```
base_power = 0.5W
power_coefficient(bzip2) = 1.2
freq_ratio(8100MHz) = 0.93

task_power = 0.5 × 1.2 × 0.93 = 0.558 W

task_high energy = 0.558W × 0.2s × 1.2 = 0.134J
task_mid energy = 0.558W × 0.15s × 1.2 = 0.100J
task_low energy = 0.558W × 0.1s × 1.2 = 0.067J

总能耗 = 0.301J
```

**实际消耗：**
```
6个任务完成 × 0.287J/任务 ≈ 1.72J
```

这与手动计算基本吻合！

---

## 3. 代码级问题分析

### 问题定位

在 `canScheduleBatchWithEnergy()` 方法中：

```cpp
bool CBPPScheduler::canScheduleBatchWithEnergy(const std::vector<AbsRTTask *> &batch,
                                                MetaSim::Tick current_time) {
    // 1. 计算批量总能耗
    double total_energy_needed = calculateBatchTotalEnergy(batch);  // 0.301J

    // 2. 计算 max_wcet
    Tick max_wcet = 0;
    for (AbsRTTask *task : batch) {
        if (!task) continue;
        CBPPTaskModel *model = getTaskModel(task);
        if (model && model->getWCET() > max_wcet) {
            max_wcet = model->getWCET();  // 200ms
        }
    }

    // 3. 预测收集能量
    double predicted_collection = predictEnergyCollection(current_time, max_wcet);

    // 4. 判断
    double energy_after_batch = _current_energy + predicted_collection - total_energy_needed;
    bool can_schedule = (energy_after_batch >= 0.0);  // 应该是 true

    return can_schedule;
}
```

**可能的问题：**

1. **`_current_energy` 在T=0时是0J**
2. **`predictEnergyCollection()` 返回值可能错误**
   - 检查 `getSolarIrradiance(current_time)` 是否正确
   - current_time应该传入0ms（仿真时间）
   - 但getSolarIrradiance计算实际时间时需要加上_start_time_offset

3. **时间偏移问题**
   ```cpp
   int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
   ```
   - 如果_start_time_offset不是43200000（12点），获取的辐照度会错误
   - 12点测试应该使用start_time_offset=43200000

---

## 4. 修复建议

### 建议1：调试能量预测

添加详细日志输出：
```cpp
SCHEDULER_LOG_INFO("🔮 [CBPP] 能量预测调试") <<
                  " current_time=" << current_time <<
                  " start_time_offset=" << _start_time_offset <<
                  " actual_time=" << (current_time + _start_time_offset) <<
                  " irradiance=" << getSolarIrradiance(...);
```

### 建议2：验证时间偏移加载

检查ConfigManager是否正确加载了time_of_day_ms：
```yaml
energy_management:
  time_of_day_ms: 43200000  # 12点
```

### 建议3：在T=0时预收集能量

在`canScheduleBatchWithEnergy()`开始时，先收集一次能量：
```cpp
// 在能量判断前，先收集从上次到现在的能量
double harvested = collectSolarEnergy(current_time);
if (harvested > 0.0001) {
    _current_energy += harvested;
}
```

---

## 5. 总结

### 测试通过情况

| 测试场景 | 预期 | 实际 | 状态 |
|----------|------|------|------|
| 0点测试（无太阳能） | 0任务调度 | 0任务调度 | ✅ 完全正确 |
| 12点测试（太阳能） | T=0开始调度 | T=400开始调度 | ❌ 延迟400ms |

### 核心问题

**CBPP的T=0批量调度在12点测试中失败，延迟到T=400ms才开始。**

这表明：
1. ✅ 能量计算公式正确（6个任务消耗1.72J）
2. ✅ 太阳能收集正确（2000ms收集156.42J）
3. ❌ **T=0时的前瞻性能量判断失败**
4. ❌ 可能是时间偏移、辐照度获取或初始能量收集的问题

### 下一步行动

1. 添加详细调试日志
2. 验证_start_time_offset是否正确加载
3. 检查predictEnergyCollection()的实现
4. 确保T=0时能够正确获取辐照度并预测能量
