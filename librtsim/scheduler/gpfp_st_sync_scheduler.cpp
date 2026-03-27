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
        : MetaSim::Event("STSyncTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // 与 ST-Block / ST-NonBlock 对齐：tick 优先级低于 arrival，
        // 确保 0ms 到达任务先入队，再进行首个同步批次调度。
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
        SCHEDULER_LOG_WARNING(std::string("⏰ [ST-Sync V130] 组唤醒事件触发 @ ") +
                             std::to_string(static_cast<int64_t>(current_time)) + "ms");

        _valid = false;

        // 唤醒事件只负责把“重新评估时刻”对齐到当前 tick。
        // 真正的解锁、等待队列回流与批次重建都放在 performTickScheduling() 内统一完成，
        // 避免 wake event 在 tick 之前抢先清锁，导致等待中的同步组丢失窗口语义。
        SCHEDULER_LOG_INFO(std::string("🔔 [ST-Sync V130] 唤醒时刻已到，等待同tick统一解锁与重建同步组"));
    }

    void STSyncGroupWakeEvent::schedule(MetaSim::Tick wake_time) {
        _wake_time = wake_time;
        _valid = true;

        // 注册新事件到模拟器事件队列
        post(wake_time);

        SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync V122] 唤醒事件已注册: ") +
                          "唤醒时间=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
    }

    // 兼容旧接口的空壳事件。
    // 当前 ST-Sync 的能量记账完全在 tick 级批量逻辑中完成。

    STSyncEnergyCheckEvent::STSyncEnergyCheckEvent(STSyncScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("STSyncEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // 当前实现没有按任务运行时能量事件；能量在 tick 级批量逻辑中统一处理。
    }

    void STSyncEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

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
          _group_wake_event(nullptr),  // ⭐ V131修复：初始化为nullptr
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
          _is_charging_sleep(false),  // ⭐ V130: 深度休眠锁初始化
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

        // 每个物理 tick 都重新开始一轮批次审批。
        // 旧 tick 批准的同步组不能跨 tick 泄漏到新的 dispatch 回合里继续被消费。
        _batch_scheduled_this_tick = false;
        _current_batch_size = 0;
        _preempt_batch_tasks.clear();

        // ========== V130: 深度休眠锁检查（消灭1ms碎片化抖动） ==========
        // ⭐ 核心逻辑：缺电后锁住整个同步组；只有阻塞组自然消亡或电池充满才真正解锁。
        // Slack 归零只意味着“停止继续延期充电”，不意味着允许用残余能量做 1ms 脉冲式重调度。
        if (_is_charging_sleep) {
            Tick min_slack = calculateMinSlack();
            int64_t min_slack_ms = static_cast<int64_t>(min_slack);

            if (_waiting_queue.empty()) {
                _is_charging_sleep = false;
                _deep_charging = false;
                _energy_depleted = false;
                if (_group_wake_event) {
                    _group_wake_event->invalidate();
                }
                SCHEDULER_LOG_INFO("🧹 [ST-Sync V130] 阻塞同步组已清空，释放充电休眠锁");
            }
            else if (_current_energy >= _max_energy - 0.000001) {
                _is_charging_sleep = false;
                _deep_charging = false;
                _energy_depleted = false;
                if (_group_wake_event) {
                    _group_wake_event->invalidate();
                }
                SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync V130] 深度休眠解锁：电池充满") +
                                  " 能量=" + std::to_string(_current_energy * 1000) + "mJ" +
                                  "（保留阻塞同步组在waiting_queue，优先恢复原组）");
            }
            else if (min_slack_ms <= 0) {
                if (_group_wake_event) {
                    _group_wake_event->invalidate();
                }
                SCHEDULER_LOG_WARNING(std::string("⏳ [ST-Sync V130] 阻塞同步组Slack已耗尽，但旧组尚未自然清空") +
                                     " Slack=" + std::to_string(min_slack_ms) + "ms" +
                                     " waiting=" + std::to_string(_waiting_queue.size()) +
                                     " energy=" + std::to_string(_current_energy * 1000) + "mJ" +
                                     "（继续保持charging wall，等待旧组miss/kill或电池充满）");
                return;
            }
            else {
                SCHEDULER_LOG_INFO(std::string("😴 [ST-Sync V130] 深度休眠中...") +
                                  " 能量=" + std::to_string(_current_energy * 1000) + "mJ" +
                                  " Slack=" + std::to_string(min_slack_ms) + "ms");
                return;
            }
        }

        if (!_deferred_arrivals.empty()) {
            std::vector<AbsRTTask *> deferred(_deferred_arrivals.begin(), _deferred_arrivals.end());
            _deferred_arrivals.clear();
            for (AbsRTTask *task : deferred) {
                if (!task || !task->isActive()) {
                    continue;
                }
                if (isInWaitingQueue(task) || isInReadyQueue(task)) {
                    continue;
                }
                addToReadyQueue(task);
            }
            SCHEDULER_LOG_INFO(std::string("📥 [ST-Sync] tick边界吸纳延后到达任务: count=") +
                              std::to_string(deferred.size()));
        }

        // ⭐ Bug修复3：能量耗尽时跳过调度（但已经收集了太阳能）
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [ST-Sync] 能量已耗尽，跳过Tick调度"));
            return;  // 不进行任何调度，包括中断检查
        }

        // Tick 入口不再做额外扣电；
        // ST-Sync 的能量记账由下面的批量准入/续期路径统一处理。

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

            // 深度充电检查改为使用当前原子批次下一毫秒总能耗
            for (const auto& map_pair : running_tasks) {
                if (map_pair.second && map_pair.second->isExecuting() &&
                    _tasks_completed_wcet.find(map_pair.second) == _tasks_completed_wcet.end()) {
                    AbsRTTask* task = map_pair.second;
                    double unit_energy = calculateUnitEnergyForTask(task);
                    total_energy_needed += unit_energy;
                }
            }

            for (int i = 0; i < new_tasks_needed && i < ready_count; ++i) {
                if (sorted_ready[i] && sorted_ready[i]->isActive()) {
                    AbsRTTask* task = sorted_ready[i];
                    double unit_energy = calculateUnitEnergyForTask(task);
                    total_energy_needed += unit_energy;
                }
            }

            SCHEDULER_LOG_INFO(std::string("🔋 [ST-Sync Atomic] 深度充电中... Slack=") +
                              std::to_string(min_slack_ms) + "ms " +
                              "能量=" + std::to_string(_current_energy * 1000) + "mJ " +
                              "K=" + std::to_string(K) +
                              " 1ms预算=" + std::to_string(total_energy_needed * 1000) + "mJ");

            // 唤醒条件：阻塞组Slack到期、阻塞组已全部自然消亡，或电池充满。
            // Slack<=0 时结束本轮充电窗口，但不把旧阻塞组回流到 ready queue，
            // 这样旧组仍等待真实 deadline miss / kill，同时允许系统重新评估新实例形成的新同步组。
            if (min_slack_ms <= 0) {
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：阻塞组Slack到期，重新评估新同步组");
                _deep_charging = false;
                _energy_depleted = false;
            } else if (_waiting_queue.empty()) {
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：阻塞同步组已清空");
                _deep_charging = false;
                _energy_depleted = false;
            } else if (_current_energy >= _max_energy - 0.000001) {
                SCHEDULER_LOG_INFO("🔋 [ST-Sync] 深度充电结束：电池充满，恢复阻塞同步组的重新评估");
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
        
        // ========== Step 1: 收集候选任务（ST-Sync：优先恢复阻塞同步组）==========
        // 先按 waiting_queue 恢复被阻塞的同步组；只有当其成员自然消亡后，新的实例才会填补空位。
        std::vector<AbsRTTask*> ready_tasks_v90;
        if (!_waiting_queue.empty()) {
            for (auto* task : _waiting_queue) {
                if (!task || !task->isActive()) continue;

                bool is_running = false;
                for (auto* rt : running_task_list) {
                    if (task == rt) { is_running = true; break; }
                }
                if (is_running) continue;

                ready_tasks_v90.push_back(task);
            }
            SCHEDULER_LOG_INFO(std::string("🧱 [ST-Sync Atomic] 当前存在阻塞同步组，优先恢复 waiting_queue 任务: count=") +
                              std::to_string(ready_tasks_v90.size()));
        } else {
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

            for (auto* task : sorted_ready_v90) {
                if (!task || !task->isActive()) continue;

                bool is_running = false;
                for (auto* rt : running_task_list) {
                    if (task == rt) { is_running = true; break; }
                }
                if (is_running) continue;

                ready_tasks_v90.push_back(task);
            }
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

        // ⭐ V128修复：只在有变化时才重新评估 V92 批量调度！
        // 问题根源：V92 批量调度在每次 Tick 都会重新评估，导致碎片化抖动
        // 修复：只有当没有运行任务或有新事件时才重新评估
        bool has_running_tasks = !running_task_list.empty();
        bool has_ready_tasks = !ready_tasks_v90.empty();
        bool should_reevaluate = (!has_running_tasks) ||  // 没有运行任务时需要评估
                                  _energy_depleted ||      // 能量耗尽时需要评估
                                  _deep_charging;          // 深度充电状态变化时需要评估
        int available_free_cpus_v90 = total_cpus - static_cast<int>(running_task_list.size());

        if (has_running_tasks && !_energy_depleted && !_deep_charging &&
            (available_free_cpus_v90 <= 0 || !has_ready_tasks)) {
            double running_batch_energy = calculateBatchUnitEnergy(running_task_list);
            const double EPSILON_V90_RUNNING = 1e-9;

            if (_current_energy + EPSILON_V90_RUNNING < running_batch_energy) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync Atomic] 运行中同步组续期能量不足，整组挂起") +
                                     " 需要=" + std::to_string(running_batch_energy * 1000) + " mJ" +
                                     " 当前=" + std::to_string(_current_energy * 1000) + " mJ");
                suspendBatchForInsufficientEnergy(running_task_list,
                                                 running_batch_energy,
                                                 std::string("running batch renewal @ ") + std::to_string(static_cast<int64_t>(current_time)) + "ms");
                return;
            }

            double old_energy = _current_energy;
            _current_energy -= running_batch_energy;
            _stats.total_energy_consumed += running_batch_energy;
            clampCurrentEnergyNonNegative(std::string("running batch renew @ ") + std::to_string(static_cast<int64_t>(current_time)) + "ms");

            SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 组级统一扣除续期1ms能量: ") +
                              "任务数=" + std::to_string(running_task_list.size()) +
                              " 扣除=" + std::to_string(running_batch_energy * 1000) + " mJ" +
                              " " + std::to_string(old_energy * 1000) + " → " +
                              std::to_string(_current_energy * 1000) + " mJ");
            return;
        }

        // ========== Step 4: 计算候选原子批次的 1ms 总能量 ==========
        double total_energy_budget = 0.0;  // 原子批次下一毫秒总能耗
        std::vector<AbsRTTask*> k_tasks_v90;

        int running_cnt_v90 = static_cast<int>(running_task_list.size());
        int free_cpus_v90 = total_cpus - running_cnt_v90;

        for (auto* task : running_task_list) {
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }

            k_tasks_v90.push_back(task);
            double unit_energy = calculateUnitEnergyForTask(task);
            total_energy_budget += unit_energy;

            SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync Atomic] 运行任务 1ms 预算: ") +
                               getTaskName(task) +
                               " 单位=" + std::to_string(unit_energy * 1000) + "mJ/ms");
        }

        int num_new_tasks = std::min(K_v90, free_cpus_v90);
        for (int i = 0; i < num_new_tasks && i < static_cast<int>(ready_tasks_v90.size()); ++i) {
            auto* task = ready_tasks_v90[i];
            k_tasks_v90.push_back(task);

            double unit_energy = calculateUnitEnergyForTask(task);
            total_energy_budget += unit_energy;

            SCHEDULER_LOG_DEBUG(std::string("📊 [ST-Sync Atomic] 新任务 1ms 预算: ") +
                               getTaskName(task) +
                               " 单位=" + std::to_string(unit_energy * 1000) + "mJ/ms");
        }

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync Atomic] 批量决策: ") +
                          "运行中=" + std::to_string(running_task_list.size()) +
                          " 就绪=" + std::to_string(ready_tasks_v90.size()) +
                          " K(新任务)=" + std::to_string(K_v90) +
                          " 可调度=" + std::to_string(num_new_tasks) +
                          " 最短Slack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");

        SCHEDULER_LOG_INFO(std::string("📊 [ST-Sync Atomic] 能量预算(1ms): ") +
                          "K个任务=" + std::to_string(k_tasks_v90.size()) +
                          " 总预算=" + std::to_string(total_energy_budget * 1000) + " mJ" +
                          " 当前=" + std::to_string(_current_energy * 1000) + " mJ");

        // ========== Step 5: All-or-Nothing决策（ST特有充电逻辑）==========
        if (_current_energy >= total_energy_budget - EPSILON_V90) {
            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync Atomic] 能量充足，整组调度"));

            _current_batch_tasks = k_tasks_v90;
            _current_batch_size = k_tasks_v90.size();
            _batch_scheduled_this_tick = true;
            _stats.total_batch_schedules++;

            if (!_current_batch_tasks.empty()) {
                double batch_energy_to_deduct = calculateBatchUnitEnergy(_current_batch_tasks);
                if (batch_energy_to_deduct > EPSILON_V90) {
                    double old_energy = _current_energy;
                    _current_energy -= batch_energy_to_deduct;
                    _stats.total_energy_consumed += batch_energy_to_deduct;
                    clampCurrentEnergyNonNegative(std::string("batch dispatch deduct @ ") + std::to_string(static_cast<int64_t>(current_time)) + "ms");

                    SCHEDULER_LOG_INFO(std::string("⚡ [ST-Sync] 组级统一扣除首个1ms能量: ") +
                                      "任务数=" + std::to_string(_current_batch_tasks.size()) +
                                      " 扣除=" + std::to_string(batch_energy_to_deduct * 1000) + " mJ" +
                                      " " + std::to_string(old_energy * 1000) + " → " +
                                      std::to_string(_current_energy * 1000) + " mJ");
                }
            }

            _deep_charging = false;
            _energy_depleted = false;
            _is_charging_sleep = false;

            SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync Atomic] 批量调度成功，跳过旧代码路径") +
                              " 批量任务数=" + std::to_string(_current_batch_tasks.size()));

            if (_kernel && !_current_batch_tasks.empty()) {
                SCHEDULER_LOG_INFO(std::string("🚀 [ST-Sync Atomic] 调用dispatch") +
                                  " 批量任务数=" + std::to_string(_current_batch_tasks.size()));
                _kernel->dispatch();
            }
            return;

        } else {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync Atomic] 能量不足，整组拒绝派发") +
                                 " 需要=" + std::to_string(total_energy_budget * 1000) + " mJ" +
                                 " 当前=" + std::to_string(_current_energy * 1000) + " mJ" +
                                 " 最短Slack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");

            suspendBatchForInsufficientEnergy(k_tasks_v90,
                                             total_energy_budget,
                                             std::string("dispatch gate @ ") + std::to_string(static_cast<int64_t>(current_time)) + "ms");
            return;
        }

        // 旧的 V9x/V12x fallback 调度路径已删除。
        // ST-Sync 现在只保留上面的单一原子批次决策与 dispatch 通道。
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
        if (_energy_depleted && _current_energy < ENERGY_EPSILON) {
            SCHEDULER_LOG_INFO("💀 [ST-Sync] getTaskN: 能量已耗尽");
            return nullptr;
        }

        if (_is_charging_sleep || _deep_charging) {
            SCHEDULER_LOG_INFO("🔋 [ST-Sync] getTaskN: 充电休眠中，拒绝调度");
            return nullptr;
        }

        if (!_batch_scheduled_this_tick || _current_batch_tasks.empty()) {
            SCHEDULER_LOG_INFO("📭 [ST-Sync] getTaskN: 当前没有已批准的同步组");
            return nullptr;
        }

        std::vector<AbsRTTask *> stable_batch;
        stable_batch.reserve(_current_batch_tasks.size());
        for (AbsRTTask *task : _current_batch_tasks) {
            if (!task || !task->isActive()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            stable_batch.push_back(task);
        }

        std::sort(stable_batch.begin(), stable_batch.end(),
            [this](AbsRTTask *a, AbsRTTask *b) {
                auto model_a = getTaskModel(a);
                auto model_b = getTaskModel(b);
                if (model_a && model_b) {
                    return model_a->getRMPriority() < model_b->getRMPriority();
                }
                return false;
            });

        if (n >= stable_batch.size()) {
            SCHEDULER_LOG_INFO(std::string("📭 [ST-Sync] getTaskN(") + std::to_string(n) +
                               ") 批量中没有更多任务，batch_size=" + std::to_string(stable_batch.size()));
            return nullptr;
        }

        AbsRTTask *task = stable_batch[n];
        if (!task) {
            SCHEDULER_LOG_INFO(std::string("📭 [ST-Sync] getTaskN(") + std::to_string(n) +
                               ") 目标任务为空");
            return nullptr;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ST-Sync] getTaskN(") + std::to_string(n) +
                           ") 返回已批准批量任务: " + getTaskName(task));
        return task;
    }

    // =====================================================
    // notify - ST-Sync不再扣减能量（已在批量时扣减）
    // =====================================================

    void STSyncScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        if (_kernel && _kernel->getProcessor(task) != nullptr) {
            SCHEDULER_LOG_DEBUG(std::string("⏭️ [ST-Sync] notify: 跳过运行中任务: ") + getTaskName(task));
            return;
        }

        if (_is_charging_sleep || _deep_charging) {
            if (std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task) == _deferred_arrivals.end()) {
                _deferred_arrivals.push_back(task);
            }
            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] notify: 充电窗口内延后吸纳任务到下一tick: ") + getTaskName(task));
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
        clearPersistentTaskState(task);
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

        // ⭐ ST-Sync：新到达任务也必须服从 tick 边界统一准入。
        // 缺电充电窗口期间，不允许新实例在 tick 中途直接改写 ready_group。
        // 旧阻塞组由 waiting_queue 保持；新实例暂时只留在调度器基类容器，等待下一 tick 统一吸纳。
        Scheduler::insert(task);

        bool suspended_for_energy = (getSuspendReason(task) == "insufficient_energy");
        bool in_charging_window = (_is_charging_sleep || _deep_charging);

        if (suspended_for_energy) {
            addToWaitingQueue(task);
        } else if (in_charging_window) {
            removeFromReadyQueue(task);
            removeFromWaitingQueue(task);
            if (std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task) == _deferred_arrivals.end()) {
                _deferred_arrivals.push_back(task);
            }
            SCHEDULER_LOG_INFO(std::string("⏸️ [ST-Sync] charging窗口内延后吸纳新实例到下一tick: ") +
                              getTaskName(task));
        } else {
            addToReadyQueue(task);
        }

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

    void STSyncScheduler::clearPersistentTaskState(AbsRTTask *task) {
        if (!task) {
            return;
        }

        removeFromWaitingQueue(task);
        clearSuspendReason(task);
    }

    void STSyncScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ST-Sync] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        clearPersistentTaskState(task);
        removeFromReadyQueue(task);
        auto deferred_it = std::find(_deferred_arrivals.begin(), _deferred_arrivals.end(), task);
        if (deferred_it != _deferred_arrivals.end()) {
            _deferred_arrivals.erase(deferred_it);
        }
        stopEnergyCheckForTask(task);
        _tasks_completed_wcet.erase(task);
        auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
        if (batch_it != _current_batch_tasks.end()) {
            _current_batch_tasks.erase(batch_it);
        }
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

        if (_kernel && _kernel->getProcessor(task) != nullptr) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ST-Sync] 任务已在CPU上运行，跳过重新入队: ") + getTaskName(task));
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
        if (isInWaitingQueue(task)) {
            return;
        }

        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ST-Sync] 任务加入等待队列: ") + getTaskName(task));
    }

    void STSyncScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    void STSyncScheduler::promoteWaitingTasksToReadyQueue(const std::string &context) {
        if (_waiting_queue.empty()) {
            return;
        }

        std::vector<AbsRTTask *> waiting_tasks(_waiting_queue.begin(), _waiting_queue.end());
        _waiting_queue.clear();

        for (AbsRTTask *task : waiting_tasks) {
            if (!task || !task->isActive()) {
                continue;
            }
            addToReadyQueue(task);
        }

        SCHEDULER_LOG_INFO(std::string("🔓 [ST-Sync] 充电窗口结束，等待队列任务回到就绪队列: ") +
                          context +
                          " count=" + std::to_string(waiting_tasks.size()));
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

    void STSyncScheduler::clampCurrentEnergyNonNegative(const std::string &context) {
        const double ENERGY_EPSILON = 1e-9;

        if (_current_energy < 0.0) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 负能量保护触发: ") +
                                 context +
                                 " energy=" + std::to_string(_current_energy * 1000.0) + " mJ");
            _current_energy = 0.0;
        } else if (_current_energy < ENERGY_EPSILON) {
            _current_energy = 0.0;
        }
    }

    std::vector<AbsRTTask *> STSyncScheduler::collectActiveRunningBatchTasks() {
        std::vector<AbsRTTask *> running_batch;

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return running_batch;
        }

        const auto &running_tasks = _kernel->getCurrentExecutingTasks();
        for (const auto &map_pair : running_tasks) {
            AbsRTTask *task = map_pair.second;
            if (!task || !task->isExecuting()) {
                continue;
            }
            if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
                continue;
            }
            running_batch.push_back(task);
        }

        return running_batch;
    }

    bool STSyncScheduler::isTaskInActiveRunningBatch(AbsRTTask *task) {
        if (!task) {
            return false;
        }

        if (_tasks_completed_wcet.find(task) != _tasks_completed_wcet.end()) {
            return false;
        }

        if (!_kernel) {
            _kernel = getKernel();
        }
        if (!_kernel) {
            return false;
        }

        CPU *cpu = _kernel->getProcessor(task);
        if (!cpu) {
            return false;
        }

        const auto &running_tasks = _kernel->getCurrentExecutingTasks();
        auto it = running_tasks.find(cpu);
        if (it == running_tasks.end() || it->second != task) {
            return false;
        }

        return task->isExecuting();
    }

    double STSyncScheduler::calculateBatchUnitEnergy(const std::vector<AbsRTTask *> &tasks) {
        double total_unit_energy = 0.0;
        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            total_unit_energy += calculateUnitEnergyForTask(task);
        }
        return total_unit_energy;
    }

    void STSyncScheduler::suspendBatchForInsufficientEnergy(const std::vector<AbsRTTask *> &tasks,
                                                            double required_energy,
                                                            const std::string &context) {
        Tick min_slack = std::numeric_limits<Tick::impl_t>::max();

        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            Tick task_slack = calculateSlackForTask(task);
            if (task_slack < min_slack) {
                min_slack = task_slack;
            }
        }

        if (min_slack == std::numeric_limits<Tick::impl_t>::max()) {
            min_slack = 0;
        }

        SCHEDULER_LOG_WARNING(std::string("⚠️ [ST-Sync] 批次原子挂起: ") +
                             context +
                             " tasks=" + std::to_string(tasks.size()) +
                             " need=" + std::to_string(required_energy * 1000.0) + " mJ" +
                             " current=" + std::to_string(_current_energy * 1000.0) + " mJ" +
                             " minSlack=" + std::to_string(static_cast<int64_t>(min_slack)) + "ms");

        _deep_charging = true;
        _energy_depleted = true;
        _is_charging_sleep = true;
        clampCurrentEnergyNonNegative("suspendBatchForInsufficientEnergy");

        Tick wake_time = calculateGroupWakeTime(min_slack, required_energy);
        if (wake_time > SIMUL.getTime()) {
            scheduleGroupWakeEvent(wake_time);
        }

        for (auto *task : tasks) {
            if (!task) {
                continue;
            }
            addToWaitingQueue(task);
            if (_kernel && task->isExecuting()) {
                stopEnergyCheckForTask(task);
                setSuspendReason(task, "insufficient_energy");
                SCHEDULER_LOG_WARNING(std::string("🛑 [ST-Sync] 原子挂起任务: ") + getTaskName(task));
                _kernel->suspend(task);
            }
        }

        _current_batch_tasks.clear();
        _preempt_batch_tasks.clear();
        _current_batch_size = 0;
        _batch_scheduled_this_tick = false;
        _stats.total_batch_skipped++;
    }

    // 兼容旧调用点；当前 ST-Sync 不启动按任务运行时能量事件。

    void STSyncScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        (void)task;
        (void)cpu;
        return;
    }

    void STSyncScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            _energy_check_events.erase(it);
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
        _deferred_arrivals.clear();
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
        _preempt_batch_tasks.clear();

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

        // MRTKernel::onEnd() 已先调用 extract()；这里不要再次清理 ready/waiting，
        // 否则周期复用对象会在“新实例已到达、旧实例随后kill/end”时误删新实例状态。

        // ⭐ 逐渐扣除模式：从计数集合中移除任务
        _counted_tasks_in_dispatch.erase(task);
        _tasks_completed_wcet.erase(task);

        // ⭐ 停止能量检查事件
        stopEnergyCheckForTask(task);

        auto batch_it = std::find(_current_batch_tasks.begin(), _current_batch_tasks.end(), task);
        if (batch_it != _current_batch_tasks.end()) {
            _current_batch_tasks.erase(batch_it);
        }

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

        // ST-Sync 的组装与准入只在 tick 边界进行。
        // 任务中途结束后保持 CPU 空闲，等待下一个 tick 统一重建同步组。
        if (!_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("⏭️ [ST-Sync] 任务结束后不做 mid-tick dispatch，等待下一 tick 统一调度");
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

        // 充电窗口内优先只看等待中的同步组，避免组外任务改变当前窗口的唤醒时机。
        if (!_waiting_queue.empty()) {
            for (auto* task : _waiting_queue) {
                if (!task) continue;
                Tick slack = calculateSlackForTask(task);
                if (slack < min_slack) {
                    min_slack = slack;
                }
            }
        } else {
            for (auto* task : _ready_queue) {
                if (!task) continue;
                Tick slack = calculateSlackForTask(task);
                if (slack < min_slack) {
                    min_slack = slack;
                }
            }
        }

        // 如果没有任务，返回0
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

        // 计算充满电需要的时间。
        // ST-Sync 缺电后进入整组充电窗口，唤醒条件是组Slack归零或电池充满，
        // 不是“只要够下一拍就提前放行”。
        double energy_needed = _max_energy - _current_energy;
        if (energy_needed < 0) energy_needed = 0;

        double harvest_rate = _base_harvest_rate;
        int64_t charge_time_ms = (harvest_rate > 1e-12)
            ? static_cast<int64_t>(std::ceil(energy_needed / harvest_rate))
            : static_cast<int64_t>(group_slack);

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
        // 只在没有定时器或新唤醒时间更早时更新，避免重复无效重建
        bool should_update = false;
        if (!_group_wake_event || !_group_wake_event->isValid()) {
            should_update = true;
        } else {
            const int64_t existing_wake_time = static_cast<int64_t>(_group_wake_event->getWakeTime());
            const int64_t new_wake_time = static_cast<int64_t>(wake_time);
            if (new_wake_time < existing_wake_time) {
                should_update = true;
                SCHEDULER_LOG_INFO(std::string("⏰ [ST-Sync] 发现更早的组唤醒时间: ") +
                                  "现有=" + std::to_string(existing_wake_time) + "ms " +
                                  "新=" + std::to_string(new_wake_time) + "ms，更新");
            }
        }

        if (!should_update) {
            SCHEDULER_LOG_DEBUG(std::string("⏰ [ST-Sync] 保留现有组唤醒定时器: ") +
                               "现有=" + std::to_string(static_cast<int64_t>(_group_wake_event->getWakeTime())) + "ms " +
                               "新=" + std::to_string(static_cast<int64_t>(wake_time)) + "ms");
            return;
        }

        if (_group_wake_event) {
            _group_wake_event->invalidate();
        }

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

        const std::vector<AbsRTTask *> running_batch = collectActiveRunningBatchTasks();
        if (running_batch.empty()) {
            return;
        }

        const double required_energy = calculateBatchUnitEnergy(running_batch);
        const double EPSILON = 1e-9;

        if (_current_energy + EPSILON < required_energy) {
            suspendBatchForInsufficientEnergy(running_batch,
                                             required_energy,
                                             std::string("checkAndInterruptRunningTasks @ ") +
                                                 std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms");
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("✅ [ST-Sync] 运行批次下一毫秒能量充足: ") +
                           "tasks=" + std::to_string(running_batch.size()) +
                           " need=" + std::to_string(required_energy * 1000.0) + " mJ" +
                           " current=" + std::to_string(_current_energy * 1000.0) + " mJ");
    }
} // namespace RTSim
