# 约束截止时间测试 (Constrained Deadline Test)

## 测试目的

验证三个调度算法（TIE、TGF、BTIE）在约束截止时间场景下的行为，即截止时间小于周期（D < T）的情况。

## 任务集配置

| 任务 | 周期 (T) | WCET | 截止时间 (D) | D/T 比例 | 工作负载 |
|------|---------|------|-------------|---------|---------|
| task_1 | 50ms | 10ms | 40ms | 0.8 | bzip2 |
| task_2 | 100ms | 20ms | 80ms | 0.8 | hash |
| task_3 | 150ms | 25ms | 120ms | 0.8 | encrypt |

**特点**：
- 所有任务的截止时间都是周期的 80% (D = 0.8T)
- 这是典型的约束截止时间场景
- 任务必须在截止时间前完成，否则会错过截止期

## 系统配置

- **CPU数量**: 2核
- **初始能量**: 5.0 J
- **太阳能**: 启用（中午12点，有太阳能充电）
- **仿真时长**: 200ms

## 测试结果

### 生成的文件

```
test_results/constrained_deadline_test/
├── configs/
│   ├── system_2core_gpfp_tie.yml    # TIE 配置
│   ├── system_2core_gpfp_tgf.yml    # TGF 配置
│   └── system_2core_gpfp_btie.yml   # BTIE 配置
├── tasks/
│   └── tasks.yml                     # 任务集定义
├── taskset.yml                       # 可视化用任务集配置
└── traces/
    ├── trace_tie.json                # TIE 追踪文件
    ├── trace_tie.png                 # TIE 甘特图
    ├── trace_tgf.json                # TGF 追踪文件
    ├── trace_tgf.png                 # TGF 甘特图
    ├── trace_btie.json               # BTIE 追踪文件
    └── trace_btie.png                # BTIE 甘特图
```

### 可视化图表

所有追踪文件都已生成对应的甘特图，图表中包含：
- 任务执行时间线
- **到达时间箭头**（向下箭头）
- **截止时间箭头**（向上箭头）
- 任务统计信息

## 验证要点

1. **截止时间约束**：检查所有任务是否在截止时间前完成
2. **调度行为差异**：对比三个算法在约束截止时间下的调度策略
3. **能量管理**：验证能量充足情况下的调度效率

## 运行测试

```bash
# TIE
./build/rtsim/rtsim test_results/constrained_deadline_test/configs/system_2core_gpfp_tie.yml \
    test_results/constrained_deadline_test/tasks/tasks.yml 200 \
    -t test_results/constrained_deadline_test/traces/trace_tie.json

# TGF
./build/rtsim/rtsim test_results/constrained_deadline_test/configs/system_2core_gpfp_tgf.yml \
    test_results/constrained_deadline_test/tasks/tasks.yml 200 \
    -t test_results/constrained_deadline_test/traces/trace_tgf.json

# BTIE
./build/rtsim/rtsim test_results/constrained_deadline_test/configs/system_2core_gpfp_btie.yml \
    test_results/constrained_deadline_test/tasks/tasks.yml 200 \
    -t test_results/constrained_deadline_test/traces/trace_btie.json
```

## 可视化

```bash
# 可视化所有追踪文件
for trace in test_results/constrained_deadline_test/traces/*.json; do
    output="${trace%.json}.png"
    python3 trace_visualizer.py "$trace" -o "$output" \
        --taskset test_results/constrained_deadline_test/taskset.yml
done
```

## 测试日期

2026-02-07
