# EnergyMRTKernel 技术深度解析

**文档版本：** 1.0
**创建时间：** 2026-01-25
**基于源码：** `librtsim/energyMRTKernel.cpp` + `librtsim/include/rtsim/energyMRTKernel.hpp`

---

## 🎯 设计目标

EnergyMRTKernel 是为 **ARM Big-LITTLE 异构多核架构** 设计的高级能量感知实时内核。

**核心设计理念：**
1. **能效优先** - 在满足实时约束的前提下最小化能量消耗
2. **智能迁移** - 任务可以在CPU核心之间动态迁移
3. **频率调节** - 通过OPP（Operating Performance Point）动态调节频率
4. **负载均衡** - 在核心之间平衡负载以节省能量

---

## 📐 架构概览

### 1. 类继承关系

```
RTKernel (基础实时内核)
    ↓
MRTKernel (多核实时内核)
    ↓
EnergyMRTKernel (能量感知扩展内核)
```

### 2. 核心组件

```cpp
class EnergyMRTKernel : public MRTKernel {
private:
    // 1. Big-LITTLE 架构支持
    Island_BL *_islands[2];           // _islands[0] = LITTLE, _islands[1] = BIG

    // 2. CBS 服务器封装
    map<AbsRTTask *, CBServerCallingEMRTKernel *> _envelopes;

    // 3. 核心队列管理
    EnergyMultiCoresScheds *_queues;  // 管理每个CPU的就绪队列

    // 4. 临时迁移跟踪
    vector<MigrationProposal> _temporarilyMigrated;

    // 5. 调试支持
    map<AbsRTTask *, std::tuple<CPU_BL *, unsigned int, unsigned int>> _m_forcedDispatch;
};
```

---

## 🔧 核心技术机制

### 机制1: CPU 选择算法 (chooseCPU_BL)

**位置：** `energyMRTKernel.cpp:820-863`

**算法流程：**

```cpp
void EnergyMRTKernel::chooseCPU_BL(AbsRTTask *t, vector<ConsumptionTable> iDeltaPows) {
    // 步骤1: 按功耗增量排序 (升序)
    sort(iDeltaPows.begin(), iDeltaPows.end(),
         [](ConsumptionTable const &e1, ConsumptionTable const &e2) {
             return e1.cons < e2.cons;  // 功耗增量最小的在前
         });

    // 步骤2: 选择功耗最小的配置
    struct ConsumptionTable chosen = iDeltaPows[0];
    CPU_BL *chosenCPU = chosen.cpu;
    unsigned int chosenOPP = chosen.opp;

    // 步骤3: 应用负载均衡策略
    balanceLoadEnergy(&chosenCPU, chosenOPP, chosenCPUchanged, iDeltaPows);

    // 步骤4: 应用保留LITTLE_3策略
    leaveLittle3(t, iDeltaPows, chosenCPU);

    // 步骤5: 执行调度
    dispatch(chosenCPU, t, chosenOPP);
}
```

**关键数据结构：**
```cpp
struct ConsumptionTable {
    double cons;      // 功耗增量 (iDeltaPow)
    CPU_BL *cpu;      // CPU指针
    int opp;          // OPP索引
};
```

**功耗增量计算：** (`energyMRTKernel.cpp:1047-1073`)
```cpp
// 新配置下的功耗
iPowWithNewTask = (newUtilizationIsland + utilization_t) * c->getPower(newFreq);

// 当前配置下的功耗
iOldPow = oldUtilizationIsland * c->getPower(frequency);

// 功耗增量
iDeltaPow = iPowWithNewTask - iOldPow;
```

**选择依据：**
- ✅ 能量增量最小
- ✅ CPU利用率不超过100%
- ✅ 满足实时约束 (U + U_newTask ≤ 1.0)

---

### 机制2: 任务迁移系统

#### 2.1 永久迁移 (migrateInto)

**位置：** `energyMRTKernel.cpp:574-623`

**触发条件：**
- 任务结束，CPU变为空闲
- 迁入的CPU没有就绪任务
- 迁入的CPU没有运行中任务

**迁移策略：**

