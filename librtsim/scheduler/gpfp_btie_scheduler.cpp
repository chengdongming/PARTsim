// gpfp_btie_scheduler.cpp - BTIE (Batch Tick-based Instant Energy-aware) Scheduler Implementation
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
#include <rtsim/scheduler/gpfp_btie_scheduler.hpp>
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
    // BTIETickEvent 实现
    // =====================================================

    BTIETickEvent::BTIETickEvent(BTIEScheduler *scheduler)
        : MetaSim::Event("BTIETickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行
    }

    void BTIETickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [BTIE] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // BTIEEnergyCheckEvent 实现 - 运行时能量检查
    // =====================================================

    BTIEEnergyCheckEvent::BTIEEnergyCheckEvent(BTIEScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("BTIEEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void BTIEEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // 🔍 调试：记录能量检���事件触发时间
        Tick actual_trigger_time = SIMUL.getTime();
        SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] 能量检查事件触发: ") +
                           _scheduler->getTaskName(_task) +
                           " 触发时间=" + std::to_string(static_cast<int64_t>(actual_trigger_time)) + "ms" +
                           " _ms_executed=" + std::to_string(_ms_executed));

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 能量检查：事件已失效，停止检查"));
            return;
        }

        // 计算任务每1ms的能耗
        double unit_energy = _scheduler->calculateUnitEnergyForTask(_task);
        double current_energy = _scheduler->getCurrentEnergy();
        const double EPSILON = 1e-9;

        _ms_executed++;

        // ⭐ 检查任务是���仍在执行状态
        // 如果任务已被中断（suspend），则不应再扣除能量
        if (!_task->isExecuting()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 能量检查：任务已停止执行，不再扣除能量: ") +
                               _scheduler->getTaskName(_task) + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        BTIETaskModel *task_model = _scheduler->getTaskModel(_task);

        // 🔍 调试日志：检查WCET
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_DEBUG(std::string("🔍 [BTIE] WCET���查: ") +
                           task_name + " 已执行=" + std::to_string(_ms_executed) +
                           "ms task_model=" + (task_model ? "有效" : "NULL"));

        if (task_model) {
            int wcet = task_model->getWCET();
            SCHEDULER_LOG_DEBUG(std::string("🔍 [BTIE] WCET值: ") +
                               std::to_string(wcet) + "ms 判断: " +
                               std::to_string(_ms_executed) + " >= " + std::to_string(wcet) +
                               " = " + (_ms_executed >= wcet ? "TRUE" : "FALSE"));

            if (_ms_executed >= wcet) {
                SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 任务已达到WCET，完成执行: ") +
                                   task_name + " 已执行=" + std::to_string(_ms_executed) +
                                   "ms WCET=" + std::to_string(wcet) + "ms");
                // ⭐ 关键修复：标记任务已达到WCET，防止批量调度重复扣除能量
                _scheduler->_tasks_completed_wcet.insert(_task);
                SCHEDULER_LOG_INFO(std::string("🏁 [BTIE] 标记任务已完成WCET: ") +
                                   task_name);

                // ⭐ 关键修复：不直接调用onEnd()，而是让任务自然结束
                // 终止能量检查事件，让内核在下一个tick时检测到任务完成并调用onEnd()
                // 避免在能量检查事件中调用onEnd()导致的"No CPU"崩溃
                SCHEDULER_LOG_INFO(std::string("🛑 [BTIE] 任务达到WCET，终止能量检查事件: ") + task_name);

                // 任务已完成，不再检查能量预扣，也不重新调度事件
                return;
            }
        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] WCET检查失败：找不到TaskModel ") + task_name);
        }

        // ⭐ BTIE关键修复：在扣除能量之前检查是否有足够能量（与TIE保持一致）
        // 设计原则：
        // - 批量调度时进行"全有或全无"门槛检查，但不预扣能量
        // - 能量检查事件在实际执行时每1ms扣除一次能量
        // - ⭐ 关键：先检查能量是否足够再扣除，不足则立即中断任务

        // 检查是否有足够能量续期1ms
        if (current_energy < unit_energy - EPSILON) {
            // ❌ 能量不足，立即中断任务
            SCHEDULER_LOG_WARNING(std::string("⚡ [BTIE] 续期能量不足，立即中断任务: ") +
                                 _scheduler->getTaskName(_task) +
                                 " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                 " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                                 " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;

            // ⭐ 关键修复：立即suspend任务（与TIE保持一致）
            if (_scheduler->_kernel && _task->isExecuting()) {
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_WARNING(std::string("🛑 [BTIE] 任务因能量不足被挂起: ") +
                                     _scheduler->getTaskName(_task));
            }

            // 不重新调度能量检查事件
            return;
        }

            // ⭐ 关键修复：能量已在批量调度时预扣，不再重复扣除
            // 只检查预扣能量是否耗尽，不再扣除实时能量
            if (_scheduler->_current_energy < unit_energy * 0.1) {
                // ❌ 预扣能量已耗尽，立即中断任务
                SCHEDULER_LOG_WARNING(std::string("⚡ [BTIE] 预扣能量已耗尽，中断任务: ") +
                                     _scheduler->getTaskName(_task) +
                                     " 剩余=" + std::to_string(_scheduler->_current_energy * 1000) + " mJ");

                _scheduler->_energy_depleted = true;
                if (_scheduler->_kernel && _task->isExecuting()) {
                    _scheduler->_kernel->suspend(_task);
                }
                return;
            }

            // ✅ 预扣能量充足，只记录日志，不扣除
            SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 预扣能量充足，任务继续: ") +
                               _scheduler->getTaskName(_task) +
                               " 剩余=" + std::to_string(_scheduler->_current_energy * 1000) + " mJ");

        // ✅ 能量充足，继续执行
        SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 能量充足，任务继续: ") +
                           _scheduler->getTaskName(_task) +
                           " 剩余=" + std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }

    // =====================================================
    // BTIETaskModel 实现
    // =====================================================

    BTIETaskModel::BTIETaskModel(AbsRTTask *t, int period, int wcet,
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

    BTIETaskModel::~BTIETaskModel() {}

    Tick BTIETaskModel::getPriority() const {
        return _rm_priority;
    }

    void BTIETaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void BTIETaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // BTIEScheduler 实现
    // =====================================================

    BTIEScheduler::BTIEScheduler()
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
          _kernel(nullptr),
          _batch_scheduled_this_tick(false),
          _energy_depleted(false),
          _current_batch_size(0) {

        SCHEDULER_LOG_INFO("🚀 [BTIE] TIE Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [BTIE] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [BTIE] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [BTIE] 开始时间偏移: ") +
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

                    SCHEDULER_LOG_INFO(std::string("☀️ [BTIE] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy > 0) {
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [BTIE] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy > 0) {
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [BTIE] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [BTIE] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new BTIETickEvent(this);

        SCHEDULER_LOG_INFO("✅ [BTIE] TIE Scheduler 初始化完成");
    }

    BTIEScheduler::BTIEScheduler(const std::vector<std::string> &params)
        : BTIEScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<BTIEScheduler>
        BTIEScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<BTIEScheduler>(params);
    }

    BTIEScheduler::~BTIEScheduler() {
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

    int BTIEScheduler::calculateBatchSize() {
        // k = min(CPU核心总数, 就绪队列任务数)
        ConfigManager &configMgr = ConfigManager::getInstance();
        int total_cpus = configMgr.getNumCores();
        int ready_tasks = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(total_cpus, ready_tasks);

        SCHEDULER_LOG_DEBUG(std::string("📊 [BTIE] calculateBatchSize: ") +
                           "CPU核心数=" + std::to_string(total_cpus) +
                           " 就绪任务=" + std::to_string(ready_tasks) +
                           " 批量k=" + std::to_string(batch_size));

        return batch_size;
    }


    void BTIEScheduler::executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy) {
        // ⭐ BTIE核心：批量调度时一次性扣减k个任务的1ms能耗
        // 当前时刻能量 = 上一时刻结余 + 本次充电能量 - 已消耗能量 - 本次批量调度能耗
        double old_energy = _current_energy;
        _current_energy -= total_energy;
        _stats.total_energy_consumed += total_energy;

        SCHEDULER_LOG_INFO(std::string("📋 [BTIE] 批量调度: ") +
                           "任务数=" + std::to_string(tasks.size()) +
                           " 总能耗=" + std::to_string(total_energy * 1000) + " mJ" +
                           " 能量=" + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    // =====================================================
    // 核心调度逻辑 - BTIE批量调度算法
    // =====================================================

    void BTIEScheduler::performTickScheduling() {
        SCHEDULER_LOG_DEBUG(std::string("🔄 [BTIE] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");

        // ⭐ Bug修复3：能量耗尽时跳过���度
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，跳过Tick调度"));
            return;  // 不进行任何调度，包括中断检查
        }

        _stats.total_tick_count++;

        // ⭐ BTIE核心：在tick边界（即上一tick结束、本tick开始）收集能量
        // 收集太阳能（从上次tick到现在）
        Tick current_time = SIMUL.getTime();
        Tick elapsed = current_time - _last_tick_time;

        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO(std::string("☀️ [BTIE] Tick边界收集能量: ") +
                                   std::to_string(harvested) + "J" +
                                   " 当前能量: " + std::to_string(_current_energy) + "J" +
                                   " 经过时间: " + std::to_string(static_cast<int64_t>(elapsed)) + "ms");
            }
        }

        _last_tick_time = current_time;

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ⭐ BTIE修复：真正的批量调度 - 收集所有任务（运行中+就绪队列）
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [BTIE] _kernel为nullptr，跳过批量调度");
                return;
            }
        }

        // ⭐ Bug #5修复：能量已耗尽，跳过所有调度
        const double ENERGY_EPSILON = 1e-9;
        if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，跳过批量调度"));
            return;  // 不再调度任何任务
        }

        // ⭐ BTIE关键修复：采用正确的能量扣除逻辑（后扣方式）

        // 1. ⭐ BTIE关键：从kernel获取真正在运行的任务
        // 因为这些才是实际消耗能量的任务
        std::vector<AbsRTTask *> running_task_list;
        double energy_to_deduct = 0.0;

        const auto& running_tasks = _kernel->getCurrentExecutingTasks();

        // 🔍 调试：输出_m_currExe的内容
        SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] _m_currExe内容 (") +
                           std::to_string(running_tasks.size()) + "个任务) @ " +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms:");
        for (const auto& map_pair : running_tasks) {
            if (map_pair.second) {
                bool is_executing = map_pair.second->isExecuting();
                SCHEDULER_LOG_INFO(std::string("  [CPU ") +
                                   std::to_string(reinterpret_cast<uintptr_t>(map_pair.first) % 1000) +
                                   "] → " + getTaskName(map_pair.second) +
                                   " isExecuting=" + (is_executing ? "TRUE" : "FALSE"));
            }
        }

        for (const auto& map_pair : running_tasks) {
            AbsRTTask* task = map_pair.second;
            // ⭐ 关键修复：只统计真正在执行的任务，过滤已达到WCET的任务
            // _m_currExe可能包含已完成的任务（isExecuting=TRUE，但已达到WCET）
            // 使用_tasks_completed_wcet集合来判断任务是否真正完成
            if (task && task->isExecuting() && _tasks_completed_wcet.find(task) == _tasks_completed_wcet.end()) {
                running_task_list.push_back(task);
                double unit_energy = calculateUnitEnergyForTask(task);
                energy_to_deduct += unit_energy;
            }
        }

        // 如果kernel中还没有运行任务（第一次调度），检查_current_batch_tasks
        if (running_task_list.empty() && !_current_batch_tasks.empty()) {
            for (AbsRTTask* task : _current_batch_tasks) {
                // ⭐ 关键修复：过滤已达到WCET的任务
                if (task && task->isExecuting() && _tasks_completed_wcet.find(task) == _tasks_completed_wcet.end()) {
                    running_task_list.push_back(task);
                    double unit_energy = calculateUnitEnergyForTask(task);
                    energy_to_deduct += unit_energy;
                }
            }
        }

        // ⭐ 预扣模式：能量将在批量调度时统一扣除，这里不再扣除运行任务的能量
        // (运行任务的能量已在上一次批量调度时预扣，新任务的能量将在本次批量调度时预扣)
        if (false) {  // Disabled in pre-deduction mode
            const double EPSILON = 1e-9;
            if (_current_energy >= energy_to_deduct - EPSILON) {
                // ⭐ Bug #5修复：能量不足时，强制结束所有运行中任务
                SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] 能量不足，强制结束所有运行中任务: ") +
                                        "需要=" + std::to_string(energy_to_deduct * 1000) + " mJ " +
                                        "当前=" + std::to_string(_current_energy * 1000) + " mJ " +
                                        "运行中任务数=" + std::to_string(running_task_list.size()));

                // 设置能量耗尽标志
                _energy_depleted = true;

                // 强制结束所有运行中任务（直接清理，不调用onTaskEnd避免增加完成计数）
                std::vector<AbsRTTask *> tasks_to_end = running_task_list;
                for (auto* task : tasks_to_end) {
                    SCHEDULER_LOG_INFO(std::string("🛑 [BTIE] 强制结束任务: ") +
                                       getTaskName(task) + " (能量不足)");

                    // 从就绪队列移除
                    removeFromReadyQueue(task);

                    // 从运行任务映射中移除
                    for (auto &pair : _running_tasks) {
                        if (pair.second == task) {
                            pair.second = nullptr;
                            break;
                        }
                    }

                    // 不增加任务完成计数（这不是真正的完成）
                    // 不触发立即调度（能量已耗尽）
                }

                // 记录实际剩余能量为0
                double old_energy = _current_energy;
                _current_energy = 0.0;
                _stats.total_energy_consumed += old_energy;

                SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 能量耗尽: ") +
                                   std::to_string(old_energy * 1000) + " mJ → 0.000000 mJ");

                // ⭐ 关键修复：能量耗尽后，直接返回，不继续调度新任务
                return;
            }
        }

        // ⭐ 关键修复：如果能量已耗尽，不调度新任务
        if (_energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，跳过批量调度") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        // ⭐ Bug #9修复：不在批量调度决策之前调用checkAndInterruptRunningTasks()
        // 因为那时_batch_scheduled_this_tick还没有设置，检查结果会被覆盖
        // 只在批量调度决策之后调用一次，让它根据_batch_scheduled_this_tick决定是否检查

        // ⭐ 关键修复：中断任务后，清空上一tick的批量任务队列
        // 并且如果能量已耗尽（在checkAndInterruptRunningTasks中设置的），直接返回
        _current_batch_tasks.clear();
        _current_batch_size = 0;

        if (_energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 检测到能量在运行时检查中耗尽，跳过批量调度") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        // 3. ⭐ 选择K个新任务（不扣除它们的能量）
        size_t running_count = running_task_list.size();

        // ⭐ 修复硬编码：从kernel获取CPU数量
        // getCurrentExecutingTasks()返回_m_currExe的引用，其大小即为CPU总数
        size_t total_cpus = _kernel->getCurrentExecutingTasks().size();

        size_t free_cpus = total_cpus - running_count;

        // ⭐ Bug #1修复：调度所有就绪任务，而不是限制为空闲CPU数
        // 这样确保所有任务（包括低优先级task_4）都能被调度
        size_t K = _ready_queue.size();

        // ⭐ Bug #4修复：计算实际能调度的新任务数（考虑CPU限制）
        // 实际可调度 = min(K - 运行中任务数, 空闲CPU数)
        int actual_new_tasks_can_schedule = static_cast<int>(K) - static_cast<int>(running_count);
        if (actual_new_tasks_can_schedule < 0) actual_new_tasks_can_schedule = 0;
        if (actual_new_tasks_can_schedule > static_cast<int>(free_cpus)) {
            actual_new_tasks_can_schedule = static_cast<int>(free_cpus);
        }

        std::vector<AbsRTTask *> new_tasks_to_schedule;
        std::vector<AbsRTTask *> all_ready_tasks;
        if (K > 0) {
            // ⭐ 关键修复：清理_ready_queue中过期的周期性任务实例（同步TIE修复）
            // 周期性任务的旧实例在完成后会留在队列中，需要定期清理
            Tick current_time = SIMUL.getTime();
            _ready_queue.erase(
                std::remove_if(_ready_queue.begin(), _ready_queue.end(),
                    [this, current_time](AbsRTTask *task) {
                        if (!task) return true;
                        // 移除不活动的任务
                        if (!task->isActive()) {
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [BTIE] 清理不活动任务: ") + getTaskName(task));
                            return true;
                        }
                        // ⭐ 移除过期的周期性任务实例：使用getDeadline()获取绝对截止时间
                        Tick deadline = task->getDeadline();
                        if (deadline < current_time) {
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [BTIE] 清理过期任务实例: ") +
                                           getTaskName(task) +
                                           " 截止=" + std::to_string(static_cast<int64_t>(deadline)) +
                                           " 当前=" + std::to_string(static_cast<int64_t>(current_time)));
                            return true;
                        }
                        return false;
                    }),
                _ready_queue.end()
            );

            std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
            std::sort(sorted_ready.begin(), sorted_ready.end(),
                [](AbsRTTask* a, AbsRTTask* b) { return a->getDeadline() < b->getDeadline(); });

            // 🔍 调试：输出就绪队列内容
            SCHEDULER_LOG_INFO(std::string("📋 [BTIE] 就绪队列内容 (共") +
                               std::to_string(sorted_ready.size()) + "个任务):");
            for (size_t i = 0; i < sorted_ready.size() && i < 5; ++i) {
                SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                                   getTaskName(sorted_ready[i]) +
                                   " deadline=" + std::to_string(static_cast<int>(sorted_ready[i]->getDeadline())));
            }

            // 🔍 调试：输出运行中任务列表
            SCHEDULER_LOG_INFO(std::string("🏃 [BTIE] 运行中任务列表 (共") +
                               std::to_string(running_task_list.size()) + "个任务):");
            for (size_t i = 0; i < running_task_list.size(); ++i) {
                SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                                   getTaskName(running_task_list[i]));
            }

            // ⭐ 关键修复：排除已经在运行中的任务，避免重复调度
            std::vector<AbsRTTask *> filtered_ready;
            for (auto* task : sorted_ready) {
                bool is_running = false;
                for (auto* running_task : running_task_list) {
                    if (task == running_task) {
                        is_running = true;
                        SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 跳过已在运行中的任务: ") +
                                           getTaskName(task));
                        break;
                    }
                }
                if (!is_running) {
                    filtered_ready.push_back(task);
                }
            }

            // ⭐ Bug #4修复：只选择实际能调度的任务（从过滤后的队列）
            for (int j = 0; j < actual_new_tasks_can_schedule && j < static_cast<int>(filtered_ready.size()); ++j) {
                new_tasks_to_schedule.push_back(filtered_ready[j]);
                SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 选择新任务: ") +
                                   getTaskName(filtered_ready[j]));
            }

            // 保存所有就绪任务用于日志
            all_ready_tasks.assign(sorted_ready.begin(), sorted_ready.end());
        }

        // 3. ⭐ BTIE关键：每个tick都预扣运行任务续期+新任务的能量
        // 这样可以在能量耗尽时及时中断任务

        // 计算运行中任务的续期能���（每个tick都要续期）
        double running_tasks_renewal_energy = 0.0;
        for (auto* task : running_task_list) {
            // ⭐ 关键修复：跳过已达到WCET的任务，避免重复扣除能量
            // 因为能量检查事件在任务达到WCET时会标记完成，但kernel可能还没处理end_instance
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                SCHEDULER_LOG_INFO(std::string("⚠️ [BTIE] 批量调度：跳过已完成WCET的任务: ") +
                                   getTaskName(task) + " (能量检查事件已标记完成)");
                continue;
            }
            running_tasks_renewal_energy += calculateUnitEnergyForTask(task);
        }

        // 计算新任务的能量
        double new_tasks_energy = 0.0;
        for (auto* task : new_tasks_to_schedule) {
            new_tasks_energy += calculateUnitEnergyForTask(task);
        }

        // ⭐ BTIE总能量需求 = 运行中任务续期 + 新任务（每个tick都扣除）
        double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;

        SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 批量调度决策: ") +
                          "总CPU=" + std::to_string(total_cpus) +
                          " 运行中=" + std::to_string(running_count) +
                          " 空闲=" + std::to_string(free_cpus) +
                          " 就绪队列=" + std::to_string(_ready_queue.size()) +
                          " 选择K=" + std::to_string(K) +
                          " 实际可调度=" + std::to_string(new_tasks_to_schedule.size()) +
                          " ⭐ 运行任务能量已扣除=" + std::to_string(running_count) + "个任务" +
                          " 新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ" +
                          " 总能量需求=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                          " 当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

        // 4. ⭐ BTIE核心：批量能量判断（"全有或全无"���
        // Bug #3修复：检查总能量需求（运行中续期+新任务），确保有足夠能量才调度
        const double EPSILON = 1e-9;
        if (_current_energy >= total_energy_needed - EPSILON) {
            // 能量充足：调度新任务
            _batch_scheduled_this_tick = true;
            
            // _current_batch_tasks包含：运行中任务 + 新任务
            std::vector<AbsRTTask *> all_tasks_to_dispatch;
            for (auto* task : running_task_list) {
                // ⭐ 关键修复：跳过已达到WCET的任务
                if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                    continue;
                }
                all_tasks_to_dispatch.push_back(task);
            }
            for (auto* task : new_tasks_to_schedule) {
                all_tasks_to_dispatch.push_back(task);
            }
            
            // ⭐ BTIE关键设计："全有或全无"门槛检查，不预扣能量
            // 能量将在任务实际执行时由BTIEEnergyCheckEvent每1ms扣除

            // ⭐ 关键修复：立即预扣全部能量（实现真正的"全有或全无"）
            // 只有当前能量足够支撑整个批次时才扣除，避免逐ms批准导致的超额透支
            double old_energy = _current_energy;
            _current_energy -= total_energy_needed;
            _stats.total_energy_consumed += total_energy_needed;

            SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 批量调度门槛检查通过: ") +
                              "新任务数=" + std::to_string(new_tasks_to_schedule.size()) +
                              " 运行任务数=" + std::to_string(running_count) +
                              " 总能耗需求=" + std::to_string(total_energy_needed * 1000) + " mJ " +
                              "当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

            _current_batch_tasks = all_tasks_to_dispatch;
            _current_batch_size = all_tasks_to_dispatch.size();
            _stats.total_batch_schedules++;
            
            SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 批量调度成功: ") +
                              "运行中=" + std::to_string(running_count) +
                              " 新任务=" + std::to_string(new_tasks_to_schedule.size()) +
                              " 总任务=" + std::to_string(all_tasks_to_dispatch.size()) +
                              " 总能耗=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                              " ⭐ (运行任务能量已扣除，只调度新任务)");

            // 不调用checkAndInterruptRunningTasks()，避免潜在的segfault
        } else {
            // ❌ 能量不足：BTIE原则 - "全无"
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _stats.total_batch_skipped++;

            SCHEDULER_LOG_WARNING(std::string("❌ [BTIE] 能量不足，批量调度失败（全无原则）: ") +
                              "总需要=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                              " (新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ)" +
                              " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                              " 运行中=" + std::to_string(running_count) +
                              " → 终止所有运行任务");

            // ⭐ BTIE关键：能量不足时，标记能量已耗尽
            _energy_depleted = true;

            // ⭐ 关键修复：立即suspend所有运行中任务（BTIE"全无"原则）
            // 不仅仅是取消能量检查事件，还要强制中断运行中的任务
            if (!running_task_list.empty() && _kernel) {
                SCHEDULER_LOG_WARNING(std::string("🛑 [BTIE] 能量不足，立即中断") +
                                     std::to_string(running_task_list.size()) +
                                     "个运行任务（遵循BTIE'全无'原则）");

                for (auto* task : running_task_list) {
                    if (task && task->isExecuting()) {
                        SCHEDULER_LOG_WARNING(std::string("  - 挂起任务: ") + getTaskName(task));
                        _kernel->suspend(task);

                        // 取消能量检查事件
                        auto it = _energy_check_events.find(task);
                        if (it != _energy_check_events.end()) {
                            _energy_check_events.erase(it);
                        }
                    }
                }

                SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，所有运行任务已挂起，系统进入空闲等待状态"));
            } else if (!running_task_list.empty()) {
                // 如果没有kernel，只取消能量检查事件（降级处理）
                SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] 无法挂起任务（kernel为nullptr），仅取消能量检查事件"));
                for (auto* task : running_task_list) {
                    auto it = _energy_check_events.find(task);
                    if (it != _energy_check_events.end()) {
                        _energy_check_events.erase(it);
                        SCHEDULER_LOG_DEBUG(std::string("  - 已取消: ") + getTaskName(task));
                    }
                }
            }
        }

        checkAndPreempt();

        // 如果有kernel，循环触发dispatch直到填满所有CPU
        if (!_kernel) {
            SCHEDULER_LOG_DEBUG("⚠��� [BTIE] performTickScheduling: _kernel为nullptr，尝试获取");
            _kernel = getKernel();
        }

        if (_kernel) {
            SCHEDULER_LOG_INFO("🔔 [BTIE] performTickScheduling: 开始循环调度填满所有CPU");
            // ⭐ V31关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
            // 这是多核调度器的正确行为：在一个tick内尽可能多地调度任务
            int dispatch_attempts = 0;
            const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

            while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));

                // 检查是否所有CPU都已填满
                // ⭐ 关键修复：如果_running_tasks为空，说明没有任何CPU被占用，应该继续调度
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
                    SCHEDULER_LOG_INFO("✅ [BTIE] 所有CPU已填满，停止调度");
                    break;
                }

                // 记录调度前的任务数
                size_t tasks_before = _ready_queue.size() + _running_tasks.size();

                // 调用dispatch尝试调度更多任务
                SCHEDULER_LOG_INFO(std::string("🚀 [BTIE] 调用 _kernel->dispatch()"));
                _kernel->dispatch();
                dispatch_attempts++;

                // 记录调度后的任务数
                size_t tasks_after = _ready_queue.size() + _running_tasks.size();

                // 如果没有任务被调度（状态没变化），停止调度
                if (tasks_before == tasks_after) {
                    SCHEDULER_LOG_DEBUG("⏹️ [BTIE] 无更多任务可调度，停止dispatch循环");
                    break;
                }

                SCHEDULER_LOG_DEBUG(std::string("🔄 [BTIE] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
            }

            if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_WARNING("⚠️ [BTIE] dispatch循环达到最大迭代次数，可能存在bug");
            }
        } else {
            SCHEDULER_LOG_INFO("⚠️ [BTIE] performTickScheduling: _kernel仍为nullptr，跳过dispatch");
        }
    }

    void BTIEScheduler::schedule() {
        // BTIE依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [BTIE] schedule() 被调用");
    }

    // =====================================================
    // getFirst - BTIE废弃，返回nullptr
    // =====================================================

    AbsRTTask *BTIEScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [BTIE] getFirst() 被调用（BTIE已废弃）"));
        // BTIE使用批量调度，不使用getFirst
        return nullptr;
    }

    // =====================================================
    // getTaskN - 返回批量中的第n个任务
    // =====================================================

    AbsRTTask *BTIEScheduler::getTaskN(unsigned int n) {
        SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] getTaskN(") + std::to_string(n) + ") " +
                           "当前能量: " + std::to_string(_current_energy) + "J" +
                           " 批量任务数=" + std::to_string(_current_batch_tasks.size()));

        // ⭐ 关键：当n==0时，表示新的调度周期开始

        // ⭐ 关键Bug修复：检查能量是否已耗尽
        // 当能量在能量检查事件中耗尽时，_energy_depleted被设置为true
        // 但performTickScheduling()可能还在执行，_current_batch_tasks可能还没清空
        // 所以需要在getTaskN()中检查_energy_depleted标志
        const double ENERGY_EPSILON = 1e-9;
        if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] getTaskN: 能量已耗尽，清空批量任务队列并返回nullptr") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
            // 清空批量任务队列，防止后续getTaskN()调用返回过期任务
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            return nullptr;
        }

        if (n == 0) {
            // 注意：能量已在performTickScheduling中批量扣除，这里不重复扣除
            SCHEDULER_LOG_DEBUG(std::string("🔄 [BTIE] 新调度��期开始"));
        }

        // ⭐ 关键修复：使用_current_batch_tasks而不是_ready_queue
        // _current_batch_tasks在performTickScheduling()中设置，已经考虑了能量检查
        if (_current_batch_tasks.empty()) {
            SCHEDULER_LOG_INFO("📭 [BTIE] getTaskN: 批量任务队列为空（能量不足）");
            return nullptr;
        }

        // 检查���引是否有效
        if (n >= _current_batch_tasks.size()) {
            SCHEDULER_LOG_DEBUG(std::string("📭 [BTIE] getTaskN: 索引超出范围") +
                               " n=" + std::to_string(n) +
                               " size=" + std::to_string(_current_batch_tasks.size()));
            return nullptr;
        }

        // ⭐ 直接从批量任务队列中获取第n个任务
        AbsRTTask *task = _current_batch_tasks[n];
        if (!task) {
            return nullptr;
        }

        // ⭐ Bug修复：通过内核检查任务是否在运行
        bool is_running = false;
        if (_kernel) {
            CPU *proc = _kernel->getProcessor(task);
            is_running = (proc != nullptr);
        }

        if (is_running) {
            SCHEDULER_LOG_DEBUG(std::string("♻️ [BTIE] 运行中任务续期: ") + getTaskName(task));
        } else {
            SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 调度新任务: ") + getTaskName(task));
        }

        return task;
    }

    // =====================================================
    // notify - BTIE不再扣减能量（已在批量时扣减）
    // =====================================================

    void BTIEScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例到达）
        // 周期性任务复用同一个AbsRTTask对象，但每个实例都是独立的
        SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] notify: 检查WCET完成标志: ") +
                           getTaskName(task) + " 集合大小=" + std::to_string(_tasks_completed_wcet.size()));
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [BTIE] notify: 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否充足
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [BTIE] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void BTIEScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] addTask: 任务为空");
            return;
        }

        // ⭐ Bug修复：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [BTIE] 能量已耗尽，拒绝添加新任务: ") +
                                         getTaskName(task));
            return;  // 拒绝任务
        }

        SCHEDULER_LOG_INFO(std::string("📥 [BTIE] 添加任务: ") + getTaskName(task));
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
        BTIETaskModel *model = new BTIETaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void BTIEScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [BTIE] 移除任务: ") + getTaskName(task));

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

        SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void BTIEScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [BTIE] 任务到达: ") + getTaskName(task));

        // ⭐ 关键修复：清除任务的WCET完成标志（新实例重新开始）
        // 周期性任务��复用同一个AbsRTTask对象，但每个实例都是独立的
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [BTIE] 清除任务的WCET完成标志: ") +
                               getTaskName(task) + " (新实例到达)");
        }

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
            checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void BTIEScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [BTIE] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void BTIEScheduler::checkAndPreemptOnAllCPUs() {
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
                SCHEDULER_LOG_INFO(std::string("🔄 [BTIE] 抢占CPU: ") +
                                  " 挂起低优先级任务=" + getTaskName(running_task) +
                                  " 调度高优先级任务=" + getTaskName(highest));

                // ⭐ 实际抢占逻辑：挂起当前运行的任务
                // suspend会自动调用deschedule()并将任务重新放回调度队列
                if (_kernel) {
                    _kernel->suspend(running_task);
                    SCHEDULER_LOG_DEBUG(std::string("⏸️ [BTIE] 已挂起任务: ") + getTaskName(running_task));
                } else {
                    SCHEDULER_LOG_WARNING("⚠️ [BTIE] 抢占失败：_kernel为nullptr");
                }
            }
        }
    }

    bool BTIEScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        BTIETaskModel *running_model = getTaskModel(running_task);
        BTIETaskModel *new_model = getTaskModel(new_task);

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

    void BTIEScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [BTIE] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);

        // ⭐ BTIE修复：不在insert()中触发批量调度
        // 让所有任务先到达，然后在tick事件中统一批量调度
    }

    void BTIEScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [BTIE] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
    }

    void BTIEScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是否已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        BTIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            BTIETaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [BTIE] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void BTIEScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [BTIE] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void BTIEScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [BTIE] 任务加入等待队列: ") + getTaskName(task));
    }

    void BTIEScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool BTIEScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool BTIEScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *BTIEScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double BTIEScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        BTIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    double BTIEScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        BTIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] calculateTotalEnergyForTask: 任务模型不存在");
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

    double BTIEScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [BTIE] 功率计算: ") +
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

    void BTIEScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [BTIE] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        BTIEEnergyCheckEvent *evt = new BTIEEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        Tick current_time = SIMUL.getTime();
        Tick scheduled_time = current_time + 1;
        evt->post(scheduled_time);

        SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString() +
                           " 当前时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                           " 调度时间=" + std::to_string(static_cast<int64_t>(scheduled_time)) + "ms");
    }

    void BTIEScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double BTIEScheduler::collectSolarEnergy(Tick current_time) {
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

    double BTIEScheduler::getSolarIrradiance(int64_t time_ms) {
        if (!_use_real_solar_data) {
            // 简化模型
            int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
            int64_t hour_of_day = (actual_time_ms % 86400000) / 3600000;

            if (hour_of_day >= 6 && hour_of_day <= 18) {
                return 500.0;
            } else {
                return 0.0;
            }
        }

        // 使用真实NASA太阳能数据
        int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
        int64_t minute_of_day = (actual_time_ms % 86400000) / 60000;  // 0-1439

        int line_number = minute_of_day + 2;  // +2跳过标题行

        std::ifstream file(_solar_data_file);
        if (!file.is_open()) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [BTIE] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void BTIEScheduler::scheduleNextTick() {
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

    BTIETaskModel *BTIEScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string BTIEScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *BTIEScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int BTIEScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *BTIEScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void BTIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [BTIE] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [BTIE] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void BTIEScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [BTIE] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void BTIEScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void BTIEScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
    }

    MRTKernel *BTIEScheduler::getKernel() {
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

    void BTIEScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [BTIE] newRun - 仿真开始");

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

        // BTIE批量调度状态初始化
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _current_batch_tasks.clear();

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [BTIE] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void BTIEScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [BTIE] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [BTIE] ===== BTIE批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void BTIEScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 任务结束: ") + getTaskName(task));

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
            SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
            SCHEDULER_LOG_INFO("🔄 [BTIE] 任务结束，触发立即调度");
            _kernel->dispatch();
        }
    }

    bool BTIEScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void BTIEScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [BTIE] ===== BTIE批量调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  批量调度成功: ") + std::to_string(_stats.total_batch_schedules));
        SCHEDULER_LOG_INFO(std::string("  批量调度跳过: ") + std::to_string(_stats.total_batch_skipped));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string BTIEScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> BTIEScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

    void BTIEScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [BTIE] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [BTIE] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
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
                SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 运行任务续期能量充足: ") +
                                   "需要=" + std::to_string(total_energy_to_deduct * 1000) + " mJ " +
                                   "当前=" + std::to_string(_current_energy * 1000) + " mJ " +
                                   "(能量已在批量调度中扣除)");
            } else {
                // ❌ 能量不足，中断所有运行中的任务
                SCHEDULER_LOG_WARNING(std::string("❌ [BTIE] 运行任务续期能量不足，将中断所有运行任务: ") +
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

                SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，将中断") +
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
            SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 当前tick有") +
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
                    SCHEDULER_LOG_WARNING(std::string("⚡ [BTIE] 任务能量不足，将中断: ") +
                                         getTaskName(task) +
                                         " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                         " 当前能量=" + std::to_string(_current_energy) + "J");

                    tasks_to_interrupt.push_back(task);
                    _stats.total_skipped_energy++;
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("✅ [BTIE] 任务能量充足: ") +
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
            SCHEDULER_LOG_INFO(std::string("💀 [BTIE] 能量已耗尽，") +
                               std::to_string(tasks_to_interrupt.size()) + "个任务将自然完成" +
                               "（不再调度新任务，遵循BTIE'全无'原则）");
        }
    }
} // namespace RTSim
