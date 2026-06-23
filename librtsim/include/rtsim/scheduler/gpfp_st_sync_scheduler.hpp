#ifndef GPFP_ST_Sync_SCHEDULER_HPP
#define GPFP_ST_Sync_SCHEDULER_HPP

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
#include <cstdint>

namespace RTSim {

    // 前向声明
    class CPU;
    class AbsRTTask;
    class STSyncScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // ST-Sync Tick级调度事件（每1ms触发一次）
    // =====================================================
    class STSyncTickEvent : public MetaSim::Event {
    private:
        STSyncScheduler *_scheduler;

    public:
        STSyncTickEvent(STSyncScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // V116: ST-Sync 组唤醒事件（消灭"迷失在深渊的松弛时间闹钟"Bug）
    // 当能量不足挂起任务组时，在最小Slack时间后唤醒调度器
    // =====================================================
    class STSyncGroupWakeEvent : public MetaSim::Event {
    private:
        STSyncScheduler *_scheduler;
        MetaSim::Tick _wake_time;
        bool _valid;  // 防止幽灵唤醒

    public:
        STSyncGroupWakeEvent(STSyncScheduler *scheduler);
        void doit() override;
        void schedule(MetaSim::Tick wake_time);
        void invalidate() { _valid = false; }
        bool isValid() const { return _valid; }
        MetaSim::Tick getWakeTime() const { return _wake_time; }
    };

    // =====================================================
    // ST-Sync运行时能量检查事件（每1ms检查运行中任务的能量）
    // =====================================================
    class STSyncEnergyCheckEvent : public MetaSim::Event {
    private:
        STSyncScheduler *_scheduler;
        AbsRTTask *_task;
        CPU *_cpu;
        int _ms_executed;  // 已执行的ms数

    public:
        STSyncEnergyCheckEvent(STSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu);
        void doit() override;
        int getMsExecuted() const { return _ms_executed; }
        void setMsExecuted(int ms) { _ms_executed = ms; }
    };

    // =====================================================
    // STSyncTaskModel 类声明
    // =====================================================
    class STSyncTaskModel : public TaskModel {
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
        STSyncTaskModel(AbsRTTask *t, int period, int wcet,
                      const std::string &workload_type,
                      double energy_coefficient = 1.0,
                      MetaSim::Tick arrival_offset = 0);
        virtual ~STSyncTaskModel();

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
    // STSyncScheduler 类声明 - ST-Sync批量调度算法
    // =====================================================
    class STSyncScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _dispatching_tasks_total_energy; // 本次dispatch中已调度任务的总能耗
        MetaSim::Tick _last_tick_time;       // 上次tick时间
        MetaSim::Tick _last_collection_time; // 上次能量收集时间

        // ========== 太阳能配置 ==========
        std::string _solar_data_file;
        double _pv_efficiency;
        double _pv_area_m2;
        bool _use_real_solar_data;
        MetaSim::Tick _start_time_offset;
        double _base_harvest_rate;  // ⭐ V93修复：从配置读取基础收集率 (J/ms)

        // ========== Tick事件 ==========
        STSyncTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度

        // ========== V116: 组唤醒事件（消灭"迷失在深渊的松弛时间闹钟"Bug） ==========
        STSyncGroupWakeEvent *_group_wake_event;  // Slack时间后的唤醒定时器

        // ========== 本次dispatch中已计数的任务（用于逐渐扣除模式） ==========
        std::set<AbsRTTask *> _counted_tasks_in_dispatch; // 本次dispatch中已计数的任务，避免重复

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, STSyncTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::vector<AbsRTTask *> _deferred_arrivals;
        std::map<CPU *, AbsRTTask *> _running_tasks;
        MRTKernel *_kernel;

        // 空壳映射，仅用于兼容旧清理路径；ST-Sync 不再依赖按任务运行时能量事件。
        std::map<AbsRTTask *, STSyncEnergyCheckEvent *> _energy_check_events;

        // ========== WCET完成追踪（用于批量调度判断任务是否已完成） ==========
        std::set<AbsRTTask *> _tasks_completed_wcet;  // 已达到WCET的任务集合

        // ========== ST-Sync批量调度状态 ==========
        std::vector<AbsRTTask *> _current_batch_tasks;  // 当前批量任务（tick边界预计算）
        std::vector<AbsRTTask *> _preempt_batch_tasks;    // 抢占批量任务（mid-tick抢占创建的微型批���）
        bool _batch_scheduled_this_tick;                // 本tick是否已批量调度
        bool _energy_depleted;                          // 能量是否已耗尽（Bug #5修复）
        int _current_batch_size;                        // 当前批量大小
        MetaSim::Tick _selection_tick;                  // 当前冻结同步组所属tick
        uint64_t _selection_generation;                 // 每次冻结同步组递增，防stale dispatch
        bool _selection_frozen;                         // 当前tick是否已有冻结同步组
        MetaSim::Tick _energy_commit_tick;              // 能量提交所属tick
        uint64_t _energy_commit_generation;             // 能量提交所属generation
        bool _energy_commit_valid;                      // 当前generation是否已提交能量
        bool _v108_batch_energy_checked;                // ⭐ V108: 本tick是否已做过批量能量检查
        bool _v108_batch_energy_sufficient;             // ⭐ V108: 本tick批量能量是否充足
        int _v108_batch_k_approved;                     // ⭐ V108: 已批准扣除能量的任务数
        double _v108_batch_start_energy;                // ⭐ V108: 批量检查开始时的能量快照
        double _v108_batch_total_energy;                // ⭐ V108: 批量任务总能量（已扣除）
        MetaSim::Tick _last_v108_insert_time;            // ⭐ V108: 上次insert的时间
        size_t _v108_last_ready_queue_size;           // ⭐ V108: 上次检查时的就绪队列大小
        MetaSim::Tick _last_v108_check_time;             // ⭐ V108: 上次检查的时间

        // ========== ST深度充电管理 ==========
        bool _deep_charging;           // ⭐ ST特有：是否处于深度充电模式
        MetaSim::Tick _charge_start_time;  // 充电开始时间

        // ========== V130: 深度休眠锁（消灭1ms碎片化抖动） ==========
        bool _is_charging_sleep;       // ⭐ 全局深度休眠锁：能量不足时锁住，充满电或Slack=0时解锁

        // ========== 抢占防抖 ==========
        AbsRTTask *_last_preempted_task;                // 最近被抢占的任务
        MetaSim::Tick _last_preempted_tick;             // 最近抢占发生的时间
        size_t _last_ready_queue_size;                  // V96：上次tick的就绪队列大小

        // ========== V115：挂起原因追踪（消灭幽灵抢占） ==========
        std::map<AbsRTTask *, std::string> _suspend_reasons;  // 任务被挂起的真正原因
        void setSuspendReason(AbsRTTask *task, const std::string &reason);
        std::string getSuspendReason(AbsRTTask *task) const override;  // 实现EnergyInfoProvider接口
        void clearSuspendReason(AbsRTTask *task) override;  // 实现EnergyInfoProvider接口
        void clearPersistentTaskState(AbsRTTask *task);

        // ========== 能量记账（每ms累计） ==========
        struct TaskEnergyAccount {
            double total_consumed;      // 累计消耗能量（每ms累加）
            MetaSim::Tick start_time;
            MetaSim::Tick last_unit_time;

            TaskEnergyAccount() : total_consumed(0.0), start_time(0), last_unit_time(0) {}
        };
        std::map<AbsRTTask *, TaskEnergyAccount> _energy_accounts;

        // ========== 统计信息 ==========
        struct {
            int total_scheduled = 0;
            int total_task_completions = 0;
            int total_skipped_energy = 0;
            int total_deadline_misses = 0;
            double total_energy_consumed = 0.0;
            double total_energy_harvested = 0.0;
            int total_tick_count = 0;
            int total_batch_schedules = 0;        // ST-Sync: 批量调度次数
            int total_batch_skipped = 0;          // ST-Sync: 批量调度跳过次数
            int total_alap_forced_idle = 0;       // ⭐ ALAP: 强制休眠次数
        } _stats;

        // ========== 私有方法 ==========

        // 核心调度逻辑 - ST-Sync批量调度
        void performTickScheduling();

        // ⭐ ALAP时序门控（阶段一）
        bool checkALAPBatchTimingGate(const std::vector<AbsRTTask *> &batch);  // ⭐ 基于批次的ALAP时序门控（原论文正确实现）
        bool checkALAPTimingGate();  // 全局ALAP时序门控（保留兼容性）
        MetaSim::Tick calculateSlackForTask(AbsRTTask *task);  // 计算任务的Slack
        MetaSim::Tick calculateMinSlack();  // ⭐ ST特有：计算所有就绪任务的最小Slack

        // ⭐ V89: 同步组充电相关
        MetaSim::Tick calculateGroupWakeTime(MetaSim::Tick group_slack, double group_energy);  // 计算组唤醒时间
        void scheduleGroupWakeEvent(MetaSim::Tick wake_time);  // 设置组唤醒定时器

        // ⭐ 过期任务清理
        void cleanupExpiredTasks();  // 清理超过截止期的旧任务实例

        // ⭐ 运行时能量检查和任务中断（V28.15新增）
        void checkAndInterruptRunningTasks();  // 检查所有运行中的任务，能量不足时中断
        void clampCurrentEnergyNonNegative(const std::string &context);
        std::vector<AbsRTTask *> collectActiveRunningBatchTasks();
        bool isTaskInActiveRunningBatch(AbsRTTask *task);
        double calculateBatchUnitEnergy(const std::vector<AbsRTTask *> &tasks);
        void suspendBatchForInsufficientEnergy(const std::vector<AbsRTTask *> &tasks,
                                              double required_energy,
                                              const std::string &context);
        void rebuildApprovedBatchForImmediateDispatch();

        // ST-Sync批量计算
        int calculateBatchSize();                              // 计算批量大小 k
        void executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy);  // 执行批量调度

        // 能量计算
        double calculateTotalEnergyForTask(AbsRTTask *task); // 计算任务总能耗
        double calculateRemainingEnergyForTask(AbsRTTask *task); // 计算任务剩余能耗
        double calculatePowerForWorkload(const std::string &workload, double frequency);
        double collectSolarEnergy(MetaSim::Tick current_time);
        double getSolarIrradiance(int64_t time_ms);

        // 任务管理
        STSyncTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);

