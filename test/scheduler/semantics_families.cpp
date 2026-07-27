#include <functional>
#include <set>
#include <string>
#include <type_traits>
#include <utility>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/json_trace.hpp>
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
FamilyOutcome runAbundantEnergyPreemptionScenario(
    MechanismSummary *observed_summary = nullptr,
    bool enable_summary = true) {
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

    std::unique_ptr<JSONTrace> trace;
    if (observed_summary) {
        trace = std::make_unique<JSONTrace>(
            "/tmp/partsim_scheduler_decision_family.json",
            Tick(3));
        scheduler.setTraceLogger(trace.get());
        scheduler.setSemanticTraceEnabled(false);
    }

    simulation.initSingleRun();
    if (trace && enable_summary) {
        trace->enableObservabilitySummaries(Tick(3));
    }
    scheduler._tick_event->drop();
    scheduler._first_tick_scheduled = false;
    scheduler._initial_energy = 20.0;
    scheduler._current_energy = 20.0;
    scheduler._max_energy = 100.0;

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

    if (trace && enable_summary) {
        trace->finalizeObservabilitySummaries(Tick(3));
    }
    if (observed_summary) {
        *observed_summary = trace->mechanismSummary();
    }

    simulation.endSingleRun();
    return outcome;
}

template <typename SchedulerType, typename ModelType>
void expectThreeObservedDecisions() {
    MechanismSummary summary;
    (void)runAbundantEnergyPreemptionScenario<SchedulerType, ModelType>(
        &summary, true);
    EXPECT_EQ(summary.observed_decision_ticks, 3u);
}

struct DecisionScenarioTaskSpec {
    int task_number;
    int period;
    int relative_deadline;
    int wcet;
    double unit_energy;
};

template <typename SchedulerType, typename ModelType>
MechanismSummary runSingleObservedDecision(
    const std::vector<DecisionScenarioTaskSpec> &specs,
    double available_energy,
    std::size_t processor_count = 1,
    bool force_energy_depleted = false) {
    auto &simulation = MetaSim::Simulation::getInstance();
    FamilyTestScheduler<SchedulerType> scheduler;
    std::vector<std::unique_ptr<CPU>> cpus;
    std::set<CPU *> cpu_set;
    for (std::size_t i = 0; i < processor_count; ++i) {
        cpus.push_back(std::make_unique<CPU>(
            "decision-cpu-" + std::to_string(i), nullptr));
        cpu_set.insert(cpus.back().get());
    }
    MRTKernel kernel(&scheduler, cpu_set);
    std::vector<std::unique_ptr<FamilyScenarioTask>> tasks;
    for (const auto &spec : specs) {
        tasks.push_back(std::make_unique<FamilyScenarioTask>(
            spec.task_number,
            spec.period,
            spec.relative_deadline,
            static_cast<double>(spec.wcet)));
        auto *model = new ModelType(
            tasks.back().get(), spec.period, spec.wcet, "control");
        model->_unit_energy = spec.unit_energy;
        model->_total_energy = spec.unit_energy * spec.wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[tasks.back().get()] = model;
    }

    JSONTrace trace(
        "/tmp/partsim_single_scheduler_decision.json", Tick(1));
    scheduler.setTraceLogger(&trace);
    scheduler.setSemanticTraceEnabled(false);
    simulation.initSingleRun();
    trace.enableObservabilitySummaries(Tick(1));
    scheduler._tick_event->drop();
    scheduler._first_tick_scheduled = false;
    scheduler._initial_energy = available_energy;
    scheduler._current_energy = available_energy;
    scheduler._max_energy = 100.0;
    if constexpr (std::is_same_v<SchedulerType, STNonBlockScheduler>) {
        scheduler._energy_depleted = force_energy_depleted;
    } else {
        EXPECT_FALSE(force_energy_depleted);
    }
    for (auto &task : tasks) {
        task->releaseAt(Tick(0));
        scheduler.onTaskArrival(task.get());
    }

    scheduler.performTickScheduling();
    trace.finalizeObservabilitySummaries(Tick(1));
    const MechanismSummary summary = trace.mechanismSummary();
    simulation.endSingleRun();
    return summary;
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

TEST(SchedulerDecisionReporting,
     AllNineSchedulersReportOneRecordPerTickWithSemanticTraceDisabled) {
    expectThreeObservedDecisions<ASAPBlockScheduler,
                                 ASAPBlockTaskModel>();
    expectThreeObservedDecisions<ASAPNonBlockScheduler,
                                 ASAPNonBlockTaskModel>();
    expectThreeObservedDecisions<ASAPSyncScheduler,
                                 ASAPSyncTaskModel>();
    expectThreeObservedDecisions<ALAPBlockScheduler,
                                 ALAPBlockTaskModel>();
    expectThreeObservedDecisions<ALAPNonBlockScheduler,
                                 ALAPNonBlockTaskModel>();
    expectThreeObservedDecisions<ALAPSyncScheduler,
                                 ALAPSyncTaskModel>();
    expectThreeObservedDecisions<STBlockScheduler,
                                 STBlockTaskModel>();
    expectThreeObservedDecisions<STNonBlockScheduler,
                                 STNonBlockTaskModel>();
    expectThreeObservedDecisions<STSyncScheduler,
                                 STSyncTaskModel>();
}

TEST(SchedulerDecisionReporting,
     DisabledSummarySkipsConstructionAndPreservesFamilyOutcome) {
    const auto baseline = runAbundantEnergyPreemptionScenario<
        ASAPBlockScheduler, ASAPBlockTaskModel>();
    MechanismSummary disabled_summary;
    const auto with_disabled_trace = runAbundantEnergyPreemptionScenario<
        ASAPBlockScheduler, ASAPBlockTaskModel>(
            &disabled_summary, false);

    EXPECT_EQ(with_disabled_trace, baseline);
    EXPECT_EQ(disabled_summary.observed_decision_ticks, 0u);
}

TEST(SchedulerDecisionReporting,
     BlockNonBlockAndSyncUseNativeEnergyExclusionSemantics) {
    const std::vector<DecisionScenarioTaskSpec> specs{
        {0, 5, 1, 1, 2.0},
        {1, 10, 1, 1, 0.5},
    };
    const auto block = runSingleObservedDecision<
        ASAPBlockScheduler, ASAPBlockTaskModel>(specs, 0.5);
    const auto nonblock = runSingleObservedDecision<
        ASAPNonBlockScheduler, ASAPNonBlockTaskModel>(specs, 0.5);
    const auto sync = runSingleObservedDecision<
        ASAPSyncScheduler, ASAPSyncTaskModel>(specs, 0.5);

    EXPECT_EQ(block.bypass_opportunity_ticks, 1u);
    EXPECT_EQ(block.actual_bypass_ticks, 0u);
    EXPECT_EQ(block.hp_energy_blocked_ticks, 1u);
    EXPECT_EQ(nonblock.bypass_opportunity_ticks, 1u);
    EXPECT_EQ(nonblock.actual_bypass_ticks, 1u);
    EXPECT_EQ(sync.actual_bypass_ticks, 0u);
    EXPECT_EQ(sync.hp_energy_blocked_ticks, 1u);
}

TEST(SchedulerDecisionReporting,
     AlapTimingDeferIsNotClassifiedAsEnergyBlocking) {
    const auto summary = runSingleObservedDecision<
        ALAPBlockScheduler, ALAPBlockTaskModel>(
            {{0, 10, 5, 1, 0.5}}, 1.0);

    EXPECT_EQ(summary.observed_decision_ticks, 1u);
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 0u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 0u);
}

