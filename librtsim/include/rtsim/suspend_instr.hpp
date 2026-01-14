#ifndef __SUSPEND_INSTR__
#define __SUSPEND_INSTR__

#include <metasim/gevent.hpp>
#include <metasim/randomvar.hpp>
#include <rtsim/instr.hpp>
#include <string>
#include <vector>

namespace RTSim {

    class SuspendInstr : public Instr {
        MetaSim::GEvent<SuspendInstr> suspEvt;
        MetaSim::GEvent<SuspendInstr> resumeEvt;

        Tick delay;

        SuspendInstr(const SuspendInstr &other);

    public:
        SuspendInstr(Task *f, MetaSim::Tick delay);

        CLONEABLE(Instr, SuspendInstr, override)

        static SuspendInstr *createInstance(std::vector<std::string> &par);

        void schedule() override;
        void deschedule() override;
        Tick getExecTime() const override {
            return 0;
        };
        double getActCycles() const override {
            return 0.0;
        }
        Tick getDuration() const override {
            return 0;
        };
        Tick getWCET() const override { // throw(RandomVar::MaxException) {
            return 0;
        }
        void reset() override {}
        virtual void setTrace(Trace *);

        Tick getDelay() const {
            return delay;
        }

        void onSuspend(MetaSim::Event *evt);
        void onEnd(MetaSim::Event *evt);
        void newRun() override;
        void endRun() override;

        /** Function inherited from clss Instr.It refreshes the state
         *  of the executing instruction when a change of the CPU speed occurs.
         */
        void refreshExec(double, double) override {}
    };
} // namespace RTSim

#endif
