// gpfp_asap_block_scheduler.cpp - ASAP-Block (As Soon As Possible Block) Scheduler Implementation
// 算法特点：
// 1. 基于当前实际能量进行即时判断（无前瞻性预测）
// 2. 每个tick统一选择并一次性提交能耗
// 3. 能量不足立即停止，禁止低优先级任务绕过
// 4. 所有active jobs在tick边界按RM重新排序
// 5. tick决策前收集已经可用的能量

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/scheduler/gpfp_asap_block_scheduler.hpp>
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

    static SchedulerTraceJob makeASAPBlockTraceJob(
        AbsRTTask *task,
        const std::map<AbsRTTask *, ASAPBlockTaskModel *> &models,
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

    static std::vector<SchedulerTraceJob> makeASAPBlockTraceJobs(
        const std::vector<AbsRTTask *> &tasks,
        const std::map<AbsRTTask *, ASAPBlockTaskModel *> &models) {
        std::vector<SchedulerTraceJob> jobs;
        jobs.reserve(tasks.size());
        for (std::size_t i = 0; i < tasks.size(); ++i) {
            jobs.push_back(makeASAPBlockTraceJob(tasks[i], models, static_cast<int>(i)));
        }
        return jobs;
    }

    // =====================================================
    // ASAPBlockTickEvent 实现
    // =====================================================

    ASAPBlockTickEvent::ASAPBlockTickEvent(ASAPBlockScheduler *scheduler)
        : MetaSim::Event("ASAPBlockTickEvent", MetaSim::Event::_DEFAULT_PRIORITY + 10),
          _scheduler(scheduler) {
        // ⭐ V30修复：较低优先级，确保任务到达事件先于tick执行，这样所有任务都在ready queue中
    }

    void ASAPBlockTickEvent::doit() {
        if (!_scheduler) {
            return;
        }

        Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏱️ [ASAP-Block] ===== Tick事件触发 @ ") +
                           std::to_string(current_ms) + "ms =====");

        // 执行tick调度
        _scheduler->performTickScheduling();

        // 调度下一个tick（1ms后）
        _scheduler->scheduleNextTick();
    }

    // =====================================================
    // ASAPBlockTaskModel 实现
    // =====================================================

    ASAPBlockTaskModel::ASAPBlockTaskModel(AbsRTTask *t, int period, int wcet,
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

    ASAPBlockTaskModel::~ASAPBlockTaskModel() {}

    Tick ASAPBlockTaskModel::getPriority() const {
        return _rm_priority;
    }

    void ASAPBlockTaskModel::changePriority(Tick p) {
        _rm_priority = p;
    }

    void ASAPBlockTaskModel::setPeriod(int period) {
        _period = period;
        _rm_priority = period;  // RM优先级等于周期
    }

    // =====================================================
    // ASAPBlockScheduler 实现
    // =====================================================

    ASAPBlockScheduler::ASAPBlockScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _selected_energy(0.0),
          _selection_tick(-1),
          _last_energy_commit_tick(-1),
          _selection_frozen(false),
          _has_energy_commit(false),
          _selection_stopped_by_energy(false),
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
          _semantic_trace_enabled(false) {

        SCHEDULER_LOG_INFO("🚀 [ASAP-Block] ASAP Block Scheduler 初始化");

        // 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        _max_energy = configMgr.getMaxEnergy();
        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Block] 最大能量: ") + std::to_string(_max_energy) + "J");

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [ASAP-Block] 配置文件: ") + config_file);
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [ASAP-Block] EnergyBridge 初始化成功");

            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [ASAP-Block] 开始时间偏移: ") +
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
                                SCHEDULER_LOG_DEBUG(std::string("📄 [ASAP-Block] YAML行: '") + line + "'");
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
                                SCHEDULER_LOG_INFO(std::string("📖 [ASAP-Block] 解析到solar_data_file: '") + value + "'");
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
                                SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-Block] V93: base_harvesting_rate = ") +
                                                  std::to_string(_base_harvest_rate) + " J/ms (" +
                                                  std::to_string(_base_harvest_rate * 1000) + " mW)");
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [ASAP-Block] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²" +
                                      " harvest_rate=" + std::to_string(_base_harvest_rate * 1000) + "mW");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Block] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Block] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Block] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy >= 0) {  // ⭐ 修复：允许initial_energy=0的情况
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Block] 从ConfigManager获取初始能量: ") +
                                  std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [ASAP-Block] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 创建Tick事件
        _tick_event = new ASAPBlockTickEvent(this);

        SCHEDULER_LOG_INFO("✅ [ASAP-Block] ASAP Block Scheduler 初始化完成");
    }

    ASAPBlockScheduler::ASAPBlockScheduler(const std::vector<std::string> &params)
        : ASAPBlockScheduler() {
        // 委托给默认构造函数
    }

    std::unique_ptr<ASAPBlockScheduler>
        ASAPBlockScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<ASAPBlockScheduler>(params);
    }

    ASAPBlockScheduler::~ASAPBlockScheduler() {
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

    void ASAPBlockScheduler::performTickScheduling() {
        Tick current_time = SIMUL.getTime();
        _stats.total_tick_count++;

        if (!_kernel) {
            _kernel = getKernel();
        }

        const double harvested = collectEnergyAtTickBoundary();
        const double available_energy =
            std::min(_max_energy, _current_energy + harvested);

        std::vector<AbsRTTask *> active_jobs =
            collectActiveJobs(current_time);
        sortByRMPriority(active_jobs);

        const std::size_t processor_count =
            _kernel ? _kernel->getCurrentExecutingTasks().size() : 0;
        double reserved_energy = 0.0;
        bool stopped_by_energy = false;
        std::vector<AbsRTTask *> selected =
            selectASAPBlockPrefix(active_jobs,
                                  processor_count,
                                  available_energy,
                                  reserved_energy,
                                  stopped_by_energy);

        if (_trace_logger && _semantic_trace_enabled && !active_jobs.empty()) {
            std::string decision_reason = "selected_prefix";
            if (stopped_by_energy && selected.empty()) {
                decision_reason = "highest_priority_energy_insufficient";
            } else if (stopped_by_energy) {
                decision_reason = "prefix_energy_insufficient";
            } else if (selected.size() >= processor_count &&
                       selected.size() < active_jobs.size()) {
                decision_reason = "processor_capacity_reached";
            }
            _trace_logger->logSchedulerDecision(
                "ASAP-Block",
                available_energy * 1000.0,
                makeASAPBlockTraceJobs(active_jobs, _task_models),
                makeASAPBlockTraceJobs(selected, _task_models),
                decision_reason);
            if (stopped_by_energy && selected.empty()) {
                _trace_logger->logEnergyBlock(
                    "ASAP-Block",
                    makeASAPBlockTraceJob(active_jobs.front(),
                                          _task_models,
                                          0),
                    available_energy * 1000.0);
            }
        }

        freezeTickSelection(current_time,
                            std::move(selected),
                            reserved_energy,
                            stopped_by_energy);

        if (_kernel) {
            suspendUnselectedRunningJobs();

            // A context switch started by an older selection must be rebound
            // to this tick's frozen prefix before normal dispatch fills CPUs.
            for (const auto &[cpu, running] :
                 _kernel->getCurrentExecutingTasks()) {
                if (!running && _kernel->isCPUDispatching(cpu)) {
                    _kernel->dispatch(cpu);
                }
            }
            _kernel->dispatch();
        }

        commitTickEnergy(current_time, available_energy);
        _last_tick_time = current_time;

        SCHEDULER_LOG_INFO(
            std::string("[ASAP-Block] tick=") +
            std::to_string(static_cast<int64_t>(current_time)) +
            " active=" + std::to_string(active_jobs.size()) +
            " selected=" + std::to_string(_dispatch_selection_order.size()) +
            " harvested=" + std::to_string(harvested) +
            " consumed=" + std::to_string(reserved_energy) +
            " remain=" + std::to_string(_current_energy));
    }

    std::vector<AbsRTTask *>
        ASAPBlockScheduler::collectActiveJobs(Tick current_time) {
        std::set<AbsRTTask *> unique_jobs;

        auto collect = [&](AbsRTTask *task) {
            if (isSchedulableActiveJob(task, current_time)) {
                unique_jobs.insert(task);
            }
        };

        if (_kernel) {
            for (const auto &[cpu, task] :
                 _kernel->getCurrentExecutingTasks()) {
                (void) cpu;
                collect(task);
            }
        }

        for (AbsRTTask *task : _ready_queue) {
            collect(task);
        }
        for (const auto &[task, arrival_tick] : _pending_arrivals) {
            if (arrival_tick <= current_time) {
                collect(task);
            }
        }
        for (AbsRTTask *task : _dispatch_selection_order) {
            collect(task);
        }

        for (auto it = _pending_arrivals.begin();
             it != _pending_arrivals.end();) {
            if (it->second <= current_time) {
                it = _pending_arrivals.erase(it);
            } else {
                ++it;
            }
        }

        return std::vector<AbsRTTask *>(unique_jobs.begin(),
                                        unique_jobs.end());
    }

    bool ASAPBlockScheduler::isSchedulableActiveJob(
        AbsRTTask *task, Tick current_time) const {
        if (!task || task->getArrival() > current_time ||
            task->getRemainingWCET() <= 0.0) {
            return false;
        }

        auto model_it = _task_models.find(task);
        if (model_it == _task_models.end() || !model_it->second) {
            return false;
        }

        Task *concrete_task = dynamic_cast<Task *>(task);
        if (concrete_task) {
            const task_state state = concrete_task->getState();
            return state == TSK_READY || state == TSK_EXEC;
        }

        return task->isExecuting() || task->isActive() ||
               model_it->second->isActive();
    }

    void ASAPBlockScheduler::sortByRMPriority(
        std::vector<AbsRTTask *> &tasks) const {
        std::sort(tasks.begin(), tasks.end(),
                  [this](AbsRTTask *lhs, AbsRTTask *rhs) {
                      const auto lhs_it = _task_models.find(lhs);
                      const auto rhs_it = _task_models.find(rhs);
                      const int lhs_period = lhs_it->second->getPeriod();
                      const int rhs_period = rhs_it->second->getPeriod();
                      if (lhs_period != rhs_period) {
                          return lhs_period < rhs_period;
                      }
                      return lhs->getTaskNumber() < rhs->getTaskNumber();
                  });
    }

    std::vector<AbsRTTask *> ASAPBlockScheduler::selectASAPBlockPrefix(
        const std::vector<AbsRTTask *> &active_jobs,
        std::size_t processor_count,
        double available_energy,
        double &reserved_energy,
        bool &stopped_by_energy) const {
        constexpr double EPSILON = 1e-9;
        std::vector<AbsRTTask *> selected;
        reserved_energy = 0.0;
        stopped_by_energy = false;

        for (AbsRTTask *task : active_jobs) {
            if (selected.size() >= processor_count) {
                break;
            }

            const auto model_it = _task_models.find(task);
            if (model_it == _task_models.end() || !model_it->second) {
                throw std::logic_error(
                    "ASAP-Block active job has no task model");
            }

            const double unit_energy = model_it->second->getUnitEnergy();
            if (available_energy - reserved_energy <
                unit_energy - EPSILON) {
                stopped_by_energy = true;
                break;
            }

            selected.push_back(task);
            reserved_energy += unit_energy;
        }

        return selected;
    }

    void ASAPBlockScheduler::freezeTickSelection(
        Tick tick,
        std::vector<AbsRTTask *> selected,
        double reserved_energy,
        bool stopped_by_energy) {
        _dispatch_selection_order = std::move(selected);
        _selected_energy = reserved_energy;
        _selection_tick = tick;
        _selection_frozen = true;
        _selection_stopped_by_energy = stopped_by_energy;
    }

    bool ASAPBlockScheduler::isSelectedThisTick(
        AbsRTTask *task) const {
        return _selection_frozen &&
               _selection_tick == SIMUL.getTime() &&
               std::find(_dispatch_selection_order.begin(),
                         _dispatch_selection_order.end(),
                         task) != _dispatch_selection_order.end();
    }

    void ASAPBlockScheduler::suspendUnselectedRunningJobs() {
        if (!_kernel) {
            return;
        }

        std::vector<AbsRTTask *> to_suspend;
        for (const auto &[cpu, task] :
             _kernel->getCurrentExecutingTasks()) {
            (void) cpu;
            if (task && task->isExecuting() &&
                !isSelectedThisTick(task)) {
                to_suspend.push_back(task);
            }
        }

        for (AbsRTTask *task : to_suspend) {
            setSuspendReason(
                task,
                _selection_stopped_by_energy
                    ? "insufficient_energy"
                    : "preemption");
            _kernel->suspend(task);
        }
    }

    void ASAPBlockScheduler::commitTickEnergy(
        Tick tick,
        double available_energy) {
        constexpr double EPSILON = 1e-9;
        if (_has_energy_commit && _last_energy_commit_tick == tick) {
            throw std::logic_error(
                "ASAP-Block energy committed more than once in one tick");
        }
        if (!_selection_frozen || _selection_tick != tick) {
            throw std::logic_error(
                "ASAP-Block energy commit requires a frozen tick selection");
        }
        if (_selected_energy < -EPSILON ||
            _selected_energy > available_energy + EPSILON) {
            throw std::logic_error(
                "ASAP-Block invalid reserved energy");
        }

        _current_energy =
            std::max(0.0, available_energy - _selected_energy);
        _stats.total_energy_consumed += _selected_energy;
        _last_energy_commit_tick = tick;
        _has_energy_commit = true;
    }


    void ASAPBlockScheduler::schedule() {
        // TIE依赖MRTKernel::dispatch() -> getTaskN()流程
        SCHEDULER_LOG_DEBUG("🔔 [ASAP-Block] schedule() 被调用");
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务
    // =====================================================

    AbsRTTask *ASAPBlockScheduler::getFirst() {
        return getTaskN(0);
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（级联调度）
    // =====================================================

    AbsRTTask *ASAPBlockScheduler::getTaskN(unsigned int n) {
        if (!_selection_frozen || _selection_tick != SIMUL.getTime() ||
            n >= _dispatch_selection_order.size()) {
            return nullptr;
        }
        return _dispatch_selection_order[n];
    }

    bool ASAPBlockScheduler::acceptsDispatchCompletion(
        AbsRTTask *task) const {
        return task && isSelectedThisTick(task);
    }

    // =====================================================
    // notify - dispatch完成后仅同步执行态，不再做二次能量门槛
    // =====================================================

    void ASAPBlockScheduler::notify(AbsRTTask *task) {
        Scheduler::notify(task);

        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔔 [ASAP-Block] notify: 任务进入执行态（不再做二次能量门槛）: ") +
                          getTaskName(task));

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void ASAPBlockScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Block] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [ASAP-Block] 添加任务: ") + getTaskName(task));
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
        ASAPBlockTaskModel *model = new ASAPBlockTaskModel(task, period, wcet, workload, energy_coeff, arrival_offset);

        // ⭐ 关键修复：先将模型添加到映射，再计算能量
        enqueueModel(model);
        _task_models[task] = model;

        // 计算能量（总能耗和每ms能耗）
        double total_energy = calculateTotalEnergyForTask(task);
        double unit_energy = total_energy / static_cast<double>(wcet);  // 每ms能耗

        model->_total_energy = total_energy;
        model->_unit_energy = unit_energy;

        SCHEDULER_LOG_INFO(std::string("⚡ [ASAP-Block] 任务能耗计算: ") +
                          "总能耗=" + std::to_string(total_energy) + "J" +
                          " 每ms能耗=" + std::to_string(unit_energy) + "J" +
                          " WCET=" + std::to_string(wcet) + "ms");

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Block] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void ASAPBlockScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [ASAP-Block] 移除任务: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        _pending_arrivals.erase(task);
        _suspend_reasons.erase(task);

        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Block] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void ASAPBlockScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [ASAP-Block] 任务到达: ") + getTaskName(task));
        _pending_arrivals[task] = SIMUL.getTime();

        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);
        }

    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void ASAPBlockScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➕ [ASAP-Block] insert: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::insert(task);
        addToReadyQueue(task);
    }

    void ASAPBlockScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [ASAP-Block] extract: ") + getTaskName(task) +
                          " _ready_queue.size()=" + std::to_string(_ready_queue.size()));

        Scheduler::extract(task);
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
    }

    void ASAPBlockScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // ⭐ 修复重复实例bug：检查任务是���已在就绪队列中
        if (std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end()) {
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [ASAP-Block] 任务已在就绪队列，跳过添加: ") + getTaskName(task));
            return;
        }

        removeFromWaitingQueue(task);

        ASAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Block] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 按RM优先级插入（周期短的优先）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            ASAPBlockTaskModel *other_model = getTaskModel(*it);
            if (other_model) {
                const Tick other_priority =
                    other_model->getRMPriority();
                if (other_priority > priority ||
                    (other_priority == priority &&
                     (*it)->getTaskNumber() >
                         task->getTaskNumber())) {
                    break;
                }
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [ASAP-Block] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void ASAPBlockScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [ASAP-Block] removeFromReadyQueue: ") + getTaskName(task) +
                               " 剩余size=" + std::to_string(_ready_queue.size()));
        }
    }

    void ASAPBlockScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }
        removeFromReadyQueue(task);
        _waiting_queue.push_back(task);
        SCHEDULER_LOG_DEBUG(std::string("⏸️ [ASAP-Block] 任务加入等待队列: ") + getTaskName(task));
    }

    void ASAPBlockScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
        }
    }

    bool ASAPBlockScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool ASAPBlockScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double ASAPBlockScheduler::calculateUnitEnergyForTask(AbsRTTask *task) {
        ASAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Block] calculateUnitEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // 返回预先计算的每ms能耗
        return model->getUnitEnergy();
    }

    // ⭐ EnergyInfoProvider接口实现
    double ASAPBlockScheduler::getTaskUnitEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getUnitEnergy();
    }

    double ASAPBlockScheduler::getTaskTotalEnergy(AbsRTTask *task) const {
        auto it = _task_models.find(task);
        if (it == _task_models.end()) {
            return 0.0;
        }
        return it->second->getTotalEnergy();
    }

    void ASAPBlockScheduler::setSuspendReason(AbsRTTask *task, const std::string &reason) {
        if (task) {
            _suspend_reasons[task] = reason;
        }
    }

    std::string ASAPBlockScheduler::getSuspendReason(AbsRTTask *task) const {
        if (!task) {
            return "unknown";
        }
        auto it = _suspend_reasons.find(task);
        if (it != _suspend_reasons.end()) {
            return it->second;
        }
        return "unknown";
    }

    void ASAPBlockScheduler::clearSuspendReason(AbsRTTask *task) {
        if (task) {
            _suspend_reasons.erase(task);
        }
    }

    void ASAPBlockScheduler::setTraceLogger(void *trace) {
        _trace_logger = static_cast<JSONTrace *>(trace);
    }

    void ASAPBlockScheduler::setSemanticTraceEnabled(bool enabled) {
        _semantic_trace_enabled = enabled;
    }

    double ASAPBlockScheduler::calculateTotalEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        ASAPBlockTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [ASAP-Block] calculateTotalEnergyForTask: 任务模型不存在");
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

    double ASAPBlockScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        ConfigManager &configMgr = ConfigManager::getInstance();
        double power_coeff = configMgr.getPowerCoefficient(workload);

        int frequency_mhz = static_cast<int>(frequency);
        double freq_ratio = configMgr.getFrequencyPowerRatio(frequency_mhz);

        double base_power = configMgr.getBasePower();
        double power = base_power * power_coeff * freq_ratio;

        SCHEDULER_LOG_DEBUG(std::string("⚡ [ASAP-Block] 功率计算: ") +
                           "workload=" + workload +
                           " coeff=" + std::to_string(power_coeff) +
                           " freq=" + std::to_string(frequency_mhz) + "MHz" +
                           " freq_ratio=" + std::to_string(freq_ratio) +
                           " base_power=" + std::to_string(base_power) +
                           " → " + std::to_string(power) + "W");

        return power;
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double ASAPBlockScheduler::collectEnergyAtTickBoundary() {
        const double harvested = collectSolarEnergy(SIMUL.getTime());
        _stats.total_energy_harvested += harvested;
        return harvested;
    }

    double ASAPBlockScheduler::collectSolarEnergy(Tick current_time) {
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

    double ASAPBlockScheduler::getSolarIrradiance(int64_t time_ms) {
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
            SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Block] 无法打开太阳能数据文件: ") + _solar_data_file);
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
                SCHEDULER_LOG_WARNING(std::string("⚠️ [ASAP-Block] 解析辐照度失败: ") + e.what());
                return 0.0;
            }
        }

        return 0.0;
    }

    // =====================================================
    // Tick事件调度
    // =====================================================

    void ASAPBlockScheduler::scheduleNextTick() {
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

    ASAPBlockTaskModel *ASAPBlockScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string ASAPBlockScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }
        return task->toString();
    }

    void ASAPBlockScheduler::resetTickDispatchState() {
        _dispatch_selection_order.clear();
        _selected_energy = 0.0;
        _selection_tick = Tick(-1);
        _selection_frozen = false;
        _selection_stopped_by_energy = false;
    }

    void ASAPBlockScheduler::clearTaskTickSelection(AbsRTTask *task) {
        if (!task) {
            return;
        }

        _dispatch_selection_order.erase(
            std::remove(_dispatch_selection_order.begin(), _dispatch_selection_order.end(), task),
            _dispatch_selection_order.end());
        _selected_energy = 0.0;
        for (AbsRTTask *selected : _dispatch_selection_order) {
            _selected_energy += calculateUnitEnergyForTask(selected);
        }
    }

    // =====================================================
    // 配置方法
    // =====================================================

    void ASAPBlockScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [ASAP-Block] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    void ASAPBlockScheduler::setStartTimeOffset(Tick offset) {
        _start_time_offset = offset;
    }

    void ASAPBlockScheduler::setKernel(AbsKernel *kernel) {
        // ⭐ V96修复：重写基类方法，同时设置基类和派生类的_kernel成员
        Scheduler::setKernel(kernel);
        _kernel = dynamic_cast<MRTKernel*>(kernel);
    }

    MRTKernel *ASAPBlockScheduler::getKernel() {
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

    void ASAPBlockScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-Block] newRun - 仿真开始");

        Scheduler::newRun();
        _current_energy = _initial_energy;
        _last_tick_time = SIMUL.getTime();
        _last_collection_time = SIMUL.getTime();
        _last_energy_commit_tick = Tick(-1);
        _has_energy_commit = false;
        _first_tick_scheduled = false;

        _ready_queue.clear();
        _waiting_queue.clear();
        _pending_arrivals.clear();
        resetTickDispatchState();
        _suspend_reasons.clear();

        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;
        _stats.total_energy_consumed = 0.0;
        _stats.total_energy_harvested = 0.0;
        _stats.total_tick_count = 0;

        // 启动第一个tick事件
        scheduleNextTick();

        SCHEDULER_LOG_INFO(std::string("💰 [ASAP-Block] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void ASAPBlockScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [ASAP-Block] endRun - 仿真结束");

        resetTickDispatchState();

        // 仿真结束前，收集最后一次能量
        Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.0001) {
            _current_energy += harvested;
            _stats.total_energy_harvested += harvested;
        }

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [ASAP-Block] ===== TIE调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    void ASAPBlockScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [ASAP-Block] 任务结束: ") + getTaskName(task));

        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);
        clearTaskTickSelection(task);
        _pending_arrivals.erase(task);
        clearSuspendReason(task);

        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [ASAP-Block] 当前能量: ") + std::to_string(_current_energy) + "J");

        // A same-tick dispatch may only reuse the already frozen prefix.
        if (!_ready_queue.empty() && _kernel) {
            _kernel->dispatch();
        }
    }

    bool ASAPBlockScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        return true;
    }

    // =====================================================
    // 统计和调试
    // =====================================================

    void ASAPBlockScheduler::printStats() const {
        SCHEDULER_LOG_INFO("📊 [ASAP-Block] ===== TIE调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  Tick总次数: ") + std::to_string(_stats.total_tick_count));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    std::string ASAPBlockScheduler::getEnergyStatus() const {
        return "当前能量: " + std::to_string(_current_energy) + "J";
    }

    const std::map<AbsRTTask *, std::string> ASAPBlockScheduler::getTaskWorkloads() const {
        std::map<AbsRTTask *, std::string> workloads;
        for (const auto &pair : _task_models) {
            workloads[pair.first] = pair.second->getWorkloadType();
        }
        return workloads;
    }

} // namespace RTSim
