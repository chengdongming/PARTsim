// 修复后的TGF getTaskN()方法
// 替换 gpfp_tgf_scheduler.cpp 中的第620-781行

AbsRTTask *TGFScheduler::getTaskN(unsigned int n) {
    // ✅ 修复：真正的全队列贪婪扫描
    // 不再依赖"第n个任务"的语义，改为"返回第一个能量足够的任务"

    // 能量耗尽检查
    if (_energy_depleted) {
        SCHEDULER_LOG_DEBUG(std::string("💀 [TGF] 能量已耗尽，跳过调度") +
                           " n=" + std::to_string(n) +
                           " energy=" + std::to_string(_current_energy * 1000) + " mJ");
        return nullptr;
    }

    SCHEDULER_LOG_DEBUG(std::string("🔍 [TGF] getTaskN(") + std::to_string(n) + ") - 全队列贪婪扫描" +
                       " 当前能量: " + std::to_string(_current_energy * 1000) + " mJ" +
                       " 已调度能耗=" + std::to_string(_dispatching_tasks_total_energy * 1000) + " mJ");

    if (_ready_queue.empty()) {
        SCHEDULER_LOG_DEBUG("📭 [TGF] getTaskN: 就绪队列为空");
        return nullptr;
    }

    const double EPSILON = 1e-9;

    // ✅ 全队列贪婪扫描：从高优先级到低优先级遍历所有任务
    for (size_t i = 0; i < _ready_queue.size(); ++i) {
        AbsRTTask *task = _ready_queue[i];

        if (!task) {
            continue;
        }

        // ✅ 跳过已在本tick中调度的任务
        if (_counted_tasks_in_dispatch.find(task) != _counted_tasks_in_dispatch.end()) {
            SCHEDULER_LOG_DEBUG(std::string("  ⏭️ [TGF] 跳过已调度任务: ") + getTaskName(task));
            continue;
        }

        // ✅ 跳过运行中的任务
        bool is_running = false;
        if (_kernel) {
            CPU *proc = _kernel->getProcessor(task);
            is_running = (proc != nullptr);
        }

        if (is_running) {
            SCHEDULER_LOG_DEBUG(std::string("  ⏭️ [TGF] 跳过运行中任务: ") + getTaskName(task));
            continue;
        }

        // ✅ 检查能量
        double unit_energy = calculateUnitEnergyForTask(task);
        double available_energy = _current_energy - _dispatching_tasks_total_energy;

        SCHEDULER_LOG_DEBUG(std::string("  🔍 [TGF] 检查任务[") + std::to_string(i) + "]: " +
                           getTaskName(task) +
                           " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                           " 剩余=" + std::to_string(available_energy * 1000) + " mJ");

        if (available_energy >= unit_energy - EPSILON) {
            // ✅ 找到第一个能量足够的任务！

            // ✅ 只在tick边界扣除能量（配合Bug #3修复）
            if (_in_tick_boundary_dispatch) {
                _current_energy -= unit_energy;
                _stats.total_energy_consumed += unit_energy;
            }

            _counted_tasks_in_dispatch.insert(task);
            _newly_dispatched_this_tick.insert(task);

            SCHEDULER_LOG_INFO(std::string("✅ [TGF] 贪心策略：调度任务") +
                              " [" + std::to_string(i) + "]" + getTaskName(task) +
                              " 能量=" + std::to_string(unit_energy * 1000) + " mJ" +
                              " 剩余=" + std::to_string((available_energy - unit_energy) * 1000) + " mJ");

            return task;
        }

        // ⭐ 能量不足：跳过，继续搜索（贪心策略）
        SCHEDULER_LOG_INFO(std::string("  ⏭️ [TGF] 能量不足，跳过（贪心策略）") +
                          " [" + std::to_string(i) + "]" + getTaskName(task) +
                          " 需要=" + std::to_string(unit_energy * 1000) + " mJ" +
                          " 剩余=" + std::to_string(available_energy * 1000) + " mJ");
    }

    // 遍历完整个队列，没有找到能量足够的任务
    SCHEDULER_LOG_INFO(std::string("⚠️ [TGF] 全队列扫描完成，无能量足够的任务"));
    return nullptr;
}
