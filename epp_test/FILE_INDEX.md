# EPP调度器测试 - 文件索引

## 📂 测试文件夹结构

```
epp_test/
├── config_epp_0am.yml          # 午夜0点配置（无太阳能）
├── config_epp_8am.yml          # 上午8点配置（中等太阳能）⭐
├── config_epp_12pm.yml         # 中午12点配置（最大太阳能）⭐
├── tasks_epp.yml               # 任务集配置
├── ENERGY_DATA.md              # 能量数据详细说明
├── README.md                   # 测试说明和使用指南
├── QUICK_REFERENCE.md          # 快速参考卡片
└── FILE_INDEX.md               # 本文件
```

## 📄 文件说明

### 配置文件

#### [config_epp_0am.yml](config_epp_0am.yml)
**时间**: 午夜0点（00:00:00）
**太阳能**: 0 W/m²（无太阳能）
**用途**: 测试纯能量约束，无能量恢复

```yaml
time_of_day_ms: 0              # 午夜
initial_energy: 5.0J           # 初始能量
solar_collection: 0 J/ms       # 无太阳能
```

#### [config_epp_8am.yml](config_epp_8am.yml) ⭐ 推荐
**时间**: 上午8点（08:00:00）
**太阳能**: 541.2 W/m²（中等辐照度）
**用途**: 推荐测试配置，平衡能量收集和调度

```yaml
time_of_day_ms: 28800000       # 上午8点
initial_energy: 5.0J           # 初始能量
solar_collection: 0.097 J/ms   # 97.4 J/s
```

#### [config_epp_12pm.yml](config_epp_12pm.yml) ⭐ 推荐
**时间**: 中午12点（12:00:00）
**太阳能**: 850.5 W/m²（最大辐照度）
**用途**: 测试最大能量收集场景

```yaml
time_of_day_ms: 43200000       # 中午12点
initial_energy: 5.0J           # 初始能量
solar_collection: 0.153 J/ms   # 153.1 J/s
```

### 任务文件

#### [tasks_epp.yml](tasks_epp.yml)
**任务集**: 4个周期任务
**特点**: 不需要 `suspend(0)` - EPP是真正的抢占式调度器

```yaml
task_high:    周期500ms, WCET=250ms, 能耗=0.50J
task_mid:     周期1000ms, WCET=400ms, 能耗=0.80J
task_low:     周期2000ms, WCET=600ms, 能耗=1.20J
task_background: 周期3000ms, WCET=800ms, 能耗=1.44J

总能耗: 3.94J
```

### 参考文档

#### [ENERGY_DATA.md](ENERGY_DATA.md)
**内容**: 详细的能量计算和数据分析
**包含**:
- 功率模型
- 任务能耗表
- 太阳能收集速率
- 三个时间点的模拟结果
- 能量恢复时间计算
- 抢占场景能耗分析

#### [README.md](README.md)
**内容**: 测试说明和使用指南
**包含**:
- 快速开始
- 运行命令
- 测试场景
- 预期输出
- 故障排除

#### [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
**内容**: 快速参考卡片
**包含**:
- 核心数据表
- 三个时间点模拟结果
- 快速运行命令
- 关键发现

## 🚀 快速开始

### 1. 编译
```bash
cd /home/devcontainers/PARTSim-project
mkdir -p build && cd build
cmake ..
make -j$(nproc)
```

### 2. 运行测试
```bash
# 推荐：上午8点测试
./build/rtsim-exe -c epp_test/config_epp_8am.yml -t epp_test/tasks_epp.yml

# 推荐：中午12点测试
./build/rtsim-exe -c epp_test/config_epp_12pm.yml -t epp_test/tasks_epp.yml

# 午夜测试（无太阳能）
./build/rtsim-exe -c epp_test/config_epp_0am.yml -t epp_test/tasks_epp.yml
```

## 📊 核心数据

### 任务能耗（8.1 GHz）
| 任务 | WCET | 能耗 |
|------|------|------|
| task_high | 250ms | 0.50J |
| task_mid | 400ms | 0.80J |
| task_low | 600ms | 1.20J |
| task_background | 800ms | 1.44J |
| **总计** | - | **3.94J** |

### 太阳能收集
| 时间 | 收集速率 |
|------|----------|
| 00:00 | 0 J/s ❌ |
| 08:00 | 97.4 J/s ✅ |
| 12:00 | 153.1 J/s ✅ |

### 级联调度结果（初始能量5.0J）
```
所有时间点都能成功调度4个任务 ✅

步骤1: task_high   (-0.50J) → 4.50J
步骤2: task_mid    (-0.80J) → 3.70J
步骤3: task_low    (-1.20J) → 2.50J
步骤4: task_background (-1.44J) → 1.06J
```

## 🔗 相关文档

### 根目录文档
- [EPP_SCHEDULER_DESIGN.md](../EPP_SCHEDULER_DESIGN.md) - EPP算法设计文档
- [TASKSET_COMPARISON.md](../TASKSET_COMPARISON.md) - EPP vs CASCADE/ASAP 任务集对比
- [EPP_SIMULATION.md](../EPP_SIMULATION.md) - EPP运行模拟

### 源代码
- [librtsim/include/rtsim/scheduler/gpfp_epp_scheduler.hpp](../librtsim/include/rtsim/scheduler/gpfp_epp_scheduler.hpp) - EPP调度器头文件
- [librtsim/scheduler/gpfp_epp_scheduler.cpp](../librtsim/scheduler/gpfp_epp_scheduler.cpp) - EPP调度器实现

### 其他测试
- [preemptive_test/](../preemptive_test/) - 抢占式调度器测试
- [cascade_test/](../cascade_test/) - CASCADE调度器测试
- [energy_impact_test/](../energy_impact_test/) - 能量影响测试

## 💡 测试建议

### 推荐配置
1. **初始能量**: 5.0J
2. **测试时间**: 8:00或12:00（有太阳能）
3. **仿真时长**: 5000ms

### 测试场景
1. **级联调度测试** - 验证4个任务能否连续调度
2. **能量恢复测试** - 验证能量恢复机制
3. **抢占测试** - 验证Tick级抢占延迟
4. **长时间运行测试** - 验证周期性任务调度

## 📧 常见问题

### Q: 为什么推荐8am或12pm配置？
A: 这两个时间有太阳能收集，可以测试能量恢复机制。午夜无太阳能，能量耗尽后无法恢复。

### Q: 初始能量设置多少合适？
A: 推荐5.0J，可以调度所有4个任务。如果只测试单个任务，1.0J也足够。

### Q: 能量恢复需要多长时间？
A: 取决于时间：
- 8am: 约11ms恢复1.06J
- 12pm: 约7ms恢复1.06J
- 0am: 无法恢复（无太阳能）

### Q: 如何验证抢占是否工作？
A: 查看日志中的 "抢占" 关键字，或检查task_low执行时间是否被task_high打断。

## 🎯 预期结果

### 成功的运行应该看到：
```
✅ [EPP] 能量足够，调度任务: task_high
🔄 [EPP] 继续级联检查下一个任务
✅ [EPP] 能量足够，调度任务: task_mid
🔄 [EPP] 继续级联检查下一个任务
✅ [EPP] 能量足够，调度任务: task_low
🔄 [EPP] 继续级联检查下一个任务
✅ [EPP] 能量足够，调度任务: task_background
✅ [EPP] 本轮调度成功: 4个任务
```

---

**最后更新**: 2025-01-16
**EPP版本**: v1.0
**状态**: ✅ 已编译，准备测试
