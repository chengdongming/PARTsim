#ifndef GPFP_ST_NONBLOCK_SCHEDULER_HPP
#define GPFP_ST_NONBLOCK_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <rtsim/abstask.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/energy_info_provider.hpp>
#include <metasim/factory.hpp>
#include <cstdint>
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
    class STNonBlockScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // ST-NonBlock Tick级调度事件（每1ms触发一次��
    // =====================================================
    class STNonBlockTickEvent : public MetaSim::Event {
    private:
        STNonBlockScheduler *_scheduler;

    public:
        STNonBlockTickEvent(STNonBlockScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // ST-NonBlock 被跳过任务的专属唤醒定时器
    // ⭐ 策略2核心：高优缺电时设置专属定时器，Slack=0或满电时唤醒抢占
    // =====================================================
    class STNonBlockWakeEvent : public MetaSim::Event {
    private:
        STNonBlockScheduler *_scheduler;
        AbsRTTask *_task;           // 被跳过的任务
        MetaSim::Tick _wake_time;   // 唤醒时间

    public:
        STNonBlockWakeEvent(STNonBlockScheduler *scheduler, AbsRTTask *task, MetaSim::Tick wake_time);
        void doit() override;
        AbsRTTask *getTask() const { return _task; }
        MetaSim::Tick getWakeTime() const { return _wake_time; }
    };

    // =====================================================
    // ST-NonBlock运行时能量检查事件（每1ms检查运行中任务的能量）
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
    // =====================================================
    /*
    class ST-NonBlockEnergyCheckEvent : public MetaSim::Event {
    private:
        STNonBlockScheduler *_scheduler;
        AbsRTTask *_task;
        CPU *_cpu;
        int _ms_executed;  // 已执行的ms数

    public:
        ST-NonBlockEnergyCheckEvent(STNonBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu);
        void doit() override;
        int getMsExecuted() const { return _ms_executed; }
        void setMsExecuted(int ms) { _ms_executed = ms; }
    };
    */

    // =====================================================
    // STNonBlockTaskModel 类声明
    // =====================================================
    class STNonBlockTaskModel : public TaskModel {
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
        STNonBlockTaskModel(AbsRTTask *t, int period, int wcet,
                     const std::string &workload_type,
                     double energy_coefficient = 1.0,
                     MetaSim::Tick arrival_offset = 0);
        virtual ~STNonBlockTaskModel();

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
    // STNonBlockScheduler 类声明
    // =====================================================
    class STNonBlockScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _dispatching_tasks_total_energy; // 本次dispatch中已调度任务的总能耗
        std::set<AbsRTTask *> _counted_tasks_in_dispatch; // 本次dispatch中已计数的任务，避免重复
        std::vector<AbsRTTask *> _dispatch_selection_order; // 本轮dispatch已选中的稳定顺序
        std::set<AbsRTTask *> _energy_deducted_tasks; // 已扣除初始能量的任务（跨tick持久化）
        std::set<AbsRTTask *> _newly_dispatched_this_tick; // ⭐ V42：当前tick新调度的任务（用于跳过续期扣除）
        bool _in_tick_boundary_dispatch = false;  // ⭐ 标记是否在tick边界调度中（用于能量扣除时机控制）
        MetaSim::Tick _selection_tick;       // 当前冻结选择对应tick
        uint64_t _selection_generation;      // 每次tick选择递增，防stale EndDispatch
        bool _selection_frozen;              // 当前tick是否已经冻结选择
        MetaSim::Tick _energy_commit_tick;   // 最近一次能量提交tick
        uint64_t _energy_commit_generation;  // 最近一次能量提交generation
        bool _energy_commit_valid;           // 是否已有能量提交记录
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
        STNonBlockTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, STNonBlockTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::map<CPU *, AbsRTTask *> _running_tasks;
        MRTKernel *_kernel;

        // ========== 运行时能量检查事件（每任务一个） ==========
        // ⭐ V40��构：能量检查事件已删除，能量由performTickScheduling处理
        // std::map<AbsRTTask *, ST-NonBlockEnergyCheckEvent *> _energy_check_events;

        // ========== 能量耗尽管理 ==========
        bool _energy_depleted;  // ⭐ 能量是否已耗尽（Bug修复）

        // ========== V87：待唤醒任务能量预留 ==========
        AbsRTTask *_pending_wake_task;  // ⭐ 待唤醒的高优先级任务
        double _pending_wake_energy;    // ⭐ 为待唤醒任务预留的能量

        // ========== ST深度充电管理 ==========
        bool _deep_charging;           // ⭐ ST特有：是否处于深度充电模式
        MetaSim::Tick _charge_start_time;  // 充电开始时间

        // ========== V130: 深度休眠锁（注意：ST-NonBlock不使用全局锁！） ==========
        // ⭐ ST-NonBlock特有：不上锁，只设置专属唤醒定时器，继续贪婪捡漏
        bool _is_charging_sleep;       // 保留变量但不使用，仅用于兼容性

        // ========== ST-NonBlock专属：被跳过任务的唤醒定时器 ==========
        // ⭐ 策略2核心：高优缺电时设置专属定时器，Slack=0或满电时唤醒抢占
        std::map<AbsRTTask *, STNonBlockWakeEvent *> _skip_wake_events;  // 被跳过任务的唤醒定时器
        std::set<AbsRTTask *> _skipped_tasks;  // 当前被跳过（因能量不足）的任务集合

        // ========== 抢占防抖 ==========
        // ⭐ 防止频繁抢占：在同一个tick内，同一个任务不应该被反复抢占
        AbsRTTask *_last_preempted_task;  // 最近被挂起的任务
        MetaSim::Tick _last_preempted_tick;  // 最近被挂起的tick

        // ========== 能量记账（每ms累计） ==========
        struct TaskEnergyAccount {
            double total_consumed;      // 累计消耗能量（每ms累加）
            MetaSim::Tick start_time;
            MetaSim::Tick last_unit_time;

            TaskEnergyAccount() : total_consumed(0.0), start_time(0), last_unit_time(0) {}
        };
        std::map<AbsRTTask *, TaskEnergyAccount> _energy_accounts;
        std::map<AbsRTTask *, std::string> _suspend_reasons;
        std::map<AbsRTTask *, MetaSim::Tick> _deadline_miss_arrivals;

        // ========== 统计信息 ==========
        struct {
            int total_scheduled = 0;
            int total_task_completions = 0;
            int total_skipped_energy = 0;
            int total_deadline_misses = 0;
            double total_energy_consumed = 0.0;
            double total_energy_harvested = 0.0;
            int total_tick_count = 0;
            int total_alap_forced_idle = 0;  // ⭐ ALAP: 强制休眠次数
        } _stats;

        // ========== 私有方法 ==========

        // 核心调度逻辑
        void performTickScheduling();
        void collectEnergyAtTickBoundary();

        // ⭐ ALAP时序门控（阶段一）
        bool checkALAPTimingGate();  // 检查是否需要强制休眠
        MetaSim::Tick calculateSlackForTask(AbsRTTask *task);  // 计算任务的Slack
        MetaSim::Tick calculateMinSlack();  // ⭐ ST特有：计算所有就绪任务的最小Slack

        // ⭐ 过期任务清理
        void cleanupExpiredTasks();  // 清理超过截止期的旧任务实例

        // ⭐ 运行时能量检查和任务中断（V28.15新增）
        void checkAndInterruptRunningTasks();  // 检查所有运行中的任务，能量不足时中断

        // 能量计算
        double calculateTotalEnergyForTask(AbsRTTask *task); // 计算任务总能耗
        double calculatePowerForWorkload(const std::string &workload, double frequency);
        double collectSolarEnergy(MetaSim::Tick current_time);
        double getSolarIrradiance(int64_t time_ms);

        // 任务管理
        STNonBlockTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);
        void clearSkippedWakeState(AbsRTTask *task);
        void clearPersistentTaskState(AbsRTTask *task);
        void resetTickDispatchState();
        void clearTaskTickSelection(AbsRTTask *task);
        void markTaskSelectedThisTick(AbsRTTask *task);
        void accountInitialEnergyForSelectedTasks(const std::string &log_prefix);
        std::vector<AbsRTTask *> collectActiveJobs(MetaSim::Tick current_time);
        bool hasHigherRMPriority(AbsRTTask *lhs, AbsRTTask *rhs);
        void sortByRMPriority(std::vector<AbsRTTask *> &tasks);
        double getConfiguredUnitEnergyForTask(AbsRTTask *task) const;
        void commitTickEnergy(MetaSim::Tick tick, double energy);
        void cancelStaleDispatches(const std::vector<AbsRTTask *> &previous_selection);

        // ST wake/pending helpers (Phase 4 - for future use)
        void handleWakeTrigger(AbsRTTask *task);
        void scheduleWakeForSkippedTask(AbsRTTask *task, MetaSim::Tick current_time);
        void clearPendingWakeIfMatches(AbsRTTask *task);

        // Allow wake event to access scheduler internals
        friend class STNonBlockWakeEvent;

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
        bool shouldPreempt(CPU *cpu, AbsRTTask *new_task);
        AbsRTTask *getRunningTaskOnCPU(CPU *cpu);

        // Tick事件调度
        void scheduleNextTick();

        // CPU管理
        int getFreeCPUCount();
        CPU *getFreeCPU();
        void dispatchTask(AbsRTTask *task, CPU *cpu);

    public:
        // 构造函数/析构函数
        STNonBlockScheduler();
        STNonBlockScheduler(const std::vector<std::string> &params);
        virtual ~STNonBlockScheduler();

        // 工厂方法
        static std::unique_ptr<STNonBlockScheduler>
            createInstance(const std::vector<std::string> &params);

        // Scheduler接口实现
        void addTask(AbsRTTask *task, const std::string &params) override;
        void removeTask(AbsRTTask *task) override;
        void notify(AbsRTTask *task) override;
        bool isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                          AbsRTTask *t) override;

        // 核心调度方法
        void schedule();
        AbsRTTask *getFirst() override;
        AbsRTTask *getTaskN(unsigned int n) override;
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
        double calculateUnitEnergyForTask(AbsRTTask *task);  // MRTKernel需要调用
        double calculateMinTaskEnergyInReadyQueue();  // ⭐ 计算就绪队列中最小任务能耗（修复循环问题）

        // ⭐ EnergyInfoProvider接口实现
        double getTotalEnergyConsumed() const override { return _stats.total_energy_consumed; }
        double getTotalEnergyHarvested() const override { return _stats.total_energy_harvested; }
        double getTaskUnitEnergy(AbsRTTask *task) const override;
        double getTaskTotalEnergy(AbsRTTask *task) const override;
        void setSuspendReason(AbsRTTask *task, const std::string &reason);
        std::string getSuspendReason(AbsRTTask *task) const override;
        void clearSuspendReason(AbsRTTask *task) override;

        // ⭐ 运行时能量检查接口（V28.15新增）
//         void startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);  // 开始对任务的能量监控
//         void stopEnergyCheckForTask(AbsRTTask *task);  // 停止对任务的能量��控

        // 队列访问接口
        const std::deque<AbsRTTask *> &getReadyQueue() const { return _ready_queue; }
        const std::map<AbsRTTask *, std::string> getTaskWorkloads() const;

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
        friend class MRTKernel;
        friend class STNonBlockTickEvent;
        friend class STNonBlockWakeEvent;  // ⭐ 策略2：被跳过任务的唤醒定时器
        // friend class ST-NonBlockEnergyCheckEvent;  /* V40重构：能量检查事件已删除 */
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::STNonBlockScheduler>
        registerSTNonBlockScheduler("gpfp_st_nonblock");
}

#endif // GPFP_ST_NONBLOCK_SCHEDULER_HPP
