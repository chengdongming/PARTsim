#include <cassert>
#include <metasim/memory.hpp>

#include <metasim/factory.hpp>
#include <metasim/strtoken.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/server.hpp>
#include <rtsim/rttask.hpp>

namespace RTSim {
    using namespace MetaSim;
    using namespace parse_util;

    string Server::status_string[] = {"IDLE", "READY", "EXECUTING", "RELEASING",
                                      "RECHARGING"};

    Server::Server(const string &name, const string &s) :
        Entity(name),
        arr(0),
        last_arr(0),
        status(IDLE),
        dline(0),
        abs_dline(0),
        tasks(),
        last_exec_time(0),
        kernel(0),
        _bandExEvt("ServerBudgetExhausted", this, &Server::onBudgetExhausted,
                   Event::_DEFAULT_PRIORITY + 4),
        _dlineMissEvt("ServerDeadlineMissed", this, &Server::onDlineMiss, Event::_DEFAULT_PRIORITY + 6),
        _rechargingEvt("ServerRecharging", this, &Server::onRecharging,
                       Event::_DEFAULT_PRIORITY - 1),
        _schedEvt("ServerScheduled", this, &Server::onSched),
        _deschedEvt("ServerDescheduled", this, &Server::onDesched),
        _dispatchEvt("ServerDispatching", this, &Server::onDispatch, Event::_DEFAULT_PRIORITY + 5),
        sched_(nullptr),
        currExe_(nullptr) {
        DBGENTER(_SERVER_DBG_LEV);
        string s_name = parse_util::get_token(s);
        // only for passing to the factory
        vector<string> p = parse_util::split_param(parse_util::get_param(s));
        // create the scheduler

        DBGPRINT("SCHEDULER: ", s_name);
        DBGPRINT("PARAMETERS: ");
        for (unsigned int i = 0; i < p.size(); ++i)
            DBGPRINT(p[i]);

        sched_ = FACT(Scheduler).create(s_name, p);
        if (!sched_)
            throw ParseExc("Server::Server()", s);

        sched_->setKernel(this);
    }

    Server::~Server() {
        // delete sched_;
    }

    void Server::addTask(AbsRTTask &task, const string &params) {
        DBGENTER(_SERVER_DBG_LEV);
        task.setKernel(this);
        tasks.push_back(&task);
        DBGPRINT("Calling sched->addTask, with params = ", params);
        sched_->addTask(&task, params);
    }

    Scheduler::TheTaskList Server::getAllTasks() const {
        return sched_->getTasks();
    }

    bool taskIsActive(const AbsRTTask *task) {
        auto tt = dynamic_cast<const Task *>(task);
        auto ntt = dynamic_cast<const NonPeriodicTask *>(tt);

        // If the task ends now, skip
        if (tt->endEvt.getTime() == SIMUL.getTime())
            return false;

        // If the task is not active and its activation time is in the
        // past, skip
        if (tt->arrEvt.getTime() > SIMUL.getTime() && !tt->isActive())
            return false;

        // NOTE: non-periodic tasks can be created even without using
        // the NonPeriodicTask class, right? By manually setting the
        // Task parameters, I think? Check.

        // For non-periodic tasks, inactive or past tasks are ignored.
        if (ntt != nullptr) {
            if (!tt->isActive())
                return false;

            if (tt->arrEvt.getTime() + tt->getDeadline() <= SIMUL.getTime())
                return false;
        }

        // If none of those fire, the task is currently active
        return true;
    }

    Server::TaskList Server::getTasks() const {
        auto tasks = getAllTasks();
        return TaskList(tasks.begin(), tasks.end(), taskIsActive);
    }

    // Task interface
    void Server::schedule() {
        _schedEvt.process();
    }

    void Server::deschedule() {
        _deschedEvt.process();
    }

    Tick Server::getArrival() const {
        return arr;
    }

    Tick Server::getLastArrival() const {
        return last_arr;
    }

    void Server::setKernel(AbsKernel *k) {
        kernel = k;
    }

    Tick Server::getDeadline() const {
        return abs_dline;
    }

    Tick Server::getRelDline() const {
        return dline;
    }

