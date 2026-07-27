#include <gtest/gtest.h>

#include <rtsim/harvesting/harvest_types.hpp>
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/system.hpp>

#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>
#include <variant>
#include <vector>

extern "C" {
#include <Python.h>
}

namespace {
    thread_local bool fail_next_harvest_test_allocation = false;
}

void *operator new(std::size_t size) {
    if (fail_next_harvest_test_allocation) {
        throw std::bad_alloc();
    }
    if (void *memory = std::malloc(size)) {
        return memory;
    }
    throw std::bad_alloc();
}

void operator delete(void *memory) noexcept {
    std::free(memory);
}

void operator delete(void *memory, std::size_t) noexcept {
    std::free(memory);
}

#ifndef PARTSIM_SOURCE_DIR
#error "PARTSIM_SOURCE_DIR must be defined for harvesting config tests"
#endif

namespace RTSim {
    namespace {
        class TemporaryHarvestYaml {
        public:
            explicit TemporaryHarvestYaml(const std::string &contents) {
                const std::string pattern =
                    (std::filesystem::temp_directory_path() /
                     "partsim_harvest_config_XXXXXX")
                        .string();
                std::vector<char> mutable_pattern(
                    pattern.begin(), pattern.end());
                mutable_pattern.push_back('\0');
                char *created = ::mkdtemp(mutable_pattern.data());
                if (!created) {
                    throw std::system_error(
                        errno,
                        std::generic_category(),
                        "cannot create temporary harvesting YAML directory");
                }
                _directory = created;
                _path = _directory / "system.yml";
                std::ofstream output(_path);
                if (!output) {
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error(
                        "cannot create temporary harvesting YAML");
                }
                output << contents;
                if (!output) {
                    output.close();
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error(
                        "cannot write temporary harvesting YAML");
                }
            }

            TemporaryHarvestYaml(const TemporaryHarvestYaml &) = delete;
            TemporaryHarvestYaml &operator=(
                const TemporaryHarvestYaml &) = delete;

            ~TemporaryHarvestYaml() {
                std::error_code error;
                std::filesystem::remove_all(_directory, error);
            }

            std::string path() const {
                return _path.string();
            }

        private:
            std::filesystem::path _directory;
            std::filesystem::path _path;
        };

        class ScopedHarvestConfigCallback {
        public:
            ScopedHarvestConfigCallback() {
                ConfigManager::setConfigCallback(pythonConfigCallback);
            }

            ~ScopedHarvestConfigCallback() {
                ConfigManager::setConfigCallback(nullptr);
            }

            ScopedHarvestConfigCallback(
                const ScopedHarvestConfigCallback &) = delete;
            ScopedHarvestConfigCallback &operator=(
                const ScopedHarvestConfigCallback &) = delete;
        };

        std::string readFile(const std::filesystem::path &path) {
            std::ifstream input(path);
            if (!input) {
                throw std::runtime_error(
                    "cannot read harvesting test input: " + path.string());
            }
            return std::string(
                std::istreambuf_iterator<char>(input),
                std::istreambuf_iterator<char>());
        }

        void replaceExactlyOnce(
            std::string &text,
            const std::string &before,
            const std::string &after) {
            const std::size_t position = text.find(before);
            if (position == std::string::npos ||
                text.find(before, position + before.size()) !=
                    std::string::npos) {
                throw std::runtime_error(
                    "expected exactly one template token: " + before);
            }
            text.replace(position, before.size(), after);
        }

        std::string enabledFrozenB4Template(const std::string &alpha) {
            const auto path = std::filesystem::path(PARTSIM_SOURCE_DIR) /
                "v9_3_b4_priority_energy_system_template.yml";
            std::string contents = readFile(path);
            replaceExactlyOnce(
                contents, "  enabled: false", "  enabled: true");
            replaceExactlyOnce(
                contents, "  alpha_w: 0.0", "  alpha_w: " + alpha);
            return contents;
        }

        LegacySolarConfig legacyConfig(const ConfigManager &config) {
            return std::get<LegacySolarConfig>(
                config.getHarvestSourceConfig());
        }

        ScaledPiecewiseConfig piecewiseConfig(
            const ConfigManager &config) {
            return std::get<ScaledPiecewiseConfig>(
                config.getHarvestSourceConfig());
        }

        SampledTraceConfig traceConfig(const ConfigManager &config) {
            return std::get<SampledTraceConfig>(
                config.getHarvestSourceConfig());
        }

        void expectSafeHarvestConfiguration(const ConfigManager &config) {
            EXPECT_FALSE(config.isConfigLoaded());
            EXPECT_EQ(config.getConfigGeneration(), 0u);
            ASSERT_EQ(
                config.getHarvestSourceKind(),
                HarvestSourceKind::LegacySolar);
            const auto &safe = legacyConfig(config);
            EXPECT_DOUBLE_EQ(safe.base_harvesting_power_w, 0.054);
            EXPECT_EQ(safe.start_offset_ms, 0u);
            EXPECT_FALSE(safe.use_real_solar_data);
            EXPECT_EQ(
                safe.solar_data_file,
                "data/processed/shenyang_solar_minute.csv");
            EXPECT_DOUBLE_EQ(safe.pv_efficiency, 0.18);
            EXPECT_DOUBLE_EQ(safe.pv_area_m2, 1.0);
        }

