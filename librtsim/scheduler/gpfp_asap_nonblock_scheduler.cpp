// gpfp_asap_nonblock_scheduler.cpp - ASAP-NonBlock (As Soon As Possible NonBlock) Scheduler Implementation
// 算法特点：
// 1. 基于当前实际能量进行即时判断（无前瞻性预测）
// 2. 每ms逐次扣减能耗
// 3. 级联调度：能量不足跳过，继续检查次优先级任务（贪婪策略）
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
#include <rtsim/scheduler/gpfp_asap_nonblock_scheduler.hpp>
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
    // ASAPNonBlockTickEvent 实现
    // =====================================================

    ASAPNonBlockTickEvent::ASAPNonBlockTickEvent(ASAPNonBlockScheduler *scheduler)
        : MetaSim::Event("ASAPNonBlockTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行
    }

    void ASAPNonBlockTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ASAP-NonBlock] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ASAPNonBlockEnergyCheckEvent 实现 - 运行时能量检查
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling���理
    // =====================================================

    /*
    ASAPNonBlockEnergyCheckEvent::ASAPNonBlockEnergyCheckEvent(ASAPNonBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("ASAPNonBlockEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void ASAPNonBlockEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-NonBlock] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-NonBlock] 能量检查：事件已失效，停止检查"));
            return;
        }

        // 计算任务每1ms的能耗
        double unit_energy = _scheduler->calculateUnitEnergyForTask(_task);
        double current_energy = _scheduler->getCurrentEnergy();
        const double EPSILON = 1e-9;

        _ms_executed++;

        // ⭐ 检查任务是否仍在执行状态
        // 如果任务已被中断（suspend），则不应再扣除能量
        if (!_task->isExecuting()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-NonBlock] 能量检查：任务已停止执行，不再扣除能量: ") +
                               _scheduler->getTaskName(_task) + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // ⭐ 关键修复：清理能量检查事件映射，允许后续实例启动新的检查
            _scheduler->_energy_check_events.erase(_task);
            // ⭐ 关键修复：清理能量检查事件映射
            _scheduler->_energy_check_events.erase(_task);
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        ASAPNonBlockTaskModel *task_model = _scheduler->getTaskModel(_task);
        if (task_model && _ms_executed >= task_model->getWCET()) {
            SCHEDULER_LOG_INFO(std::string("✅ [ASAP-NonBlock] 任务已达到WCET，完成执行: ") +
                               _scheduler->getTaskName(_task) + " 已执行=" + std::to_string(_ms_executed) +
                               "ms WCET=" + std::to_string(task_model->getWCET()) + "ms");
                // ⭐ 关键修复：从_energy_check_events中移除，允许后续实例启动新的能量检查
                _scheduler->_energy_check_events.erase(_task);
            // ⭐ 关键修复：清理能量检查事件映射，允许后续实例启动新的检查
            _scheduler->_energy_check_events.erase(_task);
            // 任务已完成，不续期能量，也不重新调度事件
            // 任务会由正常的调度流程完成
            return;
        }

        // ⭐ V29.1修复：恢复运行中任务的续期能量扣除
        // 设计原则：
        // - getTaskN(): 负责新任务的首次能量扣除
        // - ASAPNonBlockEnergyCheckEvent: 负责运行中任务的续期能量扣除
        
        // 检查是否有足够能量续期1ms
        // ⭐ V35修复：当能量 <= 1ms能耗时，立即中断任务
        // 避免在能量恰好等于单位能耗时继续执行，导致下个Tick能量为负
        if (current_energy <= unit_energy + EPSILON) {
            // ⭐ 能量不足以支撑下一个1ms，立即中断任务（不扣除能量）
            SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 能量刚好耗尽或不足，立即中断任务: ") +
                               _scheduler->getTaskName(_task) + " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                               " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                               " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;

            // ⭐ V37关键修复：将剩余能量强制设为0
            // 当current_energy == unit_energy时（如0.6 mJ == 0.6 mJ），
            // 条件current_energy <= unit_energy为TRUE，任务被挂起但不扣除能量
            // 这导致剩余了unit_energy的能量，performTickScheduling的检查current_energy < 0.000001失败
            // 解决方案：强制将能量设为0，确保能量耗尽检查正确工作
            _scheduler->_current_energy = 0.0;

            // 中断当前任务
            if (_cpu) {
                _scheduler->setSuspendReason(_task, "insufficient_energy");
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_INFO(std::string("⚠️ [ASAP-NonBlock] 任务因能量不足被挂起: ") + _scheduler->getTaskName(_task));
            }

            // ⭐ 关键修复：清理能量检查事件映射，允许后续实例启动新的检查
            _scheduler->_energy_check_events.erase(_task);

            // ⭐ 历史实现说明：旧版本会在这里清理一次性记账状态
            // 现版本已改为纯tick边界扣费 + blocked黑名单，不再需要该状态清理

            // 不重新调度事件
            return;
        }

        // 能量充足（扣除后仍有剩余），扣除续期能量
        double old_energy = current_energy;
        _scheduler->_current_energy -= unit_energy;
        _scheduler->_stats.total_energy_consumed += unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 运行中任务续期: ") +
                           _scheduler->getTaskName(_task) + " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " " + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }
    */  // ⭐ V40重构：ASAPNonBlockEnergyCheckEvent已删除

    // =====================================================
    // ASAPNonBlockTaskModel 实现
    // =====================================================

    ASAPNonBlockTaskModel::ASAPNonBlockTaskModel(AbsRTTask *t, int period, int wcet,
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

    ASAPNonBlockTaskModel::~ASAPNonBlockTaskModel() {}

    Tick ASAPNonBlockTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ASAPNonBlockTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ASAPNonBlockTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ASAPNonBlockScheduler 实现
    // =====================================================

    ASAPNonBlockScheduler::ASAPNonBlockScheduler()
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
          _first_tick_scheduled(false),
          _kernel(nullptr),
          _selection_tick(-1),
          _selection_generation(0),
          _selection_frozen(false),
          _energy_commit_tick(-1),
          _energy_commit_generation(0),
          _energy_commit_valid(false),
          _energy_depleted(false) {

        SCHEDULER_LOG_INFO("🚀 [ASAP-NonBlock] ASAP NonBlock Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ASAP-NonBlock] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ASAP-NonBlock] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ASAP-NonBlock] 开始时间偏移: ") +
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
                                SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-NonBlock] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-NonBlock] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²" +
                                      " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-NonBlock] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy > 0) {
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-NonBlock] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy > 0) {
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-NonBlock] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ASAP-NonBlock] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ASAPNonBlockTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ASAP-NonBlock] ASAP NonBlock Scheduler 初始化完成");
    }

    ASAPNonBlockScheduler::ASAPNonBlockScheduler(const std::vector<std::string> &params)
        : ASAPNonBlockScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ASAPNonBlockScheduler>
        ASAPNonBlockScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ASAPNonBlockScheduler>(params);
    }

    ASAPNonBlockScheduler::~ASAPNonBlockScheduler() {
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

    std::vector<AbsRTTask *> ASAPNonBlockScheduler::collectActiveJobs(Tick current_time) {
        std::vector<AbsRTTask *> active_tasks;
        auto add_active = [this, &active_tasks, current_time](AbsRTTask *task, bool running) {
            if (!task || task->getArrival() > current_time) {
                return;
            }
            if (!running && !task->isActive()) {
                return;
            }
            if (task->getRemainingWCET() <= 0.0) {
                return;
            }
            if (_energy_blocked_tasks.find(task) != _energy_blocked_tasks.end()) {
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

    bool ASAPNonBlockScheduler::hasHigherRMPriority(AbsRTTask *lhs, AbsRTTask *rhs) {
        if (lhs == rhs) {
            return false;
        }
        ASAPNonBlockTaskModel *lhs_model = getTaskModel(lhs);
        ASAPNonBlockTaskModel *rhs_model = getTaskModel(rhs);
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

    void ASAPNonBlockScheduler::sortByRMPriority(std::vector<AbsRTTask *> &tasks) {
        std::stable_sort(tasks.begin(), tasks.end(),
                         [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                             return hasHigherRMPriority(lhs, rhs);
                         });
    }

    double ASAPNonBlockScheduler::getConfiguredUnitEnergyForTask(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end() || !it->second) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    void ASAPNonBlockScheduler::commitTickEnergy(Tick tick, double energy) {
        if (_energy_commit_valid &&
            _energy_commit_tick == tick &&
            _energy_commit_generation == _selection_generation) {
            throw std::logic_error("ASAP-NonBlock energy committed more than once in one tick");
        }
        if (energy < 0.0 || _current_energy + 1e-9 < energy) {
            throw std::logic_error("ASAP-NonBlock attempted to commit unaffordable selected energy");
        }

        _current_energy = std::max(0.0, _current_energy - energy);
        _stats.total_energy_consumed += energy;
        _energy_commit_tick = tick;
        _energy_commit_generation = _selection_generation;
        _energy_commit_valid = true;
    }

    void ASAPNonBlockScheduler::cancelStaleDispatches(
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

    void ASAPNonBlockScheduler::cleanupExpiredTasks() {
        const Tick current_time = SIMUL.getTime();

        std::vector<AbsRTTask *> expired_ready;
        for (AbsRTTask *task : _ready_queue) {
            if (!task) {
                continue;
            }
            const Tick deadline = task->getDeadline();
            if (deadline <= current_time && task->getRemainingWCET() > 0.0) {
                expired_ready.push_back(task);
                _stats.total_deadline_misses++;
                SCHEDULER_LOG_INFO(std::string("🧹 [ASAP-NonBlock] 清理过期任务: ") +
                                   getTaskName(task) +
                                   " deadline=" + std::to_string(static_cast<int64_t>(deadline)) +
                                   " current=" + std::to_string(static_cast<int64_t>(current_time)));
            }
        }

        for (AbsRTTask *task : expired_ready) {
            removeFromReadyQueue(task);
            removeFromWaitingQueue(task);
            clearTaskTickSelection(task);
            _energy_deducted_tasks.erase(task);
            clearPersistentTaskState(task);
        }
    }

    // =====================================================
    // 核心调度逻辑 - TGF算法的核心
    // =====================================================

    void ASAPNonBlockScheduler::performTickScheduling() {
        Tick current_time = SIMUL.getTime();
        if (_selection_frozen && _selection_tick == current_time) {
            SCHEDULER_LOG_DEBUG(
                std::string("🛡️ [ASAP-NonBlock] 本tick选择已冻结，跳过重复决策 @ ") +
                std::to_string(static_cast<int64_t>(current_time)) + "ms");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [ASAP-NonBlock] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms =====");
        SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

        _stats.total_tick_count++;

        // ========== 第1步：收集太阳能 ==========
        Tick elapsed = current_time - _last_tick_time;
        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO("☀️ 收集太阳能: +" +
                                   std::to_string(harvested * 1000) + " mJ → " +
                                   std::to_string(_current_energy * 1000) + " mJ");
            }
        }
        _last_tick_time = current_time;

        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        restoreEnergyBlockedTasks();

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] _kernel为nullptr，跳过调度");
            return;
        }

        cleanupExpiredTasks();

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
        sortByRMPriority(active_tasks);

        resetTickDispatchState();
        _energy_deducted_tasks.clear();

        double reserved_energy = 0.0;
        const int total_cpus = static_cast<int>(running_tasks_map.size());
        const double epsilon = 1e-9;
        for (AbsRTTask *task : active_tasks) {
            if (static_cast<int>(_dispatch_selection_order.size()) >= total_cpus) {
                break;
            }

            const double unit_energy = getConfiguredUnitEnergyForTask(task);
            if (reserved_energy + unit_energy > _current_energy + epsilon) {
                _stats.total_skipped_energy++;
                SCHEDULER_LOG_INFO(std::string("⚠️ [ASAP-NonBlock] 跳过当前不可负担任务: ") +
                                  getTaskName(task) +
                                  " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                  " 当前=" + std::to_string(_current_energy) + "J" +
                                  " 已预留=" + std::to_string(reserved_energy) + "J");
                continue;
            }

            _dispatch_selection_order.push_back(task);
            _counted_tasks_in_dispatch.insert(task);
            reserved_energy += unit_energy;
        }

        _dispatching_tasks_total_energy = reserved_energy;
        _selection_tick = current_time;
        _selection_generation++;
        _selection_frozen = true;
        _energy_depleted = _dispatch_selection_order.empty() && !active_tasks.empty();

        if (!_dispatch_selection_order.empty()) {
            commitTickEnergy(current_time, reserved_energy);
            for (AbsRTTask *task : _dispatch_selection_order) {
                _energy_deducted_tasks.insert(task);
            }
        }

        cancelStaleDispatches(previous_selection);

        const std::set<AbsRTTask *> selected_set(
            _dispatch_selection_order.begin(), _dispatch_selection_order.end());
        for (AbsRTTask *task : running_tasks) {
            if (selected_set.find(task) != selected_set.end()) {
                continue;
            }
            setSuspendReason(task, _energy_depleted ? "insufficient_energy" : "preemption");
            _kernel->suspend(task);
        }

        if (!_dispatch_selection_order.empty()) {
            _kernel->dispatch();
        }

        SCHEDULER_LOG_INFO("✅ Tick " +
                           std::to_string(static_cast<int64_t>(current_time)) +
                           "ms 完成, 剩余能量: " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    void ASAPNonBlockScheduler::schedule() {
        // TGF依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ASAP-NonBlock] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *ASAPNonBlockScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ASAP-NonBlock] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");
        return getTaskN(0);
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（贪婪策略级联调度）
    // =====================================================

    AbsRTTask *ASAPNonBlockScheduler::getTaskN(unsigned int n) {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ASAP-NonBlock] getTaskN(") + std::to_string(n) + ") 被调用" +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        if (!_selection_frozen || _selection_tick != SIMUL.getTime()) {
            return nullptr;
        }

        if (n < _dispatch_selection_order.size()) {
            AbsRTTask *selected_task = _dispatch_selection_order[n];
            if (selected_task && selected_task->getRemainingWCET() > 0.0 &&
                _counted_tasks_in_dispatch.find(selected_task) != _counted_tasks_in_dispatch.end()) {
                return selected_task;
            }
        }
        return nullptr;
    }

    // =====================================================
    // notify - dispatch完成后仅同步执行态，不再做二次能量门槛
    // =====================================================

    void ASAPNonBlockScheduler::notify(AbsRTTask *task) {
        Scheduler::notify(task);

        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔔 [ASAP-NonBlock] notify: 任务进入执行态（不再做二次能量门槛）: ") +
                          getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ASAPNonBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ASAP-NonBlock] 添加任务: ") + getTaskName(task));
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
        ASAPNonBlockTaskModel *model = new ASAPNonBlockTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-NonBlock] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ASAPNonBlockScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-NonBlock] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        _energy_deducted_tasks.erase(task);
        clearPersistentTaskState(task);

        for (auto &map_pair : _running_tasks) {
            if (map_pair.second == task) {
                _running_tasks[map_pair.first] = nullptr;
            }
        }

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-NonBlock] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ASAPNonBlockScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ASAP-NonBlock] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }

        // ASAP-NonBlock is tick-frozen: arrivals enter the ready queue and
        // are considered at the next tick boundary. Mid-tick preemption would
        // bypass the frozen selected set and can revive stale EndDispatch.
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void ASAPNonBlockScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ASAP-NonBlock] tick-frozen scheduler ignores mid-tick preemption request");
    }

    void ASAPNonBlockScheduler::checkAndPreemptOnAllCPUs() {
        // No-op by design. ASAP-NonBlock recomputes and freezes its selected
        // set only at tick boundaries.
    }

    bool ASAPNonBlockScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        ASAPNonBlockTaskModel *running_model = getTaskModel(running_task);
        ASAPNonBlockTaskModel *new_model = getTaskModel(new_task);

        if (!running_model || !new_model) {
            return false;
        }

        // 检查新任务的能量是否足够
        double unit_energy = calculateUnitEnergyForTask(new_task);
        if (_current_energy < unit_energy) {
            return false;  // 能量不足，不抢占
        }

        // 新任务优先级更高（RM优先级数值越小越高）
        return new_model->getRMPriority() < running_model->getRMPriority();
    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void ASAPNonBlockScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ASAP-NonBlock] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void ASAPNonBlockScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ASAP-NonBlock] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        std::string suspend_reason = getSuspendReason(task);
        bool preserve_blocked_state = (suspend_reason == "insufficient_energy");

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        _energy_deducted_tasks.erase(task);

        if (!preserve_blocked_state) {
            clearPersistentTaskState(task);
        }
    }

    void ASAPNonBlockScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-NonBlock] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ASAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ASAPNonBlockTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ASAP-NonBlock] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void ASAPNonBlockScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ASAP-NonBlock] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ASAPNonBlockScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ASAP-NonBlock] 任务加入等待队列: ") + getTaskName(task));
    }

    void ASAPNonBlockScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ASAPNonBlockScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ASAPNonBlockScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *ASAPNonBlockScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }

        for (AbsRTTask *task : _ready_queue) {
            if (!task || !task->isActive()) {
                continue;
            }
            if (_energy_blocked_tasks.find(task) != _energy_blocked_tasks.end()) {
                continue;
            }
            if (_kernel && _kernel->getProcessor(task) != nullptr) {
                continue;
            }
            return task;
        }

        return nullptr;
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ASAPNonBlockScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        ASAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double ASAPNonBlockScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ASAPNonBlockScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void ASAPNonBlockScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string ASAPNonBlockScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void ASAPNonBlockScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    double ASAPNonBlockScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ASAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] calculateTotalEnergyForTask: 任务模型不存在");
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

    double ASAPNonBlockScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ASAP-NonBlock] 功率计算: ") +
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
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
    // =====================================================

    /*
    void ASAPNonBlockScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ASAP-NonBlock] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        ASAPNonBlockEnergyCheckEvent *evt = new ASAPNonBlockEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
    }

    void ASAPNonBlockScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // 删除事件对象

            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }
    */  // ⭐ V40重构：能量检查方法已删除

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ASAPNonBlockScheduler::collectSolarEnergy(Tick current_time) {
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

    double ASAPNonBlockScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-NonBlock] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-NonBlock] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ASAPNonBlockScheduler::scheduleNextTick() {
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

    ASAPNonBlockTaskModel *ASAPNonBlockScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ASAPNonBlockScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    void ASAPNonBlockScheduler::clearPersistentTaskState(AbsRTTask *task) {
        if (!task) {
            return;
        }

        _energy_blocked_tasks.erase(task);
        _energy_accounts.erase(task);
        _suspend_reasons.erase(task);
    }

    void ASAPNonBlockScheduler::resetTickDispatchState() {
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _dispatching_tasks_total_energy = 0.0;
    }

    void ASAPNonBlockScheduler::clearTaskTickSelection(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_counted_tasks_in_dispatch.erase(task) > 0) {
            _dispatching_tasks_total_energy -= calculateUnitEnergyForTask(task);
            if (_dispatching_tasks_total_energy < 0.0) {
                _dispatching_tasks_total_energy = 0.0;
            }
        }

        _dispatch_selection_order.erase(
            std::remove(_dispatch_selection_order.begin(), _dispatch_selection_order.end(), task),
            _dispatch_selection_order.end());
    }

    void ASAPNonBlockScheduler::markTaskSelectedThisTick(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_counted_tasks_in_dispatch.insert(task).second) {
            _dispatch_selection_order.push_back(task);
            _dispatching_tasks_total_energy += calculateUnitEnergyForTask(task);
        }
    }

    void ASAPNonBlockScheduler::accountInitialEnergyForSelectedTasks(const std::string &log_prefix) {
        for (AbsRTTask *task : _dispatch_selection_order) {
            if (!task) {
                continue;
            }
            if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                continue;
            }
            if (_energy_deducted_tasks.find(task) != _energy_deducted_tasks.end()) {
                continue;
            }

            double unit_energy = calculateUnitEnergyForTask(task);
            _current_energy -= unit_energy;
            if (_current_energy < 0.0) {
                _current_energy = 0.0;
            }
            _stats.total_energy_consumed += unit_energy;
            _energy_deducted_tasks.insert(task);

            SCHEDULER_LOG_INFO(log_prefix + getTaskName(task) +
                               " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        }
    }

    void ASAPNonBlockScheduler::restoreEnergyBlockedTasks() {
        std::vector<AbsRTTask *> tasks_to_restore;
        std::vector<AbsRTTask *> tasks_to_clear;
        const double EPSILON = 1e-9;

        for (AbsRTTask *task : _energy_blocked_tasks) {
            if (!task || !task->isActive()) {
                tasks_to_clear.push_back(task);
                continue;
            }

            double unit_energy = calculateUnitEnergyForTask(task);
            if (_current_energy >= unit_energy - EPSILON) {
                tasks_to_restore.push_back(task);
            }
        }

        for (AbsRTTask *task : tasks_to_clear) {
            _energy_blocked_tasks.erase(task);
        }

        for (AbsRTTask *task : tasks_to_restore) {
            _energy_blocked_tasks.erase(task);
            clearSuspendReason(task);
            SCHEDULER_LOG_INFO(std::string("🔓 [ASAP-NonBlock] 恢复blocked任务资格: ") + getTaskName(task));
        }
    }

    AbsRTTask *ASAPNonBlockScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int ASAPNonBlockScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *ASAPNonBlockScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void ASAPNonBlockScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-NonBlock] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ASAPNonBlockScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ASAP-NonBlock] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ASAPNonBlockScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ASAPNonBlockScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *ASAPNonBlockScheduler::getKernel() {
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

    void ASAPNonBlockScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-NonBlock] newRun - 仿真开始");

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();
        _energy_depleted = false;  // ⭐ 重置能量耗尽标志

        _ready_queue.clear();
        _waiting_queue.clear();
        _energy_accounts.clear();
        _energy_blocked_tasks.clear();
        _energy_deducted_tasks.clear();
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _suspend_reasons.clear();
        _running_tasks.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_generation = 0;
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_generation = 0;
        _energy_commit_valid = false;

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ASAP-NonBlock] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ASAPNonBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-NonBlock] endRun - 仿真结束");

        resetTickDispatchState();
        _energy_deducted_tasks.clear();

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ASAP-NonBlock] ===== TGF调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ASAPNonBlockScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-NonBlock] 任务结束: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        _energy_deducted_tasks.erase(task);
        clearPersistentTaskState(task);

        for (auto &pair : _running_tasks) {
            if (pair.second == task) {
                pair.second = nullptr;
                break;
            }
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ASAP-NonBlock] 当前能量: ") + std::to_string(_current_energy) + "J");
    }

    bool ASAPNonBlockScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void ASAPNonBlockScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ASAP-NonBlock] ===== TGF调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ASAPNonBlockScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ASAPNonBlockScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void ASAPNonBlockScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ASAP-NonBlock] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ASAP-NonBlock] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const double EPSILON = 1e-9;
        std::vector<AbsRTTask *> tasks_to_interrupt;

        // ⭐ V28.15修复：使用kernel的getCurrentExecutingTasks()获取实际运行中的任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();

        // ⭐ 关键修复：先扣除上一ms执行消耗的能量，再检查是否足够继续
        // 这样可以确保能量扣除和能量检查的��序正确
        //         double total_energy_to_deduct = 0.0;
        //         for (auto &map_pair : running_tasks) {
        //             AbsRTTask *task = map_pair.second;
        //             if (!task) {
        //                 continue;
        //             }
        // 
            // 计算该任务执行1ms所需的能量
        //             double unit_energy = calculateUnitEnergyForTask(task);
        //             total_energy_to_deduct += unit_energy;
        //         }
        // 
        // 扣除所有运行中任务上一ms的能量
        //         if (total_energy_to_deduct > 0 && _current_energy >= total_energy_to_deduct - 1e-9) {
        //             double old_energy = _current_energy;
        //             _current_energy -= total_energy_to_deduct;
        //             _stats.total_energy_consumed += total_energy_to_deduct;
        // 
        //             SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-NonBlock] Tick事件: 扣除运行中任务能量 ") +
        //                                std::to_string(total_energy_to_deduct * 1000) + " mJ，" +
        //                                std::to_string(old_energy * 1000) + " mJ → " +
        //                                std::to_string(_current_energy * 1000) + " mJ (" +
        //                                std::to_string(running_tasks.size()) + " 个任务)");
        //         }
        // 
        // ⭐ V29完整修复：先扣除上一ms执行消耗的能量，再检查是否足够继续
        // 这样确保能量扣除和检查的时序完全正确
        for (auto &map_pair : running_tasks) {
            AbsRTTask *task = map_pair.second;
            if (!task) {
                continue;
            }

            // 计算该任务执行1ms所需的能量
            double unit_energy = calculateUnitEnergyForTask(task);

            // ⭐ 检查：当前能量是否足够该任务继续执行1ms
            if (_current_energy < unit_energy - EPSILON) {
                SCHEDULER_LOG_WARNING(std::string("⚡ [ASAP-NonBlock] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [ASAP-NonBlock] 任务能量充足: ") +
                                   getTaskName(task) +
                                   " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                   " 当前能量=" + std::to_string(_current_energy) + "J");
            }
        }

        // 2. 中断能量不足的任务
        for (AbsRTTask *task : tasks_to_interrupt) {
            if (!task) {
                continue;
            }

            SCHEDULER_LOG_INFO(std::string("🛑 [ASAP-NonBlock] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法中断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            setSuspendReason(task, "insufficient_energy");
            _kernel->suspend(task);

            // ⭐ 取消该任务的能量检查事件，防止继续扣除能量
//             auto it = _energy_check_events.find(task);
//             if (it != _energy_check_events.end()) {
//                 // 从map中移除，但不删除事件对象（它会自然结束）
//                 _energy_check_events.erase(it);
//                 SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-NonBlock] 已取消任务的能量检查事件: ") + getTaskName(task));
//             }

            SCHEDULER_LOG_INFO(std::string("⏸️ [ASAP-NonBlock] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ASAP-NonBlock] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }
} // namespace RTSim
