#include <climits>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <stdexcept>
#include <type_traits>
#include <vector>

// 统一日志系统
#include "../../utils/unified_logger.hpp"

#ifdef _WIN32
              extern "C" {
#include <Python.h>
}
#else
              extern "C" {
#include <Python.h>
}
#endif

namespace RTSim {

    std::mutex EnergyBridge::_instance_mutex;
    EnergyBridge *EnergyBridge::_instance = nullptr;

    namespace {
        struct PyObjectDeleter {
            void operator()(PyObject *object) const noexcept {
                Py_XDECREF(object);
            }
        };
        using PyObjectPtr = std::unique_ptr<PyObject, PyObjectDeleter>;

        class PythonGilGuard {
        public:
            PythonGilGuard() : _state(PyGILState_Ensure()) {}
            ~PythonGilGuard() {
                PyGILState_Release(_state);
            }

            PythonGilGuard(const PythonGilGuard &) = delete;
            PythonGilGuard &operator=(const PythonGilGuard &) = delete;

        private:
            PyGILState_STATE _state;
        };

        [[noreturn]] void pythonConfigError(const std::string &reason) {
            if (Py_IsInitialized()) {
                PyErr_Clear();
            }
            const std::string prefix = "system YAML";
            throw PriorityEnergyConfigError(
                reason.compare(0, prefix.size(), prefix) == 0
                    ? reason
                    : "system YAML: " + reason);
        }

        std::string consumePythonError(const std::string &context) {
            if (!PyErr_Occurred()) {
                return context;
            }

            PyObject *type = nullptr;
            PyObject *value = nullptr;
            PyObject *traceback = nullptr;
            PyErr_Fetch(&type, &value, &traceback);
            PyErr_NormalizeException(&type, &value, &traceback);

            PyObjectPtr owned_type(type);
            PyObjectPtr owned_value(value);
            PyObjectPtr owned_traceback(traceback);
            PyObjectPtr text(
                PyObject_Str(value != nullptr ? value : type));
            if (!text) {
                PyErr_Clear();
                return context;
            }
            const char *utf8 = PyUnicode_AsUTF8(text.get());
            if (!utf8) {
                PyErr_Clear();
                return context;
            }
            const std::string result = context + ": " + utf8;
            PyErr_Clear();
            return result;
        }

        [[noreturn]] void failPythonCall(const std::string &context) {
            pythonConfigError(consumePythonError(context));
        }

        void ensureEnergyManagerImportPath() {
            PyObject *path = PySys_GetObject("path");
            if (!path || !PyList_Check(path)) {
                failPythonCall("cannot access Python sys.path");
            }

            const std::filesystem::path source_root =
                std::filesystem::path(__FILE__)
                    .parent_path()
                    .parent_path()
                    .parent_path();
            PyObjectPtr root(
                PyUnicode_FromString(source_root.string().c_str()));
            if (!root) {
                failPythonCall(
                    "cannot construct energy_manager import path");
            }
            const int contains = PySequence_Contains(path, root.get());
            if (contains < 0) {
                failPythonCall(
                    "cannot inspect energy_manager import path");
            }
            if (contains == 0 && PyList_Insert(path, 0, root.get()) != 0) {
                failPythonCall(
                    "cannot add energy_manager import path");
            }
        }

        PyObject *requiredDictItem(PyObject *dictionary,
                                   const char *key) {
            PyObject *value = PyDict_GetItemString(dictionary, key);
            if (!value) {
                failPythonCall(
                    "Python configuration result is missing '" +
                    std::string(key) + "'");
            }
            return value;
        }

        int pythonCheckedInt(PyObject *value,
                             const std::string &field,
                             bool must_be_positive) {
            if (!PyLong_Check(value) || PyBool_Check(value)) {
                pythonConfigError(field + " must be an integer");
            }
            int overflow = 0;
            const long long result =
                PyLong_AsLongLongAndOverflow(value, &overflow);
            if (overflow != 0 || PyErr_Occurred()) {
                failPythonCall(
                    field + " is outside the C++ int range");
            }
            if (result < INT_MIN || result > INT_MAX) {
                pythonConfigError(
                    field + " is outside the C++ int range");
            }
            if (must_be_positive && result <= 0) {
                pythonConfigError(field + " must be greater than zero");
            }
            return static_cast<int>(result);
        }

        long long pythonLongLong(PyObject *value,
                                 const std::string &field,
                                 bool must_be_positive = false) {
            if (!PyLong_Check(value) || PyBool_Check(value)) {
                pythonConfigError(field + " must be an integer");
            }
            int overflow = 0;
            const long long result =
                PyLong_AsLongLongAndOverflow(value, &overflow);
            if (overflow != 0 || PyErr_Occurred()) {
                failPythonCall(
                    field + " is outside the C++ integer range");
            }
            if (must_be_positive && result <= 0) {
                pythonConfigError(field + " must be greater than zero");
            }
            return result;
        }

        std::uint64_t pythonUnsigned64(PyObject *value,
                                       const std::string &field) {
            if (!PyLong_Check(value) || PyBool_Check(value)) {
                pythonConfigError(field + " must be an unsigned integer");
            }
            const unsigned long long result =
                PyLong_AsUnsignedLongLong(value);
            if (PyErr_Occurred()) {
                failPythonCall(field + " must be an unsigned integer");
            }
            return static_cast<std::uint64_t>(result);
        }

        double pythonDouble(PyObject *value, const std::string &field) {
            if (PyBool_Check(value) ||
                (!PyFloat_Check(value) && !PyLong_Check(value))) {
                pythonConfigError(field + " must be a finite double");
            }
            const double result = PyFloat_Check(value)
                                      ? PyFloat_AsDouble(value)
                                      : PyLong_AsDouble(value);
            if (PyErr_Occurred()) {
                failPythonCall(field + " must be a finite double");
            }
            if (!std::isfinite(result)) {
                pythonConfigError(field + " must be a finite double");
            }
            return result;
        }

        bool pythonBool(PyObject *value, const std::string &field) {
            if (!PyBool_Check(value)) {
                pythonConfigError(field + " must be a boolean");
            }
            return value == Py_True;
        }

        std::string pythonString(PyObject *value,
                                 const std::string &field) {
            if (!PyUnicode_Check(value)) {
                pythonConfigError(field + " must be a string");
            }
            const char *utf8 = PyUnicode_AsUTF8(value);
            if (!utf8) {
                failPythonCall(field + " must be a UTF-8 string");
            }
            return utf8;
        }

        std::uint64_t pythonManagerGeneration(PyObject *manager) {
            PyObjectPtr value(
                PyObject_GetAttrString(manager, "config_generation"));
            if (!value) {
                failPythonCall(
                    "Python energy manager has no config_generation");
            }
            const std::uint64_t generation = pythonUnsigned64(
                value.get(),
                "energy manager config_generation");
            if (generation == 0) {
                pythonConfigError(
                    "energy manager config_generation must be positive");
            }
            return generation;
        }

