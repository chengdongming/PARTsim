#ifndef __WAITINSTR_HPP__
#define __WAITINSTR_HPP__

#include <string>
#include <vector>

// From metasim
#include <metasim/event.hpp>
#include <metasim/factory.hpp>

// From RTLIB
#include <rtsim/instr.hpp>
#include <rtsim/taskevt.hpp>

namespace RTSim {
    using namespace MetaSim;

    class Task;

    /**
        \ingroup instr

        Simple classes which model wait and signal instruction to use a resource
        @author Fabio Rossi and Giuseppe Lipari
        @see Instr
    */

    class WaitInstr : public Instr {
        string _res;
        int _numberOfRes;
        bool _waiting;

        WaitInstr(const WaitInstr &wi);

    public:
        /**
           This is the constructor of the WaitInstr.
           @param f is a pointer to the task containing the pseudo
           instruction
           @param r is the name of the resorce manager handling the
           resource which the task is accessing
           @param nr is the number of resources being taken
           @param n is the instruction name
        */
        WaitInstr(Task *f, const std::string &r, int nr = 1,
                  const std::string &n = "");

        CLONEABLE(Instr, WaitInstr, override)

        static std::unique_ptr<WaitInstr>
            createInstance(std::vector<std::string> &par);

        /// Virtual methods from Instr
        void schedule() override;
        void deschedule() override;
        Tick getExecTime() const override {
            return 0;
        }
        double getActCycles() const override {
            return 0.0;
        }
        Tick getDuration() const override {
            return 0;
        }
        Tick getWCET() const override { // throw(RandomVar::MaxException) {
            return 0;
        }
        std::string getResource() const {
            return _res;
        }
        int getNumOfResources() const {
            return _numberOfRes;
        }
        void reset() override {}

        // template <class TraceClass>
        // void setTrace(TraceClass &trace_object) {
        //     attach_stat(trace_object, _endEvt);
        //     attach_stat(trace_object, _waitEvt);
        // }

        //void onEnd() override;
        void newRun() override {  _waiting = false;  }
        void endRun() override {  }

        /** Function inherited from clss Instr.It refreshes the state
         *  of the executing instruction when a change of the CPU speed occurs.
         */
        void refreshExec(double, double) override {}

        std::string toString() const override {
            return "wait(" + _res + ")";
        }
    };

    /**
       \ingroup instr

       Simple class which models signal instruction to use a resource.
       @author Fabio Rossi and Giuseppe Lipari
       @see Instr
    */

    class SignalInstr : public Instr {
        string _res;

        int _numberOfRes;

        SignalInstr(const SignalInstr &other);

    public:
        /**
           This is the constructor of the SignalInstr
           @param f is a pointer to the task containing the pseudo
           instruction
           @param r is the name of the resource which the task has
           accessed
           @param nr is the number of resources being taken
           @param n is the instruction name
        */
        SignalInstr(Task *f, const std::string &r, int nr = 1,
                    const std::string &n = "");

        CLONEABLE(Instr, SignalInstr, override)

        static std::unique_ptr<SignalInstr>
            createInstance(std::vector<std::string> &par);

        /// Virtual methods from Instr
        void schedule() override;
        void deschedule() override;
        Tick getExecTime() const override {
            return 0;
        }
        double getActCycles() const override {
            return 0.0;
        }
        Tick getDuration() const override {
            return 0;
        }
        Tick getWCET() const override { // throw(RandomVar::MaxException) {
            return 0;
        }
        void reset() override {}
        // virtual void setTrace(Trace *);

        // template <class TraceClass>
        // void setTrace(TraceClass &trace_object) {
        //     attach_stat(trace_object, _endEvt);
        //     attach_stat(trace_object, _signalEvt);
        // }

        string getResource() const {
            return _res;
        }
        int getNumOfResources() const {
            return _numberOfRes;
        }

        //void onEnd() override;
        void newRun() override {  }
        void endRun() override {  }

        /** Function inherited from clss Instr.It refreshes the state
         *  of the executing instruction when a change of the CPU speed occurs.
         */
        void refreshExec(double, double) override {}

        std::string toString() const override {
            return "signal(" + _res + ")";
        }
    };

} // namespace RTSim

#endif
