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
#ifndef __RRSCHED_HPP__
#define __RRSCHED_HPP__

#include <map>

#include <metasim/baseexc.hpp>
#include <metasim/gevent.hpp>
#include <metasim/plist.hpp>
#include <metasim/simul.hpp>

#include <rtsim/scheduler/scheduler.hpp>

#define _RR_SCHED_DBG_LEV "RRSched"

namespace RTSim {

    using namespace MetaSim;

    class RTKernel;

    /**
        \ingroup sched

        Round Robin scheduler.
    */
    class RRScheduler : public Scheduler {
    protected:
        /**
           \ingroup sched
        */
        class RRSchedExc : public BaseExc {
        public:
            RRSchedExc(string msg) :
                BaseExc(msg, "RoundRobinScheduler", "rrsched.cpp") {}
        };

        class RRModel : public TaskModel {
        protected:
            Tick _rrSlice;

        public:
            RRModel(AbsRTTask *t) : TaskModel(t), _rrSlice(1) {}
            virtual ~RRModel() {}

            Tick getPriority() const override;
            void changePriority(Tick p) override;

            /**
               Returns the slice size (in number of ticks)
            */
            Tick getRRSlice() const {
                return _rrSlice;
            }

            /**
                Sets the slice size to s (in number of ticks)
            */
            void setRRSlice(Tick s) {
                _rrSlice = s;
            }

            /**
               This function returns true if the round has expired for the
               currently executing thread.
            */
            bool isRoundExpired() const;

            string toString() const override;
        };

        int defaultSlice;

        /**
           Introduced for the Multi Cores Queues, it prevents the scheduler
           to send events to its kernel and to post round events, if enabled =
           false
         */
        bool _enabled = true;

    public:
        // events must be public, (part of the interface)
        GEvent<RRScheduler> _rrEvt;

        /** Constructor */
        RRScheduler(int defSlice);

        void disable() {
            _enabled = false;
        }

        bool isEnabled() {
            return _enabled;
        }

        /**
           This function returns true if the round has expired for the
           given task.
        */
        bool isRoundExpired(AbsRTTask *t);

        /**
           Set the Round Robin slice.
        */
        virtual void setRRSlice(AbsRTTask *task, Tick slice);

        /**
           Notify to recompute the round
        */
        void notify(AbsRTTask *task) override;

        /**
           This is called by the event rrEvt.
        */
        void round(Event *);

        void addTask(AbsRTTask *t, int slice = 0); // throw(RRSchedExc);

        void addTask(AbsRTTask *t, const string &p) override;

        void removeTask(AbsRTTask *t) override {}

        static RRScheduler *createInstance(vector<string> &par);
    };

} // namespace RTSim

#endif
