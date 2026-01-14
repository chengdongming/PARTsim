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
#ifndef __SERVER_HPP__
#define __SERVER_HPP__

#include <metasim/baseexc.hpp>
#include <metasim/entity.hpp>
#include <metasim/gevent.hpp>
#include <metasim/simul.hpp>

#include <rtsim/abskernel.hpp>
#include <rtsim/abstask.hpp>
#include <rtsim/resource/resmanager.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>

#include <rtsim/matching_it.hpp>

#define _SERVER_DBG_LEV "server"

namespace RTSim {

    using namespace MetaSim;

    class CPU;

    /// Enumeration of possible server states
    typedef enum { IDLE, READY, EXECUTING, RELEASING, RECHARGING } ServerStatus;

    class ServerExc : public BaseExc {
    public:
        ServerExc(const string &m, const string &cl) :
            BaseExc(m, cl, "Server"){};
    };

    /**
        @ingroup server

        This class implement a generic aperiodic server. Examples are:
        - for dynamic priority:
        * the CBS (COnstant Bandwidth Server),
        * the GRUB (Greedy Reclaimation of Unused Bandwidth),
        * its Power-Aware version (GRUB-PA),
        * the SHRUB algorithm (Shared Reclamation of Unused
        Bandwidth),
        * the CASH algorithm (...), etc.
        - for fixed priority:
        * the Sporadic Server
        * the Polling Server
        * the Deferrable Server

        The server implements two interfaces: AbsRTTask and
        AbsKernel. The Kernel sees the Server as a regular
        RTTask. Therefore, there is no need to change the
        scheduler. The task sees the server as a Kernel, so there
        is no need to change the task implementation adding other
        parameters.  This class is abstract, so it cannot be
        instantiated. See one of the derived classes for more
        information.

        @todo Implement other algorithms, like Sporadic Server
        (fixed priority and Dynamic priority versions), Deferrable
        Server, etc. Need to implement TBS as well.

        @todo simplify the interface (now it is too fat)
    */
    class Server : virtual public AbsRTTask,
                   virtual public AbsKernel,
                   public Entity {
    protected:
        static string status_string[];

        Tick arr;
        Tick last_arr;
        ServerStatus status;
        Tick dline; // relative deadline
        Tick abs_dline; // absolute deadline

        std::vector<AbsRTTask *> tasks;

        Tick last_exec_time;

        AbsKernel *kernel;
        ResManager *globResManager;

        GEvent<Server> _bandExEvt;
        GEvent<Server> _dlineMissEvt;
        GEvent<Server> _rechargingEvt;

        GEvent<Server> _schedEvt;
        GEvent<Server> _deschedEvt;
        GEvent<Server> _dispatchEvt;

        std::unique_ptr<Scheduler> sched_;

        Scheduler *getScheduler() const override {
            // Never used for the servers (so far), bur necessary to build it
            return sched_.get();
        }

        AbsRTTask *currExe_;

        /** Sets the current relative deadline (if any)*/
        inline void setDeadline(Tick d) {
            dline = d;
        }

        inline void setAbsDead(Tick d) {
            abs_dline = d;
        }

        /// from idle to active contending (new work to do)
        virtual void idle_ready() = 0;

        /// from active non contending to active contending (more work)
        virtual void releasing_ready() = 0;

        /// from active contending to executing (dispatching)
        virtual void ready_executing() = 0;

        /// from executing to active contenting (preemption)
        virtual void executing_ready() = 0;

        /// from executing to active non contending (no more work)
        virtual void executing_releasing() = 0;

        /// from active non contending to idle (no lag)
        virtual void releasing_idle() = 0;

        /// from executing to recharging (budget exhausted)
        virtual void executing_recharging() = 0;

        /// from recharging to active contending (budget recharged)
        virtual void recharging_ready() = 0;

        /// from recharging to idle (nothing remains to be done)
        virtual void recharging_idle() = 0;

    public:
        /**
            The constructor.
            @param name server name
            @param s scheduler (for hierarchical scheduling servers)
        */
        Server(const string &name, const string &s = "fifo");

        /**
           Destructor
        */
        virtual ~Server();

        /**
           Get budget
        */
        virtual Tick getBudget() const = 0;

        /**
           Returns the period
        */
        Tick getPeriod() const override = 0;

        /**
           Dynamically changes the budget.

           @param new_budget  the new budget. Can be higher or lower than the
           current one

           @return the function returns the time at which the new budget
           has been imposed. Usually, an increase in the budget is
           immediately enforced, so it returns the current time. A
           decrease in the budget, instead, cannot be enforced
           immediately, so the function will return the time in the
           future when the new budget has been enforced.
        */
        virtual Tick changeBudget(const Tick &new_budget) = 0;

        /**
           Return the server virtual time.
        */
        virtual double getVirtualTime() = 0;

        /**
           Add a new task to this server, with parameters
           specified in params.

           @params task the task to be added
           @params the scheduling parameters

           @see Scheduler
        */
        virtual void addTask(AbsRTTask &task, const std::string &params = "");

        /// Returns all tasks currently in the associated scheduler
        Scheduler::TheTaskList getAllTasks() const;

        /// @return all tasks active in the server
        ///
        /// @todo Agostino said that sched_ may be returning some tasks that are
        /// not active due to problems with std::vector::erase. Check.
        using TaskList = MatchingIt<Scheduler::TaskIt, Scheduler::TaskIt,
                                    bool (*)(const AbsRTTask *)>;
        TaskList getTasks() const;

        /**
             Inherited from AbsRTTask. This function is called
             when the server is selected to execute.
        */
        void schedule() override;

        /**
             Inherited from AbsRTTask. This function is called
             when the server is selected to
             execute. Implemented here.
        */
        void deschedule() override;

        /**
            Inherited from AbsRTTask. Cannot be called here
            (throws an exception).
        */
        void activate() override { // throw(ServerExc) override {
            throw ServerExc("cannot call activate() on a server",
                            "Server::activate()");
        }

        /**
            Inherited from AbsRTTask. Returns the arrival time
            of the current istance
        */
        Tick getArrival() const override;

        /**
            Inherited from AbsRTTask. Returns the arrival time
            of the previous istance
        */
        Tick getLastArrival() const override;

        /**
            Inherited from AbsRTTask. Set the kernel for this
            server.
        */
        void setKernel(AbsKernel *k) override;

        /**
            Inherited from AbsRTTask. Returns the kernel for this
            server
        */
        AbsKernel *getKernel() override {
            return kernel;
        }

        /*------------------------------------------------------------------*/

        /**
            this is used to deal with resource management at the
            global level (not inside the server, but outside!)

            @todo think about interaction between local and global resman!
        */
        void setGlobalResManager(ResManager *rm);

        /** Inherited from AbsRTTask. Returns the current
            absolute deadline */
        Tick getDeadline() const override;

        /** Inherited from AbsRTTask. Returns the current
            relative deadline (if any)*/
        Tick getRelDline() const override;

        /** Inherited from AbsKernel. Activates a task in the
            server */
        void activate(AbsRTTask *) override;

        /** Inherited from AbsKernel. Suspend a task in the
            server */
        void suspend(AbsRTTask *) override;

        /**
            Inherited from AbsKernel. Dispatch a task in the
            server
        */
        void dispatch() override;

        /**
            Inherited from AbsKernel. Calls the corresponding
            function of RTKernel*/
        CPU *getProcessor(const AbsRTTask *) const override;

        /**
            Inherited from AbsKernel. Calls the corresponding
            function of RTKernel*/
        CPU *getOldProcessor(const AbsRTTask *) const override;

        /**
            Inherited from AbsKernel.

            This function is invoked every time an arrival
            event is triggered for this object. In periodic
            servers, arrivals are periodic (e.g. in Polling
            Server), in other servers they can be aperiodic
            (e.g. CBS or Grub).
        */
        void onArrival(AbsRTTask *t) override;

        /**
            Inherited from AbsKernel.

            This function is invoked every time the server has
            completed its current backlog.
        */
        void onEnd(AbsRTTask *t) override;

        virtual void onDispatch(Event *);

        /**
            This function is invoked every time the server
            capacity is exhausted
        */
        virtual void onBudgetExhausted(Event *);

        virtual void onSched(Event *);

        virtual void onDesched(Event *);

        /**
            This function is invoked if the server deadline is
            missed.  Should never happen! This will throw an
            exception.
        */
        void onDlineMiss(Event *);

        /**
            This function is invoked every time the server
            capacity has to be refilled.
        */
        virtual void onRecharging(Event *);

        /**
           This function returns the current server status.
        */
        inline ServerStatus getStatus() const {
            return status;
        }

        /** print the server status (only for debugging) */
        inline std::string getStatusString() const {
            return status_string[status];
        }

        void newRun() override;
        void endRun() override;

        /**
            Function inherited from AbsKernel. It should
            return the current speed of the CPU. For the
            server class, it currently returns 1.
        */
        double getSpeed() const override {
            return 1;
        }

        /**
            Function inherited from AbsKernel. It sets the
            speed of the CPU accordingly to the new system
            load, and returns the new speed. For this class,
            it does nothing and returns 0.
        */
        // virtual double setSpeed(double) { return 0; }

        /**
            Function inherited from AbsRTTask. It refreshes the
            state of the executing task when a change of the
            CPU speed occurs.  In this class, this function
            does nothing.

            @todo check that this is actually what we expect.
        */
        void refreshExec(double, double) override {}

        bool isActive() const override {
            return (status != IDLE);
        }

        bool isExecuting() const override {
            return (status == EXECUTING);
        }

        int getTaskNumber() const override {
            return getID();
        }

        bool isContextSwitching() const override {
            return false;
        }

        AbsRTTask *getCurrentTask() const {
            return currExe_;
        }

        template <class Trace>
        void setTrace(Trace &tr) {
            attach_stat(tr, _bandExEvt);
            attach_stat(tr, _dlineMissEvt);
            attach_stat(tr, _rechargingEvt);
            attach_stat(tr, _schedEvt);
            attach_stat(tr, _deschedEvt);
            attach_stat(tr, _dispatchEvt);
        }
    };
} // namespace RTSim

#endif
