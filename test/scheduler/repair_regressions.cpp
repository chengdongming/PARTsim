#include <algorithm>
#include <cmath>
#include <fstream>
#include <memory>
#include <limits>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include <gtest/gtest.h>

#include <metasim/factory.hpp>
#include <metasim/basestat.hpp>
#include <metasim/simul.hpp>
#include <rtsim/abskernel.hpp>
#include <rtsim/cpu.hpp>
#include <rtsim/instr.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/system.hpp>
#include <rtsim/scheduler/st_energy_utils.hpp>
#include <rtsim/task_model_validation.hpp>

namespace RTSim {

class Round2OutcomeEvent : public MetaSim::Event {
public:
    explicit Round2OutcomeEvent(const std::string &name)
        : MetaSim::Event(name) {}
    void doit() override {}
};

class Round2OutcomeEntity : public MetaSim::Entity {
    MetaSim::Tick _event_time;
    Round2OutcomeEvent _event;

public:
    Round2OutcomeEntity(const std::string &name, MetaSim::Tick event_time)
        : MetaSim::Entity(name),
          _event_time(event_time),
          _event(name + "-event") {}

    void newRun() override { _event.post(_event_time); }
    void endRun() override { _event.drop(); }
};

class Round2NullKernel : public AbsKernel {
public:
    void activate(AbsRTTask *) override {}
    void suspend(AbsRTTask *) override {}
    void dispatch() override {}
    void onArrival(AbsRTTask *) override {}
    void onEnd(AbsRTTask *) override {}
    CPU *getProcessor(const AbsRTTask *) const override { return nullptr; }
    CPU *getOldProcessor(const AbsRTTask *) const override { return nullptr; }
    double getSpeed() const override { return 1.0; }
    bool isContextSwitching() const override { return false; }
    Scheduler *getScheduler() const override { return nullptr; }
};

struct B3BoundaryTraceEvidence {
    double available;
    double epsilon;
    double required;
    bool recorded_affordable;
    std::string available_token;
    std::string epsilon_token;
    std::string job_required_token;
    std::string decision_required_token;
};

static std::string readJsonScalar(const std::string &contents,
                                  const std::string &field) {
    const std::string marker = "\"" + field + "\": ";
    const std::size_t start = contents.find(marker);
    EXPECT_NE(start, std::string::npos) << field;
    if (start == std::string::npos) return "";
    const std::size_t value_start = start + marker.size();
    const std::size_t value_end = contents.find_first_of(",}", value_start);
    EXPECT_NE(value_end, std::string::npos) << field;
    if (value_end == std::string::npos) return "";
    return contents.substr(value_start, value_end - value_start);
}

static B3BoundaryTraceEvidence writeB3BoundaryTrace(
    bool affordable,
    const std::string &suffix) {
    const double epsilon = 1e-6;
    const double required = 0.744;
    double available = required - epsilon;

    while (available + epsilon < required) {
        available = std::nextafter(available, required);
    }
    if (!affordable) {
        do {
            available = std::nextafter(available, 0.0);
        } while (available + epsilon >= required);
    }

    const std::string path =
        "/tmp/partsim_b3_boundary_trace_" + suffix + ".json";
    SchedulerTraceJob job{
        "boundary-task", 0.0, 1.0, 0, required, 1.0, 10.0};
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        trace.setSemanticTraceEnabled(true);
        MetaSim::SIMUL.initSingleRun();
        trace.logB3ASAPDecision(
            "ASAP-Block",
            "BLOCK",
            available,
            1,
            {job},
            affordable ? std::vector<SchedulerTraceJob>{job}
                       : std::vector<SchedulerTraceJob>{},
            {},
            affordable ? "selected_prefix"
                       : "highest_priority_energy_insufficient");
        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    EXPECT_TRUE(input.good());
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    const std::string available_token =
        readJsonScalar(contents, "available_energy_mJ");
    const std::string epsilon_token =
        readJsonScalar(contents, "native_epsilon_mJ");
    const std::string job_required_token =
        readJsonScalar(contents, "job_required_energy_mJ");
    const std::string decision_required_token =
        readJsonScalar(contents, "decision_required_energy_mJ");
    const std::string recorded_token =
        readJsonScalar(contents, "decision_energy_affordable");

    EXPECT_EQ(available + epsilon >= required, affordable);
    EXPECT_EQ(std::stod(available_token), available);
    EXPECT_EQ(std::stod(epsilon_token), epsilon);
    EXPECT_EQ(std::stod(job_required_token), required);
    EXPECT_EQ(std::stod(decision_required_token), required);

    return {
        std::stod(available_token),
        std::stod(epsilon_token),
        std::stod(decision_required_token),
        recorded_token == "true",
        available_token,
        epsilon_token,
        job_required_token,
        decision_required_token,
    };
}

TEST(B3TimingTrace, RoundTripsUnaffordableBinary64Boundary) {
    const auto evidence = writeB3BoundaryTrace(false, "unaffordable");

    EXPECT_FALSE(evidence.available_token.empty());
    EXPECT_NE(evidence.available_token.front(), '"');
    EXPECT_NE(evidence.epsilon_token.front(), '"');
    EXPECT_NE(evidence.job_required_token.front(), '"');
    EXPECT_NE(evidence.decision_required_token.front(), '"');
    EXPECT_NE(evidence.available_token, "0.743999");
    EXPECT_FALSE(evidence.recorded_affordable);
    EXPECT_FALSE(
        evidence.available + evidence.epsilon >= evidence.required);
}

TEST(B3TimingTrace, RoundTripsAffordableBinary64Boundary) {
    const auto evidence = writeB3BoundaryTrace(true, "affordable");

    EXPECT_TRUE(evidence.recorded_affordable);
    EXPECT_TRUE(
        evidence.available + evidence.epsilon >= evidence.required);
}

class Round2AccountingKernel : public Round2NullKernel {
    CPU *_cpu;

public:
    explicit Round2AccountingKernel(CPU *cpu) : _cpu(cpu) {}
    CPU *getProcessor(const AbsRTTask *) const override { return _cpu; }
    CPU *getOldProcessor(const AbsRTTask *) const override { return _cpu; }
};

class Round2CumulativeInstr : public Instr {
    Tick _duration;
    Tick _executed;
    Tick _last_schedule;
    bool _executing;

protected:
    Round2CumulativeInstr(const Round2CumulativeInstr &other)
        : Instr(other),
          _duration(other._duration),
          _executed(other._executed),
          _last_schedule(other._last_schedule),
          _executing(other._executing) {}

public:
    Round2CumulativeInstr(Task *task, Tick duration)
        : Instr(task, "round2-cumulative-instr"),
          _duration(duration),
          _executed(0),
          _last_schedule(0),
          _executing(false) {}

    CLONEABLE(Instr, Round2CumulativeInstr, override)

    void schedule() override {
        _last_schedule = MetaSim::SIMUL.getTime();
        _executing = true;
    }

    void deschedule() override {
        if (_executing)
            _executed += MetaSim::SIMUL.getTime() - _last_schedule;
        _executing = false;
    }

    void reset() override {
        _executed = 0;
        _last_schedule = 0;
        _executing = false;
    }

    Tick getExecTime() const override {
        return _executed + (_executing
            ? MetaSim::SIMUL.getTime() - _last_schedule : Tick(0));
    }

    double getActCycles() const override {
        return double(getExecTime());
    }

    Tick getDuration() const override { return _duration; }
    Tick getWCET() const override { return _duration; }
    void newRun() override { reset(); }
    void endRun() override {}
    void refreshExec(double, double) override {}
};

TEST(TaskExecutionAccounting, MultiplePreemptionsPreserveDeadlineMiss) {
    const std::string path =
        "/tmp/partsim_preemption_accounting_deadline_trace.json";
    {
        CPU cpu("deadline-accounting-cpu", nullptr);
        Round2AccountingKernel kernel(&cpu);
        PeriodicTask task(MetaSim::Tick(50), MetaSim::Tick(7),
                          MetaSim::Tick(0), "deadline-accounting-task");
        task.addInstr(
            std::make_unique<Round2CumulativeInstr>(&task, MetaSim::Tick(8)));
        task.setKernel(&kernel);
        JSONTrace trace(path, MetaSim::Tick(8));
        trace.attachToTask(task);

        MetaSim::SIMUL.initSingleRun();
        MetaSim::SIMUL.run_to(MetaSim::Tick(0));
        task.schedule();
        MetaSim::SIMUL.run_to(MetaSim::Tick(2));
        task.deschedule();
        MetaSim::SIMUL.run_to(MetaSim::Tick(3));
        task.schedule();

        // The first slice was [0,2). Resuming the same instruction must not
        // count that cumulative instruction time a second time.
        EXPECT_EQ(task.getExecTime(), MetaSim::Tick(2));
        EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 6.0);

        MetaSim::SIMUL.run_to(MetaSim::Tick(4));
        task.deschedule();
        MetaSim::SIMUL.run_to(MetaSim::Tick(5));
        task.schedule();
        MetaSim::SIMUL.run_to(MetaSim::Tick(7));
        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    ASSERT_TRUE(input.good());
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_NE(contents.find("\"event_type\": \"dline_miss\""),
              std::string::npos);
    EXPECT_NE(contents.find("\"deadline\": \"7\""), std::string::npos);
    EXPECT_NE(contents.find("\"remaining_execution_ms\": 3"),
              std::string::npos);
}

TEST(DeadlineLifecycle, NeverScheduledJobStillEmitsMiss) {
    const std::string path =
        "/tmp/partsim_never_scheduled_deadline_trace.json";
    {
        PeriodicTask task(MetaSim::Tick(50), MetaSim::Tick(5),
                          MetaSim::Tick(0), "never-scheduled-task");
        task.insertCode("fixed(2,control);");
        task.killOnMiss(false);
        JSONTrace trace(path, MetaSim::Tick(6));
        trace.attachToTask(task);
        MetaSim::SIMUL.run(MetaSim::Tick(6));
    }

    std::ifstream input(path);
    ASSERT_TRUE(input.good());
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_NE(contents.find("\"event_type\": \"dline_miss\""),
              std::string::npos);
    EXPECT_NE(contents.find("\"remaining_execution_ms\": 2"),
              std::string::npos);
}

class Round2BoundaryCompletionTask : public PeriodicTask {
public:
    explicit Round2BoundaryCompletionTask(const std::string &name)
        : PeriodicTask(MetaSim::Tick(50), MetaSim::Tick(2),
                       MetaSim::Tick(0), name) {
        insertCode("fixed(2,bzip2);");
    }

    void newRun() override {
        resetInstrQueue();
        state = TSK_READY;
        arrival = MetaSim::Tick(0);
        lastArrival = MetaSim::Tick(0);
        _dl = MetaSim::Tick(2);
        endEvt.post(MetaSim::Tick(2));
        deadEvt.post(MetaSim::Tick(2));
    }
};

class Round3ThrowingDisposableEvent : public MetaSim::Event {
    bool _throw_now;
    int *_destruction_count;

public:
    Round3ThrowingDisposableEvent(bool throw_now, int *destruction_count)
        : MetaSim::Event("round3-throwing-disposable"),
          _throw_now(throw_now),
          _destruction_count(destruction_count) {}

    ~Round3ThrowingDisposableEvent() override { ++(*_destruction_count); }

    void doit() override {
        if (_throw_now)
            throw std::runtime_error("round3 callback failure");
    }
};

class Round3LifecycleEntity : public MetaSim::Entity {
    Round2OutcomeEvent _keeper;

public:
    bool throw_now{false};
    bool throw_end{false};
    int new_run_count{0};
    int end_run_count{0};
    int disposable_destruction_count{0};

    explicit Round3LifecycleEntity(const std::string &name)
        : MetaSim::Entity(name), _keeper(name + "-keeper") {}

    void newRun() override {
        ++new_run_count;
        (new Round3ThrowingDisposableEvent(
             throw_now, &disposable_destruction_count))
            ->post(MetaSim::Tick(2), true);
        _keeper.post(MetaSim::Tick(10));
    }

