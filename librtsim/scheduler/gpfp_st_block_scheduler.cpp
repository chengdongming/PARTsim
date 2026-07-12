// gpfp_st_block_scheduler.cpp - ST-Block (Slack Time Block) Scheduler Implementation
// 算法特点：
// 1. ASAP调度：尽可能早执行任务（不需要等Slack=0）
// 2. 严格阻断：高优先级任务缺电时，阻塞所有任务
// 3. 深度充电：能量不足时进入充电模式，直到Slack=0或电池充满
// 4. Tick级能量检查和续期
// 5. Tick末尾收集能量

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_st_block_scheduler.hpp>
#include <rtsim/task.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/cpu.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>
#include <rtsim/scheduler/st_energy_utils.hpp>
#include <rtsim/mrtkernel.hpp>

// 统一日志系统
#include "../../utils/unified_logger.hpp"

namespace RTSim {

    using namespace MetaSim;

    static SchedulerTraceJob makeSTBlockTraceJob(
        AbsRTTask *task,
        const std::map<AbsRTTask *, STBlockTaskModel *> &models,
        int ready_order) {
        SchedulerTraceJob job{};
        Task *concrete_task = dynamic_cast<Task *>(task);
        job.task_name = concrete_task
            ? concrete_task->getName()
            : std::string("task_") + std::to_string(task ? task->getTaskNumber() : -1);
        job.arrival_time = concrete_task
            ? static_cast<double>(concrete_task->getLastArrival())
            : (task ? static_cast<double>(task->getArrival()) : 0.0);
        job.priority = 0.0;
        job.ready_order = ready_order;
        job.task_unit_energy_mJ = 0.0;
        job.remaining_time_ms = task ? task->getRemainingWCET() : 0.0;
        job.absolute_deadline = task ? static_cast<double>(task->getDeadline()) : 0.0;

        auto model_it = models.find(task);
        if (model_it != models.end() && model_it->second) {
            job.priority = static_cast<double>(model_it->second->getRMPriority());
            job.task_unit_energy_mJ = model_it->second->getUnitEnergy() * 1000.0;
        }
        return job;
    }

    // =====================================================
    // STBlockTickEvent 实现
    // =====================================================

    STBlockTickEvent::STBlockTickEvent(STBlockScheduler *scheduler)
        : MetaSim::Event("STBlockTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行，这样所有任务都在ready queue中
    }

