# 测试计划：验证新实例到达时杀死旧实例

## 测试场景
- **任务**：单个周期任务
- **周期**：10ms
- **WCET**：15ms（超过周期！）
- **能量**：10J（充足）

## 关键时间点分析

### 时间轴
```
时间:  0ms    10ms   25ms   35ms
       |------|------|------|
���为:  [实例0开始]
       |      |
       |      +-- 实例1到达 (实例0还在执行！)
       |      |
       |      +-- 【关键】旧实例0被杀死
       |      |
       |      +-- 新实例1开始执行
       |      |
       |      |------| 15ms
       |             实例1完成
       |
       +-------------> 25ms
       实例0本该完成的时间（但被杀死了）
```

## 修改前后的预期行为对比

### 修改前 (_kill=false)
```
0ms:  实例0到达，开始执行
10ms: 实例1到达 → deadline miss + 缓冲
      实例0继续执行（霸占CPU！）
20ms: 实例2到达 → deadline miss + 缓冲
      实例0继续执行（还在霸占！）
25ms: 实例0完成
      从缓冲队列处理实例1（已经晚了15ms！）
```

**问题：**
- CPU被旧实例长期霸占
- 新实例累积在缓冲队列
- 系统可预测性差

### 修改后 (_kill=true)
```
0ms:  实例0到达，开始执行
10ms: 实例1到达 → deadline miss（记录一次）
      deadEvt.process() → killInstance()
      实例0被杀死（deschedule）
      实例1进入缓冲队列
      killEvt触发 → onKill() → 状态设为IDLE
      fakeArrEvt处理缓冲队列
      实例1从10ms开始执行
25ms: 实例1完成（15ms执行时间）
35ms: 实例2到达 → deadline miss（记录一次）
      实例1被杀死
      实例2从35ms开始执行
50ms: 实例2完成
```

**改进：**
- ✅ 旧实例及时释放CPU
- ✅ 新实例可以开始执行
- ✅ 符合实时系统假设
- ✅ 系统行为可预测

## 关键验证点

### 1. Trace文件中应该看到的事件
```json
// 在10ms时应该看到：
{
  "time": 10,
  "event_type": "dline_miss",  // 实例0错过截止期
  "task": "task_1"
},
{
  "time": 10,
  "event_type": "kill",        // 实例0被杀死 ⭐
  "task": "task_1"
},
{
  "time": 10,
  "event_type": "arrival",      // 实例1到达
  "task": "task_1"
},
{
  "time": 10,
  "event_type": "dispatch",     // 实例1被调度 ⭐
  "task": "task_1"
}

// 在25ms时应该看到：
{
  "time": 25,
  "event_type": "end_instance", // 实例1正常完成
  "task": "task_1",
  "executed": 15  // 实际执行了15ms
}
```

### 2. 能量消耗
- **修改前**：实例0执行15ms（0-10ms正常，10-15ms浪费）= 15ms × 1W = 15mJ
- **修改后**：实例0执行10ms + 实例1执行15ms = 25ms × 1W = 25mJ

等等，这个不对！让我重新计算...

### 3. 实际能量消耗（修正）

修改前：
- 实例0: 0-25ms = 25ms × 1W = 25mJ
- 实例1: 25-40ms = 15ms × 1W = 15mJ
- 总计: 40ms = 40mJ

修改后：
- 实例0: 0-10ms = 10ms × 1W = 10mJ
- 实例1: 10-25ms = 15ms × 1W = 15mJ
- 总计: 25ms = 25mJ

所以修改后**能量消耗更少**（因为没有浪费在已经失败的旧实例上）

### 4. deadline miss统计
- **修改前**：每次新实例到达都记录一次（可能累积）
- **修改后**：每次新实例到达记录一次（但及时处理）

## 仿真命令

```bash
# 设置环境
export LD_LIBRARY_PATH=./build/librtsim:$LD_LIBRARY_PATH

# 运行仿真（50ms足够观察多个实例）
./build/rtsim/rtsim \
  test_kill_instance/config.yml \
  test_kill_instance/taskset.yml \
  50000 \
  -t test_kill_instance/traces/trace_kill_test.json
```

## 成功标准

### ✅ 关键验证点
1. [ ] 在10ms处看到 `dline_miss` 事件
2. [ ] 在10ms处看到 `kill` 或类似的终止事件
3. [ ] 在10ms处看到新实例的 `arrival` 和 `dispatch` 事件
4. [ ] 实例1在25ms处完成（执行了15ms）
5. [ ] 旧实例0**没有**执行完整的15ms

### ❌ 失败模式
- 看到实例0执行完整的15ms（0-15ms或更长时间）
- 看到实例1延迟很久才开始执行
- 没有看到kill事件
