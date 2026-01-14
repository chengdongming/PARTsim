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
#ifndef __TIMER_HPP__
#define __TIMER_HPP__

#include <metasim/gevent.hpp>
#include <metasim/simul.hpp>

#define _TIMER_DBG_LEV "Timer"

namespace RTSim {

    // using namespace MetaSim;

    class Timer : public MetaSim::Entity {
    public:
        MetaSim::GEvent<Timer> _triggerEvt;
        MetaSim::Tick lastTrigger; // Instant of the last trigger event
    public:
        Timer(const std::string &n = "", int p = 16);
        virtual void reArm() = 0;
        virtual void action() = 0;
        virtual void onTrigger(MetaSim::Event *);
        void newRun() override;
        void endRun() override;
    };

    class PeriodicTimer : public Timer {
        MetaSim::Tick _period;

    public:
        PeriodicTimer(MetaSim::Tick p, const std::string &n = "",
                      int prio = 16);
        void reArm() override;
        void action() override;
    };

} // namespace RTSim

#endif
