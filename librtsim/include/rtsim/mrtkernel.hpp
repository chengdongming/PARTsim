/***************************************************************************
 *   begin                : Thu Apr 24 15:54:58 CEST 2003
 *   copyright            : (C) 2003 by Giuseppe Lipari
 *   email                : lipari@sssup.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
#ifndef __MRTKERNEL_HPP__
#define __MRTKERNEL_HPP__

// Single CPU RTKernel
#include <rtsim/kernel.hpp>
#include <rtsim/kernevt.hpp>
#include <rtsim/task.hpp>

#define _MRTKERNEL_DBG_LEV "MRTKernel"

namespace RTSim {
    class MRTKernel;
    class CBServer;

    // =========================================================================
    // class DispatchMultiEvt
    // =========================================================================

    /// This class is the base class for event dispatchers used by MRTKernel.
    ///
    /// @sa BeginDispatchMultiEvt, EndDispatchMultiEvt
    class DispatchMultiEvt : public Event {
    protected:
        MRTKernel &_kernel;
        CPU &_cpu;
        AbsRTTask *_task;

    public:
        /// Creates a new multi event dispatcher linked to
        /// the given MRTKernel and CPU
        DispatchMultiEvt(MRTKernel &k, CPU &c, int prio) :
            Event("MultiKernelDispatch", Event::_DEFAULT_PRIORITY + 10),
            _kernel(k),
            _cpu(c),
            _task(0) {}

        CPU *getCPU() {
            return &_cpu;
        }

        const CPU *getCPU() const {
            return &_cpu;
        }

        /// Specifies the task that fired the event (?)
        void setTask(AbsRTTask *t) {
            _task = t;
        }

        AbsRTTask *getTask() {
            return _task;
        }

        const AbsRTTask *getTask() const {
            return _task;
        }

        /// Must be defined by subclasses, per usual with
        /// Events
        void doit() override = 0;

        std::string toString() const override {
            std::stringstream ss;
            ss << taskname(getTask()) << " " << getCPU()->getName() << " at "
               << getTime();
            return ss.str();
        }
    };

    // =========================================================================
    // class BeginDispatchMultiEvt
    // =========================================================================

    /// This class models events related to the beginning of a context switch.
    ///
    /// On certain processors, context switches are handled using this class.
    ///
    /// It is different than the BeginDispatchEvt for single-processor kernels
    /// (i.e. RTKernel), since it needs to store a pointer to the CPU related to
    /// the event and the amount of overhead for the context switch. This last
    /// measurement may depend on the migration status of the task.
    class BeginDispatchMultiEvt : public DispatchMultiEvt {
    public:
        /// Simply forwards parameters to base class constructor, specifying the
        /// appropriate event priority.
        ///
        /// @todo why is the priority set like that internally?
        BeginDispatchMultiEvt(MRTKernel &k, CPU &c);

        /// Signals the linked MRTKernel that the
        /// corresponding event is beginning
        void doit() override;

        std::string toString() const override {
            std::stringstream ss;
            ss << "BeginDMEvt " << DispatchMultiEvt::toString();
            return ss.str();
        }
    };

    // =========================================================================
    // class EndDispatchMultiEvt
    // =========================================================================

    /// This class models events related to the finishing of a context switch.
    ///
    /// On certain processors, context switches are handled using this class.
    ///
    /// It is different than the EndDispatchEvt for single-processor kernels
    /// (i.e. RTKernel), since it needs to store a pointer to the CPU related to
    /// the event and the amount of overhead for the context switch. This last
    /// measurement may depend on the migration status of the task.
    ///
    /// @todo fix this comment
    class EndDispatchMultiEvt : public DispatchMultiEvt {
    public:
        /// Simply forwards parameters to base class constructor, specifying the
        /// appropriate event priority.
        ///
        /// @todo why is the priority set like that internally?
        EndDispatchMultiEvt(MRTKernel &k, CPU &c);

        /// Signals the linked MRTKernel that the
        /// corresponding event is ending
        void doit() override;

        std::string toString() const override {
            std::stringstream ss;
            ss << "EndDMEvt " << DispatchMultiEvt::toString();
            return ss.str();
        }
    };

    // =========================================================================
    // class MRTKernel
    // =========================================================================

    /// \ingroup kernel
    ///
    /// An implementation of a real-time multi processor kernel with global
    /// scheduling. It contains:
    ///
    /// - a pointer to the CPU factory used to create CPUs managed by the kernel
    /// (NOTE: CPUs are NOT destroyed by the kernel itself);
    ///
    /// - a pointer to a Scheduler, which implements the scheduling policy
    /// (NOTE: not destroyed by the kernel itself);
    ///
    /// - a pointer to a Resource Manager, which is responsable for resource
    /// access related operations and thus implements a resource allocation
    /// policy (NOTE: not destroyed by the kernel itself);
    ///
    /// - a map of pointers to CPU and task, which keeps the information about
    /// current task assignment to CPUs;
    ///
    /// - the set of tasks handled by this kernel.
    ///
    /// This implementation is quite general: it lets the user of this class the
    /// freedom to adopt any scheduler derived form Scheduler and a resource
    /// manager derived from ResManager or no resource manager at all.
    ///
    /// The kernel for a multiprocessor system with different CPUs can be also
    /// be simulated. It is up to the instruction class to implement the correct
    /// duration of its execution by asking the kernel of its task the speed of
    /// the processor on whitch it is scheduled.
    ///
    /// We will probably have to derive from this class to implement static
    /// partition and mixed task allocation to CPU.
    ///
    /// @see absCPUFactory, Scheduler, ResManager, AbsRTTask
    ///
    /// @note Internally this class does not have a list of CPUs, rather it
    /// knows which CPUs are linked to it because of its mapping from CPUs to
    /// Tasks (or to null pointers if no task is in execution).
    ///
    /// @note Since it extends RTKernel, it has a single CPU associated that is
    /// managed by the superclass, but never used.
    class MRTKernel : public RTKernel {
        // =================================================
        // Internal Types
        // =================================================
    protected:
        using ITCPU = std::map<CPU *, AbsRTTask *>::iterator;
        using CITCPU = std::map<CPU *, AbsRTTask *>::const_iterator;

        // =================================================
        // Data
        // =================================================
    protected:
        /// CPU Factory. Used in one of the constructors.
        /// @deprecated removed because only needed in constructors.
        // absCPUFactory *_CPUFactory;

        /// The currently executing tasks (one per processor).
        std::map<CPU *, AbsRTTask *> _m_currExe;

        /// Where the task was executing before being suspended
        std::map<const AbsRTTask *, CPU *> _m_oldExe;

        /// This denotes where the task has been dispatched. A dispatched task
        /// is also the currently executing task once the context switch is
        /// over.
        ///
        /// The set of dispatched tasks includes the set of executing tasks:
        ///
        /// - first, in the onBeginDispatchMulti a task is dispatched, (but not
        /// executing yet);
        ///
        /// - then, in the onEndDispatchMulti, its execution starts on the
        /// processor (the task remains dispatched to that processor anyway
        /// until another task or the idle task is scheduled on it).
        std::map<const AbsRTTask *, CPU *> _m_dispatched;

        /// Indicates whether the CPU is currently in the middle of a context
        /// switch.
        std::map<CPU *, bool> _isContextSwitching;

        /// Each CPU has its own BeginDispatchMultiEvt
        std::map<CPU *, BeginDispatchMultiEvt *> _beginEvt;

        /// Each CPU has its own EndDispatchMultiEvt
        std::map<CPU *, EndDispatchMultiEvt *> _endEvt;

        /// Indicates the amount of delay due to migration of a task.
        ///
        /// @todo will become a RandomVar eventually.
        Tick _migrationDelay;

        /// Set of servers.
        ///
        /// @note: are these ever used internally? Or are these just parked
        /// here?
        std::vector<CBServer *> _servers;

        // =================================================
        // Constructors and Destructor
        // =================================================
        // private:
        /// Function that implements the actual constructor, called by all other
        /// constructors of this class.
        ///
        /// Adds the given CPUs to the current kernel and links the scheduler to
        /// the kernel itself.
        ///
        /// For each CPU it creates corresponding event dispatchers for context
        /// switches.
        // void internalConstructor(std::set<CPU *> cpus);

        /// Function that implements the actual constructor, called by all other
        /// constructors of this class.
        ///
        /// Creates n CPUs using _CPUFactory and calles the other
        /// internalConstructor.
        // void internalConstructor(size_t n);

    public:
        /// Constructor: needs to know which scheduler the kernel want to use
        /// and the processors.
        ///
        /// In this case, no CPUs will be generated and the ones given as input
        /// will be used instead.
        ///
        /// This is the "true" constructor of the class, all other constructors
        /// will call this constructor.
        ///
        /// It adds the given CPUs to the current kernel and links the scheduler
        /// to the kernel itself.
        ///
        /// @sa addCPU
        MRTKernel(Scheduler *, std::set<CPU *> cpus = {},
                  const std::string &name = "");

        // /// Constructor: needs to know which scheduler the kernel want to use
        // /// and the processors.
        // ///
        // /// In this case, no CPUs will be generated and the ones given as
        // input
        // /// will be used instead.
        // ///
        // /// @note DO NOT USE MULTIPLE TIMES THE SAME CPU POINTER, MULTIPLE
        // CPUS
        // /// POINTING TO THE SAME CPU WILL BE MERGED INTO THE SAME ONE
        // ///
        // /// @deprecated use the one with the std::set instead
        // MRTKernel(Scheduler *, std::vector<CPU *>,
        //           const std::string &name = "");

        // /// Constructor: needs to know which scheduler and CPU factory the
        // /// kernel want to use, and how many processor the system is composed
        // /// of.
        // ///
        // /// It will then use the given factory to generate that many
        // processors.
        // ///
        // /// @note the absCPUFactory given as argument will no longer be owned
        // by
        // /// this class and can be reused by others after n CPUs have been
        // /// created in this constructor. The factory will not be destroyed by
        // /// the kernel on delete.
        // MRTKernel(Scheduler *, absCPUFactory *, int n = 1,
        //           const std::string &name = "");

        // /// Constructor: needs to know which scheduler the kernel want to
        // use,
        // /// and from how many processor the system is composed.
        // ///
        // /// Internally, it will use a uniformCPUFactory to generate the n
        // CPUs. MRTKernel(Scheduler *, int n = 1, const std::string &name =
        // "");

        // /// Constructor: needs to know scheduler and name only.
        // ///
        // /// Implicitly creates one CPU only using a uniformCPUFactory.
        // MRTKernel(Scheduler *, const std::string &name);

        /// Destroys elements owned/created by this class, but NOT the CPUs,
        /// Servers, and their tasks.
        virtual ~MRTKernel();

        // =================================================
        // Methods
        // =================================================
    protected:
        /// @return a pointer to a free CPU (nullptr if every CPU is busy).
        ///
        /// @note there is no specific order in which free CPUs are chosen.
        CPU *getFreeProcessor();

        // CPU *getFreeProcessor() {
        //     return const_cast<CPU *>(
        //         const_cast<const MRTKernel *>(this)->getFreeProcessor());
        // }

        /// @return whether the given CPU as a task currently being dispatched
        /// on.
        bool isDispatched(CPU *p) const;

        /// A CPU is considered free to use if it has no _m_currExe[] task
        /// mapped, and there are no _m_dispatched[] tasks on that CPU.
        ///
        /// @returns the first free CPU between the two given iterators (first
        /// included, last excluded).
        ITCPU getNextFreeProc(ITCPU begin, ITCPU end);

        // ITCPU getNextFreeProc(CITCPU begin, CITCPU end) {
        //     return static_cast<ITCPU>(
        //         const_cast<const MRTKernel *>(this)->getNextFreeProc(begin,
        //                                                              end));
        // }

    public:
        /// Adds a CPU to the set of CPUs handled by the kernel. Initializes all
        /// CPU-related data structures and creates all event dispatchers linked
        /// to the CPU as well.
        void addCPU(CPU *);

        /// Add a task to the kernel. Specify the scheduling parameters in
        /// param. Calls base class method. Initializes task-related data
        /// structures and if the task has a CBServer it adds it to the list of
        /// servers.
        ///
        /// @sa RTKernel::addTask
        void addTask(AbsRTTask &t, const std::string &param = "") override;

        /// Called by task::onArrival function.
        ///
        /// Differently from RTKernel::onArrival, this method overlooks whether
        /// the kernel is context switching or not (because each CPU can be in a
        /// context switching state independently from each other).
        ///
        /// @see RTKernel::onArrival
        void onArrival(AbsRTTask *) override;

        /// Removes the task from the ready queue.
        ///
        /// If the task was executing on one of the CPUs managed by this kernel,
        /// the task is "descheduled", and that CPU will have no task executing
        /// on it (pointer set to nullptr).
        ///
        /// Virtually same implementation as in RTKernel, but with multiple
        /// processors available.
        ///
        /// @see RTKernel::suspend
        void suspend(AbsRTTask *) override;

        // @see RTKernel::activate
        // void activate(AbsRTTask *) override; //same as RTKernel

        /// Invoked from the task onEnd function.
        ///
        /// Removes the task from the ready queue, sets the currently executing
        /// task of the original CPU to nullptr and invokes the refresh.
        ///
        /// Virtually same implementation as in RTKernel, but with multiple
        /// processors available.
        ///
        /// @see RTKernel::onEnd
        void onEnd(AbsRTTask *) override;

        /// Dispatching on a given CPU.
        ///
        /// This is different from the version we have on RTKernel, since we may
        /// need to specify on which CPU we have to select a new task.
        ///
        /// Used in onEnd() and suspend(). In the onArrival(), instead, we still
        /// do not know which processor is free!
        virtual void dispatch(CPU *cpu);

        /// Dispatching on the CPUs managed by this kernel.
        ///
        /// Called by task::onArrival and the activate function.
        ///
        /// Selects a free processor and call the other dispatch with it.
        ///
        /// After this call, the first N tasks in the ready queue are dispatched
        /// on the N cpus managed by this kernel. If there aren't enough tasks
        /// then some CPUs will be left idle.
        void dispatch() override;

        /// Called by the BeginDispatchMultiEvt objects related to each CPU when
        /// the related event fires.
        ///
        /// Picks the first non-dispatched task in the ready queue and starts a
        /// dispatch operation (that may last some time, depending on the
        /// dispatch overhead and optionally the migration overhead) to schedule
        /// that task on the CPU selected in the dispatch().
        ///
        /// To simulate the time elapsed from the beginning of the dispatch to
        /// its end, the EndDispatchMultiEvt is set to call the
        /// onEndDispatchMulti when the dispatching operation would be over.
        ///
        /// If the CPU already had a task running on it, it will be descheduled.
        virtual void onBeginDispatchMulti(BeginDispatchMultiEvt *e);

        /// Performs the "real" context switch by finishing the dispatch
        /// operation started with the onBeginDispatchMulti.
        ///
        /// After this call, the linked CPU will run the task previously
        /// dispatched on it and it will not be in the "context switching"
        /// phase anymore.
        ///
        /// It also notifies the scheduler of the completed operation.
        virtual void onEndDispatchMulti(EndDispatchMultiEvt *e);

        /// @return used CBS servers
        /// @todo constness?
        std::vector<CBServer *> getServers() const {
            return _servers;
        }

        /// @return a pointer to the CPU on which t is running (nullptr if t is
        /// not running on any CPU)
        CPU *getProcessor(const AbsRTTask *t) const override;

        // CPU *getProcessor(const AbsRTTask *t) {
        //     return const_cast<CPU *>(
        //         const_cast<const MRTKernel *>(this)->getProcessor(t));
        // }

        /// @return a pointer to the CPU on which t was previously running
        /// (nullptr if t was not running on any CPU)
        CPU *getOldProcessor(const AbsRTTask *t) const override;

        // CPU *getOldProcessor(const AbsRTTask *t) {
        //     return const_cast<CPU *>(
        //         const_cast<const MRTKernel *>(this)->getOldProcessor(t));
        // }

        // /// @return a vector containing the pointers to the processors.
        // /// @deprecated will be removed soon
        // std::vector<CPU *> getProcessors() const;

        /// Set the migration delay. This is the overhead to be added to the
        /// ContextSwitchDelay when the task is migrated from one processor to
        /// another.
        void setMigrationDelay(const Tick &t) {
            _migrationDelay = t;
        }

        /// Removes all associations between CPUs and running tasks, all
        /// dispatching tasks and all associations between tasks and the CPUs on
        /// which they were previously running
        void newRun() override;

        /// Removes all associations between CPUs and running tasks
        void endRun() override;

        /// Prints to stdout executing and dispatched tasks
        void print() const override;

        /// Prints to stdout associations between CPUs and running tasks
        void printState() const override;

        /// @return a pointer to the task which is executing on given CPU
        /// (nullptr if given CPU is idle)
        virtual AbsRTTask *getTask(const CPU *);

        // AbsRTTask *getTask(CPU *c) {
        //     return const_cast<AbsRTTask *>(
        //         const_cast<const MRTKernel *>(this)->getCPU(c));
        // }

        /// @return the name of the tasks (std::string) stored in a
        /// std::vector<std::string>. Each element of the vector corresponds to
        /// the name of a different running task (at most one per CPU).
        std::vector<std::string> getRunningTasks() override;
    };
} // namespace RTSim

#endif // __MRTKERNEL_HPP__
