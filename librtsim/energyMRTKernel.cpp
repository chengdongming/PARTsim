/***************************************************************************
 *   begin                : Thu Apr 24 15:54:58 CEST 2003
 *   copyright            : (C) 2003 by Agostino Mascitti
 *   email                : a.mascitti@sssup.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

#include <cassert>

#include <rtsim/cbserver.hpp>
#include <rtsim/cpu.hpp>
#include <rtsim/energyMRTKernel.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>
#include <rtsim/utils.hpp>

namespace RTSim {
    using namespace MetaSim;

    bool EnergyMRTKernel::EMRTK_BALANCE_ENABLED =
        1; /* Can't imagine disabling it, but so policy is in the list :) */
    bool EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED = 0;
    bool EnergyMRTKernel::EMRTK_MIGRATE_ENABLED = 1;
    bool EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED = 0;
    bool EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME = 1;
    bool EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 1;

    bool EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED = 1;
    bool EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 1;
    bool EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 0;

    // EnergyMRTKernel::EnergyMRTKernel(vector<Scheduler *> &qs, Scheduler *s,
    //                                  Island_BL *big, Island_BL *little,
    //                                  const string &name) :
    //     MRTKernel(s,
    //               big->getProcessors().size() +
    //               little->getProcessors().size(), name) {
    //     setIslandBig(big);
    //     setIslandLittle(little);
    //     // // FIXME: Why did the old implementation set the kernel for each
    //     island?
    //     // big->setKernel(this);
    //     // little->setKernel(this);
    //     for (CPU_BL *c : getProcessors()) {
    //         _m_currExe[c] = NULL;
    //         _isContextSwitching[c] = false;
    //     }
    //     _sched->setKernel(this);
    //     setTryingTaskOnCPU_BL(false);
    //     // todo is there a shorter solution to pass directly vector<CPU_BL*>
    //     to
    //     // EMS?
    //     vector<CPU_BL *> cpus = getProcessors();
    //     vector<CPU *> v;
    //     for (CPU_BL *c : cpus)
    //         v.push_back((CPU *) c);
    //     _queues =
    //         new EnergyMultiCoresScheds(this, v, qs, "energymultischeduler");
    // }

    EnergyMultiCoresScheds::EnergyMultiCoresScheds(MRTKernel *kernel,
                                                   vector<CPU *> &cpus,
                                                   vector<Scheduler *> &s,
                                                   const string &name) :
        MultiCoresScheds(kernel, cpus, s, kernel->getName() + name) {}

    AbsRTTask *EnergyMRTKernel::getRunningTask(CPU *c) {
        AbsRTTask *t = _queues->getRunningTask(c);

        if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED)
            t = getEnveloper(t);

        return t;
    }

    vector<AbsRTTask *> EnergyMRTKernel::getReadyTasks(CPU_BL *c) const {
        vector<AbsRTTask *> t = _queues->getReadyTasks(c);
        vector<AbsRTTask *> tt;

        if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED)
            tt = getEnvelopers(t);
        else
            tt = t;

        for (AbsRTTask *a : tt)
            assert(NULL != dynamic_cast<CBServerCallingEMRTKernel *>(a));

        return tt;
    }

    CPU_BL *EnergyMRTKernel::getProcessorReady(AbsRTTask *t) const {
        if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
            dynamic_cast<PeriodicTask *>(t))
            t = getEnveloper(t);

        CPU_BL *c = dynamic_cast<CPU_BL *>(_queues->isInAnyQueue(t));
        if (_queues->getRunningTask(c) == t)
            c = NULL;

        return c;
    }

    double EnergyMRTKernel::getUtilization(AbsRTTask *task,
                                           double capacity) const {
        double util =
            ceil(task->getRemainingWCET(capacity)) / double(task->getPeriod());

        printf(
            "\t\t\tgetUtilization of considered task %f/%f (capacity=%f)=%f\n",
            task->getRemainingWCET(capacity), double(task->getPeriod()),
            capacity, util);
        return util;
    }

    double EnergyMRTKernel::getUtilization(CPU_BL *c, double capacity) const {
        double utilization = 0.0;
        vector<AbsRTTask *> ths = getReadyTasks(c);
        AbsRTTask *runningTask =
            const_cast<EnergyMRTKernel *>(this)->getRunningTask(c);
        if (runningTask != NULL)
            ths.push_back(runningTask);

        for (AbsRTTask *th : ths) {
            if (isTaskTemporarilyMigrated(th, c))
                continue;

            if (getCBServer_Utilization(th, utilization, capacity))
                continue;

            utilization +=
                ceil(th->getRemainingWCET(capacity)) / double(th->getPeriod());
            std::cout << "\t\t\tUtilization task already in CPU, "
                      << th->toString() << ", is "
                      << ceil(th->getWCET(capacity)) << "/" << th->getPeriod()
                      << " = "
                      << ceil(th->getRemainingWCET(capacity)) /
                             double(th->getPeriod())
                      << " - CPU capacity=" << capacity << std::endl
                      << "\t\t\t\ttask WCET "
                      << ceil(th->getRemainingWCET(capacity)) << " DL "
                      << th->getPeriod() << std::endl;
        }

        double u_active = _queues->getUtilization_active(c);
        std::cout << "\t\t\tU_active on core " << c->getName()
                  << " (for CBS server): " << u_active << std::endl;
        utilization += u_active;

        double u_tempMig = getUtilization_temporarilyMigrated(c);
        std::cout << "\t\t\tU_tempMig on " << c->toString() << " = "
                  << u_tempMig << std::endl;
        utilization += u_tempMig;

        return utilization;
    }

    double EnergyMRTKernel::getIslandUtilization(double capacity,
                                                 IslandType island,
                                                 int *nTasksIsland) const {
        std::cout << "\t\t\t" << __func__ << "()" << std::endl;
        // Sum of running and ready tasks on island + utils active
        double utilizationIsland = 0.0;

        for (CPU_BL *c1 : getProcessors(island)) {
            std::cout << "\t\t\t\t" << c1->getName() << ":" << std::endl;

            utilizationIsland += getUtilization(c1, capacity);

            double u_active_c1 = _queues->getUtilization_active(c1);
            utilizationIsland += u_active_c1;
            std::cout << "\t\t\t\tutil active=" << u_active_c1 << " ("
                      << c1->getName() << ") -> isl util=" << utilizationIsland
                      << std::endl;
        }

        std::cout << "\t\t\t\tisland utilization=" << utilizationIsland
                  << std::endl;
        return utilizationIsland;
    }

    /// Adds the CBS server utilization to "utilization", if server status is
    /// executing or recharging, and returns true
    bool EnergyMRTKernel::getCBServer_Utilization(AbsRTTask *task,
                                                  double &utilization,
                                                  const double capacity) const {
        std::cout << "\t\t\t\t" << __func__ << "(). init util=" << utilization
                  << std::endl;
        CBServerCallingEMRTKernel *cbs =
            dynamic_cast<CBServerCallingEMRTKernel *>(task);

        if (cbs == NULL) {
            std::cout << "\t\t\t\t\tnot a CBServerCallingEMRTKernel => skip"
                      << std::endl;
            return false;
        }

        if (cbs->isEmpty()) {
            std::cout << "\t\t\t\t\tServer's empty => skip, you consider "
                         "Util_actives ("
                      << cbs->getName() << ")" << std::endl;
            return true;
        }

        // todo rem
        std::cout << "\t\t\t\t\tserver status: " << cbs->getStatusString()
                  << std::endl;
        // server utilization (its WCET/period) considered only if it's
        // executing or recharging
        if (cbs->getStatus() == ServerStatus::EXECUTING ||
            cbs->getStatus() == ServerStatus::RECHARGING) {
            utilization +=
                cbs->getRemainingWCET(capacity) / double(cbs->getPeriod());
            std::cout
                << "\t\t\t\t\tCBS server is executing. utilization increased "
                   "to "
                << cbs->getRemainingWCET(capacity) << "/"
                << double(cbs->getPeriod()) << "=" << utilization
                << " capacity=" << capacity << std::endl;
            return true;
        }

        std::cout << "\t\t\t\t\tCBS server not executing or recharging => skip"
                  << std::endl;
        return false;
    }

    double EnergyMRTKernel::getUtilization_temporarilyMigrated(
        CPU_BL *from) const {
        double util = 0.0;

        for (MigrationProposal mp : _temporarilyMigrated) {
            if (mp.from == from)
                util += getUtilization(
                    mp.task,
                    from->getSpeed()); // if speed's changed, than you've passed
                                       // through dispatch() => temp.migr.ed
                                       // task removed already
        }

        return util;
    }

    void EnergyMRTKernel::onOppChanged(unsigned int curropp,
                                       Island_BL *island) {
        if (isTryngTaskOnCPU_BL())
            return;

        // std::cout << __func__ << "(). envelopes: " << std::endl;
        // printEnvelopes();

        // update budget of servers (enveloping periodic tasks) in the island
        for (auto &elem : _envelopes) {
            // std::cout << __func__ << "(). elem = " << elem.first->toString()
            // << "
            // -> " << elem.second->toString() << std::endl;
            CPU_BL *c = getProcessor(elem.first);

            // dispatch() dispatches a task per time, setting CPU OPP => some
            // tasks don't have core yet
            if (c == NULL)
                continue;

            string startingWL = c->getWorkload();
            c->setWorkload(Utils::getTaskWorkload(elem.first));
            std::cout << __func__ << "(). cpu of elem is " << c->toString()
                      << " wl: " << c->getWorkload() << std::endl;
            if (c->getIsland()->type() == island->type()) {
                Tick taskWCET = Tick(ceil(elem.first->getWCET(c->getSpeed())));
                std::cout << __func__ << "(). changing budget to "
                          << elem.second->toString() << " to " << taskWCET
                          << ". core: " << c->toString()
                          << " speed:" << c->getSpeed() << std::endl;
                elem.second->changeBudget(taskWCET);
                // elem.second->changeQ(taskWCET);
            }

            c->setWorkload(startingWL);
        }
        std::cout << std::endl;

        // scale wcets of tasks on the island
    }

    // ----------------------------------------------------------- testing

    void EnergyMRTKernel::test() {
        // PeriodicTask T7_task0 DL = T 500 WCET(abs) 63 in CPU LITTLE_0 freq
        // 500 freq 3
        AbsRTTask *t = dynamic_cast<AbsRTTask *>(_sched->getTaskN(0));
        CPU_BL *p;
        dispatch(p, t, 3);
        p->setWorkload("bzip2");
        std::cout << "CPU is " << p->toString() << " freq " << p->getFrequency()
                  << " " << t->toString() << std::endl;

        int nTaskIsland = 0;
        // std::cout << "task util " << getIslandUtilization(p->getSpeed(500.0),
        // p->type(), &nTaskIsland);

        exit(0);
    }

    // for gdb
    void EnergyMRTKernel::printMap() {
        std::cout << _queues->toString();
    }

    // for gdb
    void EnergyMRTKernel::printBool(bool b) {
        std::cout << b << std::endl;
    }

    void EnergyMRTKernel::printState(bool alsoQueues,
                                     bool alsoCBSStatus) const {
        std::cout << "t=" << SIMUL.getTime()
                  << " State of scheduler:" << std::endl
                  << "Running tasks:" << std::endl
                  << "\t";
        for (CPU_BL *c : getProcessors()) {
            AbsRTTask *t = _queues->getRunningTask(c);
            if (dynamic_cast<CBServer *>(t) &&
                dynamic_cast<CBServer *>(t)->isYielding())
                t = _queues->getFirstReady(c);
            std::cout << c->getName() << ": " << (t == NULL ? "0" : taskname(t))
                      << "\t";
        }

        if (alsoQueues)
            std::cout << std::endl
                      << "Queues:" << std::endl
                      << _queues->toString();

        if (alsoCBSStatus) {
            std::cout << "CBS servers (= periodic tasks) statuses:"
                      << std::endl;
            for (const auto &elem : _envelopes) {
                std::cout << "- " << elem.second->toString();
                if (elem.second->getStatus() == ServerStatus::RELEASING) {
                    std::cout
                        << ". util_active="
                        << std::to_string(
                               _queues->getUtilization_active(elem.second))
                        << " expires at t=" << elem.second->getVirtualTime();
                }
                std::cout << std::endl;
            }
        }

        std::cout << std::endl;
    }

    void EnergyMRTKernel::addForcedDispatch(AbsRTTask *t, CPU_BL *c,
                                            unsigned int opp,
                                            unsigned int times) {
        _m_forcedDispatch[t] = std::make_tuple(c, opp, times);
    }

    // for gdb
    bool EnergyMRTKernel::manageForcedDispatch(AbsRTTask *t) {
        if (_m_forcedDispatch.find(t) !=
            _m_forcedDispatch.end()) { //&& std::get<2>(_m_forcedDispatch[t]) ==
                                       // SIMUL.getTime()) {
            std::cout << __func__ << std::endl;
            dispatch(std::get<0>(_m_forcedDispatch[t]), t,
                     std::get<1>(_m_forcedDispatch[t]));

            std::get<2>(_m_forcedDispatch[t])--;
            if (std::get<2>(_m_forcedDispatch[t]) == 0)
                _m_forcedDispatch.erase(t);
            return true;
        }
        return false;
    }

    double EnergyMRTKernel::time() {
        return double(SIMUL.getTime());
    }

    // --------------------------------------------------------------- context
    // switch

    bool EnergyMRTKernel::isToBeDescheduled(CPU_BL *p, AbsRTTask *t) {
        assert(p != NULL);

        if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
            dynamic_cast<PeriodicTask *>(t))
            t = getEnveloper(t);

        if (dynamic_cast<RRScheduler *>(_sched) &&
            t != NULL) { // t is null at time == 0
            bool res = false;
            try {
                res = dynamic_cast<RRScheduler *>(_queues->getScheduler(p))
                          ->isRoundExpired(t);
            } catch (BaseExc &e) { /* */
            }
            return res;
        }
        return false; // neutral...
    }

    // Note MRTKernel version differs: dispatch() gives tasks a free CPU and
    // calls onBDM(), which in turns assigns them. EnergyMRTKernel, instead,
    // needs to make assignment decisions: dispatch() chooses a CPU for all
    // tasks, and on*DM() makes the context switch (split into onBDM() and
    // onEBM(), as in MRTKernel) Begins context switch on a core, task is at the
    // end of onBDM() still ready on core.
    void EnergyMRTKernel::onBeginDispatchMulti(BeginDispatchMultiEvt *e) {
        DBGENTER(_KERNEL_DBG_LEV);

        CPU_BL *p = dynamic_cast<CPU_BL *>(e->getCPU());
        AbsRTTask *dt = _queues->getRunningTask(p);
        AbsRTTask *st = e->getTask();
        assert(st != NULL);
        assert(p != NULL);

        std::cout << std::endl
                  << "time =" << SIMUL.getTime()
                  << " EnergyMRTKernel::onBeginDispatchMulti() for "
                  << taskname(st) << " on " << p->toString() << std::endl;
        if (st != NULL && dt == st) {
            string ss = "Decided to dispatch " + st->toString() +
                        " on its former CPU => skip context switch";
            DBGPRINT(ss);
            std::cout << ss << std::endl;
        }
        // if necessary, deschedule the task.
        if (dt != NULL || isToBeDescheduled(p, dt)) {
            _m_oldExe[dt] = p;
            _m_currExe[p] = NULL;
            dt->deschedule();
            std::cout << dt->toString() << " descheduled for " << taskname(st)
                      << std::endl;
        }

        DBGPRINT("Scheduling task ", taskname(st), " on cpu ", p->toString());
        std::cout << __func__ << " Scheduling task " << taskname(st)
                  << " on cpu " << p->toString() << std::endl;

        //_endEvt[p]->setTask(st);
        _isContextSwitching[p] = true;
        // if you exit(0) here, dispatch() has already chosen a CPU forall tasks
        // exit(0);
        Tick overhead(_contextSwitchDelay);
        CPU_BL *oldProcessor = dynamic_cast<CPU_BL *>(getOldProcessor(st));
        if (st != NULL && oldProcessor != p && oldProcessor != NULL)
            overhead += _migrationDelay;

        _queues->onBeginDispatchMultiFinished(p, st, overhead);

        std::cout << "\t" << taskname(st)
                  << " end ctx switch set at t=" << SIMUL.getTime() + overhead
                  << " - overhead=" << overhead << std::endl;
    }

    /**
        Called after dispatch(), i.e. after choosing a CPU forall arrived tasks.
        Here task begins executing in its core. End of context switch on core.
      */
    void EnergyMRTKernel::onEndDispatchMulti(EndDispatchMultiEvt *e) {
        AbsRTTask *t = e->getTask();
        CPU_BL *cpu = dynamic_cast<CPU_BL *>(e->getCPU());
        assert(t != NULL);
        assert(cpu != NULL);

        std::cout << std::endl
                  << "time =" << SIMUL.getTime()
                  << " EnergyMRTKernel::onEndDispatchMulti() for "
                  << taskname(t) << " on " << cpu->toString() << std::endl;
        _queues->onEndDispatchMultiFinished(cpu, t);
        MRTKernel::onEndDispatchMulti(e);

        // cpu->setBusy(true); // introduced afterwards

        // use case: 2 tasks arrive at t=0 and are scheduled on big 0 and big 1
        // freq 1100. Then, at time t=100, another task arrives and the
        // algorithm decides to schedule it on big 2 freq 2000. Thus, big island
        // freq is 2000, not 1100 (the max). todo: maybe it's useless to have
        // these instructions below
        unsigned int opp = _queues->getOPP(cpu);
        if (opp > cpu->getOPP()) {
            std::cout << "\t" << t->toString() << " " << cpu->toString()
                      << " updating opp to " << opp << std::endl;
            cpu->setOPP(opp);
        }

        _m_oldExe[t] = cpu;

        std::cout << std::endl;

        onTaskGetsRunning(t, cpu);
    }

    // ----------------------------------------------------------- task ends
    // WCET

    void EnergyMRTKernel::onEnd(AbsRTTask *t) {
        DBGENTER(_KERNEL_DBG_LEV);

        // only on big-little: update the state of the CPUs island
        CPU_BL *p = dynamic_cast<CPU_BL *>(getProcessor(t));
        DBGPRINT(t->toString(), " has just finished on ", p->toString(),
                 ". Actual time = [", SIMUL.getTime(), "]");
        std::cout << ".............................." << std::endl;
        std::cout << "\tActual time = [" << SIMUL.getTime()
                  << "]. EMRTK::" << __func__ << "(). " << t->toString()
                  << " has just finished on " << p->toString() << std::endl;

        _sched->extract(t);
        // todo delete std::cout
        string state = _sched->toString();
        if (state == "")
            std::cout << "\t(External scheduler is empty)" << std::endl;
        else
            std::cout << "\tState of external scheduler: " << std::endl
                      << "\t\t" << state << std::endl;

        _m_oldExe[t] = p;
        _m_currExe.erase(p);
        _m_dispatched.erase(t);
        _queues->onEnd(t, p);

        // // FIXME: the busy status is now an invariant
        // if (_queues->isEmpty(p) && getRunningTask(p) == NULL)
        //     p->setBusy(false);

        std::cout << "\tState before migration (migrations after end="
                  << EMRTK_CBS_MIGRATE_AFTER_END
                  << ", Temporarily migrate after end="
                  << EMRTK_TEMPORARILY_MIGRATE_END
                  << ", core is busy=" << p->busy() << "):" << std::endl;
        printState(true);

        if (!p->busy() && EMRTK_CBS_MIGRATE_AFTER_END) {
            std::cout << "\tTrying to migrate into core" << std::endl;
            if (!migrateInto(p) && EMRTK_TEMPORARILY_MIGRATE_END) {
                std::cout
                    << "\tFailed to migrate into core, trying to temporarily "
                       "migrate into core"
                    << std::endl;
                bool res = migrateTemporarily(p);
                std::cout << "\tTemporary migration into core "
                          << (res ? "succeeded" : "failed") << std::endl;
            }
        } else { // core has already some ready tasks
            std::cout << "\tcore busy, schedule ready task" << std::endl;
            _queues->schedule(p);
        }

        if (!p->getIsland()->busy()) {
            std::cout << "\t" << p->getIsland()->getName()
                      << "'s got free => clock down to min speed" << std::endl;
            p->getIsland()->setOPP(0);
        }

        if (_queues->isEmpty(p)) {
            std::cout << "\t" << p->getName() << " is empty -> wl idle"
                      << std::endl;
            p->setWorkload("idle");
        }
        std::cout << "\tState after migration:" << std::endl;
        printState(true);
    }

    bool EnergyMRTKernel::migrateInto(CPU_BL *endingCPU,
                                      vector<AbsRTTask *> toBeSkipped) {
        /**
           Migration mechanism: a task finishes on CPU c, leaving it idle.
           If c is a little, try moving ready tasks originally assigned to
           big island. If you can't find any, then balance load on little
           island. If c is a big core, balance load on big island.

           Try not to touch running tasks.
        */
        if (!EMRTK_MIGRATE_ENABLED) {
            std::cout << "Migration policy disabled => skip" << std::endl;
            return false;
        }
        if (getReadyTasks(endingCPU).size() != 0) {
            std::cout << endingCPU->getName()
                      << " already has some ready task => skip migration"
                      << std::endl;
            return false;
        }
        if (getRunningTask(endingCPU) != NULL) {
            assert(EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
                   EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END);
            std::cout << endingCPU->getName()
                      << " already has a running task => skip migration"
                      << std::endl;
            return false;
        }
        std::cout << "\t" << __func__ << "() time=" << SIMUL.getTime()
                  << std::endl;

        MigrationProposal migrationProposal =
            getTaskToMigrateInto(endingCPU, toBeSkipped);

        if (migrationProposal.task == NULL)
            std::cout << "\t\tEMRTK::" << __func__ << "(). No migration done"
                      << std::endl;
        else {
            migrationProposal.to->setWorkload(
                Utils::getTaskWorkload(migrationProposal.task));

            // make task run on ending core.
            // onEndDispatchMulti will take care of increasing core OPP
            _queues->onMigrationFinished(migrationProposal.task,
                                         migrationProposal.from,
                                         migrationProposal.to);
        }

        return migrationProposal.task != NULL;
    } // end EMRTK::migrateInto()

    EnergyMRTKernel::MigrationProposal
        EnergyMRTKernel::getTaskToMigrateInto(CPU_BL *endingCPU,
                                              vector<AbsRTTask *> toBeSkipped) {
        assert(endingCPU != NULL);
        std::cout << "\tEMRTK::" << __func__ << "()" << std::endl;

        MigrationProposal migrationProposal =
            migrateFromBig(endingCPU, toBeSkipped);

        if (migrationProposal.task == NULL) // no migration from big to little
            migrationProposal = balanceLoad(endingCPU, toBeSkipped);

        if (migrationProposal.task != NULL &&
            isMigrationSafe(migrationProposal) == false) {
            toBeSkipped.push_back(migrationProposal.task);
            migrationProposal = getTaskToMigrateInto(endingCPU, toBeSkipped);
        }

        return migrationProposal;
    }

    EnergyMRTKernel::MigrationProposal
        EnergyMRTKernel::migrateFromBig(CPU_BL *endingCPU,
                                        vector<AbsRTTask *> toBeSkipped) {
        MigrationProposal migrationProposal = {.task = NULL,
                                               .from = NULL,
                                               .to = NULL};

        vector<AbsRTTask *> readyTasks;
        if (endingCPU->getIsland()->type() == IslandType::LITTLE) {
            for (CPU_BL *c : getIslandBig()->getProcessors()) {
                readyTasks = getReadyTasks(c);
                for (AbsRTTask *tt : readyTasks) {
                    vector<struct ConsumptionTable> iDeltaPows;
                    setTryingTaskOnCPU_BL(true); // to bypass onOPPChanged()
                    tryTaskOnCPU_BL(tt, endingCPU, iDeltaPows);
                    setTryingTaskOnCPU_BL(false);
                    if (!iDeltaPows.empty() &&
                        !Utils::exists(tt, toBeSkipped)) {
                        std::cout
                            << "\t\tMigration proposal of " << tt->toString()
                            << " from " << c->toString() << " to "
                            << iDeltaPows.at(0).cpu->toString()
                            << " with frequency "
                            << endingCPU->getFrequency(iDeltaPows.at(0).opp)
                            << std::endl;
                        migrationProposal.task = tt;
                        migrationProposal.from = c;
                        migrationProposal.to = endingCPU;
                        goto endFun; // break and return
                    }
                }
            }
            std::cout
                << "\t\tNo migration from big island to endingCPU => balance "
                   "little island load"
                << std::endl;
        }

    endFun:
        return migrationProposal;
    }

    EnergyMRTKernel::MigrationProposal
        EnergyMRTKernel::balanceLoad(CPU_BL *endingCPU,
                                     vector<AbsRTTask *> toBeSkipped) {
        std::cout << "\t\tEMRTK::" << __func__
                  << "(). Balancing load of island: " << endingCPU->getName()
                  << std::endl;
        MigrationProposal migrationProposal = {.task = NULL,
                                               .from = NULL,
                                               .to = NULL};
        vector<AbsRTTask *> readyTasks;

        // Take a ready task of the same island and put it into endingCPU.
        for (CPU_BL *c : getProcessors(endingCPU->getIsland()->type())) {
            readyTasks = getReadyTasks(c);
            unsigned int nTasksOnCore =
                readyTasks.size() + (getRunningTask(c) == NULL ? 0 : 1);
            if (nTasksOnCore > 1 &&
                !Utils::exists(readyTasks.at(0), toBeSkipped)) {
                AbsRTTask *tt = readyTasks.at(0);
                std::cout << "\t\tMigration proposal of " << tt->toString()
                          << " from " << c->toString() << " to "
                          << endingCPU->toString() << " with same frequency "
                          << std::endl;
                migrationProposal.task = tt;
                migrationProposal.from = c;
                migrationProposal.to = endingCPU;
                break;
            }
        }

        return migrationProposal;
    }

    void EnergyMRTKernel::onRound(AbsRTTask *finishingTask) {
        std::cout << "t = " << SIMUL.getTime() << " " << __func__
                  << " for finishingTask = " << taskname(finishingTask)
                  << std::endl;
        finishingTask->deschedule();
        _queues->onRound(finishingTask, getProcessor(finishingTask));
    }

    // ---------------------------------------------------------- CBServer
    // management

    /// Returns active utilization on CPU c. Only for debug
    double EnergyMRTKernel::getUtilization_active(CPU_BL *c) const {
        double u_act = _queues->getUtilization_active(c);
        return u_act;
    }

    // ----------------------------------------------------------- choosing
    // cores for tasks

    void EnergyMRTKernel::leaveLittle3(
        AbsRTTask *t, vector<struct ConsumptionTable> iDeltaPows,
        CPU_BL *&chosenCPU) {
        /**
         * Policy of leaving a little core free for big-WCET tasks, which
         * otherwise would be scheduled on big cores, thus increasing power
         * consumption. Mechanism: if you want to put a task on little 3, but it
         * also fits in another little core, put it in the other little.
         * Otherwise (if it only fits on little 3 and in bigs, choose little 3).
         */

        std::cout << __func__ << "():" << std::endl;
        if (!EMRTK_LEAVE_LITTLE3_ENABLED) {
            std::cout << "\tPolicy deactivated. Skip" << std::endl;
            return;
        }
        if (chosenCPU->getName().find("LITTLE_3") == string::npos ||
            chosenCPU->getIsland()->type() == IslandType::BIG) {
            std::cout << "chosenCPU in big island or is not little_3 => skip"
                      << std::endl;
            return;
        }

        if (EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED &&
            dynamic_cast<PeriodicTask *>(t))
            t = getEnveloper(t);

        bool fitsInOtherCore = true; // if it only fits on little 3 and in bigs

        for (int i = 0; i < iDeltaPows.size(); i++) {
            std::cout << iDeltaPows[i].cons << " "
                      << iDeltaPows[i].cpu->toString() << std::endl;
            if (iDeltaPows[i].cpu->getIsland()->type() == IslandType::LITTLE &&
                iDeltaPows[i].cpu->getName().find("LITTLE_3") == string::npos) {
                chosenCPU = iDeltaPows[i].cpu;
                chosenCPU->setOPP(iDeltaPows[i].opp);
                std::cout << "Changing to " << iDeltaPows[i].cpu->toString()
                          << " - chosenCPU=" << chosenCPU->toString()
                          << std::endl;
                fitsInOtherCore = false;
                break;
            }
        }

        if (fitsInOtherCore) {
            std::cout << "Task only fits on little 3 and in bigs => stay in "
                         "LITTLE_3, CPU not changed"
                      << std::endl;
        }
    }

    void EnergyMRTKernel::balanceLoadEnergy(
        CPU_BL **chosenCPU, unsigned int &chosenOPP, bool &chosenCPUchanged,
        vector<struct ConsumptionTable> iDeltaPows) {
        // Load balancing policy: don't put all the load on a core of an island,
        // but use also the others. Mechanism: if chosen CPU is busy, find a
        // free CPU in the island with the same consumption. Note: with this
        // algorithm tasks cannot be assigned to a core in an island different
        // than the originally chosen one
        std::cout << __func__ << ":" << std::endl;
        if ((*chosenCPU)->busy()) {
            std::cout << (*chosenCPU)->toString() << " was chosen but it's busy"
                      << std::endl;
            for (int i = 1; i < iDeltaPows.size(); i++) {
                std::cout << iDeltaPows[i].cons << " VS " << iDeltaPows[0].cons
                          << " busy? " << iDeltaPows[i].cpu->busy() << " "
                          << iDeltaPows[i].cpu->toString() << std::endl;
                if (iDeltaPows[i].cons == iDeltaPows[0].cons &&
                    !iDeltaPows[i].cpu->busy()) {
                    *chosenCPU = iDeltaPows[i].cpu;
                    chosenOPP = iDeltaPows[i].opp;
                    chosenCPUchanged = true;
                    break;
                }
            }
        } else
            std::cout << "\tCPU is not busy => skip" << std::endl;
    }

    void EnergyMRTKernel::chooseCPU_BL(
        AbsRTTask *t, vector<struct ConsumptionTable> iDeltaPows) {
        // TODO: switch from nlogn to n for min()
        // sort table of consumption
        sort(iDeltaPows.begin(), iDeltaPows.end(),
             [](struct ConsumptionTable const &e1,
                struct ConsumptionTable const &e2) {
                 return e1.cons < e2.cons;
             });

        // todo delete after debug
        std::cout << "EMRTK::" << __func__ << "()" << std::endl;
        for (auto elem : iDeltaPows) {
            std::cout << elem.cons << " " << elem.cpu->toString()
                      << " with new opp " << elem.cpu->getFrequency(elem.opp)
                      << std::endl;
            assert(elem.cpu->getOPPIndex() <= elem.opp);
        }

        struct ConsumptionTable chosen = iDeltaPows[0];
        CPU_BL *chosenCPU = chosen.cpu;
        unsigned int chosenOPP = chosen.opp;
        bool chosenCPUchanged = false;
        bool toBeChanged = chosenCPU->busy();

        balanceLoadEnergy(&chosenCPU, chosenOPP, chosenCPUchanged, iDeltaPows);

        std::cout << "Temporarily chosenCPU: " << chosenCPU->toString()
                  << " with freq " << chosenCPU->getFrequency(chosenOPP)
                  << std::endl;
        leaveLittle3(t, iDeltaPows, chosenCPU);

        dispatch(chosenCPU, t, chosenOPP);
        setTryingTaskOnCPU_BL(true);
        std::cout << "time = " << SIMUL.getTime()
                  << " - going to schedule task " << t->toString() << " in CPU "
                  << chosenCPU->getName() << " with freq "
                  << chosenCPU->getFrequency(chosenOPP)
                  << " speed=" << chosenCPU->getSpeedByOPP(chosenOPP)
                  << " chosenOPP " << chosenOPP << " - CPU"
                  << (chosenCPUchanged && toBeChanged ? "" : " not")
                  << " changed " << std::endl;
        setTryingTaskOnCPU_BL(false);
    }

    void EnergyMRTKernel::dispatch(CPU *p, AbsRTTask *t, int opp) {
        std::cout << "EMRTK::" << __func__
                  << "(p, t, opp) task:" << t->toString() << std::endl;
        CPU_BL *pp = dynamic_cast<CPU_BL *>(p);

        removeTaskTemporarilyMigrated(pp);

        _queues->insertTask(t, pp, opp);

        // This is meant to be a virtual assignment of CPU OPP.
        // Needed to make the rest of the code work properly
        pp->setOPP(opp);
        // pp->setBusy(true);
    }

    /* Decide a CPU for each ready task */
    void EnergyMRTKernel::dispatch() {
        DBGENTER(_KERNEL_DBG_LEV);
        // setTryingTaskOnCPU_BL(true);

        int num_newtasks = 0; // # "new" tasks in the ready queue
        int i = 0;

        while (_sched->getTaskN(num_newtasks) != NULL)
            num_newtasks++;

        _sched->print();
        DBGPRINT("New tasks: ", num_newtasks);
        print();
        if (num_newtasks == 0)
            return; // nothing to do

        i = 0;
        std::vector<CPU_BL *> cpus = getProcessors();
        do {
            AbsRTTask *t = dynamic_cast<AbsRTTask *>(_sched->getTaskN(i++));
            if (t == NULL)
                break;
            std::cout << "Actual time = [" << SIMUL.getTime() << "]"
                      << std::endl;
            std::cout << "Dealing with task " << t->toString() << "."
                      << std::endl;

            // for testing
            if (manageForcedDispatch(t) || manageDiscartedTask(t)) {
                num_newtasks--;
                continue;
            }

            if (_queues->isInAnyQueue(t)) {
                // dispatch() is called even before onEndMultiDispatch()
                // finishes and thus tasks seem not to be dispatching (i.e.,
                // assigned to a processor)
                std::cout
                    << "\tTask has already been dispatched, but dispatching "
                       "is not complete => skip (you\'ll still see "
                       "desched&sched evt, to trace tasks)"
                    << std::endl;
                continue;
            }

            if (getProcessor(t) !=
                NULL) { // e.g., task ends => migrateInto() => dispatch()
                std::cout << "\tTask is running on a CPU already => skip"
                          << std::endl;
                continue;
            }

            // otherwise scale up CPUs frequency
            DBGPRINT("Trying to scale up CPUs");
            std::cout << std::endl << "Trying to scale up CPUs" << std::endl;
            vector<struct ConsumptionTable> iDeltaPows;
            std::cout << std::endl
                      << "\t------------\n\tCurrent situation:\n\t"
                      << _queues->toString() << "\t------------" << std::endl;

            setTryingTaskOnCPU_BL(true);
            for (CPU_BL *c : cpus)
                tryTaskOnCPU_BL(t, c, iDeltaPows);
            setTryingTaskOnCPU_BL(false);

            if (!iDeltaPows.empty())
                chooseCPU_BL(t, iDeltaPows);
            else
                std::cout << "Cannot schedule " << t->toString() << " anywhere"
                          << std::endl;

            _sched->extract(t);
            num_newtasks--;

            std::cout << "Decisions 'til now:" << std::endl;
            std::cout << _queues->toString() << std::endl;

            // if you get here, task is not schedulable in real-time
        } while (num_newtasks > 0);
        // setTryingTaskOnCPU_BL(false);

        for (CPU_BL *c : getProcessors()) {
            _queues->schedule(c);
        }
    }

    void EnergyMRTKernel::tryTaskOnCPU_BL(
        AbsRTTask *t, CPU_BL *c, vector<struct ConsumptionTable> &iDeltaPows) {
        if (c->disabled())
            return;
        int startingOPP = c->getOPPIndex();
        double frequency = c->getFrequency();
        string startingWL = c->getWorkload();
        c->setWorkload(Utils::getTaskWorkload(t));

        std::cout << "\tTrying to schedule on CPU " << c->toString()
                  << " using freq " << frequency
                  << " - it has already ntasks=" << getReadyTasks(c).size()
                  << std::endl;

        // for (int ooo = c->getIslandCurOPP(); ooo < c->getOPPs().size();
        // ooo++) {
        for (OPP tryOPP : c->getHigherOPPs()) {
            int ooo = c->getIsland()->getOPPIndexByOPP(tryOPP);
            double newFreq = c->getFrequency(ooo);
            double newCapacity = 0.0;

            c->setOPP(ooo);
            newCapacity = c->getSpeed(newFreq);
            printf("\t\tUsing frequency %d instead of %d (cap. %f)\n",
                   (int) newFreq, (int) frequency, newCapacity);

            // check whether task is admissible with the new frequency and where
            if (_sched->isAdmissible(c, getReadyTasks(c), t)) {
                std::cout << "\t\t\tHere task would be admissible" << std::endl;

                double utilization =
                    0.0; // utilization on the CPU c (without new task)
                double utilization_t =
                    0.0; // utilization of the considered new task
                double newUtilizationIsland =
                    0.0; // utilization of tasks in the island with new freq -
                         // cores share frequency
                double oldUtilizationIsland = 0.0;
                double iPowWithNewTask = 0.0;
                double iOldPow = 0.0;
                double iDeltaPow = 0.0; // additional power to schedule t on CPU
                                        // c on the whole island (big/little)
                int nTaskIsland = 0;
                IslandType island;

                // utilization on CPU c with the new frequency
                utilization = getUtilization(c, newCapacity);

                if (utilization > 1.0) {
                    std::cout
                        << "\t\t\tCPU utilization is already >= 100% => skip "
                           "OPP"
                        << std::endl;
                    continue;
                } else
                    std::cout
                        << "\t\t\tTotal utilization (running+ready+active-new "
                           "task) tasks already in CPU "
                        << c->toString() << " = " << utilization << std::endl;

                utilization_t = getUtilization(t, newCapacity);
                std::cout << "\t\t\tUtilization cur/new task "
                          << t->toString().substr(0, 25) << "... would be "
                          << utilization_t << " - CPU capacity=" << newCapacity
                          << std::endl;
                std::cout << "\t\t\t\tScaled task WCET "
                          << t->getRemainingWCET(newCapacity) << " DL "
                          << t->getPeriod() << std::endl;

                if (utilization + utilization_t > 1.0) {
                    std::cout << "\t\t\tTotal utilization + cur/new task "
                                 "utilization would be "
                              << utilization << "+" << utilization_t << "="
                              << utilization + utilization_t
                              << " >= 100% => skip OPP" << std::endl;
                    continue;
                }
                // std::cout << "Final core utilization running+ready+active+new
                // task = " << utilization + utilization_t << std::endl;

                // Ok, task can be placed on CPU c, compute power delta

                // utilization island where CPU c is
                island = c->getIsland()->type();
                newUtilizationIsland =
                    getIslandUtilization(newCapacity, island, NULL);
                oldUtilizationIsland = getIslandUtilization(
                    c->getSpeed(frequency), island, &nTaskIsland);
                std::cout << "\t\t\tIn the CPU island of " << c->getName()
                          << ", " << nTaskIsland << " are being scheduled"
                          << std::endl;

                iPowWithNewTask = (newUtilizationIsland + utilization_t) *
                                  c->getPower(newFreq);
                iOldPow = oldUtilizationIsland * c->getPower(frequency);

                // todo remove after debug
                //

                printf("\t\t\tnew = [(util_isl_newFreq) %f + (util_new_task) "
                       "%f] * (pow_newFreq) %.17g=%f, old: (util_isl_curFreq) "
                       "%f * (pow_curFreq) %.17g=%f\n",
                       newUtilizationIsland, utilization_t,
                       c->getPower(newFreq), iPowWithNewTask,
                       oldUtilizationIsland, c->getPower(frequency), iOldPow);

                iDeltaPow = iPowWithNewTask - iOldPow;
                assert(iPowWithNewTask >= 0.0);
                assert(iOldPow >= 0.0);
                std::cout << "\t\t\tiDeltaPow = new-old = " << iDeltaPow
                          << std::endl;
                struct ConsumptionTable row = {.cons = iDeltaPow,
                                               .cpu = c,
                                               .opp = ooo};
                iDeltaPows.push_back(row);

                // break; (i.e. skip foreach OPP) xk è ovvio che aumentando la
                // freq della stessa CPU, t è ammissibile
            } else {
                std::cout
                    << "\t\t\tHere task wouldn't be admissible (U + U_newTask "
                       "> 1)"
                    << std::endl;
            }
        }

        c->setOPP(startingOPP);
        c->setWorkload(startingWL);
    } // end of tryTaskOnCPU_BL()

} // namespace RTSim
