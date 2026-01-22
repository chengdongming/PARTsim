# 能量扣除Bug报告

## Bug���述
**任务执行了2ms，但只扣除了1ms的能量**

## 发现时间
2026-01-23

## 严重级别
**高** - 能量计算错误，影响所有能量感知调度算法

## 问题详细

### 观察到的行为
```
初始能量: 1.5mJ
任务: 2个高能耗任务，每ms消耗0.6mJ

执行时间线:
t=0ms:  两个任务开始调度
t=1ms:  能量检查事件扣除 0.6mJ × 2 = 1.2mJ
         剩余能量: 1.5mJ - 1.2mJ = 0.3mJ
t=2ms:  Tick事件检测到能量不足，中断任务
         Trace显示: descheduled @ t=2ms

能量消耗: 1.2mJ
剩余能量: 0.3mJ
```

### 问题分析

**实际执行**: 2ms (t=0到t=2)
**能量扣除**: 只扣除1ms的能量 (1.2mJ)

**能量缺口**: t=1到t=2的1ms执行没有对应的能量扣除！

### 根本原因

能量检查事件和suspend()的时间不同步：

1. **t=1ms**: 能量检查事件触发
   - 扣除1.2mJ（对应t=0到t=1的执行）
   - 剩余0.3mJ

2. **t=1到t=2**: 任务继续执行
   - **但没有扣除能量** ✗

3. **t=2ms**: Tick事件触发
   - 检测到能量不足（0.3mJ < 1.2mJ）
   - 调用suspend()中断任务
   - t=2ms的能量检查事件发现isExecuting()=false，不扣除能量

### 事件优先级

```
能量检查事件: _DEFAULT_PRIORITY - 5  (更高优先级)
Tick事件:      _DEFAULT_PRIORITY - 10
```

在t=1ms：
1. 能量检查事件先执行（扣除1.2mJ）
2. Tick事件后执行（但没有检测到能量不足，继续调度）

在t=2ms：
1. 能量检查事件先执行（但任务已suspend，不扣除）
2. Tick事件后执行（检测到能量不足，中断任务）

### 正确的行为应该是

**方案1**: 能量检查事件在每次执行前扣除
- t=1ms能量检查: 扣除t=0到t=1的能量（1.2mJ） ✓
- t=2ms能量检查: 扣除t=1到t=2的能量（1.2mJ） ✓
- 总扣除: 2.4mJ

**方案2**: Tick事件在检测到能量不足时补扣能量
- t=1ms能量检查: 扣除t=0到t=1的能量（1.2mJ）
- t=2ms tick: 检测到t=1到t=2已执行但未扣除，补扣1.2mJ
- 总扣除: 2.4mJ

**方案3**: 改变事件优先级
- Tick事件先于能量检查事件执行
- t=1ms tick: 检测能量不足，中断任务
- 能量检查事件不触发（任务已中断）

## 影响范围

### 影响的调度器
- TIE (gpfp_tie_scheduler.cpp)
- BTIE (gpfp_btie_scheduler.cpp)
- TGF (gpfp_tgf_scheduler.cpp)

### 影响的功能
- 运行时能量管理
- 能量中断准确性
- 能量统计精度

## 修复计划

1. **分析能量扣除时机**
   - 确定能量应该何时扣除（执行前 vs 执行后）
   - 评估各种方案的优劣

2. **实施修复**
   - 选择最优方案
   - 修改能量检查事件和tick事件逻辑
   - 确保能量扣除和任务执行同步

3. **验证修复**
   - 测试1ms执行场景
   - 测试2ms执行场景
   - 测试能量不足中断
   - 确认能量计算准确

## 相关代码位置

- `librtsim/scheduler/gpfp_tie_scheduler.cpp:79-130` (TIEEnergyCheckEvent::doit)
- `librtsim/scheduler/gpfp_tie_scheduler.cpp:778-845` (checkAndInterruptRunningTasks)
- `librtsim/scheduler/gpfp_btie_scheduler.cpp:72-111` (BTIEEnergyCheckEvent::doit)
- `librtsim/scheduler/gpfp_tgf_scheduler.cpp:72-110` (TGFEnergyCheckEvent::doit)

## 临时解决方案

在修复之前，能量消耗数据**不完全准确**，但：
- 能量中断功能正常工作（在能量不足时会中断）
- 相对的能量消耗是正确的（任务执行更久=消耗更多）
- 绝对能量消耗有误差（可能少扣除1个时间单位的能量）

## 参考资料

- 测试配置: `energy_interrupt_test/`
- 测试结果: 所有trace文件显示剩余0.3mJ
- 日志证据: `能量检查事件触发` 和 `Tick事件触发` 的时间戳
