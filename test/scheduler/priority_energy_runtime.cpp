#include <gtest/gtest.h>

#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/scheduler/priority_energy_runtime.hpp>
#include <rtsim/system.hpp>
#include <rtsim/system_descriptor.hpp>

#include <algorithm>
#include <array>
#include <cerrno>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

extern "C" {
#include <Python.h>
}

#ifndef PARTSIM_SOURCE_DIR
#error "PARTSIM_SOURCE_DIR must be defined for priority-energy integration tests"
#endif

namespace RTSim {
    namespace {
        class TemporaryYaml {
        public:
            explicit TemporaryYaml(const std::string &contents) {
                const std::string pattern =
                    (std::filesystem::temp_directory_path() /
                     "partsim_priority_energy_runtime_XXXXXX")
                        .string();
                std::vector<char> mutable_pattern(
                    pattern.begin(),
                    pattern.end());
                mutable_pattern.push_back('\0');
                char *created = ::mkdtemp(mutable_pattern.data());
                if (!created) {
                    throw std::system_error(
                        errno,
                        std::generic_category(),
                        "cannot create temporary YAML directory");
                }
                _directory = created;
                _path = _directory / "system.yml";
                std::ofstream output(_path);
                if (!output) {
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error("cannot create temporary YAML");
                }
                output << contents;
                if (!output) {
                    output.close();
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error("cannot write temporary YAML");
                }
            }

            TemporaryYaml(const TemporaryYaml &) = delete;
            TemporaryYaml &operator=(const TemporaryYaml &) = delete;

