#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <locale>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <type_traits>
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

#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/system.hpp>

#ifndef PARTSIM_SOURCE_DIR
#error "PARTSIM_SOURCE_DIR must be defined for priority-energy integration tests"
#endif

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
    explicit PriorityEnergyTestTask(
        int task_number = 17, Tick period = 120, Tick relative_deadline = 83)
        : _task_number(task_number),
          _period(period),
          _relative_deadline(relative_deadline),
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
    int64_t model_priority;
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
        static_cast<int64_t>(model->getRMPriority()),
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

void expectFixedPriorityRankError(const std::string &params) {
    errno = EDOM;
    try {
        (void)parseFixedPriorityRank(params);
        FAIL() << "expected fixed_priority_rank parsing to fail: " << params;
    } catch (const std::invalid_argument &error) {
        EXPECT_EQ(errno, EDOM);
        EXPECT_NE(std::string(error.what()).find("fixed_priority_rank"),
                  std::string::npos);
    } catch (const std::exception &error) {
        FAIL() << "expected std::invalid_argument, got std::exception: "
               << error.what();
    } catch (...) {
        FAIL() << "expected std::invalid_argument, got non-standard exception";
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

template <typename SchedulerType>
void expectSchedulerRejectsFixedPriorityRank(const std::string &params) {
    PriorityEnergyTestTask task;
    SchedulerType scheduler;
    try {
        scheduler.addTask(&task, params);
        FAIL() << "expected scheduler to reject invalid fixed_priority_rank";
    } catch (const std::invalid_argument &error) {
        EXPECT_NE(std::string(error.what()).find("fixed_priority_rank"),
                  std::string::npos);
    } catch (const std::exception &error) {
        FAIL() << "expected std::invalid_argument, got std::exception: "
               << error.what();
    } catch (...) {
        FAIL() << "expected std::invalid_argument, got non-standard exception";
    }
    EXPECT_EQ(scheduler._task_models.count(&task), 0u);
}

uint64_t binary64Bits(double value) {
    static_assert(sizeof(uint64_t) == sizeof(double),
                  "binary64 representation requires 64-bit double");
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

bool isStrictlyClose(double actual, double expected) {
    const double scale = std::max({1.0, std::abs(actual), std::abs(expected)});
    return std::abs(actual - expected) <= 1e-12 * scale;
}

int runPriorityEnergyHash9000RuntimeClosure() {
    bool valid = true;
    auto require = [&valid](bool condition, const std::string &message) {
        if (!condition) {
            valid = false;
            std::cerr << "PHASE2A_CHECK_FAILED: " << message << '\n';
        }
    };
    auto fail = []() {
        std::cerr << "PHASE2A_HASH9000_RUNTIME_CLOSURE_FAILED\n";
        std::cerr.flush();
        return EXIT_FAILURE;
    };

    try {
        const std::filesystem::path source_root{PARTSIM_SOURCE_DIR};
        require(source_root.is_absolute(),
                "PARTSIM_SOURCE_DIR is not an absolute path");
        require(std::filesystem::is_directory(source_root),
                "PARTSIM_SOURCE_DIR is not a directory");

        const std::filesystem::path template_path =
            source_root / "v9_3_b4_priority_energy_system_template.yml";
        require(template_path.is_absolute(),
                "B4-PE system template path is not absolute");
        require(std::filesystem::is_regular_file(template_path),
                "B4-PE system template is not a regular file");

        if (!valid) {
            return fail();
        }

        std::filesystem::current_path(source_root);
        require(std::filesystem::weakly_canonical(
                    std::filesystem::current_path()) ==
                    std::filesystem::weakly_canonical(source_root),
                "death-test child CWD does not match PARTSIM_SOURCE_DIR");

        if (!valid) {
            return fail();
        }

        const std::filesystem::path legacy_template_path =
            source_root / "system_config_unified_template.yml";
        require(std::filesystem::is_regular_file(legacy_template_path),
                "authoritative legacy template is not a regular file");
        require(!std::filesystem::equivalent(template_path,
                                             legacy_template_path),
                "B4-PE template aliases the legacy template");

        std::ifstream template_stream(template_path);
        require(template_stream.good(), "B4-PE system template is unreadable");
        const std::string template_text(
            (std::istreambuf_iterator<char>(template_stream)),
            std::istreambuf_iterator<char>());
        require(template_text.find("experiment_id:") == std::string::npos,
                "template contains an experiment_id");
        require(template_text.find("result_root:") == std::string::npos,
                "template contains a result root");
        require(template_text.find("formal_seal:") == std::string::npos,
                "template contains a formal seal");

        System system(template_path.string());
        ConfigManager &config = ConfigManager::getInstance();

        require(config.isConfigLoaded(), "ConfigManager did not load template");
        require(config.getConfigFilePath() == template_path.string(),
                "ConfigManager loaded a different path");
        require(config.getNumCores() == 4,
                "ConfigManager core count is not four");
        require(config.getBaseFrequency() == 9000.0,
                "ConfigManager base frequency is not 9000 MHz");
        require(config.getBasePower() == 0.5,
                "ConfigManager base power is not 0.5 W");
        require(config.getPowerCoefficient("hash") == 0.8,
                "ConfigManager hash coefficient is not 0.8");
        require(config.getFrequencyPowerRatio(9000) == 1.0,
                "ConfigManager 9000 MHz ratio is not 1.0");
        require(binary64Bits(config.getBasePower()) == binary64Bits(0.5),
                "base power binary64 materialization differs");
        require(binary64Bits(config.getPowerCoefficient("hash")) ==
                    binary64Bits(0.8),
                "hash coefficient binary64 materialization differs");
        require(binary64Bits(config.getFrequencyPowerRatio(9000)) ==
                    binary64Bits(1.0),
                "9000 MHz ratio binary64 materialization differs");

        require(system.islands.size() == 1u,
                "runtime system does not contain exactly one CPU island");
        require(system.cpus.size() == 4u,
                "runtime system does not contain four CPUs");
        require(system.scheduler_identities.size() == 1u,
                "runtime default scheduler identity is missing");
        if (!system.scheduler_identities.empty()) {
            require(system.scheduler_identities.front().configured_scheduler ==
                        "gpfp_asap_block",
                    "runtime default scheduler is not gpfp_asap_block");
        }
        if (!system.islands.empty()) {
            const auto &island = system.islands.front();
            require(island->getProcessorsNumber() == 4u,
                    "runtime island does not own four CPUs");
            require(island->getFrequency() == 9000,
                    "runtime island selected OPP is not 9000 MHz");
            require(island->getOPP().frequency == 9000,
                    "runtime island current OPP frequency is not 9000 MHz");
            require(island->getFrequency() ==
                        static_cast<freq_type>(config.getBaseFrequency()),
                    "runtime OPP and ConfigManager frequency disagree");
        }
        for (const auto &cpu : system.cpus) {
            require(cpu->getFrequency() == 9000,
                    "runtime CPU frequency is not 9000 MHz");
            require(cpu->getOPP().frequency == 9000,
                    "runtime CPU OPP frequency is not 9000 MHz");
        }

        const double p0 = config.getBasePower() *
                          config.getPowerCoefficient("hash") *
                          config.getFrequencyPowerRatio(9000);
        const double q0 = p0 * 0.001;
        require(isStrictlyClose(p0, 0.4), "p0 is not 0.4 W");
        require(isStrictlyClose(q0, 0.0004),
                "q0 is not 0.0004 J per execution millisecond");

        const auto wcet_one = captureAllSchedulers(
            "period=120,wcet=1,arrival_offset=11,workload=hash,"
            "task_energy_factor=1");
        const auto wcet_25_factor_one = captureAllSchedulers(
            "period=120,wcet=25,arrival_offset=11,workload=hash,"
            "task_energy_factor=1");
        const auto wcet_25_factor_two = captureAllSchedulers(
            "period=120,wcet=25,arrival_offset=11,workload=hash,"
            "task_energy_factor=2");

        require(wcet_one.size() == 9u && wcet_25_factor_one.size() == 9u &&
                    wcet_25_factor_two.size() == 9u,
                "nine-scheduler snapshot count is incomplete");
        for (std::size_t index = 0; index < wcet_one.size(); ++index) {
            const auto &one_ms = wcet_one[index];
            const auto &factor_one = wcet_25_factor_one[index];
            const auto &factor_two = wcet_25_factor_two[index];

            require(isStrictlyClose(one_ms.unit_energy, q0),
                    "WCET=1 factor=1 unit energy differs from q0");
            require(isStrictlyClose(one_ms.total_energy, q0),
                    "WCET=1 factor=1 total energy differs from q0");
            require(isStrictlyClose(factor_one.unit_energy, q0),
                    "WCET=25 factor=1 unit energy differs from q0");
            require(isStrictlyClose(factor_one.total_energy, 0.01),
                    "WCET=25 factor=1 total energy is not 0.01 J");
            require(isStrictlyClose(factor_two.unit_energy, 0.0008),
                    "WCET=25 factor=2 unit energy is not 0.0008 J");
            require(isStrictlyClose(factor_two.total_energy, 0.02),
                    "WCET=25 factor=2 total energy is not 0.02 J");
            require(factor_two.unit_energy / factor_one.unit_energy == 2.0,
                    "factor=2 unit-energy ratio is not exactly two");
            require(factor_two.total_energy / factor_one.total_energy == 2.0,
                    "factor=2 total-energy ratio is not exactly two");

            require(binary64Bits(one_ms.unit_energy) ==
                        binary64Bits(wcet_one.front().unit_energy),
                    "factor=1 unit energy differs across schedulers");
            require(binary64Bits(factor_one.total_energy) ==
                        binary64Bits(wcet_25_factor_one.front().total_energy),
                    "factor=1 total energy differs across schedulers");
            require(binary64Bits(factor_two.unit_energy) ==
                        binary64Bits(wcet_25_factor_two.front().unit_energy),
                    "factor=2 unit energy differs across schedulers");
            require(binary64Bits(factor_two.total_energy) ==
                        binary64Bits(wcet_25_factor_two.front().total_energy),
                    "factor=2 total energy differs across schedulers");

            require(factor_one.model_period == 120 &&
                        factor_two.model_period == factor_one.model_period,
                    "factor changed model period");
            require(factor_one.model_wcet == 25 &&
                        factor_two.model_wcet == factor_one.model_wcet,
                    "factor changed model WCET");
            require(factor_one.model_arrival_offset == 11 &&
                        factor_two.model_arrival_offset ==
                            factor_one.model_arrival_offset,
                    "factor changed arrival offset");
            require(factor_one.model_workload == "hash" &&
                        factor_two.model_workload == factor_one.model_workload,
                    "factor changed or failed to select hash workload");
            require(factor_two.task_deadline == factor_one.task_deadline &&
                        factor_two.task_relative_deadline ==
                            factor_one.task_relative_deadline &&
                        factor_two.task_period == factor_one.task_period &&
                        factor_two.task_arrival == factor_one.task_arrival &&
                        factor_two.task_wcet == factor_one.task_wcet &&
                        factor_two.task_remaining_wcet ==
                            factor_one.task_remaining_wcet &&
                        factor_two.task_execution_cycles ==
                            factor_one.task_execution_cycles,
                    "factor changed a non-energy task field");
        }

        if (!valid) {
            return fail();
        }

        std::cerr << "PHASE2A_HASH9000_RUNTIME_CLOSURE_OK"
                  << " base_power_bits=" << binary64Bits(config.getBasePower())
                  << " hash_bits="
                  << binary64Bits(config.getPowerCoefficient("hash"))
                  << " ratio_bits="
                  << binary64Bits(config.getFrequencyPowerRatio(9000))
                  << " q0_bits=" << binary64Bits(q0) << '\n';
        std::cerr.flush();
        return EXIT_SUCCESS;
    } catch (const std::filesystem::filesystem_error &error) {
        std::cerr << "PHASE2A_CHECK_FILESYSTEM_EXCEPTION: " << error.what()
                  << '\n';
    } catch (const std::exception &error) {
        std::cerr << "PHASE2A_CHECK_EXCEPTION: " << error.what() << '\n';
    } catch (...) {
        std::cerr << "PHASE2A_CHECK_EXCEPTION: non-standard exception\n";
    }

    return fail();
}

TEST(PriorityEnergyHash9000DeathTest,
     RuntimeConfigOppAndNineSchedulerQ0Closure) {
    const std::filesystem::path parent_current_path =
        std::filesystem::current_path();
    ASSERT_EXIT(
        { std::_Exit(runPriorityEnergyHash9000RuntimeClosure()); },
        ::testing::ExitedWithCode(EXIT_SUCCESS),
        "PHASE2A_HASH9000_RUNTIME_CLOSURE_OK");
    EXPECT_EQ(std::filesystem::current_path(), parent_current_path);
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

TEST(PriorityEnergyFixedPriorityRankParser, DefaultsAndAcceptedValues) {
    EXPECT_FALSE(parseFixedPriorityRank(""));
    EXPECT_EQ(parseFixedPriorityRank("fixed_priority_rank=0"), 0);
    EXPECT_EQ(parseFixedPriorityRank("period=100, fixed_priority_rank=8"), 8);
    EXPECT_EQ(parseFixedPriorityRank(
                  "\t period=100 \r,\v fixed_priority_rank \f= 1 \n"),
              1);
}

TEST(PriorityEnergyFixedPriorityRankParser, UnknownTokensRemainCompatible) {
    EXPECT_FALSE(parseFixedPriorityRank("period=100,legacy_token,workload=hash"));
    EXPECT_EQ(parseFixedPriorityRank(
                  "fixed_priority_rank=2,unknown=value,workload=hash"), 2);
}

TEST(PriorityEnergyFixedPriorityRankParser, RejectsMalformedValues) {
    const std::vector<std::string> invalid = {
        "fixed_priority_rank=",
        "fixed_priority_rank=   ",
        "fixed_priority_rank=-1",
        "fixed_priority_rank=1.5",
        "fixed_priority_rank=1e2",
        "fixed_priority_rank=abc",
        "fixed_priority_rank=1x",
        "fixed_priority_rank",
        "fixed_priority_rank=1,fixed_priority_rank=2",
        "fixed_priority_rank=18446744073709551616",
    };
    for (const auto &params : invalid) {
        expectFixedPriorityRankError(params);
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
        EXPECT_EQ(explicit_ones[index].model_priority, 120);
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
     NineSchedulersApplyOnlyFixedPriorityOverride) {
    const auto defaults = captureAllSchedulers(kBaseParams);
    const auto overridden = captureAllSchedulers(
        kBaseParams + ",fixed_priority_rank=3");

    ASSERT_EQ(overridden.size(), 9u);
    for (std::size_t index = 0; index < overridden.size(); ++index) {
        EXPECT_EQ(defaults[index].model_priority, 120);
        EXPECT_EQ(overridden[index].model_priority, 3);
        EXPECT_EQ(overridden[index].model_period, defaults[index].model_period);
        EXPECT_EQ(overridden[index].model_wcet, defaults[index].model_wcet);
        EXPECT_EQ(overridden[index].model_arrival_offset,
                  defaults[index].model_arrival_offset);
        EXPECT_EQ(overridden[index].model_workload,
                  defaults[index].model_workload);
        EXPECT_DOUBLE_EQ(overridden[index].factor, defaults[index].factor);
        EXPECT_DOUBLE_EQ(overridden[index].total_energy,
                         defaults[index].total_energy);
        EXPECT_DOUBLE_EQ(overridden[index].unit_energy,
                         defaults[index].unit_energy);
        EXPECT_EQ(overridden[index].task_deadline, defaults[index].task_deadline);
        EXPECT_EQ(overridden[index].task_relative_deadline,
                  defaults[index].task_relative_deadline);
        EXPECT_EQ(overridden[index].task_period, defaults[index].task_period);
        EXPECT_EQ(overridden[index].task_arrival, defaults[index].task_arrival);
    }
}

template <typename SchedulerType>
std::vector<AbsRTTask *> sortedTwoTaskPriorityOrder(bool use_dm) {
    PriorityEnergyTestTask task_a(1, 10, 9);
    PriorityEnergyTestTask task_b(2, 20, 5);
    SchedulerType scheduler;
    const std::string suffix = use_dm
        ? ",fixed_priority_rank=1"
        : "";
    scheduler.addTask(&task_a, "period=10,wcet=7,arrival_offset=0,workload=hash" + suffix);
    scheduler.addTask(&task_b, use_dm
        ? "period=20,wcet=7,arrival_offset=0,workload=hash,fixed_priority_rank=0"
        : "period=20,wcet=7,arrival_offset=0,workload=hash");
    std::vector<AbsRTTask *> ordered{&task_a, &task_b};
    scheduler.sortByRMPriority(ordered);
    return ordered;
}

std::vector<AbsRTTask *> sortedSTSyncTwoTaskPriorityOrder(bool use_dm) {
    PriorityEnergyTestTask task_a(1, 10, 9);
    PriorityEnergyTestTask task_b(2, 20, 5);
    STSyncScheduler scheduler;
    scheduler.addTask(&task_a,
                      "period=10,wcet=7,arrival_offset=0,workload=hash" +
                          std::string(use_dm ? ",fixed_priority_rank=1" : ""));
    scheduler.addTask(&task_b, use_dm
        ? "period=20,wcet=7,arrival_offset=0,workload=hash,fixed_priority_rank=0"
        : "period=20,wcet=7,arrival_offset=0,workload=hash");
    scheduler.addToReadyQueue(&task_a);
    scheduler.addToReadyQueue(&task_b);
    return {scheduler._ready_queue.begin(), scheduler._ready_queue.end()};
}

TEST(PriorityEnergyFixedPriorityIntegration, NineSchedulersOrderRMAndDM) {
    auto rm_asap_block = sortedTwoTaskPriorityOrder<ASAPBlockScheduler>(false);
    auto dm_asap_block = sortedTwoTaskPriorityOrder<ASAPBlockScheduler>(true);
    EXPECT_EQ(rm_asap_block[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_asap_block[0]->getTaskNumber(), 2);
    auto rm_asap_nonblock = sortedTwoTaskPriorityOrder<ASAPNonBlockScheduler>(false);
    auto dm_asap_nonblock = sortedTwoTaskPriorityOrder<ASAPNonBlockScheduler>(true);
    EXPECT_EQ(rm_asap_nonblock[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_asap_nonblock[0]->getTaskNumber(), 2);
    auto rm_asap_sync = sortedTwoTaskPriorityOrder<ASAPSyncScheduler>(false);
    auto dm_asap_sync = sortedTwoTaskPriorityOrder<ASAPSyncScheduler>(true);
    EXPECT_EQ(rm_asap_sync[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_asap_sync[0]->getTaskNumber(), 2);
    auto rm_alap_block = sortedTwoTaskPriorityOrder<ALAPBlockScheduler>(false);
    auto dm_alap_block = sortedTwoTaskPriorityOrder<ALAPBlockScheduler>(true);
    EXPECT_EQ(rm_alap_block[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_alap_block[0]->getTaskNumber(), 2);
    auto rm_alap_nonblock = sortedTwoTaskPriorityOrder<ALAPNonBlockScheduler>(false);
    auto dm_alap_nonblock = sortedTwoTaskPriorityOrder<ALAPNonBlockScheduler>(true);
    EXPECT_EQ(rm_alap_nonblock[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_alap_nonblock[0]->getTaskNumber(), 2);
    auto rm_alap_sync = sortedTwoTaskPriorityOrder<ALAPSyncScheduler>(false);
    auto dm_alap_sync = sortedTwoTaskPriorityOrder<ALAPSyncScheduler>(true);
    EXPECT_EQ(rm_alap_sync[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_alap_sync[0]->getTaskNumber(), 2);
    auto rm_st_block = sortedTwoTaskPriorityOrder<STBlockScheduler>(false);
    auto dm_st_block = sortedTwoTaskPriorityOrder<STBlockScheduler>(true);
    EXPECT_EQ(rm_st_block[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_st_block[0]->getTaskNumber(), 2);
    auto rm_st_nonblock = sortedTwoTaskPriorityOrder<STNonBlockScheduler>(false);
    auto dm_st_nonblock = sortedTwoTaskPriorityOrder<STNonBlockScheduler>(true);
    EXPECT_EQ(rm_st_nonblock[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_st_nonblock[0]->getTaskNumber(), 2);
    auto rm_st_sync = sortedSTSyncTwoTaskPriorityOrder(false);
    auto dm_st_sync = sortedSTSyncTwoTaskPriorityOrder(true);
    EXPECT_EQ(rm_st_sync[0]->getTaskNumber(), 1);
    EXPECT_EQ(dm_st_sync[0]->getTaskNumber(), 2);
};

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

TEST(PriorityEnergyFixedPriorityIntegration,
     NineSchedulersRejectInvalidRankBeforeRegistration) {
    const std::string invalid = kBaseParams + ",fixed_priority_rank=-1";
    expectSchedulerRejectsFixedPriorityRank<ASAPBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<ASAPNonBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<ASAPSyncScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<ALAPBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<ALAPNonBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<ALAPSyncScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<STBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<STNonBlockScheduler>(invalid);
    expectSchedulerRejectsFixedPriorityRank<STSyncScheduler>(invalid);
}

}  // namespace
}  // namespace RTSim
