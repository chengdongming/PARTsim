#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <locale>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include <rtsim/abstask.hpp>
#include <rtsim/scheduler/priority_energy_task_params.hpp>

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

namespace RTSim {
namespace {

class PriorityEnergyTestTask : public AbsRTTask {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    Tick _arrival;
    double _wcet;
    double _remaining_wcet;
    bool _active;
    bool _executing;
    AbsKernel *_kernel;

public:
    PriorityEnergyTestTask()
        : _task_number(17),
          _period(120),
          _relative_deadline(83),
          _arrival(13),
          _wcet(7.0),
          _remaining_wcet(6.0),
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
    double getMaxExecutionCycles() const override { return _wcet; }
    Tick getDeadline() const override { return _arrival + _relative_deadline; }
    Tick getRelDline() const override { return _relative_deadline; }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getWCET(double = 1.0) const override { return _wcet; }
    double getRemainingWCET(double = 1.0) const override {
        return _remaining_wcet;
    }
    std::string toString() const override {
        return "PriorityEnergyTestTask";
    }
};

struct SchedulerEnergySnapshot {
    double factor;
    double total_energy;
    double unit_energy;
    int model_period;
    int model_wcet;
    int64_t model_arrival_offset;
    std::string model_workload;
    int64_t task_deadline;
    int64_t task_relative_deadline;
    int64_t task_period;
    int64_t task_arrival;
    double task_wcet;
    double task_remaining_wcet;
    double task_execution_cycles;
};

const std::string kBaseParams =
    "period=120,wcet=7,arrival_offset=11,workload=hash";

template <typename SchedulerType>
SchedulerEnergySnapshot captureSchedulerEnergy(const std::string &params) {
    PriorityEnergyTestTask task;
    SchedulerType scheduler;

    scheduler.addTask(&task, params);

    const auto model_it = scheduler._task_models.find(&task);
    if (model_it == scheduler._task_models.end()) {
        throw std::runtime_error("priority-energy test model was not registered");
    }
    const auto *model = model_it->second;

    return SchedulerEnergySnapshot{
        model->getEnergyCoefficient(),
        scheduler.getTaskTotalEnergy(&task),
        scheduler.getTaskUnitEnergy(&task),
        model->getPeriod(),
        model->getWCET(),
        static_cast<int64_t>(model->getArrivalOffset()),
        model->getWorkloadType(),
        static_cast<int64_t>(task.getDeadline()),
        static_cast<int64_t>(task.getRelDline()),
        static_cast<int64_t>(task.getPeriod()),
        static_cast<int64_t>(task.getArrival()),
        task.getWCET(),
        task.getRemainingWCET(),
        task.getMaxExecutionCycles()};
}

std::vector<SchedulerEnergySnapshot> captureAllSchedulers(
    const std::string &params) {
    return {
        captureSchedulerEnergy<ASAPBlockScheduler>(params),
        captureSchedulerEnergy<ASAPNonBlockScheduler>(params),
        captureSchedulerEnergy<ASAPSyncScheduler>(params),
        captureSchedulerEnergy<ALAPBlockScheduler>(params),
        captureSchedulerEnergy<ALAPNonBlockScheduler>(params),
        captureSchedulerEnergy<ALAPSyncScheduler>(params),
        captureSchedulerEnergy<STBlockScheduler>(params),
        captureSchedulerEnergy<STNonBlockScheduler>(params),
        captureSchedulerEnergy<STSyncScheduler>(params),
    };
}

void expectStrictlyClose(double actual, double expected) {
    const double scale = std::max({1.0, std::abs(actual), std::abs(expected)});
    EXPECT_LE(std::abs(actual - expected), 1e-12 * scale);
}

void expectFactorError(const std::string &params) {
    try {
        (void)parsePriorityEnergyTaskFactor(params);
        FAIL() << "expected task_energy_factor parsing to fail: " << params;
    } catch (const std::invalid_argument &error) {
        const std::string message = error.what();
        EXPECT_NE(message.find("task_energy_factor"), std::string::npos);
        EXPECT_FALSE(message.empty());
    } catch (const std::exception &error) {
        FAIL() << "expected std::invalid_argument, got std::exception: "
               << error.what();
    } catch (...) {
        FAIL() << "expected std::invalid_argument, got non-standard exception";
    }
}

void expectFactorErrorPreservesErrno(const std::string &params,
                                     int initial_errno) {
    errno = initial_errno;
    try {
        (void)parsePriorityEnergyTaskFactor(params);
        const int observed_errno = errno;
        FAIL() << "expected task_energy_factor parsing to fail: " << params
               << "; errno=" << observed_errno;
    } catch (const std::invalid_argument &error) {
        const int observed_errno = errno;
        const std::string message = error.what();
        EXPECT_EQ(observed_errno, initial_errno);
        EXPECT_NE(message.find("task_energy_factor"), std::string::npos);
        EXPECT_FALSE(message.empty());
    } catch (const std::exception &error) {
        const int observed_errno = errno;
        FAIL() << "expected std::invalid_argument, got std::exception: "
               << error.what() << "; errno=" << observed_errno;
    } catch (...) {
        const int observed_errno = errno;
        FAIL() << "expected std::invalid_argument, got non-standard exception; errno="
               << observed_errno;
    }
}

template <typename SchedulerType>
void expectSchedulerRejectsFactor(const std::string &params) {
    PriorityEnergyTestTask task;
    SchedulerType scheduler;

    try {
        scheduler.addTask(&task, params);
        FAIL() << "expected scheduler to reject invalid task_energy_factor";
    } catch (const std::invalid_argument &error) {
        EXPECT_NE(std::string(error.what()).find("task_energy_factor"),
                  std::string::npos);
    } catch (const std::exception &error) {
        FAIL() << "expected std::invalid_argument, got std::exception: "
               << error.what();
    } catch (...) {
        FAIL() << "expected std::invalid_argument, got non-standard exception";
    }

    EXPECT_EQ(scheduler._task_models.count(&task), 0u);
}

TEST(PriorityEnergyTaskFactorParser, DefaultsAndAcceptedValues) {
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(""), 1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "period=100,wcet=20,workload=hash"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=1"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=2"),
                     2.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=0.5"),
                     0.5);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=1e-3"),
                     0.001);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=2E+1"),
                     20.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "\t task_energy_factor \r=\v 0.5 \f"),
                     0.5);
}

