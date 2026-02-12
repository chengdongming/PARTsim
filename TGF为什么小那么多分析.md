# TGF Trace文件为什么比TIE小那么多？

## 问题

在50秒的仿真中，三种调度器的trace文件大小差异显著：
- **TGF**: 89 MB, 596,232 行
- **TIE**: 147 MB, 1,259,519 行（是TGF的2.1倍）
- **BTIE**: 213 MB, 1,352,897 行（是TGF的2.3倍）

## 根本原因：DEBUG日志数量差异

### DEBUG日志总数对比

| 调度器 | DEBUG日志总数 | 相对TGF |
|--------|--------------|---------|
| TGF    | 199,337      | 1.0x    |
| TIE    | 774,364      | **3.9x** |
| BTIE   | (未统计)     | -       |

### 关键差异：getTaskN()的DEBUG输出

| 调度器 | getTaskN DEBUG日志 | 相对TGF |
|--------|-------------------|---------|
| TGF    | 17,455            | 1.0x    |
| TIE    | 188,587           | **10.8x** |

**TIE的getTaskN DEBUG日志是TGF的10.8倍！**

## 代码层面的差异

### TIE的getTaskN()有6个std::cout输出

```cpp
// 1. 输出ready_queue大小
std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - ready_queue.size()="
          << _ready_queue.size() << std::endl;

// 2. 循环输出ready_queue中的每个任务
for (size_t i = 0; i < _ready_queue.size(); ++i) {
    std::cout << "[DEBUG]   ready_queue[" << i << "]="
              << getTaskName(_ready_queue[i]) << std::endl;
}

// 3. 输出开始遍历的消息
std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 开始遍历ready_queue, 查找第"
          << n << "个未调度任务" << std::endl;

// 4. 在循环中输出每个任务的详细信息
std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - i=" << i << " task="
          << getTaskName(task) << " ready_index=" << ready_index
          << " is_running=" << is_running << " already_counted="
          << already_counted << std::endl;

// 5. 输出准备调度的任务信息
std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 准备调度第" << ready_index
          << "个任务: " << getTaskName(task) << " 需要1ms="
          << unit_energy * 1000 << " mJ 当前能量="
          << _current_energy * 1000 << " mJ" << std::endl;

// 6. 输出能量不足的消息
std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 能量不足，返回nullptr"
          << std::endl;
```

### TGF的getTaskN()只有1个std::cout输出

```cpp
// 只输出ready_queue大小
std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - ready_queue.size()="
          << _ready_queue.size() << std::endl;
```

## 为什么TIE的DEBUG日志这么多？

### 1. 循环输出ready_queue内容

TIE在每次getTaskN()调用时都会循环输出ready_queue中的所有任务：

```cpp
for (size_t i = 0; i < _ready_queue.size(); ++i) {
    std::cout << "[DEBUG]   ready_queue[" << i << "]="
              << getTaskName(_ready_queue[i]) << std::endl;
}
```

如果ready_queue有4个任务，每次getTaskN()就会产生4条DEBUG日志。

### 2. 循环中输��每个任务的详细信息

TIE在遍历ready_queue时，对每个任务都输出详细信息：

```cpp
for (size_t i = 0; i < _ready_queue.size(); ++i) {
    std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - i=" << i
              << " task=" << getTaskName(task) << " ..." << std::endl;
}
```

### 3. getTaskN()被频繁调用

在50秒（50,000个tick）的仿真中：
- 每个tick可能调用多次getTaskN()（n=0, 1, 2, 3...）
- 每次调用都会产生多条DEBUG日志

**估算**：
- 50,000 ticks × 平均4次getTaskN调用 × 平均5条DEBUG日志 = 1,000,000条日志
- 实际：188,587条getTaskN DEBUG日志（因为很多时候ready_queue为空）

## 其他DEBUG日志来源

除了getTaskN()，还有其他来源的DEBUG日志：

### MRTKernel的DEBUG日志

```
[DEBUG] MRTKernel::dispatch() - CALLED! _dispatch_start_time=2
[DEBUG] MRTKernel::dispatch() - 计算num_newtasks, ncpu=4
[DEBUG]   getTaskN(0)=task_0 getProcessor=nullptr _m_dispatched=nullptr is_new=1
[DEBUG] MRTKernel::dispatch() - 循环结束, num_newtasks=0
```

这些日志在TIE和TGF中都存在，但TIE因为getTaskN()调用更频繁，所以这些日志也更多。

### Task相关的DEBUG日志

```
[DEBUG] Task::onArrival() - 开始: task_0 当前时间: 2ms isActive: 0
[DEBUG] Task::handleArrival() - 开始: task_0 到达时间: 2ms
[DEBUG] Scheduler::insert() - 开始: task_0 当前时间: 2ms
```

这些日志在三种调度器中应该是相同的，因为它们来自Task和Scheduler基类。

## 性能影响

### 日志输出开销

大量的DEBUG日志会影响仿真性能：

| 调度器 | 仿真时间 | 日志量 | 性能 |
|--------|---------|--------|------|
| TGF    | 3分39秒  | 596K行 | 最快 |
| TIE    | 3分45秒  | 1.26M行 | 中等 |
| BTIE   | 7分46秒  | 1.35M行 | 最慢 |

**注意**：BTIE最慢不是因为日志，而是因为批量调度的计算复杂度更高。

### 磁盘空间占用

- TGF: 89 MB
- TIE: 147 MB（多占用58 MB）
- BTIE: 213 MB（多占用124 MB）

## 建议

### 1. 生产环境应该禁用DEBUG日志

在生产环境中，应该：
- 只保留ERROR和WARNING级别的日志
- 禁用所有DEBUG和INFO日志
- 或者使用条件编译来完全移除DEBUG代码

### 2. 清理TIE的冗余DEBUG输出

TIE的getTaskN()中有很多冗余的DEBUG输出，建议：
- 移除循环输出ready_queue内容的代码
- 移除循环中输出每个任务详细信息的代码
- 只保留关键决策点的日志

### 3. 使用日志级别控制

可以添加一个日志级别配置：
```cpp
enum LogLevel { ERROR, WARNING, INFO, DEBUG, TRACE };
LogLevel current_level = INFO;  // 默认只输出INFO及以上

#define LOG_DEBUG(msg) if (current_level >= DEBUG) { std::cout << msg; }
```

## 结论

**TGF的trace文件小是因为它的DEBUG日志少，特别是getTaskN()中的DEBUG输出比TIE少得多。**

具体来说：
1. TGF的getTaskN()只有1个cout，TIE有6个
2. TIE在循环中输出每个任务的详细信息，导致日志量成倍增加
3. TIE的getTaskN DEBUG日志是TGF的10.8倍（188,587 vs 17,455）
4. 总体DEBUG日志TIE是TGF的3.9倍（774,364 vs 199,337）

这不是算法性能的差异，而是**调试日志详细程度的差异**。在实际应用中，应该禁用或减少DEBUG日志以提高性能。

---

**分析日期**: 2026-02-12
**仿真时长**: 50,000ms
**数据来源**: /tmp/tie_output.txt, /tmp/tgf_output.txt