    void endRun() override {
        ++end_run_count;
        _keeper.drop();
        if (throw_end)
            throw std::runtime_error("round4 endRun failure");
    }
};

class Round4InitializationEntity : public MetaSim::Entity {
    Round2OutcomeEvent _keeper;

public:
    bool throw_new{false};
    bool throw_end{false};
    int new_run_count{0};
    int end_run_count{0};

    explicit Round4InitializationEntity(const std::string &name)
        : MetaSim::Entity(name), _keeper(name + "-keeper") {}

    void newRun() override {
        ++new_run_count;
        if (throw_new)
            throw std::runtime_error("round4 newRun failure");
        _keeper.post(MetaSim::Tick(10));
    }

    void endRun() override {
        ++end_run_count;
        _keeper.drop();
        if (throw_end)
            throw std::runtime_error("round4 endRun failure");
    }
};

class Round3AlternatingTraceTask : public PeriodicTask {
public:
    bool complete_before_deadline;

    Round3AlternatingTraceTask(const std::string &name, bool complete)
        : PeriodicTask(MetaSim::Tick(50), MetaSim::Tick(2),
                       MetaSim::Tick(0), name),
          complete_before_deadline(complete) {
        insertCode("fixed(5,bzip2);");
    }

    void newRun() override {
        resetInstrQueue();
        state = TSK_READY;
        arrival = MetaSim::Tick(0);
        lastArrival = MetaSim::Tick(0);
        _dl = MetaSim::Tick(2);
        if (complete_before_deadline)
            endEvt.post(MetaSim::Tick(1));
        deadEvt.post(MetaSim::Tick(2));
    }
};

class Round5TransactionalStat : public MetaSim::BaseStat {
public:
    bool throw_init{false};
    bool throw_rollback{false};
    int init_count{0};
    int rollback_count{0};
    std::vector<std::string> *order{nullptr};

    Round5TransactionalStat(const std::string &name,
                            std::vector<std::string> *rollback_order = nullptr)
        : MetaSim::BaseStat(name), order(rollback_order) {}

    void record(double value) override { _val += value; }

    void initValue() override {
        ++init_count;
        _val = 0;
        if (throw_init)
            throw std::runtime_error("round5 stat init failure");
    }

    void captureRunState() override {
        MetaSim::BaseStat::captureRunState();
    }

    void rollbackRun() override {
        ++rollback_count;
        if (order)
            order->push_back(getName());
        MetaSim::BaseStat::rollbackRun();
        if (throw_rollback)
            throw std::runtime_error("round5 stat rollback failure");
    }
};

class Round6DerivedTransactionalStat : public MetaSim::BaseStat {
public:
    int derived_state{0};
    int derived_state_before_run{0};
    bool throw_collect{false};
    bool throw_init_after_mutation{false};

    explicit Round6DerivedTransactionalStat(const std::string &name)
        : MetaSim::BaseStat(name) {}

    void record(double value) override {
        _val += value;
        derived_state += static_cast<int>(value);
    }

    void initValue() override {
        _val = 0;
        derived_state = 0;
        if (throw_init_after_mutation) {
            derived_state = 777;
            throw std::runtime_error("round6 derived init failure");
        }
    }

    void captureRunState() override {
        MetaSim::BaseStat::captureRunState();
        derived_state_before_run = derived_state;
    }

    void rollbackRun() override {
        MetaSim::BaseStat::rollbackRun();
        derived_state = derived_state_before_run;
    }

    void collect() override {
        // Deliberately mutate derived state before the possible failure.  The
        // transaction must restore both this state and BaseStat's sample.
        derived_state += 1000;
        MetaSim::BaseStat::collect();
        if (throw_collect)
            throw std::runtime_error("round6 derived collect failure");
    }
};

TEST(SchedulerIdentity, NineCanonicalFactoryMappingsAreIndependent) {
    const std::vector<std::tuple<std::string, std::string, std::string>> cases = {
        {"gpfp_asap_block", "ASAP-Block", "GPFPASAPBlockScheduler"},
        {"gpfp_asap_nonblock", "ASAP-NonBlock", "GPFPASAPNonBlockScheduler"},
        {"gpfp_asap_sync", "ASAP-Sync", "GPFPASAPSyncScheduler"},
        {"gpfp_alap_block", "ALAP-Block", "GPFPALAPBlockScheduler"},
        {"gpfp_alap_nonblock", "ALAP-NonBlock", "GPFPALAPNonBlockScheduler"},
        {"gpfp_alap_sync", "ALAP-Sync", "GPFPALAPSyncScheduler"},
        {"gpfp_st_block", "ST-Block", "GPFPSTBlockScheduler"},
        {"gpfp_st_nonblock", "ST-NonBlock", "GPFPSTNonBlockScheduler"},
        {"gpfp_st_sync", "ST-Sync", "GPFPSTSyncScheduler"},
    };

    std::vector<std::string> rtti_names;
    for (const auto &[configured, display, implementation] : cases) {
        std::vector<std::string> params;
        auto scheduler = genericFactory<Scheduler>::instance().create(
            configured, params);
        ASSERT_NE(scheduler, nullptr) << configured;
        const auto identity = scheduler_identity_for(configured, *scheduler);
        EXPECT_EQ(identity.configured_scheduler, configured);
        EXPECT_EQ(identity.display_name, display);
        EXPECT_EQ(identity.implementation_id, implementation);
        EXPECT_FALSE(identity.rtti_name.empty());
        rtti_names.push_back(identity.rtti_name);
    }

    std::sort(rtti_names.begin(), rtti_names.end());
    EXPECT_EQ(std::unique(rtti_names.begin(), rtti_names.end()),
              rtti_names.end());

    std::vector<std::string> params;
    EXPECT_EQ(genericFactory<Scheduler>::instance().create(
                  "gpfp_unknown_audit_scheduler", params),
              nullptr);
}

TEST(STEnergyUtils, ChargingTimeUsesWattsToJoulesPerMillisecond) {
    EXPECT_EQ(STEnergy::estimateChargeTimeMs(0.1, 0.1), 1000);
    EXPECT_EQ(STEnergy::estimateChargeTimeMs(1.0, 0.1), 10000);
    EXPECT_EQ(STEnergy::estimateChargeTimeMs(1.0, 1.0), 1000);
    EXPECT_EQ(STEnergy::estimateChargeTimeMs(0.0, 1.0), 0);
    EXPECT_GT(STEnergy::estimateChargeTimeMs(1.0, 0.0), 10000);
    EXPECT_GT(STEnergy::estimateChargeTimeMs(1.0, INFINITY), 10000);
}

TEST(STEnergyUtils, BatteryFullBoundaryAndReleaseReasonAreCanonical) {
    const double capacity = 1.0;
    const double epsilon = STEnergy::kEnergyEpsilonJ;
    const double delta = epsilon / 2.0;

    EXPECT_FALSE(STEnergy::isBatteryFull(
        capacity - epsilon - delta, capacity));
    EXPECT_TRUE(STEnergy::isBatteryFull(capacity - epsilon, capacity));
    EXPECT_TRUE(STEnergy::isBatteryFull(
        capacity - epsilon + delta, capacity));
    EXPECT_TRUE(STEnergy::isBatteryFull(capacity, capacity));
    EXPECT_TRUE(STEnergy::isBatteryFull(capacity + delta, capacity));

    EXPECT_EQ(STEnergy::chargingReleaseReason(capacity, capacity, true),
              "battery_full_and_slack_exhausted");
    EXPECT_EQ(STEnergy::chargingReleaseReason(capacity, capacity, false),
              "battery_full");
    EXPECT_EQ(STEnergy::chargingReleaseReason(0.5, capacity, true),
              "slack_exhausted");
    EXPECT_TRUE(STEnergy::chargingReleaseReason(
                    0.5, capacity, false).empty());
}

TEST(SimulationOutcome, ReachedHorizonUsesActualLogicalTime) {
    Round2OutcomeEntity entity("round2-reached-horizon", MetaSim::Tick(10));
    MetaSim::SIMUL.run(MetaSim::Tick(5));

    const auto &outcome = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_EQ(outcome.actual_end_time, MetaSim::Tick(5));
    EXPECT_EQ(outcome.requested_end_time, MetaSim::Tick(5));
    EXPECT_TRUE(outcome.reached_requested_horizon);
    EXPECT_EQ(outcome.reason,
              MetaSim::SimulationCompletionReason::ReachedHorizon);
}

TEST(SimulationOutcome, EarlyEventQueueExhaustionIsExplicit) {
    Round2OutcomeEntity entity("round2-queue-exhausted", MetaSim::Tick(2));
    MetaSim::SIMUL.run(MetaSim::Tick(5));

    const auto &outcome = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_EQ(outcome.actual_end_time, MetaSim::Tick(2));
    EXPECT_EQ(outcome.requested_end_time, MetaSim::Tick(5));
    EXPECT_FALSE(outcome.reached_requested_horizon);
    EXPECT_EQ(outcome.reason,
              MetaSim::SimulationCompletionReason::EventQueueExhausted);
}

TEST(DeadlineTrace, ConstrainedDeadlineEmitsExactlyOneCanonicalMiss) {
    const std::string path = "/tmp/partsim_round2_deadline_trace.json";
    {
        PeriodicTask task(
            MetaSim::Tick(50), MetaSim::Tick(2), MetaSim::Tick(0),
            "round2-deadline-task");
        task.insertCode("fixed(5,bzip2);");
        task.killOnMiss(false);
        JSONTrace trace(path, MetaSim::Tick(3));
        trace.attachToTask(task);
        MetaSim::SIMUL.run(MetaSim::Tick(3));
        const auto &outcome = MetaSim::SIMUL.getLastRunOutcome();
        trace.setSimulationOutcome(
            outcome.actual_end_time,
            outcome.reached_requested_horizon,
            MetaSim::simulationCompletionReasonName(outcome.reason));
    }

    std::ifstream input(path);
    ASSERT_TRUE(input.good());
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    const std::string marker = "\"event_type\": \"dline_miss\"";
    const auto first = contents.find(marker);
    ASSERT_NE(first, std::string::npos);
    EXPECT_EQ(contents.find(marker, first + marker.size()), std::string::npos);
    EXPECT_NE(contents.find("\"deadline\": \"2\""), std::string::npos);
    EXPECT_NE(contents.find("\"remaining_execution_ms\": 5"),
              std::string::npos);
    EXPECT_NE(contents.find("\"trace_schema_version\": 2"),
              std::string::npos);
    EXPECT_NE(contents.find("\"observed_simulation_end_ms\": 3"),
              std::string::npos);
    EXPECT_NE(contents.find("\"simulation_completed\": true"),
              std::string::npos);
    EXPECT_NE(contents.find(
                  "\"simulation_completion_reason\": \"reached_horizon\""),
              std::string::npos);
}

TEST(DeadlineTrace, CompletionAtDeadlineSuppressesMiss) {
    const std::string path =
        "/tmp/partsim_round2_deadline_boundary_trace.json";
    {
        Round2BoundaryCompletionTask task("round2-boundary-task");
        Round2NullKernel kernel;
        task.setKernel(&kernel);
        Round2OutcomeEntity keeper("round2-boundary-keeper",
                                   MetaSim::Tick(4));
        JSONTrace trace(path, MetaSim::Tick(3));
        trace.enableObservabilitySummaries(MetaSim::Tick(3));
        trace.setSemanticTraceEnabled(false);
        trace.attachToTask(task);
        MetaSim::SIMUL.run(MetaSim::Tick(3));
        const auto &outcome = MetaSim::SIMUL.getLastRunOutcome();
        trace.setSimulationOutcome(
            outcome.actual_end_time,
            outcome.reached_requested_horizon,
            MetaSim::simulationCompletionReasonName(outcome.reason));
        for (std::int64_t tick = 0; tick < 3; ++tick) {
            DecisionRecord record;
            record.tick = tick;
            record.processor_count = 1;
            record.available_energy_j = 0.0;
            trace.observeDecision(record);
        }
        trace.finalizeObservabilitySummaries(MetaSim::Tick(3));
        const auto summaries = trace.perTaskLifecycleSummary();
        ASSERT_EQ(summaries.size(), 1u);
        EXPECT_EQ(summaries[0].task_name, "round2-boundary-task");
        EXPECT_EQ(summaries[0].released_jobs, 1u);
        EXPECT_EQ(summaries[0].completed_jobs, 1u);
        EXPECT_EQ(summaries[0].deadline_miss_jobs, 0u);
        EXPECT_EQ(summaries[0].completed_response_time_count, 1u);
    }

    std::ifstream input(path);
    ASSERT_TRUE(input.good());
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(contents.find("\"event_type\": \"dline_miss\""),
              std::string::npos);
    EXPECT_NE(contents.find("\"event_type\": \"end_instance\""),
              std::string::npos);
    EXPECT_NE(contents.find("\"simulation_completed\": true"),
              std::string::npos);
    EXPECT_NE(contents.find(
                  "\"simulation_completion_reason\": \"reached_horizon\""),
              std::string::npos);
}

TEST(TaskModelValidation, EnforcesConstrainedDeadlineContract) {
    EXPECT_NO_THROW(validateConstrainedDeadlineTask(
        "valid-constrained", MetaSim::Tick(2), MetaSim::Tick(5),
        MetaSim::Tick(10)));
    EXPECT_NO_THROW(validateConstrainedDeadlineTask(
        "valid-implicit", MetaSim::Tick(2), MetaSim::Tick(10),
        MetaSim::Tick(10)));

    for (const auto &values : std::vector<std::tuple<int, int, int>>{
             {2, 11, 10}, {6, 5, 10}, {0, 5, 10},
             {2, 0, 10}, {2, 5, 0}, {-1, 5, 10},
             {2, -1, 10}, {2, 5, -1}}) {
        EXPECT_THROW(
            validateConstrainedDeadlineTask(
                "invalid", MetaSim::Tick(std::get<0>(values)),
                MetaSim::Tick(std::get<1>(values)),
                MetaSim::Tick(std::get<2>(values))),
            InvalidTaskModel);
    }

    EXPECT_EQ(parseStrictTaskInteger("task", "deadline", "-1"), -1);
    EXPECT_EQ(parseStrictTaskInteger("task", "deadline", "+10"), 10);
    EXPECT_THROW(
        parseStrictTaskInteger("task", "deadline", "10abc"),
        InvalidTaskModel);
    EXPECT_THROW(
        parseStrictTaskInteger("task", "deadline", "true"),
        InvalidTaskModel);
}

TEST(SimulationOutcome, NewRunExceptionRollsBackOnlyInitializedEntities) {
    Round4InitializationEntity first("round4-new-first");
    Round4InitializationEntity throwing("round4-new-throwing");
    Round4InitializationEntity untouched("round4-new-untouched");
    throwing.throw_new = true;

    try {
        MetaSim::SIMUL.run(MetaSim::Tick(5));
        FAIL() << "newRun exception was not propagated";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round4 newRun failure");
    }
    const auto &failed = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_FALSE(failed.completed);
    EXPECT_FALSE(failed.reached_requested_horizon);
    EXPECT_EQ(failed.reason,
              MetaSim::SimulationCompletionReason::RuntimeError);
    EXPECT_EQ(failed.actual_end_time, MetaSim::Tick(0));
    EXPECT_EQ(first.new_run_count, 1);
    EXPECT_EQ(first.end_run_count, 1);
    EXPECT_EQ(throwing.new_run_count, 1);
    EXPECT_EQ(throwing.end_run_count, 0);
    EXPECT_EQ(untouched.new_run_count, 0);
    EXPECT_EQ(untouched.end_run_count, 0);

    throwing.throw_new = false;
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)));
    EXPECT_TRUE(MetaSim::SIMUL.getLastRunOutcome().completed);
    EXPECT_EQ(first.new_run_count, 2);
    EXPECT_EQ(first.end_run_count, 2);
    EXPECT_EQ(throwing.new_run_count, 2);
    EXPECT_EQ(throwing.end_run_count, 1);
    EXPECT_EQ(untouched.new_run_count, 1);
    EXPECT_EQ(untouched.end_run_count, 1);
}

