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
#include <rtsim/scheduler/gpfp_st_nonblock_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestSTNonBlockScheduler : public STNonBlockScheduler {
public:
    using Scheduler::enqueueModel;
};

class FakeSTNonBlockTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeSTNonBlockTask(int task_number,
                       int period,
                       int relative_deadline,
                       double remaining,
                       int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeSTNonBlockTask" + std::to_string(task_number),
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

    void markRunningWithoutScheduleCount() { state = TSK_EXEC; }
};

class STNonBlockTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit STNonBlockTestActionEvent(std::function<void()> action)
        : MetaSim::Event("STNonBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestSTNonBlockMRTKernel : public MRTKernel {
public:
    TestSTNonBlockMRTKernel(Scheduler *scheduler, const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class STNonBlockSchedulerTestPeer {
public:
    static void addTaskModel(TestSTNonBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model =
            new STNonBlockTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(STNonBlockScheduler &scheduler,
                        AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(STNonBlockScheduler &scheduler,
                       AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(STNonBlockScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._base_harvest_rate = 0.0;
        scheduler._use_real_solar_data = false;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
        scheduler._last_collection_time = MetaSim::SIMUL.getTime();
        scheduler._energy_depleted = false;
        scheduler._deep_charging = false;
        scheduler._is_charging_sleep = false;
        scheduler._pending_wake_task = nullptr;
        scheduler._pending_wake_energy = 0.0;
        scheduler._dispatching_tasks_total_energy = 0.0;
        scheduler._counted_tasks_in_dispatch.clear();
        scheduler._dispatch_selection_order.clear();
        scheduler._energy_deducted_tasks.clear();
        scheduler._newly_dispatched_this_tick.clear();
        scheduler._skipped_tasks.clear();
        for (auto &entry : scheduler._skip_wake_events) {
            if (entry.second) {
                entry.second->drop();
                delete entry.second;
            }
        }
        scheduler._skip_wake_events.clear();
    }

    static void tick(STNonBlockScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static Tick slack(STNonBlockScheduler &scheduler, AbsRTTask *task) {
        return scheduler.calculateSlackForTask(task);
    }

    static void cleanup(STNonBlockScheduler &scheduler) {
        scheduler.cleanupExpiredTasks();
    }

    static int deadlineMisses(STNonBlockScheduler &scheduler) {
        return scheduler._stats.total_deadline_misses;
    }

    static AbsRTTask *selectSlot(STNonBlockScheduler &scheduler,
                                 unsigned int slot) {
        return scheduler.getTaskN(slot);
    }

    static bool isInReadyQueue(STNonBlockScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static void cancelAutomaticTick(STNonBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
        for (auto &entry : scheduler._skip_wake_events) {
            if (entry.second) {
                entry.second->drop();
            }
        }
    }
};

TEST(STNonBlockScheduler, EnergyAvailableRunsBeforeSlackZero) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-asap-when-energy-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask task(1, 100, 100, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 100, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 4.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler,
     HighPriorityShortageWithSlackAllowsBypassAndKeepsHighReady) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-slack-wait-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask high(1, 5, 10, 1.0);
    FakeSTNonBlockTask low(2, 20, 20, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_TRUE(low.isExecuting());
    EXPECT_TRUE(STNonBlockSchedulerTestPeer::isInReadyQueue(
        scheduler, &high));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, HighPriorityShortageNoSlackAllowsBypass) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-no-slack-bypass-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask high(1, 5, 1, 1.0);
    FakeSTNonBlockTask low(2, 20, 20, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, UsesRelativeDeadlineForSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    FakeSTNonBlockTask task(1, 20, 10, 3.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(7));

    EXPECT_EQ(static_cast<int64_t>(
                  STNonBlockSchedulerTestPeer::slack(scheduler, &task)),
              0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, UsesCeilRemainingExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    FakeSTNonBlockTask task(1, 10, 10, 1.2);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 2, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(8));

    // deadline 10 - current 8 - ceil(1.2) == 0.
    EXPECT_EQ(static_cast<int64_t>(
                  STNonBlockSchedulerTestPeer::slack(scheduler, &task)),
              0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, DeadlineMissKeepsJobAndUsesRelativeDeadline) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    FakeSTNonBlockTask task(1, 20, 5, 10.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 20, 10, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task);
    simulation.run_to(Tick(6));

    STNonBlockSchedulerTestPeer::cleanup(scheduler);
    STNonBlockSchedulerTestPeer::cleanup(scheduler);

    EXPECT_TRUE(STNonBlockSchedulerTestPeer::isInReadyQueue(
        scheduler, &task));
    EXPECT_EQ(STNonBlockSchedulerTestPeer::deadlineMisses(scheduler), 1);
    EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 10.0);
    EXPECT_EQ(task.getDeadline(), Tick(5));

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, EnergyReservationPreventsOversubscription) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu0("st-nonblock-reserve-cpu0", nullptr);
    CPU cpu1("st-nonblock-reserve-cpu1", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTNonBlockTask first(1, 10, 10, 1.0);
    FakeSTNonBlockTask second(2, 20, 20, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &first, 10, 1, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &first);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &second);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    const int scheduled = first.getScheduleCount() + second.getScheduleCount();
    EXPECT_EQ(scheduled, 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, ExactEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu0("st-nonblock-exact-cpu0", nullptr);
    CPU cpu1("st-nonblock-exact-cpu1", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTNonBlockTask first(1, 10, 10, 1.0);
    FakeSTNonBlockTask second(2, 20, 20, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &first, 10, 1, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 2.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &first);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &second);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, NoFreeFirstTick) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-no-free-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask task(1, 10, 10, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, PreserveResidualEnergy) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-residual-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask task(1, 10, 10, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    task.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, RunningTaskMustBeInFrozenSelected) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-running-selection-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask running_low(2, 20, 20, 5.0);
    FakeSTNonBlockTask ready_high(1, 5, 5, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &running_low, 20, 5, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &ready_high, 5, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 2.0);
    running_low.releaseAt(Tick(0));
    ready_high.releaseAt(Tick(0));
    running_low.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu, &running_low);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &ready_high);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(ready_high.getScheduleCount(), 1);
    EXPECT_FALSE(running_low.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, NoMidTickArrivalPreemptsFrozenSelection) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-mid-tick-arrival-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask low(2, 20, 20, 2.0);
    FakeSTNonBlockTask high(1, 5, 5, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 2, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 2.0);
    low.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    ASSERT_TRUE(low.isExecuting());
    ASSERT_EQ(kernel.getTask(&cpu), &low);
    ASSERT_EQ(STNonBlockSchedulerTestPeer::selectSlot(scheduler, 0), &low);
    ASSERT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    ASSERT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    high.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::arrive(scheduler, &high);

    EXPECT_TRUE(low.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &low);
    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_TRUE(STNonBlockSchedulerTestPeer::isInReadyQueue(scheduler, &high));
    EXPECT_EQ(STNonBlockSchedulerTestPeer::selectSlot(scheduler, 0), &low);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, RealStaleEndDispatchIgnored) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-real-stale-dispatch-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask low(2, 20, 20, 1.0);
    FakeSTNonBlockTask high(1, 5, 5, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);
    STNonBlockSchedulerTestPeer::tick(scheduler);

    STNonBlockTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        STNonBlockSchedulerTestPeer::arrive(scheduler, &high);
        STNonBlockSchedulerTestPeer::tick(scheduler);
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

TEST(STNonBlockScheduler,
     PreemptedJobRequeuesAndResumesWhenEnergySufficient) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-resume-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask low(2, 20, 20, 5.0);
    FakeSTNonBlockTask high(1, 5, 5, 1.0, 1);

    STNonBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 5, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 10.0);
    low.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);
    STNonBlockSchedulerTestPeer::tick(scheduler);

    STNonBlockTestActionEvent preempt([&]() {
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        STNonBlockSchedulerTestPeer::arrive(scheduler, &high);
        STNonBlockSchedulerTestPeer::tick(scheduler);
    });
    preempt.post(Tick(1));
    STNonBlockTestActionEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        STNonBlockSchedulerTestPeer::tick(scheduler);
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

TEST(STNonBlockScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    FakeSTNonBlockTask low(2, 20, 20, 1.0);
    FakeSTNonBlockTask high(1, 5, 5, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low.releaseAt(Tick(0));
    high.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    EXPECT_EQ(STNonBlockSchedulerTestPeer::selectSlot(scheduler, 0), &low);

    STNonBlockSchedulerTestPeer::enqueue(scheduler, &high);

    EXPECT_EQ(STNonBlockSchedulerTestPeer::selectSlot(scheduler, 0), &high);

    simulation.endSingleRun();
}

TEST(STNonBlockScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTNonBlockScheduler scheduler;
    CPU cpu("st-nonblock-tie-cpu", nullptr);
    TestSTNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTNonBlockTask task2(2, 10, 10, 1.0);
    FakeSTNonBlockTask task1(1, 10, 10, 1.0);

    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task2, 10, 1, 1.0);
    STNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task1, 10, 1, 1.0);

    simulation.initSingleRun();
    STNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task2);
    STNonBlockSchedulerTestPeer::enqueue(scheduler, &task1);

    STNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);
    EXPECT_EQ(kernel.getTask(&cpu), &task1);

    simulation.endSingleRun();
}

} // namespace RTSim
