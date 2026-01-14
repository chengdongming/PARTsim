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
#ifndef __TEXTTRACE_HPP__
#define __TEXTTRACE_HPP__

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
#include <rtsim/instr.hpp>

namespace RTSim {

    using namespace MetaSim;

    using std::map;
    class TextTrace {
    protected:
        std::ofstream fd;

    public:
        TextTrace(const string &name);

        ~TextTrace();

        void probe(ArrEvt &e);

        void probe(EndEvt &e);

        void probe(SchedEvt &e);

        void probe(DeschedEvt &e);

        void probe(DeadEvt &e);

        void probe(KillEvt &e);

        void probe(EndInstrEvt &e);

        void attachToTask(AbsRTTask &t);

        template <class X>
        void probe(MetaSim::GEvent<X> &e) {
            fd << "[Time:" << SIMUL.getTime() << "]\t";
            fd << e.toString() << std::endl;;
        }
    };

    class VirtualTrace {
        map<string, int> *results;

    public:
        VirtualTrace(map<string, int> *r);

        ~VirtualTrace();

        void probe(EndEvt &e);

        void attachToTask(Task &t);
    };
} // namespace RTSim

#endif
