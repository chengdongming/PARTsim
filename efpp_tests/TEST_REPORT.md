# EPP/EFPP 全方位测试报告

## 测试环境
- CPU: 2核
- 时间: T=0 (无太阳能)
- 任务集: test3_tasks.yml

## 任务配置
| 任务 | 周期 | WCET | 能量系数 | 优先级 | 预估能量 |
|------|------|------|----------|--------|----------|
| task_high | 400ms | 100ms | 2.0 | 最高 | ~0.06J |
| task_mid | 600ms | 100ms | 1.2 | 中 | ~0.05J |
| task_low | 800ms | 25ms | 1.2 | 低 | ~0.0125J |

---

## 测试场景1: 优先级反转 (初始能量0.08J)

### 手动模拟预测

#### T=0时刻
- 初始能量: 0.08J
- 3个任务同时到达

**EPP行为（刚性优先级）：**
1. 检查task_high: 0.08J - 0.06J = 0.02J ≥ 0 ✅ 预扣0.06J
2. 级联检查task_mid: 0.02J - 0.05J = -0.03J < 0 ❌ → **立即停止**
3. 结果: 只调度task_high

**EFPP行为（弹性优先级）：**
1. 检查task_high: 0.08J - 0.06J = 0.02J ≥ 0 ✅ 预扣0.06J，剩余0.02J
2. 继续task_mid: 0.02J - 0.05J = -0.03J < 0 ❌ → 跳过
3. 继续task_low: 0.02J - 0.0125J = 0.0075J ≥ 0 ✅ 预扣0.0125J，剩余0.0075J
4. 结果: 调度task_high + task_low（2个CPU并行）

### 实际追踪文件验证

#### EFPP结果 (trace_test3_efpp_FINAL.json)
```json
T=0ms:
  - scheduled: task_high
  - scheduled: task_low
T=25ms:  end_instance: task_low
T=100ms: end_instance: task_high
T=400ms: arrival: task_high
  - scheduled: task_high
T=500ms: end_instance: task_high
T=600ms: arrival: task_mid
  - dline_miss: task_mid (第一次到达的)
```

**验证结果：✅ 符合预期！**
- T=0时刻确实调度了task_high和task_low
- 体现了EFPP的弹性特性：能量不足时继续检查低优先级任务
- task_mid因能量不足被跳过

#### EPP结果 (trace_test3_epp_FINAL.json)
```bash
cat efpp_tests/trace_test3_epp_FINAL.json | python3 -m json.tool | grep -E "\"time\":|event_type" | head -30
```

---

## 测试场景2: 能量充足场景

**配置:** 修改初始能量为0.20J

### 手动模拟
- 初始: 0.20J
- task_high: 0.06J → 剩余0.14J
- task_mid: 0.05J → 剩余0.09J
- 预期: EPP和EFPP都应该调度所有3个任务（受限于2核，调度2个）

---

## 测试场景3: 太阳能收集对比

**配置:**
- 初始能量: 0J
- 测试时间: 0:00 vs 12:00

### T=0:00 (无太阳能)
- 预期: 无能量，无法调度任何任务
- 所有任务deadline_miss

### T=12:00 (最大太阳能)
- 沈阳7月中午约800-900 W/m²
- PV效率18%, 面积1m²
- 预估收集功率: 800 × 0.18 ≈ 144W
- 100ms可收集: 144W × 0.1s = 14.4J
- 预期: 能量充足，正常调度

---

## 能量计算验证

### 理论计算
```
能量(J) = 功率(W) × 时间(s) × 能量系数
task_high = 0.0077W × 0.1s × 2.0 ≈ 0.00154J (基础) + 动态功率
```

### 实际消耗
从日志和追踪文件提取实际能量消耗数据，对比理论值。

---

## 关键发现

### EFPP vs EPP 核心差异

| 场景 | EPP行为 | EFPP行为 |
|------|---------|----------|
| 能量不足 | 立即停止，不调度任何任务 | 跳过高优先级，检查低优先级 |
| task_high能量不足 | 无任务调度 | 调度task_low（如果有能量） |
| 优先级反转 | 不发生 | **可能发生** |

### Bug修复记录
1. **重复能量检查**: `dispatch()`和`onBeginDispatchMulti()`都调用`getTaskN()`
   - 修复: 在能量检查前先检查`_task_prepaid_energy`
2. **Kernel不识别EPP/EFPP**: 没有跳过kernel的能量检查
   - 修复: 在mrtkernel.cpp中添加EPP/EFPP识别
3. **工厂注册冲突**: registerGPFPASAP重复命名
   - 修复: 改为registerEPPScheduler和registerEFPFPScheduler

---

## 下一步测试计划
1. ✅ 优先级反转场景
2. ✅ 能量充足场景
3. ✅ 太阳能收集场景（0点vs12点）- 见SOLAR_ENERGY_TEST_REPORT.md
4. ⏳ 长时间运行稳定性
5. ⏳ 能量恢复机制
6. ✅ 截止时间违例统计 - 已在下方测试对比中完成

---

## 实测对比：EPP vs EFPP (初始能量0.08J)

### 统计数据对比

| 指标 | EPP | EFPP | 差异 |
|------|-----|------|------|
| 总事件数 | 27 | 31 | +4 |
| scheduled事件 | 5 | 8 | +3 |
| end_instance事件 | 5 | 8 | +3 |
| dline_miss事件 | 5 | 3 | -2 |
| task_high完成数 | 5 | 5 | 相同 |
| task_mid完成数 | 0 | 0 | 相��� |
| task_low完成数 | 0 | 3 | **+3** |

### 关键发现

#### T=0时刻调度差异
**EPP (刚性):**
```
T=0ms: scheduled: task_high
T=100ms: end_instance: task_high
```
- 只调度了task_high
- task_low和task_mid全部miss deadline

**EFPP (弹性):**
```
T=0ms: scheduled: task_high
         scheduled: task_low
T=25ms: end_instance: task_low
T=100ms: end_instance: task_high
```
- 同时调度了task_high和task_low（2个CPU并行）
- **task_low成功完成3次实例！**
- task_mid仍然miss（能量不够）

### EFPP优势验证

1. **资源利用率提升**: 
   - EPP: 只用了1个CPU（task_high）
   - EFPP: 用了2个CPU（task_high + task_low）

2. **任务完成率提升**:
   - EPP: 5个任务完成（全是task_high）
   - EFPP: 8个任务完成（5个task_high + 3个task_low）

3. **截止时间违例减少**:
   - EPP: 5次deadline miss
   - EFPP: 3次deadline miss

**结论：EFPP的弹性优先级策略确实有效！在能量不足时，通过调度低优先级任务，提高了系统吞吐量和资源利用率。**

---
