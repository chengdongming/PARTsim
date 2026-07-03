#include <algorithm>
#include <functional>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/task.hpp>

#define private public
#define protected public
#include <rtsim/scheduler/gpfp_asap_sync_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestASAPSyncScheduler : public ASAPSyncScheduler {
public:
    using Scheduler::enqueueModel;
};

class FakeASAPSyncTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeASAPSyncTask(int task_number,
                     int period,
                     int relative_deadline,
                     double remaining,
                     int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeASAPSyncTask" + std::to_string(task_number),
               1000,
               Tick(static_cast<Tick::impl_t>(remaining))),
          _task_number(task_number),
          _period(period),
          _relative_deadline(relative_deadline),
          _remaining(remaining),
          _schedule_count(0) {
        insertCode("fixed(1,control);");
    }

    void schedule() override {
        state = TSK_EXEC;
        ++_schedule_count;
    }

    void deschedule() override { state = TSK_READY; }

    Tick getDeadline() const override { return arrival + _relative_deadline; }
    Tick getRelDline() const override { return _relative_deadline; }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getRemainingWCET(double = 1.0) const override {
        return _remaining;
    }

    int getScheduleCount() const { return _schedule_count; }
    void setRemaining(double remaining) { _remaining = remaining; }

    void releaseAt(Tick tick) {
        arrival = tick;
        lastArrival = tick;
        state = TSK_READY;
    }
};

class ASAPSyncTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit ASAPSyncTestActionEvent(std::function<void()> action)
        : MetaSim::Event("ASAPSyncTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestASAPSyncMRTKernel : public MRTKernel {
public:
    TestASAPSyncMRTKernel(Scheduler *scheduler, const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class ASAPSyncSchedulerTestPeer {
public:
    static void addTaskModel(TestASAPSyncScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model = new ASAPSyncTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(ASAPSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(ASAPSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(ASAPSyncScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._base_harvest_rate = 0.0;
        scheduler._use_real_solar_data = false;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
        scheduler._last_collection_time = MetaSim::SIMUL.getTime();
        scheduler._energy_depleted = false;
        scheduler._batch_scheduled_this_tick = false;
        scheduler._current_batch_tasks.clear();
        scheduler._current_batch_size = 0;
    }

    static void tick(ASAPSyncScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void cancelAutomaticTick(ASAPSyncScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
    }

    static bool isInReadyQueue(ASAPSyncScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static int batchSize(ASAPSyncScheduler &scheduler) {
        return scheduler.calculateBatchSize();
    }
};

static bool ContainsTask(const std::vector<AbsRTTask *> &tasks,
                         AbsRTTask *task) {
    return std::find(tasks.begin(), tasks.end(), task) != tasks.end();
}

TEST(ASAPSyncScheduler, SyncBatchInsufficientEnergyStartsNone) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-insufficient-cpu0", nullptr);
    CPU cpu1("asap-sync-insufficient-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask first(1, 10, 10, 1.0);
    FakeASAPSyncTask second(2, 20, 20, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, ExactBatchEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-exact-cpu0", nullptr);
    CPU cpu1("asap-sync-exact-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask first(1, 10, 10, 1.0);
    FakeASAPSyncTask second(2, 20, 20, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, NoPartialBatchExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-all-or-none-cpu0", nullptr);
    CPU cpu1("asap-sync-all-or-none-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask first(1, 10, 10, 1.0);
    FakeASAPSyncTask second(2, 20, 20, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    const bool first_ran = first.getScheduleCount() > 0;
    const bool second_ran = second.getScheduleCount() > 0;
    EXPECT_EQ(first_ran, second_ran);
    EXPECT_TRUE(first_ran);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, RunningTaskMustBeInFrozenBatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-running-selected-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask low(2, 20, 20, 1.0);
    FakeASAPSyncTask high(1, 5, 5, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low.releaseAt(Tick(0));
    high.releaseAt(Tick(0));
    low.schedule();
    kernel.setRunning(&cpu, &low);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &high);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_EQ(kernel.getTask(&cpu), &high);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler,
     InsufficientIdleCoreBatchPreservesAffordableContinuation) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-running-partial-cpu0", nullptr);
    CPU cpu1("asap-sync-running-partial-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask running(2, 20, 20, 1.0);
    FakeASAPSyncTask ready(1, 5, 5, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 20, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &ready, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.5);
    running.releaseAt(Tick(0));
    ready.releaseAt(Tick(0));
    running.schedule();
    kernel.setRunning(&cpu0, &running);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &ready);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_TRUE(running.isExecuting());
    EXPECT_EQ(ready.getScheduleCount(), 0);
    EXPECT_EQ(scheduler.getCurrentBatchTasks(),
              (std::vector<AbsRTTask *>{&running}));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, BatchSizeUsesIdleCoresNotTotalCores) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-idle-size-cpu0", nullptr);
    CPU cpu1("asap-sync-idle-size-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask running(1, 5, 10, 2.0);
    FakeASAPSyncTask next(2, 10, 10, 1.0);
    FakeASAPSyncTask waiting(3, 20, 20, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 5, 2, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &next, 10, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &waiting, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    running.releaseAt(Tick(0));
    next.releaseAt(Tick(0));
    waiting.releaseAt(Tick(0));
    running.schedule();
    kernel.setRunning(&cpu0, &running);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &next);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &waiting);

    EXPECT_EQ(ASAPSyncSchedulerTestPeer::batchSize(scheduler), 1);
    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_TRUE(running.isExecuting());
    EXPECT_EQ(next.getScheduleCount(), 1);
    EXPECT_EQ(waiting.getScheduleCount(), 0);
    EXPECT_EQ(scheduler.getCurrentBatchSize(), 2);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &running));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &next));

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, NoFreeFirstTick) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-no-free-first-tick-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask task(1, 10, 10, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-stale-dispatch-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask low(2, 20, 20, 1.0);
    FakeASAPSyncTask high(1, 5, 5, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &low);
    ASAPSyncSchedulerTestPeer::tick(scheduler);

    ASAPSyncTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        ASAPSyncSchedulerTestPeer::arrive(scheduler, &high);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
    });
    release_high.post(Tick(1));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_TRUE(high.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &high);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler,
     PreemptedJobRequeuesAndResumesWhenEnergySufficient) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-resume-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask low(2, 20, 20, 5.0);
    FakeASAPSyncTask high(1, 5, 5, 1.0, 1);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 5, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 10.0);
    low.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &low);
    ASAPSyncSchedulerTestPeer::tick(scheduler);

    ASAPSyncTestActionEvent preempt([&]() {
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        ASAPSyncSchedulerTestPeer::arrive(scheduler, &high);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
    });
    preempt.post(Tick(1));
    ASAPSyncTestActionEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
    });
    resume.post(Tick(2));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 2);
    EXPECT_TRUE(low.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &low);
    EXPECT_DOUBLE_EQ(low.getRemainingWCET(), 4.0);
    EXPECT_EQ(low.getDeadline(), Tick(20));
    EXPECT_EQ(low.getArrival(), Tick(0));

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-tie-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask task2(2, 10, 10, 1.0);
    FakeASAPSyncTask task1(1, 10, 10, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task2, 10, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task1, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &task2);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &task1);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);
    EXPECT_EQ(kernel.getTask(&cpu), &task1);

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, BatchBuiltFromActiveCandidates) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu0("asap-sync-active-batch-cpu0", nullptr);
    CPU cpu1("asap-sync-active-batch-cpu1", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPSyncTask low_running(3, 30, 30, 1.0);
    FakeASAPSyncTask high_a(1, 5, 5, 1.0);
    FakeASAPSyncTask high_b(2, 10, 10, 1.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &low_running, 30, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high_a, 5, 1, 1.0);
    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high_b, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low_running.releaseAt(Tick(0));
    high_a.releaseAt(Tick(0));
    high_b.releaseAt(Tick(0));
    low_running.schedule();
    kernel.setRunning(&cpu0, &low_running);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &high_a);
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &high_b);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(low_running.isExecuting());
    EXPECT_EQ(high_a.getScheduleCount(), 1);
    EXPECT_EQ(high_b.getScheduleCount(), 1);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &high_a));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &high_b));
    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &low_running));

    simulation.endSingleRun();
}

TEST(ASAPSyncScheduler, UsesRelativeDeadlineForDeadlineMiss) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPSyncScheduler scheduler;
    CPU cpu("asap-sync-deadline-cpu", nullptr);
    TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPSyncTask task(1, 20, 2, 3.0);

    ASAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 0.0);
    task.releaseAt(Tick(0));
    ASAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPSyncSchedulerTestPeer::tick(scheduler);
    ASAPSyncTestActionEvent late_tick(
        [&scheduler]() { ASAPSyncSchedulerTestPeer::tick(scheduler); });
    late_tick.post(Tick(3));
    simulation.run_to(Tick(3));

    EXPECT_FALSE(ASAPSyncSchedulerTestPeer::isInReadyQueue(scheduler, &task));

    simulation.endSingleRun();
}

}  // namespace RTSim