```cpp
bool EnergyMRTKernel::migrateInto(CPU_BL *endingCPU, vector<AbsRTTask *> toBeSkipped) {
    // 策略1: 如果是LITTLE核心空闲，优先从BIG核心迁移任务
    MigrationProposal proposal = migrateFromBig(endingCPU, toBeSkipped);

    // 策略2: 如果没有BIG→LITTLE迁移，则平衡同一Island内的负载
    if (proposal.task == NULL)
        proposal = balanceLoad(endingCPU, toBeSkipped);

    // 策略3: 验证迁移的安全性
    if (proposal.task != NULL && !isMigrationSafe(proposal)) {
        toBeSkipped.push_back(proposal.task);
        proposal = getTaskToMigrateInto(endingCPU, toBeSkipped);
    }

    // 执行迁移
    if (proposal.task != NULL) {
        _queues->onMigrationFinished(proposal.task, proposal.from, proposal.to);
        return true;
    }
    return false;
}
```

**BIG→LITTLE 迁移逻辑：** (`energyMRTKernel.cpp:646-686`)
```cpp
MigrationProposal EnergyMRTKernel::migrateFromBig(CPU_BL *endingCPU, vector<AbsRTTask *> toBeSkipped) {
    // 只在LITTLE核心空闲时尝试
    if (endingCPU->getIsland()->type() == IslandType::LITTLE) {
        // 遍历所有BIG核心的就绪任务
        for (CPU_BL *c : getIslandBig()->getProcessors()) {
            vector<AbsRTTask *> readyTasks = getReadyTasks(c);
            for (AbsRTTask *tt : readyTasks) {
                // 尝试将BIG任务放到LITTLE核心
                tryTaskOnCPU_BL(tt, endingCPU, iDeltaPows);
                if (!iDeltaPows.empty()) {
                    // 找到可以迁移的任务
                    return MigrationProposal{.task = tt, .from = c, .to = endingCPU};
                }
            }
        }
    }
    return NULL;
}
```

#### 2.2 临时迁移 (migrateTemporarily)

**位置：** `energyMRTKernel.hpp:341-378`

**概念：**
- 临时迁移是为了充分利用空闲CPU
- 迁移是"假"的（没有迁移开销）
- 任务在新CPU上运行直到：
  - 原CPU有新任务到达
  - 任务截止期
  - 虚拟时间结束（CBS服务器）

**代码：**
```cpp
bool EnergyMRTKernel::migrateTemporarily(CPU_BL *endingCPU) {
    // 在同一Island内平衡负载
    MigrationProposal proposal = balanceLoad(endingCPU, {});

    if (proposal.task != NULL) {
        // 记录临时迁移
        _temporarilyMigrated.push_back(proposal);
        _queues->onMigrationFinished(proposal.task, proposal.from, proposal.to);
        return true;
    }
    return false;
}
```

**取消临时迁移：** (`energyMRTKernel.hpp:381-397`)
```cpp
void EnergyMRTKernel::removeTaskTemporarilyMigrated(CPU_BL *to) {
    for (int i = 0; i < _temporarilyMigrated.size(); i++) {
        if (_temporarilyMigrated.at(i).to == to) {
            MigrationProposal mp = _temporarilyMigrated.at(i);
            // 将任务迁移回原CPU
            _queues->onMigrationFinished(mp.task, mp.to, mp.from);
            _temporarilyMigrated.erase(...);
        }
    }
}
```

---

### 机制3: CBS (Constant Bandwidth Server) 封装

**位置：** `energyMRTKernel.hpp:471-489`

**目的：**
- 将周期性任务封装在CBS服务器中
- 提供更好的带宽隔离和可调度性保证
- 支持任务在迁移时的WCET重新计算

**封装过程：**
```cpp
CBServerCallingEMRTKernel *EnergyMRTKernel::addTaskAndEnvelope(AbsRTTask *t, const string &param) {
    CBServerCallingEMRTKernel *serv = dynamic_cast<CBServerCallingEMRTKernel *>(t);

    if (serv == NULL) {  // 如果是普通周期性任务
        // 创建CBS服务器
        serv = new CBServerCallingEMRTKernel(
            Tick(t->getWCET(1.0)),    // budget = WCET
            t->getDeadline(),           // period = deadline
            t->getDeadline(),           // deadline
            "hard",
            "CBS(" + t->toString() + ")",
            "fifo"
        );
        serv->addTask(*t);

        addTask(*serv, param);

        // 记录任务和服务器的映射
        _envelopes[t] = serv;
    }

    return serv;
}
```

