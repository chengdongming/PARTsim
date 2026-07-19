// gpfp_asap_block_scheduler.cpp - ALAP-Block (Tick-based Instant Energy-aware) Scheduler Implementation
// 算法特点：
// 1. 基于当前实际能量进行即时判断（无前瞻性预测）
// 2. 每ms逐次扣减能耗
// 3. 级联调度：能量不足立即停止
// 4. Tick级抢占
// 5. Tick末尾收集能量

#include <algorithm>
#include <cmath>
#include <cassert>
#include <stdexcept>
#include <fstream>
#include <iostream>
#include <memory>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_alap_block_scheduler.hpp>
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
    // ALAPBlockTickEvent 实现
    // =====================================================

    ALAPBlockTickEvent::ALAPBlockTickEvent(ALAPBlockScheduler *scheduler)
        : MetaSim::Event("ALAPBlockTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行，这样所有任务都在ready queue中
    }
    // =====================================================
    // ALAPWakeEvent 实现
    // =====================================================
    ALAPWakeEvent::ALAPWakeEvent(ALAPBlockScheduler *scheduler)
        : MetaSim::Event("ALAPWakeEvent", MetaSim::Event::_DEFAULT_PRIORITY - 1),
          _scheduler(scheduler) {
    }

    void ALAPWakeEvent::doit() {
        if (!_scheduler) return;

        MRTKernel* kernel = _scheduler->getKernel();
        if (kernel) {
            SCHEDULER_LOG_INFO("⏰ [ALAP-Block] 闹钟响起！Slack归零，触发内核抢占调度！");
            kernel->dispatch();
        }
    }

    // =====================================================
    // ⭐ EnergyDepletedEvent 实现（Bug修复：防止虚空借电）
    // =====================================================

    ALAPBlockEnergyDepletedEvent::ALAPBlockEnergyDepletedEvent(ALAPBlockScheduler *scheduler)
        : MetaSim::Event("ALAPBlockEnergyDepletedEvent", MetaSim::Event::_DEFAULT_PRIORITY - 100),
          _scheduler(scheduler),
          _scheduled_depletion_time(0),
          _energy_at_prediction(0.0) {
        // ⭐ 最高优先级（负数确保在其他事件之前处理）
    }

    void ALAPBlockEnergyDepletedEvent::doit() {
        if (!_scheduler) return;
        _scheduler->onEnergyDepleted();
    }

    void ALAPBlockTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ALAP-Block] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ALAP-BlockEnergyCheckEvent 实现 - 运行时能量检查
    // ⭐ V40重构：能量检查事件已删除，能量由performTickScheduling处理
    // =====================================================

    /*
    ALAP-BlockEnergyCheckEvent::ALAP-BlockEnergyCheckEvent(ALAPBlockScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("ALAP-BlockEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // ⭐ V29修复：较低优先级，确保tick先执行
    }

    void ALAP-BlockEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // ⭐ 调试日志：记录事件触发时间
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 能量检查事件触发: ") +
                           "任务=" + task_name +
                           " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Block] 能量检查：任务已删除，停止检查"));
            // 不重新调度事件，让它自然结束
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Block] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Block] 能量检查：任务已停止执行，��再扣除能量: ") +
                               task_name + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        ALAPBlockTaskModel *task_model = _scheduler->getTaskModel(_task);

        // 🔍 调试日志：检查WCET
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Block] WCET检查: ") +
                           task_name + " 已执行=" + std::to_string(_ms_executed) +
                           "ms task_model=" + (task_model ? "有效" : "NULL"));

        if (task_model) {
            int wcet = task_model->getWCET();
            SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Block] WCET值: ") +
                               std::to_string(wcet) + "ms 判断: " +
                               std::to_string(_ms_executed) + " >= " + std::to_string(wcet) +
                               " = " + (_ms_executed >= wcet ? "TRUE" : "FALSE"));

            if (_ms_executed >= wcet) {
                SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Block] 任务已达到WCET，完成执行: ") +
                                   task_name + " 已执行=" + std::to_string(_ms_executed) +
                                   "ms WCET=" + std::to_string(wcet) + "ms");
                // ⭐ 关键修复：从_energy_check_events中移除，允许后续实例启动新的能量检查
                _scheduler->_energy_check_events.erase(_task);
                // 任务已完成，不续期能量，也不重新调度事件
                // 任务会由正常的调度流程完成
                return;
            }
        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Block] WCET检查失败：找不到TaskModel ") + task_name);
        }

        // ⭐ V29.1修复：恢复运行中任务的续期能量扣除
        // 设计原则：
        // - getTaskN(): 负责新任务的首次能量扣除
        // - ALAP-BlockEnergyCheckEvent: 负责运行中任务的续期能量扣除
        //
        // 检查是否有足够能量续期1ms
        // ⭐ V33修复：当能量不足以支撑下一个1ms时立即中断
        // ⭐ V34修复：当能量 <= 1ms能耗时，立即中断任务
        // 这样可以避免任务在执行中途能量耗尽（因为执行后就没有能量了）
        // 🔍 调试：打印实际的比较值
        bool energy_insufficient = current_energy <= unit_energy + EPSILON;
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Block] 能量检查: current=") +
                           std::to_string(current_energy * 1000) + " mJ" +
                           " unit=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " check=" + (energy_insufficient ? "TRUE (suspend)" : "FALSE (continue)"));

        // ⭐ 修复能量检查条件：当能量 <= 单位能耗时立即中断
        // 避免在能量恰好等于单位能耗时继续执行，导致下个Tick能量为负
        if (current_energy <= unit_energy + EPSILON) {
            // ⭐ 能量不足以支撑下一个1ms，立即中断任务（不扣除能量）
            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 能量刚好耗尽或不足，立即中断任务: ") +
                               task_name + " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                               " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                               " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;

            // ⭐ V37关键修复：将剩余能量强制设为0
            // 当current_energy == unit_energy时（如0.6 mJ == 0.6 mJ），
            // 条件current_energy <= unit_energy为TRUE，任务被挂起但不扣除能量
            // 这导致剩余了unit_energy的能量，performTickScheduling的检查current_energy < 0.000001失败
            // 解决方案：���制将能量设为0，确保能量耗尽检查正确工作
            // ⭐ 能量守恒修复：严禁强制清零！保留真实残血电量

            // 中断当前任务（调用kernel的suspend机制）
            if (_cpu) {
                _scheduler->setSuspendReason(_task, "insufficient_energy");
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-Block] 任务因能量不足被挂起: ") + task_name);
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

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 运行中任务续期: ") +
                           task_name + " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " " + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }
    */  // ⭐ V40重构：ALAP-BlockEnergyCheckEvent已删除

    // =====================================================
    // ALAPBlockTaskModel 实现
    // =====================================================

    ALAPBlockTaskModel::ALAPBlockTaskModel(AbsRTTask *t, int period, int wcet,
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

    ALAPBlockTaskModel::~ALAPBlockTaskModel() {}

    Tick ALAPBlockTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ALAPBlockTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ALAPBlockTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ALAPBlockScheduler 实现
    // =====================================================

    ALAPBlockScheduler::ALAPBlockScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _dispatching_tasks_total_energy(0.0),
          _selection_tick(-1),
          _selection_generation(0),
          _selection_frozen(false),
          _energy_commit_tick(-1),
          _energy_commit_valid(false),
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
          _energy_depleted(false),
          _alap_blocking(false),
          _blocking_task(nullptr),
          _last_preempted_task(nullptr),
          _last_preempted_tick(0) {

        SCHEDULER_LOG_INFO("🚀 [ALAP-Block] ALAP-Block Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ALAP-Block] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ALAP-Block] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ALAP-Block] 开始时间偏移: ") +
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
                            // DEBUG: 显示所有energy section的行（注释行除外）
                            if (line.find("use_real_solar_data:") == std::string::npos &&
                                line.find("solar_data_file:") == std::string::npos &&
                                line.find("pv_efficiency:") == std::string::npos &&
                                line.find("pv_area_m2:") == std::string::npos &&
                                !line.empty()) {
                                SCHEDULER_LOG_DEBUG(std::string("📄 [ALAP-Block] YAML行: '") + line + "'");
                            }

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
                                SCHEDULER_LOG_INFO(std::string("📖 [ALAP-Block] 解析到solar_data_file: '") + value + "'");
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
                                SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Block] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Block] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²" +
                                      " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Block] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Block] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Block] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ALAP-Block] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ALAPBlockTickEvent(this);
        _alap_wake_event = new ALAPWakeEvent(this);
        _energy_depleted_event = new ALAPBlockEnergyDepletedEvent(this);
        SCHEDULER_LOG_INFO("✅ [ALAP-Block] ALAP-Block Scheduler 初始化完成");
    }

    ALAPBlockScheduler::ALAPBlockScheduler(const std::vector<std::string> &params)
        : ALAPBlockScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ALAPBlockScheduler>
        ALAPBlockScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ALAPBlockScheduler>(params);
    }

    ALAPBlockScheduler::~ALAPBlockScheduler() {
        if (_tick_event) {
            delete _tick_event;
            _tick_event = nullptr;
        }
        if (_alap_wake_event) {
            _alap_wake_event->drop();
            delete _alap_wake_event;
            _alap_wake_event = nullptr;
        }
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
    // 核心调度逻辑 - ALAP-Block算法的核心
    // =====================================================

    void ALAPBlockScheduler::performTickScheduling() {
        Tick current_time = SIMUL.getTime();
        if (_selection_frozen && _selection_tick == current_time) {
            SCHEDULER_LOG_DEBUG(
                std::string("🛡️ [ALAP-Block] 本tick选择已冻结，跳过重复决策 @ ") +
                std::to_string(static_cast<int64_t>(current_time)) + "ms");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Block] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms =====");
        _stats.total_tick_count++;
        _blocking_task = nullptr;
        _alap_blocking = false;

        Tick elapsed = current_time - _last_tick_time;
        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
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
            SCHEDULER_LOG_WARNING(
                "⚠️ [ALAP-Block] _kernel为nullptr，跳过本tick调度");
            return;
        }

        const auto &running_tasks_map =
            _kernel->getCurrentExecutingTasks();
        std::set<AbsRTTask *> running_tasks;
        for (const auto &[cpu, task] : running_tasks_map) {
            (void)cpu;
            if (task && task->isExecuting()) {
                running_tasks.insert(task);
            }
        }

        std::vector<AbsRTTask *> active_tasks =
            collectActiveJobs(current_time);
        std::vector<AbsRTTask *> candidates =
            collectALAPCandidates(active_tasks, current_time);
        sortByRMPriority(candidates);

        std::vector<AbsRTTask *> previous_selection =
            _dispatch_selection_order;
        std::vector<AbsRTTask *> selected_tasks;
        std::set<AbsRTTask *> newly_paid_pending_tasks;
        double tick_energy = 0.0;
        const double epsilon = 1e-9;
        const size_t processor_count = running_tasks_map.size();

        for (AbsRTTask *task : candidates) {
            if (selected_tasks.size() >= processor_count) {
                break;
            }

            const bool is_running =
                running_tasks.find(task) != running_tasks.end();
            const bool has_execution_credit =
                is_running &&
                _paid_execution_credit_tasks.find(task) !=
                    _paid_execution_credit_tasks.end();
            const bool is_prepaid_pending =
                !is_running &&
                _paid_pending_tasks.find(task) !=
                    _paid_pending_tasks.end();
            const double task_energy =
                (has_execution_credit || is_prepaid_pending)
                    ? 0.0
                    : getConfiguredUnitEnergyForTask(task);

            if (tick_energy + task_energy >
                _current_energy + epsilon) {
                _alap_blocking = true;
                _blocking_task = task;
                _stats.total_skipped_energy++;
                break;
            }

            selected_tasks.push_back(task);
            tick_energy += task_energy;
            if (!is_running && !is_prepaid_pending) {
                newly_paid_pending_tasks.insert(task);
            }
        }

        _selection_tick = current_time;
        _selection_generation++;
        _selection_frozen = true;
        _dispatch_selection_order = selected_tasks;
        _dispatching_tasks_total_energy = tick_energy;

        if (_trace_logger && _semantic_trace_enabled &&
            !active_tasks.empty()) {
            std::vector<AbsRTTask *> trace_active = active_tasks;
            sortByRMPriority(trace_active);
            std::vector<AbsRTTask *> continuing_tasks;
            for (AbsRTTask *task : selected_tasks) {
                if (running_tasks.count(task) > 0) {
                    continuing_tasks.push_back(task);
                }
            }
            _trace_logger->logB3ALAPDecision(
                "ALAP-Block",
                "BLOCK",
                _current_energy * 1000.0,
                processor_count,
                makeB3TraceJobs(trace_active, _task_models),
                makeB3TraceJobs(candidates, _task_models),
                makeB3TraceJobs(selected_tasks, _task_models),
                makeB3TraceJobs(continuing_tasks, _task_models),
                _alap_blocking ? "ALAP_BLOCK_ENERGY_STOP"
                               : "ALAP_NATIVE_GATE");
        }

        commitTickEnergy(current_time, tick_energy);

        for (AbsRTTask *task : selected_tasks) {
            if (running_tasks.find(task) != running_tasks.end()) {
                _paid_execution_credit_tasks.erase(task);
                _paid_pending_tasks.erase(task);
                _pending_payment_ticks.erase(task);
            }
        }
        for (AbsRTTask *task : newly_paid_pending_tasks) {
            _paid_pending_tasks.insert(task);
            _pending_payment_ticks[task] = current_time;
        }
        for (auto it = _paid_pending_tasks.begin();
             it != _paid_pending_tasks.end();) {
            if (std::find(selected_tasks.begin(),
                          selected_tasks.end(),
                          *it) == selected_tasks.end()) {
                _pending_payment_ticks.erase(*it);
                it = _paid_pending_tasks.erase(it);
            } else {
                ++it;
            }
        }

        cancelStaleDispatches(previous_selection);

        const std::set<AbsRTTask *> selected_set(
            selected_tasks.begin(), selected_tasks.end());
        for (AbsRTTask *task : running_tasks) {
            if (selected_set.find(task) != selected_set.end()) {
                continue;
            }
            setSuspendReason(
                task,
                _alap_blocking ? "insufficient_energy"
                               : "preemption");
            _kernel->suspend(task);
        }

        _energy_depleted =
            selected_tasks.empty() && !candidates.empty();

        if (!selected_tasks.empty()) {
            _kernel->dispatch();
        }

        SCHEDULER_LOG_INFO(
            std::string("📊 [ALAP-Block] Tick选择: active=") +
            std::to_string(active_tasks.size()) +
            " candidates=" +
            std::to_string(candidates.size()) +
            " selected=" +
            std::to_string(selected_tasks.size()) +
            " 扣减=" +
            std::to_string(tick_energy * 1000) + " mJ" +
            " 剩余=" +
            std::to_string(_current_energy * 1000) + " mJ");
    }


    void ALAPBlockScheduler::schedule() {
        // ALAP-Block依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-Block] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *ALAPBlockScheduler::getFirst() {
        return getTaskN(0);
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（级联调度）
    // =====================================================

    AbsRTTask *ALAPBlockScheduler::getTaskN(unsigned int n) {
        if (!_selection_frozen ||
            _selection_tick != SIMUL.getTime()) {
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
    // notify - 每ms逐次扣减能耗（ALAP-Block核心逻辑）
    // =====================================================

    void ALAPBlockScheduler::notify(AbsRTTask *task) {
        Scheduler::notify(task);

        if (!task) {
            return;
        }

        auto payment_it = _pending_payment_ticks.find(task);
        if (payment_it != _pending_payment_ticks.end()) {
            if (SIMUL.getTime() > payment_it->second) {
                _paid_execution_credit_tasks.insert(task);
            }
            _pending_payment_ticks.erase(payment_it);
            _paid_pending_tasks.erase(task);
        }

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ALAPBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-Block] 添加任务: ") + getTaskName(task));
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
        ALAPBlockTaskModel *model = new ALAPBlockTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Block] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ALAPBlockScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-Block] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearBlockingStateIfOwner(task, "removeTask");
        clearTaskTickSelection(task);
        clearPersistentTaskState(task);

        // ⭐ Bug修复：不再使用_running_tasks，内核管理任务状态
        // for (auto &map_pair : _running_tasks) {
        //     if (map_pair.second == task) {
        //         _running_tasks[map_pair.first] = nullptr;
        //     }
        // }

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Block] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ALAPBlockScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ALAP-Block] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
            // ⭐ V44修复：移除任务到达时的抢占检查
            // 原因：
            // 1. 任务到达时立即抢占会导致刚调度的任务在下一个tick就被抢占
            // 2. 违背ALAP的"尽可能晚调度"原则
            // 3. 抢占检查应该在tick边界统一进行，而不是每次任务到达都检查
            // checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void ALAPBlockScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ALAP-Block] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void ALAPBlockScheduler::checkAndPreemptOnAllCPUs() {
        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) return;
        }

        const auto& running_tasks_map = _kernel->getCurrentExecutingTasks();

        // ⭐ V45关键修复：准确计算真正空闲的CPU数量
        // 空闲CPU的定义：_m_currExe[cpu] == nullptr 且 没有任务正在dispatch到这个CPU
        // 注意：上下文切换中的CPU（有任务dispatch但还没执行）不应该被认为是空闲的
        //       但也不应该被抢占
        int truly_free_cpus = 0;   // 真正空闲（可以调度新任务）
        int busy_executing = 0;     // 正在执行任务（可以被抢占）
        int busy_dispatching = 0;   // 上下文切换中（不应该被抢占）

        for (const auto& [cpu, task] : running_tasks_map) {
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
                // 任务存在但没有在执行，可能是上下文切换中
                busy_dispatching++;
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Block] CPU状态: 空闲=") +
                          std::to_string(truly_free_cpus) +
                          " 执行中=" + std::to_string(busy_executing) +
                          " 上下文切换中=" + std::to_string(busy_dispatching));

        // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
        // 新任务会被dispatch到空闲CPU，不需要抢占正在运行的任务
        if (truly_free_cpus > 0) {
            SCHEDULER_LOG_INFO("⏭️ [ALAP-Block] 有" + std::to_string(truly_free_cpus) + "个空闲CPU，跳过抢占");
            return;
        }

        // ⭐ 抢占防抖：检查最近被挂起的任务是否应该被重新调度
        // 如果同一个任务在同一个tick内被连续抢占，跳过本次抢占
        // 这防止了"挂起→调度→挂起"的恶性循环
        Tick current_time = SIMUL.getTime();
        if (_last_preempted_task && _last_preempted_tick == current_time) {
            // 检查是否有更高优先级的候选任务
            bool has_higher_priority = false;
            for (AbsRTTask *candidate : _ready_queue) {
                if (!candidate) continue;
                ALAPBlockTaskModel *model = getTaskModel(candidate);
                if (!model) continue;
                // 如果有任务的优先级高于被挂起的任务，才允许抢占
                ALAPBlockTaskModel *preempted_model = getTaskModel(_last_preempted_task);
                if (preempted_model && model->getRMPriority() < preempted_model->getRMPriority()) {
                    has_higher_priority = true;
                    break;
                }
            }
            if (!has_higher_priority) {
                SCHEDULER_LOG_DEBUG("⏸️ [ALAP-Block] 抢占防抖：跳过同tick连续抢占 " + getTaskName(_last_preempted_task));
                return;
            }
        }

        // 找就绪队列中Slack≤0且优先级最高的候选任务（不在CPU上运行的）
        AbsRTTask *best_candidate = nullptr;
        ALAPBlockTaskModel *best_model = nullptr;
        Tick best_slack = 0;

        for (AbsRTTask *candidate : _ready_queue) {
            if (!candidate) continue;
            CPU *cand_cpu = _kernel->getProcessor(candidate);
            if (cand_cpu != nullptr) continue;  // 已在运行

            ALAPBlockTaskModel *model = getTaskModel(candidate);
            if (!model) continue;

            Tick candidate_slack = calculateSlackForTask(candidate);

            // ⭐ ALAP核心修复：抢占候选任务必须满足 Slack <= 0
            // 如果 Slack > 0，说明任务还没到执行时间，不能作为抢占候选
            // 这防止了"高优任务到达但Slack>0时盲目抢占低优任务"导致的1ms抖动
            if (candidate_slack > 0) {
                continue;  // 跳过 Slack > 0 的候选任务
            }

            if (!best_candidate || model->getRMPriority() < best_model->getRMPriority()) {
                best_candidate = candidate;
                best_model = model;
                best_slack = candidate_slack;
            }
        }

        if (!best_candidate) return;

        // 找运行中优先级最低的任务
        // ⭐ V67修复：确定性 victim 选择
        // 当多个运行任务 RM 优先级相同时，需要稳定 tie-break：
        //   1. RM 优先级数值越大越差（周期越长优先级越低）
        //   2. 同优先级时，Slack 越大越不紧急，优先被踢
        //   3. Slack 也相同时，deadline 越晚越不紧急，优先被踢
        AbsRTTask *worst_running = nullptr;
        ALAPBlockTaskModel *worst_model = nullptr;
        Tick worst_running_slack = 0;
        Tick worst_running_deadline = 0;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            ALAPBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick task_slack = calculateSlackForTask(task);
            Tick task_deadline = task->getDeadline();

            if (!worst_running) {
                worst_running = task;
                worst_model = model;
                worst_running_slack = task_slack;
                worst_running_deadline = task_deadline;
                continue;
            }

            // 比较：谁更"差"谁被踢
            Tick cur_pri = model->getRMPriority();
            Tick worst_pri = worst_model->getRMPriority();

            if (cur_pri > worst_pri) {
                // 当前任务优先级更低，替换
                worst_running = task;
                worst_model = model;
                worst_running_slack = task_slack;
                worst_running_deadline = task_deadline;
            } else if (cur_pri == worst_pri) {
                // 同优先级：Slack 更大 = 更不紧急 = 更该被踢
                if (task_slack > worst_running_slack) {
                    worst_running = task;
                    worst_model = model;
                    worst_running_slack = task_slack;
                    worst_running_deadline = task_deadline;
                } else if (task_slack == worst_running_slack) {
                    // Slack 也相同：deadline 更晚 = 更不紧急
                    if (task_deadline > worst_running_deadline) {
                        worst_running = task;
                        worst_model = model;
                        worst_running_slack = task_slack;
                        worst_running_deadline = task_deadline;
                    } else if (task_deadline == worst_running_deadline) {
                        // 最终稳定 tie-break：名称字典序更大的任务视为更差
                        // 这样在完全并列的情况下也不会退回到 map 遍历顺序
                        if (getTaskName(task) > getTaskName(worst_running)) {
                            worst_running = task;
                            worst_model = model;
                            worst_running_slack = task_slack;
                            worst_running_deadline = task_deadline;
                        }
                    }
                }
            }
        }

        if (!worst_running || !worst_model) return;

        // ⭐ V46关键修复：改进抢占条件，支持Slack=0任务的紧急调度
        //
        // 原有问题：
        // - 旧逻辑只有当候选任务优先级更高时才抢占
        // - 但当Slack=0的任务优先级低于运行任务时，无法被调度
        // - 这违背了"任务必须在Slack=0的精准时刻被唤醒"的原则
        //
        // 修复策略：
        // 1. 候选任务优先级更高：正常抢占（RM原则）
        // 2. 候选任务Slack=0（紧急）且被抢占任务Slack>0（不紧急）：
        //    允许抢占，即使候选任务优先级更低
        //    这确保紧急任务能够及时执行
        //
        // 注意：需要确保被抢占的任务仍有足够Slack完成执行

        Tick worst_slack = calculateSlackForTask(worst_running);
        bool preempt_by_priority = best_model->getRMPriority() < worst_model->getRMPriority();
        bool preempt_by_urgency = (best_slack <= 0) && (worst_slack > 0);

        if (preempt_by_priority || preempt_by_urgency) {
            double unit_energy = calculateUnitEnergyForTask(best_candidate);
            if (_current_energy < unit_energy) return;

            std::string reason = preempt_by_priority ? "优先级抢占" : "紧急抢占(Slack=0)";
            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Block] ALAP抢占(") + reason + "): " +
                              " 挂起=" + getTaskName(worst_running) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(worst_model->getRMPriority())) +
                              " Slack=" + std::to_string(static_cast<int64_t>(worst_slack)) + ")" +
                              " 调度=" + getTaskName(best_candidate) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(best_model->getRMPriority())) +
                              " Slack=" + std::to_string(static_cast<int64_t>(best_slack)) + ")");

            // ⭐ 记录最近被挂起的任务，用于防抖
            _last_preempted_task = worst_running;
            _last_preempted_tick = current_time;

            setSuspendReason(worst_running, "preemption");
            _kernel->suspend(worst_running);
        }
    }

    // =====================================================
    // 运行时能量检查和任务中断（V28.15新增）
    // =====================================================

    void ALAPBlockScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ALAP-Block] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const double EPSILON = 1e-9;
        std::vector<AbsRTTask *> tasks_to_interrupt;

        // ⭐ V28.15修复：使用kernel的getCurrentExecutingTasks()获取实际运行中的任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Block] getCurrentExecutingTasks() 返回 ") +
                           std::to_string(running_tasks.size()) + " 个运行中任务");

        // ⭐ 预扣模式：能量已在调度时通过getTaskN()扣除，这里不再重复扣除
        // (旧代码：扣除上一ms执行消耗的能量 - 已废弃)

        // 1. 检查所有运行中的任务
        for (auto &map_pair : running_tasks) {
            AbsRTTask *task = map_pair.second;
            if (!task) {
                continue;
            }

            // 计算该任务执行1ms所需的能量
            double unit_energy = calculateUnitEnergyForTask(task);

            // ⭐ 检查：当前能量是否足够该任务继续��行1ms
            if (_current_energy < unit_energy - EPSILON) {
                SCHEDULER_LOG_WARNING(std::string("⚡ [ALAP-Block] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [ALAP-Block] 任务能量充足: ") +
                                   getTaskName(task) +
                                   " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                   " 当前能量=" + std::to_string(_current_energy) + "J");
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Block] 运行任务检查完成: map大小=") +
                           std::to_string(running_tasks.size()));

        // 2. 中断能量不足的任务
        for (AbsRTTask *task : tasks_to_interrupt) {
            if (!task) {
                continue;
            }

            SCHEDULER_LOG_INFO(std::string("🛑 [ALAP-Block] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法中断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            setSuspendReason(task, "insufficient_energy");
            _kernel->suspend(task);

            // ⭐ V40重构：能量检查事件已删除，不再需要取消能量检查事件
            // auto it = _energy_check_events.find(task);
            // if (it != _energy_check_events.end()) {
            //     // 从map中移除，但不删除事件对象（它会自然结束）
            //     _energy_check_events.erase(it);
            //     SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Block] 已取消任务的能量检查事件: ") + getTaskName(task));
            // }

            SCHEDULER_LOG_INFO(std::string("⏸️ [ALAP-Block] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Block] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }

    bool ALAPBlockScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        ALAPBlockTaskModel *running_model = getTaskModel(running_task);
        ALAPBlockTaskModel *new_model = getTaskModel(new_task);

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

    void ALAPBlockScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ALAP-Block] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void ALAPBlockScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ALAP-Block] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        const std::string suspend_reason = getSuspendReason(task);
        const bool preserve_runtime_state =
            suspend_reason == "insufficient_energy" ||
            suspend_reason == "preemption" ||
            suspend_reason == "alap_blocking";

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        clearBlockingStateIfOwner(task, "extract");
        if (!preserve_runtime_state) {
            clearPersistentTaskState(task);
        }
    }

    void ALAPBlockScheduler::tryImmediateRedispatch(const char *reason) {
        if (!_kernel) {
            _kernel = getKernel();
        }

        if (!_kernel) {
            return;
        }

        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Block] 跳过立即重调度，能量已耗尽: ") + reason);
            return;
        }

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG(std::string("📭 [ALAP-Block] 跳过立即重调度，就绪队列为空: ") + reason);
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Block] 立即重调度: ") + reason);
        _kernel->dispatch();
    }

    void ALAPBlockScheduler::clearBlockingStateIfOwner(AbsRTTask *task, const char *reason) {
        if (!task) {
            return;
        }

        if (_blocking_task != task) {
            return;
        }

        SCHEDULER_LOG_WARNING(std::string("🔓 [ALAP-Block] 阻塞拥有者已离开系统，清除严格阻塞: ") +
                              getTaskName(task) + " reason=" + reason);

        _blocking_task = nullptr;
        _alap_blocking = false;

        tryImmediateRedispatch(reason);
    }

    void ALAPBlockScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是���已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ALAP-Block] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ALAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            if (hasHigherRMPriority(task, *it)) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ALAP-Block] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(model->getRMPriority())));
    }

    void ALAPBlockScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ALAP-Block] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ALAPBlockScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ALAP-Block] 任务加入等待队列: ") + getTaskName(task));
    }

    void ALAPBlockScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ALAPBlockScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ALAPBlockScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *ALAPBlockScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ALAPBlockScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        if (_paid_pending_tasks.find(task) !=
            _paid_pending_tasks.end()) {
            return 0.0;
        }
        return getConfiguredUnitEnergyForTask(task);
    }

    // =====================================================
    // ⭐ 能量耗尽预测机制（Bug修复：防止虚空借电）
    // =====================================================

    double ALAPBlockScheduler::calculateTotalPowerConsumption() {
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

    MetaSim::Tick ALAPBlockScheduler::predictTimeToDepletion(double energy, double power) {
        if (power <= 0.0 || energy <= 0.0) {
            return MetaSim::Tick(-1);  // 无法预测
        }
        // time_to_deplete = energy / power (单位：ms)
        // 返回从当前时间算起，还能运行多少ms
        double time_ms = energy / power;
        return static_cast<MetaSim::Tick>(ceil(time_ms));
    }

    void ALAPBlockScheduler::scheduleEnergyDepletionEvent(MetaSim::Tick time_until_depletion) {
        // ⭐ V56捉鬼：废除"全局能量耗尽预测闹钟"！
        // ALAP-Block不再需要预测"全局何时耗尽"——这是一个错误的全局断头台概念。
        // Block壁垒完全由performTickScheduling()中的逐级剥夺逻辑在Tick边界建立。
        SCHEDULER_LOG_DEBUG("⚡ [ALAP-Block] scheduleEnergyDepletionEvent()已被废除，不执行任何操作！");
    }

    void ALAPBlockScheduler::cancelEnergyDepletionEvent() {
        if (_energy_depleted_event) {
            _energy_depleted_event->drop();
        }
    }

    void ALAPBlockScheduler::onEnergyDepleted() {
        // ⭐ V56捉鬼：废除"全局断头台"！
        // onEnergyDepleted()曾是全局耗尽事件的处理器，会无脑挂起所有任务。
        // 现在它被彻底废除——不再有任何全局耗尽事件被调度！
        // Block壁垒的建立完全由performTickScheduling()中的逐级剥夺逻辑处理。
        Tick current_time = SIMUL.getTime();
        SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] onEnergyDepleted()已被废除，本函数不再执行任何操作！"
                             " 时间=" + std::to_string(static_cast<int64_t>(current_time)) + "ms");
        // ⚠️ 绝对不设置 _energy_depleted = true
        // ⚠️ 绝对不调用任何 _kernel->suspend()
        // ⚠️ 绝对不调用 dispatch()
        // → Block壁垒由逐级剥夺逻辑自然建立
    }

    // ⭐ EnergyInfoProvider接口实现
    double ALAPBlockScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ALAPBlockScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void ALAPBlockScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string ALAPBlockScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void ALAPBlockScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    double ALAPBlockScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ALAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] calculateTotalEnergyForTask: 任务模型不存在");
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

    double ALAPBlockScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-Block] 功率计算: ") +
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
    void ALAPBlockScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        SCHEDULER_LOG_INFO(std::string("🔍 [ALAP-Block] startEnergyCheckForTask调用: ") +
                          getTaskName(task) + " CPU=" + (cpu ? cpu->toString() : "NULL"));

        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING(std::string("❌ [ALAP-Block] startEnergyCheckForTask提前返回: ") +
                                 "task=" + (task ? getTaskName(task) : "NULL") +
                                 " cpu=" + (cpu ? cpu->toString() : "NULL"));
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [ALAP-Block] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        ALAP-BlockEnergyCheckEvent *evt = new ALAP-BlockEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
    }

    void ALAPBlockScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [ALAP-Block] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }
    */  // ⭐ V40重构：能量检查方法已删除

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ALAPBlockScheduler::collectSolarEnergy(Tick current_time) {
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

    double ALAPBlockScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Block] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Block] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ALAPBlockScheduler::scheduleNextTick() {
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

    ALAPBlockTaskModel *ALAPBlockScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ALAPBlockScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    void ALAPBlockScheduler::clearPersistentTaskState(
        AbsRTTask *task) {
        if (!task) {
            return;
        }

        _energy_accounts.erase(task);
        _suspend_reasons.erase(task);
        _paid_pending_tasks.erase(task);
        _pending_payment_ticks.erase(task);
        _paid_execution_credit_tasks.erase(task);
    }

    void ALAPBlockScheduler::resetTickDispatchState() {
        _dispatch_selection_order.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_frozen = false;
    }

    void ALAPBlockScheduler::clearTaskTickSelection(
        AbsRTTask *task) {
        if (!task) {
            return;
        }

        _dispatch_selection_order.erase(
            std::remove(
                _dispatch_selection_order.begin(),
                _dispatch_selection_order.end(),
                task),
            _dispatch_selection_order.end());
    }

    std::vector<AbsRTTask *>
    ALAPBlockScheduler::collectActiveJobs(Tick current_time) {
        std::vector<AbsRTTask *> active_tasks;
        auto add_active =
            [&active_tasks, current_time](
                AbsRTTask *task, bool running) {
                if (!task || task->getArrival() > current_time) {
                    return;
                }
                if (!running && !task->isActive()) {
                    return;
                }
                if (task->getRemainingWCET() <= 0.0) {
                    return;
                }
                if (std::find(
                        active_tasks.begin(),
                        active_tasks.end(),
                        task) == active_tasks.end()) {
                    active_tasks.push_back(task);
                }
            };

        if (_kernel) {
            for (const auto &[cpu, task] :
                 _kernel->getCurrentExecutingTasks()) {
                (void)cpu;
                add_active(
                    task,
                    task && task->isExecuting());
            }
        }
        for (AbsRTTask *task : _ready_queue) {
            add_active(task, false);
        }
        return active_tasks;
    }

    std::vector<AbsRTTask *>
    ALAPBlockScheduler::collectALAPCandidates(
        const std::vector<AbsRTTask *> &active_tasks,
        Tick current_time) {
        std::vector<AbsRTTask *> candidates;
        for (AbsRTTask *task : active_tasks) {
            if (calculateSlackForTask(task, current_time) <=
                Tick(0)) {
                candidates.push_back(task);
            }
        }
        return candidates;
    }

    bool ALAPBlockScheduler::hasHigherRMPriority(
        AbsRTTask *lhs, AbsRTTask *rhs) {
        if (lhs == rhs) {
            return false;
        }

        ALAPBlockTaskModel *lhs_model = getTaskModel(lhs);
        ALAPBlockTaskModel *rhs_model = getTaskModel(rhs);
        if (lhs_model && rhs_model &&
            lhs_model->getRMPriority() !=
                rhs_model->getRMPriority()) {
            return lhs_model->getRMPriority() <
                   rhs_model->getRMPriority();
        }
        return lhs->getTaskNumber() < rhs->getTaskNumber();
    }

    void ALAPBlockScheduler::sortByRMPriority(
        std::vector<AbsRTTask *> &tasks) {
        std::stable_sort(
            tasks.begin(),
            tasks.end(),
            [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                return hasHigherRMPriority(lhs, rhs);
            });
    }

    double ALAPBlockScheduler::getConfiguredUnitEnergyForTask(
        AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end() || !it->second) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    void ALAPBlockScheduler::commitTickEnergy(
        Tick tick, double energy) {
        if (_energy_commit_valid &&
            _energy_commit_tick == tick) {
            throw std::logic_error(
                "ALAP-Block energy committed more than once in one tick");
        }
        if (energy < 0.0 ||
            _current_energy + 1e-9 < energy) {
            throw std::logic_error(
                "ALAP-Block attempted to commit unaffordable energy");
        }

        _current_energy =
            std::max(0.0, _current_energy - energy);
        _stats.total_energy_consumed += energy;
        _energy_commit_tick = tick;
        _energy_commit_valid = true;
    }

    void ALAPBlockScheduler::cancelStaleDispatches(
        const std::vector<AbsRTTask *> &previous_selection) {
        bool has_stale_dispatch = false;
        for (AbsRTTask *task : previous_selection) {
            if (!task || _kernel->getProcessor(task) != nullptr) {
                continue;
            }
            if (std::find(
                    _dispatch_selection_order.begin(),
                    _dispatch_selection_order.end(),
                    task) ==
                _dispatch_selection_order.end()) {
                has_stale_dispatch = true;
                break;
            }
        }
        if (!has_stale_dispatch) {
            return;
        }

        for (const auto &[cpu, running] :
             _kernel->getCurrentExecutingTasks()) {
            if (!running && _kernel->isCPUDispatching(cpu)) {
                _kernel->dispatch(cpu);
            }
        }
    }

    AbsRTTask *ALAPBlockScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int ALAPBlockScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *ALAPBlockScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void ALAPBlockScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ALAP-Block] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ALAPBlockScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ALAP-Block] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ALAPBlockScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ALAPBlockScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *ALAPBlockScheduler::getKernel() {
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

    void ALAPBlockScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-Block] newRun - 仿真开始");

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();

        _ready_queue.clear();
        _waiting_queue.clear();
        _energy_accounts.clear();
        _suspend_reasons.clear();
        _running_tasks.clear();
        _dispatch_selection_order.clear();
        _paid_pending_tasks.clear();
        _pending_payment_ticks.clear();
        _paid_execution_credit_tasks.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_generation = 0;
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_valid = false;
        _energy_depleted = false;
        _alap_blocking = false;
        _blocking_task = nullptr;

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Block] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ALAPBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-Block] endRun - 仿真结束");

        resetTickDispatchState();
        _paid_pending_tasks.clear();
        _pending_payment_ticks.clear();
        _paid_execution_credit_tasks.clear();

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ALAP-Block] ===== ALAP-Block调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ALAPBlockScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Block] 任务结束: ") + getTaskName(task));

        clearBlockingStateIfOwner(task, "onTaskEnd");

        // 从就绪队列移除
        removeFromReadyQueue(task);
        clearTaskTickSelection(task);
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
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Block] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Block] 当前能量: ") + std::to_string(_current_energy) + "J");

        // The next tick owns the next ALAP decision. Dispatching here would
        // bypass the frozen selection and its committed energy reservation.
    }

    bool ALAPBlockScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 过期任务清理 - 清理超过截止期的旧任务实例
    // =====================================================

    void ALAPBlockScheduler::cleanupExpiredTasks() {
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
                ALAPBlockTaskModel *model = getTaskModel(task);
                if (!model) continue;

                Tick arrival = task->getArrival();
                Tick deadline = task->getDeadline();

                if (deadline <= current_time) {
                    to_suspend.push_back(task);
                    SCHEDULER_LOG_INFO("💀 [ALAP-Block] 过期任务运行中，将挂起: " +
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
            ALAPBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            Tick arrival = task->getArrival();
            Tick deadline = task->getDeadline();

            if (deadline <= current_time) {
                expired.push_back(task);
                SCHEDULER_LOG_INFO("🧹 [ALAP-Block] 清理过期任务: " +
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
    bool ALAPBlockScheduler::checkALAPTimingGate() {
        Tick current_time = SIMUL.getTime();
        Tick min_slack = 0;
        bool first_task = true; // 关键修复：用于正确初始化最小值

        // 收集所有需要评估的任务
        std::vector<AbsRTTask *> all_tasks;

        // 1. 就绪队列中的未调度任务
        for (AbsRTTask *task : _ready_queue) {
            if (task) all_tasks.push_back(task);
        }

        // 2. 运行中的任务
        if (!_kernel) {
            _kernel = getKernel();
        }

        if (_kernel) {
            const auto& running_tasks = _kernel->getCurrentExecutingTasks();
            for (const auto& map_pair : running_tasks) {
                AbsRTTask *task = map_pair.second;
                if (task && task->isExecuting()) {
                    all_tasks.push_back(task);
                }
            }
        }

        // 如果没有任何任务，通过门控
        if (all_tasks.empty()) {
            return true;
        }

        // 计算所有任务的Slack，找最小值
        for (AbsRTTask *task : all_tasks) {
            if (!task || !task->isActive()) continue;

            Tick slack;
            try {
                slack = calculateSlackForTask(task);
            } catch (...) {
                continue;
            }

            // ⭐ 关键修复：绝对安全的求最小值逻辑
            if (first_task) {
                min_slack = slack;
                first_task = false;
            } else if (slack < min_slack) {
                min_slack = slack;
            }
        }

        // 如果仍为 first_task，说明有效任务为空
        if (first_task) return true;

        // 门控逻辑
        if (min_slack > 0) {
            // ⭐ 纯正 ALAP 核心：设置精确唤醒闹钟
            Tick wake_time = current_time + min_slack;
            
            // 直接 drop() 旧闹钟，发布新闹钟
            if (_alap_wake_event) {
                _alap_wake_event->drop();
                _alap_wake_event->post(wake_time);
            }

            SCHEDULER_LOG_INFO("⏸️  [ALAP-Block] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠。已定闹钟于 " +
                               std::to_string(static_cast<int64_t>(wake_time)) + "ms");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ALAP-Block] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }


    MetaSim::Tick ALAPBlockScheduler::calculateSlackForTask(AbsRTTask *task) {
        return calculateSlackForTask(task, SIMUL.getTime());
    }

    MetaSim::Tick ALAPBlockScheduler::calculateSlackForTask(
        AbsRTTask *task, Tick current_time) {
        if (!task) {
            return Tick(0);
        }

        const Tick absolute_deadline = task->getDeadline();
        const double remaining_double =
            std::max(0.0, task->getRemainingWCET());
        const auto remaining_ticks =
            static_cast<Tick::impl_t>(
                std::ceil(remaining_double));
        const Tick remaining(remaining_ticks);
        const Tick slack =
            absolute_deadline - remaining - current_time;

        SCHEDULER_LOG_DEBUG("🧮 [ALAP-Block] Slack计算: " +
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

    void ALAPBlockScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ALAP-Block] ===== ALAP-Block调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO(std::string("  ALAP强制休眠次数: ") + std::to_string(_stats.total_alap_forced_idle));
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ALAPBlockScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ALAPBlockScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

} // namespace RTSim