TEST(PriorityEnergyTaskFactorParser, AcceptsAnyTokenPosition) {
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor=2,period=100,wcet=20"),
                     2.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "period=100,task_energy_factor=2,wcet=20"),
                     2.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "period=100,wcet=20,task_energy_factor=2"),
                     2.0);
}

TEST(PriorityEnergyTaskFactorParser, PreservesLegacyTokens) {
    const std::string legacy =
        "period=100,wcet=20,legacy_token,workload=hash";
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(legacy), 1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         legacy + ",unknown=value,task_energy_factor=2"),
                     2.0);
}

TEST(PriorityEnergyTaskFactorParser, RejectsInvalidValues) {
    const std::vector<std::string> invalid = {
        "task_energy_factor=",
        "task_energy_factor=   ",
        "task_energy_factor",
        "task_energy_factor=0",
        "task_energy_factor=-1",
        "task_energy_factor=-0",
        "task_energy_factor=nan",
        "task_energy_factor=NaN",
        "task_energy_factor=inf",
        "task_energy_factor=infinity",
        "task_energy_factor=1e9999",
        "task_energy_factor=1e-99999",
        "task_energy_factor=2abc",
        "task_energy_factor=1.0f",
        "task_energy_factor=1.0 garbage",
        "period=100,task_energy_factor,workload=hash",
    };

    for (const auto &params : invalid) {
        expectFactorError(params);
    }
}

TEST(PriorityEnergyTaskFactorParser, RejectsDuplicateKey) {
    expectFactorError(
        "task_energy_factor=1,period=100,task_energy_factor=2");
    expectFactorError(
        "task_energy_factor =1, task_energy_factor=2");
}

