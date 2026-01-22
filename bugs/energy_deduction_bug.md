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

## 修复记录

### Commit: e78e1e5 (2026-01-23) - 阶段1：修复事件执行顺序

**修复方案**: 改变事件优先级

1. **修改事件优先级**
   ```cpp
   // 修复前
   Tick事件:      _DEFAULT_PRIORITY - 10
   能量检查事件:  _DEFAULT_PRIORITY - 5  (更高优先级)

   // 修复后
   Tick事件:      _DEFAULT_PRIORITY - 5   (更高优先级)
   能量检查事件:  _DEFAULT_PRIORITY - 10
   ```

2. **禁用能量检查事件的能量扣除**
   - 避免重复扣除
   - 能量检查事件现在仅用于记录

**修复效果**:
- ✅ 任务在能量不足时立即中断（t=1ms）
- ✅ 时序问题修复：tick先检查能量再调度
- ✅ 不再继续执行能量不足的任务

### Commit: 4e662b2 (2026-01-23) - 阶段2：完整修复能量扣除机制

**修复内容**:

1. **在tick事件中添加能量扣除逻辑**
   ```cpp
   // 在checkAndInterruptRunningTasks()开始时执行
   double total_energy_to_deduct = 0.0;
   for (auto &map_pair : running_tasks) {
       double unit_energy = calculateUnitEnergyForTask(task);
       total_energy_to_deduct += unit_energy;
   }
   _current_energy -= total_energy_to_deduct;
   _stats.total_energy_consumed += total_energy_to_deduct;
   ```

2. **确保时序正确**
   - Tick事件触发 → 先扣除上一ms执行能量
   - 再检查剩余能量是否足够继续
   - 如果不足，立即中断任务

**完整修复效果**:
- ✅ 能量扣除时序完全正确
- ✅ 能量统计准确：total_energy_consumed正确统计
- ✅ 任务在能量不足时立即中断（t=1ms）
- ✅ 所有三个调度器（TIE/BTIE/TGF）行为一致
- ✅ 不再有能量透支问题

**测试验证**:
```
初始能量: 1.5mJ
任务: 2个高能耗任务，每ms消耗0.6mJ

t=1ms: Tick事件扣除1.2mJ → 剩余0.3mJ
t=1ms: 检测能量不足 → 立即中断

结果:
- 扣除能量: 1.2mJ ✓
- 总消耗能量: 1.2mJ ✓
- 剩余能量: 0.3mJ ✓
- 任务执行时间: 1ms ✓
```

所有三个调度器测试通过！

### Commit: 8569788 (2026-01-23) - 阶段3：修正事件优先级顺序

**发现的问题**:
阶段1的修复虽然改变了事件优先级，但优先级值设置错误。在MetaSim事件系统中，**数值越小=优先级越高**。

**错误配置**（阶段1）:
```cpp
Tick事件:      _DEFAULT_PRIORITY - 5   // 数值较大，优先级较低
能量检查事件:  _DEFAULT_PRIORITY - 10  // 数值较小，优先级较高
```
这导致能量检查事件仍在tick事件之前执行！

**正确配置**（阶段3）:
```cpp
// MetaSim: 数值越小 = 优先级越高
Tick事件:      _DEFAULT_PRIORITY - 10  // 数值最小，最高优先级，先执行
能量检查事件:  _DEFAULT_PRIORITY - 5   // 数值较大，较低优先级，后执行
```

**最终修复效果**:
- ✅ Tick事件现在确实在能量检查事件之前执行
- ✅ 日志显示: `Tick事件触发 @ 1ms` → `能量检查事件触发: 时间=1ms`
- ✅ 能量扣除和检查时序完全正确
- ✅ 所有三个调度器行为一致

**测试验证**（使用bzip2工作负载，实际功耗计算）:
```
初始能量: 1.5mJ
任务: 2个bzip2任务，每ms消耗0.558mJ（考虑频率系数0.93）

t=1ms: Tick事件扣除 0.558mJ × 2 = 1.116mJ
t=1ms: 检测能量不足（剩余0.384mJ < 1.116mJ）→ 立即中断

结果:
- 扣除能量: 1.116mJ ✓
- 总消耗能量: 1.116mJ ✓
- 剩余能量: 0.384mJ ✓
- 任务执行时间: 1ms ✓（不再是2ms）
```

所有三个调度器（TIE/BTIE/TGF）测试通过！

## 后续工作

1. ✅ ~~完善能量扣除机制~~ (已完成)
2. ✅ ~~确保能量统计准确~~ (已完成)
3. ✅ ~~测试各种场景的能量计算~~ (已完成)
4. 回归测试
   - 验证抢占功能正常
   - 验证正常调度场景（能量充足情况）
   - 验证多核场景

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
