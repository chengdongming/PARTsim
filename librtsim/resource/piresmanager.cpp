/***************************************************************************
    begin                : Thu Apr 24 15:54:58 CEST 2003
    copyright            : (C) 2003 by Giuseppe Lipari
    email                : lipari@sssup.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
#include <metasim/simul.hpp>

#include <rtsim/abskernel.hpp>
#include <rtsim/resource/piresmanager.hpp>
#include <rtsim/resource/resource.hpp>

namespace RTSim {
    using namespace MetaSim;

    PIRManager::PIRManager(const string &n) : ResManager(n) {}

    void PIRManager::newRun() {
        _oldPriorities.clear();
        _blocked.clear();
    }

    void PIRManager::endRun() {}

    bool PIRManager::request(AbsRTTask *t, Resource *r, int nr) {
        DBGENTER(_PIRESMAN_DBG_LEV);

        bool ret;

        auto incomingScheduler = t->getKernel()->getScheduler();
        auto taskModel = incomingScheduler->find(t);
        if (taskModel == nullptr)
            throw BaseExc("Cannot find task model!");

        if (r->available() < nr)
            goto block;

        // There are enough resources, but can we use them?
        // Or should we enqueue? Let's check the top-waiting
        // process, if any

        // FIXME: greater or equal?
        if (_blocked[r].size() &&
            _blocked[r].front()->getPriority() > taskModel->getPriority())
            goto block;

        // All good, we have a higher priority than the highest priority blocked
        // task and there are enough resources to use.

        // Save current task original priority
        _oldPriorities[t][r] = taskModel->getPriority();
        _running[r].insert(taskModel);
        r->lock(t, nr);
        return true;

    block:
        DBGPRINT("Resource is locked");

        /// Extract the highest priority task for this resource
        auto highest = _running[r].front();
        auto runningScheduler = highest->getTask()->getKernel()->getScheduler();

        /// Suspend the current task
        t->getKernel()->suspend(t);

        DBGPRINT("Raising priority");

        // TODO: how do we compare priorities for different schedulers?
        // FIXME: check whether this should be inverted with a '!' on the
        // outside
        if (TaskModel::TaskModelCmp()(highest, taskModel)) {
            // Remove the running task from the scheduler and re-insert it into
            // the running queue with the new priority
            runningScheduler->extract(highest->getTask());
            highest->changePriority(taskModel->getPriority());
            highest->getTask()->getKernel()->activate(highest->getTask());
        }

        _blocked[r].insert(taskModel);
        return false;
    }

    void PIRManager::release(AbsRTTask *t, Resource *r, int n) {
        // TODO: change
        TaskModel *taskModel = _sched->find(t);

        DBGENTER(_PIRESMAN_DBG_LEV);

        r->unlock();
        // see if there is any blocked task
        if (!blocked[r->getName()].empty()) {
            TaskModel *newTaskModel = blocked[r->getName()].front();
            blocked[r->getName()].erase(newTaskModel);
            _kernel->suspend(t);
            taskModel->changePriority((oldPriorities[t])[r->getName()]);
            if (t->isActive())
                _kernel->activate(t);
            _kernel->activate(newTaskModel->getTask());
            r->lock(newTaskModel->getTask());
        }
    }

} // namespace RTSim
