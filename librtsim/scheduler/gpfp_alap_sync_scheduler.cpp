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
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_alap_sync_scheduler.hpp>
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
        if (!_scheduler || !_task) {
            return;
        }

        // 🔍 调试：记录能量检���事件触发时间
        Tick actual_trigger_time = SIMUL.getTime();
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 能量检查事件触发: ") +
                           _scheduler->getTaskName(_task) +
                           " 触发时间=" + std::to_string(static_cast<int64_t>(actual_trigger_time)) + "ms" +
                           " _ms_executed=" + std::to_string(_ms_executed));

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Sync] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Sync] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Sync] 能量检查：任务已停止执行，不再扣除能量: ") +
                               _scheduler->getTaskName(_task) + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        ALAPSyncTaskModel *task_model = _scheduler->getTaskModel(_task);

        // 🔍 调试日志：检查WCET
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Sync] WCET���查: ") +
                           task_name + " 已执行=" + std::to_string(_ms_executed) +
                           "ms task_model=" + (task_model ? "有效" : "NULL"));

        if (task_model) {
            int wcet = task_model->getWCET();
            SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Sync] WCET值: ") +
                               std::to_string(wcet) + "ms 判断: " +
                               std::to_string(_ms_executed) + " >= " + std::to_string(wcet) +
                               " = " + (_ms_executed >= wcet ? "TRUE" : "FALSE"));

            if (_ms_executed >= wcet) {
                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 任务已达到WCET，完成执行: ") +
                                   task_name + " 已执行=" + std::to_string(_ms_executed) +
                                   "ms WCET=" + std::to_string(wcet) + "ms");
                // ⭐ 关键修复：标记任务已达到WCET，防止批量调度重复扣除能量
                _scheduler->_tasks_completed_wcet.insert(_task);
                SCHEDULER_LOG_INFO(std::string("🏁 [ALAP-Sync] 标记任务已完成WCET: ") +
                                   task_name);

                // ⭐ 关键修复：不直接调用onEnd()，而是让任务自然结束
                // 终止能量检查事件，让内核在下一个tick时检测到任务完成并调用onEnd()
                // 避免在能量检查事件中调用onEnd()导致的"No CPU"崩溃
                SCHEDULER_LOG_INFO(std::string("🛑 [ALAP-Sync] 任务达到WCET，终止能量检查事件: ") + task_name);

                // 任务已完成，不再检查能量预扣，也不重新调度事件
                return;
            }
        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] WCET检查失败：找不到TaskModel ") + task_name);
        }

        // ⭐ ALAP-Sync关键修复：在扣除能量之前检查是否有足够能量（与TIE保持一致）
        // 设计原则：
        // - 批量调度时进行"全有或全无"门槛检查，但不预扣能量
        // - 能量检查事件在实际执行时每1ms扣除一次能量
        // - ⭐ 关键：先检查能量是否足够再扣除，不足则立即中断任务

        // 检查是否有足够能量续期1ms
        if (current_energy < unit_energy - EPSILON) {
            // ❌ 能量不足，立即中断任务
            SCHEDULER_LOG_WARNING(std::string("⚡ [ALAP-Sync] 续期能量不足，立即中断任务: ") +
                                 _scheduler->getTaskName(_task) +
                                 " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                 " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                                 " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;

            // ⭐ 关键修复：立即suspend任务（与TIE保持一致）
            if (_scheduler->_kernel && _task->isExecuting()) {
                _scheduler->setSuspendReason(_task, "insufficient_energy");
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_WARNING(std::string("🛑 [ALAP-Sync] 任务因能量不足被挂起: ") +
                                     _scheduler->getTaskName(_task));
            }

            // 不重新调度能量检查事件
            return;
        }

        // ✅ 能量充足，立即扣除 1ms 的能量
        _scheduler->_current_energy -= unit_energy;
        _scheduler->_stats.total_energy_consumed += unit_energy;

        SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-Sync] 扣除 1ms 能量: ") +
                           _scheduler->getTaskName(_task) +
                           " 扣除=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " 剩余=" + std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
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
        // k = min(CPU核心总数, 就绪队列任务数)
        ConfigManager &configMgr = ConfigManager::getInstance();
        int total_cpus = configMgr.getNumCores();
        int ready_tasks = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(total_cpus, ready_tasks);

        SCHEDULER_LOG_DEBUG(std::string("📊 [ALAP-Sync] calculateBatchSize: ") +
                           "CPU核心数=" + std::to_string(total_cpus) +
                           " 就绪任务=" + std::to_string(ready_tasks) +
                           " 批量k=" + std::to_string(batch_size));

        return batch_size;
    }


    void ALAPSyncScheduler::executeBatchScheduling(const std::vector<AbsRTTask *> &tasks, double total_energy) {
        // ⭐ ALAP-Sync核心：批量调度时一次性扣减k个任务的1ms能耗
        // 当前时刻能量 = 上一时刻结余 + 本次充电能量 - 已消耗能量 - 本次批量调度能耗
        double old_energy = _current_energy;
        _current_energy -= total_energy;
        _stats.total_energy_consumed += total_energy;

        SCHEDULER_LOG_INFO(std::string("📋 [ALAP-Sync] 批量调度: ") +
                           "任务数=" + std::to_string(tasks.size()) +
                           " 总能耗=" + std::to_string(total_energy * 1000) + " mJ" +
                           " 能量=" + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_current_energy * 1000) + " mJ");
    }

    // =====================================================
    // 核心调度逻辑 - ALAP-Sync批量调度算法
    // =====================================================

        void ALAPSyncScheduler::performTickScheduling() {
        SCHEDULER_LOG_DEBUG(std::string("🔄 [ALAP-Sync] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");

        // ⭐ Micro-Batch Preemption：不清除抢占批量，让它在dispatch完成后自然过期
        // 抢占批量中的任务执行完成后，新tick的批量调度会重新计算
        // 这样可以确保mid-tick抢占的任务有机会被调度到CPU上

        _stats.total_tick_count++;

        // ⭐ 逐渐扣除模式：清空本次tick的调度记录
        _counted_tasks_in_dispatch.clear();

        // ========== 第1步：收集太阳能 ==========
        // ⭐ 关键修复：太阳能收集必须在能量耗尽检查之前执行
        // 否则当初始能量为0时，系统会因为能量耗尽而跳过太阳能收集，形成死锁
        Tick current_time = SIMUL.getTime();
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

                // ⭐ 如果收集到能量，清除能量耗尽标志
                if (_energy_depleted && _current_energy > 0.000001) {
                    _energy_depleted = false;
                    SCHEDULER_LOG_INFO("🔋 [ALAP-Sync] 太阳能充电成功，恢复调度");
                }
            }
        }

        _last_tick_time = current_time;

        // ⭐ Bug修复3：能量耗尽时跳过调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 能量已耗尽，跳过Tick调度"));
            return;  // 不进行任何调度，包括中断检查
        }

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第1.5步：清理过期任务实例 ==========
        // ⭐ 已改用killOnMiss(true)，框架自动处理过期实例
        // cleanupExpiredTasks();

        // ========== 阶段一：准备批量调度 ==========
        // ⭐ ALAP-Sync使用个体Slack门控（在任务选择时过滤），不使用全局门控

        // 先收集运行中任务和就绪队列（用于构建批次）
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] _kernel为nullptr，跳过批量调度");
                return;
            }
        }

        // 获取运行中任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        std::vector<AbsRTTask *> running_task_list;
        double energy_to_deduct = 0.0;  // ⭐ V58：统一在这里计算energy_to_deduct
        // ⭐ V58修复：只添加真正在执行且未完成WCET的任务
        for (const auto& map_pair : running_tasks) {
            AbsRTTask* task = map_pair.second;
            if (task && task->isExecuting() && _tasks_completed_wcet.find(task) == _tasks_completed_wcet.end()) {
                running_task_list.push_back(task);
                energy_to_deduct += calculateUnitEnergyForTask(task);
            }
        }

        // 获取就绪队列（已按 RM 优先级排序）
        std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());

        // ⭐ 关键修复：使用ConfigManager获取实际CPU核心数，而非_running_tasks.size()
        // _running_tasks可能为空（未被内核填充），导致total_cpus=0，free_cpus=0，永远无法调度
        ConfigManager &configMgr_batch = ConfigManager::getInstance();
        int total_cpus = configMgr_batch.getNumCores();
        int free_cpus = total_cpus - static_cast<int>(running_task_list.size());

        // ========== 核心参数：K = min(就绪任务数, 空闲核心数)==========
        // ⭐ 这是"全员进退、同生共死"批处理的关键
        // K是动态值，受实际就绪任务数和空闲核心���限制
        int ready_count = static_cast<int>(sorted_ready.size());
        int K = std::min(ready_count, total_cpus);  // 批次大小不超过总核心数（允许抢占）

        // ========== 构建"全员进退、同生共死"批次 ==========
        // ⭐ 关键修复：不过滤Slack，让所有top K任务都进入候选批次
        // Slack过滤移到能量检查之后，避免过早过滤导致调度延迟
        // 这样可以保证调度时机与Block/NonBlock一致
        std::vector<AbsRTTask *> candidate_batch;
        for (int i = 0; i < K && i < static_cast<int>(sorted_ready.size()); ++i) {
            candidate_batch.push_back(sorted_ready[i]);
        }

        // ========== Phase 2: ALAP-Sync 批次级时序门控（已禁用）==========
        // ⭐ 关键修复：移除批次级时序门控，避免过度阻塞
        // 原因：批次时序门控计算Batch Slack = min(Slack_i)，导致在t=96时如果批次中有一个任务Slack>0，
        //      整个批次被阻塞，调度延迟到t=100，导致deadline miss
        // 解决方案：在任务选择时进行个体Slack过滤（第798-804行）���只调度Slack<=0的任务
        // 这样既保证了ALAP时序门控的功能，又避免了过度阻塞
        // ========== Phase 2: ALAP-Sync 个体时序门控 ==========
        // ⭐ 关键修复：使用个体Slack检查，不是批次级门控
        // 原因：每个任务有自己的Slack和释放时间，应该独立判断
        //      Slack<=0的任务进入就绪队列接受调度
        //      这才是"尽可能晚执行"的正确实现
        //
        // ⭐ 批次级门控是错误的！它会让所有任务一起调度，
        //      违反了"尽可能晚执行"的原则

        // ⭐ ALAP-Sync关键修复：采用正确的能量扣除逻辑（后扣方式）
        // ⭐ V58：energy_to_deduct已在上面统一计算

        // 🔍 调试：输出_currExe的内容
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] _currExe内容 (") +
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

        // ⭐ V66: All-or-Nothing批量能量检查（完整版）
        // 1. 收集所有Slack<=0的任务（运行中 + 就绪队列中）
        // 2. K = min(总候选数, CPU核心数)
        // 3. 计算K个任务的总能量
        // 4. 能量足够→全部执行；能量不足→全部挂起
        
        const double EPSILON_V66 = 1e-9;
        
        // Step 1: 收集Slack<=0的就绪任务
        std::vector<AbsRTTask*> slack_ready_tasks;
        
        // 按RM优先级排序就绪队列
        std::vector<AbsRTTask*> sorted_ready_v66(_ready_queue.begin(), _ready_queue.end());
        std::sort(sorted_ready.begin(), sorted_ready.end(),
            [this](AbsRTTask* a, AbsRTTask* b) {
                auto model_a = getTaskModel(a);
                auto model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });
        
        for (auto* task : sorted_ready_v66) {
            if (!task || !task->isActive()) continue;
            
            // 排除运行中的任务
            bool is_running = false;
            for (auto* rt : running_task_list) {
                if (task == rt) { is_running = true; break; }
            }
            if (is_running) continue;
            
            // 检查Slack<=0
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack <= 0) {
                slack_ready_tasks.push_back(task);
            }
        }
        
        // Step 2: 计算K
        int total_candidates_v66 = running_task_list.size() + slack_ready_tasks.size();
        int K_v66 = std::min(total_candidates_v66, static_cast<int>(total_cpus));
        
        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync V66] All-or-Nothing决策: ") +
                          "运行中=" + std::to_string(running_task_list.size()) +
                          " Slack<=0就绪=" + std::to_string(slack_ready_tasks.size()) +
                          " K=" + std::to_string(K_v66));
        
        // Step 3: 计算K个任务的总能量
        double total_energy_v66 = 0.0;
        std::vector<AbsRTTask*> k_tasks_v66;
        
        // 先加入运行中任务
        for (auto* task : running_task_list) {
            if (static_cast<int>(k_tasks_v66.size()) >= K_v66) break;
            if (_tasks_completed_wcet.find(task) == _tasks_completed_wcet.end()) {
                k_tasks_v66.push_back(task);
                total_energy_v66 += calculateUnitEnergyForTask(task);
            }
        }
        
        // 再加入Slack<=0的就绪任务（按优先级）
        for (auto* task : slack_ready_tasks) {
            if (static_cast<int>(k_tasks_v66.size()) >= K_v66) break;
            k_tasks_v66.push_back(task);
            total_energy_v66 += calculateUnitEnergyForTask(task);
        }
        
        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync V66] 能量需求: ") +
                          "K个任务=" + std::to_string(k_tasks_v66.size()) +
                          " 总能量=" + std::to_string(total_energy_v66 * 1000) + " mJ" +
                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
        
        // Step 4: All-or-Nothing决策
        if (_current_energy >= total_energy_v66 - EPSILON_V66) {
            // 能量充足：扣除能量，设置批量任务
            for (auto* task : k_tasks_v66) {
                double unit_energy = calculateUnitEnergyForTask(task);
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;
            }
            
            _current_batch_tasks = k_tasks_v66;
            _current_batch_size = k_tasks_v66.size();
            _batch_scheduled_this_tick = true;
            _stats.total_batch_schedules++;
            
            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync V66] All-or-Nothing通过: ") +
                              "调度" + std::to_string(k_tasks_v66.size()) + "个任务");
        } else {
            // 能量不足：全部挂起
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync V66] All-or-Nothing失败: ") +
                                 "需要=" + std::to_string(total_energy_v66 * 1000) + " mJ" +
                                 " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
            
            // 挂起所有运行中的任务
            for (auto* task : running_task_list) {
                if (_kernel && task->isExecuting()) {
                    SCHEDULER_LOG_WARNING(std::string("🛑 [ALAP-Sync V66] 挂起: ") + getTaskName(task));
                    setSuspendReason(task, "insufficient_energy");
                    _kernel->suspend(task);
                }
            }
            
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _batch_scheduled_this_tick = false;
            _energy_depleted = true;
            _stats.total_batch_skipped++;
            
            return;  // 直接返回
        }

        // ⭐ 关键修复：如果能量已耗尽，不调度新任务
        if (_energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 能量已耗尽，跳过批量调度") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");

            // ⭐ 关键修复：清空批量任务队列，防止后续BeginDispatchMultiEvt事件访问过期批量
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _preempt_batch_tasks.clear();

            return;
        }

        // ⭐ 关键修复：在checkAndPreempt()之前清空上一tick的批量任务队���
        // 原因：checkAndPreempt()会使用_current_batch_tasks来判断是否需要抢占
        //      如果不清空，会使用旧的批量任务，导致误判
        // ⭐ 同时保存running_task_list的快照，用于后续构建新批量
        std::vector<AbsRTTask *> last_tick_running_tasks(running_task_list);
        _current_batch_tasks.clear();
        _current_batch_size = 0;

        if (_energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 检测到能量在运行时检查中耗尽，跳过批量调度") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        \
        // ========== 第2.5步：Tick边界抢占检查 ==========
        // ⭐ V56修复：重新启用tick边界抢占，添加防抖机制
        // 每个tick开始时清除抢占防抖标记
        _last_preempted_task = nullptr;

        // ⭐ 调用checkAndPreempt进行抢占检查
        checkAndPreempt();

        // ⭐ Bug #9修复：不在批量调度决策之前调用checkAndInterruptRunningTasks()
        // 因为那时_batch_scheduled_this_tick还没有设置，检查结果会被覆盖
        // 只在批量调度决策之后调用一次，让它根据_batch_scheduled_this_tick决定是否检查

        // 3. ⭐ 选择K个新任务（不扣除它们的能量）
        int running_count = running_task_list.size();

        // ⭐ 批量大小计算：实际可调度的新任务数
        // 批量大小 = min(就绪队列大小, 空闲CPU数)
        // 注意：_ready_queue和running_task_list是互斥的，不应该相减
        int actual_new_tasks_can_schedule = std::min(static_cast<int>(K), static_cast<int>(free_cpus));

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
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [ALAP-Sync] 清理不活动任务: ") + getTaskName(task));
                            return true;
                        }
                        // ⭐ 移除过期的周期性任务实例：使用getDeadline()获取绝对截止时间
                        Tick deadline = task->getDeadline();
                        if (deadline < current_time) {
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [ALAP-Sync] 清理过期任务实例: ") +
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
                [this](AbsRTTask* a, AbsRTTask* b) {
                    auto model_a = getTaskModel(a);
                    auto model_b = getTaskModel(b);
                    if (model_a && model_b) {
                        // RM排序：周期越短，优先级越高（数值越小）
                        return model_a->getRMPriority() < model_b->getRMPriority();
                    }
                    return false;
                });

            // 🔍 调试：输出就绪队列内容
            SCHEDULER_LOG_INFO(std::string("📋 [ALAP-Sync] 就绪队列内容 (共") +
                               std::to_string(sorted_ready.size()) + "个任务):");
            for (size_t i = 0; i < sorted_ready.size() && i < 5; ++i) {
                auto model = getTaskModel(sorted_ready[i]);
                Tick rm_priority = model ? model->getRMPriority() : Tick(0);
                SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                                   getTaskName(sorted_ready[i]) +
                                   " RM优先级(周期)=" + std::to_string(static_cast<int>(rm_priority)) +
                                   " deadline=" + std::to_string(static_cast<int>(sorted_ready[i]->getDeadline())));
            }

            // 🔍 调试：输出运行中任务列表
            SCHEDULER_LOG_INFO(std::string("🏃 [ALAP-Sync] 运行中任务列表 (共") +
                               std::to_string(running_task_list.size()) + "个任务):");
            for (size_t i = 0; i < running_task_list.size(); ++i) {
                SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                                   getTaskName(running_task_list[i]));
            }

            // ⭐ 关键修复：排除已经在运行中的任务，避免重复调度
            std::vector<AbsRTTask *> filtered_ready;
            for (auto* task : sorted_ready_v66) {
                bool is_running = false;
                for (auto* running_task : running_task_list) {
                    if (task == running_task) {
                        is_running = true;
                        SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Sync] 跳过已在运行中的任务: ") +
                                           getTaskName(task));
                        break;
                    }
                }
                if (!is_running) {
                    filtered_ready.push_back(task);
                }
            }

            // ⭐ "全员进退、同生共死"：从candidate_batch选择新任务（受free_cpus限制）
            // ⭐ 关键修复：任务选择时不过滤Slack，让getTaskN在调度时动态���查
            // 原因：如果在这里过滤Slack>0，会导致t=60-96之间所有任务被跳过
            //      无法触发调度，直到t=100某个任务Slack<=0才调度，延迟4ms
            // 解决方案：让所有候选任务进入_current_batch_tasks，在getTaskN中检查Slack
            int selected_count = 0;
            for (size_t j = 0; j < candidate_batch.size(); ++j) {
                if (selected_count >= free_cpus) break;
                AbsRTTask *task = candidate_batch[j];

                // 排除已在运行中的任务
                bool is_running = false;
                for (auto* running_task : running_task_list) {
                    if (task == running_task) {
                        is_running = true;
                        break;
                    }
                }
                if (is_running) {
                    continue;  // 跳过运行中的任务
                }

                // ⭐ ALAP-Sync个体时序门控：只调度Slack<=0的任务
                // 每个任务独立检查Slack，Slack<=0才进入就绪队列
                // 这是"尽可能晚执行"原则的体现
                Tick task_slack = calculateSlackForTask(task);
                if (task_slack > 0) {
                    SCHEDULER_LOG_DEBUG(std::string("⏸️  [ALAP-Sync] 任务选择: Slack>0，跳过 ") +
                                       getTaskName(task) +
                                       " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                    continue;  // Slack>0，跳过（尽可能晚执行）
                }

                new_tasks_to_schedule.push_back(task);
                selected_count++;
                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 从批次选择新任务: ") +
                                   getTaskName(task) + " (" + std::to_string(selected_count) + "/" +
                                   std::to_string(free_cpus) + ")");
            }

            // 保存所有就绪任务用于日志
            all_ready_tasks.assign(sorted_ready.begin(), sorted_ready.end());
        }

