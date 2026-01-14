// #pragma once

#ifndef RTLIB_CBSERVER_HPP
#define RTLIB_CBSERVER_HPP

// std
#include <list>
#include <sstream>

// MetaSim

// RTSim
#include <rtsim/rttask.hpp>
#include <rtsim/server.hpp>

// TODO: what is this?
#include <rtsim/capacitytimer.hpp>

namespace RTSim {
    using namespace MetaSim;

    class CBServer : public Server {
        // =================================================
        // Enums and subtypes
        // =================================================
    public:
        /// @sa idle_policy
        enum policy_t {
            ORIGINAL = 0,
            REUSE_DLINE,
        };

        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        /// @param q budget
        /// @param p period
        /// @param d relative deadline (more accurate=first absolute deadline)
        /// @param HR *don't know*
        /// @param name the name of this server
        /// @param sched the name of the scheduler used
        CBServer(Tick q, Tick p, Tick d, bool HR, const std::string &name,
                 const std::string &sched = "fifo");

        DEFAULT_VIRTUAL_DES(CBServer);

        // =================================================
        // Operations
        // =================================================
    public:
        /// Resets the server for a new run. Replenishes the
        /// capacity, puts it back to IDLE and resets vtime
        /// accounting.
        void newRun() override;

        /// Does nothing, if more than one run is performed the newRun takes
        /// everything into account.
        void endRun() override;

        /// @return the total budget
        Tick getBudget() const override {
            return Q;
        }

        /// @return the period
        Tick getPeriod() const override {
            return P;
        }

        /// Updates the server budget to the value n.
        ///
        /// Updates capacity accounting accordingly and manages capacity expire
        /// and virtual time timers.
        ///
        /// @param n the new capacity (if 0 or less, nothing happens and returns
        /// 0)
        ///
        /// @return the time at which the new budget has been imposed: either
        /// the current simulation time or never (per implementation of this
        /// specific server). Budgets will never be updated in the future by
        /// this server.
        Tick changeBudget(const Tick &n) override;

        /// @return the server accounted virtual time when running and the
        /// system time when IDLE
        double getVirtualTime() override;

        // @note used only in testing
        // @return the remaining budget for this server (in number of Ticks)
        // Tick get_remaining_budget() const {
        //     double dist = (double(getDeadline()) - vtime.get_value()) *
        //                       double(Q) / double(P) +
        //                   0.00000000001;

        //     return Tick::floor(dist);
        // }

        /// @return the server policy when it goes IDLE
        policy_t get_policy() const {
            return idle_policy;
        }

        void set_policy(policy_t p) {
            idle_policy = p;
        }

        // --------------> BEGIN FUNCTIONS ADDED BY AGOSTINO <-------------- //

        /// @todo should we check something here? Since we change the yielding
        /// status
        void addTask(AbsRTTask &task, const std::string &params = "") override;

        /// @return true if the server does not hold any task (or all
        /// non-periodic tasks are in the past)
        bool isEmpty() const;

        // @return true if the server was killed
        //
        // @todo this value is never checked anywhere
        //
        bool isKilled() const {
            return _killed;
        }

        /// @return true if t is in this server
        ///
        /// @note this uses a different mechanism than getTasks
        bool isInServer(AbsRTTask *t);

        /// @return true if the server is yielding the CPU
        bool isYielding() const {
            return _yielding;
        }

        /// Used to start yielding the CPU
        ///
        /// @todo that's it? should it do anything else?
        void yield() {
            _yielding = true;
        }

        /// Server to human-readable string
        string toString() const override;

        // ---------------> END FUNCTIONS ADDED BY AGOSTINO <--------------- //

    protected:
        /// Transitions from idle to active contending (new work to do)
        void idle_ready() override;

        /// Transitions from active non contending to active contending (more
        /// work)
        void releasing_ready() override;

        /// Transitions from active contending to executing (dispatching)
        void ready_executing() override;

        /// Transitions from executing to active contenting (preemption)
        void executing_ready() override;

        /// Transitions from executing to active non contending (no more work)
        void executing_releasing() override;

        /// Transitions from active non contending to idle (no lag)
        void releasing_idle() override;

        /// Transitions from executing to recharging (budget exhausted)
        void executing_recharging() override;

