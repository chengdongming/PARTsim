#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/harvesting/scaled_piecewise_source.hpp>
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/scheduler/gpfp_asap_block_scheduler.hpp>
#include <rtsim/scheduler/priority_energy_runtime.hpp>
#include <rtsim/scheduler/scheduler_harvest_runtime.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace RTSim {
    namespace {
        class TemporaryHarvestDirectory {
        public:
            TemporaryHarvestDirectory() {
                char pattern[] = "/tmp/partsim_scheduler_harvest_XXXXXX";
                char *created = ::mkdtemp(pattern);
                if (created == nullptr) {
                    throw std::runtime_error(
                        "cannot create scheduler harvest test directory");
                }
                _path = created;
            }

            ~TemporaryHarvestDirectory() {
                std::error_code error;
                std::filesystem::remove_all(_path, error);
            }

            std::filesystem::path write(const std::string &name,
                                        const std::string &contents) const {
                const std::filesystem::path file = _path / name;
                std::ofstream output(
                    file, std::ios::binary | std::ios::trunc);
                if (!output.is_open()) {
                    throw std::runtime_error(
                        "cannot write scheduler harvest test trace");
                }
                output.write(contents.data(),
                             static_cast<std::streamsize>(contents.size()));
                if (!output.good()) {
                    throw std::runtime_error(
                        "cannot finish scheduler harvest test trace");
                }
                return file;
            }

        private:
            std::filesystem::path _path;
        };

        class ScopedSchedulerHarvestConfiguration {
        public:
            explicit ScopedSchedulerHarvestConfiguration(
                const std::filesystem::path &config_file) {
                EnergyBridge::getInstance().shutdown();
                EnergyBridge::ensureConfigCallbackRegistered();
                if (!ConfigManager::getInstance().loadSystemConfig(
                        config_file.string())) {
                    throw std::runtime_error(
                        "cannot load scheduler harvest test configuration");
                }
            }

            ~ScopedSchedulerHarvestConfiguration() {
                EnergyBridge::getInstance().shutdown();
                ConfigManager::setConfigCallback(nullptr);
                (void)ConfigManager::getInstance().loadSystemConfig("");
            }
        };

        std::uint64_t binary64Bits(double value) {
            static_assert(sizeof(double) == sizeof(std::uint64_t),
                          "scheduler harvest tests require binary64 double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        ScaledPiecewiseConfig constantConfig(double power_w,
                                             std::uint64_t horizon_ms) {
            ScaledPiecewiseConfig config;
            config.scale_w = power_w;
            config.segments = {{0, horizon_ms, 1.0}};
            return config;
        }

        ScaledPiecewiseConfig b4Config(double alpha_w) {
            ScaledPiecewiseConfig config;
            config.scale_w = alpha_w;
            config.segments = {
                {0, 5000, 1.0},
                {5000, 15000, 0.2},
                {15000, 30000, 1.0},
            };
            return config;
        }

        PriorityEnergyProfileConfig b4OracleConfig(double alpha_w) {
            PriorityEnergyProfileConfig config;
            config.enabled = true;
            config.profile_id = "b4_pe_three_stage_v1";
            config.alpha_w = alpha_w;
            config.horizon_ms = 30000;
            config.tick_ms = 1;
            return config;
        }

        SampledTraceConfig sampledConfig(
            const std::filesystem::path &file) {
            SampledTraceConfig config;
            config.file = file.string();
            config.time_column = "timestamp_ms";
            config.value_column = "power_w";
            config.value_type = TraceValueType::ElectricalPower;
            config.interpolation = TraceInterpolation::ZeroOrderHold;
            config.after_trace = TraceAfterEnd::Zero;
            config.panel_area_m2 = 0.0;
            config.conversion_efficiency = 0.0;
            config.max_file_size_bytes = UINT64_C(1048576);
            config.max_rows = UINT64_C(1000);
            return config;
        }
    } // namespace

    TEST(SchedulerHarvestRuntimeIntegration,
         TimeZeroDoesNotApplyAnIntervalOrChangeBattery) {
        SchedulerHarvestRuntime runtime;
        runtime.beginRun(constantConfig(1.0, 10));

        const double negative_zero = -0.0;
        const HarvestResult result =
            runtime.applyAtDecisionTime(0, negative_zero, 1.0);

        EXPECT_EQ(binary64Bits(result.offered_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(result.actual_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(result.clipped_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(result.battery_before_j),
                  binary64Bits(negative_zero));
        EXPECT_EQ(binary64Bits(result.battery_after_j),
                  binary64Bits(negative_zero));
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         DecisionOneAppliesIntervalZeroAndDuplicateIsRejected) {
        const ScaledPiecewiseConfig config = constantConfig(2.0, 10);
        const ScaledPiecewiseSource oracle(config);
        SchedulerHarvestRuntime runtime;
        runtime.beginRun(config);

        const HarvestResult first =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        EXPECT_EQ(binary64Bits(first.offered_j),
                  binary64Bits(oracle.offeredEnergyForInterval(
                      {0, 0, 1})));
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);

        EXPECT_THROW(
            (void)runtime.applyAtDecisionTime(1, first.battery_after_j, 1.0),
            std::invalid_argument);
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);

        const HarvestResult second = runtime.applyAtDecisionTime(
            2, first.battery_after_j, 1.0);
        EXPECT_EQ(binary64Bits(second.offered_j),
                  binary64Bits(oracle.offeredEnergyForInterval(
                      {1, 1, 2})));
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 1u);
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         LastHorizonIntervalIsAppliedExactlyOnceWithoutEndRunAccounting) {
        SchedulerHarvestRuntime runtime;
        runtime.beginRun(b4Config(0.054));

        double battery = 0.0;
        for (std::int64_t decision_time = 1;
             decision_time <= 30000;
             ++decision_time) {
            const HarvestResult result = runtime.applyAtDecisionTime(
                decision_time,
                battery,
                std::numeric_limits<double>::max());
            battery = result.battery_after_j;
        }

        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), UINT64_C(29999));
        EXPECT_THROW(
            (void)runtime.applyAtDecisionTime(30000, battery,
                                              std::numeric_limits<double>::max()),
            std::invalid_argument);
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), UINT64_C(29999));
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         RuntimeOwnsClippingAndPreservesZeroOffered) {
        SchedulerHarvestRuntime full;
        full.beginRun(constantConfig(1.0, 2));
        const HarvestResult clipped =
            full.applyAtDecisionTime(1, 1.0, 1.0);
        EXPECT_GT(clipped.offered_j, 0.0);
        EXPECT_EQ(binary64Bits(clipped.actual_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(clipped.clipped_j),
                  binary64Bits(clipped.offered_j));
        EXPECT_EQ(binary64Bits(clipped.battery_after_j),
                  binary64Bits(1.0));

        SchedulerHarvestRuntime zero;
        zero.beginRun(constantConfig(0.0, 2));
        const HarvestResult unchanged =
            zero.applyAtDecisionTime(1, 0.25, 1.0);
        EXPECT_EQ(binary64Bits(unchanged.offered_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(unchanged.actual_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(unchanged.clipped_j), binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(unchanged.battery_after_j),
                  binary64Bits(0.25));
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         SourceExceptionDoesNotAdvanceLedgerOrPartiallyUpdateCallerBattery) {
        TemporaryHarvestDirectory directory;
        const std::filesystem::path trace = directory.write(
            "invalid_legacy.txt", "irradiance\nnan\n2\n");
        LegacySolarConfig config;
        config.use_real_solar_data = true;
        config.solar_data_file = trace.string();

        SchedulerHarvestRuntime runtime;
        runtime.beginRun(config);
        double battery = 0.25;
        EXPECT_THROW(
            {
                const HarvestResult result =
                    runtime.applyAtDecisionTime(1, battery, 1.0);
                battery = result.battery_after_j;
            },
            std::domain_error);
        EXPECT_EQ(binary64Bits(battery), binary64Bits(0.25));
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         B4ResultBitsMatchFrozenOracleForAllNineSchedulerInstances) {
        constexpr double alpha_w = 0.054;
        std::array<SchedulerHarvestRuntime, 9> runtimes;
        for (auto &runtime : runtimes) {
            runtime.beginRun(b4Config(alpha_w));
        }
        const PriorityEnergyRuntime oracle(b4OracleConfig(alpha_w));

        for (std::uint64_t index = 0; index < 30000; ++index) {
            const double offered = oracle.applyHarvest(
                index + 1,
                0.0,
                std::numeric_limits<double>::max()).offered_j;
            for (std::size_t algorithm = 0;
                 algorithm < runtimes.size();
                 ++algorithm) {
                double battery_before = 0.0;
                switch (algorithm) {
                    case 0:
                        battery_before = 0.0;
                        break;
                    case 1:
                        battery_before = 1.0;
                        break;
                    case 2:
                        battery_before = 0.99999;
                        break;
                    case 3:
                        battery_before = 0.5;
                        break;
                    case 4:
                        battery_before =
                            std::nextafter(1.0, 0.0);
                        break;
                    case 5:
                        battery_before = index % 2 == 0 ? 0.0 : 1.0;
                        break;
                    case 6:
                        battery_before = 0.25;
                        break;
                    case 7:
                        battery_before = 1.0 - offered;
                        break;
                    case 8:
                        battery_before = -0.0;
                        break;
                }
                const PriorityEnergyHarvestStep expected =
                    oracle.applyHarvest(
                        index + 1, battery_before, 1.0);
                const HarvestResult actual =
                    runtimes[algorithm].applyAtDecisionTime(
                        static_cast<std::int64_t>(index + 1),
                        battery_before,
                        1.0);
                if (binary64Bits(actual.offered_j) !=
                        binary64Bits(expected.offered_j) ||
                    binary64Bits(actual.actual_j) !=
                        binary64Bits(expected.actual_j) ||
                    binary64Bits(actual.clipped_j) !=
                        binary64Bits(expected.clipped_j) ||
                    binary64Bits(actual.battery_after_j) !=
                        binary64Bits(expected.battery_after_j)) {
                    ADD_FAILURE()
                        << "algorithm=" << algorithm
                        << " interval=" << index
                        << " expected_offered_bits="
                        << binary64Bits(expected.offered_j)
                        << " actual_offered_bits="
                        << binary64Bits(actual.offered_j)
                        << " expected_actual_bits="
                        << binary64Bits(expected.actual_j)
                        << " actual_actual_bits="
                        << binary64Bits(actual.actual_j)
                        << " expected_clipped_bits="
                        << binary64Bits(expected.clipped_j)
                        << " actual_clipped_bits="
                        << binary64Bits(actual.clipped_j)
                        << " expected_after_bits="
                        << binary64Bits(expected.battery_after_j)
                        << " actual_after_bits="
                        << binary64Bits(actual.battery_after_j);
                    return;
                }
            }
        }
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         OfferedIsSharedWhileActualAndClippedFollowBatteryState) {
        const ScaledPiecewiseConfig config = constantConfig(1.0, 2);
        SchedulerHarvestRuntime empty;
        SchedulerHarvestRuntime full;
        empty.beginRun(config);
        full.beginRun(config);

        const HarvestResult accepted =
            empty.applyAtDecisionTime(1, 0.0, 1.0);
        const HarvestResult clipped =
            full.applyAtDecisionTime(1, 1.0, 1.0);

        EXPECT_EQ(binary64Bits(accepted.offered_j),
                  binary64Bits(clipped.offered_j));
        EXPECT_GT(accepted.actual_j, clipped.actual_j);
        EXPECT_LT(accepted.clipped_j, clipped.clipped_j);
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         SampledTraceIsPreloadedBeforeRuntimeQueries) {
        TemporaryHarvestDirectory directory;
        const std::filesystem::path trace = directory.write(
            "trace.csv", "timestamp_ms,power_w\n0,2\n10,0\n");
        const SampledTraceConfig config = sampledConfig(trace);
        SchedulerHarvestRuntime runtime;
        runtime.beginRun(config);

        ASSERT_TRUE(std::filesystem::remove(trace));
        const HarvestResult result =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        EXPECT_GT(result.offered_j, 0.0);
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         ASAPBlockConsumesPreloadedSampledTraceAcrossIntervalsAndRuns) {
        TemporaryHarvestDirectory directory;
        const std::string trace_contents =
            "timestamp_ms,power_w\n"
            "0,1000\n"
            "2,0\n"
            "5,2000\n"
            "7,0\n";
        const std::filesystem::path trace = directory.write(
            "formal_scheduler_trace.csv", trace_contents);
        const std::filesystem::path system_config = directory.write(
            "formal_scheduler.yml",
            "energy_management:\n"
            "  initial_energy: 0.0\n"
            "  max_energy: 100.0\n"
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: " + trace.string() + "\n"
            "    time_column: timestamp_ms\n"
            "    value_column: power_w\n"
            "    value_type: electrical_power\n"
            "    interpolation: zero_order_hold\n"
            "    after_trace: zero\n"
            "    max_file_size_bytes: 1048576\n"
            "    max_rows: 1000\n");
        ScopedSchedulerHarvestConfiguration configured(system_config);
        ConfigManager::getInstance().setBaseHarvestRate(1.0e9);

        ASAPBlockScheduler scheduler;
        auto &simulation = MetaSim::Simulation::getInstance();
        simulation.initSingleRun();

        EXPECT_TRUE(std::filesystem::remove(trace));

        constexpr std::array<double, 9> expected_battery = {
            0.0, 1.0, 2.0, 2.0, 2.0, 2.0, 4.0, 6.0, 6.0};
        constexpr std::array<double, 8> expected_offered = {
            1.0, 1.0, 0.0, 0.0, 0.0, 2.0, 2.0, 0.0};

        simulation.run_to(MetaSim::Tick(0));
        EXPECT_EQ(binary64Bits(scheduler.getCurrentEnergy()),
                  binary64Bits(expected_battery[0]));
        EXPECT_EQ(binary64Bits(scheduler.getTotalEnergyHarvested()),
                  binary64Bits(0.0));

        double previous_offered_total = 0.0;
        for (std::int64_t decision_time = 1;
             decision_time <= 8;
             ++decision_time) {
            simulation.run_to(MetaSim::Tick(decision_time));
            const double offered_total =
                scheduler.getTotalEnergyHarvested();
            const double interval_offered =
                offered_total - previous_offered_total;
            EXPECT_EQ(binary64Bits(interval_offered),
                      binary64Bits(expected_offered[
                          static_cast<std::size_t>(decision_time - 1)]))
                << "decision_time=" << decision_time;
            EXPECT_EQ(binary64Bits(scheduler.getCurrentEnergy()),
                      binary64Bits(expected_battery[
                          static_cast<std::size_t>(decision_time)]))
                << "decision_time=" << decision_time;
            EXPECT_EQ(binary64Bits(offered_total),
                      binary64Bits(expected_battery[
                          static_cast<std::size_t>(decision_time)]))
                << "decision_time=" << decision_time;
            previous_offered_total = offered_total;
        }

        const double first_run_battery = scheduler.getCurrentEnergy();
        const double first_run_offered =
            scheduler.getTotalEnergyHarvested();
        simulation.endSingleRun();
        EXPECT_EQ(binary64Bits(scheduler.getCurrentEnergy()),
                  binary64Bits(first_run_battery));
        EXPECT_EQ(binary64Bits(scheduler.getTotalEnergyHarvested()),
                  binary64Bits(first_run_offered));

        (void)directory.write("formal_scheduler_trace.csv", trace_contents);
        simulation.initSingleRun();
        EXPECT_TRUE(std::filesystem::remove(trace));
        simulation.run_to(MetaSim::Tick(0));
        EXPECT_EQ(binary64Bits(scheduler.getCurrentEnergy()),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(scheduler.getTotalEnergyHarvested()),
                  binary64Bits(0.0));
        simulation.run_to(MetaSim::Tick(1));
        EXPECT_EQ(binary64Bits(scheduler.getCurrentEnergy()),
                  binary64Bits(1.0));
        EXPECT_EQ(binary64Bits(scheduler.getTotalEnergyHarvested()),
                  binary64Bits(1.0));
        simulation.endSingleRun();
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         FirstBeginRunFailureAllowsCleanRecovery) {
        TemporaryHarvestDirectory directory;
        const std::filesystem::path missing_trace = directory.write(
            "missing_on_first_begin.csv",
            "timestamp_ms,power_w\n0,1\n1,0\n");
        ASSERT_TRUE(std::filesystem::remove(missing_trace));

        SchedulerHarvestRuntime runtime;
        EXPECT_THROW(
            runtime.beginRun(sampledConfig(missing_trace)),
            std::runtime_error);
        EXPECT_FALSE(runtime.isInitialized());
        EXPECT_THROW((void)runtime.runtime(), std::logic_error);
        EXPECT_THROW(
            (void)runtime.applyAtDecisionTime(1, 0.0, 10.0),
            std::logic_error);

        runtime.beginRun(constantConfig(1000.0, 2));
        ASSERT_TRUE(runtime.isInitialized());
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());

        const HarvestResult time_zero =
            runtime.applyAtDecisionTime(0, 0.0, 10.0);
        EXPECT_EQ(binary64Bits(time_zero.offered_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(time_zero.actual_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(time_zero.clipped_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(time_zero.battery_before_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(time_zero.battery_after_j),
                  binary64Bits(0.0));
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());

        const HarvestResult interval_zero =
            runtime.applyAtDecisionTime(1, 0.0, 10.0);
        EXPECT_EQ(binary64Bits(interval_zero.offered_j),
                  binary64Bits(1.0));
        EXPECT_EQ(binary64Bits(interval_zero.actual_j),
                  binary64Bits(1.0));
        EXPECT_EQ(binary64Bits(interval_zero.clipped_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(interval_zero.battery_before_j),
                  binary64Bits(0.0));
        EXPECT_EQ(binary64Bits(interval_zero.battery_after_j),
                  binary64Bits(1.0));
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);

        const HarvestResult interval_one =
            runtime.applyAtDecisionTime(
                2, interval_zero.battery_after_j, 10.0);
        EXPECT_EQ(binary64Bits(interval_one.offered_j),
                  binary64Bits(1.0));
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 1u);
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         BeginRunConstructionFailureClearsPreviousRuntimeAndCanRecover) {
        TemporaryHarvestDirectory directory;
        const std::filesystem::path missing_trace = directory.write(
            "missing_after_write.csv",
            "timestamp_ms,power_w\n0,1\n1,0\n");
        ASSERT_TRUE(std::filesystem::remove(missing_trace));

        SchedulerHarvestRuntime runtime;
        runtime.beginRun(constantConfig(1.0, 2));
        const HarvestResult first =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        ASSERT_TRUE(runtime.isInitialized());
        ASSERT_TRUE(runtime.runtime().hasAppliedInterval());
        ASSERT_EQ(runtime.runtime().lastAppliedIndex(), 0u);

        EXPECT_ANY_THROW(runtime.beginRun(sampledConfig(missing_trace)));
        EXPECT_FALSE(runtime.isInitialized());
        EXPECT_THROW((void)runtime.runtime(), std::logic_error);
        EXPECT_THROW(
            (void)runtime.applyAtDecisionTime(
                2, first.battery_after_j, 1.0),
            std::logic_error);

        runtime.beginRun(constantConfig(2.0, 2));
        ASSERT_TRUE(runtime.isInitialized());
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());
        const HarvestResult recovered =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);
        EXPECT_NE(binary64Bits(first.offered_j),
                  binary64Bits(recovered.offered_j));
    }

    TEST(SchedulerHarvestRuntimeIntegration,
         BeginRunReplacesSourceAndResetsLedgerBetweenRuns) {
        SchedulerHarvestRuntime runtime;
        runtime.beginRun(constantConfig(1.0, 2));
        const HarvestResult first =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        ASSERT_TRUE(runtime.runtime().hasAppliedInterval());

        runtime.beginRun(constantConfig(2.0, 2));
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());
        const HarvestResult second =
            runtime.applyAtDecisionTime(1, 0.0, 1.0);
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);
        EXPECT_NE(binary64Bits(first.offered_j),
                  binary64Bits(second.offered_j));

        runtime.beginRun(constantConfig(0.0, 2));
        EXPECT_FALSE(runtime.runtime().hasAppliedInterval());
        EXPECT_NO_THROW(
            (void)runtime.applyAtDecisionTime(1, 0.0, 1.0));
        EXPECT_EQ(runtime.runtime().lastAppliedIndex(), 0u);
    }

} // namespace RTSim