        void expectRejected(
            ConfigManager &config,
            const std::string &contents) {
            TemporaryHarvestYaml file(contents);
            EXPECT_FALSE(config.loadSystemConfig(file.path()))
                << contents;
            expectSafeHarvestConfiguration(config);
        }

        std::string explicitLegacyYaml() {
            return
                "energy_management:\n"
                "  initial_energy: 4.0\n"
                "harvesting:\n"
                "  source: legacy_solar\n"
                "  legacy_solar:\n"
                "    base_harvesting_power_w: 0.125\n"
                "    start_offset_ms: 123456\n"
                "    use_real_solar_data: false\n"
                "    solar_data_file: legacy.csv\n"
                "    pv_efficiency: 0.21\n"
                "    pv_area_m2: 0.04\n";
        }

        std::string oneSegmentYaml() {
            return
                "harvesting:\n"
                "  source: scaled_piecewise\n"
                "  scaled_piecewise:\n"
                "    scale_w: 3.5\n"
                "    segments:\n"
                "      - start_ms: 0\n"
                "        end_ms: 42\n"
                "        multiplier: 1.0\n";
        }

        std::string electricalTraceYaml(
            const std::string &limits = "") {
            return
                "harvesting:\n"
                "  source: sampled_trace\n"
                "  sampled_trace:\n"
                "    file: /does/not/exist/measured_solar.csv\n"
                "    time_column: timestamp_ms\n"
                "    value_column: power_w\n"
                "    value_type: electrical_power\n" +
                limits;
        }

        class ScopedForcedHarvestAllocationFailure {
        public:
            ScopedForcedHarvestAllocationFailure() {
                fail_next_harvest_test_allocation = true;
            }

            ~ScopedForcedHarvestAllocationFailure() {
                fail_next_harvest_test_allocation = false;
            }

            ScopedForcedHarvestAllocationFailure(
                const ScopedForcedHarvestAllocationFailure &) = delete;
            ScopedForcedHarvestAllocationFailure &operator=(
                const ScopedForcedHarvestAllocationFailure &) = delete;
        };

        bool forceValuelessByException(HarvestSourceConfig &config) {
            bool allocation_failed = false;
            try {
                ScopedForcedHarvestAllocationFailure failure;
                config.emplace<LegacySolarConfig>();
            } catch (const std::bad_alloc &) {
                allocation_failed = true;
            }
            return allocation_failed && config.valueless_by_exception();
        }

        struct TestPyObjectDeleter {
            void operator()(PyObject *object) const noexcept {
                Py_XDECREF(object);
            }
        };
        using TestPyObjectPtr =
            std::unique_ptr<PyObject, TestPyObjectDeleter>;

        class PythonGilTestGuard {
        public:
            PythonGilTestGuard() : _state(PyGILState_Ensure()) {}
            ~PythonGilTestGuard() {
                PyGILState_Release(_state);
            }

            PythonGilTestGuard(const PythonGilTestGuard &) = delete;
            PythonGilTestGuard &operator=(
                const PythonGilTestGuard &) = delete;

        private:
            PyGILState_STATE _state;
        };

        [[noreturn]] void throwPythonHarvestTestError(
            const std::string &message) {
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            throw std::runtime_error(message);
        }

        class ScopedLateHarvestFailureLoader {
        public:
            ScopedLateHarvestFailureLoader() {
                PythonGilTestGuard gil;
                TestPyObjectPtr module(
                    PyImport_ImportModule("energy_manager"));
                TestPyObjectPtr original(
                    module
                        ? PyObject_GetAttrString(
                              module.get(), "load_config_for_cpp")
                        : nullptr);
                TestPyObjectPtr globals(PyDict_New());
                if (!module || !original || !globals ||
                    PyDict_SetItemString(
                        globals.get(),
                        "__builtins__",
                        PyEval_GetBuiltins()) != 0 ||
                    PyDict_SetItemString(
                        globals.get(),
                        "_real_loader",
                        original.get()) != 0) {
                    throwPythonHarvestTestError(
                        "cannot prepare late harvesting failure loader");
                }
                TestPyObjectPtr replacement(PyRun_String(
                    "lambda path: (lambda snapshot: "
                    "(snapshot['harvest_source'].__setitem__("
                    "'scale_w', 'late-type-failure'), snapshot)[1])"
                    "(_real_loader(path))",
                    Py_eval_input,
                    globals.get(),
                    globals.get()));
                if (!replacement ||
                    PyObject_SetAttrString(
                        module.get(),
                        "load_config_for_cpp",
                        replacement.get()) != 0) {
                    throwPythonHarvestTestError(
                        "cannot install late harvesting failure loader");
                }
                _module.reset(module.release());
                _original.reset(original.release());
                _active = true;
            }

