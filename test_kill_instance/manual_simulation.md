# 手动仿真推演（独立于真实仿真）

## 测试场景
- **任务**: 单个周期任务 task_1
- **周期**: 10ms
- **WCET**: 15ms
- **初始能量**: 10,000 mJ
- **CPU数量**: 1
- **调度器**: TIE
- **功率**: 1W = 1 mJ/ms

## 手动推演（逐ms分析）

### 0ms - 第一个实例到达
```
状态:
- 时间: 0ms
- 就绪队列: [task_1(实例0, 到达时间=0ms)]
- 运行中: []
- 能量: 10,000 mJ

事件:
1. 实例0到达（arrival）
2. 调度器调度实例0到CPU0
3. 扣除初始能量: 0.42 mJ（从trace得知）
4. 能量剩余: 9999.58 mJ

预测:
✅ arrival事件 @ 0ms
✅ scheduled事件 @ 0ms
✅ 能量: 9999.58 mJ
```

### 1ms - 9ms - 实例0执行中
```
每ms扣除续期能量:
- 功率: 1W = 1 mJ/ms
- 但从trace看，实���扣除的是0.42 mJ/tick

计算:
- 初始: 9999.58 mJ
- 9ms × 0.42 mJ/ms = 3.78 mJ
- 剩余: 9995.8 mJ

预测:
✅ 10ms时能量应为: 9995.8 mJ
```

### 10ms - 关键时间点！新实例1到达
```
状态:
- 时间: 10ms
- 就绪队列: [task_1(实例0)]（还在运行）
- 运行中: [task_1(实例0) @ CPU0]
- 能量: 9995.8 mJ

事件序列:
1. 实例1到达（arrival @ 10ms）
2. 检测到实例0仍在运行（isActive = true）
3. 触发 deadEvt.process()
4. 由于 _kill=true，调用 killInstance()
5. deschedule() - 从CPU移除实例0
6. killEvt -> onKill() - 状态设为IDLE
7. 实例1进入缓冲队列（buffArrival）
8. fakeArrEvt处理缓冲队列
9. handleArrival(10ms) - 实例1从10ms开始
10. 调度器调度实例1

关键数据:
- 实例0执行时间: 10ms（0-10ms）
- 实例0未完成WCET（15ms）
- 能量消耗: 10ms × 0.42 mJ/ms = 4.2 mJ

预测:
✅ arrival事件（实例1）@ 10ms
✅ dline_miss事件（实例0）@ 10ms
✅ descheduled事件（实例0）@ 10ms, executed_time_ms=10
✅ kill事件（实例0）@ 10ms
✅ 能量: 9995.8 mJ
```

### 11ms - 19ms - 实例1执行中
```
状态:
- 时间: 11-19ms
- 运行中: [task_1(实例1) @ CPU0]
- 实例1到达时间: 10ms

每ms扣除续期能量: 0.42 mJ/ms
```

### 20ms - 实例2到达
```
状态:
- 时间: 20ms
- 运行中: [task_1(实例1) @ CPU0]（已执行10ms）

事件序列:
1. 实例2到达（arrival @ 20ms）
2. 检测到实例1仍在运行
3. killInstance() - 杀死实例1
4. 实例1执行时间: 10ms（10-20ms）
5. 实例2开始执行

预测:
✅ arrival事件（实例2）@ 20ms
✅ dline_miss事件（实例1）@ 20ms
✅ kill事件（实例1）@ 20ms
```

### 30ms - 实例3到达
```
预测:
✅ arrival事件（实例3）@ 30ms
✅ dline_miss事件（实例2）@ 30ms
✅ kill事件（实例2）@ 30ms
```

### 40ms - 实例4到达
```
预测:
✅ arrival事件（实例4）@ 40ms
✅ dline_miss事件（实例3）@ 40ms
✅ kill事件（实例3）@ 40ms
```

### 50ms - 实例5到达
```
预测:
✅ arrival事件（实例5）@ 50ms
✅ dline_miss事件（实例4）@ 50ms
✅ kill事件（实例4）@ 50ms
```

---

## 手动仿真总结（0-50ms）

| 时间 | 事件类型 | 实例编号 | 到达时间 | 执行时间 |
|------|---------|---------|---------|---------|
| 0ms  | arrival | 0 | 0ms | - |
| 0ms  | scheduled | 0 | 0ms | - |
| 10ms | arrival | 1 | 10ms | - |
| 10ms | dline_miss | 0 | 0ms | 10ms |
| 10ms | descheduled | 0 | 0ms | 10ms |
| 10ms | kill | 0 | 0ms | - |
| 20ms | arrival | 2 | 20ms | - |
| 20ms | dline_miss | 1 | 10ms | 10ms |
| 20ms | kill | 1 | 10ms | - |
| 30ms | arrival | 3 | 30ms | - |
| 30ms | dline_miss | 2 | 20ms | 10ms |
| 30ms | kill | 2 | 20ms | - |
| 40ms | arrival | 4 | 40ms | - |
| 40ms | dline_miss | 3 | 30ms | 10ms |
| 40ms | kill | 3 | 30ms | - |
| 50ms | arrival | 5 | 50ms | - |
| 50ms | dline_miss | 4 | 40ms | 10ms |
| 50ms | kill | 4 | 40ms | - |

### 能量计算
- 初始: 10,000 mJ
- 0ms扣除: 0.42 mJ
- 0-10ms续期: 9 × 0.42 = 3.78 mJ
- 总消耗: 0.42 + 3.78 = 4.2 mJ
- 剩余: 9995.8 mJ

### 关键预测
1. ✅ 每个实例执行10ms（而不是15ms）
2. ✅ 每10ms发生一次kill事件
3. ✅ 能量剩余9995.8 mJ
4. ✅ 总共10个arrival，9个kill
