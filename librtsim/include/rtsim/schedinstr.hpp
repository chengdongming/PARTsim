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
#ifndef __SCHEDINSTR_HPP__
#define __SCHEDINSTR_HPP__

#include <string>
#include <vector>

// From METASIM
#include <metasim/event.hpp>
#include <metasim/factory.hpp>

// From RTLIB
#include <rtsim/instr.hpp>
#include <rtsim/taskevt.hpp>

namespace RTSim {

    using namespace MetaSim;

    class Task;
    class SchedInstr;

    /**
       \ingroup instr

       event for threshold instr
    */
    class SchedIEvt : public TaskEvt {
    protected:
        SchedInstr *ti;

    public:
        SchedIEvt(Task *t, SchedInstr *in) :
            TaskEvt(t, _DEFAULT_PRIORITY - 1),
            ti(in) {}
        SchedInstr *getInstr() {
            return ti;
        }
        void doit() override {}
    };

    /**
        \ingroup instr

        Simple classes which model instruction to set a preemption threshold
        @author Francesco Prosperi
        @see Instr
    */
    class SchedInstr : public Instr {
        EndInstrEvt _endEvt;
        SchedIEvt _threEvt;

        // Copy constructor
        SchedInstr(const SchedInstr &si);

    public:
        /**
         //      This is the constructor of the SchedInstr.
         //      @param f is a pointer to the task containing the pseudo
         //      instruction
         */
        SchedInstr(Task *f, const string &s, const string &n = "");

        CLONEABLE(Instr, SchedInstr, override)

        static std::unique_ptr<SchedInstr>
            createInstance(const std::vector<std::string> &par);

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

        template <class TraceClass>
        void setTrace(TraceClass &trace_obj) {
            attach_stat(trace_obj, _endEvt);
            attach_stat(trace_obj, _threEvt);
        }

        void onEnd() override;
        void newRun() override {}
        void endRun() override;

        /** Function inherited from clss Instr.It refreshes the state
         *  of the executing instruction when a change of the CPU speed occurs.
         */
        void refreshExec(double, double) override {}
    };

} // namespace RTSim

#endif
