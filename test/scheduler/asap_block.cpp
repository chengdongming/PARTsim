#include <algorithm>
#include <functional>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/scheduler/gpfp_asap_block_scheduler.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

class FakeASAPBlockTask : public AbsRTTask {
private:
    int _task_number;
    Tick _period;
    Tick _arrival;
    bool _active;
    bool _executing;
    AbsKernel *_kernel;

public:
    FakeASAPBlockTask(int task_number, int period, int arrival = 0)
        : _task_number(task_number),
          _period(period),
          _arrival(arrival),
          _active(true),
          _executing(false),
          _kernel(nullptr) {}

    void schedule() override { _executing = true; }
    void deschedule() override { _executing = false; }
    void activate() override { _active = true; }
    bool isActive() const override { return _active; }
    bool isExecuting() const override { return _executing; }
    Tick getArrival() const override { return _arrival; }
    Tick getLastArrival() const override { return _arrival; }
    void setKernel(AbsKernel *kernel) override { _kernel = kernel; }
    AbsKernel *getKernel() override { return _kernel; }
    void refreshExec(double, double) override {}
    double getMaxExecutionCycles() const override { return 1.0; }
    Tick getDeadline() const override { return _arrival + _period; }
    Tick getRelDline() const override { return _period; }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getWCET(double = 1.0) const override { return 1.0; }
    double getRemainingWCET(double = 1.0) const override { return 1.0; }
    std::string toString() const override {
        return "FakeASAPBlockTask" + std::to_string(_task_number);
    }

    void setExecuting(bool executing) { _executing = executing; }
    void setActive(bool active) { _active = active; }
};

class DispatchTestTask : public Task {
private:
    Tick _period;
    int _schedule_count;

public:
    DispatchTestTask(int period, const std::string &name)
        : Task(nullptr, Tick(period), Tick(0), name, 1000, Tick(1)),
          _period(period),
          _schedule_count(0) {
        insertCode("fixed(1,control);");
    }

    Tick getPeriod() const override { return _period; }

    void schedule() override {
        state = TSK_EXEC;
        ++_schedule_count;
    }

    void deschedule() override {
        state = TSK_READY;
    }

    void releaseAt(Tick tick) {
        arrival = tick;
        lastArrival = tick;
        state = TSK_READY;
    }

    int getScheduleCount() const { return _schedule_count; }
};

class TestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit TestActionEvent(std::function<void()> action)
        : MetaSim::Event("ASAPBlockTestAction"),
          _action(std::move(action)) {}

    void doit() override {
        _action();
    }
};

class ASAPBlockSchedulerTestPeer {
public:
    static void addTaskModel(ASAPBlockScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             double unit_energy) {
        auto *model =
            new ASAPBlockTaskModel(task, period, 1, "test");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy;
        model->setActive();
        scheduler._task_models[task] = model;
    }

    static void sort(ASAPBlockScheduler &scheduler,
                     std::vector<AbsRTTask *> &tasks) {
        scheduler.sortByRMPriority(tasks);
    }

    static std::vector<AbsRTTask *> select(
        ASAPBlockScheduler &scheduler,
        const std::vector<AbsRTTask *> &tasks,
        std::size_t processors,
        double available_energy,
        double &reserved_energy,
        bool &stopped_by_energy) {
        return scheduler.selectASAPBlockPrefix(tasks,
                                               processors,
                                               available_energy,
                                               reserved_energy,
                                               stopped_by_energy);
    }

    static void commit(ASAPBlockScheduler &scheduler,
                       Tick tick,
                       double available_energy) {
        scheduler.commitTickEnergy(tick, available_energy);
    }