TEST(SchedulerDecisionReporting,
     StEnergyChargeWaitIsReportedAsEnergyBlocking) {
    const auto summary = runSingleObservedDecision<
        STBlockScheduler, STBlockTaskModel>(
            {{0, 10, 5, 1, 2.0}}, 0.5);

    EXPECT_EQ(summary.observed_decision_ticks, 1u);
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 1u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 1u);
}

TEST(SchedulerDecisionReporting,
     FrozenRmRankMarksFifthTaskAsBottomSixBypass) {
    const auto summary = runSingleObservedDecision<
        ASAPNonBlockScheduler, ASAPNonBlockTaskModel>(
            {
                {0, 5, 1, 1, 2.0},
                {1, 10, 1, 1, 2.0},
                {2, 15, 1, 1, 2.0},
                {3, 20, 1, 1, 2.0},
                {4, 25, 1, 1, 0.5},
            },
            0.5);

    EXPECT_EQ(summary.actual_bypass_ticks, 1u);
    EXPECT_EQ(summary.low_priority_bypass_core_ticks, 1u);
}

TEST(SchedulerDecisionReporting,
     EmptyReadyCpuCapacityAndEnergyEarlyReturnStillReportOneTick) {
    const auto empty = runSingleObservedDecision<
        ASAPBlockScheduler, ASAPBlockTaskModel>({}, 1.0);
    const auto capacity_limited = runSingleObservedDecision<
        ASAPBlockScheduler, ASAPBlockTaskModel>(
            {
                {0, 5, 1, 1, 0.5},
                {1, 10, 1, 1, 0.5},
            },
            1.0,
            1);
    const auto early_return = runSingleObservedDecision<
        STNonBlockScheduler, STNonBlockTaskModel>(
            {{0, 5, 1, 1, 0.5}}, 0.0, 1, true);

    EXPECT_EQ(empty.observed_decision_ticks, 1u);
    EXPECT_EQ(capacity_limited.observed_decision_ticks, 1u);
    EXPECT_EQ(early_return.observed_decision_ticks, 1u);
}

}  // namespace RTSim