**CBS服务器状态机：**
```
IDLE → EXECUTING → RELEASING → RECHARGING → IDLE
       ↓           ↓
     (budget    (virtual time ends
      depletion)  or yielding)
```

**虚拟时间结束时的迁移：** (`energyMRTKernel.hpp:811-861`)
```cpp
void EnergyMRTKernel::onReleasingIdle(CBServer *cbs) {
    CPU_BL *endingCPU = dynamic_cast<CPU_BL *>(_queues->onReleasingIdle(cbs));

    // 如果CPU没有就绪任务，尝试迁移
    if (getReadyTasks(endingCPU).empty()) {
        MigrationProposal proposal;
        while (true) {
            proposal = getTaskToMigrateInto(endingCPU, toBeSkipped);

            if (proposal.task == NULL)
                break;

            // 验证迁移的安全性和能效
            if (isMigrationSafe(proposal) && isMigrationEnergConvenient(proposal))
                break;
            else
                toBeSkipped.push_back(proposal.task);
        }

        if (proposal.task != NULL)
            _queues->onMigrationFinished(proposal.task, proposal.from, proposal.to);
        else
            migrateTemporarily(endingCPU);
    }
}
```

---

### 机制4: OPP (Operating Performance Point) 管理

**OPP定义：**
- OPP是电压-频率对的索引
- 每个CPU有多个OPP可选
- 同一Island内的CPU共享频率

**OPP选择算法：** (`energyMRTKernel.cpp:967-1095`)

```cpp
void EnergyMRTKernel::tryTaskOnCPU_BL(AbsRTTask *t, CPU_BL *c, vector<ConsumptionTable> &iDeltaPows) {
    double frequency = c->getFrequency();
    string startingWL = c->getWorkload();
    c->setWorkload(Utils::getTaskWorkload(t));

    // 遍历所有更高的OPP
    for (OPP tryOPP : c->getHigherOPPs()) {
        int ooo = c->getIsland()->getOPPIndexByOPP(tryOPP);
        double newFreq = c->getFrequency(ooo);
        double newCapacity = c->getSpeed(newFreq);

        c->setOPP(ooo);

        // 检查任务是否可调度
        if (_sched->isAdmissible(c, getReadyTasks(c), t)) {
            // 计算CPU利用率
            utilization = getUtilization(c, newCapacity);
            utilization_t = getUtilization(t, newCapacity);

            // 检查利用率约束
            if (utilization + utilization_t > 1.0)
                continue;

            // 计算功耗增量
            newUtilizationIsland = getIslandUtilization(newCapacity, island, NULL);
            oldUtilizationIsland = getIslandUtilization(c->getSpeed(frequency), island, &nTaskIsland);

            iPowWithNewTask = (newUtilizationIsland + utilization_t) * c->getPower(newFreq);
            iOldPow = oldUtilizationIsland * c->getPower(frequency);

            iDeltaPow = iPowWithNewTask - iOldPow;

            // 添加到候选列表
            iDeltaPows.push_back({.cons = iDeltaPow, .cpu = c, .opp = ooo});
        }
    }

    c->setOPP(startingOPP);
    c->setWorkload(startingWL);
}
```

**OPP变化时的预算调整：** (`energyMRTKernel.cpp:252-291`)
```cpp
void EnergyMRTKernel::onOppChanged(unsigned int curropp, Island_BL *island) {
    // 遍历所有封装的任务
    for (auto &elem : _envelopes) {
        CPU_BL *c = getProcessor(elem.first);
        if (c == NULL) continue;

        // 如果任务在受影响的Island上
        if (c->getIsland()->type() == island->type()) {
            // 重新计算WCET和预算
            Tick taskWCET = Tick(ceil(elem.first->getWCET(c->getSpeed())));
            elem.second->changeBudget(taskWCET);
        }
    }
}
```

---

### 机制5: 负载均衡策略

