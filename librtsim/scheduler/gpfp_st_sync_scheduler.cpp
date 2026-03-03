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
        : MetaSim::Event("STSyncTickEvent", MetaSim::Event::_DEFAULT_PRIORITY - 1),
          _scheduler(scheduler) {
        // ⭐ 关键修复：提高tick事件优先级，确保tick事件及时触发
        // 原优先级_DEFAULT_PRIORITY + 10太低，导致tick事件被延迟
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
        SCHEDULER_LOG_WARNING(std::string("⏰ [ST-Sync V122] 组唤醒事件触发 @ ") +
                             std::to_string(static_cast<int64_t>(current_time)) + "ms");

        // 清除深度充电状态，允许调度
        _scheduler->_deep_charging = false;
        _scheduler->_energy_depleted = false;
        _valid = false;

        // 立即触发一次调度
        _scheduler->performTickScheduling();
    }

    void STSyncGroupWakeEvent::schedule(MetaSim::Tick wake_time) {
        _wake_time = wake_time;
        _valid = true;

        // 注册新事件到模拟器事件队列
        post(wake_time);

        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync V122] 唤醒事件已注册: ") +
                          "唤醒时间=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
    }

    // =====================================================
    // STSyncEnergyCheckEvent 实现 - 运行时能量检查
    // =====================================================

    STSyncEnergyCheckEvent::STSyncEnergyCheckEvent(STSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("STSyncEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 更高优先级，确保能量检查及时执行
    }

    void STSyncEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // 🔍 调试：记录能量检���事件触发时间
        Tick actual_trigger_time = SIMUL.getTime();
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 能量检查事件触发: ") +
                           _scheduler->getTaskName(_task) +
                           " 触发时间=" + std::to_string(static_cast<int64_t>(actual_trigger_time)) + "ms" +
                           " _ms_executed=" + std::to_string(_ms_executed));

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 能量检查：任务已删除，停止检查"));
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 能量检查：任务已停止执行，不再扣除能量: ") +
                               _scheduler->getTaskName(_task) + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        STSyncTaskModel *task_model = _scheduler->getTaskModel(_task);

        // 🔍 调试日志：检查WCET
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ST-Sync] WCET���查: ") +
                           task_name + " 已执行=" + std::to_string(_ms_executed) +
                           "ms task_model=" + (task_model ? "有效" : "NULL"));

        if (task_model) {
            int wcet = task_model->getWCET();
            SCHEDULER_LOG_DEBUG(std::string("🔍 [ST-Sync] WCET值: ") +
                               std::to_string(wcet) + "ms 判断: " +
                               std::to_string(_ms_executed) + " >= " + std::to_string(wcet) +
                               " = " + (_ms_executed >= wcet ? "TRUE" : "FALSE"));

            if (_ms_executed >= wcet) {
                SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 任务已达到WCET，完成执行: ") +
                                   task_name + " 已执行=" + std::to_string(_ms_executed) +
                                   "ms WCET=" + std::to_string(wcet) + "ms");
                // ⭐ 关键修复：标记任务已达到WCET，防止批量调度重复扣除能量
                _scheduler->_tasks_completed_wcet.insert(_task);
                SCHEDULER_LOG_INFO(std::string("🏁 [ST-Sync] 标记任务已完成WCET: ") +
                                   task_name);

                // ⭐ 关键修复：不直接调用onEnd()，而是让任务自然结束
                // 终止能量检查事件，让内核在下一个tick时检测到任务完成并调用onEnd()
                // 避免在能量检查事件中调用onEnd()导致的"No CPU"崩溃
                SCHEDULER_LOG_INFO(std::string("🛑 [ST-Sync] 任务达到WCET，终止能量检查事件: ") + task_name);

                // 任务已完成，不再检查能量预扣，也不重新调度事件
                return;
            }
        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] WCET检查失败：找不到TaskModel ") + task_name);
        }

        // ⭐ ST-Sync关键修复：在扣除能量之前检查是否有足够能量（与TIE保持一致）
        // 设计原则：
        // - 批量调度时进行"全有或全无"门槛检查，但不预扣能量
        // - 能量检查事件在实际执行时每1ms扣除一次能量
        // - ⭐ 关键：先检查能量是否足够再扣除，不足则立即中断任务

        // 检查是否有足够能量续期1ms
        if (current_energy < unit_energy - EPSILON) {
            // ❌ 能量不足，立即中断任务
            SCHEDULER_LOG_WARNING(std::string("⚡ [ST-Sync] 续期能量不足，立即中断任务: ") +
                                 _scheduler->getTaskName(_task) +
                                 " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                 " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                                 " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;
            // ⭐ V121修复：同时设置深度充电标志，确保唤醒闹钟能正常工作
            _scheduler->_deep_charging = true;

            // ⭐ 关键修复：立即suspend任务（与TIE保持一致）
            if (_scheduler->_kernel && _task->isExecuting()) {
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_WARNING(std::string("🛑 [ST-Sync] 任务因能量不足被挂起: ") +
                                     _scheduler->getTaskName(_task));
            }

            // 不重新调度能量检查事件
            return;
        }

        // ✅ 能量充足，立即扣除 1ms 的能量
        _scheduler->_current_energy -= unit_energy;
        _scheduler->_stats.total_energy_consumed += unit_energy;

        SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 扣除 1ms 能量: ") +
                           _scheduler->getTaskName(_task) +
                           " 扣除=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " 剩余=" + std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
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
        // k = min(CPU核心总数, 就绪队列任务数)
        ConfigManager &configMgr = ConfigManager::getInstance();
        int total_cpus = configMgr.getNumCores();
        int ready_tasks = static_cast<int>(_ready_queue.size());
        int batch_size = std::min(total_cpus, ready_tasks);

        SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync] calculateBatchSize: ") +
                           "CPU核心数=" + std::to_string(total_cpus) +
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
        SCHEDULER_LOG_DEBUG(std::string("🔄 [ST-Sync] performTickScheduling @ ") +
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
                SCHEDULER_LOG_INFO(std::string("☀️ [ST-Sync] Tick边界收集能量: ") +
                                   std::to_string(harvested) + "J" +
                                   " 当前能量: " + std::to_string(_current_energy) + "J" +
                                   " 经过时间: " + std::to_string(static_cast<int64_t>(elapsed)) + "ms");

                // ⭐ 如果收集到能量，清除能量耗尽标志
                if (_energy_depleted && _current_energy > 0.000001) {
                    _energy_depleted = false;
                    SCHEDULER_LOG_INFO("🔋 [ST-Sync] 太阳能充电成功，恢复调度");
                }
            }
        }

        _last_tick_time = current_time;

        // ⭐ Bug修复3：能量耗尽时跳过调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，跳过Tick调度"));
            return;  // 不进行任何调度，包括中断检查
        }

        // ⭐ V121修复：删除Tick级能量扣除，避免与EnergyCheckEvent双重扣电
        // EnergyCheckEvent已经在每毫秒精确扣除每个任务的能量
        // 之前这里额外扣除一次导致2倍速耗电

        // ========== ST深度充电检查 ==========
        // 能量不足时进入深度充电模式，充电到 minSlack=0 或电池充满
        if (_deep_charging) {
            // 计算最小Slack
            Tick min_slack = calculateMinSlack();
            int64_t min_slack_ms = static_cast<int64_t>(min_slack);

            // 收集运行中任务 + top K 个就绪任务的能量需求
            // （与Step 4中的计算逻辑一致）
            double total_energy_needed = 0.0;
            std::vector<AbsRTTask*> sorted_ready(_ready_queue.begin(), _ready_queue.end());
            std::sort(sorted_ready.begin(), sorted_ready.end(),
                [this](AbsRTTask* a, AbsRTTask* b) {
                    auto model_a = getTaskModel(a);
                    auto model_b = getTaskModel(b);
                    if (model_a && model_b) {
                        return model_a->getRMPriority() < model_b->getRMPriority();
                    }
                    return false;
                });

            // 获取CPU核心数
            ConfigManager &configMgr = ConfigManager::getInstance();
            int total_cpus = configMgr.getNumCores();

            // 获取当前运行中任务数
            const auto& running_tasks = _kernel->getCurrentExecutingTasks();
            int running_count = 0;
            for (const auto& map_pair : running_tasks) {
                if (map_pair.second && map_pair.second->isExecuting() &&
                    _tasks_completed_wcet.find(map_pair.second) == _tasks_completed_wcet.end()) {
                    running_count++;
                }
            }

            // 计算K：与 ALAP-Sync 一致，K = min(就绪任务数, 总核心数)
            int ready_count = static_cast<int>(sorted_ready.size());
            int K = std::min(ready_count, total_cpus);

            // 计算空闲核心数
            int free_cpus = total_cpus - running_count;
            int new_tasks_needed = std::min(K, free_cpus);

            // ⭐⭐⭐ V111修复：深度充电检查使用"总能量预算"而非"单步能量" ⭐⭐⭐
            // 运行中任务：剩余执行时间 × 单位能耗
            for (const auto& map_pair : running_tasks) {
                if (map_pair.second && map_pair.second->isExecuting() &&
                    _tasks_completed_wcet.find(map_pair.second) == _tasks_completed_wcet.end()) {
                    AbsRTTask* task = map_pair.second;
                    double remaining_double = task->getRemainingWCET();
                    int64_t remaining_ms = static_cast<int64_t>(remaining_double);
                    if (remaining_ms < 1) remaining_ms = 1;
                    double unit_energy = calculateUnitEnergyForTask(task);
                    total_energy_needed += unit_energy * remaining_ms;  // 总能量预算
                }
            }

            // 新任务：完整WCET × 单位能耗
            for (int i = 0; i < new_tasks_needed && i < ready_count; ++i) {
                if (sorted_ready[i] && sorted_ready[i]->isActive()) {
                    AbsRTTask* task = sorted_ready[i];
                    STSyncTaskModel *model = getTaskModel(task);
                    int64_t wcet_ms = model ? model->getWCET() : 10;
                    if (wcet_ms < 1) wcet_ms = 1;
                    double unit_energy = calculateUnitEnergyForTask(task);
                    total_energy_needed += unit_energy * wcet_ms;  // 总能量预算
                }
            }

            SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync V111] 深度充电中... Slack=") +
                              std::to_string(min_slack_ms) + "ms " +
                              "能量=" + std::to_string(_current_energy * 1000) + "mJ " +
                              "K=" + std::to_string(K) +
                              " 总预算=" + std::to_string(total_energy_needed * 1000) + "mJ");

            // 唤醒条件：Slack<=0 或 电池充满 或 能量足够调度K个任务
            if (min_slack_ms <= 0) {
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：Slack<=0，唤醒调度");
                _deep_charging = false;
                _energy_depleted = false;
            } else if (_current_energy >= _max_energy - 0.000001) {
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：电池充满，唤醒调度");
                _deep_charging = false;
                _energy_depleted = false;
            } else if (_current_energy >= total_energy_needed - 1e-9) {
                // 新增：能量已足够调度K个任务，唤醒调度
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：能量已足够，唤醒调度");
                _deep_charging = false;
                _energy_depleted = false;
            } else {
                // 仍在深度充电，跳过本次调度
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电中，跳过调度");
                return;
            }
        }

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第1.5步：清理过期任务实例 ==========
        // ⭐ 已改用killOnMiss(true)，框架自动处理过期实例
        // cleanupExpiredTasks();

        // ========== 阶段一：准备批量调度 ==========
        // ⭐ ST-Sync使用个体Slack门控（在任务选择时过滤），不使用全局门控

        // 先收集运行中任务和就绪队列（用于构建批次）
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] _kernel为nullptr，跳过批量调度");
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

        // ========== Phase 2: ST-Sync 批次级时序门控（已禁用）==========
        // ⭐ 关键修复：移除批次级时序门控，避免过度阻塞
        // 原因：批次时序门控计算Batch Slack = min(Slack_i)，导致在t=96时如果批次中有一个任务Slack>0，
        //      整个批次被阻塞，调度延迟到t=100，导致deadline miss
        // 解决方案：在任务选择时进行个体Slack过滤（第798-804行）���只调度Slack<=0的任务
        // 这样既保证了ALAP时序门控的功能，又避免了过度阻塞
        // ========== Phase 2: ST-Sync 个体时序门控 ==========
        // ⭐ 关键修复：使用个体Slack检查，不是批次级门控
        // 原因：每个任务有自己的Slack和释放时间，应该独立判断
        //      Slack<=0的任务进入就绪队列接受调度
        //      这才是"尽可能晚执行"的正确实现
        //
        // ⭐ 批次级门控是错误的！它会让所有任务一起调度，
        //      违反了"尽可能晚执行"的原则

        // ⭐ ST-Sync关键修复：采用正确的能量扣除逻辑（后扣方式）
        // ⭐ V58：energy_to_deduct已在上面统一计算

        // 🔍 调试：输出_currExe的内容
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] _currExe内容 (") +
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

        // ⭐⭐⭐ V90: ST-Sync统一批量调度框架 ⭐⭐⭐
        // 与ASAP-Sync/ALAP-Sync统一框架，加入ST特有逻辑：
        // - 能量充足时：像ASAP一样立即调度所有任务（不管Slack）
        // - 能量不足时：全部挂起，充电到能量满或最短Slack归零
        
        const double EPSILON_V90 = 1e-9;
        
        // ========== Step 1: 收集候选任务（ST特有：能量充足时不管Slack）==========
        // 按RM优先级排序就绪队列
        std::vector<AbsRTTask*> sorted_ready_v90(_ready_queue.begin(), _ready_queue.end());
        std::sort(sorted_ready_v90.begin(), sorted_ready_v90.end(),
            [this](AbsRTTask* a, AbsRTTask* b) {
                auto model_a = getTaskModel(a);
                auto model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });
        
        // ST逻辑：收集所有就绪任务（排除运行中的），不管Slack
        std::vector<AbsRTTask*> ready_tasks_v90;
        for (auto* task : sorted_ready_v90) {
            if (!task || !task->isActive()) continue;
            
            // 排除运行中的任务
            bool is_running = false;
            for (auto* rt : running_task_list) {
                if (task == rt) { is_running = true; break; }
            }
            if (is_running) continue;
            
            ready_tasks_v90.push_back(task);
        }
        
        // ========== Step 2: 计算K（批量大小）==========
        // 与 ALAP-Sync 一致：K = min(就绪任务数, 总核心数)
        int ready_cnt_v90 = static_cast<int>(ready_tasks_v90.size());
        int K_v90 = std::min(ready_cnt_v90, total_cpus);
        
        // ========== Step 3: 计算最短Slack（用于能量不足时的唤醒）==========
        // ⭐ V117+V118修复：检查就绪队列、等待队列和运行中的任务
        // ⭐ V120修复: 使用Tick::impl_t获取最大值
        Tick min_slack = std::numeric_limits<Tick::impl_t>::max();

        // 检查就绪队列
        SCHEDULER_LOG_INFO(std::string("🔍 [V117] Ready tasks: size=") +
                          std::to_string(ready_tasks_v90.size()));
        for (auto* task : ready_tasks_v90) {
            Tick task_slack = calculateSlackForTask(task);
            SCHEDULER_LOG_INFO(std::string("🔍 [V117] Ready task: ") +
                              getTaskName(task) + " slack=" + std::to_string(static_cast<int64_t>(task_slack)) +
                              " min_slack=" + std::to_string(static_cast<int64_t>(min_slack)));
            if (static_cast<int64_t>(task_slack) < static_cast<int64_t>(min_slack)) {
                min_slack = task_slack;
                SCHEDULER_LOG_INFO(std::string("🔍 [V117] Updated min_slack to ") +
                                  std::to_string(static_cast<int64_t>(min_slack)));
            }
        }

        // ⭐ V117修复：也检查等待队列
        for (auto* task : _waiting_queue) {
            if (!task) continue;
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack < min_slack) {
                min_slack = task_slack;
            }
        }

        // ⭐ V117修复：也检查运行中的任务
        SCHEDULER_LOG_INFO(std::string("🔍 [V117] Running tasks check: size=") +
                          std::to_string(running_task_list.size()));
        for (auto* task : running_task_list) {
            if (!task) continue;
            Tick task_slack = calculateSlackForTask(task);
            SCHEDULER_LOG_INFO(std::string("🔍 [V117] Running task: ") +
                              getTaskName(task) + " slack=" + std::to_string(static_cast<int64_t>(task_slack)));
            if (task_slack < min_slack) {
                min_slack = task_slack;
                SCHEDULER_LOG_INFO(std::string("🔍 [V117] Updated min_slack to ") +
                                  std::to_string(static_cast<int64_t>(min_slack)));
            }
        }

        // ⭐ V118修复：只有当所有队列都为空时才设置Slack=0
        if (ready_tasks_v90.empty() && _waiting_queue.empty() && running_task_list.empty()) {
            min_slack = 0;
        }

        // ========== Step 4: 计算K个任务的总能量预算（V111 ST修复）==========
        // ⭐⭐⭐ V111修复：使用"总能量预算"而非"单步能量" ⭐⭐⭐
        //
        // ST算法核心原则：只有当电量足以支撑任务组"一口气跑完全程"时才调度
        // - 运行中任务：剩余执行时间 * 单位能耗
        // - 新任务：完整WCET * 单位能耗
        //
        // 之前的V92使用"单步能量"是ASAP逻辑，不是ST逻辑
        // 单步能量会导致"调度-挂起-调度-挂起"的抖动
        double total_energy_budget = 0.0;  // 总能量预算（跑完全程需要的能量）
        std::vector<AbsRTTask*> k_tasks_v90;

        // 计算空闲核心数
        int running_cnt_v90 = static_cast<int>(running_task_list.size());
        int free_cpus_v90 = total_cpus - running_cnt_v90;

        // ⭐⭐⭐ V111：运行中任务使用"剩余执行时间"计算能量预算
        for (auto* task : running_task_list) {
            if (_tasks_completed_wcet.find(task) == _tasks_completed_wcet.end()) {
                k_tasks_v90.push_back(task);

                // 获取剩余执行时间
                double remaining_double = task->getRemainingWCET();
                int64_t remaining_ms = static_cast<int64_t>(remaining_double);
                if (remaining_ms < 1) remaining_ms = 1;  // 至少1ms

                // 总能量预算 = 剩余时间 * 单位能耗
                double unit_energy = calculateUnitEnergyForTask(task);
                double task_budget = unit_energy * remaining_ms;

                total_energy_budget += task_budget;

                SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync V111] 运行任务能量预算: ") +
                                   getTaskName(task) +
                                   " 剩余=" + std::to_string(remaining_ms) + "ms" +
                                   " 单位=" + std::to_string(unit_energy * 1000) + "mJ/ms" +
                                   " 预算=" + std::to_string(task_budget * 1000) + "mJ");
            }
        }

        // ⭐⭐⭐ V111：新任务使用"完整WCET"计算能量预算
        int num_new_tasks = std::min(K_v90, free_cpus_v90);
        for (int i = 0; i < num_new_tasks && i < static_cast<int>(ready_tasks_v90.size()); ++i) {
            auto* task = ready_tasks_v90[i];
            k_tasks_v90.push_back(task);

            // 获取任务的完整WCET
            STSyncTaskModel *model = getTaskModel(task);
            int64_t wcet_ms = model ? model->getWCET() : 10;  // 默认10ms
            if (wcet_ms < 1) wcet_ms = 1;

            // 总能量预算 = WCET * 单位能耗
            double unit_energy = calculateUnitEnergyForTask(task);
            double task_budget = unit_energy * wcet_ms;

            total_energy_budget += task_budget;

            SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync V111] 新任务能量预算: ") +
                               getTaskName(task) +
                               " WCET=" + std::to_string(wcet_ms) + "ms" +
                               " 单位=" + std::to_string(unit_energy * 1000) + "mJ/ms" +
                               " 预算=" + std::to_string(task_budget * 1000) + "mJ");
        }

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync V111] 批量决策: ") +
                          "运行中=" + std::to_string(running_task_list.size()) +
                          " 就绪=" + std::to_string(ready_tasks_v90.size()) +
                          " K(新任务)=" + std::to_string(K_v90) +
                          " 可调度=" + std::to_string(num_new_tasks) +
                          " 最短Slack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync V111] 能量预算(总能量): ") +
                          "K个任务=" + std::to_string(k_tasks_v90.size()) +
                          " 总预算=" + std::to_string(total_energy_budget * 1000) + " mJ" +
                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
        
        // ========== Step 5: All-or-Nothing决策（ST特有充电逻辑）==========
        if (_current_energy >= total_energy_budget - EPSILON_V90) {
            // ⭐⭐⭐ 能量充足：像ASAP一样，全部调度 ⭐⭐⭐
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync V92] 能量充足��全部调度"));

            // ⭐ 关键修复：不在 tick 边界扣除能量！
            // 能量由 STSyncEnergyCheckEvent 在实际执行时每 1ms 扣除
            // 这里只做"全有或全无"门槛检查，不预扣能量

            _current_batch_tasks = k_tasks_v90;
            _current_batch_size = k_tasks_v90.size();
            _batch_scheduled_this_tick = true;
            _stats.total_batch_schedules++;
            
            // 清除深度充电状态
            _deep_charging = false;
            _energy_depleted = false;

            // ⭐⭐⭐ V91修复：V90成功后直接返回，跳过旧的批量调度代码 ⭐⭐⭐
            // 旧的代码会覆盖_current_batch_tasks并应用Slack过滤，导致任务丢��
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync V92] V92批量调度成功，跳过旧代码路径") +
                              " 批量任务数=" + std::to_string(_current_batch_tasks.size()));

            // ⭐ V99修复：只在有新任务时才调用dispatch
            // 避免不必要的dispatch调用导致频繁抢占
            if (_kernel && !_current_batch_tasks.empty()) {
                // ⭐ V106修复：重新启用dispatch，解决核心碎片化问题
                // 当任务完成后有空闲核心时，必须立即dispatch等待的任务
                // 否则会导致核心空闲等待，浪费计算资源
                SCHEDULER_LOG_INFO(std::string("🚀 [ST-Sync V106] 有新任务，调用dispatch") +
                                  " 批量任务数=" + std::to_string(_current_batch_tasks.size()));
                _kernel->dispatch();
            }
            return;  // 直接返回，不再执行旧的批量调度代码

        } else {
            // ⭐⭐⭐ 能量不足���全部挂起，充电到能量满或最短Slack归零 ⭐⭐⭐
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync V92] 能量不足，全部挂起") +
                                 " 需要=" + std::to_string(total_energy_budget * 1000) + " mJ" +
                                 " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                                 " 最短Slack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");
            
            // 设置深度充电状态
            _deep_charging = true;
            // V123修复：Slack<=0时的紧急调度 - 使用首毫秒能量而非完整WCET能量
            if (static_cast<int64_t>(min_slack) <= 0 && !ready_tasks_v90.empty()) {
                SCHEDULER_LOG_WARNING("Emergency: Slack<=0, trying degraded scheduling");
                std::vector<AbsRTTask*> emergency_tasks;
                double emergency_energy = 0.0;
                std::vector<AbsRTTask*> sorted_emergency(ready_tasks_v90.begin(), ready_tasks_v90.end());
                std::sort(sorted_emergency.begin(), sorted_emergency.end(),
                    [this](AbsRTTask* a, AbsRTTask* b) {
                        auto model_a = getTaskModel(a);
                        auto model_b = getTaskModel(b);
                        if (model_a && model_b) return model_a->getRMPriority() < model_b->getRMPriority();
                        return false;
                    });
                for (auto* task : sorted_emergency) {
                    if (!task || !task->isActive()) continue;
                    // V123修复：使用首毫秒能量，而非完整WCET能量
                    double task_unit_energy = calculateUnitEnergyForTask(task);
                    if (_current_energy >= emergency_energy + task_unit_energy - 1e-9) {
                        emergency_tasks.push_back(task);
                        emergency_energy += task_unit_energy;
                        SCHEDULER_LOG_INFO(std::string("Emergency task: ") + getTaskName(task) +
                                          " unit_energy=" + std::to_string(task_unit_energy * 1000) + "mJ");
                    }
                }
                if (!emergency_tasks.empty()) {
                    SCHEDULER_LOG_WARNING(std::string("V123 Emergency scheduling ") + std::to_string(emergency_tasks.size()) + " tasks");
                    // 清除深度充电状态
                    _deep_charging = false;
                    _energy_depleted = false;
                    _current_batch_tasks = emergency_tasks;
                    _current_batch_size = emergency_tasks.size();
                    _batch_scheduled_this_tick = true;
                    _stats.total_batch_schedules++;
                    if (_kernel && !_current_batch_tasks.empty()) _kernel->dispatch();
                    return;
                }
            }
            _energy_depleted = true;
            
            // 计算唤醒时间：最短Slack归零 或 电池充满（取较早者）
            Tick wake_time = calculateGroupWakeTime(min_slack, total_energy_budget);
            
            SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync V90] 深度充电：唤醒时间=") +
                              std::to_string(static_cast<int64_t>(wake_time)) + "ms");
            
            // 挂起所有运行中的任务（All-or-Nothing）
            for (auto* task : running_task_list) {
                if (_kernel && task->isExecuting()) {
                    setSuspendReason(task, "insufficient_energy");  // V115：记录挂起原因
                    SCHEDULER_LOG_WARNING(std::string("🛑 [ST-Sync V90] 挂起: ") + getTaskName(task));
                    _kernel->suspend(task);
                }
            }
            
            _current_batch_tasks.clear();
            _current_batch_size = 0;
            _batch_scheduled_this_tick = false;
            _stats.total_batch_skipped++;
            
            // 设置唤醒定时器（在最短Slack归零或电池充满时唤醒）
            if (wake_time > current_time) {
                scheduleGroupWakeEvent(wake_time);
            }
            
            return;  // 直接返回，跳过后续调度
        }

        // ⭐ 关键修复：如果能量已耗尽，不调度新任务
        if (_energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，跳过批量调度") +
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
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 检测到能量在运行时检查中耗尽，跳过批量调度") +
                               " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        \
        // ========== 第2.5步：Tick边界抢占检查 ==========
        // ⭐ V56修复：重新启用tick边界抢占，添加防抖机制
        // 每个tick开始时清除抢占防抖标记
        _last_preempted_task = nullptr;

        // ⭐ V96修复：大幅减少不必要的抢占检查
        // 问题：V95的条件太宽松，导致每个tick都触发抢占
        // 解决：只在有新任务到达时才检查抢占
        // 判断方法：比较当前就绪队列大小与上次记录的大小
        size_t current_ready_size = _ready_queue.size();
        bool has_new_tasks = (current_ready_size > _last_ready_queue_size);
        _last_ready_queue_size = current_ready_size;  // 更新记录

        // ⭐ 调用checkAndPreempt进行抢占检查（V96：只在有新任务时触发）
        if (has_new_tasks) {
            SCHEDULER_LOG_INFO(std::string("🔔 [ST-Sync V96] 有新任务到达，触发抢占检查") +
                              " 当前就绪=" + std::to_string(current_ready_size));
            checkAndPreempt();
        }

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
        std::vector<AbsRTTask *> all_ready_tasks_2;
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
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [ST-Sync] 清理不活动任务: ") + getTaskName(task));
                            return true;
                        }
                        // ⭐ 移除过期的周期性任务实例：使用getDeadline()获取绝对截止时间
                        Tick deadline = task->getDeadline();
                        if (deadline < current_time) {
                            SCHEDULER_LOG_DEBUG(std::string("🧹 [ST-Sync] 清理过期任务实例: ") +
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
            SCHEDULER_LOG_INFO(std::string("📋 [ST-Sync] 就绪队列内容 (共") +
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
            SCHEDULER_LOG_INFO(std::string("🏃 [ST-Sync] 运行中任务列表 (共") +
                               std::to_string(running_task_list.size()) + "个任务):");
            for (size_t i = 0; i < running_task_list.size(); ++i) {
                SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                                   getTaskName(running_task_list[i]));
            }

            // ⭐ 关键修复：排除已经在运行中的任务，避免重复调度
            std::vector<AbsRTTask *> filtered_ready;
            for (auto* task : sorted_ready_v90) {
                bool is_running = false;
                for (auto* running_task : running_task_list) {
                    if (task == running_task) {
                        is_running = true;
                        SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 跳过已在运行中的任务: ") +
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

                // ⭐ ST-Sync个体时序门控：只调度Slack<=0的任务
                // 每个任务独立检查Slack，Slack<=0才进入就绪队列
                // 这是"尽可能晚执行"原则的体现
                Tick task_slack = calculateSlackForTask(task);
                if (task_slack > 0) {
                    SCHEDULER_LOG_DEBUG(std::string("⏸️  [ST-Sync] 任务选择: Slack>0，跳过 ") +
                                       getTaskName(task) +
                                       " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                    continue;  // Slack>0，跳过（尽可能晚执行）
                }

                new_tasks_to_schedule.push_back(task);
                selected_count++;
                SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 从批次选择新任务: ") +
                                   getTaskName(task) + " (" + std::to_string(selected_count) + "/" +
                                   std::to_string(free_cpus) + ")");
            }

            // 保存所有就绪任务用于日志
            all_ready_tasks_2.assign(sorted_ready.begin(), sorted_ready.end());
        }

#if 0
        // 3. ⭐ ST-Sync关键：每个tick都预扣运行任务续期+新任务的能量
        // 这样可以在能量耗尽时及时中断任务

        // 计算运行中任务的续期能���（每个tick都要续期）
        double running_tasks_renewal_energy = 0.0;
        for (auto* task : running_task_list) {
            // ⭐ 关键修复：跳过已达到WCET的任务，避免重复扣除能量
            // 因为能量检查事件在任务达到WCET时会标记完成，但kernel可能还没处理end_instance
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                SCHEDULER_LOG_INFO(std::string("⚠️ [ST-Sync] 批量调度：跳过已完成WCET的任务: ") +
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

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 开始遍历候选批次: ") +
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
                SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 跳过运行中任务: ") + getTaskName(task));
                continue;  // 跳过运行中的任务
            }

            // ⭐ ST-Sync核心：个体Slack检查，只有Slack<=0的任务才能调度
            // 这体现了"尽可能晚执行"的原则
            Tick task_slack = calculateSlackForTask(task);
            Tick current_time = SIMUL.getTime();
            double remaining_double = task->getRemainingWCET();
            SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 检查任务: ") + getTaskName(task) +
                              " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms" +
                              " (deadline=" + std::to_string(static_cast<int64_t>(task->getDeadline())) +
                              " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                              " remaining=" + std::to_string(static_cast<int64_t>(Tick(remaining_double))) + ")");

            if (task_slack > 0) {
                skipped_slack++;
                SCHEDULER_LOG_DEBUG(std::string("⏸️  [ST-Sync] Slack>0，跳过任务 ") +
                                   getTaskName(task) +
                                   " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                continue;  // Slack>0，跳过（尽可能晚执行）
            }

            // Slack<=0，任务必须调度，计算其能耗
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Slack<=0，可选任务: ") + getTaskName(task) +
                              " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms" +
                              " 能耗=" + std::to_string(calculateUnitEnergyForTask(task) * 1000) + " mJ");
            new_tasks_energy += calculateUnitEnergyForTask(task);
            candidate_count++;
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 候选批次遍历完成: ") +
                          "检查=" + std::to_string(checked_count) +
                          " 跳过运行中=" + std::to_string(skipped_running) +
                          " 跳过Slack>0=" + std::to_string(skipped_slack) +
                          " 可用=" + std::to_string(candidate_count));

        // ⭐ ST-Sync总能量需求 = 运行中任务续期 + 新任务（每个tick都扣除）
        double total_energy_needed = running_tasks_renewal_energy + new_tasks_energy;

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] 批量调度决策: ") +
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

        // 4. ⭐ ST-Sync核心：批量能量判断（"全有或全无"���
        // ⭐ Bug #3修复：检查总能量需求（运行中续期+新任务），确保有足夠能量才调度
        const double EPSILON = 1e-9;
        // ⭐ V38修复：使用与TIE/TGF相同的能量检查条件
        // 当能量 <= 总能量需求时，立即中断任务（不扣除能量），避免超额透支
        // ⭐ V39修复：使用与TIE/TGF完全相同的能量检查条件
        // TIE: current_energy <= unit_energy + EPSILON → 挂起
        // ST-Sync: current_energy <= total_energy_needed + EPSILON → 挂起
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

            SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 批量调度门槛检查通过: ") +
                              "新任务数=" + std::to_string(new_tasks_to_schedule.size()) +
                              " 运行任务数=" + std::to_string(running_count) +
                              " 新任务能耗=" + std::to_string(new_tasks_energy * 1000) + " mJ " +
                              "当前能量=" + std::to_string(_current_energy * 1000) + " mJ");

            _current_batch_tasks = all_tasks_to_dispatch;
            _current_batch_size = all_tasks_to_dispatch.size();
            _stats.total_batch_schedules++;

            // ⭐ V122修复：删除这里的能量扣除
            // 能量由EnergyCheckEvent在实际执行时每1ms精确扣除
            // 之前这里额外扣除导致total_consumed被重复计算

            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 批量调度成功: ") +
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

            SCHEDULER_LOG_WARNING(std::string("⚠️  [ST-Sync] 能量不足，全体下处理机: ") +
                                "总需要=" + std::to_string(total_energy_needed * 1000) + " mJ" +
                                " (运行��续期=" + std::to_string(running_tasks_renewal_energy * 1000) + " mJ" +
                                " 新任务=" + std::to_string(new_tasks_energy * 1000) + " mJ)" +
                                " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                                " 运行中=" + std::to_string(running_count) +
                                " → 全员休眠，等待能量收集");

            // ⭐ 关键：挂起运行中的任务（"全员进退、同生共死"）
            if (!running_task_list.empty()) {
                SCHEDULER_LOG_WARNING(std::string("🔴 [ST-Sync] 挂起运行中的任务: ") +
                                    std::to_string(running_task_list.size()) + " 个");
                for (auto* task : running_task_list) {
                    if (_kernel && task) {
                        SCHEDULER_LOG_INFO(std::string("🔴 [ST-Sync] 挂起任务: ") + getTaskName(task));
                        _kernel->suspend(task);
                    }
                }
            }
        }

