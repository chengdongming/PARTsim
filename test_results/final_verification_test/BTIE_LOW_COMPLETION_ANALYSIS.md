# BTIE完成率低的根本原因分析

## 🎯 问题现象

**测试结果对比：**
| 算法 | 完成率 | 失败率 |
|------|--------|--------|
| TIE | 97.7% | 4.0% |
| TGF | 97.7% | 4.0% |
| **BTIE** | **80.1%** | **17.6%** |

**BTIE的异常表现：**
- task_0（最低优先级）：只调度2次，19个miss，0%完成率
- task_3（次低优先级）：调度19次，12个miss，40.9%完成率
- 批量调度大小：95.3%的时候只调度1个任务

---

## 🔍 深度分析

### 1. 批量调度行为异常

**预期行为：** BTIE应该批量调度多个任务（批量大小 = min(就绪任务数, 空闲CPU数)）

**实际行为：**
- 批量大小=1: 141次（95.3%）
- 批量大小=2: 7次（4.7%）

**结论：** BTIE几乎总是只调度1个任务，完全失去了批量调度的优势！

---

### 2. 低优先级任务饥饿

**task_0的调度历史：**
- t=6ms: 首次调度 ✓
- t=16ms: 第二次调度 ✓
- t=56ms之后: **再也没有被调度** ✗

**task_3的调度历史：**
- t=10ms - t=438ms: 正常调度（19次）✓
- t=480ms之后: **再也没有被调度** ✗

**结论：** 低优先级任务在某个时间点后完全停止被调度

---

### 3. 高优先级任务正常

**task_1-task_5的表现：**
- 调度间隔非常规律（等于周期）
- 完成率96.7%-100%
- 没有异常

**结论：** 高优先级任务不受影响

---

## 🐛 根本原因

### 代码缺陷位置

**文件：** `librtsim/scheduler/gpfp_btie_scheduler.cpp`
**行号：** 664-667

```cpp
// ⭐ Bug #4修复：计算实际能调度的新任务数（考虑CPU限制）
// 实际可调度 = min(K - 运行中任务数, 空闲CPU数)
int actual_new_tasks_can_schedule = static_cast<int>(K) - static_cast<int>(running_count);
if (actual_new_tasks_can_schedule < 0) actual_new_tasks_can_schedule = 0;
if (actual_new_tasks_can_schedule > static_cast<int>(free_cpus)) {
    actual_new_tasks_can_schedule = static_cast<int>(free_cpus);
}
```

### 错误逻辑分析

**当前计算公式：**
```
actual_new_tasks_can_schedule = min(max(K - running_count, 0), free_cpus)
```

其中：
- `K` = `_ready_queue.size()`（就绪队列中的任务数）
- `running_count` = 正在运行的任务数
- `free_cpus` = 空闲CPU数

**问题：**
1. `_ready_queue`只包含就绪但**未运行**的任务
2. `running_task_list`包含**正在运行**的任务
3. 这两个集合是**互斥的**，不应该相减！

**错误示例：**
```
场景：4个CPU，3个任务正在运行，3个任务在就绪队列
- K = 3（就绪队列）
- running_count = 3（运行中）
- free_cpus = 1（空闲）

当前计算：
actual = min(max(3 - 3, 0), 1) = min(0, 1) = 0 ❌

结果：无法调度任何新任务！
```

---

## 📊 影响分析

### 场景模拟

| 场景 | 运行中 | 就绪队列 | 空闲CPU | 当前计算 | 正确计算 | 影响 |
|------|--------|----------|---------|----------|----------|------|
| 初始状态 | 0 | 4 | 4 | 4 | 4 | ✓ 正常 |
| 1个运行 | 1 | 3 | 3 | 2 | 3 | ⚠️ 少调度1个 |
| 2个运行 | 2 | 2 | 2 | 0 | 2 | ✗ 无法调度 |
| 3个运行 | 3 | 3 | 1 | 0 | 1 | ✗ 无法调度 |
| 满载 | 4 | 2 | 0 | 0 | 0 | ✓ 正常 |