        /// Transitions from recharging to active contending (budget recharged
        /// and there is some work ready to do in the queue)
        void recharging_ready() override;

        /// Transitions from recharging to active contending (budget recharged
        /// but nobody is ready to run in the queue)
        ///
        /// @note this should never happen, if it does it is an erroneous
        /// condition
        void recharging_idle() override;

        /// Invoked by the replenishment event to recharge the server budget
        /// @todo add to parent class?
        virtual void onReplenishment(Event *e);

        /// Invoked by the finishing event of a task inside this server
        ///
        /// @sa releasing_idle
        ///
        /// @todo add to parent class?
        virtual void onIdle(Event *e);

        // Never used (implementation only in SporadicServer):
        // void prepare_replenishment(const Tick &t);

        // Never used (implementation only in SporadicServer):
        // void check_repl();

        // -------------------> Check from here onwards <------------------- //
    public:
        /// Kills the server and its task. It can stay killed since now on or
        /// only until task next period
        void killInstance(bool onlyOnce = true);

        /// @todo this function may return a task not belonging to the set of
        /// active tasks (returned by the getTasks)
        AbsRTTask *getFirstTask() const {
            return sched_->getFirst();
        }

        /// @todo update it in parent class too!
        double getWCET(double capacity) const override;

        // CPU *getProcessor(const AbsRTTask *t) const {
        //     auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        //     AbsRTTask *tt = const_cast<AbsRTTask *>(t);

        //     if (dynamic_cast<PeriodicTask *>(tt)) {
        //         assert(emrtk != nullptr);
        //         return emrtk->getProcessor(tt);
        //     } else
        //         return CBServer::getProcessor(tt);
        // }

        /// @todo why is this function returning the WHOLE WCET instead of the
        /// remaining WCET??
        double getRemainingWCET(double capacity) const override {
            return getWCET(capacity);
        }

        ResManager *getResManager() const {
            return kernel->getResManager();
        }

    protected:
        /// Arrival event of task of server
        void onArrival(AbsRTTask *t) override;

        // task of server ends
        void onEnd(AbsRTTask *t) override;

        /// On deschedule event (of server - and of tasks in it)
        void onDesched(Event *e) override;

        // =================================================
        // Data
        // =================================================
    private:
        /// Budget
        Tick Q;

        /// Period
        Tick P;

        /// Absolute deadline
        Tick d;

        /// Current capacity
        Tick cap;

        /// Stores the last time the server capacity has been updated
        Tick last_time;

        /// Changes the behavior of the server when the budget finishes during
        /// execution.
        ///
        /// When this value is set to true, the server waits for the next
        /// deadline to replenish the capacity, otherwise the capacity is
        /// replenished immediately and the server is put back in the ready
        /// queue (postponing its absolute deadline).
        int HR;

        /// A replenishment is a pair of <t,b>, meaning that at time t the
        /// budget should be replenished by b ticks.
        using repl_t = std::pair<Tick, Tick>;

        // Queue of replenishments, all times are in the future!
        //
        // @todo are they absolute or relative?
        //
        // @todo never used.

        // std::list<repl_t> repl_queue;

        // Accounts for all past replenishments. At the replenishment time, the
        // replenishment is moved from the repl_queue to the capacity_queue, so
        // all times are in the past.
        //
        // @todo they are all absolute times, right?

        // std::list<repl_t> capacity_queue;

    protected:
        /// A new event replenishment, different from the general
        /// "recharging" used in the Server class
        GEvent<CBServer> _replEvt;

        /// When the server becomes idle
        GEvent<CBServer> _idleEvt;

        /// Accounts the virtual time of the server
        CapacityTimer vtime;

        /// Controls the behavior when the server goes idle.
        ///
        /// If the server is in IDLE, and idle_policy == ORIGINAL, the original
        /// CBS policy is used (that computes a new deadline as t + P).
        ///
        /// If the server is IDLE and t < d and idle_policy == REUSE_DLINE, then
        /// reuses the old deadline, and computes a new "safe" budget as
        ///
        ///    floor((d - vtime) * Q / P)
        policy_t idle_policy;

        // Is CBS server killed?
        //
        bool _killed = false;

        /// True if CBS server has decided to yield core (= to leave it to
        /// ready tasks)
        bool _yielding = false;
    };

} // namespace RTSim

#endif // RTLIB_CBSERVER_HPP