        HarvestSourceConfig parseHarvestSourceConfig(PyObject *root) {
            if (!PyDict_Check(root)) {
                pythonConfigError("harvest_source must be a mapping");
            }
            const std::string kind = pythonString(
                requiredDictItem(root, "kind"),
                "harvest_source.kind");

            if (kind == "legacy_solar") {
                LegacySolarConfig config;
                config.base_harvesting_power_w = pythonDouble(
                    requiredDictItem(
                        root, "base_harvesting_power_w"),
                    "harvest_source.base_harvesting_power_w");
                if (config.base_harvesting_power_w < 0.0) {
                    pythonConfigError(
                        "harvest_source.base_harvesting_power_w "
                        "must be non-negative");
                }
                config.start_offset_ms = pythonUnsigned64(
                    requiredDictItem(root, "start_offset_ms"),
                    "harvest_source.start_offset_ms");
                config.use_real_solar_data = pythonBool(
                    requiredDictItem(root, "use_real_solar_data"),
                    "harvest_source.use_real_solar_data");
                config.solar_data_file = pythonString(
                    requiredDictItem(root, "solar_data_file"),
                    "harvest_source.solar_data_file");
                if (config.solar_data_file.empty()) {
                    pythonConfigError(
                        "harvest_source.solar_data_file "
                        "must be non-empty");
                }
                config.pv_efficiency = pythonDouble(
                    requiredDictItem(root, "pv_efficiency"),
                    "harvest_source.pv_efficiency");
                config.pv_area_m2 = pythonDouble(
                    requiredDictItem(root, "pv_area_m2"),
                    "harvest_source.pv_area_m2");
                if (config.pv_efficiency <= 0.0 ||
                    config.pv_area_m2 <= 0.0) {
                    pythonConfigError(
                        "harvest_source legacy photovoltaic parameters "
                        "must be greater than zero");
                }
                return config;
            }

            if (kind == "scaled_piecewise") {
                ScaledPiecewiseConfig config;
                config.scale_w = pythonDouble(
                    requiredDictItem(root, "scale_w"),
                    "harvest_source.scale_w");
                if (config.scale_w < 0.0) {
                    pythonConfigError(
                        "harvest_source.scale_w must be non-negative");
                }
                if (config.scale_w == 0.0) {
                    config.scale_w = 0.0;
                }
                PyObject *segments =
                    requiredDictItem(root, "segments");
                if (!PyList_Check(segments) ||
                    PyList_Size(segments) <= 0) {
                    pythonConfigError(
                        "harvest_source.segments must be a "
                        "non-empty sequence");
                }

                std::uint64_t previous_start = 0;
                std::uint64_t previous_end = 0;
                bool has_previous = false;
                const Py_ssize_t count = PyList_Size(segments);
                config.segments.reserve(
                    static_cast<std::size_t>(count));
                for (Py_ssize_t index = 0; index < count; ++index) {
                    PyObject *entry = PyList_GetItem(segments, index);
                    if (!entry) {
                        failPythonCall(
                            "cannot read harvest_source segment");
                    }
                    if (!PyDict_Check(entry)) {
                        pythonConfigError(
                            "harvest_source segment must be a mapping");
                    }
                    PiecewiseSegment segment;
                    const std::string prefix =
                        "harvest_source.segments[" +
                        std::to_string(index) + "]";
                    segment.start_time_ms = pythonUnsigned64(
                        requiredDictItem(entry, "start_time_ms"),
                        prefix + ".start_time_ms");
                    segment.end_time_ms = pythonUnsigned64(
                        requiredDictItem(entry, "end_time_ms"),
                        prefix + ".end_time_ms");
                    segment.multiplier = pythonDouble(
                        requiredDictItem(entry, "multiplier"),
                        prefix + ".multiplier");
                    if (segment.start_time_ms >=
                        segment.end_time_ms) {
                        pythonConfigError(
                            prefix + " must have start < end");
                    }
                    if (segment.multiplier < 0.0) {
                        pythonConfigError(
                            prefix +
                            ".multiplier must be non-negative");
                    }
                    if (segment.multiplier == 0.0) {
                        segment.multiplier = 0.0;
                    }
                    if (has_previous &&
                        segment.start_time_ms <= previous_start) {
                        pythonConfigError(
                            prefix +
                            " is not strictly ordered by start");
                    }
                    if (has_previous &&
                        segment.start_time_ms < previous_end) {
                        pythonConfigError(
                            prefix + " overlaps the previous segment");
                    }
                    previous_start = segment.start_time_ms;
                    previous_end = segment.end_time_ms;
                    has_previous = true;
                    config.segments.push_back(segment);
                }
                return config;
            }

            if (kind == "sampled_trace") {
                SampledTraceConfig config;
                config.file = pythonString(
                    requiredDictItem(root, "file"),
                    "harvest_source.file");
                config.time_column = pythonString(
                    requiredDictItem(root, "time_column"),
                    "harvest_source.time_column");
                config.value_column = pythonString(
                    requiredDictItem(root, "value_column"),
                    "harvest_source.value_column");
                if (config.file.empty() ||
                    config.time_column.empty() ||
                    config.value_column.empty()) {
                    pythonConfigError(
                        "harvest_source trace file and columns "
                        "must be non-empty");
                }

                const std::string value_type = pythonString(
                    requiredDictItem(root, "value_type"),
                    "harvest_source.value_type");
                if (value_type == "electrical_power") {
                    config.value_type =
                        TraceValueType::ElectricalPower;
                } else if (value_type == "irradiance") {
                    config.value_type = TraceValueType::Irradiance;
                } else {
                    pythonConfigError(
                        "harvest_source.value_type is unknown");
                }

                const std::string interpolation = pythonString(
                    requiredDictItem(root, "interpolation"),
                    "harvest_source.interpolation");
                if (interpolation != "zero_order_hold") {
                    pythonConfigError(
                        "harvest_source.interpolation is unknown");
                }
                config.interpolation =
                    TraceInterpolation::ZeroOrderHold;

                const std::string after_trace = pythonString(
                    requiredDictItem(root, "after_trace"),
                    "harvest_source.after_trace");
                if (after_trace != "zero") {
                    pythonConfigError(
                        "harvest_source.after_trace is unknown");
                }
                config.after_trace = TraceAfterEnd::Zero;

                config.panel_area_m2 = pythonDouble(
                    requiredDictItem(root, "panel_area_m2"),
                    "harvest_source.panel_area_m2");
                config.conversion_efficiency = pythonDouble(
                    requiredDictItem(root, "conversion_efficiency"),
                    "harvest_source.conversion_efficiency");
                if (config.value_type == TraceValueType::Irradiance) {
                    if (config.panel_area_m2 <= 0.0 ||
                        config.conversion_efficiency <= 0.0 ||
                        config.conversion_efficiency > 1.0) {
                        pythonConfigError(
                            "harvest_source irradiance conversion "
                            "parameters are invalid");
                    }
                } else if (config.panel_area_m2 != 0.0 ||
                           config.conversion_efficiency != 0.0) {
                    pythonConfigError(
                        "harvest_source electrical power must not "
                        "provide irradiance conversion parameters");
                }
                config.max_file_size_bytes = pythonUnsigned64(
                    requiredDictItem(root, "max_file_size_bytes"),
                    "harvest_source.max_file_size_bytes");
                config.max_rows = pythonUnsigned64(
                    requiredDictItem(root, "max_rows"),
                    "harvest_source.max_rows");
                if (config.max_file_size_bytes == 0 ||
                    config.max_rows == 0) {
                    pythonConfigError(
                        "harvest_source trace limits "
                        "must be greater than zero");
                }
                return config;
            }

            pythonConfigError("harvest_source.kind is unknown");
        }

