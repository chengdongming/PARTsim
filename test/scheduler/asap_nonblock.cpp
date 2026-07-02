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
#include <rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestASAPNonBlockScheduler : public ASAPNonBlockScheduler {
public:
    using Scheduler::enqueueModel;
};

class FakeASAPNonBlockTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeASAPNonBlockTask(int task_number,
                         int period,
                         int relative_deadline,
                         double remaining,
                         int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeASAPNonBlockTask" + std::to_string(task_number),
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

class ASAPNonBlockTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit ASAPNonBlockTestActionEvent(std::function<void()> action)
        : MetaSim::Event("ASAPNonBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestASAPNonBlockMRTKernel : public MRTKernel {
public:
    TestASAPNonBlockMRTKernel(Scheduler *scheduler,
                              const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class ASAPNonBlockSchedulerTestPeer {
public:
    static void addTaskModel(TestASAPNonBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model =
            new ASAPNonBlockTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(ASAPNonBlockScheduler &scheduler,
                        AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(ASAPNonBlockScheduler &scheduler,
                       AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(ASAPNonBlockScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._base_harvest_rate = 0.0;
        scheduler._use_real_solar_data = false;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
        scheduler._last_collection_time = MetaSim::SIMUL.getTime();
        scheduler._energy_depleted = false;
        scheduler._dispatching_tasks_total_energy = 0.0;
        scheduler._counted_tasks_in_dispatch.clear();
        scheduler._dispatch_selection_order.clear();
        scheduler._energy_deducted_tasks.clear();
        scheduler._energy_blocked_tasks.clear();
    }

    static void tick(ASAPNonBlockScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void cancelAutomaticTick(ASAPNonBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
    }

    static bool isInReadyQueue(ASAPNonBlockScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static AbsRTTask *chargeTarget(ASAPNonBlockScheduler &scheduler) {
        return scheduler._highest_priority_energy_blocked_task;
    }

    static void recharge(ASAPNonBlockScheduler &scheduler, double energy) {
        scheduler._current_energy = energy;
    }
};

static bool ContainsTask(const std::vector<AbsRTTask *> &tasks,
                         AbsRTTask *task) {
    return std::find(tasks.begin(), tasks.end(), task) != tasks.end();
}

TEST(ASAPNonBlockScheduler, NoAlapSlackGate) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-no-alap-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask task(1, 100, 100, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 100, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, NonBlockBypassAllowed) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-bypass-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask high(1, 5, 5, 1.0);
    FakeASAPNonBlockTask low(2, 20, 20, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, HighestAffordableLowerPrioritySelected) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-third-affordable-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask first(1, 5, 5, 1.0);
    FakeASAPNonBlockTask second(2, 10, 10, 1.0);
    FakeASAPNonBlockTask third(3, 20, 20, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &first, 5, 1, 3.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &second, 10, 1, 2.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &third, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    third.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &second);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &third);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_EQ(third.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler,
     ChargeTargetAfterFullScanIsHighestPriorityEnergyBlockedJob) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-charge-target-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask high(1, 5, 10, 1.0);
    FakeASAPNonBlockTask low(2, 20, 20, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    ASSERT_EQ(low.getScheduleCount(), 1);
    ASSERT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(ASAPNonBlockSchedulerTestPeer::chargeTarget(scheduler),
              &high);
    EXPECT_TRUE(ASAPNonBlockSchedulerTestPeer::isInReadyQueue(
        scheduler, &high));

    ASAPNonBlockTestActionEvent next_tick([&]() {
        ASAPNonBlockSchedulerTestPeer::recharge(scheduler, 2.0);
        ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    });
    next_tick.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_TRUE(high.isExecuting());
    EXPECT_FALSE(low.isExecuting());

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, EnergyReservationPreventsOversubscription) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu0("asap-nonblock-reserve-cpu0", nullptr);
    CPU cpu1("asap-nonblock-reserve-cpu1", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPNonBlockTask first(1, 10, 10, 1.0);
    FakeASAPNonBlockTask second(2, 20, 20, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &first, 10, 1, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &second);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    const int scheduled = first.getScheduleCount() + second.getScheduleCount();
    EXPECT_EQ(scheduled, 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, ExactEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu0("asap-nonblock-exact-cpu0", nullptr);
    CPU cpu1("asap-nonblock-exact-cpu1", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeASAPNonBlockTask first(1, 10, 10, 1.0);
    FakeASAPNonBlockTask second(2, 20, 20, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &first, 10, 1, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 2.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &first);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &second);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, NoFreeFirstTick) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-no-free-first-tick-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask task(1, 10, 10, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, PreserveResidualEnergy) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-residual-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask task(1, 10, 10, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    task.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, RunningTaskMustBeInFrozenSelected) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-running-selected-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask low(2, 20, 20, 1.0);
    FakeASAPNonBlockTask high(1, 5, 5, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low.releaseAt(Tick(0));
    high.releaseAt(Tick(0));
    low.schedule();
    kernel.setRunning(&cpu, &low);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &high);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_EQ(kernel.getTask(&cpu), &high);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-stale-dispatch-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask low(2, 20, 20, 1.0);
    FakeASAPNonBlockTask high(1, 5, 5, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);
    ASAPNonBlockSchedulerTestPeer::tick(scheduler);

    ASAPNonBlockTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        ASAPNonBlockSchedulerTestPeer::arrive(scheduler, &high);
        ASAPNonBlockSchedulerTestPeer::tick(scheduler);
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

TEST(ASAPNonBlockScheduler,
     PreemptedJobRequeuesAndResumesWhenEnergySufficient) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-resume-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask low(2, 20, 20, 5.0);
    FakeASAPNonBlockTask high(1, 5, 5, 1.0, 1);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 5, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 10.0);
    low.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);
    ASAPNonBlockSchedulerTestPeer::tick(scheduler);

    ASAPNonBlockTestActionEvent preempt([&]() {
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        ASAPNonBlockSchedulerTestPeer::arrive(scheduler, &high);
        ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    });
    preempt.post(Tick(1));
    ASAPNonBlockTestActionEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        ASAPNonBlockSchedulerTestPeer::tick(scheduler);
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

TEST(ASAPNonBlockScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-tie-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask task2(2, 10, 10, 1.0);
    FakeASAPNonBlockTask task1(1, 10, 10, 1.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task2, 10, 1, 1.0);
    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task1, 10, 1, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task2);
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task1);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);
    EXPECT_EQ(kernel.getTask(&cpu), &task1);

    simulation.endSingleRun();
}

TEST(ASAPNonBlockScheduler, UsesRelativeDeadlineForDeadlineMiss) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestASAPNonBlockScheduler scheduler;
    CPU cpu("asap-nonblock-deadline-cpu", nullptr);
    TestASAPNonBlockMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeASAPNonBlockTask task(1, 20, 2, 3.0);

    ASAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ASAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ASAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 0.0);
    task.releaseAt(Tick(0));
    ASAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ASAPNonBlockSchedulerTestPeer::tick(scheduler);
    ASAPNonBlockTestActionEvent late_tick(
        [&scheduler]() { ASAPNonBlockSchedulerTestPeer::tick(scheduler); });
    late_tick.post(Tick(3));
    simulation.run_to(Tick(3));

    EXPECT_FALSE(
        ASAPNonBlockSchedulerTestPeer::isInReadyQueue(scheduler, &task));

    simulation.endSingleRun();
}

}  // namespace RTSim