TEST(SimulationOutcome, EndRunExceptionsPublishRuntimeErrorAfterFullCleanup) {
    Round4InitializationEntity first("round4-end-first");
    Round4InitializationEntity second("round4-end-second");
    Round4InitializationEntity third("round4-end-third");
    first.throw_end = true;
    second.throw_end = true;

    try {
        MetaSim::SIMUL.run(MetaSim::Tick(5));
        FAIL() << "endRun exception was not propagated";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round4 endRun failure");
    }
    const auto &failed = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_FALSE(failed.completed);
    EXPECT_FALSE(failed.reached_requested_horizon);
    EXPECT_EQ(failed.reason,
              MetaSim::SimulationCompletionReason::RuntimeError);
    EXPECT_EQ(failed.actual_end_time, MetaSim::Tick(5));
    EXPECT_EQ(first.end_run_count, 1);
    EXPECT_EQ(second.end_run_count, 1);
    EXPECT_EQ(third.end_run_count, 1);

    first.throw_end = false;
    second.throw_end = false;
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)));
    EXPECT_TRUE(MetaSim::SIMUL.getLastRunOutcome().completed);
}

TEST(SimulationOutcome, CallbackExceptionOutranksEndRunException) {
    Round3LifecycleEntity entity("round4-callback-cleanup-priority");
    entity.throw_now = true;
    entity.throw_end = true;
    try {
        MetaSim::SIMUL.run(MetaSim::Tick(5));
        FAIL() << "callback exception was not propagated";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round3 callback failure");
    }
    EXPECT_EQ(MetaSim::SIMUL.getLastRunOutcome().reason,
              MetaSim::SimulationCompletionReason::RuntimeError);
    EXPECT_EQ(MetaSim::SIMUL.getLastRunOutcome().actual_end_time,
              MetaSim::Tick(2));
    EXPECT_EQ(entity.disposable_destruction_count, 1);
    EXPECT_EQ(entity.end_run_count, 1);
}

TEST(SimulationOutcome, SuccessThenCallbackExceptionResetsAndCleansRun) {
    Round3LifecycleEntity entity("round3-success-exception");
    entity.throw_now = false;
    MetaSim::SIMUL.run(MetaSim::Tick(5));
    EXPECT_TRUE(MetaSim::SIMUL.getLastRunOutcome().completed);
    EXPECT_EQ(MetaSim::SIMUL.getLastRunOutcome().reason,
              MetaSim::SimulationCompletionReason::ReachedHorizon);

    entity.throw_now = true;
    EXPECT_THROW(
        MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    const auto &failed = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_EQ(failed.actual_end_time, MetaSim::Tick(2));
    EXPECT_FALSE(failed.reached_requested_horizon);
    EXPECT_FALSE(failed.completed);
    EXPECT_EQ(failed.reason,
              MetaSim::SimulationCompletionReason::RuntimeError);
    EXPECT_EQ(entity.new_run_count, 2);
    EXPECT_EQ(entity.end_run_count, 2);
    EXPECT_EQ(entity.disposable_destruction_count, 2);
}

TEST(SimulationOutcome, ExceptionThenSuccessAndRepeatedExceptionAreIsolated) {
    Round3LifecycleEntity entity("round3-exception-success");
    entity.throw_now = true;
    EXPECT_THROW(
        MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    EXPECT_EQ(MetaSim::SIMUL.getLastRunOutcome().reason,
              MetaSim::SimulationCompletionReason::RuntimeError);

    entity.throw_now = false;
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)));
    EXPECT_TRUE(MetaSim::SIMUL.getLastRunOutcome().completed);
    EXPECT_TRUE(
        MetaSim::SIMUL.getLastRunOutcome().reached_requested_horizon);

    entity.throw_now = true;
    EXPECT_THROW(
        MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    EXPECT_EQ(MetaSim::SIMUL.getLastRunOutcome().reason,
              MetaSim::SimulationCompletionReason::RuntimeError);
    EXPECT_EQ(entity.new_run_count, entity.end_run_count);
    EXPECT_EQ(entity.disposable_destruction_count, entity.new_run_count);
}

TEST(SimulationOutcome, ZeroHorizonCompletesAndNegativeHorizonIsRejected) {
    Round3LifecycleEntity entity("round3-zero-negative");
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(0)));
    const auto &zero = MetaSim::SIMUL.getLastRunOutcome();
    EXPECT_TRUE(zero.completed);
    EXPECT_TRUE(zero.reached_requested_horizon);
    EXPECT_EQ(zero.actual_end_time, MetaSim::Tick(0));
    EXPECT_EQ(entity.new_run_count, 1);
    EXPECT_EQ(entity.end_run_count, 1);

    EXPECT_THROW(
        MetaSim::SIMUL.run(MetaSim::Tick(-1)), std::invalid_argument);
    EXPECT_EQ(entity.new_run_count, 1);
    EXPECT_EQ(entity.end_run_count, 1);
}

TEST(BaseStatLifecycle, FailedMiddleInitializationRollsBackCapturedStats) {
    std::vector<std::string> rollback_order;
    Round5TransactionalStat first("round5-stat-first", &rollback_order);
    Round5TransactionalStat middle("round5-stat-middle", &rollback_order);
    Round5TransactionalStat last("round5-stat-last", &rollback_order);
    MetaSim::BaseStat::init(3);
    middle.throw_init = true;

    try {
        MetaSim::BaseStat::newRun();
        FAIL() << "expected BaseStat initialization failure";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round5 stat init failure");
    }

    EXPECT_EQ(first.getExpNum(), 0u);
    EXPECT_EQ(first.rollback_count, 1);
    EXPECT_EQ(middle.rollback_count, 1);
    EXPECT_EQ(last.init_count, 0);
    ASSERT_EQ(rollback_order.size(), 2u);
    EXPECT_EQ(rollback_order[0], "round5-stat-middle");
    EXPECT_EQ(rollback_order[1], "round5-stat-first");

    middle.throw_init = false;
    MetaSim::BaseStat::newRun();
    first.record(3);
    middle.record(4);
    last.record(5);
    MetaSim::BaseStat::endRun();
    EXPECT_EQ(first.getExpNum(), 1u);
    EXPECT_DOUBLE_EQ(first.getLastValue(), 3);
    EXPECT_DOUBLE_EQ(middle.getLastValue(), 4);
    EXPECT_DOUBLE_EQ(last.getLastValue(), 5);
}

TEST(BaseStatLifecycle, RollbackFailureDoesNotReplaceInitializationFailure) {
    Round5TransactionalStat first("round5-stat-primary-first");
    Round5TransactionalStat failing("round5-stat-primary-failing");
    MetaSim::BaseStat::init(2);
    first.throw_rollback = true;
    failing.throw_init = true;

    try {
        MetaSim::BaseStat::newRun();
        FAIL() << "expected BaseStat initialization failure";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round5 stat init failure");
    }
    EXPECT_EQ(first.getExpNum(), 0u);
    EXPECT_EQ(first.rollback_count, 1);
}

TEST(BaseStatLifecycle, FirstAndLastInitializationFailuresHaveExactOwnership) {
    std::vector<std::string> rollback_order;
    Round5TransactionalStat first("round5-stat-boundary-first", &rollback_order);
    Round5TransactionalStat middle("round5-stat-boundary-middle", &rollback_order);
    Round5TransactionalStat last("round5-stat-boundary-last", &rollback_order);

    MetaSim::BaseStat::init(3);
    first.throw_init = true;
    EXPECT_THROW(MetaSim::BaseStat::newRun(), std::runtime_error);
    ASSERT_EQ(rollback_order.size(), 1u);
    EXPECT_EQ(rollback_order[0], "round5-stat-boundary-first");
    EXPECT_EQ(middle.init_count, 0);
    EXPECT_EQ(first.getExpNum(), 0u);

    first.throw_init = false;
    last.throw_init = true;
    rollback_order.clear();
    MetaSim::BaseStat::init(3);
    EXPECT_THROW(MetaSim::BaseStat::newRun(), std::runtime_error);
    ASSERT_EQ(rollback_order.size(), 3u);
    EXPECT_EQ(rollback_order[0], "round5-stat-boundary-last");
    EXPECT_EQ(rollback_order[1], "round5-stat-boundary-middle");
    EXPECT_EQ(rollback_order[2], "round5-stat-boundary-first");
    EXPECT_EQ(last.rollback_count, 1);
    EXPECT_EQ(first.getExpNum(), 0u);
}