TEST(PriorityEnergyTaskFactorParser, RequiresExactCaseSensitiveKey) {
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "task_energy_factor_extra=2"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "prefix_task_energy_factor=2"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "Task_energy_factor=2"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "TASK_ENERGY_FACTOR=2"),
                     1.0);
    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(
                         "my_task_energy_factor=2"),
                     1.0);
}

TEST(PriorityEnergyTaskFactorParser, DoesNotMutateInputOrGlobalLocale) {
    std::string params =
        "period=100,legacy_token,task_energy_factor=2,workload=hash";
    const std::string original = params;
    const std::locale locale_before;

    EXPECT_DOUBLE_EQ(parsePriorityEnergyTaskFactor(params), 2.0);

    const std::locale locale_after;
    EXPECT_EQ(params, original);
    EXPECT_EQ(locale_before.name(), locale_after.name());
}

TEST(PriorityEnergyTaskFactorParser, PreservesErrnoOnSuccessfulPaths) {
    const std::vector<std::pair<std::string, double>> cases = {
        {"", 1.0},
        {"period=100,legacy_token,workload=hash", 1.0},
        {"task_energy_factor=1", 1.0},
        {"task_energy_factor=2.5", 2.5},
    };

    for (const auto &test_case : cases) {
        errno = EDOM;
        const double factor = parsePriorityEnergyTaskFactor(test_case.first);
        const int observed_errno = errno;
        EXPECT_DOUBLE_EQ(factor, test_case.second);
        EXPECT_EQ(observed_errno, EDOM);
    }
}

TEST(PriorityEnergyTaskFactorParser, PreservesErrnoOnInvalidPaths) {
    const std::vector<std::string> invalid = {
        "task_energy_factor=",
        "task_energy_factor",
        "task_energy_factor=0",
        "task_energy_factor=-1",
        "task_energy_factor=nan",
        "task_energy_factor=inf",
        "task_energy_factor=1e99999",
        "task_energy_factor=1e-99999",
        "task_energy_factor=2abc",
        "task_energy_factor=1,task_energy_factor=2",
        "period=100,task_energy_factor,workload=hash",
        "task_energy_factor =1, task_energy_factor=2",
    };

    for (const auto &params : invalid) {
        expectFactorErrorPreservesErrno(params, EDOM);
    }
}

TEST(PriorityEnergyTaskFactorParser, PreservesArbitraryErrnoValue) {
    errno = ENOENT;
    const double factor = parsePriorityEnergyTaskFactor(
        "period=100,task_energy_factor=2.5,workload=hash");
    const int observed_errno = errno;
    EXPECT_DOUBLE_EQ(factor, 2.5);
    EXPECT_EQ(observed_errno, ENOENT);

    expectFactorErrorPreservesErrno(
        "task_energy_factor=1e99999", ENOENT);
}

TEST(PriorityEnergyTaskFactorParser, RepeatedCallsAreDeterministic) {
    const std::string params =
        "period=100,task_energy_factor=2.5,workload=hash";

    for (int iteration = 0; iteration < 5; ++iteration) {
        errno = EDOM;
        const double factor = parsePriorityEnergyTaskFactor(params);
        const int observed_errno = errno;
        EXPECT_DOUBLE_EQ(factor, 2.5);
        EXPECT_EQ(observed_errno, EDOM);
    }
}