            ~ScopedLateHarvestFailureLoader() {
                (void)restore();
            }

            ScopedLateHarvestFailureLoader(
                const ScopedLateHarvestFailureLoader &) = delete;
            ScopedLateHarvestFailureLoader &operator=(
                const ScopedLateHarvestFailureLoader &) = delete;

            bool restore() noexcept {
                if (!_active) {
                    return true;
                }
                bool restored = false;
                if (Py_IsInitialized()) {
                    PythonGilTestGuard gil;
                    restored = PyObject_SetAttrString(
                        _module.get(),
                        "load_config_for_cpp",
                        _original.get()) == 0;
                    if (PyErr_Occurred()) {
                        PyErr_Clear();
                    }
                    _original.reset();
                    _module.reset();
                } else {
                    (void)_original.release();
                    (void)_module.release();
                }
                _active = false;
                return restored;
            }

        private:
            TestPyObjectPtr _module;
            TestPyObjectPtr _original;
            bool _active = false;
        };

        bool pythonHasNoPendingError() {
            PythonGilTestGuard gil;
            const bool no_error = PyErr_Occurred() == nullptr;
            if (!no_error) {
                PyErr_Clear();
            }
            return no_error;
        }

        ConfigManager::ConfigurationState sentinelConfigurationState() {
            ConfigManager::ConfigurationState state;
            state.config_generation = 9001u;
            state.num_cores = 17;
            state.scheduler_type = "sentinel-scheduler";
            state.base_frequency = 1234.5;
            state.unit_time = 37;
            state.initial_energy = 11.25;
            state.max_energy = 91.75;
            state.base_harvest_rate = 0.333;
            state.start_time_offset = 7654321;
            state.enable_energy_recovery = false;
            state.periodic_collection_interval = 41;
            state.priority_energy_profile = {
                true,
                "sentinel-profile",
                8.5,
                43210u,
                7u,
            };
            state.harvest_source_config = LegacySolarConfig{
                6.25,
                8765u,
                true,
                "sentinel-legacy.csv",
                0.27,
                0.45,
            };
            state.base_power = 2.75;
            state.power_coefficients = {{"sentinel-workload", 4.5}};
            state.frequency_power_ratios = {{1234, 0.625}};
            return state;
        }

        void expectHarvestSourceEqual(
            const HarvestSourceConfig &actual,
            const HarvestSourceConfig &expected) {
            ASSERT_EQ(actual.index(), expected.index());
            if (const auto *legacy =
                    std::get_if<LegacySolarConfig>(&expected)) {
                const auto &value = std::get<LegacySolarConfig>(actual);
                EXPECT_DOUBLE_EQ(
                    value.base_harvesting_power_w,
                    legacy->base_harvesting_power_w);
                EXPECT_EQ(value.start_offset_ms, legacy->start_offset_ms);
                EXPECT_EQ(
                    value.use_real_solar_data,
                    legacy->use_real_solar_data);
                EXPECT_EQ(value.solar_data_file, legacy->solar_data_file);
                EXPECT_DOUBLE_EQ(value.pv_efficiency, legacy->pv_efficiency);
                EXPECT_DOUBLE_EQ(value.pv_area_m2, legacy->pv_area_m2);
                return;
            }
            if (const auto *piecewise =
                    std::get_if<ScaledPiecewiseConfig>(&expected)) {
                const auto &value =
                    std::get<ScaledPiecewiseConfig>(actual);
                EXPECT_DOUBLE_EQ(value.scale_w, piecewise->scale_w);
                ASSERT_EQ(
                    value.segments.size(), piecewise->segments.size());
                for (std::size_t index = 0;
                     index < value.segments.size();
                     ++index) {
                    EXPECT_EQ(
                        value.segments[index].start_time_ms,
                        piecewise->segments[index].start_time_ms);
                    EXPECT_EQ(
                        value.segments[index].end_time_ms,
                        piecewise->segments[index].end_time_ms);
                    EXPECT_DOUBLE_EQ(
                        value.segments[index].multiplier,
                        piecewise->segments[index].multiplier);
                }
                return;
            }
            const auto &value = std::get<SampledTraceConfig>(actual);
            const auto &trace = std::get<SampledTraceConfig>(expected);
            EXPECT_EQ(value.file, trace.file);
            EXPECT_EQ(value.time_column, trace.time_column);
            EXPECT_EQ(value.value_column, trace.value_column);
            EXPECT_EQ(value.value_type, trace.value_type);
            EXPECT_EQ(value.interpolation, trace.interpolation);
            EXPECT_EQ(value.after_trace, trace.after_trace);
            EXPECT_DOUBLE_EQ(value.panel_area_m2, trace.panel_area_m2);
            EXPECT_DOUBLE_EQ(
                value.conversion_efficiency,
                trace.conversion_efficiency);
            EXPECT_EQ(
                value.max_file_size_bytes,
                trace.max_file_size_bytes);
            EXPECT_EQ(value.max_rows, trace.max_rows);
        }

