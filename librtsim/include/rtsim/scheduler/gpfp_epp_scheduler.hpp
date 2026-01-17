#ifndef GPFP_EPP_SCHEDULER_HPP
#define GPFP_EPP_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <rtsim/abstask.hpp>
#include <rtsim/rttask.hpp>
#include <metasim/factory.hpp>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>
#include <deque>

namespace RTSim {

    // 前向声明
    class CPU;
    class AbsRTTask;
    class EPPScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // 任务激活仿真事件类声明
    // =====================================================
    class EPPTaskActivationSimEvent : public MetaSim::Event {
    private:
        EPPScheduler *_scheduler;
        AbsRTTask *_task;
        std::string _task_name;
        bool _is_periodic;
        int _period;
        int64_t _planned_time_ms;

    public:
        EPPTaskActivationSimEvent(EPPScheduler *scheduler,
                                      AbsRTTask *task,
                                      const std::string &task_name,
                                      bool is_periodic, int period,
                                      int64_t planned_time_ms);
        void doit() override;
    };

    // =====================================================
    // EPP周期性等待队列检查事件
    // =====================================================
    class EPPWaitingQueueCheckEvent : public MetaSim::Event {
    private:
        EPPScheduler *_scheduler;

    public:
        EPPWaitingQueueCheckEvent(EPPScheduler *scheduler);
        void doit() override;

        // 设置周期性检查
        void scheduleNextCheck(int delay_ms);
    };

    // =====================================================
    // V28.14: 能量恢复事件
    // =====================================================
    class EPPEnergyRecoveryEvent : public MetaSim::Event {
    private:
        EPPScheduler *_scheduler;

    public:
        EPPEnergyRecoveryEvent(EPPScheduler *scheduler);
        void doit() override;
    };

    // ⭐ 周期性能量收集事件
    class EPPPeriodicEnergyCollectionEvent : public MetaSim::Event {
    private:
        EPPScheduler *_scheduler;

