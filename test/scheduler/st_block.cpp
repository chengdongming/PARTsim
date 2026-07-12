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
#include <rtsim/scheduler/gpfp_st_block_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestSTBlockScheduler : public STBlockScheduler {
public:
    using Scheduler::enqueueModel;
    std::size_t baseQueueSize() const { return _queue.size(); }
    TaskModel *baseModel(AbsRTTask *task) const { return find(task); }
};

class FakeSTBlockTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeSTBlockTask(int task_number,
                    int period,
                    int relative_deadline,
                    double remaining,
                    int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeSTBlockTask" + std::to_string(task_number),
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

class STBlockTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit STBlockTestActionEvent(std::function<void()> action)
        : MetaSim::Event("STBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestSTBlockMRTKernel : public MRTKernel {
public:
    TestSTBlockMRTKernel(Scheduler *scheduler, const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class STBlockSchedulerTestPeer {
public:
    static void addTaskModel(TestSTBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model = new STBlockTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(STBlockScheduler &scheduler, AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void setEnergy(STBlockScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._base_harvest_rate = 0.0;
        scheduler._use_real_solar_data = false;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
        scheduler._last_collection_time = MetaSim::SIMUL.getTime();
        scheduler._energy_depleted = false;
        scheduler._alap_blocking = false;
        scheduler._deep_charging = false;
        scheduler._is_charging_sleep = false;
        scheduler._dispatching_tasks_total_energy = 0.0;
        scheduler._counted_tasks_in_dispatch.clear();
        scheduler._dispatch_selection_order.clear();
        scheduler._energy_deducted_tasks.clear();
        if (scheduler._wake_event) {
            scheduler._wake_event->drop();
            delete scheduler._wake_event;
            scheduler._wake_event = nullptr;
        }
    }

    static void tick(STBlockScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static Tick slack(STBlockScheduler &scheduler, AbsRTTask *task) {
        return scheduler.calculateSlackForTask(task);
    }

    static void cleanup(STBlockScheduler &scheduler) {
        scheduler.cleanupExpiredTasks();
    }

    static int deadlineMisses(STBlockScheduler &scheduler) {
        return scheduler._stats.total_deadline_misses;
    }

    static AbsRTTask *selectSlot(STBlockScheduler &scheduler,
                                 unsigned int slot) {
        return scheduler.getTaskN(slot);
    }

    static bool isInReadyQueue(STBlockScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static void cancelAutomaticTick(STBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
        if (scheduler._wake_event) {
            scheduler._wake_event->drop();
        }
    }
};

TEST(STBlockScheduler, EnergyAvailableRunsBeforeSlackZero) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-asap-when-energy-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 100, 100, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 100, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 4.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, UsesRelativeDeadlineForSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    FakeSTBlockTask task(1, 20, 10, 3.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(7));

    EXPECT_EQ(static_cast<int64_t>(STBlockSchedulerTestPeer::slack(
                  scheduler, &task)),
              0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, UsesCeilRemainingExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    FakeSTBlockTask task(1, 10, 10, 1.2);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 10, 2, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(8));

    // deadline 10 - current 8 - ceil(1.2) == 0.
    EXPECT_EQ(static_cast<int64_t>(STBlockSchedulerTestPeer::slack(
                  scheduler, &task)),
              0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, HighPriorityEnergyShortageBlocksLowerPriority) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-energy-wall-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask high(1, 5, 5, 1.0);
    FakeSTBlockTask low(2, 20, 20, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 2.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler,
     HighPriorityEnergyShortageWithSlackWaitsAndDoesNotBypass) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-slack-wait-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask high(1, 5, 10, 1.0);
    FakeSTBlockTask low(2, 20, 20, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 2.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, HighPriorityEnergyShortageWithoutSlackStillBlocks) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-no-slack-wall-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask high(1, 5, 1, 1.0);
    FakeSTBlockTask low(2, 20, 20, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 2.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, CumulativePrefixEnergyReservation) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu0("st-block-prefix-cpu0", nullptr);
    CPU cpu1("st-block-prefix-cpu1", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTBlockTask first(1, 5, 5, 1.0);
    FakeSTBlockTask second(2, 10, 10, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &first, 5, 1, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &second, 10, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &first);
    STBlockSchedulerTestPeer::enqueue(scheduler, &second);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.5);

    simulation.endSingleRun();
}

TEST(STBlockScheduler,
     ChargingSleepDoesNotLetAffordablePrefixRunForFreeAcrossTicks) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu0("st-block-charging-prefix-cpu0", nullptr);
    CPU cpu1("st-block-charging-prefix-cpu1", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTBlockTask affordable(1, 5, 10, 2.0);
    FakeSTBlockTask blocked(2, 10, 10, 2.0);

    STBlockSchedulerTestPeer::addTaskModel(
        scheduler, &affordable, 5, 2, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(
        scheduler, &blocked, 10, 2, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    affordable.releaseAt(Tick(0));
    blocked.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &affordable);
    STBlockSchedulerTestPeer::enqueue(scheduler, &blocked);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));
    ASSERT_FALSE(affordable.isExecuting());
    ASSERT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 0.0);
    ASSERT_TRUE(scheduler.isChargingSleepActive());

    STBlockTestActionEvent next_tick([&]() {
        scheduler._current_energy = 1.5;
        STBlockSchedulerTestPeer::tick(scheduler);
    });
    next_tick.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_FALSE(affordable.isExecuting());
    EXPECT_EQ(blocked.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 0.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.5);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, ChargingSleepReleasesWhenBatteryFull) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-full-release-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 5, 10, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 5, 1, 2.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    scheduler._max_energy = 2.0;
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STBlockSchedulerTestPeer::tick(scheduler);
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    ASSERT_EQ(task.getScheduleCount(), 0);

    STBlockTestActionEvent full([&]() {
        scheduler._current_energy = 2.0;
        STBlockSchedulerTestPeer::tick(scheduler);
    });
    full.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, ChargingSleepReleasesWhenSlackExhausted) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-slack-release-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask high(1, 5, 2, 1.0);
    FakeSTBlockTask low(2, 20, 20, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 2.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &high);
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);

    STBlockSchedulerTestPeer::tick(scheduler);
    ASSERT_TRUE(scheduler.isChargingSleepActive());

    STBlockTestActionEvent slack_exhausted([&]() {
        STBlockSchedulerTestPeer::tick(scheduler);
    });
    slack_exhausted.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, ExactEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-exact-energy-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 5, 5, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, NoFreeFirstTick) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-no-free-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 5, 5, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, PreserveResidualEnergy) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-residual-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 5, 5, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 1.25);
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.25);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, RunningTaskMustBeInFrozenSelected) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-running-selection-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask running_low(2, 20, 20, 5.0);
    FakeSTBlockTask ready_high(1, 5, 5, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(
        scheduler, &running_low, 20, 5, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(
        scheduler, &ready_high, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 2.0);
    running_low.releaseAt(Tick(0));
    ready_high.releaseAt(Tick(0));
    running_low.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu, &running_low);
    STBlockSchedulerTestPeer::enqueue(scheduler, &ready_high);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(ready_high.getScheduleCount(), 1);
    EXPECT_FALSE(running_low.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    FakeSTBlockTask low(2, 20, 20, 1.0);
    FakeSTBlockTask high(1, 5, 5, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low.releaseAt(Tick(0));
    high.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);

    EXPECT_EQ(STBlockSchedulerTestPeer::selectSlot(scheduler, 0), &low);

    STBlockSchedulerTestPeer::enqueue(scheduler, &high);

    EXPECT_EQ(STBlockSchedulerTestPeer::selectSlot(scheduler, 0), &high);

    simulation.endSingleRun();
}

TEST(STBlockScheduler,
     PreemptedJobRequeuesAndResumesWhenEnergySufficient) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-resume-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask low(2, 20, 20, 5.0);
    FakeSTBlockTask high(1, 5, 5, 1.0, 1);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 5, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 10.0);
    low.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &low);
    STBlockSchedulerTestPeer::tick(scheduler);

    STBlockTestActionEvent preempt([&]() {
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        STBlockSchedulerTestPeer::enqueue(scheduler, &high);
        STBlockSchedulerTestPeer::tick(scheduler);
    });
    preempt.post(Tick(1));
    STBlockTestActionEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        STBlockSchedulerTestPeer::tick(scheduler);
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

