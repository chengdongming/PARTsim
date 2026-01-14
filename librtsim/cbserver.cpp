#include <rtsim/cbserver.hpp>
// #include <rtsim/energyMRTKernel.hpp>
namespace RTSim {

    CBServer::CBServer(Tick q, Tick p, Tick d, bool HR, const std::string &name,
                       const std::string &sched) :
        Server(name, sched),
        Q(q),
        P(p),
        d(d),
        cap(0),
        last_time(0),
        HR(HR),
        _replEvt("replenishing", this, &CBServer::onReplenishment,
                 Event::_DEFAULT_PRIORITY - 1),
        // For idle, standard version of RTSim uses _DEFAULT_PRIORITY
        _idleEvt("going idle", this, &CBServer::onIdle, Event::_DEFAULT_PRIORITY - 1),
        vtime(),
        idle_policy(ORIGINAL) {
        DBGENTER(_SERVER_DBG_LEV);
        // DBGPRINT(s);
    }

    void CBServer::newRun() {
        DBGENTER(_SERVER_DBG_LEV);
        DBGPRINT("HR ", HR);

        cap = Q;
        last_time = 0;
        // recharging_time = 0;
        status = IDLE;
        // capacity_queue.clear();
        if (vtime.get_status() == CapacityTimer::RUNNING)
            vtime.stop();
        vtime.set_value(0);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::endRun() {}

    Tick CBServer::changeBudget(const Tick &n) {
        // NOTE: I removed all custom print to stdout because code was
        // getting cluttered
        DBGENTER(_SERVER_DBG_LEV);

        Tick cur_time = SIMUL.getTime();

        if (n <= 0)
            return 0;

        if (n == Q) {
            DBGPRINT("No Capacity change: n=Q=", Q);
            return cur_time;
        }

        // NOTE: if n < Q then cap is decremented
        Q = n;
        cap += n - Q;

        if (status == EXECUTING) {
            DBGPRINT("Server ", getName(), " is executing");

            // Capacity may have decreased. If 0, a recharging event is fired at
            // the current time. If less than 0, an exception is thrown when
            // postponing the bandwidth expire event.

            cap = cap - (cur_time - last_time);
            last_time = cur_time;

            if (cap == 0) {
                DBGPRINT("Server capacity is zero, go to recharging");
            } else {
                DBGPRINT("Reposting bandExEvt at ", cur_time + cap);
            }

            _bandExEvt.drop();
            DBGPRINT("_bandExEvt.post ", cur_time + cap);
            _bandExEvt.post(cur_time + cap);
            vtime.stop();
            vtime.start(double(P) / double(Q));
        }

        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
        return cur_time;
    }

    double CBServer::getVirtualTime() {
        DBGENTER(_SERVER_DBG_LEV);
        DBGPRINT("Status = ", status_string[status]);
        double vt;
        if (status == IDLE)
            vt = double(SIMUL.getTime());
        else
            vt = vtime.get_value();
        return vt;
    }

    // --------------> BEGIN FUNCTIONS ADDED BY AGOSTINO <-------------- //

    void CBServer::addTask(AbsRTTask &task, const std::string &params) {
        Server::addTask(task, params);
        _yielding = false;
    }

    bool CBServer::isEmpty() const {
        return getTasks().size() == 0;
    }

    bool CBServer::isInServer(AbsRTTask *t) {
        // Servers are not hierarchical
        if (dynamic_cast<Server *>(t))
            return false;

        bool res = sched_->isFound(t);
        return res;
    }

    std::string CBServer::toString() const {
        std::stringstream s;
        s << "\ttasks: [ " << sched_->toString() << "]"
          << (isYielding() ? " yielding" : "") << " (Q:" << getBudget()
          << ", P:" << getPeriod() << ")\tstatus: " << getStatusString();
        return s.str();
    }

    // ---------------> END FUNCTIONS ADDED BY AGOSTINO <--------------- //

    // TODO: GA: study functions in this file below this comment

    void CBServer::idle_ready() {
        DBGENTER(_SERVER_DBG_LEV);
        assert(status == IDLE);
        status = READY;

        cap = 0;

        if (idle_policy == REUSE_DLINE && SIMUL.getTime() < getDeadline()) {
            double diff =
                double(getDeadline() - SIMUL.getTime()) * double(Q) / double(P);
            cap = Tick(std::floor(diff));
        }

        if (cap == 0) {
            cap = Q;
            // Postpone absolute deadline
            d = SIMUL.getTime() + P;
            DBGPRINT("new deadline ", d);
            setAbsDead(d);
        }

        vtime.set_value(SIMUL.getTime());
        DBGPRINT("Going to active contending ", SIMUL.getTime());
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), ": Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    // We should compare the actual bandwidth with the assigned Q of this
    // server, postpone the deadline and fully recharge or just use what is
    // left. However, this is not necessary, in fact, the releasing state should
    // be the state in which:
    //  1. the server is not executing.
    //  2. the if (condition) is false.
    //
    // In other words, it should be possible to avoid the if.
    void CBServer::releasing_ready() {
        DBGENTER(_SERVER_DBG_LEV);

        status = READY;
        _idleEvt.drop();

        DBGPRINT("FROM NON CONTENDING TO CONTENDING");
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), ": Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::ready_executing() {
        DBGENTER(_SERVER_DBG_LEV);

        status = EXECUTING;
        last_time = SIMUL.getTime();
        vtime.start(double(P) / double(Q));

        DBGPRINT("Last time is: ", last_time);

        DBGPRINT("_bandExEvt.post ", last_time + cap);
        _bandExEvt.post(last_time + cap);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::executing_ready() {
        DBGENTER(_SERVER_DBG_LEV);

        status = READY;
        cap = cap - (SIMUL.getTime() - last_time);
        vtime.stop();
        _bandExEvt.drop();
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::executing_releasing() {
        DBGENTER(_SERVER_DBG_LEV);

        if (status == EXECUTING) {
            cap = cap - (SIMUL.getTime() - last_time);
            vtime.stop();
            _bandExEvt.drop();
        }

        // XXX: likely this is the culprit for the bug?
        if (vtime.get_value() <= double(SIMUL.getTime())) {
            status = IDLE;
            DBGPRINT("CARRAMBAAAAAAAAAAAAAAAA!");
        } else {
            DBGPRINT("_idleEvt.post ", vtime.get_value());
            _idleEvt.post(Tick(vtime.get_value()));
            status = RELEASING;
        }

        DBGPRINT("Status is now ", status_string[status]);

        // // FIXME: If compiling against EnergyMRTKernel uncomment
        // // The EMRTKernel saves the active utilization on
        // // release in the onExecutingReleasing method.
        // auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        // if (emrtk != nullptr)
        //     emrtk->onExecutingReleasing(this);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::releasing_idle() {
        DBGENTER(_SERVER_DBG_LEV);
        status = IDLE;

        // // FIXME: If compiling against EnergyMRTKernel uncomment
        // // TODO: why does this call this?
        // auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        // if (emrtk != nullptr)
        //     emrtk->onReleasingIdle(this);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::executing_recharging() {
        // Server queue may be empty or not.

        DBGENTER(_SERVER_DBG_LEV);

        _bandExEvt.drop();
        vtime.stop();

        DBGPRINT("Capacity before: ", cap);
        DBGPRINT("Time is: ", SIMUL.getTime());
        DBGPRINT("Last time is: ", last_time);
        DBGPRINT("HR: ", HR);

        if (!HR) {
            // Postpone the absolute deadline and instantly replenish the budget
            cap = Q;
            d = d + P;
            setAbsDead(d);

            DBGPRINT("Capacity is now: ", cap);
            // DBGPRINT("Capacity queue: ", capacity_queue.size());
            DBGPRINT("new_deadline: ", d);

            status = READY;
            DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
            DBGPRINT("_replEvt.post1 ", SIMUL.getTime());
            _replEvt.post(SIMUL.getTime());
        } else {
            // Set the next replenishment event to the current absolute deadline
            // and then postpone the deadline
            cap = 0;
            DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
            DBGPRINT("_replEvt.post2 ", d);
            _replEvt.post(d);
            d = d + P;
            setAbsDead(d);
            status = RECHARGING;
        }

        // inserted by rodrigo seems we do not stop the capacity_timer
        // moved up
        // vtime.stop();

        DBGPRINT("The status is now ", status_string[status]);

        // // FIXME: If compiling against EnergyMRTKernel uncomment
        // auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        // if (emrtk != nullptr)
        //     emrtk->onExecutingRecharging(this);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::recharging_ready() {
        DBGENTER(_SERVER_DBG_LEV);
        status = READY;
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::recharging_idle() {
        assert(false);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::onReplenishment(Event *e) {
        DBGENTER(_SERVER_DBG_LEV);

        _replEvt.drop();

        DBGPRINT("Status before: ", status);
        DBGPRINT("Capacity before: ", cap);

        switch (status) {
        case RECHARGING:
        case RELEASING:
        case IDLE: {
            cap = Q; // repl_queue.front().second;
            if (sched_->getFirst() != nullptr) {
                // There is some work ready to go
                recharging_ready();
                kernel->onArrival(this);
            } else if (status != IDLE) {
                if (double(SIMUL.getTime()) < vtime.get_value()) {
                    status = RELEASING;
                    DBGPRINT("_idleEvt.post ", vtime.get_value());
                    _idleEvt.post(Tick::ceil(vtime.get_value()));
                } else {
                    status = IDLE;
                }

                currExe_ = nullptr;
                sched_->notify(nullptr);
            }
        } break;
        case READY:
        case EXECUTING: {
            // TODO: what should we do now?
            //       repl_queue.pop_front();
            // capacity_queue.push_back(r);
            // if (repl_queue.size() > 1) check_repl();
            // me falta reinsertar el servidor con la prioridad adecuada
        } break;
        default:
            assert(false);
        }

        DBGPRINT("Status is now: ", status_string[status]);
        DBGPRINT("Capacity is now: ", cap);

        // // FIXME: If compiling against EnergyMRTKernel uncomment
        // auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        // if (emrtk != nullptr)
        //     emrtk->onReplenishment(this);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::onIdle(Event *e) {
        DBGENTER(_SERVER_DBG_LEV);
        releasing_idle();
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    // TODO: check if this method is good and that no other
    // method will be ever invoked for this CBServer once
    // killed with this method
    void CBServer::killInstance(bool onlyOnce) {
        auto t = dynamic_cast<Task *>(sched_->getFirst());
        auto cpu = t->getCPU();
        assert(t != NULL);
        assert(cpu != NULL);

        executing_releasing();
        status = EXECUTING;
        yield();

        _bandExEvt.drop();
        _replEvt.drop();
        _rechargingEvt.drop();

        _killed = true;

        t->killInstance();
        t->killEvt.doit();

        // std::cout << "Kill event is dropped? " << t->killEvt.toString() <<
        // std::endl;

        status = RELEASING;
        _dispatchEvt.drop();
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    double CBServer::getWCET(double capacity) const {
        double wcet = 0.0;

        // TODO: this assumes that all absRTTasks properly implement the
        // getWCET(capacity) method (and do not simply return 0.0)
        for (auto *t : getTasks()) {
            wcet += double(t->getWCET(capacity));
        }

        return wcet;

        // double wcet = 0.0;
        // for (AbsRTTask *t : getTasks())
        //     wcet += double(dynamic_cast<Task *>(t)->getWCET());
        // wcet = wcet / capacity;
        // return wcet;
    }

    void CBServer::onArrival(AbsRTTask *t) {
        _yielding = false;
        Server::onArrival(t);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::onEnd(AbsRTTask *t) {
        Server::onEnd(t);

        if (isEmpty())
            yield();

        // // FIXME: If compiling against EnergyMRTKernel uncomment
        // auto emrtk = dynamic_cast<EnergyMRTKernel *>(kernel);
        // if (emrtk != nullptr)
        //     emrtk->onTaskInServerEnd(t, dynamic_cast<Task *>(t)->getCPU(),
        //                              this);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

    void CBServer::onDesched(Event *e) {
        if (isEmpty())
            yield();
        else // could happend with non-periodic tasks
            Server::onDesched(e);
        DBGPRINT("[t=", SIMUL.getTime(), "] CBServer ", getName(), " in ", __func__, "(): Q=", Q, ", P=", P, ", d=", d, ", cap=", cap, ", last_time=", last_time, ", HR=", HR, ", vtime=", vtime.get_value(), ", _killed=", _killed, ", _yielding=", _yielding, ", status=", status_string[status]);
    }

} // namespace RTSim
