// gpfp_st_sync_scheduler.cpp - ST-Sync (Slack Time Sync) Scheduler Implementation
// 算法特点：
// 1. ASAP调度：尽可能早执行任务（不需要等Slack=0）
// 2. All-or-Nothing批量：能量足够全部调度，不足全部挂起
// 3. 深度充电：能量不足时进入充电模式，直到Slack=0或电池充满
// 4. Tick级能量检查和续期
// 5. Tick末尾收集能量

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_st_sync_scheduler.hpp>
#include <rtsim/task.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/cpu.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/mrtkernel.hpp>

// 统一日志系统
#include "../../utils/unified_logger.hpp"

namespace RTSim {

    using namespace MetaSim;

    // =====================================================
    // STSyncTickEvent 实现
    // =====================================================

    STSyncTickEvent::STSyncTickEvent(STSyncScheduler *scheduler)
        : MetaSim::Event("STSyncTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // 与 ST-Block / ST-NonBlock 对齐：tick 优先级低于 arrival，
        // 确保 0ms 到达任务先入队，再进行首个同步批次调度。
    }

    void STSyncTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_WARNING(std::string("⏱️ [ST-Sync] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // STSyncGroupWakeEvent 实现 - Slack时间后的唤醒定时器
    // ⭐ V122修复：真正注册唤醒事件到模拟器事件队列
    // =====================================================

    STSyncGroupWakeEvent::STSyncGroupWakeEvent(STSyncScheduler *scheduler)
        : MetaSim::Event("STSyncGroupWakeEvent", MetaSim::Event::_DEFAULT_PRIORITY - 2),
          _scheduler(scheduler),
          _wake_time(0),
          _valid(false) {
    }

    void STSyncGroupWakeEvent::doit() {
        if (!_scheduler || !_valid) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        SCHEDULER_LOG_WARNING(std::string("⏰ [ST-Sync V130] 组唤醒事件触发 @ ") +
                             std::to_string(static_cast<int64_t>(current_time)) + "ms");

        _valid = false;

        // 唤醒事件只负责把“重新评估时刻”对齐到当前 tick。
        // 真正的解锁、等待队列回流与批次重建都放在 performTickScheduling() 内统一完成，
        // 避免 wake event 在 tick 之前抢先清锁，导致等待中的同步组丢失窗口语义。
        SCHEDULER_LOG_INFO(std::string("🔔 [ST-Sync V130] 唤醒时刻已到，等待同tick统一解锁与重建同步组"));
    }

    void STSyncGroupWakeEvent::schedule(MetaSim::Tick wake_time) {
        _wake_time = wake_time;
        _valid = true;

        // 注册新事件到模拟器事件队列
        post(wake_time);

        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync V122] 唤醒事件已注册: ") +
                          "唤醒时间=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
    }

    // 兼容旧接口的空壳事件。
    // 当前 ST-Sync 的能量记账完全在 tick 级批量逻辑中完成。

    STSyncEnergyCheckEvent::STSyncEnergyCheckEvent(STSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("STSyncEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 当前实现没有按任务运行时能量事件；能量在 tick 级批量逻辑中统一处理。
    }

    void STSyncEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        return;
    }

    // =====================================================
    // STSyncTaskModel 实现
    // =====================================================

    STSyncTaskModel::STSyncTaskModel(AbsRTTask *t, int period, int wcet,
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

    STSyncTaskModel::~STSyncTaskModel() {}

    Tick STSyncTaskModel::getPriority() const {
        return _rm_priority;
    }

    void STSyncTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void STSyncTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // STSyncScheduler 实现
    // =====================================================

    STSyncScheduler::STSyncScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _dispatching_tasks_total_energy(0.0),
          _last_tick_time(0),
          _last_collection_time(0),
          _solar_data_file(""),
          _pv_efficiency(0.18),
          _pv_area_m2(1.0),
          _use_real_solar_data(false),
          _start_time_offset(0),
          _base_harvest_rate(0.054),  // ⭐ V93修复：默认值 54 mW
          _tick_event(nullptr),
          _group_wake_event(nullptr),  // ⭐ V131修复：初始化为nullptr
          _first_tick_scheduled(false),
          _kernel(nullptr),
          _batch_scheduled_this_tick(false),
          _energy_depleted(false),
          _selection_tick(-1),
          _selection_generation(0),
          _selection_frozen(false),
          _energy_commit_tick(-1),
          _energy_commit_generation(0),
          _energy_commit_valid(false),
          _v108_batch_energy_checked(false),
          _v108_batch_energy_sufficient(true),
          _last_v108_insert_time(0),
          _v108_last_ready_queue_size(0),
          _v108_batch_start_energy(0.0),
          _v108_batch_k_approved(0),
          _v108_batch_total_energy(0.0),
          _last_v108_check_time(0),
          _current_batch_size(0),
          _deep_charging(false),
          _charge_start_time(0),
          _is_charging_sleep(false),  // ⭐ V130: 深度休眠锁初始化
          _last_preempted_task(nullptr),
          _last_preempted_tick(0),
          _last_ready_queue_size(0) {

        SCHEDULER_LOG_INFO("🚀 [ST-Sync] ST-Sync Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ST-Sync] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ST-Sync] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync] 开始时间偏移: ") +
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
                                SCHEDULER_LOG_INFO(std::string("☀️ [ST-Sync] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ST-Sync] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ST-Sync] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ST-Sync] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ST-Sync] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new STSyncTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ST-Sync] ST-Sync Scheduler 初始化完成");
    }

    STSyncScheduler::STSyncScheduler(const std::vector<std::string> &params)
        : STSyncScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<STSyncScheduler>
        STSyncScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<STSyncScheduler>(params);
    }

    STSyncScheduler::~STSyncScheduler() {
        if (_tick_event) {
            delete _tick_event;
            _tick_event = nullptr;
        }

        // 清理任务模型
        for (auto &pair : _task_models) {
            delete pair.second;
        }
        _task_models.clear();
    }

    // =====================================================
    // ST-Sync批量调度辅助方法
    // =====================================================

    int STSyncScheduler::calculateBatchSize() {
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
        int ready_tasks = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(idle_cpus, ready_tasks);

        SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync] calculateBatchSize: ") +
                           "空闲CPU=" + std::to_string(idle_cpus) +
                           " 就绪任务=" + std::to_string(ready_tasks) +
                           " 批量k=" + std::to_string(batch_size));

        return batch_size;
    }


