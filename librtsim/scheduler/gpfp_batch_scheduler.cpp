// gpfp_batch_scheduler.cpp - BATCH算法完整修复版
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <metasim/factory.hpp>
#include <mutex>
#include <queue>
#include <regex>
#include <rtsim/kernel.hpp>
#include <rtsim/scheduler/gpfp_batch_scheduler.hpp>
#include <sstream>
#include <vector>

// 确保包含所有必要的头文件
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/yaml.hpp>

// 统一日志系统
#include "../../utils/unified_logger.hpp"
#include <unordered_set>

namespace RTSim {

    // 使用统一的日志系统
    // 静态日志函数已移除，使用统一的日志宏

    // =====================================================
    // BatchTaskActivationSimEvent 实现
    // =====================================================

    BatchTaskActivationSimEvent::BatchTaskActivationSimEvent(
        GPFPBatchScheduler *scheduler, AbsRTTask *task,
        const std::string &task_name, bool is_periodic, int period,
        int64_t planned_time_ms) :
        MetaSim::Event("BatchTaskActivationSimEvent"),
        _scheduler(scheduler),
        _task(task),
        _task_name(task_name),
        _is_periodic(is_periodic),
        _period(period),
        _planned_time_ms(planned_time_ms) {}

    void BatchTaskActivationSimEvent::doit() {
        if (!_task)
            return;

        // 直接调用激活函数
        _scheduler->activateTaskAtExactTime(
            _task, MetaSim::Tick(
                       static_cast<MetaSim::Tick::impl_t>(_planned_time_ms)));

        // 如果是周期性任务，安排下一次激活
        if (_is_periodic && _period > 0) {
            int64_t next_activation = _planned_time_ms + _period;
            _scheduler->schedulePreciseActivationEvent(_task, next_activation);
        }

        // 记录日志
        int64_t current_ms = static_cast<int64_t>(SIMUL.getTime());
        if (current_ms != _planned_time_ms) {
            std::cout << "[BATCH-WARNING] 激活事件时间偏差: " + _task_name +
                             " 计划=" + std::to_string(_planned_time_ms) +
                             "ms" + " 实际=" + std::to_string(current_ms) +
                             "ms" + " 偏差=" +
                             std::to_string(current_ms - _planned_time_ms) +
                             "ms"
                      << std::endl;
        } else {
            std::cout << "[BATCH-INFO] ✅ 精确仿真事件激活: " + _task_name +
                             " @ " + std::to_string(_planned_time_ms) + "ms"
                      << std::endl;
        }

        // 激活后立即调度
        if (!_scheduler->_active_tasks.empty()) {
            _scheduler->schedule();
        }
    }

    // =====================================================
    // GPFPBatchTaskModel 实现
    // =====================================================

    GPFPBatchTaskModel::GPFPBatchTaskModel(AbsRTTask *t, int period, int wcet,
                                           const std::string &workload_type,
                                           MetaSim::Tick arrival_offset) :
        TaskModel(t),
        _period(period),
        _wcet(wcet),
        _workload_type(workload_type),
        _arrival_offset(arrival_offset) {
        setPeriod(period);
    }

    GPFPBatchTaskModel::~GPFPBatchTaskModel() {}

    MetaSim::Tick GPFPBatchTaskModel::getPriority() const {
        return _rm_priority;
    }

    void GPFPBatchTaskModel::changePriority(MetaSim::Tick p) {
        _rm_priority = p;
    }