    static void arrive(ASAPBlockScheduler &scheduler,
                       AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static std::vector<AbsRTTask *> collect(
        ASAPBlockScheduler &scheduler,
        Tick tick) {
        return scheduler.collectActiveJobs(tick);
    }

    static void freeze(ASAPBlockScheduler &scheduler,
                       Tick tick,
                       std::vector<AbsRTTask *> selected,
                       double reserved_energy,
                       bool stopped_by_energy) {
        scheduler.freezeTickSelection(tick,
                                      std::move(selected),
                                      reserved_energy,
                                      stopped_by_energy);
    }

    static void cancelAutomaticTick(
        ASAPBlockScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
    }

    static bool acceptsDispatchCompletion(
        const ASAPBlockScheduler &scheduler,
        AbsRTTask *task) {
        return scheduler.acceptsDispatchCompletion(task);
    }
};

TEST(ASAPBlockScheduler, SortsByPeriodThenTaskNumber) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask task3(3, 10);
    FakeASAPBlockTask task1(1, 10);
    FakeASAPBlockTask task2(2, 5);

    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task3, 10, 1.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task1, 10, 1.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task2, 5, 1.0);

    std::vector<AbsRTTask *> tasks{&task3, &task1, &task2};
    ASAPBlockSchedulerTestPeer::sort(scheduler, tasks);

    EXPECT_EQ(tasks,
              (std::vector<AbsRTTask *>{&task2, &task1, &task3}));
}

TEST(ASAPBlockScheduler, EnergyWallPreventsLowerPriorityBypass) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask task1(1, 5);
    FakeASAPBlockTask task2(2, 10);
    FakeASAPBlockTask task3(3, 20);

    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task1, 5, 2.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task2, 10, 5.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &task3, 20, 0.5);

    std::vector<AbsRTTask *> tasks{&task1, &task2, &task3};
    double reserved = 0.0;
    bool stopped = false;
    auto selected = ASAPBlockSchedulerTestPeer::select(
        scheduler, tasks, 3, 3.0, reserved, stopped);

    EXPECT_EQ(selected, (std::vector<AbsRTTask *>{&task1}));
    EXPECT_DOUBLE_EQ(reserved, 2.0);
    EXPECT_TRUE(stopped);
}

TEST(ASAPBlockScheduler, UnaffordableHighestPrioritySelectsNothing) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask high(1, 5);
    FakeASAPBlockTask low(2, 10);

    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 2.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 10, 0.5);

    std::vector<AbsRTTask *> tasks{&high, &low};
    double reserved = 0.0;
    bool stopped = false;
    auto selected = ASAPBlockSchedulerTestPeer::select(
        scheduler, tasks, 2, 1.5, reserved, stopped);

    EXPECT_TRUE(selected.empty());
    EXPECT_DOUBLE_EQ(reserved, 0.0);
    EXPECT_TRUE(stopped);
}

TEST(ASAPBlockScheduler, EnergyBlockedJobRemainsEligibleNextTick) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask high(1, 5);
    ASAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &high, 5, 2.0);

    std::vector<AbsRTTask *> tasks{&high};
    double reserved = 0.0;
    bool stopped = false;
    auto first_tick = ASAPBlockSchedulerTestPeer::select(
        scheduler, tasks, 1, 1.5, reserved, stopped);

    EXPECT_TRUE(first_tick.empty());
    EXPECT_TRUE(high.isActive());

    auto next_tick = ASAPBlockSchedulerTestPeer::select(
        scheduler, tasks, 1, 2.0, reserved, stopped);

    EXPECT_EQ(next_tick,
              (std::vector<AbsRTTask *>{&high}));
    EXPECT_TRUE(high.isActive());
}

TEST(ASAPBlockScheduler, RunningJobDoesNotDependOnIsActiveFlag) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask running(1, 5);
    ASAPBlockSchedulerTestPeer::addTaskModel(
        scheduler, &running, 5, 1.0);
    ASAPBlockSchedulerTestPeer::arrive(scheduler, &running);
    running.setActive(false);
    running.setExecuting(true);

    auto active = ASAPBlockSchedulerTestPeer::collect(
        scheduler, MetaSim::Simulation::getInstance().getTime());

    EXPECT_EQ(active,
              (std::vector<AbsRTTask *>{&running}));
}

