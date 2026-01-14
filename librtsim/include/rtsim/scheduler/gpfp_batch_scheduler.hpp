// gpfp_batch_scheduler.hpp - BATCH算法完整修复版头文件
#ifndef GPFP_BATCH_SCHEDULER_HPP
#define GPFP_BATCH_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

namespace RTSim {

    // 前向声明
    class CPU;
    class AbsRTTask;
    class GPFPBatchScheduler;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // 任务激活仿真事件类声明
    // =====================================================
    class BatchTaskActivationSimEvent : public MetaSim::Event {
    private:
        GPFPBatchScheduler *_scheduler;
        AbsRTTask *_task;
        std::string _task_name;
        bool _is_periodic;
        int _period;
        int64_t _planned_time_ms;

    public:
        BatchTaskActivationSimEvent(GPFPBatchScheduler *scheduler,
                                    AbsRTTask *task,
                                    const std::string &task_name,
                                    bool is_periodic, int period,
                                    int64_t planned_time_ms);
        void doit() override;
    };

    // =====================================================
    // GPFPBatchTaskModel 类声明
    // =====================================================
    class GPFPBatchTaskModel : public TaskModel {
    private:
        int _period;
        int _wcet;
        std::string _workload_type;
        double _base_energy_consumption;
        MetaSim::Tick _rm_priority;
        MetaSim::Tick _arrival_offset;
        MetaSim::Tick _next_release;

    public:
        GPFPBatchTaskModel(AbsRTTask *t, int period, int wcet,
                           const std::string &workload_type,
                           MetaSim::Tick arrival_offset = 0);
        virtual ~GPFPBatchTaskModel();

        MetaSim::Tick getPriority() const override;
        void changePriority(MetaSim::Tick p) override;

        // Getter方法
        int getPeriod() const {
            return _period;
        }
        int getWCET() const {
            return _wcet;
        }
        std::string getWorkloadType() const {
            return _workload_type;
        }
        MetaSim::Tick getArrivalOffset() const {
            return _arrival_offset;
        }

        // 设置周期
        void setPeriod(int period);
    };

    // =====================================================
    // GPFPBatchScheduler 类声明
    // =====================================================
    class GPFPBatchScheduler : public Scheduler {
    private:
        // 任务参数结构
        struct TaskParams {
            int period;
            int wcet;
            std::string workload;
            int arrival_offset;
        };

        // 任务激活事件
        struct BatchTaskActivationEvent {
            AbsRTTask *task;
            MetaSim::Tick activation_time;
            int64_t activation_ms;
            std::string task_name;
            bool is_periodic;
            int period;
        };

        // ========== 配置参数 ==========
        int _num_cores;
        double _current_frequency;
        int _unit_time;
        bool _strict_priority;
        bool _energy_stop_policy;
        bool _enable_energy_recovery;
        bool _recovery_in_progress;
        int _consecutive_waits;
        MetaSim::Tick _start_time_offset;
        bool _config_loaded;
        bool _delayed_initialization_done;
        bool _need_delayed_init;

        // ========== 恢复相关参数 ==========
        AbsRTTask *_recovery_target; // 恢复目标任务
        double _recovery_required_energy; // 恢复所需能量
        std::vector<AbsRTTask *> _recovery_batch_tasks; // 恢复批量任务列表
        MetaSim::Tick _recovery_start_time; // 恢复开始时间
        MetaSim::Tick _recovery_end_time; // 恢复结束时间

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, GPFPBatchTaskModel *> _task_models;
        std::map<AbsRTTask *, int> _task_periods;
        std::map<AbsRTTask *, int> _task_wcets;
        std::map<AbsRTTask *, std::string> _task_workloads;
        std::map<AbsRTTask *, int> _task_remaining_time;
        std::map<AbsRTTask *, int> _task_executed_time;
        std::map<AbsRTTask *, MetaSim::Tick> _task_arrival_offsets;
        std::map<AbsRTTask *, MetaSim::Tick> _task_next_releases;
        std::map<AbsRTTask *, MetaSim::Tick> _task_start_times;
        std::set<AbsRTTask *> _active_tasks;
        std::set<AbsRTTask *> _completed_tasks;
        std::map<int, AbsRTTask *> _core_assignments;
        std::vector<AbsRTTask *> _running_tasks;
        std::map<std::string, TaskParams> _task_params_from_config;
        std::map<AbsRTTask *, std::string> _task_original_names;

