#include <cassert>
#include <iostream>
#include <sstream>

// #include <rtsim/energyMRTKernel.hpp>
#include <rtsim/scheduler/rrsched.hpp>
#include <rtsim/task.hpp>

namespace RTSim {
    using namespace MetaSim;

    bool RRScheduler::RRModel::isRoundExpired() const {
        Task *t = dynamic_cast<Task *>(_rtTask);

        assert(t != NULL);

        Tick last = t->getLastSched();
        if (_rrSlice > 0 && (SIMUL.getTime() - last) >= _rrSlice)
            return true;
        return false;
    }

    string RRScheduler::RRModel::toString() const {
        string s = "RRModel for " + _rtTask->toString() + ", slice " +
                   std::to_string(double(getRRSlice())) +
                   ", expired: " + (isRoundExpired() ? "yes" : "no");
        return s;
    }

    bool RRScheduler::isRoundExpired(AbsRTTask *task) {
        assert(isEnabled());

        RRModel *model = dynamic_cast<RRModel *>(find(task));
        if (model == 0)
            throw RRSchedExc("Cannot find task");
        bool res = model->isRoundExpired();
        return res;
    }

    Tick RRScheduler::RRModel::getPriority() const {
        return 1;
    }

    void RRScheduler::RRModel::changePriority(Tick p) {
        throw RRSchedExc("Cannot change priority in RRSched!");
    }

    RRScheduler::RRScheduler(int defSlice) :
        Scheduler(),
        defaultSlice(defSlice),
        _rrEvt("RoundRobinRound", this, &RRScheduler::round),
        _enabled(true) {
        DBGENTER(_RR_SCHED_DBG_LEV);
        DBGPRINT("DEFAULT SLICE = ", defaultSlice);
    }

    void RRScheduler::setRRSlice(AbsRTTask *task, Tick slice) {
        RRModel *model = dynamic_cast<RRModel *>(find(task));
        if (model == 0)
            throw RRSchedExc("Cannot find task");
        model->setRRSlice(slice);
    }

    void RRScheduler::notify(AbsRTTask *task) {
        std::cout << __func__ << "() " << getName() << ":" << std::endl;
        if (!isEnabled()) {
            std::cout << "\tdisabled, skip" << std::endl;
            return;
        }
        DBGENTER(_RR_SCHED_DBG_LEV);
        _rrEvt.drop();

        if (task != NULL) {
            RRModel *model = dynamic_cast<RRModel *>(find(task));
            if (model == 0)
                throw RRSchedExc("Cannot find task");
            if (model->getRRSlice() > 0) {
                _rrEvt.post(SIMUL.getTime() + model->getRRSlice());
                DBGPRINT("rrEvt post at time ",
                         SIMUL.getTime() + model->getRRSlice());
                std::cout << "\trrEvt post at time "
                          << SIMUL.getTime() + model->getRRSlice() << " task "
                          << taskname(task) << std::endl;
            }
        }
    }

    void RRScheduler::round(Event *) {
        std::cout << __func__ << "() t = " << SIMUL.getTime() << " "
                  << getName() << ":" << std::endl;
        if (!isEnabled()) {
            std::cout << "\tdisabled, skip" << std::endl;
            return;
        }

        DBGENTER(_RR_SCHED_DBG_LEV);
        RRModel *model = dynamic_cast<RRModel *>(_queue.front());
        if (model == 0)
            return; // throw RRSchedExc("Cannot find task");

        if (model->isRoundExpired()) {
            DBGPRINT("Round expired");
            _queue.erase(model);
            // todo temp
            std::cout << "\tRound expired for task " << model->toString()
                      << " => removed" << std::endl;
            if (model->isActive()) {
                model->setInsertTime(SIMUL.getTime());
                _queue.insert(model);
                // todo temp
                std::cout << "\tand then reinserted into queue" << std::endl;
            }
        }

        //         if it is not active... ?
        RRModel *first = dynamic_cast<RRModel *>(_queue.front());
        //         if (first == 0) throw RRSchedExc("Cannot find task");

        if (first != 0) {
            Tick slice = first->getRRSlice();
            if (slice == 0)
                _rrEvt.drop();
            else {
                if (first != model || first->isRoundExpired()) {
                    _rrEvt.drop();
                    _rrEvt.post(SIMUL.getTime() + slice);
                    // todo rem
                    std::cout << "\tround evt set at "
                              << SIMUL.getTime() + slice << " for task "
                              << first->toString() << std::endl;
                }
            }
        }

        std::cout << "\tRRScheduler queue: " << toString() << std::endl;

        if (_kernel) {
            DBGPRINT("informing the kernel");
            std::cout << "\tInforming the kernel" << std::endl;

            // // FIXME: If compiling against EnergyMRTKernel uncomment
            // if (dynamic_cast<EnergyMRTKernel *>(_kernel))
            //     dynamic_cast<EnergyMRTKernel *>(_kernel)->onRound(
            //         model->getTask());
            // else // if you are not using EMRTKernel
            _kernel->dispatch();
        }
    }

    void RRScheduler::addTask(AbsRTTask *task, int slice) {
        DBGENTER(_RR_SCHED_DBG_LEV);
        auto model = new RRModel(task);
        enqueueModel(model);
        if (slice < 1) {
            slice = defaultSlice;
            DBGPRINT("Default slice set: ", defaultSlice);
        }
        DBGPRINT("Slice set to: ", slice);
        model->setRRSlice(slice);
    }

    void RRScheduler::addTask(AbsRTTask *task, const std::string &p) {
        DBGENTER(_RR_SCHED_DBG_LEV);
        int slice = defaultSlice;

        std::stringstream ss(p);
        ss >> slice;
        if (!ss) {
            DBGPRINT("Default slice set: ", defaultSlice);
        }

        addTask(task, slice);
    }

    RRScheduler *RRScheduler::createInstance(vector<string> &par) {
        int slice;
        std::stringstream ss(par[0]);
        ss >> slice;
        return new RRScheduler(slice);
    }
} // namespace RTSim
