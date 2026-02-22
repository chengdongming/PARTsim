// gpfp_tie_scheduler.cpp - ALAP-Block (Tick-based Instant Energy-aware) Scheduler Implementation
// 算法特点：
// 1. 基于当前实际能量进行即时判断（无前瞻性预测）
// 2. 每ms逐次扣减能耗
// 3. 级联调度：能量不足立即停止
// 4. Tick级抢占
// 5. Tick末尾收集能量

#include <algorithm>
#include <cmath>
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
            _scheduler->_current_energy = 0.0;

            // 中断当前任务（调用kernel的suspend机制）
            if (_cpu) {
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
          _energy_depleted(false),
          _alap_blocking(false),
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
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ALAP-Block] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
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
        SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Block] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms =====");
        SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

        // ⭐ 每个Tick开始时清除抢占防抖标记
        // 这样在下一个tick可以正常进行抢占检查
        _last_preempted_task = nullptr;

        _stats.total_tick_count++;

        // ⭐ 关键修复：每个 Tick 开始时清除 ALAP 阻塞标志
        // 阻塞只在一个 Tick 内有效，下一个 Tick 重新评估
        _alap_blocking = false;

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
                    SCHEDULER_LOG_INFO("🔋 [ALAP-Block] 太阳能充电成功，恢复调度 (能量=" +
                                      std::to_string(_current_energy * 1000) + " mJ >= 阈值=" +
                                      std::to_string(RECOVERY_THRESHOLD * 1000) + " mJ)");
                }
            }
        }
        _last_tick_time = current_time;

        // ⭐ Bug修复3：能量耗尽时跳过任务调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Block] 能量已耗尽，跳过任务调度"));
            return;
        }

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第1.5步：清理过期任务实例 ==========
        // ⭐ 已改用killOnMiss(true)，框架自动处理过期实例
        // cleanupExpiredTasks();

        // ========== 阶段一：ALAP个体时序门控 ==========
        // ⭐ 修复：移除批量级别的S_min门控，改为个体Slack过滤
        // 原因：每个任务独立计算Slack，Slack<=0的任务才能调度
        // 个体Slack检查在getTaskN中进行，这里不再做全局门控
        SCHEDULER_LOG_INFO("✅ [ALAP-Block] 个体时序门控：跳过全局S_min检查，个体Slack在getTaskN中过滤");

        // ========== 第2步：处理运行中任务的续期能量 ==========
        // ⭐ 重构：在tick边界扣除运行任务的续期能量（替代ALAP-BlockEnergyCheckEvent）
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
                if (!task || !task->isExecuting()) continue;

                double unit_energy = calculateUnitEnergyForTask(task);

                // 检查是否有足够能量续期1ms
                const double EPSILON = 1e-9;
                if (_current_energy < unit_energy - EPSILON) {
                    // ⭐ V43修复：能量不足时设置能量耗尽标志
                    if (!_energy_depleted) {
                        _energy_depleted = true;
                        _current_energy = 0.0;  // 强制设为0，防止变负
                        SCHEDULER_LOG_WARNING("💀 [ALAP-Block] 能量耗尽，设置_energy_depleted标志");
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
        // checkAndPreempt();  // 禁用tick边界抢占，防止suspend-insert循环

        // ========== 第4步：��度新任务 ==========
        // ⭐ V40修复：确保kernel已设置
        if (!_kernel) {
            _kernel = getKernel();
        }

        if (_kernel) {
            SCHEDULER_LOG_INFO("🔔 开始调度新任务");

            // 记录调度前的能量
            double energy_before_scheduling = _current_energy;

            // ⭐ 关键：清空本次tick的调度记录
            // getTaskN会填充这个集合，但不扣除能量
            _counted_tasks_in_dispatch.clear();
            _dispatching_tasks_total_energy = 0.0;

            // 调度任务（getTaskN只做决策和标记，不扣除能量）
            _kernel->dispatch();

            // ⭐ 关键：在dispatch后，统一扣除所有已标记任务的能量
            for (AbsRTTask *task : _counted_tasks_in_dispatch) {
                double unit_energy = calculateUnitEnergyForTask(task);
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;
                _dispatching_tasks_total_energy += unit_energy;

                SCHEDULER_LOG_INFO("✅ 新任务扣除初始能量: " +
                                   getTaskName(task) +
                                   " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                                   std::to_string(_current_energy * 1000) + " mJ");
            }

            SCHEDULER_LOG_INFO("📊 调度完成: 新任务=" +
                               std::to_string(_counted_tasks_in_dispatch.size()) +
                               " 扣除能量=" + std::to_string(_dispatching_tasks_total_energy * 1000) + " mJ " +
                               std::to_string(energy_before_scheduling * 1000) + " → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        }

        // ========== 第5步：调度后抢占检查 ==========
        // ⭐ V44修复：在调度新任务后进行抢占检查
        // 原因：需要让新任务先调度完成，然后再检查是否需要抢占
        // 这样可以避免"刚调度就被抢占"的问题
        // 同时确保在tick边界统一进行抢占决策
        checkAndPreempt();

        SCHEDULER_LOG_INFO("✅ Tick " +
                           std::to_string(static_cast<int64_t>(current_time)) +
                           "ms 完成, 剩余能量: " +
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
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Block] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // ⭐ 核心：不在这里收集能量，能量收集在tick边界完成

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-Block] getFirst: 就绪队列为空");
            return nullptr;
        }

        AbsRTTask *first_task = _ready_queue.front();
        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-Block] getFirst: 队列首任务为空");
            return nullptr;
        }

        // ⭐ 核心：即时能量判断（当前能量 >= 1ms能耗）
        double unit_energy = calculateUnitEnergyForTask(first_task);

        if (_current_energy < unit_energy) {
            // ⭐ 关键修复：根据原论文，ALAP-Block 应该"死守高优，宁缺毋滥"
            // 能量不足时，设置阻塞标志，本 Tick 拒绝调度任何任务（包括次高优先级任务）
            _alap_blocking = true;
            SCHEDULER_LOG_WARNING(std::string("🚫 [ALAP-Block] 能量不足，启动严格阻塞模式（死守高优，宁缺毋滥）") +
                                 " 任务: " + getTaskName(first_task) +
                                 " 需要: " + std::to_string(unit_energy) + "J" +
                                 " 当前: " + std::to_string(_current_energy) + "J" +
                                 " → 本 Tick 阻塞全部调度");
            return nullptr;
        }

        // 能量充足，清除阻塞标志
        _alap_blocking = false;

        // 返回任务（能量在notify时扣减）
        return first_task;
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（级联调度）
    // =====================================================

    AbsRTTask *ALAPBlockScheduler::getTaskN(unsigned int n) {

        // ⭐ V43修复：能量耗尽时立即返回，不调度任何任务
        if (_energy_depleted) {
            SCHEDULER_LOG_DEBUG(std::string("💀 [ALAP-Block] getTaskN: 能量已耗尽，拒绝调度") +
                               " n=" + std::to_string(n) +
                               " energy=" + std::to_string(_current_energy * 1000) + " mJ");
            return nullptr;
        }

        // ⭐ 关键修复：ALAP-Block 严格阻塞机制
        // 如果本 Tick 已触发阻塞（能量不足），拒绝调度任何次高优先级任务
        if (_alap_blocking) {
            SCHEDULER_LOG_DEBUG(std::string("🚫 [ALAP-Block] getTaskN: ALAP严格阻塞模式，拒绝调度") +
                               " n=" + std::to_string(n) +
                               " 原因：高优先级任务能量不足，宁缺毋滥");
            return nullptr;
        }

        // ⭐ ALAP时序门控：不再在getTaskN中调用全局checkALAPTimingGate()（性能瓶颈）
        // 改为在遍历任务时逐个检查个体Slack，只调度Slack≤0的任务
        // 效果等价：如果所有任务Slack>0，getTaskN返回nullptr

        SCHEDULER_LOG_DEBUG(std::string("🔍 [ALAP-Block] getTaskN(") + std::to_string(n) + ") " +
                           "已调度能耗=" + std::to_string(_dispatching_tasks_total_energy) + "J " +
                           "当前能量=" + std::to_string(_current_energy) + "J " +
                           "队���大小=" + std::to_string(_ready_queue.size()));


        if (_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("📭 [ALAP-Block] getTaskN: 就绪队列为空");
            return nullptr;
        }

        // ⭐ 暂时注释掉清理逻辑，先观察队列实际状态
        /*
        // ⭐ 关键修复：清理_ready_queue中过期的周期性任务实例
        // 对于周期性任务，使用到达时间来判断实例是否过期
        Tick current_time = SIMUL.getTime();
        _ready_queue.erase(
            std::remove_if(_ready_queue.begin(), _ready_queue.end(),
                [this, current_time](AbsRTTask *task) {
                    if (!task) return true;
                    // 移除不活动的任务
                    if (!task->isActive()) {
                        SCHEDULER_LOG_DEBUG(std::string("🧹 [ALAP-Block] 清理不活动任务: ") + getTaskName(task));
                        return true;
                    }
                    // ⭐ 移除过期的周期性任务实例：到达时间+截止时间 < 当前时间
                    Tick arrival = task->getArrival();
                    Tick deadline = arrival + Tick(20);  // 周期性任务的截止时间是到达时间+周期
                    if (deadline < current_time) {
                        SCHEDULER_LOG_DEBUG(std::string("🧹 [ALAP-Block] 清理过期任务实例: ") +
                                       getTaskName(task) +
                                       " 到达=" + std::to_string(static_cast<int64_t>(arrival)) +
                                       " 截止=" + std::to_string(static_cast<int64_t>(deadline)) +
                                       " 当前=" + std::to_string(static_cast<int64_t>(current_time)));
                        return true;
                    }
                    return false;
                }),
            _ready_queue.end()
        );

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [ALAP-Block] getTaskN: 清理后队列为空");
            return nullptr;
        }
        */

        // ⭐ V30调试：输出ready queue信息
        std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - ready_queue.size()=" << _ready_queue.size() << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            std::cout << "[DEBUG]   ready_queue[" << i << "]=" << getTaskName(_ready_queue[i]) << std::endl;
        }

        // ⭐ 级联调度：遍历就绪队列，运行中任务也要检查能量
        unsigned int ready_index = 0;
        std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - 开始遍历ready_queue, 查找第" << n << "个未调度任务" << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            AbsRTTask *task = _ready_queue[i];

            if (!task) {
                continue;
            }

            // ⭐ killOnMiss安全检查：跳过已被框架终止的任务实例
            if (!task->isActive()) {
                continue;
            }
            // _counted_tasks_in_dispatch只是用于跟踪本次tick中已扣除能量的任务
            // 避免重复扣除能量
            // 重复调度的问题由内核的_m_dispatched检查来处理
            bool is_running = false;
            if (_kernel) {
                CPU *proc = _kernel->getProcessor(task);
                is_running = (proc != nullptr);
            }

            // 检查是否已在本tick中扣除过能量
            bool already_counted = _counted_tasks_in_dispatch.find(task) != _counted_tasks_in_dispatch.end();

            std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - i=" << i << " task=" << getTaskName(task)
                      << " ready_index=" << ready_index << " is_running=" << is_running
                      << " already_counted=" << already_counted << std::endl;

            // ⭐ V29.1修复：运行中任务的续期由ALAP-BlockEnergyCheckEvent处理，getTaskN()不再扣除续期能量
            // 设计原则：
            // - getTaskN(): 只负责新任务的首次调度和能量扣除
            // - ALAP-BlockEnergyCheckEvent: 负责运行中任务的续期能量扣除（每1ms触发一次）
            if (is_running) {
                // 运行中任务：直接返回让kernel继续调度
                // 不检查能量（由ALAP-BlockEnergyCheckEvent检查）
                // 不扣除能量（由ALAP-BlockEnergyCheckEvent扣除）

                if (ready_index == n) {
                    return task;
                }

                ready_index++;
                continue;
            }


            // 这是第ready_index个未dispatch的任务
            if (ready_index == n) {
                // ⭐ 关键修复：跳过已过期的任务实例
                ALAPBlockTaskModel *task_model = getTaskModel(task);
                if (task_model) {
                    Tick arrival = task->getArrival();
                    Tick deadline = arrival + Tick(task_model->getPeriod());
                    Tick current_time = SIMUL.getTime();
                    if (deadline <= current_time) {
                        SCHEDULER_LOG_INFO(std::string("🧹 [ALAP-Block] getTaskN: 跳过过期任务 ") +
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
                    SCHEDULER_LOG_INFO(std::string("⏸️ [ALAP-Block] getTaskN: 个体Slack>0，跳过 ") +
                                      getTaskName(task) +
                                      " Slack=" + std::to_string(static_cast<int64_t>(individual_slack)) + "ms");
                    // 不增加ready_index，继续找下一个Slack≤0的任务
                    continue;
                }

                // ⭐ 计算任务的1ms能耗
                double unit_energy = calculateUnitEnergyForTask(task);

                // ⭐ V30调试：输出能量检查信息
                std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - 准备调度第" << ready_index << "个任务: " << getTaskName(task)
                          << " 需要1ms=" << unit_energy * 1000 << " mJ"
                          << " 当前能量=" << _current_energy * 1000 << " mJ" << std::endl;

                const double EPSILON = 1e-9;
                // ⭐ 预扣模式：检查当前能量是否足够当前任务的1ms能耗
                if (_current_energy < unit_energy - EPSILON) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [ALAP-Block] 能量不足，停止级联") +
                                      " 任务=" + getTaskName(task) +
                                      " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                      " 当前能量=" + std::to_string(_current_energy) + "J");
                    std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - 能量不足，返回nullptr" << std::endl;
                    return nullptr;  // ⭐ 立即停止级联
                }

                // ⭐ 重构：只标记任务，不扣除能量
                // 能量将在performTickScheduling的dispatch后统一扣除
                if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                    // 尚未标记，标记任务
                    _counted_tasks_in_dispatch.insert(task);

                    SCHEDULER_LOG_INFO(std::string("✅ [ALAP-Block] 决定调度任务（已标记，暂不扣能量）: ") + getTaskName(task) +
                                      " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ");
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("♻️ [ALAP-Block] 任务已标记，直接返回: ") + getTaskName(task));
                }

                return task;
            } else {
                // ⭐ V32关键修复：不是我们要找的第n个任务，继续寻找
                ready_index++;
            }

        }

        std::cout << "[DEBUG] ALAP-Block::getTaskN(" << n << ") - 循环结束，未找到第" << n << "个任务，返回nullptr (ready_index=" << ready_index << ")" << std::endl;
        return nullptr;
    }

    // =====================================================
    // notify - 每ms逐次扣减能耗（ALAP-Block核心逻辑）
    // =====================================================

    void ALAPBlockScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否足够
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ALAP-Block] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [ALAP-Block] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ALAPBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] addTask: 任务为空");
            return;
        }

        // ⭐ Bug修复4：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [ALAP-Block] 能量已耗尽，拒绝添加新任务: ") +
                                     getTaskName(task));
            return;  // 拒绝添加
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

            // ⭐ V44关键修复：恢复抢占检查中的ALAP Slack门控
            // 原因：保持与getTaskN中个体Slack门控的一致性
            //
            // 问题背景：
            // - getTaskN中：Slack>0的任务被跳过，不调度
            // - 抢占检查中：如果移除Slack门控，会导致Slack>0的高优先级任务抢占低优先级任务
            // - 这造成不一致：调度时不用，抢占时用
            //
            // 修复策略：
            // - 抢占检查中也应用Slack门控：只有Slack≤0的任务才能参与抢占
            // - 这样确保"尽可能晚调度"原则在抢占时也生效
            // - 避免Slack>0的任务抢占Slack≤0的任务

            ALAPBlockTaskModel *model = getTaskModel(candidate);
            if (!model) continue;

            // 计算候选任务的Slack
            Tick candidate_slack = calculateSlackForTask(candidate);

            // ⭐ Slack门控：只有Slack≤0的任务才能参与抢占
            if (candidate_slack > 0) {
                continue;  // Slack>0，跳过这个候选任务
            }

            // 在Slack≤0的任务中，找优先级最高的
            if (!best_candidate || model->getRMPriority() < best_model->getRMPriority()) {
                best_candidate = candidate;
                best_model = model;
                best_slack = candidate_slack;
            }
        }

        if (!best_candidate) return;

        // 找运行中优先级最低的任务
        AbsRTTask *worst_running = nullptr;
        ALAPBlockTaskModel *worst_model = nullptr;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            ALAPBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            if (!worst_running || model->getRMPriority() > worst_model->getRMPriority()) {
                worst_running = task;
                worst_model = model;
            }
        }

        if (!worst_running || !worst_model) return;

        // 只有候选任务优先级更高时才抢占（只抢占一个）
        if (best_model->getRMPriority() < worst_model->getRMPriority()) {
            double unit_energy = calculateUnitEnergyForTask(best_candidate);
            if (_current_energy < unit_energy) return;

            SCHEDULER_LOG_INFO(std::string("🔄 [ALAP-Block] ALAP抢占: ") +
                              " 挂起=" + getTaskName(worst_running) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(worst_model->getRMPriority())) + ")" +
                              " 调度=" + getTaskName(best_candidate) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(best_model->getRMPriority())) +
                              " Slack=" + std::to_string(static_cast<int64_t>(best_slack)) + ")");

            // ⭐ 记录最近被挂起的任务，用于防抖
            _last_preempted_task = worst_running;
            _last_preempted_tick = current_time;

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
            // suspend会自动调用deschedule()并将任务重新放回调���队列
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

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
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

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ALAPBlockTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ALAP-Block] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
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
        ALAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
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

        // 获取当前辐照度（根据use_real_solar_data选择NASA数据或函数曲线）
        double irradiance = getSolarIrradiance(current_ms);

        // 计算收集能量
        double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
        double energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;

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

    void ALAPBlockScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
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

        SCHEDULER_LOG_INFO(std::string("💰 [ALAP-Block] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ALAPBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ALAP-Block] endRun - 仿真结束");

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
            SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Block] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ALAP-Block] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [ALAP-Block] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
            SCHEDULER_LOG_INFO("🔄 [ALAP-Block] 任务结束，触发立即调度");
            _kernel->dispatch();
        }
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
                Tick deadline = arrival + Tick(model->getPeriod());

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
            Tick deadline = arrival + Tick(model->getPeriod());

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
        Tick min_slack = Tick(-1);

        // ⭐ 关键修复：同时检查就绪队列和运行中的任务
        // 根据原论文，应该检查"所有就绪任务"，包括正在运行的任务
        std::vector<AbsRTTask *> all_tasks;

        // 1. 添加就绪队列中的未调度任务
        for (AbsRTTask *task : _ready_queue) {
            if (task) all_tasks.push_back(task);
        }

        // 2. ⭐ 添加运行中的任务
        // ⭐ 确保 _kernel 已设置
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
            if (!task) continue;

            // ⭐ 关键修复：检查任务是否仍然有效
            // 在任务结束时，任务可能已经被部分删除，导致vtable指针为NULL
            // 检查任务是否活跃可以防止访问无效的对象
            if (!task->isActive()) {
                SCHEDULER_LOG_DEBUG("⏭️ [ALAP-Block] checkALAPTimingGate: 跳过非活跃任务");
                continue;
            }

            // ⭐ 使用try-catch保护calculateSlackForTask调用
            // 防止访问已删除的对象
            Tick slack;
            try {
                slack = calculateSlackForTask(task);
            } catch (...) {
                SCHEDULER_LOG_WARNING("⚠️ [ALAP-Block] checkALAPTimingGate: 计算Slack时发生异常，跳过任务");
                continue;
            }

            if (min_slack < 0 || slack < min_slack) {
                min_slack = slack;
            }
        }

        // 门控逻辑
        if (min_slack > 0) {
            SCHEDULER_LOG_INFO("⏸️  [ALAP-Block] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ALAP-Block] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }

    MetaSim::Tick ALAPBlockScheduler::calculateSlackForTask(AbsRTTask *task) {
        if (!task) return MetaSim::Tick(0);

        Tick current_time = SIMUL.getTime();
        Tick arrival = task->getArrival();
        int period_int = task->getPeriod();
        Tick period = Tick(period_int > 0 ? period_int : 100);
        Tick absolute_deadline = arrival + period;

        double remaining_double = task->getRemainingWCET();
        Tick remaining = Tick(remaining_double);
        Tick slack = absolute_deadline - remaining - current_time;

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
