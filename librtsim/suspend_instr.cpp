#include <iostream>
#include <rtsim/kernel.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>

#include <rtsim/suspend_instr.hpp>

namespace RTSim {
    using namespace MetaSim;

    using std::vector;

    SuspendInstr::SuspendInstr(Task *f, Tick d) :
        Instr(f),
        suspEvt("suspending", this, &SuspendInstr::onSuspend),
        resumeEvt("resuming", this, &SuspendInstr::onEnd),
        delay(d) {}

    SuspendInstr::SuspendInstr(const SuspendInstr &other) :
        Instr(other),
        suspEvt("suspending", this, &SuspendInstr::onSuspend),
        resumeEvt("resuming", this, &SuspendInstr::onEnd),
        delay(other.getDelay()) {}

    SuspendInstr *SuspendInstr::createInstance(vector<string> &par) {
        if (par.size() != 2)
            throw parse_util::ParseExc("SuspendInstr::createInstance",
                                       "Wrong number of arguments");

        Task *t = dynamic_cast<Task *>(Entity::_find(par[1]));
        Tick d = stoi(par[0]);

        return new SuspendInstr(t, d);
    }

    void SuspendInstr::schedule() {
        suspEvt.process();
    }

    void SuspendInstr::deschedule() {}

    void SuspendInstr::setTrace(Trace *t) {}

    void SuspendInstr::onSuspend(Event *evt) {
        AbsKernel *k = _father->getKernel();
        k->suspend(_father);
        // 先drop可能存在的旧resumeEvt，避免重复post
        resumeEvt.drop();
        resumeEvt.post(SIMUL.getTime() + delay);
    }

    void SuspendInstr::onEnd(Event *evt) {
        // 添加调试输出
        static int call_count = 0;
        call_count++;
        std::cout << "[DEBUG] SuspendInstr::onEnd() called, count=" << call_count
                  << " task=" << _father->getName()
                  << " time=" << SIMUL.getTime() << "ms" << std::endl;

        if (call_count > 1000) {
            std::cout << "[ERROR] SuspendInstr::onEnd() called too many times! Possible infinite loop." << std::endl;
            throw std::runtime_error("SuspendInstr::onEnd() infinite loop detected");
        }

        // suspend结束后，调用Task::onInstrEnd()来推进指令指针
        // Task::onInstrEnd()会检测到is_suspend_instr=true，并重新调度任务
        _father->onInstrEnd();
    }

    void SuspendInstr::newRun() {
        // nothing to be done
    }

    void SuspendInstr::endRun() {
        suspEvt.drop();
        resumeEvt.drop();
    }

} // namespace RTSim
