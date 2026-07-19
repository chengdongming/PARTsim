// gpfp_asap_sync_scheduler.cpp - ALAP-Sync (Batch Tick-based Instant Energy-aware) Scheduler Implementation
// 算法特点：
// 1. 基于当前实际能量进行批量调度判断（无前瞻性预测）
// 2. 批量扣减能耗（一次性扣减k个任务的1ms能耗）
// 3. "全有或全无"批量调度：能量不足则不调度任何任务
// 4. Tick级抢占
// 5. Tick末尾收集能量

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_alap_sync_scheduler.hpp>
#include <rtsim/task.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/cpu.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/b3_timing_trace.hpp>

// 统一日志系统
#include "../../utils/unified_logger.hpp"

namespace RTSim {

    using namespace MetaSim;

    // =====================================================
    // ALAPSyncWakeEvent 实现
    // =====================================================
    ALAPSyncWakeEvent::ALAPSyncWakeEvent(ALAPSyncScheduler *scheduler)
        : MetaSim::Event("ALAPSyncWakeEvent", MetaSim::Event::_DEFAULT_PRIORITY - 1),
          _scheduler(scheduler) {
    }

    void ALAPSyncWakeEvent::doit() {
        if (!_scheduler) return;
        MRTKernel* kernel = _scheduler->getKernel();
        if (kernel) {
            SCHEDULER_LOG_INFO("⏰ [ALAP-Sync] 闹钟响起！全局最小 Slack 归零，触发内核抢占调度！");
            kernel->dispatch();
        }
    }

    // =====================================================
    // ALAPSyncTickEvent 实现
    // =====================================================

    ALAPSyncTickEvent::ALAPSyncTickEvent(ALAPSyncScheduler *scheduler)
        : MetaSim::Event("ALAPSyncTickEvent", MetaSim::Event::_DEFAULT_PRIORITY - 1),
          _scheduler(scheduler) {
        // ⭐ 关键修复：提高tick事件优先级，确保tick事件及时触发
        // 原优先级_DEFAULT_PRIORITY + 10太低，导致tick事件被延迟
    }

