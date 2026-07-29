#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/mrtkernel.hpp>
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

#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/scheduler/st_energy_utils.hpp>

#ifndef PARTSIM_SOURCE_DIR
#error "PARTSIM_SOURCE_DIR must be defined for exclusive-horizon tests"
#endif

#ifndef PARTSIM_RTSIM_EXE
#error "PARTSIM_RTSIM_EXE must be defined for exclusive-horizon tests"
#endif

namespace RTSim {
namespace {

constexpr std::uint64_t kExclusiveHorizonMs = 3;

MetaSim::Tick testTick(std::uint64_t value) {
    return MetaSim::Tick(
        static_cast<MetaSim::Tick::impl_t>(value));
}

class ScopedSchedulerProfile {
public:
    explicit ScopedSchedulerProfile(bool enabled) {
        install(enabled);
    }

    ~ScopedSchedulerProfile() {
        EnergyBridge::getInstance().shutdown();
        install(false);
        ConfigManager::setConfigCallback(nullptr);
    }

    ScopedSchedulerProfile(const ScopedSchedulerProfile &) = delete;
    ScopedSchedulerProfile &operator=(const ScopedSchedulerProfile &) =
        delete;

private:
    static void install(bool enabled) {
        ConfigManager::setConfigCallback(
            [enabled](
                const std::string &,
                ConfigManager::ConfigurationState &state) {
                static std::uint64_t generation = UINT64_C(900000);
                state.config_generation = generation++;
                state.num_cores = 1;
                state.scheduler_type = "exclusive-horizon-test";
                state.base_frequency = 9000.0;
                state.unit_time = 1;
                state.initial_energy = 10.0;
                state.max_energy = 20.0;
                state.base_harvest_rate = 0.0;
                state.enable_energy_recovery = false;
                state.periodic_collection_interval = 1;
                state.base_power = 0.5;
                state.frequency_power_ratios = {{9000, 1.0}};
                state.priority_energy_profile.enabled = enabled;
                state.priority_energy_profile.profile_id =
                    enabled ? "b4_pe_three_stage_v1" : "";
                state.priority_energy_profile.alpha_w = 0.0;
                state.priority_energy_profile.horizon_ms =
                    kExclusiveHorizonMs;
                state.priority_energy_profile.tick_ms = 1;
                LegacySolarConfig source;
                source.base_harvesting_power_w = 0.0;
                source.use_real_solar_data = false;
                state.harvest_source_config = source;
                return true;
            });
        if (!ConfigManager::getInstance().loadSystemConfig(
                "priority-energy-exclusive-horizon-test")) {
            throw std::runtime_error(
                "cannot install exclusive-horizon test profile");
        }
    }
};

class ExclusiveHorizonTask : public Task {
public:
    ExclusiveHorizonTask()
        : Task(
              nullptr,
              MetaSim::Tick(10),
              MetaSim::Tick(0),
              "exclusive-horizon-active-job",
              1000,
              MetaSim::Tick(10)),
          _schedule_count(0) {
        insertCode("fixed(10, control);");
    }

    MetaSim::Tick getPeriod() const override {
        return MetaSim::Tick(10);
    }

    void schedule() override {
        state = TSK_EXEC;
        ++_schedule_count;
        _last_schedule_tick =
            static_cast<std::int64_t>(SIMUL.getTime());
    }

    void deschedule() override {
        state = TSK_READY;
    }

    int scheduleCount() const noexcept {
        return _schedule_count;
    }

    std::int64_t lastScheduleTick() const noexcept {
        return _last_schedule_tick;
    }

private:
    int _schedule_count;
    std::int64_t _last_schedule_tick = -1;
};

class ScopedSimulationRun {
public:
    explicit ScopedSimulationRun(MetaSim::Simulation &simulation)
        : _simulation(simulation) {}

    ~ScopedSimulationRun() {
        _simulation.endSingleRun();
    }