TEST(STBlockScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-tie-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task2(2, 10, 10, 1.0);
    FakeSTBlockTask task1(1, 10, 10, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task2, 10, 1, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task1, 10, 1, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task2);
    STBlockSchedulerTestPeer::enqueue(scheduler, &task1);

    STBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);

    simulation.endSingleRun();
}

TEST(STBlockScheduler, DeadlineMissKeepsJobAndUsesRelativeDeadline) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    FakeSTBlockTask task(1, 20, 5, 10.0);

    STBlockSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 10, 1.0);

    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    STBlockSchedulerTestPeer::enqueue(scheduler, &task);
    simulation.run_to(Tick(6));

    STBlockSchedulerTestPeer::cleanup(scheduler);
    STBlockSchedulerTestPeer::cleanup(scheduler);

    EXPECT_TRUE(STBlockSchedulerTestPeer::isInReadyQueue(scheduler, &task));
    EXPECT_EQ(STBlockSchedulerTestPeer::deadlineMisses(scheduler), 1);
    EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 10.0);
    EXPECT_EQ(task.getDeadline(), Tick(5));

    simulation.endSingleRun();
}

TEST(STBlockScheduler, NewRunClearsChargingHoldAndMatchesFreshRunState) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTBlockScheduler scheduler;
    CPU cpu("st-block-new-run-cpu", nullptr);
    TestSTBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTBlockTask task(1, 100, 100, 1.0);

    STBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 100, 1, 1.0);
    simulation.initSingleRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);

    STBlockSchedulerTestPeer::setEnergy(scheduler, 0.5);
    task.releaseAt(Tick(0));
    scheduler.insert(&task);
    ASSERT_EQ(scheduler.baseQueueSize(), 1u);
    ASSERT_TRUE(scheduler.baseModel(&task)->isActive());
    STBlockSchedulerTestPeer::tick(scheduler);
    ASSERT_TRUE(scheduler._is_charging_sleep);
    ASSERT_EQ(scheduler._st_charge_blocked_task, &task);
    ASSERT_NE(scheduler._wake_event, nullptr);

    scheduler.newRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    EXPECT_FALSE(scheduler._is_charging_sleep);
    EXPECT_FALSE(scheduler._deep_charging);
    EXPECT_EQ(scheduler._st_charge_blocked_task, nullptr);
    EXPECT_DOUBLE_EQ(scheduler._st_charge_required_energy, 0.0);
    EXPECT_EQ(scheduler._st_charge_slack_at_begin, Tick(0));
    EXPECT_EQ(scheduler._wake_event, nullptr);
    EXPECT_TRUE(scheduler._dispatch_selection_order.empty());
    EXPECT_FALSE(scheduler._selection_frozen);
    EXPECT_FALSE(scheduler._energy_commit_valid);
    EXPECT_EQ(scheduler.baseQueueSize(), 0u);
    ASSERT_NE(scheduler.baseModel(&task), nullptr);
    EXPECT_FALSE(scheduler.baseModel(&task)->isActive());

    TestSTBlockScheduler fresh;
    CPU fresh_cpu("st-block-fresh-run-cpu", nullptr);
    TestSTBlockMRTKernel fresh_kernel(
        &fresh, std::set<CPU *>{&fresh_cpu});
    FakeSTBlockTask fresh_task(2, 100, 100, 1.0);
    STBlockSchedulerTestPeer::addTaskModel(
        fresh, &fresh_task, 100, 1, 1.0);
    fresh.newRun();
    STBlockSchedulerTestPeer::cancelAutomaticTick(fresh);

    STBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    STBlockSchedulerTestPeer::setEnergy(fresh, 5.0);
    task.releaseAt(Tick(0));
    fresh_task.releaseAt(Tick(0));
    scheduler.insert(&task);
    fresh.insert(&fresh_task);
    STBlockSchedulerTestPeer::tick(scheduler);
    STBlockSchedulerTestPeer::tick(fresh);
    simulation.run_to(Tick(0));
    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_EQ(fresh_task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(
        scheduler.getCurrentEnergy(), fresh.getCurrentEnergy());
    EXPECT_EQ(scheduler.baseQueueSize(), fresh.baseQueueSize());

    simulation.endSingleRun();
}

} // namespace RTSim
