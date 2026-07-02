#include <algorithm>
#include <cmath>
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
#include <rtsim/scheduler/gpfp_alap_sync_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestALAPSyncScheduler : public ALAPSyncScheduler {
public:
    using Scheduler::enqueueModel;
};

class FakeALAPSyncTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeALAPSyncTask(int task_number,
                     int period,
                     int relative_deadline,
                     double remaining,
                     int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeALAPSyncTask" + std::to_string(task_number),
               1000,
               Tick(static_cast<Tick::impl_t>(std::ceil(remaining)))),
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

class ALAPSyncTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit ALAPSyncTestActionEvent(std::function<void()> action)
        : MetaSim::Event("ALAPSyncTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestMRTKernel : public MRTKernel {
public:
    TestMRTKernel(Scheduler *scheduler, const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class ALAPSyncSchedulerTestPeer {
public:
    static void addTaskModel(TestALAPSyncScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model = new ALAPSyncTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(ALAPSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(ALAPSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(ALAPSyncScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._base_harvest_rate = 0.0;
        scheduler._use_real_solar_data = false;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
        scheduler._last_collection_time = MetaSim::SIMUL.getTime();
        scheduler._energy_depleted = false;
    }

    static void tick(ALAPSyncScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void cleanup(ALAPSyncScheduler &scheduler) {
        scheduler.cleanupExpiredTasks();
    }

    static void cancelAutomaticTick(ALAPSyncScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
        if (scheduler._alap_wake_event) {
            scheduler._alap_wake_event->drop();
        }
    }

    static bool isInReadyQueue(ALAPSyncScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }
};

static bool ContainsTask(const std::vector<AbsRTTask *> &tasks,
                         AbsRTTask *task) {
    return std::find(tasks.begin(), tasks.end(), task) != tasks.end();
}

TEST(ALAPSyncScheduler, UsesRelativeDeadlineNotPeriod) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-deadline-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPSyncTask task(1, 20, 10, 3.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 10.0);
    task.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    std::vector<std::unique_ptr<ALAPSyncTestActionEvent>> tick_events;
    for (int tick = 1; tick <= 7; ++tick) {
        tick_events.push_back(std::make_unique<ALAPSyncTestActionEvent>(
            [&scheduler]() { ALAPSyncSchedulerTestPeer::tick(scheduler); }));
        tick_events.back()->post(Tick(tick));
    }

    simulation.run_to(Tick(6));
    EXPECT_EQ(task.getScheduleCount(), 0);

    simulation.run_to(Tick(7));
    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, DoesNotRunBeforeSlackZero) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-no-early-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPSyncTask task(1, 10, 10, 2.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 10, 2, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 0);
    EXPECT_FALSE(task.isExecuting());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 5.0);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, UsesCeilRemainingExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-ceil-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    // With D=2 and remaining=1.2 at t=0:
    // floor semantics gives slack=1, but ceil semantics gives slack=0.
    FakeALAPSyncTask task(1, 2, 2, 1.2);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 2, 2, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, PositiveSlackRunningTaskDoesNotBypassGate) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-running-gate-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPSyncTask running(1, 5, 10, 1.0);
    FakeALAPSyncTask urgent(2, 20, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 5, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &urgent, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    running.releaseAt(Tick(0));
    urgent.releaseAt(Tick(0));
    running.schedule();
    kernel.setRunning(&cpu, &running);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &urgent);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &running));
    EXPECT_EQ(urgent.getScheduleCount(), 1);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, UrgentCandidatesFormAtomicBatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu0("alap-sync-batch-cpu0", nullptr);
    CPU cpu1("alap-sync-batch-cpu1", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPSyncTask running(1, 5, 10, 1.0);
    FakeALAPSyncTask urgent(2, 20, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 5, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &urgent, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    running.releaseAt(Tick(0));
    urgent.releaseAt(Tick(0));
    running.schedule();
    kernel.setRunning(&cpu0, &running);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &urgent);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &running));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &urgent));
    EXPECT_EQ(scheduler.getCurrentBatchSize(), 1);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, BatchInsufficientEnergyStartsNone) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu0("alap-sync-insufficient-cpu0", nullptr);
    CPU cpu1("alap-sync-insufficient-cpu1", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPSyncTask first(1, 10, 1, 1.0);
    FakeALAPSyncTask second(2, 20, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, ExactBatchEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu0("alap-sync-exact-cpu0", nullptr);
    CPU cpu1("alap-sync-exact-cpu1", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPSyncTask first(1, 10, 1, 1.0);
    FakeALAPSyncTask second(2, 20, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, NoPartialBatchExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu0("alap-sync-all-or-none-cpu0", nullptr);
    CPU cpu1("alap-sync-all-or-none-cpu1", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPSyncTask first(1, 10, 1, 1.0);
    FakeALAPSyncTask second(2, 20, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &second);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    const bool first_ran = first.getScheduleCount() > 0;
    const bool second_ran = second.getScheduleCount() > 0;
    EXPECT_EQ(first_ran, second_ran);
    EXPECT_TRUE(first_ran);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, StableRmTieBreakForEqualPeriod) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu0("alap-sync-stable-tie-cpu0", nullptr);
    CPU cpu1("alap-sync-stable-tie-cpu1", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPSyncTask task3(3, 10, 1, 1.0);
    FakeALAPSyncTask task1(1, 10, 1, 1.0);
    FakeALAPSyncTask task2(2, 10, 1, 1.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task3, 10, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task1, 10, 1, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task2, 10, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    task3.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    task2.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task3);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task1);
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task2);

    ALAPSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 1);
    EXPECT_EQ(task3.getScheduleCount(), 0);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &task1));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &task2));
    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &task3));
    EXPECT_EQ(scheduler.getCurrentBatchSize(), 2);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, RealStaleEndDispatchIgnored) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-real-stale-dispatch-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPSyncTask low(2, 20, 20, 20.0);
    FakeALAPSyncTask high(1, 5, 1, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 20, 1.0);
    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &low);
    ALAPSyncSchedulerTestPeer::tick(scheduler);

    ALAPSyncTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        ALAPSyncSchedulerTestPeer::arrive(scheduler, &high);
        ALAPSyncSchedulerTestPeer::tick(scheduler);
    });
    release_high.post(Tick(1));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_TRUE(high.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &high);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);

    simulation.endSingleRun();
}

TEST(ALAPSyncScheduler, CleanupUsesRelativeDeadline) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestALAPSyncScheduler scheduler;
    CPU cpu("alap-sync-cleanup-cpu", nullptr);
    TestMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPSyncTask task(1, 20, 2, 3.0);

    ALAPSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ALAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPSyncSchedulerTestPeer::setEnergy(scheduler, 0.0);
    task.releaseAt(Tick(0));
    ALAPSyncSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPSyncTestActionEvent cleanup_event(
        [&scheduler]() { ALAPSyncSchedulerTestPeer::cleanup(scheduler); });
    cleanup_event.post(Tick(3));
    simulation.run_to(Tick(3));

    EXPECT_FALSE(ALAPSyncSchedulerTestPeer::isInReadyQueue(scheduler, &task));

    simulation.endSingleRun();
}

}  // namespace RTSim