**结论：** 当CPU利用率较高时（2个或更多任务运行），新任务无法被调度！

---

### 为什么低优先级任务饥饿？

1. **初期（t=0-50ms）：**
   - CPU利用率低，任务可以正常调度
   - task_0和task_3得到调度机会

2. **中期（t=50-500ms）：**
   - 高优先级任务（task_1-task_5）逐渐占满CPU
   - 经常有2-3个任务同时运行
   - `actual_new_tasks_can_schedule`计算错误，变成0
   - 低优先级任务无法进入批次

3. **后期（t=500-1000ms）：**
   - 低优先级任务持续饥饿
   - 累积大量deadline miss

---

### 为什么批量大小几乎总是1？

**原因：** 由于`actual_new_tasks_can_schedule`计算错误，大部分时候只能调度0-1个新任务。

**批量组成：**
```
批量 = 运行中任务（续期） + 新任务
```

当`actual_new_tasks_can_schedule = 0`时：
- 批量只包含运行中的任务（续期）
- 没有新任务加入
- 批量大小 = 运行中任务数

当`actual_new_tasks_can_schedule = 1`时：
- 批量 = 运行中任务 + 1个新任务
- 如果运行中任务较少，批量大小可能是1-2

---

## ✅ 修复方案

### 正确的计算公式

```cpp
// 正确计算：实际可调度的新任务数 = min(就绪队列大小, 空闲CPU数)
int actual_new_tasks_can_schedule = std::min(
    static_cast<int>(K),
    static_cast<int>(free_cpus)
);
```

**逻辑：**
- 就绪队列中有K个任务等待调度
- 但只有free_cpus个空闲CPU可用
- 所以最多只能调度min(K, free_cpus)个新任务

---

### 修改位置

**文件：** `librtsim/scheduler/gpfp_btie_scheduler.cpp`
**行号：** 664-667

**修改前：**
```cpp
int actual_new_tasks_can_schedule = static_cast<int>(K) - static_cast<int>(running_count);
if (actual_new_tasks_can_schedule < 0) actual_new_tasks_can_schedule = 0;
if (actual_new_tasks_can_schedule > static_cast<int>(free_cpus)) {
    actual_new_tasks_can_schedule = static_cast<int>(free_cpus);
}
```

**修改后：**
```cpp
// 正确计算：实际可调度的新任务数 = min(就绪队列大小, 空闲CPU数)
int actual_new_tasks_can_schedule = std::min(
    static_cast<int>(K),
    static_cast<int>(free_cpus)
);
```

---

## 📈 预期改善

修复后的预期效果：

1. **批量大小增加：**
   - 当前：95.3%的时候批量=1
   - 修复后：批量大小应该接近空闲CPU数（1-4）

2. **低优先级任务得到调度：**
   - task_0和task_3应该能持续得到调度机会
   - 完成率应该显著提升

3. **总体完成率提升：**
   - 当前：80.1%
   - 预期：接近TIE/TGF的97.7%

4. **批量调度优势体现：**
   - 真正实现多任务批量调度
   - 能量效率保持优势

---

## 🎯 总结

**BTIE完成率低的根本原因：**

1. ✗ **批量大小计算错误**
   - 错误地将就绪队列大小减去运行中任务数
   - 导致大部分时候无法调度新任务

2. ✗ **低优先级任务饥饿**
   - 当CPU利用率高时，新任务无法进入批次
   - 低优先级任务长期得不到调度

3. ✗ **批量调度失效**
   - 95.3%的时候只调度1个任务
   - 完全失去批量调度的优势

**修复方案：**
- 修改第664行的计算公式
- 使用`min(K, free_cpus)`代替`K - running_count`

**预期效果：**
- 完成率从80.1%提升到接近97.7%
- 批量调度真正发挥作用
- 低优先级任务不再饥饿
