// gpfp_tgf_scheduler.cpp - ALAP-NonBlock (Tick-based Greedy First) Scheduler Implementation
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
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_alap_nonblock_scheduler.hpp>
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
    // ALAPNonBlockTickEvent 实现
    // =====================================================

    ALAPNonBlockTickEvent::ALAPNonBlockTickEvent(ALAPNonBlockScheduler *scheduler)
        : MetaSim::Event("ALAPNonBlockTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行
    }

    void ALAPNonBlockTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ALAP-NonBlock] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ALAP-NonBlockEnergyCheckEvent 实现 - 运行时能量检查
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling���理
    // =====================================================

    /*
    ALAP-NonBlockEnergyCheckEvent::ALAP-NonBlockEnergyCheckEvent(ALAPNonBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("ALAP-NonBlockEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void ALAP-NonBlockEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-NonBlock] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-NonBlock] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-NonBlock] 能量检查：任务已停止执行，不再扣除能量: ") +
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
        ALAPNonBlockTaskModel *task_model = _scheduler->getTaskModel(_task);
        if (task_model && _ms_executed >= task_model->getWCET()) {
            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 任务已达到WCET，完成执行: ") +
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
        // - ALAP-NonBlockEnergyCheckEvent: 负责运行中任务的续期能量扣除
        
        // 检查是否有足够能量续期1ms
        // ⭐ V35修复：当能量 <= 1ms能耗时，立即中断任务
        // 避免在能量恰好等于单位能耗时继续执行，导致下个Tick能量为负
        if (current_energy <= unit_energy + EPSILON) {
            // ⭐ 能量不足以支撑下一个1ms，立即中断任务（不扣除能量）
            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 能量刚好耗尽或不足，立即中断任务: ") +
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
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-NonBlock] 任务因能量不足被挂起: ") + _scheduler->getTaskName(_task));
            }

            // ⭐ 关键修复：清理能量检查事件映射，允许后续实例启动新的检查
            _scheduler->_energy_check_events.erase(_task);

            // ⭐ V36 Bug修复：从_counted_tasks_in_dispatch中移除任务
            // 避免任务被重新调度时"免费"运行（不扣除能量）
            // 当任务因能量不足被挂起时，内核会将其重新插入就绪队列
            // 如果不移除_counted_tasks_in_dispatch中的记录，getTaskN()会认为能量已扣除，
            // 直接返回任务而不重新扣除能量，导致任务免费运行
            _scheduler->_counted_tasks_in_dispatch.erase(_task);

            // 不重新调度事件
            return;
        }

        // 能量充足（扣除后仍有剩余），扣除续期能量
        double old_energy = current_energy;
        _scheduler->_current_energy -= unit_energy;
        _scheduler->_stats.total_energy_consumed += unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 运行中任务续期: ") +
                           _scheduler->getTaskName(_task) + " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " " + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }
    */  // ⭐ V40重构：ALAP-NonBlockEnergyCheckEvent已删除

    // =====================================================
    // ALAPNonBlockTaskModel 实现
    // =====================================================

    ALAPNonBlockTaskModel::ALAPNonBlockTaskModel(AbsRTTask *t, int period, int wcet,
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

    ALAPNonBlockTaskModel::~ALAPNonBlockTaskModel() {}

    Tick ALAPNonBlockTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ALAPNonBlockTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ALAPNonBlockTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ALAPNonBlockScheduler 实现
    // =====================================================

    ALAPNonBlockScheduler::ALAPNonBlockScheduler()
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
          _tick_event(nullptr),
          _first_tick_scheduled(false),
          _kernel(nullptr) {

        SCHEDULER_LOG_INFO("🚀 [ALAP-NonBlock] ALAP-NonBlock Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ALAP-NonBlock] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ALAP-NonBlock] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ALAP-NonBlock] 开始时间偏移: ") +
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
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-NonBlock] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-NonBlock] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy > 0) {
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-NonBlock] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy > 0) {
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-NonBlock] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ALAP-NonBlock] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ALAPNonBlockTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ALAP-NonBlock] ALAP-NonBlock Scheduler 初始化完成");
    }

    ALAPNonBlockScheduler::ALAPNonBlockScheduler(const std::vector<std::string> &params)
        : ALAPNonBlockScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ALAPNonBlockScheduler>
        ALAPNonBlockScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ALAPNonBlockScheduler>(params);
    }

    ALAPNonBlockScheduler::~ALAPNonBlockScheduler() {
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
    // 核心调度逻辑 - ALAP-NonBlock算法的核心
    // =====================================================

    void ALAPNonBlockScheduler::performTickScheduling() {
        SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-NonBlock] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms =====");
        SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

        _stats.total_tick_count++;

        // ⭐ V42修复：清空当前tick新调度任务标记
        // 这样只有本次tick中新调度的任务才会被跳过续期扣除
        _newly_dispatched_this_tick.clear();

        Tick current_time = SIMUL.getTime();

        // ========== 第1步：收集太阳能 ==========
        // ⭐ 关键修复：太阳能收集必须在能量耗尽检查之前执行
        // 否则当初始能量为0时，系统会因为能量耗尽而跳过太阳能收集，形成死锁
        Tick elapsed = current_time - _last_tick_time;
        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO("☀️ 收集太阳能: +" +
                                   std::to_string(harvested * 1000) + " mJ → " +
                                   std::to_string(_current_energy * 1000) + " mJ");

                // ⭐ V43修复：只有当能量足够时才清除能量耗尽标志
                // 使用合理阈值（10 mJ）判断能量是否足够恢复调度
                const double RECOVERY_THRESHOLD = 0.010;  // 10 mJ
                if (_energy_depleted && _current_energy >= RECOVERY_THRESHOLD) {
                    _energy_depleted = false;
                    SCHEDULER_LOG_INFO("🔋 [ALAP-NonBlock] 太阳能充电成功，恢复调度 (能量=" +
                                      std::to_string(_current_energy * 1000) + " mJ >= 阈值=" +
                                      std::to_string(RECOVERY_THRESHOLD * 1000) + " mJ)");
                }
            }
        }
        _last_tick_time = current_time;

        // ⭐ Bug修复3：能量耗尽时跳过任务调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-NonBlock] 能量已耗尽，跳过任务调度"));
            return;
        }

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第1.5步：清理过期任务实例 ==========
        // ⭐ 已改用killOnMiss(true)，框架自动处理过期实例
        // cleanupExpiredTasks();

        // ========== 阶段一：ALAP时序门控 ==========
        // ⭐ ALAP核心：在能量分发之前检查Slack，决定是否强制休眠
        if (!checkALAPTimingGate()) {
            // Slack > 0，强制休眠，本tick不进行任何任务调度
            SCHEDULER_LOG_INFO("⏸️  [ALAP-NonBlock] ALAP时序门控：强制休眠，跳过本Tick调度");
            return;
        }
        // Slack ≤ 0，唤醒，继续进行能量分发

        // ========== 第2步：处理运行中任务的续期能量 ==========
        // ⭐ 重构：在tick边界扣除运行任务的续期能量（替代ALAP-NonBlockEnergyCheckEvent）
        // ⭐ V40修复：确保kernel已设置，如果没有则尝试获取
        if (!_kernel) {
            _kernel = getKernel();
        }

        if (_kernel) {
            const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();
            std::vector<AbsRTTask *> tasks_to_suspend;

            SCHEDULER_LOG_INFO("🏃 检查运行任务: " +
                               std::to_string(running_tasks_map.size()) + " 个");

            for (const auto& [cpu, task] : running_tasks_map) {
                if (!task || !task->isActive()) continue;

                // ⭐ V42修复：跳过当前tick中新调度的任务（能量已在getTaskN中扣除）
                // 使用_newly_dispatched_this_tick而不是_counted_tasks_in_dispatch
                if (_newly_dispatched_this_tick.find(task) != _newly_dispatched_this_tick.end()) {
                    SCHEDULER_LOG_DEBUG(std::string("⏭️ [ALAP-NonBlock] 跳过新任务的续期扣除: ") + getTaskName(task));
                    continue;
                }

                double unit_energy = calculateUnitEnergyForTask(task);

                // 检查是否有足够能量续期1ms
                const double EPSILON = 1e-9;
                if (_current_energy < unit_energy - EPSILON) {
                    // ⭐ V43修复：能量不足时设置能量耗尽标志
                    if (!_energy_depleted) {
                        _energy_depleted = true;
                        _current_energy = 0.0;  // 强制设为0，防止变负
                        SCHEDULER_LOG_WARNING("💀 [ALAP-NonBlock] 能量耗尽，设置_energy_depleted标志");
                    }

                    // 能量不足，加入挂起列表
                    tasks_to_suspend.push_back(task);
                    SCHEDULER_LOG_WARNING("⚠️ 续期能量不足，将挂起: " +
                                         getTaskName(task) +
                                         " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                         " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
                } else {
                    // 扣除续期能量
                    double old_energy = _current_energy;
                    _current_energy -= unit_energy;
                    _stats.total_energy_consumed += unit_energy;

                    SCHEDULER_LOG_INFO("⚡ 扣除续期能量: " +
                                       getTaskName(task) +
                                       " -" + std::to_string(unit_energy * 1000) + " mJ " +
                                       std::to_string(old_energy * 1000) + " → " +
                                       std::to_string(_current_energy * 1000) + " mJ");
                }
            }

            // 挂起能量不足的任务
            for (AbsRTTask *task : tasks_to_suspend) {
                _kernel->suspend(task);
                SCHEDULER_LOG_INFO("🛑 挂起任务: " + getTaskName(task));
            }
        }

        // ========== 第3步：检查抢占 ==========
        checkAndPreempt();

        // ========== 第4步：调度新任务 ==========
        if (_kernel) {
            SCHEDULER_LOG_INFO("🔔 开始调度新任务");

            // 记录调度前的能量
            double energy_before_scheduling = _current_energy;

            // ⭐ 关键：先扣除已标记但未扣除的任务能量（来自arrival或onTaskEnd的dispatch）
            for (AbsRTTask *task : _counted_tasks_in_dispatch) {
                if (_energy_deducted_tasks.find(task) == _energy_deducted_tasks.end()) {
                    double unit_energy = calculateUnitEnergyForTask(task);
                    _current_energy -= unit_energy;
                    _stats.total_energy_consumed += unit_energy;
                    _energy_deducted_tasks.insert(task);

                    SCHEDULER_LOG_INFO("✅ [ALAP-NonBlock] 扣除上周期任务初始能量: " +
                                       getTaskName(task) +
                                       " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                                       std::to_string(_current_energy * 1000) + " mJ");
                }
            }

            // ⭐ 清空本次tick的调度记录
            _counted_tasks_in_dispatch.clear();
            _dispatching_tasks_total_energy = 0.0;

            // ⭐ ALAP-NonBlock关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
            int dispatch_attempts = 0;
            const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

            while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
                // 检查是否所有CPU都已填满
                // ⭐ 关键修复：_running_tasks为空时不应认为所有CPU已满
                bool all_cpus_full = false;
                if (!_running_tasks.empty()) {
                    all_cpus_full = true;
                    for (auto &map_pair : _running_tasks) {
                        if (map_pair.second == nullptr) {
                            all_cpus_full = false;
                            break;
                        }
                    }
                }

                if (all_cpus_full) {
                    SCHEDULER_LOG_DEBUG("✅ [ALAP-NonBlock] 所有CPU已填满，停止调度");
                    break;
                }

                // 记录调度前的任务数
                size_t tasks_before = _ready_queue.size() + _running_tasks.size();

                // 调用dispatch尝试调度更多任务
                _kernel->dispatch();
                dispatch_attempts++;

                // 记录调度后的任务数
                size_t tasks_after = _ready_queue.size() + _running_tasks.size();

                // 如果没有任务被调度（状态没变化），停止调度
                if (tasks_before == tasks_after) {
                    SCHEDULER_LOG_DEBUG("⏹️ [ALAP-NonBlock] 无更多任务可调度，停止dispatch循环");
                    break;
                }

                SCHEDULER_LOG_DEBUG(std::string("🔄 [ALAP-NonBlock] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
            }

            if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] dispatch循环达到最大迭代次数，可能存在bug");
            }

            // ⭐ 关键：在dispatch后，统一扣除所有已标记任务的能量
            // 只扣除尚未扣除过的任务（检查_energy_deducted_tasks）
            for (AbsRTTask *task : _counted_tasks_in_dispatch) {
                if (_energy_deducted_tasks.find(task) == _energy_deducted_tasks.end()) {
                    // 任务尚未扣除能量，现在扣除
                    double unit_energy = calculateUnitEnergyForTask(task);
                    _current_energy -= unit_energy;
                    _stats.total_energy_consumed += unit_energy;
                    _energy_deducted_tasks.insert(task);  // 标记已扣除

                    SCHEDULER_LOG_INFO("✅ [ALAP-NonBlock] 新任务扣除初始能量: " +
                                       getTaskName(task) +
                                       " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                                       std::to_string(_current_energy * 1000) + " mJ");
                }
            }
        }

        SCHEDULER_LOG_INFO("✅ Tick " +
                           std::to_string(static_cast<int64_t>(current_time)) +
                           "ms 完成, 剩余能量: " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    void ALAPNonBlockScheduler::schedule() {
        // ALAP-NonBlock依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-NonBlock] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *ALAPNonBlockScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-NonBlock] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // ⭐ 核心：不在这里收集能量，能量收集在tick边界完成

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-NonBlock] getFirst: 就绪队列为空");
            return nullptr;
        }

        AbsRTTask *first_task = _ready_queue.front();
        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-NonBlock] getFirst: 队列首任务为空");
            return nullptr;
        }

        // ⭐ 核心：即时能量判断（当前能量 >= 1ms能耗）
        double unit_energy = calculateUnitEnergyForTask(first_task);

        if (_current_energy < unit_energy) {
            SCHEDULER_LOG_INFO(std::string("❌ [ALAP-NonBlock] getFirst: 能量不足") +
                              " 任务: " + getTaskName(first_task) +
                              " 需要: " + std::to_string(unit_energy) + "J" +
                              " 当前: " + std::to_string(_current_energy) + "J");
            return nullptr;
        }

        // 返回任务（能量在notify时扣减）
        return first_task;
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（贪婪策略级联调度）
    // =====================================================

    AbsRTTask *ALAPNonBlockScheduler::getTaskN(unsigned int n) {
        // ⭐ V43修复：能量耗尽时立即返回，不调度任何任务
        if (_energy_depleted) {
            SCHEDULER_LOG_DEBUG(std::string("💀 [ALAP-NonBlock] getTaskN: 能量已耗尽，拒绝调度") +
                               " n=" + std::to_string(n) +
                               " energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

                // STAR Critical fix: if energy depleted, don\'t schedule any tasks
        if (_energy_depleted) {
        // STAR Critical fix: if energy depleted, don't schedule any tasks
        if (_energy_depleted) {
            SCHEDULER_LOG_DEBUG(std::string("STAR [ALAP-NonBlock] getTaskN: Energy depleted") +
                               " n=" + std::to_string(n) +
                               " energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

            SCHEDULER_LOG_DEBUG(std::string("STAR [ALAP-NonBlock] getTaskN: Energy depleted, not scheduling task") +
                               " n=" + std::to_string(n) +
                               " current_energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-NonBlock] getTaskN(") + std::to_string(n) + ") 被调用" +
                           " 当前能量: " + std::to_string(_current_energy) + "J" +
                           " 已调度能耗=" + std::to_string(_dispatching_tasks_total_energy) + "J");

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-NonBlock] getTaskN: 就绪队列为空");
            return nullptr;
        }

        // ⭐ ALAP时序门控：不再在getTaskN中调用全局min_slack检查（性能瓶颈）
        // 改为在遍历任务时逐个检查个体Slack，只调度Slack≤0的任务
        // 效果等价：如果所有任务Slack>0，getTaskN返回nullptr

        // ⭐ 级联调度：遍历就绪队列，运行中任务也要检查能量
        unsigned int ready_index = 0;
        unsigned int original_target_n = n;  // 记住最初请求的n值
        const double EPSILON = 1e-9;
        bool skipped_energy_insufficient = false;  // 是否跳过了能量不足的任务

        std::cout << "[DEBUG] ALAP-NonBlock::getTaskN(" << n << ") - ready_queue.size()=" << _ready_queue.size() << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            AbsRTTask *task = _ready_queue[i];

            if (!task) {
                continue;
            }

            // ⭐ killOnMiss安全检查：跳过已被框架终止的任务实例
            if (!task->isActive()) {
                continue;
            }

            // ⭐ 关键修复：不再跳过已调度的任务（与TIE保持一致）
            // _counted_tasks_in_dispatch只是用于跟踪本次tick中已扣除能量的任务
            // 避免重复扣除能量
            // 重复调度的问题由内核的_m_dispatched检查来处理
            bool is_running_check = false;
            if (_kernel) {
                CPU *proc = _kernel->getProcessor(task);
                is_running_check = (proc != nullptr);
            }

            // 检查是否已在本tick中扣除过能量
            // ⭐ V29.1修复：运行中任务的续期由ALAP-NonBlockEnergyCheckEvent处理
            // getTaskN()只负责新任务，运行中任务直接返回
            if (is_running_check) {
                if (ready_index == n) {
                    return task;
                }
                ready_index++;
                continue;
            }

            // 这是第ready_index个未dispatch的任务
            if (ready_index == n) {
                // ⭐ 关键修复：跳过已过期的任务实例
                ALAPNonBlockTaskModel *task_model = getTaskModel(task);
                if (task_model) {
                    Tick arrival = task->getArrival();
                    Tick deadline = arrival + Tick(task_model->getPeriod());
                    Tick current_time = SIMUL.getTime();
                    if (deadline <= current_time) {
                        SCHEDULER_LOG_INFO(std::string("🧹 [ALAP-NonBlock] getTaskN: 跳过过期任务 ") +
                                          getTaskName(task) +
                                          " deadline=" + std::to_string(static_cast<int64_t>(deadline)) +
                                          " current=" + std::to_string(static_cast<int64_t>(current_time)));
                        continue;
                    }
                }

                // ⭐ 关键修复：个体任务ALAP时序门控
                // 全局门控决定系统是否唤醒，个体门控决定哪些任务可以调度
                // 只有Slack≤0的任务才应该被调度
                Tick individual_slack = calculateSlackForTask(task);
                if (individual_slack > 0) {
                    // 这个任务还有等待余地，跳过（不计入ready_index）
                    SCHEDULER_LOG_INFO(std::string("⏸️ [ALAP-NonBlock] getTaskN: 个体Slack>0，跳过 ") +
                                      getTaskName(task) +
                                      " Slack=" + std::to_string(static_cast<int64_t>(individual_slack)) + "ms");
                    continue;
                }

                // ⭐ 全局ALAP时序门控已在函数开头检查，这里按优先级调度
                // ⭐ 计算任务的1ms能耗
                double unit_energy = calculateUnitEnergyForTask(task);
                double available_energy = _current_energy - _dispatching_tasks_total_energy;

                // ⭐ 贪心策略：如果能量不足，跳过这个任务���继续查找后面的任务
                if (available_energy < unit_energy - EPSILON) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-NonBlock] 任务能量不足，跳过（贪心策略）") +
                                      " 任务=" + getTaskName(task) +
                                      " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                      " 已调度能耗=" + std::to_string(_dispatching_tasks_total_energy) + "J" +
                                      " 剩余=" + std::to_string(available_energy) + "J");

                    // ⭐ 贪心策略：继续查找队列中是否有能量足够的后续任务
                    for (size_t j = i + 1; j < _ready_queue.size(); ++j) {
                        AbsRTTask *next_task = _ready_queue[j];
                        if (!next_task) {
                            continue;
                        }

                        // ⭐ 关键修复：检查任务是否已被调度（在counted_tasks中）
                        if (_counted_tasks_in_dispatch.find(next_task) != _counted_tasks_in_dispatch.end()) {
                            // 任务已被调度（可能还没开始运行），跳过
                            SCHEDULER_LOG_DEBUG(std::string("  [ALAP-NonBlock] 贪心搜索：跳过已调度任务: ") + getTaskName(next_task));
                            continue;
                        }

                        // 检查下一个任务是否已经在运行
                        bool next_is_running = false;
                        if (_kernel) {
                            CPU *proc = _kernel->getProcessor(next_task);
                            next_is_running = (proc != nullptr);
                        }

                        if (next_is_running) {
                            // 运行中的任务已在上面处理过，跳过
                            continue;
                        }

                        // ⭐ Bug修复：检查任务是否之前在本周期被dispatch过
                        // 如果任务之前被抢占，它可能还保留着旧的dispatch时间，会导致时间倒流错误
                        // 解决方案：跳过那些之前被dispatch但现在不在运行的任务（说明它们被抢占了）
                        PeriodicTask *pt = dynamic_cast<PeriodicTask *>(next_task);
                        if (pt) {
                            // 检查任务是否有剩余执行时间但不在运行
                            // 这表明任务之前被dispatch过但被抢占了
                            Tick remaining = pt->getWCET() - pt->getExecTime();
                            if (remaining > 0 && remaining < pt->getWCET() && !next_is_running) {
                                SCHEDULER_LOG_DEBUG(std::string("  [ALAP-NonBlock] 贪心搜索：跳过被抢占的任务: ") +
                                                  getTaskName(next_task) +
                                                  " 剩余=" + std::to_string(static_cast<int64_t>(remaining)) +
                                                  " WCET=" + std::to_string(static_cast<int64_t>(pt->getWCET())));
                                continue;
                            }
                        }

                        double next_unit_energy = calculateUnitEnergyForTask(next_task);
                        double next_available = _current_energy - _dispatching_tasks_total_energy;

                        // ⭐ 关键修复：贪心搜索跳过过期任务
                        ALAPNonBlockTaskModel *next_model = getTaskModel(next_task);
                        if (next_model) {
                            Tick next_arrival = next_task->getArrival();
                            Tick next_deadline = next_arrival + Tick(next_model->getPeriod());
                            if (next_deadline <= SIMUL.getTime()) {
                                continue;  // 过期任务，跳过
                            }
                        }

                        // ⭐ 关键修复：贪心搜索也需要检查个体Slack
                        // 只有Slack≤0的任务才能被贪心回填
                        Tick next_slack = calculateSlackForTask(next_task);
                        if (next_slack > 0) {
                            SCHEDULER_LOG_DEBUG(std::string("  [ALAP-NonBlock] 贪心搜索：Slack>0，跳过 ") +
                                              getTaskName(next_task) +
                                              " Slack=" + std::to_string(static_cast<int64_t>(next_slack)) + "ms");
                            continue;
                        }

                        if (next_available >= next_unit_energy - EPSILON) {
                            // ⭐ 找到能量足够的后续任务，调度它！
                            // ⭐ 只标记任务，不扣除能量（能量将在dispatch后统一扣除）
                            if (_counted_tasks_in_dispatch.find(next_task) == _counted_tasks_in_dispatch.end()) {
                                _counted_tasks_in_dispatch.insert(next_task);
                                _newly_dispatched_this_tick.insert(next_task);

                                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 贪心策略：调度后续任务（已标记，暂不扣能量）") +
                                                  " 替换=" + getTaskName(task) +
                                                  " → " + getTaskName(next_task) +
                                                  " 1ms能耗=" + std::to_string(next_unit_energy * 1000) + " mJ");
                            }

                            return next_task;
                        }
                    }

                    // 没有找到能量足够的任务
                    SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-NonBlock] 贪心策略：未找到能量足够的任务"));
                    return nullptr;
                }

                // ⭐ 能量足够，正常调度
                // ⭐ V41修复���对于新任务（非运行中），立即扣除初始能量
                // 这解决了tick边界处理时序问题（任务在tick结束时被调度，但能量在下一tick才扣除）
                if (!is_running_check) {
                    // 新任务：检查是否已扣除过初始能量
                    if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                        // 首次调度此任务，标记任务
                        _counted_tasks_in_dispatch.insert(task);  // 标记已调度
                        _newly_dispatched_this_tick.insert(task);

                        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 新任务已标记（暂不扣能量）: ") + getTaskName(task) +
                                          " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ");
                    }
                    // 否则已标记过，直接返回任务
                    return task;
                }
                // 运行中任务不需要标记，因为它们已经扣除过初始能量
                return task;
            } else {
                // ⭐ V32关键修复：不是我们要找的第n个任务，继续寻找
                ready_index++;
            }
        }

        return nullptr;
    }

    // =====================================================
    // notify - 每ms逐次扣减能耗（ALAP-NonBlock核心逻辑）
    // =====================================================

    void ALAPNonBlockScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否足够
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-NonBlock] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-NonBlock] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ALAPNonBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-NonBlock] 添加任务: ") + getTaskName(task));
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
        ALAPNonBlockTaskModel *model = new ALAPNonBlockTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ALAPNonBlockScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-NonBlock] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);

        for (auto &map_pair : _running_tasks) {
            if (map_pair.second == task) {
                _running_tasks[map_pair.first] = nullptr;
            }
        }

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ALAPNonBlockScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ALAP-NonBlock] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
            checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void ALAPNonBlockScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-NonBlock] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void ALAPNonBlockScheduler::checkAndPreemptOnAllCPUs() {
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) return;
        }

        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();

        // ⭐ 先检查是否有空闲CPU
        int free_cpus = 0;
        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) free_cpus++;
        }
        if (free_cpus > 0) return;

        // 找就绪队列中Slack≤0且优先级最高的候选任务
        AbsRTTask *best_candidate = nullptr;
        ALAPNonBlockTaskModel *best_model = nullptr;
        Tick best_slack = 0;

        for (AbsRTTask *candidate : _ready_queue) {
            if (!candidate) continue;
            CPU *cand_cpu = _kernel->getProcessor(candidate);
            if (cand_cpu != nullptr) continue;

            Tick slack = calculateSlackForTask(candidate);
            if (slack > 0) continue;

            ALAPNonBlockTaskModel *model = getTaskModel(candidate);
            if (!model) continue;

            if (!best_candidate || model->getRMPriority() < best_model->getRMPriority()) {
                best_candidate = candidate;
                best_model = model;
                best_slack = slack;
            }
        }

        if (!best_candidate) return;

        // 找运行中优先级最低的任务
        AbsRTTask *worst_running = nullptr;
        ALAPNonBlockTaskModel *worst_model = nullptr;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            ALAPNonBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            if (!worst_running || model->getRMPriority() > worst_model->getRMPriority()) {
                worst_running = task;
                worst_model = model;
            }
        }

        if (!worst_running || !worst_model) return;

        if (best_model->getRMPriority() < worst_model->getRMPriority()) {
            double unit_energy = calculateUnitEnergyForTask(best_candidate);
            if (_current_energy < unit_energy) return;

            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-NonBlock] ALAP抢占: ") +
                              " 挂起=" + getTaskName(worst_running) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(worst_model->getRMPriority())) + ")" +
                              " 调度=" + getTaskName(best_candidate) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(best_model->getRMPriority())) +
                              " Slack=" + std::to_string(static_cast<int64_t>(best_slack)) + ")");

            _kernel->suspend(worst_running);
        }
    }

    bool ALAPNonBlockScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        ALAPNonBlockTaskModel *running_model = getTaskModel(running_task);
        ALAPNonBlockTaskModel *new_model = getTaskModel(new_task);

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

    void ALAPNonBlockScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ALAP-NonBlock] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void ALAPNonBlockScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ALAP-NonBlock] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
    }

    void ALAPNonBlockScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-NonBlock] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ALAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ALAPNonBlockTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ALAP-NonBlock] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void ALAPNonBlockScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ALAP-NonBlock] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ALAPNonBlockScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ALAP-NonBlock] 任务加入等待队列: ") + getTaskName(task));
    }

    void ALAPNonBlockScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ALAPNonBlockScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ALAPNonBlockScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *ALAPNonBlockScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ALAPNonBlockScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        ALAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double ALAPNonBlockScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ALAPNonBlockScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    double ALAPNonBlockScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ALAPNonBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] calculateTotalEnergyForTask: 任务模型不存在");
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

    double ALAPNonBlockScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-NonBlock] 功率计算: ") +
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
    void ALAPNonBlockScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-NonBlock] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        ALAP-NonBlockEnergyCheckEvent *evt = new ALAP-NonBlockEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
    }

    void ALAPNonBlockScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // 删除事件对象

            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }
    */  // ⭐ V40重构：能量检查方法已删除

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ALAPNonBlockScheduler::collectSolarEnergy(Tick current_time) {
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 计算自上次收集以来的时间
        Tick elapsed = current_time - _last_collection_time;

        if (elapsed <= 0) {
            return 0.0;
        }

        // 获取当前辐照度（根据use_real_solar_data选择NASA数据或函数曲线）
        double irradiance = getSolarIrradiance(current_ms);

        // 计算收集能量
        double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
        double energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;

        // 更新最后收集时间
        _last_collection_time = current_time;

        return energy;
    }

    double ALAPNonBlockScheduler::getSolarIrradiance(int64_t time_ms) {
        if (!_use_real_solar_data) {
            // ⭐ 分段函数模型：模拟真实太阳能曲线
            int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);

            // 转换为小时（用于分段判断）
            int64_t ms_of_day = actual_time_ms % 86400000;
            double hour_of_day = static_cast<double>(ms_of_day) / 3600000.0;  // 0.0-24.0

            // 分段函数定义（更真实的太阳能曲线）
            const double PEAK_IRRADIANCE = 800.0;  // 峰值辐照度 (W/m²)

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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-NonBlock] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-NonBlock] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ALAPNonBlockScheduler::scheduleNextTick() {
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

    ALAPNonBlockTaskModel *ALAPNonBlockScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ALAPNonBlockScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *ALAPNonBlockScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int ALAPNonBlockScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *ALAPNonBlockScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void ALAPNonBlockScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-NonBlock] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ALAPNonBlockScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ALAP-NonBlock] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ALAPNonBlockScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ALAPNonBlockScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
    }

    MRTKernel *ALAPNonBlockScheduler::getKernel() {
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

    void ALAPNonBlockScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-NonBlock] newRun - 仿真开始");

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();
        _energy_depleted = false;  // ⭐ 重置能量耗尽标志

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

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ALAP-NonBlock] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ALAPNonBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-NonBlock] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ALAP-NonBlock] ===== ALAP-NonBlock调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ALAPNonBlockScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-NonBlock] 任务结束: ") + getTaskName(task));

        // 从就绪队列移除
        removeFromReadyQueue(task);

        // 从运行任务映射中移除
        for (auto &pair : _running_tasks) {
            if (pair.second == task) {
                pair.second = nullptr;
                break;
            }
        }

        // 从能量扣除记录中移除（任务结束后可以重新扣除）
        _energy_deducted_tasks.erase(task);

        // 打印能量消耗统计
        auto it = _energy_accounts.find(task);
        if (it != _energy_accounts.end()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-NonBlock] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-NonBlock] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 注意：任务结束后的调度由tick事件自动处理，不在此处调用dispatch()
        // 这样可以避免在任务对象部分销毁时访问导致的崩溃

    }

    bool ALAPNonBlockScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 过期任务清理 - 清理超过截止期的旧任务实例
    // =====================================================

    void ALAPNonBlockScheduler::cleanupExpiredTasks() {
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
                ALAPNonBlockTaskModel *model = getTaskModel(task);
                if (!model) continue;

                Tick arrival = task->getArrival();
                Tick deadline = arrival + Tick(model->getPeriod());

                if (deadline <= current_time) {
                    to_suspend.push_back(task);
                    SCHEDULER_LOG_INFO("💀 [ALAP-NonBlock] 过期任务运行中，将挂起: " +
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
            ALAPNonBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick arrival = task->getArrival();
            Tick deadline = arrival + Tick(model->getPeriod());

            if (deadline <= current_time) {
                expired.push_back(task);
                SCHEDULER_LOG_INFO("🧹 [ALAP-NonBlock] 清理过期任务: " +
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
    }

    // =====================================================
    // ALAP时序门控（阶段一）
    // =====================================================

    bool ALAPNonBlockScheduler::checkALAPTimingGate() {
        // ⭐ 关键修复：收集所有任务（ready + running）来计算全局min_slack
        std::vector<AbsRTTask *> all_tasks;

        // 添加ready queue中的任务
        for (AbsRTTask *task : _ready_queue) {
            if (task) all_tasks.push_back(task);
        }

        // 添加运行中的任务
        if (_kernel) {
            const auto& running_tasks = _kernel->getCurrentExecutingTasks();
            for (const auto& map_pair : running_tasks) {
                AbsRTTask *task = map_pair.second;
                if (task && task->isExecuting()) {
                    all_tasks.push_back(task);
                }
            }
        }

        if (all_tasks.empty()) {
            return true;  // 没有任务，通过门控
        }

        Tick current_time = SIMUL.getTime();
        Tick min_slack = Tick(-1);

        // 计算所有任务的Slack，找最小值
        for (AbsRTTask *task : all_tasks) {
            if (!task) continue;

            // 异常处理：防止访问已删除的任务
            if (!task->isActive()) {
                continue;
            }

            Tick slack;
            try {
                slack = calculateSlackForTask(task);
            } catch (...) {
                continue;  // 跳过计算失败的任务
            }

            if (min_slack < 0 || slack < min_slack) {
                min_slack = slack;
            }
        }

        // 门控逻辑
        if (min_slack > 0) {
            SCHEDULER_LOG_INFO("⏸️  [ALAP-NonBlock] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ALAP-NonBlock] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }

    MetaSim::Tick ALAPNonBlockScheduler::calculateSlackForTask(AbsRTTask *task) {
        if (!task) return MetaSim::Tick(0);

        Tick current_time = SIMUL.getTime();
        Tick arrival = task->getArrival();
        int period_int = task->getPeriod();
        Tick period = Tick(period_int > 0 ? period_int : 100);
        Tick absolute_deadline = arrival + period;

        double remaining_double = task->getRemainingWCET();
        Tick remaining = Tick(remaining_double);
        Tick slack = absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_DEBUG("🧮 [ALAP-NonBlock] Slack计算: " +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " => slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        return slack;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void ALAPNonBlockScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ALAP-NonBlock] ===== ALAP-NonBlock调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO(std::string("  ALAP强制休眠次数: ") + std::to_string(_stats.total_alap_forced_idle));
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ALAPNonBlockScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ALAPNonBlockScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void ALAPNonBlockScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ALAP-NonBlock] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-NonBlock] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
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
        //             SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-NonBlock] Tick事件: 扣除运行中任务能量 ") +
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
                SCHEDULER_LOG_WARNING(std::string("⚡ [ALAP-NonBlock] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-NonBlock] 任务能量充足: ") +
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

            SCHEDULER_LOG_INFO(std::string("🛑 [ALAP-NonBlock] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法��断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            _kernel->suspend(task);

            // ⭐ 取消该任务的能量检查事件，防止继续扣除能量
//             auto it = _energy_check_events.find(task);
//             if (it != _energy_check_events.end()) {
//                 // 从map中移除，但不删除事件对象（它会自然结束）
//                 _energy_check_events.erase(it);
//                 SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-NonBlock] 已取消任务的能量检查事件: ") + getTaskName(task));
//             }

            SCHEDULER_LOG_INFO(std::string("⏸️ [ALAP-NonBlock] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-NonBlock] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }
} // namespace RTSim
