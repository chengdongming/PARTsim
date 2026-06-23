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
#include <rtsim/scheduler/gpfp_alap_block_scheduler.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

class FakeALAPBlockTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeALAPBlockTask(int task_number,
                      int period,
                      int relative_deadline,
                      double remaining,
                      int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeALAPBlockTask" + std::to_string(task_number),
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

class ALAPBlockTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit ALAPBlockTestActionEvent(std::function<void()> action)
        : MetaSim::Event("ALAPBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class ALAPBlockSchedulerTestPeer {
public:
    static void addTaskModel(ALAPBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model =
            new ALAPBlockTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(ALAPBlockScheduler &scheduler,
                        AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(ALAPBlockScheduler &scheduler,
                       AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(ALAPBlockScheduler &scheduler,
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

    static void tick(ALAPBlockScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void cancelAutomaticTick(ALAPBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
    }
};

TEST(ALAPBlockScheduler, UsesRelativeDeadlineNotPeriod) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-deadline-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask task(1, 20, 10, 3.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 10.0);
    task.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    std::vector<std::unique_ptr<ALAPBlockTestActionEvent>>
        tick_events;
    for (int tick = 1; tick <= 9; ++tick) {
        tick_events.push_back(
            std::make_unique<ALAPBlockTestActionEvent>(
                [&scheduler]() {
                    ALAPBlockSchedulerTestPeer::tick(scheduler);
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

TEST(ALAPBlockScheduler,
     PositiveSlackHighPriorityDoesNotBlockUrgentLowerPriority) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-positive-slack-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask high(1, 5, 10, 1.0);
    FakeALAPBlockTask low(2, 20, 1, 1.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ALAPBlockScheduler,
     CandidateHighPriorityEnergyShortageBlocksLowerPriority) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-block-wall-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask high(1, 5, 1, 1.0);
    FakeALAPBlockTask low(2, 20, 1, 1.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 2.0);
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 1, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPBlockScheduler, CumulativePrefixEnergyReservation) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu0("alap-block-prefix-cpu0", nullptr);
    CPU cpu1("alap-block-prefix-cpu1", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeALAPBlockTask high(1, 5, 5, 5.0);
    FakeALAPBlockTask low(2, 20, 20, 20.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 5, 0.75);
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 20, 0.75);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    high.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &high);
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &low);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.25);
    EXPECT_GE(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(ALAPBlockScheduler, ExactEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-exact-energy-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask task(1, 1, 1, 1.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 1, 1, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPBlockScheduler, PreserveResidualEnergy) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-residual-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask task(1, 1, 1, 1.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task, 1, 1, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.5);
    task.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &task);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_TRUE(task.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);

    simulation.endSingleRun();
}

TEST(ALAPBlockScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-stale-dispatch-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask low(2, 20, 20, 20.0);
    FakeALAPBlockTask high(1, 5, 1, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &low, 20, 20, 1.0);
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &low);
    ALAPBlockSchedulerTestPeer::tick(scheduler);

    ALAPBlockTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        ALAPBlockSchedulerTestPeer::arrive(scheduler, &high);
        ALAPBlockSchedulerTestPeer::tick(scheduler);
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

TEST(ALAPBlockScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ALAPBlockScheduler scheduler;
    CPU cpu("alap-block-tie-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeALAPBlockTask task2(2, 10, 10, 10.0);
    FakeALAPBlockTask task1(1, 10, 10, 10.0);

    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task2, 10, 10, 1.0);
    ALAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &task1, 10, 10, 1.0);

    simulation.initSingleRun();
    ALAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);
    ALAPBlockSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &task2);
    ALAPBlockSchedulerTestPeer::enqueue(scheduler, &task1);

    ALAPBlockSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);

    simulation.endSingleRun();
}

} // namespace RTSim