#### 5.1 能量负载均衡 (balanceLoadEnergy)

**位置：** `energyMRTKernel.cpp:792-818`

**策略：**
- 如果选中的CPU忙，寻找Island内功耗相同且空闲的CPU
- 避免所有任务集中在同一个核心

```cpp
void EnergyMRTKernel::balanceLoadEnergy(CPU_BL **chosenCPU, unsigned int &chosenOPP,
                                        bool &chosenCPUchanged, vector<ConsumptionTable> iDeltaPows) {
    if ((*chosenCPU)->busy()) {
        // 查找功耗相同但空闲的CPU
        for (int i = 1; i < iDeltaPows.size(); i++) {
            if (iDeltaPows[i].cons == iDeltaPows[0].cons &&
                !iDeltaPows[i].cpu->busy()) {
                *chosenCPU = iDeltaPows[i].cpu;
                chosenOPP = iDeltaPows[i].opp;
                chosenCPUchanged = true;
                break;
            }
        }
    }
}
```

#### 5.2 负载均衡迁移 (balanceLoad)

**位置：** `energyMRTKernel.cpp:688-719`

**策略：**
- 从同一Island内选择有多个任务的核心
- 迁移一个就绪任务到空闲核心

```cpp
MigrationProposal EnergyMRTKernel::balanceLoad(CPU_BL *endingCPU, vector<AbsRTTask *> toBeSkipped) {
    // 遍历同一Island的所有CPU
    for (CPU_BL *c : getProcessors(endingCPU->getIsland()->type())) {
        vector<AbsRTTask *> readyTasks = getReadyTasks(c);
        unsigned int nTasksOnCore = readyTasks.size() + (getRunningTask(c) == NULL ? 0 : 1);

        // 如果核心有多个任务
        if (nTasksOnCore > 1 && !Utils::exists(readyTasks.at(0), toBeSkipped)) {
            AbsRTTask *tt = readyTasks.at(0);
            return MigrationProposal{.task = tt, .from = c, .to = endingCPU};
        }
    }
    return NULL;
}
```

---

### 机制6: 保留LITTLE_3策略

**位置：** `energyMRTKernel.cpp:741-790`

**目的：**
- 保留一个LITTLE核心（LITTLE_3）空闲
- 以备高WCET任务到来时使用
- 避免高WCET任务被迫调度到BIG核心

**实现：**
```cpp
void EnergyMRTKernel::leaveLittle3(AbsRTTask *t, vector<ConsumptionTable> iDeltaPows, CPU_BL *&chosenCPU) {
    if (!EMRTK_LEAVE_LITTLE3_ENABLED) {
        return;  // 策略可通过标志禁用
    }

    // 只有当选中的是LITTLE_3时才处理
    if (chosenCPU->getName().find("LITTLE_3") == string::npos ||
        chosenCPU->getIsland()->type() == IslandType::BIG) {
        return;
    }

    bool fitsInOtherCore = true;

    // 检查任务是否可以在其他LITTLE核心上运行
    for (int i = 0; i < iDeltaPows.size(); i++) {
        if (iDeltaPows[i].cpu->getIsland()->type() == IslandType::LITTLE &&
            iDeltaPows[i].cpu->getName().find("LITTLE_3") == string::npos) {
            // 使用其他LITTLE核心
            chosenCPU = iDeltaPows[i].cpu;
            chosenCPU->setOPP(iDeltaPows[i].opp);
            fitsInOtherCore = false;
            break;
        }
    }

    if (fitsInOtherCore) {
        std::cout << "Task only fits on little 3 and in bigs => stay in LITTLE_3" << std::endl;
    }
}
```

**🔴 硬编码位置：** `energyMRTKernel.cpp:757, 774`
```cpp
if (chosenCPU->getName().find("LITTLE_3") == string::npos ||  // ❌ 硬编码
    iDeltaPows[i].cpu->getName().find("LITTLE_3") == string::npos) {  // ❌ 硬编码
```

**为什么需要修复：**
- 硬编码了CPU名称 "LITTLE_3"
- 如果CPU命名不同，策略失效
- 应该使用CPU类型或属性而非名称

---

## 📊 调度流程

### 完整调度决策流程