    ScopedSimulationRun(const ScopedSimulationRun &) = delete;
    ScopedSimulationRun &operator=(const ScopedSimulationRun &) = delete;

private:
    MetaSim::Simulation &_simulation;
};

template <typename SchedulerType>
MetaSim::Tick energyCommitTick(const SchedulerType &scheduler) {
    return scheduler._energy_commit_tick;
}

template <>
MetaSim::Tick energyCommitTick<ASAPBlockScheduler>(
    const ASAPBlockScheduler &scheduler) {
    return scheduler._last_energy_commit_tick;
}

struct HorizonOutcome {
    int decision_ticks = 0;
    std::int64_t last_selection_tick = -1;
    std::int64_t last_energy_commit_tick = -1;
    std::int64_t last_dispatch_tick = -1;
    int task_schedule_count = 0;
    bool active_job_at_end = false;
    MechanismSummary mechanism;
    B4ObservabilityEnergySnapshot energy;
};

template <typename SchedulerType>
HorizonOutcome runExclusiveHorizonScenario(
    MetaSim::Tick stop_time,
    bool finalize_energy) {
    ScopedSchedulerProfile profile(true);
    auto &simulation = MetaSim::Simulation::getInstance();
    SchedulerType scheduler;
    CPU cpu("exclusive-horizon-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    ExclusiveHorizonTask task;
    kernel.addTask(
        task,
        "period=10,wcet=10,arrival_offset=0,workload=control");

    static std::uint64_t trace_serial = 0;
    const std::string trace_path =
        "/tmp/partsim_priority_energy_exclusive_horizon_" +
        std::to_string(trace_serial++) + ".json";
    std::remove(trace_path.c_str());
    JSONTrace trace(trace_path, testTick(kExclusiveHorizonMs));
    scheduler.setTraceLogger(&trace);
    scheduler.setSemanticTraceEnabled(true);

    simulation.initSingleRun();
    ScopedSimulationRun run_guard(simulation);
    trace.enableObservabilitySummaries(
        testTick(kExclusiveHorizonMs));
    scheduler._initial_energy = 10.0;
    scheduler._current_energy = 10.0;
    scheduler._max_energy = 20.0;
    task.activate(MetaSim::Tick(0));
    simulation.run_to(stop_time);

    HorizonOutcome outcome;
    outcome.decision_ticks = scheduler._stats.total_tick_count;
    outcome.last_selection_tick =
        static_cast<std::int64_t>(scheduler._selection_tick);
    outcome.last_energy_commit_tick =
        static_cast<std::int64_t>(energyCommitTick(scheduler));
    outcome.last_dispatch_tick = task.lastScheduleTick();
    outcome.task_schedule_count = task.scheduleCount();
    outcome.active_job_at_end = task.isActive();

    if (finalize_energy) {
        trace.finalizeObservabilitySummaries(
            testTick(kExclusiveHorizonMs));
        outcome.mechanism = trace.mechanismSummary();
        outcome.energy = scheduler.getB4ObservabilityEnergySnapshot(
            kExclusiveHorizonMs);
    }

    std::remove(trace_path.c_str());
    return outcome;
}

template <typename SchedulerType>
int runNonPriorityEnergyScenario() {
    ScopedSchedulerProfile profile(false);
    auto &simulation = MetaSim::Simulation::getInstance();
    SchedulerType scheduler;
    CPU cpu("non-priority-energy-horizon-cpu", nullptr);
    MRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});

