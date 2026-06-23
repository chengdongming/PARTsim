#include <chrono>
#include <cstdarg>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <stdexcept>
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

    bool pythonConfigCallback(const std::string &config_file,
                              ConfigManager &config) {
        SCHEDULER_LOG_INFO("调用Python配置回调，配置文件: " + config_file);

        if (!Py_IsInitialized()) {
            SCHEDULER_LOG_ERROR("Python未初始化，无法调用配置回调");
            return false;
        }

        try {
            // 关键修复：构建Python代码，传递配置文件名
            std::string get_config_code_str =
                "import sys\n"
                "sys.path.append('.')\n"
                "sys.path.append('./simconf/systems')\n"
                "import energy_manager\n"
                "\n"
                "try:\n"
                "    print(f'[Python] C++传递的配置文件: \\'' + '" + config_file + "' + '\\'')\n"
                "    manager = energy_manager.get_energy_manager('" + config_file + "')\n"
                "    config_dict = manager.get_config_for_cpp()\n"
                "    print(f'[Python] 配置解析完成: {len(config_dict)} "
                "个参数')\n"
                "    result = config_dict\n"
                "except Exception as e:\n"
                "    print(f'[Python] 配置解析错误: {e}')\n"
                "    result = {}\n"
                "\n"
                "result\n";

            const char *get_config_code = get_config_code_str.c_str();

            PyObject *main_module = PyImport_AddModule("__main__");
            PyObject *globals = PyModule_GetDict(main_module);
            PyObject *locals = PyDict_New();

            PyObject *result =
                PyRun_String(get_config_code, Py_file_input, globals, locals);

            if (!result) {
                PyErr_Print();
                SCHEDULER_LOG_ERROR("Python代码执行失败");
                Py_DECREF(locals);
                return false;
            }

            Py_DECREF(result);

            // 获取结果
            PyObject *pResult = PyDict_GetItemString(locals, "result");
            if (!pResult || !PyDict_Check(pResult)) {
                SCHEDULER_LOG_ERROR("无法获取配置结果");
                Py_DECREF(locals);
                return false;
            }

            // 解析配置字典
            PyObject *key, *value;
            Py_ssize_t pos = 0;

            SCHEDULER_LOG_INFO("解析Python配置...");

            // ========== 关键修复：确保所有配置都正确设置 ==========
            // 先重置所有配置为默认值
            config.setNumCores(4);
            config.setBaseFrequency(1400.0);
            // ⭐ 修复：删除硬编码的unit_time，让配置文件的值生效
            // config.setUnitTime(50);  // 删除硬编码
            config.setInitialEnergy(200.0); // 默认200J
            config.setMaxEnergy(600.0);
            // ⭐ 修复：不再硬编码base_harvest_rate，让Python配置文件的值生效
            // config.setBaseHarvestRate(0.00002);  // 删除硬编码
            config.setStartTimeOffset(0);
            config.setEnableEnergyRecovery(true);
            config.setBasePower(0.5);

            // 修复：不调用不存在的方法，而是重新设置默认值
            // ConfigManager已经设置了默认值，我们只需要覆盖它

            // 先设置默认的工作负载系数
            config.setPowerCoefficient("bzip2", 1.2);
            config.setPowerCoefficient("hash", 0.8);
            config.setPowerCoefficient("encrypt", 1.5);
            config.setPowerCoefficient("decrypt", 1.5);
            config.setPowerCoefficient("control", 0.1);

            // 先设置默认的频率功率比
            config.setFrequencyPowerRatio(1000, 0.7);
            config.setFrequencyPowerRatio(1100, 0.75);
            config.setFrequencyPowerRatio(1200, 0.8);
            config.setFrequencyPowerRatio(1300, 0.85);
            config.setFrequencyPowerRatio(1400, 0.9);
            config.setFrequencyPowerRatio(1500, 0.95);
            config.setFrequencyPowerRatio(1600, 1.0);
            config.setFrequencyPowerRatio(1700, 1.05);
            config.setFrequencyPowerRatio(1800, 1.1);
            config.setFrequencyPowerRatio(1900, 1.15);
            config.setFrequencyPowerRatio(2000, 1.2);
            config.setFrequencyPowerRatio(2100, 1.25);

            while (PyDict_Next(pResult, &pos, &key, &value)) {
                std::string key_name;
                if (PyUnicode_Check(key)) {
                    PyObject *utf8_key = PyUnicode_AsUTF8String(key);
                    if (utf8_key) {
                        const char *key_cstr = PyBytes_AsString(utf8_key);
                        if (key_cstr) {
                            key_name = key_cstr;
                        }
                        Py_DECREF(utf8_key);
                    }
                }

                if (key_name.empty())
                    continue;

                // 根据键名设置配置
                if (key_name == "num_cores" && PyLong_Check(value)) {
                    int num_cores = PyLong_AsLong(value);
                    config.setNumCores(num_cores);
                    SCHEDULER_LOG_INFO("  num_cores: " + std::to_string(num_cores));
                } else if (key_name == "base_frequency" &&
                           (PyLong_Check(value) || PyFloat_Check(value))) {
                    double freq = PyFloat_Check(value) ? PyFloat_AsDouble(value)
                                                        : PyLong_AsLong(value);
                    config.setBaseFrequency(freq);
                    SCHEDULER_LOG_INFO("  base_frequency: " + std::to_string(freq) + " MHz");
                } else if (key_name == "unit_time" && PyLong_Check(value)) {
                    int unit_time = PyLong_AsLong(value);
                    config.setUnitTime(unit_time);
                    SCHEDULER_LOG_INFO("  unit_time: " + std::to_string(unit_time) + " ms");
                } else if (key_name == "expected_task_count" &&
                           PyLong_Check(value)) {
                    int task_count = PyLong_AsLong(value);
                    if (config.getExpectedTaskCount() <= 0) {
                        config.setExpectedTaskCount(task_count);
                        SCHEDULER_LOG_INFO("  expected_task_count: " + std::to_string(task_count));
                    } else {
                        SCHEDULER_LOG_INFO("  expected_task_count ignored, keep loaded taskset count: " + std::to_string(config.getExpectedTaskCount()));
                    }
                } else if (key_name == "initial_energy" &&
                           PyFloat_Check(value)) {
                    double initial_energy = PyFloat_AsDouble(value);
                    config.setInitialEnergy(initial_energy);
                    SCHEDULER_LOG_INFO("  initial_energy: " + std::to_string(initial_energy) + " J");
                } else if (key_name == "max_energy" && PyFloat_Check(value)) {
                    double max_energy = PyFloat_AsDouble(value);
                    config.setMaxEnergy(max_energy);
                    SCHEDULER_LOG_INFO("  max_energy: " + std::to_string(max_energy) + " J");
                } else if (key_name == "base_harvest_rate" &&
                           PyFloat_Check(value)) {
                    double harvest_rate = PyFloat_AsDouble(value);
                    config.setBaseHarvestRate(harvest_rate);
                    SCHEDULER_LOG_INFO("  base_harvest_rate: " + std::to_string(harvest_rate) + " J/ms");
                } else if (key_name == "start_time_offset" &&
                           PyLong_Check(value)) {
                    int64_t offset = PyLong_AsLongLong(value);
                    config.setStartTimeOffset(offset);
                    // ⭐ 关键修复：同时设置EnergyBridge的_start_time_offset
                    EnergyBridge::getInstance().setStartTimeOffset(offset);
                    SCHEDULER_LOG_INFO("  start_time_offset: " + std::to_string(offset) + " ms");
                } else if (key_name == "enable_energy_recovery") {
                    bool enabled = false;
                    if (PyBool_Check(value)) {
                        enabled = (value == Py_True);
                    } else if (PyLong_Check(value)) {
                        enabled = (PyLong_AsLong(value) != 0);
                    }
                    config.setEnergyRecoveryEnabled(enabled);
                    SCHEDULER_LOG_INFO("  enable_energy_recovery: " + std::string(enabled ? "true" : "false"));
                } else if (key_name == "periodic_collection_interval" && PyLong_Check(value)) {
                    int interval = PyLong_AsLong(value);
                    config.setPeriodicCollectionInterval(interval);
                    SCHEDULER_LOG_INFO("  periodic_collection_interval: " + std::to_string(interval) + " ms");
                } else if (key_name == "base_power" && PyFloat_Check(value)) {
                    double base_power = PyFloat_AsDouble(value);
                    config.setBasePower(base_power);
                    SCHEDULER_LOG_INFO("  base_power: " + std::to_string(base_power) + " W");
                } else if (key_name == "power_coefficients" &&
                           PyDict_Check(value)) {
                    PyObject *coeff_key, *coeff_value;
                    Py_ssize_t coeff_pos = 0;

                    while (PyDict_Next(value, &coeff_pos, &coeff_key,
                                       &coeff_value)) {
                        std::string workload;
                        if (PyUnicode_Check(coeff_key)) {
                            PyObject *utf8_coeff_key =
                                PyUnicode_AsUTF8String(coeff_key);
                            if (utf8_coeff_key) {
                                const char *coeff_cstr =
                                    PyBytes_AsString(utf8_coeff_key);
                                if (coeff_cstr) {
                                    workload = coeff_cstr;
                                }
                                Py_DECREF(utf8_coeff_key);
                            }
                        }

                        if (!workload.empty() && PyFloat_Check(coeff_value)) {
                            double coefficient = PyFloat_AsDouble(coeff_value);
                            config.setPowerCoefficient(workload, coefficient);
                            SCHEDULER_LOG_INFO("  " + workload + " coefficient: " + std::to_string(coefficient));
                        }
                    }
                } else if (key_name == "frequency_power_ratios" &&
                           PyDict_Check(value)) {
                    PyObject *freq_key, *freq_value;
                    Py_ssize_t freq_pos = 0;

                    while (
                        PyDict_Next(value, &freq_pos, &freq_key, &freq_value)) {
                        if (PyLong_Check(freq_key) &&
                            PyFloat_Check(freq_value)) {
                            int frequency = PyLong_AsLong(freq_key);
                            double ratio = PyFloat_AsDouble(freq_value);
                            config.setFrequencyPowerRatio(frequency, ratio);
                            SCHEDULER_LOG_INFO("  " + std::to_string(frequency) + " MHz ratio: " + std::to_string(ratio));
                        }
                    }
                }
            }

            Py_DECREF(locals);

            // ========== 关键修复：验证配置一致性 ==========
            SCHEDULER_LOG_INFO("Python配置已加载到C++ ConfigManager");

            // 输出最终的配置状态
            SCHEDULER_LOG_INFO("\n配置汇总:");
            SCHEDULER_LOG_INFO("  初始能量: " + std::to_string(config.getInitialEnergy()) + " J");
            SCHEDULER_LOG_INFO("  最大能量: " + std::to_string(config.getMaxEnergy()) + " J");
            SCHEDULER_LOG_INFO("  基础功耗: " + std::to_string(config.getBasePower()) + " W");
            SCHEDULER_LOG_INFO("  单位时间: " + std::to_string(config.getUnitTime()) + " ms");
            SCHEDULER_LOG_INFO("  核心数: " + std::to_string(config.getNumCores()));

            config.printConfig();

            return true;

        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("配置回调异常: " + std::string(e.what()));
            return false;
        }
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

    // =====================================================
    // 构造函数和析构函数
    // =====================================================
    EnergyBridge::EnergyBridge() :
        _python_energy_manager(nullptr),
        _python_initialized(false),
        _initialized(false),
        _start_time_offset(0),
        _energy_debug(false),
        _last_energy_check(0),
        _total_calls(0),
        _python_error_count(0), // 现在正确初始化
        _use_fallback_mode(false), // 现在正确初始化
        _config_file("") { // 初始化新添加的成员变量

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

    bool EnergyBridge::initialize(const std::string &python_script_path) {
        std::lock_guard<std::mutex> lock(_python_mutex);

        // 🔑 保存配置文件路径
        _config_file = python_script_path;

        if (_initialized) {
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Already initialized");
            }
            return true;
        }

        try {
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: 初始化Python能量管理器...");
            }

            // 检查Python是否已经初始化
            if (!Py_IsInitialized()) {
                // 初始化Python
                Py_Initialize();
                if (!Py_IsInitialized()) {
                    SCHEDULER_LOG_ERROR("EnergyBridge: Failed to initialize Python");
                    return false;
                }

                // 添加Python路径 - 智能检测运行目录
                PyRun_SimpleString("import sys");
                PyRun_SimpleString("import os");
                PyRun_SimpleString("sys.path.append('.')");
                // 如果在build目录，添加父目录以访问energy_manager
                PyRun_SimpleString("if 'build' in os.getcwd().split(os.sep): sys.path.append('..')");
                PyRun_SimpleString("sys.path.append('./simconf/systems')");

                if (_energy_debug) {
                    SCHEDULER_LOG_DEBUG("EnergyBridge: Python初始化成功");
                }
            }

            // 设置配置回调
            ConfigManager::setConfigCallback(pythonConfigCallback);

            // 导入energy_manager模块并创建实例
            // 智能路径处理：自动检测运行目录
            std::string python_code =
                "import sys\n"
                "import os\n"
                "sys.path.append('.')\n"
                // 如果在rtsim目录，添加父目录以访问energy_manager
                "cwd_parts = os.getcwd().split(os.sep)\n"
                "if len(cwd_parts) > 0 and cwd_parts[-1] == 'rtsim':\n"
                "    sys.path.append('..')\n"
                "sys.path.append('./simconf/systems')\n"
                "import energy_manager\n"
                "config_file = '" + python_script_path + "'\n"
                "print(f'[Python] 使用配置文件: {config_file}')\n"
                "manager = energy_manager.get_energy_manager(config_file)\n"
                "print('[Python] 能量管理器加载成功')\n"
                "manager\n";

            PyObject *main_module = PyImport_AddModule("__main__");
            PyObject *globals = PyModule_GetDict(main_module);
            PyObject *locals = PyDict_New();

            PyObject *result =
                PyRun_String(python_code.c_str(), Py_file_input, globals, locals);
            if (!result) {
                PyErr_Print();
                SCHEDULER_LOG_ERROR("EnergyBridge: 执行Python代码失败");
                Py_DECREF(locals);
                return false;
            }

            Py_DECREF(result);

            // 从locals获取manager对象
            _python_energy_manager = PyDict_GetItemString(locals, "manager");
            if (!_python_energy_manager) {
                SCHEDULER_LOG_ERROR("EnergyBridge: 无法获取manager对象");
                Py_DECREF(locals);
                return false;
            }

            Py_INCREF(_python_energy_manager);
            Py_DECREF(locals);

            // === 修复：显式调用Python配置回调以更新ConfigManager ===
            // 🔑 修复：使用传入的配置文件路径，而不是从环境变量读取
            const char *config_file = python_script_path.c_str();
            SCHEDULER_LOG_INFO("EnergyBridge: 调用Python配置回调更新ConfigManager，配置文件: " + std::string(config_file));
            bool config_loaded = pythonConfigCallback(config_file, ConfigManager::getInstance());
            if (config_loaded) {
                SCHEDULER_LOG_INFO("EnergyBridge: ConfigManager已从Python配置更新");
            } else {
                SCHEDULER_LOG_WARNING("EnergyBridge: ConfigManager更新失败，使用默认配置");
            }

            _initialized = true;
            _python_initialized = true;
            _python_error_count = 0;
            _use_fallback_mode = false;

            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Python能量管理器初始化成功");
            }

            return true;

        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("EnergyBridge: 初始化异常: " + std::string(e.what()));
            return false;
        }
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

        PyObject *pArgs = nullptr;
        const char *fmt = format.c_str();

        try {
            // 修复：添加LdLd格式用于ASAP恢复
            if (format == "LdL") {
                long long value1 = va_arg(args, long long);
                double value2 = va_arg(args, double);
                long long value3 = va_arg(args, long long);
                pArgs = PyTuple_New(3);
                PyTuple_SetItem(pArgs, 0, PyLong_FromLongLong(value1));
                PyTuple_SetItem(pArgs, 1, PyFloat_FromDouble(value2));
                PyTuple_SetItem(pArgs, 2, PyLong_FromLongLong(value3));
            } else if (format == "dLL") { // 新增：修复的能量恢复格式
                double value1 = va_arg(args, double);
                long long value2 = va_arg(args, long long);
                long long value3 = va_arg(args, long long);
                pArgs = PyTuple_New(3);
                PyTuple_SetItem(pArgs, 0, PyFloat_FromDouble(value1));
                PyTuple_SetItem(pArgs, 1, PyLong_FromLongLong(value2));
                PyTuple_SetItem(pArgs, 2, PyLong_FromLongLong(value3));
            }
            // ====== 关键修复：添加"bL"格式支持（布尔值 + 长整型）=====
            else if (format == "bL") {
                bool value1 = static_cast<bool>(va_arg(args, int)); // va_arg for bool is int
                long long value2 = va_arg(args, long long);
                pArgs = PyTuple_New(2);
                PyTuple_SetItem(pArgs, 0, value1 ? Py_True : Py_False);
                Py_INCREF(value1 ? Py_True : Py_False);
                PyTuple_SetItem(pArgs, 1, PyLong_FromLongLong(value2));
            }
            // ====== 关键修复：添加"ds"格式支持 ======
            else if (format == "ds") {
                double value1 = va_arg(args, double);
                const char *value2 = va_arg(args, const char *);
                pArgs = PyTuple_New(2);
                PyTuple_SetItem(pArgs, 0, PyFloat_FromDouble(value1));
                PyTuple_SetItem(pArgs, 1, PyUnicode_FromString(value2));
            }
            // ====== 关键修复：添加"dL"格式支持 ======
            else if (format == "dL") {
                double value1 = va_arg(args, double);
                long long value2 = va_arg(args, long long);
                pArgs = PyTuple_New(2);
                PyTuple_SetItem(pArgs, 0, PyFloat_FromDouble(value1));
                PyTuple_SetItem(pArgs, 1, PyLong_FromLongLong(value2));
            }
            // ====== 关键修复：添加"L"格式支持 ======
            else if (format == "L") {
                long long value = va_arg(args, long long);
                pArgs = PyTuple_New(1);
                PyTuple_SetItem(pArgs, 0, PyLong_FromLongLong(value));
            }
            // ====== 关键修复：添加"s"格式支持 ======
            else if (format == "s") {
                const char *value = va_arg(args, const char *);
                pArgs = PyTuple_New(1);
                PyTuple_SetItem(pArgs, 0, PyUnicode_FromString(value));
            }
            // ====== 关键修复：添加"d"格式支持 ======
            else if (format == "d") {
                double value = va_arg(args, double);
                pArgs = PyTuple_New(1);
                PyTuple_SetItem(pArgs, 0, PyFloat_FromDouble(value));
            } else {
                SCHEDULER_LOG_ERROR("EnergyBridge: Unsupported format string: " + format);
                return PyTuple_New(0);
            }
        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Error building Python arguments: " + std::string(e.what()));
            if (pArgs) {
                Py_DECREF(pArgs);
            }
            return PyTuple_New(0);
        }

        return pArgs;
    }

    // =====================================================
    // Python方法调用 - 修复版，添加错误恢复
    // =====================================================
    double EnergyBridge::callPythonDoubleMethod(const std::string &method_name,
                                                const std::string &format,
                                                ...) {
        _total_calls++;

        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized when calling " + method_name);
            return 0.0;
        }

        std::lock_guard<std::mutex> lock(_python_mutex);

        // 检查Python对象状态
        if (!checkPythonObject()) {
            SCHEDULER_LOG_WARNING("EnergyBridge: Python对象无效，尝试重新初始化...");
            if (!reinitializePythonManager()) {
                SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化失败，使用后备方案");
                return getFallbackValue(method_name);
            }
        }

        PyObject *pMethod = nullptr;
        PyObject *pArgs = nullptr;
        PyObject *pResult = nullptr;

        try {
            // 最多重试2次
            int max_retries = 2;
            for (int retry = 0; retry <= max_retries; retry++) {
                pMethod = PyObject_GetAttrString(
                    reinterpret_cast<PyObject *>(_python_energy_manager),
                    method_name.c_str());

                if (!pMethod || !PyCallable_Check(pMethod)) {
                    Py_XDECREF(pMethod);
                    if (retry < max_retries) {
                        SCHEDULER_LOG_WARNING("EnergyBridge: 获取方法失败，重试 " + 
                                              std::to_string(retry + 1) + "/" + 
                                              std::to_string(max_retries) + " - " + 
                                              method_name);
                        reinitializePythonManager();
                        continue;
                    } else {
                        SCHEDULER_LOG_ERROR("EnergyBridge: 获取方法失败: " + method_name);
                        return getFallbackValue(method_name);
                    }
                }
                break;
            }

            // 构建参数
            va_list args;
            va_start(args, format);
            pArgs = reinterpret_cast<PyObject *>(buildPythonArgs(format, args));
            va_end(args);

            if (!pArgs) {
                Py_DECREF(pMethod);
                SCHEDULER_LOG_ERROR("EnergyBridge: 构建参数失败: " + method_name);
                return getFallbackValue(method_name);
            }

            // 执行调用
            pResult = PyObject_CallObject(pMethod, pArgs);

            Py_DECREF(pMethod);
            Py_DECREF(pArgs);

            if (!pResult) {
                if (PyErr_Occurred()) {
                    PyErr_Print();
                    SCHEDULER_LOG_ERROR("EnergyBridge: Python调用异常: " + method_name);
                } else {
                    SCHEDULER_LOG_ERROR("EnergyBridge: 调用失败，无结果: " + method_name);
                }

                _python_error_count++;
                if (_python_error_count > 10) {
                    SCHEDULER_LOG_WARNING("EnergyBridge: Python错误过多，切换到后备模式");
                    _use_fallback_mode = true;
                }

                return getFallbackValue(method_name);
            }

            // 解析结果
            double result = 0.0;
            if (PyFloat_Check(pResult)) {
                result = PyFloat_AsDouble(pResult);
            } else if (PyLong_Check(pResult)) {
                result = static_cast<double>(PyLong_AsLongLong(pResult));
            } else if (PyNumber_Check(pResult)) {
                PyObject *pFloat = PyNumber_Float(pResult);
                if (pFloat) {
                    result = PyFloat_AsDouble(pFloat);
                    Py_DECREF(pFloat);
                }
            } else {
                SCHEDULER_LOG_ERROR("EnergyBridge: 结果不是数字: " + method_name);
                Py_DECREF(pResult);
                return getFallbackValue(method_name);
            }

            Py_DECREF(pResult);

            // 成功调用，重置错误计数
            _python_error_count = 0;

            if (_energy_debug && _total_calls % 100 == 0) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Called " + method_name +
                                    " (total calls: " + std::to_string(_total_calls) + ")" +
                                    " result: " + std::to_string(result));
            }

            return result;

        } catch (const std::exception &e) {
            Py_XDECREF(pMethod);
            Py_XDECREF(pArgs);
            Py_XDECREF(pResult);
            SCHEDULER_LOG_ERROR("EnergyBridge: 异常调用Python方法 " + method_name + ": " + std::string(e.what()));
            return getFallbackValue(method_name);
        }
    }

    double EnergyBridge::getFallbackValue(const std::string &method_name) {
        // 根据不同方法返回不同的后备值
        if (method_name == "get_current_energy_value") {
            return 3.0; // 返回初始能量3J
        } else if (method_name == "get_harvesting_rate_wrapper") {
            return 0.054; // 基础收集率：54W (0.054 J/ms)
        } else if (method_name == "update_energy_continuously_wrapper") {
            return 0.0; // 不收集能量
        } else if (method_name == "calculate_task_energy_cpp") {
            return 0.05; // 默认任务能量
        }
        return 0.0;
    }

    bool EnergyBridge::checkPythonObject() {
        if (!_python_energy_manager) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Python对象为空");
            return false;
        }

        // 检查Python对象是否仍然有效
        PyObject *pType =
            PyObject_Type(reinterpret_cast<PyObject *>(_python_energy_manager));
        if (!pType) {
            SCHEDULER_LOG_ERROR("EnergyBridge: 无法获取Python对象类型");
            return false;
        }

        Py_DECREF(pType);
        return true;
    }

    // 重新初始化Python管理器
    bool EnergyBridge::reinitializePythonManager() {
        std::lock_guard<std::mutex> lock(_python_mutex);

        try {
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: 尝试重新初始化Python管理器...");
            }

            // 清理旧对象
            if (_python_energy_manager) {
                Py_DECREF(reinterpret_cast<PyObject *>(_python_energy_manager));
                _python_energy_manager = nullptr;
            }

            // 重新导入模块
            // 🔑 修复：使用保存的配置文件路径
            std::string python_code =
                "import sys\n"
                "sys.path.append('.')\n"
                "sys.path.append('..')\n"  // 修复：添加父目录以访问energy_manager.py
                "sys.path.append('./simconf/systems')\n"
                "import energy_manager\n"
                "config_file = '" + _config_file + "'\n"
                "print(f'[Python] 重新初始化使用配置文件: {config_file}')\n"
                "manager = energy_manager.get_energy_manager(config_file)\n"
                "print('[Python] 重新初始化能量管理器成功')\n"
                "manager\n";

            PyObject *main_module = PyImport_AddModule("__main__");
            PyObject *globals = PyModule_GetDict(main_module);
            PyObject *locals = PyDict_New();

            PyObject *result =
                PyRun_String(python_code.c_str(), Py_file_input, globals, locals);
            if (!result) {
                PyErr_Print();
                SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化Python代码执行失败");
                Py_DECREF(locals);
                return false;
            }

            Py_DECREF(result);

            // 获取新的manager对象
            _python_energy_manager = PyDict_GetItemString(locals, "manager");
            if (!_python_energy_manager) {
                SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化后无法获取manager对象");
                Py_DECREF(locals);
                return false;
            }

            Py_INCREF(_python_energy_manager);
            Py_DECREF(locals);

            _initialized = true;
            _python_initialized = true;
            _python_error_count = 0;

            // 重新设置时间偏移
            setStartTimeOffset(_start_time_offset);

            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Python管理器重新初始化成功");
            }

            return true;

        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化异常: " + std::string(e.what()));
            return false;
        }
    }

    bool EnergyBridge::callPythonBoolMethod(const std::string &method_name,
                                            const std::string &format, ...) {
        _total_calls++;

        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized when calling " + method_name);
            return false;
        }

        std::lock_guard<std::mutex> lock(_python_mutex);

        // 检查Python对象状态
        if (!checkPythonObject()) {
            SCHEDULER_LOG_WARNING("EnergyBridge: Python对象无效，尝试重新初始化...");
            if (!reinitializePythonManager()) {
                SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化失败，使用后备方案");
                return getFallbackBoolValue(method_name);
            }
        }

        PyObject *pMethod = nullptr;
        PyObject *pArgs = nullptr;
        PyObject *pResult = nullptr;

        try {
            // 最多重试2次
            int max_retries = 2;
            for (int retry = 0; retry <= max_retries; retry++) {
                pMethod = PyObject_GetAttrString(
                    reinterpret_cast<PyObject *>(_python_energy_manager),
                    method_name.c_str());

                if (!pMethod || !PyCallable_Check(pMethod)) {
                    Py_XDECREF(pMethod);
                    if (retry < max_retries) {
                        SCHEDULER_LOG_WARNING("EnergyBridge: 获取方法失败，重试 " + 
                                              std::to_string(retry + 1) + "/" + 
                                              std::to_string(max_retries) + " - " + 
                                              method_name);
                        reinitializePythonManager();
                        continue;
                    } else {
                        SCHEDULER_LOG_ERROR("EnergyBridge: 获取方法失败: " + method_name);
                        return getFallbackBoolValue(method_name);
                    }
                }
                break;
            }

            // 构建参数
            va_list args;
            va_start(args, format);
            pArgs = reinterpret_cast<PyObject *>(buildPythonArgs(format, args));
            va_end(args);

            if (!pArgs) {
                Py_DECREF(pMethod);
                SCHEDULER_LOG_ERROR("EnergyBridge: 构建参数失败: " + method_name);
                return getFallbackBoolValue(method_name);
            }

            // 执行调用
            pResult = PyObject_CallObject(pMethod, pArgs);

            Py_DECREF(pMethod);
            Py_DECREF(pArgs);

            if (!pResult) {
                if (PyErr_Occurred()) {
                    PyErr_Print();
                    SCHEDULER_LOG_ERROR("EnergyBridge: Python调用异常: " + method_name);
                } else {
                    SCHEDULER_LOG_ERROR("EnergyBridge: 调用失败，无结果: " + method_name);
                }

                _python_error_count++;
                if (_python_error_count > 10) {
                    SCHEDULER_LOG_WARNING("EnergyBridge: Python错误过多，切换到后备模式");
                    _use_fallback_mode = true;
                }

                return getFallbackBoolValue(method_name);
            }

            // 解析结果
            bool result = false;
            if (PyBool_Check(pResult)) {
                result = (pResult == Py_True);
            } else if (PyLong_Check(pResult)) {
                result = (PyLong_AsLongLong(pResult) != 0);
            } else if (PyNumber_Check(pResult)) {
                PyObject *pLong = PyNumber_Long(pResult);
                if (pLong) {
                    result = (PyLong_AsLongLong(pLong) != 0);
                    Py_DECREF(pLong);
                }
            } else {
                SCHEDULER_LOG_ERROR("EnergyBridge: 结果不是布尔值: " + method_name);
                Py_DECREF(pResult);
                return getFallbackBoolValue(method_name);
            }

            Py_DECREF(pResult);

            // 成功调用，重置错误计数
            _python_error_count = 0;

            if (_energy_debug && _total_calls % 100 == 0) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Called " + method_name +
                                    " (total calls: " + std::to_string(_total_calls) + ")" +
                                    " result: " + (result ? "true" : "false"));
            }

            return result;

        } catch (const std::exception &e) {
            Py_XDECREF(pMethod);
            Py_XDECREF(pArgs);
            Py_XDECREF(pResult);
            SCHEDULER_LOG_ERROR("EnergyBridge: 异常调用Python方法 " + method_name + ": " + std::string(e.what()));
            return getFallbackBoolValue(method_name);
        }
    }

    bool EnergyBridge::getFallbackBoolValue(const std::string &method_name) {
        // 根据不同方法返回不同的后备布尔值
        if (method_name == "consume_energy") {
            return true; // 假设能量消耗成功，避免死锁
        } else if (method_name == "check_asap_scheduling") {
            // 检查是否有足够能量 - 默认返回false，让调度器等待
            return false;
        } else if (method_name == "wait_for_energy_recovery_wrapper") {
            return false; // 恢复失败，让调度器处理
        } else if (method_name == "has_sufficient_energy") {
            return false; // 默认能量不足
        } else if (method_name == "has_sufficient_energy_for_batch") {
            return false; // 批量能量不足
        } else if (method_name == "load_system_config") {
            return true; // 假设配置加载成功
        }

        return false;
    }

    std::string
        EnergyBridge::callPythonStringMethod(const std::string &method_name,
                                             const std::string &format, ...) {
        _total_calls++;

        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized when calling " + method_name);
            return "";
        }

        std::lock_guard<std::mutex> lock(_python_mutex);

        // 检查Python对象状态
        if (!checkPythonObject()) {
            SCHEDULER_LOG_WARNING("EnergyBridge: Python对象无效，尝试重新初始化...");
            if (!reinitializePythonManager()) {
                SCHEDULER_LOG_ERROR("EnergyBridge: 重新初始化失败，使用后备方案");
                return getFallbackStringValue(method_name);
            }
        }

        PyObject *pMethod = nullptr;
        PyObject *pArgs = nullptr;
        PyObject *pResult = nullptr;

        try {
            // 最多重试2次
            int max_retries = 2;
            for (int retry = 0; retry <= max_retries; retry++) {
                pMethod = PyObject_GetAttrString(
                    reinterpret_cast<PyObject *>(_python_energy_manager),
                    method_name.c_str());

                if (!pMethod || !PyCallable_Check(pMethod)) {
                    Py_XDECREF(pMethod);
                    if (retry < max_retries) {
                        SCHEDULER_LOG_WARNING("EnergyBridge: 获取方法失败，重试 " + 
                                              std::to_string(retry + 1) + "/" + 
                                              std::to_string(max_retries) + " - " + 
                                              method_name);
                        reinitializePythonManager();
                        continue;
                    } else {
                        SCHEDULER_LOG_ERROR("EnergyBridge: 获取方法失败: " + method_name);
                        return getFallbackStringValue(method_name);
                    }
                }
                break;
            }

            // 构建参数
            va_list args;
            va_start(args, format);
            pArgs = reinterpret_cast<PyObject *>(buildPythonArgs(format, args));
            va_end(args);

            if (!pArgs) {
                Py_DECREF(pMethod);
                SCHEDULER_LOG_ERROR("EnergyBridge: 构建参数失败: " + method_name);
                return getFallbackStringValue(method_name);
            }

            // 执行调用
            pResult = PyObject_CallObject(pMethod, pArgs);

            Py_DECREF(pMethod);
            Py_DECREF(pArgs);

            if (!pResult) {
                if (PyErr_Occurred()) {
                    PyErr_Print();
                    SCHEDULER_LOG_ERROR("EnergyBridge: Python调用异常: " + method_name);
                } else {
                    SCHEDULER_LOG_ERROR("EnergyBridge: 调用失败，无结果: " + method_name);
                }

                _python_error_count++;
                if (_python_error_count > 10) {
                    SCHEDULER_LOG_WARNING("EnergyBridge: Python错误过多，切换到后备模式");
                    _use_fallback_mode = true;
                }

                return getFallbackStringValue(method_name);
            }

            // 解析结果
            std::string result;
            if (PyUnicode_Check(pResult)) {
                PyObject *pBytes =
                    PyUnicode_AsEncodedString(pResult, "UTF-8", "strict");
                if (pBytes) {
                    result = std::string(PyBytes_AsString(pBytes));
                    Py_DECREF(pBytes);
                }
            } else if (PyBytes_Check(pResult)) {
                result = std::string(PyBytes_AsString(pResult));
            } else if (PyBool_Check(pResult)) {
                result = (pResult == Py_True) ? "True" : "False";
            } else if (PyLong_Check(pResult) || PyFloat_Check(pResult)) {
                PyObject *pStr = PyObject_Str(pResult);
                if (pStr) {
                    PyObject *pBytes =
                        PyUnicode_AsEncodedString(pStr, "UTF-8", "strict");
                    if (pBytes) {
                        result = std::string(PyBytes_AsString(pBytes));
                        Py_DECREF(pBytes);
                    }
                    Py_DECREF(pStr);
                }
            } else {
                // 尝试转换为字符串
                PyObject *pStr = PyObject_Str(pResult);
                if (pStr) {
                    PyObject *pBytes =
                        PyUnicode_AsEncodedString(pStr, "UTF-8", "strict");
                    if (pBytes) {
                        result = std::string(PyBytes_AsString(pBytes));
                        Py_DECREF(pBytes);
                    }
                    Py_DECREF(pStr);
                }
            }

            Py_DECREF(pResult);

            // 成功调用，重置错误计数
            _python_error_count = 0;

            if (_energy_debug && _total_calls % 100 == 0) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: Called " + method_name +
                                    " (total calls: " + std::to_string(_total_calls) + ")" +
                                    " result: " + result);
            }

            return result;

        } catch (const std::exception &e) {
            Py_XDECREF(pMethod);
            Py_XDECREF(pArgs);
            Py_XDECREF(pResult);
            SCHEDULER_LOG_ERROR("EnergyBridge: 异常调用Python方法 " + method_name + ": " + std::string(e.what()));
            return getFallbackStringValue(method_name);
        }
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

    // =====================================================
    // 创建后备管理器 - 修复版，添加ASAP支持
    // =====================================================
    bool EnergyBridge::createFallbackManager() {
        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Creating fallback manager...");
        }

        // 创建简单的后备管理器
        // 在 energy_bridge.cpp 的 createFallbackManager 函数中修改
        std::string fallback_code =
            "class SimpleFallbackEnergyManager:\n"
            "    def __init__(self):\n"
            "        self.current_energy = 200.0\n"
            "        self.max_energy = 600.0\n"
            "        self.start_time_offset = 0\n"
            "        self.last_update_time = 0\n"
            "        self.total_consumed = 0.0\n"
            "        self.total_harvested = 0.0\n"
            "        self.base_harvest_rate = 0.054  # 54W (300W/m² × 1m² × 0.18 = 54W = 0.054 J/ms)\n"
            "        self.asap_recovery_target = None\n"
            "        print('[SimpleFallback] 初始化完成，能量: 200J, 基础收集率: 0.054 J/ms')\n"
            "    \n"
            "    def load_system_config(self, config_file):\n"
            "        print(f'[SimpleFallback] 加载配置: {config_file}')\n"
            "        return True\n"
            "    \n"
            "    def set_start_time_offset(self, offset):\n"
            "        self.start_time_offset = offset\n"
            "        self.last_update_time = offset\n"
            "        print(f'[SimpleFallback] 设置时间偏移: {offset}ms')\n"
            "    \n"
            "    def get_current_energy_value(self):\n"
            "        return self.current_energy\n"
            "    \n"
            "    def consume_energy(self, energy, task_name):\n"
            "        if energy <= self.current_energy:\n"
            "            self.current_energy -= energy\n"
            "            self.total_consumed += energy\n"
            "            print(f'[SimpleFallback] 消耗能量: {energy}J, 任务: "
            "{task_name}')\n"
            "            return True\n"
            "        print(f'[SimpleFallback] 能量不足: 需要{energy}J, "
            "只有{self.current_energy}J')\n"
            "        return False\n"
            "    \n"
            "    def update_energy_continuously_wrapper(self, "
            "current_time_ms):  # 关键修复：添加self参数\n"
            "        if self.last_update_time == 0:\n"
            "            self.last_update_time = current_time_ms\n"
            "            return 0.0\n"
            "        \n"
            "        time_elapsed = current_time_ms - self.last_update_time\n"
            "        if time_elapsed > 0:\n"
            "            # 基于时间的能量收集\n"
            "            hour = ((current_time_ms + self.start_time_offset) // "
            "3600000) % 24\n"
            "            if hour == 12:  # 中午12点\n"
            "                harvest_multiplier = 5.0\n"
            "            elif 6 <= hour <= 18:  # 白天\n"
            "                harvest_multiplier = 2.0\n"
            "            else:  # 晚上\n"
            "                harvest_multiplier = 0.5\n"
            "            \n"
            "            harvested = self.base_harvest_rate * time_elapsed * "
            "harvest_multiplier\n"
            "            \n"
            "            self.current_energy = min(self.max_energy, "
            "self.current_energy + harvested)\n"
            "            self.total_harvested += harvested\n"
            "            self.last_update_time = current_time_ms\n"
            "            return harvested\n"
            "        return 0.0\n"
            "    \n"
            "    def get_harvesting_rate_wrapper(self, current_time_ms):\n"
            "        hour = ((current_time_ms + self.start_time_offset) // "
            "3600000) % 24\n"
            "        if hour == 12:\n"
            "            return self.base_harvest_rate * 5.0\n"
            "        elif 6 <= hour <= 18:\n"
            "            return self.base_harvest_rate * 2.0\n"
            "        else:\n"
            "            return self.base_harvest_rate * 0.5\n"
            "    \n"
            "    def wait_for_energy_recovery_wrapper(self, current_time_ms, "
            "required_energy, max_wait=10000):\n"
            "        print(f'[SimpleFallback] ASAP恢复: "
            "需要{required_energy}J, 当前{self.current_energy}J')\n"
            "        self.asap_recovery_target = 'asap_task'\n"
            "        \n"
            "        # 简单模拟：等待3个时间单位\n"
            "        for i in range(3):\n"
            "            "
            "self.update_energy_continuously_wrapper(current_time_ms + i * "
            "1000)\n"
            "            if self.current_energy >= required_energy:\n"
            "                self.asap_recovery_target = None\n"
            "                print(f'[SimpleFallback] 恢复成功: "
            "{self.current_energy}J >= {required_energy}J')\n"
            "                return True\n"
            "        \n"
            "        self.asap_recovery_target = None\n"
            "        print(f'[SimpleFallback] 恢复失败: {self.current_energy}J "
            "< {required_energy}J')\n"
            "        return False\n"
            "    \n"
            "    def check_asap_scheduling(self, required_energy):\n"
            "        result = self.current_energy >= required_energy\n"
            "        print(f'[SimpleFallback] ASAP检查: "
            "需要{required_energy}J, 当前{self.current_energy}J, 结果: "
            "{result}')\n"
            "        return result\n"
            "    \n"
            "    def get_energy_status_string(self):\n"
            "        return f'Energy: "
            "{self.current_energy:.1f}/{self.max_energy:.1f} J (Fallback)'\n"
            "\n"
            "_simple_fallback_manager = SimpleFallbackEnergyManager()\n"
            "def get_energy_manager(*args, **kwargs):\n"
            "    return _simple_fallback_manager\n";

        PyRun_SimpleString(fallback_code.c_str());

        // 获取后备管理器
        PyObject *pMain = PyImport_AddModule("__main__");
        PyObject *pFunc = PyObject_GetAttrString(pMain, "get_energy_manager");

        if (pFunc && PyCallable_Check(pFunc)) {
            _python_energy_manager = PyObject_CallObject(pFunc, nullptr);
            if (_python_energy_manager) {
                Py_INCREF(_python_energy_manager);
                _initialized = true;
                if (_energy_debug) {
                    SCHEDULER_LOG_DEBUG("EnergyBridge: 使用简单后备energy管理器");
                }
                return true;
            }
        }

        return false;
    }

    void EnergyBridge::shutdown() {
        std::lock_guard<std::mutex> lock(_python_mutex);

        if (_initialized && _python_energy_manager) {
            if (_energy_debug) {
                SCHEDULER_LOG_DEBUG("EnergyBridge: 关闭Python energy管理器...");
            }

            Py_DECREF(reinterpret_cast<PyObject *>(_python_energy_manager));
            _python_energy_manager = nullptr;
        }

        _initialized = false;

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: 关闭完成 (总调用次数: " + std::to_string(_total_calls) + ")");
        }
    }

    // =====================================================
    // 配置管理
    // =====================================================
    bool EnergyBridge::loadSystemConfig(const std::string &config_file) {
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        _config_file = config_file;

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: Loading system config: " + config_file);
        }

        return callPythonBoolMethod("load_system_config", "s",
                                    config_file.c_str());
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
        if (!_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge: Not initialized");
            return false;
        }

        if (_energy_debug) {
            SCHEDULER_LOG_DEBUG("EnergyBridge: hasSufficientEnergyForBatch - tasks=" + 
                                std::to_string(task_workloads.size()) + ", duration=" + 
                                std::to_string(duration_ms) + "ms");
        }

        std::lock_guard<std::mutex> lock(_python_mutex);

        PyObject *pMethod = PyObject_GetAttrString(
            reinterpret_cast<PyObject *>(_python_energy_manager),
            "has_sufficient_energy_for_batch");

        if (!pMethod || !PyCallable_Check(pMethod)) {
            Py_XDECREF(pMethod);
            SCHEDULER_LOG_ERROR("EnergyBridge: Failed to get has_sufficient_energy_for_batch method");
            return false;
        }

        // 创建工作负载列表
        PyObject *pWorkloads = PyList_New(task_workloads.size());
        for (size_t i = 0; i < task_workloads.size(); ++i) {
            PyList_SetItem(pWorkloads, i,
                           PyUnicode_FromString(task_workloads[i].c_str()));
        }

        // 创建参数元组
        PyObject *pArgs =
            PyTuple_Pack(2, pWorkloads, PyFloat_FromDouble(duration_ms));
        PyObject *pResult = PyObject_CallObject(pMethod, pArgs);

        Py_DECREF(pMethod);
        Py_DECREF(pWorkloads);
        Py_DECREF(pArgs);

        bool result = false;
        if (pResult && PyBool_Check(pResult)) {
            result = (pResult == Py_True);
        }

        Py_XDECREF(pResult);
        return result;
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