TEST(PriorityEnergyTaskFactorIntegration,
     NineSchedulersDefaultExplicitOneAndDoubleEnergy) {
    const auto defaults = captureAllSchedulers(kBaseParams);
    const auto explicit_ones = captureAllSchedulers(
        kBaseParams + ",task_energy_factor=1");
    const auto doubles = captureAllSchedulers(
        kBaseParams + ",task_energy_factor=2");

    ASSERT_EQ(defaults.size(), 9u);
    ASSERT_EQ(explicit_ones.size(), defaults.size());
    ASSERT_EQ(doubles.size(), defaults.size());

    for (std::size_t index = 0; index < defaults.size(); ++index) {
        EXPECT_DOUBLE_EQ(defaults[index].factor, 1.0);
        EXPECT_DOUBLE_EQ(explicit_ones[index].factor, 1.0);
        EXPECT_DOUBLE_EQ(doubles[index].factor, 2.0);
        EXPECT_GT(defaults[index].unit_energy, 0.0);
        expectStrictlyClose(explicit_ones[index].total_energy,
                            defaults[index].total_energy);
        expectStrictlyClose(explicit_ones[index].unit_energy,
                            defaults[index].unit_energy);
        expectStrictlyClose(doubles[index].total_energy,
                            2.0 * defaults[index].total_energy);
        expectStrictlyClose(doubles[index].unit_energy,
                            2.0 * defaults[index].unit_energy);
    }
}

TEST(PriorityEnergyTaskFactorIntegration,
     NineSchedulersAgreeAndPreserveNonEnergyFields) {
    const auto explicit_ones = captureAllSchedulers(
        kBaseParams + ",task_energy_factor=1");
    const auto doubles = captureAllSchedulers(
        kBaseParams + ",task_energy_factor=2");

    ASSERT_EQ(explicit_ones.size(), 9u);
    ASSERT_EQ(doubles.size(), explicit_ones.size());

    for (std::size_t index = 0; index < explicit_ones.size(); ++index) {
        expectStrictlyClose(explicit_ones[index].total_energy,
                            explicit_ones.front().total_energy);
        expectStrictlyClose(explicit_ones[index].unit_energy,
                            explicit_ones.front().unit_energy);
        expectStrictlyClose(doubles[index].total_energy,
                            doubles.front().total_energy);
        expectStrictlyClose(doubles[index].unit_energy,
                            doubles.front().unit_energy);

        EXPECT_EQ(explicit_ones[index].model_period, 120);
        EXPECT_EQ(explicit_ones[index].model_wcet, 7);
        EXPECT_EQ(explicit_ones[index].model_arrival_offset, 11);
        EXPECT_EQ(explicit_ones[index].model_workload, "hash");
        EXPECT_EQ(doubles[index].model_period,
                  explicit_ones[index].model_period);
        EXPECT_EQ(doubles[index].model_wcet,
                  explicit_ones[index].model_wcet);
        EXPECT_EQ(doubles[index].model_arrival_offset,
                  explicit_ones[index].model_arrival_offset);
        EXPECT_EQ(doubles[index].model_workload,
                  explicit_ones[index].model_workload);
        EXPECT_EQ(doubles[index].task_deadline,
                  explicit_ones[index].task_deadline);
        EXPECT_EQ(doubles[index].task_relative_deadline,
                  explicit_ones[index].task_relative_deadline);
        EXPECT_EQ(doubles[index].task_period,
                  explicit_ones[index].task_period);
        EXPECT_EQ(doubles[index].task_arrival,
                  explicit_ones[index].task_arrival);
        EXPECT_DOUBLE_EQ(doubles[index].task_wcet,
                         explicit_ones[index].task_wcet);
        EXPECT_DOUBLE_EQ(doubles[index].task_remaining_wcet,
                         explicit_ones[index].task_remaining_wcet);
        EXPECT_DOUBLE_EQ(doubles[index].task_execution_cycles,
                         explicit_ones[index].task_execution_cycles);
    }
}

TEST(PriorityEnergyTaskFactorIntegration,
     NineSchedulersRejectInvalidFactorBeforeRegistration) {
    const std::string invalid = kBaseParams + ",task_energy_factor=0";

    expectSchedulerRejectsFactor<ASAPBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<ASAPNonBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<ASAPSyncScheduler>(invalid);
    expectSchedulerRejectsFactor<ALAPBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<ALAPNonBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<ALAPSyncScheduler>(invalid);
    expectSchedulerRejectsFactor<STBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<STNonBlockScheduler>(invalid);
    expectSchedulerRejectsFactor<STSyncScheduler>(invalid);
}

}  // namespace
}  // namespace RTSim