    void ALAPSyncTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_WARNING(std::string("⏱️ [ALAP-Sync] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ALAPSyncEnergyCheckEvent 实现 - 运行时能量检查
    // =====================================================

    ALAPSyncEnergyCheckEvent::ALAPSyncEnergyCheckEvent(ALAPSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("ALAPSyncEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void ALAPSyncEnergyCheckEvent::doit() {
        // ALAP-Sync 不再依赖异步能量检查事件作为真实扣费/挂起路径。
        // 所有能量记账和 suspend 都统一收敛到 tick 调度与 MRTKernel 可见链路。
        if (!_scheduler || !_task) {
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("ℹ️ [ALAP-Sync] 忽略过时的运行时能量检查事件: ") +
                            _scheduler->getTaskName(_task));
    }

    // =====================================================
    // ALAPSyncTaskModel 实现
    // =====================================================

    ALAPSyncTaskModel::ALAPSyncTaskModel(AbsRTTask *t, int period, int wcet,
                               const std::string &workload_type,
                               double energy_coefficient,
                               MetaSim::Tick arrival_offset)
        : TaskModel(t),
          _period(period),
          _wcet(wcet),
          _workload_type(workload_type),
          _energy_coefficient(energy_coefficient),
          _rm_priority(period),  // RM优先级：周期越短优先级越高
          _arrival_offset(arrival_offset),
          _next_release(arrival_offset),
          _total_energy(0.0),
          _unit_energy(0.0) {
        // 能量计算稍后在调度器初始化时完成
    }

    ALAPSyncTaskModel::~ALAPSyncTaskModel() {}

    Tick ALAPSyncTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ALAPSyncTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ALAPSyncTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ⭐ ALAPSyncEnergyDepletedEvent 实现（Bug修复：防止虚空借电）
    // =====================================================

    ALAPSyncEnergyDepletedEvent::ALAPSyncEnergyDepletedEvent(ALAPSyncScheduler *scheduler)
        : MetaSim::Event("ALAPSyncEnergyDepletedEvent", MetaSim::Event::_DEFAULT_PRIORITY - 100),
          _scheduler(scheduler),
          _scheduled_depletion_time(0),
          _energy_at_prediction(0.0) {
        // ⭐ 最高优先级（_DEFAULT_PRIORITY - 100 确保在其他事件之前处理）
    }

    void ALAPSyncEnergyDepletedEvent::doit() {
        if (!_scheduler) return;
        _scheduler->onEnergyDepleted();
    }

    // =====================================================
    // ALAPSyncScheduler 实现
    // =====================================================

    ALAPSyncScheduler::ALAPSyncScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _last_tick_time(0),
          _last_collection_time(0),
          _solar_data_file(""),
          _pv_efficiency(0.18),
          _pv_area_m2(1.0),
          _use_real_solar_data(false),
          _start_time_offset(0),
          _base_harvest_rate(0.054),  // ⭐ V93修复：默认值 54 mW
          _tick_event(nullptr),
          _first_tick_scheduled(false),
          _kernel(nullptr),
          _batch_scheduled_this_tick(false),
          _energy_depleted(false),
          _current_batch_size(0),
          _pending_dispatch_tasks(),
          _pending_dispatch_energy(0.0),
          _selection_tick(-1),
          _selection_generation(0),
          _selection_frozen(false),
          _energy_commit_tick(-1),
          _energy_commit_generation(0),
          _energy_commit_valid(false),
          _last_preempted_task(nullptr),
          _last_preempted_tick(0) {

        SCHEDULER_LOG_INFO("🚀 [ALAP-Sync] TIE Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ALAP-Sync] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ALAP-Sync] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ALAP-Sync] 开始时间偏移: ") +
                              std::to_string(static_cast<int64_t>(_start_time_offset)) + "ms");

            // 读取太阳能配置
            try {
                std::ifstream yaml_file(config_file);
                if (yaml_file.good()) {
                    std::string line;
                    bool in_energy_section = false;

                    while (std::getline(yaml_file, line)) {
                        std::string original_line = line;
                        line.erase(0, line.find_first_not_of(" \t"));
                        line.erase(line.find_last_not_of(" \t") + 1);

                        if (line.empty() || line[0] == '#') {
                            continue;
                        }

                        if (line.find("energy_management:") != std::string::npos) {
                            in_energy_section = true;
                            continue;
                        }

                        if (in_energy_section && !line.empty() && line[0] != '-' && line[0] != '#') {
                            size_t leading_spaces = original_line.find_first_not_of(" \t");
                            if (leading_spaces == 0 && line.find(':') != std::string::npos &&
                                line.find("energy_management:") == std::string::npos) {
                                break;
                            }
                        }

                        if (in_energy_section) {
                            if (line.find("use_real_solar_data:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t") + 1);
                                _use_real_solar_data = (value == "true");
                            }
                            else if (line.find("solar_data_file:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t\""));
                                value.erase(value.find_last_not_of(" \t\"") + 1);
                                _solar_data_file = value;
                            }
                            else if (line.find("pv_efficiency:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t") + 1);
                                _pv_efficiency = std::stod(value);
                            }
                            else if (line.find("pv_area_m2:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t") + 1);
                                _pv_area_m2 = std::stod(value);
                            }
                            // ⭐ V93修复：读取base_harvesting_rate配置
                            else if (line.find("base_harvesting_rate:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                size_t comment_pos = value.find('#');
                                if (comment_pos != std::string::npos) {
                                    value = value.substr(0, comment_pos);
                                }
                                value.erase(0, value.find_first_not_of(" \t"));
                                value.erase(value.find_last_not_of(" \t") + 1);
                                _base_harvest_rate = std::stod(value);
                                SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Sync] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Sync] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²" +
                                      " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Sync] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Sync] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ALAP-Sync] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ALAPSyncTickEvent(this);
        _alap_wake_event = new ALAPSyncWakeEvent(this);
        _energy_depleted_event = new ALAPSyncEnergyDepletedEvent(this);  // ⭐ Bug修复：能量耗尽预测事件
        SCHEDULER_LOG_INFO("✅ [ALAP-Sync] TIE Scheduler 初始化完成");
    }

    ALAPSyncScheduler::ALAPSyncScheduler(const std::vector<std::string> &params)
        : ALAPSyncScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ALAPSyncScheduler>
        ALAPSyncScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ALAPSyncScheduler>(params);
    }

    ALAPSyncScheduler::~ALAPSyncScheduler() {
        if (_tick_event) {
            delete _tick_event;
            _tick_event = nullptr;
        }
        if (_alap_wake_event) {
            _alap_wake_event->drop();
            delete _alap_wake_event;
            _alap_wake_event = nullptr;
        }
        // ⭐ Bug修复：清理能量耗尽事件
        if (_energy_depleted_event) {
            _energy_depleted_event->drop();
            delete _energy_depleted_event;
            _energy_depleted_event = nullptr;
        }

        // 清理任务模型
        for (auto &pair : _task_models) {
            delete pair.second;
        }
        _task_models.clear();
    }

    // =====================================================
    // ALAP-Sync批量调度辅助方法
    // =====================================================

    int ALAPSyncScheduler::calculateBatchSize() {
        int total_cpus = _kernel
            ? static_cast<int>(_kernel->getCurrentExecutingTasks().size())
            : ConfigManager::getInstance().getNumCores();
        int occupied_cpus = 0;
        if (_kernel) {
            for (const auto &[cpu, task] :
                 _kernel->getCurrentExecutingTasks()) {
                if ((task && task->isExecuting()) ||
                    _kernel->isCPUDispatching(cpu)) {
                    ++occupied_cpus;
                }
            }
        }
        const int idle_cpus = std::max(0, total_cpus - occupied_cpus);
        int eligible_jobs = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(idle_cpus, eligible_jobs);

        SCHEDULER_LOG_DEBUG(std::string("📊 [ALAP-Sync] calculateBatchSize: ") +
                           "空闲CPU=" + std::to_string(idle_cpus) +
                           " 候选任务=" + std::to_string(eligible_jobs) +
                           " K=" + std::to_string(batch_size));

        return batch_size;
    }


    void ALAPSyncScheduler::executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy) {
        // ⭐ ALAP-Sync核心：批量调度时一次性扣减k个任务的1ms能耗
        // 当前时刻能量 = 上一时刻结余 + 本次充电能量 - 已消耗能量 - 本次批量调度能耗
        double old_energy = _current_energy;
        _current_energy -= total_energy;
        // ⭐ V51修复：软性守卫 - 防止能量透支（不使用assert避免core dump）
        if (_current_energy < 0.0) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] 能量透支检测！强制归零: " +
                                 std::to_string(_current_energy * 1000) + " mJ → 0 mJ");
            _current_energy = 0.0; // ⭐ 能量守恒：消除浮点误差
        }
        _stats.total_energy_consumed += total_energy;

        SCHEDULER_LOG_INFO(std::string("📋 [ALAP-Sync] 批量调度: ") +
                           "任务数=" + std::to_string(tasks.size()) +
                           " 总能耗=" + std::to_string(total_energy * 1000) + " mJ" +
                           " 能量=" + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    // =====================================================
    // Tick dispatch state helpers
    // =====================================================

    void ALAPSyncScheduler::resetTickDispatchState() {
        _newly_dispatched_this_tick.clear();
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _energy_deducted_tasks.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_valid = false;
        _paid_pending_tasks.clear();
    }

    void ALAPSyncScheduler::clearTaskTickSelection(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_counted_tasks_in_dispatch.erase(task) > 0) {
            _dispatching_tasks_total_energy -= calculateUnitEnergyForTask(task);
            if (_dispatching_tasks_total_energy < 0.0) {
                _dispatching_tasks_total_energy = 0.0;
            }
        }
        _newly_dispatched_this_tick.erase(task);
        _energy_deducted_tasks.erase(task);

        auto order_it = std::remove(_dispatch_selection_order.begin(), _dispatch_selection_order.end(), task);
        if (order_it != _dispatch_selection_order.end()) {
            _dispatch_selection_order.erase(order_it, _dispatch_selection_order.end());
        }
    }

    void ALAPSyncScheduler::markTaskSelectedThisTick(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_counted_tasks_in_dispatch.insert(task).second) {
            _dispatch_selection_order.push_back(task);
            _newly_dispatched_this_tick.insert(task);
            _dispatching_tasks_total_energy += getConfiguredUnitEnergyForTask(task);
        }
    }

    void ALAPSyncScheduler::accountInitialEnergyForSelectedTasks(const std::string &log_prefix) {
        for (AbsRTTask *task : _dispatch_selection_order) {
            if (!task || _energy_deducted_tasks.find(task) != _energy_deducted_tasks.end()) {
                continue;
            }

            double unit_energy = getConfiguredUnitEnergyForTask(task);
            _current_energy -= unit_energy;
            if (_current_energy < 0.0) {
                _current_energy = 0.0;
            }
            _stats.total_energy_consumed += unit_energy;
            _energy_deducted_tasks.insert(task);

            SCHEDULER_LOG_INFO(log_prefix + getTaskName(task) +
                               " 1ms=" + std::to_string(unit_energy * 1000.0) + " mJ" +
                               " 剩余=" + std::to_string(_current_energy * 1000.0) + " mJ");
        }
    }

    void ALAPSyncScheduler::rejectDispatchedTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_energy_deducted_tasks.find(task) != _energy_deducted_tasks.end()) {
            double unit_energy = getConfiguredUnitEnergyForTask(task);
            _current_energy += unit_energy;
            if (_current_energy > _max_energy) {
                _current_energy = _max_energy;
            }
            _stats.total_energy_consumed -= unit_energy;
            if (_stats.total_energy_consumed < 0.0) {
                _stats.total_energy_consumed = 0.0;
            }
            _energy_deducted_tasks.erase(task);

            SCHEDULER_LOG_WARNING(std::string("↩️ [ALAP-Sync] 撤销失败派发的预扣能量: ") +
                                  getTaskName(task) +
                                  " 退款=" + std::to_string(unit_energy * 1000.0) + " mJ" +
                                  " 当前=" + std::to_string(_current_energy * 1000.0) + " mJ");
        }

        SCHEDULER_LOG_WARNING(std::string("↩️ [ALAP-Sync] 撤销本tick未完成准入的任务选择: ") +
                              getTaskName(task));
        clearTaskTickSelection(task);
    }

    void ALAPSyncScheduler::clearPersistentTaskState(AbsRTTask *task) {
        if (!task) {
            return;
        }

        clearTaskTickSelection(task);

        auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
        if (batch_it != _current_batch_tasks.end()) {
            _current_batch_tasks.erase(batch_it);
            _current_batch_size = static_cast<int>(_current_batch_tasks.size());
            if (_current_batch_tasks.empty()) {
                _batch_scheduled_this_tick = false;
            }
        }

        _tasks_completed_wcet.erase(task);
        _pending_dispatch_tasks.erase(
            std::remove(_pending_dispatch_tasks.begin(), _pending_dispatch_tasks.end(), task),
            _pending_dispatch_tasks.end());
        _energy_accounts.erase(task);
        _suspend_reasons.erase(task);
    }

    std::vector<AbsRTTask *> ALAPSyncScheduler::collectActiveJobs(Tick current_time) {
        std::vector<AbsRTTask *> active_tasks;
        auto add_active = [&active_tasks, current_time](AbsRTTask *task, bool running) {
            if (!task || task->getArrival() > current_time) {
                return;
            }
            if (!running && !task->isActive()) {
                return;
            }
            if (task->getRemainingWCET() <= 0.0) {
                return;
            }
            if (std::find(active_tasks.begin(), active_tasks.end(), task) == active_tasks.end()) {
                active_tasks.push_back(task);
            }
        };

        if (_kernel) {
            for (const auto &[cpu, task] : _kernel->getCurrentExecutingTasks()) {
                (void)cpu;
                add_active(task, task && task->isExecuting());
            }
        }
        for (AbsRTTask *task : _ready_queue) {
            add_active(task, false);
        }
        return active_tasks;
    }

    std::vector<AbsRTTask *> ALAPSyncScheduler::collectALAPCandidates(
        const std::vector<AbsRTTask *> &active_tasks,
        Tick current_time) {
        std::vector<AbsRTTask *> candidates;
        for (AbsRTTask *task : active_tasks) {
            if (calculateSlackForTask(task, current_time) <= Tick(0)) {
                candidates.push_back(task);
            }
        }
        return candidates;
    }

    bool ALAPSyncScheduler::hasHigherRMPriority(AbsRTTask *lhs, AbsRTTask *rhs) {
        if (lhs == rhs) {
            return false;
        }
        ALAPSyncTaskModel *lhs_model = getTaskModel(lhs);
        ALAPSyncTaskModel *rhs_model = getTaskModel(rhs);
        if (lhs_model && rhs_model &&
            lhs_model->getRMPriority() != rhs_model->getRMPriority()) {
            return lhs_model->getRMPriority() < rhs_model->getRMPriority();
        }
        if (lhs && rhs && lhs->getPeriod() != rhs->getPeriod()) {
            return lhs->getPeriod() < rhs->getPeriod();
        }
        if (lhs && rhs) {
            return lhs->getTaskNumber() < rhs->getTaskNumber();
        }
        return lhs != nullptr;
    }

    void ALAPSyncScheduler::sortByRMPriority(std::vector<AbsRTTask *> &tasks) {
        std::stable_sort(tasks.begin(), tasks.end(),
                         [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                             return hasHigherRMPriority(lhs, rhs);
                         });
    }

    double ALAPSyncScheduler::getConfiguredUnitEnergyForTask(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end() || !it->second) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    void ALAPSyncScheduler::commitTickEnergy(Tick tick, double energy) {
        if (_energy_commit_valid &&
            _energy_commit_tick == tick &&
            _energy_commit_generation == _selection_generation) {
            throw std::logic_error("ALAP-Sync energy committed more than once in one tick");
        }
        if (energy < 0.0 || _current_energy + 1e-9 < energy) {
            throw std::logic_error("ALAP-Sync attempted to commit unaffordable batch energy");
        }

        _current_energy = std::max(0.0, _current_energy - energy);
        _stats.total_energy_consumed += energy;
        _energy_commit_tick = tick;
        _energy_commit_generation = _selection_generation;
        _energy_commit_valid = true;
    }

    void ALAPSyncScheduler::cancelStaleDispatches(
        const std::vector<AbsRTTask *> &previous_selection) {
        if (!_kernel) {
            return;
        }
        bool has_stale_dispatch = false;
        for (AbsRTTask *task : previous_selection) {
            if (!task || _kernel->getProcessor(task) != nullptr) {
                continue;
            }
            if (std::find(_dispatch_selection_order.begin(),
                          _dispatch_selection_order.end(),
                          task) == _dispatch_selection_order.end()) {
                has_stale_dispatch = true;
                break;
            }
        }
        if (!has_stale_dispatch) {
            return;
        }

        for (const auto &[cpu, running] : _kernel->getCurrentExecutingTasks()) {
            if (!running && _kernel->isCPUDispatching(cpu)) {
                _kernel->dispatch(cpu);
            }
        }
    }

    void ALAPSyncScheduler::rollbackFailedRunningTasks(const std::vector<AbsRTTask *> &running_task_list) {
        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] 回滚缺电续跑任务失败：_kernel为nullptr");
            return;
        }

        std::vector<AbsRTTask *> rollback_tasks;
        rollback_tasks.reserve(running_task_list.size());
        for (AbsRTTask *task : running_task_list) {
            if (!task || !task->isActive() || !task->isExecuting()) {
                continue;
            }
            rollback_tasks.push_back(task);
        }

        if (rollback_tasks.empty()) {
            return;
        }

        SCHEDULER_LOG_WARNING(std::string("🔄 [ALAP-Sync] 组级验资失败，回滚续跑任务到ready_queue: ") +
                              std::to_string(rollback_tasks.size()) + " 个");

        for (AbsRTTask *task : rollback_tasks) {
            setSuspendReason(task, "insufficient_energy");
            SCHEDULER_LOG_WARNING(std::string("   ↩️ [ALAP-Sync] 回滚任务: ") + getTaskName(task));
            _kernel->suspend(task);
        }
    }

    // =====================================================
    // 核心调度逻辑 - ALAP-Sync批量调度算法
    // =====================================================

    void ALAPSyncScheduler::performTickScheduling() {
        Tick current_time = SIMUL.getTime();
        if (_selection_frozen && _selection_tick == current_time) {
            SCHEDULER_LOG_DEBUG(
                std::string("🛡️ [ALAP-Sync] 本tick选择已冻结，跳过重复决策 @ ") +
                std::to_string(static_cast<int64_t>(current_time)) + "ms");
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("🔄 [ALAP-Sync] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");
        _stats.total_tick_count++;

        Tick elapsed = current_time - _last_tick_time;
        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Sync] Tick边界收集能量: ") +
                                   std::to_string(harvested) + "J" +
                                   " 当前能量: " + std::to_string(_current_energy) + "J" +
                                   " 经过时间: " + std::to_string(static_cast<int64_t>(elapsed)) + "ms");
            }
        }
        _last_tick_time = current_time;
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] _kernel为nullptr，跳过批量调度");
            return;
        }

        const auto &running_tasks_map = _kernel->getCurrentExecutingTasks();
        std::set<AbsRTTask *> running_tasks;
        for (const auto &[cpu, task] : running_tasks_map) {
            (void)cpu;
            if (task && task->isExecuting()) {
                running_tasks.insert(task);
            }
        }

        std::vector<AbsRTTask *> previous_selection = _dispatch_selection_order;
        std::vector<AbsRTTask *> active_tasks = collectActiveJobs(current_time);
        std::vector<AbsRTTask *> urgent_candidates = collectALAPCandidates(active_tasks, current_time);
        sortByRMPriority(urgent_candidates);

        resetTickDispatchState();
        _current_batch_tasks.clear();
        _current_batch_size = 0;
        _batch_scheduled_this_tick = false;
        _preempt_batch_tasks.clear();
        _pending_dispatch_tasks.clear();
        _pending_dispatch_energy = 0.0;

        std::vector<AbsRTTask *> desired_tasks;
        const int total_cpus = static_cast<int>(running_tasks_map.size());
        for (AbsRTTask *task : urgent_candidates) {
            if (static_cast<int>(desired_tasks.size()) >= total_cpus) {
                break;
            }
            desired_tasks.push_back(task);
        }

        std::vector<AbsRTTask *> continuation_tasks;
        std::vector<AbsRTTask *> idle_core_batch;
        double continuation_energy = 0.0;
        double idle_core_batch_energy = 0.0;
        for (AbsRTTask *task : desired_tasks) {
            if (running_tasks.find(task) != running_tasks.end()) {
                continuation_tasks.push_back(task);
                continuation_energy += getConfiguredUnitEnergyForTask(task);
            } else {
                idle_core_batch.push_back(task);
                idle_core_batch_energy += getConfiguredUnitEnergyForTask(task);
            }
        }

        std::vector<AbsRTTask *> selected_tasks = continuation_tasks;
        double required_group_energy = continuation_energy;
        const double epsilon = 1e-9;
        const bool continuation_affordable =
            _current_energy + epsilon >= continuation_energy;
        const bool idle_core_batch_affordable =
            continuation_affordable &&
            _current_energy + epsilon >=
                continuation_energy + idle_core_batch_energy;
        if (!continuation_affordable) {
            _energy_depleted = true;
            _stats.total_batch_skipped++;
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] urgent continuation 整组验资失败: K=") +
                                  std::to_string(continuation_tasks.size()) +
                                  " 组总需能=" + std::to_string(required_group_energy * 1000.0) + " mJ" +
                                  " 当前=" + std::to_string(_current_energy * 1000.0) + " mJ");
            selected_tasks.clear();
            required_group_energy = 0.0;
        } else if (!idle_core_batch.empty() &&
                   !idle_core_batch_affordable) {
            _energy_depleted = true;
            _stats.total_batch_skipped++;
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] 空闲核 urgent batch 验资失败，continuation保持运行: K=") +
                                  std::to_string(idle_core_batch.size()) +
                                  " 批次需能=" + std::to_string(idle_core_batch_energy * 1000.0) + " mJ" +
                                  " continuation需能=" + std::to_string(continuation_energy * 1000.0) + " mJ" +
                                  " 当前=" + std::to_string(_current_energy * 1000.0) + " mJ");
        } else {
            _energy_depleted = false;
            selected_tasks = desired_tasks;
            required_group_energy += idle_core_batch_energy;
        }

        _selection_tick = current_time;
        _selection_generation++;
        _selection_frozen = true;
        _current_batch_tasks = selected_tasks;
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());
        _batch_scheduled_this_tick = !selected_tasks.empty();

        if (_trace_logger && _semantic_trace_enabled &&
            !active_tasks.empty()) {
            std::vector<AbsRTTask *> trace_active = active_tasks;
            sortByRMPriority(trace_active);
            const bool sync_energy_blocked =
                !continuation_affordable ||
                (!idle_core_batch.empty() && !idle_core_batch_affordable);
            _trace_logger->logB3ALAPDecision(
                "ALAP-Sync",
                "SYNC",
                _current_energy * 1000.0,
                static_cast<std::size_t>(total_cpus),
                makeB3TraceJobs(trace_active, _task_models),
                makeB3TraceJobs(urgent_candidates, _task_models),
                makeB3TraceJobs(selected_tasks, _task_models),
                makeB3TraceJobs(continuation_tasks, _task_models),
                sync_energy_blocked ? "ALAP_SYNC_ATOMIC_ENERGY_WAIT"
                                    : "ALAP_SYNC_NATIVE_GATE");
        }

        if (!selected_tasks.empty()) {
            _stats.total_batch_schedules++;
            for (AbsRTTask *task : selected_tasks) {
                markTaskSelectedThisTick(task);
                _paid_pending_tasks.insert(task);
            }
            if (std::abs(_dispatching_tasks_total_energy -
                         required_group_energy) > epsilon) {
                throw std::logic_error(
                    "ALAP-Sync selected energy accounting mismatch");
            }
            commitTickEnergy(current_time, required_group_energy);
        }

        cancelStaleDispatches(previous_selection);

        const std::set<AbsRTTask *> selected_set(selected_tasks.begin(), selected_tasks.end());
        for (AbsRTTask *task : running_tasks) {
            if (selected_set.find(task) != selected_set.end()) {
                continue;
            }
            setSuspendReason(task, _energy_depleted ? "insufficient_energy" : "preemption");
            _kernel->suspend(task);
        }

        if (!selected_tasks.empty()) {
            _kernel->dispatch();
        }

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] Tick选择: active=") +
                           std::to_string(active_tasks.size()) +
                           " candidates=" + std::to_string(urgent_candidates.size()) +
                           " selected=" + std::to_string(selected_tasks.size()) +
                           " 扣减=" + std::to_string(required_group_energy * 1000.0) + " mJ" +
                           " 剩余=" + std::to_string(_current_energy * 1000.0) + " mJ");
    }

    void ALAPSyncScheduler::schedule() {
        // ALAP-Sync依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-Sync] schedule() 被调用");
    }

    // =====================================================
    // getFirst - ALAP-Sync废弃，返回nullptr
    // =====================================================

    AbsRTTask *ALAPSyncScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Sync] getFirst() 被调用（ALAP-Sync已废弃）"));
        // ALAP-Sync使用批量调度，不使用getFirst
        return nullptr;
    }

    // =====================================================
    // getTaskN - 返回批量中的第n个任务
    // =====================================================

    // =====================================================
    // getTaskN - 返回批量中的第n个任务（坚决服从 V66 批次）
    // =====================================================
    AbsRTTask *ALAPSyncScheduler::getTaskN(unsigned int n) {
        if (!_selection_frozen ||
            _selection_tick != SIMUL.getTime() ||
            !_batch_scheduled_this_tick) {
            return nullptr;
        }
        if (n >= _dispatch_selection_order.size()) {
            return nullptr;
        }

        AbsRTTask *task = _dispatch_selection_order[n];
        if (!task || !task->isActive()) {
            return nullptr;
        }
        return task;
    }

    // =====================================================
    // commitDispatch - 确认派发
    // =====================================================
    void ALAPSyncScheduler::commitDispatch() {
        // ALAP-Sync now commits the complete batch atomically when the tick
        // selection is frozen. EndDispatch only confirms already-paid tasks;
        // it must not charge energy a second time.
        _pending_dispatch_tasks.clear();
        _pending_dispatch_energy = 0.0;
    }


    // =====================================================
    // notify - arrival 仅入队，由同步组准入逻辑决定是否上机
    // =====================================================

    void ALAPSyncScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_kernel && _kernel->getProcessor(task) != nullptr) {
            SCHEDULER_LOG_DEBUG(std::string("⏭️ [ALAP-Sync] notify: 跳过运行中任务: ") + getTaskName(task));
            return;
        }

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例到达）
        // 周期性任务复用同一个AbsRTTask对象，但每个实例都是独立的
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] notify: 检查WCET完成标志: ") +
                           getTaskName(task) + " 集合大小=" + std::to_string(_tasks_completed_wcet.size()));
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Sync] notify: 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        // 缺电实例也必须先进入系统生命周期，由同步组准入逻辑决定是否上机。
        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-Sync] 任务到达并添加到就绪队列（不再做arrival能量门槛）: ") +
                          getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ALAPSyncScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-Sync] 添加任务: ") + getTaskName(task));
        SCHEDULER_LOG_DEBUG(std::string("   参数: ") + params);

        // 解析参数
        int period = 100;
        int wcet = 20;
        MetaSim::Tick arrival_offset = 0;
        std::string workload = "bzip2";
        double energy_coeff = 1.0;

        size_t period_pos = params.find("period=");
        if (period_pos != std::string::npos) {
            size_t comma_pos = params.find(",", period_pos);
            std::string period_str = params.substr(period_pos + 7,
                comma_pos != std::string::npos ? comma_pos - period_pos - 7 : std::string::npos);
            period = std::stoi(period_str);
        }

        size_t wcet_pos = params.find("wcet=");
        if (wcet_pos != std::string::npos) {
            size_t comma_pos = params.find(",", wcet_pos);
            std::string wcet_str = params.substr(wcet_pos + 5,
                comma_pos != std::string::npos ? comma_pos - wcet_pos - 5 : std::string::npos);
            wcet = std::stoi(wcet_str);
        }

        size_t offset_pos = params.find("arrival_offset=");
        if (offset_pos != std::string::npos) {
            size_t comma_pos = params.find(",", offset_pos);
            std::string offset_str = params.substr(offset_pos + 15,
                comma_pos != std::string::npos ? comma_pos - offset_pos - 15 : std::string::npos);
            arrival_offset = MetaSim::Tick(static_cast<MetaSim::Tick::impl_t>(std::stoll(offset_str)));
        }

        size_t workload_pos = params.find("workload=");
        if (workload_pos != std::string::npos) {
            size_t comma_pos = params.find(",", workload_pos);
            workload = params.substr(workload_pos + 9,
                comma_pos != std::string::npos ? comma_pos - workload_pos - 9 : std::string::npos);
            // 移除可能的尾部引号
            if (!workload.empty() && workload.back() == '"') {
                workload.pop_back();
            }
        }

        // ⭐ 启用killOnMiss：当任务超过截止期时，框架自动终止旧实例并启动新实例
        Task *concrete_task = dynamic_cast<Task *>(task);
        if (concrete_task) {
            concrete_task->killOnMiss(true);
        }

        // 创建任务模型
        ALAPSyncTaskModel *model = new ALAPSyncTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ALAPSyncScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-Sync] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);

        for (auto &map_pair : _running_tasks) {
            if (map_pair.second == task) {
                _running_tasks[map_pair.first] = nullptr;
            }
        }

        clearPersistentTaskState(task);

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ALAPSyncScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ALAP-Sync] 任务到达: ") + getTaskName(task));

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例重新开始）
        // 周期性任务复用同一个AbsRTTask对象，但每个实例都是独立的
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Sync] 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);

            // ⭐ 注意：mid-tick抢占已在insert()中通过Micro-Batch机制实现
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void ALAPSyncScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-Sync] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void ALAPSyncScheduler::checkAndPreemptOnAllCPUs() {
        // Sync 修复：禁止 tick 边界 / mid-tick 额外抢占路径篡改同步组选择。
        // 本轮仅允许 performTickScheduling() 形成稳定批次，然后由 getTaskN() 按快照发放。
        _preempt_batch_tasks.clear();
        SCHEDULER_LOG_DEBUG("⏸️ [ALAP-Sync] checkAndPreemptOnAllCPUs 已停用，保持稳定同步批次");
    }

    bool ALAPSyncScheduler::shouldPreempt(AbsRTTask *running_task, AbsRTTask *new_task) {
        if (!running_task || !new_task) {
            SCHEDULER_LOG_INFO(std::string("❌ [ALAP-Sync] shouldPreempt: running_task或new_task为空"));
            return false;
        }

        ALAPSyncTaskModel *running_model = getTaskModel(running_task);
        ALAPSyncTaskModel *new_model = getTaskModel(new_task);

        if (!running_model || !new_model) {
            SCHEDULER_LOG_INFO(std::string("❌ [ALAP-Sync] shouldPreempt: 获取task model失败"));
            return false;
        }

        // 检查新任务的能量是否足够
        double unit_energy = calculateUnitEnergyForTask(new_task);
        if (_current_energy < unit_energy) {
            SCHEDULER_LOG_INFO(std::string("❌ [ALAP-Sync] shouldPreempt: 能量不足 _current_energy=") +
                              std::to_string(_current_energy * 1000) + " < unit_energy=" +
                              std::to_string(unit_energy * 1000) + " mJ");
            return false;  // 能量不足，不抢占
        }

        // 新任务优先级更高（RM优先级数值越小越高）
        int running_prio = running_model->getRMPriority();
        int new_prio = new_model->getRMPriority();
        bool should = new_prio < running_prio;

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] shouldPreempt: ") +
                          getTaskName(running_task) + "(prio=" + std::to_string(running_prio) + ") vs " +
                          getTaskName(new_task) + "(prio=" + std::to_string(new_prio) + ") = " +
                          (should ? "true" : "false"));

        return should;
    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void ALAPSyncScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_WARNING(std::string("➕ [ALAP-Sync] insert: ") + getTaskName(task) +
                          " @ " + std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);

        // 严格同步语义：新到达任务只进入就绪队列，等待下一个 tick 统一成组决策。
        _preempt_batch_tasks.clear();
    }

    void ALAPSyncScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ALAP-Sync] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearPersistentTaskState(task);
    }

    void ALAPSyncScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Sync] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ALAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ALAPSyncTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ALAP-Sync] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void ALAPSyncScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ALAP-Sync] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ALAPSyncScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ALAP-Sync] 任务加入等待队列: ") + getTaskName(task));
    }

    void ALAPSyncScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ALAPSyncScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ALAPSyncScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *ALAPSyncScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }

        // ⭐ V56修复：返回Slack<=0的最高优先级任务
        // 遍历就绪队列，找第一个Slack<=0的任务
        for (AbsRTTask *task : _ready_queue) {
            if (!task || !task->isActive()) continue;

            // 检查是否已在运行
            if (_kernel) {
                CPU *proc = _kernel->getProcessor(task);
                if (proc != nullptr) continue;  // 已在运行
            }

            // 检查Slack
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack <= 0) {
                return task;  // 返回第一个Slack<=0的任务
            }
        }

        return nullptr;  // 没有Slack<=0的任务
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ALAPSyncScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        const double unit_energy = getConfiguredUnitEnergyForTask(task);
        if (!task) {
            return 0.0;
        }

        if (_selection_frozen && _selection_tick == SIMUL.getTime()) {
            const bool selected =
                std::find(_dispatch_selection_order.begin(),
                          _dispatch_selection_order.end(),
                          task) != _dispatch_selection_order.end();
            if (!selected) {
                // A stale EndDispatch from an older generation must not pass
                // the MRTKernel energy pre-check and enter execution.
                return _current_energy + unit_energy + 1.0;
            }
            if (_paid_pending_tasks.find(task) != _paid_pending_tasks.end()) {
                return 0.0;
            }
        }

        return unit_energy;
    }

    // ⭐ EnergyInfoProvider接口实现
    double ALAPSyncScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ALAPSyncScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void ALAPSyncScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string ALAPSyncScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void ALAPSyncScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    double ALAPSyncScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ALAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] calculateTotalEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 计算完整WCET的能耗
        Tick wcet = model->getWCET();
        std::string workload = model->getWorkloadType();

        // 从ConfigManager获取base_freq
        ConfigManager &configMgr = ConfigManager::getInstance();
        double base_frequency = configMgr.getBaseFrequency();  // MHz
        double power = calculatePowerForWorkload(workload, base_frequency);

        // 能量 = 功率(W) × 时间(s)
        double wcet_seconds = static_cast<double>(wcet) * 0.001;
        double energy = power * wcet_seconds;

        // 应用��量系数
        energy *= model->getEnergyCoefficient();

        return energy;
    }

    double ALAPSyncScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-Sync] 功率计算: ") +
                           "workload=" + workload +
                           " coeff=" + std::to_string(power_coeff) +
                           " freq=" + std::to_string(frequency_mhz) + "MHz" +
                           " freq_ratio=" + std::to_string(freq_ratio) +
                           " base_power=" + std::to_string(base_power) +
                           " → " + std::to_string(power) + "W");

        return power;
    }

    // =====================================================
    // 运行时能量检查方法（V28.15新增）
    // =====================================================

    void ALAPSyncScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        (void)task;
        (void)cpu;
        SCHEDULER_LOG_DEBUG("ℹ️ [ALAP-Sync] startEnergyCheckForTask 已停用");
    }

    void ALAPSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        _energy_check_events.erase(task);
        SCHEDULER_LOG_DEBUG(std::string("ℹ️ [ALAP-Sync] stopEnergyCheckForTask 清理占位记录: ") + getTaskName(task));
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ALAPSyncScheduler::collectSolarEnergy(Tick current_time) {
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 计算自上次收集以来的时间
        Tick elapsed = current_time - _last_collection_time;

        if (elapsed <= 0) {
            return 0.0;
        }

        double energy = 0.0;

        if (_use_real_solar_data) {
            // ⭐ 使用真实NASA太阳能数据
            double irradiance = getSolarIrradiance(current_ms);  // W/m²
            double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
            energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;
        } else {
            // ⭐ V95修复：线性函数模型也应该使用面积和效率
            // 与真实太阳能模式使用相同的公式：energy = irradiance × area × efficiency × time
            int64_t actual_time_ms = current_ms + static_cast<int64_t>(_start_time_offset);
            int64_t ms_of_day = actual_time_ms % 86400000;
            double hour_of_day = static_cast<double>(ms_of_day) / 3600000.0;  // 0.0-24.0

            // 计算时间因子（线性函数）
            double time_factor = 0.0;
            if (hour_of_day < 6.0) {
                // 夜晚 (0:00-6:00)
                time_factor = 0.0;
            } else if (hour_of_day < 11.0) {
                // 日出阶段 (6:00-11:00): 线性增加
                time_factor = (hour_of_day - 6.0) / 5.0;  // 0.0-1.0
            } else if (hour_of_day < 13.0) {
                // 白天峰值 (11:00-13:00): 保持峰值
                time_factor = 1.0;
            } else if (hour_of_day < 18.0) {
                // 日落阶段 (13:00-18:00): 线性降低
                time_factor = (18.0 - hour_of_day) / 5.0;  // 1.0-0.0
            } else {
                // 夜晚 (18:00-24:00)
                time_factor = 0.0;
            }

            // 计算峰值辐照度 (W/m²)
            // base_harvest_rate (W) = irradiance (W/m²) × area (m²) × efficiency
            // 所以 peak_irradiance = base_harvest_rate / (area × efficiency)
            const double PEAK_IRRADIANCE = _base_harvest_rate / (_pv_area_m2 * _pv_efficiency);
            double irradiance = PEAK_IRRADIANCE * time_factor;  // W/m²

            // 使用与真实太阳能模式相同的公式
            double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
            energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;
        }

        // 更新最后收集时间
        _last_collection_time = current_time;

        return energy;
    }

    double ALAPSyncScheduler::getSolarIrradiance(int64_t time_ms) {
        if (!_use_real_solar_data) {
            // ⭐ 分段函数模型：模拟真实太阳能曲线
            int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);

            // 转换为小时（用于分段判断）
            int64_t ms_of_day = actual_time_ms % 86400000;
            double hour_of_day = static_cast<double>(ms_of_day) / 3600000.0;  // 0.0-24.0

            // 分段函数定义（更真实的太阳能曲线）
            // ⭐ V94修复：使用base_harvest_rate计算等效辐照度，而不是硬编码
            // base_harvest_rate (J/ms) = irradiance (W/m²) * area (m²) * efficiency * 0.001 (s/ms)
            // 所以 irradiance = base_harvest_rate / (area * efficiency * 0.001)
            const double PEAK_IRRADIANCE = _base_harvest_rate / (_pv_area_m2 * _pv_efficiency * 0.001);

            if (hour_of_day < 6.0) {
                // 夜晚 (0:00-6:00)
                return 0.0;
            } else if (hour_of_day < 11.0) {
                // 日出阶段 (6:00-11:00): 线性增加，5小时
                double progress = (hour_of_day - 6.0) / 5.0;  // 0.0-1.0
                return PEAK_IRRADIANCE * progress;
            } else if (hour_of_day < 13.0) {
                // 白天峰值 (11:00-13:00): 保持峰值，2小时
                return PEAK_IRRADIANCE;
            } else if (hour_of_day < 18.0) {
                // 日落阶段 (13:00-18:00): 线性降低，5小时
                double progress = (18.0 - hour_of_day) / 5.0;  // 1.0-0.0
                return PEAK_IRRADIANCE * progress;
            } else {
                // 夜晚 (18:00-24:00)
                return 0.0;
            }
        }

        // 使用真实NASA太阳能数据
        int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
        // ⭐ Bug修复：计算从数据开始的总分钟数，而不是当天的分钟数
        // 数据文件按分钟索引，包含多天的数据（370天 × 1440分钟/天 = 532800分钟）
        int64_t total_minutes = actual_time_ms / 60000;  // 从数据开始的总分钟数

        int line_number = total_minutes + 2;  // +2跳过标题行

        std::ifstream file(_solar_data_file);
        if (!file.is_open()) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] 无法打开太阳能数据文件: ") + _solar_data_file);
            return 0.0;
        }

        std::string line;
        int current_line = 1;
        while (current_line < line_number && std::getline(file, line)) {
            current_line++;
        }

        if (std::getline(file, line)) {
            try {
                double irradiance = std::stod(line);
                return irradiance;
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ALAPSyncScheduler::scheduleNextTick() {
        if (!_tick_event) {
            return;
        }

        Tick current_time = SIMUL.getTime();

        // ⭐ 修复：第一个tick在当前时间触发（0ms），后续tick每1ms触发一次
        if (!_first_tick_scheduled) {
            _tick_event->post(current_time);  // 第一个tick立即触发
            _first_tick_scheduled = true;
        } else {
            _tick_event->post(current_time + Tick(1));  // 后续tick每1ms触发一次
        }
    }

    // =====================================================
    // 任务管理方法
    // =====================================================

    ALAPSyncTaskModel *ALAPSyncScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ALAPSyncScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *ALAPSyncScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int ALAPSyncScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *ALAPSyncScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void ALAPSyncScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-Sync] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;

        // ⭐ 启动能量检查事件（每 1ms 扣除能量）
        startEnergyCheckForTask(task, cpu);
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ALAPSyncScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ALAP-Sync] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ALAPSyncScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ALAPSyncScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *ALAPSyncScheduler::getKernel() {
        if (!_kernel && !_ready_queue.empty()) {
            AbsRTTask *task = _ready_queue.front();
            if (task) {
                _kernel = dynamic_cast<MRTKernel *>(task->getKernel());
            }
        }
        return _kernel;
    }

    // =====================================================
    // 生命周期方法
    // =====================================================

    void ALAPSyncScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-Sync] newRun - 仿真开始");

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();

        _ready_queue.clear();
        _waiting_queue.clear();
        _energy_accounts.clear();
        _running_tasks.clear();

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;
        _stats.total_batch_schedules = 0;
        _stats.total_batch_skipped = 0;

        // ALAP-Sync批量调度状态初始化
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _current_batch_tasks.clear();
        _preempt_batch_tasks.clear();
        _pending_dispatch_tasks.clear();
        _pending_dispatch_energy = 0.0;
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _newly_dispatched_this_tick.clear();
        _energy_deducted_tasks.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_generation = 0;
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_generation = 0;
        _energy_commit_valid = false;
        _paid_pending_tasks.clear();

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Sync] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ALAPSyncScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-Sync] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ALAP-Sync] ===== ALAP-Sync批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ALAPSyncScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 任务结束: ") + getTaskName(task));

        // ⭐ 停止能量检查事件
        stopEnergyCheckForTask(task);

        // 从就绪/等待/同步组/稳定选择视图中移除任务，避免旧实例状态串味
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearPersistentTaskState(task);

        // 从运行任务映射中移除
        for (auto &pair : _running_tasks) {
            if (pair.second == task) {
                pair.second = nullptr;
                break;
            }
        }

        // 打印能量消耗统计
        auto it = _energy_accounts.find(task);
        if (it != _energy_accounts.end()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ Bug修复：清除批次标记，防止 dispatch() 中调用 getTaskN 时崩溃
        // 当任务结束时，旧的批次已经失效，需要清除标记
        _batch_scheduled_this_tick = false;

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            SCHEDULER_LOG_INFO("🔄 [ALAP-Sync] 任务结束，触发立即调度");
            _kernel->dispatch();
        }
    }

    bool ALAPSyncScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 过期任务清理 - 清理超过截止期的旧任务实例
    // =====================================================

    void ALAPSyncScheduler::cleanupExpiredTasks() {
        Tick current_time = SIMUL.getTime();

        if (!_kernel) {
            _kernel = getKernel();
        }

        // 1. 检查运行中的任务，挂起已过期的
        if (_kernel) {
            const auto& running = _kernel->getCurrentExecutingTasks();
            std::vector<AbsRTTask *> to_suspend;

            for (const auto& [cpu, task] : running) {
                if (!task || !task->isExecuting()) continue;
                ALAPSyncTaskModel *model = getTaskModel(task);
                if (!model) continue;

                Tick arrival = task->getArrival();
                Tick deadline = task->getDeadline();

                if (deadline <= current_time) {
                    to_suspend.push_back(task);
                    SCHEDULER_LOG_INFO("💀 [ALAP-Sync] 过期任务运行中，将挂起: " +
                        getTaskName(task) +
                        " arrival=" + std::to_string(static_cast<int64_t>(arrival)) +
                        " deadline=" + std::to_string(static_cast<int64_t>(deadline)) +
                        " current=" + std::to_string(static_cast<int64_t>(current_time)));
                }
            }

            for (AbsRTTask *task : to_suspend) {
                _kernel->suspend(task);
            }
        }

        // 2. 清理就绪队列中已过期的任务实例
        std::vector<AbsRTTask *> expired;
        for (AbsRTTask *task : _ready_queue) {
            if (!task) continue;
            ALAPSyncTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick arrival = task->getArrival();
            Tick deadline = task->getDeadline();

            if (deadline <= current_time) {
                expired.push_back(task);
                SCHEDULER_LOG_INFO("🧹 [ALAP-Sync] 清理过期任务: " +
                    getTaskName(task) +
                    " arrival=" + std::to_string(static_cast<int64_t>(arrival)) +
                    " deadline=" + std::to_string(static_cast<int64_t>(deadline)) +
                    " current=" + std::to_string(static_cast<int64_t>(current_time)));
                _stats.total_deadline_misses++;
            }
        }

        for (AbsRTTask *task : expired) {
            removeFromReadyQueue(task);
        }

        // 3. 清理批量任务中已过期的
        std::vector<AbsRTTask *> expired_batch;
        for (AbsRTTask *task : _current_batch_tasks) {
            if (!task) continue;
            ALAPSyncTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick arrival = task->getArrival();
            Tick deadline = task->getDeadline();

            if (deadline <= current_time) {
                expired_batch.push_back(task);
            }
        }

        for (AbsRTTask *task : expired_batch) {
            auto it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
            if (it != _current_batch_tasks.end()) {
                _current_batch_tasks.erase(it);
                _current_batch_size = static_cast<int>(_current_batch_tasks.size());
                SCHEDULER_LOG_INFO("🧹 [ALAP-Sync] 从批量任务清理过期任务: " + getTaskName(task));
            }
        }
    }

    // =====================================================
    // ALAP时序门控（阶段一）
    // =====================================================

    // ⭐ 新增：基于批次的 ALAP 时序门控（原论文正确实现）
    bool ALAPSyncScheduler::checkALAPBatchTimingGate(const std::vector<AbsRTTask *> &batch) {
        if (batch.empty()) {
            return true;  // 空批次，通过门控
        }

        Tick current_time = SIMUL.getTime();
        Tick min_slack = Tick(-1);

        // ⭐ 关键修复：只计算批次内任务的 Slack，找最小值（S_batch）
        for (AbsRTTask *task : batch) {
            if (!task) continue;

            Tick slack = calculateSlackForTask(task);

            if (min_slack < 0 || slack < min_slack) {
                min_slack = slack;
            }
        }

        // 门控逻辑
        if (min_slack > 0) {
            SCHEDULER_LOG_INFO("⏸️  [ALAP-Sync] ALAP批次时序门控：S_batch > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，批次集体休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 批次集体休眠
        } else {
            SCHEDULER_LOG_INFO("✅ [ALAP-Sync] ALAP批次时序门控：S_batch ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，批次唤醒，允许调度");
            return true;  // 批次唤醒
        }
    }

    // =====================================================
    // ALAP 全局时序门控（阶段一）
    // =====================================================
    bool ALAPSyncScheduler::checkALAPTimingGate() {
        Tick current_time = SIMUL.getTime();
        Tick min_slack = 0;
        bool first_task = true;

        std::vector<AbsRTTask *> all_tasks;
        for (AbsRTTask *task : _ready_queue) { 
            if (task) all_tasks.push_back(task); 
        }
        
        if (!_kernel) _kernel = getKernel();
        if (_kernel) {
            const auto& running_tasks = _kernel->getCurrentExecutingTasks();
            for (const auto& map_pair : running_tasks) {
                if (map_pair.second && map_pair.second->isExecuting()) {
                    all_tasks.push_back(map_pair.second);
                }
            }
        }

        if (all_tasks.empty()) return true;

        bool has_ready_to_run = false;
        for (AbsRTTask *task : all_tasks) {
            if (!task || !task->isActive()) continue;
            Tick slack;
            try { slack = calculateSlackForTask(task); } catch (...) { continue; }

            if (slack <= 0) {
                has_ready_to_run = true;
            }

            if (first_task) {
                min_slack = slack;
                first_task = false;
            } else if (slack < min_slack) {
                min_slack = slack;
            }
        }

        if (first_task) return true;

        if (has_ready_to_run) {
            return true;
        }

        if (min_slack > 0) {
            // ⭐ 纯正 ALAP 核心：设定精确唤醒闹钟！
            Tick wake_time = current_time + min_slack;
            if (_alap_wake_event) {
                _alap_wake_event->drop();
                _alap_wake_event->post(wake_time);
            }
            SCHEDULER_LOG_INFO("⏸️  [ALAP-Sync] 全局时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠。已定闹钟于 " +
                               std::to_string(static_cast<int64_t>(wake_time)) + "ms");
            _stats.total_alap_forced_idle++;
            return false; // 强制系统休眠
        }
        
        return true; // 允许调度
    }


    MetaSim::Tick ALAPSyncScheduler::calculateSlackForTask(AbsRTTask *task) {
        return calculateSlackForTask(task, SIMUL.getTime());
    }

    MetaSim::Tick ALAPSyncScheduler::calculateSlackForTask(
        AbsRTTask *task, Tick current_time) {
        if (!task) {
            return MetaSim::Tick(0);
        }

        if (task->getArrival() > current_time) {
            return task->getArrival() - current_time;
        }

        const Tick absolute_deadline = task->getDeadline();
        const double remaining_double = std::max(0.0, task->getRemainingWCET());
        const auto remaining_ticks = static_cast<Tick::impl_t>(std::ceil(remaining_double));
        const Tick remaining(remaining_ticks);
        const Tick slack = absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_INFO(std::string("🧮 [ALAP-Sync] Slack计算: ") +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        return slack;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void ALAPSyncScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ALAP-Sync] ===== ALAP-Sync批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO(std::string("  ALAP强制休眠次数: ") + std::to_string(_stats.total_alap_forced_idle));
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ALAPSyncScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ALAPSyncScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void ALAPSyncScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ALAP-Sync] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const double EPSILON = 1e-9;
        std::vector<AbsRTTask *> tasks_to_interrupt;

        // ⭐ V28.15修复：使用kernel的getCurrentExecutingTasks()获取实际运行中的任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();

        // ⭐ 关键修复：先扣除上一ms执行消耗的能量，再检查是否足够继续
        // 这样可以确保能量扣除和能量检查的时序正确
        double total_energy_to_deduct = 0.0;
        for (auto &map_pair : running_tasks) {
            AbsRTTask *task = map_pair.second;
            if (!task) {
                continue;
            }

            // 计算该任务执行1ms所需的能量
            double unit_energy = calculateUnitEnergyForTask(task);
            total_energy_to_deduct += unit_energy;
        }

        // ⭐ 检查运行任务续期能量是否充足（不扣除，扣除在批量调度中完成）
        if (total_energy_to_deduct > 0) {
            if (_current_energy >= total_energy_to_deduct) {
                // ✅ 能量充足，记录日志
                SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-Sync] 运行任务续期能量充足: ") +
                                   "需要=" + std::to_string(total_energy_to_deduct * 1000) + " mJ " +
                                   "当前=" + std::to_string(_current_energy * 1000) + " mJ " +
                                   "(能量已在批量调度中扣除)");
            } else {
                // ❌ 能量不足，中断所有运行中的任务
                SCHEDULER_LOG_WARNING(std::string("❌ [ALAP-Sync] 运行任务续期能量不足，将中断所有运行任务: ") +
                                        "需要=" + std::to_string(total_energy_to_deduct * 1000) + " mJ " +
                                        "当前=" + std::to_string(_current_energy * 1000) + " mJ");

                // 将所有运行中的任务添加到中断列表
                for (auto &map_pair : running_tasks) {
                    AbsRTTask *task = map_pair.second;
                    if (task) {
                        tasks_to_interrupt.push_back(task);
                    }
                }

                // 标记能量已耗尽
                _energy_depleted = true;

                SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 能量已耗尽，将中断") +
                                   std::to_string(tasks_to_interrupt.size()) + "个运行任务");
            }
        }

        // 2. 检查所有运行中的任务（细粒度监控）
        // ⭐ Bug #9修复v2：如果当前tick有任务在运行，不中断它们
        // ALAP-Sync的核心原则：要么全不调度要么全部调度
        // - 如果有任务在运行：让它们继续运行到下一个tick
        // - 如果没有任务在运行：检查能量是否足够调度新任务
        bool has_running_tasks = !running_tasks.empty();
        if (has_running_tasks) {
            SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-Sync] 当前tick有") +
                               std::to_string(running_tasks.size()) +
                               "个任务在运行，允许继续执行到下一个tick");
        }

        if (!has_running_tasks && !_batch_scheduled_this_tick && !_energy_depleted) {
            for (auto &map_pair : running_tasks) {
                AbsRTTask *task = map_pair.second;
                if (!task) {
                    continue;
                }

                // 计算该任务执行1ms所需的能量
                double unit_energy = calculateUnitEnergyForTask(task);

                // ⭐ 检查：当前能量是否足够该任务继续执行1ms
                if (_current_energy < unit_energy - EPSILON) {
                    SCHEDULER_LOG_WARNING(std::string("⚡ [ALAP-Sync] 任务能量不足，将中断: ") +
                                         getTaskName(task) +
                                         " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                         " 当前能量=" + std::to_string(_current_energy) + "J");

                    tasks_to_interrupt.push_back(task);
                    _stats.total_skipped_energy++;
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-Sync] 任务能量充足: ") +
                                       getTaskName(task) +
                                       " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                       " 当前能量=" + std::to_string(_current_energy) + "J");
                }
            }
        }

        // 2. ⭐ ALAP-Sync"全无"原则：能量不足时，不调度任何新任务
        // 注意：当前正在运行的任务会继续执行，但由于：
        //   - _energy_depleted = true
        //   - _current_batch_tasks已清空（在批量调度的else分支中）
        //   - getTaskN()会返回nullptr
        // 所以不会有任何新任务被调度，当前任务完成后就会停止
        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 能量已耗尽，") +
                               std::to_string(tasks_to_interrupt.size()) + "个任务将自然完成" +
                               "（不再调度新任务，遵循ALAP-Sync'全无'原则）");
        }
    }

    // =====================================================
    // ⭐ 能量耗尽预测机制（Bug修复：防止虚空借电）
    // =====================================================

    double ALAPSyncScheduler::calculateTotalPowerConsumption() {
        if (!_kernel) {
            return 0.0;
        }

        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
        double total_power = 0.0;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            total_power += calculateUnitEnergyForTask(task);
        }

        return total_power;
    }

    MetaSim::Tick ALAPSyncScheduler::predictTimeToDepletion(double energy, double power) {
        if (power <= 0.0 || energy <= 0.0) {
            return MetaSim::Tick(-1);  // 无法预测
        }
        // time_to_deplete = energy / power (单位：ms)
        // 返回从当前时间算起，还能运行多少ms
        double time_ms = energy / power;
        return static_cast<MetaSim::Tick>(ceil(time_ms));
    }

    void ALAPSyncScheduler::scheduleEnergyDepletionEvent(MetaSim::Tick time_until_depletion) {
        (void)time_until_depletion;
        SCHEDULER_LOG_DEBUG("⚡ [ALAP-Sync] scheduleEnergyDepletionEvent() 已停用");
    }

    void ALAPSyncScheduler::cancelEnergyDepletionEvent() {
        if (_energy_depleted_event) {
            _energy_depleted_event->drop();
        }
    }

    void ALAPSyncScheduler::onEnergyDepleted() {
        cancelEnergyDepletionEvent();
        SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] onEnergyDepleted() 已停用，真实挂起仅在 tick 同步组决策点发生");
    }
} // namespace RTSim