    public:
        EPPPeriodicEnergyCollectionEvent(EPPScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // EPPTaskModel 类声明
    // =====================================================
    class EPPTaskModel : public TaskModel {
    private:
        int _period;
        int _wcet;
        std::string _workload_type;
        double _base_energy_consumption;
        double _energy_coefficient;  // ⭐ 能量系数
        MetaSim::Tick _rm_priority;
        MetaSim::Tick _arrival_offset;
        MetaSim::Tick _next_release;

    public:
        EPPTaskModel(AbsRTTask *t, int period, int wcet,
                     const std::string &workload_type,
                     double energy_coefficient = 1.0,
                     MetaSim::Tick arrival_offset = 0);
        virtual ~EPPTaskModel();

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
        double getEnergyCoefficient() const {
            return _energy_coefficient;
        }
        MetaSim::Tick getRMPriority() const {
            return _rm_priority;
        }
        MetaSim::Tick getArrivalOffset() const {
            return _arrival_offset;
        }

        // 设置周期
        void setPeriod(int period);
    };

    // =====================================================
    // EPPScheduler 类声明
    // =====================================================
    class EPPScheduler : public Scheduler {
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
        bool _initial_energy_collected;  // ⭐ 标记是否已收集初始太阳能

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, EPPTaskModel *> _task_models;
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
        std::map<std::string, TaskParams> _task_params_from_config;
        std::map<AbsRTTask *, std::string> _task_original_names;

        // ========== 激活系统 ==========
        std::multimap<int64_t, TaskActivationEvent> _precise_activation_events;
        std::map<AbsRTTask *, int64_t> _task_next_activation_ms;
        std::vector<EPPTaskActivationSimEvent *> _scheduled_sim_events;
        std::map<int64_t, std::vector<AbsRTTask *>> _scheduled_activations;

        // ========== EPP不需要分片管理 ==========
        // EPP使用Tick级抢占，不使用50ms分片

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
        std::deque<AbsRTTask *> _waiting_queue_deque;  // ⭐ 使用deque支持pop_front

        // ========== ⭐ EPP就绪队列 ==========
        std::deque<AbsRTTask *> _ready_queue;  // ⭐ EPP专用的就绪队列

        // ========== Kernel引用（用于触发dispatch） ==========
        // 用于在恢复等待队列任务后触发dispatch，解决250-500ms空隙问题
        MRTKernel *_kernel;

        // ========== ⭐ CPU运行任务映射 ==========
        std::map<CPU *, AbsRTTask *> _running_tasks;  // ⭐ CPU到运行任务的映射

        // ========== ⭐ 太阳能数据文件 ==========
        std::string _solar_data_file;

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

        // ========== ⭐ 新增能量管理成员变量 ==========
        double _current_energy;                    // 当前可用能量
        double _initial_energy;                    // 初始能量
        double _max_energy;                        // 最大能量容量
        MetaSim::Tick _last_collection_time;       // 上次能量收集时间
        double _pv_efficiency;                     // PV效��
        double _pv_area_m2;                        // PV面板面积（平方米）
        bool _use_real_solar_data;                 // 是否使用真实NASA太阳能数据
        int _max_recovery_wait_time_ms;            // 最大恢复等待时间
        EPPEnergyRecoveryEvent *_recovery_event;   // 能量恢复事件
        EPPPeriodicEnergyCollectionEvent *_periodic_collection_event;  // ⭐ 周期性能量收集事件
        ConfigManager *_config_manager;            // 配置管理器
        bool _enable_periodic_collection;          // 是否启用周期性能量收集
        Tick _periodic_collection_interval;        // 周期性收集间隔（默认100ms）

        // ========== ⭐ EPP能量记账系统（方案3） ==========
        struct TaskEnergyAccount {
            double prepaid;              // 预扣减能量（完整WCET）
            double consumed;             // 实际消耗能量（累计）
            double harvested;            // 执行期间实际收集能量
            double predicted;            // 预测收集能量
            MetaSim::Tick start_time;    // 任务开始时间
            MetaSim::Tick last_unit_time; // 上次单位时间时间

            TaskEnergyAccount() : prepaid(0.0), consumed(0.0), harvested(0.0),
                                  predicted(0.0), start_time(0), last_unit_time(0) {}
        };

        std::map<AbsRTTask *, TaskEnergyAccount> _energy_accounts;

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

        // ========== ⭐ EPP能量记账方法 ==========
        void settleEnergyAccount(AbsRTTask *task);

        // ========== ⭐ EPP保守预测方法 ==========
        double predictEnergyHarvestConservative(
            MetaSim::Tick wcet,
            MetaSim::Tick current_time);

        // ========== ⭐ EPP抢占方法 ==========
        void preemptTask(AbsRTTask *task);
        bool checkPreemption(AbsRTTask *new_task, AbsRTTask *running_task);

        // ========== ⭐ EPP辅助方法 ==========
        double collectSolarEnergy(MetaSim::Tick current_time);
        void restoreWaitingQueueToReadyQueue();
        int getFreeCPUCount();
        AbsRTTask *getHighestPriorityTaskFromReadyQueue();
        double calculateEnergyForWCET(AbsRTTask *task, MetaSim::Tick wcet);
        double calculatePowerForWorkload(const std::string &workload, double frequency);
        double predictEnergyCollection(MetaSim::Tick current_time, MetaSim::Tick duration);
        bool canScheduleWithEnergy(AbsRTTask *task, MetaSim::Tick current_time);
        CPU *getFreeCPU();
        double calculateEnergyForTask(AbsRTTask *task);
        void dispatchTask(AbsRTTask *task, CPU *cpu);
        MetaSim::Tick calculateEnergyRecoveryTime(double energy_needed);
        void scheduleEnergyRecoveryEvent(MetaSim::Tick delay);
        void cancelEnergyRecoveryEvent();
        void startPeriodicCollection();              // 启动周期性能量收集
        void onPeriodicCollection();                 // 周期性收集回调

        // Getter方法
        bool isPeriodicCollectionEnabled() const { return _enable_periodic_collection; }
        Tick getPeriodicCollectionInterval() const { return _periodic_collection_interval; }
        void addToReadyQueue(AbsRTTask *task);
        void addToWaitingQueue(AbsRTTask *task);
        void removeFromReadyQueue(AbsRTTask *task);
        void removeFromWaitingQueue(AbsRTTask *task);
        bool isInReadyQueue(AbsRTTask *task) const;
        bool isInWaitingQueue(AbsRTTask *task) const;
        EPPTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);
        void checkAndPreempt();
        void checkAndPreemptOnAllCPUs();
        bool shouldPreempt(CPU *cpu, AbsRTTask *new_task);
        AbsRTTask *getRunningTaskOnCPU(CPU *cpu);
        void setPVConfig(double efficiency, double area, const std::string &solar_file);
        double getSolarIrradiance(int64_t time_ms);

    public:
        // 构造函数/析构函数
        EPPScheduler();
        EPPScheduler(const std::vector<std::string> &params);
        virtual ~EPPScheduler();

        // 工厂方法
        static std::unique_ptr<EPPScheduler>
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

        // 注意：extract不是虚函数，无法override
        // 改为在onTaskEnd()中手动清理_ready_queue
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
        friend class EPPTaskActivationSimEvent;
        friend class EPPEnergyRecoveryEvent;
        friend class EPPPeriodicEnergyCollectionEvent;  // ⭐ 周期性能量收集
        // EPP不需要分片事件：使用Tick级抢占
        friend class EPPWaitingQueueCheckEvent;

        // ========== V28.13: 周期性等待队列检查 ==========
        void triggerWaitingQueueCheck();
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::EPPScheduler>
        registerGPFPASAP("gpfp_epp");
}

#endif // GPFP_EPP_SCHEDULER_HPP