TEST(BaseStatLifecycle, CallbackFailureCancelsStatisticsWithoutCollecting) {
    Round5TransactionalStat stat("round5-stat-callback");
    Round3LifecycleEntity entity("round5-stat-callback-entity");
    entity.throw_now = true;

    EXPECT_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    EXPECT_EQ(stat.getExpNum(), 0u);
    EXPECT_EQ(stat.rollback_count, 1);

    entity.throw_now = false;
    MetaSim::SIMUL.run(MetaSim::Tick(5));
    EXPECT_EQ(stat.getExpNum(), 1u);
}

TEST(BaseStatLifecycle, EntityFinalizationFailureCancelsStatistics) {
    Round5TransactionalStat stat("round5-stat-endrun");
    Round3LifecycleEntity entity("round5-stat-endrun-entity");
    entity.throw_end = true;

    EXPECT_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    EXPECT_EQ(stat.getExpNum(), 0u);
    EXPECT_EQ(stat.rollback_count, 1);

    entity.throw_end = false;
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)));
    EXPECT_EQ(stat.getExpNum(), 1u);
}

TEST(BaseStatLifecycle, SimulationRecoversAfterStatInitializationFailure) {
    Round5TransactionalStat stat("round5-stat-simulation-init");
    Round3LifecycleEntity entity("round5-stat-simulation-init-entity");
    stat.throw_init = true;

    EXPECT_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)), std::runtime_error);
    EXPECT_EQ(stat.getExpNum(), 0u);
    EXPECT_EQ(entity.new_run_count, 1);
    EXPECT_EQ(entity.end_run_count, 1);

    stat.throw_init = false;
    EXPECT_NO_THROW(MetaSim::SIMUL.run(MetaSim::Tick(5)));
    EXPECT_EQ(stat.getExpNum(), 1u);
    EXPECT_EQ(entity.new_run_count, 2);
    EXPECT_EQ(entity.end_run_count, 2);
}

TEST(BaseStatLifecycle, MiddleCollectFailureRollsBackBaseAndDerivedState) {
    Round6DerivedTransactionalStat first("round6-derived-first");
    Round6DerivedTransactionalStat middle("round6-derived-middle");
    Round6DerivedTransactionalStat last("round6-derived-last");
    MetaSim::BaseStat::init(3);

    MetaSim::BaseStat::newRun();
    first.record(1);
    middle.record(2);
    last.record(3);
    middle.throw_collect = true;
    try {
        MetaSim::BaseStat::endRun();
        FAIL() << "expected derived collect failure";
    } catch (const std::runtime_error &error) {
        EXPECT_STREQ(error.what(), "round6 derived collect failure");
    }

    EXPECT_EQ(first.getExpNum(), 0u);
    EXPECT_EQ(first.derived_state, 0);
    EXPECT_EQ(middle.derived_state, 0);
    EXPECT_EQ(last.derived_state, 0);
    EXPECT_DOUBLE_EQ(first.getLastValue(), 0);
    EXPECT_DOUBLE_EQ(middle.getLastValue(), 0);

    middle.throw_collect = false;
    MetaSim::BaseStat::newRun();
    first.record(4);
    middle.record(5);
    last.record(6);
    EXPECT_NO_THROW(MetaSim::BaseStat::endRun());
    EXPECT_EQ(first.getExpNum(), 1u);
    EXPECT_DOUBLE_EQ(first.getLastValue(), 4);
    EXPECT_DOUBLE_EQ(middle.getLastValue(), 5);
    EXPECT_DOUBLE_EQ(last.getLastValue(), 6);
}

TEST(BaseStatLifecycle, FailingInitializerRestoresItsDerivedMutation) {
    Round6DerivedTransactionalStat stat("round6-derived-init");
    MetaSim::BaseStat::init(2);
    stat.derived_state = 41;
    stat.throw_init_after_mutation = true;

    EXPECT_THROW(MetaSim::BaseStat::newRun(), std::runtime_error);
    EXPECT_EQ(stat.getExpNum(), 0u);
    EXPECT_EQ(stat.derived_state, 41);

    stat.throw_init_after_mutation = false;
    EXPECT_NO_THROW(MetaSim::BaseStat::newRun());
    stat.record(9);
    EXPECT_NO_THROW(MetaSim::BaseStat::endRun());
    EXPECT_EQ(stat.getExpNum(), 1u);
    EXPECT_DOUBLE_EQ(stat.getLastValue(), 9);
}

static std::size_t countMarker(const std::string &contents,
                               const std::string &marker) {
    std::size_t count = 0;
    std::size_t position = 0;
    while ((position = contents.find(marker, position)) != std::string::npos) {
        ++count;
        position += marker.size();
    }
    return count;
}

TEST(DeadlineTrace, RepeatedRunMissDedupeIsRunLocal) {
    const std::string path = "/tmp/partsim_round3_repeated_miss.json";
    std::uint64_t first_generation = 0;
    std::uint64_t second_generation = 0;
    {
        PeriodicTask task(
            MetaSim::Tick(50), MetaSim::Tick(2), MetaSim::Tick(0),
            "round3-repeated-miss-task");
        task.insertCode("fixed(5,bzip2);");
        task.killOnMiss(false);
        JSONTrace trace(path, MetaSim::Tick(3));
        trace.attachToTask(task);

        MetaSim::SIMUL.run(MetaSim::Tick(3));
        first_generation = MetaSim::SIMUL.getRunGeneration();
        auto outcome = MetaSim::SIMUL.getLastRunOutcome();
        trace.setSimulationOutcome(
            outcome.actual_end_time, outcome.reached_requested_horizon,
            MetaSim::simulationCompletionReasonName(outcome.reason));

        MetaSim::SIMUL.run(MetaSim::Tick(3));
        second_generation = MetaSim::SIMUL.getRunGeneration();
        outcome = MetaSim::SIMUL.getLastRunOutcome();
        trace.setSimulationOutcome(
            outcome.actual_end_time, outcome.reached_requested_horizon,
            MetaSim::simulationCompletionReasonName(outcome.reason));
    }

    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(countMarker(contents, "\"event_type\": \"dline_miss\""), 2);
    EXPECT_EQ(countMarker(
                  contents,
                  "\"event_type\": \"simulation_run_outcome\""),
              2);
    EXPECT_NE(first_generation, second_generation);
    EXPECT_NE(contents.find(
                  "\"run_generation\": "
                  + std::to_string(first_generation)),
              std::string::npos);
    EXPECT_NE(contents.find(
                  "\"run_generation\": "
                  + std::to_string(second_generation)),
              std::string::npos);
    EXPECT_NE(contents.find("\"run_count\": 2"), std::string::npos);
    EXPECT_NE(contents.find(
                  "\"target_run_generation\": "
                  + std::to_string(second_generation)),
              std::string::npos);
    EXPECT_EQ(
        countMarker(contents, "\"event_type\": "),
        countMarker(contents, "\"run_generation\": ") - 1);
}

TEST(DeadlineTrace, AcceptedThenMissAndMissThenAcceptedDoNotLeakState) {
    for (const bool accepted_first : {true, false}) {
        const std::string path = accepted_first
            ? "/tmp/partsim_round3_accepted_then_miss.json"
            : "/tmp/partsim_round3_miss_then_accepted.json";
        {
            Round3AlternatingTraceTask task(
                accepted_first ? "round3-accepted-miss"
                               : "round3-miss-accepted",
                accepted_first);
            Round2NullKernel kernel;
            task.setKernel(&kernel);
            JSONTrace trace(path, MetaSim::Tick(3));
            trace.attachToTask(task);

            MetaSim::SIMUL.run(MetaSim::Tick(3));
            auto outcome = MetaSim::SIMUL.getLastRunOutcome();
            trace.setSimulationOutcome(
                outcome.actual_end_time, outcome.reached_requested_horizon,
                MetaSim::simulationCompletionReasonName(outcome.reason));

            task.complete_before_deadline = !accepted_first;
            MetaSim::SIMUL.run(MetaSim::Tick(3));
            outcome = MetaSim::SIMUL.getLastRunOutcome();
            trace.setSimulationOutcome(
                outcome.actual_end_time, outcome.reached_requested_horizon,
                MetaSim::simulationCompletionReasonName(outcome.reason));
        }

        std::ifstream input(path);
        const std::string contents(
            (std::istreambuf_iterator<char>(input)),
            std::istreambuf_iterator<char>());
        EXPECT_EQ(
            countMarker(contents, "\"event_type\": \"dline_miss\""), 1);
        EXPECT_EQ(countMarker(
                      contents,
                      "\"event_type\": \"simulation_run_outcome\""),
                  2);
    }
}

class ReleaseCutoffProbeTask : public PeriodicTask {
public:
    std::vector<MetaSim::Tick> observed_arrivals;

    explicit ReleaseCutoffProbeTask(const std::string &name)
        : PeriodicTask(
              MetaSim::Tick(10), MetaSim::Tick(10), MetaSim::Tick(0),
              name) {
        insertCode("fixed(1,bzip2);");
    }

protected:
    void handleArrival(MetaSim::Tick arrival) override {
        observed_arrivals.push_back(arrival);
    }
};

TEST(ReleaseCutoff, OptInSuppressesOnlyArrivalsAtOrAfterHorizon) {
    {
        ReleaseCutoffProbeTask task("release-cutoff-enabled");
        task.setReleaseCutoff(MetaSim::Tick(30));
        MetaSim::SIMUL.run(MetaSim::Tick(35));
        EXPECT_EQ(
            task.observed_arrivals,
            (std::vector<MetaSim::Tick>{
                MetaSim::Tick(0), MetaSim::Tick(10), MetaSim::Tick(20)}));
    }
    {
        ReleaseCutoffProbeTask task("release-cutoff-disabled");
        MetaSim::SIMUL.run(MetaSim::Tick(35));
        EXPECT_EQ(
            task.observed_arrivals,
            (std::vector<MetaSim::Tick>{
                MetaSim::Tick(0), MetaSim::Tick(10), MetaSim::Tick(20),
                MetaSim::Tick(30)}));
    }
}

