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
/*
 * $Id: exeinstr.hpp,v 1.7 2005/10/05 16:46:22 cesare Exp $
 *
 * $Log: exeinstr.hpp,v $
 * Revision 1.7  2005/10/05 16:46:22  cesare
 * Added DLL support.
 *
 * Revision 1.6  2005/04/28 01:34:47  cesare
 * Moved to sstream. Headers install. Code cleaning.
 *
 * Revision 1.5  2004/11/26 03:47:10  cesare
 * Finished merging the main trunk with Lipari's branch.
 *
 */
#ifndef __EXEINSTR_HPP__
#define __EXEINSTR_HPP__

#include <metasim/memory.hpp>

// From metasim
#include <metasim/debugstream.hpp>
#include <metasim/event.hpp>
#include <metasim/randomvar.hpp>
#include <metasim/simul.hpp>

// From RTLIB
#include <rtsim/instr.hpp>
#include <sstream>

namespace RTSim {

    using namespace MetaSim;

    /**
        \ingroup instr

        These instructions (ExecInstr & FixedInstr) are used to model a
        basic block of time-consuming computation, and are the basic
        building block of a real task

        @author Luigi Palopoli, Giuseppe Lipari, Gerardo Lamastra, Antonio
       Casile
        @version 2.0
        @see Instr */

    class ExecInstr : public Instr {
    protected:
        /// End of Instruction flag
        bool isBegOfInstr;
        /// Random var representing the instruction cost/duration
        std::unique_ptr<RandomVar> cost;
        /// Random var representing the instruction cost/duration
        string workload;
        /// Actual Real-Time execution of the instruction
        Tick execdTime;
        /// Duration of the current instruction
        Tick currentCost;
        /// Execution cycles spent by the instruction (independently of the CPU
        /// speed they've been consumed with)
        double actCycles;
        /// Last instant of time this instruction was scheduled
        Tick lastTime;
        /// True if the instruction is currently executing
        bool executing;

        // copy constructor
        ExecInstr(const ExecInstr &obj);

    public:
        EndInstrEvt _endEvt;
        /** This is the constructor of the ExecInstr
            @param f is a pointer to the task containing the pseudo instruction
            @param c is a random variable containing the duration of the
           instruction
            @param n is the instruction name
        */
        ExecInstr(Task *f, std::unique_ptr<RandomVar> c,
                  const std::string &wl = "", const std::string &n = "");
        static Instr *createInstance(const std::vector<std::string> &par);

        CLONEABLE(Instr, ExecInstr, override)

        virtual ~ExecInstr();

        string toString() const override {
            std::stringstream ss;
            ss << "ExecInstr(" << getWCET() << ", " << workload << ")";
            return ss.str();
        }

        // Virtual methods from Instr
        void schedule() override; // throw(InstrExc) override;
        void deschedule() override;
        void onEnd() override;
        void reset() override;
        Tick getDuration() const override;
        Tick getWCET() const override;
        Tick getExecTime() const override;
        inline double getActCycles() const override {
            return actCycles;
        }
        virtual string getWorkload() const {
            return workload;
        }

        /* Commented, because the tracing mechanism has changed */
        // virtual void setTrace(Trace *t);

        template <class TraceClass>
        void setTrace(TraceClass &traceobj) {
            attach_stat(traceobj, _endEvt);
        }

        // From Entity...
        void newRun() override;
        void endRun() override;

        /** Function inherited from Instr. It refreshes the state of the
         *  executing instruction when a change of the CPU speed occurs.
         */
        void refreshExec(double oldSpeed, double newSpeed) override;
    };

    /**
       \ingroup instr

       This is defined for user's commodity. Actually it is an ExecInstr
       having a fixed duration.
       @author Luigi Palopoli, Giuseppe Lipari, Gerardo Lamastra, Antonio Casile
       @version 2.0
       @see Instr, Task
    */
    class FixedInstr : public ExecInstr {
    public:
        FixedInstr(Task *t, Tick duration, const std::string &wl = "",
                   const std::string &n = "");

        static std::unique_ptr<Instr>
            createInstance(const std::vector<std::string> &par);
    };

} // namespace RTSim

#endif
