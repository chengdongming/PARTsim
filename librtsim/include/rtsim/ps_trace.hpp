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
#ifndef __PSTRACE_HPP__
#define __PSTRACE_HPP__

#include <fstream>
#include <iosfwd>
#include <string>

#include <metasim/baseexc.hpp>
#include <metasim/basetype.hpp>
#include <metasim/event.hpp>
#include <metasim/particle.hpp>
#include <metasim/trace.hpp>

#include <rtsim/rttask.hpp>
#include <rtsim/taskevt.hpp>

namespace RTSim {
    class PSTrace {
    protected:
        std::ofstream fd;
        bool first_event;

        void writeTaskEvent(const Task &tt, const std::string &evt_name,
                            TaskEvt *evt);

    public:
        PSTrace(const std::string &name);
        ~PSTrace();

        void probe(ArrEvt &e);
        void probe(EndEvt &e);
        void probe(SchedEvt &e);
        void probe(DeschedEvt &e);
        void probe(DeadEvt &e);
        void attachToTask(Task &t);
    };
} // namespace RTSim

#endif
