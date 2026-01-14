/***************************************************************************
    begin                : Thu Apr 24 15:54:58 CEST 2003
    copyright            : (C) 2003 by Giuseppe Lipari
    email                : lipari@sssup.it
 ***************************************************************************/
/***************************************************************************
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

#ifndef RTLIB2_0_ENERGYMRTKERNEL_H
#define RTLIB2_0_ENERGYMRTKERNEL_H

#include <rtsim/cbserver.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/multisched.hpp>
#include <rtsim/scheduler/rrsched.hpp>
#include <rtsim/task.hpp>
#include <rtsim/utils.hpp>

#define _ENERGYMRTKERNEL_DBG_LEV "EnergyMRTKernel"

namespace RTSim {

    class CBServer;
    using CBServerCallingEMRTKernel = CBServer;

    using CPU_BL = CPU;
    using Island_BL = CPUIsland;
    using IslandType = CPUIsland::Type;

    using std::map;

    /**
        \ingroup sched

        Extension of MultiCoresScheds for Big-Little.
        Tasks are also bound to the desired OPP.

        Notice this is not a scheduler, its a class to manage cores queues
        of ready and running tasks.
     */
    class EnergyMultiCoresScheds : public MultiCoresScheds {
    private:
        /// OPP needed by tasks
        map<AbsRTTask *, std::pair<CPU_BL *, unsigned int>> _opps;

        //      multimap<AbsRTTask*, pair<Tick, CPU_BL*> > _tasks_history; //
        //      Tasks history: task tt run on core c at time t

    public:
        EnergyMultiCoresScheds(MRTKernel *kernel, vector<CPU *> &cpus,
                               vector<Scheduler *> &s, const string &name);

        ~EnergyMultiCoresScheds() {
            std::cout << __func__ << std::endl;
        }

        /**
         * Add a task to the queue of a core. Use instead
         * of addTask if you don't need specific task parameters.
         *
         * In this kernel, periodic tasks are incapsulated in a
         * specific CBS server.
         */
        virtual void insertTask(AbsRTTask *t, CPU_BL *c, unsigned int opp) {
            assert(opp >= 0 && opp < c->getIsland()->getOPPsize());
            MultiCoresScheds::insertTask(t, c);
            _opps[t] = std::make_pair(c, opp);
        }

        /**
           To be called when migration finishes.
           It deletes ctx switch events on the original core and
           prepares for context switch on the final core (= chosen after
           migration).

           Migration is specifically meant for big-littles. It updates task WCET
        */
        void onMigrationFinished(AbsRTTask *t, CPU *original,
                                 CPU *final) override {
            CBServerCallingEMRTKernel *cbs =
                dynamic_cast<CBServerCallingEMRTKernel *>(t);
            assert(cbs != NULL);
            assert(original != NULL);
            assert(final != NULL);
            std::cout << "\t\tEMCS::" << __func__ << "()" << std::endl;

            final->setWorkload(Utils::getTaskWorkload(t));
            Tick newBudget =
                Tick(ceil(cbs->getFirstTask()->getWCET(final->getSpeed())));
            cbs->changeBudget(Tick(newBudget));

            MultiCoresScheds::onMigrationFinished(t, original, final);
        }

        /// Remove the first task of a core queue
        virtual void removeFirstReadyFromQueue(CPU_BL *c) {
            AbsRTTask *t = getFirstReady(c);
            removeFromQueue(c, t);
        }

        /// Remove a specific task from a core queue
        void removeFromQueue(CPU_BL *c, AbsRTTask *t) override {
            MultiCoresScheds::removeFirstFromQueue(c); // todo bug? first...
            _opps.erase(t);
            assert(_opps.find(t) == _opps.end());
        }

        /// Empties a core queue
        void empty(CPU_BL *c) override {
            vector<AbsRTTask *> tasks = getAllTasksInQueue(c);
            MultiCoresScheds::empty(c);
            for (AbsRTTask *t : tasks)
                _opps.erase(t);
        }

        void makeRunning(AbsRTTask *t, CPU_BL *c) override {
            MultiCoresScheds::makeRunning(t, c);
            c->setOPP(getOPP(c));
        }

        void endRun() override {
            MultiCoresScheds::endRun();
            _opps.clear();
        }

        virtual unsigned int getOPP(CPU_BL *c) {
            unsigned int maxOPP = 0;
            for (const auto &e : _opps)
                if (e.second.first->getName() == c->getName() &&
                    e.second.second > maxOPP)
                    maxOPP = e.second.second;
            return maxOPP;
        }

        string toString(CPU_BL *c) const {
            vector<AbsRTTask *> tasks = getAllTasksInQueue(c);
            std::stringstream ss;
            int i = 1;
            for (AbsRTTask *t : tasks)
                ss << "\t\t" << i++ << ") " << t->toString() << std::endl;
            return ss.str();
        }

        string toString() const override {
            std::stringstream ss;
            ss << "EnergyMultiCoresScheds::toString(), t=" << SIMUL.getTime()
               << ":" << std::endl;
            for (const auto &q : _queues) {
                string qs = toString(dynamic_cast<CPU_BL *>(q.first));
                if (qs == "")
                    ss << "\tEmpty queue for " << q.first->getName()
                       << std::endl
                       << std::endl;
                else {
                    ss << "\t" << q.first->getName()
                       << " ( freq: " << q.first->getFrequency()
                       << ", wl:" << q.first->getWorkload()
                       << ", speed: " << q.first->getSpeed()
                       << ", u_active: " << getUtilization_active(q.first)
                       << " ):" << std::endl;
                    ss << qs << std::endl;
                }
            }
            return ss.str();
        }
    };

    /**
        \ingroup kernel

        An implementation of a real-time multi processor kernel with
        global scheduling and a policy to smartly select CPU_BLs on big-LITTLE
        architectures. It contains:

        - a pointer to a list of CPU_BLs managed by the kernel;

        - a pointer to a Scheduler, which implements the scheduling
          policy;

        - a pointer to a Resource Manager, which is responsible for
          resource access related operations and thus implements a
          resource allocation policy;

        - a map of pointers to CPU_BL and task, which keeps the
          information about current task assignment to CPU_BLs;

        - the set of tasks handled by this kernel.

        This implementation is not quite general: it lets the user of this
        class the freedom to adopt any scheduler derived form
        Scheduler and a resource manager derived from ResManager or no
        resource manager at all.  The kernel for a multiprocessor
        system with different CPU_BLs can be also be simulated. It is up
        to the instruction class to implement the correct duration of
        its execution by asking the kernel of its task the speed of
        the processor on which it's scheduled.
        It is not that general because the aim was to make everything work
        as expected. That's why there are so many dynamic_cast<CPU_BL*>,
        even when CPU is enough.

        @see absCPU_BLFactory, Scheduler, ResManager, AbsRTTask
    */

    class EnergyMRTKernel : public MRTKernel {
    private:
        struct ConsumptionTable {
            double cons;
            CPU_BL *cpu;
            int opp;
        };

        /// A task envoleped inside a server
        struct EnvelopedTask {
            AbsRTTask *_task;
            CBServerCallingEMRTKernel *_server;
        };

        struct MigrationProposal {
            AbsRTTask *task;
            CPU_BL *from;
            CPU_BL *to;
        };

        // little, big (order matters for speed)
        Island_BL *_islands[2];

        bool _tryingTaskOnCPU_BL;

        /// Map of tasks and their own server, where they are enveloped
        map<AbsRTTask *, CBServerCallingEMRTKernel *> _envelopes;

        /// cores queues, containing ready and running tasks for each core
        EnergyMultiCoresScheds *_queues;

        /// for debug, if you want to force a certain choice of cores and
        /// frequencies and how many times to dispatch
        map<AbsRTTask *, std::tuple<CPU_BL *, unsigned int, unsigned int>>
            _m_forcedDispatch;

        /// for debug, list of discarded tasks and how many times to discard
        /// them
        map<AbsRTTask *, std::tuple<unsigned int>> _discardedTasks;

        /// Temporarily migrated tasks, ie tasks migrated until arrival or
        /// deadline new core or scheduling on original core
        vector<MigrationProposal> _temporarilyMigrated;

        // ----------------------------------- Migrations

        /// island cores load balancing policy: if possible, make all island
        /// cores work
        void balanceLoadEnergy(CPU_BL **chosenCPU, unsigned int &chosenOPP,
                               bool &chosenCPUchanged,
                               vector<struct ConsumptionTable> iDeltaPows);

        /// Balance load by migration. Tasks in toBeSkipped will be skipped.
        /// todo joinable with balanceLoadEnergy?
        MigrationProposal balanceLoad(CPU_BL *endingCPU,
                                      vector<AbsRTTask *> toBeSkipped = {});

        /// Proposes a migration from Big to Little. Tasks in toBeSkipped will
        /// be skipped
        MigrationProposal migrateFromBig(CPU_BL *endingCPU,
                                         vector<AbsRTTask *> toBeSkipped = {});

        /**
         * CPU_BL choice from the table of consumptions (not sorted).
         * It tries to spread tasks on CPU_BLs if they have the same energy
         * consumption
         */
        void chooseCPU_BL(AbsRTTask *t, vector<ConsumptionTable> iDeltaPows);

        /// Returns the CBS servers enveloping the periodic tasks
        vector<AbsRTTask *> getEnvelopers(vector<AbsRTTask *> ptasks) const {
            if (!EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED)
                return ptasks;

            vector<AbsRTTask *> envelopes;
            for (AbsRTTask *t : ptasks)
                if (isCBServer(t))
                    envelopes.push_back(t);
                else
                    envelopes.push_back(getEnveloper(t));
            return envelopes;
        }

        /// Get island big/little
        Island_BL *getIsland(IslandType island) const {
            // return _islands[island];
            switch (IslandType::T(island)) {
            case IslandType::LITTLE:
                return _islands[0];
            case IslandType::BIG:
                return _islands[1];
            default:
                assert(false);
                return nullptr;
            }
        }

        bool isCBServer(AbsRTTask *t) const {
            return dynamic_cast<CBServerCallingEMRTKernel *>(t) != NULL;
        }

        /**
           Tells if a task is to be descheduled on a CPU

           This method has been introduced for RRScheduler, which needs to tell
           wheather a task has finished its quantum
        */
        bool isToBeDescheduled(CPU_BL *p, AbsRTTask *t);

        /**
         * Implements the policy of leaving little 3 free, just in case a task
         * with high WCET arrives, risking to be forced to schedule it on big
         * cores, increasing power consumption.
         */
        void leaveLittle3(AbsRTTask *t,
                          std::vector<ConsumptionTable> iDeltaPows,
                          CPU_BL *&chosenCPU_BL);

        /// Returns a possible migration to endingCPU. Tasks in toBeSkipped will
        /// be skipped. Implements migration mechanism on task end.
        MigrationProposal
            getTaskToMigrateInto(CPU_BL *endingCPU,
                                 vector<AbsRTTask *> toBeSkipped = {});

        /// Pulls a task into endingCPU_BL. It finally performs the migration.
        /// Tasks in toBeSkipped will be skipped
        bool migrateInto(CPU_BL *endingCPU_BL,
                         vector<AbsRTTask *> toBeSkipped = {});

        /// Performs a temporary migration, i.e. one that last only until a task
        /// arrives on endingCPU
        bool migrateTemporarily(CPU_BL *endingCPU) {
            /**
              Balance on island and execute until ending task DL or new task
              arrives. At that point, move back task on its original core, so
              that I don't break schedulability on the ending core and it is
              always utilized. There is no migration overhead and energy
              consumption is the same because it is the same island, so same
              cache and frequency.
              */
            if (!EMRTK_TEMPORARILY_MIGRATE_VTIME &&
                !EMRTK_TEMPORARILY_MIGRATE_END) {
                std::cout << "\tTemporary migrations disabled => skip"
                          << std::endl;
                return false;
            }

            std::cout << "\tEMRTK::" << __func__ << "()" << std::endl;
            MigrationProposal migrationProposal = balanceLoad(endingCPU, {});

            if (migrationProposal.task == NULL) {
                std::cout
                    << "\tNo temporary migration (by balancing) possible. skip"
                    << std::endl;
                return false;
            }

            std::cout << "\tTemporarily migrated "
                      << migrationProposal.task->toString() << " from "
                      << migrationProposal.from->toString() << " to "
                      << migrationProposal.to->toString() << std::endl;
            _temporarilyMigrated.push_back(migrationProposal);
            migrationProposal.to->setWorkload(
                Utils::getTaskWorkload(migrationProposal.task));
            _queues->onMigrationFinished(migrationProposal.task,
                                         migrationProposal.from,
                                         migrationProposal.to);
            return true;
        }

        /// Remove tasks temporarily migrated task to core 'to'
        void removeTaskTemporarilyMigrated(CPU_BL *to) {
            std::cout << "\tEMRTK::" << __func__
                      << "() to core=" << to->toString() << std::endl;
            for (int i = 0; i < _temporarilyMigrated.size(); i++) {
                if (_temporarilyMigrated.at(i).to == to) {
                    MigrationProposal mp = _temporarilyMigrated.at(i);
                    if (dynamic_cast<CBServerCallingEMRTKernel *>(mp.task)
                            ->getFirstTask() !=
                        NULL) { // is task ended already TODO
                        mp.from->setWorkload(Utils::getTaskWorkload(mp.task));
                        _queues->onMigrationFinished(mp.task, mp.to, mp.from);
                    }
                    _temporarilyMigrated.erase(_temporarilyMigrated.begin() +
                                               i);
                }
            }
        }

        /// needed for onOPPChanged()
        bool isTryngTaskOnCPU_BL() {
            return _tryingTaskOnCPU_BL;
        }

        /**
           Tries to schedule a task on a CPU_BL, for all valid OPPs,
           remembering power consumption
        */
        void tryTaskOnCPU_BL(AbsRTTask *t, CPU_BL *c,
                             vector<struct ConsumptionTable> &iDeltaPows);

        /// needed for onOPPChanged()
        void setTryingTaskOnCPU_BL(bool b) {
            _tryingTaskOnCPU_BL = b;
        }

    public:
        static bool EMRTK_BALANCE_ENABLED; /// Can't imagine disabling it, but
                                           /// so policy is in the list :)
        static bool EMRTK_LEAVE_LITTLE3_ENABLED;
        static bool EMRTK_MIGRATE_ENABLED; /// Migrations enabled? (if disabled,
                                           /// its dependencies won't work, e.g.
                                           /// EMRTK_CBS_MIGRATE_AFTER_END)
        static bool EMRTK_CBS_YIELD_ENABLED;
        static bool
            EMRTK_TEMPORARILY_MIGRATE_VTIME; /// Enables temporary (and fake)
                                             /// migrations on vtime end evt,
                                             /// i.e. migration that only last
                                             /// until until a task starts on
                                             /// endingCPU
        static bool
            EMRTK_TEMPORARILY_MIGRATE_END; /// As VTIME, but on task end evt
                                           /// (killed, end WCET, etc.)

        static bool
            EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED; /// CBS server enveloping
                                                   /// periodic tasks?
        static bool EMRTK_CBS_MIGRATE_AFTER_END; /// After a task ends its WCET,
                                                 /// can you migrate? Needs
                                                 /// EMRTK_MIGRATE_ENABLED
        static bool
            EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END; /// After task ends
                                                          /// its virtual time,
                                                          /// there can be
                                                          /// migrations
                                                          /// (requires
                                                          /// EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED)

        /**
         * Kernel with scheduler s and CPU_BLs CPU_BLs.
         * qs are the schedulers you want for MultiCoresScheds, the queues of
         * cores.
         *
         * @see MultiCoresScheds
         */
        EnergyMRTKernel(vector<Scheduler *> &qs, Scheduler *s, Island_BL *big,
                        Island_BL *little, const string &name = "");

        virtual ~EnergyMRTKernel() {
            std::cout << "~EnergyMRTKernel" << std::endl;
            delete _islands[0];
            delete _islands[1];

            delete _queues;
        }

        /**
          Adds a periodic task into scheduler and returns the CBS server
          enveloping it. Call this function instead of addTask() for periodic
          tasks. It also supports CBS servers, which don't get enveloped.
          */
        CBServerCallingEMRTKernel *
            addTaskAndEnvelope(AbsRTTask *t, const string &param = "") {
            CBServerCallingEMRTKernel *serv =
                dynamic_cast<CBServerCallingEMRTKernel *>(t);

            if (serv == NULL) { // periodic task
                serv = new CBServerCallingEMRTKernel(
                    Tick(t->getWCET(1.0)), t->getDeadline(), t->getDeadline(),
                    "hard", "CBS(" + t->toString() + ")", "fifo");
                serv->addTask(*t);

                addTask(*serv, param);

                assert(_envelopes.find(t) == _envelopes.end());
                _envelopes[t] = serv;
            }

            return serv;
        }

        Island_BL *getIslandLittle() const {
            return _islands[0];
        }
        Island_BL *getIslandBig() const {
            return _islands[1];
        }
        void setIslandLittle(Island_BL *island) {
            _islands[0] = island;
        }
        void setIslandBig(Island_BL *island) {
            _islands[1] = island;
        }
        Scheduler *getScheduler() {
            return _sched;
        }

        /**
           This is different from the version we have in MRTKernel: here you
           decide a processor for arrived tasks. See also @chooseCPU_BL() and
           @dispatch(CPU_BL*)

           This function is called by the onArrival and by the
            activate function. When we call this, we first select a
            processor, chosen smartly, for arrived tasks, then we call the other
           dispatch, confirming the dispatch process.
         */
        void dispatch() override;

        /**
           This function only calls dispatch(CPU_BL*) and assigns a task to a
           CPU_BL, which should be actually done by dispatch(CPU_BL*) itself or
           onEndDispatchMulti(), but the last one is only called after
           dispatch(), and I need the assignment CPU_BL - task to be done before
           for getTask(CPU_BL*) to work
         */
        void dispatch(CPU *p, AbsRTTask *t, int opp);

        /**
            Dispatching a task on a given CPU_BL.

            This is different from the version we have on RTKernel,
            since we may need to specify on which CPU_BL we have to
            select a new task (for example, in the onEnd() and
            suspend() functions). In the onArrival() function,
            instead, we still do not know which processor is
            free!

            This is not used at all here because MRTkernel decides here
            to be task context switch in the current simulation or after a
           while. But that's because dispatch() just selects a free CPU and puts
           the new task there. EnergyMRTKernel, instead, allows CPUs to have a
           queue of tasks. Info about task is needed
         */
        void dispatch(CPU *c) override {}

        /// Returns the enveloped RTask of CBS server
        AbsRTTask *getEnveloped(AbsRTTask *cbs) const {
            assert(dynamic_cast<CBServer *>(cbs));
            assert(EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED);

            for (const auto &elem : _envelopes)
                if (elem.second == cbs)
                    return elem.first;
            return NULL;
        }

        /// Returns the CBS server enveloping the periodic task t
        AbsRTTask *getEnveloper(AbsRTTask *t) const {
            if (!EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED || isCBServer(t))
                return t;

            for (auto &elem : _envelopes)
                if (elem.first == t)
                    return elem.second;

            return NULL;
        }

        /// Tells where a task has been dispatched (when it's in the limbo
        /// between onBeginDispatchMulti and onEndDispatchMulti). Similar to
        /// getProcessor() not anymore

        /// Tells on what core queue a task is ready (and not running)
        CPU_BL *getProcessorReady(AbsRTTask *t) const;

        /// Get core where task is running
        virtual CPU_BL *getProcessorRunning(AbsRTTask *t) const {
            if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
                dynamic_cast<PeriodicTask *>(t))
                t = getEnveloper(t);

            CPU *c = _queues->getProcessorRunning(t);
            CPU_BL *cc = dynamic_cast<CPU_BL *>(c);
            return cc;
        }

        /// Get core where task is dispatched (either running and ready). Change
        /// of semantics wrt MRTKernel todo
        virtual CPU_BL *getProcessor(AbsRTTask *t) const {
            if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
                dynamic_cast<PeriodicTask *>(t))
                t = getEnveloper(t);

            CPU *c = _queues->getProcessor(t);
            CPU_BL *cc = dynamic_cast<CPU_BL *>(c);
            return cc;
        }

        /// Get all processors, in all islands
        vector<CPU_BL *> getProcessors() const {
            vector<CPU_BL *> CPU_BLs;
            for (CPU_BL *c : getIslandLittle()->getProcessors())
                CPU_BLs.push_back(c);
            for (CPU_BL *c : getIslandBig()->getProcessors())
                CPU_BLs.push_back(c);
            return CPU_BLs;
        }

        /// Get processors of an island
        vector<CPU_BL *> getProcessors(IslandType island) const {
            return getIsland(island)->getProcessors();
        }

        /// For debug. Returns the layer managing CPUs queues/schedulers
        EnergyMultiCoresScheds *getEnergyMultiCoresScheds() const {
            return _queues;
        }

        /**
           Returns island utilization given a capacity to scale up/down tasks
           WCET. It also returns the number of tasks being scheduled in the
           island
        */
        double getIslandUtilization(double capacity, IslandType island,
                                    int *nTaskIsland) const;

        /// Returns utilization of task t on CPU_BL c. This method could be
        /// defined for tasks, but this way I can make this implementation
        /// private
        double getUtilization(AbsRTTask *t, double capacity) const;

        /// Returns utilization of tasks on CPU_BL c, supposing it runs with
        /// given freq and capacity. This method could be defined for tasks, but
        /// this way I can make this implementation private
        double getUtilization(CPU_BL *c, double capacity) const;

        /// Returns the sum of utilization active of tasks on core c
        double getUtilization_active(CPU_BL *c) const;

        /// Returns utilization of temporaritly migrated task from CPU 'from'
        double getUtilization_temporarilyMigrated(CPU_BL *from) const;

        /**
          If it's an executing CBS server CEMRTK., it increases utilization by
          the CBS server utilization and return true.
          */
        bool getCBServer_Utilization(AbsRTTask *cbs,
                                     double &utilization_initial,
                                     const double cbs_core_capacity) const;

        /// Returns the set of tasks in the runqueue of CPU_BL c, but the
        /// runnning one, ordered by DL (300, 400, ...)
        virtual vector<AbsRTTask *> getReadyTasks(CPU_BL *c) const;

        /**
         *  Returns a pointer to the task which is executing on given
         *  CPU_BL (NULL if given CPU_BL is idle)
         */
        virtual AbsRTTask *getRunningTask(CPU *c);

        /// Is migration energetically convenient? True if power consumption
        /// decreases or is equal between cores
        bool isMigrationEnergConvenient(const MigrationProposal mp) {
            return mp.to->getPower(mp.to->getFrequency()) <=
                   mp.from->getPower(mp.from->getFrequency());
        }

        /// Does migration break schedulability on the ending core?
        bool isMigrationSafe(const MigrationProposal mp) {
            assert(mp.task != NULL);
            assert(mp.from != NULL);
            assert(mp.to != NULL);
            CPU_BL *endingCPU = mp.to;
            CBServerCallingEMRTKernel *task_m =
                dynamic_cast<CBServerCallingEMRTKernel *>(mp.task);

            string starting_wl = endingCPU->getWorkload();
            endingCPU->setWorkload(Utils::getTaskWorkload(task_m));
            std::cout << "\tEMRTK::" << __func__ << "()" << std::endl;

            double utilCore = getUtilization(endingCPU, endingCPU->getSpeed());
            bool safe =
                (double) task_m->getWCET(1.0) / endingCPU->getSpeed() <=
                (1 - utilCore) *
                    double(task_m->getDeadline() -
                           SIMUL.getTime()); // remaining utilization of
                                             // migrated task > 1 - U_core
                                             // (remaining core util)

            if (!safe)
                std::cout << "\t\tNot safe to migrate " << task_m->toString()
                          << " into " << endingCPU->toString()
                          << " => pick next ready" << std::endl;
            else
                std::cout << "\t\tMigration safe " << task_m->toString()
                          << mp.from->toString() << " -> "
                          << endingCPU->toString() << std::endl;

            std::cout << "\t\t\t" << endingCPU->toString() << " "
                      << endingCPU->getWorkload() << " "
                      << endingCPU->getFrequency() << std::endl;
            std::cout << "\t\t\tCalculation: "
                      << (double) task_m->getWCET(1.0) / endingCPU->getSpeed()
                      << " > (" << 1 - utilCore << ") * ("
                      << task_m->getDeadline() << " - " << SIMUL.getTime()
                      << ")? if yes, unsafe" << std::endl;

            endingCPU->setWorkload(starting_wl);
            return safe;
        }

        /// Is task temporarily migrated on core to
        bool isTaskTemporarilyMigrated(AbsRTTask *t, CPU_BL *to) const {
            bool found = false;

            for (MigrationProposal mp : _temporarilyMigrated) {
                if (mp.task == t && mp.to == to) {
                    found = true;
                    break;
                }
            }

            return found;
        }

        /**
         * Begins the dispatch process (context switch). The task is dispatched,
         * but not executed yet. Its execution on its CPU_BL starts with
         * onEndDispatchMulti()
         */
        void onBeginDispatchMulti(BeginDispatchMultiEvt *e) override;

        /**
         *  First a task is dispatched, but not executed yet, in the
         *  onBeginDispatchMulti. Then, in the onEndDispatchMulti, its execution
         * starts on the processor.
         */
        void onEndDispatchMulti(EndDispatchMultiEvt *e) override;

        /// called when OPP changes on an island
        void onOppChanged(unsigned int curropp, Island_BL *island);

        /// Invoked when a task ends
        void onEnd(AbsRTTask *t) override;

        void onExecutingRecharging(CBServer *cbs) {
            std::cout << "EMRTK::" << __func__ << "()" << std::endl;

            _queues->onExecutingRecharging(cbs);
        }

        /// Callback called when a task on a CBS CEMRTK. goes executing ->
        /// releasing
        void onExecutingReleasing(CBServer *cbs) {
            std::cout << "EMRTK::" << __func__ << "()" << std::endl;
            CPU *cpu = getOldProcessor(cbs);

            // for some reason, here task has wl idle, wrongly (should be kept
            // until the end of this function). reset:
            cpu->setWorkload(Utils::getTaskWorkload(cbs));
            std::cout << "\t" << cpu->getName()
                      << " has now wl: " << cpu->getWorkload()
                      << ", speed: " << cpu->getSpeed() << std::endl;

            _queues->onExecutingReleasing(cpu, cbs);

            if (EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED && cbs->isEmpty()) {
                /**
                  Policy for CBS servers:
                  If the task on a CBS server ends and the server gets empty,
                  schedule ready tasks on the core (i.e., deschedule & schedule
                  server)
                  */
                std::cout << "\tServer's got empty. Server yields (= schedule "
                             "a ready task of core)"
                          << std::endl;
                _queues->yield(cpu);
                cbs->yield(); // server might still have higher priority and
                              // thus still get scheduled (=> 2 running tasks on
                              // cpu)
            }
            if (EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED) {
                /**
                  Schedule ready task on core
                  */
                assert(cbs->getStatus() == ServerStatus::RELEASING);
                std::cout << "CBS enveloping periodic tasks enabled => "
                             "schedule ready on "
                          << cpu->getName() << std::endl;
                _queues->schedule(cpu);
            }

            // cpu->setWorkload("idle");
        }

        /// Callback, when CBS server is killed. It expects the tasks inside the
        /// CBS server still alive.
        void onCBSKilled(AbsRTTask *t, CPU_BL *cpu,
                         CBServerCallingEMRTKernel *cbs) {
            assert(cpu != NULL);
            assert(cbs != NULL);
            std::cout << "EMRTK::" << __func__ << "() for " << cbs->toString()
                      << std::endl;

            _queues->onTaskInServerEnd(t, cpu, cbs); // save util active
            _queues->onEnd(cbs, cpu);
        }

        /// Callback called when a task on a CBS CEMRTK. goes executing ->
        /// releasing (virtual time ends)
        void onReleasingIdle(CBServer *cbs) {
            std::cout << "EMRTK::" << __func__ << "()" << std::endl;
            CPU_BL *endingCPU = dynamic_cast<CPU_BL *>(
                _queues->onReleasingIdle(cbs)); // forget U_active
            AbsRTTask *oldTask = getRunningTask(endingCPU);

            if (!getReadyTasks(endingCPU).empty()) {
                std::cout << "\tCore already has some ready tasks. Scheduling "
                             "one of those"
                          << std::endl;
                _queues->schedule(endingCPU);
                return;
            }
            if (!EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END) {
                std::cout
                    << "\tEMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END disabled"
                    << std::endl;
                return;
            }

            // no ready task on core. Core's idle. Try migrating from other
            // cores
            vector<AbsRTTask *> toBeSkipped;
            MigrationProposal migrationProposal;
            CBServerCallingEMRTKernel *task_m = NULL;
            while (true) {
                // Pick a task to be migrated
                migrationProposal = getTaskToMigrateInto(
                    dynamic_cast<CPU_BL *>(endingCPU), toBeSkipped);
                task_m = dynamic_cast<CBServerCallingEMRTKernel *>(
                    migrationProposal.task);
                if (task_m == NULL)
                    break;
                toBeSkipped.push_back(task_m);

                // Double check if migration breaks previous task schedulability
                if (isMigrationSafe(migrationProposal) &&
                    isMigrationEnergConvenient(migrationProposal))
                    break;
            }

            if (task_m != NULL) {
                std::cout << "\tConfirmed migration of " << task_m->toString()
                          << " into " << endingCPU->toString() << std::endl;
                _queues->onMigrationFinished(migrationProposal.task,
                                             migrationProposal.from,
                                             migrationProposal.to);
            } else // core would be idle. No ready on core and no migrable tasks
                   // found
                migrateTemporarily(endingCPU);
        }

        void onReplenishment(CBServer *cbs) {
            std::cout << "EMRTK::" << __func__ << "()" << std::endl;
            _queues->onReplenishment(cbs);
            dispatch();
        }

        /// Callback, when a CBS server ends a task
        void onTaskInServerEnd(AbsRTTask *t, CPU_BL *cpu, CBServer *cbs) {
            assert(t != NULL);
            assert(cpu != NULL);
            assert(cbs != NULL);

            std::cout << "\tEMRTK::" << __func__ << "()" << std::endl;
            _queues->onTaskInServerEnd(t, cpu, cbs); // save util active
            std::cout << __func__ << "() cbs is empty? " << cbs->isEmpty()
                      << std::endl;
            if (cbs->isEmpty())
                onEnd(cbs);
        }

        /// Callback, when a task gets running on a core
        void onTaskGetsRunning(AbsRTTask *t, CPU_BL *cpu) {
            assert(t != NULL);
            assert(cpu != NULL);
            std::cout << "EMRTK::" << __func__ << "() " << taskname(t) << " on "
                      << cpu->getName() << std::endl;

            std::cout << "\t" << cpu->getName()
                      << " had wl: " << cpu->getWorkload()
                      << ", speed: " << cpu->getSpeed()
                      << ", freq: " << cpu->getFrequency() << std::endl;
            cpu->setWorkload(Utils::getTaskWorkload(t));
            assert(cpu->getWorkload() != "");
            std::cout << "\t" << cpu->getName()
                      << " has now wl: " << cpu->getWorkload()
                      << ", speed: " << cpu->getSpeed()
                      << ", freq: " << cpu->getFrequency() << std::endl
                      << std::endl;
        }

        /**
         * Specifically called when RRScheduler is used, it informs the kernel
         * that finishingTask has just finished its round (slice)
         */
        void onRound(AbsRTTask *finishingTask);

        void newRun() override {
            MRTKernel::newRun();
            _queues->newRun();
        }

        void endRun() override {
            MRTKernel::endRun();
            _queues->endRun();
        }

        /// ---------------------------------------------------- to debug
        /// internal functions...
        bool isDispatchable(AbsRTTask *t, CPU_BL *c) {
            bool isDispatchable = false;

            vector<struct ConsumptionTable> iDeltaPows;
            tryTaskOnCPU_BL(t, c, iDeltaPows);
            isDispatchable = iDeltaPows.empty();

            return isDispatchable;
        }

        void test();

        static double time();

        void printEnvelopes() const {
            for (auto &elem : _envelopes)
                std::cout << elem.first->toString() << " -> "
                          << elem.second->toString() << std::endl;
        }

        void printMap();

        void printBool(bool b);

        void printPolicies() const {
            std::cout << "EMRTK_BALANCE_ENABLED: " << EMRTK_BALANCE_ENABLED
                      << std::endl;
            std::cout << "EMRTK_LEAVE_LITTLE3_ENABLED: "
                      << EMRTK_LEAVE_LITTLE3_ENABLED << std::endl;
            std::cout << "EMRTK_MIGRATE_ENABLED: " << EMRTK_MIGRATE_ENABLED
                      << std::endl; /// Migrations enabled? (if disabled, its
                                    /// dependencies won't work, e.g.
                                    /// EMRTK_CBS_MIGRATE_AFTER_END)
            std::cout << "EMRTK_CBS_YIELD_ENABLED: " << EMRTK_CBS_YIELD_ENABLED
                      << std::endl;

            std::cout << "EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED: "
                      << EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED
                      << std::endl; /// CBS server enveloping periodic tasks?
            std::cout << "EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END: "
                      << EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END
                      << std::endl; /// After task ends its virtual time, it can
                                    /// be migrated (requires CBS_ENVELOPING)
            std::cout << "EMRTK_CBS_MIGRATE_AFTER_END: "
                      << EMRTK_CBS_MIGRATE_AFTER_END << std::endl;
        }

        void printState() const override {
            printState(false);
        }

        void printState(bool alsoQueues, bool alsoCBSStatus = false) const;

        bool manageForcedDispatch(AbsRTTask *);

        void addForcedDispatch(AbsRTTask *t, CPU_BL *c, unsigned int opp,
                               unsigned int times = 1);

        bool manageDiscartedTask(AbsRTTask *t) {
            assert(t != NULL);

            if (_discardedTasks.find(t) != _discardedTasks.end()) {
                std::get<0>(_discardedTasks[t])--;
                if (std::get<0>(_discardedTasks[t]) == 0)
                    _discardedTasks.erase(t);
                return true;
            }

            return false;
        }
    };
} // namespace RTSim

#endif // RTLIB2_0_ENERGYMRTKERNEL_H