TEST(ReleaseCutoffTrace, MetadataIsOptInAndRecordsCompletedWindow) {
    const std::string enabled_path =
        "/tmp/partsim_release_cutoff_trace_enabled.json";
    {
        JSONTrace trace(enabled_path, MetaSim::Tick(35));
        trace.setReleaseObservationWindow(
            MetaSim::Tick(30), MetaSim::Tick(35));
        trace.setSimulationOutcome(
            MetaSim::Tick(35), true, "reached_horizon");
    }
    std::ifstream enabled_input(enabled_path);
    const std::string enabled(
        (std::istreambuf_iterator<char>(enabled_input)),
        std::istreambuf_iterator<char>());
    EXPECT_NE(
        enabled.find(
            "\"simulator_trace_contract_version\": "
            "\"ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V2\""),
        std::string::npos);
    EXPECT_NE(
        enabled.find("\"release_horizon_ms\": 30"),
        std::string::npos);
    EXPECT_NE(
        enabled.find("\"observation_horizon_ms\": 35"),
        std::string::npos);
    EXPECT_NE(
        enabled.find("\"release_cutoff_enabled\": true"),
        std::string::npos);
    EXPECT_NE(
        enabled.find("\"observation_horizon_reached\": true"),
        std::string::npos);

    const std::string legacy_path =
        "/tmp/partsim_release_cutoff_trace_legacy.json";
    {
        JSONTrace trace(legacy_path, MetaSim::Tick(35));
        trace.setSimulationOutcome(
            MetaSim::Tick(35), true, "reached_horizon");
    }
    std::ifstream legacy_input(legacy_path);
    const std::string legacy(
        (std::istreambuf_iterator<char>(legacy_input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(
        legacy.find("\"release_cutoff_enabled\""),
        std::string::npos);
    EXPECT_EQ(
        legacy.find("\"simulator_trace_contract_version\""),
        std::string::npos);
    EXPECT_EQ(
        legacy.find("\"event_type\": \"release_energy_snapshot\""),
        std::string::npos);
}

TEST(ReleaseEnergySnapshot,
     RecordsExactPostHarvestPreConsumptionValueForEveryReleasedJob) {
    const std::string path =
        "/tmp/partsim_release_energy_snapshot_v2.json";
    const double available =
        std::nextafter(19999.962500164998, 20000.0);
    {
        JSONTrace trace(path, MetaSim::Tick(35));
        trace.setSchedulerIdentity(
            "gpfp_asap_block",
            "ASAP-Block",
            "GPFPASAPBlockScheduler");
        trace.setReleaseObservationWindow(
            MetaSim::Tick(30), MetaSim::Tick(35));
        MetaSim::SIMUL.initSingleRun();
        trace.logReleaseEnergySnapshots(
            "gpfp_asap_block",
            available,
            {
                {"v93_task_0", MetaSim::Tick(0)},
                {"v93_task_1", MetaSim::Tick(0)},
            });
        trace.setSimulationOutcome(
            MetaSim::Tick(35), true, "reached_horizon");
        MetaSim::SIMUL.endSingleRun();
    }
    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(
        countMarker(
            contents,
            "\"event_type\": \"release_energy_snapshot\""),
        2);
    EXPECT_EQ(
        countMarker(
            contents,
            "\"sampling_stage\": "
            "\"post_harvest_pre_consumption\""),
        2);
    EXPECT_EQ(
        countMarker(
            contents,
            "\"scheduler\": \"gpfp_asap_block\""),
        2);
    EXPECT_EQ(
        countMarker(
            contents,
            "\"trace_contract_version\": "
            "\"ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V2\""),
        2);
    const std::string token =
        readJsonScalar(contents, "available_energy_mJ");
    EXPECT_EQ(std::stod(token), available);
}

static DecisionJobRecord summaryDecisionJob(
    const std::string &job_id,
    std::uint32_t priority_rank,
    bool top4,
    bool candidate,
    bool infinite_demand,
    bool actual_dispatch,
    double incremental_energy_j,
    DecisionExclusionReason exclusion_reason) {
    DecisionJobRecord job;
    job.job_id = job_id;
    job.task_name = job_id.substr(0, job_id.find('@'));
    job.priority_rank = priority_rank;
    job.candidate = candidate;
    job.infinite_energy_dispatch_demand = infinite_demand;
    job.actual_dispatch = actual_dispatch;
    job.is_top4 = top4;
    job.is_bottom6 = !top4;
    job.incremental_energy_cost_j = incremental_energy_j;
    job.exclusion_reason = exclusion_reason;
    return job;
}

static DecisionRecord summaryDecision(
    std::int64_t tick,
    std::size_t processor_count,
    double available_energy_j,
    std::vector<DecisionJobRecord> jobs) {
    DecisionRecord record;
    record.tick = tick;
    record.processor_count = processor_count;
    record.available_energy_j = available_energy_j;
    record.energy_epsilon_j = 1e-9;
    record.jobs = std::move(jobs);
    return record;
}

TEST(MechanismSummaryCore, OpportunityWithoutActualBypass) {
    MechanismSummaryAccumulator accumulator(1);
    accumulator.observe(summaryDecision(
        0, 1, 1.0,
        {
            summaryDecisionJob(
                "high@0", 0, true, true, true, false, 2.0,
                DecisionExclusionReason::DirectEnergyShortage),
            summaryDecisionJob(
                "low@0", 1, false, true, false, false, 1.0,
                DecisionExclusionReason::BlockHeadOfLine),
        }));

    const MechanismSummary &summary = accumulator.finalize();
    EXPECT_EQ(summary.bypass_opportunity_ticks, 1u);
    EXPECT_EQ(summary.actual_bypass_ticks, 0u);
    EXPECT_EQ(summary.low_priority_bypass_core_ticks, 0u);
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 1u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 1u);
    EXPECT_EQ(summary.hp_energy_blocked_job_ticks, 1u);
    EXPECT_EQ(summary.observed_decision_ticks, 1u);
}

TEST(MechanismSummaryCore, NonBlockBypassCountsBottomSixCoreTicks) {
    MechanismSummaryAccumulator accumulator(1);
    accumulator.observe(summaryDecision(
        0, 3, 1.0,
        {
            summaryDecisionJob(
                "high@0", 0, true, true, true, false, 2.0,
                DecisionExclusionReason::DirectEnergyShortage),
            summaryDecisionJob(
                "low-a@0", 1, false, true, true, true, 0.4,
                DecisionExclusionReason::None),
            summaryDecisionJob(
                "low-b@0", 2, false, true, true, true, 0.4,
                DecisionExclusionReason::None),
        }));

    const MechanismSummary &summary = accumulator.finalize();
    EXPECT_EQ(summary.bypass_opportunity_ticks, 1u);
    EXPECT_EQ(summary.actual_bypass_ticks, 1u);
    EXPECT_EQ(summary.low_priority_bypass_core_ticks, 2u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 1u);
}

TEST(MechanismSummaryCore, AllEnergyReasonsBlockHighPriorityDemand) {
    const std::vector<DecisionExclusionReason> energy_reasons{
        DecisionExclusionReason::DirectEnergyShortage,
        DecisionExclusionReason::BlockHeadOfLine,
        DecisionExclusionReason::SyncAtomicUnaffordable,
        DecisionExclusionReason::StEnergyChargeWait,
    };
    MechanismSummaryAccumulator accumulator(energy_reasons.size());
    for (std::size_t tick = 0; tick < energy_reasons.size(); ++tick) {
        accumulator.observe(summaryDecision(
            static_cast<std::int64_t>(tick), 1, 0.0,
            {summaryDecisionJob(
                "high@" + std::to_string(tick),
                0, true, true, true, false, 1.0,
                energy_reasons[tick])}));
    }

    const MechanismSummary &summary = accumulator.finalize();
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 4u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 4u);
    EXPECT_EQ(summary.hp_energy_blocked_job_ticks, 4u);
}

TEST(MechanismSummaryCore, MultipleHighPriorityJobsUseOneWallTick) {
    MechanismSummaryAccumulator accumulator(1);
    accumulator.observe(summaryDecision(
        0, 2, 0.0,
        {
            summaryDecisionJob(
                "high-a@0", 0, true, true, true, false, 1.0,
                DecisionExclusionReason::SyncAtomicUnaffordable),
            summaryDecisionJob(
                "high-b@0", 1, true, true, true, false, 1.0,
                DecisionExclusionReason::SyncAtomicUnaffordable),
        }));

    const MechanismSummary &summary = accumulator.finalize();
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 1u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 1u);
    EXPECT_EQ(summary.hp_energy_blocked_job_ticks, 2u);
}

TEST(MechanismSummaryCore, CpuAndTimingDeferralAreNotEnergyBlocks) {
    MechanismSummaryAccumulator accumulator(1);
    accumulator.observe(summaryDecision(
        0, 1, 1.0,
        {
            summaryDecisionJob(
                "timing@0", 0, true, false, false, false, 0.0,
                DecisionExclusionReason::TimingDefer),
            summaryDecisionJob(
                "selected@0", 1, false, true, true, true, 0.1,
                DecisionExclusionReason::None),
            summaryDecisionJob(
                "cpu@0", 2, true, true, false, false, 0.1,
                DecisionExclusionReason::CpuCapacity),
        }));

    const MechanismSummary &summary = accumulator.finalize();
    EXPECT_EQ(summary.hp_dispatch_demand_ticks, 0u);
    EXPECT_EQ(summary.hp_energy_blocked_ticks, 0u);
    EXPECT_EQ(summary.hp_energy_blocked_job_ticks, 0u);
}

TEST(MechanismSummaryCore, RejectsDuplicateOutOfOrderAndHorizonTick) {
    const DecisionRecord tick_zero = summaryDecision(
        0, 1, 0.0,
        {summaryDecisionJob(
            "job@0", 0, true, true, true, true, 0.0,
            DecisionExclusionReason::None)});

    MechanismSummaryAccumulator duplicate(2);
    duplicate.observe(tick_zero);
    EXPECT_THROW(duplicate.observe(tick_zero), std::logic_error);
    EXPECT_THROW((void)duplicate.finalize(), std::logic_error);

    MechanismSummaryAccumulator out_of_order(2);
    EXPECT_THROW(
        out_of_order.observe(summaryDecision(
            1, 1, 0.0,
            {summaryDecisionJob(
                "job@1", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)})),
        std::logic_error);

    MechanismSummaryAccumulator horizon(2);
    EXPECT_THROW(
        horizon.observe(summaryDecision(
            2, 1, 0.0,
            {summaryDecisionJob(
                "job@2", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)})),
        std::out_of_range);
}

TEST(MechanismSummaryCore, RejectsUnknownReasonAndIncompleteRecord) {
    MechanismSummaryAccumulator unknown(1);
    EXPECT_THROW(
        unknown.observe(summaryDecision(
            0, 1, 0.0,
            {summaryDecisionJob(
                "job@0", 0, true, true, true, false, 1.0,
                static_cast<DecisionExclusionReason>(255))})),
        std::invalid_argument);

    MechanismSummaryAccumulator incomplete(1);
    DecisionJobRecord job = summaryDecisionJob(
        "job@0", 0, true, true, true, false, 1.0,
        DecisionExclusionReason::DirectEnergyShortage);
    job.task_name.clear();
    EXPECT_THROW(
        incomplete.observe(summaryDecision(0, 1, 0.0, {job})),
        std::invalid_argument);
}

TEST(MechanismSummaryCore,
     SummariesAreDisabledByDefaultAndSchemaTwoOutcomeDoesNotFinalize) {
    const std::string path =
        "/tmp/partsim_shared_summary_schema2_default.json";
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        MetaSim::SIMUL.initSingleRun();

        EXPECT_FALSE(trace.observabilitySummariesEnabled());
        EXPECT_FALSE(trace.observabilitySummariesFinalized());
        EXPECT_EQ(
            trace.observabilitySummaryState(),
            ObservabilitySummaryState::Disabled);
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 0u);
        EXPECT_TRUE(trace.perTaskLifecycleSummary().empty());
        EXPECT_EQ(trace.traceSchemaVersion(), 2);
        EXPECT_FALSE(trace.b4ObservabilitySchemaEnabled());
        EXPECT_FALSE(trace.observabilityPayloadSealed());
        EXPECT_NO_THROW(trace.setSimulationOutcome(
            MetaSim::Tick(2), true, "completed"));
        EXPECT_FALSE(trace.observabilitySummariesFinalized());
        EXPECT_THROW(
            trace.observeDecision(summaryDecision(
                0, 1, 0.0,
                {summaryDecisionJob(
                    "job@0", 0, true, true, true, true, 0.0,
                    DecisionExclusionReason::None)})),
            std::logic_error);
        EXPECT_THROW(
            trace.finalizeObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);

        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_NE(
        contents.find("\"event_type\": \"simulation_run_outcome\""),
        std::string::npos);
    EXPECT_NE(contents.find("\"trace_schema_version\": 2"),
              std::string::npos);
    EXPECT_EQ(contents.find("\"mechanism_summary\""), std::string::npos);
    EXPECT_EQ(contents.find("\"energy_summary\""), std::string::npos);
    EXPECT_EQ(contents.find("\"per_task_summary\""), std::string::npos);
    EXPECT_EQ(
        contents.find("\"observability_summary_contract_version\""),
        std::string::npos);
}

