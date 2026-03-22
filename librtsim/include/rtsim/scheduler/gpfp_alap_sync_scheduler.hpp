#ifndef GPFP_ALAP_Sync_SCHEDULER_HPP
#define GPFP_ALAP_Sync_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <rtsim/abstask.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/energy_info_provider.hpp>
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
    class ALAPSyncScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // ALAP-Sync Tick级调度事件（每1ms触发一次）
    // =====================================================
    class ALAPSyncTickEvent : public MetaSim::Event {
    private:
        ALAPSyncScheduler *_scheduler;

    public:
        ALAPSyncTickEvent(ALAPSyncScheduler *scheduler);
        void doit() override;
    };
    class ALAPSyncScheduler; // 前置声明

    // =====================================================
    // ALAP-Sync 专属唤醒闹钟事件
    // =====================================================
    class ALAPSyncWakeEvent : public MetaSim::Event {
    private:
        ALAPSyncScheduler *_scheduler;
    public:
        ALAPSyncWakeEvent(ALAPSyncScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // ALAP-Sync运行时能量检查事件（每1ms检查运行中任务的能量）
    // =====================================================
    class ALAPSyncEnergyCheckEvent : public MetaSim::Event {
    private:
        ALAPSyncScheduler *_scheduler;
        AbsRTTask *_task;
        CPU *_cpu;
        int _ms_executed;  // 已执行的ms数

    public:
        ALAPSyncEnergyCheckEvent(ALAPSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu);
        void doit() override;
        int getMsExecuted() const { return _ms_executed; }
        void setMsExecuted(int ms) { _ms_executed = ms; }
    };

    // =====================================================
    // ⭐ 能量耗尽预测事件（虚空借电Bug修复）
    // 当系统预测到电池将在某时刻耗尽时，在事件队列中插入此事件
    // 确保任务在电池真正耗尽时被正确中断，而不是"惯性"跑完
    // =====================================================
    class ALAPSyncEnergyDepletedEvent : public MetaSim::Event {
    private:
        ALAPSyncScheduler *_scheduler;

    public:
        MetaSim::Tick _scheduled_depletion_time;  // 预测的耗尽时刻
        double _energy_at_prediction;               // 预测时的能量值

    public:
        ALAPSyncEnergyDepletedEvent(ALAPSyncScheduler *scheduler);
        void doit() override;

        MetaSim::Tick getScheduledDepletionTime() const { return _scheduled_depletion_time; }
        double getEnergyAtPrediction() const { return _energy_at_prediction; }
    };

    // =====================================================
    // ALAPSyncTaskModel 类声明
    // =====================================================
    class ALAPSyncTaskModel : public TaskModel {
    private:
        int _period;
        int _wcet;
        std::string _workload_type;
        double _energy_coefficient;
        MetaSim::Tick _rm_priority;
        MetaSim::Tick _arrival_offset;
        MetaSim::Tick _next_release;

    public:
        double _total_energy;          // 任务总能耗
        double _unit_energy;           // 每ms能耗

    public:
        ALAPSyncTaskModel(AbsRTTask *t, int period, int wcet,
                      const std::string &workload_type,
                      double energy_coefficient = 1.0,
                      MetaSim::Tick arrival_offset = 0);
        virtual ~ALAPSyncTaskModel();

        MetaSim::Tick getPriority() const override;
        void changePriority(MetaSim::Tick p) override;

        // Getter方法
        int getPeriod() const { return _period; }
        int getWCET() const { return _wcet; }
        std::string getWorkloadType() const { return _workload_type; }
        double getEnergyCoefficient() const { return _energy_coefficient; }
        double getTotalEnergy() const { return _total_energy; }
        double getUnitEnergy() const { return _unit_energy; }
        MetaSim::Tick getRMPriority() const { return _rm_priority; }
        MetaSim::Tick getArrivalOffset() const { return _arrival_offset; }

        // 设置周期
        void setPeriod(int period);
    };

    // =====================================================
    // ALAPSyncScheduler 类声明 - ALAP-Sync批量调度算法
    // =====================================================
    class ALAPSyncScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _dispatching_tasks_total_energy; // 本次dispatch中已调度任务的总能耗
        MetaSim::Tick _last_tick_time;       // 上次tick时间
        MetaSim::Tick _last_collection_time; // 上次能量收集时间
        ALAPSyncWakeEvent* _alap_wake_event = nullptr;

        // ⭐ 能量耗尽预测事件（Bug修复：防止虚空借电）
        ALAPSyncEnergyDepletedEvent *_energy_depleted_event = nullptr;

        // ========== 太阳能配置 ==========
        std::string _solar_data_file;
        double _pv_efficiency;
        double _pv_area_m2;
        bool _use_real_solar_data;
        MetaSim::Tick _start_time_offset;
        double _base_harvest_rate;  // ⭐ V93修复：从配置读取基础收集率 (J/ms)

        // ========== Tick事件 ==========
        ALAPSyncTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度

        // ========== 本次dispatch中已计数的任务（用于逐渐扣除模式） ==========
        std::set<AbsRTTask *> _counted_tasks_in_dispatch; // 本次dispatch中已计数的任务，避免重复

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, ALAPSyncTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::map<CPU *, AbsRTTask *> _running_tasks;
        MRTKernel *_kernel;

        // ========== 运行时能量检查事件（每任务一个） ==========
        std::map<AbsRTTask *, ALAPSyncEnergyCheckEvent *> _energy_check_events;

        // ========== WCET完成追踪（用于批量调度判断任务是否已完成） ==========
        std::set<AbsRTTask *> _tasks_completed_wcet;  // 已达到WCET的任务集合

        // ========== ALAP-Sync批量调度状态 ==========
        std::vector<AbsRTTask *> _current_batch_tasks;  // 当前批量任务（tick边界预计算）
        std::vector<AbsRTTask *> _preempt_batch_tasks;    // 抢占批量任务（mid-tick抢占创建的微型批量）
        bool _batch_scheduled_this_tick;                // 本tick是否已批量调度
        bool _energy_depleted;                          // 能量是否已耗尽（Bug #5修复）
        int _current_batch_size;                        // 当前批量大小

        // ⭐ 同时间戳并发派发修复：待派发任务队列和能量预占
        std::vector<AbsRTTask *> _pending_dispatch_tasks;   // 待派发任务列表
        double _pending_dispatch_energy;                   // 待扣除能量（在commitDispatch时真正扣除）

        // ========== 抢占防抖 ==========
        AbsRTTask *_last_preempted_task;                // 最近被抢占的任务
        MetaSim::Tick _last_preempted_tick;             // 最近抢占发生的时间

        // ========== 能量记账（每ms累计） ==========
        struct TaskEnergyAccount {
            double total_consumed;      // 累计消耗能量（每ms累加）
            MetaSim::Tick start_time;
            MetaSim::Tick last_unit_time;

            TaskEnergyAccount() : total_consumed(0.0), start_time(0), last_unit_time(0) {}
        };
        std::map<AbsRTTask *, TaskEnergyAccount> _energy_accounts;
        std::map<AbsRTTask *, std::string> _suspend_reasons;

        // ========== 统计信息 ==========
        struct {
            int total_scheduled = 0;
            int total_task_completions = 0;
            int total_skipped_energy = 0;
            int total_deadline_misses = 0;
            double total_energy_consumed = 0.0;
            double total_energy_harvested = 0.0;
            int total_tick_count = 0;
            int total_batch_schedules = 0;        // ALAP-Sync: 批量调度次数
            int total_batch_skipped = 0;          // ALAP-Sync: 批量调度跳过次数
            int total_alap_forced_idle = 0;       // ⭐ ALAP: 强制休眠次数
        } _stats;

        // ========== 私有方法 ==========

        // 核心调度逻辑 - ALAP-Sync批量调度
        void performTickScheduling();

        // ⭐ ALAP时序门控（阶段一）
        bool checkALAPBatchTimingGate(const std::vector<AbsRTTask *> &batch);  // ⭐ 基于批次的ALAP时序门控（原论文正确实现）
        bool checkALAPTimingGate();  // 全局ALAP时序门控（保留兼容性）
        MetaSim::Tick calculateSlackForTask(AbsRTTask *task);  // 计算任务的Slack

        // ⭐ 过期任务清理
        void cleanupExpiredTasks();  // 清理超过截止期的旧任务实例

        // ⭐ 运行时能量检查和任务中断（V28.15新增）
        void checkAndInterruptRunningTasks();  // 检查所有运行中的任务，能量不足时中断

        // ALAP-Sync批量计算
        int calculateBatchSize();                              // 计算批量大小 k
        void executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy);  // 执行批量调度

        // 能量计算
        double calculateTotalEnergyForTask(AbsRTTask *task); // 计算任务总能耗
        double calculatePowerForWorkload(const std::string &workload, double frequency);
        double collectSolarEnergy(MetaSim::Tick current_time);
        double getSolarIrradiance(int64_t time_ms);

        // ⭐ 能量耗尽预测与事件注册（Bug修复）
        double calculateTotalPowerConsumption();                              // 计算当前总功耗
        MetaSim::Tick predictTimeToDepletion(double energy, double power);    // 预测能量耗尽时间
        void scheduleEnergyDepletionEvent(MetaSim::Tick depletion_time);     // 注册能量耗尽事件
        void cancelEnergyDepletionEvent();                                    // 取消能量耗尽事件

        // 任务管理
        ALAPSyncTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);

        // 队列管理
        void addToReadyQueue(AbsRTTask *task);
        void removeFromReadyQueue(AbsRTTask *task);
        void addToWaitingQueue(AbsRTTask *task);
        void removeFromWaitingQueue(AbsRTTask *task);
        bool isInReadyQueue(AbsRTTask *task) const;
        bool isInWaitingQueue(AbsRTTask *task) const;
        AbsRTTask *getHighestPriorityTaskFromReadyQueue();

        // 抢占管理
        void checkAndPreempt();
        void checkAndPreemptOnAllCPUs();
        bool shouldPreempt(AbsRTTask *running_task, AbsRTTask *new_task);
        AbsRTTask *getRunningTaskOnCPU(CPU *cpu);

        // Tick事件调度
        void scheduleNextTick();

        // CPU管理
        int getFreeCPUCount();
        CPU *getFreeCPU();
        void dispatchTask(AbsRTTask *task, CPU *cpu);

    public:
        // 构造函数/析构函数
        ALAPSyncScheduler();
        ALAPSyncScheduler(const std::vector<std::string> &params);
        virtual ~ALAPSyncScheduler();

        // 工厂方法
        static std::unique_ptr<ALAPSyncScheduler>
            createInstance(const std::vector<std::string> &params);

        // Scheduler接口实现
        void addTask(AbsRTTask *task, const std::string &params) override;
        void removeTask(AbsRTTask *task) override;
        void notify(AbsRTTask *task) override;  // ALAP-Sync: 不再扣减能量（已在批量时扣减）
        bool isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                          AbsRTTask *t) override;

        // 核心调度方法
        void schedule();
        AbsRTTask *getFirst() override;    // ALAP-Sync: 废弃，返回nullptr
        AbsRTTask *getTaskN(unsigned int n) override;  // ALAP-Sync: 返回批量中的第n个任务
        void commitDispatch();  // ⭐ 确认派发，真正扣除能量（同时间戳并发派发修复）

        void insert(AbsRTTask *task) override;
        void extract(AbsRTTask *task);

        // 生命周期
        void newRun() override;
        void endRun() override;
        void onTaskEnd(AbsRTTask *task);

        // ⭐ 能量耗尽处理（public供ALAPSyncEnergyDepletedEvent调用）
        void onEnergyDepleted();

        // 能量管理接口
        double getCurrentEnergy() const override { return _current_energy; }
        double getInitialEnergy() const { return _initial_energy; }
        double getMaxEnergy() const { return _max_energy; }
        double calculateUnitEnergyForTask(AbsRTTask *task);  // MRTKernel需要调用

        // ⭐ EnergyInfoProvider接口实现
        double getTotalEnergyConsumed() const override { return _stats.total_energy_consumed; }
        double getTotalEnergyHarvested() const override { return _stats.total_energy_harvested; }
        double getTaskUnitEnergy(AbsRTTask *task) const override;
        double getTaskTotalEnergy(AbsRTTask *task) const override;
        void setSuspendReason(AbsRTTask *task, const std::string &reason);
        std::string getSuspendReason(AbsRTTask *task) const override;
        void clearSuspendReason(AbsRTTask *task) override;

        // ⭐ 运行时能量检查接口（V28.15新增）
        void startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);  // 开始对任务的能量监控
        void stopEnergyCheckForTask(AbsRTTask *task);  // 停止对任务的能量监控

        // 队列访问接口
        const std::deque<AbsRTTask *> &getReadyQueue() const { return _ready_queue; }
        const std::map<AbsRTTask *, std::string> getTaskWorkloads() const;

        // ALAP-Sync批量调度接口
        const std::vector<AbsRTTask *> &getCurrentBatchTasks() const { return _current_batch_tasks; }
        int getCurrentBatchSize() const { return _current_batch_size; }
        bool isBatchScheduledThisTick() const { return _batch_scheduled_this_tick; }

        // Kernel管理
        void setKernel(AbsKernel *kernel) override;  // ⭐ V96修复
        MRTKernel *getKernel();

        // 配置接口
        void setPVConfig(double efficiency, double area, const std::string &solar_file);
        void setStartTimeOffset(MetaSim::Tick offset);

        // 统计和调试
        void printStats() const;
        std::string getEnergyStatus() const;

        // 友元类声明
        friend class ALAPSyncTickEvent;
        friend class ALAPSyncEnergyCheckEvent;
        friend class ALAPSyncEnergyDepletedEvent;  // ⭐ Bug修复：能量耗尽预测事件
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::ALAPSyncScheduler>
        registerALAPSyncScheduler("gpfp_alap_sync");
}

#endif // GPFP_ALAP-Sync_SCHEDULER_HPP
