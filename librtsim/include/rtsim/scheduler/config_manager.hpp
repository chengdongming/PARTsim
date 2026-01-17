#ifndef CONFIG_MANAGER_HPP
#define CONFIG_MANAGER_HPP

#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <cstdint>

namespace RTSim {

    class ConfigManager {
    private:
        static std::mutex _instance_mutex;
        static std::unique_ptr<ConfigManager> _instance;

        bool _config_loaded;
        bool _tasks_loaded;

        // 配置文件路径
        std::string _config_file_path;

        // CPU配置
        int _num_cores;
        std::string _scheduler_type;
        double _base_frequency;
        int _unit_time;

        // 能量配置
        double _initial_energy;
        double _max_energy;
        double _base_harvest_rate;
        int64_t _start_time_offset;  // ⭐ 修复：改为int64_t支持大时间偏移
        bool _enable_energy_recovery;
        int64_t _periodic_collection_interval;  // ⭐ 新增：周期性能量收集间隔

        // 功率模型配置
        double _base_power;
        std::map<std::string, double> _power_coefficients;
        std::map<int, double> _frequency_power_ratios;

        // 新增：任务到达配置
        std::map<std::string, int> _task_arrival_config; // 任务名->到达偏移

        // 新增：调度器特定参数
        struct SchedulerParams {
            bool strict_priority = true;
            bool energy_stop_policy = true;
            int max_consecutive_waits = 10;
            int batch_size = 4; // 为未来算法预留
            int adaptation_interval = 1000; // 为未来算法预留
        } _scheduler_params;

        // 任务配置结构体
        struct TaskConfig {
            std::string name;
            int period;
            int wcet;
            std::string workload_type;
            double energy_consumption;
        };
        std::vector<TaskConfig> _tasks;
        int _expected_task_count = 0;

        // 回调函数类型（用于从Python获取配置）
        using ConfigCallback =
            std::function<bool(const std::string &, ConfigManager &)>;
        static ConfigCallback _config_callback;

        

    public:
        static ConfigManager &getInstance();
        ConfigManager();

        // 设置配置回调（Python端调用）
        static void setConfigCallback(ConfigCallback callback);

        // 加载配置
        bool loadSystemConfig(const std::string &config_file);
        bool loadTaskConfig(const std::string &task_file);
        void setExpectedTaskCount(int count);
        int getExpectedTaskCount() const;

        // CPU配置获取
        int getNumCores() const {
            return _num_cores;
        }
        std::string getSchedulerType() const {
            return _scheduler_type;
        }
        double getBaseFrequency() const {
            return _base_frequency;
        }
        int getUnitTime() const {
            return _unit_time;
        }

        // 能量配置获取
        double getInitialEnergy() const {
            return _initial_energy;
        }
        double getMaxEnergy() const {
            return _max_energy;
        }
        double getBaseHarvestRate() const {
            return _base_harvest_rate;
        }
        int64_t getStartTimeOffset() const {
            return _start_time_offset;
        }
        bool isEnergyRecoveryEnabled() const {
            return _enable_energy_recovery;
        }
        int64_t getPeriodicCollectionInterval() const {
            return _periodic_collection_interval;
        }
        void setPeriodicCollectionInterval(int64_t interval) {
            _periodic_collection_interval = interval;
        }
        void setEnergyRecoveryEnabled(bool enabled) {
            _enable_energy_recovery = enabled;
        }

        // 新增获取方法
        int getTaskArrivalOffset(const std::string &task_name) const {
            auto it = _task_arrival_config.find(task_name);
            return (it != _task_arrival_config.end()) ? it->second : 0;
        }

        const SchedulerParams &getSchedulerParams() const {
            return _scheduler_params;
        }

        // 新增设置方法
        void setTaskArrivalOffset(const std::string &task_name, int offset) {
            _task_arrival_config[task_name] = offset;
        }

        void setSchedulerParams(const SchedulerParams &params) {
            _scheduler_params = params;
        }

        // 功率模型配置获取
        double getBasePower() const {
            return _base_power;
        }
        double getPowerCoefficient(const std::string &workload_type) const;
        double getFrequencyPowerRatio(int frequency) const;
        const std::map<std::string, double> &getAllPowerCoefficients() const {
            return _power_coefficients;
        }
        const std::map<int, double> &getAllFrequencyRatios() const {
            return _frequency_power_ratios;
        }

        // 任务配置获取
        const std::vector<TaskConfig> &getTasks() const {
            return _tasks;
        }

        // 设置方法
        void setNumCores(int cores) {
            _num_cores = cores;
        }
        void setSchedulerType(const std::string &type) {
            _scheduler_type = type;
        }
        void setBaseFrequency(double freq) {
            _base_frequency = freq;
        }
        void setUnitTime(int time) {
            _unit_time = time;
        }
        void setInitialEnergy(double energy) {
            _initial_energy = energy;
        }
        void setMaxEnergy(double energy) {
            _max_energy = energy;
        }
        void setBaseHarvestRate(double rate) {
            _base_harvest_rate = rate;
        }
        void setStartTimeOffset(int64_t offset) {
            _start_time_offset = offset;
        }
        void setEnableEnergyRecovery(bool enable) {
            _enable_energy_recovery = enable;
        }
        void setBasePower(double power) {
            _base_power = power;
        }
        void setPowerCoefficient(const std::string &workload_type,
                                 double coefficient) {
            _power_coefficients[workload_type] = coefficient;
        }
        void setFrequencyPowerRatio(int frequency, double ratio) {
            _frequency_power_ratios[frequency] = ratio;
        }

        void setAllFrequencyRatios(const std::map<int, double> &ratios) {
            _frequency_power_ratios = ratios;
        }

        // 状态检查
        bool isConfigLoaded() const {
            return _config_loaded;
        }
        bool areTasksLoaded() const {
            return _tasks_loaded;
        }

        // 获取配置文件路径
        std::string getConfigFilePath() const {
            return _config_file_path;
        }

        // 调试信息
        void printConfig() const;
    };

} // namespace RTSim

#endif // CONFIG_MANAGER_HPP