    void Server::activate(AbsRTTask *task) {
        DBGENTER(_SERVER_DBG_LEV);

        sched_->insert(task);

        if (status == EXECUTING) {
            dispatch();
        } else if (status == IDLE) {
            idle_ready();
            kernel->activate(this);
        } else if (status == RELEASING) {
            releasing_ready();
            kernel->activate(this);
        } else if (status == RECHARGING || status == READY) {
            DBGPRINT("Server is RECHARGING or READY, activated task will wait...");
        } else {
            assert(false);
        }

        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::suspend(AbsRTTask *task) {
        DBGENTER(_SERVER_DBG_LEV);
        sched_->extract(task);

        assert(status == EXECUTING);

        if (getProcessor(task) != nullptr) {
            task->deschedule();
            currExe_ = nullptr;
            sched_->notify(nullptr);
        }

        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
        dispatch();
    }

    void Server::dispatch() {
        DBGENTER(_SERVER_DBG_LEV);
        _dispatchEvt.drop();
        _dispatchEvt.post(SIMUL.getTime());
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    CPU *Server::getProcessor(const AbsRTTask *) const {
        return kernel->getProcessor(this);
    }

    CPU *Server::getOldProcessor(const AbsRTTask *) const {
        return kernel->getOldProcessor(this);
    }

    void Server::onArrival(AbsRTTask *t) {
        DBGENTER(_SERVER_DBG_LEV);

        sched_->insert(t);

        if (status == EXECUTING) {
            dispatch();
        } else if (status == IDLE) {
            idle_ready();
            kernel->onArrival(this);
        } else if (status == RELEASING) {
            releasing_ready();
            kernel->onArrival(this);
        } else if (status == RECHARGING || status == READY) {
            DBGPRINT("Server is RECHARGING or READY, arrived task will wait...");
        }
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::onEnd(AbsRTTask *t) {
        DBGENTER(_SERVER_DBG_LEV);

        assert(status == EXECUTING);
        sched_->extract(t);
        currExe_ = nullptr;
        sched_->notify(nullptr); // round robin case
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
        dispatch();
    }

    void Server::onBudgetExhausted(Event *e) {
        DBGENTER(_SERVER_DBG_LEV);
        // std::cout << "t=" << SIMUL.getTime() << " Server::" << __func__ <<
        // "() "
        //           << std::endl;

        assert(status == EXECUTING);

        _dispatchEvt.drop();

        if (currExe_ != nullptr) {
            currExe_->deschedule();
            currExe_ = nullptr;
        }

        kernel->suspend(this);
        sched_->notify(nullptr);
        kernel->dispatch();

        executing_recharging();

        if (status == READY)
            kernel->onArrival(this);
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::onSched(Event *) {
        DBGENTER(_SERVER_DBG_LEV);

        // FIXME: when using HR==true we could end up here with status ==
        // RECHARGING.

        // FIXME: When HR!=true we could end up here with status == IDLE.

        assert(status == READY);

        ready_executing();
        dispatch();
    }

    void Server::onDesched(Event *) {
        DBGENTER(_SERVER_DBG_LEV);

        // I cannot assume it is still executing, maybe it is already
        // in recharging status (bacause of previous onEnd(task))
        if (status == EXECUTING) {
            executing_ready();
            // signal the task
            currExe_->deschedule();
            currExe_ = nullptr;
            sched_->notify(nullptr);
        }
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::onDlineMiss(Event *) {}

    void Server::onRecharging(Event *) {
        DBGENTER(_SERVER_DBG_LEV);

        assert(status == RECHARGING);

        if (sched_->getFirst() != nullptr) {
            recharging_ready();
            kernel->onArrival(this);
        } else {
            recharging_idle();
            currExe_ = nullptr;
            sched_->notify(nullptr);
        }
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::newRun() {
        arr = 0;
        last_arr = 0;
        status = IDLE;
        dline = 0;
        abs_dline = 0;
        last_exec_time = 0;
        _bandExEvt.drop();
        _dlineMissEvt.drop();
        _rechargingEvt.drop();

        _schedEvt.drop();
        _deschedEvt.drop();
        _dispatchEvt.drop();
        currExe_ = nullptr;
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

    void Server::endRun() {}

    void Server::onDispatch(Event *e) {
        DBGENTER(_SERVER_DBG_LEV);

        assert(status == EXECUTING);

        AbsRTTask *newExe = sched_->getFirst();

        DBGPRINT("Current situation");
        DBGPRINT("newExe: ", taskname(newExe));
        DBGPRINT("currExe_: ", taskname(currExe_));

        if (newExe != currExe_) {
            if (currExe_ != nullptr)
                currExe_->deschedule();
            currExe_ = newExe;
            if (currExe_ != nullptr)
                currExe_->schedule();
        }

        DBGPRINT("Now Running: ", taskname(newExe));

        if (currExe_ == nullptr) {
            sched_->notify(nullptr);
            executing_releasing();
            kernel->suspend(this);
            kernel->dispatch();
        }
        DBGPRINT("[t=", SIMUL.getTime(), "] Server ", getName(), " in ", __func__, "(): status=", status_string[status]);
    }

} // namespace RTSim