```
1. dispatch() 被调用
   ↓
2. 获取所有新任务 (num_newtasks)
   ↓
3. for each task:
   ↓
4.     tryTaskOnCPU_BL() - 在所有CPU的所有OPP上尝试任务
   ↓
5.     计算功耗增量表 (iDeltaPows)
   ↓
6.     chooseCPU_BL():
   ↓
7.         按功耗增量排序
   ↓
8.         选择最小功耗配置
   ↓
9.         balanceLoadEnergy() - 应用负载均衡
   ↓
10.        leaveLittle3() - 应用保留策略
   ↓
11.        dispatch(cpu, task, opp) - 确认调度
   ↓
12. onBeginDispatchMulti() - 开始上下文切换
   ↓
13. onEndDispatchMulti() - 任务开始执行
   ↓
14. onTaskGetsRunning() - 设置CPU工作负载类型
```

### 任务结束时的迁移流程

```
1. onEnd(task) 被调用
   ↓
2. 提取任务，清空CPU
   ↓
3. if CPU空闲:
   ↓
4.     if EMRTK_CBS_MIGRATE_AFTER_END:
   ↓
5.         migrateInto(endingCPU):
   ↓
6.             migrateFromBig() - BIG→LITTLE迁移
   ↓
7.             balanceLoad() - Island内负载均衡
   ↓
8.             isMigrationSafe() - 验证安全性
   ↓
9.         if 迁移失败 && EMRTK_TEMPORARILY_MIGRATE_END:
   ↓
10.            migrateTemporarily() - 临时迁移
   ↓
11.    else:
   ↓
12.        schedule() - 调度就绪任务
   ↓
13. if Island空闲:
   ↓
14.     setOPP(0) - 降低频率到最小值
```

---

## 🔍 策略控制标志

```cpp
// 全局策略开关
bool EnergyMRTKernel::EMRTK_BALANCE_ENABLED = 1;              // 负载均衡
bool EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED = 0;        // 保留LITTLE_3策略
bool EnergyMRTKernel::EMRTK_MIGRATE_ENABLED = 1;              // 任务迁移
bool EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED = 0;            // CBS服务器让出
bool EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME = 1;    // 虚拟时间结束时的临时迁移
bool EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 1;      // 任务结束时的临时迁移

// CBS相关
bool EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED = 1;              // CBS封装
bool EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 1;       // 虚拟时间结束后迁移
bool EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 0;                        // 任务结束后迁移
```

---

## ⚡ 能量模型

### 能量计算公式

**瞬时功耗：**
```
P = U_island * P_freq
```
其中：
- `U_island` - Island利用率（所有核心的任务利用率之和）
- `P_freq` - 当前频率下的功率

**功耗增量：**
```
ΔP = P_new - P_old
  = (U_island_new + U_task) × P_freq_new - U_island_old × P_freq_old
```

**能量消耗：**
```
E = P × t
```

### 工作负载类型影响

不同工作负载类型有不同的功耗系数：
- `idle`: 最低功耗
- `control`: 非常低功耗
- `hash`: 低功耗
- `bzip2`: 中等功耗
- `encrypt/decrypt`: 高功耗

---

## 🎯 与 MRTKernel 的关键区别

| 特性 | MRTKernel | EnergyMRTKernel |
|------|-----------|-----------------|
| **调度决策** | 调度器选择任务，内核分配CPU | 内核智能选择CPU+OPP |
| **CPU选择** | 第一个空闲CPU | 基于功耗增量的最优选择 |
| **任务迁移** | ❌ 无 | ✅ 支持（永久+临时） |
| **频率调节** | 手动设置 | 自动选择OPP |
| **Big-LITTLE** | ❌ 不支持 | ✅ 原生支持 |
| **CBS封装** | ❌ 无 | ✅ 周期性任务自动封装 |
| **负载均衡** | ❌ 无 | ✅ Island内和跨Island |
| **能量管理** | 调度器内部实现 | 内核层实现 |

---

## 💡 使用场景

### 适合使用 EnergyMRTKernel 的场景：

1. **ARM Big-LITTLE 异构多核**
   - 需要在BIG和LITTLE核心之间智能调度任务