#endif

        // 如果有kernel，循环触发dispatch直到填满所有CPU
        if (!_kernel) {
            SCHEDULER_LOG_DEBUG("⚠��� [ST-Sync] performTickScheduling: _kernel为nullptr，尝试获取");
            _kernel = getKernel();
        }

        // ⭐⭐⭐ Bug修复3：深度充电状态下跳过dispatch循环 ⭐⭐⭐
        // 如果在深度充电状态或能量耗尽，不应该尝试调度任务
        if (_deep_charging || _energy_depleted) {
            SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync] 深度充电/能量耗尽状态，跳过dispatch循环") +
                              " _deep_charging=" + (_deep_charging ? "true" : "false") +
                              " _energy_depleted=" + (_energy_depleted ? "true" : "false"));
            return;
        }

        if (_kernel) {
            SCHEDULER_LOG_INFO("🔔 [ST-Sync] performTickScheduling: 开始循环调度填满所有CPU");
            // ⭐ V31关键修复：循环调用dispatch()直到所有CPU被填满或无法调度更多任务
            // 这是多核调度器的正确行为：在一个tick内尽可能多地调度任务
            int dispatch_attempts = 0;
            const int MAX_DISPATCH_ITERATIONS = 100;  // 防止无限循环

            while (dispatch_attempts < MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] dispatch循环 #") + std::to_string(dispatch_attempts) +
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
                    SCHEDULER_LOG_INFO("✅ [ST-Sync] 所有CPU已填满，停止调度");
                    break;
                }

                // 记录调度前的任务数
                size_t tasks_before = _ready_queue.size() + _running_tasks.size();

                // 调用dispatch尝试调度更多任务
                SCHEDULER_LOG_INFO(std::string("🚀 [ST-Sync] 调用 _kernel->dispatch()"));
                // _kernel->dispatch(); // V105: 禁用，避免频繁deschedule

                // ⭐ Micro-Batch Preemption：dispatch后清除抢占批量
                // 抢占批量用于mid-tick立即调度，dispatch调用后即被"消费"
                // 无论任务是否成功调度，都清除抢占批量，避免阻塞后续调度
                if (!_preempt_batch_tasks.empty()) {
                    SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] dispatch后清除抢占批量") +
                                       " size=" + std::to_string(_preempt_batch_tasks.size()));
                    _preempt_batch_tasks.clear();
                }

                dispatch_attempts++;

                // 记录调度后的任务数
                size_t tasks_after = _ready_queue.size() + _running_tasks.size();

                // 如果没有任务被调度（状态没变化），停止调度
                if (tasks_before == tasks_after) {
                    SCHEDULER_LOG_DEBUG("⏹️ [ST-Sync] 无更多任务可调度，停止dispatch循环");
                    break;
                }

                SCHEDULER_LOG_DEBUG(std::string("🔄 [ST-Sync] dispatch循环 #") + std::to_string(dispatch_attempts) +
                                   " _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                                   " _running_tasks.size()=" + std::to_string(_running_tasks.size()));
            }

            if (dispatch_attempts >= MAX_DISPATCH_ITERATIONS) {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] dispatch循环达到最大迭代次数，可能存在bug");
            }

            // ⭐ 注意：批量调度后的抢占检查已删除
            // 原因：mid-tick抢占已在insert()中通过Micro-Batch抢占处理
        } else {
            SCHEDULER_LOG_INFO("⚠️ [ST-Sync] performTickScheduling: _kernel仍为nullptr，跳过dispatch");
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
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] getTaskN(") + std::to_string(n) + ") @ " +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms " +
                           "能量: " + std::to_string(_current_energy * 1000) + " mJ " +
                           "ready_queue: " + std::to_string(_ready_queue.size()));

        const double ENERGY_EPSILON = 1e-9;

        // ⭐ 能量耗尽检查
        if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
            SCHEDULER_LOG_INFO("💀 [ST-Sync] getTaskN: 能量已耗尽");
            return nullptr;
        }

        // ⭐⭐⭐ V112修复：深度充电时拒绝调度新任务 ⭐⭐⭐
        // 深度充电模式下，不应返回任何新任务
        // 直到能量恢复或Slack归零
        if (_deep_charging) {
            SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync V112] getTaskN: 深度充电中，拒绝调度"));
            return nullptr;
        }

        // ⭐ n==0时重置计数器
        // 注意：虽然kernel会多次调用getTaskN(0)，但必须在���里清除
        // 否则_counted_tasks_in_dispatch会累积导致任务无法被选择
        if (n == 0) {
            _counted_tasks_in_dispatch.clear();

            // ⭐ V124修复：如果紧急调度已设置批量任务，不清空它
            if (!_current_batch_tasks.empty()) {
                SCHEDULER_LOG_INFO(std::string("V124: Emergency batch set, size=") +
                                  std::to_string(_current_batch_tasks.size()));
                // 直接返回紧急批量中的任务
                if (n < _current_batch_tasks.size()) {
                    AbsRTTask* task = _current_batch_tasks[n];
                    if (task) {
                        _counted_tasks_in_dispatch.insert(task);
                        return task;
                    }
                }
                return nullptr;
            }

            // ⭐ V107修复：清除旧的批量状态
            // 当任务完成触发新的dispatch时，需要重新构建批量
            // 否则会使用过时的批量列表，导致已完成的任务被重复返回
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            // _v108_batch_energy_checked = false;  // ⭐ V108: 不再每次重置，改用时间戳检查

            // ⭐⭐⭐ V108修复：ST-Sync "全有或全无"批量能量检查 ⭐⭐⭐
            // 在返回任何任���之前，先检查是否有足够能量调度K个任务
            // 如果能量不足，不返回任何任务（全无）- 这才是SYNC的正确行为
            // 使用缓存机制避免重复计算
            // ⭐ V108时间戳修复：只在时间推进时重新检查
            size_t current_ready_size_v108 = _ready_queue.size();
            MetaSim::Tick current_time_v108 = SIMUL.getTime();
            bool time_changed_v108 = (current_time_v108 != _last_v108_check_time);
            bool queue_changed_v108 = (current_ready_size_v108 != _v108_last_ready_queue_size);
            bool need_check_v108 = (!_v108_batch_energy_checked) ||
                                   time_changed_v108 ||
                                   queue_changed_v108;
            _last_v108_check_time = current_time_v108;
            _v108_last_ready_queue_size = current_ready_size_v108;

            // ⭐ V124修复：如果紧急调度已设置批量任务，跳过V108检查
            bool emergency_batch_set = !_current_batch_tasks.empty();

            if (need_check_v108 && !_ready_queue.empty() && !_energy_depleted && !emergency_batch_set) {
                // ⭐ V108: 保存能量快照，用于后续比较
                // 只在首次检查时保存快照，后续检查使用已保存的快照
                // 这样即使getTaskN()扣减了能量，批量检查仍然使用原始值
                if (!_v108_batch_energy_checked) {
                    _v108_batch_start_energy = _current_energy;
                }

                // 获取核心数
                ConfigManager &configMgr_v108 = ConfigManager::getInstance();
                int total_cpus_v108 = configMgr_v108.getNumCores();

                // 获取运行中任务数
                int running_cnt_v108 = 0;
                if (_kernel) {
                    const auto& running_v108 = _kernel->getCurrentExecutingTasks();
                    for (const auto& pair : running_v108) {
                        if (pair.second && pair.second->isExecuting()) {
                            running_cnt_v108++;
                        }
                    }
                }

                // 计算K = min(就绪任务数, 空闲核心数)
                int ready_cnt_v108 = static_cast<int>(_ready_queue.size());
                int free_cpus_v108 = total_cpus_v108 - running_cnt_v108;
                int K_v108 = std::min(ready_cnt_v108, free_cpus_v108);

                if (K_v108 > 0) {
                    // 按RM优先级排序就绪队列
                    std::vector<AbsRTTask*> sorted_ready_v108(_ready_queue.begin(), _ready_queue.end());
                    std::sort(sorted_ready_v108.begin(), sorted_ready_v108.end(),
                        [this](AbsRTTask* a, AbsRTTask* b) {
                            auto model_a = getTaskModel(a);
                            auto model_b = getTaskModel(b);
                            if (model_a && model_b) {
                                return model_a->getRMPriority() < model_b->getRMPriority();
                            }
                            return false;
                        });

                    // ⭐⭐⭐ V113修复：计算K个任务的"总能量预算"而非"单步能量" ⭐⭐⭐
                    // ST算法核心原则：只有当电量足以支撑任务组"一口气跑完全程"时才调度
                    // - 新任务：完整WCET × 单位能耗
                    double batch_energy_v108 = 0.0;
                    for (int i = 0; i < K_v108 && i < static_cast<int>(sorted_ready_v108.size()); ++i) {
                        AbsRTTask* task = sorted_ready_v108[i];
                        double unit_energy = calculateUnitEnergyForTask(task);
                        STSyncTaskModel *model = getTaskModel(task);
                        int64_t wcet_ms = model ? model->getWCET() : 10;  // 默认10ms
                        if (wcet_ms < 1) wcet_ms = 1;
                        batch_energy_v108 += unit_energy * wcet_ms;  // 总能量预算 = 单位能耗 × WCET
                    }

                    SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync V113] 批量能量检查(总预算): K=") +
                                      std::to_string(K_v108) +
                                      " 需要=" + std::to_string(batch_energy_v108 * 1000) + " mJ" +
                                      " 快照=" + std::to_string(_v108_batch_start_energy * 1000) + " mJ" +
                                      " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                                      " 已扣除=" + std::to_string(_v108_batch_total_energy * 1000) + " mJ");

                    // 缓存检查结果 - 使用快照能量，不受getTaskN()扣减影响
                    _v108_batch_energy_sufficient = (_v108_batch_start_energy >= batch_energy_v108 - ENERGY_EPSILON);
                    _v108_batch_energy_checked = true;

                    if (!_v108_batch_energy_sufficient) {
                        SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync V114] 批量能量不足���全部不调度: ") +
                                             "需要=" + std::to_string(batch_energy_v108 * 1000) + " mJ" +
                                             " 快照=" + std::to_string(_v108_batch_start_energy * 1000) + " mJ");
                        _energy_depleted = true;
                        return nullptr;
                    }

                    // ⭐⭐⭐ V114修复：移除双重扣费 ⭐⭐⭐
                    // getTaskN()只做检查，不扣费！
                    // 能量只在Tick级（V111）扣除，避免"进餐厅点菜先收一顿饭钱，吃的时候再收一次"的问题
                    SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync V114] 批量能量检查通过（只检查不扣费）: ") +
                                      "需要=" + std::to_string(batch_energy_v108 * 1000) + " mJ" +
                                      " 快照=" + std::to_string(_v108_batch_start_energy * 1000) + " mJ" +
                                      " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
                    _v108_batch_k_approved = K_v108;
                }
            } else if (_v108_batch_energy_checked && !_v108_batch_energy_sufficient) {
                // 已经检查过且能量不足，直接返回nullptr
                SCHEDULER_LOG_INFO("⚠️ [ST-Sync V108] 使用缓存的能量检查结果：能量不足");
                return nullptr;
            }
        }
        // ⭐⭐⭐ V92修复：优先使用_current_batch_tasks ⭐⭐⭐
        // 当V90批量调度成功时，_current_batch_tasks包含正确排序的任务列表
        // getTaskN应该直接使用这个列表，而不是重新排序_ready_queue
        if (_batch_scheduled_this_tick && !_current_batch_tasks.empty()) {
            // 按RM优先级排序批量任务（确保顺序正确）
            std::vector<AbsRTTask*> sorted_batch = _current_batch_tasks;
            std::sort(sorted_batch.begin(), sorted_batch.end(),
                [this](AbsRTTask* a, AbsRTTask* b) {
                    auto model_a = getTaskModel(a);
                    auto model_b = getTaskModel(b);
                    if (model_a && model_b) {
                        return model_a->getRMPriority() < model_b->getRMPriority();
                    }
                    return false;
                });

            SCHEDULER_LOG_INFO(std::string("📦 [ST-Sync V92] getTaskN使用批量任务列表: ") +
                              "n=" + std::to_string(n) +
                              " 批量大小=" + std::to_string(sorted_batch.size()));

            // 返回第n个任务（如果存在）
            if (n < sorted_batch.size()) {
                AbsRTTask* task = sorted_batch[n];

                // 检查任务是否有效
                if (task && task->isActive()) {
                    // 检查是否已在运行
                    bool is_running = false;
                    if (_kernel) {
                        CPU *proc = _kernel->getProcessor(task);
                        is_running = (proc != nullptr);
                    }

                    if (!is_running) {
                        // 检查是否已在本轮dispatch中处理过
                        if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                            // ⭐ V122修复：删除能量扣除，由EnergyCheckEvent负责
                            _counted_tasks_in_dispatch.insert(task);

                            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync V92] getTaskN(") + std::to_string(n) +
                                              ") 从批量返回: " + getTaskName(task));
                            return task;
                        }
                    }
                }
            }

            // 批量中没有足够的任务，返回nullptr
            SCHEDULER_LOG_INFO(std::string("📭 [ST-Sync V92] getTaskN(") + std::to_string(n) +
                              std::string(") 批量中没有更多任务"));
            return nullptr;
        }

        // ⭐ V52核心：实时遍历ready_queue，按RM优先级找Slack<=0且能量足够的任务
        if (_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("📭 [ST-Sync] getTaskN: 就绪队列为空");
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

        // ⭐ V92调试：输出排序后的任务列表
        SCHEDULER_LOG_INFO(std::string("📋 [ST-Sync V92] 排序后的就绪队列 (n=") + std::to_string(n) + "):");
        for (size_t i = 0; i < sorted_ready.size() && i < 5; ++i) {
            auto model = getTaskModel(sorted_ready[i]);
            Tick rm_priority = model ? model->getRMPriority() : Tick(0);
            SCHEDULER_LOG_INFO(std::string("  [") + std::to_string(i) + "] " +
                               getTaskName(sorted_ready[i]) +
                               " RM优先级=" + std::to_string(static_cast<int>(rm_priority)));
        }

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

            // ⭐ 关键修复：检查是否已在本轮dispatch中处理过
            // 如果任务已被处理，仍然需要递增found_count以保持索引一致性
            // 这样getTaskN(n)才能正确返回第n+1个任务
            if (_counted_tasks_in_dispatch.find(task) != _counted_tasks_in_dispatch.end()) {
                found_count++;  // 递增计数以保持索引一致
                continue;
            }

            // ⭐ V88修复：ST算法在能量充足时移除Slack门控
            // getTaskN只负责选择任务，不检查Slack
            // Slack检查在performTickScheduling的All-or-Nothing逻辑中进行
            // 这样能量充足时可以像ASAP一样立即调度所有任务

            // ⭐ 检查能量是否足够
            double unit_energy = calculateUnitEnergyForTask(task);
            if (_current_energy < unit_energy - ENERGY_EPSILON) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 能量不足: ") + getTaskName(task) +
                                     " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                                     " 剩余=" + std::to_string(_current_energy * 1000) + " mJ");
                found_count++;  // 递增计数以保持索引一致
                continue;
            }

            // 找到第n个有效任务
            if (found_count == n) {
                // ⭐ V122修复：删除能量扣除，由EnergyCheckEvent负责
                _counted_tasks_in_dispatch.insert(task);

                SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] getTaskN(") + std::to_string(n) + ") 返回: " +
                                  getTaskName(task));
                return task;
            }

            found_count++;
        }

        // 没找到足够的任务
        SCHEDULER_LOG_INFO(std::string("📭 [ST-Sync] getTaskN(") + std::to_string(n) +
                           ") 没有更多可调度任务，found_count=" + std::to_string(found_count));
        return nullptr;
    }

    // =====================================================
    // notify - ST-Sync不再扣减能量（已在批量时扣减）
    // =====================================================

    void STSyncScheduler::notify(AbsRTTask *task) {
        if (!task) {
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

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否充足
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
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

        // ⭐ Bug修复：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [ST-Sync] 能量已耗尽，拒绝添加新任务: ") +
                                         getTaskName(task));
            return;  // 拒绝任务
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
        auto it = _tasks_completed_wcet.find(task);
        if (it != _tasks_completed_wcet.end()) {
            _tasks_completed_wcet.erase(it);
            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync] 清除任务的WCET完成标志: ") +
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

    void STSyncScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ST-Sync] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void STSyncScheduler::checkAndPreemptOnAllCPUs() {
        // ⭐ 优化：如果有抢占批量任务，说明mid-tick抢占已经处理，跳过tick边界抢占检查
        if (!_preempt_batch_tasks.empty()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ST-Sync] checkAndPreemptOnAllCPUs: 跳过检查，抢占批量size=") +
                               std::to_string(_preempt_batch_tasks.size()));
            return;
        }

        // ⭐ 修复：不使用_running_tasks（它从未被正确填充）
        // 直接从kernel获取实际运行中的任务
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_INFO("❌ [ST-Sync] checkAndPreemptOnAllCPUs: _kernel为null，无法检查抢占");
                return;
            }
        }

        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] checkAndPreemptOnAllCPUs: 运行中任务数量=") +
                          std::to_string(running_tasks.size()) +
                          " _current_batch_tasks.size()=" + std::to_string(_current_batch_tasks.size()));

        if (running_tasks.empty()) {
            SCHEDULER_LOG_INFO("❌ [ST-Sync] checkAndPreemptOnAllCPUs: 没有运行中的任务");
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
                    SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] checkAndPreempt: 从batch选择Slack<=0任务: ") +
                                      getTaskName(task) +
                                      " Slack=" + std::to_string(static_cast<int64_t>(task_slack)) + "ms");
                    break;  // 找到了，退出循环
                } else {
                    SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] checkAndPreempt: 跳过batch任务Slack>0: ") +
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
            SCHEDULER_LOG_INFO("❌ [ST-Sync] checkAndPreemptOnAllCPUs: 没有候选任务进行抢占");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] checkAndPreemptOnAllCPUs: 最高优先级任务=") +
                          getTaskName(highest) +
                          " (from_" + (from_ready_queue ? "ready_queue" : "batch") + ")");

        // ⭐ Bug修复：如果最高优先级任务来自就绪队列，且_current_batch_tasks为空，
        // 说明可能是在suspend()后重新插入的任务。为了避免"假抢占"循���，
        // 检查该任务是否已经在运行中。如果是，则跳过抢占。
        if (from_ready_queue && _current_batch_tasks.empty()) {
            for (const auto& [cpu, running_task] : running_tasks) {
                if (running_task == highest) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [ST-Sync] 跳过假抢占: 最高优先级任务正在运行中=") +
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

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] CPU状态: 总数=") +
                          std::to_string(total_cpus) +
                          " 空闲=" + std::to_string(truly_free_cpus) +
                          " 执行中=" + std::to_string(busy_executing) +
                          " 上下文切换中=" + std::to_string(busy_dispatching));

        // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
        // 新任务会被dispatch到空闲CPU，不需要抢占正在运行的任务
        // ⭐ V59修复：有空闲CPU时，需要将highest任务加入批量并触发dispatch
        if (truly_free_cpus > 0) {
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 有空闲CPU，无需抢占，直接调度新任务: ") +
                              getTaskName(highest));
            // ⭐ V59：将任务加入批量并触发调度
            if (highest) {
                // 检查任务是否已在批量中
                auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), highest);
                if (batch_it == _current_batch_tasks.end()) {
                    _current_batch_tasks.push_back(highest);
                    SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] 将任务加入批量: ") + getTaskName(highest));
                }
                // 触发dispatch
                // _kernel->dispatch(); // V105: 禁用，避免频繁deschedule
            }
            return;
        }

        // ⭐ 没有空闲CPU，需要抢占最低优先级的运行任务
        SCHEDULER_LOG_INFO("⚠️ [ST-Sync] CPU已满，需要抢占最低优先级任务");

        // 找到优先级最低的运行任务
        AbsRTTask *lowest_priority_task = nullptr;
        int lowest_priority = -1;

        for (const auto& [cpu, running_task] : running_tasks) {
            if (!running_task) {
                continue;
            }

            STSyncTaskModel *model = getTaskModel(running_task);
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
            SCHEDULER_LOG_INFO("❌ [ST-Sync] 未找到可抢占的任务");
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
            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Sync] 抢占CPU: ") +
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
                SCHEDULER_LOG_DEBUG(std::string("🔄 [ST-Sync] 从批量任务移除: ") + getTaskName(lowest_priority_task));
            }

            // 3. 将高优先级任务加入批量任务（放在最前面）
            _current_batch_tasks.insert(_current_batch_tasks.begin(), highest);

            // ⭐ 修复：不在tick边界抢占时扣除能量，避免双重扣除
            // 能量将在批量调度中统一扣除
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Tick边界抢占: 任务加入批量（能量将在批量调度中扣除）: ") +
                              getTaskName(highest));

            // 5. 挂起低优先级任务
            _kernel->suspend(lowest_priority_task);

            // 6. 重新调度所有CPU
            // _kernel->dispatch(); // V105: 禁用，避免频繁deschedule
        } else {
            SCHEDULER_LOG_INFO(std::string("❌ [ST-Sync] 新任务优先级不够高，无需抢占"));
        }
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

        // ⭐ ST-Sync：任务到达时直接加入等待队列
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

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] Mid-tick抢占检查: _kernel=") +
                          (_kernel ? "valid" : "null") +
                          " _energy_depleted=" + (_energy_depleted ? "true" : "false") +
                          " is_reinserted=" + (is_reinserted ? "true" : "false") +
                          " isExecuting()=" + (task->isExecuting() ? "true" : "false"));

        // ⭐ V97修复：暂时禁用mid-tick抢占，因为它导致频繁调度循环
        // 问题：任务被descheduled后重新插入，触发mid-tick抢占，然后又dispatch，循环...
        // 解决：只在任务到达时（不是重新插入）且确实需要抢占时才触发
        // 暂时完全禁用mid-tick抢占，让tick边界的调度处理所有任务
        bool disable_mid_tick_preempt = true;  // V97：禁用mid-tick抢占

        if (_kernel && !_energy_depleted && !is_reinserted && !disable_mid_tick_preempt) {
            // ⭐ 关键修复：移除mid-tick抢占中的Slack检查
            // 原因：mid-tick抢占应该由RM优先级决定，而不是Slack
            // Slack过滤应该在批量调度的任务选择时进行（第808-815行）
            // 这样可以确保高优先级任务（如Task_Assassin_Hungry, period=50）能及时抢占低优先级任务
            //
            // 举例：Task_Assassin_Hungry (arrival=50) 在t=50时Slack=30ms>0，但不抢占会导致
            //       在t=80时Slack=0ms≤0时，已经没有mid-tick抢占机会，导致deadline miss

            const auto& running_tasks = _kernel->getCurrentExecutingTasks();

            SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] 运行中任务数量: ") +
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

            SCHEDULER_LOG_INFO(std::string("🔍 [ST-Sync] Mid-tick CPU状态: 总数=") +
                              std::to_string(total_cpus) +
                              " 空闲=" + std::to_string(truly_free_cpus) +
                              " 执行中=" + std::to_string(busy_executing) +
                              " 上下文切换中=" + std::to_string(busy_dispatching));

            // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
            if (truly_free_cpus > 0) {
                SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Mid-tick: 有空闲CPU，直接调度新任务: ") +
                                  getTaskName(task));
                // 检查能量（但不扣除，让下一个tick的批量调度统一扣除）
                double unit_energy = calculateUnitEnergyForTask(task);
                const double EPSILON = 1e-9;

                if (_current_energy >= unit_energy - EPSILON) {
                    // ⭐ 修复：不在mid-tick扣除能量，避免双重扣除
                    // 能量将在下一个tick的批量调度中统一扣除
                    SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Mid-tick: 能量充足，调度任务（能量将在下一tick扣除）: ") +
                                      getTaskName(task));

                    // 创建抢占微型批量
                    _preempt_batch_tasks.push_back(task);

                    // 立即调度到空闲CPU
                    // _kernel->dispatch(); // V105: 禁用，避免频繁deschedule
                }
                return;
            }

            // ⭐ CPU已满，需要找到优先级最低的任务进行抢占
            SCHEDULER_LOG_INFO("⚠️ [ST-Sync] Mid-tick: CPU已满，需要抢占最低优先级任务");

            AbsRTTask *lowest_priority_task = nullptr;
            int lowest_priority = -1;

            for (const auto& [cpu, running_task] : running_tasks) {
                if (!running_task) {
                    continue;
                }

                STSyncTaskModel *model = getTaskModel(running_task);
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
                SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Mid-tick: 找到可抢占任务: ") +
                                  getTaskName(lowest_priority_task));

                // 检查能量（但不扣除，让下一个tick的批量调度统一扣除）
                double unit_energy = calculateUnitEnergyForTask(task);
                const double EPSILON = 1e-9;

                if (_current_energy >= unit_energy - EPSILON) {
                    // 能量充足，执行mid-tick抢占
                    SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] Micro-Batch抢占: ") +
                                      getTaskName(lowest_priority_task) + " → " + getTaskName(task) +
                                      " [微型批量调度]");

                    // 创建抢占微型批量
                    _preempt_batch_tasks.push_back(task);

                    // ⭐ 修复：不在mid-tick扣除能量，避免双重扣除
                    // 能量将在下一个tick的批量调度中统一扣除
                    SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] Micro-Batch: 能量充足，执行抢占（能量将在下一tick扣除）: ") +
                                      getTaskName(task));

                    // 挂起低优先级任务
                    _kernel->suspend(lowest_priority_task);

                    // 立即调度高优先级任务
                    SCHEDULER_LOG_INFO(std::string("🚀 [ST-Sync] 对调后立即dispatch调度高优先级任务"));
                    // _kernel->dispatch(); // V105: 禁用，避免频繁deschedule

                    return;  // 抢占完成，退出
                }
            }
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

    void STSyncScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ST-Sync] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
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

        removeFromWaitingQueue(task);

        STSyncTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Sync] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            STSyncTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
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
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ST-Sync] 任务加入等待队列: ") + getTaskName(task));
    }

    void STSyncScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
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
        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end() && it->second) {
            executed = it->second->getMsExecuted();
        }

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

    // =====================================================
    // 运行时能量检查方法（V28.15新增）
    // =====================================================

    void STSyncScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ST-Sync] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        STSyncEnergyCheckEvent *evt = new STSyncEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        Tick current_time = SIMUL.getTime();
        Tick scheduled_time = current_time + 1;
        evt->post(scheduled_time);

        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString() +
                           " 当前时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms" +
                           " 调度时间=" + std::to_string(static_cast<int64_t>(scheduled_time)) + "ms");
    }

    void STSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 停止运行时能量检查: ") +
                               getTaskName(task));
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

        // ST-Sync批量调度状态初始化
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _current_batch_tasks.clear();

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
            SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
            SCHEDULER_LOG_INFO("🔄 [ST-Sync] 任务结束，触发立即调度");
            // ⭐ V106修复：重新启用dispatch，解决核心碎片化问题
            _kernel->dispatch();
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
                STSyncTaskModel *model = getTaskModel(task);
                if (!model) continue;

                Tick arrival = task->getArrival();
                Tick deadline = arrival + Tick(model->getPeriod());

                if (deadline <= current_time) {
                    to_suspend.push_back(task);
                    SCHEDULER_LOG_INFO("💀 [ST-Sync] 过期任务运行中，将挂起: " +
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
            STSyncTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick arrival = task->getArrival();
            Tick deadline = arrival + Tick(model->getPeriod());

            if (deadline <= current_time) {
                expired.push_back(task);
                SCHEDULER_LOG_INFO("🧹 [ST-Sync] 清理过期任务: " +
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
            STSyncTaskModel *model = getTaskModel(task);
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
                SCHEDULER_LOG_INFO("🧹 [ST-Sync] 从批量任务清理过期任务: " + getTaskName(task));
            }
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
        Tick original_arrival = task->getArrival();
        int period_int = task->getPeriod();
        Tick period = Tick(period_int > 0 ? period_int : 100);

        // ⭐ V118修复：计算当前周期实例的正确截止时间
        // 对于周期性任务，deadline = original_arrival + period * ceil((current_time - original_arrival) / period)
        int64_t periods_elapsed = 0;
        if (current_time > original_arrival) {
            periods_elapsed = (static_cast<int64_t>(current_time) - static_cast<int64_t>(original_arrival)) / static_cast<int64_t>(period);
        }
        Tick absolute_deadline = original_arrival + period * (periods_elapsed + 1);

        double remaining_double = task->getRemainingWCET();
        Tick remaining = Tick(remaining_double);
        Tick slack = absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_DEBUG("🧮 [ST-Sync] Slack计算: " +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " => slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        return slack;
    }

    // ⭐ ST特有：计算所有就绪任务的最小Slack
    MetaSim::Tick STSyncScheduler::calculateMinSlack() {
        // 注意: Tick是类，需要用impl_t获取最大值
        Tick min_slack = std::numeric_limits<Tick::impl_t>::max();

        // 检查就绪队列中所有任务的Slack
        for (auto* task : _ready_queue) {
            if (!task) continue;
            Tick slack = calculateSlackForTask(task);
            if (slack < min_slack) {
                min_slack = slack;
            }
        }

        // 如果没有就绪任务，返回0
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

        // 计算充满电需要的时间
        double energy_needed = group_energy - _current_energy;
        if (energy_needed < 0) energy_needed = 0;

        double harvest_rate = 0.003;  // mJ/ms (默认值，实际应从配置获取)
        int64_t charge_time_ms = static_cast<int64_t>(energy_needed * 1000 / harvest_rate) + 1;

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
        // V122修复：每次创建新事件，避免重复post问题
        if (_group_wake_event) {
            // 使旧事件失效
            _group_wake_event->invalidate();
        }
        // 创建新事件
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
                SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 运行任务续期能量充足: ") +
                                   "需要=" + std::to_string(total_energy_to_deduct * 1000) + " mJ " +
                                   "当前=" + std::to_string(_current_energy * 1000) + " mJ " +
                                   "(能量已在批量调度中扣除)");
            } else {
                // ❌ 能量不足，中断所有运行中的任务
                SCHEDULER_LOG_WARNING(std::string("❌ [ST-Sync] 运行任务续期能量不足，将中断所有运行任务: ") +
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

                SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，将中断") +
                                   std::to_string(tasks_to_interrupt.size()) + "个运行任务");
            }
        }

        // 2. 检查所有运行中的任务（细粒度监控）
        // ⭐ Bug #9修复v2：如果当前tick有任务在运行，不中断它们
        // ST-Sync的核心原则：要么全不调度要么全部调度
        // - 如果有任务在运行：让它们继续运行到下一个tick
        // - 如果没有任务在运行：检查能量是否足够调度新任务
        bool has_running_tasks = !running_tasks.empty();
        if (has_running_tasks) {
            SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 当前tick有") +
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
                    SCHEDULER_LOG_WARNING(std::string("⚡ [ST-Sync] 任务能量不足，将中断: ") +
                                         getTaskName(task) +
                                         " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                         " 当前能量=" + std::to_string(_current_energy) + "J");

                    tasks_to_interrupt.push_back(task);
                    _stats.total_skipped_energy++;
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 任务能量充足: ") +
                                       getTaskName(task) +
                                       " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                       " 当前能量=" + std::to_string(_current_energy) + "J");
                }
            }
        }

        // 2. ⭐ ST-Sync"全无"原则：能量不足时，不调度任何新任务
        // 注意：当前正在运行的任务会继续执行，但由于：
        //   - _energy_depleted = true
        //   - _current_batch_tasks已清空（在批量调度的else分支中）
        //   - getTaskN()会返回nullptr
        // 所以不会有任何新任务被调度，当前任务完成后就会停止
        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，") +
                               std::to_string(tasks_to_interrupt.size()) + "个任务将自然完成" +
                               "（不再调度新任务，遵循ST-Sync'全无'原则）");
        }
    }
} // namespace RTSim
