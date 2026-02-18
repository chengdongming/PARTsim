# ALAP调度器性能测试报告

## 测试配置
- **仿真时长**: 2000ms
- **CPU核心数**: 4个
- **能量管理**: 太阳能收集 + 能量感知调度
- **测试日期**: 2026-02-17
- **调度器版本**: 抢占式ALAP调度器

## 性能对比结果

### 总体排名

| 排名 | 算法 | 完成数 | 超时数 | 完成率 |
|------|------|--------|--------|--------|
| 🥇 | **ALAP-Block** | 77 | 42 | **64.71%** |
| 🥈 | **ALAP-NonBlock** | 76 | 43 | **63.87%** |
| 🥉 | **ALAP-Sync** | 40 | 79 | **33.61%** |

### 各任务详细完成率

#### ALAP-Block (64.71%)
- Task_Mid_A: 100.0% (19/19) ✅
- Task_Survivor_Eco: 93.9% (31/33) ✅
- Task_Assassin_Hungry: 66.7% (26/39) ⚠️
- Task_Mid_B: 6.2% (1/16) ❌
- Task_Low_A: 0.0% (0/9) ❌
- Task_Low_B: 0.0% (0/3) ❌

#### ALAP-NonBlock (63.87%)
- Task_Mid_A: 100.0% (19/19) ✅
- Task_Survivor_Eco: 90.9% (30/33) ✅
- Task_Assassin_Hungry: 66.7% (26/39) ⚠️
- Task_Mid_B: 6.2% (1/16) ❌
- Task_Low_A: 0.0% (0/9) ❌
- Task_Low_B: 0.0% (0/3) ❌

#### ALAP-Sync (33.61%)
- Task_Mid_A: 100.0% (19/19) ✅
- Task_Assassin_Hungry: 51.3% (20/39) ⚠️
- Task_Survivor_Eco: 3.0% (1/33) ❌ 严重失败！
- Task_Mid_B: 0.0% (0/16) ❌
- Task_Low_A: 0.0% (0/9) ❌
- Task_Low_B: 0.0% (0/3) ❌

## 关键发现

1. **短时间仿真性能更好**: 2000ms vs 10000ms，完成率提升18-40%
2. **ALAP-Block与ALAP-NonBlock性能相当**: 仅相差0.84%
3. **Task_Assassin_Hungry改善**: 从13-21%提升到51-67%
4. **长周期任务饥饿**: period>=120的任务完成率极低
5. **ALAP-Sync严重失败**: Task_Survivor_Eco仅3.0%

## 推荐

🏆 **推荐使用 ALAP-Block** (64.71%完成率)

❌ **不推荐 ALAP-Sync** (33.61%完成率)

---

**测试时间**: 2026-02-17 02:22