        void invalidatePythonConfiguration(
            PyObject *module,
            std::uint64_t expected_generation) noexcept {
            PyObjectPtr invalidator(
                PyObject_GetAttrString(module, "_invalidate_config_for_cpp"));
            if (!invalidator || !PyCallable_Check(invalidator.get())) {
                PyErr_Clear();
                return;
            }
            PyObjectPtr generation(
                PyLong_FromUnsignedLongLong(expected_generation));
            if (!generation) {
                PyErr_Clear();
                return;
            }
            PyObjectPtr result(PyObject_CallFunctionObjArgs(
                invalidator.get(), generation.get(), nullptr));
            if (!result) {
                PyErr_Clear();
            }
        }
    } // namespace

    static_assert(
        std::is_nothrow_move_assignable_v<
            ConfigManager::ConfigurationState>,
        "ConfigurationState commit must not throw after pending changes");

    bool pythonConfigCallback(
        const std::string &config_file,
        ConfigManager::ConfigurationState &pending) {
        SCHEDULER_LOG_INFO("调用Python配置回调，配置文件: " + config_file);

        ConfigManager::ConfigurationState candidate = pending;

        if (!Py_IsInitialized()) {
            Py_Initialize();
            if (!Py_IsInitialized()) {
                pythonConfigError("cannot initialize Python");
            }
        }

        PythonGilGuard gil;
        if (PyErr_Occurred()) {
            PyErr_Clear();
        }
        ensureEnergyManagerImportPath();

        PyObjectPtr module(PyImport_ImportModule("energy_manager"));
        if (!module) {
            failPythonCall("cannot import energy_manager");
        }
        invalidatePythonConfiguration(module.get(), 0);
        PyObjectPtr loader(
            PyObject_GetAttrString(module.get(), "load_config_for_cpp"));
        if (!loader) {
            failPythonCall(
                "energy_manager.load_config_for_cpp is unavailable");
        }
        if (!PyCallable_Check(loader.get())) {
            pythonConfigError(
                "energy_manager.load_config_for_cpp is not callable");
        }
        PyObjectPtr path(PyUnicode_FromString(config_file.c_str()));
        if (!path) {
            failPythonCall("configuration path is not valid UTF-8");
        }
        PyObjectPtr result(
            PyObject_CallFunctionObjArgs(loader.get(), path.get(), nullptr));
        if (!result) {
            failPythonCall("cannot load system YAML");
        }
        std::uint64_t config_generation = 0;
        try {
            if (!PyDict_Check(result.get())) {
                pythonConfigError(
                    "Python configuration result must be a mapping");
            }
            config_generation = pythonUnsigned64(
                requiredDictItem(result.get(), "config_generation"),
                "config_generation");
            if (config_generation == 0) {
                pythonConfigError(
                    "config_generation must be greater than zero");
            }
            candidate.config_generation = config_generation;

            candidate.num_cores = pythonCheckedInt(
            requiredDictItem(result.get(), "num_cores"),
            "num_cores",
            true);
        candidate.scheduler_type = pythonString(
            requiredDictItem(result.get(), "scheduler_type"),
            "scheduler_type");
        candidate.base_frequency = pythonDouble(
            requiredDictItem(result.get(), "base_frequency"),
            "base_frequency");
        if (candidate.base_frequency <= 0.0) {
            pythonConfigError(
                "base_frequency must be greater than zero");
        }
        candidate.unit_time = pythonCheckedInt(
            requiredDictItem(result.get(), "unit_time"),
            "unit_time",
            true);
        candidate.initial_energy = pythonDouble(
            requiredDictItem(result.get(), "initial_energy"),
            "initial_energy");
        candidate.max_energy = pythonDouble(
            requiredDictItem(result.get(), "max_energy"),
            "max_energy");
        candidate.base_harvest_rate = pythonDouble(
            requiredDictItem(result.get(), "base_harvest_rate"),
            "base_harvest_rate");
        candidate.start_time_offset =
            static_cast<std::int64_t>(pythonLongLong(
            requiredDictItem(result.get(), "start_time_offset"),
            "start_time_offset"));
        candidate.enable_energy_recovery = pythonBool(
            requiredDictItem(result.get(), "enable_energy_recovery"),
            "enable_energy_recovery");
        candidate.periodic_collection_interval =
            static_cast<std::int64_t>(pythonLongLong(
                requiredDictItem(
                    result.get(),
                    "periodic_collection_interval"),
                "periodic_collection_interval",
                true));
        candidate.base_power = pythonDouble(
            requiredDictItem(result.get(), "base_power"),
            "base_power");

        PyObject *coefficients =
            requiredDictItem(result.get(), "power_coefficients");
        if (!PyDict_Check(coefficients)) {
            pythonConfigError("power_coefficients must be a mapping");
        }
        std::map<std::string, double> parsed_coefficients;
        PyObject *key = nullptr;
        PyObject *value = nullptr;
        Py_ssize_t position = 0;
        while (PyDict_Next(
            coefficients,
            &position,
            &key,
            &value)) {
            const std::string workload =
                pythonString(key, "power_coefficients key");
            parsed_coefficients[workload] = pythonDouble(
                value,
                "power_coefficients." + workload);
        }
        candidate.power_coefficients = std::move(parsed_coefficients);

        PyObject *ratios =
            requiredDictItem(result.get(), "frequency_power_ratios");
        if (!PyDict_Check(ratios)) {
            pythonConfigError("frequency_power_ratios must be a mapping");
        }
        std::map<int, double> parsed_ratios;
        position = 0;
        while (PyDict_Next(ratios, &position, &key, &value)) {
            const int frequency = pythonCheckedInt(
                key,
                "frequency_power_ratios key",
                true);
            parsed_ratios[frequency] = pythonDouble(
                value,
                "frequency_power_ratios." +
                    std::to_string(frequency));
        }
        candidate.frequency_power_ratios = std::move(parsed_ratios);

        PyObject *profile =
            requiredDictItem(result.get(), "priority_energy");
        if (!PyDict_Check(profile)) {
            pythonConfigError("priority_energy must be a mapping");
        }
        PriorityEnergyProfileConfig parsed_profile;
        parsed_profile.enabled = pythonBool(
            requiredDictItem(profile, "enabled"),
            "priority_energy.enabled");
        parsed_profile.profile_id = pythonString(
            requiredDictItem(profile, "profile_id"),
            "priority_energy.profile_id");
        parsed_profile.alpha_w = pythonDouble(
            requiredDictItem(profile, "alpha_w"),
            "priority_energy.alpha_w");
        parsed_profile.horizon_ms = pythonUnsigned64(
            requiredDictItem(profile, "horizon_ms"),
            "priority_energy.horizon_ms");
        parsed_profile.tick_ms = pythonUnsigned64(
            requiredDictItem(profile, "tick_ms"),
            "priority_energy.tick_ms");
            validatePriorityEnergyProfileConfig(parsed_profile);
            candidate.priority_energy_profile = std::move(parsed_profile);
            HarvestSourceConfig parsed_harvest_source =
                parseHarvestSourceConfig(requiredDictItem(
                    result.get(), "harvest_source"));
            candidate.harvest_source_config =
                std::move(parsed_harvest_source);
        } catch (...) {
            invalidatePythonConfiguration(
                module.get(), config_generation);
            throw;
        }

        SCHEDULER_LOG_INFO(
            "严格PyYAML配置已完整暂存，等待ConfigManager原子提交");
        pending = std::move(candidate);
        return true;
    }

