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
#ifndef __JSONTRACE_HPP__
#define __JSONTRACE_HPP__

#include <fstream>
#include <iosfwd>
#include <string>

#include <metasim/baseexc.hpp>
#include <metasim/basetype.hpp>
#include <metasim/event.hpp>
#include <metasim/particle.hpp>
#include <metasim/trace.hpp>

#include <rtsim/rttask.hpp>
#include <rtsim/taskevt.hpp>
#include <rtsim/energy_info_provider.hpp>
#include <map>
#include <set>

namespace RTSim {
    class JSONTrace {
    protected:
        // V98修复：成员变量按析构需要顺序排列
        // 成员变量按声明顺序的逆序销毁，所以 fd 放在最后，确保先销毁
        bool first_event;
        MetaSim::Tick max_time; // 最大时间，用于过滤事件
        EnergyInfoProvider *_energy_provider; // 能量信息提供者

        // 追踪任务执行信息
        std::map<AbsRTTask*, MetaSim::Tick> _task_start_times; // 任务开始执行时间
        std::map<AbsRTTask*, double> _task_start_consumed; // 任务开始时的累计消耗能量

        // V83修复：跟踪已deadline miss的任务，避免重复记录descheduled事件
        std::set<AbsRTTask*> _deadline_missed_tasks;

        // V98: fd 放在最后，确保先被销毁，避免缓冲区问题影响其他成员
        std::ofstream fd;

        void writeTaskEvent(const Task &tt, const std::string &evt_name);
        void writeEnergyInfo(); // 写入能量信息
        void writeTaskEnergyInfo(AbsRTTask *task); // 写入任务能量信息

    public:
        JSONTrace(const std::string &name);
        JSONTrace(const std::string &name, MetaSim::Tick max);

        ~JSONTrace();

        // 设置能量信息提供者
        void setEnergyProvider(EnergyInfoProvider *provider) {
            _energy_provider = provider;
        }

        void probe(ArrEvt &e);

        void probe(EndEvt &e);

        void probe(SchedEvt &e);

        void probe(DeschedEvt &e);

        void probe(DeadEvt &e);

        void probe(KillEvt &e);

        void attachToTask(AbsRTTask &t);

        template <class X>
        void probe(GEvent<X> &e) {
            fd << "{ event: " << e.toString() << " }";
        }
    };
} // namespace RTSim

#endif