TEST(MechanismSummaryCore, SetOutcomeDoesNotBypassExplicitFinalizeGate) {
    const std::string path =
        "/tmp/partsim_shared_summary_explicit_finalize.json";
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        MetaSim::SIMUL.initSingleRun();
        trace.enableObservabilitySummaries(MetaSim::Tick(2));
        trace.observeDecision(summaryDecision(
            0, 1, 0.0,
            {summaryDecisionJob(
                "job@0", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)}));

        EXPECT_NO_THROW(trace.setSimulationOutcome(
            MetaSim::Tick(2), true, "completed"));
        EXPECT_FALSE(trace.observabilitySummariesFinalized());
        EXPECT_THROW(
            trace.finalizeObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);
        EXPECT_FALSE(trace.observabilitySummariesFinalized());

        MetaSim::SIMUL.endSingleRun();
    }
}

TEST(MechanismSummaryCore,
     LifecycleStateRejectsDuplicateCallsWithoutResettingData) {
    const std::string path =
        "/tmp/partsim_shared_summary_lifecycle_state.json";
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        MetaSim::SIMUL.initSingleRun();
        trace.enableObservabilitySummaries(MetaSim::Tick(2));
        EXPECT_EQ(
            trace.observabilitySummaryState(),
            ObservabilitySummaryState::Enabled);

        trace.observeDecision(summaryDecision(
            0, 1, 0.0,
            {summaryDecisionJob(
                "job@0", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)}));
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 1u);
        EXPECT_THROW(
            trace.enableObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 1u);
        EXPECT_THROW(
            trace.finalizeObservabilitySummaries(MetaSim::Tick(1)),
            std::invalid_argument);
        EXPECT_EQ(
            trace.observabilitySummaryState(),
            ObservabilitySummaryState::Enabled);

        trace.observeDecision(summaryDecision(
            1, 1, 0.0,
            {summaryDecisionJob(
                "job@1", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)}));
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));
        EXPECT_EQ(
            trace.observabilitySummaryState(),
            ObservabilitySummaryState::Finalized);
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 2u);

        EXPECT_THROW(
            trace.finalizeObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);
        EXPECT_THROW(
            trace.enableObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);
        EXPECT_THROW(
            trace.observeDecision(summaryDecision(
                2, 1, 0.0,
                {summaryDecisionJob(
                    "job@2", 0, true, true, true, true, 0.0,
                    DecisionExclusionReason::None)})),
            std::logic_error);
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 2u);

        MetaSim::SIMUL.endSingleRun();
    }
}

TEST(MechanismSummaryCore, SemanticTraceSwitchDoesNotGateSummary) {
    const std::string path =
        "/tmp/partsim_shared_summary_semantic_independence.json";
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        MetaSim::SIMUL.initSingleRun();
        trace.enableObservabilitySummaries(MetaSim::Tick(2));
        trace.setSemanticTraceEnabled(false);
        trace.observeDecision(summaryDecision(
            0, 1, 0.0,
            {summaryDecisionJob(
                "job@0", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)}));
        trace.setSemanticTraceEnabled(true);
        trace.observeDecision(summaryDecision(
            1, 1, 0.0,
            {summaryDecisionJob(
                "job@1", 0, true, true, true, true, 0.0,
                DecisionExclusionReason::None)}));
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));
        const MechanismSummary &summary = trace.mechanismSummary();
        EXPECT_EQ(summary.observed_decision_ticks, 2u);
        EXPECT_TRUE(trace.observabilitySummariesFinalized());
        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(contents.find("\"mechanism_summary\""), std::string::npos);
    EXPECT_NE(contents.find("\"trace_schema_version\": 2"),
              std::string::npos);
}

TEST(PerTaskLifecycleCore, AggregatesTerminalAndHorizonStates) {
    PerTaskLifecycleAccumulator lifecycle;
    lifecycle.reset(10);

    lifecycle.onRelease("completed", 0);
    lifecycle.onSchedule("completed", 0, 0);
    lifecycle.onSchedule("completed", 0, 2);
    lifecycle.onDeadlineMiss("completed", 0, 5);
    lifecycle.onDeadlineMiss("completed", 0, 6);
    lifecycle.onCompletion("completed", 0, 10);

    lifecycle.onRelease("killed", 1);
    lifecycle.onSchedule("killed", 1, 1);
    lifecycle.onKill("killed", 1, 4);

    lifecycle.onRelease("unfinished", 2);
    lifecycle.onSchedule("unfinished", 2, 5);

    lifecycle.onRelease("horizon-release", 10);
    lifecycle.onCompletion("horizon-release", 10, 10);
    lifecycle.finalize(10);

    const auto summaries = lifecycle.summaries();
    ASSERT_EQ(summaries.size(), 3u);

    const auto find_summary = [&summaries](const std::string &name)
        -> const PerTaskLifecycleSummary & {
        const auto it = std::find_if(
            summaries.begin(), summaries.end(),
            [&name](const PerTaskLifecycleSummary &summary) {
                return summary.task_name == name;
            });
        EXPECT_NE(it, summaries.end());
        return *it;
    };

    const auto &completed = find_summary("completed");
    EXPECT_EQ(completed.released_jobs, 1u);
    EXPECT_EQ(completed.completed_jobs, 1u);
    EXPECT_EQ(completed.deadline_miss_jobs, 1u);
    EXPECT_EQ(completed.unfinished_at_horizon_jobs, 0u);
    EXPECT_EQ(completed.executed_core_ticks, 10u);
    EXPECT_EQ(completed.completed_response_time_count, 1u);
    EXPECT_EQ(completed.completed_response_time_sum_ms, 10u);
    EXPECT_EQ(completed.completed_response_time_max_ms, 10u);

    const auto &killed = find_summary("killed");
    EXPECT_EQ(killed.completed_jobs, 0u);
    EXPECT_EQ(killed.terminated_jobs, 1u);
    EXPECT_EQ(killed.completed_response_time_count, 0u);
    EXPECT_EQ(killed.unfinished_at_horizon_jobs, 0u);
    EXPECT_EQ(killed.executed_core_ticks, 3u);

    const auto &unfinished = find_summary("unfinished");
    EXPECT_EQ(unfinished.completed_jobs, 0u);
    EXPECT_EQ(unfinished.unfinished_at_horizon_jobs, 1u);
    EXPECT_EQ(unfinished.executed_core_ticks, 5u);
}

TEST(PerTaskLifecycleCore, MigrationCloseOpenDoesNotDoubleCount) {
    PerTaskLifecycleAccumulator lifecycle;
    lifecycle.reset(8);
    lifecycle.onRelease("migrating", 0);
    lifecycle.onSchedule("migrating", 0, 0);
    lifecycle.onDeschedule("migrating", 0, 3);
    lifecycle.onSchedule("migrating", 0, 3);
    lifecycle.onSchedule("migrating", 0, 3);
    lifecycle.onCompletion("migrating", 0, 6);
    lifecycle.finalize(8);

    const auto summaries = lifecycle.summaries();
    ASSERT_EQ(summaries.size(), 1u);
    EXPECT_EQ(summaries[0].executed_core_ticks, 6u);
    EXPECT_EQ(summaries[0].completed_jobs, 1u);
}

TEST(PerTaskLifecycleCore,
     RetainsOnlyActiveIdentitiesAcrossCompletedAndKilledMisses) {
    PerTaskLifecycleAccumulator lifecycle;
    lifecycle.reset(100);

    constexpr std::int64_t historical_job_count = 20;
    for (std::int64_t job_index = 0;
         job_index < historical_job_count;
         ++job_index) {
        const std::int64_t release_time = job_index * 4;
        lifecycle.onRelease("periodic", release_time);
        lifecycle.onDeadlineMiss(
            "periodic", release_time, release_time + 1);
        lifecycle.onDeadlineMiss(
            "periodic", release_time, release_time + 2);
        if (job_index % 2 == 0) {
            lifecycle.onCompletion(
                "periodic", release_time, release_time + 3);
        } else {
            lifecycle.onKill(
                "periodic", release_time, release_time + 3);
        }
        EXPECT_EQ(lifecycle.retainedJobIdentityCount(), 0u);
        EXPECT_FALSE(lifecycle.hasActiveJob(
            "periodic", release_time));
    }

    lifecycle.onRelease("periodic", 90);
    lifecycle.onDeadlineMiss("periodic", 90, 91);
    lifecycle.onDeadlineMiss("periodic", 90, 92);
    EXPECT_EQ(lifecycle.retainedJobIdentityCount(), 1u);
    lifecycle.finalize(100);
    EXPECT_EQ(lifecycle.retainedJobIdentityCount(), 0u);

    const auto summaries = lifecycle.summaries();
    ASSERT_EQ(summaries.size(), 1u);
    EXPECT_EQ(summaries[0].released_jobs, 21u);
    EXPECT_EQ(summaries[0].completed_jobs, 10u);
    EXPECT_EQ(summaries[0].terminated_jobs, 10u);
    EXPECT_EQ(summaries[0].deadline_miss_jobs, 21u);
    EXPECT_EQ(summaries[0].unfinished_at_horizon_jobs, 1u);
    EXPECT_EQ(summaries[0].completed_response_time_count, 10u);
}

TEST(PerTaskLifecycleCore, FinalizedAccumulatorRejectsFurtherWrites) {
    PerTaskLifecycleAccumulator lifecycle;
    lifecycle.reset(2);
    lifecycle.onRelease("finalized", 0);
    lifecycle.onDeadlineMiss("finalized", 0, 1);
    lifecycle.finalize(2);

    EXPECT_THROW(
        lifecycle.onRelease("late", 1),
        std::logic_error);
    EXPECT_THROW(
        lifecycle.onCompletion("finalized", 0, 2),
        std::logic_error);
}

TEST(ObservabilityReleaseIntegration,
     IndependentHorizonAllowsReleaseTailAfterSummaryFinalize) {
    const std::string path =
        "/tmp/partsim_summary_release_independent_horizons.json";
    {
        Round2OutcomeEntity keeper(
            "summary-release-tail-keeper", MetaSim::Tick(6));
        JSONTrace trace(path, MetaSim::Tick(5));
        trace.setSchedulerIdentity(
            "gpfp_asap_block",
            "ASAP-Block",
            "GPFPASAPBlockScheduler");
        trace.setReleaseObservationWindow(
            MetaSim::Tick(4), MetaSim::Tick(5));
        trace.setSemanticTraceEnabled(false);

        MetaSim::SIMUL.initSingleRun();
        EXPECT_THROW(
            trace.enableObservabilitySummaries(MetaSim::Tick(0)),
            std::invalid_argument);
        EXPECT_THROW(
            trace.enableObservabilitySummaries(MetaSim::Tick(6)),
            std::invalid_argument);
        trace.enableObservabilitySummaries(MetaSim::Tick(2));
        trace.observeDecision(summaryDecision(0, 1, 1.0, {}));
        trace.observeDecision(summaryDecision(1, 1, 1.0, {}));
        MetaSim::SIMUL.run_to(MetaSim::Tick(2));
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));

        EXPECT_TRUE(trace.observabilitySummariesFinalized());
        EXPECT_THROW(
            trace.observeDecision(summaryDecision(2, 1, 1.0, {})),
            std::logic_error);

        MetaSim::SIMUL.run_to(MetaSim::Tick(3));
        EXPECT_NO_THROW(trace.logReleaseEnergySnapshots(
            "gpfp_asap_block",
            1750.25,
            {{"release-tail-task", MetaSim::Tick(3)}}));
        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(
        countMarker(
            contents,
            "\"event_type\": \"release_energy_snapshot\""),
        1);
    EXPECT_NE(
        contents.find("\"time\": \"3\""),
        std::string::npos);
    EXPECT_NE(
        contents.find(
            "\"sampling_stage\": "
            "\"post_harvest_pre_consumption\""),
        std::string::npos);
    EXPECT_NE(
        contents.find("\"available_energy_mJ\": 1750.25"),
        std::string::npos);
    EXPECT_EQ(contents.find("\"mechanism_summary\""), std::string::npos);
    EXPECT_NE(contents.find("\"trace_schema_version\": 2"),
              std::string::npos);
}