    // =====================================================
    // 获取单例实例
    // =====================================================

    EnergyBridge &EnergyBridge::getInstance() {
        std::lock_guard<std::mutex> lock(_instance_mutex);
        if (!_instance) {
            _instance = new EnergyBridge();
        }
        return *_instance;
    }

    void EnergyBridge::ensureConfigCallbackRegistered() {
        ConfigManager::setConfigCallback(pythonConfigCallback);
    }

    // =====================================================
    // 构造函数和析构函数
    // =====================================================
    EnergyBridge::EnergyBridge() :
        _python_energy_manager(nullptr),
        _python_initialized(false),
        _initialized(false),
        _config_generation(0),
        _start_time_offset(0),
        _energy_debug(false),
        _last_energy_check(0),
        _total_calls(0),
        _python_error_count(0), // 现在正确初始化
        _use_fallback_mode(false) {

        const char *env_debug = std::getenv("RTSIM_ENERGY_DEBUG");
        if (env_debug != nullptr && std::string(env_debug) == "1") {
            _energy_debug = true;
        }

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Constructor called (debug mode)");
        }
    }

    EnergyBridge::~EnergyBridge() {
        shutdown();
        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Destructor called");
        }

        std::lock_guard<std::mutex> lock(_instance_mutex);
        if (_instance == this) {
            _instance = nullptr;
        }
    }

    // =====================================================
    // Python初始化管理 - 修复版
    // =====================================================

    bool EnergyBridge::initialize() {
        ensureConfigCallbackRegistered();
        ConfigManager &config = ConfigManager::getInstance();
        const std::uint64_t expected_generation =
            config.getConfigGeneration();
        if (!config.isConfigLoaded() || expected_generation == 0) {
            std::lock_guard<std::mutex> lock(_python_mutex);
            if (Py_IsInitialized()) {
                PythonGilGuard gil;
                invalidateCurrentManagerLocked();
            } else {
                invalidateCurrentManagerLocked();
            }
            SCHEDULER_LOG_ERROR(
                "EnergyBridge: no authoritative ConfigManager "
                "generation is loaded");
            return false;
        }

        try {
            std::lock_guard<std::mutex> lock(_python_mutex);
            if (_initialized && _python_energy_manager != nullptr &&
                _config_generation == expected_generation &&
                Py_IsInitialized()) {
                PythonGilGuard gil;
                if (validateCurrentManagerLocked()) {
                    return true;
                }
            }

            if (!Py_IsInitialized()) {
                Py_Initialize();
                if (!Py_IsInitialized()) {
                    throw PriorityEnergyConfigError(
                        "system YAML: cannot initialize Python");
                }
            }

            PythonGilGuard gil;
            if (PyErr_Occurred()) {
                PyErr_Clear();
            }
            ensureEnergyManagerImportPath();

            PyObjectPtr module(PyImport_ImportModule("energy_manager"));
            if (!module) {
                failPythonCall("cannot import energy_manager");
            }
            PyObjectPtr factory(
                PyObject_GetAttrString(
                    module.get(),
                    "get_energy_manager"));
            if (!factory) {
                failPythonCall(
                    "energy_manager.get_energy_manager is unavailable");
            }
            if (!PyCallable_Check(factory.get())) {
                pythonConfigError(
                    "energy_manager.get_energy_manager is not callable");
            }
            PyObjectPtr generation(
                PyLong_FromUnsignedLongLong(expected_generation));
            if (!generation) {
                failPythonCall(
                    "cannot construct expected config_generation");
            }
            PyObjectPtr manager(
                PyObject_CallFunctionObjArgs(
                    factory.get(),
                    generation.get(),
                    nullptr));
            if (!manager) {
                failPythonCall(
                    "cannot access authoritative Python energy manager");
            }
            if (pythonManagerGeneration(manager.get()) !=
                expected_generation) {
                pythonConfigError(
                    "Python energy manager generation does not match "
                    "ConfigManager");
            }

            Py_XDECREF(
                reinterpret_cast<PyObject *>(_python_energy_manager));
            _python_energy_manager = manager.release();
            _config_generation = expected_generation;
            _start_time_offset = config.getStartTimeOffset();
            _initialized = true;
            _python_initialized = true;
            _python_error_count = 0;
            _use_fallback_mode = false;

            SCHEDULER_LOG_INFO(
                "EnergyBridge: connected to authoritative ConfigManager "
                "configuration");
            return true;
        } catch (const std::exception &e) {
            std::lock_guard<std::mutex> lock(_python_mutex);
            if (Py_IsInitialized()) {
                PythonGilGuard gil;
                invalidateCurrentManagerLocked();
            } else {
                invalidateCurrentManagerLocked();
            }
            SCHEDULER_LOG_ERROR(
                "EnergyBridge: initialization failed: " +
                std::string(e.what()));
            return false;
        }
    }

    bool EnergyBridge::isInitialized() const {
        const ConfigManager &config = ConfigManager::getInstance();
        return _initialized && _python_energy_manager != nullptr &&
               config.isConfigLoaded() && _config_generation != 0 &&
               _config_generation == config.getConfigGeneration();
    }

    void EnergyBridge::finalizePython() {
        if (_python_initialized) {
            _python_initialized = false;
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Python finalized");
            }
        }
    }

    // =====================================================
    // Python参数构建 - 修复版，添加ASAP专用格式
    // =====================================================
    // energy_bridge.cpp - buildPythonArgs函数（完整替换）
    void *EnergyBridge::buildPythonArgs(const std::string &format,
                                        va_list args) {
        if (format.empty()) {
            return PyTuple_New(0);
        }

        if (format == "LdL") {
            PyObjectPtr first(PyLong_FromLongLong(va_arg(args, long long)));
            PyObjectPtr second(PyFloat_FromDouble(va_arg(args, double)));
            PyObjectPtr third(PyLong_FromLongLong(va_arg(args, long long)));
            return first && second && third
                       ? PyTuple_Pack(
                             3, first.get(), second.get(), third.get())
                       : nullptr;
        }
        if (format == "dLL") {
            PyObjectPtr first(PyFloat_FromDouble(va_arg(args, double)));
            PyObjectPtr second(PyLong_FromLongLong(va_arg(args, long long)));
            PyObjectPtr third(PyLong_FromLongLong(va_arg(args, long long)));
            return first && second && third
                       ? PyTuple_Pack(
                             3, first.get(), second.get(), third.get())
                       : nullptr;
        }
        if (format == "bL") {
            PyObject *first = va_arg(args, int) != 0 ? Py_True : Py_False;
            PyObjectPtr second(PyLong_FromLongLong(va_arg(args, long long)));
            return second ? PyTuple_Pack(2, first, second.get()) : nullptr;
        }
        if (format == "ds") {
            PyObjectPtr first(PyFloat_FromDouble(va_arg(args, double)));
            PyObjectPtr second(
                PyUnicode_FromString(va_arg(args, const char *)));
            return first && second
                       ? PyTuple_Pack(2, first.get(), second.get())
                       : nullptr;
        }
        if (format == "dL") {
            PyObjectPtr first(PyFloat_FromDouble(va_arg(args, double)));
            PyObjectPtr second(PyLong_FromLongLong(va_arg(args, long long)));
            return first && second
                       ? PyTuple_Pack(2, first.get(), second.get())
                       : nullptr;
        }
        if (format == "LL") {
            PyObjectPtr first(PyLong_FromLongLong(va_arg(args, long long)));
            PyObjectPtr second(PyLong_FromLongLong(va_arg(args, long long)));
            return first && second
                       ? PyTuple_Pack(2, first.get(), second.get())
                       : nullptr;
        }
        if (format == "sdd") {
            PyObjectPtr first(
                PyUnicode_FromString(va_arg(args, const char *)));
            PyObjectPtr second(PyFloat_FromDouble(va_arg(args, double)));
            PyObjectPtr third(PyFloat_FromDouble(va_arg(args, double)));
            return first && second && third
                       ? PyTuple_Pack(
                             3, first.get(), second.get(), third.get())
                       : nullptr;
        }
        if (format == "L") {
            PyObjectPtr value(PyLong_FromLongLong(va_arg(args, long long)));
            return value ? PyTuple_Pack(1, value.get()) : nullptr;
        }
        if (format == "s") {
            PyObjectPtr value(
                PyUnicode_FromString(va_arg(args, const char *)));
            return value ? PyTuple_Pack(1, value.get()) : nullptr;
        }
        if (format == "d") {
            PyObjectPtr value(PyFloat_FromDouble(va_arg(args, double)));
            return value ? PyTuple_Pack(1, value.get()) : nullptr;
        }

        PyErr_Format(
            PyExc_ValueError,
            "unsupported EnergyBridge argument format: %s",
            format.c_str());
        return nullptr;
    }

    // =====================================================
    // Python方法调用 - 修复版，添加错误恢复
    // =====================================================
    double EnergyBridge::callPythonDoubleMethod(const std::string &method_name,
                                                const std::string &format,
                                                ...) {
        _total_calls++;
        std::lock_guard<std::mutex> lock(_python_mutex);
        if (!Py_IsInitialized()) {
            invalidateCurrentManagerLocked();
            return 0.0;
        }
        PythonGilGuard gil;
        if (!validateCurrentManagerLocked()) {
            return 0.0;
        }

        PyObjectPtr method(PyObject_GetAttrString(
            reinterpret_cast<PyObject *>(_python_energy_manager),
            method_name.c_str()));
        if (!method || !PyCallable_Check(method.get())) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot access callable Python method " + method_name));
            return getFallbackValue(method_name);
        }

        va_list args;
        va_start(args, format);
        PyObjectPtr arguments(reinterpret_cast<PyObject *>(
            buildPythonArgs(format, args)));
        va_end(args);
        if (!arguments) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot build arguments for " + method_name));
            return getFallbackValue(method_name);
        }

        PyObjectPtr result(
            PyObject_CallObject(method.get(), arguments.get()));
        if (!result) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "Python method failed: " + method_name));
            _python_error_count++;
            if (_python_error_count > 10) {
                _use_fallback_mode = true;
            }
            return getFallbackValue(method_name);
        }

        PyObjectPtr numeric;
        PyObject *source = result.get();
        if (!PyFloat_Check(source) && !PyLong_Check(source)) {
            if (!PyNumber_Check(source)) {
                SCHEDULER_LOG_ERROR(
                    "EnergyBridge: Python result is not numeric: " +
                    method_name);
                return getFallbackValue(method_name);
            }
            numeric.reset(PyNumber_Float(source));
            if (!numeric) {
                SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                    "cannot convert Python result for " + method_name));
                return getFallbackValue(method_name);
            }
            source = numeric.get();
        }
        const double converted = PyFloat_AsDouble(source);
        if (PyErr_Occurred()) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot read Python numeric result for " + method_name));
            return getFallbackValue(method_name);
        }

        _python_error_count = 0;
        if (_energy_debug && _total_calls % 100 == 0) {
            SCHEDULER_LOG_DEBUG(
                "EnergyBridge: Called " + method_name + " result: " +
                std::to_string(converted));
        }
        return converted;
    }

    double EnergyBridge::getFallbackValue(const std::string &method_name) {
        // 根据不同方法返回不同的后备值
        if (method_name == "get_current_energy_value") {
            return 3.0; // 返回初始能量3J
        } else if (method_name == "get_harvesting_rate_wrapper") {
            return 0.054; // Legacy fallback value; normalized power is 0.054 W.
        } else if (method_name == "update_energy_continuously_wrapper") {
            return 0.0; // 不收集能量
        } else if (method_name == "calculate_task_energy_cpp") {
            return 0.05; // 默认任务能量
        }
        return 0.0;
    }

    void EnergyBridge::invalidateCurrentManagerLocked() noexcept {
        if (_python_energy_manager != nullptr && Py_IsInitialized()) {
            Py_DECREF(reinterpret_cast<PyObject *>(_python_energy_manager));
        }
        _python_energy_manager = nullptr;
        _initialized = false;
        _python_initialized = false;
        _config_generation = 0;
        if (Py_IsInitialized() && PyErr_Occurred()) {
            PyErr_Clear();
        }
    }

    bool EnergyBridge::validateCurrentManagerLocked() {
        const ConfigManager &config = ConfigManager::getInstance();
        const std::uint64_t current_generation =
            config.getConfigGeneration();
        if (!_initialized || _python_energy_manager == nullptr ||
            !config.isConfigLoaded() || current_generation == 0 ||
            _config_generation == 0 ||
            _config_generation != current_generation) {
            SCHEDULER_LOG_ERROR(
                "EnergyBridge: Python manager generation is stale");
            invalidateCurrentManagerLocked();
            return false;
        }

        try {
            if (pythonManagerGeneration(reinterpret_cast<PyObject *>(
                    _python_energy_manager)) != current_generation) {
                SCHEDULER_LOG_ERROR(
                    "EnergyBridge: Python manager object generation is stale");
                invalidateCurrentManagerLocked();
                return false;
            }
        } catch (const std::exception &error) {
            SCHEDULER_LOG_ERROR(
                "EnergyBridge: cannot validate Python manager generation: " +
                std::string(error.what()));
            invalidateCurrentManagerLocked();
            return false;
        }
        return true;
    }

    bool EnergyBridge::callPythonBoolMethod(const std::string &method_name,
                                            const std::string &format, ...) {
        _total_calls++;
        std::lock_guard<std::mutex> lock(_python_mutex);
        if (!Py_IsInitialized()) {
            invalidateCurrentManagerLocked();
            return false;
        }
        PythonGilGuard gil;
        if (!validateCurrentManagerLocked()) {
            return false;
        }

        PyObjectPtr method(PyObject_GetAttrString(
            reinterpret_cast<PyObject *>(_python_energy_manager),
            method_name.c_str()));
        if (!method || !PyCallable_Check(method.get())) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot access callable Python method " + method_name));
            return false;
        }

        va_list args;
        va_start(args, format);
        PyObjectPtr arguments(reinterpret_cast<PyObject *>(
            buildPythonArgs(format, args)));
        va_end(args);
        if (!arguments) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot build arguments for " + method_name));
            return false;
        }

        PyObjectPtr result(
            PyObject_CallObject(method.get(), arguments.get()));
        if (!result) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "Python method failed: " + method_name));
            _python_error_count++;
            if (_python_error_count > 10) {
                _use_fallback_mode = true;
            }
            return false;
        }

        bool converted = false;
        if (PyBool_Check(result.get())) {
            converted = result.get() == Py_True;
        } else {
            PyObjectPtr numeric;
            PyObject *source = result.get();
            if (!PyLong_Check(source)) {
                if (!PyNumber_Check(source)) {
                    SCHEDULER_LOG_ERROR(
                        "EnergyBridge: Python result is not boolean: " +
                        method_name);
                    return false;
                }
                numeric.reset(PyNumber_Long(source));
                if (!numeric) {
                    SCHEDULER_LOG_ERROR("EnergyBridge: " +
                        consumePythonError(
                            "cannot convert Python result for " +
                            method_name));
                    return false;
                }
                source = numeric.get();
            }
            converted = PyLong_AsLongLong(source) != 0;
            if (PyErr_Occurred()) {
                SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                    "cannot read Python boolean result for " +
                    method_name));
                return false;
            }
        }

        _python_error_count = 0;
        if (_energy_debug && _total_calls % 100 == 0) {
            SCHEDULER_LOG_DEBUG(
                "EnergyBridge: Called " + method_name + " result: " +
                (converted ? "true" : "false"));
        }
        return converted;
    }

    std::string
        EnergyBridge::callPythonStringMethod(const std::string &method_name,
                                             const std::string &format, ...) {
        _total_calls++;
        std::lock_guard<std::mutex> lock(_python_mutex);
        if (!Py_IsInitialized()) {
            invalidateCurrentManagerLocked();
            return "";
        }
        PythonGilGuard gil;
        if (!validateCurrentManagerLocked()) {
            return "";
        }

        PyObjectPtr method(PyObject_GetAttrString(
            reinterpret_cast<PyObject *>(_python_energy_manager),
            method_name.c_str()));
        if (!method || !PyCallable_Check(method.get())) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot access callable Python method " + method_name));
            return getFallbackStringValue(method_name);
        }

        va_list args;
        va_start(args, format);
        PyObjectPtr arguments(reinterpret_cast<PyObject *>(
            buildPythonArgs(format, args)));
        va_end(args);
        if (!arguments) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot build arguments for " + method_name));
            return getFallbackStringValue(method_name);
        }

        PyObjectPtr result(
            PyObject_CallObject(method.get(), arguments.get()));
        if (!result) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "Python method failed: " + method_name));
            _python_error_count++;
            if (_python_error_count > 10) {
                _use_fallback_mode = true;
            }
            return getFallbackStringValue(method_name);
        }

        PyObjectPtr converted;
        PyObject *text = result.get();
        if (!PyUnicode_Check(text)) {
            converted.reset(PyObject_Str(text));
            if (!converted) {
                SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                    "cannot convert Python result for " + method_name));
                return getFallbackStringValue(method_name);
            }
            text = converted.get();
        }
        const char *utf8 = PyUnicode_AsUTF8(text);
        if (!utf8) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot read Python string result for " + method_name));
            return getFallbackStringValue(method_name);
        }

        _python_error_count = 0;
        return utf8;
    }
    std::string
        EnergyBridge::getFallbackStringValue(const std::string &method_name) {
        // 根据不同方法返回不同的后备字符串值
        if (method_name == "get_energy_status_string") {
            return "Energy: 3.0/400.0 J (Fallback Mode)";
        } else if (method_name == "get_detailed_energy_status") {
            return "=== Fallback Energy Status ===\nCurrent Energy: 3.0 J\nMax "
                   "Capacity: 400.0 J\nEnergy Level: CRITICAL\nMode: Fallback "
                   "due to Python communication failure";
        }

        return "";
    }

    void EnergyBridge::shutdown() {
        std::lock_guard<std::mutex> lock(_python_mutex);
        if (Py_IsInitialized()) {
            PythonGilGuard gil;
            invalidateCurrentManagerLocked();
        } else {
            invalidateCurrentManagerLocked();
        }

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: 关闭完成 (总调用次数: " + std::to_string(_total_calls) + ")");
        }
    }

    void EnergyBridge::setStartTimeOffset(int64_t offset) {
        _start_time_offset = offset;

        if (_initialized) {
            callPythonBoolMethod("set_start_time_offset", "L", offset);
        }

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Start time offset set to " + std::to_string(offset) + " ms");
        }
    }

    // energy_bridge.cpp - 修复 getAdjustedTime 函数
    int64_t EnergyBridge::getAdjustedTime(int64_t current_time_ms) const {
        // === 关键修复：统一时间转换逻辑 ===
        // current_time_ms 是仿真时间
        // 加上开始时间偏移得到绝对时间
        int64_t adjusted_time = current_time_ms + _start_time_offset;

        // 调试输出（限制频率）
        static int debug_count = 0;
        if (_energy_debug && debug_count++ < 50) {
            int64_t hour = (adjusted_time / 3600000) % 24;
            int64_t minute = (adjusted_time % 3600000) / 60000;
            int64_t second = (adjusted_time % 60000) / 1000;

            SCHEDULER_LOG_DEBUG("EnergyBridge::getAdjustedTime:");
            SCHEDULER_LOG_DEBUG("  仿真时间: " + std::to_string(current_time_ms) + "ms");
            SCHEDULER_LOG_DEBUG("  时间偏移: " + std::to_string(_start_time_offset) + "ms");
            SCHEDULER_LOG_DEBUG("  绝对时间: " + std::to_string(adjusted_time) + "ms");
            SCHEDULER_LOG_DEBUG("  格式化: " + std::to_string(hour) + ":" + std::to_string(minute) + ":" + std::to_string(second));
        }

        return adjusted_time;
    }
    // =====================================================
    // 能量查询 - 修复版，添加ASAP专用接口
    // =====================================================
    double EnergyBridge::getCurrentEnergy() {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        double energy = callPythonDoubleMethod("get_current_energy_value");

        // 定期检查能量状态
        static int64_t last_check = 0;
        int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();

        if (now - last_check > 5000) { // 每5秒检查一次
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Current energy: " + std::to_string(energy) + " J");
            }
            last_check = now;
        }

        return energy;
    }

    double EnergyBridge::getInitialEnergy() {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        // 从ConfigManager获取初始能量
        return ConfigManager::getInstance().getInitialEnergy();
    }

    int64_t
        EnergyBridge::convertToAbsoluteTime(int64_t simulation_time_ms) const {
        return simulation_time_ms + _start_time_offset;
    }

    int64_t
        EnergyBridge::convertToSimulationTime(int64_t absolute_time_ms) const {
        return absolute_time_ms - _start_time_offset;
    }

    bool EnergyBridge::validateTimeParameters(int64_t simulation_time_ms,
                                              const char *function_name) const {
        if (simulation_time_ms < 0) {
            SCHEDULER_LOG_ERROR("EnergyBridge::" + std::string(function_name) + 
                                ": 无效的仿真时间: " + std::to_string(simulation_time_ms) + " ms");
            return false;
        }
        return true;
    }

    double EnergyBridge::getHarvestingRate(int64_t current_time_ms) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        // === 关键修复：必须转换为绝对时间 ===
        // Python能量管理器需要绝对时间来计算收集率
        int64_t absolute_time_ms = convertToAbsoluteTime(current_time_ms);

        return callPythonDoubleMethod("get_harvesting_rate_wrapper", "L",
                                      absolute_time_ms);
    }

    std::string EnergyBridge::getEnergyStatus() {
        if (!_initialized) {
            return "EnergyBridge: Not initialized";
        }

        std::string status = callPythonStringMethod("get_energy_status_string");

        if (_energy_debug && _total_calls % 50 == 0) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Energy status - " + status);
        }

        return status;
    }

    std::string EnergyBridge::getDetailedEnergyStatus() {
        if (!_initialized) {
            return "EnergyBridge: Not initialized";
        }

        return callPythonStringMethod("get_detailed_energy_status");
    }

    // =====================================================
    // ASAP专用接口 - 新增
    // =====================================================
    bool EnergyBridge::checkAsapScheduling(double required_energy) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: checkAsapScheduling - required=" + std::to_string(required_energy) + "J");
        }

        return callPythonBoolMethod("check_asap_scheduling", "d",
                                    required_energy);
    }

    // =====================================================
    // 能量消耗和收集
    // =====================================================