            ~TemporaryYaml() {
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

        class ScopedStrictConfigCallback {
        public:
            ScopedStrictConfigCallback() {
                ConfigManager::setConfigCallback(pythonConfigCallback);
            }

            ~ScopedStrictConfigCallback() {
                ConfigManager::setConfigCallback(nullptr);
            }

            ScopedStrictConfigCallback(
                const ScopedStrictConfigCallback &) = delete;
            ScopedStrictConfigCallback &operator=(
                const ScopedStrictConfigCallback &) = delete;
        };

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

        struct PythonManagerSnapshot {
            std::uintptr_t identity = 0;
            std::uint64_t generation = 0;
            double base_frequency = 0.0;
            std::string profile_id;
        };

        [[noreturn]] void throwPythonTestError(const std::string &message) {
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            throw std::runtime_error(message);
        }

        TestPyObjectPtr importEnergyManagerForTest() {
            TestPyObjectPtr module(PyImport_ImportModule("energy_manager"));
            if (!module) {
                throwPythonTestError("cannot import energy_manager");
            }
            return module;
        }

        PythonManagerSnapshot pythonManagerSnapshot(
            std::uint64_t expected_generation) {
            PythonGilTestGuard gil;
            TestPyObjectPtr module = importEnergyManagerForTest();
                TestPyObjectPtr accessor(PyObject_GetAttrString(
                    module.get(), "get_energy_manager"));
                if (!accessor || !PyCallable_Check(accessor.get())) {
                    throwPythonTestError(
                        "get_energy_manager is unavailable");
                }
                TestPyObjectPtr generation(
                    PyLong_FromUnsignedLongLong(expected_generation));
                TestPyObjectPtr manager(PyObject_CallFunctionObjArgs(
                    accessor.get(), generation.get(), nullptr));
                if (!manager) {
                    throwPythonTestError(
                        "cannot obtain authoritative manager");
                }

                TestPyObjectPtr manager_generation(
                    PyObject_GetAttrString(
                        manager.get(), "config_generation"));
                TestPyObjectPtr config(
                    PyObject_GetAttrString(manager.get(), "config"));
                TestPyObjectPtr frequency(
                    config ? PyObject_GetAttrString(
                                 config.get(), "base_frequency")
                           : nullptr);
                TestPyObjectPtr profile(
                    config ? PyObject_GetAttrString(
                                 config.get(), "priority_energy")
                           : nullptr);
                if (!manager_generation || !frequency || !profile ||
                    !PyDict_Check(profile.get())) {
                    throwPythonTestError(
                        "manager configuration snapshot is incomplete");
                }
                PyObject *profile_id =
                    PyDict_GetItemString(profile.get(), "profile_id");
                if (!profile_id || !PyUnicode_Check(profile_id)) {
                    throwPythonTestError(
                        "manager profile_id is unavailable");
                }

                PythonManagerSnapshot snapshot;
                snapshot.identity = reinterpret_cast<std::uintptr_t>(
                    manager.get());
                snapshot.generation = PyLong_AsUnsignedLongLong(
                    manager_generation.get());
                snapshot.base_frequency =
                    PyFloat_AsDouble(frequency.get());
                const char *profile_text =
                    PyUnicode_AsUTF8(profile_id);
                if (PyErr_Occurred() || !profile_text) {
                    throwPythonTestError(
                        "manager snapshot conversion failed");
                }
                snapshot.profile_id = profile_text;
                return snapshot;
        }

        bool pythonManagerLookupFails(
            std::uint64_t expected_generation) {
            PythonGilTestGuard gil;
            TestPyObjectPtr module = importEnergyManagerForTest();
            TestPyObjectPtr accessor(PyObject_GetAttrString(
                module.get(), "get_energy_manager"));
            TestPyObjectPtr generation(
                PyLong_FromUnsignedLongLong(expected_generation));
            TestPyObjectPtr manager(
                accessor && generation
                    ? PyObject_CallFunctionObjArgs(
                          accessor.get(), generation.get(), nullptr)
                    : nullptr);
            const bool failed = manager == nullptr;
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            return failed;
        }

        bool pythonObjectHasAttribute(
            PyObject *object,
            const char *attribute) {
            const int result = PyObject_HasAttrString(object, attribute);
            if (result < 0) {
                throwPythonTestError(
                    std::string("cannot inspect Python attribute: ") +
                    attribute);
            }
            return result != 0;
        }

        bool pythonPublicReloadApisAreAbsent(
            std::uint64_t expected_generation) {
            PythonGilTestGuard gil;
            TestPyObjectPtr module = importEnergyManagerForTest();
            TestPyObjectPtr accessor(PyObject_GetAttrString(
                module.get(), "get_energy_manager"));
            TestPyObjectPtr generation(
                PyLong_FromUnsignedLongLong(expected_generation));
            TestPyObjectPtr manager(
                accessor && generation
                    ? PyObject_CallFunctionObjArgs(
                          accessor.get(), generation.get(), nullptr)
                    : nullptr);
            if (!manager) {
                throwPythonTestError(
                    "cannot inspect authoritative manager API");
            }
            return !pythonObjectHasAttribute(
                       module.get(), "load_system_config") &&
                   !pythonObjectHasAttribute(
                       manager.get(), "load_system_config");
        }

        bool pythonEmptyAuthoritativeLoadFails() {
            PythonGilTestGuard gil;
            TestPyObjectPtr module = importEnergyManagerForTest();
            TestPyObjectPtr loader(PyObject_GetAttrString(
                module.get(), "load_config_for_cpp"));
            TestPyObjectPtr empty(PyUnicode_FromString(""));
            TestPyObjectPtr result(
                loader && empty
                    ? PyObject_CallFunctionObjArgs(
                          loader.get(), empty.get(), nullptr)
                    : nullptr);
            const bool failed = result == nullptr;
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            return failed;
        }

        bool pythonHasNoPendingError() {
            PythonGilTestGuard gil;
            return PyErr_Occurred() == nullptr;
        }

        TestPyObjectPtr pythonManagerForTest(
            std::uint64_t expected_generation) {
            TestPyObjectPtr module = importEnergyManagerForTest();
            TestPyObjectPtr accessor(PyObject_GetAttrString(
                module.get(), "get_energy_manager"));
            TestPyObjectPtr generation(
                PyLong_FromUnsignedLongLong(expected_generation));
            TestPyObjectPtr manager(
                accessor && generation
                    ? PyObject_CallFunctionObjArgs(
                          accessor.get(), generation.get(), nullptr)
                    : nullptr);
            if (!manager) {
                throwPythonTestError(
                    "cannot obtain Python manager for batch test");
            }
            return manager;
        }

        bool pythonManagerBatchResult(
            std::uint64_t expected_generation,
            double duration_ms = 1.0) {
            PythonGilTestGuard gil;
            TestPyObjectPtr manager =
                pythonManagerForTest(expected_generation);
            TestPyObjectPtr method(PyObject_GetAttrString(
                manager.get(), "has_sufficient_energy_for_batch"));
            TestPyObjectPtr workloads(PyList_New(1));
            TestPyObjectPtr workload(PyUnicode_FromString("hash"));
            TestPyObjectPtr duration(PyFloat_FromDouble(duration_ms));
            if (!method || !PyCallable_Check(method.get()) || !workloads ||
                !workload || !duration) {
                throwPythonTestError(
                    "cannot prepare direct Python batch call");
            }
            PyList_SET_ITEM(workloads.get(), 0, workload.release());
            TestPyObjectPtr arguments(
                PyTuple_Pack(2, workloads.get(), duration.get()));
            TestPyObjectPtr result(
                arguments ? PyObject_CallObject(method.get(), arguments.get())
                          : nullptr);
            if (!result || !PyBool_Check(result.get())) {
                throwPythonTestError(
                    "direct Python batch call did not return bool");
            }
            return result.get() == Py_True;
        }

        struct BatchReferenceCounts {
            Py_ssize_t manager = 0;
            Py_ssize_t method = 0;
        };

        BatchReferenceCounts pythonBatchReferenceCounts(
            std::uint64_t expected_generation) {
            PythonGilTestGuard gil;
            TestPyObjectPtr manager =
                pythonManagerForTest(expected_generation);
            TestPyObjectPtr manager_type(PyObject_Type(manager.get()));
            TestPyObjectPtr method(
                manager_type
                    ? PyObject_GetAttrString(
                          manager_type.get(),
                          "has_sufficient_energy_for_batch")
                    : nullptr);
            if (!manager_type || !method) {
                throwPythonTestError(
                    "cannot inspect Python batch reference counts");
            }
            return {Py_REFCNT(manager.get()), Py_REFCNT(method.get())};
        }

        class ScopedManagerBatchOverride {
        public:
            explicit ScopedManagerBatchOverride(
                std::uint64_t expected_generation) {
                PythonGilTestGuard gil;
                TestPyObjectPtr manager =
                    pythonManagerForTest(expected_generation);
                TestPyObjectPtr dictionary(PyObject_GetAttrString(
                    manager.get(), "__dict__"));
                if (!dictionary || !PyDict_Check(dictionary.get())) {
                    throwPythonTestError(
                        "cannot inspect manager instance dictionary");
                }
                PyObject *existing = PyDict_GetItemString(
                    dictionary.get(),
                    "has_sufficient_energy_for_batch");
                TestPyObjectPtr original;
                if (existing != nullptr) {
                    Py_INCREF(existing);
                    original.reset(existing);
                }

                TestPyObjectPtr globals(PyDict_New());
                TestPyObjectPtr calls(PyList_New(0));
                if (!globals || !calls ||
                    PyDict_SetItemString(
                        globals.get(), "__builtins__", PyEval_GetBuiltins()) !=
                        0 ||
                    PyDict_SetItemString(
                        globals.get(), "_b4_batch_calls", calls.get()) != 0) {
                    throwPythonTestError(
                        "cannot prepare counted manager batch override");
                }
                TestPyObjectPtr replacement(PyRun_String(
                    "lambda workloads, duration: "
                    "(_b4_batch_calls.append(duration) or True)",
                    Py_eval_input,
                    globals.get(),
                    globals.get()));
                if (!replacement ||
                    PyObject_SetAttrString(
                        manager.get(),
                        "has_sufficient_energy_for_batch",
                        replacement.get()) != 0) {
                    throwPythonTestError(
                        "cannot install counted manager batch override");
                }

                _manager.reset(manager.release());
                _calls.reset(calls.release());
                _original.reset(original.release());
                _active = true;
            }

            ~ScopedManagerBatchOverride() {
                (void)restore();
            }

            ScopedManagerBatchOverride(
                const ScopedManagerBatchOverride &) = delete;
            ScopedManagerBatchOverride &operator=(
                const ScopedManagerBatchOverride &) = delete;

            Py_ssize_t callCount() const {
                PythonGilTestGuard gil;
                const Py_ssize_t count = PyList_Size(_calls.get());
                if (count < 0) {
                    throwPythonTestError(
                        "cannot inspect manager batch call count");
                }
                return count;
            }

            Py_ssize_t managerRefCount() const {
                PythonGilTestGuard gil;
                return Py_REFCNT(_manager.get());
            }

            bool restore() noexcept {
                if (!_active) {
                    return true;
                }
                bool restored = false;
                if (Py_IsInitialized()) {
                    PythonGilTestGuard gil;
                    const int status = _original
                        ? PyObject_SetAttrString(
                              _manager.get(),
                              "has_sufficient_energy_for_batch",
                              _original.get())
                        : PyObject_DelAttrString(
                              _manager.get(),
                              "has_sufficient_energy_for_batch");
                    restored = status == 0;
                    if (PyErr_Occurred()) {
                        PyErr_Clear();
                    }
                    _original.reset();
                    _calls.reset();
                    _manager.reset();
                } else {
                    (void)_original.release();
                    (void)_calls.release();
                    (void)_manager.release();
                }
                _active = false;
                return restored;
            }

        private:
            TestPyObjectPtr _manager;
            TestPyObjectPtr _calls;
            TestPyObjectPtr _original;
            bool _active = false;
        };

        enum class BatchClassBehavior {
            Missing,
            Raises,
            WrongType,
        };

        class ScopedBatchClassMethod {
        public:
            ScopedBatchClassMethod(
                std::uint64_t expected_generation,
                BatchClassBehavior behavior) {
                PythonGilTestGuard gil;
                TestPyObjectPtr manager =
                    pythonManagerForTest(expected_generation);
                TestPyObjectPtr manager_type(PyObject_Type(manager.get()));
                TestPyObjectPtr original(
                    manager_type
                        ? PyObject_GetAttrString(
                              manager_type.get(),
                              "has_sufficient_energy_for_batch")
                        : nullptr);
                if (!manager_type || !original) {
                    throwPythonTestError(
                        "cannot save Python batch class method");
                }

                int status = -1;
                if (behavior == BatchClassBehavior::Missing) {
                    status = PyObject_DelAttrString(
                        manager_type.get(),
                        "has_sufficient_energy_for_batch");
                } else {
                    TestPyObjectPtr globals(PyDict_New());
                    if (!globals ||
                        PyDict_SetItemString(
                            globals.get(),
                            "__builtins__",
                            PyEval_GetBuiltins()) != 0) {
                        throwPythonTestError(
                            "cannot prepare Python batch class override");
                    }
                    const char *source =
                        behavior == BatchClassBehavior::Raises
                            ? "lambda self, workloads, duration: "
                              "(_ for _ in ()).throw("
                              "RuntimeError('batch failure'))"
                            : "lambda self, workloads, duration: "
                              "'not-a-bool'";
                    TestPyObjectPtr replacement(PyRun_String(
                        source,
                        Py_eval_input,
                        globals.get(),
                        globals.get()));
                    status = replacement
                        ? PyObject_SetAttrString(
                              manager_type.get(),
                              "has_sufficient_energy_for_batch",
                              replacement.get())
                        : -1;
                }
                if (status != 0) {
                    throwPythonTestError(
                        "cannot install Python batch class override");
                }

                _manager_type.reset(manager_type.release());
                _original.reset(original.release());
                _active = true;
            }

            ~ScopedBatchClassMethod() {
                (void)restore();
            }

            ScopedBatchClassMethod(const ScopedBatchClassMethod &) = delete;
            ScopedBatchClassMethod &operator=(
                const ScopedBatchClassMethod &) = delete;

            bool restore() noexcept {
                if (!_active) {
                    return true;
                }
                bool restored = false;
                if (Py_IsInitialized()) {
                    PythonGilTestGuard gil;
                    restored = PyObject_SetAttrString(
                        _manager_type.get(),
                        "has_sufficient_energy_for_batch",
                        _original.get()) == 0;
                    if (PyErr_Occurred()) {
                        PyErr_Clear();
                    }
                    _original.reset();
                    _manager_type.reset();
                } else {
                    (void)_original.release();
                    (void)_manager_type.release();
                }
                _active = false;
                return restored;
            }

        private:
            TestPyObjectPtr _manager_type;
            TestPyObjectPtr _original;
            bool _active = false;
        };

        void invalidatePythonManagerForTest() {
            if (!Py_IsInitialized()) {
                return;
            }
            PythonGilTestGuard gil;
            TestPyObjectPtr module = importEnergyManagerForTest();
                TestPyObjectPtr invalidator(PyObject_GetAttrString(
                    module.get(), "_invalidate_config_for_cpp"));
                TestPyObjectPtr zero(PyLong_FromLong(0));
                TestPyObjectPtr result(
                    invalidator && zero
                        ? PyObject_CallFunctionObjArgs(
                              invalidator.get(), zero.get(), nullptr)
                        : nullptr);
                if (!result) {
                    throwPythonTestError(
                        "cannot invalidate Python manager in teardown");
                }
        }

        std::string enabledProfileYaml(const std::string &alpha,
                                       const std::string &profile =
                                           "b4_pe_three_stage_v1",
                                       const std::string &horizon = "30000",
                                       const std::string &tick = "1") {
            return "priority_energy:\n"
                   "  enabled: true\n"
                   "  profile_id: " +
                   profile + "\n"
                             "  alpha_w: " +
                   alpha + "\n"
                           "  horizon_ms: " +
                   horizon + "\n"
                             "  tick_ms: " +
                   tick + "\n";
        }

        PriorityEnergyProfileConfig enabledConfig(double alpha_w) {
            PriorityEnergyProfileConfig config;
            config.enabled = true;
            config.profile_id = "b4_pe_three_stage_v1";
            config.alpha_w = alpha_w;
            config.horizon_ms = 30000;
            config.tick_ms = 1;
            return config;
        }

        std::uint64_t binary64Bits(double value) {
            static_assert(sizeof(std::uint64_t) == sizeof(double),
                          "binary64 representation requires 64-bit double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        double binary64FromBits(std::uint64_t bits) {
            double value = 0.0;
            std::memcpy(&value, &bits, sizeof(value));
            return value;
        }

        constexpr std::uint64_t high_energy_bits =
            UINT64_C(0x3f50624dd2f1a9fc);
        constexpr std::uint64_t low_energy_bits =
            UINT64_C(0x3f2a36e2eb1c432d);
        constexpr std::uint64_t negative_zero_bits =
            UINT64_C(0x8000000000000000);

        bool strictlyClose(double left, double right) {
            const double scale =
                std::max({1.0, std::abs(left), std::abs(right)});
            return std::abs(left - right) <= 1e-12 * scale;
        }

        void expectSafeConfiguration(const ConfigManager &config) {
            const ConfigManager::ConfigurationState defaults;
            EXPECT_FALSE(config.isConfigLoaded());
            EXPECT_EQ(config.getConfigGeneration(), 0u);
            EXPECT_EQ(config.getNumCores(), defaults.num_cores);
            EXPECT_EQ(config.getSchedulerType(), defaults.scheduler_type);
            EXPECT_DOUBLE_EQ(
                config.getBaseFrequency(),
                defaults.base_frequency);
            EXPECT_EQ(config.getUnitTime(), defaults.unit_time);
            EXPECT_DOUBLE_EQ(
                config.getInitialEnergy(),
                defaults.initial_energy);
            EXPECT_DOUBLE_EQ(config.getMaxEnergy(), defaults.max_energy);
            EXPECT_DOUBLE_EQ(
                config.getBaseHarvestRate(),
                defaults.base_harvest_rate);
            EXPECT_EQ(
                config.getStartTimeOffset(),
                defaults.start_time_offset);
            EXPECT_EQ(
                config.isEnergyRecoveryEnabled(),
                defaults.enable_energy_recovery);
            EXPECT_EQ(
                config.getPeriodicCollectionInterval(),
                defaults.periodic_collection_interval);
            EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
            EXPECT_TRUE(config.getPriorityEnergyProfileId().empty());
            EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 0.0);
            EXPECT_DOUBLE_EQ(config.getBasePower(), defaults.base_power);
            EXPECT_EQ(
                config.getAllPowerCoefficients(),
                defaults.power_coefficients);
            EXPECT_EQ(
                config.getAllFrequencyRatios(),
                defaults.frequency_power_ratios);
        }

        std::string completeConfigYaml(
            const std::string &cores,
            const std::string &frequency,
            const std::string &unit_time,
            const std::string &base_power,
            const std::string &alpha,
            const std::string &initial_energy = "123.0") {
            return "cpu_islands:\n"
                   "  - numcpus: " +
                   cores +
                   "\n"
                   "    base_freq: " +
                   frequency +
                   "\n"
                   "    kernel:\n"
                   "      scheduler: gpfp_asap_block\n"
                   "energy_management:\n"
                   "  initial_energy: " + initial_energy + "\n"
                   "  max_energy: 456.0\n"
                   "  base_harvesting_rate: 0.054\n"
                   "  day_of_year: 1\n"
                   "  time_of_day_ms: 0\n"
                   "  unit_time: " +
                   unit_time +
                   "\n"
                   "  periodic_collection_interval_ms: 5\n"
                   "  enable_energy_recovery: true\n"
                   "  scheduler_energy_model:\n"
                   "    base_power: " +
                   base_power +
                   "\n"
                   "    workload_coefficients:\n"
                   "      hash: 1.7\n"
                   "    frequency_power_ratios:\n"
                   "      " +
                   frequency + ": 0.77\n" +
                   enabledProfileYaml(alpha);
        }

        class PriorityEnergyManagerIdentity : public ::testing::Test {
        protected:
            void SetUp() override {
                EnergyBridge::getInstance().shutdown();
                EnergyBridge::ensureConfigCallbackRegistered();
                const std::filesystem::path system_template =
                    std::filesystem::path(PARTSIM_SOURCE_DIR) /
                    "v9_3_b4_priority_energy_system_template.yml";
                ASSERT_TRUE(ConfigManager::getInstance().loadSystemConfig(
                    system_template.string()));
                ASSERT_TRUE(
                    ConfigManager::getInstance().isConfigLoaded());
                ASSERT_GT(
                    ConfigManager::getInstance().getConfigGeneration(),
                    0u);
            }

            void TearDown() override {
                EnergyBridge::getInstance().shutdown();
                try {
                    invalidatePythonManagerForTest();
                } catch (const std::exception &error) {
                    ADD_FAILURE() << error.what();
                }
                ConfigManager::setConfigCallback(nullptr);
                EXPECT_FALSE(
                    ConfigManager::getInstance().loadSystemConfig(""));
                EXPECT_EQ(
                    ConfigManager::getInstance().getConfigGeneration(),
                    0u);
            }
        };

        class PriorityEnergyBridgeLifecycle : public ::testing::Test {
        protected:
            void SetUp() override {
                EnergyBridge::getInstance().shutdown();
                EnergyBridge::ensureConfigCallbackRegistered();
            }

            void TearDown() override {
                EnergyBridge::getInstance().shutdown();
                try {
                    invalidatePythonManagerForTest();
                } catch (const std::exception &error) {
                    ADD_FAILURE() << error.what();
                }
                ConfigManager::setConfigCallback(nullptr);
                EXPECT_FALSE(
                    ConfigManager::getInstance().loadSystemConfig(""));
                EXPECT_EQ(
                    ConfigManager::getInstance().getConfigGeneration(),
                    0u);
                EXPECT_TRUE(pythonHasNoPendingError());
            }
        };

        void expectConfigError(const std::string &yaml,
                               const std::string &reason_fragment) {
            TemporaryYaml file(yaml);
            try {
                (void)loadPriorityEnergyProfileConfig(file.path());
                FAIL() << "expected PriorityEnergyConfigError";
            } catch (const PriorityEnergyConfigError &error) {
                EXPECT_NE(std::string(error.what()).find(reason_fragment),
                          std::string::npos)
                    << error.what();
            } catch (...) {
                FAIL() << "unexpected exception type";
            }
        }

        void expectStepConservesEnergy(const PriorityEnergyHarvestStep &step,
                                       double capacity) {
            EXPECT_GE(step.offered_j, 0.0);
            EXPECT_GE(step.actual_j, 0.0);
            EXPECT_GE(step.clipped_j, 0.0);
            EXPECT_TRUE(strictlyClose(step.actual_j + step.clipped_j,
                                      step.offered_j));
            EXPECT_GE(step.battery_after_j, 0.0);
            EXPECT_LE(step.battery_after_j, capacity);
        }
    } // namespace

