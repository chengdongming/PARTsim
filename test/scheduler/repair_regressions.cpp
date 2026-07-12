#include <algorithm>
#include <cmath>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include <gtest/gtest.h>

#include <metasim/factory.hpp>
#include <metasim/basestat.hpp>
#include <metasim/simul.hpp>
#include <rtsim/abskernel.hpp>
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

} // namespace RTSim
