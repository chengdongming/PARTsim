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
#include <metasim/factory.hpp>
#include <metasim/strtoken.hpp>

#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/fifosched.hpp>
#include <rtsim/scheduler/fpsched.hpp>
#include <rtsim/scheduler/rmsched.hpp>
#include <rtsim/scheduler/rrsched.hpp>
#include <rtsim/scheduler/truefifo.hpp>

namespace RTSim {
    static const std::string name_EDF = "edf";
    static const std::string name_FIFO = "fifo";
    static const std::string name_FP = "fp";
    static const std::string name_RM = "rm";
    static const std::string name_RR = "rr";
    static const std::string name_TrueFIFO = "truefifo";

// This magic macro relies on the fact that the scheduler classes follow a
// convention
#define registerInSchedFactory(schedclass)                                     \
    static registerInFactory<Scheduler, schedclass##Scheduler, std::string>    \
        register_##schedclass(name_##schedclass)

    /**
        This namespace should never be used by the user. Contains
        functions to initialize the abstract factory that builds
        the scheduler.
    */
    namespace __sched_stub {

        registerInSchedFactory(EDF);
        registerInSchedFactory(FIFO);
        registerInSchedFactory(FP);
        registerInSchedFactory(RM);
        registerInSchedFactory(RR);
        registerInSchedFactory(TrueFIFO);

    } // namespace __sched_stub
    void __regsched_init() {}
} // namespace RTSim