2. **动态负载变化**
   - 任务数量和WCET变化大
   - 需要动态迁移任务

3. **精细的能量管理**
   - 需要OPP级别的频率调节
   - 需要功耗增量计算

4. **复杂的可调度性需求**
   - 需要CBS服务器提供带宽隔离
   - 需要临时迁移平衡负载

### 当前系统为什么不需要 EnergyMRTKernel：

✅ **使用同构多核**（所有CPU性能相同）
✅ **能量管理已在调度器中实现**（TIE/TGF/BTIE）
✅ **不需要任务迁移**（能量约束已在调度时考虑）
✅ **不需要Big-LITTLE调度**（没有异构核心）

---

## 🔴 已知硬编码问题

### 1. CPU名称硬编码

**位置：** `energyMRTKernel.cpp:757, 774`

**问题：**
```cpp
if (chosenCPU->getName().find("LITTLE_3") == string::npos)
```

**影响：**
- 只适用于名为 "LITTLE_3" 的CPU
- 降低了代码可移植性

**修复建议：**
```cpp
// 方案1: 使用索引选择
int little_cpu_index = 0;
int target_little_index = 2;  // 可配置
for (CPU_BL *c : getProcessors(IslandType::LITTLE)) {
    if (little_cpu_index == target_little_index) {
        chosenCPU = c;
        break;
    }
    little_cpu_index++;
}

// 方案2: 使用频率选择（最节能）
CPU_BL *chosenCPU = nullptr;
double min_freq = std::numeric_limits<double>::max();
for (CPU_BL *c : getProcessors(IslandType::LITTLE)) {
    double cpu_freq = c->getFrequency();
    if (cpu_freq < min_freq) {
        min_freq = cpu_freq;
        chosenCPU = c;
    }
}
```

---

## 📈 性能权衡

### 能量 vs 性能

| 策略 | 能量优化 | 性能影响 | 实时性保证 |
|------|---------|---------|-----------|
| **低OPP** | ✅ 节能 | ⚠️ 执行慢 | ✅ 如果可调度 |
| **BIG→LITTLE迁移** | ✅ 节能 | ⚠️ 可能变慢 | ✅ 如果安全 |
| **临时迁移** | ✅ 利用空闲 | ✅ 性能提升 | ⚠️ 需要验证 |
| **CBS封装** | ⚠️ 开销 | ✅ 隔离性好 | ✅ 更强保证 |

### 设计哲学

EnergyMRTKernel 采用 **能量优先** 的设计哲学：
1. 在满足实时约束的前提下最小化能量
2. 通过迁移和频率调节优化能效
3. 使用CBS提供更强的可调度性保证

---

## 📚 总结

### EnergyMRTKernel 的核心价值

1. **智能CPU选择** - 基于功耗增量的最优选择
2. **动态任务迁移** - 在Big-LITTLE之间迁移以节省能量
3. **精细OPP管理** - 自动选择最优频率
4. **CBS服务器支持** - 提供带宽隔离和可调度性保证
5. **复杂负载均衡** - Island内和跨Island的负载均衡

### 技术亮点

- ✅ **功耗增量计算** - 精确评估调度决策的能量影响
- ✅ **安全性验证** - 确保迁移不破坏可调度性
- ✅ **临时迁移机制** - 无开销的"假"迁移
- ✅ **策略可配置** - 通过静态标志启用/禁用各项策略

### 适用性

**EnergyMRTKernel 是一个高级特性内核，适用于：**
- ARM Big-LITTLE异构多核
- 需要精细能量管理的场景
- 复杂的实时调度需求

**对于当前系统的意义：**
- 📌 学习参考价值 - 了解高级能量管理技术
- 📌 未来扩展选项 - 如果需要迁移和Big-LITTLE支持
- 📌 死代码 - 当前系统不使用，LITTLE_3硬编码不影响功能

---

**文档结束**

**相关文档：**
- [MRTKERNEL_COMPARISON.md](test_results/MRTKERNEL_COMPARISON.md) - MRTKernel vs EnergyMRTKernel对比
- [PRACTICAL_HARDCODE_FIX.md](test_results/PRACTICAL_HARDCODE_FIX.md) - 硬编码问题修复方案
