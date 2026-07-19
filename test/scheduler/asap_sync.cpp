#include <algorithm>
#include <functional>
#include <fstream>
#include <iterator>
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
#include <rtsim/json_trace.hpp>

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
    int _deschedule_count;

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
          _schedule_count(0),
          _deschedule_count(0) {
        insertCode("fixed(1,control);");
    }

    void schedule() override {
        state = TSK_EXEC;
        ++_schedule_count;
    }

    void deschedule() override {
        state = TSK_READY;
        ++_deschedule_count;
    }

    Tick getDeadline() const override { return arrival + _relative_deadline; }
    Tick getRelDline() const override { return _relative_deadline; }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getRemainingWCET(double = 1.0) const override {
        return _remaining;
    }

    int getScheduleCount() const { return _schedule_count; }
    int getDescheduleCount() const { return _deschedule_count; }
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

    static void remove(ASAPSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.removeFromReadyQueue(task);
    }

    static void setCurrentEnergy(ASAPSyncScheduler &scheduler,
                                 double current_energy) {
        scheduler._current_energy = current_energy;
        scheduler._last_tick_time = MetaSim::SIMUL.getTime();
    }
};

static bool ContainsTask(const std::vector<AbsRTTask *> &tasks,
                         AbsRTTask *task) {
    return std::find(tasks.begin(), tasks.end(), task) != tasks.end();
}

