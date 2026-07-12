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
#include <exception>
#include <iostream>
#include <sstream>
#include <stdexcept>

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
        end(false),
        runGeneration(0),
        lastRunOutcome() {}

    const char *simulationCompletionReasonName(
        SimulationCompletionReason reason) {
        switch (reason) {
        case SimulationCompletionReason::ReachedHorizon:
            return "reached_horizon";
        case SimulationCompletionReason::EventQueueExhausted:
            return "event_queue_exhausted";
        case SimulationCompletionReason::RuntimeError:
            return "runtime_error";
        case SimulationCompletionReason::Initializing:
            return "initializing";
        case SimulationCompletionReason::Running:
            return "running";
        case SimulationCompletionReason::Finalizing:
            return "finalizing";
        case SimulationCompletionReason::NotStarted:
        default:
            return "not_started";
        }
    }

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

        // Ownership transfers to the engine when a queued event is marked
        // disposable. Preserve that guarantee even when doit/probe throws.
        const bool disposable = temp->isDisposable();
        try {
            temp->action(); // do what it is supposed to do...
        } catch (...) {
            if (disposable)
                delete temp;
            throw;
        }
        if (disposable)
            delete temp;

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
        ++runGeneration;

        // Run Initialization:
        // Before each run, call the newRun() of every entity
        // and setup statistics
        try {
            Entity::callNewRun();
        } catch (...) {
            const std::exception_ptr initialization_error =
                std::current_exception();
            try {
                clearEventQueue();
            } catch (...) {
            }
            std::rethrow_exception(initialization_error);
        }

        try {
            BaseStat::newRun();
        } catch (...) {
            const std::exception_ptr initialization_error =
                std::current_exception();
            try {
                Entity::callEndRun();
            } catch (...) {
            }
            try {
                clearEventQueue();
            } catch (...) {
            }
            std::rethrow_exception(initialization_error);
        }
    }

    void Simulation::endSingleRun(bool commit_statistics) {
        const Tick finalization_time = globTime;
        std::exception_ptr first_error;
        try {
            Entity::callEndRun();
        } catch (...) {
            first_error = std::current_exception();
        }
        if (commit_statistics && !first_error) {
            try {
                BaseStat::endRun();
            } catch (...) {
                if (!first_error)
                    first_error = std::current_exception();
            }
        } else {
            try {
                BaseStat::cancelRun();
            } catch (...) {
                if (!first_error)
                    first_error = std::current_exception();
            }
        }
        try {
            clearEventQueue();
        } catch (...) {
            if (!first_error)
                first_error = std::current_exception();
        }
        if (first_error) {
            lastRunOutcome.actual_end_time = finalization_time;
            lastRunOutcome.reached_requested_horizon = false;
            lastRunOutcome.completed = false;
            lastRunOutcome.reason = SimulationCompletionReason::RuntimeError;
            std::rethrow_exception(first_error);
        }
    }

    // Main function:
    // This is the simulation engine
    void Simulation::run(Tick endTick, int nRuns) {
        DBGENTER(_SIMUL_DBG_LEV);
        lastRunOutcome.requested_end_time = endTick;
        lastRunOutcome.actual_end_time = globTime;
        lastRunOutcome.reached_requested_horizon = false;
        lastRunOutcome.completed = false;
        lastRunOutcome.reason = SimulationCompletionReason::NotStarted;
        if (endTick < Tick(0))
            throw std::invalid_argument(
                "simulation horizon must be non-negative");
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

            lastRunOutcome.requested_end_time = endTick;
            lastRunOutcome.actual_end_time = globTime;
            lastRunOutcome.reached_requested_horizon = false;
            lastRunOutcome.completed = false;
            lastRunOutcome.reason =
                SimulationCompletionReason::Initializing;

            bool initialized = false;
            bool finalization_attempted = false;
            Tick run_end_time = globTime;
            try {
                initSingleRun();
                initialized = true;
                lastRunOutcome.reason = SimulationCompletionReason::Running;

                SimulationCompletionReason completion_reason =
                    SimulationCompletionReason::ReachedHorizon;

                // MAIN CYCLE!! Do not execute an event beyond the requested
                // horizon. If the next event is later, advancing logical time
                // to the horizon is a complete run; an empty queue before the
                // horizon is an explicit infrastructure outcome.
                try {
                    while (globTime < endTick) {
                        if (getNextEventTime() > endTick) {
                            globTime = endTick;
                            break;
                        }
                        globTime = sim_step();
                    }
                } catch (NoMoreEventsInQueue &e) {
                    std::cerr << "No more events in queue: simulation time ="
                              << globTime << std::endl;
                    completion_reason =
                        SimulationCompletionReason::EventQueueExhausted;
                }

                run_end_time = globTime;
                lastRunOutcome.actual_end_time = run_end_time;
                lastRunOutcome.reason =
                    SimulationCompletionReason::Finalizing;
                finalization_attempted = true;
                endSingleRun();

                // Publish success only after every finalizer and queue cleanup
                // has completed successfully.
                lastRunOutcome.requested_end_time = endTick;
                lastRunOutcome.actual_end_time = run_end_time;
                lastRunOutcome.reached_requested_horizon =
                    completion_reason ==
                        SimulationCompletionReason::ReachedHorizon &&
                    run_end_time >= endTick;
                lastRunOutcome.completed = true;
                lastRunOutcome.reason = completion_reason;
            } catch (...) {
                const std::exception_ptr primary_error =
                    std::current_exception();
                const Tick failure_time =
                    lastRunOutcome.reason ==
                            SimulationCompletionReason::RuntimeError
                        ? lastRunOutcome.actual_end_time
                        : globTime;
                if (initialized && !finalization_attempted) {
                    try {
                        endSingleRun(false);
                    } catch (...) {
                        // The initialization/callback exception has priority.
                    }
                }
                lastRunOutcome.requested_end_time = endTick;
                lastRunOutcome.actual_end_time = failure_time;
                lastRunOutcome.reached_requested_horizon = false;
                lastRunOutcome.completed = false;
                lastRunOutcome.reason =
                    SimulationCompletionReason::RuntimeError;
                std::rethrow_exception(primary_error);
            }

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
