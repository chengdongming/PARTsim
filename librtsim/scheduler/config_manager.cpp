#include <fstream>
#include <iostream>
#include <memory>
#include <regex>
#include <rtsim/scheduler/config_manager.hpp>
#include <sstream>

// 统一日志系统
#include "../../utils/unified_logger.hpp"

namespace RTSim {

    // =====================================================
    // ConfigManager 的静态成员定义
    // =====================================================

    std::mutex ConfigManager::_instance_mutex;
    std::unique_ptr<ConfigManager> ConfigManager::_instance;
    ConfigManager::ConfigCallback ConfigManager::_config_callback = nullptr;

    // =====================================================
    // 构造函数
    // =====================================================

    ConfigManager::ConfigManager() :
        _config_loaded(false),
        _tasks_loaded(false),
        _num_cores(4),
        _scheduler_type("gpfp_asap"),
        _base_frequency(8100.0),  // ⭐ 修复：从1400.0改为8100.0，匹配YAML配置
        _unit_time(50),
        _initial_energy(0.3),  // 默认初始能量 0.3J
        _max_energy(600.0),
        _base_harvest_rate(0.054),  // 默认基础收集率：54W (300W/m² × 1m² × 0.18 = 54W)
        _start_time_offset(0),
        _enable_energy_recovery(true),
        _periodic_collection_interval(100),  // ⭐ 默认100ms周期性能量收集
        _base_power(0.5) {
        // === 修复：从环境变量读取核心数（如果设置了的话） ===
        const char *env_cores = std::getenv("RTSIM_NUM_CORES");
        if (env_cores != nullptr) {
            try {
                _num_cores = std::stoi(env_cores);
                SCHEDULER_LOG_INFO("ConfigManager: 从环境变量设置核心数: " + std::to_string(_num_cores));
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING("ConfigManager: 无法解析环境变量 RTSIM_NUM_CORES: " + std::string(e.what()));
            }
        }

        // ⭐ 修复：设置默认功率系数（与system_config_unified_template.yml一致）
        _power_coefficients = {{"bzip2", 1.2},
                               {"hash", 0.8},
                               {"encrypt", 1.5},
                               {"decrypt", 1.5},
                               {"control", 0.1},
                               {"idle", 0.1}};

        // ⭐ 修复：设置默认频率功率比（匹配YAML的7000-10500 MHz范围）
        _frequency_power_ratios = {
            {7000, 0.85},  {7500, 0.88}, {8000, 0.92},
            {8100, 0.93},  {8200, 0.94}, {8300, 0.95},
            {8400, 0.96},  {8500, 0.97}, {9000, 1.0},
            {9500, 1.05},  {10000, 1.1}, {10500, 1.15}
        };
    }

    // =====================================================
    // 获取单例
    // =====================================================

    ConfigManager &ConfigManager::getInstance() {
        std::lock_guard<std::mutex> lock(_instance_mutex);
        if (!_instance) {
            _instance = std::make_unique<ConfigManager>();
        }
        return *_instance;
    }

    // =====================================================
    // 设置配置回调
    // =====================================================

    void ConfigManager::setConfigCallback(ConfigCallback callback) {
        _config_callback = callback;
    }

    // =====================================================
    // 加载系统配置文件
    // =====================================================

