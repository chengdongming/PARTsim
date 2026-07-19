#ifndef GPFP_ALAP_BLOCK_SCHEDULER_HPP
#define GPFP_ALAP_BLOCK_SCHEDULER_HPP

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
    class ALAPBlockScheduler;
    class MRTKernel;
    class JSONTrace;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // ALAP-Block Tick级调度事件（每1ms触发一次）
    // =====================================================
    class ALAPBlockTickEvent : public MetaSim::Event {
    private:
        ALAPBlockScheduler *_scheduler;

    public:
        ALAPBlockTickEvent(ALAPBlockScheduler *scheduler);
        void doit() override;
    };
    class ALAPBlockScheduler; // 前置声明

    // =====================================================
    // ALAP专属唤醒闹钟事件
    // =====================================================
    class ALAPWakeEvent : public MetaSim::Event {
    private:
        ALAPBlockScheduler *_scheduler;
    public:
        ALAPWakeEvent(ALAPBlockScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // ALAP-Block运行时能量检查事件（每1ms检查运行中任务的能量）
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
    // =====================================================
    /*
    class ALAPBlockEnergyCheckEvent : public MetaSim::Event {
    private:
        ALAPBlockScheduler *_scheduler;
        AbsRTTask *_task;
        CPU *_cpu;
        int _ms_executed;  // 已执行的ms数

    public:
        ALAP-BlockEnergyCheckEvent(ALAPBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu);
        void doit() override;
        int getMsExecuted() const { return _ms_executed; }
        void setMsExecuted(int ms) { _ms_executed = ms; }
    };
    */

    // =====================================================
    // ⭐ 能量耗尽预测事件（虚空借电Bug修复）
    // 当系统预测到电池将在某时刻耗尽时，在事件队列中插入此事件
    // 确保任务在电池真正耗尽时被正确中断，而不是"惯性"跑完
    // =====================================================
    class ALAPBlockEnergyDepletedEvent : public MetaSim::Event {
    private:
        ALAPBlockScheduler *_scheduler;

    public:
        MetaSim::Tick _scheduled_depletion_time;  // 预测的耗尽时刻
        double _energy_at_prediction;               // 预测时的能量值

    public:
        ALAPBlockEnergyDepletedEvent(ALAPBlockScheduler *scheduler);
        void doit() override;

        MetaSim::Tick getScheduledDepletionTime() const { return _scheduled_depletion_time; }
        double getEnergyAtPrediction() const { return _energy_at_prediction; }
    };

    // =====================================================
    // ALAPBlockTaskModel 类声明
    // =====================================================
    class ALAPBlockTaskModel : public TaskModel {
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
        ALAPBlockTaskModel(AbsRTTask *t, int period, int wcet,
                     const std::string &workload_type,
                     double energy_coefficient = 1.0,
                     MetaSim::Tick arrival_offset = 0);
        virtual ~ALAPBlockTaskModel();

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
    // ALAPBlockScheduler ��声明 - ALAP-Block调度算法（基于TIE + ALAP时序门控）
    // =====================================================
    class ALAPBlockScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _dispatching_tasks_total_energy; // 本次dispatch中已调度任务的总能耗
        std::set<AbsRTTask *> _counted_tasks_in_dispatch; // 本次dispatch中已计数的任务，避免重复
        std::vector<AbsRTTask *> _dispatch_selection_order;
        MetaSim::Tick _selection_tick;
        uint64_t _selection_generation;
        bool _selection_frozen;
        MetaSim::Tick _energy_commit_tick;
        bool _energy_commit_valid;
        std::set<AbsRTTask *> _paid_pending_tasks;
        std::map<AbsRTTask *, MetaSim::Tick> _pending_payment_ticks;
        std::set<AbsRTTask *> _paid_execution_credit_tasks;
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
        ALAPBlockTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度
        MetaSim::Tick _last_prediction_tick = -1;  // ⭐ 上次更新能量预测的tick（防止同一tick内重复预测）
        // ALAP 专属唤醒闹钟
        ALAPWakeEvent* _alap_wake_event;
        // ⭐ 能量耗尽预测事件（Bug修复：防止虚空借电）
        ALAPBlockEnergyDepletedEvent *_energy_depleted_event;

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, ALAPBlockTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::map<CPU *, AbsRTTask *> _running_tasks;
        MRTKernel *_kernel;
        JSONTrace *_trace_logger = nullptr;
        bool _semantic_trace_enabled = false;

        // ========== 运行时能量检查事件（每任务一个） ==========
        // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
        // std::map<AbsRTTask *, ALAP-BLOCKEnergyCheckEvent *> _energy_check_events;

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
            int total_alap_forced_idle = 0;  // ⭐ ALAP: 强制休眠次数
        } _stats;

        // ========== 能量耗尽管理 ==========
        bool _energy_depleted;  // ⭐ 能量是否已耗尽（Bug修复）
        bool _alap_blocking;   // ⭐ ALAP-Block 特有：严格阻塞标志（能量不足时阻塞全部调度）
        AbsRTTask *_blocking_task;  // ⭐ 当前导致严格阻塞的任务实例

        // ========== 抢占防抖 ==========
        // ⭐ 防止频繁抢占：在同一个tick内，同一个任务不应该被反复抢占
        AbsRTTask *_last_preempted_task;  // 最近被挂起的任务
        MetaSim::Tick _last_preempted_tick;  // 最近被挂起的tick

        // ========== 私有方法 ==========

        // 核心调度逻辑
        void performTickScheduling();
        void collectEnergyAtTickBoundary();

        // ⭐ ALAP时序门控（阶段一）
        bool checkALAPTimingGate();  // 检查是否需要强制休眠
        MetaSim::Tick calculateSlackForTask(AbsRTTask *task);  // 计算任务的Slack
        MetaSim::Tick calculateSlackForTask(
            AbsRTTask *task, MetaSim::Tick current_time);

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
        ALAPBlockTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);
        void clearPersistentTaskState(AbsRTTask *task);
        void resetTickDispatchState();
        void clearTaskTickSelection(AbsRTTask *task);
        std::vector<AbsRTTask *> collectActiveJobs(
            MetaSim::Tick current_time);
        std::vector<AbsRTTask *> collectALAPCandidates(
            const std::vector<AbsRTTask *> &active_tasks,
            MetaSim::Tick current_time);
        bool hasHigherRMPriority(
            AbsRTTask *lhs, AbsRTTask *rhs);
        void sortByRMPriority(
            std::vector<AbsRTTask *> &tasks);
        double getConfiguredUnitEnergyForTask(
            AbsRTTask *task) const;
        void commitTickEnergy(
            MetaSim::Tick tick, double energy);
        void cancelStaleDispatches(
            const std::vector<AbsRTTask *> &previous_selection);

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

        void scheduleNextTick();

        // ⭐ 能量耗尽预测与事件注册（Bug修复）
        double calculateTotalPowerConsumption();                              // 计算当前总功耗
        MetaSim::Tick predictTimeToDepletion(double energy, double power);    // 预测能量耗尽时间
        void scheduleEnergyDepletionEvent(MetaSim::Tick depletion_time);     // 注册能量耗尽事件
        void cancelEnergyDepletionEvent();                                    // 取消能量耗尽事件

        // 阻塞状态管理
        void clearBlockingStateIfOwner(AbsRTTask *task, const char *reason);
        void tryImmediateRedispatch(const char *reason);

        // CPU管理
        int getFreeCPUCount();
        CPU *getFreeCPU();
        void dispatchTask(AbsRTTask *task, CPU *cpu);

    public:
        // ⭐ 能量耗尽处理（public供ALAPBlockEnergyDepletedEvent调用）
        void onEnergyDepleted();
        // 构造函数/析构函数
        ALAPBlockScheduler();
        ALAPBlockScheduler(const std::vector<std::string> &params);
        virtual ~ALAPBlockScheduler();

        // 工厂方法
        static std::unique_ptr<ALAPBlockScheduler>
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
        void setTraceLogger(void *trace) override {
            _trace_logger = static_cast<JSONTrace *>(trace);
        }
        void setSemanticTraceEnabled(bool enabled) override {
            _semantic_trace_enabled = enabled;
        }

        // ⭐ 运行时能量检查接口（V28.15新增）
        // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
        // void startEnergyCheckForTask(AbsRTTask *task, CPU *cpu);  // 开始对任务的能量监控
        // void stopEnergyCheckForTask(AbsRTTask *task);  // 停止对任务的能量监控

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
        friend class ALAPBlockTickEvent;
        friend class ALAPBlockSchedulerTestPeer;
        // ⭐ V40重构：能量检查事件已删除
        // friend class ALAP-BLOCKEnergyCheckEvent;
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::ALAPBlockScheduler>
        registerALAPBlockScheduler("gpfp_alap_block");
}

#endif // GPFP_ALAP_BLOCK_SCHEDULER_HPP
