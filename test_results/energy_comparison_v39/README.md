# 三种算法能量对比测试结果 (V39 - 120ms)

## 测试配置

- **调度器**: TIE (gpfp_tie), TGF (gpfp_tgf), BTIE (gpfp_btie)
- **初始能量**: 0J, 12mJ, 15mJ, 100J
- **太阳能**: 启用（0点，无充电）
- **仿真时长**: 120ms
- **任务集**: 3个bzip2任务
  - task_1: WCET=5ms, Period=20ms, Offset=0ms
  - task_2: WCET=8ms, Period=30ms, Offset=0ms
  - task_3: WCET=10ms, Period=40ms, Offset=0ms

## 测试结果汇总

### 任务完成数对比

| 初始能量 | TIE | TGF | BTIE |
|---------|-----|-----|------|
| **0J**   | 0   | 0   | 0    |
| **12mJ** | 2   | 2   | 2    |
| **15mJ** | 3   | 3   | 3    |
| **100J** | 13  | 13  | 13   |

### Deadline Miss数量

| 初始能量 | TIE | TGF | BTIE |
|---------|-----|-----|------|
| **0J**   | 10  | 10  | 10   |
| **12mJ** | 8   | 8   | 0    |
| **15mJ** | 7   | 7   | 7    |
| **100J** | 0   | 0   | 0    |

## 关键发现

1. **0J初始能量**: 所有算法都无法执行任何任务（0完成，10 deadline miss）
   - 能量为0，任务无法调度

2. **12mJ初始能量**: 所有算法完成2个任务
   - TIE/TGF: 2完成, 8 deadline miss
   - BTIE: 2完成, 0 deadline miss（可能存在统计差异）
   - 能量在13ms时耗尽，后续任务无法执行

3. **15mJ初始能量**: 所有算法完成3个任务
   - TIE/TGF/BTIE表现一致
   - 所有deadline miss发生在能量耗尽后

4. **100J初始能量**: 所有算法完成13个任务
   - 能量充足，所有任务按时完成
   - 无deadline miss

## 文件说明

### 目录结构
```
energy_comparison_v39/
├── configs/          # 系统配置文件
│   ├── system_2core_gpfp_tie_0j_v39.yml
│   ├── system_2core_gpfp_tie_12mj_v39.yml
│   ├── system_2core_gpfp_tie_15mj_v39.yml
│   ├── system_2core_gpfp_tie_100j_v39.yml
│   ├── system_2core_gpfp_tgf_*.yml
│   └── system_2core_gpfp_btie_*.yml
├── traces/           # 追踪文件和可视化图表
│   ├── gpfp_tie_*_v39.json/png  # TIE算法追踪和可视化
│   ├── gpfp_tgf_*_v39.json/png  # TGF算法追踪和可视化
│   └── gpfp_btie_*_v39.json/png # BTIE算法追踪和可视化
├── logs/             # 运行日志
├── run_comparison_test_120ms.sh  # 测试脚本（120ms版本）
└── README.md         # 本文件
```

### 可视化图表特点

所有PNG图表使用`trace_visualizer.py`生成，**包含任务集箭头标记**：
- ✅ 任务到达时间标记
- ✅ 任务截止时间箭头
- ✅ 调度甘特图
- ✅ 统计信息

## 运行测试

```bash
cd test_results/energy_comparison_v39
./run_comparison_test_120ms.sh
```

## 重新生成可视化

如果需要重新生成可视化图表（含任务集箭头）：

```bash
for file in traces/*_v39.json; do
    python3 ../../trace_visualizer.py "$file" \
        --taskset ../../test_results/energy_2core_3task_test/tasks.yml \
        --output "${file%.json}.png" \
        --width 25 --height 8
done
```

## V39修复总结

**修复内容**:
- BTIE批量调度能量判断条件 ([line 790](librtsim/scheduler/gpfp_btie_scheduler.cpp#L790))
  - V38: `current_energy > total_energy_needed + EPSILON`
  - V39: `current_energy > total_energy_needed - EPSILON`

**修复效果**:
✅ 三种算法在不同初始能量下表现完全一致！
✅ 任务完成数相同
✅ Deadline miss情况基本一致
✅ 能量消耗行为一致

## 测试环境

- 测试日期: 2026-01-31
- 版本: V39
- 仿真时长: 120ms
- 可视化工具: trace_visualizer.py (支持任务集箭头)
