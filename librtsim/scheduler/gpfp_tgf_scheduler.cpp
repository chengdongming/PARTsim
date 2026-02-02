// gpfp_tgf_scheduler.cpp - TGF (Tick-based Greedy First) Scheduler Implementation
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
#include <rtsim/scheduler/gpfp_tgf_scheduler.hpp>
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
    // TGFTickEvent 实现
    // =====================================================

    TGFTickEvent::TGFTickEvent(TGFScheduler *scheduler)
        : MetaSim::Event("TGFTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行
    }

    void TGFTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [TGF] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // TGFEnergyCheckEvent 实现 - 运行时能量检查
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling���理
    // =====================================================

    /*
    TGFEnergyCheckEvent::TGFEnergyCheckEvent(TGFScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("TGFEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void TGFEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TGF] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TGF] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TGF] 能量检查：任务已停止执行，不再扣除能量: ") +
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
        TGFTaskModel *task_model = _scheduler->getTaskModel(_task);
        if (task_model && _ms_executed >= task_model->getWCET()) {
            SCHEDULER_LOG_INFO(std::string("✅ [TGF] 任务已达到WCET，完成执行: ") +
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
        // - TGFEnergyCheckEvent: 负责运行中任务的续期能量扣除
        
        // 检查是否有足够能量续期1ms
        // ⭐ V35修复：当能量 <= 1ms能耗时，立即中断任务
        // 避免在能量恰好等于单位能耗时继续执行，导致下个Tick能量为负
        if (current_energy <= unit_energy + EPSILON) {
            // ⭐ 能量不足以支撑下一个1ms，立即中断任务（不扣除能量）
            SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 能量刚好耗尽或不足，立即中断任务: ") +
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
                SCHEDULER_LOG_INFO(std::string("⚠️ [TGF] 任务因能量不足被挂起: ") + _scheduler->getTaskName(_task));
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

        SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 运行中任务续期: ") +
                           _scheduler->getTaskName(_task) + " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " " + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }
    */  // ⭐ V40重构：TGFEnergyCheckEvent已删除

    // =====================================================
    // TGFTaskModel 实现
    // =====================================================

    TGFTaskModel::TGFTaskModel(AbsRTTask *t, int period, int wcet,
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

    TGFTaskModel::~TGFTaskModel() {}

    Tick TGFTaskModel::getPriority() const {
        return _rm_priority;
    }

    void TGFTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void TGFTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // TGFScheduler 实现
    // =====================================================

    TGFScheduler::TGFScheduler()
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

        SCHEDULER_LOG_INFO("🚀 [TGF] TGF Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [TGF] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [TGF] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [TGF] 开始时间偏移: ") +
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

                    SCHEDULER_LOG_INFO(std::string("☀️ [TGF] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [TGF] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy > 0) {
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [TGF] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy > 0) {
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [TGF] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [TGF] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new TGFTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [TGF] TGF Scheduler 初始化完成");
    }

    TGFScheduler::TGFScheduler(const std::vector<std::string> &params)
        : TGFScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<TGFScheduler>
        TGFScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<TGFScheduler>(params);
    }

    TGFScheduler::~TGFScheduler() {
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
    // 核心调度逻辑 - TGF算法的核心
    // =====================================================

    void TGFScheduler::performTickScheduling() {
        SCHEDULER_LOG_INFO(std::string("🔄 [TGF] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms =====");
        SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

        // ⭐ Bug修复3：能量耗尽时跳过调度
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [TGF] 能量已耗尽，跳过Tick调度"));
            return;
        }

        _stats.total_tick_count++;

        // ⭐ V42修复：清空当前tick新调度任务标记
        // 这样只有本次tick中新调度的任务才会被跳过续期扣除
        _newly_dispatched_this_tick.clear();

        Tick current_time = SIMUL.getTime();

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

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第2步：处理运行中任务的续期能量 ==========
        // ⭐ 重构：在tick边界扣除运行任务的续期能量（替代TGFEnergyCheckEvent）
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
                    SCHEDULER_LOG_DEBUG(std::string("⏭️ [TGF] 跳过新任务的续期扣除: ") + getTaskName(task));
                    continue;
                }

                double unit_energy = calculateUnitEnergyForTask(task);

                // 检查是否有足够能量续期1ms
                const double EPSILON = 1e-9;
                if (_current_energy < unit_energy - EPSILON) {
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

            // ⭐ V42修复：不在dispatch开始时清空标记
            // 标记应该在dispatch后自然累积，只在tick边界事件之间保持
            // 这样可以防止已调度任务在下一个tick被重新扣除

            // ⭐ TGF关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
            int dispatch_attempts = 0;
            const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

            while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
                // 检查是否所有CPU都已填满
                bool all_cpus_full = true;
                for (auto &map_pair : _running_tasks) {
                    if (map_pair.second == nullptr) {
                        all_cpus_full = false;
                        break;
                    }
                }

                if (all_cpus_full) {
                    SCHEDULER_LOG_DEBUG("✅ [TGF] 所有CPU已填满，停止调度");
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
                    SCHEDULER_LOG_DEBUG("⏹️ [TGF] 无更多任务可调度，停止dispatch循环");
                    break;
                }

                SCHEDULER_LOG_DEBUG(std::string("🔄 [TGF] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
            }

            if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_WARNING("⚠️ [TGF] dispatch循环达到最大迭代次数，可能存在bug");
            }

            // ⭐ V41重构：能量扣除已在getTaskN()中立即完成，此处不需要额外扣除
            // ⭐ V42修复：不再在此处清空标记，否则会导致中途dispatch的任务被重复扣除
            // 标记会自然累积，只有在任务真正结束或被抢占时才应该移除
        }

        SCHEDULER_LOG_INFO("✅ Tick " +
                           std::to_string(static_cast<int64_t>(current_time)) +
                           "ms 完成, 剩余能量: " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    void TGFScheduler::schedule() {
        // TGF依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [TGF] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *TGFScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [TGF] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // ⭐ 核心：不在这里收集能量，能量收集在tick边界完成

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [TGF] getFirst: 就绪队列为空");
            return nullptr;
        }

        AbsRTTask *first_task = _ready_queue.front();
        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [TGF] getFirst: 队列首任务为空");
            return nullptr;
        }

        // ⭐ 核心：即时能量判断（当前能量 >= 1ms能耗）
        double unit_energy = calculateUnitEnergyForTask(first_task);

        if (_current_energy < unit_energy) {
            SCHEDULER_LOG_INFO(std::string("❌ [TGF] getFirst: 能量不足") +
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

    AbsRTTask *TGFScheduler::getTaskN(unsigned int n) {
                // STAR Critical fix: if energy depleted, don\'t schedule any tasks
        if (_energy_depleted) {
        // STAR Critical fix: if energy depleted, don't schedule any tasks
        if (_energy_depleted) {
            SCHEDULER_LOG_DEBUG(std::string("STAR [TGF] getTaskN: Energy depleted") +
                               " n=" + std::to_string(n) +
                               " energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

            SCHEDULER_LOG_DEBUG(std::string("STAR [TGF] getTaskN: Energy depleted, not scheduling task") +
                               " n=" + std::to_string(n) +
                               " current_energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

        SCHEDULER_LOG_DEBUG(std::string("🔍 [TGF] getTaskN(") + std::to_string(n) + ") 被调用" +
                           " 当前能量: " + std::to_string(_current_energy) + "J" +
                           " 已调度能耗=" + std::to_string(_dispatching_tasks_total_energy) + "J");

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [TGF] getTaskN: 就绪队列为空");
            return nullptr;
        }


        // ⭐ 级联调度：遍历就绪队列，运行中任务也要检查能量
        unsigned int ready_index = 0;
        unsigned int original_target_n = n;  // 记住最初请求的n值
        const double EPSILON = 1e-9;
        bool skipped_energy_insufficient = false;  // 是否跳过了能量不足的任务

        std::cout << "[DEBUG] TGF::getTaskN(" << n << ") - ready_queue.size()=" << _ready_queue.size() << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            AbsRTTask *task = _ready_queue[i];

            if (!task) {
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
            // ⭐ V29.1修复：运行中任务的续期由TGFEnergyCheckEvent处理
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
                // ⭐ 计算任务的1ms能耗
                double unit_energy = calculateUnitEnergyForTask(task);
                double available_energy = _current_energy - _dispatching_tasks_total_energy;

                // ⭐ 贪心策略：如果能量不足，跳过这个任务���继续查找后面的任务
                if (available_energy < unit_energy - EPSILON) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [TGF] 任务能量不足，跳过（贪心策略）") +
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
                            SCHEDULER_LOG_DEBUG(std::string("  [TGF] 贪心搜索：跳过已调度任务: ") + getTaskName(next_task));
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

                        double next_unit_energy = calculateUnitEnergyForTask(next_task);
                        double next_available = _current_energy - _dispatching_tasks_total_energy;

                        if (next_available >= next_unit_energy - EPSILON) {
                            // ⭐ 找到能量足够的后续任务，调度它！
                            // ⭐ V41修复：立即扣除初始能量（仅一次）
                            if (_counted_tasks_in_dispatch.find(next_task) == _counted_tasks_in_dispatch.end()) {
                                _current_energy -= next_unit_energy;
                                _stats.total_energy_consumed += next_unit_energy;
                                _counted_tasks_in_dispatch.insert(next_task);
                                _newly_dispatched_this_tick.insert(next_task);  // ⭐ V42：标记为当前tick新调度

                                SCHEDULER_LOG_INFO(std::string("✅ [TGF] 贪心策略：调度后续任务并扣除能量") +
                                                  " 替换=" + getTaskName(task) +
                                                  " → " + getTaskName(next_task) +
                                                  " -" + std::to_string(next_unit_energy * 1000) + " mJ → " +
                                                  std::to_string(_current_energy * 1000) + " mJ");
                            }

                            return next_task;
                        }
                    }

                    // 没有找到能量足够的任务
                    SCHEDULER_LOG_INFO(std::string("⚠️ [TGF] 贪心策略：未找到能量足够的任务"));
                    return nullptr;
                }

                // ⭐ 能量足够，正常调度
                // ⭐ V41修复���对于新任务（非运行中），立即扣除初始能量
                // 这解决了tick边界处理时序问题（任务在tick结束时被调度，但能量在下一tick才扣除）
                if (!is_running_check) {
                    // 新任务：检查是否已扣除过初始能量
                    if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                        // 首次调度此任务，扣除初始能量
                        _current_energy -= unit_energy;
                        _stats.total_energy_consumed += unit_energy;
                        _counted_tasks_in_dispatch.insert(task);  // 标记已扣除
                        _newly_dispatched_this_tick.insert(task);  // ⭐ V42：标记为当前tick新调度

                        SCHEDULER_LOG_INFO(std::string("✅ [TGF] 新任务立即扣除初始能量: ") + getTaskName(task) +
                                          " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                                          std::to_string(_current_energy * 1000) + " mJ");
                    }
                    // 否则已扣除过，直接返回任务
                } else {
                    // 运行中任务：标记用于tick边界续期扣除
                    if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                        _counted_tasks_in_dispatch.insert(task);
                    }
                }
                return task;
            } else {
                // ⭐ V32关键修复：不是我们要找的第n个任务，继续寻找
                ready_index++;
            }
        }

        return nullptr;
    }

    // =====================================================
    // notify - 每ms逐次扣减能耗（TGF核心逻辑）
    // =====================================================

    void TGFScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否足够
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [TGF] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [TGF] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void TGFScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [TGF] 添加任务: ") + getTaskName(task));
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
        TGFTaskModel *model = new TGFTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [TGF] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void TGFScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [TGF] 移除任务: ") + getTaskName(task));

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

        SCHEDULER_LOG_INFO(std::string("✅ [TGF] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void TGFScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [TGF] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
            checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void TGFScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [TGF] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void TGFScheduler::checkAndPreemptOnAllCPUs() {
        for (auto &map_pair : _running_tasks) {
            CPU *cpu = map_pair.first;
            AbsRTTask *running_task = map_pair.second;

            if (!running_task) {
                continue;
            }

            AbsRTTask *highest = getHighestPriorityTaskFromReadyQueue();
            if (!highest) {
                continue;
            }

            if (shouldPreempt(cpu, highest)) {
                SCHEDULER_LOG_INFO(std::string("🔄 [TGF] 抢占CPU: ") +
                                  " 挂起低优先级任务=" + getTaskName(running_task) +
                                  " 调度高优先级任务=" + getTaskName(highest));

                // ⭐ 实际抢占逻辑：挂起当前运行的任务
                // suspend会自动调用deschedule()并将任务重新放回调度队列
                if (_kernel) {
                    _kernel->suspend(running_task);
                    SCHEDULER_LOG_DEBUG(std::string("⏸️ [TGF] 已挂起任务: ") + getTaskName(running_task));
                } else {
                    SCHEDULER_LOG_WARNING("⚠️ [TGF] 抢占失败：_kernel为nullptr");
                }
            }
        }
    }

    bool TGFScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        TGFTaskModel *running_model = getTaskModel(running_task);
        TGFTaskModel *new_model = getTaskModel(new_task);

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

    void TGFScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [TGF] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void TGFScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [TGF] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
    }

    void TGFScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TGF] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        TGFTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            TGFTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [TGF] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void TGFScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [TGF] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void TGFScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [TGF] 任务加入等待队列: ") + getTaskName(task));
    }

    void TGFScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool TGFScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool TGFScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *TGFScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double TGFScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        TGFTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double TGFScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double TGFScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    double TGFScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        TGFTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] calculateTotalEnergyForTask: 任务模型不存在");
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

    double TGFScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [TGF] 功率计算: ") +
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
    void TGFScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [TGF] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        TGFEnergyCheckEvent *evt = new TGFEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
    }

    void TGFScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // 删除事件对象

            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [TGF] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }
    */  // ⭐ V40重构：能量检查方法已删除

    // =====================================================
    // 能量收集方法
    // =====================================================

    double TGFScheduler::collectSolarEnergy(Tick current_time) {
        if (!_use_real_solar_data) {
            return 0.0;
        }

        int64_t current_ms = static_cast<int64_t>(current_time);

        // 计算自上次收集以来的时间
        Tick elapsed = current_time - _last_collection_time;

        if (elapsed <= 0) {
            return 0.0;
        }

        // 获取当前辐照度
        double irradiance = getSolarIrradiance(current_ms);

        // 计算收集能量
        double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
        double energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;

        // 更新最后收集时间
        _last_collection_time = current_time;

        return energy;
    }

    double TGFScheduler::getSolarIrradiance(int64_t time_ms) {
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
        int64_t minute_of_day = (actual_time_ms % 86400000) / 60000;  // 0-1439

        int line_number = minute_of_day + 2;  // +2跳过标题行

        std::ifstream file(_solar_data_file);
        if (!file.is_open()) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [TGF] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [TGF] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void TGFScheduler::scheduleNextTick() {
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

    TGFTaskModel *TGFScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string TGFScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *TGFScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int TGFScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *TGFScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void TGFScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [TGF] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [TGF] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void TGFScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [TGF] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void TGFScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void TGFScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
    }

    MRTKernel *TGFScheduler::getKernel() {
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

    void TGFScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [TGF] newRun - 仿真开始");

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

        SCHEDULER_LOG_INFO(std::string("💰 [TGF] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void TGFScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [TGF] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [TGF] ===== TGF调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void TGFScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [TGF] 任务结束: ") + getTaskName(task));

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
            SCHEDULER_LOG_INFO(std::string("📊 [TGF] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [TGF] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [TGF] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
            SCHEDULER_LOG_INFO("🔄 [TGF] 任务结束，触发立即调度");
            _kernel->dispatch();
        }

    }

    bool TGFScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void TGFScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [TGF] ===== TGF调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string TGFScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> TGFScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void TGFScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [TGF] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [TGF] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
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
        //             SCHEDULER_LOG_INFO(std::string("⚡ [TGF] Tick事件: 扣除运行中任务能量 ") +
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
                SCHEDULER_LOG_WARNING(std::string("⚡ [TGF] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [TGF] 任务能量充足: ") +
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

            SCHEDULER_LOG_INFO(std::string("🛑 [TGF] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法��断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            _kernel->suspend(task);

            // ⭐ 取消该任务的能量检查事件，防止继续扣除能量
//             auto it = _energy_check_events.find(task);
//             if (it != _energy_check_events.end()) {
//                 // 从map中移除，但不删除事件对象（它会自然结束）
//                 _energy_check_events.erase(it);
//                 SCHEDULER_LOG_DEBUG(std::string("⚠️ [TGF] 已取消任务的能量检查事件: ") + getTaskName(task));
//             }

            SCHEDULER_LOG_INFO(std::string("⏸️ [TGF] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [TGF] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }
} // namespace RTSim
