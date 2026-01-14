// This file contains notes and other kinds of code
// re-arranged to understand what's going on

#include <rtsim/energyMRTKernel.hpp>

using namespace RTSim;

// Code:

/// Tries dispatching a task on a CPU belonging to a BL pair of islands
/// @param t the task to dispatch
/// @param c the CPU on which the task should be dispatched
/// @param iDeltaPows vector of results containing [...]
void EnergyMRTKernel::tryTaskOnCPU_BL(
    AbsRTTask *t, CPU_BL *c vector<struct ConsumptionTable> &iDeltaPows) {
    auto task_wl = Utils::getTaskWorkload(t);

    // Do not dispatch tasks to disabled CPUs
    if (c->disabled())
        return;

    // Save current system configuration
    int startingOPP = c->getOPP();
    double frequency = c->getFrequency();
    string startingWL = c->getWorkload();
    c->setWorkload(Utils::getTaskWorkload(t));

    // Trying to schedule on CPU c using its current frequency

    // NOTE: (a copy of) the full queue of tasks in the ready queue of the given
    // CPU can be obtained with: getReadyTasks(c)

    // Iterate all OPPs starting from the *current one and above* (I think it
    // assumes that if an OPP is selected then no lower OPP can satisfy the
    // already dispatched tasks)

    for (auto tryOPP : c->getHigherOPPs()) {
        int opp_index = c->getIsland()->getOPPIndexByOPP(tryOPP);
        double newFrequency = c->getIsland()->getFrequency(opp_index);
        double newCapacity = 0.0;

        c->setOPP(opp_index);
        newCapacity = c->getSpeed(newFrequency, task_wl);

        // Check if task is admissible using this admission policy
    }

    for (struct OPP tryOPP : c->getHigherOPPs()) {
        int ooo = c->getIsland()->getOPPindex(tryOPP);
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
                0.0; // utilization of tasks in the island with new freq - cores
                     // share frequency
            double oldUtilizationIsland = 0.0;
            double iPowWithNewTask = 0.0;
            double iOldPow = 0.0;
            double iDeltaPow = 0.0; // additional power to schedule t on CPU c
                                    // on the whole island (big/little)
            int nTaskIsland = 0;
            IslandType island;

            // utilization on CPU c with the new frequency
            utilization = getUtilization(c, newCapacity);

            if (utilization > 1.0) {
                std::cout
                    << "\t\t\tCPU utilization is already >= 100% => skip OPP"
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
                std::cout
                    << "\t\t\tTotal utilization + cur/new task utilization "
                       "would be "
                    << utilization << "+" << utilization_t << "="
                    << utilization + utilization_t << " >= 100% => skip OPP"
                    << std::endl;
                continue;
            }
            // std::cout << "Final core utilization running+ready+active+new
            // task = "
            // << utilization + utilization_t << std::endl;

            // Ok, task can be placed on CPU c, compute power delta

            // utilization island where CPU c is
            island = c->getIslandType();
            newUtilizationIsland =
                getIslandUtilization(newCapacity, island, NULL);
            oldUtilizationIsland = getIslandUtilization(c->getSpeed(frequency),
                                                        island, &nTaskIsland);
            std::cout << "\t\t\tIn the CPU island of " << c->getName() << ", "
                      << nTaskIsland << " are being scheduled" << std::endl;

            iPowWithNewTask = (newUtilizationIsland + utilization_t) *
                              c->getPowerConsumption(newFreq);
            iOldPow = oldUtilizationIsland * c->getPowerConsumption(frequency);

            // todo remove after debug

            printf("\t\t\tnew = [(util_isl_newFreq) %f + (util_new_task) %f] * "
                   "(pow_newFreq) %.17g=%f, old: (util_isl_curFreq) %f * "
                   "(pow_curFreq) %.17g=%f\n",
                   newUtilizationIsland, utilization_t,
                   c->getPowerConsumption(newFreq), iPowWithNewTask,
                   oldUtilizationIsland, c->getPowerConsumption(frequency),
                   iOldPow);

            iDeltaPow = iPowWithNewTask - iOldPow;
            assert(iPowWithNewTask >= 0.0);
            assert(iOldPow >= 0.0);
            std::cout << "\t\t\tiDeltaPow = new-old = " << iDeltaPow
                      << std::endl;
            struct ConsumptionTable row = {.cons = iDeltaPow,
                                           .cpu = c,
                                           .opp = ooo};
            iDeltaPows.push_back(row);

            // break; (i.e. skip foreach OPP) xk è ovvio che aumentando la freq
            // della stessa CPU, t è ammissibile
        } else {
            std::cout
                << "\t\t\tHere task wouldn't be admissible (U + U_newTask > 1)"
                << std::endl;
        }
    }

    c->setOPP(startingOPP);
    c->setWorkload(startingWL);
} // end of tryTaskOnCPU_BL()

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
    vector<CPU_BL *> cpus = getProcessors();
    do {
        AbsRTTask *t = dynamic_cast<AbsRTTask *>(_sched->getTaskN(i++));
        if (t == NULL)
            break;
        std::cout << "Actual time = [" << SIMUL.getTime() << "]" << std::endl;
        std::cout << "Dealing with task " << t->toString() << "." << std::endl;

        // for testing
        if (manageForcedDispatch(t) || manageDiscartedTask(t)) {
            num_newtasks--;
            continue;
        }

        if (_queues->isInAnyQueue(t)) {
            // dispatch() is called even before onEndMultiDispatch() finishes
            // and thus tasks seem not to be dispatching (i.e., assigned to a
            // processor)
            std::cout
                << "\tTask has already been dispatched, but dispatching is "
                   "not complete => skip (you\'ll still see desched&sched "
                   "evt, to trace tasks)"
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

/// This method is used to rescale all budgets of tasks assigned to the current
/// island whenever the island changes OPP
/// @param island the island that changed OPP
/// @param curropp the destination OPP after the change
void EnergyMRTKernel::onOppChanged(unsigned int curropp, Island_BL *island) {
    // This flag is necessary only for this method, if this
    // method is not used the tryingtaskonCPU_BL flag should
    // be removed

    // Skip this method if the kernel is trying to place a
    // task on a BL CPU

    // TODO: Can technically avoid "trying" to place tasks
    // on CPUs and tryggering OPPs just to check their
    // speed? Just use the lookup mechanism.
    if (isTryngTaskOnCPU_BL())
        return;

    // Update budgets of all servers that envelope periodic
    // tasks on the same island
    for (auto &elem : _envelopes) {
        // Unpack iterator content
        AbsRTTask *task_ptr = elem.first;
        CBServerCallingEMRTKernel *server_ptr = elem.second;
        auto task_wl = Utils::getTaskWorkload(task_ptr);

        // Get the CPU on which the server is assigned
        CPU_BL *c = getProcessor(task_ptr);

        // dispatch() method apparently dispatches tasks one
        // at a time and each task triggers an OPP change
        // (which calls this method). For this reason, some
        // tasks may not have a CPU yet when this method is
        // called (skip).
        if (c == nullptr)
            continue;

        // If the target cpu is on the same island that triggered the OPP change

        // NOTE: in a more generalized fashion, should check if the two islands
        // are the same, not only if they are the same type (that works only on
        // BL system)
        if (c->getIsland()->getIslandType() == island->getIslandType()) {
            // Get the predicted speed on the selected CPU when the triggering
            // OPP is selected and the workload associated to the task is
            // running
            auto speed_wl = c->getSpeedByOPP(curropp, task_wl);

            // Resize the WCET of the task using the predicted speed
            Tick taskWCET = Tick(ceil(task_ptr->getWCET(speed_wl)));

            // Update the budget of the server accordingly
            server_ptr->changeBudget(taskWCET);
        }
    }
}
