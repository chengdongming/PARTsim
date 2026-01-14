#ifndef __SPORADICSERVER_H__
#define __SPORADICSERVER_H__

#include <list>
#include <rtsim/capacitytimer.hpp>
#include <rtsim/server.hpp>

namespace RTSim {

    using namespace MetaSim;

    class SporadicServer : public Server {
    private:
        Tick Q, P;
        Tick cap;
        Tick last_time;
        Tick recharging_time;

        /// replenishment: it is a pair of <t,b>, meaning that
        /// at time t the budget should be replenished by b.
        typedef std::pair<Tick, Tick> repl_t;

        /// queue of replenishments
        /// all times are in the future!
        std::list<repl_t> repl_queue;

        /// at the replenishment time, the replenishment is moved
        /// from the repl_queue to the capacity_queue, so
        /// all times are in the past.
        std::list<repl_t> capacity_queue;

        /// A new event replenishment, different from the general
        /// "recharging" used in the Server class
        GEvent<SporadicServer> _replEvt;

        /// when the server becomes idle
        GEvent<SporadicServer> _idleEvt;

        CapacityTimer vtime;

    public:
        SporadicServer(Tick q, Tick p, const std::string &name,
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

        Tick changeQ(const Tick &n);
        double getVirtualTime() override;

        // TODO correct?
        double getWCET(double capacity) const override {
            return Q;
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

        void onReplenishment(Event *e);

        void onIdle(Event *e);

        void prepare_replenishment(const Tick &t);

        void check_repl();
    };
} // namespace RTSim

#endif
