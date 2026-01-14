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
#include <metasim/simul.hpp>

#include <rtsim/kernel.hpp>
#include <rtsim/task.hpp>
#include <rtsim/waitinstr.hpp>

namespace RTSim {

    using std::unique_ptr;
    using std::vector;

    WaitInstr::WaitInstr(Task *f, const string &r, int nr, const string &n) :
        Instr(f, n),
        _res(r),
        _waiting(false),
        _numberOfRes(nr) {}

    WaitInstr::WaitInstr(const WaitInstr &other) :
        Instr(other),
        _res(other.getResource()),
        _waiting(other._waiting),
        _numberOfRes(other.getNumOfResources()) {}

    unique_ptr<WaitInstr> WaitInstr::createInstance(vector<string> &par) {
        unique_ptr<WaitInstr> ptr(
            new WaitInstr(dynamic_cast<Task *>(Entity::_find(par[1])), par[0]));

        return ptr;
    }

    /// Since tasks can be nested and parent tasks usually implement both the
    /// AbsRTTask and the AbsRTKernel interfaces (see Servers), we need to climb
    /// up the ladder until we find a kernel on which we can request the
    /// resource and the task seen by that kernel that is issuing the request.
    ///
    /// Given the task that issued the wait/signal instruction, this function
    /// will climb up said ladder until a suitable pair is found.
    ///
    /// @note this means that, in case a task is wrapped in a server, the server
    /// will be the one issuing the request for the task, at least from a
    /// RTKernel/Scheduler point of view. This way, RTKernel/Scheduler
    /// implementation can be agnostic with respect to tasks hierarchy.
    ///
    /// @returns true if a suitable pair is found
    ///
    /// @param[inout] task_ptr      used to supply the initial task on which
    /// perform the research; after this call it will contain the task on which
    /// the actual request must be done
    ///
    /// @param[out]   kernel_ptr    the kernel on which the request must be done
    bool findKernelTask(AbsRTTask **task_ptr, RTKernel **rtkernel_ptr) {
        AbsRTTask *&task = (*task_ptr);
        RTKernel *&rtkernel = (*rtkernel_ptr);

        rtkernel = nullptr;
        while (rtkernel == nullptr && task != nullptr) {
            AbsKernel *kernel = task->getKernel();
            rtkernel = dynamic_cast<RTKernel *>(kernel);

            if (rtkernel == nullptr) {
                // The kernel is a server, not an actual rtkernel convert it to
                // its task interface and keep climbing up
                task = dynamic_cast<AbsRTTask *>(kernel);
            }
        }

        return task != nullptr;
    }

    void WaitInstr::schedule() {
        DBGENTER(_INSTR_DBG_LEV);
        DBGPRINT("Scheduling WaitInstr named: ", getName(), " within ", _father->toString());

        AbsRTTask *task = _father;
        RTKernel *rtkernel;
        auto found = findKernelTask(&task, &rtkernel);
        if (!found)
            throw BaseExc("WaitInstr: Kernel not found!");

        if (!_waiting) {
            // normal condition, when we enter the wait instruction
            if (_father->getKernel()->requestResource(_father, _res, _numberOfRes)) {
                DBGPRINT("Resource acquired, task ", task->toString(), ", _father ", _father->toString());
                _father->onInstrEnd();
            } else {
              DBGPRINT("Resource locked, task ", task->toString(), ", _father ", _father->toString());
                /* the res manager already suspends the task, but let's mark we're waiting,
                 * so the next time we're schedule()ed, we move forward */
                _waiting = true;
            }
        } else {
            // schedule()ed again after blocking
            DBGPRINT("Woken-up after block with Resource acquired, task ", task->toString(), ", _father ", _father->toString());
            _waiting = false;
            _father->onInstrEnd();
        }
    }

    void WaitInstr::deschedule() {
        DBGPRINT("Descheduling WaitInstr named: ", getName(), " within ", _father->toString());
    }

    // void WaitInstr::setTrace(Trace *t)
    // {
    //     _endEvt.addTrace(t);
    //     _waitEvt.addTrace(t);
    // }

    SignalInstr::SignalInstr(Task *f, const string &r, int nr,
                             const string &n) :
        Instr(f, n),
        _res(r),
        _numberOfRes(nr) {}

    SignalInstr::SignalInstr(const SignalInstr &other) :
        Instr(other),
        _res(other.getResource()),
        _numberOfRes(other.getNumOfResources()) {}

    unique_ptr<SignalInstr> SignalInstr::createInstance(vector<string> &par) {
        unique_ptr<SignalInstr> ptr(new SignalInstr(
            dynamic_cast<Task *>(Entity::_find(par[1])), par[0]));
        return ptr;
    }

    void SignalInstr::schedule() {
        DBGENTER(_INSTR_DBG_LEV);

        AbsRTTask *task = _father;
        RTKernel *rtkernel;
        auto found = findKernelTask(&task, &rtkernel);
        if (!found)
            throw BaseExc("SignalInstr: Kernel not found!");

        _father->getKernel()->releaseResource(_father, _res, _numberOfRes);

        _father->onInstrEnd();
    }

    void SignalInstr::deschedule() {
    }

    // void SignalInstr::setTrace(Trace *t)
    // {
    //     _endEvt.addTrace(t);
    //     _signalEvt.addTrace(t);
    // }

} // namespace RTSim