    void STBlockTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ST-Block] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // STBlockWakeEvent 实现 - 深度充电唤醒定时器
    // ⭐ V74新增：在Slack=0或电池充满时唤醒系统
    // =====================================================

    STBlockWakeEvent::STBlockWakeEvent(STBlockScheduler *scheduler, MetaSim::Tick wake_time)
        : MetaSim::Event("STBlockWakeEvent", MetaSim::Event::_DEFAULT_PRIORITY + 5),
          _scheduler(scheduler),
          _wake_time(wake_time) {
        // 设置唤醒时间
        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Block] 唤醒定时器创建: wake_time=") +
                          std::to_string(static_cast<int64_t>(wake_time)) + "ms");
    }

    void STBlockWakeEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_WARNING(std::string("🔔 [ST-Block V130] ===== 唤醒定时器触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // Wake 只对齐重新评估时刻。PFPST 的释放条件必须在
        // performTickScheduling() 中基于当前能量和 slack 统一判断。
        SCHEDULER_LOG_INFO(std::string("🔔 [ST-Block] ST charge wake reached, waiting for tick reevaluation"));

        // 注意：不需要调度下一个tick，因为tick事件仍在正常运行
    }

    // 旧的按任务 EnergyCheckEvent 续期通道已移除。
    // ST-Block 现在统一在 performTickScheduling() 中做运行组续期检查与扣能。

    // =====================================================
    // STBlockTaskModel 实现
    // =====================================================

    STBlockTaskModel::STBlockTaskModel(AbsRTTask *t, int period, int wcet,
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

    STBlockTaskModel::~STBlockTaskModel() {}

    Tick STBlockTaskModel::getPriority() const {
        return _rm_priority;
    }

    void STBlockTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void STBlockTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // STBlockScheduler 实现
    // =====================================================

    STBlockScheduler::STBlockScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _dispatching_tasks_total_energy(0.0),  // ⭐ V130修复：初始化
          _selection_tick(-1),
          _selection_generation(0),
          _selection_frozen(false),
          _energy_commit_tick(-1),
          _energy_commit_generation(0),
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
          _trace_logger(nullptr),
          _semantic_trace_enabled(false),
          _energy_depleted(false),
          _alap_blocking(false),
          _deep_charging(false),
          _charge_start_time(0),
          _charge_until_slack_zero(0),
          _wake_event(nullptr),  // ⭐ V74：初始化唤醒定时器
          _st_charge_blocked_task(nullptr),
          _st_charge_required_energy(0.0),
          _st_charge_slack_at_begin(0),
          _is_charging_sleep(false),  // ⭐ V130: 深度休眠锁初始化
          _last_preempted_task(nullptr),
          _last_preempted_tick(0) {

        SCHEDULER_LOG_INFO("🚀 [ST-Block] ST-Block Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Block] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ST-Block] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // ⭐ V73修复：先读取太阳能配置（无论EnergyBridge是否成功都要读取）
        _start_time_offset = configMgr.getStartTimeOffset();
        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Block] 开始时间偏移: ") +
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
                            SCHEDULER_LOG_INFO(std::string("☀️ [ST-Block] V93: base_harvesting_rate = ") +
                                              std::to_string(_base_harvest_rate) + " J/ms (" +
                                              std::to_string(_base_harvest_rate * 1000) + " mW)");
                        }
                    }
                }

                SCHEDULER_LOG_INFO(std::string("☀️ [ST-Block] 太阳能配置: ") +
                                  "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                  " file=" + _solar_data_file +
                                  " eff=" + std::to_string(_pv_efficiency) +
                                  " area=" + std::to_string(_pv_area_m2) + "m²" +
                                  " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
            }
        } catch (const std::exception &e) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Block] 解析太阳能配置失败: ") + e.what());
        }

        // 初始化EnergyBridge并获取初始能量
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ST-Block] EnergyBridge 初始化成功");

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ST-Block] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ST-Block] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ST-Block] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new STBlockTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ST-Block] ST-Block Scheduler 初始化完成");
    }

    STBlockScheduler::STBlockScheduler(const std::vector<std::string> &params)
        : STBlockScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<STBlockScheduler>
        STBlockScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<STBlockScheduler>(params);
    }

    STBlockScheduler::~STBlockScheduler() {
        resetPersistentState();
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

    void STBlockScheduler::resetPersistentState() {
        if (_tick_event) {
            _tick_event->drop();
        }
        _first_tick_scheduled = false;
        if (_wake_event) {
            _wake_event->drop();
            delete _wake_event;
            _wake_event = nullptr;
        }

        _ready_queue.clear();
        _waiting_queue.clear();
        _running_tasks.clear();
        _energy_accounts.clear();
        _suspend_reasons.clear();
        _deadline_miss_arrivals.clear();
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _energy_deducted_tasks.clear();
        _dispatching_tasks_total_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_generation = 0;
        _selection_frozen = false;
        _energy_commit_tick = Tick(-1);
        _energy_commit_generation = 0;
        _energy_commit_valid = false;

        _energy_depleted = false;
        _alap_blocking = false;
        _deep_charging = false;
        _is_charging_sleep = false;
        _charge_start_time = Tick(0);
        _charge_until_slack_zero = Tick(0);
        _st_charge_blocked_task = nullptr;
        _st_charge_required_energy = 0.0;
        _st_charge_slack_at_begin = Tick(0);
        _last_preempted_task = nullptr;
        _last_preempted_tick = Tick(0);
    }

    void STBlockScheduler::clampCurrentEnergyNonNegative(const std::string &context) {
        const double ENERGY_EPSILON = 1e-9;
        if (_current_energy < 0.0) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Block] 负能量保护触发: ") +
                                 context +
                                 " energy=" + std::to_string(_current_energy * 1000) + " mJ");
            _current_energy = 0.0;
        } else if (_current_energy < ENERGY_EPSILON) {
            _current_energy = 0.0;
        }
    }

    MetaSim::Tick STBlockScheduler::computeSafeWakeTimeFromOffset(int64_t offset_ms) const {
        Tick current_time = SIMUL.getTime();
        int64_t safe_offset_ms = offset_ms;
        if (safe_offset_ms < 1) {
            SCHEDULER_LOG_WARNING(std::string("⏰ [ST-Block] 唤醒偏移过小，钳制到1ms: 原始偏移=") +
                                 std::to_string(offset_ms) + "ms 当前时间=" +
                                 std::to_string(static_cast<int64_t>(current_time)) + "ms");
            safe_offset_ms = 1;
        }
        return current_time + safe_offset_ms;
    }

    // =====================================================
    // 核心调度逻辑 - ALAP-Block算法的核心
    // =====================================================

    void STBlockScheduler::performTickScheduling() {
        Tick current_time = SIMUL.getTime();
        if (_selection_frozen && _selection_tick == current_time) {
            SCHEDULER_LOG_DEBUG(
                std::string("🛡️ [ST-Block] 本tick选择已冻结，跳过重复决策 @ ") +
                std::to_string(static_cast<int64_t>(current_time)) + "ms");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [ST-Block] ===== Tick ") +
                           std::to_string(static_cast<int64_t>(current_time)) + "ms =====");
        SCHEDULER_LOG_INFO("⚡ 初始能量: " + std::to_string(_current_energy * 1000) + " mJ");

        // ⭐ 每个Tick开始时清除抢占防抖标记
        // 这样在下一个tick可以正常进行抢占检查
        _last_preempted_task = nullptr;

        _stats.total_tick_count++;

        // ⭐ 关键修复：每个 Tick 开始时清除 ALAP 阻塞标志
        // 阻塞只在一个 Tick 内有效，下一个 Tick 重新评估
        _alap_blocking = false;

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
                    SCHEDULER_LOG_INFO("🔋 [ST-Block] 太阳能充电成功，恢复调度 (能量=" +
                                      std::to_string(_current_energy * 1000) + " mJ >= 阈值=" +
                                      std::to_string(RECOVERY_THRESHOLD * 1000) + " mJ)");
                }
            }
        }
        _last_tick_time = current_time;

        // ========== PFPST charging gate ==========
        if (_is_charging_sleep || _deep_charging) {
            Tick blocked_slack = _st_charge_blocked_task
                ? calculateSlackForTask(_st_charge_blocked_task)
                : calculateMinSlack();
            int64_t blocked_slack_ms = static_cast<int64_t>(blocked_slack);
            const std::string release_reason = STEnergy::chargingReleaseReason(
                _current_energy, _max_energy, blocked_slack_ms <= 0);

            if (!release_reason.empty()) {
                logSTChargeEvent("st_charge_release",
                                 _st_charge_blocked_task,
                                 _st_charge_required_energy,
                                 _st_charge_slack_at_begin,
                                 release_reason);
                _is_charging_sleep = false;
                _deep_charging = false;
                _energy_depleted = false;
                _alap_blocking = false;
                _st_charge_blocked_task = nullptr;
                _st_charge_required_energy = 0.0;
                _st_charge_slack_at_begin = 0;
                if (_wake_event) {
                    _wake_event->drop();
                    delete _wake_event;
                    _wake_event = nullptr;
                }
                SCHEDULER_LOG_INFO(std::string("🔓 [ST-Block] PFPST charge release: ") +
                                  release_reason);
            } else {
                logSTChargeEvent("st_charge_hold",
                                 _st_charge_blocked_task,
                                 _st_charge_required_energy,
                                 _st_charge_slack_at_begin);
                SCHEDULER_LOG_INFO(std::string("😴 [ST-Block] PFPST charge hold: ") +
                                  " energy=" + std::to_string(_current_energy * 1000) +
                                  "mJ slack=" + std::to_string(blocked_slack_ms) + "ms");

                if (!_kernel) {
                    _kernel = getKernel();
                }
                if (_kernel) {
                    const auto &running_tasks_map =
                        _kernel->getCurrentExecutingTasks();
                    for (const auto &[cpu, task] : running_tasks_map) {
                        (void)cpu;
                        if (task && task->isExecuting()) {
                            setSuspendReason(task, "st_charging_hold");
                            _kernel->suspend(task);
                        }
                    }
                }
                resetTickDispatchState();
                _dispatching_tasks_total_energy = 0.0;
                _selection_tick = current_time;
                _selection_generation++;
                _selection_frozen = true;
                _alap_blocking = true;
                _energy_depleted = true;
                return;
            }
        }

        // ⭐ Bug修复3：能量耗尽时跳过任务调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Block] 能量已耗尽，跳过任务调度"));
            return;
        }

        // 确保能量不超过最大容量
        if (_current_energy > _max_energy) {
            _current_energy = _max_energy;
        }

        // ========== 第1.5步：清理过期任务实例 ==========
        // ⭐ 已改用killOnMiss(true)，框架自动处理过期实例
        // cleanupExpiredTasks();

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] _kernel为nullptr，跳过本tick调度");
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
        const double epsilon = 1e-9;
        const size_t processor_count = running_tasks_map.size();
        AbsRTTask *blocking_task = nullptr;

        for (AbsRTTask *task : active_tasks) {
            if (_dispatch_selection_order.size() >= processor_count) {
                break;
            }

            const double unit_energy = getConfiguredUnitEnergyForTask(task);
            if (reserved_energy + unit_energy > _current_energy + epsilon) {
                blocking_task = task;
                _stats.total_skipped_energy++;
                break;
            }

            _dispatch_selection_order.push_back(task);
            _counted_tasks_in_dispatch.insert(task);
            reserved_energy += unit_energy;
        }

        _dispatching_tasks_total_energy = reserved_energy;
        _selection_tick = current_time;
        _selection_generation++;
        _selection_frozen = true;

        _alap_blocking = (blocking_task != nullptr);
        _energy_depleted = _dispatch_selection_order.empty() && !active_tasks.empty();
        if (blocking_task) {
            Tick slack = calculateSlackForTask(blocking_task);
            int64_t slack_ms = static_cast<int64_t>(slack);
            if (slack_ms > 0) {
                _deep_charging = true;
                _is_charging_sleep = true;
                _energy_depleted = true;
                _st_charge_blocked_task = blocking_task;
                _st_charge_required_energy =
                    getConfiguredUnitEnergyForTask(blocking_task);
                _st_charge_slack_at_begin = slack;
                const double decision_available_energy =
                    std::max(0.0, _current_energy - reserved_energy);
                logSTChargeEvent("st_charge_begin",
                                 blocking_task,
                                 _st_charge_required_energy,
                                 _st_charge_slack_at_begin,
                                 decision_available_energy);

                _dispatch_selection_order.clear();
                _counted_tasks_in_dispatch.clear();
                reserved_energy = 0.0;
                _dispatching_tasks_total_energy = 0.0;

                Tick wake_time =
                    computeSafeWakeTimeFromOffset(std::max<int64_t>(0, slack_ms));
                if (_wake_event) {
                    _wake_event->drop();
                    delete _wake_event;
                }
                _wake_event = new STBlockWakeEvent(this, wake_time);
                _wake_event->post(wake_time);
                SCHEDULER_LOG_INFO(std::string("🔒 [ST-Block] PFPST charge begin: ") +
                                   getTaskName(blocking_task) +
                                   " slack=" + std::to_string(slack_ms) + "ms");
            } else {
                _deep_charging = false;
                _is_charging_sleep = false;
            }
        } else {
            _deep_charging = false;
            _is_charging_sleep = false;
            _st_charge_blocked_task = nullptr;
            _st_charge_required_energy = 0.0;
            _st_charge_slack_at_begin = 0;
            if (_wake_event) {
                _wake_event->drop();
                delete _wake_event;
                _wake_event = nullptr;
            }
        }

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
            setSuspendReason(task, blocking_task ? "insufficient_energy" : "preemption");
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


    void STBlockScheduler::schedule() {
        // ALAP-Block依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ST-Block] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *STBlockScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [ST-Block] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // ⭐ 核心：不在这里收集能量，能量收集在tick边界完成

        if (_is_charging_sleep || _deep_charging) {
            Tick min_slack = _st_charge_blocked_task
                ? calculateSlackForTask(_st_charge_blocked_task)
                : calculateMinSlack();
            if (!STEnergy::isBatteryFull(_current_energy, _max_energy) &&
                static_cast<int64_t>(min_slack) > 0) {
                logSTChargeEvent("st_charge_hold",
                                 _st_charge_blocked_task,
                                 _st_charge_required_energy,
                                 _st_charge_slack_at_begin);
                return nullptr;
            }
        }

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [ST-Block] getFirst: 就绪队列为空");
            return nullptr;
        }

        AbsRTTask *first_task = _ready_queue.front();
        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [ST-Block] getFirst: 队列首任务为空");
            return nullptr;
        }

        // ⭐ 核心：即时能量判断（当前能量 >= 1ms能耗）
        double unit_energy = calculateUnitEnergyForTask(first_task);

        if (_current_energy < unit_energy) {
            // ⭐⭐⭐ V130修复：深度休眠锁（消灭1ms碎片化抖动） ⭐⭐⭐
            // 核心逻辑：高优先级任务能量不足时，设置全局锁，系统死睡直到充满电或Slack=0

            // 计算高优先级任务的Slack
            Tick slack = calculateSlackForTask(first_task);
            int64_t slack_ms = static_cast<int64_t>(slack);

            _alap_blocking = true;
            _energy_depleted = true;

            SCHEDULER_LOG_WARNING(std::string("🔒 [ST-Block V130] 深度休眠锁已启用！") +
                                 " 任务=" + getTaskName(first_task) +
                                 " 需要=" + std::to_string(unit_energy * 1000) + "mJ" +
                                 " 当前=" + std::to_string(_current_energy * 1000) + "mJ" +
                                 " Slack=" + std::to_string(slack_ms) + "ms");

            if (slack_ms <= 0) {
                return nullptr;
            }

            _is_charging_sleep = true;
            _deep_charging = true;
            _st_charge_blocked_task = first_task;
            _st_charge_required_energy = unit_energy;
            _st_charge_slack_at_begin = slack;
            logSTChargeEvent("st_charge_begin",
                             first_task,
                             unit_energy,
                             slack);

            // 设置唤醒定时器（Slack归零或充满电时唤醒）
            Tick current_time = SIMUL.getTime();
            double energy_needed = std::max(0.0, _max_energy - _current_energy);
            int64_t charge_time_ms =
                (_base_harvest_rate > 1e-12)
                    ? static_cast<int64_t>(std::ceil(energy_needed / _base_harvest_rate))
                    : slack_ms;
            int64_t wake_offset_ms = std::min(slack_ms, charge_time_ms);
            if (wake_offset_ms < 1) {
                wake_offset_ms = 1;
            }
            Tick wake_time = computeSafeWakeTimeFromOffset(wake_offset_ms);

            SCHEDULER_LOG_INFO(std::string("⏰ [ST-Block] PFPST charge wake:") +
                              " Slack=" + std::to_string(slack_ms) + "ms" +
                              " full_charge_time=" + std::to_string(charge_time_ms) + "ms" +
                              " wake_time=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");

            // 注册唤醒事件
            if (_wake_event) {
                _wake_event->drop();
                delete _wake_event;
            }
            _wake_event = new STBlockWakeEvent(this, wake_time);
            _wake_event->post(wake_time);

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

    AbsRTTask *STBlockScheduler::getTaskN(unsigned int n) {
        if ((!_selection_frozen || _selection_tick != SIMUL.getTime()) && !_kernel) {
            // Direct unit-test probes do not have a kernel tick to freeze the
            // selection. Keep the production path strict, but let the no-kernel
            // path rebuild a deterministic prefix so stale direct queries see
            // the newest ready set instead of an old selection.
            std::vector<AbsRTTask *> active_tasks = collectActiveJobs(SIMUL.getTime());
            sortByRMPriority(active_tasks);
            resetTickDispatchState();

            double reserved_energy = 0.0;
            const double epsilon = 1e-9;
            for (AbsRTTask *task : active_tasks) {
                const double unit_energy = getConfiguredUnitEnergyForTask(task);
                if (reserved_energy + unit_energy > _current_energy + epsilon) {
                    break;
                }
                _dispatch_selection_order.push_back(task);
                _counted_tasks_in_dispatch.insert(task);
                reserved_energy += unit_energy;
            }
            _dispatching_tasks_total_energy = reserved_energy;
            _selection_tick = SIMUL.getTime();
            _selection_generation++;
            _selection_frozen = true;
        }

        if (!_selection_frozen || _selection_tick != SIMUL.getTime()) {
            return nullptr;
        }
        if (n >= _dispatch_selection_order.size()) {
            return nullptr;
        }
        AbsRTTask *task = _dispatch_selection_order[n];
        if (!task || task->getRemainingWCET() <= 0.0 ||
            _counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
            return nullptr;
        }
        return task;
    }

    // =====================================================
    // notify - 每ms逐次扣减能耗（ALAP-Block核心逻辑）
    // =====================================================

    void STBlockScheduler::notify(AbsRTTask *task) {
        Scheduler::notify(task);

        if (!task) {
            return;
        }

        clampCurrentEnergyNonNegative(std::string("notify: ") + getTaskName(task));

        SCHEDULER_LOG_INFO(std::string("📥 [ST-Block] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void STBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ST-Block] 添加任务: ") + getTaskName(task));
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
        STBlockTaskModel *model = new STBlockTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ST-Block] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Block] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void STBlockScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        std::string task_name = getTaskName(task);
        SCHEDULER_LOG_INFO(std::string("📤 [ST-Block] 移除任务: ") + task_name);

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearPersistentTaskState(task);

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Block] 任务已移除: ") + task_name);
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void STBlockScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ST-Block] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }

        if (!_kernel) {
            _kernel = getKernel();
        }

        // Tick-level ST-BLOCK decisions are frozen in performTickScheduling().
        // Arrival-time dispatch/preemption would bypass generation checks and
        // can create stale EndDispatch effects, so arrivals only update queues.
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void STBlockScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [ST-Block] Tick-frozen语义下跳过即时抢占");
    }

    void STBlockScheduler::checkAndPreemptOnAllCPUs() {
        SCHEDULER_LOG_DEBUG("🔔 [ST-Block] Tick-frozen语义下跳过全CPU即时抢占");
        return;

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

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Block] CPU状态: 空闲=") +
                          std::to_string(truly_free_cpus) +
                          " 执行中=" + std::to_string(busy_executing) +
                          " 上下文切换中=" + std::to_string(busy_dispatching));

        // ⭐ V45修复：如果有真正空闲的CPU，不进行抢占
        // 新任务会被dispatch到空闲CPU，不需要抢占正在运行的任务
        if (truly_free_cpus > 0) {
            SCHEDULER_LOG_INFO("⏭️ [ST-Block] 有" + std::to_string(truly_free_cpus) + "个空闲CPU，跳过抢占");
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
                STBlockTaskModel *model = getTaskModel(candidate);
                if (!model) continue;
                // 如果有任务的优先级高于被挂起的任务，才允许抢占
                STBlockTaskModel *preempted_model = getTaskModel(_last_preempted_task);
                if (preempted_model && model->getRMPriority() < preempted_model->getRMPriority()) {
                    has_higher_priority = true;
                    break;
                }
            }
            if (!has_higher_priority) {
                SCHEDULER_LOG_DEBUG("⏸️ [ST-Block] 抢占防抖：跳过同tick连续抢占 " + getTaskName(_last_preempted_task));
                return;
            }
        }

        // ST-Block 是 ASAP + 固定优先级语义：
        // 只要存在更高优先级的 ready 任务且当前没有空闲CPU，就允许正常 RM 抢占。
        // 这里不引入 ALAP/Slack 紧急抢占，否则低优任务在 Slack=0 时会反复抢占高优任务，
        // 导致单核场景出现 1ms preemption chatter。
        AbsRTTask *best_candidate = nullptr;
        STBlockTaskModel *best_model = nullptr;

        for (AbsRTTask *candidate : _ready_queue) {
            if (!candidate) continue;
            CPU *cand_cpu = _kernel->getProcessor(candidate);
            if (cand_cpu != nullptr) continue;  // 已在运行

            STBlockTaskModel *model = getTaskModel(candidate);
            if (!model) continue;

            // 在所有就绪但未运行的任务中，找固定优先级最高的候选任务。
            if (!best_candidate || model->getRMPriority() < best_model->getRMPriority()) {
                best_candidate = candidate;
                best_model = model;
            }
        }

        if (!best_candidate) return;

        // 找运行中优先级最低的任务
        AbsRTTask *worst_running = nullptr;
        STBlockTaskModel *worst_model = nullptr;

        for (const auto& [cpu, task] : running_tasks_map) {
            if (!task || !task->isExecuting()) continue;
            STBlockTaskModel *model = getTaskModel(task);
            if (!model) continue;

            if (!worst_running || model->getRMPriority() > worst_model->getRMPriority()) {
                worst_running = task;
                worst_model = model;
            }
        }

        if (!worst_running || !worst_model) return;

        // ST-Block 只做固定优先级抢占：ready 中更高优任务可以抢占正在运行的较低优任务。
        bool preempt_by_priority = best_model->getRMPriority() < worst_model->getRMPriority();

        if (preempt_by_priority) {
            double unit_energy = calculateUnitEnergyForTask(best_candidate);
            if (_current_energy < unit_energy) return;

            SCHEDULER_LOG_INFO(std::string("🔄 [ST-Block] RM抢占: ") +
                              " 挂起=" + getTaskName(worst_running) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(worst_model->getRMPriority())) + ")" +
                              " 调度=" + getTaskName(best_candidate) +
                              "(优先级=" + std::to_string(static_cast<int64_t>(best_model->getRMPriority())) + ")");

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

    void STBlockScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [ST-Block] 检查运行中任务的能量状态");
        clampCurrentEnergyNonNegative("checkAndInterruptRunningTasks entry");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Block] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const double EPSILON = 1e-9;
        std::vector<AbsRTTask *> tasks_to_interrupt;

        // ⭐ V28.15修复：使用kernel的getCurrentExecutingTasks()获取实际运行中的任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Block] getCurrentExecutingTasks() 返回 ") +
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
                SCHEDULER_LOG_WARNING(std::string("⚡ [ST-Block] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Block] 任务能量充足: ") +
                                   getTaskName(task) +
                                   " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                   " 当前能量=" + std::to_string(_current_energy) + "J");
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [ST-Block] 运行任务检查完成: map大小=") +
                           std::to_string(running_tasks.size()));

        // 2. 中断能量不足的任务
        for (AbsRTTask *task : tasks_to_interrupt) {
            if (!task) {
                continue;
            }

            SCHEDULER_LOG_INFO(std::string("🛑 [ST-Block] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法中断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            setSuspendReason(task, "insufficient_energy");
            _kernel->suspend(task);

            // ⭐ V40重构：能量检查事件已删除，不再需要取消能量检查事件
            // auto it = _energy_check_events.find(task);
            // if (it != _energy_check_events.end()) {
            //     // 从map中移除，但不删除事件对象（它会自然结束）
            //     _energy_check_events.erase(it);
            //     SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Block] 已取消任务的能量检查事件: ") + getTaskName(task));
            // }

            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Block] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [ST-Block] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }

        clampCurrentEnergyNonNegative("checkAndInterruptRunningTasks exit");
    }

    bool STBlockScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        STBlockTaskModel *running_model = getTaskModel(running_task);
        STBlockTaskModel *new_model = getTaskModel(new_task);

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

    void STBlockScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ST-Block] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void STBlockScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ST-Block] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearPersistentTaskState(task);
    }

    void STBlockScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是���已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Block] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        STBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按稳定RM优先级插入：period/RM priority，再按task number打破平局。
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            if (hasHigherRMPriority(task, *it)) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        if (!_kernel) {
            _selection_frozen = false;
            _selection_tick = Tick(-1);
            resetTickDispatchState();
        }

        SCHEDULER_LOG_DEBUG(std::string("➕ [ST-Block] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void STBlockScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ST-Block] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void STBlockScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ST-Block] 任务加入等待队列: ") + getTaskName(task));
    }

    void STBlockScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool STBlockScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool STBlockScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *STBlockScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double STBlockScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        STBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double STBlockScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double STBlockScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void STBlockScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string STBlockScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void STBlockScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    void STBlockScheduler::setTraceLogger(void *trace) {
        _trace_logger = static_cast<JSONTrace *>(trace);
    }

    void STBlockScheduler::setSemanticTraceEnabled(bool enabled) {
        _semantic_trace_enabled = enabled;
    }

    void STBlockScheduler::logSTChargeEvent(const std::string &event_type,
                                            AbsRTTask *blocked_task,
                                            double required_energy,
                                            Tick slack_at_begin,
                                            const std::string &release_reason) {
        logSTChargeEvent(event_type,
                         blocked_task,
                         required_energy,
                         slack_at_begin,
                         _current_energy,
                         release_reason);
    }

    void STBlockScheduler::logSTChargeEvent(const std::string &event_type,
                                            AbsRTTask *blocked_task,
                                            double required_energy,
                                            Tick slack_at_begin,
                                            double available_energy,
                                            const std::string &release_reason) {
        if (!_trace_logger || !_semantic_trace_enabled || !blocked_task) {
            return;
        }
        std::vector<SchedulerTraceJob> blocked_jobs{
            makeSTBlockTraceJob(blocked_task, _task_models, 0)};
        _trace_logger->logSTChargeEvent(
            event_type,
            "ST-Block",
            blocked_jobs,
            available_energy * 1000.0,
            required_energy * 1000.0,
            static_cast<double>(slack_at_begin),
            release_reason);
    }

    double STBlockScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        STBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] calculateTotalEnergyForTask: 任务模型不存在");
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

    double STBlockScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ST-Block] 功率计算: ") +
                           "workload=" + workload +
                           " coeff=" + std::to_string(power_coeff) +
                           " freq=" + std::to_string(frequency_mhz) + "MHz" +
                           " freq_ratio=" + std::to_string(freq_ratio) +
                           " base_power=" + std::to_string(base_power) +
                           " → " + std::to_string(power) + "W");

        return power;
    }

    // 旧的按任务 EnergyCheckEvent 接口已移除。
    // 运行期能量处理统一由 performTickScheduling() 完成。

    // =====================================================
    // 能量收集方法
    // =====================================================

    double STBlockScheduler::collectSolarEnergy(Tick current_time) {
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

    double STBlockScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Block] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Block] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void STBlockScheduler::scheduleNextTick() {
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

    STBlockTaskModel *STBlockScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string STBlockScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    std::vector<AbsRTTask *> STBlockScheduler::collectActiveJobs(Tick current_time) {
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

    bool STBlockScheduler::hasHigherRMPriority(AbsRTTask *lhs, AbsRTTask *rhs) {
        if (lhs == rhs) {
            return false;
        }
        STBlockTaskModel *lhs_model = getTaskModel(lhs);
        STBlockTaskModel *rhs_model = getTaskModel(rhs);
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

    void STBlockScheduler::sortByRMPriority(std::vector<AbsRTTask *> &tasks) {
        std::stable_sort(tasks.begin(), tasks.end(),
                         [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                             return hasHigherRMPriority(lhs, rhs);
                         });
    }

    double STBlockScheduler::getConfiguredUnitEnergyForTask(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end() || !it->second) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    void STBlockScheduler::commitTickEnergy(Tick tick, double energy) {
        if (_energy_commit_valid &&
            _energy_commit_tick == tick &&
            _energy_commit_generation == _selection_generation) {
            throw std::logic_error("ST-Block energy committed more than once in one tick");
        }
        if (energy < 0.0 || _current_energy + 1e-9 < energy) {
            throw std::logic_error("ST-Block attempted to commit unaffordable prefix energy");
        }

        _current_energy = std::max(0.0, _current_energy - energy);
        _stats.total_energy_consumed += energy;
        _energy_commit_tick = tick;
        _energy_commit_generation = _selection_generation;
        _energy_commit_valid = true;
        clampCurrentEnergyNonNegative("commitTickEnergy");
    }

    void STBlockScheduler::cancelStaleDispatches(
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

    void STBlockScheduler::resetTickDispatchState() {
        _counted_tasks_in_dispatch.clear();
        _dispatch_selection_order.clear();
        _dispatching_tasks_total_energy = 0.0;
    }

    void STBlockScheduler::clearTaskTickSelection(AbsRTTask *task) {
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

    void STBlockScheduler::markTaskSelectedThisTick(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_counted_tasks_in_dispatch.insert(task).second) {
            _dispatch_selection_order.push_back(task);
            _dispatching_tasks_total_energy += calculateUnitEnergyForTask(task);
        }
    }

    void STBlockScheduler::clearPersistentTaskState(AbsRTTask *task) {
        if (!task) {
            return;
        }

        clearTaskTickSelection(task);
        _energy_deducted_tasks.erase(task);
        _energy_accounts.erase(task);
        clearSuspendReason(task);

        if (_last_preempted_task == task) {
            _last_preempted_task = nullptr;
            _last_preempted_tick = 0;
        }
    }

    void STBlockScheduler::accountInitialEnergyForSelectedTasks(const std::string &log_prefix) {
        for (AbsRTTask *task : _counted_tasks_in_dispatch) {
            if (_energy_deducted_tasks.find(task) != _energy_deducted_tasks.end()) {
                continue;
            }

            double unit_energy = calculateUnitEnergyForTask(task);
            _current_energy -= unit_energy;
            clampCurrentEnergyNonNegative(std::string("accountInitialEnergyForSelectedTasks: ") + getTaskName(task));
            _stats.total_energy_consumed += unit_energy;
            _energy_deducted_tasks.insert(task);

            SCHEDULER_LOG_INFO(log_prefix + getTaskName(task) +
                               " -" + std::to_string(unit_energy * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ");
        }
    }

    AbsRTTask *STBlockScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int STBlockScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *STBlockScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void STBlockScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [ST-Block] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ST-Block] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void STBlockScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ST-Block] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void STBlockScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void STBlockScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *STBlockScheduler::getKernel() {
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

    void STBlockScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ST-Block] newRun - 仿真开始");

        Scheduler::newRun();
        resetPersistentState();

        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ST-Block] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void STBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ST-Block] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ST-Block] ===== ST-Block调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void STBlockScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Block] 任务结束: ") + getTaskName(task));
        clampCurrentEnergyNonNegative(std::string("onTaskEnd: ") + getTaskName(task));

        // 从就绪队列移除
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
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
            SCHEDULER_LOG_INFO(std::string("📊 [ST-Block] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
        }
        _energy_accounts.erase(task);

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Block] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ST-BLOCK uses tick-frozen selections. Task-end events must not
        // rebuild dispatch mid-tick, otherwise stale EndDispatch can bypass
        // the next tick's prefix and energy generation checks.
    }

    bool STBlockScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 过期任务清理 - 清理超过截止期的旧任务实例
    // =====================================================

    void STBlockScheduler::cleanupExpiredTasks() {
        const Tick current_time = SIMUL.getTime();
        std::set<AbsRTTask *> jobs(_ready_queue.begin(), _ready_queue.end());
        jobs.insert(_waiting_queue.begin(), _waiting_queue.end());
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
            SCHEDULER_LOG_WARNING("⏰ [ST-Block] deadline miss recorded; job remains eligible/waiting: " +
                                  getTaskName(task));
        }
    }

    // =====================================================
    // ALAP时序门控（阶段一）
    // =====================================================

    bool STBlockScheduler::checkALAPTimingGate() {
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
                SCHEDULER_LOG_DEBUG("⏭️ [ST-Block] checkALAPTimingGate: 跳过非活跃任务");
                continue;
            }

            // ⭐ 使用try-catch保护calculateSlackForTask调用
            // 防止访问已删除的对象
            Tick slack;
            try {
                slack = calculateSlackForTask(task);
            } catch (...) {
                SCHEDULER_LOG_WARNING("⚠️ [ST-Block] checkALAPTimingGate: 计算Slack时发生异常，跳过任务");
                continue;
            }

            if (min_slack < 0 || slack < min_slack) {
                min_slack = slack;
            }
        }

        // 门控逻辑
        if (min_slack > 0) {
            SCHEDULER_LOG_INFO("⏸️  [ST-Block] ALAP时序门控：Slack > 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，强制休眠");
            _stats.total_alap_forced_idle++;
            return false;  // 强制IDLE，不调度任何任务
        } else {
            SCHEDULER_LOG_INFO("✅ [ST-Block] ALAP时序门控：Slack ≤ 0 (" +
                               std::to_string(static_cast<int64_t>(min_slack)) + "ms)，唤醒，允许调度");
            return true;  // 门控通过，允许调度
        }
    }

    MetaSim::Tick STBlockScheduler::calculateSlackForTask(AbsRTTask *task) {
        if (!task) return MetaSim::Tick(0);

        Tick current_time = SIMUL.getTime();
        Tick absolute_deadline = task->getDeadline();

        double remaining_double = task->getRemainingWCET();

        // ⭐ V76关键修复：处理剩余时间为负的情况
        // 原因：当任务被suspend时，execdTime可能被累加导致超过WCET
        // 修复：剩余时间最小为0（任务已完成或超时）
        if (remaining_double < 0) {
            remaining_double = 0;
        }

        Tick remaining = Tick(static_cast<Tick::impl_t>(std::ceil(remaining_double)));
        Tick slack = absolute_deadline - remaining - current_time;

        // ⭐ V76调试：使用INFO级别输出详细信息
        SCHEDULER_LOG_INFO("🧮 [ST-Block] Slack计算: " +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining_double=" + std::to_string(remaining_double) +
                           " remaining_int=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " => slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        SCHEDULER_LOG_DEBUG("🧮 [ST-Block] Slack计算: " +
                           getTaskName(task) +
                           " deadline=" + std::to_string(static_cast<int64_t>(absolute_deadline)) +
                           " remaining=" + std::to_string(static_cast<int64_t>(remaining)) +
                           " current=" + std::to_string(static_cast<int64_t>(current_time)) +
                           " => slack=" + std::to_string(static_cast<int64_t>(slack)) + "ms");

        return slack;
    }

    // ⭐ ST特有：计算所有就绪任务的最小Slack
    MetaSim::Tick STBlockScheduler::calculateMinSlack() {
        // ⭐ V72修复：使用MAXTICK代替std::numeric_limits<Tick>::max()
        // Tick是自定义类，std::numeric_limits不正确
        int64_t min_slack_value = INT64_MAX;
        bool found_valid_task = false;

        SCHEDULER_LOG_INFO(std::string("🧮 [ST-Block] calculateMinSlack: 就绪队列大小=") +
                          std::to_string(_ready_queue.size()));

        // 检查就绪队列中所有任务的Slack
        for (auto* task : _ready_queue) {
            if (!task) continue;
            if (!task->isActive()) {
                SCHEDULER_LOG_INFO(std::string("🧮 [ST-Block] 跳过不活跃任务: ") + getTaskName(task));
                continue;
            }
            Tick slack = calculateSlackForTask(task);
            int64_t slack_value = static_cast<int64_t>(slack);
            SCHEDULER_LOG_INFO(std::string("🧮 [ST-Block] 任务Slack: ") +
                              getTaskName(task) + " Slack=" +
                              std::to_string(slack_value) + "ms");
            if (slack_value < min_slack_value) {
                min_slack_value = slack_value;
                found_valid_task = true;
            }
        }

        Tick min_slack;
        if (!found_valid_task) {
            // 没有活跃任务，返回一个大值让系统继续充电
            // 但不超过最大充电时间（到电池充满）
            double energy_to_full = _max_energy - _current_energy;
            double harvest_rate = 0.008;  // mJ/ms
            int64_t charge_time_ms = static_cast<int64_t>(energy_to_full * 1000 / harvest_rate) + 1;
            min_slack = Tick(charge_time_ms);
            SCHEDULER_LOG_INFO(std::string("🧮 [ST-Block] 没有活跃任务，返回充电时间: ") +
                              std::to_string(charge_time_ms) + "ms");
        } else {
            min_slack = Tick(min_slack_value);
        }

        SCHEDULER_LOG_INFO("🧮 [ST-Block] calculateMinSlack: min_slack=" +
                           std::to_string(static_cast<int64_t>(min_slack)) + "ms");
        return min_slack;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void STBlockScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ST-Block] ===== ST-Block调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO(std::string("  ST深度充电次数: ") + std::to_string(_stats.total_alap_forced_idle));
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string STBlockScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> STBlockScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

} // namespace RTSim
