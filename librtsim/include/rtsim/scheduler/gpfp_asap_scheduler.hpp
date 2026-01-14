#ifndef GPFP_ASAP_SCHEDULER_HPP
#define GPFP_ASAP_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <metasim/factory.hpp>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

namespace RTSim {

    // 前向声明
    class CPU;
    class AbsRTTask;
    class GPFPASAPScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // 任务激活仿真事件类声明
    // =====================================================
    class ASAPTaskActivationSimEvent : public MetaSim::Event {
    private:
        GPFPASAPScheduler *_scheduler;
        AbsRTTask *_task;
        std::string _task_name;
        bool _is_periodic;
        int _period;
        int64_t _planned_time_ms;

    public:
        ASAPTaskActivationSimEvent(GPFPASAPScheduler *scheduler,
                                      AbsRTTask *task,
                                      const std::string &task_name,
                                      bool is_periodic, int period,
                                      int64_t planned_time_ms);
        void doit() override;
    };

    // =====================================================
    // ASAP任务分片定时器事件
    // =====================================================
    class ASAPSlicingEvent : public MetaSim::Event {
    private:
        GPFPASAPScheduler *_scheduler;
        AbsRTTask *_task;

    public:
        ASAPSlicingEvent(GPFPASAPScheduler *scheduler, AbsRTTask *task);
        void doit() override;

        // 设置优先级比EndInstrEvt更高（数值更小）
        // EndInstrEvt使用_DEFAULT_PRIORITY - 3，我们使用_DEFAULT_PRIORITY - 4
        // 这样分片事件会在指令结束事件之前处理
        static const int _SLICING_EVT_PRIORITY = MetaSim::Event::_DEFAULT_PRIORITY - 4;
    };

    // =====================================================
    // V28.13: 周期性等待队列检查事件
    // =====================================================
    class ASAPWaitingQueueCheckEvent : public MetaSim::Event {
    private:
        GPFPASAPScheduler *_scheduler;

    public:
        ASAPWaitingQueueCheckEvent(GPFPASAPScheduler *scheduler);
        void doit() override;

        // 设置周期性检查
        void scheduleNextCheck(int delay_ms);
    };

    // =====================================================
    // V28.14: 能量恢复事件
    // =====================================================
    class ASAPEnergyRecoveryEvent : public MetaSim::Event {
    private:
        GPFPASAPScheduler *_scheduler;

