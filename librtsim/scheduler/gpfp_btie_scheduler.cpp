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
        : MetaSim::Event("BTIETickEvent", MetaSim::Event::_DEFAULT_PRIORITY - 10),
          _scheduler(scheduler) {
        // 高优先级事件，确保tick调度及时执行
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


        // ⭐ 能量检查事件只负责扣除能量，不负责中断
        // 中断由tick事件���一处理，避免重复中断
        // ⭐ V29修复：能量检查事件不再扣除能量
        // 能量扣除已移到tick事件中，避免重复扣除和时序问题
        SCHEDULER_LOG_DEBUG(std::string("⚡ [BTIE] 能量检查事件（仅记录，不扣除）: ") +
                           "任务=" + _scheduler->getTaskName(_task) + " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " 当前=" + std::to_string(_scheduler->getCurrentEnergy() * 1000) + " mJ");

        // 重新调度下一次能量检查
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
          _kernel(nullptr),
          _batch_scheduled_this_tick(false),
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
        // ⭐ 修复：批量调度不再扣减能量
        // 能量应该在任务被调度时（notify方法）扣减，而不是在批量调度决策时扣减
        // 这里只记录批量调度的任务列表
        SCHEDULER_LOG_DEBUG(std::string("📋 [BTIE] 批量调度决策: ") +
                           "任务数=" + std::to_string(tasks.size()) +
                           " 总能量=" + std::to_string(total_energy) + "J" +
                           " （能量将在notify时扣减）");
    }

    // =====================================================
    // 核心调度逻辑 - BTIE批量调度算法
    // =====================================================

    void BTIEScheduler::performTickScheduling() {
        SCHEDULER_LOG_DEBUG(std::string("🔄 [BTIE] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");

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

        // ⭐ 运行时能量检查：中断能量不足的任务
        checkAndInterruptRunningTasks();

        // ⭐ BTIE批量调度决策：使用当前能量（已包含收集的能量）
        // 计算批量大小k
        int batch_size = calculateBatchSize();
        _current_batch_size = batch_size;

        SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 批量大小k=") + std::to_string(batch_size) +
                          " 就绪队列大小=" + std::to_string(_ready_queue.size()));

        if (batch_size <= 0 || _ready_queue.empty()) {
            SCHEDULER_LOG_INFO("📭 [BTIE] 无任务可调度");
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            checkAndPreempt();
            return;
        }

        // 计算前k个任务的总能耗
        double total_batch_energy = 0.0;
        std::vector<AbsRTTask *> batch_tasks;
        for (int i = 0; i < batch_size && i < static_cast<int>(_ready_queue.size()); ++i) {
            AbsRTTask *task = _ready_queue[i];
            if (task) {
                // ⭐ 修复：只计算1ms的能耗，而不是任务的总能耗
                double unit_energy = calculateUnitEnergyForTask(task);
                total_batch_energy += unit_energy;
                batch_tasks.push_back(task);

                SCHEDULER_LOG_INFO(std::string("🔢 [BTIE] 批量能量计算: ") +
                                   "任务[" + std::to_string(i) + "] " +
                                   "unit_energy=" + std::to_string(unit_energy * 1000) + " mJ" +
                                   "累计=" + std::to_string(total_batch_energy * 1000) + " mJ");
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔢 [BTIE] 批量决策: k=") + std::to_string(batch_size) +
                           " 总能耗=" + std::to_string(total_batch_energy) + "J" +
                           " 当前能量=" + std::to_string(_current_energy) + "J");

        // ⭐ BTIE核心：一次性判断能量是否充足
        if (_current_energy >= total_batch_energy) {
            // 能量充足：一次性扣减全部k个任务的能耗
            executeBatchScheduling(batch_tasks, total_batch_energy);
            _batch_scheduled_this_tick = true;
            _current_batch_tasks = batch_tasks;
            _stats.total_batch_schedules++;
            SCHEDULER_LOG_INFO(std::string("✅ [BTIE] 批量调度成功: k=") + std::to_string(batch_size) +
                              " 扣减=" + std::to_string(total_batch_energy) + "J" +
                              " 剩余=" + std::to_string(_current_energy) + "J");
        } else {
            // 能量不足：不调度任何任务
            _batch_scheduled_this_tick = false;
            _current_batch_tasks.clear();
            _stats.total_batch_skipped++;
            SCHEDULER_LOG_INFO(std::string("❌ [BTIE] 能量不足，跳过批量调度") +
                              " 需要=" + std::to_string(total_batch_energy) + "J" +
                              " 当前=" + std::to_string(_current_energy) + "J");
        }

        // Tick边界：检查抢占（高优先级任务到达时）
        checkAndPreempt();

        // 如果有kernel，触发dispatch进行调度
        if (!_kernel) {
            SCHEDULER_LOG_DEBUG("⚠️ [BTIE] performTickScheduling: _kernel为nullptr，尝试获取");
            _kernel = getKernel();
        }

        if (_kernel) {
            SCHEDULER_LOG_DEBUG("🔔 [BTIE] performTickScheduling: 触发dispatch");
            _kernel->dispatch();
        } else {
            SCHEDULER_LOG_DEBUG("⚠️ [BTIE] performTickScheduling: _kernel仍为nullptr，跳过dispatch");
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
        SCHEDULER_LOG_INFO(std::string("🔍 [BTIE] getTaskN(") + std::to_string(n) + ") 被调用" +
                           " 就绪队列大小=" + std::to_string(_ready_queue.size()) +
                           " 是否已批量调度=" + (_batch_scheduled_this_tick ? "true" : "false"));

        // ⭐ 关键修复：每次dispatch都从就绪队列动态选择任务
        // 而不是使用固定的批量，避免任务饥饿
        if (_batch_scheduled_this_tick && n < static_cast<unsigned int>(_ready_queue.size())) {
            AbsRTTask *task = _ready_queue[n];
            SCHEDULER_LOG_INFO(std::string("✅ [BTIE] getTaskN: 返回就绪队列任务 #") +
                              std::to_string(n) + ": " + getTaskName(task));
            return task;
        }

        SCHEDULER_LOG_INFO("📭 [BTIE] getTaskN: 无可调度任务，返回nullptr");
        return nullptr;
    }

    // =====================================================
    // notify - BTIE不再扣减能量（已在批量时扣减）
    // =====================================================

    void BTIEScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
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

        // ⭐ 修复0时刻调度延迟：如果是第一个任务且在0时刻，立即触发批量调度
        if (_ready_queue.size() == 1 && SIMUL.getTime() == Tick(0)) {
            SCHEDULER_LOG_INFO("⚡ [BTIE] 第一个任务在0ms到达，立即触发批量调度");
            performTickScheduling();
        }
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
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
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

        // 每1ms触发一次tick
        _tick_event->post(SIMUL.getTime() + Tick(1));
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

        // 扣除所有运行中任务上一ms的能量
        if (total_energy_to_deduct > 0 && _current_energy >= total_energy_to_deduct - 1e-9) {
            double old_energy = _current_energy;
            _current_energy -= total_energy_to_deduct;
            _stats.total_energy_consumed += total_energy_to_deduct;

            SCHEDULER_LOG_INFO(std::string("⚡ [BTIE] Tick事件: 扣除运行中任务能量 ") +
                               std::to_string(total_energy_to_deduct * 1000) + " mJ，" +
                               std::to_string(old_energy * 1000) + " mJ → " +
                               std::to_string(_current_energy * 1000) + " mJ (" +
                               std::to_string(running_tasks.size()) + " 个任务)");
        }


        // 1. 检查所有运行中的任务
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

        // 2. 中断能量不足的任务
        for (AbsRTTask *task : tasks_to_interrupt) {
            if (!task) {
                continue;
            }

            SCHEDULER_LOG_INFO(std::string("🛑 [BTIE] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法中断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            _kernel->suspend(task);

            // ⭐ 取消该任务的能量检查事件，防止继续扣除能量
            auto it = _energy_check_events.find(task);
            if (it != _energy_check_events.end()) {
                // 从map中移除，但不删除事件对象（它会自然结束）
                _energy_check_events.erase(it);
                SCHEDULER_LOG_DEBUG(std::string("⚠️ [BTIE] 已取消任务的能量检查事件: ") + getTaskName(task));
            }

            SCHEDULER_LOG_INFO(std::string("⏸️ [BTIE] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [BTIE] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }
} // namespace RTSim