        // ========== 激活系统 ==========
        std::multimap<int64_t, BatchTaskActivationEvent>
            _precise_activation_events;
        std::map<AbsRTTask *, int64_t> _task_next_activation_ms;
        std::vector<BatchTaskActivationSimEvent *> _scheduled_sim_events;
        std::map<int64_t, std::vector<AbsRTTask *>> _scheduled_activations;

        // ========== 功率模型 ==========
        std::map<std::string, double> _power_coefficients;
        double _base_power;
        std::map<int, double> _frequency_power_ratios;

        // ========== 统计信息 ==========
        struct {
            int total_scheduled = 0;
            int total_task_completions = 0;
            int total_skipped_energy = 0;
            int total_recovery_waits = 0;
            int total_batch_executions = 0;
            int total_partial_batches = 0;
            double total_energy_consumed = 0.0;
            double total_energy_harvested = 0.0;
            double total_batch_energy_required = 0.0;
        } _stats;

        int _schedule_count;
        MetaSim::Tick _last_schedule_time;
        mutable int _total_debug_count = 0;

        // ========== 恢复统计 ==========
        struct RecoveryStats {
            int64_t recovery_attempts = 0;
            int64_t recovery_success = 0;
            int64_t recovery_failed = 0;
            int64_t time_conversion_errors = 0;
            double total_recovery_energy = 0.0;
        };
        RecoveryStats _recovery_stats;

        // ========== 私有方法 ==========
        void initializePowerModel();
        void parseBatchParams(const std::vector<std::string> &params);
        void validateEnergyCalculations();
        void validateConfiguration();

        // 任务信息提取
        int extractPeriodFromTaskName(const std::string &task_name) const;
        int extractWCETFromTaskName(const std::string &task_name) const;
        std::string
            extractWorkloadTypeFromTaskName(const std::string &task_name) const;

        // 任务生命周期管理
        void initializeTaskRemainingTime(AbsRTTask *task);
        void resetTaskForNextPeriod(AbsRTTask *task,
                                    MetaSim::Tick current_time);
        bool assignTaskToCore(AbsRTTask *task, int core_id);
        void releaseCore(int core_id);
        int findAvailableCore() const;

        // 激活系统管理
        void schedulePreciseActivationEvent(AbsRTTask *task,
                                            int64_t activation_ms);
        void processPreciseActivations(int64_t current_ms);
        void onTaskActivationTimer(const BatchTaskActivationEvent &event);
        void checkScheduledActivations(MetaSim::Tick current_time);
        void checkAndProcessAllMissedActivations(MetaSim::Tick current_time);
        void initializePreciseActivationSystem();
        void initializeTaskActivation();

        // 能量管理
        void handleBatchEnergyRecovery(double required_batch_energy,
                                       MetaSim::Tick current_time);
        void handleBatchEnergyRecoverySimple(
            MetaSim::Tick current_time); // 简化恢复函数
        bool checkAndStartBatchRecovery(
            double required_batch_energy, MetaSim::Tick current_time,
            const std::vector<AbsRTTask *> &batch_tasks);

        // 调试辅助
        std::string getTaskShortName(AbsRTTask *task) const;

        // 能量计算
        double getUnitTimeEnergy(AbsRTTask *task) const;
        double getWorkloadPower(const std::string &workload_type) const;
        double getFrequencyPowerRatio(double frequency) const;
        double calculateTaskEnergy(AbsRTTask *task,
                                   MetaSim::Tick execution_time) const;

        // BATCH核心方法
        double calculateBatchEnergyRequired(
            const std::vector<AbsRTTask *> &tasks) const;
        bool executeBatch(const std::vector<AbsRTTask *> &tasks,
                          MetaSim::Tick current_time);