    public:
        ASAPEnergyRecoveryEvent(GPFPASAPScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // GPFPASAPTaskModel 类声明
    // =====================================================
    class GPFPASAPTaskModel : public TaskModel {
    private:
        int _period;
        int _wcet;
        std::string _workload_type;
        double _base_energy_consumption;
        MetaSim::Tick _rm_priority;
        MetaSim::Tick _arrival_offset;
        MetaSim::Tick _next_release;

    public:
        GPFPASAPTaskModel(AbsRTTask *t, int period, int wcet,
                             const std::string &workload_type,
                             MetaSim::Tick arrival_offset = 0);
        virtual ~GPFPASAPTaskModel();

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
    // GPFPASAPScheduler 类声明
    // =====================================================
    class GPFPASAPScheduler : public Scheduler {
    private:
        // 任务参数结构
        struct TaskParams {
            int period;
            int wcet;
            std::string workload;
            int arrival_offset;
        };

        // 任务激活事件
        struct TaskActivationEvent {
            AbsRTTask *task;
            MetaSim::Tick activation_time;
            int64_t activation_ms;
            std::string task_name;
            bool is_periodic;
            int period;
        };

        // ASAP统计信息
        struct ASAPStats {
            int cascade_scheduled_tasks = 0;
            int cascade_skipped_tasks = 0;
            int cascade_complete_pass = 0;
            int cascade_partial_pass = 0;
            double cascade_total_energy_used = 0.0;
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
        MetaSim::Tick _recovery_start_time;
        MetaSim::Tick _recovery_end_time;
        bool _enable_trace_recording;

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, GPFPASAPTaskModel *> _task_models;
        std::map<AbsRTTask *, int> _task_periods;
        std::map<AbsRTTask *, int> _task_wcets;
        std::map<AbsRTTask *, std::string> _task_workloads;
        std::map<AbsRTTask *, int> _task_remaining_time;
        std::map<AbsRTTask *, int> _task_executed_time;
        std::map<AbsRTTask *, MetaSim::Tick> _task_arrival_offsets;
        std::map<AbsRTTask *, MetaSim::Tick> _task_next_releases;
        std::set<AbsRTTask *> _active_tasks;
        std::set<AbsRTTask *> _completed_tasks;
        std::set<AbsRTTask *> _really_scheduled_tasks; // 新增：真正被调度的任务
        std::set<AbsRTTask *> _energy_insufficient_tasks; // 🔒 V28.9修复：能量不足任务集合
        std::map<int, AbsRTTask *> _core_assignments;
        std::vector<AbsRTTask *> _running_tasks;
        std::map<std::string, TaskParams> _task_params_from_config;
        std::map<AbsRTTask *, std::string> _task_original_names;

        // ========== 激活系统 ==========
        std::multimap<int64_t, TaskActivationEvent> _precise_activation_events;
        std::map<AbsRTTask *, int64_t> _task_next_activation_ms;
        std::vector<ASAPTaskActivationSimEvent *> _scheduled_sim_events;
        std::map<int64_t, std::vector<AbsRTTask *>> _scheduled_activations;

        // ========== 任务分片管理 ==========
        std::map<AbsRTTask *, ASAPSlicingEvent *> _active_slicing_events;

        // ========== 时间片调度管理 ==========
        // 存储需要重新调度的任务（还有剩余执行时间的任务）
        std::set<AbsRTTask *> _tasks_need_reschedule;

        // ========== 能量预留管理 ==========
        std::map<AbsRTTask *, double> _reserved_energy;
        double _dispatch_reserved_energy;  // ⭐ V28.13：dispatch阶段的总预留能量

        // ========== 时间片能量管理 ==========
        // 记录每个任务预付的能量（notify时预付1个时间片）
        std::map<AbsRTTask *, double> _task_prepaid_energy;

        // ========== 等待队列（关键修复） ==========
        // 存储因队列满而无法立即调度的任务
        std::vector<AbsRTTask *> _waiting_queue;

        // ========== Kernel引用（用于触发dispatch） ==========
        // 用于在恢复等待队列任务后触发dispatch，解决250-500ms空隙问题
        MRTKernel *_kernel;

        // ========== 批量插入延迟检查机制（避免优先级反转） ==========
        // 用于跟踪同一时间点（如suspend结束后）的批量插入操作
        MetaSim::Tick _last_batch_time;      // 上次批量插入的时间戳
        int _batch_insert_count;             // 当前批次已插入的任务数
        int _expected_batch_size;            // 预期的批次大小（通常是CPU核心数）
        bool _batch_insert_in_progress;      // 是否正在进行批量插入
        std::map<MetaSim::Tick, int> _extract_count_per_tick;  // 每个时间戳的extract计数

        // ========== 功率模型 ==========
        std::map<std::string, double> _power_coefficients;
        double _base_power;
        std::map<int, double> _frequency_power_ratios;

        // ========== 本地能量管理（当EnergyBridge失败时使用） ==========
        mutable double _local_energy;
        mutable bool _use_local_energy;
        mutable std::recursive_mutex _energy_mutex;  // 🔒 V28.8修复：使用递归互斥锁避免死锁

        // ========== 智能能量预算系统（方案3） ==========
        struct TaskEnergyBudget {
            double total_required;      // 总共需要的能量
            double current_available;   // 当前可用能量（当前+预测）
            MetaSim::Tick deadline;     // 绝对截止时间
            bool can_complete;          // 是否能够完成
            bool is_true_miss;         // 是否真的是deadline miss
        };

        std::map<AbsRTTask *, TaskEnergyBudget> _task_budgets;

        // ========== 统计信息 ==========
        struct {
            int total_scheduled = 0;
            int total_task_completions = 0;
            int total_skipped_energy = 0;
            int total_recovery_waits = 0;
            int total_deadline_misses = 0;  // 新增：真实deadline miss计数
            double total_energy_consumed = 0.0;
            double total_energy_harvested = 0.0;
        } _stats;

        // ASAP特定统计
        ASAPStats _cascade_stats;

        int _schedule_count;
        MetaSim::Tick _last_schedule_time;
        mutable int _total_debug_count = 0;

        // 能量恢复相关
        AbsRTTask *_recovery_target;
        double _recovery_required_energy;

        // 任务时间记录
        std::map<AbsRTTask *, MetaSim::Tick> _task_completion_times;
        std::map<AbsRTTask *, MetaSim::Tick> _task_start_times;

        // ========== 私有方法 ==========
        void initializePowerModel();
        void parseASAPParams(const std::vector<std::string> &params);
        void validateEnergyCalculations();
        void validateConfiguration();
        
        // ASAP核心算法
        std::vector<AbsRTTask *> performASAPSchedule(MetaSim::Tick current_time,
                                                       double current_energy);
        void executeASAPSelectedTasks(const std::vector<AbsRTTask *> &tasks_to_run);

        // 任务信息提取
        int extractPeriodFromTaskName(const std::string &task_name) const;
        int extractWCETFromTaskName(const std::string &task_name) const;
        std::string extractWorkloadTypeFromTaskName(const std::string &task_name) const;
        void processCompletedTasks();
        void validateTaskStates();

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
        void onTaskActivationTimer(const TaskActivationEvent &event);
        void checkScheduledActivations(MetaSim::Tick current_time);
        void checkAndProcessAllMissedActivations(MetaSim::Tick current_time);
        void initializePreciseActivationSystem();
        void initializeTaskActivation();
        void recordTaskCompletion(AbsRTTask *task, MetaSim::Tick completion_time);

        // 能量管理
        void handleEnergyRecovery(MetaSim::Tick current_time);
        void handleEnergyRecoverySimple(MetaSim::Tick current_time);
        bool waitForEnergyRecovery(double required_energy,
                                   MetaSim::Tick current_time);

        // 调试辅助
        std::string getTaskShortName(AbsRTTask *task) const;

        // 能量计算
        double getWorkloadPower(const std::string &workload_type) const;
        double getFrequencyPowerRatio(double frequency) const;
        double calculateTaskEnergy(AbsRTTask *task,
                                   MetaSim::Tick execution_time) const;

        // ========== 方案3：智能能量感知调度 ==========
        double predictEnergyHarvest(MetaSim::Tick time_window_ms);
        bool isTrueDeadlineMiss(AbsRTTask *task);
        MetaSim::Tick getAbsoluteDeadline(AbsRTTask *task);

        // 任务状态检查
        int getRMPriority(AbsRTTask *task) const;
        bool isTaskActive(AbsRTTask *task) const;
        bool isTaskRunning(AbsRTTask *task) const;
        bool isTaskCompleted(AbsRTTask *task) const;
        bool isTaskReady(AbsRTTask *task) const;

        // 任务优先级获取
        MetaSim::Tick getTaskPriority(AbsRTTask *task) const;

        // 统一的能量计算
        double calculateUnifiedEnergy(AbsRTTask *task, int duration_ms) const;
        double getUnifiedUnitTimeEnergy(AbsRTTask *task) const;

        // 任务执行辅助函数
        bool executeTaskWithEnergyCheck(AbsRTTask *task, MetaSim::Tick current_time);

        // ASAP核心算法辅助
        std::vector<AbsRTTask *> getActiveTasksByRMPriority() const;

        // 能量恢复后重新排队任务
        // current_task: 当前正在insert的任务（可选），用于避免优先级反转
        bool requeueWaitingTasks(double current_energy, AbsRTTask *current_task = nullptr);

        // 立即激活
        void forceImmediateActivationAllTasks();

        // 配置加载
        void loadTasksFromConfig(const std::string &task_file);
        TaskParams getTaskParamsFromConfig(const std::string &task_name) const;

        // 调试方法
        void debugEnergyCalculation(AbsRTTask *task) const;
        void printASAPStats() const;

        // 任务分片处理
        void onUnitTimeElapsed(AbsRTTask *task);

    public:
        // 构造函数/析构函数
        GPFPASAPScheduler();
        GPFPASAPScheduler(const std::vector<std::string> &params);
        virtual ~GPFPASAPScheduler();

        // 工厂方法
        static std::unique_ptr<GPFPASAPScheduler>
            createInstance(const std::vector<std::string> &params);

        // Scheduler接口实现
        void addTask(AbsRTTask *task, const std::string &params) override;
        void removeTask(AbsRTTask *task) override;
        void notify(AbsRTTask *task) override;
        bool isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                          AbsRTTask *t) override;

        // 调度方法
        void schedule();

        // ASAP就绪队列管理
        void insertTopTasksIntoReadyQueue(double current_energy);

        // 任务激活
        void activateTaskAtExactTime(AbsRTTask *task,
                                     MetaSim::Tick activation_time);
        void completeTaskExecution(AbsRTTask *task);
        void onTaskEnd(AbsRTTask *task);  // ⭐ 通用接口：任务结束时调用

        // 能量管理接口
        double getCurrentEnergy();  // 🔒 V28.8修复：移除const以允许加锁
        double getUnitTimeEnergy(AbsRTTask *task) const;  // 🔒 V28.9修复：移到public供kernel预检查使用
        bool hasSufficientEnergy(double required_energy) const;
        bool consumeEnergy(double energy_joules, const std::string &task_name);
        double updateEnergyContinuously(TimeMs current_time);
        bool checkAndStartRecovery(double required_energy,
                                   MetaSim::Tick current_time);

        // 配置接口
        void setStartTimeOffset(MetaSim::Tick offset);
        void setKernel(MRTKernel *kernel);
        MRTKernel* getKernel();  // ⭐ V28.14修复：在cpp中实现，需要dynamic_cast
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

        // 统一的能量计算接口
        double getTaskEnergyConsumption(AbsRTTask *task) const;

        // 新增验证函数
        void validateEnergyParameters();

        // 重写基类方法
        AbsRTTask *getFirst() override;
        AbsRTTask *getTaskN(unsigned int n) override;

        // 重写extract方法 - 在任务从队列移除后检查等待队列
        void extract(AbsRTTask *task);

        // 重写insert方法 - 在能量不足时不将任务添加到就绪队列
        void insert(AbsRTTask *task) override;

        // 重写newRun和endRun方法
        void newRun() override;
        void endRun() override;

        // 新增：检查任务是否真的被调度了
        bool isTaskReallyScheduled(AbsRTTask *task) const;

        // ⭐ V28.10新增：用于MRTKernel能量紧张检查的getter方法
        double getInitialEnergy() const;
        const std::vector<AbsRTTask *> &getReadyQueue() const;
        const std::map<AbsRTTask *, std::string> &getTaskWorkloads() const;

        // ========== 单位时间边界调度（V28.3） ==========
        // 计算下一个单位时间边界
        MetaSim::Tick getNextUnitTimeBoundary(MetaSim::Tick current_time) const;

        // 延迟dispatch到下一个单位时间边界
        void delayedDispatch(MetaSim::Tick current_time);

        // ========== 批量插入延迟检查机制（避免优先级反转） ==========
        // 这些方法可以被ASAP、ASAP、Batch等算法共用
        void startBatchInsert(MetaSim::Tick current_time);
        void endBatchInsert();
        bool shouldCheckWaitingQueue(AbsRTTask *task);
        void flushBatchInsertIfNeeded(MetaSim::Tick current_time);

        // 友元类声明
        friend class ASAPTaskActivationSimEvent;
        friend class ASAPSlicingEvent;
        friend class ASAPWaitingQueueCheckEvent;

        // ========== V28.13: 周期性等待队列检查 ==========
        void triggerWaitingQueueCheck();
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::GPFPASAPScheduler>
        registerGPFPASAP("gpfp_asap");
}

#endif // GPFP_ASAP_SCHEDULER_HPP
