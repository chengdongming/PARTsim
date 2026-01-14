// #pragma once

#ifndef RTSIM_MIGRATION_HPP
#define RTSIM_MIGRATION_HPP

#include <algorithm>
#include <ostream>
#include <vector>

// MetaSim
#include <metasim/entity.hpp>

// RTSim
#include <rtsim/cpu.hpp>
#include <rtsim/task.hpp>
#include <rtsim/utils.hpp>

namespace RTSim {

    using namespace MetaSim;

    /// Accounts all task status change operations.
    ///
    /// It accounts a detailed history of all task migrations and for how long
    /// each task ran on each CPU before being migrated.
    ///
    /// @todo Unused? Do not use before fixing the private insertion method.
    class MigrationManager {
    public:
        /// All different kinds of accounted events for each task
        enum class EventType {
            SCHEDULE = 0,
            DESCHEDULE,
            WL_CHANGE,
            SUSPEND,
            END,
        };

        struct MigrationTaskRow {
            AbsRTTask *task;
            Tick tick;
            EventType evt;
            CPU *cpu;
            std::string wl;
            MigrationTaskRow() = default;

            // Used to emplace rather than push back a copy
            MigrationTaskRow(AbsRTTask *task, Tick tick, EventType evt,
                             CPU *cpu, const std::string &wl) :
                task(task),
                tick(tick),
                evt(evt),
                cpu(cpu),
                wl(wl) {}
        };

        // =================================================
        // Data
        // =================================================
    protected:
        /// History of all events registered for each task.
        std::vector<MigrationTaskRow> _tasks_history;

        // =================================================
        // Constructors and Destructors
        // =================================================
    public:
        MigrationManager() {}

        DEFAULT_VIRTUAL_DES(MigrationManager);

        // =================================================
        // Methods
        // =================================================
    protected:
        /// Adds an event to the task history. Called by all specialized
        /// operations below.
        void addTaskEvent(AbsRTTask *task, Tick when, EventType event,
                          CPU *cpu);

        std::string mapEventType(EventType e) const;

    public:
        // =================================================
        // Insertion Operations
        // =================================================

        /// Adds a new SCHEDULE event to the history.
        ///
        /// @note IT ALSO CHANGES THE CPU WORKLOAD TEMPORARILY TO MATCH THE ONE
        /// OF THE GIVEN TASK. THIS TRIGGERS A WHOLE UPDATE OF THE MODEL! TWICE!
        ///
        /// @todo FIX IF THIS CLASS IS USED
        void addSchedulingEvent(AbsRTTask *t, Tick when, CPU *cpu);

        /// Adds a new SUSPEND event to the history.
        void addSupensionEvent(AbsRTTask *task, Tick when);

        /// Adds a new DESCHEDULE event to the history.
        void addDeschedulingEvent(AbsRTTask *task, Tick when);

        /// Adds a new END event to the history.
        void addEndEvent(AbsRTTask *task, Tick when);

        /// Adds a new WL_CHANGE event to the history.
        /// you should change CPU worload before to call this method.
        void addWorloadChangeEvent(AbsRTTask *task, Tick when, CPU *c);

        // =================================================
        // Select Operations
        // =================================================
    public:
        std::vector<MigrationTaskRow> getEventsForTask(AbsRTTask *task) const;

        /// Returns true if the task has had some migrations
        ///
        /// @todo false? this returns whether the task has at least one event
        /// saved, which may not correspond to a migration event
        bool hasMigrated(AbsRTTask *task) const;

        virtual std::string toString() const;
    };

    static inline std::ostream &operator<<(std::ostream &out,
                                           const MigrationManager &m) {
        return out << m.toString();
    }
} // namespace RTSim

#endif // RTSIM_MIGRATION_HPP