TEST(ObservabilityReleaseIntegration,
     NewRunResetsSummaryTicksAndReleaseSnapshotIdentity) {
    const std::string path =
        "/tmp/partsim_summary_release_new_run_reset.json";
    {
        Round2OutcomeEntity keeper(
            "summary-release-reset-keeper", MetaSim::Tick(6));
        JSONTrace trace(path, MetaSim::Tick(5));
        trace.setSchedulerIdentity(
            "gpfp_asap_block",
            "ASAP-Block",
            "GPFPASAPBlockScheduler");
        trace.setReleaseObservationWindow(
            MetaSim::Tick(4), MetaSim::Tick(5));

        MetaSim::SIMUL.initSingleRun();
        trace.enableObservabilitySummaries(MetaSim::Tick(2));
        trace.logReleaseEnergySnapshots(
            "gpfp_asap_block",
            1000.0,
            {{"same-job", MetaSim::Tick(0)}});
        trace.observeDecision(summaryDecision(0, 1, 1.0, {}));
        trace.observeDecision(summaryDecision(1, 1, 1.0, {}));
        MetaSim::SIMUL.run_to(MetaSim::Tick(2));
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));
        MetaSim::SIMUL.endSingleRun();

        MetaSim::SIMUL.initSingleRun();
        EXPECT_NO_THROW(trace.logReleaseEnergySnapshots(
            "gpfp_asap_block",
            1000.0,
            {{"same-job", MetaSim::Tick(0)}}));
        EXPECT_TRUE(trace.observabilitySummariesEnabled());
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 0u);
        EXPECT_TRUE(trace.perTaskLifecycleSummary().empty());
        trace.observeDecision(summaryDecision(0, 1, 1.0, {}));
        trace.observeDecision(summaryDecision(1, 1, 1.0, {}));
        MetaSim::SIMUL.run_to(MetaSim::Tick(2));
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 2u);
        MetaSim::SIMUL.endSingleRun();
    }

    std::ifstream input(path);
    const std::string contents(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
    EXPECT_EQ(
        countMarker(
            contents,
            "\"event_type\": \"release_energy_snapshot\""),
        2);
    EXPECT_NE(contents.find("\"run_count\": 2"), std::string::npos);
}

static std::vector<ObservabilityTaskMetadata> b4Schema3Metadata(
    bool special_name = false) {
    std::vector<ObservabilityTaskMetadata> metadata;
    for (std::uint32_t rank = 10; rank > 0; --rank) {
        const std::uint32_t actual_rank = rank - 1;
        metadata.push_back({
            special_name && actual_rank == 3
                ? std::string("special-\"-\\-\n-task")
                : std::string("task-") + std::to_string(actual_rank),
            actual_rank,
        });
    }
    return metadata;
}

static EnergySummary b4Schema3Energy(std::uint64_t horizon) {
    EnergySummary summary;
    summary.offered_energy_j = 0.3;
    summary.credited_energy_j = 0.2;
    summary.clipped_energy_j = 0.1;
    summary.consumed_energy_j = 0.05;
    summary.battery_min_j = 0.1;
    summary.battery_max_j = 1.0;
    summary.battery_final_j = 0.5;
    summary.battery_empty_ticks = 0;
    summary.battery_full_ticks = horizon > 0 ? 1 : 0;
    summary.observed_energy_intervals = horizon;
    return summary;
}

static void observeEmptySchema3Horizon(
    JSONTrace &trace,
    std::uint64_t horizon,
    std::size_t processor_count = 4) {
    for (std::uint64_t tick = 0; tick < horizon; ++tick) {
        trace.observeDecision(summaryDecision(
            static_cast<std::int64_t>(tick),
            processor_count,
            1.0,
            {}));
    }
}

static std::string readFileContents(const std::string &path) {
    std::ifstream input(path);
    EXPECT_TRUE(input.good());
    return std::string(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());
}

TEST(ObservabilitySchema3Core,
     ValidSealedTraceHasExactBlocksOrderAndStableRankOrder) {
    const std::string path =
        "/tmp/partsim_b4_schema3_valid_sealed.json";
    {
        JSONTrace trace(path, MetaSim::Tick(2));
        MetaSim::SIMUL.initSingleRun();
        trace.configureB4ObservabilitySchema3(
            MetaSim::Tick(2), b4Schema3Metadata());
        EXPECT_EQ(trace.traceSchemaVersion(), 3);
        EXPECT_TRUE(trace.b4ObservabilitySchemaEnabled());
        EXPECT_FALSE(trace.observabilityPayloadSealed());
        ASSERT_EQ(trace.perTaskLifecycleSummary().size(), 10u);
        observeEmptySchema3Horizon(trace, 2);
        trace.setSimulationOutcome(MetaSim::Tick(2), true, "completed");
        trace.finalizeObservabilitySummaries(MetaSim::Tick(2));
        trace.setObservabilityEnergySummary(b4Schema3Energy(2));
        trace.sealObservabilityPayloadForSerialization();
        EXPECT_TRUE(trace.observabilityPayloadSealed());
        MetaSim::SIMUL.endSingleRun();
    }

    const std::string contents = readFileContents(path);
    EXPECT_NE(contents.find("\"trace_schema_version\": 3"), std::string::npos);
    const std::vector<std::string> top_level_markers = {
        "    \"observability_summary_contract_version\": ",
        "    \"observability_summary_horizon_ms\": ",
        "    \"mechanism_summary\": ",
        "    \"energy_summary\": ",
        "    \"per_task_summary\": ",
        "    \"simulation_completion_reason\": ",
    };
    std::size_t previous = 0;
    for (const auto &marker : top_level_markers) {
        const std::size_t position = contents.find(marker);
        ASSERT_NE(position, std::string::npos) << marker;
        EXPECT_GE(position, previous) << marker;
        previous = position;
    }
    EXPECT_EQ(countMarker(contents, "\"priority_rank\": "), 10u);
    EXPECT_EQ(countMarker(contents, "\"is_top4\": true"), 4u);
    EXPECT_EQ(countMarker(contents, "\"is_top4\": false"), 6u);
    EXPECT_EQ(countMarker(contents, "\"is_bottom6\": true"), 6u);
    EXPECT_EQ(countMarker(contents, "\"is_bottom6\": false"), 4u);
    previous = 0;
    for (std::uint32_t rank = 0; rank < 10; ++rank) {
        const std::string marker =
            "\"priority_rank\": " + std::to_string(rank);
        const std::size_t position = contents.find(marker, previous);
        ASSERT_NE(position, std::string::npos) << marker;
        EXPECT_GE(position, previous);
        previous = position;
    }
    EXPECT_EQ(countMarker(contents, "\"released_jobs\": 0"), 10u);
    const std::vector<std::string> mechanism_markers = {
        "\"bypass_opportunity_ticks\": ",
        "\"actual_bypass_ticks\": ",
        "\"low_priority_bypass_core_ticks\": ",
        "\"hp_dispatch_demand_ticks\": ",
        "\"hp_energy_blocked_ticks\": ",
        "\"hp_energy_blocked_job_ticks\": ",
        "\"observed_decision_ticks\": ",
    };
    previous = contents.find("    \"mechanism_summary\": ");
    for (const auto &marker : mechanism_markers) {
        const std::size_t position = contents.find(marker, previous);
        ASSERT_NE(position, std::string::npos) << marker;
        EXPECT_GE(position, previous) << marker;
        previous = position;
    }
    const std::vector<std::string> energy_markers = {
        "\"offered_energy_j\": ",
        "\"credited_energy_j\": ",
        "\"clipped_energy_j\": ",
        "\"consumed_energy_j\": ",
        "\"battery_min_j\": ",
        "\"battery_max_j\": ",
        "\"battery_final_j\": ",
        "\"battery_empty_ticks\": ",
        "\"battery_full_ticks\": ",
        "\"observed_energy_intervals\": ",
    };
    previous = contents.find("    \"energy_summary\": ");
    for (const auto &marker : energy_markers) {
        const std::size_t position = contents.find(marker, previous);
        ASSERT_NE(position, std::string::npos) << marker;
        EXPECT_GE(position, previous) << marker;
        previous = position;
    }
    const std::vector<std::string> task_markers = {
        "\"task_name\": ",
        "\"priority_rank\": ",
        "\"is_top4\": ",
        "\"is_bottom6\": ",
        "\"released_jobs\": ",
        "\"completed_jobs\": ",
        "\"terminated_jobs\": ",
        "\"deadline_miss_jobs\": ",
        "\"unfinished_at_horizon_jobs\": ",
        "\"executed_core_ticks\": ",
        "\"completed_response_time_count\": ",
        "\"completed_response_time_sum_ms\": ",
        "\"completed_response_time_max_ms\": ",
    };
    previous = contents.find("    \"per_task_summary\": ");
    for (const auto &marker : task_markers) {
        const std::size_t position = contents.find(marker, previous);
        ASSERT_NE(position, std::string::npos) << marker;
        EXPECT_GE(position, previous) << marker;
        previous = position;
    }
    EXPECT_EQ(contents.find("_exact\""), std::string::npos);
}

TEST(ObservabilitySchema3Core, SerializesMaxDigits10AndEscapesTaskNames) {
    const std::string path =
        "/tmp/partsim_b4_schema3_binary64_escape.json";
    const double exact_value = std::nextafter(0.1, 1.0);
    {
        JSONTrace trace(path, MetaSim::Tick(1));
        MetaSim::SIMUL.initSingleRun();
        trace.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata(true));
        observeEmptySchema3Horizon(trace, 1);
        trace.finalizeObservabilitySummaries(MetaSim::Tick(1));
        EnergySummary energy = b4Schema3Energy(1);
        energy.offered_energy_j = exact_value;
        energy.credited_energy_j = exact_value;
        energy.clipped_energy_j = 0.0;
        trace.setObservabilityEnergySummary(energy);
        trace.sealObservabilityPayloadForSerialization();
        MetaSim::SIMUL.endSingleRun();
    }
    const std::string contents = readFileContents(path);
    const std::string token = readJsonScalar(contents, "offered_energy_j");
    EXPECT_EQ(std::stod(token), exact_value);
    EXPECT_NE(
        contents.find("special-\\\"-\\\\-\\n-task"),
        std::string::npos);
}

TEST(ObservabilitySchema3Core,
     RejectsInvalidTaskCountsNamesRanksAndDuplicateConfiguration) {
    MetaSim::SIMUL.initSingleRun();
    {
        JSONTrace fewer("/tmp/partsim_schema3_fewer.json", MetaSim::Tick(1));
        auto metadata = b4Schema3Metadata();
        metadata.pop_back();
        EXPECT_THROW(
            fewer.configureB4ObservabilitySchema3(MetaSim::Tick(1), metadata),
            std::invalid_argument);
    }
    {
        JSONTrace more("/tmp/partsim_schema3_more.json", MetaSim::Tick(1));
        auto metadata = b4Schema3Metadata();
        metadata.push_back({"extra", 10});
        EXPECT_THROW(
            more.configureB4ObservabilitySchema3(MetaSim::Tick(1), metadata),
            std::invalid_argument);
    }
    {
        JSONTrace duplicate_name(
            "/tmp/partsim_schema3_duplicate_name.json", MetaSim::Tick(1));
        auto metadata = b4Schema3Metadata();
        metadata[0].task_name = metadata[1].task_name;
        EXPECT_THROW(
            duplicate_name.configureB4ObservabilitySchema3(
                MetaSim::Tick(1), metadata),
            std::invalid_argument);
    }
    {
        JSONTrace duplicate_rank(
            "/tmp/partsim_schema3_duplicate_rank.json", MetaSim::Tick(1));
        auto metadata = b4Schema3Metadata();
        metadata[0].priority_rank = metadata[1].priority_rank;
        EXPECT_THROW(
            duplicate_rank.configureB4ObservabilitySchema3(
                MetaSim::Tick(1), metadata),
            std::invalid_argument);
    }
    {
        JSONTrace gap("/tmp/partsim_schema3_rank_gap.json", MetaSim::Tick(1));
        auto metadata = b4Schema3Metadata();
        metadata[0].priority_rank = 10;
        EXPECT_THROW(
            gap.configureB4ObservabilitySchema3(MetaSim::Tick(1), metadata),
            std::invalid_argument);
    }
    {
        JSONTrace duplicate_config(
            "/tmp/partsim_schema3_duplicate_config.json", MetaSim::Tick(1));
        duplicate_config.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        EXPECT_THROW(
            duplicate_config.configureB4ObservabilitySchema3(
                MetaSim::Tick(1), b4Schema3Metadata()),
            std::logic_error);
    }
    MetaSim::SIMUL.endSingleRun();
}

