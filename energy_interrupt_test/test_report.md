# 能量中断测试报告

## 测试目标
验证TIE/BTIE/TGF三种调度算法在运行时能量不足时的主动中断功能

## 测试场景
- **任务数量**: 2个高能耗任务
- **任务WCET**: 10ms
- **初始能量**: 0.0015J (1.5mJ)
- **能耗**: 每任务每ms消耗0.6mJ
- **预期**: 2个任务并行运行1ms后能量耗尽（消耗1.2mJ），剩余0.3mJ

## 测试配置

### 能量配置
```yaml
initial_energy: 0.0015J    # 1.5 mJ
max_energy: 1000.0J        # 1000 J
enable_energy_recovery: false  # 禁用能量恢复
```

### 任务配置
```yaml
任务1: task_high_energy_1
  - 周期: 20ms
  - WCET: 10ms
  - 工作负载: bzip2
  - 能耗: 0.6mJ/ms

任务2: task_high_energy_2
  - 周期: 20ms
  - WCET: 10ms
  - 工作负载: bzip2
  - 能耗: 0.6mJ/ms
```

## 测试结果

### TIE 调度器
- ✅ 任务执行1ms后能量不足，正确中断
- ✅ 剩余能量: 0.3mJ
- ✅ 能量消耗: 1.2mJ (2任务 × 0.6mJ × 1ms)
- 📄 [Trace文件](trace_tie_final.json)

### BTIE 调度器
- ✅ 任务执行1ms后能量不足，正确中断
- ✅ 剩余能量: 0.3mJ
- ✅ 能量消耗: 1.2mJ
- 📄 [Trace文件](trace_btie_final.json)

### TGF 调度器
- ✅ 任务执行1ms后能量不足，正确中断
- ✅ 剩余能量: 0.3mJ
- ✅ 能量消耗: 1.2mJ
- 📄 [Trace文件](trace_tgf_final.json)

## 能量平衡验证

| 调度器 | 初始能量 | 消耗能量 | 剩余能量 | 状态 |
|--------|----------|----------|----------|------|
| TIE    | 1.5 mJ   | 1.2 mJ   | 0.3 mJ   | ✅ 正确 |
| BTIE   | 1.5 mJ   | 1.2 mJ   | 0.3 mJ   | ✅ 正确 |
| TGF    | 1.5 mJ   | 1.2 mJ   | 0.3 mJ   | ✅ 正确 |

**计算验证**: 2任务 × 1ms × 0.6mJ/ms = 1.2mJ, 1.5mJ - 1.2mJ = 0.3mJ ✓

## 关键事件时间线 (TIE为例)

```
t=0ms:  task_high_energy_1 到达并调度
t=0ms:  task_high_energy_2 到达并调度
t=1ms:  能量检查事件触发，扣除0.6mJ × 2 = 1.2mJ
t=1ms:  剩余能量0.3mJ，不足下次调度
t=2ms:  Tick事件检测到能量不足
t=2ms:  中断两个任务
t=2ms:  取消能量检查事件
```

## 测试文件

### 用到的配置文件
- [system_tie.yml](system_tie.yml) - TIE系统配置
- [system_btie.yml](system_btie.yml) - BTIE系统配置
- [system_tgf.yml](system_tgf.yml) - TGF系统配置
- [tasks.yml](tasks.yml) - 任务集配置

### 生成的追踪文件
- [trace_tie_final.json](trace_tie_final.json) - TIE调度追踪
- [trace_btie_final.json](trace_btie_final.json) - BTIE调度追踪
- [trace_tgf_final.json](trace_tgf_final.json) - TGF调度追踪

## 结论

✅ **所有三种调度算法均正确实现运行时能量中断功能**

1. 能量检查事件每1ms触发，正确扣除能量
2. Tick事件检测到能量不足后立即中断任务
3. 中断后取消能量检查事件，避免继续扣除能量
4. 所有调度器行为一致，能量计算准确
5. 不影响调度算法的核心逻辑（抢占、任务选择等）

## 修复前后对比

### 修复前
- TIE/BTIE: 能量透支至负值（-2.3J）
- TGF: 正确中断（0.3mJ剩余）

### 修复后
- TIE/BTIE/TGF: 全部正确中断（0.3mJ剩余）
- 行为完全一致
- 无能量透支

---
测试日期: 2026-01-23
调度器版本: commit 87d483d
