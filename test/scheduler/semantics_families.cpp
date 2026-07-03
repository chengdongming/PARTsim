#include <functional>
#include <set>
#include <string>
#include <utility>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/task.hpp>

#define private public
#define protected public
#include <rtsim/scheduler/gpfp_alap_block_scheduler.hpp>
#include <rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp>
#include <rtsim/scheduler/gpfp_alap_sync_scheduler.hpp>
#include <rtsim/scheduler/gpfp_asap_block_scheduler.hpp>
#include <rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp>
#include <rtsim/scheduler/gpfp_asap_sync_scheduler.hpp>
#include <rtsim/scheduler/gpfp_st_block_scheduler.hpp>
#include <rtsim/scheduler/gpfp_st_nonblock_scheduler.hpp>
#include <rtsim/scheduler/gpfp_st_sync_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class FamilyScenarioTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FamilyScenarioTask(int task_number,
                       int period,
                       int relative_deadline,
                       double remaining,
                       int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FamilyScenarioTask" + std::to_string(task_number),
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

class FamilyScenarioEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit FamilyScenarioEvent(std::function<void()> action)
        : MetaSim::Event("FamilyScenarioEvent"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

struct FamilyOutcome {
    int high_schedules;
    int medium_schedules;
    int low_schedules;
    bool medium_running;
    bool low_running;
    double medium_remaining;
    double low_remaining;
    Tick medium_deadline;
    Tick low_deadline;
    double energy_consumed;

    bool operator==(const FamilyOutcome &other) const {
        return high_schedules == other.high_schedules &&
               medium_schedules == other.medium_schedules &&
               low_schedules == other.low_schedules &&
               medium_running == other.medium_running &&
               low_running == other.low_running &&
               medium_remaining == other.medium_remaining &&
               low_remaining == other.low_remaining &&
               medium_deadline == other.medium_deadline &&
               low_deadline == other.low_deadline &&
               energy_consumed == other.energy_consumed;
    }
};

template <typename SchedulerType>
class FamilyTestScheduler : public SchedulerType {
public:
    using Scheduler::enqueueModel;
};

template <typename SchedulerType, typename ModelType>
FamilyOutcome runAbundantEnergyPreemptionScenario() {
    auto &simulation = MetaSim::Simulation::getInstance();
    FamilyTestScheduler<SchedulerType> scheduler;
    CPU cpu0("family-cpu0", nullptr);
    CPU cpu1("family-cpu1", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});

    // Both long jobs are urgent at release for ALAP: D - C == 0.
    FamilyScenarioTask medium(2, 10, 5, 5.0);
    FamilyScenarioTask low(3, 20, 5, 5.0);
    FamilyScenarioTask high(1, 5, 1, 1.0, 1);

    auto add_model = [&scheduler](FamilyScenarioTask *task,
                                  int period,
                                  int wcet) {
        auto *model = new ModelType(task, period, wcet, "control");
        model->_unit_energy = 1.0;
        model->_total_energy = static_cast<double>(wcet);
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    };
    add_model(&medium, 10, 5);
    add_model(&low, 20, 5);
    add_model(&high, 5, 1);

    simulation.initSingleRun();
    scheduler._tick_event->drop();
    scheduler._first_tick_scheduled = false;
    scheduler._initial_energy = 20.0;
    scheduler._current_energy = 20.0;
    scheduler._max_energy = 100.0;
    scheduler._base_harvest_rate = 0.0;
    scheduler._use_real_solar_data = false;
    scheduler._last_tick_time = MetaSim::SIMUL.getTime();
    scheduler._last_collection_time = MetaSim::SIMUL.getTime();

    medium.releaseAt(Tick(0));
    low.releaseAt(Tick(0));
    scheduler.onTaskArrival(&medium);
    scheduler.onTaskArrival(&low);
    scheduler.performTickScheduling();

    FamilyScenarioEvent preempt([&]() {
        medium.setRemaining(4.0);
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        scheduler.onTaskArrival(&high);
        scheduler.performTickScheduling();
    });
    preempt.post(Tick(1));

    FamilyScenarioEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        scheduler.performTickScheduling();
    });
    resume.post(Tick(2));

    simulation.run_to(Tick(2));

    FamilyOutcome outcome{
        high.getScheduleCount(),
        medium.getScheduleCount(),
        low.getScheduleCount(),
        medium.isExecuting(),
        low.isExecuting(),
        medium.getRemainingWCET(),
        low.getRemainingWCET(),
        medium.getDeadline(),
        low.getDeadline(),
        scheduler.getTotalEnergyConsumed()};

    simulation.endSingleRun();
    return outcome;
}

TEST(ASAPFamily, AbundantEnergyBlockNonBlockSyncEquivalent) {
    const auto block = runAbundantEnergyPreemptionScenario<
        ASAPBlockScheduler, ASAPBlockTaskModel>();
    const auto nonblock = runAbundantEnergyPreemptionScenario<
        ASAPNonBlockScheduler, ASAPNonBlockTaskModel>();
    const auto sync = runAbundantEnergyPreemptionScenario<
        ASAPSyncScheduler, ASAPSyncTaskModel>();

    EXPECT_EQ(block, nonblock);
    EXPECT_EQ(block, sync);
    EXPECT_EQ(block.high_schedules, 1);
    EXPECT_EQ(block.medium_schedules, 1);
    EXPECT_EQ(block.low_schedules, 2);
    EXPECT_TRUE(block.medium_running);
    EXPECT_TRUE(block.low_running);
    EXPECT_DOUBLE_EQ(block.energy_consumed, 6.0);
}

TEST(STFamily, AbundantEnergyEqualsASAPFamily) {
    const auto asap_block = runAbundantEnergyPreemptionScenario<
        ASAPBlockScheduler, ASAPBlockTaskModel>();
    const auto asap_nonblock = runAbundantEnergyPreemptionScenario<
        ASAPNonBlockScheduler, ASAPNonBlockTaskModel>();
    const auto asap_sync = runAbundantEnergyPreemptionScenario<
        ASAPSyncScheduler, ASAPSyncTaskModel>();
    const auto st_block = runAbundantEnergyPreemptionScenario<
        STBlockScheduler, STBlockTaskModel>();
    const auto st_nonblock = runAbundantEnergyPreemptionScenario<
        STNonBlockScheduler, STNonBlockTaskModel>();
    const auto st_sync = runAbundantEnergyPreemptionScenario<
        STSyncScheduler, STSyncTaskModel>();

    EXPECT_EQ(st_block, asap_block);
    EXPECT_EQ(st_nonblock, asap_nonblock);
    EXPECT_EQ(st_sync, asap_sync);
}

TEST(ALAPFamily, AbundantEnergyBlockNonBlockSyncEquivalent) {
    const auto block = runAbundantEnergyPreemptionScenario<
        ALAPBlockScheduler, ALAPBlockTaskModel>();
    const auto nonblock = runAbundantEnergyPreemptionScenario<
        ALAPNonBlockScheduler, ALAPNonBlockTaskModel>();
    const auto sync = runAbundantEnergyPreemptionScenario<
        ALAPSyncScheduler, ALAPSyncTaskModel>();

    EXPECT_EQ(block, nonblock);
    EXPECT_EQ(block, sync);
    EXPECT_EQ(block.high_schedules, 1);
    EXPECT_EQ(block.low_schedules, 2);
}

}  // namespace RTSim
