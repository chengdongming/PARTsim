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
#include <sstream>

#include <rtsim/kernel.hpp>
#include <rtsim/scheduler/fpsched.hpp>

namespace RTSim {

    void FPScheduler::addTask(AbsRTTask *task, int prio) {
        enqueueModel(new FPModel(task, prio));
    }

    FPScheduler *FPScheduler::createInstance(vector<string> &par) {
        // todo: check the parameters (i.e. to set the default
        // time quantum)
        return new FPScheduler;
    }

    void FPScheduler::addTask(AbsRTTask *task, const std::string &p) {
        DBGENTER(_FP_SCHED_DBG_LEV);
        int prio;
        std::stringstream str(p);
        str >> prio;
        addTask(task, prio);
    }

} // namespace RTSim