    TEST_F(PriorityEnergyManagerIdentity,
           PublicReloadIsAbsentAndAccessorIsPure) {
        ConfigManager &config = ConfigManager::getInstance();
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_GT(generation, 0u);
        ASSERT_DOUBLE_EQ(config.getBaseFrequency(), 9000.0);

        const PythonManagerSnapshot first =
            pythonManagerSnapshot(generation);
        const PythonManagerSnapshot second =
            pythonManagerSnapshot(generation);
        ASSERT_NE(first.identity, 0u);
        EXPECT_EQ(first.identity, second.identity);
        EXPECT_EQ(first.generation, generation);
        EXPECT_EQ(second.generation, generation);
        EXPECT_DOUBLE_EQ(first.base_frequency, 9000.0);
        EXPECT_DOUBLE_EQ(second.base_frequency, 9000.0);
        EXPECT_EQ(first.profile_id, "b4_pe_three_stage_v1");
        EXPECT_EQ(second.profile_id, first.profile_id);
        EXPECT_TRUE(pythonPublicReloadApisAreAbsent(generation));
        EXPECT_TRUE(pythonManagerLookupFails(generation + 1));

        const std::filesystem::path legacy =
            std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "system_config_unified_template.yml";
        ASSERT_TRUE(std::filesystem::is_regular_file(legacy));
        const PythonManagerSnapshot after =
            pythonManagerSnapshot(generation);
        EXPECT_EQ(after.identity, first.identity);
        EXPECT_EQ(after.generation, first.generation);
        EXPECT_DOUBLE_EQ(after.base_frequency, first.base_frequency);
        EXPECT_EQ(after.profile_id, first.profile_id);
        EXPECT_TRUE(config.isConfigLoaded());
        EXPECT_EQ(config.getConfigGeneration(), generation);
        EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 9000.0);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyManagerIdentity,
           EmptyAuthoritativePathFailsAndInvalidatesManager) {
        ConfigManager &config = ConfigManager::getInstance();
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_NO_THROW((void)pythonManagerSnapshot(generation));

        EXPECT_TRUE(pythonEmptyAuthoritativeLoadFails());
        EXPECT_TRUE(pythonManagerLookupFails(generation));
        EXPECT_FALSE(EnergyBridge::getInstance().initialize(""));
        EXPECT_FALSE(EnergyBridge::getInstance().isInitialized());
        EXPECT_EQ(
            EnergyBridge::getInstance().getConfigGeneration(),
            0u);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyManagerIdentity,
           ValidInvalidValidLifecycleIsGenerationBound) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        const std::uint64_t first_generation =
            config.getConfigGeneration();
        const PythonManagerSnapshot first =
            pythonManagerSnapshot(first_generation);
        ASSERT_TRUE(bridge.initialize());
        ASSERT_TRUE(bridge.isInitialized());
        ASSERT_EQ(
            bridge.getConfigGeneration(), first_generation);

        TemporaryYaml malformed(
            completeConfigYaml(
                "8", "8008", "9", "0.7", "2.5") +
            "broken: [\n");
        EXPECT_FALSE(config.loadSystemConfig(malformed.path()));
        expectSafeConfiguration(config);
        EXPECT_TRUE(pythonManagerLookupFails(first_generation));
        EXPECT_FALSE(bridge.isInitialized());
        EXPECT_FALSE(bridge.initialize());
        EXPECT_EQ(bridge.getConfigGeneration(), 0u);
        EXPECT_TRUE(pythonHasNoPendingError());