#if 0
        // 3. ⭐ ALAP-Sync关键：每个tick都预扣运行任务续期+新任务的能量
        // 这样可以在能量耗尽时及时中断任务

        // 计算运行中任务的续期能���（每个tick都要续期）
        double running_tasks_renewal_energy = 0.0;
        for (auto* task : running_task_list) {
            // ⭐ 关键修复：跳过已达到WCET的任务，避免重复扣除能量
            // 因为能量检查事件在任务达到WCET时会标记完成，但kernel可能还没处理end_instance
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-Sync] 批量调度：跳过已完成WCET的任务: ") +
                                   getTaskName(task) + " (能量检查事件已标记完成)");
                continue;
            }
            running_tasks_renewal_energy += calculateUnitEnergyForTask(task);
        }

        // ⭐ "全员进退、同生共死"：计算candidate_batch中非运行任务的能量（受free_cpus限制）
        // ⭐ 关键修复：能量计算时不过滤Slack>0，避免过早过滤导致调度延迟
        // 原因：如果在能量计算时就过滤Slack>0，会导致t=60-96之间所有任务被跳过，
        //      无法触发调度决策，直到t=100某个任务Slack<=0才调度，延迟4ms
        // 解决方案：能量计算考虑所有候选任务，Slack过滤移到任务选择阶段
        double new_tasks_energy = 0.0;
        int candidate_count = 0;
        int skipped_running = 0;
        int skipped_slack = 0;
        int checked_count = 0;

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 开始遍历候选批次: ") +
                          "批次大小=" + std::to_string(candidate_batch.size()) +
                          " 空闲CPU=" + std::to_string(free_cpus));

        for (auto* task : candidate_batch) {
            if (candidate_count >= free_cpus) break;  // 最多free_cpus个新任务
            checked_count++;

            // 排除运行中的任务
            bool is_running = false;
            for (auto* running_task : running_task_list) {
                if (task == running_task) {
                    is_running = true;
                    break;
                }
            }
            if (is_running) {
                skipped_running++;
                SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 跳过运行中任务: ") + getTaskName(task));
                continue;  // 跳过运行中的任务
            }

            // ⭐ ALAP-Sync核心：个体Slack检查，只有Slack<=0的任务才能调度
            // 这体现了"尽可能晚执行"的原则
            Tick task_slack = calculateSlackForTask(task);
            Tick current_time = SIMUL.getTime();
            double remaining_double = task->getRemainingWCET();
            // ⭐ V76修复：处理负值
            if (remaining_double < 0) {
                remaining_double = 0;
            }
            SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 检查任务: ") + getTaskName(task) +
                              " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms" +
                              " (deadline=" + std::to_string(static_cast<int64_t>(task->getDeadline())) +
                              " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                              " remaining=" + std::to_string(static_cast<int64_t>(Tick(remaining_double))) + ")");

            if (task_slack > 0) {
                skipped_slack++;
                SCHEDULER_LOG_DEBUG(std::string("⏸️  [ALAP-Sync] Slack>0，跳过任务 ") +
                                   getTaskName(task) +
                                   " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                continue;  // Slack>0，跳过（尽可能晚执行）
            }

            // Slack<=0，任务必须调度，计算其能耗
            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Slack<=0，可选任务: ") + getTaskName(task) +
                              " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms" +
                              " 能耗=" + std::to_string(calculateUnitEnergyForTask(task) * 1000) + " mJ");
            new_tasks_energy += calculateUnitEnergyForTask(task);
            candidate_count++;
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 候选批次遍历完成: ") +
                          "检查=" + std::to_string(checked_count) +
                          " 跳过运行中=" + std::to_string(skipped_running) +
                          " 跳过Slack>0=" + std::to_string(skipped_slack) +
                          " 可用=" + std::to_string(candidate_count));

        // ⭐ ALAP-Sync总能量需求 = 运行中任务续期 + 新任务（每个tick都扣除）
        double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] 批量调度决策: ") +
                          "总CPU=" + std::to_string(total_cpus) +
                          " 运行中=" + std::to_string(running_count) +
                          " 空闲=" + std::to_string(free_cpus) +
                          " 就绪队列=" + std::to_string(_ready_queue.size()) +
                          " K=min(ready,free)=" + std::to_string(K) +
                          " 候选批次=" + std::to_string(candidate_batch.size()) +
                          " 能量计算基数=" + std::to_string(candidate_count) +
                          " ⭐ 运行任务能量已扣除=" + std::to_string(running_count) + "个任务" +
                          " 新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ" +
                          " 总能量需求=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                          " 当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

        // 4. ⭐ ALAP-Sync核心：批量能量判断（"全有或全无"���
        // ⭐ Bug #3修复：检查总能量需求（运行中续期+新任务），确保有足夠能量才调度
        const double EPSILON = 1e-9;
        // ⭐ V38修复：使用与TIE/TGF相同的能量检查条件
        // 当能量 <= 总能量需求时，立即中断任务（不扣除能量），避免超额透支
        // ⭐ V39修复：使用与TIE/TGF完全相同的能量检查条件
        // TIE: current_energy <= unit_energy + EPSILON → 挂起
        // ALAP-Sync: current_energy <= total_energy_needed + EPSILON → 挂起
        // 这样当能量 == 需求时，允许继续执行（与TIE一致）
        // ⭐ 关键修复：运行任务能量已扣除，���检查新任务能量
        if (_current_energy > new_tasks_energy - EPSILON) {
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

            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] 批量调度门槛检查通过: ") +
                              "新任务数=" + std::to_string(new_tasks_to_schedule.size()) +
                              " 运行任务数=" + std::to_string(running_count) +
                              " 新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ " +
                              "当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

            _current_batch_tasks = all_tasks_to_dispatch;
            _current_batch_size = all_tasks_to_dispatch.size();
            _stats.total_batch_schedules++;

            // ⭐ 逐渐扣除模式：扣除新任务的初始能量（1ms）
            // 运行中任务的续期能量已在performTickScheduling开始时扣除
            for (auto* task : new_tasks_to_schedule) {
                double unit_energy = calculateUnitEnergyForTask(task);
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;

                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 扣除新任务初始能量: ") +
                                   getTaskName(task) +
                                   " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                                   std::to_string(_current_energy * 1000) + " mJ");
            }

            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 批量调度成功: ") +
                              "运行中=" + std::to_string(running_count) +
                              " 新任务=" + std::to_string(new_tasks_to_schedule.size()) +
                              " 总任务=" + std::to_string(all_tasks_to_dispatch.size()) +
                              " 新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ" +
                              " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");

            // 不调用checkAndInterruptRunningTasks()，避免潜在的segfault
        } else {
            // ⭐ 修复：能量不足时只拒绝调度新任务，不挂起运行中的高优先级任务
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            _preempt_batch_tasks.clear();
            _current_batch_size = 0;
            _stats.total_batch_skipped++;

            SCHEDULER_LOG_WARNING(std::string("⚠️  [ALAP-Sync] 能量不足，全体下处理机: ") +
                                "总需要=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                                " (运行��续期=" + std::to_string(running_tasks_renewal_energy * 1000) + " mJ" +
                                " 新任务=" + std::to_string(new_tasks_energy * 1000) + " mJ)" +
                                " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                                " 运行中=" + std::to_string(running_count) +
                                " → 全员休眠，等待能量收集");

            // ⭐ 关键：挂起运行中的任务（"全员进退、同生共死"）
            if (!running_task_list.empty()) {
                SCHEDULER_LOG_WARNING(std::string("🔴 [ALAP-Sync] 挂起运行中的任务: ") +
                                    std::to_string(running_task_list.size()) + " 个");
                for (auto* task : running_task_list) {
                    if (_kernel && task) {
                        SCHEDULER_LOG_INFO(std::string("🔴 [ALAP-Sync] 挂起任务: ") + getTaskName(task));
                        setSuspendReason(task, "insufficient_energy");
                        _kernel->suspend(task);
                    }
                }
            }
        }

#endif

        // 如果有kernel，循环触发dispatch直到填满所有CPU
        if (!_kernel) {
            SCHEDULER_LOG_DEBUG("⚠��� [ALAP-Sync] performTickScheduling: _kernel为nullptr，尝试获取");
            _kernel = getKernel();
        }

        if (_kernel) {
            SCHEDULER_LOG_INFO("🔔 [ALAP-Sync] performTickScheduling: 开始循环调度填满所有CPU");
            // ⭐ V31关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
            // 这是多核调度器的正确行为：在一个tick内尽可能多地调度任务
            int dispatch_attempts = 0;
            const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

            while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] dispatch循环 #") + std::to_string(dispatch_attempts) +
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
                    SCHEDULER_LOG_INFO("✅ [ALAP-Sync] 所有CPU已填满，停止调度");
                    break;
                }

                // 记录调度前的任务数
                size_t tasks_before = _ready_queue.size() + _running_tasks.size();

                // 调用dispatch尝试调度更多任务
                SCHEDULER_LOG_INFO(std::string("🚀 [ALAP-Sync] 调用 _kernel->dispatch()"));
                _kernel->dispatch();

                // ⭐ Micro-Batch Preemption：dispatch后清除抢占批量
                // 抢占批量用于mid-tick立即调度，dispatch调用后即被"消费"
                // 无论任务是否成功调度，都清除抢占批量，避免阻塞后续调度
                if (!_preempt_batch_tasks.empty()) {
                    SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] dispatch后清除抢占批量") +
                                       " size=" + std::to_string(_preempt_batch_tasks.size()));
                    _preempt_batch_tasks.clear();
                }

                dispatch_attempts++;

                // 记录调度后的任务数
                size_t tasks_after = _ready_queue.size() + _running_tasks.size();

                // 如果没有任务被调度（状态没变化），停止调度
                if (tasks_before == tasks_after) {
                    SCHEDULER_LOG_DEBUG("⏹️ [ALAP-Sync] 无更多任务可调度，停止dispatch循环");
                    break;
                }

                SCHEDULER_LOG_DEBUG(std::string("🔄 [ALAP-Sync] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
            }

            if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] dispatch循环达到最大迭代次数，可能存在bug");
            }

            // ⭐ 注意：批量调度后的抢占检查已删除
            // 原因：mid-tick抢占已在insert()中通过Micro-Batch抢占处理
        } else {
            SCHEDULER_LOG_INFO("⚠️ [ALAP-Sync] performTickScheduling: _kernel仍为nullptr，跳过dispatch");
        }
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

    AbsRTTask *ALAPSyncScheduler::getTaskN(unsigned int n) {
        Tick current_time = SIMUL.getTime();
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] getTaskN(") + std::to_string(n) + ") @ " +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms " +
                           "能量: " + std::to_string(_current_energy * 1000) + " mJ " +
                           "ready_queue: " + std::to_string(_ready_queue.size()));

        const double ENERGY_EPSILON = 1e-9;

        // ⭐ 能量耗尽检查
        if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
            SCHEDULER_LOG_INFO("💀 [ALAP-Sync] getTaskN: 能量已耗尽");
            return nullptr;
        }

        // ⭐ n==0时重置计数器
        if (n == 0) {
            _counted_tasks_in_dispatch.clear();
        }

        // ⭐ V52核心：实时遍历ready_queue，按RM优先级找Slack<=0且能量足够的任务
        if (_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("📭 [ALAP-Sync] getTaskN: 就绪队列为空");
            return nullptr;
        }

        // 按RM优先级排序（周期越短优先级越高）
        std::vector<AbsRTTask *> sorted_ready(_ready_queue.begin(), _ready_queue.end());
        std::sort(sorted_ready.begin(), sorted_ready.end(),
            [this](AbsRTTask* a, AbsRTTask* b) {
                auto model_a = getTaskModel(a);
                auto model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });

        // ⭐ 遍历找到第n个Slack<=0且能量足够的任务
        unsigned int found_count = 0;
        for (AbsRTTask *task : sorted_ready) {
            if (!task || !task->isActive()) {
                continue;
            }

            // 检查是否已在运行（通过kernel的getProcessor）
            bool is_running = false;
            if (_kernel) {
                CPU *proc = _kernel->getProcessor(task);
                is_running = (proc != nullptr);
            }
            if (is_running) {
                continue;  // 跳过已运行的任务
            }

            // 检查是否已在本轮dispatch中处理过（避免重复扣除能量）
            if (_counted_tasks_in_dispatch.find(task) != _counted_tasks_in_dispatch.end()) {
                continue;
            }

            // ⭐ V52核心：实时检查Slack
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack > 0) {
                // Slack>0，跳过（ALAP：尽可能晚执行）
                continue;
            }

            // ⭐ 检查能量是否足够
            double unit_energy = calculateUnitEnergyForTask(task);
            if (_current_energy < unit_energy - ENERGY_EPSILON) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] 能量不足: ") + getTaskName(task) +
                                     " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                     " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
                continue;
            }

            // 找到第n个有效任务
            if (found_count == n) {
                // ⭐ 扣除能量
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;
                _counted_tasks_in_dispatch.insert(task);

                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] getTaskN(") + std::to_string(n) + ") 返回: " +
                                  getTaskName(task) +
                                  " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) +
                                  " 能量=" + std::to_string(unit_energy * 1000) + " mJ" +
                                  " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
                return task;
            }

            found_count++;
        }

        // 没找到足够的任务
        SCHEDULER_LOG_INFO(std::string("📭 [ALAP-Sync] getTaskN(") + std::to_string(n) +
                           ") 没有更多可调度任务，found_count=" + std::to_string(found_count));
        return nullptr;
    }

    // =====================================================
    // notify - ALAP-Sync不再扣减能量（已在批量时扣减）
    // =====================================================

    void ALAPSyncScheduler::notify(AbsRTTask *task) {
        if (!task) {
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

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否充足
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Sync] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-Sync] 任务到达并添加到就绪队列: ") + getTaskName(task));
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

        // ⭐ Bug修复：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [ALAP-Sync] 能量已耗尽，拒绝添加新任务: ") +
                                         getTaskName(task));
            return;  // 拒绝任务
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
        // ⭐ 优化：如果有抢占批量任务，说明mid-tick抢占已经处理，跳过tick边界抢占检查
        if (!_preempt_batch_tasks.empty()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-Sync] checkAndPreemptOnAllCPUs: 跳过检查，抢占批量size=") +
                               std::to_string(_preempt_batch_tasks.size()));
            return;
        }

        // ⭐ 修复：不使用_running_tasks（它从未被正确填充）
        // 直接从kernel获取实际运行中的任务
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_INFO("❌ [ALAP-Sync] checkAndPreemptOnAllCPUs: _kernel为null，无法检查抢占");
                return;
            }
        }

        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] checkAndPreemptOnAllCPUs: 运行中任务数量=") +
                          std::to_string(running_tasks.size()) +
                          " _current_batch_tasks.size()=" + std::to_string(_current_batch_tasks.size()));

        if (running_tasks.empty()) {
            SCHEDULER_LOG_INFO("❌ [ALAP-Sync] checkAndPreemptOnAllCPUs: 没有运行中的任务");
            return;
        }

        // ⭐ 关键修复：检查_current_batch_tasks中的第一个Slack<=0的任务
        // 因为mid-tick抢占可能已经将高优先级任务移到了_batch_tasks头部
        // ⭐ V59修复：遍历_current_batch_tasks找到第一个Slack<=0的任务
        // 原因：最高RM优先级任务的Slack可能>0，此时应该选择下一个Slack<=0的任务
        AbsRTTask *highest = nullptr;
        bool from_ready_queue = false;  // 标记是否来自就绪队列

        if (!_current_batch_tasks.empty()) {
            // ⭐ V59：遍历_current_batch_tasks找第一个Slack<=0的任务
            for (AbsRTTask *task : _current_batch_tasks) {
                if (!task || !task->isActive()) continue;

                // 检查是否已在运行
                bool is_running = false;
                for (const auto& [cpu, running_task] : running_tasks) {
                    if (running_task == task) {
                        is_running = true;
                        break;
                    }
                }
                if (is_running) continue;

                // ⭐ V59关键：检查Slack
                Tick task_slack = calculateSlackForTask(task);
                if (task_slack <= 0) {
                    highest = task;
                    from_ready_queue = false;
                    SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] checkAndPreempt: 从batch选择Slack<=0任务: ") +
                                      getTaskName(task) +
                                      " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                    break;  // 找到了，退出循环
                } else {
                    SCHEDULER_LOG_INFO(std::string("⏸️ [ALAP-Sync] checkAndPreempt: 跳过batch任务Slack>0: ") +
                                      getTaskName(task) +
                                      " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                }
            }
        }

        // 如果批量任务中没有Slack<=0的，才从就绪队列查找
        if (!highest) {
            highest = getHighestPriorityTaskFromReadyQueue();
            from_ready_queue = true;
        }

        if (!highest) {
            SCHEDULER_LOG_INFO("❌ [ALAP-Sync] checkAndPreemptOnAllCPUs: 没有候选任务进行抢占");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] checkAndPreemptOnAllCPUs: 最高优先级任务=") +
                          getTaskName(highest) +
                          " (from_" + (from_ready_queue ? "ready_queue" : "batch") + ")");

        // ⭐ Bug修复：如果最高优先级任务来自就绪队列，且_current_batch_tasks为空，
        // 说明可能是在suspend()后重新插入的任务。为了避免"假抢占"循���，
        // 检查该任务是否已经在运行中。如果是，则跳过抢占。
        if (from_ready_queue && _current_batch_tasks.empty()) {
            for (const auto& [cpu, running_task] : running_tasks) {
                if (running_task == highest) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-Sync] 跳过假抢占: 最高优先级任务正在运行中=") +
                                      getTaskName(highest));
                    return;  // 跳过抢占
                }
            }
        }

        // ⭐ V45关键修复：准确计算真正空闲的CPU数量
        // 空闲CPU的定义：_m_currExe[cpu] == nullptr 且 没���任务正在dispatch到这个CPU
        // 注意：上下文切换中的CPU（有任务dispatch但还没执行）不应该被认为是空闲的
        //       但也不应该被抢占
        int truly_free_cpus = 0;   // 真正空闲（可以调度新任务）
        int busy_executing = 0;     // 正在执行任务（可以被抢占）
        int busy_dispatching = 0;   // 上下文切换中（不应该被抢占）

        for (const auto& [cpu, running_task] : running_tasks) {
            bool is_dispatching = _kernel->isCPUDispatching(cpu);
            if (!running_task) {
                if (!is_dispatching) {
                    truly_free_cpus++;
                } else {
                    busy_dispatching++;
                }
            } else if (running_task->isExecuting()) {
                busy_executing++;
            } else {
                // 任务存在但没有在执行，可能是上下文切换中
                busy_dispatching++;
            }
        }

        int total_cpus = running_tasks.size();

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] CPU状态: 总数=") +
                          std::to_string(total_cpus) +
                          " 空闲=" + std::to_string(truly_free_cpus) +
                          " 执行中=" + std::to_string(busy_executing) +
                          " 上下文切换中=" + std::to_string(busy_dispatching));

        // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
        // 新任务会被dispatch到空闲CPU，不需要抢占正在运行的任务
        // ⭐ V59修复：有空闲CPU时，需要将highest任务加入批量并触发dispatch
        if (truly_free_cpus > 0) {
            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 有空闲CPU，无需抢占，直接调度新任务: ") +
                              getTaskName(highest));
            // ⭐ V59：将任务加入批量并触发调度
            if (highest) {
                // 检查任务是否已在批量中
                auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), highest);
                if (batch_it == _current_batch_tasks.end()) {
                    _current_batch_tasks.push_back(highest);
                    SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] 将任务加入批量: ") + getTaskName(highest));
                }
                // 触发dispatch
                _kernel->dispatch();
            }
            return;
        }

        // ⭐ 没有空闲CPU，需要抢占最低优先级的运行任务
        SCHEDULER_LOG_INFO("⚠️ [ALAP-Sync] CPU已满，需要抢占最低优先级任务");

        // 找到优先级最低的运行任务
        AbsRTTask *lowest_priority_task = nullptr;
        int lowest_priority = -1;

        for (const auto& [cpu, running_task] : running_tasks) {
            if (!running_task) {
                continue;
            }

            ALAPSyncTaskModel *model = getTaskModel(running_task);
            if (!model) {
                continue;
            }

            int priority = model->getRMPriority();
            if (lowest_priority_task == nullptr || priority > lowest_priority) {
                lowest_priority_task = running_task;
                lowest_priority = priority;
            }
        }

        if (!lowest_priority_task) {
            SCHEDULER_LOG_INFO("❌ [ALAP-Sync] 未找到可抢占的任务");
            return;
        }

        // ⭐ 关键修复：移除抢占检查中的ALAP Slack门控，让高优先级任务可以立即抢占
        // 原因：ALAP的Slack门控应该在调度时过滤（getTaskN），而不应该在抢占时过滤
        // 如果在抢占时也过��Slack，会导致高优先级任务（如Task_Assassin_Hungry, period=50）
        // 因为Slack>0而无法抢占低优先级任务（如Task_Mid_A, period=100），
        // 违反RM调度原则，导致饥饿和超时
        //
        // 新策略：
        // - 抢占时只看RM优先级，不看Slack（类似TIE调度器）
        // - Slack门控在批量调度的任务选择时生效（第799-805行）

        // 检查是否需要抢占（新任务优先级更高）
        if (shouldPreempt(lowest_priority_task, highest)) {
            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Sync] 抢占CPU: ") +
                              " 挂起低优先级任务=" + getTaskName(lowest_priority_task) +
                              " 调度高优先级任务=" + getTaskName(highest));

            // ⭐ 完整的抢占实现：
            // ⭐ V60修复：不从_ready_queue移除任务！
            // 原因：getTaskN()从_ready_queue获取任务，移除后会导致任务无法被调度
            // _current_batch_tasks只是标记哪些任务需要调度，实际调度仍通过getTaskN()
            // removeFromReadyQueue(highest);  // V60: 注释掉

            // 2. 从批量任务中移除被抢占的任务
            auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), lowest_priority_task);
            if (batch_it != _current_batch_tasks.end()) {
                _current_batch_tasks.erase(batch_it);
                SCHEDULER_LOG_DEBUG(std::string("🔄 [ALAP-Sync] 从批量任务移除: ") + getTaskName(lowest_priority_task));
            }

            // 3. 将高优先级任务加入批量任务（放在最前面）
            _current_batch_tasks.insert(_current_batch_tasks.begin(), highest);

            // ⭐ 修复：不在tick边界抢占时扣除能量，避免双重扣除
            // 能量将在批量调度中统一扣除
            SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Tick边界抢占: 任务加入批量（能量将在批量调度中扣除）: ") +
                              getTaskName(highest));

            // 5. 挂起低优先级任务
            setSuspendReason(lowest_priority_task, "preemption");
            _kernel->suspend(lowest_priority_task);

            // 6. 重新调度所有CPU
            _kernel->dispatch();
        } else {
            SCHEDULER_LOG_INFO(std::string("❌ [ALAP-Sync] 新任务优先级不够高，无需抢占"));
        }
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

        // ⭐ ALAP-Sync：任务到达时直接加入等待队列
        // Slack检查在每Tick的调度决策时进行
        Scheduler::insert(task);
        addToReadyQueue(task);

        // ⭐ 新增：mid-tick抢占支持
        // 仅对真正的新任务执行mid-tick抢占，跳过suspend重新插入的任务
        //
        // ⭐ Bug修复：使用isExecuting()作为启发式标志来判断是否是suspend重新插入
        // 原因：_current_batch_tasks在performTickScheduling()中被清空（Line 692），
        //      导致is_reinserted检查失效。suspend()后的任务仍然在执行中（isExecuting()=true），
        //      而真正的新任务不在运行中（isExecuting()=false）。
        //
        // ⭐ 关键逻辑：
        // - suspend()后的任务：isExecuting()=true，刚刚被挂起，应该跳过mid-tick抢占
        // - 真正的新任务：isExecuting()=false，刚到达，应该触发mid-tick抢占
        bool is_reinserted = task->isExecuting();

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] Mid-tick抢占检查: _kernel=") +
                          (_kernel ? "valid" : "null") +
                          " _energy_depleted=" + (_energy_depleted ? "true" : "false") +
                          " is_reinserted=" + (is_reinserted ? "true" : "false") +
                          " isExecuting()=" + (task->isExecuting() ? "true" : "false"));

        if (_kernel && !_energy_depleted && !is_reinserted) {
            // ⭐ 关键修复：移除mid-tick抢占中的Slack检查
            // 原因：mid-tick抢占应该由RM优先级决定，而不是Slack
            // Slack过滤应该在批量调度的任务选择时进行（第808-815行）
            // 这样可以确保高优先级任务（如Task_Assassin_Hungry, period=50）能及时抢占低优先级任务
            //
            // 举例：Task_Assassin_Hungry (arrival=50) 在t=50时Slack=30ms>0，但不抢占会导致
            //       在t=80时Slack=0ms≤0时，已经没有mid-tick抢占机会，导致deadline miss

            const auto& running_tasks = _kernel->getCurrentExecutingTasks();

            SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] 运行中任务数量: ") +
                              std::to_string(running_tasks.size()));

            // ⭐ V45关键修复：准确计算真正空闲的CPU数量
            int truly_free_cpus = 0;
            int busy_executing = 0;
            int busy_dispatching = 0;

            for (const auto& [cpu, running_task] : running_tasks) {
                bool is_dispatching = _kernel->isCPUDispatching(cpu);
                if (!running_task) {
                    if (!is_dispatching) {
                        truly_free_cpus++;
                    } else {
                        busy_dispatching++;
                    }
                } else if (running_task->isExecuting()) {
                    busy_executing++;
                } else {
                    busy_dispatching++;
                }
            }

            int total_cpus = running_tasks.size();

            SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Sync] Mid-tick CPU状态: 总数=") +
                              std::to_string(total_cpus) +
                              " 空闲=" + std::to_string(truly_free_cpus) +
                              " 执行中=" + std::to_string(busy_executing) +
                              " 上下文切换中=" + std::to_string(busy_dispatching));

            // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
            if (truly_free_cpus > 0) {
                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Mid-tick: 有空闲CPU，直接调度新任务: ") +
                                  getTaskName(task));
                // 检查能量（但不扣除，让下一个tick的批量调度统一扣除）
                double unit_energy = calculateUnitEnergyForTask(task);
                const double EPSILON = 1e-9;

                if (_current_energy >= unit_energy - EPSILON) {
                    // ⭐ 修复：不在mid-tick扣除能量，避免双重扣除
                    // 能量将在下一个tick的批量调度中统一扣除
                    SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Mid-tick: 能量充足，调度任务（能量将在下一tick扣除）: ") +
                                      getTaskName(task));

                    // 创建抢占微型批量
                    _preempt_batch_tasks.push_back(task);

                    // 立即调度到空闲CPU
                    _kernel->dispatch();
                }
                return;
            }

            // ⭐ CPU已满，需要找到优先级最低的任务进行抢占
            SCHEDULER_LOG_INFO("⚠️ [ALAP-Sync] Mid-tick: CPU已满，需要抢占最低优先级任务");

            AbsRTTask *lowest_priority_task = nullptr;
            int lowest_priority = -1;

            for (const auto& [cpu, running_task] : running_tasks) {
                if (!running_task) {
                    continue;
                }

                ALAPSyncTaskModel *model = getTaskModel(running_task);
                if (!model) {
                    continue;
                }

                int priority = model->getRMPriority();
                if (lowest_priority_task == nullptr || priority > lowest_priority) {
                    lowest_priority_task = running_task;
                    lowest_priority = priority;
                }
            }

            if (lowest_priority_task && shouldPreempt(lowest_priority_task, task)) {
                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Mid-tick: 找到可抢占任务: ") +
                                  getTaskName(lowest_priority_task));

                // 检查能量（但不扣除，让下一个tick的批量调度统一扣除）
                double unit_energy = calculateUnitEnergyForTask(task);
                const double EPSILON = 1e-9;

                if (_current_energy >= unit_energy - EPSILON) {
                    // 能量充足，执行mid-tick抢占
                    SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] Micro-Batch抢占: ") +
                                      getTaskName(lowest_priority_task) + " → " + getTaskName(task) +
                                      " [微型批量调度]");

                    // 创建抢占微型批量
                    _preempt_batch_tasks.push_back(task);

                    // ⭐ 修复：不在mid-tick扣除能量，避免双重扣除
                    // 能量将在下一个tick的批量调度中统一扣除
                    SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Sync] Micro-Batch: 能量充足，执行抢占（能量将在下一tick扣除）: ") +
                                      getTaskName(task));

                    // 挂起低优先级任务
                    setSuspendReason(lowest_priority_task, "preemption");
                    _kernel->suspend(lowest_priority_task);

                    // 立即调度高优先级任务
                    SCHEDULER_LOG_INFO(std::string("🚀 [ALAP-Sync] 对调后立即dispatch调度高优先级任务"));
                    _kernel->dispatch();

                    return;  // 抢占完成，退出
                }
            }
        }

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
        ALAPSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Sync] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
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
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-Sync] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        ALAPSyncEnergyCheckEvent *evt = new ALAPSyncEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        Tick current_time = SIMUL.getTime();
        Tick scheduled_time = current_time + 1;
        evt->post(scheduled_time);

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString() +
                           " 当前时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                           " 调度时间=" + std::to_string(static_cast<int64_t>(scheduled_time)) + "ms");
    }

    void ALAPSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Sync] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
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

        // ⭐ 逐渐扣除模式：从计数集合中移除任务
        _counted_tasks_in_dispatch.erase(task);

        // ⭐ 停止能量检查事件
        stopEnergyCheckForTask(task);

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
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Sync] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Sync] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
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
                Tick deadline = arrival + Tick(model->getPeriod());

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
            Tick deadline = arrival + Tick(model->getPeriod());

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
            Tick deadline = arrival + Tick(model->getPeriod());

            if (deadline <= current_time) {
                expired_batch.push_back(task);
            }
        }

        for (AbsRTTask *task : expired_batch) {
            auto it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
            if (it != _current_batch_tasks.end()) {
                _current_batch_tasks.erase(it);
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

    // ⭐ 保留原有的全局检查函数（供兼容性使用）
    bool ALAPSyncScheduler::checkALAPTimingGate() {
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
            SCHEDULER_LOG_INFO("⏸️  [ALAP-Sync] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ALAP-Sync] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }

    MetaSim::Tick ALAPSyncScheduler::calculateSlackForTask(AbsRTTask *task) {
        if (!task) return MetaSim::Tick(0);

        Tick current_time = SIMUL.getTime();
        Tick arrival = task->getArrival();
        int period_int = task->getPeriod();
        Tick period = Tick(period_int > 0 ? period_int : 100);
        Tick absolute_deadline = arrival + period;

        double remaining_double = task->getRemainingWCET();

        // ⭐ V76关键修复：处理剩余时间为负的情况
        // 原因：当任务被suspend时，execdTime可能被累加导致超过WCET
        // 修复：剩余时间最小为0（任务已完成或超时）
        if (remaining_double < 0) {
            remaining_double = 0;
        }

        Tick remaining = Tick(remaining_double);
        Tick slack = absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_DEBUG("🧮 [ALAP-Sync] Slack计算: " +
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
} // namespace RTSim