        void expectConfigurationStateEqual(
            const ConfigManager::ConfigurationState &actual,
            const ConfigManager::ConfigurationState &expected) {
            EXPECT_EQ(actual.config_generation, expected.config_generation);
            EXPECT_EQ(actual.num_cores, expected.num_cores);
            EXPECT_EQ(actual.scheduler_type, expected.scheduler_type);
            EXPECT_DOUBLE_EQ(actual.base_frequency, expected.base_frequency);
            EXPECT_EQ(actual.unit_time, expected.unit_time);
            EXPECT_DOUBLE_EQ(actual.initial_energy, expected.initial_energy);
            EXPECT_DOUBLE_EQ(actual.max_energy, expected.max_energy);
            EXPECT_DOUBLE_EQ(
                actual.base_harvest_rate,
                expected.base_harvest_rate);
            EXPECT_EQ(actual.start_time_offset, expected.start_time_offset);
            EXPECT_EQ(
                actual.enable_energy_recovery,
                expected.enable_energy_recovery);
            EXPECT_EQ(
                actual.periodic_collection_interval,
                expected.periodic_collection_interval);
            EXPECT_EQ(
                actual.priority_energy_profile.enabled,
                expected.priority_energy_profile.enabled);
            EXPECT_EQ(
                actual.priority_energy_profile.profile_id,
                expected.priority_energy_profile.profile_id);
            EXPECT_DOUBLE_EQ(
                actual.priority_energy_profile.alpha_w,
                expected.priority_energy_profile.alpha_w);
            EXPECT_EQ(
                actual.priority_energy_profile.horizon_ms,
                expected.priority_energy_profile.horizon_ms);
            EXPECT_EQ(
                actual.priority_energy_profile.tick_ms,
                expected.priority_energy_profile.tick_ms);
            expectHarvestSourceEqual(
                actual.harvest_source_config,
                expected.harvest_source_config);
            EXPECT_DOUBLE_EQ(actual.base_power, expected.base_power);
            EXPECT_EQ(
                actual.power_coefficients,
                expected.power_coefficients);
            EXPECT_EQ(
                actual.frequency_power_ratios,
                expected.frequency_power_ratios);
        }

        class ScopedSingletonHarvestCleanup {
        public:
            ScopedSingletonHarvestCleanup() = default;

            ~ScopedSingletonHarvestCleanup() {
                (void)cleanup();
            }

            bool cleanup() {
                if (!_active) {
                    return true;
                }
                EnergyBridge::getInstance().shutdown();
                const bool rejected =
                    !ConfigManager::getInstance().loadSystemConfig("");
                _active = false;
                return rejected;
            }

            ScopedSingletonHarvestCleanup(
                const ScopedSingletonHarvestCleanup &) = delete;
            ScopedSingletonHarvestCleanup &operator=(
                const ScopedSingletonHarvestCleanup &) = delete;

        private:
            bool _active = true;
        };
    } // namespace

    TEST(HarvestConfig, PublicTypesFreezeUnitsAndIntervalSemantics) {
        const HarvestInterval interval{7u, 100u, 101u};
        EXPECT_EQ(interval.index, 7u);
        EXPECT_EQ(interval.start_time_ms, 100u);
        EXPECT_EQ(interval.end_time_ms, 101u);
        EXPECT_LT(interval.start_time_ms, interval.end_time_ms);

        const HarvestResult result{1.0, 0.75, 0.25, 2.0, 2.75};
        EXPECT_DOUBLE_EQ(result.offered_j, 1.0);
        EXPECT_DOUBLE_EQ(result.actual_j, 0.75);
        EXPECT_DOUBLE_EQ(result.clipped_j, 0.25);
        EXPECT_DOUBLE_EQ(result.battery_before_j, 2.0);
        EXPECT_DOUBLE_EQ(result.battery_after_j, 2.75);

    }

    TEST(HarvestConfig, SourceKindMapsThreeAlternatives) {
        HarvestSourceConfig source = LegacySolarConfig{};
        EXPECT_EQ(sourceKind(source), HarvestSourceKind::LegacySolar);
        source = ScaledPiecewiseConfig{};
        EXPECT_EQ(sourceKind(source), HarvestSourceKind::ScaledPiecewise);
        source = SampledTraceConfig{};
        EXPECT_EQ(sourceKind(source), HarvestSourceKind::SampledTrace);
    }

    TEST(HarvestConfig, SourceKindValuelessFailsExplicitly) {
        HarvestSourceConfig source = SampledTraceConfig{};
        ASSERT_TRUE(forceValuelessByException(source));
        ASSERT_TRUE(source.valueless_by_exception());
        EXPECT_THROW(sourceKind(source), std::bad_variant_access);
    }

