# TIE vs TGF: Algorithm Difference Summary

## Overview

This document explains when the TIE (Threshold-based Interruptible Execution) and TGF (Greedy Forwarding) scheduling algorithms behave differently versus identically.

## Core Algorithm Difference

### TIE: Conservative Strategy
**Code Location:** [gpfp_tie_scheduler.cpp:605-612](../../librtsim/scheduler/gpfp_tie_scheduler.cpp#L605-L612)

```cpp
if (available_energy < unit_energy - EPSILON) {
    SCHEDULER_LOG_INFO("⚠️ [TIE] 能量不足，停止级联" +
                      " 任务=" + getTaskName(task));
    return nullptr;  // ⭐ Immediately stops cascade
}
```

**Behavior:** When the first task in the ready queue has insufficient energy, TIE immediately returns `nullptr` and stops checking any other tasks in the queue.

### TGF: Greedy Strategy
**Code Location:** [gpfp_tgf_scheduler.cpp:542-579](../../librtsim/scheduler/gpfp_tgf_scheduler.cpp#L542-L579)

```cpp
if (available_energy < unit_energy - EPSILON) {
    // ⭐ Don't stop! Continue searching for energy-sufficient tasks
    for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
        AbsRTTask *next_task = _ready_queue[j];
        double next_available = _current_energy - _dispatching_tasks_total_energy;

        if (next_available >= next_unit_energy - EPSILON) {
            // Found an energy-sufficient task! Schedule it.
            return next_task;
        }
    }
    return nullptr;  // Only stop if ALL tasks are energy-insufficient
}
```

**Behavior:** When a task has insufficient energy, TGF skips it and continues checking subsequent tasks in the ready queue until it finds one with sufficient energy (or exhausts the queue).

## When Algorithms Differ

### Scenario: High-Priority Tasks Never Executed

**Configuration:**
- Initial energy: 0.15mJ (insufficient for high-priority tasks from the start)
- task_1: Priority=20, Energy=0.7mJ/ms (encrypt)
- task_2: Priority=25, Energy=0.7mJ/ms (encrypt)
- task_3: Priority=40, Energy=0.0465mJ/ms (control)
- task_4: Priority=50, Energy=0.0465mJ/ms (control)

**Ready Queue State:**
```
[task_1, task_2, task_3, task_4]  ← All in queue, none have run yet
```

**TIE Behavior:**
```
getTaskN(0):
  Check task_1: Needs 0.7mJ, has 0.15mJ ✗
  → STOP CASCADE
  → return nullptr

getTaskN(1):
  Check task_1: Needs 0.7mJ, has 0.15mJ ✗
  → STOP CASCADE
  → return nullptr

Result: 0 tasks scheduled
```

**TGF Behavior:**
```
getTaskN(0):
  Check task_1: Needs 0.7mJ, has 0.15mJ ✗ → SKIP
  Check task_2: Needs 0.7mJ, has 0.15mJ ✗ → SKIP
  Check task_3: Needs 0.0465mJ, has 0.15mJ ✓ → SCHEDULE

getTaskN(1):
  task_3 already counted
  Check task_1: SKIP
  Check task_2: SKIP
  Check task_4: Needs 0.0465mJ, has 0.1035mJ ✓ → SCHEDULE

Result: 2 tasks scheduled (task_3, task_4)
```

**Difference:** TGF schedules 2 low-priority tasks that TIE misses.

**Test File:** [tasks_4tasks_difference.yml](./tasks_4tasks_difference.yml)

---

## When Algorithms Behave Identically

### Scenario: High-Priority Tasks Executed Then Interrupted

**Configuration:**
- Initial energy: 1.6mJ (sufficient for high-priority tasks to run for 1ms)
- task_1: Priority=20, Energy=0.7mJ/ms (encrypt), Runtime=5ms
- task_2: Priority=25, Energy=0.7mJ/ms (encrypt), Runtime=5ms
- task_3: Priority=40, Energy=0.0465mJ/ms (control), Runtime=3ms
- task_4: Priority=50, Energy=0.0465mJ/ms (control), Runtime=3ms

**Timeline:**

**0ms - Initial Scheduling:**
```
Both algorithms schedule task_1 and task_2 to 2 CPUs
```

**1ms Tick - High-Priority Tasks Interrupted:**
```
Energy deduction: 0.7mJ × 2 = 1.4mJ
Remaining: 1.6mJ - 1.4mJ = 0.2mJ

Both task_1 and task_2 interrupted (need 0.7mJ for 2nd ms, only 0.2mJ available)

Critical: Kernel calls suspend() → extract() on interrupted tasks
→ task_1 and task_2 REMOVED from ready queue
```

**After Interruption - Re-scheduling:**

```
Queue state BEFORE re-scheduling:
  Ready Queue: [task_3, task_4]  ← task_1, task_2 were extracted!
  Running: {} (both interrupted)

TIE:
  getTaskN(0):
    Front of queue: task_3 (not task_1!)
    Check task_3: Needs 0.0465mJ, has 0.2mJ ✓ → SCHEDULE
  getTaskN(1):
    Next: task_4
    Check task_4: Needs 0.0465mJ, has 0.1535mJ ✓ → SCHEDULE

TGF:
  getTaskN(0):
    Front of queue: task_3
    Check task_3: Needs 0.0465mJ, has 0.2mJ ✓ → SCHEDULE
  getTaskN(1):
    Next: task_4
    Check task_4: Needs 0.0465mJ, has 0.1535mJ ✓ → SCHEDULE

Result: Both schedule task_3 and task_4
```

**Why Identical?**
- Interrupted tasks are `extract()`-ed from the queue and temporarily removed
- When `getTaskN(0)` is called, task_3 is at the front of the queue (not task_1)
- TIE sees task_3 first, checks energy, and it's sufficient → schedules it
- No cascade stopping occurs because the first task in queue (task_3) has sufficient energy

**Test File:** [tasks_execute_then_interrupt.yml](./tasks_execute_then_interrupt.yml)

---

## Key Implementation Detail

### Task Interruption Mechanism

**Code Location:** [gpfp_tie_scheduler.cpp:920-934](../../librtsim/scheduler/gpfp_tie_scheduler.cpp#L920-L934)

```cpp
void TIEScheduler::checkAndInterruptRunningTasks() {
    // ...
    for (AbsRTTask *task : tasks_to_interrupt) {
        SCHEDULER_LOG_INFO("🛑 [TIE] 中断任务（能量不足）: " + getTaskName(task));

        // ⭐ Critical: suspend() calls extract() internally
        _kernel->suspend(task);

        // suspend() → deschedule() → extract()
        // This REMOVES the task from the ready queue temporarily
    }
}
```

**What happens when a task is interrupted:**
1. `suspend()` is called on the task
2. This triggers `deschedule()` which calls `extract()` on the task
3. The task is **removed from the ready queue** (not at the front, not at the back, just removed)
4. Later, the task may be re-inserted into the queue (but not immediately)

**Implication:**
- When re-scheduling happens after interruption, the interrupted tasks are NOT in the queue
- Lower-priority tasks that were behind them in the queue now become accessible
- TIE can now schedule these lower-priority tasks because they appear at the front of the queue

---

## Summary Table

| Scenario | TIE Result | TGF Result | Difference? |
|----------|-----------|-----------|-------------|
| **High-priority tasks never run (insufficient energy)** | 0 tasks | 2 tasks | ✅ **YES** |
| **High-priority tasks run then interrupted** | 2 low-priority tasks | 2 low-priority tasks | ❌ No |
| **All tasks have sufficient energy** | All tasks | All tasks | ❌ No |
| **No tasks in queue** | 0 tasks | 0 tasks | ❌ No |

---

## Practical Implications

### TGF Advantage Scenario

TGF's greedy strategy shines when:
1. System has limited energy that's insufficient for high-priority tasks
2. But sufficient for lower-priority tasks
3. High-priority tasks have never run (stays at front of queue)

**Example:** Energy harvesting system with depleted battery
- High-priority control tasks can't run
- TGF utilizes remaining energy for low-priority data processing tasks
- TIE would waste this opportunity

### No Advantage Scenario

TGF and TIE behave the same when:
1. High-priority tasks run briefly then get interrupted
2. Interrupted tasks are removed from queue
3. Lower-priority tasks naturally become accessible

**Example:** System where all tasks briefly execute before energy depletion
- Both algorithms eventually schedule all runnable tasks
- Difference is in timing, not final outcome

---

## Testing Recommendations

To observe TIE vs TGF differences, create tests where:
1. ✅ Initial energy is insufficient for the first task(s) in queue
2. ✅ High-priority tasks have higher energy consumption than low-priority tasks
3. ✅ Tasks never get to run (no interruption scenario, pure initial scheduling)

To observe TIE = TGF behavior:
1. ❌ Tests where high-priority tasks run then get interrupted
2. ❌ Tests where all tasks have the same energy consumption rate
3. ❌ Tests with abundant energy for all tasks