static std::string ReadASAPSyncTrace(const std::string &path) {
    std::ifstream input(path);
    return std::string(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
}

static std::size_t CountASAPSyncTraceMarker(
    const std::string &contents, const std::string &marker) {
    std::size_t count = 0;
    std::size_t position = 0;
    while ((position = contents.find(marker, position)) != std::string::npos) {
        ++count;
        position += marker.size();
    }
    return count;
}

struct ContinuationWaitObservation {
    std::vector<int> selected_task_numbers;
    int continuation_schedule_count;
    int candidate_schedule_count;
    int continuation_deschedule_count;
    int candidate_deschedule_count;
    bool continuation_executing;
    bool candidate_executing;
    double current_energy;
    double consumed_energy;
    std::string trace;
};

static ContinuationWaitObservation RunContinuationWaitObservation(
    bool semantic_trace_enabled, const std::string &suffix,
    double available_energy = 1.5) {
    const std::string path =
        "/tmp/partsim_b2_continuation_wait_" + suffix + ".json";
    ContinuationWaitObservation result;
    {
        auto &simulation = MetaSim::Simulation::getInstance();
        TestASAPSyncScheduler scheduler;
        CPU cpu0("asap-sync-trace-continuation-cpu0-" + suffix, nullptr);
        CPU cpu1("asap-sync-trace-continuation-cpu1-" + suffix, nullptr);
        TestASAPSyncMRTKernel kernel(
            &scheduler, std::set<CPU *>{&cpu0, &cpu1});
        JSONTrace trace(path, Tick(2));
        FakeASAPSyncTask continuation(2, 20, 20, 1.0);
        FakeASAPSyncTask candidate(1, 5, 5, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &continuation, 20, 1, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &candidate, 5, 1, 1.0);

        trace.setEnergyProvider(&scheduler);
        trace.setSemanticTraceEnabled(semantic_trace_enabled);
        scheduler.setTraceLogger(&trace);
        scheduler.setSemanticTraceEnabled(semantic_trace_enabled);

        simulation.initSingleRun();
        ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
        ASAPSyncSchedulerTestPeer::setEnergy(scheduler, available_energy);
        continuation.releaseAt(Tick(0));
        candidate.releaseAt(Tick(0));
        continuation.schedule();
        kernel.setRunning(&cpu0, &continuation);
        ASAPSyncSchedulerTestPeer::enqueue(scheduler, &candidate);

        ASAPSyncSchedulerTestPeer::tick(scheduler);
        simulation.run_to(Tick(0));

        for (AbsRTTask *task : scheduler.getCurrentBatchTasks()) {
            result.selected_task_numbers.push_back(task->getTaskNumber());
        }
        result.continuation_schedule_count = continuation.getScheduleCount();
        result.candidate_schedule_count = candidate.getScheduleCount();
        result.continuation_deschedule_count =
            continuation.getDescheduleCount();
        result.candidate_deschedule_count = candidate.getDescheduleCount();
        result.continuation_executing = continuation.isExecuting();
        result.candidate_executing = candidate.isExecuting();
        result.current_energy = scheduler.getCurrentEnergy();
        result.consumed_energy = scheduler.getTotalEnergyConsumed();
        trace.setSimulationOutcome(
            Tick(0), true, "bounded_continuation_wait_microcase");
        simulation.endSingleRun();
        scheduler.setTraceLogger(nullptr);
    }
    result.trace = ReadASAPSyncTrace(path);
    return result;
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

TEST(ASAPSyncScheduler,
     ContinuationCandidateWaitTraceIsCompleteAndEmittedOnce) {
    const auto observed = RunContinuationWaitObservation(
        true, "complete-event");

    EXPECT_EQ(observed.selected_task_numbers, (std::vector<int>{2}));
    EXPECT_EQ(observed.continuation_schedule_count, 1);
    EXPECT_EQ(observed.candidate_schedule_count, 0);
    EXPECT_TRUE(observed.continuation_executing);
    EXPECT_FALSE(observed.candidate_executing);
    EXPECT_DOUBLE_EQ(observed.current_energy, 0.5);
    EXPECT_DOUBLE_EQ(observed.consumed_energy, 1.0);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  observed.trace,
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              1);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  observed.trace,
                  "\"event_type\": \"scheduler_decision\""),
              1);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  observed.trace,
                  "\"event_type\": \"sync_batch_block\""),
              0);
    for (const std::string &field : {
             "active_top_m_tasks", "continuation_tasks",
             "new_candidate_tasks", "selected_tasks",
             "active_top_m_count", "continuation_count",
             "new_candidate_count", "selected_count",
             "active_top_m_required_energy_mJ",
             "active_top_m_required_energy_mJ_exact",
             "continuation_required_energy_mJ",
             "continuation_required_energy_mJ_exact",
             "new_candidate_required_energy_mJ",
             "new_candidate_required_energy_mJ_exact",
             "available_energy_before_decision_mJ",
             "available_energy_before_decision_mJ_exact",
             "residual_energy_after_continuation_reservation_mJ",
             "residual_energy_after_continuation_reservation_mJ_exact",
             "whole_active_top_m_affordable",
             "all_new_candidates_affordable_after_continuation",
             "feasible_new_candidate_subset_exists",
             "native_affordability_epsilon_mJ",
             "native_affordability_epsilon_mJ_exact"}) {
        EXPECT_NE(observed.trace.find("\"" + field + "\""),
                  std::string::npos) << field;
    }
    EXPECT_NE(observed.trace.find("\"active_top_m_count\": 2"),
              std::string::npos);
    EXPECT_NE(observed.trace.find("\"continuation_count\": 1"),
              std::string::npos);
    EXPECT_NE(observed.trace.find("\"new_candidate_count\": 1"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"active_top_m_required_energy_mJ\": 2000"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"continuation_required_energy_mJ\": 1000"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"new_candidate_required_energy_mJ\": 1000"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"available_energy_before_decision_mJ\": 1500"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"residual_energy_after_continuation_reservation_mJ\": 500"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"whole_active_top_m_affordable\": false"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"all_new_candidates_affordable_after_continuation\": false"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"feasible_new_candidate_subset_exists\": false"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"native_affordability_epsilon_mJ\": 1e-06"),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"available_energy_before_decision_mJ_exact\": \"1500\""),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"residual_energy_after_continuation_reservation_mJ_exact\": \"500\""),
              std::string::npos);
    EXPECT_NE(observed.trace.find(
                  "\"native_affordability_epsilon_mJ_exact\": \""
                  "1.0000000000000002e-06\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler,
     AffordableContinuationCandidateDoesNotEmitCandidateWait) {
    const auto observed = RunContinuationWaitObservation(
        true, "affordable", 2.0);

    EXPECT_EQ(observed.selected_task_numbers, (std::vector<int>{1, 2}));
    EXPECT_EQ(observed.candidate_schedule_count, 1);
    EXPECT_DOUBLE_EQ(observed.current_energy, 0.0);
    EXPECT_EQ(observed.trace.find(
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler, EmptySelectionKeepsLegacySyncBatchBlockOnly) {
    const std::string path =
        "/tmp/partsim_b2_legacy_empty_selection_block.json";
    {
        auto &simulation = MetaSim::Simulation::getInstance();
        TestASAPSyncScheduler scheduler;
        CPU cpu0("asap-sync-legacy-block-cpu0", nullptr);
        CPU cpu1("asap-sync-legacy-block-cpu1", nullptr);
        TestASAPSyncMRTKernel kernel(
            &scheduler, std::set<CPU *>{&cpu0, &cpu1});
        JSONTrace trace(path, Tick(2));
        FakeASAPSyncTask first(1, 5, 5, 1.0);
        FakeASAPSyncTask second(2, 10, 10, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &first, 5, 1, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &second, 10, 1, 1.0);
        trace.setSemanticTraceEnabled(true);
        scheduler.setTraceLogger(&trace);
        scheduler.setSemanticTraceEnabled(true);

        simulation.initSingleRun();
        ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
        ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.5);
        first.releaseAt(Tick(0));
        second.releaseAt(Tick(0));
        ASAPSyncSchedulerTestPeer::enqueue(scheduler, &first);
        ASAPSyncSchedulerTestPeer::enqueue(scheduler, &second);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
        simulation.run_to(Tick(0));
        EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
        simulation.endSingleRun();
        scheduler.setTraceLogger(nullptr);
    }
    const std::string trace = ReadASAPSyncTrace(path);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  trace, "\"event_type\": \"sync_batch_block\""),
              1);
    EXPECT_EQ(trace.find(
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler, OnlyContinuationDoesNotEmitCandidateWait) {
    const std::string path =
        "/tmp/partsim_b2_only_continuation_no_wait.json";
    {
        auto &simulation = MetaSim::Simulation::getInstance();
        TestASAPSyncScheduler scheduler;
        CPU cpu("asap-sync-only-continuation-cpu", nullptr);
        TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
        JSONTrace trace(path, Tick(2));
        FakeASAPSyncTask continuation(1, 5, 5, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &continuation, 5, 1, 1.0);
        trace.setSemanticTraceEnabled(true);
        scheduler.setTraceLogger(&trace);
        scheduler.setSemanticTraceEnabled(true);

        simulation.initSingleRun();
        ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
        ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
        continuation.releaseAt(Tick(0));
        continuation.schedule();
        kernel.setRunning(&cpu, &continuation);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
        simulation.run_to(Tick(0));
        EXPECT_EQ(scheduler.getCurrentBatchTasks(),
                  (std::vector<AbsRTTask *>{&continuation}));
        simulation.endSingleRun();
        scheduler.setTraceLogger(nullptr);
    }
    const std::string trace = ReadASAPSyncTrace(path);
    EXPECT_EQ(trace.find(
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler, UnaffordableContinuationEmitsGeneralLegacyBlockAtQ0) {
    const std::string path =
        "/tmp/partsim_b2_q0_general_legacy_block.json";
    {
        auto &simulation = MetaSim::Simulation::getInstance();
        TestASAPSyncScheduler scheduler;
        CPU cpu("asap-sync-q0-block-cpu", nullptr);
        TestASAPSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
        JSONTrace trace(path, Tick(2));
        FakeASAPSyncTask continuation(1, 5, 5, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &continuation, 5, 1, 1.0);
        trace.setSemanticTraceEnabled(true);
        scheduler.setTraceLogger(&trace);
        scheduler.setSemanticTraceEnabled(true);

        simulation.initSingleRun();
        ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
        ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 0.5);
        continuation.releaseAt(Tick(0));
        continuation.schedule();
        kernel.setRunning(&cpu, &continuation);
        ASAPSyncSchedulerTestPeer::tick(scheduler);
        simulation.run_to(Tick(0));
        EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
        EXPECT_FALSE(continuation.isExecuting());
        simulation.endSingleRun();
        scheduler.setTraceLogger(nullptr);
    }
    const std::string trace = ReadASAPSyncTrace(path);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  trace, "\"event_type\": \"sync_batch_block\""),
              1);
    EXPECT_EQ(trace.find(
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler, SemanticTraceTogglePreservesSchedulingAndEnergy) {
    const auto enabled = RunContinuationWaitObservation(true, "enabled");
    const auto disabled = RunContinuationWaitObservation(false, "disabled");

    EXPECT_EQ(enabled.selected_task_numbers, disabled.selected_task_numbers);
    EXPECT_EQ(enabled.continuation_schedule_count,
              disabled.continuation_schedule_count);
    EXPECT_EQ(enabled.candidate_schedule_count,
              disabled.candidate_schedule_count);
    EXPECT_EQ(enabled.continuation_deschedule_count,
              disabled.continuation_deschedule_count);
    EXPECT_EQ(enabled.candidate_deschedule_count,
              disabled.candidate_deschedule_count);
    EXPECT_EQ(enabled.continuation_executing,
              disabled.continuation_executing);
    EXPECT_EQ(enabled.candidate_executing, disabled.candidate_executing);
    EXPECT_DOUBLE_EQ(enabled.current_energy, disabled.current_energy);
    EXPECT_DOUBLE_EQ(enabled.consumed_energy, disabled.consumed_energy);
    for (const std::string &task_event : {
             "\"event_type\": \"scheduled\"",
             "\"event_type\": \"descheduled\"",
             "\"event_type\": \"end_instance\"",
             "\"event_type\": \"dline_miss\"",
             "\"event_type\": \"simulation_run_outcome\""}) {
        EXPECT_EQ(CountASAPSyncTraceMarker(enabled.trace, task_event),
                  CountASAPSyncTraceMarker(disabled.trace, task_event));
    }
    EXPECT_EQ(
        enabled.trace.find("\"simulation_completed\": true") !=
            std::string::npos,
        disabled.trace.find("\"simulation_completed\": true") !=
            std::string::npos);
    EXPECT_EQ(
        enabled.trace.find(
            "\"simulation_completion_reason\": \"bounded_continuation_wait_microcase\"") !=
            std::string::npos,
        disabled.trace.find(
            "\"simulation_completion_reason\": \"bounded_continuation_wait_microcase\"") !=
            std::string::npos);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  enabled.trace,
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              1);
    EXPECT_EQ(disabled.trace.find(
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              std::string::npos);
}

TEST(ASAPSyncScheduler, CandidateWaitIsOncePerTickAndUsesFreshJobIdentity) {
    const std::string path =
        "/tmp/partsim_b2_candidate_wait_repeated_ticks.json";
    {
        auto &simulation = MetaSim::Simulation::getInstance();
        TestASAPSyncScheduler scheduler;
        CPU cpu0("asap-sync-repeat-cpu0", nullptr);
        CPU cpu1("asap-sync-repeat-cpu1", nullptr);
        TestASAPSyncMRTKernel kernel(
            &scheduler, std::set<CPU *>{&cpu0, &cpu1});
        JSONTrace trace(path, Tick(3));
        FakeASAPSyncTask continuation(3, 20, 20, 2.0);
        FakeASAPSyncTask first_candidate(1, 5, 5, 1.0);
        FakeASAPSyncTask fresh_candidate(2, 10, 10, 1.0, 1);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &continuation, 20, 2, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &first_candidate, 5, 1, 1.0);
        ASAPSyncSchedulerTestPeer::addTaskModel(
            scheduler, &fresh_candidate, 10, 1, 1.0);
        trace.setSemanticTraceEnabled(true);
        scheduler.setTraceLogger(&trace);
        scheduler.setSemanticTraceEnabled(true);

        simulation.initSingleRun();
        ASAPSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
        ASAPSyncSchedulerTestPeer::setEnergy(scheduler, 1.5);
        continuation.releaseAt(Tick(0));
        first_candidate.releaseAt(Tick(0));
        continuation.schedule();
        kernel.setRunning(&cpu0, &continuation);
        ASAPSyncSchedulerTestPeer::enqueue(scheduler, &first_candidate);
        ASAPSyncSchedulerTestPeer::tick(scheduler);

        ASAPSyncTestActionEvent next_tick([&]() {
            first_candidate.setRemaining(0.0);
            ASAPSyncSchedulerTestPeer::remove(
                scheduler, &first_candidate);
            fresh_candidate.releaseAt(Tick(1));
            ASAPSyncSchedulerTestPeer::enqueue(
                scheduler, &fresh_candidate);
            ASAPSyncSchedulerTestPeer::setCurrentEnergy(scheduler, 1.5);
            ASAPSyncSchedulerTestPeer::tick(scheduler);
        });
        next_tick.post(Tick(1));
        simulation.run_to(Tick(1));
        EXPECT_EQ(first_candidate.getScheduleCount(), 0);
        EXPECT_EQ(fresh_candidate.getScheduleCount(), 0);
        simulation.endSingleRun();
        scheduler.setTraceLogger(nullptr);
    }
    const std::string trace = ReadASAPSyncTrace(path);
    EXPECT_EQ(CountASAPSyncTraceMarker(
                  trace,
                  "\"event_type\": \"sync_batch_candidate_wait\""),
              2);
    EXPECT_EQ(CountASAPSyncTraceMarker(trace, "\"time\": \"0\""), 2);
    EXPECT_EQ(CountASAPSyncTraceMarker(trace, "\"time\": \"1\""), 2);
    EXPECT_NE(trace.find("\"task_name\": \"FakeASAPSyncTask1\""),
              std::string::npos);
    EXPECT_NE(trace.find("\"task_name\": \"FakeASAPSyncTask2\""),
              std::string::npos);
    EXPECT_NE(trace.find("\"arrival_time\": 1"), std::string::npos);
    const std::string marker =
        "\"event_type\": \"sync_batch_candidate_wait\"";
    const std::size_t first_wait = trace.find(marker);
    ASSERT_NE(first_wait, std::string::npos);
    const std::size_t second_wait = trace.find(marker, first_wait + marker.size());
    ASSERT_NE(second_wait, std::string::npos);
    const std::size_t second_wait_end = trace.find('\n', second_wait);
    const std::string second_wait_event = trace.substr(
        second_wait,
        second_wait_end == std::string::npos
            ? std::string::npos
            : second_wait_end - second_wait);
    EXPECT_NE(second_wait_event.find(
                  "\"task_name\": \"FakeASAPSyncTask2\""),
              std::string::npos);
    EXPECT_NE(second_wait_event.find("\"arrival_time\": 1"),
              std::string::npos);
    EXPECT_EQ(second_wait_event.find(
                  "\"task_name\": \"FakeASAPSyncTask1\""),
              std::string::npos);
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