    simulation.initSingleRun();
    ScopedSimulationRun run_guard(simulation);
    scheduler._initial_energy = 10.0;
    scheduler._current_energy = 10.0;
    scheduler._max_energy = 20.0;
    simulation.run_to(testTick(kExclusiveHorizonMs));
    return scheduler._stats.total_tick_count;
}

template <typename SchedulerType>
class PriorityEnergyExclusiveHorizonTest : public testing::Test {};

using PriorityEnergySchedulers = testing::Types<
    ASAPBlockScheduler,
    ASAPNonBlockScheduler,
    ASAPSyncScheduler,
    ALAPBlockScheduler,
    ALAPNonBlockScheduler,
    ALAPSyncScheduler,
    STBlockScheduler,
    STNonBlockScheduler,
    STSyncScheduler>;

TYPED_TEST_SUITE(
    PriorityEnergyExclusiveHorizonTest,
    PriorityEnergySchedulers);

TYPED_TEST(
    PriorityEnergyExclusiveHorizonTest,
    PrefixThroughHMinusOneRetainsEveryDecisionAndCommit) {
    const HorizonOutcome outcome =
        runExclusiveHorizonScenario<TypeParam>(
            testTick(kExclusiveHorizonMs - 1),
            false);
    EXPECT_EQ(outcome.decision_ticks, 3);
    EXPECT_EQ(outcome.last_selection_tick, 2);
    EXPECT_EQ(outcome.last_energy_commit_tick, 2);
    EXPECT_GE(outcome.last_dispatch_tick, 0);
    EXPECT_LT(
        outcome.last_dispatch_tick,
        static_cast<std::int64_t>(kExclusiveHorizonMs));
    EXPECT_GT(outcome.task_schedule_count, 0);
    EXPECT_TRUE(outcome.active_job_at_end);
}

TYPED_TEST(
    PriorityEnergyExclusiveHorizonTest,
    StopsBeforeHAndFinalizerClosesLastEnergyInterval) {
    const HorizonOutcome outcome =
        runExclusiveHorizonScenario<TypeParam>(
            testTick(kExclusiveHorizonMs),
            true);
    EXPECT_EQ(outcome.decision_ticks, 3);
    EXPECT_EQ(outcome.last_selection_tick, 2);
    EXPECT_EQ(outcome.last_energy_commit_tick, 2);
    EXPECT_GE(outcome.last_dispatch_tick, 0);
    EXPECT_LT(
        outcome.last_dispatch_tick,
        static_cast<std::int64_t>(kExclusiveHorizonMs));
    EXPECT_GT(outcome.task_schedule_count, 0);
    EXPECT_TRUE(outcome.active_job_at_end);
    EXPECT_EQ(outcome.mechanism.observed_decision_ticks, 3u);
    EXPECT_EQ(outcome.energy.summary.observed_energy_intervals, 3u);

    const double reconciled =
        outcome.energy.initial_energy_j +
        outcome.energy.summary.credited_energy_j -
        outcome.energy.summary.consumed_energy_j;
    const double scale = std::max(
        {1.0,
         std::abs(reconciled),
         std::abs(outcome.energy.summary.battery_final_j)});
    EXPECT_LE(
        std::abs(
            reconciled -
            outcome.energy.summary.battery_final_j),
        STEnergy::kEnergyEpsilonJ * scale);
}

TYPED_TEST(
    PriorityEnergyExclusiveHorizonTest,
    DisabledProfilePreservesInclusiveEndpointScheduling) {
    EXPECT_EQ(runNonPriorityEnergyScenario<TypeParam>(), 4);
}

TEST(PriorityEnergyExclusiveHorizon,
     SharedBoundaryRejectsNegativeBeforeUnsignedConversion) {
    EXPECT_THROW(
        (void)priorityEnergyDecisionTickWithinExclusiveHorizon(-1, 3),
        std::logic_error);
    EXPECT_TRUE(
        priorityEnergyDecisionTickWithinExclusiveHorizon(0, 3));
    EXPECT_TRUE(
        priorityEnergyDecisionTickWithinExclusiveHorizon(2, 3));
    EXPECT_FALSE(
        priorityEnergyDecisionTickWithinExclusiveHorizon(3, 3));
}

class TemporaryRegressionDirectory {
public:
    TemporaryRegressionDirectory() {
        const std::string pattern =
            (std::filesystem::temp_directory_path() /
             "partsim_exclusive_horizon_e2e_XXXXXX")
                .string();
        std::vector<char> mutable_pattern(
            pattern.begin(), pattern.end());
        mutable_pattern.push_back('\0');
        char *created = ::mkdtemp(mutable_pattern.data());
        if (!created) {
            throw std::system_error(
                errno,
                std::generic_category(),
                "cannot create exclusive-horizon regression directory");
        }
        _path = created;
    }

    ~TemporaryRegressionDirectory() {
        std::error_code error;
        std::filesystem::remove_all(_path, error);
    }

    TemporaryRegressionDirectory(
        const TemporaryRegressionDirectory &) = delete;
    TemporaryRegressionDirectory &operator=(
        const TemporaryRegressionDirectory &) = delete;

    const std::filesystem::path &path() const noexcept {
        return _path;
    }

private:
    std::filesystem::path _path;
};

std::string readFile(const std::filesystem::path &path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error(
            "cannot read regression file: " + path.string());
    }
    return std::string(
        std::istreambuf_iterator<char>(input),
        std::istreambuf_iterator<char>());
}

void writeFile(
    const std::filesystem::path &path,
    const std::string &contents) {
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error(
            "cannot create regression file: " + path.string());
    }
    output << contents;
    if (!output) {
        throw std::runtime_error(
            "cannot write regression file: " + path.string());
    }
}

