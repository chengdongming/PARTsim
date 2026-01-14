// #pragma once

#ifndef RTSIM_MULTISCHED_HPP
#define RTSIM_MULTISCHED_HPP

// #include <cassert>

// // MetaSim
// #include <metasim/entity.hpp>

// RTSim
// #include <rtsim/cpu.hpp>
#include <rtsim/mrtkernel.hpp>
// #include <rtsim/scheduler/rrscheduler.hpp>
#include <rtsim/scheduler/scheduler.hpp>

namespace RTSim {
    /// \ingroup sched
    ///
    /// An implementation of multi-scheduler, i.e. every core has a queue,
    /// implemented as scheduler. Notice it is not a scheduler, but an interface
    /// to manage cores queues, which are implemented as schedulers. This class
    /// is also named Multi Cores Queues.
    ///
    /// The typical scenario is: you have N cores and you need a queue per core.
    /// Each queue can be modelled as a scheduler. Schedulers are supposed to be
    /// equal!
    ///
    /// This interface has been introduced for EnergyMRTkernel, managing tasks
    /// for bigLITTLE cores, where for energetic reasons the scheduler (the
    /// kernel in RTSim) might decide to dispatch many tasks to a certain core.
    ///
    /// Notice that this layer (laid above a multicore kernel) does not
    /// de/schedule() tasks, it only posts begin and end context switches. To
    /// enforce consistency between kernel and multi-scheduler, kernel needs to
    /// provide, for each relevant function, task and core where it knows that
    /// the task is currently scheduled.
    ///
    /// @todo why please? is it optional?
    ///
    /// Before using this layer, please disable (disable()) the kernel
    /// scheduler. It will still generate onArrival() => dispatch() events, but
    /// other callbacks will get disabled.
    ///
    /// This implementation is general and you can specialize it.
    ///
    /// @sa CPU, AbsRTTask, EnergyMultiCoresScheds
    class MultiCoresScheds : public MetaSim::Entity {
        // =================================================
        // Data
        // =================================================
    protected:
        /// One task queue per CPU, ordered according to scheduling policy.
        std::map<CPU *, Scheduler *> _queues;

        /// Running task per CPU. The running task is the first task in each CPU
        /// queue
        std::map<CPU *, AbsRTTask *> _running_tasks;

        /// Events that manage the beginning of context switches for each CPU
        std::map<CPU *, BeginDispatchMultiEvt *> _beginEvts;

        /// Events that manage the ending of context switches for each CPU
        std::map<CPU *, EndDispatchMultiEvt *> _endEvts;

        /// The kernel that is currently leveraging these multi-CPU queues. This
        /// kernel must disable internal management of CPU queues.
        ///
        /// @todo is this true?
        MRTKernel *_kernel;

        struct CPU_Utilizations {
            CPU *cpu;
            Tick virtual_time;
            double uact;
        };

        /// Maps a task wrapped in a CBServer to its CPU, virtual time (=time to
        /// forget its active utilization) and its active utilization
        // std::map<AbsRTTask *, std::tuple<CPU *, Tick, double>>
        //     _active_utilizations;
        std::map<AbsRTTask *, CPU_Utilizations> _active_utilizations;

        // =================================================
        // Constructors and Destructors
        // =================================================
    public:
        // @todo why trash?
        // MultiCoresScheds(const std::string &name = "trash multisched") :
        //     Entity(name) {}

        /// @todo std::set for CPUs
        MultiCoresScheds(MRTKernel *kernel, std::vector<CPU *> &cpus,
                         std::vector<Scheduler *> &scheds,
                         const std::string &name);

        /// @todo should it delete tasks in queues?
        virtual ~MultiCoresScheds() {
            for (auto it : _queues) {
                for (auto task : getAllTasksInQueue(it.first)) {
                    delete task;
                }
                delete it.second;
            }
        }

        // =================================================
        // Methods
        // =================================================
    protected:
        // Posts a context switch event
        //
        // @param endevt is true if this is the ending of a context switch,
        // false if it is the beginning
        // void postEvt(CPU *cpu, AbsRTTask *task, Tick when, bool endevt);

        /// Post begin event for a task on a core
        void postBeginEvt(CPU *cpu, AbsRTTask *task, Tick when);

        /// Post end event for a task on a core
        void postEndEvt(CPU *cpu, AbsRTTask *task, Tick when);

        /// Drops all context switch events originated by the given task on that
        /// CPU
        void dropEvt(CPU *cpu, AbsRTTask *task);

        /// The given (ready) task is set as the running task on the given CPU
        virtual void makeRunning(AbsRTTask *task, CPU *cpu);

        /// Preempts the task on the given CPU bringing it back ot its ready
        /// state (because a higher-priority task is assigned to that CPU).
        ///
        /// Called also when the running task is done after removing it from the
        /// queue.
        ///
        /// Drops possible ends of context switch events if the current task was
        /// not running yet but context switching instead.
        ///
        /// @note Differently than in MRTKernel, in this class a "dispatched"
        /// task that is not running yet but it is in the middle of a context
        /// switch is considered the running task.
        virtual void makeReady(CPU *cpu);

        /// Add a task to the scheduler of a core.
        ///
        /// @see insertTask
        virtual void addTask(AbsRTTask *task, CPU *cpu,
                             const std::string &params);

