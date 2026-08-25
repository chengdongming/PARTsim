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
#include <rtsim/mrtkernel.hpp>
#include <rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

class FakeALAPNonBlockTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeALAPNonBlockTask(int task_number,
                         int period,
                         int relative_deadline,
                         double remaining,
                         int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeALAPNonBlockTask" + std::to_string(task_number),
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
    Tick getDeadline() const override {
        return arrival + _relative_deadline;
    }
    Tick getRelDline() const override {
        return _relative_deadline;
    }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getRemainingWCET(double = 1.0) const override {
        return _remaining;
    }

    int getScheduleCount() const { return _schedule_count; }

    void releaseAt(Tick tick) {
        arrival = tick;
        lastArrival = tick;
        state = TSK_READY;
    }
};

class ALAPNonBlockTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit ALAPNonBlockTestActionEvent(std::function<void()> action)
        : MetaSim::Event("ALAPNonBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class ALAPNonBlockSchedulerTestPeer {
public:
    static void addTaskModel(ALAPNonBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model =
            new ALAPNonBlockTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(ALAPNonBlockScheduler &scheduler,
                        AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(ALAPNonBlockScheduler &scheduler,
                       AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(ALAPNonBlockScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._energy_depleted = false;
    }

    static void tick(ALAPNonBlockScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void cleanup(ALAPNonBlockScheduler &scheduler) {
        scheduler.cleanupExpiredTasks();
    }

    static bool isInReadyQueue(ALAPNonBlockScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static void cancelAutomaticTick(ALAPNonBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
    }

    static std::vector<AbsRTTask *> candidates(
        ALAPNonBlockScheduler &scheduler,
        const std::vector<AbsRTTask *> &active_tasks,
        Tick current_time) {
        return scheduler.collectALAPCandidates(active_tasks, current_time);
    }

    static void bindKernel(ALAPNonBlockScheduler &scheduler, MRTKernel *kernel) {
        scheduler._kernel = kernel;
    }
};

TEST(ALAPNonBlockScheduler, ActiveHigherPriorityReserveGate) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu0("alap-nonblock-reserve-cpu0", nullptr);
    CPU cpu1("alap-nonblock-reserve-cpu1", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});

    FakeALAPNonBlockTask no_hp_positive(1, 20, 10, 3.0);
    FakeALAPNonBlockTask no_hp_zero(2, 30, 2, 2.0);
    FakeALAPNonBlockTask one_hp_target(3, 20, 3, 2.0);
    FakeALAPNonBlockTask one_hp(4, 5, 10, 2.0);
    FakeALAPNonBlockTask two_hp_target(5, 30, 4, 2.0);
    FakeALAPNonBlockTask two_hp_a(6, 5, 10, 2.0);
    FakeALAPNonBlockTask two_hp_b(7, 10, 10, 2.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &no_hp_positive, 20, 3, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &no_hp_zero, 30, 2, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &one_hp_target, 20, 2, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &one_hp, 5, 2, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &two_hp_target, 30, 2, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &two_hp_a, 5, 2, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &two_hp_b, 10, 2, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::bindKernel(scheduler, &kernel);

    auto candidates = ALAPNonBlockSchedulerTestPeer::candidates(
        scheduler, {&no_hp_positive}, Tick(0));
    EXPECT_TRUE(candidates.empty());

    candidates = ALAPNonBlockSchedulerTestPeer::candidates(
        scheduler, {&no_hp_zero}, Tick(0));
    ASSERT_EQ(candidates.size(), 1U);
    EXPECT_EQ(candidates[0], &no_hp_zero);

    candidates = ALAPNonBlockSchedulerTestPeer::candidates(
        scheduler, {&one_hp, &one_hp_target}, Tick(0));
    EXPECT_TRUE(candidates.empty());

    candidates = ALAPNonBlockSchedulerTestPeer::candidates(
        scheduler, {&two_hp_a, &two_hp_b, &two_hp_target}, Tick(0));
    ASSERT_EQ(candidates.size(), 1U);
    EXPECT_EQ(candidates[0], &two_hp_target);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, UsesRelativeDeadlineNotPeriod) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-deadline-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task(1, 20, 10, 3.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 10.0);
    task.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    std::vector<std::unique_ptr<ALAPNonBlockTestActionEvent>>
        tick_events;
    for (int tick = 1; tick <= 9; ++tick) {
        tick_events.push_back(
            std::make_unique<ALAPNonBlockTestActionEvent>(
                [&scheduler]() {
                    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
                }));
        tick_events.back()->post(Tick(tick));
    }
    simulation.run_to(Tick(6));
    EXPECT_EQ(task.getScheduleCount(), 0);

    simulation.run_to(Tick(7));
    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());

    simulation.run_to(Tick(9));
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 3.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 7.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, DoesNotRunBeforeSlackZero) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-no-early-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task(1, 10, 10, 2.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 10, 2, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 5.0);
    task.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 0);
    EXPECT_FALSE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 5.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, CleanupUsesAbsoluteDeadline) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-cleanup-deadline-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task(1, 100, 10, 1.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 100, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPNonBlockTestActionEvent cleanup_at_deadline([&]() {
        ALAPNonBlockSchedulerTestPeer::cleanup(scheduler);
    });
    cleanup_at_deadline.post(Tick(10));
    simulation.run_to(Tick(10));

    EXPECT_FALSE(
        ALAPNonBlockSchedulerTestPeer::isInReadyQueue(scheduler, &task));

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler,
     PositiveSlackHighPriorityDoesNotBlockUrgentLowerPriority) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-positive-slack-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask high(1, 5, 10, 1.0);
    FakeALAPNonBlockTask low(2, 20, 1, 1.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler,
     CandidateHighPriorityEnergyShortageAllowsLowerPriorityBypass) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-bypass-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask high(1, 5, 1, 1.0);
    FakeALAPNonBlockTask low(2, 20, 1, 1.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, ExactEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-exact-energy-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task(1, 1, 1, 1.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 1, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, PreserveResidualEnergy) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-residual-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task(1, 1, 1, 1.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 1, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    task.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPNonBlockScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-stale-dispatch-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask low(2, 20, 20, 20.0);
    FakeALAPNonBlockTask high(1, 5, 1, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 20, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &low);
    ALAPNonBlockSchedulerTestPeer::tick(scheduler);

    ALAPNonBlockTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        ALAPNonBlockSchedulerTestPeer::arrive(scheduler, &high);
        ALAPNonBlockSchedulerTestPeer::tick(scheduler);
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

TEST(ALAPNonBlockScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPNonBlockScheduler scheduler;
    CPU cpu("alap-nonblock-tie-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPNonBlockTask task2(2, 10, 10, 10.0);
    FakeALAPNonBlockTask task1(1, 10, 10, 10.0);

    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task2, 10, 10, 1.0);
    ALAPNonBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task1, 10, 10, 1.0);

    simulation.initSingleRun();
    ALAPNonBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPNonBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task2);
    ALAPNonBlockSchedulerTestPeer::enqueue(scheduler, &task1);

    ALAPNonBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);

    simulation.endSingleRun();
}

} // namespace RTSim