    // 添加setPeriod方法
    void GPFPBatchTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = -period; // RM优先级：周期越小，优先级越高
    }

    // =====================================================
    // GPFPBatchScheduler 实现
    // =====================================================

    GPFPBatchScheduler::GPFPBatchScheduler() :
        Scheduler(),
        _num_cores(4),
        _current_frequency(1400.0),
        _unit_time(50),
        _strict_priority(true),
        _energy_stop_policy(true),
        _enable_energy_recovery(true),
        _recovery_in_progress(false),
        _consecutive_waits(0),
        _start_time_offset(0),
        _schedule_count(0),
        _recovery_target(nullptr),
        _recovery_required_energy(0.0),
        _recovery_start_time(0),
        _recovery_end_time(0),
        _last_schedule_time(0),
        _total_debug_count(0),
        _config_loaded(false),
        _delayed_initialization_done(false),
        _need_delayed_init(false) {
        SCHEDULER_LOG_INFO("🚀 GPFP_BATCH Scheduler: 初始化开始");

        // 1. 从环境变量获取配置文件名
        const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
        std::string config_file =
            config_file_env ? config_file_env : "gpfp_system.yml";
        SCHEDULER_LOG_INFO("  配置文件: " + config_file);

        // 2. 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(".");
        if (!bridge_initialized) {
            SCHEDULER_LOG_ERROR("EnergyBridge 初始化失败");
        }

        // 3. 加载系统配置
        ConfigManager &config = ConfigManager::getInstance();
        if (!config.loadSystemConfig(config_file)) {
            SCHEDULER_LOG_WARNING("无法从配置文件加载配置，使用默认值");
        }

        // 4. 从ConfigManager获取配置
        _num_cores = config.getNumCores();
        _current_frequency = config.getBaseFrequency();
        _unit_time = config.getUnitTime();
        _start_time_offset = config.getStartTimeOffset();
        _enable_energy_recovery = config.isEnergyRecoveryEnabled();

        // 5. 检查环境变量中的开始时间偏移
        const char *env_offset = std::getenv("START_TIME_OFFSET");
        if (env_offset != nullptr) {
            try {
                std::string env_str = std::string(env_offset);
                if (!env_str.empty()) {
                    int64_t offset_value = std::stoll(env_str);
                    MetaSim::Tick env_tick = MetaSim::Tick(
                        static_cast<MetaSim::Tick::impl_t>(offset_value));

                    // 环境变量优先级最高
                    _start_time_offset = env_tick;
                    config.setStartTimeOffset(_start_time_offset);

                    SCHEDULER_LOG_INFO("  从环境变量设置开始时间偏移: " +
                             std::to_string(
                                 static_cast<int64_t>(_start_time_offset)) +
                             " ms");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_ERROR("解析环境变量START_TIME_OFFSET失败: " +
                          std::string(e.what()));
            }
        }

        // 6. 如果没有设置时间偏移，使用配置文件中的值（默认为0）
        if (_start_time_offset == 0) {
            _start_time_offset =
                MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(0));
            config.setStartTimeOffset(_start_time_offset);
            SCHEDULER_LOG_INFO("  使用配置文件中的开始时间: " + std::to_string(static_cast<int64_t>(_start_time_offset)) + " ms");
        }

        // 7. 将开始时间偏移设置到EnergyBridge
        if (bridge_initialized) {
            EnergyBridge::getInstance().setStartTimeOffset(_start_time_offset);
            SCHEDULER_LOG_INFO("  时间偏移已设置到EnergyBridge: " +
                     std::to_string(static_cast<int64_t>(_start_time_offset)) +
                     " ms");
        }

        // 8. 初始化功率模型
        initializePowerModel();

        // 9. 确保核心分配映射初始化
        for (int i = 0; i < _num_cores; ++i) {
            _core_assignments[i] = nullptr;
        }

        // 10. 加载任务配置
        const char *task_file_env = std::getenv("TASKSET_CONFIG_PATH");
        if (task_file_env) {
            loadTasksFromConfig(task_file_env);
        } else {
            loadTasksFromConfig("custom_energy_tasks.yml");
        }

        // 11. 初始化统计信息
        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_recovery_waits = 0;
        _stats.total_batch_executions = 0;
        _stats.total_partial_batches = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_batch_energy_required = 0.0;

        SCHEDULER_LOG_INFO("🚀 GPFP_BATCH Scheduler: 初始化完成");
        SCHEDULER_LOG_INFO("  核心数: " + std::to_string(_num_cores));
        SCHEDULER_LOG_INFO("  频率: " + std::to_string(_current_frequency) + " MHz");
        SCHEDULER_LOG_INFO("  单位时间: " + std::to_string(_unit_time) + " ms");
        SCHEDULER_LOG_INFO("  开始时间偏移: " +
                 std::to_string(static_cast<int64_t>(_start_time_offset)) +
                 " ms");
        SCHEDULER_LOG_INFO("  能量恢复: " +
                 std::string(_enable_energy_recovery ? "启用" : "禁用"));
        SCHEDULER_LOG_INFO("  严格优先级: " +
                 std::string(_strict_priority ? "启用" : "禁用"));

        // 12. 安排0ms的立即激活
        SCHEDULER_LOG_INFO("安排0ms的立即激活事件...");
        MetaSim::Tick init_time = SIMUL.getTime();
        initializeTaskActivation();
        checkScheduledActivations(init_time);
        processPreciseActivations(0);

        // 检查并激活所有应该立即激活的任务
        forceImmediateActivationAllTasks();

        // 如果有任务在0ms激活，立即调度一次
        if (!_active_tasks.empty()) {
            SCHEDULER_LOG_INFO("发现" + std::to_string(_active_tasks.size()) +
                     "个任务在0ms激活，立即调度");
            schedule();
        }
    }

    GPFPBatchScheduler::GPFPBatchScheduler(
        const std::vector<std::string> &params) :
        Scheduler(),
        _num_cores(4),
        _current_frequency(1400.0),
        _unit_time(50),
        _strict_priority(true),
        _energy_stop_policy(true),
        _enable_energy_recovery(true),
        _recovery_in_progress(false),
        _consecutive_waits(0),
        _start_time_offset(0),
        _recovery_target(nullptr),
        _recovery_required_energy(0.0),
        _recovery_start_time(0),
        _recovery_end_time(0),
        _schedule_count(0),
        _last_schedule_time(0),
        _total_debug_count(0),
        _config_loaded(false),
        _delayed_initialization_done(false),
        _need_delayed_init(false) {
        SCHEDULER_LOG_INFO("🚀 GPFP_BATCH Scheduler: 带参数初始化");

        // 1. 从ConfigManager获取基础配置
        ConfigManager &config = ConfigManager::getInstance();
        _num_cores = config.getNumCores();
        _current_frequency = config.getBaseFrequency();
        _unit_time = config.getUnitTime();
        _start_time_offset = config.getStartTimeOffset();
        _enable_energy_recovery = config.isEnergyRecoveryEnabled();

        // 2. 解析传入的参数
        if (!params.empty()) {
            parseBatchParams(params);
        }

        // 3. 检查环境变量中的开始时间偏移
        const char *env_offset = std::getenv("START_TIME_OFFSET");
        if (env_offset != nullptr) {
            try {
                std::string env_str = std::string(env_offset);
                if (!env_str.empty()) {
                    int64_t offset_value = std::stoll(env_str);
                    MetaSim::Tick env_tick = MetaSim::Tick(
                        static_cast<MetaSim::Tick::impl_t>(offset_value));

                    if (_start_time_offset != env_tick) {
                        _start_time_offset = env_tick;
                        config.setStartTimeOffset(_start_time_offset);
                        SCHEDULER_LOG_INFO("  从环境变量覆盖开始时间偏移: " +
                                 std::to_string(
                                     static_cast<int64_t>(_start_time_offset)) +
                                 " ms");
                    }
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_ERROR("解析环境变量START_TIME_OFFSET失败: " +
                          std::string(e.what()));
            }
        }

        // 4. 如果没有设置时间偏移，使用配置文件中的值（默认为0）
        if (_start_time_offset == 0) {
            _start_time_offset =
                MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(0));
            config.setStartTimeOffset(_start_time_offset);
            SCHEDULER_LOG_INFO("  使用配置文件中的开始时间: " + std::to_string(static_cast<int64_t>(_start_time_offset)) + " ms");
        }

        // 5. 初始化功率模型
        initializePowerModel();

        // 6. 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(".");
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("EnergyBridge 初始化成功");
            EnergyBridge::getInstance().setStartTimeOffset(_start_time_offset);
            SCHEDULER_LOG_INFO("  时间偏移已设置到EnergyBridge: " +
                     std::to_string(static_cast<int64_t>(_start_time_offset)) +
                     " ms");
        } else {
            SCHEDULER_LOG_ERROR("EnergyBridge 初始化完全失败，使用本地能量管理");
        }

        // 7. 确保核心分配映射初始化
        for (int i = 0; i < _num_cores; ++i) {
            _core_assignments[i] = nullptr;
        }

        // 8. 加载任务配置
        const char *task_file_env = std::getenv("TASKSET_CONFIG_PATH");
        if (task_file_env) {
            loadTasksFromConfig(task_file_env);
        } else {
            loadTasksFromConfig("custom_energy_tasks.yml");
        }

        // 9. 初始化统计信息
        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_recovery_waits = 0;
        _stats.total_batch_executions = 0;
        _stats.total_partial_batches = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_batch_energy_required = 0.0;

        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 批量调度模式初始化完成");
        SCHEDULER_LOG_INFO("  核心数: " + std::to_string(_num_cores));
        SCHEDULER_LOG_INFO("  单位时间: " + std::to_string(_unit_time) + " ms");
        SCHEDULER_LOG_INFO("  开始时间偏移: " +
                 std::to_string(static_cast<int64_t>(_start_time_offset)) +
                 " ms");

        // 10. 验证能量计算
        validateEnergyCalculations();
        validateConfiguration();

        // 11. 安排0ms的立即激活
        SCHEDULER_LOG_INFO("安排0ms的立即激活事件...");
        MetaSim::Tick init_time = SIMUL.getTime();
        initializeTaskActivation();
        checkScheduledActivations(init_time);
        processPreciseActivations(0);

        // 检查并激活所有应该立即激活的任务
        forceImmediateActivationAllTasks();

        // 如果有任务在0ms激活，立即调度一次
        if (!_active_tasks.empty()) {
            SCHEDULER_LOG_INFO("发现" + std::to_string(_active_tasks.size()) +
                     "个任务在0ms激活，立即调度");
            schedule();
        }
    }

    void GPFPBatchScheduler::initializePowerModel() {
        SCHEDULER_LOG_INFO("功率模型初始化 - 从ConfigManager获取参数");

        ConfigManager &config = ConfigManager::getInstance();
        _power_coefficients = config.getAllPowerCoefficients();
        _base_power = config.getBasePower();
        _frequency_power_ratios = config.getAllFrequencyRatios();

        SCHEDULER_LOG_INFO("  基础功耗: " + std::to_string(_base_power) + " W");
        for (const auto &pair : _power_coefficients) {
            SCHEDULER_LOG_INFO("  " + pair.first +
                     " 功率系数: " + std::to_string(pair.second) + " W");
        }

        validateEnergyCalculations();
    }

    void GPFPBatchScheduler::parseBatchParams(
        const std::vector<std::string> &params) {
        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 开始解析参数，参数数量: " +
                 std::to_string(params.size()));

        for (size_t i = 0; i < params.size(); ++i) {
            std::string param = params[i];
            std::istringstream iss(param);
            std::string key, value;

            if (std::getline(iss, key, '=') && std::getline(iss, value)) {
                key.erase(0, key.find_first_not_of(" \t"));
                key.erase(key.find_last_not_of(" \t") + 1);
                value.erase(0, value.find_first_not_of(" \t"));
                value.erase(value.find_last_not_of(" \t") + 1);

                try {
                    if (key == "num_cores") {
                        _num_cores = std::stoi(value);
                        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 核心数设置为: " +
                                 std::to_string(_num_cores));
                    } else if (key == "base_frequency") {
                        _current_frequency = std::stod(value);
                        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 基础频率设置为: " +
                                 std::to_string(_current_frequency) + " MHz");
                    } else if (key == "unit_time") {
                        _unit_time = std::stoi(value);
                        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 单位时间设置为: " +
                                 std::to_string(_unit_time) + " ms");
                    } else if (key == "strict_priority") {
                        _strict_priority = (value == "true" || value == "1");
                        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 严格优先级: " +
                                 std::string(_strict_priority ? "是" : "否"));
                    } else if (key == "energy_stop_policy") {
                        _energy_stop_policy = (value == "true" || value == "1");
                        SCHEDULER_LOG_INFO(
                            "GPFP_BATCH Scheduler: 能量停止策略: " +
                            std::string(_energy_stop_policy ? "启用" : "禁用"));
                    } else if (key == "enable_energy_recovery") {
                        _enable_energy_recovery =
                            (value == "true" || value == "1");
                        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: 能量恢复: " +
                                 std::string(_enable_energy_recovery ? "启用"
                                                                     : "禁用"));
                    } else if (key == "start_time_offset") {
                        if (!value.empty()) {
                            bool is_numeric = true;
                            for (char c : value) {
                                if (!std::isdigit(c) && c != '-') {
                                    is_numeric = false;
                                    break;
                                }
                            }

                            if (is_numeric) {
                                long long offset_value = std::stoll(value);
                                _start_time_offset = MetaSim::Tick(
                                    static_cast<MetaSim::Tick::impl_t>(
                                        offset_value));
                                EnergyBridge::getInstance().setStartTimeOffset(
                                    _start_time_offset);
                                SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: "
                                         "从参数设置开始时间偏移: " +
                                         std::to_string(static_cast<int64_t>(
                                             _start_time_offset)) +
                                         " ms");
                            }
                        }
                    }
                } catch (const std::exception &e) {
                    SCHEDULER_LOG_ERROR("GPFP_BATCH Scheduler: 参数解析错误: " +
                              std::string(e.what()));
                }
            }
        }
    }

    // =====================================================
    // 解析辅助方法
    // =====================================================

    int GPFPBatchScheduler::extractPeriodFromTaskName(
        const std::string &task_name) const {
        std::regex pattern(R"(DL = T (\d+))");
        std::smatch match;

        if (std::regex_search(task_name, match, pattern) && match.size() > 1) {
            try {
                return std::stoi(match[1].str());
            } catch (...) {
                SCHEDULER_LOG_ERROR("Failed to parse period from: " + task_name);
            }
        }

        size_t pos = task_name.find("T ");
        if (pos != std::string::npos) {
            size_t start = pos + 2;
            size_t end = task_name.find(' ', start);
            if (end != std::string::npos) {
                std::string period_str = task_name.substr(start, end - start);
                try {
                    return std::stoi(period_str);
                } catch (...) {
                }
            }
        }

        SCHEDULER_LOG_ERROR("Could not extract period from task name: " + task_name);
        return 1000;
    }

    int GPFPBatchScheduler::extractWCETFromTaskName(
        const std::string &task_name) const {
        std::regex pattern(R"(WCET\(abs\) (\d+))");
        std::smatch match;

        if (std::regex_search(task_name, match, pattern) && match.size() > 1) {
            try {
                return std::stoi(match[1].str());
            } catch (...) {
                SCHEDULER_LOG_ERROR("Failed to parse WCET from: " + task_name);
            }
        }

        size_t pos = task_name.find("WCET(abs) ");
        if (pos != std::string::npos) {
            size_t start = pos + 10;
            size_t end = task_name.find(' ', start);
            if (end == std::string::npos)
                end = task_name.length();

            std::string wcet_str = task_name.substr(start, end - start);
            try {
                return std::stoi(wcet_str);
            } catch (...) {
            }
        }

        SCHEDULER_LOG_ERROR("Could not extract WCET from task name: " + task_name);
        return 100;
    }

    std::string GPFPBatchScheduler::extractWorkloadTypeFromTaskName(
        const std::string &task_name) const {
        std::vector<std::string> workload_types = {"encrypt", "decrypt", "hash",
                                                   "bzip2", "control"};
        for (const auto &workload : workload_types) {
            if (task_name.find(workload) != std::string::npos) {
                return workload;
            }
        }

        static const std::map<int, std::string> workload_map = {
            {0, "hash"},    {1, "bzip2"},   {2, "control"},  {3, "bzip2"},
            {4, "encrypt"}, {5, "decrypt"}, {6, "control"},  {7, "hash"},
            {8, "control"}, {9, "bzip2"},   {10, "control"}, {11, "control"}};

        std::regex pattern(R"(task_(\d+))");
        std::smatch match;

        if (std::regex_search(task_name, match, pattern) && match.size() > 1) {
            try {
                int task_index = std::stoi(match[1].str());
                auto it = workload_map.find(task_index);
                if (it != workload_map.end()) {
                    return it->second;
                }
            } catch (...) {
            }
        }

        SCHEDULER_LOG_WARNING("Could not determine workload type for task: " + task_name +
                    ", using default 'control'");
        return "control";
    }

    std::string GPFPBatchScheduler::getTaskShortName(AbsRTTask *task) const {
        if (!task)
            return "null";
        std::string full_name = task->toString();

        std::regex pattern(R"(task_\d+)");
        std::smatch match;
        if (std::regex_search(full_name, match, pattern)) {
            return match[0].str();
        }

        return full_name;
    }

    std::unique_ptr<GPFPBatchScheduler> GPFPBatchScheduler::createInstance(
        const std::vector<std::string> &params) {
        return std::unique_ptr<GPFPBatchScheduler>(
            new GPFPBatchScheduler(params));
    }

    // =====================================================
    // BATCH核心算法辅助方法 - 新增/修改
    // =====================================================

    double GPFPBatchScheduler::calculateBatchEnergyRequired(
        const std::vector<AbsRTTask *> &tasks) const {
        double total_energy = 0.0;

        for (AbsRTTask *task : tasks) {
            double task_energy = getUnitTimeEnergy(task);
            total_energy += task_energy;

            SCHEDULER_LOG_DEBUG("  批量能量计算: " + getTaskShortName(task) +
                      " 需要: " + std::to_string(task_energy) + " J");
        }

        SCHEDULER_LOG_DEBUG("批量总能量需求: " + std::to_string(total_energy) + " J");
        return total_energy;
    }

    // =====================================================
    // 新增辅助函数：处理已完成的任务
    // =====================================================
    void GPFPBatchScheduler::processCompletedTasks() {
        std::vector<AbsRTTask *> tasks_to_complete;

        // 收集所有剩余时间<=0且未完成的任务
        for (auto &[task, remaining] : _task_remaining_time) {
            if (remaining <= 0 && !isTaskCompleted(task)) {
                tasks_to_complete.push_back(task);
            }
        }

        // 处理所有需要完成的任务
        for (AbsRTTask *task : tasks_to_complete) {
            std::string task_name = getTaskShortName(task);
            SCHEDULER_LOG_INFO("🎉 检测到任务完成: " + task_name + " (剩余时间=" +
                     std::to_string(_task_remaining_time[task]) + "ms)");
            completeTaskExecution(task);
        }
    }

    // =====================================================
    // 新增辅助函数：统一处理任务执行
    // =====================================================
    void GPFPBatchScheduler::executeSelectedTasks(
        const std::vector<AbsRTTask *> &tasks_to_run) {
        for (AbsRTTask *task : tasks_to_run) {
            std::string task_name = getTaskShortName(task);
            double unit_energy = getUnitTimeEnergy(task);

            // 1. 检查任务状态
            if (!isTaskActive(task)) {
                SCHEDULER_LOG_DEBUG("任务 " + task_name + " 不在活跃集合中，跳过执行");
                continue;
            }

            // 2. 检查任务剩余时间
            auto remaining_it = _task_remaining_time.find(task);
            if (remaining_it == _task_remaining_time.end()) {
                SCHEDULER_LOG_DEBUG("任务 " + task_name + " 剩余时间未初始化");
                continue;
            }

            int &remaining = remaining_it->second;

            // === 关键修复：防止剩余时间变为负数 ===
            if (remaining <= 0) {
                SCHEDULER_LOG_DEBUG("任务 " + task_name + " 剩余时间已耗尽");

                // 标记为已完成
                _task_remaining_time[task] = 0;

                // 检查是否是周期性任务
                int period = _task_periods[task];
                if (period > 0) {
                    // 周期性任务：完成当前周期，等待下一个周期
                    SCHEDULER_LOG_INFO("周期性任务 " + task_name + " 当前周期完成");
                    completeTaskExecution(task);
                } else {
                    // 非周期性任务：永久完成
                    SCHEDULER_LOG_INFO("非周期性任务 " + task_name + " 完成");
                    _completed_tasks.insert(task);
                    _active_tasks.erase(task);
                }
                continue;
            }

            // 3. 能量消耗
            if (consumeEnergy(unit_energy, task_name + "_batch")) {
                // 4. 更新任务剩余时间
                if (remaining >= _unit_time) {
                    remaining -= _unit_time;
                } else {
                    // 剩余时间不足一个单位时间，标记为0
                    remaining = 0;
                    SCHEDULER_LOG_DEBUG("任务 " + task_name + " 剩余时间不足，标记为0");
                }

                // 统计信息
                _stats.total_scheduled++;
                _stats.total_energy_consumed += unit_energy;

                // 5. 尝试分配核心
                if (!isTaskRunning(task)) {
                    int core_id = findAvailableCore();
                    if (core_id >= 0) {
                        assignTaskToCore(task, core_id);
                        _running_tasks.push_back(task);
                        SCHEDULER_LOG_DEBUG("任务分配核心: " + task_name +
                                  " 核心: " + std::to_string(core_id));
                    }
                }

                SCHEDULER_LOG_DEBUG("任务执行: " + task_name +
                          " 剩余时间: " + std::to_string(remaining) +
                          "ms 消耗能量: " + std::to_string(unit_energy) + "J");

                // 6. 检查是否完成
                if (remaining <= 0) {
                    SCHEDULER_LOG_INFO("🎉 任务执行完成: " + task_name);
                    completeTaskExecution(task);
                }
            } else {
                SCHEDULER_LOG_WARNING("任务能量消耗失败: " + task_name);
            }
        }
    }

    void GPFPBatchScheduler::validateTaskStates() {
        // 检查并修正所有任务的剩余时间
        for (auto &[task, remaining] : _task_remaining_time) {
            if (remaining < 0) {
                std::string task_name = getTaskShortName(task);
                SCHEDULER_LOG_WARNING("检测到负的剩余时间: " + task_name + " = " +
                            std::to_string(remaining) + "ms，自动修正为0");
                remaining = 0;

                // 如果是周期性任务，立即完成当前周期
                int period = _task_periods[task];
                if (period > 0) {
                    completeTaskExecution(task);
                }
            }
        }
    }

    bool GPFPBatchScheduler::executeBatch(const std::vector<AbsRTTask *> &tasks,
                                          MetaSim::Tick current_time) {
        if (tasks.empty()) {
            return false;
        }

        int64_t current_ms = static_cast<int64_t>(current_time);
        bool all_executed = true;

        // 计算批量总能量需求
        double batch_energy_required = calculateBatchEnergyRequired(tasks);
        _stats.total_batch_energy_required += batch_energy_required;

        // 检查是否有足够能量执行整个批量
        double current_energy = getCurrentEnergy();

        if (current_energy < batch_energy_required) {
            SCHEDULER_LOG_INFO("⚡ BATCH: 批量能量不足！");
            SCHEDULER_LOG_INFO("  需要: " + std::to_string(batch_energy_required) + " J");
            SCHEDULER_LOG_INFO("  可用: " + std::to_string(current_energy) + " J");
            SCHEDULER_LOG_INFO("  缺少: " +
                     std::to_string(batch_energy_required - current_energy) +
                     " J");
            return false;
        }

        // 消耗批量总能量
        if (!consumeEnergy(batch_energy_required, "batch_execution")) {
            SCHEDULER_LOG_ERROR("批量能量消耗失败！");
            return false;
        }

        SCHEDULER_LOG_INFO("✅ BATCH调度: 执行 " + std::to_string(tasks.size()) +
                 " 个任务，总消耗: " + std::to_string(batch_energy_required) +
                 " J");

        // 执行每个任务
        for (size_t i = 0; i < tasks.size(); ++i) {
            AbsRTTask *task = tasks[i];
            std::string task_name = getTaskShortName(task);

            // 检查任务是否还有剩余时间
            auto remaining_it = _task_remaining_time.find(task);
            if (remaining_it == _task_remaining_time.end() ||
                remaining_it->second <= 0) {
                // 任务可能已完成，跳过执行但记录
                SCHEDULER_LOG_DEBUG("任务 " + task_name + " 可能已完成，跳过执行");
                continue;
            }

            int &remaining = remaining_it->second;

            // 更新任务执行时间
            int time_to_execute = _unit_time;
            if (remaining < time_to_execute) {
                time_to_execute = remaining;
            }

            remaining -= time_to_execute;

            // 分配核心（只分配前M个核心）
            if (i < static_cast<size_t>(_num_cores)) {
                if (!isTaskRunning(task)) {
                    assignTaskToCore(task, i);
                    _running_tasks.push_back(task);
                    SCHEDULER_LOG_INFO("  任务" + std::to_string(i + 1) + ": " +
                             task_name + " 分配核心" + std::to_string(i) +
                             "，剩余时间: " + std::to_string(remaining) + "ms");
                }
            }

            _stats.total_scheduled++;
        }

        // 记录统计
        if (tasks.size() == static_cast<size_t>(_num_cores)) {
            _stats.total_batch_executions++;
            SCHEDULER_LOG_INFO("完整批量执行 (" + std::to_string(_num_cores) + "个任务)");
        } else {
            _stats.total_partial_batches++;
            SCHEDULER_LOG_INFO("部分批量执行 (" + std::to_string(tasks.size()) +
                     "个任务)");
        }

        // 处理可能已完成的任务
        processCompletedTasks();

        return true;
    }

    // =====================================================
    // 新增：BATCH简化能量恢复处理
    // =====================================================
    void GPFPBatchScheduler::handleBatchEnergyRecoverySimple(
        MetaSim::Tick current_time) {
        SCHEDULER_LOG_INFO("=== BATCH能量恢复处理开始 ===");

        if (_recovery_batch_tasks.empty()) {
            SCHEDULER_LOG_WARNING("恢复批量任务为空，跳过恢复");
            _recovery_in_progress = false;
            return;
        }

        // === 关键修复：验证能量参数 ===
        if (_recovery_required_energy <= 0.0) {
            SCHEDULER_LOG_ERROR("恢复所需能量无效: " +
                      std::to_string(_recovery_required_energy) + "J");
            _recovery_in_progress = false;
            _recovery_target = nullptr;
            _recovery_batch_tasks.clear();
            return;
        }

        // 检查能量值是否合理
        if (_recovery_required_energy > 100.0) {
            SCHEDULER_LOG_ERROR("恢复所需能量异常大: " +
                      std::to_string(_recovery_required_energy) +
                      "J，可能是参数传递错误");
            // 尝试修复：重新计算批量能量需求
            _recovery_required_energy =
                calculateBatchEnergyRequired(_recovery_batch_tasks);
            SCHEDULER_LOG_WARNING("使用修复后的能量值: " +
                        std::to_string(_recovery_required_energy) + "J");
        }

        // 检查当前能量
        double current_energy = getCurrentEnergy();
        int64_t current_time_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(
            "BATCH恢复检查: " + std::to_string(_recovery_batch_tasks.size()) +
            "个任务" + " 需要=" + std::to_string(_recovery_required_energy) +
            "J" + " 当前=" + std::to_string(current_energy) + "J" +
            " 时间=" + std::to_string(current_time_ms) + "ms");

        if (current_energy >= _recovery_required_energy) {
            SCHEDULER_LOG_INFO("✅ BATCH能量已足够，恢复完成");
            _recovery_in_progress = false;
            _recovery_target = nullptr;
            _recovery_required_energy = 0.0;
            _recovery_batch_tasks.clear();
            _consecutive_waits = 0;
            return;
        }

        SCHEDULER_LOG_INFO("调用BATCH能量恢复: " +
                 std::to_string(_recovery_batch_tasks.size()) + "个任务" +
                 " 需要能量=" + std::to_string(_recovery_required_energy) +
                 "J" + " 当前能量=" + std::to_string(current_energy) + "J" +
                 " 仿真时间=" + std::to_string(current_time_ms) + "ms");

        bool recovered = EnergyBridge::getInstance().waitForEnergyRecovery(
            _recovery_required_energy, // 正确的能量参数
            current_time_ms, // 正确的仿真时间参数
            10000 // 最大等待时间
        );

        if (recovered) {
            SCHEDULER_LOG_INFO("✅ BATCH能量恢复成功");
            _recovery_in_progress = false;
            _recovery_target = nullptr;
            _recovery_required_energy = 0.0;
            _recovery_batch_tasks.clear();
            _consecutive_waits = 0;
        } else {
            SCHEDULER_LOG_WARNING("BATCH能量恢复失败");
            _consecutive_waits++;

            if (_consecutive_waits > 5) {
                SCHEDULER_LOG_ERROR("多次BATCH恢复失败，放弃恢复");
                _recovery_in_progress = false;
                _recovery_target = nullptr;
                _recovery_required_energy = 0.0;
                _recovery_batch_tasks.clear();
                _consecutive_waits = 0;
            }
        }
    }

    // =====================================================
    // 任务管理方法
    // =====================================================

    void GPFPBatchScheduler::addTask(AbsRTTask *task,
                                     const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_ERROR("GPFP_BATCH Scheduler: Cannot add null task");
            return;
        }

        std::string task_name = task->toString();
        _task_original_names[task] = task_name;
        SCHEDULER_LOG_INFO("===============================================");
        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Adding task: " + task_name);

        // 提取基础信息
        int period = extractPeriodFromTaskName(task_name);
        int wcet = extractWCETFromTaskName(task_name);
        std::string workload_type = extractWorkloadTypeFromTaskName(task_name);

        // 尝试从配置中获取准确参数
        TaskParams config_params = getTaskParamsFromConfig(task_name);
        int64_t arrival_offset = config_params.arrival_offset;

        // 使用配置中的参数覆盖提取的参数
        if (config_params.period > 0) {
            period = config_params.period;
            SCHEDULER_LOG_INFO("使用配置中的period: " + std::to_string(period) + " ms");
        }
        if (config_params.wcet > 0) {
            wcet = config_params.wcet;
            SCHEDULER_LOG_INFO("使用配置中的wcet: " + std::to_string(wcet) + " ms");
        }
        if (!config_params.workload.empty()) {
            workload_type = config_params.workload;
            SCHEDULER_LOG_INFO("使用配置中的workload: " + workload_type);
        }

        // 存储任务参数
        _task_periods[task] = period;
        _task_wcets[task] = wcet;
        _task_workloads[task] = workload_type;
        _task_arrival_offsets[task] =
            MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(arrival_offset));
        _task_next_releases[task] =
            MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(arrival_offset));

        // 创建任务模型
        GPFPBatchTaskModel *model = new GPFPBatchTaskModel(
            task, period, wcet, workload_type,
            MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(arrival_offset)));
        enqueueModel(model);
        _task_models[task] = model;

        // 初始化任务状态
        initializeTaskRemainingTime(task);

        SCHEDULER_LOG_INFO("任务参数:");
        SCHEDULER_LOG_INFO("  Period: " + std::to_string(period) + " ms");
        SCHEDULER_LOG_INFO("  WCET: " + std::to_string(wcet) + " ms");
        SCHEDULER_LOG_INFO("  Workload: " + workload_type);
        SCHEDULER_LOG_INFO("  Arrival offset: " + std::to_string(arrival_offset) + " ms");

        // 验证能量计算
        double unit_energy = getUnitTimeEnergy(task);
        SCHEDULER_LOG_INFO("  单位时间(50ms)能耗: " + std::to_string(unit_energy) + " J");
        SCHEDULER_LOG_INFO("===============================================");

        // 安排精确激活
        int64_t current_ms = static_cast<int64_t>(SIMUL.getTime());
        if (arrival_offset <= current_ms) {
            SCHEDULER_LOG_INFO("🚨 任务 " + task_name + " 应该立即激活！arrival_offset=" +
                     std::to_string(arrival_offset) +
                     "ms 当前时间=" + std::to_string(current_ms) + "ms");

            activateTaskAtExactTime(
                task,
                MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(current_ms)));

            if (period > 0) {
                int64_t next_activation = current_ms + period;
                schedulePreciseActivationEvent(task, next_activation);
            }
        } else {
            schedulePreciseActivationEvent(task, arrival_offset);
        }

        // 根据配置文件中的任务总数动态判断
        int expected_task_count = _task_params_from_config.size();
        int current_task_count = _task_models.size();

        SCHEDULER_LOG_DEBUG("任务添加统计: 当前 " + std::to_string(current_task_count) +
                  "/" + std::to_string(expected_task_count) + " 个任务");

        // 当所有预期任务都已添加时，进行延迟初始化
        if (current_task_count >= expected_task_count &&
            !_delayed_initialization_done) {
            _delayed_initialization_done = true;
            SCHEDULER_LOG_INFO("所有 " + std::to_string(expected_task_count) +
                     " 个任务已添加完成，开始延迟初始化...");
            initializePreciseActivationSystem();
            _need_delayed_init = true;
        }
    }

    // =====================================================
    // 精确激活系统
    // =====================================================

    void GPFPBatchScheduler::schedulePreciseActivationEvent(
        AbsRTTask *task, int64_t activation_ms) {
        if (!task)
            return;

        std::string task_name = getTaskShortName(task);

        // === 关键修复：加强重复检查 ===
        // 1. 精确时间检查（±1ms）
        const int64_t EXACT_TOLERANCE_MS = 1;
        for (const auto &pair : _precise_activation_events) {
            if (pair.second.task == task) {
                int64_t existing_time = pair.first;
                int64_t time_diff = std::abs(existing_time - activation_ms);

                if (time_diff <= EXACT_TOLERANCE_MS) {
                    SCHEDULER_LOG_DEBUG("任务 " + task_name + " 已在 " +
                              std::to_string(existing_time) + "ms (±" +
                              std::to_string(EXACT_TOLERANCE_MS) +
                              "ms) 安排激活，跳过");
                    return;
                }
            }
        }

        // 2. 检查周期性任务的下一个激活时间
        auto period_it = _task_periods.find(task);
        if (period_it != _task_periods.end() && period_it->second > 0) {
            // 对于周期性任务，检查是否有接近周期的激活安排
            for (const auto &pair : _precise_activation_events) {
                if (pair.second.task == task) {
                    int64_t existing_time = pair.first;
                    int64_t period = period_it->second;

                    // 检查是否在整数倍周期附近
                    int64_t cycle_diff =
                        std::abs(existing_time - activation_ms);
                    if (cycle_diff % period == 0) {
                        SCHEDULER_LOG_DEBUG(
                            "周期性任务 " + task_name +
                            " 已有激活时间: " + std::to_string(existing_time) +
                            "ms，跳过新安排: " + std::to_string(activation_ms) +
                            "ms");
                        return;
                    }
                }
            }
        }

        // ========== 创建新的激活事件 ==========
        // 获取任务参数
        int period = _task_periods[task];
        std::string workload = _task_workloads[task];

        // 创建仿真事件
        BatchTaskActivationSimEvent *sim_event =
            new BatchTaskActivationSimEvent(this, task, task_name, (period > 0),
                                            period, activation_ms);

        // === 关键修复：检查时间是否在过去 ===
        MetaSim::Tick current_time = SIMUL.getTime();
        MetaSim::Tick activation_tick =
            MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(activation_ms));
        
        // 如果激活时间在过去，调整为当前时间
        if (activation_tick < current_time) {
            SCHEDULER_LOG_WARNING("激活时间在过去: " + task_name +
                     " 计划时间=" + std::to_string(activation_ms) + "ms" +
                     " 当前时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                     " 调整为当前时间");
            activation_tick = current_time;
            activation_ms = static_cast<int64_t>(current_time);
        }
        
        // 安排仿真事件
        try {
            sim_event->post(activation_tick);
            SCHEDULER_LOG_INFO("🔔 创建仿真激活事件: " + task_name +
                     " 时间: " + std::to_string(activation_ms) + "ms" +
                     (period > 0 ? " (周期: " + std::to_string(period) + "ms)"
                                 : " (非周期)"));
        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("安排激活事件失败: " + std::string(e.what()));
            delete sim_event;
            return;
        }

        // 记录到内部结构
        BatchTaskActivationEvent event;
        event.task = task;
        event.activation_time = activation_tick;
        event.activation_ms = activation_ms;
        event.task_name = task_name;
        event.is_periodic = (period > 0);
        event.period = period;

        _precise_activation_events.emplace(activation_ms, event);
        _task_next_activation_ms[task] = activation_ms;
        _scheduled_sim_events.push_back(sim_event);

        // 调试输出
        if (_total_debug_count++ < 20) {
            SCHEDULER_LOG_DEBUG("激活事件统计: 总事件数=" +
                      std::to_string(_precise_activation_events.size()) +
                      " 最近安排: " + task_name + " @ " +
                      std::to_string(activation_ms) + "ms");
        }
    }

    void GPFPBatchScheduler::processPreciseActivations(int64_t current_ms) {
        if (_total_debug_count++ < 10) {
            SCHEDULER_LOG_DEBUG("🔍 processPreciseActivations: 当前时间=" +
                      std::to_string(current_ms) + "ms, 待处理事件=" +
                      std::to_string(_precise_activation_events.size()));
        }

        // 处理所有在当前时间或之前应该激活的任务
        auto it = _precise_activation_events.begin();
        while (it != _precise_activation_events.end() &&
               it->first <= current_ms) {
            BatchTaskActivationEvent event = it->second;
            int64_t scheduled_time = it->first;

            SCHEDULER_LOG_INFO("🕐 精确激活触发: 任务=" + event.task_name +
                     " 计划时间=" + std::to_string(scheduled_time) + "ms" +
                     " 当前时间=" + std::to_string(current_ms) + "ms");

            // 执行激活
            onTaskActivationTimer(event);

            // 如果是周期性任务，安排下一次激活
            if (event.is_periodic && event.period > 0) {
                int64_t next_activation = scheduled_time + event.period;
                schedulePreciseActivationEvent(event.task, next_activation);
                SCHEDULER_LOG_DEBUG("周期性任务下次激活安排: " + event.task_name +
                          " 周期=" + std::to_string(event.period) + "ms" +
                          " 下次激活=" + std::to_string(next_activation) +
                          "ms");
            }

            // 移除已处理的事件
            it = _precise_activation_events.erase(it);
        }
    }

    void GPFPBatchScheduler::onTaskActivationTimer(
        const BatchTaskActivationEvent &event) {
        if (!event.task)
            return;

        std::string task_name = event.task_name;
        int64_t scheduled_time = event.activation_ms;
        int64_t current_ms = static_cast<int64_t>(SIMUL.getTime());

        // 1. 检查任务周期
        auto period_it = _task_periods.find(event.task);
        int period = (period_it != _task_periods.end()) ? period_it->second : 0;

        if (period <= 0) {
            // 非周期性任务：检查是否已完成
            if (isTaskCompleted(event.task)) {
                SCHEDULER_LOG_DEBUG("非周期性任务 " + task_name + " 已经完成，跳过激活");
                return;
            }
        } else {
            // 周期性任务：总是可以激活下一个周期
            SCHEDULER_LOG_DEBUG("周期性任务 " + task_name + " 准备激活第" +
                      std::to_string(event.activation_ms / period) + "个周期");
        }

        // 2. 检查任务是否已经激活
        if (isTaskActive(event.task)) {
            SCHEDULER_LOG_DEBUG("任务 " + task_name + " 已经处于激活状态");

            // 如果是周期性任务且已激活，可能是因为上一个周期还没完成
            // 这种情况下不重复激活，但确保下一次激活已安排
            if (period > 0) {
                int64_t next_activation = scheduled_time + period;
                auto next_it = _task_next_activation_ms.find(event.task);
                if (next_it == _task_next_activation_ms.end() ||
                    next_it->second <= current_ms) {
                    schedulePreciseActivationEvent(event.task, next_activation);
                    SCHEDULER_LOG_DEBUG("周期性任务 " + task_name + " 安排下一次激活: " +
                              std::to_string(next_activation) + "ms");
                }
            }
            return;
        }

        // 3. 精确时间激活任务
        activateTaskAtExactTime(event.task, event.activation_time);

        // 4. 记录精确激活信息
        if (current_ms != scheduled_time) {
            SCHEDULER_LOG_WARNING("时间偏差: " + task_name +
                        " 计划=" + std::to_string(scheduled_time) + "ms" +
                        " 实际=" + std::to_string(current_ms) + "ms" +
                        " 偏差=" + std::to_string(current_ms - scheduled_time) +
                        "ms");
        } else {
            SCHEDULER_LOG_INFO("✅ 精确时间激活: " + task_name +
                     " 时间: " + std::to_string(scheduled_time) + "ms");
        }
    }

    void GPFPBatchScheduler::activateTaskAtExactTime(
        AbsRTTask *task, MetaSim::Tick activation_time) {
        if (!task) {
            SCHEDULER_LOG_ERROR("激活任务失败：任务为空");
            return;
        }

        std::string task_name = getTaskShortName(task);
        int64_t activation_ms = static_cast<int64_t>(activation_time);
        int64_t current_ms = static_cast<int64_t>(SIMUL.getTime());

        // 1. 检查任务是否已经激活
        if (_active_tasks.find(task) != _active_tasks.end()) {
            SCHEDULER_LOG_DEBUG("任务 " + task_name + " 已经处于激活状态");
            return;
        }

        // 2. 检查任务周期
        auto period_it = _task_periods.find(task);
        int period = (period_it != _task_periods.end()) ? period_it->second : 0;

        if (period <= 0) {
            // 非周期性任务：检查是否已完成
            if (isTaskCompleted(task)) {
                SCHEDULER_LOG_DEBUG("非周期性任务 " + task_name + " 已经完成，跳过激活");
                return;
            }
        }

        // 3. 检查时间偏差
        int64_t time_diff = std::abs(current_ms - activation_ms);
        if (time_diff > 10) {
            SCHEDULER_LOG_WARNING("任务激活时间偏差较大: " + task_name +
                        " 期望=" + std::to_string(activation_ms) + "ms" +
                        " 实际=" + std::to_string(current_ms) + "ms" +
                        " 偏差=" + std::to_string(time_diff) + "ms");
        }

        // 4. 将任务加入活跃集合
        _active_tasks.insert(task);

        // 5. 确保任务有剩余时间
        if (_task_remaining_time.find(task) == _task_remaining_time.end()) {
            initializeTaskRemainingTime(task);
        } else {
            // 对于周期性任务，如果剩余时间为0，重置为WCET
            if (period > 0) {
                auto remaining_it = _task_remaining_time.find(task);
                if (remaining_it != _task_remaining_time.end() &&
                    remaining_it->second <= 0) {
                    auto wcet_it = _task_wcets.find(task);
                    if (wcet_it != _task_wcets.end()) {
                        _task_remaining_time[task] = wcet_it->second;
                        SCHEDULER_LOG_DEBUG("重置周期性任务 " + task_name +
                                  " 剩余时间为WCET: " +
                                  std::to_string(wcet_it->second) + "ms");
                    }
                }
            }
        }

        // 6. 记录激活信息
        SCHEDULER_LOG_INFO("✅ 任务激活: " + task_name +
                 " 期望激活时间: " + std::to_string(activation_ms) + "ms" +
                 " 实际激活时间: " + std::to_string(current_ms) + "ms" +
                 " 工作负载: " + _task_workloads[task] +
                 " WCET: " + std::to_string(_task_wcets[task]) + "ms" +
                 " 剩余时间: " + std::to_string(_task_remaining_time[task]) +
                 "ms");
    }

    // =====================================================
    // 立即激活方法
    // =====================================================

    void GPFPBatchScheduler::forceImmediateActivationAllTasks() {
        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO("=== 强制立即激活所有任务 ===");
        SCHEDULER_LOG_INFO("当前仿真时间: " + std::to_string(current_ms) + "ms");

        int activated_count = 0;

        // 激活所有尚未激活且未完成的任务
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            std::string task_name = getTaskShortName(task);

            // 如果任务已经激活或已完成，跳过
            if (isTaskActive(task) || isTaskCompleted(task)) {
                continue;
            }

            // 获取任务的到达时间偏移
            auto offset_it = _task_arrival_offsets.find(task);
            if (offset_it == _task_arrival_offsets.end()) {
                continue;
            }

            int64_t arrival_offset = static_cast<int64_t>(offset_it->second);

            // 如果到达时间已经过去（包括当前时间），强制激活
            if (arrival_offset <= current_ms) {
                SCHEDULER_LOG_INFO("强制激活: " + task_name + " (arrival_offset=" +
                         std::to_string(arrival_offset) + "ms)");

                activateTaskAtExactTime(task, current_time);
                activated_count++;

                // 如果是周期性任务，安排下一次激活
                int period = _task_periods[task];
                if (period > 0) {
                    int64_t next_activation = current_ms + period;
                    schedulePreciseActivationEvent(task, next_activation);
                }
            }
        }

        SCHEDULER_LOG_INFO("强制激活完成: 激活了 " + std::to_string(activated_count) +
                 " 个任务");

        // 如果有激活的任务，立即调度
        if (activated_count > 0) {
            SCHEDULER_LOG_INFO("有 " + std::to_string(_active_tasks.size()) +
                     " 个激活任务，立即调度");
            schedule();
        }
    }

    void GPFPBatchScheduler::checkScheduledActivations(
        MetaSim::Tick current_time) {
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 使用<=确保在计划时间精确激活
        auto it = _scheduled_activations.begin();
        while (it != _scheduled_activations.end()) {
            int64_t scheduled_time = it->first;

            if (scheduled_time <= current_ms) {
                std::vector<AbsRTTask *> &tasks_to_activate = it->second;

                if (!tasks_to_activate.empty()) {
                    SCHEDULER_LOG_INFO("🕐 定时器精确触发: 计划时间=" +
                             std::to_string(scheduled_time) +
                             "ms, 当前时间=" + std::to_string(current_ms) +
                             "ms, " + std::to_string(tasks_to_activate.size()) +
                             "个任务激活");

                    for (AbsRTTask *task : tasks_to_activate) {
                        std::string task_name = getTaskShortName(task);
                        activateTaskAtExactTime(
                            task,
                            MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(
                                scheduled_time)));
                        SCHEDULER_LOG_INFO("✅ 任务精确激活: " + task_name +
                                 " 计划时间: " +
                                 std::to_string(scheduled_time) + "ms");

                        // 周期性任务的下一周期安排
                        auto period_it = _task_periods.find(task);
                        if (period_it != _task_periods.end() &&
                            period_it->second > 0) {
                            int period = period_it->second;
                            int64_t next_activation = scheduled_time + period;
                            _scheduled_activations[next_activation].push_back(
                                task);
                        }
                    }

                    // 清除已处理的任务
                    it = _scheduled_activations.erase(it);
                    continue;
                }
            } else {
                break;
            }
            ++it;
        }
    }

    // =====================================================
    // 任务生命周期管理
    // =====================================================

    void GPFPBatchScheduler::initializeTaskRemainingTime(AbsRTTask *task) {
        auto wcet_it = _task_wcets.find(task);
        if (wcet_it == _task_wcets.end()) {
            SCHEDULER_LOG_ERROR("初始化任务剩余时间失败：找不到WCET");
            return;
        }

        int wcet = wcet_it->second;
        _task_remaining_time[task] = wcet;
        _task_executed_time[task] = 0;

        SCHEDULER_LOG_DEBUG("初始化任务 " + getTaskShortName(task) +
                  " 剩余时间: " + std::to_string(wcet) + "ms");
    }

    void
        GPFPBatchScheduler::resetTaskForNextPeriod(AbsRTTask *task,
                                                   MetaSim::Tick current_time) {
        if (!task)
            return;

        std::string task_name = getTaskShortName(task);

        // 1. 重置任务执行时间
        auto wcet_it = _task_wcets.find(task);
        if (wcet_it != _task_wcets.end()) {
            _task_remaining_time[task] = wcet_it->second;
            _task_executed_time[task] = 0;
            SCHEDULER_LOG_DEBUG("重置周期性任务 " + task_name +
                      " 剩余时间: " + std::to_string(wcet_it->second) + "ms");
        }

        // 2. 更新下一次释放时间
        int period = _task_periods[task];
        if (period > 0) {
            _task_next_releases[task] = current_time + MetaSim::Tick(period);
        }

        // 3. 从已完成集合中移除（确保周期性任务可以重新激活）
        _completed_tasks.erase(task);

        // 4. 确保任务不在活跃集合中（等待重新激活）
        _active_tasks.erase(task);

        SCHEDULER_LOG_DEBUG("周期性任务重置: " + task_name +
                  " 周期: " + std::to_string(period) + "ms");
    }

    void GPFPBatchScheduler::completeTaskExecution(AbsRTTask *task) {
        std::string task_name = getTaskShortName(task);
        SCHEDULER_LOG_INFO("🎉 任务当前周期完成: " + task_name);

        _stats.total_task_completions++;

        // 1. 从运行队列移除（如果存在）
        auto running_it =
            std::find(_running_tasks.begin(), _running_tasks.end(), task);
        if (running_it != _running_tasks.end()) {
            _running_tasks.erase(running_it);
            SCHEDULER_LOG_DEBUG("任务 " + task_name + " 已从运行队列移除");
        }

        // 2. 从核心分配移除
        for (auto &pair : _core_assignments) {
            if (pair.second == task) {
                pair.second = nullptr;
                SCHEDULER_LOG_DEBUG("任务 " + task_name + " 释放核心 " +
                          std::to_string(pair.first));
            }
        }

        // 3. 检查任务周期
        int period = _task_periods[task];

        if (period > 0) {
            // ========== 周期性任务处理 ==========
            SCHEDULER_LOG_INFO("周期性任务 " + task_name + " 周期完成，周期=" +
                     std::to_string(period) + "ms，等待下一个周期激活");

            // 3.1 获取当前仿真时间
            MetaSim::Tick current_time = SIMUL.getTime();
            int64_t current_ms = static_cast<int64_t>(current_time);

            // 3.2 重置任务执行时间（为下一个周期做准备）
            auto wcet_it = _task_wcets.find(task);
            if (wcet_it != _task_wcets.end()) {
                _task_remaining_time[task] = wcet_it->second; // 重置剩余时间
                _task_executed_time[task] = 0; // 重置已执行时间
                SCHEDULER_LOG_DEBUG("重置任务 " + task_name + " 剩余时间为WCET: " +
                          std::to_string(wcet_it->second) + "ms");
            }

            // 3.3 ========== 关键修复：周期性任务完成时不应在活跃集合中
            // ==========
            // 周期性任务在当前周期完成后，应该从活跃集合移除，等待下一个周期激活
            _active_tasks.erase(task);
            SCHEDULER_LOG_DEBUG("周期性任务 " + task_name +
                      " 从活跃集合移除，等待下一个周期");

            // 3.4 从已完成集合移除（周期性任务不应在已完成集合中）
            _completed_tasks.erase(task);

            // 3.5 计算下一次激活时间
            int64_t next_activation = current_ms + period;

            // 检查是否已经有近似时间的激活安排
            bool already_scheduled = false;
            for (const auto &pair : _precise_activation_events) {
                if (pair.second.task == task) {
                    int64_t existing_time = pair.first;
                    int64_t time_diff =
                        std::abs(existing_time - next_activation);

                    if (time_diff <= 100) { // 100ms容忍窗口
                        already_scheduled = true;
                        SCHEDULER_LOG_DEBUG(
                            "周期性任务 " + task_name + " 下次激活已安排: " +
                            std::to_string(existing_time) + "ms，跳过新安排");
                        break;
                    }
                }
            }

            // 3.6 安排下一个周期的激活（如果没有重复）
            if (!already_scheduled) {
                schedulePreciseActivationEvent(task, next_activation);
                SCHEDULER_LOG_INFO("周期性任务安排下一个周期激活: " + task_name + " @ " +
                         std::to_string(next_activation) + "ms");
            }

        } else {
            // ========== 非周期性任务处理 ==========
            SCHEDULER_LOG_INFO("非周期性任务 " + task_name + " 永久完成");

            // 4.1 标记为已完成
            _completed_tasks.insert(task);

            // 4.2 从活跃集合移除
            _active_tasks.erase(task);

            // 4.3 清理任务状态数据
            _task_remaining_time.erase(task);
            _task_executed_time.erase(task);
            _task_original_names.erase(task);

            // 4.4 从任务模型中移除
            auto model_it = _task_models.find(task);
            if (model_it != _task_models.end()) {
                extract(task);
                delete model_it->second;
                _task_models.erase(model_it);
                SCHEDULER_LOG_INFO("任务模型已移除: " + task_name);
            }
        }

        // 5. 检查是否所有任务都已完成
        if (areAllTasksCompleted()) {
            SCHEDULER_LOG_INFO("✅ 所有任务已完成！");
            printStats();
        }
    }

    // =====================================================
    // 核心管理
    // =====================================================

    bool GPFPBatchScheduler::assignTaskToCore(AbsRTTask *task, int core_id) {
        if (core_id < 0 || core_id >= _num_cores) {
            SCHEDULER_LOG_ERROR("无效的核心ID: " + std::to_string(core_id));
            return false;
        }

        // 检查核心是否已被占用
        if (_core_assignments[core_id] != nullptr &&
            _core_assignments[core_id] != task) {
            AbsRTTask *old_task = _core_assignments[core_id];
            SCHEDULER_LOG_INFO("抢占: 核心" + std::to_string(core_id) + " 任务" +
                     getTaskShortName(old_task) + " → " +
                     getTaskShortName(task));

            // 从运行队列移除旧任务
            auto it = std::find(_running_tasks.begin(), _running_tasks.end(),
                                old_task);
            if (it != _running_tasks.end()) {
                _running_tasks.erase(it);
            }
        }

        _core_assignments[core_id] = task;
        return true;
    }

    void GPFPBatchScheduler::releaseCore(int core_id) {
        if (core_id >= 0 && core_id < _num_cores) {
            _core_assignments[core_id] = nullptr;
        }
    }

    int GPFPBatchScheduler::findAvailableCore() const {
        for (int i = 0; i < _num_cores; ++i) {
            if (_core_assignments.find(i) == _core_assignments.end() ||
                _core_assignments.at(i) == nullptr) {
                return i;
            }
        }
        return -1;
    }

    // =====================================================
    // 任务状态检查
    // =====================================================

    bool GPFPBatchScheduler::isTaskActive(AbsRTTask *task) const {
        return _active_tasks.find(task) != _active_tasks.end();
    }

    bool GPFPBatchScheduler::isTaskRunning(AbsRTTask *task) const {
        return std::find(_running_tasks.begin(), _running_tasks.end(), task) !=
               _running_tasks.end();
    }

    bool GPFPBatchScheduler::isTaskCompleted(AbsRTTask *task) const {
        if (!task)
            return true;

        // 检查是否在已完成集合中
        bool in_completed_set =
            _completed_tasks.find(task) != _completed_tasks.end();

        // 如果是周期性任务，即使不在已完成集合中，也需要检查剩余时间
        if (!in_completed_set) {
            auto period_it = _task_periods.find(task);
            if (period_it != _task_periods.end() && period_it->second > 0) {
                // 周期性任务：检查是否有剩余执行时间
                auto remaining_it = _task_remaining_time.find(task);
                if (remaining_it == _task_remaining_time.end() ||
                    remaining_it->second <= 0) {
                    // 周期性任务当前周期已完成，但不算永久完成
                    return false; // 返回false，因为还有下一个周期
                }
            }
        }

        return in_completed_set;
    }

    bool GPFPBatchScheduler::isTaskReady(AbsRTTask *task) const {
        if (!task)
            return false;

        // 1. 任务必须激活且未永久完成
        if (!isTaskActive(task) || isTaskCompleted(task))
            return false;

        // 2. 检查剩余时间
        auto remaining_it = _task_remaining_time.find(task);
        if (remaining_it == _task_remaining_time.end())
            return false;

        // 3. ========== 关键修复：确保剩余时间有效 ==========
        // 对于周期性任务，如果剩余时间<=0，说明当前周期已完成
        // 应该等待下一个周期激活，而不是立即就绪
        int remaining_time = remaining_it->second;
        if (remaining_time <= 0) {
            SCHEDULER_LOG_DEBUG("任务 " + getTaskShortName(task) +
                      " 剩余时间<=0，未就绪");
            return false;
        }

        // 4.
        // 检查周期（周期性任务即使当前周期完成，只要已激活且不在已完成集合，就算准备好）
        auto period_it = _task_periods.find(task);
        if (period_it != _task_periods.end() && period_it->second > 0) {
            // 周期性任务：剩余时间>0且激活状态，认为是准备好的
            return true;
        }

        // 5. 非周期性任务：必须有剩余时间
        return remaining_time > 0;
    }

    int GPFPBatchScheduler::getRMPriority(AbsRTTask *task) const {
        auto it = _task_periods.find(task);
        int period = (it != _task_periods.end()) ? it->second : 1000;
        return -period;
    }

    std::vector<AbsRTTask *>
        GPFPBatchScheduler::getActiveTasksByRMPriority() const {
        std::vector<AbsRTTask *> active_list;

        // 收集所有活跃且未完成的任务
        for (AbsRTTask *task : _active_tasks) {
            if (!isTaskCompleted(task) && isTaskReady(task)) {
                // ========== 关键修复：双重检查任务状态 ==========
                // 确保任务有正的剩余时间
                auto remaining_it = _task_remaining_time.find(task);
                if (remaining_it != _task_remaining_time.end() &&
                    remaining_it->second > 0) {
                    active_list.push_back(task);
                } else {
                    SCHEDULER_LOG_DEBUG("任务 " + getTaskShortName(task) +
                              " 剩余时间无效，排除在活跃列表外");
                }
            }
        }

        // 按RM优先级排序: 周期越小，优先级越高
        // 使用稳定的排序保持相同周期的任务顺序
        std::stable_sort(active_list.begin(), active_list.end(),
                         [this](AbsRTTask *a, AbsRTTask *b) {
                             int period_a = 1000000; // 默认大值
                             int period_b = 1000000;

                             auto it_a = _task_periods.find(a);
                             auto it_b = _task_periods.find(b);

                             if (it_a != _task_periods.end())
                                 period_a = it_a->second;
                             if (it_b != _task_periods.end())
                                 period_b = it_b->second;

                             // 周期小的优先级高，排在前面
                             if (period_a != period_b) {
                                 return period_a < period_b;
                             }
                             
                             // 如果周期相同，按任务编号排序（task_0, task_1, task_2...）
                             std::string name_a = getTaskShortName(a);
                             std::string name_b = getTaskShortName(b);
                             
                             // 提取任务编号
                             int num_a = 0, num_b = 0;
                             std::regex pattern(R"(task_(\d+))");
                             std::smatch match_a, match_b;
                             
                             if (std::regex_search(name_a, match_a, pattern) && match_a.size() > 1) {
                                 num_a = std::stoi(match_a[1].str());
                             }
                             if (std::regex_search(name_b, match_b, pattern) && match_b.size() > 1) {
                                 num_b = std::stoi(match_b[1].str());
                             }
                             
                             return num_a < num_b;
                         });

        return active_list;
    }

    bool GPFPBatchScheduler::areAllTasksCompleted() const {
        // 如果没有任务，返回true
        if (_task_models.empty()) {
            SCHEDULER_LOG_DEBUG("没有任务模型，返回true");
            return true;
        }

        SCHEDULER_LOG_DEBUG("=== 检查所有任务完成状态 ===");
        SCHEDULER_LOG_DEBUG("任务模型数: " + std::to_string(_task_models.size()));
        SCHEDULER_LOG_DEBUG("已完成任务数: " + std::to_string(_completed_tasks.size()));
        SCHEDULER_LOG_DEBUG("活跃任务数: " + std::to_string(_active_tasks.size()));

        // 检查是否有非周期性任务未完成
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            std::string task_name = getTaskShortName(task);

            // 检查是否是周期性任务
            int period = _task_periods.at(task);

            if (period <= 0) { // 非周期性任务
                if (_completed_tasks.find(task) == _completed_tasks.end()) {
                    SCHEDULER_LOG_DEBUG("非周期性任务未完成: " + task_name);
                    return false;
                }
            } else { // 周期性任务
                // 检查是否有剩余的激活事件
                for (const auto &[time, event] : _precise_activation_events) {
                    if (event.task == task) {
                        SCHEDULER_LOG_DEBUG("周期性任务有未来激活事件: " + task_name +
                                  " @ " + std::to_string(time) + "ms");
                        return false;
                    }
                }
            }
        }

        SCHEDULER_LOG_DEBUG("所有任务检查通过");
        return true;
    }

    // =====================================================
    // 新添加的函数实现
    // =====================================================

    double GPFPBatchScheduler::calculateUnifiedEnergy(AbsRTTask *task,
                                                      int duration_ms) const {
        if (!task) {
            return 0.0;
        }

        std::string workload = "control";
        auto it = _task_workloads.find(task);
        if (it != _task_workloads.end()) {
            workload = it->second;
        }

        // 使用 EnergyBridge 的统一计算
        return EnergyBridge::getInstance().calculateTaskEnergy(
            workload, static_cast<double>(duration_ms), _current_frequency);
    }

    double GPFPBatchScheduler::getUnifiedUnitTimeEnergy(AbsRTTask *task) const {
        return calculateUnifiedEnergy(task, _unit_time);
    }

    double GPFPBatchScheduler::getTaskEnergyConsumption(AbsRTTask *task) const {
        return getUnifiedUnitTimeEnergy(task);
    }

    bool GPFPBatchScheduler::checkAndStartRecovery(double required_energy,
                                                   MetaSim::Tick current_time) {
        if (required_energy <= 0) {
            SCHEDULER_LOG_ERROR("无效的恢复所需能量: " + std::to_string(required_energy) +
                      "J");
            return false;
        }

        double current_energy = getCurrentEnergy();

        if (current_energy >= required_energy) {
            SCHEDULER_LOG_DEBUG("能量已足够，无需恢复");
            return true;
        }

        // 启动恢复
        _recovery_in_progress = true;
        _recovery_required_energy = required_energy;

        SCHEDULER_LOG_INFO("启动能量恢复: 需要=" + std::to_string(required_energy) + "J" +
                 " 当前=" + std::to_string(current_energy) + "J");

        // 调用简化恢复处理
        handleBatchEnergyRecoverySimple(current_time);

        return true;
    }

    bool GPFPBatchScheduler::checkAndStartBatchRecovery(
        double required_batch_energy, MetaSim::Tick current_time,
        const std::vector<AbsRTTask *> &batch_tasks) {
        if (required_batch_energy <= 0) {
            SCHEDULER_LOG_ERROR("无效的批量恢复所需能量: " +
                      std::to_string(required_batch_energy) + "J");
            return false;
        }

        double current_energy = getCurrentEnergy();

        if (current_energy >= required_batch_energy) {
            SCHEDULER_LOG_DEBUG("批量能量已足够，无需恢复");
            return true;
        }

        // 启动批量恢复
        _recovery_in_progress = true;
        _recovery_required_energy = required_batch_energy;
        _recovery_batch_tasks = batch_tasks;

        SCHEDULER_LOG_INFO("启动批量能量恢复: " + std::to_string(batch_tasks.size()) +
                 "个任务" + " 需要=" + std::to_string(required_batch_energy) +
                 "J" + " 当前=" + std::to_string(current_energy) + "J");

        // 调用批量恢复处理
        handleBatchEnergyRecoverySimple(current_time);

        return true;
    }

    void GPFPBatchScheduler::validateEnergyParameters() {
        SCHEDULER_LOG_INFO("=== 能量参数验证 ===");

        // 验证基础参数
        SCHEDULER_LOG_INFO("基础功耗: " + std::to_string(_base_power) + " W");
        SCHEDULER_LOG_INFO("单位时间: " + std::to_string(_unit_time) + " ms");
        SCHEDULER_LOG_INFO("当前频率: " + std::to_string(_current_frequency) + " MHz");

        // 验证功率系数
        SCHEDULER_LOG_INFO("工作负载功率系数:");
        for (const auto &pair : _power_coefficients) {
            SCHEDULER_LOG_INFO("  " + pair.first + ": " + std::to_string(pair.second) +
                     " W");
        }

        // 验证任务能量计算
        if (!_task_models.empty()) {
            auto it = _task_models.begin();
            AbsRTTask *sample_task = it->first;
            std::string task_name = getTaskShortName(sample_task);

            // 计算单位时间能量
            double unit_energy = getUnitTimeEnergy(sample_task);

            SCHEDULER_LOG_INFO("示例任务能量计算:");
            SCHEDULER_LOG_INFO("  任务: " + task_name);
            SCHEDULER_LOG_INFO("  工作负载: " + _task_workloads[sample_task]);
            SCHEDULER_LOG_INFO("  单位时间能量: " + std::to_string(unit_energy) + " J");

            // 手动计算验证
            std::string workload = _task_workloads[sample_task];
            double workload_power = getWorkloadPower(workload);
            double freq_ratio = getFrequencyPowerRatio(_current_frequency);
            double total_power = _base_power + workload_power * freq_ratio;
            double manual_energy = total_power * (_unit_time / 1000.0);

            SCHEDULER_LOG_INFO("  手动计算验证:");
            SCHEDULER_LOG_INFO("    工作负载功率: " + std::to_string(workload_power) +
                     " W");
            SCHEDULER_LOG_INFO("    频率比例: " + std::to_string(freq_ratio));
            SCHEDULER_LOG_INFO("    总功率: " + std::to_string(total_power) + " W");
            SCHEDULER_LOG_INFO("    能量: " + std::to_string(manual_energy) + " J");

            if (abs(unit_energy - manual_energy) > 0.001) {
                SCHEDULER_LOG_WARNING("能量计算不一致！");
            }
        }

        SCHEDULER_LOG_INFO("=== 验证完成 ===");
    }

    void GPFPBatchScheduler::debugEnergyCalculation(AbsRTTask *task) {
        if (!task) {
            SCHEDULER_LOG_WARNING("任务为空，无法计算能量");
            return;
        }

        std::string task_name = getTaskShortName(task);
        double unit_energy = getUnitTimeEnergy(task);
        double unified_energy = getUnifiedUnitTimeEnergy(task);

        SCHEDULER_LOG_INFO("=== 任务能量计算调试 ===");
        SCHEDULER_LOG_INFO("任务: " + task_name);
        SCHEDULER_LOG_INFO("工作负载: " + _task_workloads[task]);
        SCHEDULER_LOG_INFO("周期: " + std::to_string(_task_periods[task]) + " ms");
        SCHEDULER_LOG_INFO("WCET: " + std::to_string(_task_wcets[task]) + " ms");
        SCHEDULER_LOG_INFO("传统方法能量: " + std::to_string(unit_energy) + " J");
        SCHEDULER_LOG_INFO("统一方法能量: " + std::to_string(unified_energy) + " J");
        SCHEDULER_LOG_INFO("差异: " + std::to_string(abs(unit_energy - unified_energy)) +
                 " J");
        SCHEDULER_LOG_INFO("=== 调试结束 ===");
    }

    void GPFPBatchScheduler::debugTimeConversion(
        MetaSim::Tick current_time) const {
        int64_t current_ms = static_cast<int64_t>(current_time);
        TimeMs adjusted_time = getAdjustedTime(current_time);

        SCHEDULER_LOG_DEBUG("时间转换调试:");
        SCHEDULER_LOG_DEBUG("  仿真时间: " + std::to_string(current_ms) + " ms");
        SCHEDULER_LOG_DEBUG("  开始偏移: " +
                  std::to_string(static_cast<int64_t>(_start_time_offset)) +
                  " ms");
        SCHEDULER_LOG_DEBUG("  调整后时间: " + std::to_string(adjusted_time) + " ms");

        // 转换为小时:分钟:秒格式
        int64_t total_seconds = adjusted_time / 1000;
        int64_t hour = (total_seconds / 3600) % 24;
        int64_t minute = (total_seconds % 3600) / 60;
        int64_t second = total_seconds % 60;

        SCHEDULER_LOG_DEBUG("  绝对时间: " + std::to_string(hour) + ":" +
                  std::to_string(minute) + ":" + std::to_string(second));
    }

    // =====================================================
    // 配置加载
    // =====================================================

    void GPFPBatchScheduler::loadTasksFromConfig(const std::string &task_file) {
        if (_config_loaded)
            return;

        SCHEDULER_LOG_INFO("尝试从配置文件加载任务参数: " + task_file);

        // 尝试从YAML配置文件中动态加载任务
        try {
            // 检查文件是否存在
            std::ifstream file(task_file);
            if (!file.good()) {
                SCHEDULER_LOG_WARNING("任务配置文件不存在: " + task_file + 
                         "，使用默认配置");
                // 使用默认配置（所有周期设为1000，与YAML文件一致）
                std::map<std::string, TaskParams> task_configs = {
                    {"task_0", {1000, 60, "bzip2", 0}},
                    {"task_1", {1000, 80, "bzip2", 0}},
                    {"task_2", {1000, 100, "bzip2", 0}},
                    {"task_3", {1000, 120, "bzip2", 0}},
                    {"task_4", {1000, 140, "bzip2", 0}}};
                
                _task_params_from_config = task_configs;
                _config_loaded = true;
                SCHEDULER_LOG_INFO("已加载 " + std::to_string(task_configs.size()) +
                         " 个任务的默认配置");
                return;
            }
            
            // 解析YAML文件 - 使用项目自带的yaml解析器
            yaml::Object_ptr config_node = yaml::parse(task_file);

            if (!config_node->has("taskset")) {
                SCHEDULER_LOG_WARNING("任务配置文件格式错误，缺少taskset节点: " + task_file);
                _config_loaded = true;
                return;
            }

            yaml::Object_ptr taskset = config_node->get("taskset");
            std::map<std::string, TaskParams> task_configs;
            int loaded_count = 0;

            // 遍历taskset中的任务
            for (size_t i = 0; i < taskset->size(); ++i) {
                yaml::Object_ptr task_node = taskset->get(i);

                if (!task_node->has("name")) {
                    SCHEDULER_LOG_WARNING("任务节点缺少name字段，跳过");
                    continue;
                }

                std::string task_name = task_node->get("name")->get();

                // 解析任务参数
                int period = 1000;  // 默认值
                int wcet = 100;     // 默认值
                std::string workload = "bzip2";  // 默认值
                int arrival_offset = 0;  // 默认值

                // 从params字段解析参数
                if (task_node->has("params")) {
                    std::string params_str = task_node->get("params")->get();
                    std::istringstream iss(params_str);
                    std::string token;

                    while (std::getline(iss, token, ',')) {
                        size_t eq_pos = token.find('=');
                        if (eq_pos != std::string::npos) {
                            std::string key = token.substr(0, eq_pos);
                            std::string value = token.substr(eq_pos + 1);

                            // 去除空格
                            key.erase(0, key.find_first_not_of(" \t"));
                            key.erase(key.find_last_not_of(" \t") + 1);
                            value.erase(0, value.find_first_not_of(" \t"));
                            value.erase(value.find_last_not_of(" \t") + 1);

                            if (key == "period") {
                                period = std::stoi(value);
                            } else if (key == "arrival_offset") {
                                arrival_offset = std::stoi(value);
                            } else if (key == "workload") {
                                workload = value;
                            }
                        }
                    }
                }

                // 从runtime字段获取WCET
                if (task_node->has("runtime")) {
                    std::string runtime_str = task_node->get("runtime")->get();
                    try {
                        wcet = std::stoi(runtime_str);
                    } catch (...) {
                        SCHEDULER_LOG_WARNING("无法解析runtime: " + runtime_str);
                    }
                }

                // 从iat字段获取周期（如果params中没有period）
                if (period == 1000 && task_node->has("iat")) {
                    std::string iat_str = task_node->get("iat")->get();
                    try {
                        period = std::stoi(iat_str);
                    } catch (...) {
                        SCHEDULER_LOG_WARNING("无法解析iat: " + iat_str);
                    }
                }

                // 存储任务配置
                task_configs[task_name] = {period, wcet, workload, arrival_offset};
                loaded_count++;

                SCHEDULER_LOG_INFO("从配置文件加载任务: " + task_name +
                          " period=" + std::to_string(period) +
                          " wcet=" + std::to_string(wcet) +
                          " workload=" + workload +
                          " arrival_offset=" + std::to_string(arrival_offset) + "ms");
            }

            // 存储配置
            _task_params_from_config = task_configs;
            _config_loaded = true;
            
            SCHEDULER_LOG_INFO("成功从配置文件加载 " + std::to_string(loaded_count) + " 个任务参数");
            
        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("加载任务配置文件失败: " + std::string(e.what()));
            SCHEDULER_LOG_WARNING("使用默认配置");
            
            // 使用默认配置
            std::map<std::string, TaskParams> task_configs = {
                {"task_0", {1000, 60, "bzip2", 0}},
                {"task_1", {1000, 80, "bzip2", 0}},
                {"task_2", {1000, 100, "bzip2", 0}},
                {"task_3", {1000, 120, "bzip2", 0}},
                {"task_4", {1000, 140, "bzip2", 0}}};
            
            _task_params_from_config = task_configs;
            _config_loaded = true;
            SCHEDULER_LOG_INFO("已加载 " + std::to_string(task_configs.size()) + " 个任务的默认配置");
        }
    }

    GPFPBatchScheduler::TaskParams GPFPBatchScheduler::getTaskParamsFromConfig(
        const std::string &task_name) const {
        std::regex pattern(R"(task_(\d+))");
        std::smatch match;

        if (std::regex_search(task_name, match, pattern) && match.size() > 1) {
            std::string task_key = "task_" + match[1].str();
            auto it = _task_params_from_config.find(task_key);
            if (it != _task_params_from_config.end()) {
                return it->second;
            }
        }

        return {0, 0, "", 0};
    }

    // =====================================================
    // 初始化方法
    // =====================================================

    void GPFPBatchScheduler::initializeTaskActivation() {
        SCHEDULER_LOG_INFO("=== 初始化任务激活系统 ===");

        // 获取所有任务并按到达时间排序
        std::vector<std::pair<int64_t, AbsRTTask *>> timed_tasks;

        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            auto offset_it = _task_arrival_offsets.find(task);
            if (offset_it != _task_arrival_offsets.end()) {
                int64_t arrival_ms = static_cast<int64_t>(offset_it->second);
                timed_tasks.push_back({arrival_ms, task});
            }
        }

        // 按到达时间排序
        std::sort(
            timed_tasks.begin(), timed_tasks.end(),
            [](const auto &a, const auto &b) { return a.first < b.first; });

        SCHEDULER_LOG_INFO("任务激活计划:");
        for (const auto &[arrival_ms, task] : timed_tasks) {
            std::string task_name = getTaskShortName(task);
            auto workload_it = _task_workloads.find(task);
            std::string workload = (workload_it != _task_workloads.end())
                                       ? workload_it->second
                                       : "unknown";
            SCHEDULER_LOG_INFO("  " + task_name + " -> " + std::to_string(arrival_ms) +
                     "ms (" + workload + ")");
        }

        SCHEDULER_LOG_INFO("=== 任务激活系统初始化完成 ===");
    }

    void GPFPBatchScheduler::initializePreciseActivationSystem() {
        SCHEDULER_LOG_INFO("=== 初始化精确激活系统 ===");

        // 清理旧的激活事件
        _precise_activation_events.clear();
        _task_next_activation_ms.clear();

        // 为所有已添加的任务安排激活
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            auto offset_it = _task_arrival_offsets.find(task);
            if (offset_it == _task_arrival_offsets.end())
                continue;

            int64_t arrival_offset = static_cast<int64_t>(offset_it->second);
            int64_t current_ms = static_cast<int64_t>(SIMUL.getTime());

            if (arrival_offset <= current_ms) {
                SCHEDULER_LOG_INFO("立即激活（已过时）: " + getTaskShortName(task) +
                         " 到达时间: " + std::to_string(arrival_offset) + "ms" +
                         " 当前时间: " + std::to_string(current_ms) + "ms");

                activateTaskAtExactTime(
                    task, MetaSim::Tick(
                              static_cast<MetaSim::Tick::impl_t>(current_ms)));

                int period = _task_periods[task];
                if (period > 0) {
                    int64_t next_activation = current_ms + period;
                    schedulePreciseActivationEvent(task, next_activation);
                }
            } else {
                schedulePreciseActivationEvent(task, arrival_offset);
            }
        }

        SCHEDULER_LOG_INFO("精确激活系统初始化完成: " +
                 std::to_string(_precise_activation_events.size()) +
                 " 个激活事件已安排");
    }

    // =====================================================
    // 事件处理
    // =====================================================

    void GPFPBatchScheduler::notify(AbsRTTask *task) {
        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 处理可能错过的激活
        checkAndProcessAllMissedActivations(current_time);

        // 更新能量收集
        double harvested = updateEnergyContinuously(current_time);
        if (harvested > 0) {
            SCHEDULER_LOG_DEBUG("能量收集: " + std::to_string(harvested) + "J");
        }

        // 执行调度
        schedule();

        // 原有的父类调用
        Scheduler::notify(task);
    }

    void GPFPBatchScheduler::checkAndProcessAllMissedActivations(
        MetaSim::Tick current_time) {
        int64_t current_ms = static_cast<int64_t>(current_time);

        // === 关键修复：每次调度都检查所有错过的激活事件 ===
        auto it = _precise_activation_events.begin();
        bool missed_events_found = false;

        while (it != _precise_activation_events.end() &&
               it->first < current_ms) {
            BatchTaskActivationEvent event = it->second;
            int64_t scheduled_time = it->first;

            // 计算时间偏差
            int64_t time_diff = current_ms - scheduled_time;

            // 如果偏差超过10ms，记录为错过的激活事件
            if (time_diff > 10) {
                SCHEDULER_LOG_INFO("🕐 检测到错过的激活事件: 任务=" + event.task_name +
                         " 计划时间=" + std::to_string(scheduled_time) + "ms" +
                         " 当前时间=" + std::to_string(current_ms) + "ms" +
                         " 偏差=" + std::to_string(time_diff) + "ms");
                missed_events_found = true;
            } else {
                // 小偏差，正常处理
                SCHEDULER_LOG_DEBUG("正常处理激活事件: " + event.task_name +
                          " 计划时间=" + std::to_string(scheduled_time) + "ms" +
                          " 偏差=" + std::to_string(time_diff) + "ms");
            }

            // 执行激活
            onTaskActivationTimer(event);

            // 如果是周期性任务，安排下一次激活
            if (event.is_periodic && event.period > 0) {
                // 使用当前时间计算下一个周期，而不是错过的计划时间
                int64_t next_activation = current_ms + event.period;

                // 检查是否已安排
                bool already_scheduled = false;
                for (const auto &pair : _precise_activation_events) {
                    if (pair.second.task == event.task) {
                        int64_t existing_time = pair.first;
                        int64_t time_diff =
                            std::abs(existing_time - next_activation);
                        if (time_diff <= 100) {
                            already_scheduled = true;
                            SCHEDULER_LOG_DEBUG("周期性任务 " + event.task_name +
                                      " 下次激活已安排: " +
                                      std::to_string(existing_time) + "ms");
                            break;
                        }
                    }
                }

                if (!already_scheduled) {
                    schedulePreciseActivationEvent(event.task, next_activation);
                    SCHEDULER_LOG_DEBUG("安排周期性任务下次激活: " + event.task_name +
                              " 周期=" + std::to_string(event.period) + "ms" +
                              " 下次激活=" + std::to_string(next_activation) +
                              "ms");
                }
            }

            // 移除已处理的事件
            it = _precise_activation_events.erase(it);
        }

        if (missed_events_found) {
            SCHEDULER_LOG_INFO("✅ 已处理所有错过的激活事件");
        }
    }

    // =====================================================
    // BATCH核心调度算法 - 修复版
    // =====================================================

    void GPFPBatchScheduler::schedule() {
        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        static int schedule_count = 0;
        schedule_count++;
        bool should_log_detail =
            (schedule_count <= 10 || schedule_count % 50 == 0);

        if (should_log_detail) {
            SCHEDULER_LOG_DEBUG("=== BATCH调度 #" + std::to_string(schedule_count) +
                      " ===");
            SCHEDULER_LOG_DEBUG("当前仿真时间: " + std::to_string(current_ms) + "ms");
        }

        // ========== 新增：调度间隔控制 ==========
        static int64_t last_schedule_time_ms = 0;
        int64_t time_since_last_schedule = current_ms - last_schedule_time_ms;
        
        // 检查是否有任务激活事件需要处理
        bool has_task_activation_events = false;
        if (!_precise_activation_events.empty()) {
            auto it = _precise_activation_events.begin();
            if (it->first <= current_ms) {
                has_task_activation_events = true;
            }
        }
        
        // 如果不是调度周期（50ms）且没有任务激活事件，跳过调度
        if (time_since_last_schedule < _unit_time && !has_task_activation_events) {
            if (should_log_detail) {
                SCHEDULER_LOG_DEBUG("跳过调度: 距离上次调度仅" + 
                          std::to_string(time_since_last_schedule) + "ms < " + 
                          std::to_string(_unit_time) + "ms，且无任务激活事件");
            }
            return;
        }
        
        // 更新最后调度时间
        last_schedule_time_ms = current_ms;

        // ========== 步骤1: 处理激活事件 ==========
        checkAndProcessAllMissedActivations(current_time);
        processPreciseActivations(current_ms);
        checkScheduledActivations(current_time);

        // ========== 步骤2: 更新能量收集 ==========
        double harvested = updateEnergyContinuously(current_time);
        if (harvested > 0.001) {
            SCHEDULER_LOG_DEBUG("能量收集: " + std::to_string(harvested) + "J");
        }

        // ========== 步骤3: 获取当前能量状态 ==========
        double current_energy = getCurrentEnergy();

        if (should_log_detail) {
            SCHEDULER_LOG_DEBUG("当前系统能量: " + std::to_string(current_energy) + " J");
            SCHEDULER_LOG_DEBUG("活跃任务数: " + std::to_string(_active_tasks.size()));
            SCHEDULER_LOG_DEBUG("核心数: " + std::to_string(_num_cores));
        }

        // ========== 步骤4: 终止条件检查 ==========
        if (areAllTasksCompleted() && _running_tasks.empty()) {
            SCHEDULER_LOG_INFO("✅ 所有任务已完成！");
            printStats();
            return;
        }

        // ========== 步骤5: 处理能量恢复状态 ==========
        // 检查恢复完成条件
        if (_recovery_in_progress && _recovery_end_time > 0) {
            int64_t recovery_end_ms = static_cast<int64_t>(_recovery_end_time);

            if (current_ms >= recovery_end_ms) {
                SCHEDULER_LOG_INFO("✅ BATCH恢复时间已到，检查能量状态");

                double new_energy = getCurrentEnergy();
                if (new_energy >= _recovery_required_energy) {
                    SCHEDULER_LOG_INFO(
                        "🎉 BATCH恢复完成: 能量=" + std::to_string(new_energy) +
                        "J >= " + std::to_string(_recovery_required_energy) +
                        "J");
                    _recovery_in_progress = false;
                    _recovery_target = nullptr;
                    _recovery_required_energy = 0.0;
                    _recovery_batch_tasks.clear();
                    _recovery_end_time = 0;
                    _consecutive_waits = 0;
                } else {
                    SCHEDULER_LOG_WARNING(
                        "BATCH恢复未完成: 能量=" + std::to_string(new_energy) +
                        "J < " + std::to_string(_recovery_required_energy) +
                        "J");
                }
            } else {
                // 恢复仍在进行中
                int64_t remaining_ms = recovery_end_ms - current_ms;
                if (remaining_ms > 1000) {
                    SCHEDULER_LOG_DEBUG("⏳ BATCH恢复中... 剩余时间: " +
                              std::to_string(remaining_ms) + "ms");
                }
                return; // 继续等待
            }
        }

        // ========== 步骤6: BATCH核心算法 ==========
        // 6.1 获取按RM优先级排序的active任务
        std::vector<AbsRTTask *> active_tasks = getActiveTasksByRMPriority();

        if (active_tasks.empty()) {
            if (should_log_detail) {
                SCHEDULER_LOG_DEBUG("无活跃任务，跳过调度");
            }
            return;
        }

        if (should_log_detail) {
            SCHEDULER_LOG_DEBUG("活跃任务排序(周期从小到大):");
            for (size_t i = 0; i < active_tasks.size(); ++i) {
                AbsRTTask *task = active_tasks[i];
                int period = _task_periods[task];
                SCHEDULER_LOG_DEBUG("  " + std::to_string(i + 1) + ". " +
                          getTaskShortName(task) +
                          " 周期: " + std::to_string(period) + "ms");
            }
        }

        // 6.2 BATCH核心逻辑: 选择前K个任务，K = min(M, N)
        // M = 核心数，N = 活跃任务数
        int M = _num_cores;
        int N = static_cast<int>(active_tasks.size());
        int K = std::min(M, N);

        std::vector<AbsRTTask *> batch_tasks;
        for (int i = 0; i < K; ++i) {
            batch_tasks.push_back(active_tasks[i]);
        }

        SCHEDULER_LOG_DEBUG("BATCH候选任务: " + std::to_string(batch_tasks.size()) +
                  "个 (M=" + std::to_string(M) + ", N=" + std::to_string(N) +
                  ", K=" + std::to_string(K) + ")");

        // 6.3 计算批量任务的总能量需求
        double batch_energy_required =
            calculateBatchEnergyRequired(batch_tasks);

        if (should_log_detail) {
            SCHEDULER_LOG_DEBUG("批量能量需求: " + std::to_string(batch_energy_required) +
                      " J");
            SCHEDULER_LOG_DEBUG("当前可用能量: " + std::to_string(current_energy) + " J");
        }

        // 6.4 BATCH核心检查 - 能量是否足够执行整个批量
        if (current_energy >= batch_energy_required) {
            // 能量足够 → 执行批量调度
            SCHEDULER_LOG_INFO("✅ BATCH: 能量充足，执行批量调度");

            bool batch_executed = executeBatch(batch_tasks, current_time);

            if (batch_executed) {
                // 批量执行成功
                SCHEDULER_LOG_INFO("🎯 BATCH调度成功: " +
                         std::to_string(batch_tasks.size()) + "个任务");

                // 统一处理所有已完成的任务
                processCompletedTasks();

                // 重置恢复状态
                _recovery_in_progress = false;
                _consecutive_waits = 0;
            } else {
                SCHEDULER_LOG_ERROR("BATCH调度执行失败");
                _stats.total_skipped_energy++;
            }
        } else {
            // ========== 能量不足，等待恢复 ==========
            SCHEDULER_LOG_INFO("⚡ BATCH: 能量不足，等待恢复！");
            SCHEDULER_LOG_INFO("  批量任务数: " + std::to_string(batch_tasks.size()));
            SCHEDULER_LOG_INFO("  需要能量: " + std::to_string(batch_energy_required) +
                     " J");
            SCHEDULER_LOG_INFO("  可用能量: " + std::to_string(current_energy) + " J");
            SCHEDULER_LOG_INFO("  缺少: " +
                     std::to_string(batch_energy_required - current_energy) +
                     " J");

            _stats.total_skipped_energy++;

            // 处理能量恢复
            if (_enable_energy_recovery) {
                // 检查微小能量缺口（避免恢复循环）
                double energy_gap = batch_energy_required - current_energy;

                if (energy_gap <= 0.001) { // 小于1mJ
                SCHEDULER_LOG_INFO("微小能量缺口(" + std::to_string(energy_gap) +
                         "J)，等待下一次调度");
                    return;
                }

                // 检查是否已在恢复中
                if (_recovery_in_progress &&
                    abs(_recovery_required_energy - batch_energy_required) <
                        0.001) {
                    SCHEDULER_LOG_DEBUG("已在为同一批量恢复能量，跳过");
                    return;
                }

                // ========== 启动批量能量恢复 ==========
                _recovery_target =
                    batch_tasks.empty() ? nullptr : batch_tasks[0];
                _recovery_required_energy = batch_energy_required;
                _recovery_batch_tasks = batch_tasks;
                _recovery_start_time = current_time;
                _recovery_in_progress = true;

                // 记录恢复开始信息
                SCHEDULER_LOG_INFO("🚨 启动BATCH能量恢复: " +
                         std::to_string(batch_tasks.size()) + "个任务" +
                         " 需要能量=" + std::to_string(batch_energy_required) +
                         "J" + " 当前能量=" + std::to_string(current_energy) +
                         "J" + " 能量缺口=" + std::to_string(energy_gap) + "J");

                // 获取当前收集率来计算理论恢复时间
                TimeMs adjusted_time = getAdjustedTime(current_time);
                double harvest_rate =
                    EnergyBridge::getInstance().getHarvestingRate(
                        adjusted_time);

                if (harvest_rate <= 0) {
                    SCHEDULER_LOG_WARNING("收集率为0，恢复失败");
                    _recovery_in_progress = false;
                    _recovery_target = nullptr;
                    _recovery_required_energy = 0.0;
                    _recovery_batch_tasks.clear();
                    return;
                }

                // 计算理论恢复时间
                double estimated_wait_ms = energy_gap / harvest_rate;

                // 设置恢复结束时间
                _recovery_end_time =
                    current_time +
                    MetaSim::Tick(
                        static_cast<MetaSim::Tick::impl_t>(estimated_wait_ms));

                SCHEDULER_LOG_INFO(
                    "BATCH恢复计划: 开始=" + std::to_string(current_ms) + "ms" +
                    " 预计结束=" +
                    std::to_string(static_cast<int64_t>(_recovery_end_time)) +
                    "ms" + " 理论等待=" + std::to_string(estimated_wait_ms) +
                    "ms");

                // 更新统计
                _stats.total_recovery_waits++;
                _consecutive_waits++;

                // 恢复期间，我们仍然可以处理能量收集
                SCHEDULER_LOG_DEBUG("⏳ BATCH能量恢复中，跳过批量调度");
                return;
            }
        }

        // ========== 步骤7: 记录统计 ==========
        _last_schedule_time = current_time;

        if (schedule_count % 100 == 0) {
            SCHEDULER_LOG_INFO(
                "📊 BATCH调度统计 - 已完成: " +
                std::to_string(_stats.total_task_completions) +
                ", 批量执行: " + std::to_string(_stats.total_batch_executions) +
                ", 部分批量: " + std::to_string(_stats.total_partial_batches) +
                ", 能量消耗: " + std::to_string(_stats.total_energy_consumed) +
                "J");
        }

        // ========== 步骤8: 额外检查任务完成状态 ==========
        // 确保不会因为调度时机而错过任务完成检查
        static int last_completion_check = 0;
        if (current_ms - last_completion_check > 100) { // 每100ms检查一次
            processCompletedTasks();
            last_completion_check = current_ms;
        }
    }

    void GPFPBatchScheduler::handleBatchEnergyRecovery(
        double required_batch_energy, MetaSim::Tick current_time) {
        // 调用简化版本的恢复处理
        handleBatchEnergyRecoverySimple(current_time);
    }

    // =====================================================
    // 能量管理方法
    // =====================================================

    double GPFPBatchScheduler::getCurrentEnergy() const {
        return EnergyBridge::getInstance().getCurrentEnergy();
    }

    bool GPFPBatchScheduler::hasSufficientEnergy(double required_energy) const {
        double current_energy = getCurrentEnergy();
        bool sufficient = required_energy <= current_energy;

        if (!sufficient) {
            SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Insufficient energy - required: " +
                     std::to_string(required_energy) +
                     " J, available: " + std::to_string(current_energy) + " J");
        }

        return sufficient;
    }

    bool GPFPBatchScheduler::consumeEnergy(double energy_joules,
                                           const std::string &task_name) {
        bool success =
            EnergyBridge::getInstance().consumeEnergy(energy_joules, task_name);
        if (success) {
            _stats.total_energy_consumed += energy_joules;
        }
        return success;
    }

    double GPFPBatchScheduler::updateEnergyContinuously(TimeMs current_time) {
        double harvested =
            EnergyBridge::getInstance().updateEnergyContinuously(current_time);
        _stats.total_energy_harvested += harvested;
        return harvested;
    }

    bool GPFPBatchScheduler::waitForEnergyRecovery(double required_energy,
                                                   MetaSim::Tick current_time) {
        if (!_enable_energy_recovery) {
            return false;
        }

        TimeMs adjusted_time = getAdjustedTime(current_time);
        return EnergyBridge::getInstance().waitForEnergyRecovery(
            required_energy, adjusted_time, 10000);
    }

    // =====================================================
    // 新增：任务执行封装函数（类似CASCADE的优化）
    // =====================================================
    bool GPFPBatchScheduler::executeTaskWithEnergyCheck(AbsRTTask *task,
                                                          MetaSim::Tick current_time) {
        if (!task) {
            return false;
        }

        std::string task_name = getTaskShortName(task);
        double unit_energy = getUnitTimeEnergy(task);

        // 1. 检查能量是否足够
        if (!hasSufficientEnergy(unit_energy)) {
            SCHEDULER_LOG_DEBUG("能量不足，无法执行任务: " + task_name);
            return false;
        }

        // 2. 消耗能量
        if (!consumeEnergy(unit_energy, task_name + "_batch_execute")) {
            SCHEDULER_LOG_WARNING("能量消耗失败: " + task_name);
            return false;
        }

        // 3. 获取任务状态
        auto remaining_it = _task_remaining_time.find(task);
        if (remaining_it == _task_remaining_time.end()) {
            SCHEDULER_LOG_DEBUG("任务剩余时间未初始化: " + task_name);
            return false;
        }

        int &remaining = remaining_it->second;
        int wcet = _task_wcets[task];

        // 4. 计算执行时间
        int time_needed = remaining;
        int time_to_execute = time_needed;

        // 5. 更新剩余时间
        remaining -= time_to_execute;

        SCHEDULER_LOG_DEBUG("BATCH精确执行: " + task_name +
                  " 需要: " + std::to_string(time_needed) + "ms" +
                  " 执行: " + std::to_string(time_to_execute) + "ms" +
                  " 剩余: " + std::to_string(remaining) + "ms");

        // 6. 分配核心
        if (!isTaskRunning(task)) {
            int core_id = findAvailableCore();
            if (core_id >= 0) {
                assignTaskToCore(task, core_id);
                _running_tasks.push_back(task);
            }
        }

        // 7. 检查任务是否完成
        if (remaining <= 0) {
            // 计算理论完成时间
            auto start_time_it = _task_start_times.find(task);
            if (start_time_it != _task_start_times.end()) {
                MetaSim::Tick start_time = start_time_it->second;
                MetaSim::Tick theoretical_completion = start_time + MetaSim::Tick(wcet);

                SCHEDULER_LOG_INFO("✅ BATCH任务理论完成: " + task_name + " @ " +
                         std::to_string(static_cast<int64_t>(theoretical_completion)) +
                         "ms" + " (WCET: " + std::to_string(wcet) + "ms)");

                // 使用理论完成时间
                completeTaskExecution(task);
            } else {
                // 如果没有开始时间记录，使用当前时间
                int64_t current_ms = static_cast<int64_t>(current_time);
                SCHEDULER_LOG_INFO("✅ BATCH任务完成: " + task_name + " @ " +
                         std::to_string(current_ms) + "ms");

                completeTaskExecution(task);
            }
        }

        // 8. 更新统计信息
        _stats.total_scheduled++;
        _stats.total_energy_consumed += unit_energy;

        return true;
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

double GPFPBatchScheduler::getUnitTimeEnergy(AbsRTTask *task) const {
        if (!task) {
            return _base_power * (_unit_time / 1000.0);
        }

        auto workload_it = _task_workloads.find(task);
        std::string workload_type = (workload_it != _task_workloads.end())
                                        ? workload_it->second
                                        : "control";

        // ========== 能量计算缓存优化 ==========
        static std::unordered_map<std::string, double> energy_cache;
        static double last_frequency = -1.0;
        static double last_base_power = -1.0;
        static int last_unit_time = -1;

        // 如果系统参数发生变化，清空缓存
        if (_current_frequency != last_frequency ||
            _base_power != last_base_power ||
            _unit_time != last_unit_time) {
            energy_cache.clear();
            last_frequency = _current_frequency;
            last_base_power = _base_power;
            last_unit_time = _unit_time;
        }

        // 检查缓存
        auto cache_it = energy_cache.find(workload_type);
        if (cache_it != energy_cache.end()) {
            return cache_it->second;
        }

        // 计算并缓存结果
        double workload_power = getWorkloadPower(workload_type);
        double frequency_ratio = getFrequencyPowerRatio(_current_frequency);
        double total_power = _base_power + workload_power * frequency_ratio;
        double unit_energy = total_power * (_unit_time / 1000.0);

        // 存入缓存
        energy_cache[workload_type] = unit_energy;

        // 调试信息：只在第一次计算时输出
        static std::unordered_set<std::string> logged_workloads;
        if (logged_workloads.find(workload_type) == logged_workloads.end()) {
            SCHEDULER_LOG_DEBUG("BATCH能量计算缓存: " + workload_type +
                      " 功率=" + std::to_string(workload_power) + "W" +
                      " 频率比例=" + std::to_string(frequency_ratio) +
                      " 总功率=" + std::to_string(total_power) + "W" +
                      " 单位时间能量=" + std::to_string(unit_energy) + "J");
            logged_workloads.insert(workload_type);
        }

        return unit_energy;
    }

    double GPFPBatchScheduler::calculateTaskEnergy(
        AbsRTTask *task, MetaSim::Tick execution_time) const {
        std::string task_name = getTaskShortName(task);
        auto workload_it = _task_workloads.find(task);
        std::string workload_type = (workload_it != _task_workloads.end())
                                        ? workload_it->second
                                        : "control";

        double workload_power = getWorkloadPower(workload_type);
        double frequency_ratio = getFrequencyPowerRatio(_current_frequency);
        double total_power = _base_power + workload_power * frequency_ratio;
        double execution_time_s = tickToSeconds(execution_time);
        double energy = total_power * execution_time_s;

        return energy;
    }

    double GPFPBatchScheduler::getWorkloadPower(
        const std::string &workload_type) const {
        auto it = _power_coefficients.find(workload_type);
        if (it != _power_coefficients.end()) {
            return it->second;
        }

        SCHEDULER_LOG_WARNING("未知工作负载类型: " + workload_type +
                    "，使用默认功率 0.1 W");
        return 0.1;
    }

    double GPFPBatchScheduler::getFrequencyPowerRatio(double frequency) const {
        double closest_freq = 1400.0;
        double min_diff = std::numeric_limits<double>::max();

        for (const auto &pair : _frequency_power_ratios) {
            double diff = std::abs(pair.first - frequency);
            if (diff < min_diff) {
                min_diff = diff;
                closest_freq = pair.first;
            }
        }

        auto it = _frequency_power_ratios.find(closest_freq);
        if (it != _frequency_power_ratios.end()) {
            return it->second;
        }

        return 1.0;
    }

    // =====================================================
    // 配置和验证方法
    // =====================================================

    void GPFPBatchScheduler::setStartTimeOffset(MetaSim::Tick offset) {
        _start_time_offset = offset;
        EnergyBridge::getInstance().setStartTimeOffset(offset);

        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Start time offset set to " +
                 std::to_string(static_cast<int64_t>(offset)) + " ms");
    }

    std::string GPFPBatchScheduler::getEnergyStatus() const {
        return EnergyBridge::getInstance().getEnergyStatus();
    }

    TimeMs GPFPBatchScheduler::getAdjustedTime(MetaSim::Tick tick) const {
        int64_t sim_time_ms = static_cast<int64_t>(tick);
        int64_t start_offset_ms = static_cast<int64_t>(_start_time_offset);
        int64_t total_ms = sim_time_ms + start_offset_ms;

        // 调试输出（限制频率）
        static int debug_count = 0;
        if (debug_count++ < 50) {
            int64_t hour = (total_ms / 3600000) % 24;
            int64_t minute = (total_ms % 3600000) / 60000;
            int64_t second = (total_ms % 60000) / 1000;

            SCHEDULER_LOG_DEBUG("时间转换: 仿真时间=" + std::to_string(sim_time_ms) +
                      "ms + 偏移=" + std::to_string(start_offset_ms) +
                      "ms = " + std::to_string(total_ms) + "ms" + " (" +
                      std::to_string(hour) + ":" + std::to_string(minute) +
                      ":" + std::to_string(second) + ")");
        }

        return static_cast<TimeMs>(total_ms);
    }

    double GPFPBatchScheduler::tickToSeconds(MetaSim::Tick tick) const {
        return static_cast<double>(tick) / 1000.0;
    }

    std::string GPFPBatchScheduler::getTaskName(AbsRTTask *task) const {
        if (!task)
            return "null";
        return task->toString();
    }

    void GPFPBatchScheduler::validateEnergyCalculations() {
        SCHEDULER_LOG_INFO("=== 能量计算精确验证 ===");

        double base_power = _base_power;
        int unit_time_ms = _unit_time;

        SCHEDULER_LOG_INFO("基础参数: 基础功耗=" + std::to_string(base_power) +
                 "W, 单位时间=" + std::to_string(unit_time_ms) + "ms");

        std::vector<std::string> workloads = {"encrypt", "decrypt", "hash",
                                              "bzip2", "control"};

        for (const auto &workload : workloads) {
            double workload_power = getWorkloadPower(workload);
            double freq_ratio = getFrequencyPowerRatio(_current_frequency);
            double total_power = base_power + workload_power * freq_ratio;
            double unit_energy_j = total_power * (unit_time_ms / 1000.0);

            SCHEDULER_LOG_INFO(workload + ":" +
                     " 工作负载功率=" + std::to_string(workload_power) + "W" +
                     " 频率比例=" + std::to_string(freq_ratio) +
                     " 总功率=" + std::to_string(total_power) + "W" + " " +
                     std::to_string(unit_time_ms) +
                     "ms能耗=" + std::to_string(unit_energy_j) + "J");
        }

        SCHEDULER_LOG_INFO("=== 验证完成 ===");
    }

    void GPFPBatchScheduler::validateConfiguration() {
        SCHEDULER_LOG_INFO("=== 配置验证 ===");
        SCHEDULER_LOG_INFO("开始时间偏移: " +
                 std::to_string(static_cast<int64_t>(_start_time_offset)) +
                 " ms");

        double initial_energy = EnergyBridge::getInstance().getCurrentEnergy();
        SCHEDULER_LOG_INFO("初始能量: " + std::to_string(initial_energy) + " J");

        // === 修复：正确计算各工作负载的单位时间能量 ===
        SCHEDULER_LOG_INFO("各工作负载单位时间(50ms)能耗计算:");

        // 获取基础参数
        double base_power = _base_power;
        int unit_time_ms = _unit_time;
        double current_frequency = _current_frequency;

        std::vector<std::pair<std::string, double>> workloads = {
            {"encrypt", 1.5},
            {"decrypt", 1.5},
            {"hash", 0.8},
            {"bzip2", 1.2},
            {"control", 0.1}};

        for (const auto &[workload, workload_power] : workloads) {
            // 获取频率比例
            double freq_ratio = getFrequencyPowerRatio(current_frequency);

            // 计算总功率
            double total_power = base_power + workload_power * freq_ratio;

            // 计算单位时间能量
            double unit_energy = total_power * (unit_time_ms / 1000.0);

            SCHEDULER_LOG_INFO("  " + workload + ": " +
                     "工作负载功率=" + std::to_string(workload_power) + "W, " +
                     "频率比例=" + std::to_string(freq_ratio) + ", " +
                     "总功率=" + std::to_string(total_power) + "W, " +
                     std::to_string(unit_time_ms) +
                     "ms能耗=" + std::to_string(unit_energy) + "J");
        }

        // === 新增：验证任务能量计算 ===
        if (!_task_models.empty()) {
            SCHEDULER_LOG_INFO("\n任务能量计算验证:");

            // 取第一个任务作为样本
            auto it = _task_models.begin();
            AbsRTTask *sample_task = it->first;
            std::string task_name = getTaskShortName(sample_task);

            // 获取任务的工作负载类型
            auto workload_it = _task_workloads.find(sample_task);
            if (workload_it != _task_workloads.end()) {
                std::string workload_type = workload_it->second;
                double unit_energy = getUnitTimeEnergy(sample_task);

                SCHEDULER_LOG_INFO("  样本任务: " + task_name);
                SCHEDULER_LOG_INFO("  工作负载: " + workload_type);
                SCHEDULER_LOG_INFO("  单位时间能量: " + std::to_string(unit_energy) +
                         " J");

                // 手动计算验证
                double workload_power = getWorkloadPower(workload_type);
                double freq_ratio = getFrequencyPowerRatio(_current_frequency);
                double total_power = _base_power + workload_power * freq_ratio;
                double manual_energy = total_power * (_unit_time / 1000.0);

                if (abs(unit_energy - manual_energy) > 0.001) {
                    SCHEDULER_LOG_WARNING("  警告: 能量计算不一致!");
                    SCHEDULER_LOG_WARNING("    函数计算: " + std::to_string(unit_energy) +
                                " J");
                    SCHEDULER_LOG_WARNING("    手动计算: " +
                                std::to_string(manual_energy) + " J");
                } else {
                    SCHEDULER_LOG_INFO("  ✓ 能量计算验证通过");
                }
            }
        }

        SCHEDULER_LOG_INFO("=== 验证完成 ===");
    }

    // =====================================================
    // 调试和统计方法
    // =====================================================

    void GPFPBatchScheduler::printStats() const {
        SCHEDULER_LOG_INFO("\n=== GPFP_BATCH Scheduler Statistics ===");
        SCHEDULER_LOG_INFO("Total tasks scheduled: " +
                 std::to_string(_stats.total_scheduled));
        SCHEDULER_LOG_INFO("Total tasks completed: " +
                 std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO("Total tasks skipped due to energy: " +
                 std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO("Total energy recovery waits: " +
                 std::to_string(_stats.total_recovery_waits));
        SCHEDULER_LOG_INFO("Total batch executions (full): " +
                 std::to_string(_stats.total_batch_executions));
        SCHEDULER_LOG_INFO("Total partial batches: " +
                 std::to_string(_stats.total_partial_batches));
        SCHEDULER_LOG_INFO("Total energy consumed: " +
                 std::to_string(_stats.total_energy_consumed) + " J");
        SCHEDULER_LOG_INFO("Total energy harvested: " +
                 std::to_string(_stats.total_energy_harvested) + " J");
        SCHEDULER_LOG_INFO("Total batch energy required: " +
                 std::to_string(_stats.total_batch_energy_required) + " J");
        SCHEDULER_LOG_INFO("Consecutive waits: " + std::to_string(_consecutive_waits));
        SCHEDULER_LOG_INFO("Running tasks: " + std::to_string(_running_tasks.size()));
        SCHEDULER_LOG_INFO("Completed tasks: " + std::to_string(_completed_tasks.size()));
        SCHEDULER_LOG_INFO("Start time offset: " +
                 std::to_string(static_cast<int64_t>(_start_time_offset)) +
                 " ms");

        SCHEDULER_LOG_INFO("Task details:");
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            GPFPBatchTaskModel *model = pair.second;
            bool is_running = isTaskRunning(task);
            bool is_completed = isTaskCompleted(task);

            std::string status;
            if (is_completed)
                status = "COMPLETED";
            else if (is_running) {
                auto it = _task_remaining_time.find(task);
                int remaining =
                    (it != _task_remaining_time.end()) ? it->second : 0;
                status =
                    "RUNNING (" + std::to_string(remaining) + "ms remaining)";
            } else {
                status = "WAITING";
            }

            SCHEDULER_LOG_INFO("  " + getTaskShortName(task) +
                     " - Period: " + std::to_string(model->getPeriod()) +
                     " ms, WCET: " + std::to_string(model->getWCET()) +
                     " ms, Workload: " + model->getWorkloadType() +
                     ", Status: " + status);
        }

        SCHEDULER_LOG_INFO("=======================================");
    }

    void GPFPBatchScheduler::debugTaskInfo() const {
        SCHEDULER_LOG_INFO("\n=== Task Information Debug ===");
        SCHEDULER_LOG_INFO("Total tasks: " + std::to_string(_task_models.size()));

        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            std::string original_name = "unknown";
            if (_task_original_names.find(task) != _task_original_names.end()) {
                original_name = _task_original_names.at(task);
            }

            auto it_period = _task_periods.find(task);
            int period =
                (it_period != _task_periods.end()) ? it_period->second : 1000;

            auto it_wcet = _task_wcets.find(task);
            int wcet = (it_wcet != _task_wcets.end()) ? it_wcet->second : 100;

            auto it_workload = _task_workloads.find(task);
            std::string workload = (it_workload != _task_workloads.end())
                                       ? it_workload->second
                                       : "control";

            SCHEDULER_LOG_INFO("Task: " + getTaskShortName(task));
            SCHEDULER_LOG_INFO("  Original name: " + original_name);
            SCHEDULER_LOG_INFO("  Period: " + std::to_string(period));
            SCHEDULER_LOG_INFO("  WCET: " + std::to_string(wcet));
            SCHEDULER_LOG_INFO("  Workload: " + workload);
            SCHEDULER_LOG_INFO("  Priority: " + std::to_string(-period));
        }
        SCHEDULER_LOG_INFO("=== End Task Information Debug ===");
    }

    void GPFPBatchScheduler::debugRunningTasks() const {
        SCHEDULER_LOG_INFO("=== 运行中任务调试信息 ===");
        SCHEDULER_LOG_INFO("总运行中任务: " + std::to_string(_running_tasks.size()));

        for (AbsRTTask *task : _running_tasks) {
            auto remaining_it = _task_remaining_time.find(task);
            auto executed_it = _task_executed_time.find(task);

            int remaining = (remaining_it != _task_remaining_time.end())
                                ? remaining_it->second
                                : -1;
            int executed = (executed_it != _task_executed_time.end())
                               ? executed_it->second
                               : -1;

            SCHEDULER_LOG_INFO("  任务: " + getTaskShortName(task) +
                     "，剩余时间: " + std::to_string(remaining) + "ms" +
                     "，已执行: " + std::to_string(executed) + "ms");
        }

        SCHEDULER_LOG_INFO("=== 调试信息结束 ===");
    }

    void GPFPBatchScheduler::debugActiveTasks() const {
        SCHEDULER_LOG_INFO("\n=== Active Tasks Debug ===");
        SCHEDULER_LOG_INFO("活跃任务数: " + std::to_string(_active_tasks.size()));

        for (AbsRTTask *task : _active_tasks) {
            std::string task_name = getTaskShortName(task);
            bool is_running = isTaskRunning(task);
            bool is_completed = isTaskCompleted(task);

            auto remaining_it = _task_remaining_time.find(task);
            int remaining = (remaining_it != _task_remaining_time.end())
                                ? remaining_it->second
                                : -1;

            SCHEDULER_LOG_INFO(
                "  任务: " + task_name + " 状态: " +
                (is_completed ? "已完成" : (is_running ? "运行中" : "等待中")) +
                " 剩余时间: " + std::to_string(remaining) + "ms");
        }
        SCHEDULER_LOG_INFO("=== End Active Tasks Debug ===");
    }

    void GPFPBatchScheduler::printActivationStatus() const {
        SCHEDULER_LOG_INFO("=== 任务激活状态 ===");
        SCHEDULER_LOG_INFO("当前仿真时间: " +
                 std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms");
        SCHEDULER_LOG_INFO("已安排激活事件: " +
                 std::to_string(_precise_activation_events.size()) + " 个");

        // 显示前10个激活事件
        int count = 0;
        for (const auto &[time, event] : _precise_activation_events) {
            if (count++ < 10) {
                SCHEDULER_LOG_INFO("  事件: " + event.task_name + " @ " +
                         std::to_string(time) + "ms");
            } else {
                SCHEDULER_LOG_INFO(
                    "  还有 " +
                    std::to_string(_precise_activation_events.size() - 10) +
                    " 个事件...");
                break;
            }
        }

        // 显示任务状态
        SCHEDULER_LOG_INFO("任务状态:");
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            std::string task_name = getTaskShortName(task);
            bool is_active = isTaskActive(task);
            bool is_completed = isTaskCompleted(task);
            bool is_running = isTaskRunning(task);

            auto offset_it = _task_arrival_offsets.find(task);
            int64_t arrival_offset =
                (offset_it != _task_arrival_offsets.end())
                    ? static_cast<int64_t>(offset_it->second)
                    : -1;

            SCHEDULER_LOG_INFO("  " + task_name + ": 活跃=" + std::to_string(is_active) + " 完成=" + std::to_string(is_completed) + " 运行中=" + std::to_string(is_running) + " 到达偏移=" + std::to_string(arrival_offset) + "ms");
        }
        SCHEDULER_LOG_INFO("=== 状态结束 ===");
    }

    void GPFPBatchScheduler::initializeScheduler() {
        SCHEDULER_LOG_INFO("=== GPFPBatch调度器初始化 ===");
        MetaSim::Tick current_time = SIMUL.getTime();
        checkScheduledActivations(current_time);
        schedule();
        SCHEDULER_LOG_INFO("=== 初始化完成 ===");
    }

    // =====================================================
    // 其他接口方法
    // =====================================================

    void GPFPBatchScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            SCHEDULER_LOG_ERROR("GPFP_BATCH Scheduler: Cannot remove null task");
            return;
        }

        auto running_it =
            std::find(_running_tasks.begin(), _running_tasks.end(), task);
        if (running_it != _running_tasks.end()) {
            _running_tasks.erase(running_it);
        }

        _task_periods.erase(task);
        _task_wcets.erase(task);
        _task_workloads.erase(task);
        _task_remaining_time.erase(task);
        _task_executed_time.erase(task);
        _task_arrival_offsets.erase(task);
        _task_next_releases.erase(task);
        _active_tasks.erase(task);
        _completed_tasks.erase(task);
        _task_original_names.erase(task);

        auto model_it = _task_models.find(task);
        if (model_it != _task_models.end()) {
            extract(task);
            delete model_it->second;
            _task_models.erase(model_it);
            SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Task " + getTaskShortName(task) +
                     " removed");
        }
    }

    bool GPFPBatchScheduler::isAdmissible(CPU *c,
                                          std::vector<AbsRTTask *> tasks,
                                          AbsRTTask *t) {
        // 检查是否有足够能量执行批量
        std::vector<AbsRTTask *> test_tasks = tasks;
        test_tasks.push_back(t);

        int tasks_count =
            std::min(_num_cores, static_cast<int>(test_tasks.size()));
        double batch_energy_required = 0.0;

        for (int i = 0; i < tasks_count; ++i) {
            batch_energy_required += getUnitTimeEnergy(test_tasks[i]);
        }

        if (!hasSufficientEnergy(batch_energy_required)) {
            SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Task " + getTaskShortName(t) +
                     " not admissible due to insufficient energy for batch");
            return false;
        }

        if (tasks.size() >= _num_cores) {
            SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Task " + getTaskShortName(t) +
                     " not admissible due to no available cores");
            return false;
        }

        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Task " + getTaskShortName(t) +
                 " is admissible (batch energy sufficient)");
        return true;
    }

    void GPFPBatchScheduler::checkAndActivateTasks(MetaSim::Tick current_time) {
        static int last_debug_log = 0;
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 遍历所有任务模型
        for (const auto &pair : _task_models) {
            AbsRTTask *task = pair.first;
            std::string task_name = getTaskShortName(task);

            // 如果任务已经激活或已完成，跳过
            if (isTaskActive(task) || isTaskCompleted(task)) {
                continue;
            }

            // 获取任务的到达时间偏移
            auto offset_it = _task_arrival_offsets.find(task);
            if (offset_it == _task_arrival_offsets.end()) {
                continue;
            }

            MetaSim::Tick arrival_offset = offset_it->second;
            int64_t arrival_ms = static_cast<int64_t>(arrival_offset);

            // 检查是否到达首次激活时间
            if (current_ms == arrival_ms) {
                activateTaskAtExactTime(task, current_time);

                // 如果是周期性任务，设置下一次释放时间
                int period = _task_periods[task];
                if (period > 0) {
                    int64_t next_release_ms = arrival_ms + period;
                    _task_next_releases[task] = MetaSim::Tick(
                        static_cast<MetaSim::Tick::impl_t>(next_release_ms));
                }
            }
        }

        // 显示活跃任务统计
        if (!_active_tasks.empty() && (current_ms % 1000 == 0) &&
            (current_ms - last_debug_log > 1000)) {
            SCHEDULER_LOG_INFO("当前活跃任务数: " + std::to_string(_active_tasks.size()));
            last_debug_log = current_ms;
        }
    }

    // =====================================================
    // 析构函数
    // =====================================================

    GPFPBatchScheduler::~GPFPBatchScheduler() {
        for (auto *event : _scheduled_sim_events) {
            if (event) {
                event->drop();
            }
        }
        _scheduled_sim_events.clear();

        // 清理任务模型
        for (auto &pair : _task_models) {
            delete pair.second;
        }
        _task_models.clear();

        SCHEDULER_LOG_INFO("GPFP_BATCH Scheduler: Destroyed");
        printStats();
    }

} // namespace RTSim
