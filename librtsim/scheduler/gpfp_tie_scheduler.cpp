// gpfp_tie_scheduler.cpp - TIE (Tick-based Instant Energy-aware) Scheduler Implementation
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
#include <rtsim/scheduler/gpfp_tie_scheduler.hpp>
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
    // TIETickEvent 实现
    // =====================================================

    TIETickEvent::TIETickEvent(TIEScheduler *scheduler)
        : MetaSim::Event("TIETickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行，这样所有任务都在ready queue中
    }

    void TIETickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [TIE] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // TIEEnergyCheckEvent 实现 - 运行时能量检查
    // =====================================================

    TIEEnergyCheckEvent::TIEEnergyCheckEvent(TIEScheduler *scheduler, AbsRTTask *task, CPU *cpu)
        : MetaSim::Event("TIEEnergyCheckEvent", MetaSim::Event::_DEFAULT_PRIORITY - 5),
          _scheduler(scheduler),
          _task(task),
          _cpu(cpu),
          _ms_executed(0) {
        // ⭐ V29修复：较低优先级，确保tick先执行
    }

    void TIEEnergyCheckEvent::doit() {
        if (!_scheduler || !_task) {
            return;
        }

        // ⭐ 调试日志：记录事件触发时间
        std::string task_name = _scheduler->getTaskName(_task);
        SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 能量检查事件触发: ") +
                           "任务=" + task_name +
                           " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // ⭐ 安全检查：验证任务是否还有效（是否还在task_models中）
        if (_scheduler->_task_models.find(_task) == _scheduler->_task_models.end()) {
            // 任务已被删除，停止这个能量检查事件
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TIE] 能量检查：任务已删除，停止检查"));
            // 不重新调度事件，让它自然结束
            return;
        }

        // ⭐ 安全检查：验证这个事件是否仍在活跃列表中
        auto it = _scheduler->_energy_check_events.find(_task);
        if (it == _scheduler->_energy_check_events.end() || it->second != this) {
            // 事件已被替换或删除，停止处理
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TIE] 能量检查：事件已失效，停止检查"));
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
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TIE] 能量检查：任务已停止执行，��再扣除能量: ") +
                               task_name + " 时间=" + std::to_string(static_cast<long>(SIMUL.getTime())) + "ms");
            // 不重新调度事件
            return;
        }

        // ⭐ 关键修复：检查任务是否已经达到WCET
        // 如果已经达到WCET，任务应该完成，不应该再续期
        TIETaskModel *task_model = _scheduler->getTaskModel(_task);
        if (task_model && _ms_executed >= task_model->getWCET()) {
            SCHEDULER_LOG_INFO(std::string("✅ [TIE] 任务已达到WCET，完成执行: ") +
                               task_name + " 已执行=" + std::to_string(_ms_executed) +
                               "ms WCET=" + std::to_string(task_model->getWCET()) + "ms");
            // 任务已完成，不续期能量，也不重新调度事件
            // 任务会由正常的调度流程完成
            return;
        }

        // ⭐ V29.1修复：恢复运行中任务的续期能量扣除
        // 设计原则：
        // - getTaskN(): 负责新任务的首次能量扣除
        // - TIEEnergyCheckEvent: 负责运行中任务的续期能量扣除
        //
        // 检查是否有足够能量续期1ms
        if (current_energy < unit_energy - EPSILON) {
            // 能量不足，停止续期并中断任务
            SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 续期能量不足，中断任务: ") +
                               task_name + " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                               " 剩余=" + std::to_string(current_energy * 1000) + " mJ" +
                               " 已执行=" + std::to_string(_ms_executed) + "ms");

            // 标记能量耗尽
            _scheduler->_energy_depleted = true;

            // 中断当前任务（调用kernel的suspend机制）
            if (_cpu) {
                _scheduler->_kernel->suspend(_task);
                SCHEDULER_LOG_INFO(std::string("⚠️ [TIE] 任务因能量不足被挂起: ") + task_name);
            }

            // 不重新调度事件
            return;
        }

        // 能量充足，扣除续期能量
        double old_energy = current_energy;
        _scheduler->_current_energy -= unit_energy;
        _scheduler->_stats.total_energy_consumed += unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 运行中任务续期: ") +
                           task_name + " 1ms能耗=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " " + std::to_string(old_energy * 1000) + " mJ → " +
                           std::to_string(_scheduler->_current_energy * 1000) + " mJ" +
                           " 已执行=" + std::to_string(_ms_executed) + "ms");

        // 重新调度下一次能量检查（1ms后）
        post(SIMUL.getTime() + 1);
        return;
    }

    // =====================================================
    // TIETaskModel 实现
    // =====================================================

    TIETaskModel::TIETaskModel(AbsRTTask *t, int period, int wcet,
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

    TIETaskModel::~TIETaskModel() {}

    Tick TIETaskModel::getPriority() const {
        return _rm_priority;
    }

    void TIETaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void TIETaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // TIEScheduler 实现
    // =====================================================

    TIEScheduler::TIEScheduler()
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
          _energy_depleted(false) {  // ⭐ 初始化能量耗尽标志

        SCHEDULER_LOG_INFO("🚀 [TIE] TIE Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [TIE] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [TIE] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [TIE] 开始时间偏移: ") +
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
                                SCHEDULER_LOG_DEBUG(std::string("📄 [TIE] YAML行: '") + line + "'");
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
                                SCHEDULER_LOG_INFO(std::string("📖 [TIE] 解析到solar_data_file: '") + value + "'");
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

                    SCHEDULER_LOG_INFO(std::string("☀️ [TIE] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [TIE] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [TIE] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [TIE] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [TIE] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new TIETickEvent(this);

        SCHEDULER_LOG_INFO("✅ [TIE] TIE Scheduler 初始化完成");
    }

    TIEScheduler::TIEScheduler(const std::vector<std::string> &params)
        : TIEScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<TIEScheduler>
        TIEScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<TIEScheduler>(params);
    }

    TIEScheduler::~TIEScheduler() {
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
    // 核心调度逻辑 - TIE算法的核心
    // =====================================================

    void TIEScheduler::performTickScheduling() {
        std::cout << "[TEST] performTickScheduling called at time " << SIMUL.getTime() << std::endl;
        SCHEDULER_LOG_INFO(std::string("🔄 [TIE] performTickScheduling @ ") +
                           std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms" +
                           " 能量=" + std::to_string(_current_energy) + "J");

        // ⭐ Bug修复3：能量耗尽时跳过调度
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_INFO(std::string("💀 [TIE] 能量已耗尽，跳过Tick调度"));
            return;  // 不进行任何调度，包括中断检查
        }

        _stats.total_tick_count++;


        // ⭐ V30修复：不要在每个tick开始时清空_counted_tasks_in_dispatch
        // 这样可以避免在同一tick的多次dispatch()调用中重复扣除能量
        // ⭐ 但需要在能量耗尽时清空（下面有检查）
        // _dispatching_tasks_total_energy = 0.0;
        // _counted_tasks_in_dispatch.clear();
        SCHEDULER_LOG_DEBUG(std::string("🧹 [TIE] Tick开始: _counted_tasks_in_dispatch.size()=") + std::to_string(_counted_tasks_in_dispatch.size()));

        // ⭐ 核心：先收集能量，再调度
        // 1. 收集太阳能（从上次tick到现在）
        Tick current_time = SIMUL.getTime();

        // 计算从上次tick到现在的时间
        Tick elapsed = current_time - _last_tick_time;

        if (elapsed > 0) {
            double harvested = collectSolarEnergy(current_time);
            if (harvested > 0.000001) {
                _current_energy += harvested;
                _stats.total_energy_harvested += harvested;
                SCHEDULER_LOG_INFO(std::string("☀️ [TIE] Tick边界收集能量: ") +
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

        // ⭐ 关键修复：移除tick检查！能量管理完全由TIEEnergyCheckEvent负责
        // tick只做抢占检查，避免时序冲突导致能量白扣
        //（旧代码：checkAndInterruptRunningTasks(); // ❌ 这会导致3ms时刻提前中断任务）

        // 2. Tick边界：检查抢占（高优先级任务到达时）
        checkAndPreempt();

        // 4. 如果有kernel，循环触发dispatch直到填满所有CPU
        if (!_kernel) {
            SCHEDULER_LOG_DEBUG("⚠️ [TIE] performTickScheduling: _kernel为nullptr，尝试获取");
            _kernel = getKernel();
        }

        if (_kernel) {
            const auto& running_tasks = _kernel->getCurrentExecutingTasks();
            size_t num_running = 0;
            for (const auto& pair : running_tasks) {
                if (pair.second) num_running++;
            }

            SCHEDULER_LOG_INFO(std::string("🔔 [TIE] performTickScheduling @ ") +
                               std::to_string(static_cast<int64_t>(SIMUL.getTime())) + "ms: " +
                               "触发dispatch, _ready_queue.size()=" + std::to_string(_ready_queue.size()) +
                               " num_running=" + std::to_string(num_running) +
                               " num_cpus=" + std::to_string(running_tasks.size()));

            _kernel->dispatch();

            SCHEDULER_LOG_INFO("✅ [TIE] dispatch完成");
        } else {
            SCHEDULER_LOG_DEBUG("⚠️ [TIE] performTickScheduling: _kernel仍为nullptr，跳过dispatch");
        }
    }


    void TIEScheduler::schedule() {
        // TIE依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [TIE] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *TIEScheduler::getFirst() {
        SCHEDULER_LOG_DEBUG(std::string("🔍 [TIE] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // ⭐ 核心：不在这里收集能量，能量收集在tick边界完成

        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [TIE] getFirst: 就绪队列为空");
            return nullptr;
        }

        AbsRTTask *first_task = _ready_queue.front();
        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [TIE] getFirst: 队列首任务为空");
            return nullptr;
        }

        // ⭐ 核心：即时能量判断（当前能量 >= 1ms能耗）
        double unit_energy = calculateUnitEnergyForTask(first_task);

        if (_current_energy < unit_energy) {
            SCHEDULER_LOG_INFO(std::string("❌ [TIE] getFirst: 能量不足") +
                              " 任务: " + getTaskName(first_task) +
                              " 需要: " + std::to_string(unit_energy) + "J" +
                              " 当前: " + std::to_string(_current_energy) + "J");
            return nullptr;
        }

        // 返回任务（能量在notify时扣减）
        return first_task;
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（级联调度）
    // =====================================================

    AbsRTTask *TIEScheduler::getTaskN(unsigned int n) {

        SCHEDULER_LOG_DEBUG(std::string("🔍 [TIE] getTaskN(") + std::to_string(n) + ") " +
                           "已调度能耗=" + std::to_string(_dispatching_tasks_total_energy) + "J " +
                           "当前能量=" + std::to_string(_current_energy) + "J " +
                           "队���大小=" + std::to_string(_ready_queue.size()));


        if (_ready_queue.empty()) {
            SCHEDULER_LOG_INFO("📭 [TIE] getTaskN: 就绪队列为空");
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
                        SCHEDULER_LOG_DEBUG(std::string("🧹 [TIE] 清理不活动任务: ") + getTaskName(task));
                        return true;
                    }
                    // ⭐ 移除过期的周期性任务实例：到达时间+截止时间 < 当前时间
                    Tick arrival = task->getArrival();
                    Tick deadline = arrival + Tick(20);  // 周期性任务的截止时间是到达时间+周期
                    if (deadline < current_time) {
                        SCHEDULER_LOG_DEBUG(std::string("🧹 [TIE] 清理过期任务实例: ") +
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
            SCHEDULER_LOG_DEBUG("📭 [TIE] getTaskN: 清理后队列为空");
            return nullptr;
        }
        */

        // ⭐ V30调试：输出ready queue信息
        std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - ready_queue.size()=" << _ready_queue.size() << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            std::cout << "[DEBUG]   ready_queue[" << i << "]=" << getTaskName(_ready_queue[i]) << std::endl;
        }

        // ⭐ 级联调度：遍历就绪队列，运行中任务也要检查能量
        unsigned int ready_index = 0;
        std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 开始遍历ready_queue, 查找第" << n << "个未调度任务" << std::endl;
        for (size_t i = 0; i < _ready_queue.size(); ++i) {
            AbsRTTask *task = _ready_queue[i];

            if (!task) {
                continue;
            }

            // ⭐ 关键修复：不再跳过已调度的任务
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

            std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - i=" << i << " task=" << getTaskName(task)
                      << " ready_index=" << ready_index << " is_running=" << is_running
                      << " already_counted=" << already_counted << std::endl;

            // ⭐ V29.1修复：运行中任务的续期由TIEEnergyCheckEvent处理，getTaskN()不再扣除续期能量
            // 设计原则：
            // - getTaskN(): 只负责新任务的首次调度和能量扣除
            // - TIEEnergyCheckEvent: 负责运行中任务的续期能量扣除（每1ms触发一次）
            if (is_running) {
                // 运行中任务：直接返回让kernel继续调度
                // 不检查能量（由TIEEnergyCheckEvent检查）
                // 不扣除能量（由TIEEnergyCheckEvent扣除）

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

                // ⭐ V30调试：输出能量检查信息
                std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 准备调度第" << ready_index << "个任务: " << getTaskName(task)
                          << " 需要1ms=" << unit_energy * 1000 << " mJ"
                          << " 当前能量=" << _current_energy * 1000 << " mJ" << std::endl;

                const double EPSILON = 1e-9;
                // ⭐ 预扣模式：检查当前能量是否足够当前任务的1ms能耗
                if (_current_energy < unit_energy - EPSILON) {
                    SCHEDULER_LOG_INFO(std::string("⚠️ [TIE] 能量不足，停止级联") +
                                      " 任务=" + getTaskName(task) +
                                      " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                      " 当前能量=" + std::to_string(_current_energy) + "J");
                    std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 能量不足，返回nullptr" << std::endl;
                    return nullptr;  // ⭐ 立即停止级联
                }

                // ⭐ 预扣模式：调度时立即扣除能量��而不是记账）
                // 检查是否已经扣除过能量
                if (_counted_tasks_in_dispatch.find(task) == _counted_tasks_in_dispatch.end()) {
                    // 尚未扣除，立即扣除
                    double old_energy = _current_energy;
                    _current_energy -= unit_energy;
                    _stats.total_energy_consumed += unit_energy;
                    _dispatching_tasks_total_energy += unit_energy;  // 保持统计一致性
                    _counted_tasks_in_dispatch.insert(task);

                    SCHEDULER_LOG_INFO(std::string("✅ [TIE] 预扣能量并调度任务: ") + getTaskName(task) +
                                      " 1ms能耗=" + std::to_string(unit_energy) + "J" +
                                      " " + std::to_string(old_energy * 1000) + " mJ → " +
                                      std::to_string(_current_energy * 1000) + " mJ");
                } else {
                    SCHEDULER_LOG_DEBUG(std::string("♻️ [TIE] 任务已扣除能量，直接返回: ") + getTaskName(task));
                }

                return task;
            } else {
                // ⭐ V32关键修复：不是我们要找的第n个任务，继续寻找
                ready_index++;
            }

        }

        std::cout << "[DEBUG] TIE::getTaskN(" << n << ") - 循环结束，未找到第" << n << "个任务，返回nullptr (ready_index=" << ready_index << ")" << std::endl;
        return nullptr;
    }

    // =====================================================
    // notify - 每ms逐次扣减能耗（TIE核心逻辑）
    // =====================================================

    void TIEScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复：任务到达时只检查能量，不扣减能耗
        // 能耗在任务调度时通过getTaskN()方法扣减
        double unit_energy = calculateUnitEnergyForTask(task);

        // 检查能量是否足够
        const double EPSILON = 1e-9;
        if (_current_energy < unit_energy - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [TIE] notify: 能量不足") +
                                 " 任务=" + getTaskName(task) +
                                 " 需要=" + std::to_string(unit_energy) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J");
            return;
        }

        // 任务到达，添加到就绪队列
        SCHEDULER_LOG_INFO(std::string("📥 [TIE] 任务到达并添加到就绪队列: ") + getTaskName(task));
        addToReadyQueue(task);
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void TIEScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] addTask: 任务为空");
            return;
        }

        // ⭐ Bug修复4：能量耗尽时拒绝新任务
        if (_energy_depleted && _current_energy < 0.000001) {
            SCHEDULER_LOG_WARNING(std::string("💀 [TIE] 能量已耗尽，拒绝添加新任务: ") +
                                     getTaskName(task));
            return;  // 拒绝添加
        }

        SCHEDULER_LOG_INFO(std::string("📥 [TIE] 添加任务: ") + getTaskName(task));
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
        TIETaskModel *model = new TIETaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [TIE] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void TIEScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [TIE] 移除任务: ") + getTaskName(task));

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

        SCHEDULER_LOG_INFO(std::string("✅ [TIE] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void TIEScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [TIE] 任务到达: ") + getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
            checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void TIEScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [TIE] Tick级抢占检查");
        checkAndPreemptOnAllCPUs();
    }

    void TIEScheduler::checkAndPreemptOnAllCPUs() {
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
                SCHEDULER_LOG_INFO(std::string("🔄 [TIE] 抢占CPU: ") +
                                  " 挂起低优先级任务=" + getTaskName(running_task) +
                                  " 调度高优先级任务=" + getTaskName(highest));

                // ⭐ 实际抢占逻辑：挂起当前运行的任务
                // suspend会自动调用deschedule()并将任务重新放回调度队列
                if (_kernel) {
                    _kernel->suspend(running_task);
                    SCHEDULER_LOG_DEBUG(std::string("⏸️ [TIE] 已挂起任务: ") + getTaskName(running_task));
                } else {
                    SCHEDULER_LOG_WARNING("⚠️ [TIE] 抢占失败：_kernel为nullptr");
                }
            }
        }
    }

    // =====================================================
    // 运行时能量检查和任务中断（V28.15新增）
    // =====================================================

    void TIEScheduler::checkAndInterruptRunningTasks() {
        SCHEDULER_LOG_INFO("🔍 [TIE] 检查运行中任务的能量状态");

        if (!_kernel) {
            _kernel = getKernel();
            if (!_kernel) {
                SCHEDULER_LOG_WARNING("⚠️ [TIE] checkAndInterruptRunningTasks: _kernel为nullptr，无法中断任务");
                return;
            }
        }

        const double EPSILON = 1e-9;
        std::vector<AbsRTTask *> tasks_to_interrupt;

        // ⭐ V28.15修复：使用kernel的getCurrentExecutingTasks()获取实际运行中的任务
        const auto& running_tasks = _kernel->getCurrentExecutingTasks();
        SCHEDULER_LOG_INFO(std::string("🔍 [TIE] getCurrentExecutingTasks() 返回 ") +
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
                SCHEDULER_LOG_WARNING(std::string("⚡ [TIE] 任务能量不足，将中断: ") +
                                     getTaskName(task) +
                                     " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                     " 当前能量=" + std::to_string(_current_energy) + "J");

                tasks_to_interrupt.push_back(task);
                _stats.total_skipped_energy++;
            } else {
                SCHEDULER_LOG_DEBUG(std::string("✅ [TIE] 任务能量充足: ") +
                                   getTaskName(task) +
                                   " 需要1ms=" + std::to_string(unit_energy) + "J" +
                                   " 当前能量=" + std::to_string(_current_energy) + "J");
            }
        }

        SCHEDULER_LOG_INFO(std::string("🔍 [TIE] 运行任务检查完成: map大小=") +
                           std::to_string(running_tasks.size()));

        // 2. 中断能量不足的任务
        for (AbsRTTask *task : tasks_to_interrupt) {
            if (!task) {
                continue;
            }

            SCHEDULER_LOG_INFO(std::string("🛑 [TIE] 中断任务（能量不足）: ") + getTaskName(task));

            // 调用kernel的suspend方法中断任务
            // suspend会自动调用deschedule()并将任务重新放回调度队列
            _kernel->suspend(task);

            // ⭐ 取消该任务的能量检查事件，防止继续扣除能量
            auto it = _energy_check_events.find(task);
            if (it != _energy_check_events.end()) {
                // 从map中移除，但不删除事件对象（它会自然结束）
                _energy_check_events.erase(it);
                SCHEDULER_LOG_DEBUG(std::string("⚠️ [TIE] 已取消任务的能量检查事件: ") + getTaskName(task));
            }

            SCHEDULER_LOG_INFO(std::string("⏸️ [TIE] 任务已中断，等待能量恢复: ") + getTaskName(task));
        }

        if (!tasks_to_interrupt.empty()) {
            SCHEDULER_LOG_INFO(std::string("📊 [TIE] 本次tick中断了 ") +
                               std::to_string(tasks_to_interrupt.size()) + " 个任务（能量不足）");
        }
    }

    bool TIEScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        TIETaskModel *running_model = getTaskModel(running_task);
        TIETaskModel *new_model = getTaskModel(new_task);

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

    void TIEScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [TIE] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void TIEScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [TIE] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
    }

    void TIEScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是���已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [TIE] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        TIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            TIETaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [TIE] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void TIEScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [TIE] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void TIEScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [TIE] 任务加入等待队列: ") + getTaskName(task));
    }

    void TIEScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool TIEScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool TIEScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    AbsRTTask *TIEScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double TIEScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        TIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    double TIEScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        TIETaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] calculateTotalEnergyForTask: 任务模型不存在");
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

    double TIEScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [TIE] 功率计算: ") +
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

    void TIEScheduler::startEnergyCheckForTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            return;
        }

        // 检查是否已经有能量检查事件
        if (_energy_check_events.find(task) != _energy_check_events.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚡ [TIE] 任务已有能量检查事件: ") + getTaskName(task));
            return;
        }

        // 创建并启动能量检查事件
        TIEEnergyCheckEvent *evt = new TIEEnergyCheckEvent(this, task, cpu);
        _energy_check_events[task] = evt;

        // 1ms后触发第一次检查
        evt->post(SIMUL.getTime() + 1);

        SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 启动运行时能量检查: ") +
                           getTaskName(task) + " 在CPU " + cpu->toString());
    }

    void TIEScheduler::stopEnergyCheckForTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_check_events.find(task);
        if (it != _energy_check_events.end()) {
            // ⚠️ 不要删除事件对象，只从映射中移除
            // 事件会自然结束（不再重新 post）
            _energy_check_events.erase(it);

            SCHEDULER_LOG_INFO(std::string("⚡ [TIE] 停止运行时能量检查: ") +
                               getTaskName(task));
        }
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double TIEScheduler::collectSolarEnergy(Tick current_time) {
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

    double TIEScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [TIE] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [TIE] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void TIEScheduler::scheduleNextTick() {
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

    TIETaskModel *TIEScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string TIEScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    AbsRTTask *TIEScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    int TIEScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *TIEScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void TIEScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [TIE] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [TIE] 调度任务: ") + getTaskName(task) + " 到CPU");

        removeFromReadyQueue(task);
        _running_tasks[cpu] = task;
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void TIEScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [TIE] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void TIEScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void TIEScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
    }

    MRTKernel *TIEScheduler::getKernel() {
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

    void TIEScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [TIE] newRun - 仿真开始");

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

        SCHEDULER_LOG_INFO(std::string("💰 [TIE] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void TIEScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [TIE] endRun - 仿真结束");

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [TIE] ===== TIE调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void TIEScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [TIE] 任务结束: ") + getTaskName(task));

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
            SCHEDULER_LOG_INFO(std::string("📊 [TIE] 任务能量消耗: ") +
                              getTaskName(task) +
                              " 累计消耗=" + std::to_string(it->second.total_consumed) + "J");
            _energy_accounts.erase(it);
        }

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [TIE] 当前能量: ") + std::to_string(_current_energy) + "J");

        // ⭐ 关键修复：任务结束时触发立即调度
        // 检查是否有空闲CPU和等待的任务
        if (!_ready_queue.empty() && _kernel) {
            // ⭐ Bug修复：能量耗尽时不触发立即调度
            if (_energy_depleted) {
                SCHEDULER_LOG_INFO(std::string("💀 [TIE] 能量已耗尽，跳过任务结束后的立即调度") +
                                   " 剩余能量=" + std::to_string(_current_energy * 1000) + " mJ");
                return;
            }
            SCHEDULER_LOG_INFO("🔄 [TIE] 任务结束，触发立即调度");
            _kernel->dispatch();
        }
    }

    bool TIEScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void TIEScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [TIE] ===== TIE调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string TIEScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> TIEScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

} // namespace RTSim