    bool ConfigManager::loadSystemConfig(const std::string &config_file) {
        try {
            // 保存配置文件路径
            _config_file_path = config_file;
            SCHEDULER_LOG_INFO("ConfigManager: 保存配置文件路径: " + _config_file_path);

            // 如果有配置回调，使用回调（Python端）
            if (_config_callback) {
                bool result = _config_callback(config_file, *this);
                _config_loaded = result;
                return result;
            }

            // ⭐ 新增：尝试解析YAML文件中的scheduler_energy_model配置
            try {
                std::ifstream yaml_file(config_file);
                if (yaml_file.is_open()) {
                    std::string line;
                    bool in_scheduler_energy_model = false;
                    bool in_workload_coeffs = false;
                    bool in_freq_ratios = false;

                    while (std::getline(yaml_file, line)) {
                        // 去除首尾空白
                        size_t start = line.find_first_not_of(" \t");
                        if (start == std::string::npos) continue;
                        line = line.substr(start);

                        // 跳过注释和空行
                        if (line.empty() || line[0] == '#') continue;

                        // 检测scheduler_energy_model部分
                        if (line.find("scheduler_energy_model:") != std::string::npos) {
                            in_scheduler_energy_model = true;
                            SCHEDULER_LOG_INFO("ConfigManager: 找到scheduler_energy_model配置");
                            continue;
                        }

                        if (in_scheduler_energy_model) {
                            // 检测base_power
                            if (line.find("base_power:") != std::string::npos) {
                                size_t colon_pos = line.find(':');
                                std::string value = line.substr(colon_pos + 1);
                                // 去除注释
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                // 去除空白
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t\r\n") + 1);
                                _base_power = std::stod(value);
                                SCHEDULER_LOG_INFO("ConfigManager: base_power = " + std::to_string(_base_power));
                                continue;
                            }

                            // 检测workload_coefficients
                            if (line.find("workload_coefficients:") != std::string::npos) {
                                in_workload_coeffs = true;
                                in_freq_ratios = false;
                                continue;
                            }

                            if (line.find("frequency_power_ratios:") != std::string::npos) {
                                in_freq_ratios = true;
                                in_workload_coeffs = false;
                                continue;
                            }

                            // 解析工作负载系数
                            if (in_workload_coeffs && line.find(':') != std::string::npos) {
                                size_t colon_pos = line.find(':');
                                std::string key = line.substr(0, colon_pos);
                                std::string value = line.substr(colon_pos + 1);

                                // 清理key
                                key.erase(0, key.find_first_not_of(" \t"));
                                key.erase(key.find_last_not_of(" \t\r\n:") + 1);

                                // 清理value（去除注释）
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t\r\n") + 1);

                                _power_coefficients[key] = std::stod(value);
                                SCHEDULER_LOG_DEBUG("ConfigManager: " + key + " = " + value);
                                continue;
                            }

                            // 解析频率功率比
                            if (in_freq_ratios && line.find(':') != std::string::npos) {
                                size_t colon_pos = line.find(':');
                                std::string key = line.substr(0, colon_pos);
                                std::string value = line.substr(colon_pos + 1);

                                // 清理key（频率）
                                key.erase(0, key.find_first_not_of(" \t"));
                                key.erase(key.find_last_not_of(" \t\r\n:") + 1);
                                int freq = std::stoi(key);

                                // 清理value（功率比）
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t\r\n") + 1);
                                double ratio = std::stod(value);

                                _frequency_power_ratios[freq] = ratio;
                                SCHEDULER_LOG_DEBUG("ConfigManager: " + key + "MHz = " + value);
                                continue;
                            }

                            // 检测缩进减少，退出scheduler_energy_model
                            if (line[0] != ' ' && line[0] != '\t' && line.find(':') != std::string::npos) {
                                in_scheduler_energy_model = false;
                                in_workload_coeffs = false;
                                in_freq_ratios = false;
                            }
                        }
                    }
                    yaml_file.close();
                    SCHEDULER_LOG_INFO("ConfigManager: YAML解析完成");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING("ConfigManager: YAML解析失败，使用默认值: " + std::string(e.what()));
            }

            // 否则使用默认配置
            SCHEDULER_LOG_INFO("ConfigManager: 配置加载完成");
            _config_loaded = true;
            printConfig();
            return true;

        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("配置加载错误: " + std::string(e.what()));
            return false;
        }
    }

    void ConfigManager::setExpectedTaskCount(int count) {
        _expected_task_count = count;
    }

    int ConfigManager::getExpectedTaskCount() const {
        return _expected_task_count;
    }

    // =====================================================
    // 加载任务配置文件
    // =====================================================

    bool ConfigManager::loadTaskConfig(const std::string &task_file) {
        try {
            // 这里可以添加任务配置文件解析逻辑
            // 由于我们主要关注系统配置，这里简单标记为已加载
            _tasks_loaded = true;
            SCHEDULER_LOG_INFO("任务配置标记为已加载: " + task_file);
            return true;

        } catch (const std::exception &e) {
            SCHEDULER_LOG_ERROR("任务配置错误: " + std::string(e.what()));
            return false;
        }
    }

    // =====================================================
    // 获取功率系数
    // =====================================================

    double ConfigManager::getPowerCoefficient(
        const std::string &workload_type) const {
        auto it = _power_coefficients.find(workload_type);
        if (it != _power_coefficients.end()) {
            return it->second;
        }

        // 返回默认值
        SCHEDULER_LOG_WARNING("未知工作负载类型 '" + workload_type + "'，使用默认功率系数 1.0");
        return 1.0;
    }

    // =====================================================
    // 获取频率功率比
    // =====================================================

    double ConfigManager::getFrequencyPowerRatio(int frequency) const {
        // 找到最接近的频率
        int closest_freq = 8100;  // ⭐ 修复：从1400改为8100，匹配新的频率范围
        double min_diff = 10000.0;

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
    // 打印配置信息
    // =====================================================

    void ConfigManager::printConfig() const {
        SCHEDULER_LOG_INFO("\n=== 系统配置信息 ===");
        SCHEDULER_LOG_INFO("配置加载状态: " + std::string(_config_loaded ? "已加载" : "未加载"));
        SCHEDULER_LOG_INFO("任务加载状态: " + std::string(_tasks_loaded ? "已加载" : "未加载"));

        SCHEDULER_LOG_INFO("\nCPU配置:");
        SCHEDULER_LOG_INFO("  核心数: " + std::to_string(_num_cores));
        SCHEDULER_LOG_INFO("  调度器类型: " + _scheduler_type);
        SCHEDULER_LOG_INFO("  基础频率: " + std::to_string(_base_frequency) + " MHz");
        SCHEDULER_LOG_INFO("  单位时间: " + std::to_string(_unit_time) + " ms");

        SCHEDULER_LOG_INFO("\n能量配置:");
        SCHEDULER_LOG_INFO("  初始能量: " + std::to_string(_initial_energy) + " J");
        SCHEDULER_LOG_INFO("  最大能量: " + std::to_string(_max_energy) + " J");
        SCHEDULER_LOG_INFO("  基础收集率: " + std::to_string(_base_harvest_rate) + " J/ms");
        SCHEDULER_LOG_INFO("  开始时间偏移: " + std::to_string(_start_time_offset) + " ms");
        SCHEDULER_LOG_INFO("  能量恢复: " + std::string(_enable_energy_recovery ? "启用" : "禁用"));

        SCHEDULER_LOG_INFO("\n功率模型配置:");
        SCHEDULER_LOG_INFO("  基础功率: " + std::to_string(_base_power) + " W");
        SCHEDULER_LOG_INFO("  工作负载功率系数:");
        for (const auto &pair : _power_coefficients) {
            SCHEDULER_LOG_INFO("    " + pair.first + ": " + std::to_string(pair.second) + " W");
        }

        SCHEDULER_LOG_INFO("\n频率功率比:");
        for (const auto &pair : _frequency_power_ratios) {
            SCHEDULER_LOG_INFO("    " + std::to_string(pair.first) + " MHz: " + std::to_string(pair.second));
        }

        SCHEDULER_LOG_INFO("\n任务配置:");
        SCHEDULER_LOG_INFO("  任务数量: " + std::to_string(_tasks.size()));

        // 显示前几个任务的详细信息
        int show_count = std::min(3, static_cast<int>(_tasks.size()));
        for (int i = 0; i < show_count; ++i) {
            const auto &task = _tasks[i];
            SCHEDULER_LOG_INFO("  任务" + std::to_string(i) + ": " + task.name +
                     " (周期=" + std::to_string(task.period) + "ms, WCET=" + std::to_string(task.wcet) +
                     "ms, 工作负载=" + task.workload_type + ", 能耗≈" + std::to_string(task.energy_consumption) + "J)");
        }

        if (_tasks.size() > show_count) {
            SCHEDULER_LOG_INFO("  还有 " + std::to_string(_tasks.size() - show_count) + " 个任务...");
        }

        SCHEDULER_LOG_INFO("========================");
    }

} // namespace RTSim