void replaceExactlyOnce(
    std::string &source,
    const std::string &needle,
    const std::string &replacement) {
    const std::size_t position = source.find(needle);
    if (position == std::string::npos ||
        source.find(needle, position + needle.size()) !=
            std::string::npos) {
        throw std::runtime_error(
            "exclusive-horizon system template placeholder mismatch");
    }
    source.replace(position, needle.size(), replacement);
}

std::string systemConfigFor(const std::string &scheduler_id) {
    std::string config = readFile(
        std::filesystem::path(PARTSIM_SOURCE_DIR) /
        "v9_3_b4_priority_energy_system_template.yml");
    replaceExactlyOnce(
        config,
        "priority_energy:\n"
        "  enabled: false\n"
        "  profile_id: b4_pe_three_stage_v1\n"
        "  alpha_w: 0.0\n"
        "  horizon_ms: 30000\n"
        "  tick_ms: 1\n",
        "priority_energy:\n"
        "  enabled: true\n"
        "  profile_id: b4_pe_three_stage_v1\n"
        "  alpha_w: 0.0\n"
        "  horizon_ms: 30000\n"
        "  tick_ms: 1\n");
    replaceExactlyOnce(
        config,
        "      scheduler: gpfp_asap_block\n",
        "      scheduler: " + scheduler_id + "\n");
    replaceExactlyOnce(
        config,
        "  day_of_year: 187\n"
        "  time_of_day_ms: 21900000\n"
        "  base_harvesting_rate: 0.054\n"
        "  harvesting_scale: 1.0\n"
        "\n"
        "  use_real_solar_data: true\n"
        "  solar_data_file: \"data/processed/shenyang_solar_minute.csv\"\n"
        "  pv_efficiency: 0.18\n"
        "  pv_area_m2: 1.0\n"
        "\n",
        "");
    return config;
}

std::string activeTaskset() {
    std::ostringstream tasks;
    tasks << "taskset:\n";
    for (int index = 0; index < 10; ++index) {
        const int arrival_offset = index == 9 ? 199 : 0;
        tasks
            << "  - name: task_" << index << "\n"
            << "    iat: 200\n"
            << "    runtime: 60\n"
            << "    deadline: 200\n"
            << "    startcpu: " << index % 4 << "\n"
            << "    params: period=200,wcet=60,"
               "arrival_offset=" << arrival_offset << ",workload=control,"
               "task_energy_factor=1\n"
            << "    code:\n"
            << "      - fixed(60, control)\n";
    }
    tasks << "resources: []\n";
    return tasks.str();
}

template <typename SchedulerType>
const char *schedulerFactoryId();

template <>
const char *schedulerFactoryId<ASAPBlockScheduler>() {
    return "gpfp_asap_block";
}

template <>
const char *schedulerFactoryId<ASAPNonBlockScheduler>() {
    return "gpfp_asap_nonblock";
}

template <>
const char *schedulerFactoryId<ASAPSyncScheduler>() {
    return "gpfp_asap_sync";
}

template <>
const char *schedulerFactoryId<ALAPBlockScheduler>() {
    return "gpfp_alap_block";
}

template <>
const char *schedulerFactoryId<ALAPNonBlockScheduler>() {
    return "gpfp_alap_nonblock";
}

template <>
const char *schedulerFactoryId<ALAPSyncScheduler>() {
    return "gpfp_alap_sync";
}

template <>
const char *schedulerFactoryId<STBlockScheduler>() {
    return "gpfp_st_block";
}

template <>
const char *schedulerFactoryId<STNonBlockScheduler>() {
    return "gpfp_st_nonblock";
}

template <>
const char *schedulerFactoryId<STSyncScheduler>() {
    return "gpfp_st_sync";
}

struct ProcessResult {
    int exit_code = -1;
    int termination_signal = 0;
    bool timed_out = false;
};