    void STSyncScheduler::executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy) {
        // ⭐ ST-Sync核心：批量调度时一次性扣减k个任务的1ms能耗
        // 当前时刻能量 = 上一时刻结余 + 本次充电能量 - 已消耗能量 - 本次批量调度能耗
        double old_energy = _current_energy;
        _current_energy -= total_energy;
        _stats.total_energy_consumed += total_energy;

        SCHEDULER_LOG_INFO(std::string("📋 [ST-Sync] 批量调度: ") +
                           "任务数=" + std::to_string(tasks.size()) +
                           " 总能耗=" + std::to_string(total_energy * 1000) + " mJ" +
                           " 能量=" + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    // =====================================================
    // 核心调度逻辑 - ST-Sync批量调度算法
    // =====================================================

        void STSyncScheduler::performTickScheduling() {
        {
            Tick current_time = SIMUL.getTime();
            if (_selection_frozen && _selection_tick == current_time) {
                SCHEDULER_LOG_DEBUG(
                    std::string("🛡️ [ST-Sync] 本tick同步组已冻结，跳过重复决策 @ ") +
                    std::to_string(static_cast<int64_t>(current_time)) + "ms");
                return;
            }

            SCHEDULER_LOG_DEBUG(std::string("🔄 [ST-Sync] performTickScheduling @ ") +
                               std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                               " 能量=" + std::to_string(_current_energy) + "J");

            _stats.total_tick_count++;

            Tick elapsed = current_time - _last_tick_time;
            if (elapsed > 0) {
                double harvested = collectSolarEnergy(current_time);
                if (harvested > 0.000001) {
                    _current_energy += harvested;
                    _stats.total_energy_harvested += harvested;
                    SCHEDULER_LOG_INFO(std::string("☀️ [ST-Sync] Tick边界收集能量: ") +
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
                SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] _kernel为nullptr，跳过批量调度");
                return;
            }

            cleanupExpiredTasks();

            if (!_deferred_arrivals.empty()) {
                std::vector<AbsRTTask *> deferred(_deferred_arrivals.begin(),
                                                  _deferred_arrivals.end());
                _deferred_arrivals.clear();
                for (AbsRTTask *task : deferred) {
                    if (!task || !task->isActive()) {
                        continue;
                    }
                    if (!isInWaitingQueue(task) && !isInReadyQueue(task)) {
                        addToReadyQueue(task);
                    }
                }
            }

            bool charging_wait_active = false;
            if ((_is_charging_sleep || _deep_charging) && !_waiting_queue.empty()) {
                Tick wait_slack = calculateMinSlack();
                if (wait_slack > Tick(0) && _current_energy < _max_energy - 1e-9) {
                    // The waiting batch remains asleep, but running continuations
                    // still need a fresh frozen selection and energy charge on
                    // every tick.  Keep evaluating the global top-M below.
                    charging_wait_active = true;
                    SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync] 同步组充电等待中，继续重建本 tick top-M，group_slack=") +
                                       std::to_string(static_cast<int64_t>(wait_slack)) + "ms");
                } else {
                    promoteWaitingTasksToReadyQueue("charging window ended");
                    _is_charging_sleep = false;
                    _deep_charging = false;
                    _energy_depleted = false;
                    if (_group_wake_event) {
                        _group_wake_event->invalidate();
                    }
                }
            }

            auto has_higher_rm_priority = [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                if (lhs == rhs) {
                    return false;
                }
                STSyncTaskModel *lhs_model = getTaskModel(lhs);
                STSyncTaskModel *rhs_model = getTaskModel(rhs);
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
            };

            const auto &running_tasks_map = _kernel->getCurrentExecutingTasks();
            std::set<AbsRTTask *> running_tasks;
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

            for (const auto &[cpu, task] : running_tasks_map) {
                (void)cpu;
                if (task && task->isExecuting()) {
                    running_tasks.insert(task);
                    add_active(task, true);
                }
            }
            for (AbsRTTask *task : _ready_queue) {
                add_active(task, false);
            }
            if (charging_wait_active) {
                // Waiting jobs must still participate in global top-M ordering.
                // Otherwise a lower-priority continuation or new job could bypass
                // the blocked synchronization group while it charges.
                for (AbsRTTask *task : _waiting_queue) {
                    add_active(task, false);
                }
            }

            std::stable_sort(active_tasks.begin(), active_tasks.end(),
                             has_higher_rm_priority);

            std::vector<AbsRTTask *> previous_selection = _current_batch_tasks;
            _current_batch_tasks.clear();
            _preempt_batch_tasks.clear();
            _current_batch_size = 0;
            _batch_scheduled_this_tick = false;
            _dispatching_tasks_total_energy = 0.0;
            _counted_tasks_in_dispatch.clear();

            int total_cpus = static_cast<int>(running_tasks_map.size());
            if (total_cpus <= 0) {
                total_cpus = ConfigManager::getInstance().getNumCores();
            }

            std::vector<AbsRTTask *> desired_tasks;
            for (AbsRTTask *task : active_tasks) {
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
                    continuation_energy += calculateUnitEnergyForTask(task);
                } else {
                    idle_core_batch.push_back(task);
                    idle_core_batch_energy += calculateUnitEnergyForTask(task);
                }
            }

            _selection_tick = current_time;
            _selection_generation++;
            _selection_frozen = true;
            _energy_commit_valid = false;
            _energy_depleted = false;
            _deep_charging = false;
            _is_charging_sleep = false;

            std::vector<AbsRTTask *> selected_tasks = continuation_tasks;
            std::vector<AbsRTTask *> blocked_batch;
            double required_batch_energy = continuation_energy;
            const double epsilon = 1e-9;
            const bool continuation_affordable =
                _current_energy + epsilon >= continuation_energy;
            const bool idle_core_batch_affordable =
                !charging_wait_active && continuation_affordable &&
                _current_energy + epsilon >=
                    continuation_energy + idle_core_batch_energy;
            if (!continuation_affordable) {
                // Once a continuation in global top-M cannot be paid, the whole
                // desired synchronization group is blocked atomically.
                blocked_batch = desired_tasks;
                selected_tasks.clear();
                required_batch_energy = 0.0;
                _stats.total_batch_skipped++;
                _energy_depleted = true;
            } else if (!idle_core_batch.empty() &&
                       !idle_core_batch_affordable) {
                blocked_batch = idle_core_batch;
                _stats.total_batch_skipped++;
                _energy_depleted = true;
            } else {
                selected_tasks = desired_tasks;
                required_batch_energy += idle_core_batch_energy;
            }

            Tick group_slack = Tick(0);
            if (!blocked_batch.empty()) {
                // Always derive min slack from the actual blocked group.  In
                // particular, an unpaid continuation can make this group differ
                // from the provisional idle-core batch.
                group_slack = std::numeric_limits<Tick::impl_t>::max();
                for (AbsRTTask *task : blocked_batch) {
                    group_slack = std::min(
                        group_slack, calculateSlackForTask(task));
                }
                if (group_slack > Tick(0)) {
                    _deep_charging = true;
                    _is_charging_sleep = true;
                    for (AbsRTTask *task : blocked_batch) {
                        addToWaitingQueue(task);
                    }
                    Tick wake_time = calculateGroupWakeTime(
                        group_slack,
                        calculateBatchUnitEnergy(blocked_batch));
                    if (wake_time > current_time) {
                        scheduleGroupWakeEvent(wake_time);
                    }
                } else {
                    // Slack has expired, so no old charging window may keep jobs
                    // hidden from the next tick's normal eligibility pass.
                    promoteWaitingTasksToReadyQueue("blocked group slack exhausted");
                    if (_group_wake_event) {
                        _group_wake_event->invalidate();
                    }
                }

                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 实际同步组原子验资失败: K=") +
                                      std::to_string(blocked_batch.size()) +
                                      " group_slack=" + std::to_string(static_cast<int64_t>(group_slack)) +
                                      " 需要=" + std::to_string(calculateBatchUnitEnergy(blocked_batch) * 1000.0) + " mJ" +
                                      " 当前=" + std::to_string(_current_energy * 1000.0) + " mJ");
            } else if (charging_wait_active && !_waiting_queue.empty()) {
                // A lower-priority waiting group can remain outside this tick's
                // global top-M.  Preserve its charging window without skipping
                // accounting for the selected continuations above.
                _deep_charging = true;
                _is_charging_sleep = true;
                _energy_depleted = true;
            }

            _current_batch_tasks = selected_tasks;
            _current_batch_size = static_cast<int>(_current_batch_tasks.size());
            _batch_scheduled_this_tick = !selected_tasks.empty();
            _dispatching_tasks_total_energy = required_batch_energy;

            if (!selected_tasks.empty()) {
                if (_current_energy + epsilon < required_batch_energy) {
                    throw std::logic_error("ST-Sync attempted to commit unaffordable batch energy");
                }
                _current_energy = std::max(0.0, _current_energy - required_batch_energy);
                _stats.total_energy_consumed += required_batch_energy;
                _stats.total_batch_schedules++;
                _energy_commit_tick = current_time;
                _energy_commit_generation = _selection_generation;
                _energy_commit_valid = true;
            }

            bool has_stale_dispatch = false;
            for (AbsRTTask *task : previous_selection) {
                if (!task || _kernel->getProcessor(task) != nullptr) {
                    continue;
                }
                if (std::find(_current_batch_tasks.begin(),
                              _current_batch_tasks.end(),
                              task) == _current_batch_tasks.end()) {
                    has_stale_dispatch = true;
                    break;
                }
            }
            if (has_stale_dispatch) {
                for (const auto &[cpu, running] : running_tasks_map) {
                    if (!running && _kernel->isCPUDispatching(cpu)) {
                        _kernel->dispatch(cpu);
                    }
                }
            }

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

            SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] Tick冻结同步组: active=") +
                               std::to_string(active_tasks.size()) +
                               " selected=" + std::to_string(selected_tasks.size()) +
                               " group_slack=" + std::to_string(static_cast<int64_t>(group_slack)) +
                               " 扣减=" + std::to_string(required_batch_energy * 1000.0) + " mJ" +
                               " 剩余=" + std::to_string(_current_energy * 1000.0) + " mJ");
            return;
        }
    }

    void STSyncScheduler::schedule() {
        // ST-Sync依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ST-Sync] schedule() 被调用");
    }

    // =====================================================
    // getFirst - ST-Sync废弃，返回nullptr
    // =====================================================

    AbsRTTask *STSyncScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ST-Sync] getFirst() 被调用（ST-Sync已废弃）"));
        // ST-Sync使用批量调度，不使用getFirst
        return nullptr;
    }

    // =====================================================
    // getTaskN - 返回批量中的第n个任务
    // =====================================================

    AbsRTTask *STSyncScheduler::getTaskN(unsigned int n) {
        Tick current_time = SIMUL.getTime();
        if (!_selection_frozen ||
            _selection_tick != current_time ||
            !_batch_scheduled_this_tick) {
            return nullptr;
        }
        if (n >= _current_batch_tasks.size()) {
            return nullptr;
        }

        AbsRTTask *task = _current_batch_tasks[n];
        if (!task || !task->isActive()) {
            return nullptr;
        }
        if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
            return nullptr;
        }
        return task;
    }

    // =====================================================
    // notify - ST-Sync不再扣减能量（已在批量时扣减）
    // =====================================================

    void STSyncScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_kernel && _kernel->getProcessor(task) != nullptr) {
            SCHEDULER_LOG_DEBUG(std::string("⏭️ [ST-Sync] notify: 跳过运行中任务: ") + getTaskName(task));
            return;
        }

        if (_is_charging_sleep || _deep_charging) {
            if (std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task) == _deferred_arrivals.end()) {
                _deferred_arrivals.push_back(task);
            }
            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] notify: 充电窗口内延后吸纳任务到下一tick: ") + getTaskName(task));
            return;
        }

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例到达）
        // 周期性任务复用同一个AbsRTTask对象，但每个实例都是独立的
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] notify: 检查WCET完成标志: ") +
                           getTaskName(task) + " 集合大小=" + std::to_string(_tasks_completed_wcet.size()));
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync] notify: 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ST-Sync] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void STSyncScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] addTask: 任务为空");
            return;
        }

        // ⭐ 关键修复：在添加任务时初始化_kernel
        // 这样在第一个tick触发时，_kernel已经可用
        if (!_kernel) {
            _kernel = dynamic_cast<MRTKernel *>(task->getKernel());
            if (_kernel) {
                SCHEDULER_LOG_INFO("📥 [ST-Sync] addTask: _kernel初始化成功");
            }
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ST-Sync] 添加任务: ") + getTaskName(task));
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
        STSyncTaskModel *model = new STSyncTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void STSyncScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ST-Sync] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);

        for (auto &map_pair : _running_tasks) {
            if (map_pair.second == task) {
                _running_tasks[map_pair.first] = nullptr;
            }
        }

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void STSyncScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ST-Sync] 任务到达: ") + getTaskName(task));

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例重新开始）
        // 周期性任务复用同一个AbsRTTask对象，但每个实例都是独立的
        clearPersistentTaskState(task);
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync] 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        if (_is_charging_sleep || _deep_charging || _energy_depleted) {
            removeFromReadyQueue(task);
            if (std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task) == _deferred_arrivals.end()) {
                _deferred_arrivals.push_back(task);
            }

            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] 充电/缺电窗口内到达，保持延后到下一tick: ") +
                              getTaskName(task));
            return;
        }

        auto deferred_it = std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task);
        if (deferred_it != _deferred_arrivals.end()) {
            _deferred_arrivals.erase(deferred_it);
        }

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }

        if (!_kernel) {
            _kernel = getKernel();
        }

        if (!_kernel) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 到达任务立即参与RM抢占检查: ") + getTaskName(task));
        checkAndPreempt();
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void STSyncScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ST-Sync] tick-frozen scheduler ignores mid-tick preemption request");
    }

    void STSyncScheduler::checkAndPreemptOnAllCPUs() {
        SCHEDULER_LOG_DEBUG("⏸️ [ST-Sync] checkAndPreemptOnAllCPUs 已停用，保持稳定同步批次");
        return;

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                return;
            }
        }

        if (_is_charging_sleep || _deep_charging || _energy_depleted) {
            SCHEDULER_LOG_DEBUG("⏸️ [ST-Sync] charging/depleted窗口内跳过arrival-time抢占");
            return;
        }

        if (!_waiting_queue.empty()) {
            SCHEDULER_LOG_DEBUG("⏸️ [ST-Sync] waiting_queue拥有当前恢复窗口，arrival-time不改写批次");
            return;
        }

        const auto &running_tasks_map = _kernel->getCurrentExecutingTasks();

        int truly_free_cpus = 0;
        int busy_executing = 0;
        int busy_dispatching = 0;

        for (const auto &[cpu, task] : running_tasks_map) {
            bool is_dispatching = _kernel->isCPUDispatching(cpu);
            if (!task) {
                if (!is_dispatching) {
                    truly_free_cpus++;
                } else {
                    busy_dispatching++;
                }
            } else if (task->isExecuting()) {
                busy_executing++;
            } else {
                busy_dispatching++;
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] CPU状态: 空闲=") +
                          std::to_string(truly_free_cpus) +
                          " 执行中=" + std::to_string(busy_executing) +
                          " 上下文切换中=" + std::to_string(busy_dispatching));

        if (truly_free_cpus > 0) {
            rebuildApprovedBatchForImmediateDispatch();
            SCHEDULER_LOG_INFO(std::string("⏭️ [ST-Sync] 有") + std::to_string(truly_free_cpus) +
                              "个空闲CPU，arrival-time不抢占，仅重建稳定批次");
            return;
        }

        Tick current_time = SIMUL.getTime();
        if (_last_preempted_task && _last_preempted_tick == current_time) {
            bool has_higher_priority = false;
            STSyncTaskModel *preempted_model = getTaskModel(_last_preempted_task);

            for (AbsRTTask *candidate : _ready_queue) {
                if (!candidate) {
                    continue;
                }
                STSyncTaskModel *model = getTaskModel(candidate);
                if (!model) {
                    continue;
                }
                if (preempted_model && model->getRMPriority() < preempted_model->getRMPriority()) {
                    has_higher_priority = true;
                    break;
                }
            }

            if (!has_higher_priority) {
                SCHEDULER_LOG_DEBUG(std::string("⏸️ [ST-Sync] 抢占防抖：跳过同tick连续抢占 ") +
                                   getTaskName(_last_preempted_task));
                return;
            }
        }

        AbsRTTask *best_candidate = nullptr;
        STSyncTaskModel *best_model = nullptr;

        for (AbsRTTask *candidate : _ready_queue) {
            if (!candidate || !candidate->isActive()) {
                continue;
            }

            CPU *cand_cpu = _kernel->getProcessor(candidate);
            if (cand_cpu != nullptr) {
                continue;
            }

            STSyncTaskModel *model = getTaskModel(candidate);
            if (!model) {
                continue;
            }

            if (!best_candidate || model->getRMPriority() < best_model->getRMPriority()) {
                best_candidate = candidate;
                best_model = model;
            }
        }

        if (!best_candidate || !best_model) {
            return;
        }

        AbsRTTask *worst_running = nullptr;
        STSyncTaskModel *worst_model = nullptr;

        for (const auto &[cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) {
                continue;
            }

            STSyncTaskModel *model = getTaskModel(task);
            if (!model) {
                continue;
            }

            if (!worst_running || model->getRMPriority() > worst_model->getRMPriority()) {
                worst_running = task;
                worst_model = model;
            }
        }

        if (!worst_running || !worst_model) {
            return;
        }

        if (!shouldPreempt(worst_running, best_candidate)) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync] RM抢占: 挂起=") +
                          getTaskName(worst_running) +
                          "(优先级=" + std::to_string(static_cast<int64_t>(worst_model->getRMPriority())) + ")" +
                          " 调度=" + getTaskName(best_candidate) +
                          "(优先级=" + std::to_string(static_cast<int64_t>(best_model->getRMPriority())) + ")");

        _last_preempted_task = worst_running;
        _last_preempted_tick = current_time;

        setSuspendReason(worst_running, "preemption");
        _kernel->suspend(worst_running);
        rebuildApprovedBatchForImmediateDispatch();
    }

    bool STSyncScheduler::shouldPreempt(AbsRTTask *running_task, AbsRTTask *new_task) {
        if (!running_task || !new_task) {
            SCHEDULER_LOG_INFO(std::string("❌ [ST-Sync] shouldPreempt: running_task或new_task为空"));
            return false;
        }

        STSyncTaskModel *running_model = getTaskModel(running_task);
        STSyncTaskModel *new_model = getTaskModel(new_task);

        if (!running_model || !new_model) {
            SCHEDULER_LOG_INFO(std::string("❌ [ST-Sync] shouldPreempt: 获取task model失败"));
            return false;
        }

        // 检查新任务的能量是否足够
        double unit_energy = calculateUnitEnergyForTask(new_task);
        if (_current_energy < unit_energy) {
            SCHEDULER_LOG_INFO(std::string("❌ [ST-Sync] shouldPreempt: 能量不足 _current_energy=") +
                              std::to_string(_current_energy * 1000) + " < unit_energy=" +
                              std::to_string(unit_energy * 1000) + " mJ");
            return false;  // 能量不足，不抢占
        }

        // 新任务优先级更高（RM优先级数值越小越高）
        int running_prio = running_model->getRMPriority();
        int new_prio = new_model->getRMPriority();
        bool should = new_prio < running_prio;

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] shouldPreempt: ") +
                          getTaskName(running_task) + "(prio=" + std::to_string(running_prio) + ") vs " +
                          getTaskName(new_task) + "(prio=" + std::to_string(new_prio) + ") = " +
                          (should ? "true" : "false"));

        return should;
    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void STSyncScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_WARNING(std::string("➕ [ST-Sync] insert: ") + getTaskName(task) +
                          " @ " + std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        // ⭐ V108改进：使用时间戳避免同一时刻重复重置
        // 只有当时间推进时才重置标志，防止同一时刻多次insert导致重复检查
        MetaSim::Tick current_insert_time = SIMUL.getTime();
        if (current_insert_time != _last_v108_insert_time) {
            _v108_batch_energy_checked = false;
            _v108_batch_k_approved = 0;  // ⭐ 重置已批准任务数
            _v108_batch_total_energy = 0.0;
            _last_v108_insert_time = current_insert_time;
            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync V108] 新时间点，重置批量检查标志 @ t=") +
                              std::to_string(static_cast<int64_t>(current_insert_time)) + "ms");
        }

        // ⭐ ST-Sync：新到达任务也必须服从 tick 边界统一准入。
        // 缺电充电窗口期间，不允许新实例在 tick 中途直接改写 ready_group。
        // 旧阻塞组由 waiting_queue 保持；新实例暂时只留在调度器基类容器，等待下一 tick 统一吸纳。
        Scheduler::insert(task);

        bool suspended_for_energy = (getSuspendReason(task) == "insufficient_energy");
        bool in_charging_window = (_is_charging_sleep || _deep_charging || _energy_depleted);

        if (suspended_for_energy) {
            addToWaitingQueue(task);
        } else if (in_charging_window) {
            removeFromReadyQueue(task);
            removeFromWaitingQueue(task);
            if (std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task) == _deferred_arrivals.end()) {
                _deferred_arrivals.push_back(task);
            }
            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] charging窗口内延后吸纳新实例到下一tick: ") +
                              getTaskName(task));
        } else {
            addToReadyQueue(task);
        }
    }

    // ========== V115：挂起原因追踪（消灭幽灵抢占） ==========
    void STSyncScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    // V115: const方法实现EnergyInfoProvider接口
    std::string STSyncScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) return "unknown";
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    // V115: override方法实现EnergyInfoProvider接口
    void STSyncScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    void STSyncScheduler::clearPersistentTaskState(AbsRTTask *task) {
        if (!task) {
            return;
        }

        removeFromWaitingQueue(task);
        _current_batch_tasks.erase(
            std::remove(_current_batch_tasks.begin(),
                        _current_batch_tasks.end(),
                        task),
            _current_batch_tasks.end());
        _preempt_batch_tasks.erase(
            std::remove(_preempt_batch_tasks.begin(),
                        _preempt_batch_tasks.end(),
                        task),
            _preempt_batch_tasks.end());
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());
        if (_current_batch_tasks.empty()) {
            _batch_scheduled_this_tick = false;
        }
        _counted_tasks_in_dispatch.erase(task);
        _energy_accounts.erase(task);
        clearSuspendReason(task);
    }

    void STSyncScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ST-Sync] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        clearPersistentTaskState(task);
        removeFromReadyQueue(task);
        auto deferred_it = std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task);
        if (deferred_it != _deferred_arrivals.end()) {
            _deferred_arrivals.erase(deferred_it);
        }
        stopEnergyCheckForTask(task);
        _tasks_completed_wcet.erase(task);
        auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
        if (batch_it != _current_batch_tasks.end()) {
            _current_batch_tasks.erase(batch_it);
        }
    }

    void STSyncScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        if (_kernel && _kernel->getProcessor(task) != nullptr) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 任务已在CPU上运行，跳过重新入队: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        STSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();
        auto higher_priority = [this](AbsRTTask *lhs, AbsRTTask *rhs) {
            STSyncTaskModel *lhs_model = getTaskModel(lhs);
            STSyncTaskModel *rhs_model = getTaskModel(rhs);
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
        };

        // 按 RM / period / task number 稳定插入。
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            if (higher_priority(task, *it)) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ST-Sync] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void STSyncScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ST-Sync] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void STSyncScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        removeFromReadyQueue(task);
        if (isInWaitingQueue(task)) {
            return;
        }

        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ST-Sync] 任务加入等待队列: ") + getTaskName(task));
    }

    void STSyncScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    void STSyncScheduler::promoteWaitingTasksToReadyQueue(const std::string &context) {
        if (_waiting_queue.empty()) {
            return;
        }

        std::vector<AbsRTTask *> waiting_tasks(_waiting_queue.begin(), _waiting_queue.end());
        _waiting_queue.clear();

        for (AbsRTTask *task : waiting_tasks) {
            if (!task || !task->isActive()) {
                continue;
            }
            addToReadyQueue(task);
        }

        SCHEDULER_LOG_INFO(std::string("🔓 [ST-Sync] 充电窗口结束，等待队列任务回到就绪队列: ") +
                          context +
                          " count=" + std::to_string(waiting_tasks.size()));
    }

    void STSyncScheduler::rebuildApprovedBatchForImmediateDispatch() {
        SCHEDULER_LOG_DEBUG("⏸️ [ST-Sync] immediate rebuild disabled; batch is tick-frozen");
        return;

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return;
        }

        std::vector<AbsRTTask *> approved_batch = collectActiveRunningBatchTasks();
        size_t target_size = _kernel->getCurrentExecutingTasks().size();

        for (AbsRTTask *task : _ready_queue) {
            if (approved_batch.size() >= target_size) {
                break;
            }
            if (!task || !task->isActive()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            if (_kernel->getProcessor(task) != nullptr) {
                continue;
            }
            if (std::find(approved_batch.begin(), approved_batch.end(), task) != approved_batch.end()) {
                continue;
            }
            approved_batch.push_back(task);
        }

        std::sort(approved_batch.begin(), approved_batch.end(),
            [this](AbsRTTask *a, AbsRTTask *b) {
                STSyncTaskModel *model_a = getTaskModel(a);
                STSyncTaskModel *model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });

        _current_batch_tasks = approved_batch;
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());
        _batch_scheduled_this_tick = !_current_batch_tasks.empty();

        SCHEDULER_LOG_INFO(std::string("🧩 [ST-Sync] 立即重建稳定批次: size=") +
                          std::to_string(_current_batch_tasks.size()));
    }

    bool STSyncScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool STSyncScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *STSyncScheduler::getHighestPriorityTaskFromReadyQueue() {
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

    double STSyncScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        STSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double STSyncScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double STSyncScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    double STSyncScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        STSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] calculateTotalEnergyForTask: 任务模型不存在");
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

    double STSyncScheduler::calculateRemainingEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        STSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] calculateRemainingEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 获取 WCET
        Tick wcet = model->getWCET();

        // 从能量检查事件中获取已执行时间（如果任务正在运行）
        Tick executed = 0;

        Tick remaining = wcet - executed;

        if (remaining <= 0) {
            return 0.0;  // 任务已完成
        }

        std::string workload = model->getWorkloadType();

        // 从ConfigManager获取base_freq
        ConfigManager &configMgr = ConfigManager::getInstance();
        double base_frequency = configMgr.getBaseFrequency();  // MHz
        double power = calculatePowerForWorkload(workload, base_frequency);

        // 能量 = 功率(W) × 时间(s)
        double remaining_seconds = static_cast<double>(remaining) * 0.001;
        double energy = power * remaining_seconds;

        // 应用能量系数
        energy *= model->getEnergyCoefficient();

        SCHEDULER_LOG_DEBUG(std::string("🔋 [ST-Sync] 剩余能量计算: ") +
                           "task=" + getTaskName(task) +
                           " wcet=" + std::to_string(static_cast<int>(wcet)) +
                           " executed=" + std::to_string(static_cast<int>(executed)) +
                           " remaining=" + std::to_string(static_cast<int>(remaining)) +
                           " energy=" + std::to_string(energy * 1000) + " mJ");

        return energy;
    }

    double STSyncScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ST-Sync] 功率计算: ") +
                           "workload=" + workload +
                           " coeff=" + std::to_string(power_coeff) +
                           " freq=" + std::to_string(frequency_mhz) + "MHz" +
                           " freq_ratio=" + std::to_string(freq_ratio) +
                           " base_power=" + std::to_string(base_power) +
                           " → " + std::to_string(power) + "W");

        return power;
    }

    void STSyncScheduler::clampCurrentEnergyNonNegative(const std::string &context) {
        const double ENERGY_EPSILON = 1e-9;

        if (_current_energy < 0.0) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 负能量保护触发: ") +
                                 context +
                                 " energy=" + std::to_string(_current_energy * 1000.0) + " mJ");
            _current_energy = 0.0;
        } else if (_current_energy < ENERGY_EPSILON) {
            _current_energy = 0.0;
        }
    }

    std::vector<AbsRTTask *> STSyncScheduler::collectActiveRunningBatchTasks() {
        std::vector<AbsRTTask *> running_batch;

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return running_batch;
        }

        const auto &running_tasks = _kernel->getCurrentExecutingTasks();
        for (const auto &map_pair : running_tasks) {
            AbsRTTask *task = map_pair.second;
            if (!task || !task->isExecuting()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            running_batch.push_back(task);
        }

        return running_batch;
    }

    bool STSyncScheduler::isTaskInActiveRunningBatch(AbsRTTask *task) {
        if (!task) {
            return false;
        }

        if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
            return false;
        }

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return false;
        }

        CPU *cpu = _kernel->getProcessor(task);
        if (!cpu) {
            return false;
        }

        const auto &running_tasks = _kernel->getCurrentExecutingTasks();
        auto it = running_tasks.find(cpu);
        if (it == running_tasks.end() || it->second != task) {
            return false;
        }

        return task->isExecuting();
    }

    double STSyncScheduler::calculateBatchUnitEnergy(const std::vector<AbsRTTask *> &tasks) {
        double total_unit_energy = 0.0;
        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            total_unit_energy += calculateUnitEnergyForTask(task);
        }
        return total_unit_energy;
    }

    void STSyncScheduler::suspendBatchForInsufficientEnergy(const std::vector<AbsRTTask *> &tasks,
                                                            double required_energy,
                                                            const std::string &context) {
        Tick min_slack = std::numeric_limits<Tick::impl_t>::max();

        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack < min_slack) {
                min_slack = task_slack;
            }
        }

        if (min_slack == std::numeric_limits<Tick::impl_t>::max()) {
            min_slack = 0;
        }

        SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 批次原子挂起: ") +
                             context +
                             " tasks=" + std::to_string(tasks.size()) +
                             " need=" + std::to_string(required_energy * 1000.0) + " mJ" +
                             " current=" + std::to_string(_current_energy * 1000.0) + " mJ" +
                             " minSlack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");

        _deep_charging = true;
        _energy_depleted = true;
        _is_charging_sleep = true;
        clampCurrentEnergyNonNegative("suspendBatchForInsufficientEnergy");

        Tick wake_time = calculateGroupWakeTime(min_slack, required_energy);
        if (wake_time > SIMUL.getTime()) {
            scheduleGroupWakeEvent(wake_time);
        }

        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            addToWaitingQueue(task);
            if (_kernel && task->isExecuting()) {
                stopEnergyCheckForTask(task);
                setSuspendReason(task, "insufficient_energy");
                SCHEDULER_LOG_WARNING(std::string("🛑 [ST-Sync] 原子挂起任务: ") + getTaskName(task));
                _kernel->suspend(task);
            }
        }

        _current_batch_tasks.clear();
        _preempt_batch_tasks.clear();
        _current_batch_size = 0;
        _batch_scheduled_this_tick = false;
        _stats.total_batch_skipped++;
    }

    // 兼容旧调用点；当前 ST-Sync 不启动按任务运行时能量事件。

    void STSyncScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        (void)task;
        (void)cpu;
        return;
    }

    void STSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            _energy_check_events.erase(it);
        }
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double STSyncScheduler::collectSolarEnergy(Tick current_time) {
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

    double STSyncScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void STSyncScheduler::scheduleNextTick() {
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

    STSyncTaskModel *STSyncScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string STSyncScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *STSyncScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int STSyncScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *STSyncScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void STSyncScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ST-Sync] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;

        // ⭐ 启动能量检查事件（每 1ms 扣除能量）
        startEnergyCheckForTask(task, cpu);
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void STSyncScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ST-Sync] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void STSyncScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    // ⭐ V96修复：重���基类的setKernel方法，同时设置基类和派生类的_kernel成员
    void STSyncScheduler::setKernel(AbsKernel *kernel) {
        // 调用基类方法设置基类的_kernel
        Scheduler::setKernel(kernel);
        // 同时设置派生类的_kernel（转换为MRTKernel*）
        _kernel = dynamic_cast<MRTKernel*>(kernel);
        if (_kernel) {
            SCHEDULER_LOG_INFO("🏁 [ST-Sync] setKernel: _kernel设置成功");
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] setKernel: _kernel设置失败（kernel不是MRTKernel）");
        }
    }

    MRTKernel *STSyncScheduler::getKernel() {
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

    void STSyncScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ST-Sync] newRun - 仿真开始");

        // ⭐ 关键修复：在任务到达之前初始化_kernel
        // 这样performTickScheduling才能正确执行批量调度
        if (!_kernel) {
            _kernel = getKernel();
            if (_kernel) {
                SCHEDULER_LOG_INFO("🏁 [ST-Sync] newRun: _kernel初始化成功");
            } else {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] newRun: _kernel初始化失败");
            }
        }

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();

        _ready_queue.clear();
        _waiting_queue.clear();
        _deferred_arrivals.clear();
        _energy_accounts.clear();
        _running_tasks.clear();
        _deadline_miss_arrivals.clear();

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;
        _stats.total_batch_schedules = 0;
        _stats.total_batch_skipped = 0;

        // ST-Sync批量调度状态初始化
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _current_batch_tasks.clear();
        _preempt_batch_tasks.clear();
        _selection_tick = Tick(-1);
        _selection_generation = 0;
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_generation = 0;
        _energy_commit_valid = false;
        _dispatching_tasks_total_energy = 0.0;
        _counted_tasks_in_dispatch.clear();

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ST-Sync] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void STSyncScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ST-Sync] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ST-Sync] ===== ST-Sync批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void STSyncScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 任务结束: ") + getTaskName(task));

        // MRTKernel::onEnd() 已先调用 extract()；这里不要再次清理 ready/waiting，
        // 否则周期复用对象会在“新实例已到达、旧实例随后kill/end”时误删新实例状态。

        // ⭐ 逐渐扣除模式：从计数集合中移除任务
        _counted_tasks_in_dispatch.erase(task);
        _tasks_completed_wcet.erase(task);

        // ⭐ 停止能量检查事件
        stopEnergyCheckForTask(task);

        auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
        if (batch_it != _current_batch_tasks.end()) {
            _current_batch_tasks.erase(batch_it);
        }

        // 从就绪队列移除
        removeFromReadyQueue(task);

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
            SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ST-Sync 的组装与准入只在 tick 边界进行。
        // 任务中途结束后保持 CPU 空闲，等待下一个 tick 统一重建同步组。
        if (!_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("⏭️ [ST-Sync] 任务结束后不做 mid-tick dispatch，等待下一 tick 统一调度");
        }
    }

    bool STSyncScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 过期任务清理 - 清理超过截止期的旧任务实例
    // =====================================================

    void STSyncScheduler::cleanupExpiredTasks() {
        const Tick current_time = SIMUL.getTime();
        std::set<AbsRTTask *> jobs(_ready_queue.begin(), _ready_queue.end());
        jobs.insert(_waiting_queue.begin(), _waiting_queue.end());
        jobs.insert(_current_batch_tasks.begin(), _current_batch_tasks.end());
        if (_kernel) {
            for (const auto &[cpu, task] :
                 _kernel->getCurrentExecutingTasks()) {
                (void) cpu;
                if (task) jobs.insert(task);
            }
        }

        for (AbsRTTask *task : jobs) {
            if (!task || task->getRemainingWCET() <= 0.0 ||
                task->getDeadline() > current_time) {
                continue;
            }
            const Tick arrival = task->getArrival();
            auto recorded = _deadline_miss_arrivals.find(task);
            if (recorded != _deadline_miss_arrivals.end() &&
                recorded->second == arrival) {
                continue;
            }
            _deadline_miss_arrivals[task] = arrival;
            _stats.total_deadline_misses++;
            SCHEDULER_LOG_WARNING("⏰ [ST-Sync] deadline miss recorded; job remains eligible/waiting: " +
                                  getTaskName(task));
        }
    }

    // =====================================================
    // ALAP时序门控（阶段一）
    // =====================================================

    // ⭐ 新增：基于批次的 ALAP 时序门控（原论文正确实现）
    bool STSyncScheduler::checkALAPBatchTimingGate(const std::vector<AbsRTTask *> &batch) {
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
            SCHEDULER_LOG_INFO("⏸️  [ST-Sync] ALAP批次时序门控：S_batch > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，批次集体休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 批次集体休眠
        } else {
            SCHEDULER_LOG_INFO("✅ [ST-Sync] ALAP批次时序门控：S_batch ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，批次唤醒，允许调度");
            return true;  // 批次唤醒
        }
    }

    // ⭐ 保留原有的全局检查函数（供兼容性使用）
    bool STSyncScheduler::checkALAPTimingGate() {
        if (_ready_queue.empty()) {
            return true;  // 空队列，通过门控
        }

        Tick current_time = SIMUL.getTime();
        Tick min_slack = Tick(-1);

        // 计算所有就绪任务的Slack，找最小值
        for (AbsRTTask *task : _ready_queue) {
            if (!task) continue;

            Tick slack = calculateSlackForTask(task);

            if (min_slack < 0 || slack < min_slack) {
                min_slack = slack;
            }
        }

        // 门控逻辑
        if (min_slack > 0) {
            SCHEDULER_LOG_INFO("⏸️  [ST-Sync] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ST-Sync] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }

    MetaSim::Tick STSyncScheduler::calculateSlackForTask(AbsRTTask *task) {
        if (!task) return MetaSim::Tick(0);

        Tick current_time = SIMUL.getTime();
        Tick absolute_deadline = task->getDeadline();

        double remaining_double = task->getRemainingWCET();
        if (remaining_double < 0.0) {
            remaining_double = 0.0;
        }
        Tick remaining = Tick(static_cast<Tick::impl_t>(std::ceil(remaining_double)));
        Tick slack = absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_DEBUG("🧮 [ST-Sync] Slack计算: " +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining_double=" + std::to_string(remaining_double) +
                           " remaining_int=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " => slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        return slack;
    }

    // ⭐ ST特有：计算所有就绪任务的最小Slack
    MetaSim::Tick STSyncScheduler::calculateMinSlack() {
        // 注意: Tick是类，需要用impl_t获取最大值
        Tick min_slack = std::numeric_limits<Tick::impl_t>::max();

        // 充电窗口内优先只看等待中的同步组，避免组外任务改变当前窗口的唤醒时机。
        if (!_waiting_queue.empty()) {
            for (auto* task : _waiting_queue) {
                if (!task) continue;
                Tick slack = calculateSlackForTask(task);
                if (slack < min_slack) {
                    min_slack = slack;
                }
            }
        } else {
            for (auto* task : _ready_queue) {
                if (!task) continue;
                Tick slack = calculateSlackForTask(task);
                if (slack < min_slack) {
                    min_slack = slack;
                }
            }
        }

        // 如果没有任务，返回0
        if (min_slack == std::numeric_limits<Tick::impl_t>::max()) {
            min_slack = 0;
        }

        SCHEDULER_LOG_DEBUG("🧮 [ST-Sync] calculateMinSlack: min_slack=" +
                           std::to_string(static_cast<int64_t>(min_slack)) + "ms");
        return min_slack;
    }

    // ⭐ V89: 计算组唤醒时间
    // 唤醒条件：组Slack归零 或 电池充满（取较早者）
    MetaSim::Tick STSyncScheduler::calculateGroupWakeTime(MetaSim::Tick group_slack, double group_energy) {
        Tick current_time = SIMUL.getTime();

        // 计算充满电需要的时间。
        // ST-Sync 缺电后进入整组充电窗口，唤醒条件是组Slack归零或电池充满，
        // 不是“只要够下一拍就提前放行”。
        double energy_needed = _max_energy - _current_energy;
        if (energy_needed < 0) energy_needed = 0;

        double harvest_rate = _base_harvest_rate;
        int64_t charge_time_ms = (harvest_rate > 1e-12)
            ? static_cast<int64_t>(std::ceil(energy_needed / harvest_rate))
            : static_cast<int64_t>(group_slack);

        // 计算Slack归零时刻
        int64_t slack_deadline = static_cast<int64_t>(group_slack);

        // 唤醒时间 = min(Slack归零时刻, 充满电时刻)
        int64_t wake_offset = std::min(charge_time_ms, slack_deadline);
        if (wake_offset < 1) wake_offset = 1;  // 至少1ms后唤醒

        Tick wake_time = current_time + wake_offset;

        SCHEDULER_LOG_INFO(std::string("🧮 [ST-Sync V89] calculateGroupWakeTime: ") +
                          "组Slack=" + std::to_string(slack_deadline) + "ms" +
                          " 充电时间=" + std::to_string(charge_time_ms) + "ms" +
                          " 唤醒偏移=" + std::to_string(wake_offset) + "ms");

        return wake_time;
    }

    // ⭐ V122: 设置组唤醒定时器 - 真正注册事件到模拟器
    void STSyncScheduler::scheduleGroupWakeEvent(MetaSim::Tick wake_time) {
        // 只在没有定时器或新唤醒时间更早时更新，避免重复无效重建
        bool should_update = false;
        if (!_group_wake_event || !_group_wake_event->isValid()) {
            should_update = true;
        } else {
            const int64_t existing_wake_time = static_cast<int64_t>(_group_wake_event->getWakeTime());
            const int64_t new_wake_time = static_cast<int64_t>(wake_time);
            if (new_wake_time < existing_wake_time) {
                should_update = true;
                SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync] 发现更早的组唤醒时间: ") +
                                  "现有=" + std::to_string(existing_wake_time) + "ms " +
                                  "新=" + std::to_string(new_wake_time) + "ms，更新");
            }
        }

        if (!should_update) {
            SCHEDULER_LOG_DEBUG(std::string("⏰ [ST-Sync] 保留现有组唤醒定时器: ") +
                               "现有=" + std::to_string(static_cast<int64_t>(_group_wake_event->getWakeTime())) + "ms " +
                               "新=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
            return;
        }

        if (_group_wake_event) {
            _group_wake_event->invalidate();
        }

        _group_wake_event = new STSyncGroupWakeEvent(this);
        _group_wake_event->schedule(wake_time);

        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync V122] 组唤醒定时器已注册: ") +
                          "唤醒时间=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void STSyncScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ST-Sync] ===== ST-Sync批量调度统计 =====");
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

    std::string STSyncScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> STSyncScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void STSyncScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ST-Sync] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const std::vector<AbsRTTask *> running_batch = collectActiveRunningBatchTasks();
        if (running_batch.empty()) {
            return;
        }

        const double required_energy = calculateBatchUnitEnergy(running_batch);
        const double EPSILON = 1e-9;

        if (_current_energy + EPSILON < required_energy) {
            suspendBatchForInsufficientEnergy(running_batch,
                                             required_energy,
                                             std::string("checkAndInterruptRunningTasks @ ") +
                                                 std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms");
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 运行批次下一毫秒能量充足: ") +
                           "tasks=" + std::to_string(running_batch.size()) +
                           " need=" + std::to_string(required_energy * 1000.0) + " mJ" +
                           " current=" + std::to_string(_current_energy * 1000.0) + " mJ");
    }
} // namespace RTSim
