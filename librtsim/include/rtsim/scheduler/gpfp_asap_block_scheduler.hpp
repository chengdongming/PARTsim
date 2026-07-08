#ifndef GPFP_TIE_SCHEDULER_HPP
#define GPFP_TIE_SCHEDULER_HPP

#include "config_manager.hpp"
#include "energy_bridge.hpp"
#include "scheduler.hpp"
#include <rtsim/abstask.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/energy_info_provider.hpp>
#include <metasim/factory.hpp>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include <deque>

namespace RTSim {

    // 前向声明
    class CPU;
    class AbsRTTask;
    class ASAPBlockScheduler;
    class MRTKernel;
    class JSONTrace;

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // TIE Tick级调度事件（每1ms触发一次）
    // =====================================================
    class ASAPBlockTickEvent : public MetaSim::Event {
    private:
        ASAPBlockScheduler *_scheduler;

    public:
        ASAPBlockTickEvent(ASAPBlockScheduler *scheduler);
        void doit() override;
    };

    // =====================================================
    // ASAPBlockTaskModel 类声明
    // =====================================================
    class ASAPBlockTaskModel : public TaskModel {
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
        ASAPBlockTaskModel(AbsRTTask *t, int period, int wcet,
                     const std::string &workload_type,
                     double energy_coefficient = 1.0,
                     MetaSim::Tick arrival_offset = 0);
        virtual ~ASAPBlockTaskModel();

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
    // ASAPBlockScheduler 类声明
    // =====================================================
    class ASAPBlockScheduler : public Scheduler, public EnergyInfoProvider {
    private:
        // ========== 核心配置参数 ==========
        double _current_energy;              // 当前可用能量
        double _initial_energy;              // 初始能量
        double _max_energy;                  // 最大能量容量
        double _selected_energy;             // 当前tick冻结前缀的总能耗
        std::vector<AbsRTTask *> _dispatch_selection_order;
        MetaSim::Tick _selection_tick;
        MetaSim::Tick _last_energy_commit_tick;
        bool _selection_frozen;
        bool _has_energy_commit;
        bool _selection_stopped_by_energy;
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
        ASAPBlockTickEvent *_tick_event;
        bool _first_tick_scheduled;  // 标记第一个tick是否已调度

        // ========== 任务管理 ==========
        std::map<AbsRTTask *, ASAPBlockTaskModel *> _task_models;
        std::deque<AbsRTTask *> _ready_queue;
        std::vector<AbsRTTask *> _waiting_queue;
        std::map<AbsRTTask *, MetaSim::Tick> _pending_arrivals;
        MRTKernel *_kernel;
        JSONTrace *_trace_logger;
        bool _semantic_trace_enabled;

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
        } _stats;

        // ========== 私有方法 ==========

        // 核心调度逻辑
        void performTickScheduling();
        double collectEnergyAtTickBoundary();
        std::vector<AbsRTTask *> collectActiveJobs(MetaSim::Tick current_time);
        bool isSchedulableActiveJob(AbsRTTask *task,
                                    MetaSim::Tick current_time) const;
        void sortByRMPriority(std::vector<AbsRTTask *> &tasks) const;
        std::vector<AbsRTTask *>
            selectASAPBlockPrefix(const std::vector<AbsRTTask *> &active_jobs,
                                  std::size_t processor_count,
                                  double available_energy,
                                  double &reserved_energy,
                                  bool &stopped_by_energy) const;
        void freezeTickSelection(MetaSim::Tick tick,
                                 std::vector<AbsRTTask *> selected,
                                 double reserved_energy,
                                 bool stopped_by_energy);
        void suspendUnselectedRunningJobs();
        void commitTickEnergy(MetaSim::Tick tick,
                              double available_energy);
        bool isSelectedThisTick(AbsRTTask *task) const;
        bool acceptsDispatchCompletion(AbsRTTask *task) const;

        // 能量计算
        double calculateTotalEnergyForTask(AbsRTTask *task); // 计算任务总能耗
        double calculatePowerForWorkload(const std::string &workload, double frequency);
        double collectSolarEnergy(MetaSim::Tick current_time);
        double getSolarIrradiance(int64_t time_ms);

        // 任务管理
        ASAPBlockTaskModel *getTaskModel(AbsRTTask *task);
        std::string getTaskName(AbsRTTask *task);
        void onTaskArrival(AbsRTTask *task);
        void resetTickDispatchState();
        void clearTaskTickSelection(AbsRTTask *task);

        // 队列管理
        void addToReadyQueue(AbsRTTask *task);
        void removeFromReadyQueue(AbsRTTask *task);
        void addToWaitingQueue(AbsRTTask *task);
        void removeFromWaitingQueue(AbsRTTask *task);
        bool isInReadyQueue(AbsRTTask *task) const;
        bool isInWaitingQueue(AbsRTTask *task) const;

        // Tick事件调度
        void scheduleNextTick();

    public:
        // 构造函数/析构函数
        ASAPBlockScheduler();
        ASAPBlockScheduler(const std::vector<std::string> &params);
        virtual ~ASAPBlockScheduler();

        // 工厂方法
        static std::unique_ptr<ASAPBlockScheduler>
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
        void setTraceLogger(void *trace) override;
        void setSemanticTraceEnabled(bool enabled) override;

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
        friend class ASAPBlockTickEvent;
        friend class ASAPBlockSchedulerTestPeer;
    };

} // namespace RTSim

// 工厂注册
namespace RTSim {
    static registerInFactory<RTSim::Scheduler, RTSim::ASAPBlockScheduler>
        registerASAPBlockScheduler("gpfp_asap_block");
}

#endif // GPFP_TIE_SCHEDULER_HPP