        TemporaryYaml replacement(completeConfigYaml(
            "8", "8008", "9", "0.7", "2.5"));
        ASSERT_TRUE(config.loadSystemConfig(replacement.path()));
        const std::uint64_t second_generation =
            config.getConfigGeneration();
        EXPECT_GT(second_generation, 0u);
        EXPECT_NE(second_generation, first_generation);
        EXPECT_TRUE(pythonManagerLookupFails(first_generation));
        const PythonManagerSnapshot second =
            pythonManagerSnapshot(second_generation);
        EXPECT_NE(second.identity, first.identity);
        EXPECT_EQ(second.generation, second_generation);
        EXPECT_DOUBLE_EQ(second.base_frequency, 8008.0);
        EXPECT_EQ(second.profile_id, "b4_pe_three_stage_v1");
        ASSERT_TRUE(bridge.initialize());
        EXPECT_TRUE(bridge.isInitialized());
        EXPECT_EQ(
            bridge.getConfigGeneration(), second_generation);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyManagerIdentity,
           StaleBridgeReconnectsOnlyToCurrentGeneration) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        const std::uint64_t first_generation =
            config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());
        ASSERT_EQ(
            bridge.getConfigGeneration(), first_generation);

        TemporaryYaml replacement(completeConfigYaml(
            "6", "7606", "11", "0.6", "1"));
        ASSERT_TRUE(config.loadSystemConfig(replacement.path()));
        const std::uint64_t second_generation =
            config.getConfigGeneration();
        ASSERT_NE(second_generation, first_generation);
        EXPECT_FALSE(bridge.isInitialized());
        EXPECT_EQ(
            bridge.getConfigGeneration(), first_generation);
        EXPECT_TRUE(pythonManagerLookupFails(first_generation));

        ASSERT_TRUE(bridge.initialize());
        EXPECT_TRUE(bridge.isInitialized());
        EXPECT_EQ(
            bridge.getConfigGeneration(), second_generation);
        const PythonManagerSnapshot current =
            pythonManagerSnapshot(second_generation);
        EXPECT_DOUBLE_EQ(current.base_frequency, 7606.0);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle, StaleBatchFailsClosed) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml first(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(first.path()));
        const std::uint64_t first_generation =
            config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());
        ASSERT_EQ(bridge.getConfigGeneration(), first_generation);

        ScopedManagerBatchOverride old_manager(first_generation);
        ASSERT_TRUE(bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        ASSERT_EQ(old_manager.callCount(), 1);

        TemporaryYaml second(completeConfigYaml(
            "4", "8002", "1", "1.0", "1", "0.0"));
        ASSERT_TRUE(config.loadSystemConfig(second.path()));
        const std::uint64_t second_generation =
            config.getConfigGeneration();
        ASSERT_NE(second_generation, first_generation);
        const PythonManagerSnapshot current_before =
            pythonManagerSnapshot(second_generation);
        ASSERT_FALSE(pythonManagerBatchResult(second_generation));
        const Py_ssize_t stale_refcount = old_manager.managerRefCount();

        EXPECT_FALSE(bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_EQ(old_manager.callCount(), 1);
        EXPECT_EQ(old_manager.managerRefCount() + 1, stale_refcount);
        EXPECT_FALSE(bridge.isInitialized());
        EXPECT_EQ(bridge.getConfigGeneration(), 0u);
        EXPECT_TRUE(config.isConfigLoaded());
        EXPECT_EQ(config.getConfigGeneration(), second_generation);
        const PythonManagerSnapshot current_after =
            pythonManagerSnapshot(second_generation);
        EXPECT_EQ(current_after.identity, current_before.identity);
        EXPECT_EQ(current_after.generation, second_generation);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           ExplicitReinitializeUsesNewGeneration) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml first(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(first.path()));
        const std::uint64_t first_generation =
            config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());
        ScopedManagerBatchOverride old_manager(first_generation);
        ASSERT_TRUE(bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        ASSERT_EQ(old_manager.callCount(), 1);

        TemporaryYaml second(completeConfigYaml(
            "4", "8002", "1", "1.0", "1", "0.0"));
        ASSERT_TRUE(config.loadSystemConfig(second.path()));
        const std::uint64_t second_generation =
            config.getConfigGeneration();
        ASSERT_FALSE(pythonManagerBatchResult(second_generation));
        ASSERT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        ASSERT_EQ(old_manager.callCount(), 1);
        ASSERT_FALSE(bridge.isInitialized());

        ASSERT_TRUE(bridge.initialize());
        EXPECT_TRUE(bridge.isInitialized());
        EXPECT_EQ(bridge.getConfigGeneration(), second_generation);
        EXPECT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_EQ(old_manager.callCount(), 1);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           InvalidReloadInvalidatesBridgeBusinessApis) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml first(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(first.path()));
        const std::uint64_t first_generation =
            config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());
        ScopedManagerBatchOverride old_manager(first_generation);
        ASSERT_TRUE(bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        ASSERT_EQ(old_manager.callCount(), 1);

        TemporaryYaml malformed(
            completeConfigYaml(
                "4", "8002", "1", "1.0", "1", "0.0") +
            "broken: [\n");
        EXPECT_FALSE(config.loadSystemConfig(malformed.path()));
        expectSafeConfiguration(config);
        const Py_ssize_t stale_refcount = old_manager.managerRefCount();
        EXPECT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_EQ(old_manager.callCount(), 1);
        EXPECT_EQ(old_manager.managerRefCount() + 1, stale_refcount);
        EXPECT_FALSE(bridge.isInitialized());
        EXPECT_EQ(bridge.getConfigGeneration(), 0u);
        EXPECT_TRUE(pythonManagerLookupFails(first_generation));
        EXPECT_TRUE(pythonHasNoPendingError());

        TemporaryYaml replacement(completeConfigYaml(
            "4", "9003", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(replacement.path()));
        const std::uint64_t second_generation =
            config.getConfigGeneration();
        ASSERT_NE(second_generation, first_generation);
        ASSERT_TRUE(bridge.initialize());
        EXPECT_EQ(bridge.getConfigGeneration(), second_generation);
        EXPECT_TRUE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           MissingBatchMethodClearsPythonError) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml file(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());

        ScopedBatchClassMethod hidden(
            generation, BatchClassBehavior::Missing);
        EXPECT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
        ASSERT_TRUE(hidden.restore());
        EXPECT_TRUE(bridge.isInitialized());
        EXPECT_TRUE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           BatchMethodRaisesClearsPythonError) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml file(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());

        ScopedBatchClassMethod raising(
            generation, BatchClassBehavior::Raises);
        EXPECT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
        ASSERT_TRUE(raising.restore());
        EXPECT_TRUE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           WrongReturnTypeClearsPythonError) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml file(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());

        ScopedBatchClassMethod wrong_type(
            generation, BatchClassBehavior::WrongType);
        EXPECT_FALSE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
        ASSERT_TRUE(wrong_type.restore());
        EXPECT_TRUE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST_F(PriorityEnergyBridgeLifecycle,
           BatchReferenceCountsRemainStable) {
        ConfigManager &config = ConfigManager::getInstance();
        EnergyBridge &bridge = EnergyBridge::getInstance();
        TemporaryYaml file(completeConfigYaml(
            "4", "7001", "1", "1.0", "1", "100.0"));
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        const std::uint64_t generation = config.getConfigGeneration();
        ASSERT_TRUE(bridge.initialize());
        ASSERT_TRUE(
            bridge.hasSufficientEnergyForBatch({"hash"}, 1.0));
        const BatchReferenceCounts before =
            pythonBatchReferenceCounts(generation);

        bool all_results_true = true;
        for (int iteration = 0; iteration < 10000; ++iteration) {
            all_results_true = all_results_true &&
                bridge.hasSufficientEnergyForBatch({"hash"}, 1.0);
        }
        const BatchReferenceCounts after =
            pythonBatchReferenceCounts(generation);
        EXPECT_TRUE(all_results_true);
        EXPECT_EQ(after.manager, before.manager);
        EXPECT_EQ(after.method, before.method);
        EXPECT_TRUE(pythonHasNoPendingError());
    }

    TEST(PriorityEnergyConfigIntegration,
         MissingCallbackFailsClosedWithSafeDefaults) {
        ConfigManager::setConfigCallback(nullptr);
        TemporaryYaml file(completeConfigYaml(
            "3",
            "7003",
            "7",
            "0.4",
            "1"));
        ConfigManager config;
        EXPECT_FALSE(config.loadSystemConfig(file.path()));
        expectSafeConfiguration(config);
    }

    TEST(PriorityEnergyProfileConfig, MissingSubsectionUsesDisabledDefaults) {
        TemporaryYaml file(
            "energy_management:\n"
            "  initial_energy: 1.0\n");
        const auto config = loadPriorityEnergyProfileConfig(file.path());
        EXPECT_FALSE(config.enabled);
        EXPECT_TRUE(config.profile_id.empty());
        EXPECT_DOUBLE_EQ(config.alpha_w, 0.0);
        EXPECT_EQ(config.horizon_ms, 30000u);
        EXPECT_EQ(config.tick_ms, 1u);

        PriorityEnergyRuntime runtime(config);
        EXPECT_FALSE(runtime.enabled());
        EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(0), 0.0);
        EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(1), 0.0);
        EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(30000), 0.0);
        EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(30001), 0.0);
    }

    TEST(PriorityEnergyProfileConfig, DisabledProfileIsInert) {
        TemporaryYaml file(
            "priority_energy:\n"
            "  enabled: false\n"
            "  profile_id: unused_profile\n"
            "  alpha_w: -3.5\n"
            "  horizon_ms: 17\n"
            "  tick_ms: 9\n");
        const auto config = loadPriorityEnergyProfileConfig(file.path());
        ASSERT_FALSE(config.enabled);

        PriorityEnergyRuntime runtime(config);
        for (const std::uint64_t time : {0u, 1u, 5000u, 15001u, 30001u}) {
            EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(time), 0.0);
            const auto step = runtime.applyHarvest(time, 0.25, 1.0);
            EXPECT_DOUBLE_EQ(step.offered_j, 0.0);
            EXPECT_DOUBLE_EQ(step.actual_j, 0.0);
            EXPECT_DOUBLE_EQ(step.clipped_j, 0.0);
            EXPECT_DOUBLE_EQ(step.battery_after_j, 0.25);
        }
    }

    TEST(PriorityEnergyProfileConfig, DisabledProfileStillChecksFieldTypes) {
        expectConfigError(
            "priority_energy:\n"
            "  enabled: false\n"
            "  alpha_w: not-a-number\n",
            "priority_energy.alpha_w");
        expectConfigError(
            "priority_energy:\n"
            "  enabled: false\n"
            "  tick_ms: 1.5\n",
            "priority_energy.tick_ms");
    }

    TEST(PriorityEnergyProfileConfig, PresentSubsectionRequiresEnabledField) {
        expectConfigError(
            "priority_energy:\n"
            "  profile_id: b4_pe_three_stage_v1\n",
            "priority_energy.enabled");
    }

    TEST(PriorityEnergyProfileConfig, AcceptsStrictEnabledProfiles) {
        for (const std::string &alpha : {"0", "1", "2.5", "1e-3"}) {
            SCOPED_TRACE(alpha);
            TemporaryYaml file(enabledProfileYaml(alpha));
            const auto config = loadPriorityEnergyProfileConfig(file.path());
            EXPECT_TRUE(config.enabled);
            EXPECT_EQ(config.profile_id, "b4_pe_three_stage_v1");
            EXPECT_EQ(config.horizon_ms, 30000u);
            EXPECT_EQ(config.tick_ms, 1u);
            EXPECT_NO_THROW({
                PriorityEnergyRuntime runtime(config);
                EXPECT_TRUE(runtime.enabled());
            });
        }
    }

    TEST(PriorityEnergyProfileConfig, RejectsInvalidEnabledSemantics) {
        const std::vector<std::pair<std::string, std::string>> invalid = {
            {enabledProfileYaml("1", "unknown_profile"), "profile_id"},
            {enabledProfileYaml("-0.1"), "alpha_w"},
            {enabledProfileYaml("nan"), "alpha_w"},
            {enabledProfileYaml("inf"), "alpha_w"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "29999"),
             "horizon_ms"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "30001"),
             "horizon_ms"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "30000", "0"),
             "tick_ms"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "30000", "2"),
             "tick_ms"},
        };
        for (const auto &entry : invalid) {
            SCOPED_TRACE(entry.second + "\n" + entry.first);
            expectConfigError(entry.first, "priority_energy." + entry.second);
        }
    }

    TEST(PriorityEnergyProfileConfig, RejectsTypeErrors) {
        const std::vector<std::pair<std::string, std::string>> invalid = {
            {"priority_energy:\n  enabled: 1\n", "enabled"},
            {"priority_energy:\n  enabled: yes\n", "enabled"},
            {"priority_energy:\n  enabled: \"true\"\n", "enabled"},
            {"priority_energy:\n  enabled: null\n", "enabled"},
            {"priority_energy:\n  enabled: []\n", "enabled"},
            {enabledProfileYaml("one"), "alpha_w"},
            {enabledProfileYaml("\"1.0\""), "alpha_w"},
            {enabledProfileYaml("null"), "alpha_w"},
            {enabledProfileYaml("[]"), "alpha_w"},
            {enabledProfileYaml("{}"), "alpha_w"},
            {enabledProfileYaml(".nan"), "alpha_w"},
            {enabledProfileYaml(".inf"), "alpha_w"},
            {enabledProfileYaml("1", "123"), "profile_id"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "30000.0"),
             "horizon_ms"},
            {enabledProfileYaml("1", "b4_pe_three_stage_v1", "30000", "one"),
             "tick_ms"},
        };
        for (const auto &entry : invalid) {
            SCOPED_TRACE(entry.second + "\n" + entry.first);
            expectConfigError(entry.first, "priority_energy." + entry.second);
        }

        for (const std::string &yaml :
             {"priority_energy: null\n",
              "priority_energy: disabled\n",
              "priority_energy: []\n"}) {
            expectConfigError(yaml, "priority_energy: must be a mapping");
        }
    }

    TEST(PriorityEnergyProfileConfig, EnabledProfileRequiresEveryField) {
        const std::vector<std::pair<std::string, std::string>> invalid = {
            {"priority_energy:\n"
             "  enabled: true\n"
             "  alpha_w: 1\n"
             "  horizon_ms: 30000\n"
             "  tick_ms: 1\n",
             "profile_id"},
            {"priority_energy:\n"
             "  enabled: true\n"
             "  profile_id: b4_pe_three_stage_v1\n"
             "  horizon_ms: 30000\n"
             "  tick_ms: 1\n",
             "alpha_w"},
            {"priority_energy:\n"
             "  enabled: true\n"
             "  profile_id: b4_pe_three_stage_v1\n"
             "  alpha_w: 1\n"
             "  tick_ms: 1\n",
             "horizon_ms"},
            {"priority_energy:\n"
             "  enabled: true\n"
             "  profile_id: b4_pe_three_stage_v1\n"
             "  alpha_w: 1\n"
             "  horizon_ms: 30000\n",
             "tick_ms"},
        };
        for (const auto &entry : invalid) {
            SCOPED_TRACE(entry.second);
            expectConfigError(entry.first, "priority_energy." + entry.second);
        }
    }

    TEST(PriorityEnergyProfileConfig,
         RejectsSemanticDuplicatesAndUnknownFields) {
        expectConfigError(
            enabledProfileYaml("1") +
                "priority_energy:\n"
                "  enabled: false\n",
            "duplicate key 'priority_energy'");
        expectConfigError(
            "priority_energy:\n"
            "  enabled: false\n"
            "\"priority_energy\":\n"
            "  enabled: true\n",
            "duplicate key 'priority_energy'");
        expectConfigError(
            "\"priority_energy\":\n"
            "  enabled: false\n"
            "priority_energy:\n"
            "  enabled: true\n",
            "duplicate key 'priority_energy'");
        expectConfigError(
            "priority_energy:\n"
            "  enabled: false\n"
            "  enabled: false\n",
            "duplicate key 'enabled'");
        expectConfigError(
            "priority_energy:\n"
            "  enabled: true\n"
            "  \"enabled\": false\n",
            "duplicate key 'enabled'");
        expectConfigError(
            "priority_energy:\r\n"
            "  \"enabled\": false # first\r\n"
            "  enabled: false # duplicate\r\n",
            "duplicate key 'enabled'");
        expectConfigError(
            "legacy:\n"
            "  ordinary: 1\n"
            "  \"ordinary\": 2\n",
            "duplicate key 'ordinary'");
        expectConfigError(
            "priority_energy:\n"
            "  enabled: false\n"
            "  trace_mode: verbose\n",
            "priority_energy.trace_mode: unknown field");
        expectConfigError(
            "defaults: &defaults\n"
            "  enabled: false\n"
            "priority_energy:\n"
            "  <<: *defaults\n"
            "  enabled: false\n",
            "priority_energy: YAML merge is not allowed");
    }

    TEST(PriorityEnergyProfileConfig,
         ValidatesWholeDocumentAndDecodedYamlContent) {
        expectConfigError(
            enabledProfileYaml("1") +
                "broken_legacy: [\n",
            "system YAML malformed");
        expectConfigError(
            "legacy: [one, two\n",
            "system YAML malformed");
        expectConfigError(
            "- priority_energy\n"
            "- enabled\n",
            "root node must be a mapping");
        std::string missing_path;
        {
            TemporaryYaml removed("priority_energy:\n  enabled: false\n");
            missing_path = removed.path();
        }
        try {
            (void)loadPriorityEnergyProfileConfig(missing_path);
            FAIL() << "expected unreadable system YAML failure";
        } catch (const PriorityEnergyConfigError &error) {
            EXPECT_NE(std::string(error.what()).find("system YAML unreadable"),
                      std::string::npos)
                << error.what();
        } catch (...) {
            FAIL() << "unexpected exception type";
        }

        TemporaryYaml file(
            "notes: |\n"
            "  enabled: is text, not a key\n"
            "other_section:\n"
            "  enabled: ordinary legacy value\n"
            "priority_energy:\n"
            "  enabled: false\n");
        const auto parsed =
            loadPriorityEnergyProfileConfig(file.path());
        EXPECT_FALSE(parsed.enabled);
    }

    TEST(PriorityEnergyProfileConfig, ConfigManagerExposesNativeProfile) {
        ScopedStrictConfigCallback callback;
        TemporaryYaml file(enabledProfileYaml("2.5"));
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        EXPECT_TRUE(config.isPriorityEnergyProfileEnabled());
        EXPECT_EQ(config.getPriorityEnergyProfileId(),
                  "b4_pe_three_stage_v1");
        EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 2.5);
        EXPECT_EQ(config.getPriorityEnergyHorizonMs(), 30000u);
        EXPECT_EQ(config.getPriorityEnergyTickMs(), 1u);
        const auto snapshot = config.getPriorityEnergyProfileConfig();
        EXPECT_TRUE(snapshot.enabled);
        EXPECT_DOUBLE_EQ(snapshot.alpha_w, 2.5);
    }

    TEST(PriorityEnergyProfileConfig,
         ConfigManagerReloadHasDeterministicProfileOverwrite) {
        ScopedStrictConfigCallback callback;
        TemporaryYaml enabled_file(enabledProfileYaml("1"));
        TemporaryYaml missing_file(
            "energy_management:\n"
            "  initial_energy: 1.0\n");
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(enabled_file.path()));
        ASSERT_TRUE(config.isPriorityEnergyProfileEnabled());
        ASSERT_TRUE(config.loadSystemConfig(missing_file.path()));
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
        EXPECT_TRUE(config.getPriorityEnergyProfileId().empty());
        EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 0.0);
        EXPECT_EQ(config.getPriorityEnergyHorizonMs(), 30000u);
        EXPECT_EQ(config.getPriorityEnergyTickMs(), 1u);
    }

    TEST(PriorityEnergyProfileConfig,
         StrictPythonCallbackAtomicallyProvidesProfileAndLegacyFields) {
        ScopedStrictConfigCallback callback;
        TemporaryYaml file(completeConfigYaml(
            "3",
            "7003",
            "7",
            "0.4",
            "1"));
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(file.path()));
        EXPECT_TRUE(config.isConfigLoaded());
        EXPECT_EQ(config.getNumCores(), 3);
        EXPECT_EQ(config.getSchedulerType(), "gpfp_asap_block");
        EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 7003.0);
        EXPECT_EQ(config.getUnitTime(), 7);
        EXPECT_DOUBLE_EQ(config.getInitialEnergy(), 123.0);
        EXPECT_DOUBLE_EQ(config.getMaxEnergy(), 456.0);
        EXPECT_DOUBLE_EQ(config.getBaseHarvestRate(), 0.054);
        EXPECT_DOUBLE_EQ(config.getBasePower(), 0.4);
        EXPECT_DOUBLE_EQ(config.getPowerCoefficient("hash"), 1.7);
        EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(7003), 0.77);
        EXPECT_TRUE(config.isPriorityEnergyProfileEnabled());
        EXPECT_EQ(config.getPriorityEnergyProfileId(),
                  "b4_pe_three_stage_v1");
        EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 1.0);

        TemporaryYaml replacement(completeConfigYaml(
            "8",
            "8008",
            "9",
            "0.7",
            "2.5"));
        ASSERT_TRUE(config.loadSystemConfig(replacement.path()));
        EXPECT_EQ(config.getNumCores(), 8);
        EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 8008.0);
        EXPECT_EQ(config.getUnitTime(), 9);
        EXPECT_DOUBLE_EQ(config.getBasePower(), 0.7);
        EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 2.5);
        EXPECT_EQ(config.getAllFrequencyRatios().count(7003), 0u);
        EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8008), 0.77);
    }

    TEST(PriorityEnergyProfileConfig,
         InvalidStrictLoadLeavesDisabledUnloadedStateAndCanRecover) {
        ScopedStrictConfigCallback callback;
        const std::string changed_legacy =
            "cpu_islands:\n"
            "  - numcpus: 2\n"
            "    base_freq: 1234\n";
        TemporaryYaml valid(
            "cpu_islands:\n"
            "  - numcpus: 4\n"
            "    base_freq: 9000\n" +
            enabledProfileYaml("1"));
        TemporaryYaml malformed(
            changed_legacy +
            enabledProfileYaml("2.5") +
            "broken_legacy: [\n");
        TemporaryYaml duplicate(
            changed_legacy +
            "priority_energy:\n"
            "  enabled: false\n"
            "\"priority_energy\":\n"
            "  enabled: true\n");
        TemporaryYaml invalid(
            changed_legacy +
            enabledProfileYaml("-1"));
        TemporaryYaml missing(
            "energy_management:\n"
            "  initial_energy: 1.0\n");
        TemporaryYaml disabled(
            "priority_energy:\n"
            "  enabled: false\n");

        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(valid.path()));
        ASSERT_TRUE(config.isPriorityEnergyProfileEnabled());
        ASSERT_DOUBLE_EQ(config.getBaseFrequency(), 9000.0);

        for (const TemporaryYaml *bad :
             {&malformed, &duplicate, &invalid}) {
            EXPECT_FALSE(config.loadSystemConfig(bad->path()));
            expectSafeConfiguration(config);
            ASSERT_TRUE(config.loadSystemConfig(valid.path()));
            ASSERT_TRUE(config.isPriorityEnergyProfileEnabled());
        }

        ASSERT_TRUE(config.loadSystemConfig(missing.path()));
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
        ASSERT_TRUE(config.loadSystemConfig(disabled.path()));
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
        ASSERT_TRUE(config.loadSystemConfig(valid.path()));
        ASSERT_TRUE(config.loadSystemConfig(valid.path()));
        EXPECT_TRUE(config.isPriorityEnergyProfileEnabled());
    }

    TEST(PriorityEnergyProfileConfig, LegacyTemplateRemainsDisabled) {
        const std::filesystem::path legacy =
            std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "system_config_unified_template.yml";
        const auto parsed = loadPriorityEnergyProfileConfig(legacy.string());
        EXPECT_FALSE(parsed.enabled);
        EXPECT_TRUE(parsed.profile_id.empty());
        EXPECT_DOUBLE_EQ(parsed.alpha_w, 0.0);
        EXPECT_EQ(parsed.horizon_ms, 30000u);
        EXPECT_EQ(parsed.tick_ms, 1u);

        ScopedStrictConfigCallback callback;
        ConfigManager config;
        EXPECT_NO_THROW((void)config.loadSystemConfig(legacy.string()));
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
    }

    TEST(PriorityEnergyConfigIntegration,
         MaterializedLegacyTemplateConstructsAllNineSchedulers) {
        const std::filesystem::path legacy =
            std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "system_config_unified_template.yml";
        std::ifstream input(legacy);
        ASSERT_TRUE(input.good());
        const std::string template_text(
            (std::istreambuf_iterator<char>(input)),
            std::istreambuf_iterator<char>());
        const std::string scheduler_token = "scheduler: gpfp_tie";
        ASSERT_NE(template_text.find(scheduler_token), std::string::npos);

        const std::array<const char *, 9> schedulers = {
            "gpfp_asap_block",
            "gpfp_asap_nonblock",
            "gpfp_asap_sync",
            "gpfp_alap_block",
            "gpfp_alap_nonblock",
            "gpfp_alap_sync",
            "gpfp_st_block",
            "gpfp_st_nonblock",
            "gpfp_st_sync",
        };
        for (const char *scheduler : schedulers) {
            SCOPED_TRACE(scheduler);
            std::string materialized = template_text;
            materialized.replace(
                materialized.find(scheduler_token),
                scheduler_token.size(),
                "scheduler: " + std::string(scheduler));
            TemporaryYaml system_file(materialized);

            System system(system_file.path());
            ConfigManager &config = ConfigManager::getInstance();
            ASSERT_TRUE(config.isConfigLoaded());
            EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
            EXPECT_EQ(config.getNumCores(), 4);
            EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 8100.0);
            EXPECT_DOUBLE_EQ(config.getBasePower(), 0.5);
            EXPECT_DOUBLE_EQ(config.getBaseHarvestRate(), 0.054);
            ASSERT_EQ(system.schedulers.size(), 1u);
            ASSERT_EQ(system.scheduler_identities.size(), 1u);
            EXPECT_EQ(
                system.scheduler_identities.front().configured_scheduler,
                scheduler);
        }

        ScopedStrictConfigCallback callback;
        ASSERT_TRUE(
            ConfigManager::getInstance().loadSystemConfig(legacy.string()));
    }

    TEST(PriorityEnergyProfileConfig, Phase2B1TemplateIsExplicitlyDisabled) {
        const std::filesystem::path system_template =
            std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "v9_3_b4_priority_energy_system_template.yml";
        const auto parsed =
            loadPriorityEnergyProfileConfig(system_template.string());
        EXPECT_FALSE(parsed.enabled);
        EXPECT_EQ(parsed.profile_id, "b4_pe_three_stage_v1");
        EXPECT_DOUBLE_EQ(parsed.alpha_w, 0.0);
        EXPECT_EQ(parsed.horizon_ms, 30000u);
        EXPECT_EQ(parsed.tick_ms, 1u);

        ScopedStrictConfigCallback callback;
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(system_template.string()));
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
        EXPECT_EQ(config.getPriorityEnergyProfileId(),
                  "b4_pe_three_stage_v1");
    }

    TEST(PriorityEnergyConfigIntegration,
         CheckedIntegersRejectNarrowingAndNonPositiveValues) {
        ScopedStrictConfigCallback callback;
        ConfigManager config;

        TemporaryYaml int_max(completeConfigYaml(
            "2147483647",
            "2147483647",
            "2147483647",
            "0.4",
            "1"));
        ASSERT_TRUE(config.loadSystemConfig(int_max.path()));
        EXPECT_EQ(config.getNumCores(), INT_MAX);
        EXPECT_EQ(config.getUnitTime(), INT_MAX);
        EXPECT_EQ(
            config.getAllFrequencyRatios().count(INT_MAX),
            1u);

        const std::vector<std::string> invalid_cores = {
            "2147483648",
            "-2147483648",
            "-2147483649",
            "4294967297",
            "0",
        };
        for (const std::string &cores : invalid_cores) {
            SCOPED_TRACE(cores);
            TemporaryYaml bad(completeConfigYaml(
                cores,
                "7003",
                "7",
                "0.4",
                "1"));
            EXPECT_FALSE(config.loadSystemConfig(bad.path()));
            expectSafeConfiguration(config);
        }

        for (const std::string &unit : {"0", "-1", "2147483648"}) {
            SCOPED_TRACE(unit);
            TemporaryYaml bad(completeConfigYaml(
                "3",
                "7003",
                unit,
                "0.4",
                "1"));
            EXPECT_FALSE(config.loadSystemConfig(bad.path()));
            expectSafeConfiguration(config);
        }

        TemporaryYaml nonpositive_frequency(
            "cpu_islands:\n"
            "  - numcpus: 3\n"
            "    base_freq: 7003\n"
            "    kernel:\n"
            "      scheduler: gpfp_asap_block\n"
            "energy_management:\n"
            "  unit_time: 7\n"
            "  periodic_collection_interval_ms: 5\n"
            "  scheduler_energy_model:\n"
            "    frequency_power_ratios:\n"
            "      0: 0.77\n" +
            enabledProfileYaml("1"));
        EXPECT_FALSE(config.loadSystemConfig(
            nonpositive_frequency.path()));
        expectSafeConfiguration(config);
    }

    TEST(PriorityEnergyConfigIntegration,
         LegacyMergeAndExplicitOverrideRetainSafeLoaderSemantics) {
        ScopedStrictConfigCallback callback;
        ConfigManager config;

        TemporaryYaml inherited(
            "defaults: &energy_defaults\n"
            "  initial_energy: 123.0\n"
            "  max_energy: 456.0\n"
            "energy_management:\n"
            "  <<: *energy_defaults\n");
        ASSERT_TRUE(config.loadSystemConfig(inherited.path()));
        EXPECT_DOUBLE_EQ(config.getInitialEnergy(), 123.0);
        EXPECT_DOUBLE_EQ(config.getMaxEnergy(), 456.0);

        TemporaryYaml overridden(
            "defaults: &energy_defaults\n"
            "  initial_energy: 123.0\n"
            "  max_energy: 456.0\n"
            "energy_management:\n"
            "  <<: *energy_defaults\n"
            "  initial_energy: 150.0\n");
        ASSERT_TRUE(config.loadSystemConfig(overridden.path()));
        EXPECT_DOUBLE_EQ(config.getInitialEnergy(), 150.0);
        EXPECT_DOUBLE_EQ(config.getMaxEnergy(), 456.0);

        TemporaryYaml profile_merge(
            "defaults: &profile_defaults\n"
            "  enabled: false\n"
            "priority_energy:\n"
            "  <<: *profile_defaults\n");
        EXPECT_FALSE(config.loadSystemConfig(profile_merge.path()));
        expectSafeConfiguration(config);
    }

    TEST(PriorityEnergyConfigIntegration,
         MissingPythonLoaderAttributeClearsPyErrAndCanRecover) {
        ScopedStrictConfigCallback callback;
        TemporaryYaml valid(completeConfigYaml(
            "3",
            "7003",
            "7",
            "0.4",
            "1"));
        ConfigManager config;
        ASSERT_TRUE(config.loadSystemConfig(valid.path()));
        ASSERT_TRUE(Py_IsInitialized());

        PyObject *saved_loader = nullptr;
        bool hidden = false;
        {
            const PyGILState_STATE state = PyGILState_Ensure();
            PyObject *module = PyImport_ImportModule("energy_manager");
            if (module != nullptr) {
                saved_loader =
                    PyObject_GetAttrString(module, "load_config_for_cpp");
                if (saved_loader != nullptr) {
                    hidden =
                        PyObject_DelAttrString(
                            module,
                            "load_config_for_cpp") == 0;
                }
                Py_DECREF(module);
            }
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            PyGILState_Release(state);
        }
        ASSERT_TRUE(hidden);
        ASSERT_NE(saved_loader, nullptr);

        EXPECT_FALSE(config.loadSystemConfig(valid.path()));
        expectSafeConfiguration(config);

        bool no_pending_error = false;
        bool restored = false;
        {
            const PyGILState_STATE state = PyGILState_Ensure();
            no_pending_error = PyErr_Occurred() == nullptr;
            PyObject *module = PyImport_ImportModule("energy_manager");
            if (module != nullptr) {
                restored =
                    PyObject_SetAttrString(
                        module,
                        "load_config_for_cpp",
                        saved_loader) == 0;
                Py_DECREF(module);
            }
            Py_DECREF(saved_loader);
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            PyGILState_Release(state);
        }
        EXPECT_TRUE(no_pending_error);
        ASSERT_TRUE(restored);
        EXPECT_TRUE(config.loadSystemConfig(valid.path()));
    }

    TEST(PriorityEnergyConfigIntegration,
         InitializeRequiresAuthoritativeTransactionAndFailsClosed) {
        EnergyBridge &bridge = EnergyBridge::getInstance();
        bridge.shutdown();
        ConfigManager &config = ConfigManager::getInstance();
        ConfigManager::setConfigCallback(nullptr);
        ASSERT_FALSE(config.loadSystemConfig(""));

        TemporaryYaml valid(completeConfigYaml(
            "3",
            "7003",
            "7",
            "0.4",
            "1"));
        TemporaryYaml malformed(
            completeConfigYaml(
                "8",
                "8008",
                "9",
                "0.7",
                "2.5") +
            "broken: [\n");

        EXPECT_FALSE(bridge.initialize(valid.path()));
        EXPECT_FALSE(bridge.isInitialized());

        EnergyBridge::ensureConfigCallbackRegistered();
        ASSERT_TRUE(config.loadSystemConfig(valid.path()));
        ASSERT_TRUE(bridge.initialize(valid.path()));
        EXPECT_TRUE(config.isConfigLoaded());
        EXPECT_EQ(config.getNumCores(), 3);
        EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 7003.0);
        EXPECT_EQ(config.getUnitTime(), 7);
        EXPECT_DOUBLE_EQ(config.getBasePower(), 0.4);
        EXPECT_DOUBLE_EQ(config.getPriorityEnergyAlphaW(), 1.0);

        EXPECT_FALSE(config.loadSystemConfig(malformed.path()));
        expectSafeConfiguration(config);
        EXPECT_FALSE(bridge.initialize(malformed.path()));
        EXPECT_FALSE(bridge.isInitialized());
        bridge.shutdown();
        ConfigManager::setConfigCallback(nullptr);
    }

    TEST(PriorityEnergyConfigIntegration,
         SystemDescriptorStrictFailurePrecedesOrdinarySystemConstruction) {
        const std::filesystem::path template_path =
            std::filesystem::path(PARTSIM_SOURCE_DIR) /
            "v9_3_b4_priority_energy_system_template.yml";

        SystemDescriptor descriptor(template_path.string());
        ConfigManager &config = ConfigManager::getInstance();
        ASSERT_TRUE(config.isConfigLoaded());
        ASSERT_EQ(descriptor.islands.size(), 1u);
        EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
        EXPECT_EQ(config.getNumCores(), 4);
        EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 9000.0);
        EXPECT_DOUBLE_EQ(config.getBasePower(), 0.5);
        EXPECT_DOUBLE_EQ(config.getPowerCoefficient("hash"), 0.8);
        EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(9000), 1.0);
        EXPECT_EQ(descriptor.islands.front().numcpus, 4u);
        EXPECT_EQ(descriptor.islands.front().base_freq, 9000);

        std::ifstream input(template_path);
        ASSERT_TRUE(input.good());
        std::string invalid_text(
            (std::istreambuf_iterator<char>(input)),
            std::istreambuf_iterator<char>());
        const std::string valid_alpha = "  alpha_w: 0.0";
        const auto alpha_position = invalid_text.find(valid_alpha);
        ASSERT_NE(alpha_position, std::string::npos);
        invalid_text.replace(
            alpha_position,
            valid_alpha.size(),
            "  alpha_w: not-a-number");
        TemporaryYaml invalid(invalid_text);

        EXPECT_NO_THROW((void)yaml::parse(invalid.path()));
        EXPECT_THROW(
            (void)System(invalid.path()),
            MetaSim::BaseExc);
        expectSafeConfiguration(config);
    }

    TEST(PriorityEnergyRuntime, UsesExactDecisionTimeBoundaries) {
        PriorityEnergyRuntime runtime(enabledConfig(1.0));
        const std::vector<std::pair<std::uint64_t, std::uint64_t>> expected = {
            {0, 0},                    {1, high_energy_bits},
            {4999, high_energy_bits},  {5000, high_energy_bits},
            {5001, low_energy_bits},   {14999, low_energy_bits},
            {15000, low_energy_bits},  {15001, high_energy_bits},
            {29999, high_energy_bits}, {30000, high_energy_bits},
            {30001, 0},
        };
        for (const auto &entry : expected) {
            SCOPED_TRACE(entry.first);
            EXPECT_EQ(
                binary64Bits(
                    runtime.offeredEnergyForDecisionTime(entry.first)),
                entry.second);
        }
        EXPECT_EQ(
            binary64Bits(runtime.offeredEnergyForDecisionTime(
                std::numeric_limits<std::uint64_t>::max())),
            UINT64_C(0));
    }

    TEST(PriorityEnergyRuntime, RejectsInvalidDirectConfiguration) {
        auto config = enabledConfig(1.0);
        config.profile_id = "unknown";
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);

        config = enabledConfig(-1.0);
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);

        config = enabledConfig(
            std::numeric_limits<double>::quiet_NaN());
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);

        config = enabledConfig(
            std::numeric_limits<double>::infinity());
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);

        config = enabledConfig(1.0);
        config.horizon_ms = 29999;
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);

        config = enabledConfig(1.0);
        config.tick_ms = 2;
        EXPECT_THROW((void)PriorityEnergyRuntime(config),
                     PriorityEnergyConfigError);
    }

    TEST(PriorityEnergyRuntime, TotalsAllThirtyThousandIntervals) {
        for (const double alpha : {0.0, 1.0, 0.125, 2.5}) {
            SCOPED_TRACE(alpha);
            PriorityEnergyRuntime runtime(enabledConfig(alpha));
            const double high =
                alpha == 1.0
                    ? binary64FromBits(high_energy_bits)
                    : (alpha * 1.0) * 0.001;
            const double low =
                alpha == 1.0
                    ? binary64FromBits(low_energy_bits)
                    : (alpha * 0.2) * 0.001;
            double accumulated = 0.0;
            std::uint64_t high_steps = 0;
            std::uint64_t low_steps = 0;
            for (std::uint64_t time = 1; time <= 30000; ++time) {
                const double offered =
                    runtime.offeredEnergyForDecisionTime(time);
                const bool high_segment = time <= 5000 || time > 15000;
                EXPECT_EQ(binary64Bits(offered),
                          binary64Bits(high_segment ? high : low));
                high_steps += high_segment ? 1 : 0;
                low_steps += high_segment ? 0 : 1;
                accumulated += offered;
            }
            const double segmented_reference =
                20000.0 * high + 10000.0 * low;
            const double mathematical_reference = 22.0 * alpha;
            EXPECT_EQ(high_steps, 20000u);
            EXPECT_EQ(low_steps, 10000u);
            EXPECT_TRUE(strictlyClose(accumulated, segmented_reference));
            EXPECT_TRUE(strictlyClose(accumulated, mathematical_reference));
            EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(0), 0.0);
            EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(30001), 0.0);
            EXPECT_DOUBLE_EQ(runtime.offeredEnergyForDecisionTime(999999), 0.0);
        }
    }

    TEST(PriorityEnergyRuntime, AppliesHarvestAndPhysicalClipping) {
        PriorityEnergyRuntime runtime(enabledConfig(1.0));
        const double offered = binary64FromBits(high_energy_bits);

        const auto empty = runtime.applyHarvest(1, 0.0, 1.0);
        EXPECT_EQ(binary64Bits(empty.offered_j), binary64Bits(offered));
        EXPECT_EQ(binary64Bits(empty.actual_j), binary64Bits(offered));
        EXPECT_DOUBLE_EQ(empty.clipped_j, 0.0);
        EXPECT_EQ(binary64Bits(empty.battery_after_j), binary64Bits(offered));
        expectStepConservesEnergy(empty, 1.0);

        const double near_full_before = 0.9995;
        const double near_full_space = 1.0 - near_full_before;
        const auto near_full =
            runtime.applyHarvest(1, near_full_before, 1.0);
        EXPECT_EQ(binary64Bits(near_full.actual_j),
                  binary64Bits(near_full_space));
        EXPECT_EQ(binary64Bits(near_full.clipped_j),
                  binary64Bits(offered - near_full_space));
        EXPECT_DOUBLE_EQ(near_full.battery_after_j, 1.0);
        expectStepConservesEnergy(near_full, 1.0);

        const auto full = runtime.applyHarvest(1, 1.0, 1.0);
        EXPECT_EQ(binary64Bits(full.offered_j), binary64Bits(offered));
        EXPECT_DOUBLE_EQ(full.actual_j, 0.0);
        EXPECT_EQ(binary64Bits(full.clipped_j), binary64Bits(offered));
        EXPECT_DOUBLE_EQ(full.battery_after_j, 1.0);
        expectStepConservesEnergy(full, 1.0);

        const auto zero_capacity = runtime.applyHarvest(1, 0.0, 0.0);
        EXPECT_DOUBLE_EQ(zero_capacity.actual_j, 0.0);
        EXPECT_EQ(binary64Bits(zero_capacity.clipped_j),
                  binary64Bits(offered));
        EXPECT_DOUBLE_EQ(zero_capacity.battery_after_j, 0.0);

        for (const std::uint64_t time :
             {UINT64_C(0),
              UINT64_C(30001),
              std::numeric_limits<std::uint64_t>::max()}) {
            const auto no_offer = runtime.applyHarvest(time, 0.25, 1.0);
            EXPECT_DOUBLE_EQ(no_offer.offered_j, 0.0);
            EXPECT_DOUBLE_EQ(no_offer.actual_j, 0.0);
            EXPECT_DOUBLE_EQ(no_offer.clipped_j, 0.0);
            EXPECT_DOUBLE_EQ(no_offer.battery_after_j, 0.25);
        }

        PriorityEnergyRuntime zero_alpha(enabledConfig(0.0));
        const auto zero = zero_alpha.applyHarvest(1, 0.25, 1.0);
        EXPECT_DOUBLE_EQ(zero.offered_j, 0.0);
        EXPECT_DOUBLE_EQ(zero.battery_after_j, 0.25);
    }

    TEST(PriorityEnergyRuntime, ClippingDoesNotUseAnEpsilon) {
        PriorityEnergyRuntime runtime(enabledConfig(1.0));
        const double before = std::nextafter(1.0, 0.0);
        const double physical_space = 1.0 - before;
        ASSERT_GT(physical_space, 0.0);
        ASSERT_LT(physical_space, 1e-12);

        const auto step = runtime.applyHarvest(1, before, 1.0);
        EXPECT_EQ(binary64Bits(step.actual_j), binary64Bits(physical_space));
        EXPECT_EQ(binary64Bits(step.clipped_j),
                  binary64Bits(step.offered_j - physical_space));
        EXPECT_DOUBLE_EQ(step.battery_after_j, 1.0);
        expectStepConservesEnergy(step, 1.0);
    }

    TEST(PriorityEnergyRuntime, ZeroOfferPreservesNegativeZeroBits) {
        const double negative_zero =
            binary64FromBits(negative_zero_bits);
        ASSERT_TRUE(std::signbit(negative_zero));

        PriorityEnergyProfileConfig disabled_config;
        PriorityEnergyRuntime disabled(disabled_config);
        const auto disabled_step =
            disabled.applyHarvest(1, negative_zero, 1.0);
        EXPECT_TRUE(std::signbit(disabled_step.battery_after_j));
        EXPECT_EQ(binary64Bits(disabled_step.battery_after_j),
                  negative_zero_bits);

        PriorityEnergyRuntime zero_alpha(enabledConfig(0.0));
        const auto alpha_step =
            zero_alpha.applyHarvest(1, negative_zero, 1.0);
        EXPECT_TRUE(std::signbit(alpha_step.battery_after_j));
        EXPECT_EQ(binary64Bits(alpha_step.battery_after_j),
                  negative_zero_bits);

        PriorityEnergyRuntime enabled(enabledConfig(1.0));
        for (const std::uint64_t time :
             {UINT64_C(0),
              UINT64_C(30001),
              std::numeric_limits<std::uint64_t>::max()}) {
            const auto step =
                enabled.applyHarvest(time, negative_zero, 1.0);
            EXPECT_EQ(binary64Bits(step.offered_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(step.actual_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(step.clipped_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(step.battery_after_j),
                      negative_zero_bits);
            EXPECT_TRUE(std::signbit(step.battery_after_j));
        }
    }

    TEST(PriorityEnergyRuntime,
         TightensRepresentableSpaceInsteadOfThrowing) {
        PriorityEnergyRuntime runtime(enabledConfig(1.0));
        const double before = 1.3213248500141373e-31;
        const double capacity = 3.7098376936859965e-31;
        constexpr std::uint64_t initial_actual_bits =
            UINT64_C(0x399360bf418bd994);
        constexpr std::uint64_t corrected_actual_bits =
            UINT64_C(0x399360bf418bd993);

        const auto first = runtime.applyHarvest(1, before, capacity);
        const auto second = runtime.applyHarvest(1, before, capacity);
        EXPECT_EQ(binary64Bits(first.actual_j),
                  corrected_actual_bits);
        EXPECT_NE(binary64Bits(first.actual_j),
                  initial_actual_bits);
        EXPECT_LE(first.actual_j, first.offered_j);
        EXPECT_GE(first.clipped_j, 0.0);
        EXPECT_LE(first.battery_after_j, capacity);
        expectStepConservesEnergy(first, capacity);
        EXPECT_EQ(binary64Bits(first.actual_j),
                  binary64Bits(second.actual_j));
        EXPECT_EQ(binary64Bits(first.clipped_j),
                  binary64Bits(second.clipped_j));
        EXPECT_EQ(binary64Bits(first.battery_after_j),
                  binary64Bits(second.battery_after_j));

        const double maximum =
            std::numeric_limits<double>::max();
        PriorityEnergyRuntime maximum_alpha(enabledConfig(maximum));
        const auto maximum_step =
            maximum_alpha.applyHarvest(1, 0.0, maximum);
        EXPECT_TRUE(std::isfinite(maximum_step.offered_j));
        EXPECT_TRUE(std::isfinite(maximum_step.actual_j));
        EXPECT_LE(maximum_step.battery_after_j, maximum);
    }

    TEST(PriorityEnergyRuntime, RejectsInvalidBatteryInputs) {
        PriorityEnergyRuntime runtime(enabledConfig(1.0));
        const double nan = std::numeric_limits<double>::quiet_NaN();
        const double inf = std::numeric_limits<double>::infinity();

        EXPECT_THROW(runtime.applyHarvest(1, -0.1, 1.0),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, 1.1, 1.0),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, 0.0, -1.0),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, nan, 1.0),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, inf, inf),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, 0.0, nan),
                     PriorityEnergyRuntimeError);
        EXPECT_THROW(runtime.applyHarvest(1, 0.0, inf),
                     PriorityEnergyRuntimeError);
    }

    TEST(PriorityEnergyRuntime, IsDeterministicAndInstanceLocal) {
        PriorityEnergyRuntime one(enabledConfig(1.0));
        PriorityEnergyRuntime two(enabledConfig(2.5));
        const std::array<std::uint64_t, 8> times = {
            1, 5000, 5001, 15000, 15001, 29999, 30000, 30001};

        std::array<std::uint64_t, 8> one_forward{};
        std::array<std::uint64_t, 8> one_reverse{};
        for (std::size_t index = 0; index < times.size(); ++index) {
            one_forward[index] = binary64Bits(
                one.offeredEnergyForDecisionTime(times[index]));
        }
        for (std::size_t reverse = times.size(); reverse > 0; --reverse) {
            const std::size_t index = reverse - 1;
            one_reverse[index] = binary64Bits(
                one.offeredEnergyForDecisionTime(times[index]));
        }
        EXPECT_EQ(one_forward, one_reverse);

        for (std::size_t index = 0; index < times.size(); ++index) {
            const double expected_two =
                times[index] == 0 || times[index] > 30000
                    ? 0.0
                    : (2.5 * ((times[index] <= 5000 ||
                               times[index] > 15000)
                                  ? 1.0
                                  : 0.2)) *
                          0.001;
            EXPECT_EQ(binary64Bits(
                          two.offeredEnergyForDecisionTime(times[index])),
                      binary64Bits(expected_two));
            EXPECT_EQ(binary64Bits(
                          one.offeredEnergyForDecisionTime(times[index])),
                      one_forward[index]);
        }

        std::array<std::array<std::uint64_t, 8>, 4> threaded{};
        std::vector<std::thread> workers;
        for (std::size_t worker = 0; worker < threaded.size(); ++worker) {
            workers.emplace_back([&one, &times, &threaded, worker]() {
                for (std::size_t index = 0; index < times.size(); ++index) {
                    threaded[worker][index] = binary64Bits(
                        one.offeredEnergyForDecisionTime(times[index]));
                }
            });
        }
        for (auto &worker : workers) {
            worker.join();
        }
        for (const auto &result : threaded) {
            EXPECT_EQ(result, one_forward);
        }

        const auto first = one.applyHarvest(5001, 0.5, 1.0);
        const auto second = one.applyHarvest(5001, 0.5, 1.0);
        EXPECT_EQ(binary64Bits(first.offered_j),
                  binary64Bits(second.offered_j));
        EXPECT_EQ(binary64Bits(first.actual_j), binary64Bits(second.actual_j));
        EXPECT_EQ(binary64Bits(first.clipped_j),
                  binary64Bits(second.clipped_j));
        EXPECT_EQ(binary64Bits(first.battery_after_j),
                  binary64Bits(second.battery_after_j));
    }

} // namespace RTSim