    public:
        /// Counts the tasks of a core queue
        /// @todo const
        size_t countTasks(CPU *cpu);

        /// Empties a core queue
        ///
        /// @todo I don'task like this
        virtual void empty(CPU *cpu);

        /// True if the core queue is empty (i.e., no ready and running tasks on
        /// cpu)
        bool isEmpty(CPU *cpu);

        /// @return scheduler of a core
        Scheduler *getScheduler(CPU *cpu);

        /// Get the first (running or ready) task of a core queue
        virtual AbsRTTask *getFirst(CPU *cpu);

        /// Get the first ready task of a core or nullptr if no ready tasks are
        /// available
        virtual AbsRTTask *getFirstReady(CPU *cpu);

        /// Get processor where task is running
        CPU *getProcessorRunning(const AbsRTTask *task) const;

        /// Get processor where task is ready
        CPU *getProcessorReady(const AbsRTTask *task) const;

        /// Get core where task is dispatched (either running and ready)
        CPU *getProcessor(const AbsRTTask *task) const;

        /// Get running task for the cpu
        /// @todo const
        virtual AbsRTTask *getRunningTask(CPU *cpu);

        /// Get all tasks of a core queue
        std::vector<AbsRTTask *> getAllTasksInQueue(CPU *cpu) const;

        /// Returns all non-running tasks on the given core
        std::vector<AbsRTTask *> getReadyTasks(CPU *cpu);

        /// Add a task to the queue of a core.
        /// @todo there is something fishy here
        virtual void insertTask(AbsRTTask *task, CPU *cpu);

        /// @return CPU if task in dispatched in any queue, else nullptr
        virtual CPU *isInAnyQueue(const AbsRTTask *task);

        /// True if CPU is under a context switch (= there is a task in the
        /// limbo between beginDispatchMulti - endDispatchMulti)
        bool isContextSwitching(CPU *cpu) const;

        /// When done with the begining part of the context switch
        void onBeginDispatchMultiFinished(CPU *cpu, AbsRTTask *newTask,
                                          Tick overhead);

        /// When done with the ending part of the context switch, the task is
        /// running
        void onEndDispatchMultiFinished(CPU *cpu, AbsRTTask *task);

        /// Kernel signals end of task event (WCET finished)
        void onEnd(AbsRTTask *t, CPU *cpu);

        /// To be called when migration finishes. It deletes context switch
        /// events on the original core and prepares for context switch on the
        /// final core (= chosen after migration).
        ///
        /// Migration is not specifically meant for bigLITTLE cores, it can also
        /// be a task movement between 2 cores. That's why this function is here
        virtual void onMigrationFinished(AbsRTTask *task, CPU *original,
                                         CPU *final);

        /// @todo understand this
        /// @sa onReleasingIdle
        void onExecutingRecharging(CBServer *cbs);

        /// @todo understand this
        void onExecutingReleasing(CPU *cpu, CBServer *cbs);

        /// Callback for CBServer task going from releasing to idle => you can
        /// forget task active utilization.
        ///
        /// @return the core where the CBS server was
        ///
        /// @todo is it better to use onVirtualTimeReached(CBServer* cbs)? will
        /// this method remain the same?
        CPU *onReleasingIdle(CBServer *cbs);

        /// CBS recharging itself
        void onReplenishment(CBServer *cbs);

        /// Function called only when RRScheduler is used. It informs the queues
        /// manager that a task has finished its round => remove from queue and
        /// take next task
        void onRound(AbsRTTask *finishingTask, CPU *cpu);

        /// When task in CBS server ends
        void onTaskInServerEnd(AbsRTTask *task, CPU *cpu, CBServer *cbs);

        /// Remove a specific task from a core queue. Also removes its ctx evt
        virtual void removeFromQueue(CPU *cpu, AbsRTTask *task);

        /// Remove the first task of a core queue. Also removes its ctx evt
        virtual void removeFirstFromQueue(CPU *cpu);

        /// Schedule first task of core queue, i.e. posts its context switch
        /// event/time
        void schedule(CPU *cpu);

        /// Checks whether the task t (currently running on cpu) should be
        /// preempted
        bool shouldDeschedule(CPU *cpu, AbsRTTask *task);

        /// @return true if there is no running task or the given task is a
        /// CBServer which is currently yielding
        bool shouldSchedule(CPU *cpu, AbsRTTask *task);

        /// Executes next ready task on the cpu
        void yield(CPU *cpu);

        // =================================================
        // Operations that manage active utilizations
        // =================================================

        /// Removes Utilization active that must end CBS server t and returns
        /// core where t was
        CPU *forgetU_active(AbsRTTask *task);

        /// Returns the U_active for tasks "releasing" on CBS server
        double getUtilization_active(CPU *cpu) const;

        /// Only for debug, get utilization active of task t
        double getUtilization_active(AbsRTTask *t);

        /// Save U_active of an ending task t
        void saveU_active(CPU *cpu, CBServer *cbs);

        void newRun() override {}

        void endRun() override {
            for (auto &e : _queues)
                empty(e.first);
        }

        std::string toString() const override;
    };
} // namespace RTSim

#endif // RTSIM_MULTISCHED_HPP
