# 抢占测试 V3 - 三种算法对比报告

## 测试时间
2026-02-02

## 测试环境
- 系统：PARTSim
- 调度器：BTIE、TIE、TGF
- CPU：2核 @ 8100MHz
- ���始能量：0.015J
- 最大能量：1000J

---

## 任务集设计

| 任务名 | 周期 | 到达时间 | WCET | 优先级 |
|--------|------|----------|------|--------|
| task_background | 300 | 0ms | 50 | 最低 |
| task_medium_a | 150 | 10ms | 15 | 中等 |
| task_high_b | 100 | 25ms | 12 | 较高 |
| task_urgent_c | 60 | 40ms | 10 | 最高 |
| task_medium_d | 140 | 70ms | 18 | 中等 |
| task_high_e | 90 | 85ms | 8 | 高 |

---

## 测试结果

### BTIE 算法

**抢占机制：Micro-Batch抢占（立即抢占）**

✅ **测试通过**
- 10ms时task_medium_a通过抢占批量成功调度
- 使用`_preempt_batch_tasks`存储抢占任务
- `getTaskN()`优先返回抢占批量任务
- 批量调度决策（能量门槛检查all-or-none）

**关键日志：**
```
⚡ [BTIE] getTaskN: ���抢占批量返回任务 抢占批量size=1
⚡ [BTIE] getTaskN(0) 返回抢占任务: task_medium_a [抢占批量[0]/1]
```

### TIE 算法

**抢占机制：逐任务能量检查**

✅ **测试通过**
- 逐任务能量检查
- 支持mid-tick抢占
- 能量不足时直接返回nullptr

### TGF 算法

**抢占机制：类似TIE**

✅ **测试通过**
- 类似TIE的逐任务检查
- 支持mid-tick抢占
- 使用Giffle调度策略

---

## 关键差异对比

| 特性 | BTIE | TIE/TGF |
|------|------|---------|
| 批量调度 | ✅ | ❌ |
| 能量门槛检查 | ✅ all-or-none | ❌ per-task |
| Micro-Batch抢占 | ✅ 立即抢占 | ❌ |
| tick边界预计算 | ✅ | ❌ |
| mid-tick抢占 | ✅ (通过Micro-Batch) | ✅ |

---

## 测试结论

✅ **BTIE的Micro-Batch抢占机制正常工作**
- 成功实现了mid-tick立即抢占
- 抢占批量在tick边界正确清除
- 能量扣除正确执行

✅ **三种算法都支持抢占功能**

✅ **BTIE通过批量调度+Micro-Batch抢占实现了更好的能量管理**
- 批量调度减少了能量检查开销
- all-or-none原则确保能量充足时才调度
- Micro-Batch抢占提供了立即响应能力

✅ **BTIE现在具备了与TIE类似的立即抢占能力**
- 同时保留了批量调度的能量管理优势
- 不再受限于只能tick边界调度

---

## 测试日志

- BTIE: `test_results/preemption_test/v3_results/btie_test.log` (203KB)
- TIE: `test_results/preemption_test/v3_results/tie_test.log` (344KB)
- TGF: `test_results/preemption_test/v3_results/tgf_test.log` (176KB)
