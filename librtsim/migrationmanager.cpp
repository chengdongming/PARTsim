// RTSim
#include <rtsim/migrationmanager.hpp>

namespace RTSim {

    void MigrationManager::addTaskEvent(AbsRTTask *task, Tick when,
                                        EventType event, CPU *cpu) {
        auto wl = (cpu == nullptr ? "" : cpu->getWorkload());
        _tasks_history.emplace_back(task, when, event, cpu, wl);
    }

    std::string MigrationManager::mapEventType(EventType e) const {
        switch (e) {
        case EventType::SCHEDULE:
            return "schedule";
        case EventType::DESCHEDULE:
            return "deschedule";
        case EventType::WL_CHANGE:
            return "wl_change";
        case EventType::SUSPEND:
            return "suspend";
        case EventType::END:
            return "end";
        default:
            assert(false);
            return "?";
        }
    }

    // =====================================================
    // Insertion Operations
    // =====================================================

    void MigrationManager::addSchedulingEvent(AbsRTTask *t, Tick when,
                                              CPU *cpu) {
        std::string wl = cpu->getWorkload();
        cpu->setWorkload(Utils::getTaskWorkload(t));
        addTaskEvent(t, when, EventType::SCHEDULE, cpu);
        cpu->setWorkload(wl);
    }

    void MigrationManager::addSupensionEvent(AbsRTTask *task, Tick when) {
        addTaskEvent(task, when, EventType::SUSPEND, nullptr);
    }

    void MigrationManager::addDeschedulingEvent(AbsRTTask *task, Tick when) {
        addTaskEvent(task, when, EventType::DESCHEDULE, nullptr);
    }

    void MigrationManager::addEndEvent(AbsRTTask *task, Tick when) {
        addTaskEvent(task, when, EventType::END, nullptr);
    }

    void MigrationManager::addWorloadChangeEvent(AbsRTTask *task, Tick when,
                                                 CPU *c) {
        addTaskEvent(task, when, EventType::WL_CHANGE, c);
    }

    // =====================================================
    // Select Operations
    // =====================================================

    bool select_by_task(const AbsRTTask *task,
                        const MigrationManager::MigrationTaskRow &row) {
        return row.task == task;
    }

    std::vector<MigrationManager::MigrationTaskRow>
        MigrationManager::getEventsForTask(AbsRTTask *task) const {
        auto select_lambda = [task](const MigrationTaskRow &row) {
            return select_by_task(task, row);
        };

        std::vector<MigrationTaskRow> out_rows(_tasks_history.size());
        auto out_size =
            std::copy_if(_tasks_history.cbegin(), _tasks_history.cend(),
                         out_rows.begin(), select_lambda);
        out_rows.erase(out_size, out_rows.cend());
        return out_rows;

        // Alternative implementation using a back inserter
        // std::vector<MigrationTaskRow> out_rows;
        // std::copy_if(_tasks_history.cbegin(), _tasks_history.cend(),
        //              std::back_inserter(out_rows), select_lambda);
        // return out_rows;

        // Original implementation below
        // std::vector<MigrationTaskRow> out_rows
        // for (const auto &elem : _tasks_history)
        //     if (elem.task == task)
        //         out_rows.push_back(elem);
        // return out_rows;
    }

    /// Returns true if the task has had some migrations
    ///
    /// @todo false? this returns whether the task has at least one event
    /// saved, which may not correspond to a migration event
    bool MigrationManager::hasMigrated(AbsRTTask *task) const {
        auto select_lambda = [task](const MigrationTaskRow &row) {
            return select_by_task(task, row);
        };

        auto res = std::find_if(_tasks_history.cbegin(), _tasks_history.cend(),
                                select_lambda);
        return res != _tasks_history.cend();

        // For reference, original (less efficient) implementation below:
        // std::vector<MigrationTaskRow> res = getEventsForTask(task);
        // return !res.empty();
    }

    std::string MigrationManager::toString() const {
        std::stringstream ss;
        ss << "Tasks migration histories:" << std::endl;
        ss << "Task\tTick\tEvt\t\tcpu\twl" << std::endl;

        for (const auto &elem : _tasks_history) {
            ss << taskname(elem.task) << "\t" << double(elem.tick) << "\t"
               << mapEventType(elem.evt) << "\t"
               << (elem.cpu == nullptr ? "" : elem.cpu->getName()) << "\t"
               << elem.wl << std::endl;
        }
        return ss.str();
    }
} // namespace RTSim
