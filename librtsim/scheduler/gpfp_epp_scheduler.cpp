// gpfp_epp_scheduler.cpp - EPP (Energy-aware Preemptive Priority) Scheduler Implementation
// 算法设计文档：EPP_SCHEDULER_DESIGN.md

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <metasim/factory.hpp>
#include <metasim/simul.hpp>
#include <rtsim/scheduler/gpfp_epp_scheduler.hpp>
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

    // 时间类型别名
    using TimeMs = int64_t;

    // =====================================================
    // EPPEnergyRecoveryEvent 实现
    // =====================================================

    EPPEnergyRecoveryEvent::EPPEnergyRecoveryEvent(EPPScheduler *scheduler)
        : MetaSim::Event("EPPEnergyRecoveryEvent", MetaSim::Event::_DEFAULT_PRIORITY - 4),
          _scheduler(scheduler) {
        // 优先级高于默认事件，确保及时处理
    }

    void EPPEnergyRecoveryEvent::doit() {
        if (!_scheduler) {
            return;
        }

        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        SCHEDULER_LOG_INFO(std::string("⏰ [EPP] ===== 能量恢复事件触发 @ ") +
                          std::to_string(current_ms) + "ms =====");

        // 1. ���集能量
        double harvested = _scheduler->collectSolarEnergy(current_time);
        if (harvested > 0.001) {
            SCHEDULER_LOG_INFO(std::string("☀️ [EPP] 收集能量: ") + std::to_string(harvested) + "J");
        }

        // 2. 检查等待队列
        _scheduler->restoreWaitingQueueToReadyQueue();

        // 3. ⭐ 触发MRTKernel的dispatch()
        // 这样可以重新调度任务并生成scheduled事件
        SCHEDULER_LOG_INFO("🔄 [EPP] 能量恢复，触发MRTKernel dispatch");

        // 获取kernel并触发dispatch
        MRTKernel *kernel = _scheduler->getKernel();
        if (kernel) {
            kernel->dispatch();
            SCHEDULER_LOG_INFO("✅ [EPP] MRTKernel dispatch已触发");
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] 无法获取kernel指针");
        }

        SCHEDULER_LOG_INFO("🏁 [EPP] ===== 能量恢复事件结束 =====");
    }

    // =====================================================
    // EPPTaskModel 实现
    // =====================================================

    EPPTaskModel::EPPTaskModel(AbsRTTask *t, int period, int wcet,
                               const std::string &workload_type,
                               double energy_coefficient)
        : TaskModel(t),
          _period(period),
          _wcet(wcet),
          _workload_type(workload_type),
          _energy_coefficient(energy_coefficient),
          _rm_priority(period) {
        // RM优先级：周期越短，优先级越高（数值越小）
    }

    EPPTaskModel::~EPPTaskModel() {}

    MetaSim::Tick EPPTaskModel::getPriority() const {
        return _rm_priority;
    }

    void EPPTaskModel::changePriority(MetaSim::Tick p) {
        _rm_priority = p;
    }

    void EPPTaskModel::setPeriod(int period) {
        _period = period;
        // ⭐ RM优先级：周期越短，优先级越高（数值越小）
        _rm_priority = period;
    }

    // =====================================================
    // EPPScheduler 实现
    // =====================================================

    EPPScheduler::EPPScheduler()
        : Scheduler(),
          _current_energy(0.0),
          _initial_energy(0.0),
          _max_energy(1000.0),
          _recovery_event(nullptr),
          _config_manager(nullptr),
          _last_collection_time(0),
          _pv_efficiency(0.18),
          _pv_area_m2(1.0),
          _use_real_solar_data(false),
          _unit_time(1),
          _enable_energy_recovery(true),
          _max_recovery_wait_time_ms(10000),
          _kernel(nullptr) {

        SCHEDULER_LOG_INFO("🚀 [EPP] EPP Scheduler 初始化");

        // 1. 从ConfigManager获取配置
        ConfigManager &configMgr = ConfigManager::getInstance();
        std::string config_file = configMgr.getConfigFilePath();

        if (config_file.empty()) {
            const char *config_file_env = std::getenv("ENERGY_CONFIG_FILE");
            config_file = config_file_env ? config_file_env : "gpfp_system.yml";
        }

        SCHEDULER_LOG_INFO(std::string("📁 [EPP] 配置文件: ") + config_file);

        // 2. 设置环境变量
        setenv("ENERGY_CONFIG_FILE", config_file.c_str(), 1);

        // 3. 初始化EnergyBridge
        bool bridge_initialized = EnergyBridge::getInstance().initialize(config_file);
        if (bridge_initialized) {
            SCHEDULER_LOG_INFO("✅ [EPP] EnergyBridge 初始化成功");
            _last_collection_time = SIMUL.getTime();

            // ⭐ 读取start_time_offset（用于计算实际时间）
            _start_time_offset = configMgr.getStartTimeOffset();
            SCHEDULER_LOG_INFO(std::string("⏰ [EPP] 开始时间偏移: ") + std::to_string(static_cast<int64_t>(_start_time_offset)) + "ms");

            // ⭐ 读取太阳能配置（从配置文件直接读取）
            try {
                // 检查配置文件是否存在
                std::ifstream yaml_file(config_file);
                if (yaml_file.good()) {
                    // 简单的YAML解析：查找关键字段
                    std::string line;
                    bool in_energy_section = false;

                    while (std::getline(yaml_file, line)) {
                        // 保存原始行（用于判断缩进）
                        std::string original_line = line;

                        // 去除前后空格
                        line.erase(0, line.find_first_not_of(" \t"));
                        line.erase(line.find_last_not_of(" \t") + 1);

                        // 跳过空行和注释行
                        if (line.empty() || line[0] == '#') {
                            continue;
                        }

                        // 检查是否进入energy_management部分
                        if (line.find("energy_management:") != std::string::npos) {
                            in_energy_section = true;
                            continue;
                        }

                        // 检查是否离开energy_management部分（遇到同级或更高级的配置项）
                        if (in_energy_section && !line.empty() && line[0] != '-' && line[0] != '#') {
                            // 计算缩进级别
                            size_t leading_spaces = original_line.find_first_not_of(" \t");
                            // energy_management通常是0个空格缩进（顶级）
                            // 如果遇到0个空格缩进且包含冒号，说明是同级section
                            if (leading_spaces == 0 && line.find(':') != std::string::npos &&
                                line.find("energy_management:") == std::string::npos) {
                                break;
                            }
                        }

                        // 解析配置项
                        if (in_energy_section) {
                            if (line.find("use_real_solar_data:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                value.erase(0, value.find_first_not_of(" \t"));
                                _use_real_solar_data = (value == "true");
                            }
                            else if (line.find("solar_data_file:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                // 去除引号
                                value.erase(0, value.find_first_not_of(" \t\""));
                                value.erase(value.find_last_not_of(" \t\"") + 1);
                                _solar_data_file = value;
                            }
                            else if (line.find("pv_efficiency:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                value.erase(0, value.find_first_not_of(" \t"));
                                _pv_efficiency = std::stod(value);
                            }
                            else if (line.find("pv_area_m2:") != std::string::npos) {
                                std::string value = line.substr(line.find(":") + 1);
                                value.erase(0, value.find_first_not_of(" \t"));
                                _pv_area_m2 = std::stod(value);
                            }
                        }
                    }

                    SCHEDULER_LOG_INFO(std::string("☀️ [EPP] 太阳能配置: ") +
                                      "use_real=" + (_use_real_solar_data ? "true" : "false") +
                                      " file=" + _solar_data_file +
                                      " eff=" + std::to_string(_pv_efficiency) +
                                      " area=" + std::to_string(_pv_area_m2) + "m²");
                } else {
                    SCHEDULER_LOG_WARNING("⚠️ [EPP] 无法打开配置文件，使用默认太阳能配置");
                }
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [EPP] 解析太阳能配置失败: ") + e.what());
            }

            // 读取初始能量
            double bridge_energy = EnergyBridge::getInstance().getCurrentEnergy();
            if (bridge_energy > 0) {
                _initial_energy = bridge_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [EPP] 初始能量: ") + std::to_string(_initial_energy) + "J");
            }
        } else {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] EnergyBridge 初始化失败，使用ConfigManager获取能量");

            // ⭐ Fallback: 从ConfigManager获取初始能量
            _start_time_offset = configMgr.getStartTimeOffset();
            double config_energy = configMgr.getInitialEnergy();
            if (config_energy > 0) {
                _initial_energy = config_energy;
                _current_energy = _initial_energy;
                SCHEDULER_LOG_INFO(std::string("💰 [EPP] 从ConfigManager获取初始能量: ") + std::to_string(_initial_energy) + "J");
            } else {
                SCHEDULER_LOG_ERROR("❌ [EPP] 无法获取初始能量，调度器将无法工作！");
            }
        }

        // 4. 创建能量恢复事件
        _recovery_event = new EPPEnergyRecoveryEvent(this);

        SCHEDULER_LOG_INFO("✅ [EPP] EPP Scheduler 初始化完成");
    }

    EPPScheduler::EPPScheduler(const std::vector<std::string> &params)
        : EPPScheduler() {
        // 委托给默认构造函数
        // 参数可以在后续处理
        // 注意：基类Entity的_name是私有的，无法直接设置
    }

    std::unique_ptr<EPPScheduler>
        EPPScheduler::createInstance(const std::vector<std::string> &params) {
        return std::make_unique<EPPScheduler>(params);
    }

    EPPScheduler::~EPPScheduler() {
        if (_recovery_event) {
            delete _recovery_event;
            _recovery_event = nullptr;
        }

        // 清理任务模型
        for (auto &pair : _task_models) {
            delete pair.second;
        }
        _task_models.clear();
    }

    // =====================================================
    // 核心调度函数 - EPP算法的核心
    // =====================================================

    void EPPScheduler::schedule() {
        // ⭐ 禁用自定义schedule()方法
        // EPP完全依赖MRTKernel::dispatch() -> getTaskN()流程
        // 这样可以确保scheduled事件被正确记录

        SCHEDULER_LOG_WARNING("⚠️ [EPP] schedule()方法已禁用，EPP依赖MRTKernel的dispatch()");

        // 只收集能量和恢复等待队列，不进行调度（让MRTKernel处理）
        MetaSim::Tick current_time = SIMUL.getTime();
        double harvested = collectSolarEnergy(current_time);
        if (harvested > 0.001) {
            SCHEDULER_LOG_INFO(std::string("☀️ [EPP] 收集能量: ") + std::to_string(harvested) + "J");
            _current_energy += harvested;
        }

        // 恢复等待队列
        restoreWaitingQueueToReadyQueue();
    }

    // =====================================================
    // 添加任务
    // =====================================================

    void EPPScheduler::addTask(AbsRTTask *task, const std::string &params) {
        if (!task) {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] addTask: 任务为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📥 [EPP] 添加任务: ") + getTaskName(task));
        SCHEDULER_LOG_DEBUG(std::string("   参数: ") + params);

        // 解析参数
        // 格式: "period=100,wcet=20,workload=bzip2"
        int period = 100;
        int wcet = 20;
        std::string workload = "bzip2";
        double energy_coeff = 1.0;

        // 简单解析（实际应该更健壮）
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

        size_t workload_pos = params.find("workload=");
        if (workload_pos != std::string::npos) {
            size_t comma_pos = params.find(",", workload_pos);
            workload = params.substr(workload_pos + 9,
                comma_pos != std::string::npos ? comma_pos - workload_pos - 9 : std::string::npos);
        }

        // 创建任务模型
        EPPTaskModel *model = new EPPTaskModel(task, period, wcet, workload, energy_coeff);
        enqueueModel(model);  // ⭐ 关键：将模型添加到基类
        _task_models[task] = model;

        // 添加到就绪队列
        addToReadyQueue(task);

        SCHEDULER_LOG_INFO(std::string("✅ [EPP] 任务已添加: 周期=") + std::to_string(period) +
                          " WCET=" + std::to_string(wcet) +
                          " 工作负载=" + workload);
    }

    // =====================================================
    // 移除任务
    // =====================================================

    void EPPScheduler::removeTask(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [EPP] 移除任务: ") + getTaskName(task));

        // 从队列移除
        removeFromReadyQueue(task);
        removeFromWaitingQueue(task);

        // 从运行任务移除
        for (auto &map_pair : _running_tasks) {
            if (map_pair.second == task) {
                _running_tasks[map_pair.first] = nullptr;
            }
        }

        // 删除任务模型
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            delete it->second;
            _task_models.erase(it);
        }

        SCHEDULER_LOG_INFO(std::string("✅ [EPP] 任务已移除: ") + getTaskName(task));
    }

    // =====================================================
    // getFirst - 获取第一个要调度的任务（实现能量约束）
    // =====================================================

    AbsRTTask *EPPScheduler::getFirst() {
        // ⭐ 关键：实现前瞻性能量硬约束
        // 使用新逻辑：能量_当前 + 能量_收集 >= 能量_消耗

        SCHEDULER_LOG_DEBUG(std::string("🔍 [EPP] getFirst() 被调用") +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // 1. 检查就绪队列
        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] getFirst: 就绪队列为空");
            return nullptr;
        }

        // 2. 获取最高优先级任务
        AbsRTTask *first_task = _ready_queue.front();

        if (!first_task) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] getFirst: 队列首任务为空");
            return nullptr;
        }

        // 3. ⭐ 使用新的前瞻性能量判断
        Tick current_time = SIMUL.getTime();
        bool can_schedule = canScheduleWithEnergy(first_task, current_time);

        if (!can_schedule) {
            SCHEDULER_LOG_INFO(std::string("❌ [EPP] getFirst: 前瞻性判断能量不足，停止调度") +
                              " 任务: " + getTaskName(first_task));

            // ⭐ 能量不足时，不返回任何任务，阻止调度
            // 任务保留在就绪队列中，等待能量恢复

            // 4. 启动能量恢复
            if (_enable_energy_recovery && !_recovery_event->isInQueue()) {
                double energy_needed = calculateEnergyForTask(first_task);
                double energy_deficit = energy_needed - _current_energy;
                Tick recovery_time = calculateEnergyRecoveryTime(energy_deficit);

                SCHEDULER_LOG_INFO(std::string("⏰ [EPP] 启动能量恢复: ") +
                                  "缺口: " + std::to_string(energy_deficit) + "J" +
                                  " 预计恢复: " + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");

                scheduleEnergyRecoveryEvent(recovery_time);
            }

            return nullptr;
        }

        // 5. ⭐ 新增：扣减能量（预扣减策略）
        double energy_needed = calculateEnergyForTask(first_task);
        std::string task_name = getTaskName(first_task);

        if (!consumeEnergy(energy_needed, task_name)) {
            // 扣减失败（理论上不会发生，因为前面已经检查过）
            SCHEDULER_LOG_ERROR(std::string("❌ [EPP] getFirst: consumeEnergy失败，不应该发生") +
                                " 任务=" + task_name);
            return nullptr;
        }

        // 6. ✅ 能量已扣减，返回任务
        SCHEDULER_LOG_INFO(std::string("✅ [EPP] getFirst: 能量已扣减，返回任务: ") +
                          task_name +
                          " 当前能量: " + std::to_string(_current_energy) + "J");

        return first_task;
    }

    // =====================================================
    // getTaskN - 获取第n个要调度的任务（实现级联调度）
    // =====================================================

    AbsRTTask *EPPScheduler::getTaskN(unsigned int n) {
        // ⭐ 级联调度关键：每次调用getTaskN()时检查能量
        // MRTKernel会连续调用getTaskN(0), getTaskN(1), getTaskN(2)...
        // 使用新逻辑：前瞻性能量判断

        SCHEDULER_LOG_DEBUG(std::string("🔍 [EPP] getTaskN(") + std::to_string(n) + ") 被调用" +
                           " 当前能量: " + std::to_string(_current_energy) + "J");

        // 1. 检查就绪队列
        if (_ready_queue.empty()) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] getTaskN: 就绪队列为空");
            return nullptr;
        }

        // 2. 检查索引是否越界
        if (n >= _ready_queue.size()) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] getTaskN: 索引超出队列大小");
            return nullptr;
        }

        // 3. 获取第n个任务
        AbsRTTask *task = _ready_queue[n];

        if (!task) {
            SCHEDULER_LOG_DEBUG("📭 [EPP] getTaskN: 第" + std::to_string(n) + "个任务为空");
            return nullptr;
        }

        // 4. ⭐ 使用新的前瞻性能量判断
        Tick current_time = SIMUL.getTime();
        bool can_schedule = canScheduleWithEnergy(task, current_time);

        if (!can_schedule) {
            SCHEDULER_LOG_INFO(std::string("❌ [EPP] getTaskN: 前瞻性判断能量不足，停止级联调度") +
                              " 第" + std::to_string(n) + "个任务: " + getTaskName(task) +
                              " ⭐ 级联调度在此停止");

            // ⭐ 能量不足时，立即停止级联调度
            // 后续的低优先级任务不会被调度

            // 5. 启动能量恢复
            if (_enable_energy_recovery && !_recovery_event->isInQueue()) {
                double energy_needed = calculateEnergyForTask(task);
                double energy_deficit = energy_needed - _current_energy;
                Tick recovery_time = calculateEnergyRecoveryTime(energy_deficit);

                SCHEDULER_LOG_INFO(std::string("⏰ [EPP] 启动能量恢复: ") +
                                  "缺口: " + std::to_string(energy_deficit) + "J" +
                                  " 预计恢复: " + std::to_string(static_cast<int64_t>(recovery_time)) + "ms");

                scheduleEnergyRecoveryEvent(recovery_time);
            }

            return nullptr;  // ⭐ 停止级联调度
        }

        // 6. ⭐ 检查是否已经预付过能量（防止重复扣减）
        double energy_needed = calculateEnergyForTask(task);
        std::string task_name = getTaskName(task);

        auto prepaid_it = _task_prepaid_energy.find(task);
        if (prepaid_it != _task_prepaid_energy.end() && prepaid_it->second > 0) {
            // 已经预付过能量，直接返回任务，不再扣减
            SCHEDULER_LOG_DEBUG(std::string("⚠️ [EPP] getTaskN: 任务 ") + task_name +
                              " 已预付能量，跳过重复扣减");
            return task;
        }

        // 7. ⭐ 首次调度此任务，扣减能量（预扣减策略）
        if (!consumeEnergy(energy_needed, task_name)) {
            // 扣减失败（理论上不会发生，因为前面已经检查过）
            SCHEDULER_LOG_ERROR(std::string("❌ [EPP] getTaskN: consumeEnergy失败") +
                                " 任务=" + task_name);
            return nullptr;
        }

        // 8. ⭐ 标记任务已预付能量
        _task_prepaid_energy[task] = energy_needed;

        // 9. ✅ 能量已扣减，返回任务（继续级联调度）
        SCHEDULER_LOG_INFO(std::string("✅ [EPP] getTaskN: 能量已扣减，返回任务 #") +
                          std::to_string(n) + ": " + task_name +
                          " 当前能量: " + std::to_string(_current_energy) + "J" +
                          " ⭐ 级联调度继续");

        return task;
    }

    // =====================================================
    // 任务到达事件处理
    // =====================================================

    void EPPScheduler::onTaskArrival(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📍 [EPP] 任务到达: ") + getTaskName(task));

        // ⭐ 清除该任务的预付能量标记（允许新实例重新扣减能量）
        auto prepaid_it = _task_prepaid_energy.find(task);
        if (prepaid_it != _task_prepaid_energy.end()) {
            SCHEDULER_LOG_DEBUG(std::string("🧹 [EPP] 清除任务预付能量标记: ") + getTaskName(task) +
                              " 之前预付: " + std::to_string(prepaid_it->second) + "J");
            _task_prepaid_energy.erase(prepaid_it);
        }

        // 添加到就绪队列
        if (!isInReadyQueue(task) && !isInWaitingQueue(task)) {
            addToReadyQueue(task);

            // ⭐ Tick级抢占检查
            checkAndPreempt();
        }
    }

    // =====================================================
    // Tick级抢占检查
    // =====================================================

    void EPPScheduler::checkAndPreempt() {
        SCHEDULER_LOG_DEBUG("🔔 [EPP] Tick级抢占检查");

        checkAndPreemptOnAllCPUs();
    }

    // =====================================================
    // 队列管理方法
    // =====================================================

    void EPPScheduler::insert(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("➕ [EPP] insert: ") + getTaskName(task));

        // ⭐ 关键修复：调用基类insert以维护_queue
        Scheduler::insert(task);

        // 添加到EPP的就绪队列
        addToReadyQueue(task);
    }

    void EPPScheduler::extract(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("➖ [EPP] extract: ") + getTaskName(task));

        // ⭐ 关键修复：调用基类extract以维护_queue
        Scheduler::extract(task);

        // 从EPP的就绪队列中移除任务
        removeFromReadyQueue(task);

        // 从等待队列中移除（如果存在）
        removeFromWaitingQueue(task);
    }

    void EPPScheduler::addToReadyQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // 从等待队列移除（如果存在）
        removeFromWaitingQueue(task);

        // 按RM优先级插入（周期短的优先）
        EPPTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] addToReadyQueue: 任务模型不存在");
            _ready_queue.push_back(task);
            return;
        }

        Tick priority = model->getRMPriority();

        // 找到插入位置（保持优先级排序）
        auto it = _ready_queue.begin();
        while (it != _ready_queue.end()) {
            EPPTaskModel *other_model = getTaskModel(*it);
            if (other_model && other_model->getRMPriority() > priority) {
                break;
            }
            ++it;
        }

        _ready_queue.insert(it, task);

        SCHEDULER_LOG_DEBUG(std::string("➕ [EPP] 任务加入就绪队列: ") + getTaskName(task) +
                           " 优先级=" + std::to_string(static_cast<int64_t>(priority)));
    }

    void EPPScheduler::addToWaitingQueue(AbsRTTask *task) {
        if (!task) {
            return;
        }

        // 从就绪队列移除
        removeFromReadyQueue(task);

        _waiting_queue.push_back(task);

        SCHEDULER_LOG_DEBUG(std::string("⏸️ [EPP] 任务加入等待队列: ") + getTaskName(task));
    }

    void EPPScheduler::removeFromReadyQueue(AbsRTTask *task) {
        auto it = std::find(_ready_queue.begin(), _ready_queue.end(), task);
        if (it != _ready_queue.end()) {
            _ready_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [EPP] 任务从就绪队列移除: ") + getTaskName(task));
        }
    }

    void EPPScheduler::removeFromWaitingQueue(AbsRTTask *task) {
        auto it = std::find(_waiting_queue.begin(), _waiting_queue.end(), task);
        if (it != _waiting_queue.end()) {
            _waiting_queue.erase(it);
            SCHEDULER_LOG_DEBUG(std::string("➖ [EPP] 任务从等待队列移除: ") + getTaskName(task));
        }
    }

    AbsRTTask *EPPScheduler::getHighestPriorityTaskFromReadyQueue() {
        if (_ready_queue.empty()) {
            return nullptr;
        }
        return _ready_queue.front();
    }

    bool EPPScheduler::isInReadyQueue(AbsRTTask *task) const {
        return std::find(_ready_queue.begin(), _ready_queue.end(), task) != _ready_queue.end();
    }

    bool EPPScheduler::isInWaitingQueue(AbsRTTask *task) const {
        return std::find(_waiting_queue.begin(), _waiting_queue.end(), task) != _waiting_queue.end();
    }

    void EPPScheduler::restoreWaitingQueueToReadyQueue() {
        if (_waiting_queue.empty()) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("🔄 [EPP] 恢复等待队列到就绪队列: ") +
                          std::to_string(_waiting_queue.size()) + "个任务");

        // 将所有等待任务移回就绪队列
        while (!_waiting_queue.empty()) {
            AbsRTTask *task = _waiting_queue.front();
            _waiting_queue.erase(_waiting_queue.begin());  // vector不支持pop_front
            addToReadyQueue(task);
        }
    }

    // =====================================================
    // 能量计算方法
    // =====================================================

    double EPPScheduler::calculateEnergyForTask(AbsRTTask *task) {
        if (!task) {
            return 0.0;
        }

        EPPTaskModel *model = getTaskModel(task);
        if (!model) {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] calculateEnergyForTask: 任务模型不存在");
            return 0.0;
        }

        // ⭐ 计算完整WCET的能耗（能量硬约束需要检查完整任务能耗）
        Tick wcet = model->getWCET();
        return calculateEnergyForWCET(task, wcet);
    }

    double EPPScheduler::calculateEnergyForWCET(AbsRTTask *task, Tick wcet) {
        if (!task || wcet <= 0) {
            return 0.0;
        }

        EPPTaskModel *model = getTaskModel(task);
        if (!model) {
            return 0.0;
        }

        // 简化计算：功率 × 时间
        // 实际应该从功率模型获取
        std::string workload = model->getWorkloadType();
        double power = calculatePowerForWorkload(workload, 8100.0); // 8.1 GHz

        // 能量 = 功率(W) × 时间(s)
        // 1 Tick = 1 ms = 0.001 s
        double wcet_seconds = static_cast<double>(wcet) * 0.001;
        double energy = power * wcet_seconds;

        // 应用能量系数
        energy *= model->getEnergyCoefficient();

        return energy;
    }

    double EPPScheduler::calculatePowerForWorkload(const std::string &workload, double frequency) {
        // 简化功率模型（实际应该从配置文件读取）
        if (workload == "idle") {
            return 0.1; // 100 mW
        } else if (workload == "bzip2") {
            return 2.0; // 2 W
        } else if (workload == "hash") {
            return 1.8; // 1.8 W
        } else if (workload == "control") {
            return 0.5; // 500 mW
        }

        return 1.0; // 默认 1 W
    }

    // =====================================================
    // ⭐ 前瞻性能量判断（新逻辑）
    // =====================================================

    double EPPScheduler::predictEnergyCollection(Tick current_time, Tick duration) {
        // ⭐ 预测：在duration时间内能收集多少太阳能
        // 使用当前时刻的辐照度（简化假设：辐照度不变）
        // 更精确的做法：积分计算（考虑辐照度变化）

        double irradiance = getSolarIrradiance(current_time);

        // 能量(J) = 辐照度(W/m²) × 面积(m²) × 效率 × 时间(s)
        double duration_seconds = static_cast<double>(duration) * 0.001;
        double energy = irradiance * _pv_area_m2 * _pv_efficiency * duration_seconds;

        SCHEDULER_LOG_DEBUG(std::string("🔮 [EPP] 预测能量收集: ") +
                           "时长=" + std::to_string(static_cast<int64_t>(duration)) + "ms" +
                           " 辐照度=" + std::to_string(irradiance) + "W/m²" +
                           " 预计收集=" + std::to_string(energy) + "J");

        return energy;
    }

    bool EPPScheduler::canScheduleWithEnergy(AbsRTTask *task, Tick current_time) {
        if (!task) {
            return false;
        }

        // ⭐ 新逻辑：前瞻性能量判断
        // 判断条件：能量_当前 + 能量_收集 >= 能量_消耗

        // 1. 能量_当前
        double energy_current = _current_energy;

        // 2. 能量_消耗（完整WCET）
        double energy_consumption = calculateEnergyForTask(task);

        // 3. 获取任务WCET
        EPPTaskModel *model = getTaskModel(task);
        if (!model) {
            return false;
        }
        Tick wcet = model->getWCET();

        // 4. 能量_收集（任务执行期间）
        double energy_collection = predictEnergyCollection(current_time, wcet);

        // 5. ⭐ 核心判断
        double energy_after_task = energy_current + energy_collection - energy_consumption;
        bool can_schedule = energy_after_task >= 0.0;

        SCHEDULER_LOG_INFO(std::string("🔮 [EPP] 前瞻性能量判断: ") + getTaskName(task) +
                          " 当前=" + std::to_string(energy_current) + "J" +
                          " 收集(预测)=" + std::to_string(energy_collection) + "J" +
                          " 消耗=" + std::to_string(energy_consumption) + "J" +
                          " 结余=" + std::to_string(energy_after_task) + "J" +
                          (can_schedule ? " ✅可调度" : " ❌不可调度"));

        return can_schedule;
    }

    // =====================================================
    // 能量收集方法
    // =====================================================

    double EPPScheduler::collectSolarEnergy(Tick current_time) {
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
        // 能量(J) = 辐照度(W/m²) × 面积(m²) × 效率 × 时间(s)
        double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
        double energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;

        // 更新最后收集时间
        _last_collection_time = current_time;

        return energy;
    }

    double EPPScheduler::getSolarIrradiance(int64_t time_ms) {
        // ⭐ 使用_start_time_offset计算实际时间
        // time_ms是仿真时间（从0开始），需要加上_start_time_offset（实际一天中的时间）

        if (!_use_real_solar_data) {
            // 如果不使用真实数据，使用简化模型
            int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
            int64_t hour_of_day = (actual_time_ms % 86400000) / 3600000;

            if (hour_of_day >= 6 && hour_of_day <= 18) {
                // 白天：假设 500 W/m²
                return 500.0;
            } else {
                // 晚上：0 W/m²
                return 0.0;
            }
        }

        // ⭐ 使用真实NASA太阳能数据
        // 数据文件格式：每行一个辐照度值（W/m²）
        // 第1行：标题 "irradiance_W_per_m2"
        // 第2行开始：数据（每分钟一个值）
        // 总共532800行 = 365天 × 1440分钟/天
        //
        // 计算索引：
        // actual_time_ms % 86400000 -> 当天的毫秒数
        // / 60000 -> 当天的分钟数 (0-1439)
        // + 2 -> +2跳过标题行，得到实际行号

        int64_t actual_time_ms = time_ms + static_cast<int64_t>(_start_time_offset);
        int64_t minute_of_day = (actual_time_ms % 86400000) / 60000;  // 0-1439

        // 计算文件中的行号（跳过标题行）
        int line_number = minute_of_day + 2;  // +2因为第1行是标题

        // 读取文件
        std::ifstream file(_solar_data_file);
        if (!file.is_open()) {
            SCHEDULER_LOG_WARNING(std::string("⚠️ [EPP] 无法打开太阳能数据文件: ") + _solar_data_file);
            return 0.0;
        }

        // 跳到指定行
        std::string line;
        int current_line = 1;
        while (current_line < line_number && std::getline(file, line)) {
            current_line++;
        }

        // 读取目标行
        if (std::getline(file, line)) {
            try {
                double irradiance = std::stod(line);
                return irradiance;
            } catch (const std::exception &e) {
                SCHEDULER_LOG_WARNING(std::string("⚠️ [EPP] 解析辐照度失败: ") + e.what() +
                                     " (line: " + line + ")");
                return 0.0;
            }
        }

        SCHEDULER_LOG_WARNING(std::string("⚠️ [EPP] 读取太阳能数据失败: line_number=") +
                             std::to_string(line_number));
        return 0.0;
    }

    Tick EPPScheduler::calculateEnergyRecoveryTime(double energy_needed) {
        if (energy_needed <= 0) {
            return 0;
        }

        // 计算需要多少时间才能收集到足够的能量
        // 时间(s) = 能量(J) / (功率(W) × 面积(m²) × 效率)

        // 假设平均辐照度为 500 W/m²
        double avg_irradiance = 500.0;
        double power_output = avg_irradiance * _pv_area_m2 * _pv_efficiency; // W

        if (power_output <= 0) {
            // 无法收集能量
            return _max_recovery_wait_time_ms;
        }

        double time_seconds = energy_needed / power_output;
        Tick time_ms = static_cast<Tick>(time_seconds * 1000);

        // 限制最大等待时间
        return std::min(time_ms, static_cast<Tick>(_max_recovery_wait_time_ms));
    }

    // =====================================================
    // 能量恢复管理
    // =====================================================

    void EPPScheduler::scheduleEnergyRecoveryEvent(Tick delay) {
        if (!_recovery_event) {
            return;
        }

        if (delay <= 0) {
            delay = 1; // 至少1 Tick
        }

        SCHEDULER_LOG_DEBUG(std::string("⏰ [EPP] 调度能量恢复事件: 延迟=") +
                           std::to_string(static_cast<int64_t>(delay)) + "ms");

        _recovery_event->post(SIMUL.getTime() + delay);
    }

    void EPPScheduler::cancelEnergyRecoveryEvent() {
        if (_recovery_event) {
            _recovery_event->drop();
        }
    }

    // =====================================================
    // 任务管理方法
    // =====================================================

    EPPTaskModel *EPPScheduler::getTaskModel(AbsRTTask *task) {
        auto it = _task_models.find(task);
        if (it != _task_models.end()) {
            return it->second;
        }
        return nullptr;
    }

    std::string EPPScheduler::getTaskName(AbsRTTask *task) {
        if (!task) {
            return "nullptr";
        }

        // 使用toString()而不是getName()
        return task->toString();
    }

    // =====================================================
    // 抢占检查方法
    // =====================================================

    void EPPScheduler::checkAndPreemptOnAllCPUs() {
        // 检查所有CPU上是否有需要被抢占的任务
        for (auto &map_pair : _running_tasks) {
            CPU *cpu = map_pair.first;
            AbsRTTask *running_task = map_pair.second;

            if (!running_task) {
                continue;
            }

            // 获取就绪队列最高优先级任务
            AbsRTTask *highest = getHighestPriorityTaskFromReadyQueue();
            if (!highest) {
                continue;
            }

            // 检查是否需要抢占
            if (shouldPreempt(cpu, highest)) {
                SCHEDULER_LOG_INFO(std::string("🔄 [EPP] 抢占CPU: ") +
                                  " 高优先级任务=" + getTaskName(highest));

                // TODO: 实际的抢占逻辑
                // 挂起当前任务
                // running_task->deschedule();

                // 添加到就绪队列
                // addToReadyQueue(running_task);

                // 调度新任务
                // dispatchTask(highest, cpu);
            }
        }
    }

    bool EPPScheduler::shouldPreempt(CPU *cpu, AbsRTTask *new_task) {
        if (!cpu || !new_task) {
            return false;
        }

        // 获取CPU上运行的任务
        AbsRTTask *running_task = getRunningTaskOnCPU(cpu);
        if (!running_task) {
            return false;
        }

        // 比较优先级
        EPPTaskModel *running_model = getTaskModel(running_task);
        EPPTaskModel *new_model = getTaskModel(new_task);

        if (!running_model || !new_model) {
            return false;
        }

        // 新任务优先级更高（RM优先级数值越小越高）
        return new_model->getRMPriority() < running_model->getRMPriority();
    }

    // =====================================================
    // 调度辅助方法
    // =====================================================

    int EPPScheduler::getFreeCPUCount() {
        int count = 0;
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                count++;
            }
        }
        return count;
    }

    CPU *EPPScheduler::getFreeCPU() {
        for (auto &pair : _running_tasks) {
            if (pair.second == nullptr) {
                return pair.first;
            }
        }
        return nullptr;
    }

    void EPPScheduler::dispatchTask(AbsRTTask *task, CPU *cpu) {
        if (!task || !cpu) {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] dispatchTask: 任务或CPU为空");
            return;
        }

        SCHEDULER_LOG_INFO(std::string("📤 [EPP] 调度任务: ") + getTaskName(task) +
                          " 到CPU");

        // 从就绪队列移除
        removeFromReadyQueue(task);

        // 记录运行任务
        _running_tasks[cpu] = task;

        // 调度任务（通过MRTKernel）
        // 这里需要实际的调度逻辑
        // task->schedule();
    }

    AbsRTTask *EPPScheduler::getRunningTaskOnCPU(CPU *cpu) {
        if (!cpu) {
            return nullptr;
        }

        auto it = _running_tasks.find(cpu);
        if (it != _running_tasks.end()) {
            return it->second;
        }

        return nullptr;
    }

    // =====================================================
    // consumeEnergy - 能量扣减
    // =====================================================

    bool EPPScheduler::consumeEnergy(double energy_joules, const std::string &task_name) {
        // ⭐ 检查能量是否足够
        const double EPSILON = 1e-9;
        if (_current_energy < energy_joules - EPSILON) {
            SCHEDULER_LOG_WARNING(std::string("❌ [EPP] consumeEnergy: 能量不足") +
                                 " 需要=" + std::to_string(energy_joules) + "J" +
                                 " 当前=" + std::to_string(_current_energy) + "J" +
                                 " 任务=" + task_name);
            return false;
        }

        // ⭐ 扣减能量
        double old_energy = _current_energy;
        _current_energy -= energy_joules;

        SCHEDULER_LOG_INFO(std::string("⚡ [EPP] consumeEnergy: ") +
                          "任务=" + task_name +
                          " 扣减=" + std::to_string(energy_joules) + "J" +
                          " " + std::to_string(old_energy) + "J → " + std::to_string(_current_energy) + "J");

        return true;
    }

    void EPPScheduler::setPVConfig(double efficiency, double area, const std::string &solar_file) {
        _pv_efficiency = efficiency;
        _pv_area_m2 = area;
        _solar_data_file = solar_file;

        SCHEDULER_LOG_INFO(std::string("⚙️ [EPP] 太阳能配置更新: ") +
                          "效率=" + std::to_string(efficiency) +
                          " 面积=" + std::to_string(area) + "m²" +
                          " 数据文件=" + solar_file);
    }

    // =====================================================
    // 缺失的Scheduler接口方法实现
    // =====================================================

    void EPPScheduler::notify(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_DEBUG(std::string("📢 [EPP] notify被调用: ") + getTaskName(task));

        // ⭐ EPP算法: 不在这里扣减能量,能量已在getTaskN()/getFirst()中预扣减
        // 这里只做记账操作

        // 更新能量记账
        auto it = _energy_accounts.find(task);
        if (it != _energy_accounts.end()) {
            TaskEnergyAccount &account = it->second;
            account.consumed += 0.001;  // 1ms的能量消耗(简化)
        }
    }

    void EPPScheduler::newRun() {
        SCHEDULER_LOG_INFO("🏁 [EPP] newRun - 仿真开始");

        // 初始化能量
        _current_energy = _initial_energy;

        // 清空所有队列
        _ready_queue.clear();
        _waiting_queue.clear();
        _waiting_queue_deque.clear();

        // 清空记账系统
        _energy_accounts.clear();

        // 重置统计
        _stats.total_scheduled = 0;
        _stats.total_task_completions = 0;
        _stats.total_skipped_energy = 0;
        _stats.total_deadline_misses = 0;

        SCHEDULER_LOG_INFO(std::string("💰 [EPP] 初始能量: ") + std::to_string(_current_energy) + "J");
    }

    void EPPScheduler::endRun() {
        SCHEDULER_LOG_INFO("🏁 [EPP] endRun - 仿真结束");

        // 打印统计信息
        SCHEDULER_LOG_INFO("📊 [EPP] ===== EPP调度统计 =====");
        SCHEDULER_LOG_INFO(std::string("  总调度次数: ") + std::to_string(_stats.total_scheduled));
        SCHEDULER_LOG_INFO(std::string("  任务完成数: ") + std::to_string(_stats.total_task_completions));
        SCHEDULER_LOG_INFO(std::string("  能量不足跳过: ") + std::to_string(_stats.total_skipped_energy));
        SCHEDULER_LOG_INFO(std::string("  Deadline Miss: ") + std::to_string(_stats.total_deadline_misses));
        SCHEDULER_LOG_INFO(std::string("  总消耗能量: ") + std::to_string(_stats.total_energy_consumed) + "J");
        SCHEDULER_LOG_INFO(std::string("  总收集能量: ") + std::to_string(_stats.total_energy_harvested) + "J");
        SCHEDULER_LOG_INFO(std::string("  剩余能量: ") + std::to_string(_current_energy) + "J");
        SCHEDULER_LOG_INFO("=================================");
    }

    bool EPPScheduler::isAdmissible(CPU *c, std::vector<AbsRTTask *> tasks,
                                    AbsRTTask *t) {
        // ⭐ EPP算法的可调度性检查
        // 简化实现:总是返回true,实际应进行更严格的检查
        return true;
    }

    void EPPScheduler::onTaskEnd(AbsRTTask *task) {
        if (!task) {
            return;
        }

        SCHEDULER_LOG_INFO(std::string("✅ [EPP] 任务结束: ") + getTaskName(task));

        // ⭐ 方案1：在任务结束时收集太阳能
        // 收集从上次收集到现在的能量
        MetaSim::Tick current_time = SIMUL.getTime();
        int64_t current_ms = static_cast<int64_t>(current_time);

        // 计算时间差
        Tick elapsed = current_time - _last_collection_time;

        // 第一次调用时，_last_collection_time=0，所以elapsed=current_time
        // 之后每次任务结束时都会收集
        if (elapsed > 0) {
            // 获取当前辐照度
            double irradiance = getSolarIrradiance(current_ms);

            // 计算收集能量
            double elapsed_seconds = static_cast<double>(elapsed) * 0.001;
            double energy = irradiance * _pv_area_m2 * _pv_efficiency * elapsed_seconds;

            if (energy > 0.0001) {
                _current_energy += energy;
                _stats.total_energy_harvested += energy;

                SCHEDULER_LOG_INFO(std::string("☀️ [EPP] 任务结束时收集太阳能: ") +
                                  std::to_string(energy) + "J" +
                                  " (elapsed=" + std::to_string(static_cast<int64_t>(elapsed)) + "ms)" +
                                  " (辐照度=" + std::to_string(irradiance) + " W/m²)" +
                                  " (总收集: " + std::to_string(_stats.total_energy_harvested) + "J)");
            }
        }

        // 更新最后收集时间
        _last_collection_time = current_time;

        // ⭐ 能量结算:退还未使用的能量
        settleEnergyAccount(task);

        // 从运行任务映射中移除
        for (auto &pair : _running_tasks) {
            if (pair.second == task) {
                pair.second = nullptr;
                break;
            }
        }

        // 更新统计
        _stats.total_task_completions++;

        SCHEDULER_LOG_INFO(std::string("📊 [EPP] 当前能量: ") + std::to_string(_current_energy) + "J");
    }

    // =====================================================
    // ⭐ EPP能量结算实现
    // =====================================================

    void EPPScheduler::settleEnergyAccount(AbsRTTask *task) {
        if (!task) {
            return;
        }

        auto it = _energy_accounts.find(task);
        if (it == _energy_accounts.end()) {
            SCHEDULER_LOG_WARNING("⚠️ [EPP] settleEnergyAccount: 任务没有能量账户");
            return;
        }

        TaskEnergyAccount &account = it->second;

        // 计算实际消耗
        double actual_consumed = account.consumed;

        // 计算未使用的能量(预扣减 - 实际消耗)
        double refund = account.prepaid - actual_consumed;

        if (refund > 0.001) {
            // 退还未使用的能量
            _current_energy += refund;

            SCHEDULER_LOG_INFO(std::string("💰 [EPP] 能量结算: ") +
                              "任务=" + getTaskName(task) +
                              " 预扣=" + std::to_string(account.prepaid) + "J" +
                              " 实际=" + std::to_string(actual_consumed) + "J" +
                              " 退款=" + std::to_string(refund) + "J" +
                              " 当前能量=" + std::to_string(_current_energy) + "J");
        }

        // 清除账户
        _energy_accounts.erase(it);
    }

    // =====================================================
    // getKernel/setKernel 实现
    // =====================================================

    MRTKernel *EPPScheduler::getKernel() {
        // 如果还没有设置kernel，尝试从活跃任务中获取
        if (!_kernel && !_ready_queue.empty()) {
            AbsRTTask *task = _ready_queue.front();
            if (task) {
                _kernel = dynamic_cast<MRTKernel *>(task->getKernel());
            }
        }
        return _kernel;
    }

    void EPPScheduler::setKernel(MRTKernel *kernel) {
        _kernel = kernel;
    }

} // namespace RTSim
