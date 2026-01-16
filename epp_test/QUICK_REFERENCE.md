# EPP调度器 - 能量数据快速参考

## 📊 核心数据

### 功率模型
```
bzip2:  2.0W × 时间 = 能量(J)
hash:   1.8W × 时间 = 能量(J)
```

### 任务能耗表
```
任务           WCET    能耗     每ms能耗
task_high      250ms   0.50J    0.002000 J/ms
task_mid       400ms   0.80J    0.002000 J/ms
task_low       600ms   1.20J    0.002000 J/ms
task_background 800ms  1.44J    0.001800 J/ms
────────────────────────────────────────
总计                   3.94J
```

### 太阳能收集速率
```
时间      辐照度      收集速率       每秒收集
00:00     0 W/m²     0 J/ms        0 J/s     ❌ 无太阳能
08:00     541 W/m²   0.097 J/ms    97 J/s    ✅ 中等
12:00     850 W/m²   0.153 J/ms   153 J/s    ✅ 最大
```

## 🎯 三个时间点模拟结果

### 午夜0点（无太阳能）
```
初始: 5.0J
调度: ✅✅✅✅ (4/4)
剩余: 1.06J
恢复: ❌ 无法恢复
```

### 上午8点（中等太阳能）
```
初始: 5.0J
调度: ✅✅✅✅ (4/4)
剩余: 1.06J
恢复: ✅ 11ms恢复1.06J
```

### 中午12点（最大太阳能）
```
初始: 5.0J
调度: ✅✅✅��� (4/4)
剩余: 1.06J
恢复: ✅ 7ms恢复1.06J
```

## 📁 文件链接

### 配置文件
- [config_epp_0am.yml](config_epp_0am.yml) - 午夜配置
- [config_epp_8am.yml](config_epp_8am.yml) - 上午8点配置 ⭐
- [config_epp_12pm.yml](config_epp_12pm.yml) - 中午12点配置 ⭐

### 任务和参考
- [tasks_epp.yml](tasks_epp.yml) - 任务集
- [ENERGY_DATA.md](ENERGY_DATA.md) - 详细能量数据
- [README.md](README.md) - 测试说明

## 🚀 快速运行

```bash
# 推荐：上午8点测试（有太阳能）
./build/rtsim-exe -c epp_test/config_epp_8am.yml -t epp_test/tasks_epp.yml

# 推荐：中午12点测试（最大太阳能）
./build/rtsim-exe -c epp_test/config_epp_12pm.yml -t epp_test/tasks_epp.yml
```

## 💡 关键发现

1. **初始能量5.0J** - 可以调度所有4个任务 ✅
2. **太阳能收集** - 8am: 97 J/s, 12pm: 153 J/s
3. **能量恢复时间** - 7-30ms（取决于时间）
4. **级联调度** - 完全正常工作 ✅
