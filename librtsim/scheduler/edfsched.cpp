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

#include <iostream>

#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>

namespace RTSim {

    EDFModel::EDFModel(AbsRTTask *t) : TaskModel(t), extP(false) {}

    Tick EDFModel::getPriority() const {
        if (extP)
            return prio; // implicit conversion !!
        else
            return _rtTask->getDeadline();
    }

    void EDFModel::changePriority(Tick p) {
        if (p == _rtTask->getDeadline())
            extP = false;
        else {
            extP = true;
            prio = int(p);
        }
    }

    void EDFScheduler::addTask(AbsRTTask *task) { // throw(RTSchedExc) {
        enqueueModel(new EDFModel(task));
    }

    void EDFScheduler::addTask(AbsRTTask *task, const std::string &p) {
        // // XXX: why is this checking whether an AbsRTTask is indeed an AbsRTTask?
        // // I suppose there was once upon a time when addTask accepted AbsTasks, but then the error message is written in the other way around!
        // if (!dynamic_cast<AbsRTTask *>(task))
        //     throw RTSchedExc(
        //         "Cannot add a AbsRTTask to EDF (should be AbsTask instead");
        // // ignoring parameters
        // addTask(dynamic_cast<AbsRTTask *>(task));

        // Ignoring parameters, all info encoded in the task itself
        addTask(task);
    }

    void EDFScheduler::removeTask(AbsRTTask *task) {}

    EDFScheduler *EDFScheduler::createInstance(vector<string> &par) {
        // todo: check the parameters (i.e. to set the default
        // time quantum)
        return new EDFScheduler;
    }

    bool EDFScheduler::isAdmissible(CPU *c, vector<AbsRTTask *> tasks,
                                    AbsRTTask *toBeAdmitted) {
        double utilization = 0.0;
        double capacity = c->getSpeed();

        if (toBeAdmitted->getWCET(capacity) == 0.0)
            std::cout << "" << toBeAdmitted->getWCET(capacity);
        printf("\t\t\tEDFSched::isAdmissible WCET %.17g cap %f speed %f\n",
               toBeAdmitted->getWCET(capacity), capacity,
               double(c->getSpeed()));

        for (AbsRTTask *t : tasks) {
            utilization += t->getWCET(capacity) / double(t->getPeriod());
        }
        utilization +=
            toBeAdmitted->getWCET(capacity) / double(toBeAdmitted->getPeriod());

        return utilization <= 1.0;
    }

} // namespace RTSim