TEST(ASAPBlockScheduler, PreservesResidualAndRejectsDuplicateCommit) {
    ASAPBlockScheduler scheduler;

    ASAPBlockSchedulerTestPeer::freeze(
        scheduler, Tick(0), {}, 0.0, false);
    ASAPBlockSchedulerTestPeer::commit(
        scheduler, Tick(0), 1.5);

    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.5);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 0.0);

    FakeASAPBlockTask task(1, 5);
    ASAPBlockSchedulerTestPeer::freeze(
        scheduler,
        MetaSim::Simulation::getInstance().getTime(),
        {&task},
        0.0,
        false);
    EXPECT_EQ(scheduler.getTaskN(0), &task);
    EXPECT_EQ(scheduler.getTaskN(0), &task);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.5);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 0.0);

    EXPECT_THROW(
        ASAPBlockSchedulerTestPeer::commit(
            scheduler, Tick(0), 1.5),
        std::logic_error);

    ASAPBlockSchedulerTestPeer::freeze(
        scheduler, Tick(1), {&task}, 2.0, false);
    ASAPBlockSchedulerTestPeer::commit(
        scheduler, Tick(1), 2.5);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.5);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
}

TEST(ASAPBlockScheduler, GetTaskNOnlyReadsFrozenCurrentTickPrefix) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask task(1, 5);

    EXPECT_EQ(scheduler.getTaskN(0), nullptr);

    ASAPBlockSchedulerTestPeer::freeze(
        scheduler, MetaSim::SIMUL.getTime(), {&task}, 1.0, false);

    EXPECT_EQ(scheduler.getTaskN(0), &task);
    EXPECT_EQ(scheduler.getTaskN(1), nullptr);
}

TEST(ASAPBlockScheduler, CurrentTickReleaseDisplacesLowerPriorityJob) {
    ASAPBlockScheduler scheduler;
    FakeASAPBlockTask low(2, 20);
    FakeASAPBlockTask high(1, 5);
    low.setExecuting(true);

    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1.0);
    ASAPBlockSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1.0);
    ASAPBlockSchedulerTestPeer::arrive(scheduler, &low);
    ASAPBlockSchedulerTestPeer::arrive(scheduler, &high);

    auto active = ASAPBlockSchedulerTestPeer::collect(
        scheduler, MetaSim::Simulation::getInstance().getTime());
    ASAPBlockSchedulerTestPeer::sort(scheduler, active);

    double reserved = 0.0;
    bool stopped = false;
    auto selected = ASAPBlockSchedulerTestPeer::select(
        scheduler, active, 1, 1.0, reserved, stopped);

    ASSERT_EQ(selected.size(), 1U);
    EXPECT_EQ(selected.front(), &high);
    EXPECT_EQ(std::find(selected.begin(), selected.end(), &low),
              selected.end());
}

TEST(ASAPBlockScheduler,
     RejectsStaleDispatchCompletionAfterSelectionChanges) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ASAPBlockScheduler scheduler;
    CPU cpu("asap-block-test-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    DispatchTestTask low(20, "asap-block-low");
    DispatchTestTask high(5, "asap-block-high");

    kernel.setContextSwitchDelay(Tick(1));
    kernel.addTask(low, "period=20,wcet=1,workload=control");
    kernel.addTask(high, "period=5,wcet=1,workload=control");

    simulation.initSingleRun();
    ASAPBlockSchedulerTestPeer::cancelAutomaticTick(scheduler);

    low.releaseAt(Tick(0));
    kernel.onArrival(&low);
    ASAPBlockSchedulerTestPeer::freeze(
        scheduler, Tick(0), {&low}, 0.0, false);
    kernel.dispatch();

    TestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        kernel.onArrival(&high);
        ASAPBlockSchedulerTestPeer::freeze(
            scheduler, Tick(1), {&high}, 0.0, false);
    });
    release_high.post(Tick(1));

    TestActionEvent keep_high_selected([&]() {
        ASAPBlockSchedulerTestPeer::freeze(
            scheduler, Tick(2), {&high}, 0.0, false);
    });
    keep_high_selected.post(Tick(2));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_TRUE(high.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &high);
    EXPECT_TRUE(ASAPBlockSchedulerTestPeer::acceptsDispatchCompletion(
        scheduler, &high));
    EXPECT_FALSE(ASAPBlockSchedulerTestPeer::acceptsDispatchCompletion(
        scheduler, &low));

    simulation.endSingleRun();
}

} // namespace RTSim