    TEST(HarvestConfig, PriorityDisabledMapsAccurateLegacyFields) {
        ScopedHarvestConfigCallback callback;
        TemporaryHarvestYaml file(
            "priority_energy:\n"
            "  enabled: false\n"
            "  profile_id: inert\n"
            "  alpha_w: -4.0\n"
            "  horizon_ms: 17\n"
            "  tick_ms: 9\n"
            "energy_management:\n"
            "  base_harvesting_rate: 0.054\n"
            "  day_of_year: 2\n"
            "  time_of_day_ms: 60001\n"
            "  use_real_solar_data: false\n"
            "  solar_data_file: legacy-measured.csv\n"
            "  pv_efficiency: 0.23\n"
            "  pv_area_m2: 0.05\n"
            "  enable_energy_recovery: false\n"
            "  max_recovery_wait_time_ms: 99\n");
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::LegacySolar);
        const auto &legacy = legacyConfig(config);
        EXPECT_DOUBLE_EQ(legacy.base_harvesting_power_w, 0.054);
        EXPECT_EQ(legacy.start_offset_ms, 86460000u);
        EXPECT_FALSE(legacy.use_real_solar_data);
        EXPECT_EQ(legacy.solar_data_file, "legacy-measured.csv");
        EXPECT_DOUBLE_EQ(legacy.pv_efficiency, 0.23);
        EXPECT_DOUBLE_EQ(legacy.pv_area_m2, 0.05);
        EXPECT_DOUBLE_EQ(config.getBaseHarvestRate(), 0.054);
        EXPECT_FALSE(config.isEnergyRecoveryEnabled());
    }

    TEST(HarvestConfig, FrozenB4PriorityMapsExactThreeStagePreset) {
        ScopedHarvestConfigCallback callback;
        TemporaryHarvestYaml file(enabledFrozenB4Template("2.5"));
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::ScaledPiecewise);
        const auto &piecewise = piecewiseConfig(config);
        EXPECT_DOUBLE_EQ(piecewise.scale_w, 2.5);
        ASSERT_EQ(piecewise.segments.size(), 3u);
        EXPECT_EQ(piecewise.segments[0].start_time_ms, 0u);
        EXPECT_EQ(piecewise.segments[0].end_time_ms, 5000u);
        EXPECT_DOUBLE_EQ(piecewise.segments[0].multiplier, 1.0);
        EXPECT_EQ(piecewise.segments[1].start_time_ms, 5000u);
        EXPECT_EQ(piecewise.segments[1].end_time_ms, 15000u);
        EXPECT_DOUBLE_EQ(piecewise.segments[1].multiplier, 0.2);
        EXPECT_EQ(piecewise.segments[2].start_time_ms, 15000u);
        EXPECT_EQ(piecewise.segments[2].end_time_ms, 30000u);
        EXPECT_DOUBLE_EQ(piecewise.segments[2].multiplier, 1.0);
        EXPECT_DOUBLE_EQ(config.getBaseHarvestRate(), 0.054);
    }

    TEST(HarvestConfig, ExplicitLegacyMapsWithoutRecoveryPolicy) {
        ScopedHarvestConfigCallback callback;
        TemporaryHarvestYaml file(explicitLegacyYaml());
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::LegacySolar);
        const auto &legacy = legacyConfig(config);
        EXPECT_DOUBLE_EQ(legacy.base_harvesting_power_w, 0.125);
        EXPECT_EQ(legacy.start_offset_ms, 123456u);
        EXPECT_FALSE(legacy.use_real_solar_data);
        EXPECT_EQ(legacy.solar_data_file, "legacy.csv");
        EXPECT_DOUBLE_EQ(legacy.pv_efficiency, 0.21);
        EXPECT_DOUBLE_EQ(legacy.pv_area_m2, 0.04);
    }

    TEST(HarvestConfig, ExplicitPiecewiseSupportsOneNAndGaps) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        TemporaryHarvestYaml one(oneSegmentYaml());
        ASSERT_TRUE(config.loadSystemConfig(one.path()));
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::ScaledPiecewise);
        ASSERT_EQ(piecewiseConfig(config).segments.size(), 1u);
        EXPECT_DOUBLE_EQ(piecewiseConfig(config).scale_w, 3.5);

        TemporaryHarvestYaml three(
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  scaled_piecewise:\n"
            "    scale_w: 4.0\n"
            "    segments:\n"
            "      - {start_ms: 0, end_ms: 5, multiplier: 1.0}\n"
            "      - {start_ms: 10, end_ms: 20, multiplier: 0.5}\n"
            "      - {start_ms: 20, end_ms: 30, multiplier: 0.5}\n");
        ASSERT_TRUE(config.loadSystemConfig(three.path()));
        const auto &piecewise = piecewiseConfig(config);
        ASSERT_EQ(piecewise.segments.size(), 3u);
        EXPECT_EQ(piecewise.segments[0].end_time_ms, 5u);
        EXPECT_EQ(piecewise.segments[1].start_time_ms, 10u);
        EXPECT_EQ(piecewise.segments[1].end_time_ms, 20u);
        EXPECT_EQ(piecewise.segments[2].start_time_ms, 20u);
        EXPECT_DOUBLE_EQ(piecewise.segments[1].multiplier, 0.5);
        EXPECT_DOUBLE_EQ(piecewise.segments[2].multiplier, 0.5);
    }

    TEST(HarvestConfig, NegativeZeroIsCanonicalized) {
        ScopedHarvestConfigCallback callback;
        TemporaryHarvestYaml file(
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  scaled_piecewise:\n"
            "    scale_w: -0.0\n"
            "    segments:\n"
            "      - {start_ms: 0, end_ms: 1, multiplier: -0.0}\n"
            "      - {start_ms: 1, end_ms: 2, multiplier: 0.0}\n");
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        const auto &piecewise = piecewiseConfig(config);
        EXPECT_DOUBLE_EQ(piecewise.scale_w, 0.0);
        EXPECT_FALSE(std::signbit(piecewise.scale_w));
        ASSERT_EQ(piecewise.segments.size(), 2u);
        EXPECT_DOUBLE_EQ(piecewise.segments[0].multiplier, 0.0);
        EXPECT_FALSE(std::signbit(piecewise.segments[0].multiplier));
        EXPECT_DOUBLE_EQ(piecewise.segments[1].multiplier, 0.0);
        EXPECT_FALSE(std::signbit(piecewise.segments[1].multiplier));
    }

    TEST(HarvestConfig, ExplicitTraceMapsElectricalAndIrradianceContracts) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        TemporaryHarvestYaml electrical(electricalTraceYaml());
        ASSERT_TRUE(config.loadSystemConfig(electrical.path()));
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::SampledTrace);
        const auto &electrical_config = traceConfig(config);
        EXPECT_EQ(
            electrical_config.file,
            "/does/not/exist/measured_solar.csv");
        EXPECT_EQ(electrical_config.time_column, "timestamp_ms");
        EXPECT_EQ(electrical_config.value_column, "power_w");
        EXPECT_EQ(
            electrical_config.value_type,
            TraceValueType::ElectricalPower);
        EXPECT_EQ(
            electrical_config.interpolation,
            TraceInterpolation::ZeroOrderHold);
        EXPECT_EQ(electrical_config.after_trace, TraceAfterEnd::Zero);
        EXPECT_DOUBLE_EQ(electrical_config.panel_area_m2, 0.0);
        EXPECT_DOUBLE_EQ(electrical_config.conversion_efficiency, 0.0);
        EXPECT_EQ(electrical_config.max_file_size_bytes, 268435456u);
        EXPECT_EQ(electrical_config.max_rows, 5000000u);

        TemporaryHarvestYaml irradiance(
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: /also/missing/irradiance.csv\n"
            "    time_column: timestamp_ms\n"
            "    value_column: irradiance_w_m2\n"
            "    value_type: irradiance\n"
            "    interpolation: zero_order_hold\n"
            "    after_trace: zero\n"
            "    panel_area_m2: 0.04\n"
            "    conversion_efficiency: 0.21\n"
            "    max_file_size_bytes: 1024\n"
            "    max_rows: 17\n");
        ASSERT_TRUE(config.loadSystemConfig(irradiance.path()));
        const auto &irradiance_config = traceConfig(config);
        EXPECT_EQ(
            irradiance_config.value_type,
            TraceValueType::Irradiance);
        EXPECT_DOUBLE_EQ(irradiance_config.panel_area_m2, 0.04);
        EXPECT_DOUBLE_EQ(irradiance_config.conversion_efficiency, 0.21);
        EXPECT_EQ(irradiance_config.max_file_size_bytes, 1024u);
        EXPECT_EQ(irradiance_config.max_rows, 17u);
    }

    TEST(HarvestConfig, RejectsConflictsUnknownsDuplicatesAndMerges) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        const std::vector<std::string> invalid = {
            explicitLegacyYaml() +
                "priority_energy:\n  enabled: false\n",
            "harvesting:\n"
            "  source: wind\n"
            "  sampled_trace: {}\n",
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  mystery: 1\n"
            "  scaled_piecewise:\n"
            "    scale_w: 1\n"
            "    segments: [{start_ms: 0, end_ms: 1, multiplier: 1}]\n",
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  scaled_piecewise:\n"
            "    scale_w: 1\n"
            "    horizon_ms: 1\n"
            "    segments: [{start_ms: 0, end_ms: 1, multiplier: 1}]\n",
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  scaled_piecewise:\n"
            "    scale_w: 1\n"
            "    segments: [{start_ms: 0, end_ms: 1, multiplier: 1}]\n"
            "  sampled_trace: {}\n",
            "priority_energy:\n"
            "  enabled: true\n"
            "  profile_id: wrong\n"
            "  alpha_w: 1\n"
            "  horizon_ms: 30000\n"
            "  tick_ms: 1\n",
            "energy_management:\n"
            "  base_harvesting_rate: 0.1\n"
            "harvesting:\n"
            "  source: legacy_solar\n"
            "  legacy_solar:\n"
            "    base_harvesting_power_w: 0.125\n"
            "    start_offset_ms: 0\n"
            "    use_real_solar_data: false\n"
            "    solar_data_file: legacy.csv\n"
            "    pv_efficiency: 0.2\n"
            "    pv_area_m2: 1\n",
            "harvesting:\n"
            "  source: legacy_solar\n"
            "  source: legacy_solar\n"
            "  legacy_solar: {}\n",
            "defaults: &defaults\n"
            "  base_harvesting_power_w: 1\n"
            "harvesting:\n"
            "  source: legacy_solar\n"
            "  legacy_solar:\n"
            "    <<: *defaults\n"
            "    start_offset_ms: 0\n"
            "    use_real_solar_data: false\n"
            "    solar_data_file: legacy.csv\n"
            "    pv_efficiency: 0.2\n"
            "    pv_area_m2: 1\n",
            "harvesting:\n"
            "  source: legacy_solar\n"
            "  legacy_solar:\n"
            "    base_harvesting_power_w: 1\n"
            "    start_offset_ms: 0\n"
            "    use_real_solar_data: yes\n"
            "    solar_data_file: legacy.csv\n"
            "    pv_efficiency: 0.2\n"
            "    pv_area_m2: 1\n",
        };
        for (const std::string &contents : invalid) {
            SCOPED_TRACE(contents);
            expectRejected(config, contents);
        }
    }

    TEST(HarvestConfig, RejectsInvalidPiecewiseBoundariesAndNumbers) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        const auto yaml = [](const std::string &scale,
                             const std::string &segments) {
            return
                "harvesting:\n"
                "  source: scaled_piecewise\n"
                "  scaled_piecewise:\n"
                "    scale_w: " + scale + "\n"
                "    segments:\n" + segments;
        };
        const std::vector<std::string> invalid = {
            yaml("1", "      []\n"),
            yaml(
                "1",
                "      - {start_ms: 0, end_ms: 10, multiplier: 1}\n"
                "      - {start_ms: 9, end_ms: 20, multiplier: 1}\n"),
            yaml(
                "1",
                "      - {start_ms: 10, end_ms: 20, multiplier: 1}\n"
                "      - {start_ms: 0, end_ms: 5, multiplier: 1}\n"),
            yaml(
                "1",
                "      - {start_ms: 7, end_ms: 7, multiplier: 1}\n"),
            yaml(
                "-1",
                "      - {start_ms: 0, end_ms: 1, multiplier: 1}\n"),
            yaml(
                "1",
                "      - {start_ms: 0, end_ms: 1, multiplier: -1}\n"),
            yaml(
                ".nan",
                "      - {start_ms: 0, end_ms: 1, multiplier: 1}\n"),
            yaml(
                ".inf",
                "      - {start_ms: 0, end_ms: 1, multiplier: 1}\n"),
            yaml(
                "1",
                "      - {start_ms: 0, end_ms: 1, multiplier: .inf}\n"),
            yaml(
                "1",
                "      - {start_ms: 18446744073709551616, "
                "end_ms: 18446744073709551617, multiplier: 1}\n"),
            yaml(
                "1",
                "      - {start_ms: true, end_ms: 2, multiplier: 1}\n"),
        };
        for (const std::string &contents : invalid) {
            SCOPED_TRACE(contents);
            expectRejected(config, contents);
        }
    }

    TEST(HarvestConfig, RejectsInvalidTraceContractsAndLimits) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        const std::vector<std::string> invalid = {
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: \"\"\n"
            "    time_column: t\n"
            "    value_column: v\n"
            "    value_type: electrical_power\n",
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: trace.csv\n"
            "    time_column: \"\"\n"
            "    value_column: v\n"
            "    value_type: electrical_power\n",
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: trace.csv\n"
            "    time_column: t\n"
            "    value_column: \"\"\n"
            "    value_type: electrical_power\n",
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: trace.csv\n"
            "    time_column: t\n"
            "    value_column: v\n"
            "    value_type: voltage\n",
            electricalTraceYaml("    interpolation: linear\n"),
            electricalTraceYaml("    after_trace: loop\n"),
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: trace.csv\n"
            "    time_column: t\n"
            "    value_column: irradiance\n"
            "    value_type: irradiance\n",
            "harvesting:\n"
            "  source: sampled_trace\n"
            "  sampled_trace:\n"
            "    file: trace.csv\n"
            "    time_column: t\n"
            "    value_column: irradiance\n"
            "    value_type: irradiance\n"
            "    panel_area_m2: 1\n"
            "    conversion_efficiency: 1.01\n",
            electricalTraceYaml("    panel_area_m2: 1\n"),
            electricalTraceYaml("    max_file_size_bytes: 0\n"),
            electricalTraceYaml("    max_rows: 0\n"),
            electricalTraceYaml("    max_rows: -1\n"),
            electricalTraceYaml(
                "    max_file_size_bytes: 18446744073709551616\n"),
        };
        for (const std::string &contents : invalid) {
            SCOPED_TRACE(contents);
            expectRejected(config, contents);
        }
    }

    TEST(HarvestConfig, CallbackFailureLeavesPendingUnchanged) {
        ScopedHarvestConfigCallback callback;
        TemporaryHarvestYaml valid(oneSegmentYaml());
        ConfigManager committed;
        ASSERT_TRUE(committed.loadSystemConfig(valid.path()));
        const std::uint64_t committed_generation =
            committed.getConfigGeneration();
        const HarvestSourceConfig committed_source =
            committed.getHarvestSourceConfig();

        ScopedLateHarvestFailureLoader late_failure;
        std::vector<ConfigManager::ConfigurationState> sentinels;
        sentinels.push_back(sentinelConfigurationState());

        auto piecewise_sentinel = sentinelConfigurationState();
        piecewise_sentinel.harvest_source_config = ScaledPiecewiseConfig{
            7.25,
            {
                {10u, 20u, 0.75},
                {30u, 50u, 1.25},
            },
        };
        sentinels.push_back(piecewise_sentinel);

        auto trace_sentinel = sentinelConfigurationState();
        trace_sentinel.harvest_source_config = SampledTraceConfig{
            "sentinel-trace.csv",
            "sentinel-time",
            "sentinel-value",
            TraceValueType::Irradiance,
            TraceInterpolation::ZeroOrderHold,
            TraceAfterEnd::Zero,
            0.125,
            0.375,
            987654u,
            321u,
        };
        sentinels.push_back(trace_sentinel);

        for (auto &pending : sentinels) {
            const ConfigManager::ConfigurationState expected = pending;
            EXPECT_THROW(
                pythonConfigCallback(valid.path(), pending),
                PriorityEnergyConfigError);
            expectConfigurationStateEqual(pending, expected);
            EXPECT_TRUE(pythonHasNoPendingError());
            EXPECT_TRUE(committed.isConfigLoaded());
            EXPECT_EQ(
                committed.getConfigGeneration(),
                committed_generation);
            expectHarvestSourceEqual(
                committed.getHarvestSourceConfig(), committed_source);
        }
        EXPECT_TRUE(late_failure.restore());
    }

    TEST(HarvestConfig, InvalidReloadResetsSourceThenNewGenerationRecovers) {
        ScopedHarvestConfigCallback callback;
        ConfigManager config;
        TemporaryHarvestYaml valid_a(oneSegmentYaml());
        ASSERT_TRUE(config.loadSystemConfig(valid_a.path()));
        const std::uint64_t generation_a = config.getConfigGeneration();
        ASSERT_GT(generation_a, 0u);
        ASSERT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::ScaledPiecewise);

        TemporaryHarvestYaml invalid_b(
            "harvesting:\n"
            "  source: scaled_piecewise\n"
            "  scaled_piecewise:\n"
            "    scale_w: 9\n"
            "    segments:\n"
            "      - {start_ms: 0, end_ms: 10, multiplier: 1}\n"
            "      - {start_ms: 5, end_ms: 20, multiplier: 1}\n");
        EXPECT_FALSE(config.loadSystemConfig(invalid_b.path()));
        expectSafeHarvestConfiguration(config);

        TemporaryHarvestYaml valid_c(electricalTraceYaml(
            "    max_file_size_bytes: 4096\n"
            "    max_rows: 100\n"));
        ASSERT_TRUE(config.loadSystemConfig(valid_c.path()));
        EXPECT_TRUE(config.isConfigLoaded());
        EXPECT_GT(config.getConfigGeneration(), generation_a);
        EXPECT_EQ(
            config.getHarvestSourceKind(),
            HarvestSourceKind::SampledTrace);
        EXPECT_EQ(traceConfig(config).max_rows, 100u);
    }

    TEST(HarvestConfig,
         LegacyAndEnabledB4SystemsStillInitializeAndCleanSingletonState) {
        ScopedHarvestConfigCallback callback;
        ScopedSingletonHarvestCleanup singleton_cleanup;
        const auto template_path = std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "v9_3_b4_priority_energy_system_template.yml";
        {
            System legacy_system(template_path.string());
            ASSERT_EQ(legacy_system.schedulers.size(), 1u);
            EXPECT_EQ(
                ConfigManager::getInstance().getHarvestSourceKind(),
                HarvestSourceKind::LegacySolar);
        }

        TemporaryHarvestYaml enabled(enabledFrozenB4Template("1.25"));
        {
            System b4_system(enabled.path());
            ASSERT_EQ(b4_system.schedulers.size(), 1u);
            EXPECT_EQ(
                ConfigManager::getInstance().getHarvestSourceKind(),
                HarvestSourceKind::ScaledPiecewise);
            EXPECT_DOUBLE_EQ(
                piecewiseConfig(ConfigManager::getInstance()).scale_w,
                1.25);
        }

        ASSERT_TRUE(singleton_cleanup.cleanup());
        expectSafeHarvestConfiguration(ConfigManager::getInstance());
    }

} // namespace RTSim