bool EnergyBridge::consumeEnergy(double energy_joules,
const std::string &task_name) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        // === 关键修复：添加边界检查 ===
        // 1. 检查能量值是否有效
        if (energy_joules <= 0) {
            SCHEDULER_LOG_WARNING("EnergyBridge: 无效的能量消耗值: " + 
                                  std::to_string(energy_joules) + "J");
            return false;
        }

        // 2. 检查当前能量是否足够
        double current_energy = getCurrentEnergy();
        // 🔒 V28.9修复：使用epsilon (1e-6) 避免浮点数精度问题
        if (current_energy < energy_joules - 1e-6) {
            SCHEDULER_LOG_WARNING("EnergyBridge: 能量不足 - 需要: " +
                                  std::to_string(energy_joules) + "J, 当前: " +
                                  std::to_string(current_energy) + "J, 任务: " + task_name);
            return false;
        }

        // 调试输出控制
        static int consume_count = 0;
        consume_count++;

        bool should_log =
            (_energy_debug && (energy_joules > 0.1 ||
                               task_name.find("_asap") != std::string::npos ||
                               task_name.find("_start") != std::string::npos ||
                               consume_count % 100 == 0));

        if (should_log) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: consumeEnergy - energy=" +
                                std::to_string(energy_joules) + "J, task=" + task_name +
                                " (count: " + std::to_string(consume_count) + ")");
        }

        // 3. 调用Python函数进行实际消耗
        bool success = callPythonBoolMethod("consume_energy", "ds", energy_joules,
                                            task_name.c_str());
        
        // 4. 验证消耗后的能量状态
        if (success) {
            double new_energy = getCurrentEnergy();
            double expected_energy = current_energy - energy_joules;
            double diff = abs(new_energy - expected_energy);
            
            if (diff > 0.001) { // 1mJ的容差
                SCHEDULER_LOG_WARNING("EnergyBridge: 能量消耗后状态不一致 - 预期: " + 
                                      std::to_string(expected_energy) + "J, 实际: " + 
                                      std::to_string(new_energy) + "J, 差异: " + 
                                      std::to_string(diff) + "J");
            }
        } else {
            SCHEDULER_LOG_ERROR("EnergyBridge: Python consume_energy调用失败 - 任务: " + task_name);
        }
        
        return success;
    }

    void EnergyBridge::updateEnergyHarvesting(int64_t current_time_ms,
                                              int64_t duration_ms) {
        if (!_initialized) {
            return;
        }

        int64_t adjusted_time = getAdjustedTime(current_time_ms);

        if (_energy_debug && _total_calls % 100 == 0) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: updateEnergyHarvesting - time=" + 
                                std::to_string(current_time_ms) + "ms, duration=" + 
                                std::to_string(duration_ms) + "ms, adjusted_time=" + 
                                std::to_string(adjusted_time) + "ms");
        }

        callPythonDoubleMethod("update_energy_harvesting", "LL", adjusted_time,
                               duration_ms);
    }

    // 确保统一时间传递标准
    // energy_bridge.cpp - 完全重写 updateEnergyContinuously 函数
    double EnergyBridge::updateEnergyContinuously(int64_t simulation_time_ms) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        // 验证时间参数
        if (simulation_time_ms < 0) {
            SCHEDULER_LOG_ERROR("EnergyBridge::updateEnergyContinuously: 无效的仿真时间: " + 
                                std::to_string(simulation_time_ms) + " ms");
            return 0.0;
        }

        // === 关键修复：统一时间处理逻辑 ===
        // 仿真时间 -> 绝对时间转换
        int64_t absolute_time_ms = getAdjustedTime(simulation_time_ms);

        // 调试输出控制
        static int64_t last_logged_time = -1000;
        if (_energy_debug && (simulation_time_ms - last_logged_time > 1000)) {
            int64_t hour = (absolute_time_ms / 3600000) % 24;
            int64_t minute = (absolute_time_ms % 3600000) / 60000;
            int64_t second = (absolute_time_ms % 60000) / 1000;

            SCHEDULER_LOG_DEBUG("EnergyBridge: 能量收集调用 - 仿真时间: " + std::to_string(simulation_time_ms) + "ms, 绝对时间: " + std::to_string(hour) + ":" + std::to_string(minute) + ":" + std::to_string(second) + " (" + std::to_string(absolute_time_ms) + "ms), 偏移: " + std::to_string(_start_time_offset) + "ms");

            last_logged_time = simulation_time_ms;
        }

        // 调用Python函数，传递绝对时间
        double harvested =
            callPythonDoubleMethod("update_energy_continuously_wrapper",
                                   "L", // 格式：长整型
                                   absolute_time_ms); // 传递绝对时间

        return harvested;
    }
    // 修改waitForEnergyRecovery函数，实现ASAP算法恢复逻辑
    bool EnergyBridge::waitForEnergyRecovery(double required_energy,
                                             int64_t current_time_ms,
                                             int64_t max_wait_time_ms) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        // 验证参数
        if (required_energy <= 0.0) {
            SCHEDULER_LOG_ERROR("EnergyBridge: 错误 - 恢复所需能量无效: " + std::to_string(required_energy) + " J");
            return false;
        }

        double current_energy = getCurrentEnergy();

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("========================================");
            SCHEDULER_LOG_DEBUG("EnergyBridge: waitForEnergyRecovery - ASAP算法恢复");
            SCHEDULER_LOG_DEBUG("  需要能量: " + std::to_string(required_energy) + " J");
            SCHEDULER_LOG_DEBUG("  当前能量: " + std::to_string(current_energy) + " J");
            SCHEDULER_LOG_DEBUG("  能量差: " + std::to_string(required_energy - current_energy) + " J");
            SCHEDULER_LOG_DEBUG("  仿真时间: " + std::to_string(current_time_ms) + " ms");
            SCHEDULER_LOG_DEBUG("========================================");
        }

        // 如果能量已足够，立即返回成功
        if (current_energy >= required_energy) {
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: 能量已充足，无需等待！当前: " + 
                                    std::to_string(current_energy) + " J, 需要: " + 
                                    std::to_string(required_energy) + " J");
            }
            return true;
        }

        // === 关键修复：根据ASAP算法计算实际恢复时间 ===
        // 1. 计算能量缺口
        double energy_needed = required_energy - current_energy;
        
        // 2. 获取当前收集率（使用绝对时间）
        int64_t absolute_time_ms = getAdjustedTime(current_time_ms);
        double harvest_rate = getHarvestingRate(current_time_ms);
        
        if (harvest_rate <= 0) {
            SCHEDULER_LOG_WARNING("EnergyBridge: 收集率为0，无法恢复");
            return false;
        }
        
        // 3. 计算实际恢复时间（毫秒）
        double actual_recovery_time_ms = energy_needed / harvest_rate;
        
        // 4. 限制最大等待时间
        int64_t final_recovery_time_ms = static_cast<int64_t>(actual_recovery_time_ms);
        if (final_recovery_time_ms > max_wait_time_ms) {
            SCHEDULER_LOG_WARNING("EnergyBridge: 理论恢复时间" + std::to_string(final_recovery_time_ms) + 
                                  "ms超过最大等待时间" + std::to_string(max_wait_time_ms) + "ms");
            final_recovery_time_ms = max_wait_time_ms;
        }
        
        // 5. 计算恢复结束时间
        int64_t recovery_end_time_ms = current_time_ms + final_recovery_time_ms;
        
        SCHEDULER_LOG_INFO("EnergyBridge: ASAP恢复计算:");
        SCHEDULER_LOG_INFO("  能量缺口: " + std::to_string(energy_needed) + " J");
        SCHEDULER_LOG_INFO("  收集率: " + std::to_string(harvest_rate * 1000) + " J/s");
        SCHEDULER_LOG_INFO("  理论恢复时间: " + std::to_string(actual_recovery_time_ms) + " ms");
        SCHEDULER_LOG_INFO("  实际恢复时间: " + std::to_string(final_recovery_time_ms) + " ms");
        SCHEDULER_LOG_INFO("  恢复结束时间: " + std::to_string(recovery_end_time_ms) + " ms");

        // === 关键修复：设置能量管理器的恢复状态 ===
        // 调用Python函数设置恢复状态
        SCHEDULER_LOG_INFO("EnergyBridge: 正在调用Python的set_recovery_state_wrapper...");
        SCHEDULER_LOG_INFO("  参数: recovery_in_progress=true, recovery_end_time_ms=" + 
                           std::to_string(recovery_end_time_ms));
        
        bool recovery_set = callPythonBoolMethod("set_recovery_state_wrapper", 
                                                 "bL",  // 格式：布尔值 + 长整型
                                                 static_cast<bool>(true),  // recovery_in_progress = true
                                                 static_cast<long long>(recovery_end_time_ms));  // recovery_end_time_ms
        
        if (recovery_set) {
            SCHEDULER_LOG_INFO("EnergyBridge: ✅ 能量恢复状态已成功设置");
        } else {
            SCHEDULER_LOG_WARNING("EnergyBridge: ⚠️ 设置能量恢复状态失败");
        }

        // === 关键修复：根据ASAP算法，返回true表示成功设置了恢复状态 ===
        // 调度器会将此视为"恢复已安排"，然后推进仿真时钟
        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: 恢复状态已设置，调度器将推进" + 
                                std::to_string(final_recovery_time_ms) + "ms");
        }

        return true; // 表示成功设置了恢复状态
    }

    // =====================================================
    // 批量能量检查
    // =====================================================
    bool EnergyBridge::hasSufficientEnergyForBatch(
        const std::vector<std::string> &task_workloads, double duration_ms) {
        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: hasSufficientEnergyForBatch - tasks=" + 
                                std::to_string(task_workloads.size()) + ", duration=" + 
                                std::to_string(duration_ms) + "ms");
        }

        std::lock_guard<std::mutex> lock(_python_mutex);
        if (!Py_IsInitialized()) {
            invalidateCurrentManagerLocked();
            return false;
        }
        PythonGilGuard gil;
        if (!validateCurrentManagerLocked()) {
            return false;
        }

        PyObjectPtr method(PyObject_GetAttrString(
            reinterpret_cast<PyObject *>(_python_energy_manager),
            "has_sufficient_energy_for_batch"));
        if (!method || !PyCallable_Check(method.get())) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot access callable Python batch method"));
            return false;
        }

        PyObjectPtr workloads(PyList_New(
            static_cast<Py_ssize_t>(task_workloads.size())));
        if (!workloads) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot create Python batch workload list"));
            return false;
        }
        for (size_t i = 0; i < task_workloads.size(); ++i) {
            PyObjectPtr workload(
                PyUnicode_FromString(task_workloads[i].c_str()));
            if (!workload) {
                SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                    "cannot create Python batch workload"));
                return false;
            }
            PyList_SET_ITEM(
                workloads.get(),
                static_cast<Py_ssize_t>(i),
                workload.release());
        }

        PyObjectPtr duration(PyFloat_FromDouble(duration_ms));
        if (!duration) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot create Python batch duration"));
            return false;
        }
        PyObjectPtr arguments(
            PyTuple_Pack(2, workloads.get(), duration.get()));
        if (!arguments) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "cannot create Python batch arguments"));
            return false;
        }
        PyObjectPtr result(
            PyObject_CallObject(method.get(), arguments.get()));
        if (!result) {
            SCHEDULER_LOG_ERROR("EnergyBridge: " + consumePythonError(
                "Python batch method failed"));
            return false;
        }
        if (!PyBool_Check(result.get())) {
            SCHEDULER_LOG_ERROR(
                "EnergyBridge: Python batch method returned a non-bool");
            if (PyErr_Occurred()) {
                (void)consumePythonError(
                    "invalid Python batch return type");
            }
            return false;
        }

        return result.get() == Py_True;
    }

    // =====================================================
    // 任务能量计算
    // =====================================================
    double EnergyBridge::calculateTaskEnergy(const std::string &workload_type,
                                             double execution_time_ms,
                                             double frequency_mhz) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        if (_energy_debug && _total_calls % 50 == 0) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: calculateTaskEnergy - workload=" + 
                                workload_type + ", time=" + std::to_string(execution_time_ms) + 
                                "ms, freq=" + std::to_string(frequency_mhz) + "MHz");
        }

        return callPythonDoubleMethod("calculate_task_energy_cpp", "sdd",
                                      workload_type.c_str(), execution_time_ms,
                                      frequency_mhz);
    }

    bool EnergyBridge::hasSufficientEnergy(double required_energy) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        if (_energy_debug && _total_calls % 100 == 0) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: hasSufficientEnergy - required=" + 
                                std::to_string(required_energy) + "J");
        }

        double current_energy = getCurrentEnergy();
        bool sufficient = current_energy >= required_energy;

        if (!sufficient && _energy_debug) {
            SCHEDULER_LOG_WARNING("EnergyBridge: Insufficient energy - required: " + 
                                  std::to_string(required_energy) + " J, available: " + 
                                  std::to_string(current_energy) + " J");
        }

        return sufficient;
    }
    // 在energy_bridge.cpp的EnergyBridge类中添加
    double EnergyBridge::syncEnergyState() {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return 0.0;
        }

        // 调用Python的同步函数
        double current_energy = callPythonDoubleMethod("sync_energy_state");

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: 同步能量状态完成，当前能量: " + 
                                std::to_string(current_energy) + " J");
        }

        return current_energy;
    }

    void EnergyBridge::setEnergyParameters(double initial_energy,
                                           double max_energy) {
        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: setEnergyParameters - initial=" + 
                                std::to_string(initial_energy) + "J, max=" + 
                                std::to_string(max_energy) + "J");
            SCHEDULER_LOG_DEBUG("EnergyBridge: Energy parameters are now managed through config files");
        }
    }

} // namespace RTSim
