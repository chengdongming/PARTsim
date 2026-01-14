#ifndef ENERGY_BRIDGE_HPP
#define ENERGY_BRIDGE_HPP

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

              namespace RTSim {

    class ConfigManager;

    // Python兼容性函数声明
    bool pythonConfigCallback(const std::string &config_file,
                              ConfigManager &config);

    class EnergyBridge {
    public:
        // 单例模式
        static EnergyBridge &getInstance();

        // 初始化
        bool initialize(const std::string &python_script_path = ".");
        void shutdown();

        // 配置管理
        bool loadSystemConfig(const std::string &config_file);
        void setStartTimeOffset(int64_t offset);
        int64_t getAdjustedTime(int64_t current_time_ms) const;

        // 能量查询
        double getCurrentEnergy();
        double getHarvestingRate(int64_t current_time_ms);
        std::string getEnergyStatus();
        std::string getDetailedEnergyStatus();

        // ASAP专用接口
        bool checkAsapScheduling(double required_energy);

        // 能量操作
        bool consumeEnergy(double energy_joules, const std::string &task_name);
        void updateEnergyHarvesting(int64_t current_time_ms,
                                    int64_t duration_ms);
        double updateEnergyContinuously(int64_t current_time_ms);
        bool waitForEnergyRecovery(double required_energy,
                                   int64_t current_time_ms,
                                   int64_t max_wait_time_ms = 10000);

        // 批量操作
        bool hasSufficientEnergyForBatch(
            const std::vector<std::string> &task_workloads, double duration_ms);
        bool hasSufficientEnergy(double required_energy);

        // 能量计算
        double calculateTaskEnergy(const std::string &workload_type,
                                   double execution_time_ms,
                                   double frequency_mhz = 1400.0);

        // 状态同步
        double syncEnergyState();

        // 参数设置
        void setEnergyParameters(double initial_energy, double max_energy);

        // 工具函数
        int64_t convertToAbsoluteTime(int64_t simulation_time_ms) const;
        int64_t convertToSimulationTime(int64_t absolute_time_ms) const;
        bool validateTimeParameters(int64_t simulation_time_ms,
                                    const char *function_name) const;

        // 调试和统计
        int64_t getTotalCalls() const {
            return _total_calls;
        }
        bool isInitialized() const {
            return _initialized;
        }

        // 修复：添加Python错误处理相关方法
        void resetErrorCount() {
            _python_error_count = 0;
            _use_fallback_mode = false;
        }
        bool isUsingFallbackMode() const {
            return _use_fallback_mode;
        }

    private:
        // 单例管理
        EnergyBridge();
        ~EnergyBridge();
        EnergyBridge(const EnergyBridge &) = delete;
        EnergyBridge &operator=(const EnergyBridge &) = delete;

        // Python相关
        void *buildPythonArgs(const std::string &format, va_list args);
        double callPythonDoubleMethod(const std::string &method_name,
                                      const std::string &format = "", ...);
        bool callPythonBoolMethod(const std::string &method_name,
                                  const std::string &format = "", ...);
        std::string callPythonStringMethod(const std::string &method_name,
                                           const std::string &format = "", ...);

        // 后备方法
        double getFallbackValue(const std::string &method_name);
        bool getFallbackBoolValue(const std::string &method_name);
        std::string getFallbackStringValue(const std::string &method_name);

        // 内部管理
        bool checkPythonObject();
        bool reinitializePythonManager();
        bool createFallbackManager();
        void finalizePython();

        // 成员变量
        static std::mutex _instance_mutex;
        static EnergyBridge *_instance;

        std::mutex _python_mutex;
        void *_python_energy_manager; // PyObject*，使用void*避免包含Python.h
        bool _python_initialized;
        bool _initialized;
        int64_t _start_time_offset;
        bool _energy_debug;
        int64_t _last_energy_check;
        int64_t _total_calls;

        // Python错误处理
        int _python_error_count;
        bool _use_fallback_mode;
        std::string _config_file;

        // 时间转换常量
        static constexpr int64_t MS_PER_SECOND = 1000;
        static constexpr int64_t MS_PER_MINUTE = 60 * 1000;
        static constexpr int64_t MS_PER_HOUR = 60 * 60 * 1000;
        static constexpr int64_t MS_PER_DAY = 24 * 60 * 60 * 1000;
    };

} // namespace RTSim

#endif // ENERGY_BRIDGE_HPP
