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
#include <deque>
#include <iostream>
#include <sstream>

#include <metasim/entity.hpp>
#include <metasim/simul.hpp>
namespace MetaSim {

    class MsgEvt : public Event {
        string _msg;

    public:
        MsgEvt(const string &msg,
               int p = MetaSim::Event::_DEFAULT_PRIORITY + 10) :
            Event("GenericMessage", p),
            _msg(msg) {}
        void doit() override {
            DBGPRINT(_msg);
            std::cout << _msg << std::endl;
        }
    };

    Simulation *Simulation::instance_ = 0;

    class NoMoreEventsInQueue {};

    Simulation::Simulation() :
        dbg(),
        numRuns(0),
        actRuns(0),
        globTime(0),
        end(false) {}

    Simulation &Simulation::getInstance() {
        if (instance_ == 0)
            instance_ = new Simulation();

        return *instance_;
    }

    const Tick Simulation::getTime() {
        return globTime;
    }

    void Simulation::setTime(Tick t) {
        globTime = t;
    }

    // this function performs one single simulation step
    // It returns the tick after the simulation step has been completed
    const Tick Simulation::sim_step() {
        Event *temp;
        Tick mytime;

        DBGENTER(_SIMUL_DBG_LEV);

        temp = Event::getFirst(); // takes the first event in the queue ...
        if (temp == NULL)
            throw NoMoreEventsInQueue();
        temp->drop(); // ... and extract it!

        mytime = temp->getTime(); // stores the current time

        DBGPRINT("Executing event action at time [", mytime, "]: ");
#ifndef NDEBUG
        temp->print();
        print();
#endif

        setTime(mytime);

        temp->action(); // do what it is supposed to do...
        if (temp->isDisposable()) // if it has to be deleted...
            delete temp; // delete it!

        return mytime;
    }

    // this event returns the time of the first event in the queue
    // (i.e. the next event to be processed) or throws and exception
    // if there is no more events in the queue
    const Tick Simulation::getNextEventTime() {
        Event *temp = Event::getFirst();
        if (temp == NULL)
            throw NoMoreEventsInQueue();
        else
            return Event::getFirst()->getTime();
    }

    // this function will run until a specified time,
    // without cleaning any variable.
    // it can be used for debugging reasons.
    // it returns the final tick
    // it stops before executing the first event after stop
    const Tick Simulation::run_to(const Tick &stop) {
        try {
            MsgEvt msgEvt("run_to() end time reached");
            msgEvt.post(stop);
            while (getNextEventTime() <= stop) {
                globTime = sim_step();
            }
        } catch (NoMoreEventsInQueue &e) {
            std::cerr << "No more events in queue: simulation time = "
                      << globTime << std::endl;
        }

        return globTime;
    }

    void Simulation::initRuns(int nRuns) {
        BaseStat::init(nRuns);
        globTime = 0;
        end = false;
    }

    void Simulation::initSingleRun() {
        globTime = 0;

        // Run Initialization:
        // Before each run, call the newRun() of every entity
        // and setup statistics
        Entity::callNewRun();

        BaseStat::newRun();
    }

    void Simulation::endSingleRun() {
        Entity::callEndRun();
        BaseStat::endRun();

        clearEventQueue();
    }

    // Main function:
    // This is the simulation engine
    void Simulation::run(Tick endTick, int nRuns) {
        DBGENTER(_SIMUL_DBG_LEV);
        bool initializeRuns = true;
        bool terminateSim = true;

        if (nRuns < -1) {
            std::cout << "Initialize stats" << std::endl;
            initializeRuns = true;
            terminateSim = false;
            numRuns = 1;
            nRuns = -nRuns;
        } else if (nRuns == -1) {
            std::cout << "Will not initialize stats" << std::endl;
            initializeRuns = false;
            terminateSim = false;
            numRuns = 1;
        } else if (nRuns == 0) {
            std::cout << "Last Sim in the batch" << std::endl;
            initializeRuns = false;
            terminateSim = true;
            numRuns = 1;
        } else if (nRuns == 1) {
            std::cout << "One single run" << std::endl;
            initializeRuns = true;
            terminateSim = true;
            numRuns = 1;
        } else
            numRuns = nRuns;

        if (numRuns == 2) {
            std::cout << "Warning: Simulation cannot be "
                         "initialized with 2 runs"
                      << std::endl;
            std::cout << "         Executing 3 runs!" << std::endl;
            numRuns = 3;
        }

        if (initializeRuns)
            initRuns(numRuns);

        // Ok, now starts the main cycle of the simulation.
        // remember that actRuns is the actual run number
        // while numRuns is the maximum number of runs.
        actRuns = 0;
        while (actRuns < numRuns) {
            std::cout << "\n Run #" << actRuns << std::endl;

            initSingleRun();

            // MAIN CYCLE!!
            try {
                while (globTime < endTick) {
                    globTime = sim_step();
                }
            } catch (NoMoreEventsInQueue &e) {
                std::cerr << "No more events in queue: simulation time ="
                          << globTime << std::endl;
            }

            endSingleRun();

            actRuns++; // next run....
        }
        end = true;
        if (terminateSim)
            endSim(); // the simulation is over!!
    }

    void Simulation::clearEventQueue() {
        Event *temp;
        while ((temp = Event::getFirst()) != NULL) {
            temp->drop();
            if (temp->isDisposable()) // if it has to be deleted...
                delete temp;
        }
        globTime = 0;
    }

    // only for debug
    void Simulation::print() {
        DBGPRINT("Actual time = [", globTime, "]");
        DBGPRINT("---------- Begin Event Queue ----------");
        Event::printQueue();
        DBGPRINT("---------- End Event Queue ------------");
    }

    // wrappers for debug entry/exit
    void Simulation::dbgEnter(string lev, string header) {
        std::stringstream ss;

        ss << "t = [" << globTime << "] --> " + header;
        // string h = "t = [" + string(globTime) + "] --> " + header;
        // dbg.enter(lev, h);
        dbg.enter(lev, ss.str());
    }

    void Simulation::dbgExit() {
        dbg.exit();
    }

    void Simulation::endSim() {
        // Collect statistics
        BaseStat::endSim();
    }
} // namespace MetaSim

extern "C" {
void libmetasim_is_present() {
    return;
}
}
