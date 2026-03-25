#ifndef GPFP_ALAP_NONBLOCK_SCHEDULER_HPP
#define GPFP_ALAP_NONBLOCK_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <rtsim/abstask.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/energy_info_provider.hpp>
#include <rtsim/json_trace.hpp>
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
    class ALAPNonBlockScheduler;
    class MRTKernel;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // ALAP-NonBlock Tick级调度事件（每1ms触发一次）
    // =====================================================
    class ALAPNonBlockTickEvent : public MetaSim::Event {
    private:
        ALAPNonBlockScheduler *_scheduler;

    public:
        ALAPNonBlockTickEvent(ALAPNonBlockScheduler *scheduler);
        void doit() override;
    };

    class ALAPNonBlockScheduler; // 前置声明

    // =====================================================
    // ALAP专属唤醒闹钟事件
    // =====================================================
    class ALAPNonBlockWakeEvent : public MetaSim::Event {
    private:
        ALAPNonBlockScheduler *_scheduler;
    public:
        ALAPNonBlockWakeEvent(ALAPNonBlockScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // ALAP-NonBlock运行时能量检查事件（每1ms检查运行中任务的能量）
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
    // =====================================================
    /*
    class ALAP-NonBlockEnergyCheckEvent : public MetaSim::Event {
    private:
        ALAPNonBlockScheduler *_scheduler;
        AbsRTTask *_task;
        CPU *_cpu;
        int _ms_executed;  // 已执行的ms数

    public:
        ALAP-NonBlockEnergyCheckEvent(ALAPNonBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu);
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
    class ALAPNonBlockEnergyDepletedEvent : public MetaSim::Event {
    private:
        ALAPNonBlockScheduler *_scheduler;

    public:
        MetaSim::Tick _scheduled_depletion_time;  // 预测的耗尽时刻
        double _energy_at_prediction;               // 预测时的能量值

    public:
        ALAPNonBlockEnergyDepletedEvent(ALAPNonBlockScheduler *scheduler);
        void doit() override;

        MetaSim::Tick getScheduledDepletionTime() const { return _scheduled_depletion_time; }
        double getEnergyAtPrediction() const { return _energy_at_prediction; }
    };

    // =====================================================
    // ALAPNonBlockTaskModel 类声明
    // =====================================================
    class ALAPNonBlockTaskModel : public TaskModel {
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
        ALAPNonBlockTaskModel(AbsRTTask *t, int period, int wcet,
                     const std::string &workload_type,
                     double energy_coefficient = 1.0,
                     MetaSim::Tick arrival_offset = 0);
        virtual ~ALAPNonBlockTaskModel();

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
    // ALAPNonBlockScheduler 类声明
    // =====================================================
    class ALAPNonBlockScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _dispatching_tasks_total_energy; // 本次dispatch中已调度任务的总能耗
        std::set<AbsRTTask *> _counted_tasks_in_dispatch; // 本次dispatch中已计数的任务，避免重复
        std::vector<AbsRTTask *> _dispatch_selection_order; // 本次dispatch已选中的任务顺序，保证kernel重复查询时返回稳定结果
        std::set<AbsRTTask *> _energy_deducted_tasks; // 已扣除初始能量的任务（跨tick持久化）
        std::set<AbsRTTask *> _newly_dispatched_this_tick; // ⭐ V42：当前tick新调度的任务（用于跳过续期扣除）
        bool _in_tick_boundary_dispatch = false;  // ⭐ 标记是否在tick边界调度中（用于能量扣除时机控制）
        MetaSim::Tick _last_prediction_tick = -1;  // ⭐ 上次更新能量预测的tick（防止同一tick内重复预测）
        MetaSim::Tick _last_tick_time;       // 上次tick时间
        MetaSim::Tick _last_collection_time; // 上次能量收集时间
        ALAPNonBlockWakeEvent* _alap_wake_event = nullptr;

        // ⭐ 能量耗尽预测事件（Bug修复：防止虚空借电）
        ALAPNonBlockEnergyDepletedEvent *_energy_depleted_event = nullptr;

        // ========== 太阳能配置 ==========
        std::string _solar_data_file;
        double _pv_efficiency;
        double _pv_area_m2;
        bool _use_real_solar_data;
        MetaSim::Tick _start_time_offset;
        double _base_harvest_rate;  // ⭐ V93修复：从配置读取基础收集率 (J/ms)

        // ========== Tick事件 ==========
        ALAPNonBlockTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, ALAPNonBlockTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::map<CPU *, AbsRTTask *> _running_tasks;
        MRTKernel *_kernel;

        // ========== 运行时能量检查事件（每任务一个） ==========
        // ⭐ V40��构：能量检查事件已删除，能量由performTickScheduling处理
        // std::map<AbsRTTask *, ALAP-NonBlockEnergyCheckEvent *> _energy_check_events;

        // ========== 能量耗尽管理 ==========
        bool _energy_depleted;  // ⭐ 能量是否已耗尽（Bug修复）

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

        // ⭐ V58新增：JSONTrace指针，用于在Early Abort时注入dline_miss记录
        JSONTrace *_trace_logger = nullptr;

        // ========== 私有方法 ==========

        // 核心调度逻辑
        void performTickScheduling();
        void collectEnergyAtTickBoundary();

        // ⭐ ALAP时序门控（阶段一）
        bool checkALAPTimingGate();  // 检查是否需要强制休眠
        MetaSim::Tick calculateSlackForTask(AbsRTTask *task);  // 计算任务的Slack

        // ⭐ 过期任务清理
        void cleanupExpiredTasks();  // 清理超过截止期的旧任务实例

        // ⭐ 运行时能量检查和任务中断（V28.15新增）
        void checkAndInterruptRunningTasks();  // 检查所有运行中的任务，能量不足时中断

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
        ALAPNonBlockTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);
        void clearPersistentTaskState(AbsRTTask *task);
        void resetTickDispatchState();
        void clearTaskTickSelection(AbsRTTask *task);
        void markTaskSelectedThisTick(AbsRTTask *task);
        void accountInitialEnergyForSelectedTasks(const std::string &log_prefix);
        void refreshSchedulingAfterQueueMutation(const std::string &reason, bool immediate_dispatch = false);

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
        ALAPNonBlockScheduler();
        ALAPNonBlockScheduler(const std::vector<std::string> &params);
        virtual ~ALAPNonBlockScheduler();

        // 工厂方法
        static std::unique_ptr<ALAPNonBlockScheduler>
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

        // ⭐ 能量耗尽处理（public供ALAPNonBlockEnergyDepletedEvent调用）
        void onEnergyDepleted();

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
        friend class ALAPNonBlockTickEvent;
        friend class ALAPNonBlockEnergyDepletedEvent;  // ⭐ Bug修复：能量耗尽预测事件
        // friend class ALAP-NonBlockEnergyCheckEvent;  /* V40重构：能量检查事件已删除 */
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::ALAPNonBlockScheduler>
        registerALAPNonBlockScheduler("gpfp_alap_nonblock");
}

#endif // GPFP_ALAP_NONBLOCK_SCHEDULER_HPP
