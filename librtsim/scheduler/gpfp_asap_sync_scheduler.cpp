// gpfp_asap_sync_scheduler.cpp - ASAP-Sync (As Soon As Possible Sync) Scheduler Implementation
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
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_asap_sync_scheduler.hpp>
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
    // ASAPSyncTickEvent 实现
    // =====================================================

    ASAPSyncTickEvent::ASAPSyncTickEvent(ASAPSyncScheduler *scheduler)
        : MetaSim::Event("ASAPSyncTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行
    }

    void ASAPSyncTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ASAP-Sync] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ASAPSyncEnergyCheckEvent 实现 - 运行时能量检查
    // =====================================================

    ASAPSyncEnergyCheckEvent::ASAPSyncEnergyCheckEvent(ASAPSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("ASAPSyncEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void ASAPSyncEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        Tick actual_trigger_time = SIMUL.getTime();
        SCHEDULER_LOG_INFO(std::string("🔍 [ASAP-Sync] 能量检查事件触发: ") +
                           _scheduler->getTaskName(_task) +
                           " 触发时间=" + std::to_string(static_cast<int64_t>(actual_trigger_time)) + "ms" +
                           " _ms_executed=" + std::to_string(_ms_executed));

        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-Sync] 能量检查：任务已删除，停止检查"));
            return;
        }

        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-Sync] 能量检查：事件已失效，停止检查"));
            return;
        }

        if (!_task->isExecuting()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-Sync] 能量检查：任务已停止执行，不再续期检查: ") +
                               _scheduler->getTaskName(_task) + " 时间=" +
                               std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            return;
        }

        _ms_executed++;

        ASAPSyncTaskModel *task_model = _scheduler->getTaskModel(_task);
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ASAP-Sync] WCET检查: ") +
                           task_name + " 已执行=" + std::to_string(_ms_executed) +
                           "ms task_model=" + (task_model ? "有效" : "NULL"));

        if (task_model) {
            int wcet = task_model->getWCET();
            if (_ms_executed >= wcet) {
                SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Sync] 任务已达到WCET，完成执行: ") +
                                   task_name + " 已执行=" + std::to_string(_ms_executed) +
                                   "ms WCET=" + std::to_string(wcet) + "ms");
                _scheduler->_tasks_completed_wcet.insert(_task);
                SCHEDULER_LOG_INFO(std::string("🏁 [ASAP-Sync] 标记任务已完成WCET: ") + task_name);
                return;
            }
        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Sync] WCET检查失败：找不到TaskModel ") + task_name);
        }

        // ASAP-Sync 的组级能量续期由 performTickScheduling() 在每个 1ms tick 统一处理。
        // 这里不再执行按任务的能量判断或单独挂起，避免破坏同生共死原子性。
        post(SIMUL.getTime() + 1);
    }

    // =====================================================
    // ASAPSyncTaskModel 实现
    // =====================================================

    ASAPSyncTaskModel::ASAPSyncTaskModel(AbsRTTask *t, int period, int wcet,
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

    ASAPSyncTaskModel::~ASAPSyncTaskModel() {}

    Tick ASAPSyncTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ASAPSyncTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ASAPSyncTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ASAPSyncScheduler 实现
    // =====================================================

    ASAPSyncScheduler::ASAPSyncScheduler()
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
          _current_batch_size(0) {

        SCHEDULER_LOG_INFO("🚀 [ASAP-Sync] ASAP Sync Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ASAP-Sync] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ASAP-Sync] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ASAP-Sync] 开始时间偏移: ") +
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
                                SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-Sync] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-Sync] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²" +
                                      " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Sync] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Sync] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许初始能量为0
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Sync] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ASAP-Sync] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ASAPSyncTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ASAP-Sync] ASAP Sync Scheduler 初始化完成");
    }

    ASAPSyncScheduler::ASAPSyncScheduler(const std::vector<std::string> &params)
        : ASAPSyncScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ASAPSyncScheduler>
        ASAPSyncScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ASAPSyncScheduler>(params);
    }

    ASAPSyncScheduler::~ASAPSyncScheduler() {
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
    // BTIE批量调度辅助方法
    // =====================================================

    int ASAPSyncScheduler::calculateBatchSize() {
        // k = min(CPU核心总数, 就绪队列任务数)
        ConfigManager &configMgr = ConfigManager::getInstance();
        int total_cpus = configMgr.getNumCores();
        int ready_tasks = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(total_cpus, ready_tasks);

        SCHEDULER_LOG_DEBUG(std::string("📊 [ASAP-Sync] calculateBatchSize: ") +
                           "CPU核心数=" + std::to_string(total_cpus) +
                           " 就绪任务=" + std::to_string(ready_tasks) +
                           " 批量k=" + std::to_string(batch_size));

        return batch_size;
    }


    void ASAPSyncScheduler::executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy) {
        (void)tasks;
        (void)total_energy;
    }

    // =====================================================
    // 核心调度逻辑 - BTIE批量调度算法
    // =====================================================

    void ASAPSyncScheduler::performTickScheduling() {
        SCHEDULER_LOG_DEBUG(std::string("🔄 [ASAP-Sync] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");

        _stats.total_tick_count++;

        Tick current_time = SIMUL.getTime();
        Tick elapsed = current_time - _last_tick_time;

        if (_batch_scheduled_this_tick && !_current_batch_tasks.empty()) {
            SCHEDULER_LOG_DEBUG(std::string("🛡️ [ASAP-Sync] 保留arrival-time已批准批次，跳过tick重算 @ ") +
                               std::to_string(static_cast<int64_t>(current_time)) + "ms");
            _last_tick_time = current_time;
            return;
        }

        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-Sync] Tick边界收集能量: ") +
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
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] _kernel为nullptr，跳过批量调度");
                return;
            }
        }

        const double EPSILON = 1e-9;
        const auto &running_tasks = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> running_task_list;
        double running_batch_energy = 0.0;

        for (const auto &[cpu, task] : running_tasks) {
            if (!task || !task->isExecuting()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            running_task_list.push_back(task);
            running_batch_energy += calculateUnitEnergyForTask(task);
        }

        if (!running_task_list.empty()) {
            if (_current_energy < running_batch_energy - EPSILON) {
                _batch_scheduled_this_tick = false;
                _current_batch_tasks.clear();
                _current_batch_size = 0;
                _stats.total_batch_skipped++;
                _energy_depleted = true;

                SCHEDULER_LOG_WARNING(std::string("❌ [ASAP-Sync] 运行批次续期失败，执行同死挂起: ") +
                                      "批次数=" + std::to_string(running_task_list.size()) +
                                      " 需要=" + std::to_string(running_batch_energy * 1000) + " mJ" +
                                      " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

                for (auto *task : running_task_list) {
                    if (task && task->isExecuting()) {
                        setSuspendReason(task, "insufficient_energy");
                        _kernel->suspend(task);
                    }
                }
                return;
            }

            _current_energy -= running_batch_energy;
            _stats.total_energy_consumed += running_batch_energy;
            _energy_depleted = false;

            SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 运行批次续期成功: ") +
                               "批次数=" + std::to_string(running_task_list.size()) +
                               " 扣减=" + std::to_string(running_batch_energy * 1000) + " mJ" +
                               " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
        } else {
            _energy_depleted = (_current_energy < EPSILON);
        }

        _current_batch_tasks.clear();
        _current_batch_size = 0;

        // ASAP-Sync 保持 1ms tick 边界准入，不在 tick 内做额外抢占，避免破坏批次原子性。

        size_t running_count = running_task_list.size();
        size_t total_cpus = running_tasks.size();
        size_t free_cpus = total_cpus > running_count ? total_cpus - running_count : 0;

        std::vector<AbsRTTask *> new_tasks_to_schedule;
        if (free_cpus > 0 && !_ready_queue.empty()) {
            _ready_queue.erase(
                std::remove_if(_ready_queue.begin(), _ready_queue.end(),
                    [this, current_time](AbsRTTask *task) {
                        if (!task) return true;
                        if (!task->isActive()) {
                            return true;
                        }
                        return task->getDeadline() < current_time;
                    }),
                _ready_queue.end()
            );

            std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
            std::sort(sorted_ready.begin(), sorted_ready.end(),
                [this](AbsRTTask *a, AbsRTTask *b) {
                    auto model_a = getTaskModel(a);
                    auto model_b = getTaskModel(b);
                    if (model_a && model_b) {
                        return model_a->getRMPriority() < model_b->getRMPriority();
                    }
                    return false;
                });

            for (auto *task : sorted_ready) {
                if (!task || !task->isActive()) {
                    continue;
                }

                bool is_running = false;
                for (auto *running_task : running_task_list) {
                    if (running_task == task) {
                        is_running = true;
                        break;
                    }
                }
                if (is_running) {
                    continue;
                }

                new_tasks_to_schedule.push_back(task);
                if (new_tasks_to_schedule.size() >= free_cpus) {
                    break;
                }
            }
        }

        double new_batch_energy = 0.0;
        for (auto *task : new_tasks_to_schedule) {
            new_batch_energy += calculateUnitEnergyForTask(task);
        }

        SCHEDULER_LOG_INFO(std::string("📊 [ASAP-Sync] Tick批量决策: ") +
                           "总CPU=" + std::to_string(total_cpus) +
                           " 运行中=" + std::to_string(running_count) +
                           " 空闲=" + std::to_string(free_cpus) +
                           " 新批次数=" + std::to_string(new_tasks_to_schedule.size()) +
                           " 新批次门票=" + std::to_string(new_batch_energy * 1000) + " mJ" +
                           " 当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

        if (new_tasks_to_schedule.empty()) {
            _batch_scheduled_this_tick = false;
            return;
        }

        if (_current_energy < new_batch_energy - EPSILON) {
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _stats.total_batch_skipped++;
            _energy_depleted = true;

            SCHEDULER_LOG_WARNING(std::string("❌ [ASAP-Sync] 派发阶段门票不足，拒绝整个新批次: ") +
                                  "批次数=" + std::to_string(new_tasks_to_schedule.size()) +
                                  " 需要=" + std::to_string(new_batch_energy * 1000) + " mJ" +
                                  " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        _batch_scheduled_this_tick = true;
        _current_batch_tasks = new_tasks_to_schedule;
        _current_batch_size = static_cast<int>(new_tasks_to_schedule.size());
        _stats.total_batch_schedules++;
        _energy_depleted = false;

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Sync] 新批次通过原子门票检查: ") +
                           "批次数=" + std::to_string(_current_batch_size) +
                           " 门票=" + std::to_string(new_batch_energy * 1000) + " mJ" +
                           " 当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

        int dispatch_attempts = 0;
        const int MAX_DISPATCH_ITERATIONS = 100;
        while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
            size_t tasks_before = _ready_queue.size();
            _kernel->dispatch();

            dispatch_attempts++;

            size_t tasks_after = _ready_queue.size();
            if (tasks_before == tasks_after) {
                break;
            }
        }

        if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] dispatch循环达到最大迭代次数，可能存在bug");
        }
    }

    void ASAPSyncScheduler::schedule() {
        // BTIE依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ASAP-Sync] schedule() 被调用");
    }

    bool ASAPSyncScheduler::shouldDispatchAtTickBoundary() const {
        return _batch_scheduled_this_tick;
    }

    // =====================================================
    // getFirst - BTIE废弃，返回nullptr
    // =====================================================

    AbsRTTask *ASAPSyncScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ASAP-Sync] getFirst() 被调用"));
        return getTaskN(0);
    }

    // =====================================================
    // getTaskN - 返回批量中的第n个任务
    // =====================================================

    AbsRTTask *ASAPSyncScheduler::getTaskN(unsigned int n) {
        SCHEDULER_LOG_INFO(std::string("🔍 [ASAP-Sync] getTaskN(") + std::to_string(n) + ") " +
                           "当前能量: " + std::to_string(_current_energy) + "J" +
                           " 批量任务数=" + std::to_string(_current_batch_tasks.size()));

        if (_current_batch_tasks.empty()) {
            SCHEDULER_LOG_INFO("📭 [ASAP-Sync] getTaskN: 当前tick没有可派发批次");
            return nullptr;
        }

        if (n >= _current_batch_tasks.size()) {
            SCHEDULER_LOG_DEBUG(std::string("📭 [ASAP-Sync] getTaskN: 索引超出范围") +
                               " n=" + std::to_string(n) +
                               " size=" + std::to_string(_current_batch_tasks.size()));
            return nullptr;
        }

        AbsRTTask *task = _current_batch_tasks[n];
        if (!task || !task->isActive()) {
            SCHEDULER_LOG_DEBUG("📭 [ASAP-Sync] getTaskN: 候选任务无效或已失活");
            return nullptr;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-Sync] getTaskN(") + std::to_string(n) + ") 返回: " +
                          getTaskName(task) + " (批量任务[" + std::to_string(n) + "] / " +
                          std::to_string(_current_batch_tasks.size()) + ")");
        return task;
    }

    // =====================================================
    // notify - BTIE不再扣减能量（已在批量时扣减）
    // =====================================================

    void ASAPSyncScheduler::notify(AbsRTTask *task) {
        Scheduler::notify(task);

        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔔 [ASAP-Sync] notify: 任务进入执行态（不再做二次能量门槛）: ") +
                          getTaskName(task));

        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ASAP-Sync] notify: 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ASAPSyncScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] addTask: 任务为空");
            return;
        }

        // ⭐ Bug修复：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [ASAP-Sync] 能量已耗尽，拒绝添加新任务: ") +
                                         getTaskName(task));
            return;  // 拒绝任务
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ASAP-Sync] 添加任务: ") + getTaskName(task));
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

        // 创建任务模型
        ASAPSyncTaskModel *model = new ASAPSyncTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Sync] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ASAPSyncScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-Sync] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        stopEnergyCheckForTask(task);
        _tasks_completed_wcet.erase(task);
        _current_batch_tasks.erase(std::remove(_current_batch_tasks.begin(), _current_batch_tasks.end(), task), _current_batch_tasks.end());
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());

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

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Sync] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ASAPSyncScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ASAP-Sync] 任务到达: ") + getTaskName(task));

        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ASAP-Sync] 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
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

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 到达任务立即参与RM抢占检查: ") + getTaskName(task));
        checkAndPreempt();
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void ASAPSyncScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ASAP-Sync] arrival-time RM抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    std::vector<AbsRTTask *> ASAPSyncScheduler::collectActiveRunningBatchTasks() {
        std::vector<AbsRTTask *> running_batch;
        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return running_batch;
        }

        for (AbsRTTask *task : _current_batch_tasks) {
            if (!task || !task->isActive()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            if (_kernel->getProcessor(task) == nullptr) {
                continue;
            }
            running_batch.push_back(task);
        }
        return running_batch;
    }

    void ASAPSyncScheduler::rebuildApprovedBatchForImmediateDispatch() {
        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return;
        }

        std::vector<AbsRTTask *> approved_batch = collectActiveRunningBatchTasks();
        size_t target_size = _kernel->getCurrentExecutingTasks().size();

        for (const auto &entry : _kernel->getCurrentExecutingTasks()) {
            if (entry.second == nullptr && !_kernel->isCPUDispatching(entry.first)) {
                target_size += 1;
            }
        }

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
                ASAPSyncTaskModel *model_a = getTaskModel(a);
                ASAPSyncTaskModel *model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });

        double batch_unit_energy = 0.0;
        for (AbsRTTask *task : approved_batch) {
            batch_unit_energy += calculateUnitEnergyForTask(task);
        }

        const double EPSILON = 1e-9;
        if (!approved_batch.empty() && _current_energy + EPSILON < batch_unit_energy) {
            SCHEDULER_LOG_WARNING(std::string("❌ [ASAP-Sync] arrival-time重建批次失败：组门票不足") +
                                 " size=" + std::to_string(approved_batch.size()) +
                                 " 需要=" + std::to_string(batch_unit_energy * 1000) + " mJ" +
                                 " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _batch_scheduled_this_tick = false;
            return;
        }

        _current_batch_tasks = approved_batch;
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());
        _batch_scheduled_this_tick = !_current_batch_tasks.empty();

        SCHEDULER_LOG_INFO(std::string("🧩 [ASAP-Sync] 立即重建稳定批次: size=") +
                          std::to_string(_current_batch_tasks.size()) +
                          " 门票=" + std::to_string(batch_unit_energy * 1000) + " mJ");
    }

    void ASAPSyncScheduler::checkAndPreemptOnAllCPUs() {
        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return;
        }

        std::vector<CPU *> truly_free_cpus;
        std::vector<std::pair<CPU *, AbsRTTask *>> running_entries;

        for (const auto &entry : _kernel->getCurrentExecutingTasks()) {
            CPU *cpu = entry.first;
            AbsRTTask *running = entry.second;
            if (running != nullptr) {
                running_entries.push_back(entry);
                continue;
            }
            if (_kernel->isCPUDispatching(cpu)) {
                continue;
            }
            truly_free_cpus.push_back(cpu);
        }

        if (!truly_free_cpus.empty()) {
            rebuildApprovedBatchForImmediateDispatch();
            SCHEDULER_LOG_INFO(std::string("⏭️ [ASAP-Sync] 有") + std::to_string(truly_free_cpus.size()) +
                              "个空闲CPU，arrival-time立即重建稳定批次");
            _kernel->dispatch();
            return;
        }

        AbsRTTask *best_candidate = nullptr;
        for (AbsRTTask *task : _ready_queue) {
            if (!task || !task->isActive()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            if (_kernel->getProcessor(task) != nullptr) {
                continue;
            }
            best_candidate = task;
            break;
        }

        if (!best_candidate) {
            return;
        }

        CPU *worst_cpu = nullptr;
        AbsRTTask *worst_running = nullptr;
        ASAPSyncTaskModel *worst_model = nullptr;
        for (const auto &entry : running_entries) {
            AbsRTTask *running = entry.second;
            if (!running || !running->isActive()) {
                continue;
            }
            ASAPSyncTaskModel *running_model = getTaskModel(running);
            if (!running_model) {
                continue;
            }
            if (!worst_model || running_model->getRMPriority() > worst_model->getRMPriority()) {
                worst_cpu = entry.first;
                worst_running = running;
                worst_model = running_model;
            }
        }

        if (!worst_cpu || !worst_running) {
            return;
        }

        if (!shouldPreempt(worst_running, best_candidate)) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 触发arrival-time实时抢占: ") +
                          getTaskName(best_candidate) + " 抢占 " + getTaskName(worst_running) +
                          " @ " + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");

        setSuspendReason(worst_running, "preemption");
        _kernel->suspend(worst_running);
        clearSuspendReason(worst_running);
        rebuildApprovedBatchForImmediateDispatch();
        _kernel->dispatch();
    }

    bool ASAPSyncScheduler::shouldPreempt(AbsRTTask *running_task, AbsRTTask *new_task) {
        if (!running_task || !new_task) {
            return false;
        }

        ASAPSyncTaskModel *running_model = getTaskModel(running_task);
        ASAPSyncTaskModel *new_model = getTaskModel(new_task);
        if (!running_model || !new_model) {
            return false;
        }

        if (new_model->getRMPriority() >= running_model->getRMPriority()) {
            return false;
        }

        std::vector<AbsRTTask *> rebuilt_batch = collectActiveRunningBatchTasks();
        rebuilt_batch.erase(std::remove(rebuilt_batch.begin(), rebuilt_batch.end(), running_task), rebuilt_batch.end());
        if (std::find(rebuilt_batch.begin(), rebuilt_batch.end(), new_task) == rebuilt_batch.end()) {
            rebuilt_batch.push_back(new_task);
        }

        std::sort(rebuilt_batch.begin(), rebuilt_batch.end(),
            [this](AbsRTTask *a, AbsRTTask *b) {
                ASAPSyncTaskModel *model_a = getTaskModel(a);
                ASAPSyncTaskModel *model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });

        double rebuilt_batch_energy = 0.0;
        for (AbsRTTask *task : rebuilt_batch) {
            rebuilt_batch_energy += calculateUnitEnergyForTask(task);
        }

        const double EPSILON = 1e-9;
        if (_current_energy + EPSILON < rebuilt_batch_energy) {
            SCHEDULER_LOG_INFO(std::string("🚫 [ASAP-Sync] arrival-time抢占被拒绝：重组批次门票不足") +
                              " 候选=" + getTaskName(new_task) +
                              " 需要=" + std::to_string(rebuilt_batch_energy * 1000) + " mJ" +
                              " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
            return false;
        }

        return true;
    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void ASAPSyncScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ASAP-Sync] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);

        // ASAP-Sync 只在 1ms tick 边界做准入决策。
        // 新到达任务进入 ready_queue，等待下一个 tick 的整批原子门票检查。
    }

    void ASAPSyncScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ASAP-Sync] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        stopEnergyCheckForTask(task);
        _tasks_completed_wcet.erase(task);
        _current_batch_tasks.erase(std::remove(_current_batch_tasks.begin(), _current_batch_tasks.end(), task), _current_batch_tasks.end());
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());
    }

    void ASAPSyncScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-Sync] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ASAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ASAPSyncTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ASAP-Sync] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void ASAPSyncScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ASAP-Sync] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ASAPSyncScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ASAP-Sync] 任务加入等待队列: ") + getTaskName(task));
    }

    void ASAPSyncScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ASAPSyncScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ASAPSyncScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *ASAPSyncScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ASAPSyncScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        ASAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double ASAPSyncScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ASAPSyncScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void ASAPSyncScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string ASAPSyncScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void ASAPSyncScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    double ASAPSyncScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ASAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] calculateTotalEnergyForTask: 任务模型不存在");
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

    double ASAPSyncScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ASAP-Sync] 功率计算: ") +
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

    void ASAPSyncScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ASAP-Sync] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        ASAPSyncEnergyCheckEvent *evt = new ASAPSyncEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        Tick current_time = SIMUL.getTime();
        Tick scheduled_time = current_time + 1;
        evt->post(scheduled_time);

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString() +
                           " 当前时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                           " 调度时间=" + std::to_string(static_cast<int64_t>(scheduled_time)) + "ms");
    }

    void ASAPSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Sync] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ASAPSyncScheduler::collectSolarEnergy(Tick current_time) {
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

    double ASAPSyncScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Sync] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Sync] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ASAPSyncScheduler::scheduleNextTick() {
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

    ASAPSyncTaskModel *ASAPSyncScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ASAPSyncScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *ASAPSyncScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int ASAPSyncScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *ASAPSyncScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void ASAPSyncScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-Sync] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ASAPSyncScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ASAP-Sync] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ASAPSyncScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ASAPSyncScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *ASAPSyncScheduler::getKernel() {
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

    void ASAPSyncScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-Sync] newRun - 仿真开始");

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();

        _ready_queue.clear();
        _waiting_queue.clear();
        _energy_accounts.clear();
        _running_tasks.clear();
        _energy_check_events.clear();
        _tasks_completed_wcet.clear();

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;
        _stats.total_batch_schedules = 0;
        _stats.total_batch_skipped = 0;

        // BTIE批量调度状态初始化
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _current_batch_tasks.clear();

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Sync] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ASAPSyncScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-Sync] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ASAP-Sync] ===== BTIE批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ASAPSyncScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Sync] 任务结束: ") + getTaskName(task));

        stopEnergyCheckForTask(task);
        _tasks_completed_wcet.erase(task);
        _current_batch_tasks.erase(std::remove(_current_batch_tasks.begin(), _current_batch_tasks.end(), task), _current_batch_tasks.end());
        _current_batch_size = static_cast<int>(_current_batch_tasks.size());

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
            SCHEDULER_LOG_INFO(std::string("📊 [ASAP-Sync] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ASAP-Sync] 当前能量: ") + std::to_string(_current_energy) + "J");
    }

    bool ASAPSyncScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void ASAPSyncScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ASAP-Sync] ===== BTIE批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ASAPSyncScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ASAPSyncScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void ASAPSyncScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ASAP-Sync] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ASAP-Sync] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
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
                SCHEDULER_LOG_DEBUG(std::string("✅ [ASAP-Sync] 运行任务续期能量充足: ") +
                                   "需要=" + std::to_string(total_energy_to_deduct * 1000) + " mJ " +
                                   "当前=" + std::to_string(_current_energy * 1000) + " mJ " +
                                   "(能量已在批量调度中扣除)");
            } else {
                // ❌ 能量不足，中断所有运行中的任务
                SCHEDULER_LOG_WARNING(std::string("❌ [ASAP-Sync] 运行任务续期能量不足，将中断所有运行任务: ") +
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

                SCHEDULER_LOG_INFO(std::string("💀 [ASAP-Sync] 能量已耗尽，将中断") +
                                   std::to_string(tasks_to_interrupt.size()) + "个运行任务");
            }
        }

        // 2. 检查所有运行中的任务（细粒度监控）
        // ⭐ Bug #9修复v2：如果当前tick有任务在运行，不中断它们
        // BTIE的核心原则：要么全不调度要么全部调度
        // - 如果有任务在运行：让它们继续运行到下一个tick
        // - 如果没有任务在运行：检查能量是否足够调度新任务
        bool has_running_tasks = !running_tasks.empty();
        if (has_running_tasks) {
            SCHEDULER_LOG_DEBUG(std::string("✅ [ASAP-Sync] 当前tick有") +
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
                    SCHEDULER_LOG_WARNING(std::string("⚡ [ASAP-Sync] 任务能量不足，将中断: ") +
                                         getTaskName(task) +
                                         " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                         " 当前能量=" + std::to_string(_current_energy) + "J");

                    tasks_to_interrupt.push_back(task);
                    _stats.total_skipped_energy++;
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("✅ [ASAP-Sync] 任务能量充足: ") +
                                       getTaskName(task) +
                                       " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                       " 当前能量=" + std::to_string(_current_energy) + "J");
                }
            }
        }

        // 2. ⭐ BTIE"全无"原则：能量不足时，不调度任何新任务
        // 注意：当前正在运行的任务会继续执行，但由于：
        //   - _energy_depleted = true
        //   - _current_batch_tasks已清空（在批量调度的else分支中）
        //   - getTaskN()会返回nullptr
        // 所以不会有任何新任务被调度，当前任务完成后就会停止
        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("💀 [ASAP-Sync] 能量已耗尽，") +
                               std::to_string(tasks_to_interrupt.size()) + "个任务将自然完成" +
                               "（不再调度新任务，遵循BTIE'全无'原则）");
        }
    }
} // namespace RTSim