        // 队列管理
        void addToReadyQueue(AbsRTTask *task);
        void removeFromReadyQueue(AbsRTTask *task);
        void addToWaitingQueue(AbsRTTask *task);
        void removeFromWaitingQueue(AbsRTTask *task);
        void promoteWaitingTasksToReadyQueue(const std::string &context);
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
        STSyncScheduler();
        STSyncScheduler(const std::vector<std::string> &params);
        virtual ~STSyncScheduler();

        // 工厂方法
        static std::unique_ptr<STSyncScheduler>
            createInstance(const std::vector<std::string> &params);

        // Scheduler接口实现
        void addTask(AbsRTTask *task, const std::string &params) override;
        void removeTask(AbsRTTask *task) override;
        void notify(AbsRTTask *task) override;  // ST-Sync: 不再扣减能量（已在批量时扣减）
        bool isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                          AbsRTTask *t) override;

        // 核心调度方法
        void schedule();
        AbsRTTask *getFirst() override;    // ST-Sync: 废弃，返回nullptr
        AbsRTTask *getTaskN(unsigned int n) override;  // ST-Sync: 返回批量中的第n个任务
        void insert(AbsRTTask *task) override;
        void extract(AbsRTTask *task);

        // 生命周期
        void newRun() override;
        void endRun() override;
        void onTaskEnd(AbsRTTask *task);