TEST(ObservabilitySchema3Core,
     FrozenLifecycleUniverseRetainsZeroTasksAndCountsTerminationClosure) {
    PerTaskLifecycleAccumulator lifecycle;
    lifecycle.reset(5, b4Schema3Metadata());
    ASSERT_EQ(lifecycle.summaries().size(), 10u);
    EXPECT_THROW(lifecycle.onRelease("not-frozen", 0), std::invalid_argument);
    lifecycle.onRelease("task-0", 0);
    lifecycle.onSchedule("task-0", 0, 1);
    lifecycle.onKill("task-0", 0, 4);
    lifecycle.finalize(5);
    const auto summaries = lifecycle.summaries();
    const auto killed = std::find_if(
        summaries.begin(), summaries.end(),
        [](const PerTaskLifecycleSummary &summary) {
            return summary.task_name == "task-0";
        });
    ASSERT_NE(killed, summaries.end());
    EXPECT_EQ(killed->released_jobs, 1u);
    EXPECT_EQ(killed->completed_jobs, 0u);
    EXPECT_EQ(killed->terminated_jobs, 1u);
    EXPECT_EQ(killed->unfinished_at_horizon_jobs, 0u);
    EXPECT_EQ(killed->executed_core_ticks, 3u);
    EXPECT_EQ(
        killed->released_jobs,
        killed->completed_jobs + killed->terminated_jobs +
            killed->unfinished_at_horizon_jobs);
    EXPECT_EQ(
        std::count_if(
            summaries.begin(), summaries.end(),
            [](const PerTaskLifecycleSummary &summary) {
                return summary.released_jobs == 0;
            }),
        9);
}

TEST(ObservabilitySchema3Core, SealRequiresFinalizedSummariesAndEnergy) {
    MetaSim::SIMUL.initSingleRun();
    {
        JSONTrace unfinalized(
            "/tmp/partsim_schema3_unfinalized.json", MetaSim::Tick(1));
        unfinalized.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        unfinalized.setObservabilityEnergySummary(b4Schema3Energy(1));
        EXPECT_THROW(
            unfinalized.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    {
        JSONTrace missing_energy(
            "/tmp/partsim_schema3_missing_energy.json", MetaSim::Tick(1));
        missing_energy.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(missing_energy, 1);
        missing_energy.finalizeObservabilitySummaries(MetaSim::Tick(1));
        EXPECT_THROW(
            missing_energy.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    MetaSim::SIMUL.endSingleRun();
}

TEST(ObservabilitySchema3Core,
     EnergyInjectionIsOneShotAndRejectsInvalidValuesAndConservation) {
    MetaSim::SIMUL.initSingleRun();
    {
        JSONTrace duplicate(
            "/tmp/partsim_schema3_duplicate_energy.json", MetaSim::Tick(1));
        duplicate.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        duplicate.setObservabilityEnergySummary(b4Schema3Energy(1));
        EXPECT_THROW(
            duplicate.setObservabilityEnergySummary(b4Schema3Energy(1)),
            std::logic_error);
    }
    const auto expect_invalid = [](const std::string &path,
                                   const EnergySummary &energy) {
        JSONTrace trace(path, MetaSim::Tick(1));
        trace.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        EXPECT_THROW(
            trace.setObservabilityEnergySummary(energy),
            std::invalid_argument);
    };
    EnergySummary energy = b4Schema3Energy(1);
    energy.offered_energy_j = std::numeric_limits<double>::quiet_NaN();
    expect_invalid("/tmp/partsim_schema3_nan.json", energy);
    energy = b4Schema3Energy(1);
    energy.credited_energy_j = std::numeric_limits<double>::infinity();
    expect_invalid("/tmp/partsim_schema3_inf.json", energy);
    energy = b4Schema3Energy(1);
    energy.consumed_energy_j = -0.1;
    expect_invalid("/tmp/partsim_schema3_negative.json", energy);
    energy = b4Schema3Energy(1);
    energy.offered_energy_j = 0.9;
    expect_invalid("/tmp/partsim_schema3_conservation.json", energy);
    energy = b4Schema3Energy(1);
    energy.battery_min_j = 0.6;
    expect_invalid("/tmp/partsim_schema3_battery_bounds.json", energy);
    energy = b4Schema3Energy(1);
    energy.observed_energy_intervals = 2;
    expect_invalid("/tmp/partsim_schema3_counter_bounds.json", energy);
    MetaSim::SIMUL.endSingleRun();
}

TEST(ObservabilitySchema3Core,
     SealRejectsIntervalMismatchIncompleteDecisionsAndMechanismBounds) {
    MetaSim::SIMUL.initSingleRun();
    {
        JSONTrace interval_mismatch(
            "/tmp/partsim_schema3_interval_mismatch.json", MetaSim::Tick(2));
        interval_mismatch.configureB4ObservabilitySchema3(
            MetaSim::Tick(2), b4Schema3Metadata());
        observeEmptySchema3Horizon(interval_mismatch, 2);
        interval_mismatch.finalizeObservabilitySummaries(MetaSim::Tick(2));
        interval_mismatch.setObservabilityEnergySummary(b4Schema3Energy(1));
        EXPECT_ANY_THROW(
            interval_mismatch.sealObservabilityPayloadForSerialization());
    }
    {
        JSONTrace incomplete(
            "/tmp/partsim_schema3_incomplete_decisions.json", MetaSim::Tick(2));
        incomplete.configureB4ObservabilitySchema3(
            MetaSim::Tick(2), b4Schema3Metadata());
        incomplete.observeDecision(summaryDecision(0, 4, 1.0, {}));
        EXPECT_THROW(
            incomplete.finalizeObservabilitySummaries(MetaSim::Tick(2)),
            std::logic_error);
        EXPECT_THROW(
            incomplete.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    {
        JSONTrace invalid_mechanism(
            "/tmp/partsim_schema3_invalid_mechanism.json", MetaSim::Tick(1));
        invalid_mechanism.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(invalid_mechanism, 1);
        invalid_mechanism.finalizeObservabilitySummaries(MetaSim::Tick(1));
        invalid_mechanism.setObservabilityEnergySummary(b4Schema3Energy(1));
        MechanismSummary &tampered = const_cast<MechanismSummary &>(
            invalid_mechanism.mechanismSummary());
        tampered.actual_bypass_ticks = 1;
        EXPECT_THROW(
            invalid_mechanism.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    MetaSim::SIMUL.endSingleRun();
}

TEST(ObservabilitySchema3Core,
     DuplicateSealFailsAndStrictHorizonIsEnforcedAtSeal) {
    MetaSim::SIMUL.initSingleRun();
    {
        JSONTrace duplicate_seal(
            "/tmp/partsim_schema3_duplicate_seal.json", MetaSim::Tick(1));
        duplicate_seal.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(duplicate_seal, 1);
        duplicate_seal.finalizeObservabilitySummaries(MetaSim::Tick(1));
        duplicate_seal.setObservabilityEnergySummary(b4Schema3Energy(1));
        duplicate_seal.sealObservabilityPayloadForSerialization();
        EXPECT_THROW(
            duplicate_seal.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    {
        JSONTrace short_horizon(
            "/tmp/partsim_schema3_short_horizon.json", MetaSim::Tick(2));
        short_horizon.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(short_horizon, 1);
        short_horizon.finalizeObservabilitySummaries(MetaSim::Tick(1));
        short_horizon.setObservabilityEnergySummary(b4Schema3Energy(1));
        EXPECT_THROW(
            short_horizon.sealObservabilityPayloadForSerialization(),
            std::logic_error);
    }
    MetaSim::SIMUL.endSingleRun();
}

TEST(ObservabilitySchema3Core,
     ConfiguredButUnsealedTraceStaysSchema3AndDoesNotForgePayload) {
    const std::string path =
        "/tmp/partsim_b4_schema3_unsealed_fail_closed.json";
    {
        JSONTrace trace(path, MetaSim::Tick(1));
        MetaSim::SIMUL.initSingleRun();
        trace.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(trace, 1);
        trace.finalizeObservabilitySummaries(MetaSim::Tick(1));
        EXPECT_FALSE(trace.observabilityPayloadSealed());
        MetaSim::SIMUL.endSingleRun();
    }
    const std::string contents = readFileContents(path);
    EXPECT_NE(contents.find("\"trace_schema_version\": 3"), std::string::npos);
    EXPECT_EQ(
        contents.find("\"observability_summary_contract_version\""),
        std::string::npos);
    EXPECT_EQ(contents.find("\"mechanism_summary\""), std::string::npos);
    EXPECT_EQ(contents.find("\"energy_summary\""), std::string::npos);
    EXPECT_EQ(contents.find("\"per_task_summary\""), std::string::npos);
}

TEST(ObservabilitySchema3Core,
     DefaultSchemaTwoSerializerMatchesFrozenBytes) {
    const std::string actual_path =
        "/tmp/partsim_schema2_default_byte_fixture.json";
    {
        JSONTrace trace(actual_path, MetaSim::Tick(2));
    }
    const std::string fixture_path =
        std::string(PARTSIM_SOURCE_DIR) +
        "/test/fixtures/json_trace_schema2_default_v1.json";
    EXPECT_EQ(
        readFileContents(actual_path),
        readFileContents(fixture_path));
}

TEST(ObservabilitySchema3Core, NewRunClearsSealedPayloadAndAllCounters) {
    const std::string path =
        "/tmp/partsim_b4_schema3_new_run_reset.json";
    {
        JSONTrace trace(path, MetaSim::Tick(1));
        MetaSim::SIMUL.initSingleRun();
        trace.configureB4ObservabilitySchema3(
            MetaSim::Tick(1), b4Schema3Metadata());
        observeEmptySchema3Horizon(trace, 1);
        trace.finalizeObservabilitySummaries(MetaSim::Tick(1));
        trace.setObservabilityEnergySummary(b4Schema3Energy(1));
        trace.sealObservabilityPayloadForSerialization();
        EXPECT_TRUE(trace.observabilityPayloadSealed());
        MetaSim::SIMUL.endSingleRun();

        MetaSim::SIMUL.initSingleRun();
        trace.beginRun(MetaSim::SIMUL.getRunGeneration());
        EXPECT_FALSE(trace.observabilityPayloadSealed());
        EXPECT_TRUE(trace.observabilitySummariesEnabled());
        EXPECT_EQ(trace.mechanismSummary().observed_decision_ticks, 0u);
        const auto reset_summaries = trace.perTaskLifecycleSummary();
        ASSERT_EQ(reset_summaries.size(), 10u);
        EXPECT_TRUE(std::all_of(
            reset_summaries.begin(), reset_summaries.end(),
            [](const PerTaskLifecycleSummary &summary) {
                return summary.released_jobs == 0 &&
                       summary.completed_jobs == 0 &&
                       summary.terminated_jobs == 0 &&
                       summary.unfinished_at_horizon_jobs == 0;
            }));
        observeEmptySchema3Horizon(trace, 1);
        trace.finalizeObservabilitySummaries(MetaSim::Tick(1));
        trace.setObservabilityEnergySummary(b4Schema3Energy(1));
        trace.sealObservabilityPayloadForSerialization();
        MetaSim::SIMUL.endSingleRun();
    }
    const std::string contents = readFileContents(path);
    EXPECT_NE(contents.find("\"run_count\": 2"), std::string::npos);
    EXPECT_EQ(countMarker(contents, "\"observed_decision_ticks\": 1"), 1u);
}

} // namespace RTSim