        // 新增：批量任务处理辅助函数
        void processCompletedTasks(); // 处理已完成任务
        void executeSelectedTasks(
            const std::vector<AbsRTTask *> &tasks_to_run); // 执行选定任务
        void validateTaskStates(); // 验证任务状态

        // 任务状态检查
        int getRMPriority(AbsRTTask *task) const;
        bool isTaskActive(AbsRTTask *task) const;
        bool isTaskRunning(AbsRTTask *task) const;
        bool isTaskCompleted(AbsRTTask *task) const;
        bool isTaskReady(AbsRTTask *task) const;

        // BATCH核心算法
        std::vector<AbsRTTask *> getActiveTasksByRMPriority() const;

        // 新增：统一的能量计算接口
        double calculateUnifiedEnergy(AbsRTTask *task, int duration_ms) const;
        double getUnifiedUnitTimeEnergy(AbsRTTask *task) const;

        // 新增验证函数
        void validateEnergyParameters();
        void debugEnergyCalculation(AbsRTTask *task);

        // 时间调试函数
        void debugTimeConversion(MetaSim::Tick current_time) const;

        // 新增：任务执行函数
        bool executeTaskWithEnergyCheck(AbsRTTask *task, MetaSim::Tick current_time);

    public:
        // 构造函数/析构函数
        GPFPBatchScheduler();
        GPFPBatchScheduler(const std::vector<std::string> &params);
        virtual ~GPFPBatchScheduler();

        // 工厂方法
        static std::unique_ptr<GPFPBatchScheduler>
            createInstance(const std::vector<std::string> &params);

        // Scheduler接口实现
        void addTask(AbsRTTask *task, const std::string &params) override;
        void removeTask(AbsRTTask *task) override;
        void notify(AbsRTTask *task) override;
        bool isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                          AbsRTTask *t) override;

        // 调度方法
        void schedule();

        // 配置加载
        void loadTasksFromConfig(const std::string &task_file);
        TaskParams getTaskParamsFromConfig(const std::string &task_name) const;

        // 任务激活
        void activateTaskAtExactTime(AbsRTTask *task,
                                     MetaSim::Tick activation_time);
        void completeTaskExecution(AbsRTTask *task);

        // 能量管理接口
        double getCurrentEnergy() const;
        bool hasSufficientEnergy(double required_energy) const;
        bool consumeEnergy(double energy_joules, const std::string &task_name);
        double updateEnergyContinuously(TimeMs current_time);
        bool waitForEnergyRecovery(double required_energy,
                                   MetaSim::Tick current_time);

        // 配置接口
        void setStartTimeOffset(MetaSim::Tick offset);
        std::string getEnergyStatus() const;

        // 辅助方法
        double tickToSeconds(MetaSim::Tick tick) const;
        std::string getTaskName(AbsRTTask *task) const;
        bool areAllTasksCompleted() const;
        TimeMs getAdjustedTime(MetaSim::Tick tick) const;

        // 调试和统计
        void printStats() const;
        void debugTaskInfo() const;
        void debugRunningTasks() const;
        void debugActiveTasks() const;
        void printActivationStatus() const;
        void initializeScheduler();

        // 时间转换
        void checkAndActivateTasks(MetaSim::Tick current_time);

        // 立即激活（用于初始化）
        void forceImmediateActivationAllTasks();

        // 统一的能量计算接口
        double getTaskEnergyConsumption(AbsRTTask *task) const;

        // 改进的恢复接口
        bool checkAndStartRecovery(double required_energy,
                                   MetaSim::Tick current_time);

        // 友元类声明
        friend class BatchTaskActivationSimEvent;
    };

} // namespace RTSim

// 工厂注册
static registerInFactory<RTSim::Scheduler, RTSim::GPFPBatchScheduler>
    registerGPFPBatch("gpfp_batch");

#endif // GPFP_BATCH_SCHEDULER_HPP
