/***************************************************************************
    begin                : Feb 20 2010
    copyright            : (C) 2010 by Giuseppe Lipari
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
#ifndef __POLLINGSERVER_H__
#define __POLLINGSERVER_H__

#include <rtsim/server.hpp>

namespace RTSim {

    class PollingServer : public Server {
    private:
        Tick Q, P;
        Tick cap;
        Tick last_time;
        Tick recharging_time;

    public:
        PollingServer(Tick q, Tick p, const std::string &name,
                      const std::string &sched = "fifo");

        void newRun() override;
        void endRun() override;

        Tick getBudget() const override {
            return Q;
        }
        Tick getPeriod() const override {
            return P;
        }

        Tick changeBudget(const Tick &n) override;

        /** @todo to be completed */
        double getVirtualTime() override {
            return 0;
        }

    protected:
        /// from idle to active contending (new work to do)
        void idle_ready() override;

        /// from active non contending to active contending (more work)
        void releasing_ready() override;

        /// from active contending to executing (dispatching)
        void ready_executing() override;

        /// from executing to active contenting (preemption)
        void executing_ready() override;

        /// from executing to active non contending (no more work)
        void executing_releasing() override;

        /// from active non contending to idle (no lag)
        void releasing_idle() override;

        /// from executing to recharging (budget exhausted)
        void executing_recharging() override;

        /// from recharging to active contending (budget recharged)
        void recharging_ready() override;

        /// from recharging to active contending (budget recharged)
        void recharging_idle() override;
    };
} // namespace RTSim

#endif
