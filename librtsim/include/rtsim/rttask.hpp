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
#ifndef __RTTASK_HPP__
#define __RTTASK_HPP__

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>

#include <metasim/regvar.hpp>
#include <metasim/simul.hpp>

#include <rtsim/task.hpp>

namespace RTSim {

    using namespace MetaSim;

    using std::vector;

    /**
       Models a simple periodic task. It's a simpler interface to
       Task.
    */
    class PeriodicTask : public Task {
        Tick period;

    public:
        PeriodicTask(Tick iat);

        PeriodicTask(Tick iat, Tick rdl, Tick ph = 0,
                     const std::string &name = "", long qs = 100);

        inline Tick getPeriod() const override {
            return period;
        }

        /** Used to build tasks with the Factory.  The string
            must contain a set of comma separated values, in
            the same order as in the constructor:

            - period, deadline, phase

            Then a set of optional paramters can be given: -
            name (a string), queuesize, abort (true/false)

            Please take into account that at least 3 arguments of
            numerical type must be given!

            Example:
            - PeriodiTask *p = PeriodicTask::createInstance("10, 10, 0, task1");

            is the same as

            - PeriodicTask *p = new PeriodicTask(10, 10, 0, "task1");

        */
        static std::unique_ptr<PeriodicTask>
            createInstance(const vector<string> &par);

        /**
           Object to string. you should override this function in derived
           classes
         */
        string toString() const override;

        /// Returns deadline. Assumption is DL = period
        Tick getRelDline() const override {
            return getPeriod();
        }

        double getMaxExecutionCycles() const override {
            return getWCET(1.0);
        }
    };

    /**
       Models a simple non periodic task. Once the task finishes, it dies.
       It's a simpler interface to Task.
    */
    class NonPeriodicTask : public PeriodicTask {
        // period kept only for computing utilization
    public:
        NonPeriodicTask(Tick iat) : PeriodicTask(iat){};

        NonPeriodicTask(Tick iat, Tick rdl, Tick ph = 0,
                        const std::string &name = "", long qs = 100) :
            PeriodicTask(iat, rdl, ph, name, qs){};

        void onEndInstance(Event *e) override {
            Task::onEndInstance(e);

            std::cout << __func__ << "(): non-periodic task => endrun for "
                      << toString() << std::endl;
            endRun();
        }

        void onKill(Event *e) override {
            Task::onKill(e);

            std::cout << __func__ << "(): non-periodic task => endrun for "
                      << toString() << std::endl;
            endRun();
        }

        string toString() const override;
    };

} // namespace RTSim

#endif