ProcessResult runSimulator(
    const std::filesystem::path &system_path,
    const std::filesystem::path &taskset_path,
    const std::filesystem::path &trace_path,
    const std::filesystem::path &stderr_path,
    const std::string &run_id) {
    const pid_t child = ::fork();
    if (child < 0) {
        throw std::system_error(
            errno,
            std::generic_category(),
            "cannot fork exclusive-horizon simulator");
    }
    if (child == 0) {
        const int null_output =
            ::open("/dev/null", O_WRONLY);
        const int error_output =
            ::open(
                stderr_path.c_str(),
                O_WRONLY | O_CREAT | O_TRUNC,
                0600);
        if (null_output < 0 || error_output < 0 ||
            ::dup2(null_output, STDOUT_FILENO) < 0 ||
            ::dup2(error_output, STDERR_FILENO) < 0 ||
            ::chdir(PARTSIM_SOURCE_DIR) != 0) {
            _exit(125);
        }
        ::close(null_output);
        ::close(error_output);
        const std::string semantic_hash(64, '0');
        ::execl(
            PARTSIM_RTSIM_EXE,
            PARTSIM_RTSIM_EXE,
            system_path.c_str(),
            taskset_path.c_str(),
            "30000",
            "-t",
            trace_path.c_str(),
            "--run-id",
            run_id.c_str(),
            "--taskset-semantic-hash",
            semantic_hash.c_str(),
            "--b4-observability-summary",
            "--b4-summary-horizon",
            "30000",
            "--b4-observability-contract-version",
            "2",
            static_cast<char *>(nullptr));
        _exit(126);
    }

    int status = 0;
    const auto deadline =
        std::chrono::steady_clock::now() +
        std::chrono::seconds(120);
    while (std::chrono::steady_clock::now() < deadline) {
        const pid_t observed = ::waitpid(child, &status, WNOHANG);
        if (observed == child) {
            return {
                WIFEXITED(status) ? WEXITSTATUS(status) : -1,
                WIFSIGNALED(status) ? WTERMSIG(status) : 0,
                false};
        }
        if (observed < 0) {
            throw std::system_error(
                errno,
                std::generic_category(),
                "cannot wait for exclusive-horizon simulator");
        }
        std::this_thread::sleep_for(
            std::chrono::milliseconds(10));
    }
    (void)::kill(child, SIGKILL);
    (void)::waitpid(child, &status, 0);
    return {-1, SIGKILL, true};
}

template <typename SchedulerType>
void expectPublishedEndToEndTrace() {
    TemporaryRegressionDirectory temporary;
    const std::string scheduler_id =
        schedulerFactoryId<SchedulerType>();
    const auto system_path =
        temporary.path() / "system.yml";
    const auto taskset_path =
        temporary.path() / "tasks.yml";
    const auto trace_path =
        temporary.path() / "trace.json";
    const auto stderr_path =
        temporary.path() / "stderr.txt";
    writeFile(system_path, systemConfigFor(scheduler_id));
    writeFile(taskset_path, activeTaskset());

    const ProcessResult process = runSimulator(
        system_path,
        taskset_path,
        trace_path,
        stderr_path,
        "exclusive-horizon-" + scheduler_id);
    const std::string errors = readFile(stderr_path);
    EXPECT_FALSE(process.timed_out) << errors;
    EXPECT_EQ(process.termination_signal, 0) << errors;
    EXPECT_EQ(process.exit_code, 0) << errors;
    ASSERT_TRUE(std::filesystem::is_regular_file(trace_path))
        << errors;
    EXPECT_EQ(
        errors.find(
            "final observation battery does not match the energy ledger"),
        std::string::npos);
    EXPECT_EQ(
        errors.find("trace integrity error"),
        std::string::npos);
    EXPECT_EQ(
        errors.find("TRACE PUBLICATION ERROR"),
        std::string::npos);

    const std::string trace = readFile(trace_path);
    EXPECT_NE(
        trace.find("\"trace_schema_version\": 3"),
        std::string::npos);
    EXPECT_NE(
        trace.find("\"observed_decision_ticks\": 30000"),
        std::string::npos);
    EXPECT_NE(
        trace.find("\"observed_energy_intervals\": 30000"),
        std::string::npos);
    EXPECT_NE(
        trace.find("\"simulation_completed\": true"),
        std::string::npos);
    EXPECT_TRUE(std::regex_search(
        trace,
        std::regex(
            "\"unfinished_at_horizon_jobs\"[[:space:]]*:"
            "[[:space:]]*[1-9][0-9]*")));
    EXPECT_NE(
        trace.find(
            "\"configured_scheduler\": \"" +
            scheduler_id + "\""),
        std::string::npos);
}

TYPED_TEST(
    PriorityEnergyExclusiveHorizonTest,
    EndToEndSimulatorPublishesConservedTraceAtFrozenHorizon) {
    expectPublishedEndToEndTrace<TypeParam>();
}

} // namespace
} // namespace RTSim