        // 能量管理接口
        double getCurrentEnergy() const override { return _current_energy; }
        double getInitialEnergy() const { return _initial_energy; }
        double getMaxEnergy() const { return _max_energy; }
        bool isChargingSleepActive() const { return _is_charging_sleep || _deep_charging; }
        bool isEnergyDepletedActive() const { return _energy_depleted; }
        double calculateUnitEnergyForTask(AbsRTTask *task);  // MRTKernel需要调用

        // ⭐ EnergyInfoProvider接口实现
        double getTotalEnergyConsumed() const override { return _stats.total_energy_consumed; }
        double getTotalEnergyHarvested() const override { return _stats.total_energy_harvested; }
        double getTaskUnitEnergy(AbsRTTask *task) const override;
        double getTaskTotalEnergy(AbsRTTask *task) const override;

        // 兼容旧调用点；当前实现不启动按任务运行时能量事件。
        void startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);
        void stopEnergyCheckForTask(AbsRTTask *task);

        // 队列访问接口
        const std::deque<AbsRTTask *> &getReadyQueue() const { return _ready_queue; }
        const std::map<AbsRTTask *, std::string> getTaskWorkloads() const;

        // ST-Sync批量调度接口
        const std::vector<AbsRTTask *> &getCurrentBatchTasks() const { return _current_batch_tasks; }
        int getCurrentBatchSize() const { return _current_batch_size; }
        bool isBatchScheduledThisTick() const { return _batch_scheduled_this_tick; }

        // Kernel管理
        void setKernel(AbsKernel *kernel) override;  // ⭐ V96修复：重写基类方法
        MRTKernel *getKernel();

        // 配置接口
        void setPVConfig(double efficiency, double area, const std::string &solar_file);
        void setStartTimeOffset(MetaSim::Tick offset);

        // 统计和调试
        void printStats() const;
        std::string getEnergyStatus() const;

        // 友元类声明
        friend class MRTKernel;
        friend class STSyncTickEvent;
        friend class STSyncEnergyCheckEvent;
        friend class STSyncGroupWakeEvent;  // ⭐ V116：组唤醒事件需要访问私有成员
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::STSyncScheduler>
        registerSTSyncScheduler("gpfp_st_sync");
}

#endif // GPFP_ST_Sync_SCHEDULER_HPP
