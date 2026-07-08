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
#include <vector>

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
    struct SchedulerTraceJob {
        std::string task_name;
        double arrival_time;
        double priority;
        int ready_order;
        double task_unit_energy_mJ;
        double remaining_time_ms;
        double absolute_deadline;
    };

    class JSONTrace {
    protected:
        // V98修复：成员变量按析构需要顺序排列
        // 成员变量按声明顺序的逆序销毁，所以 fd 放在最后，确保先销毁
        bool first_event;
        MetaSim::Tick max_time; // 最大时间，用于过滤事件
        EnergyInfoProvider *_energy_provider; // 能量信息提供者
        bool _semantic_trace_enabled;

        // 追踪任务执行信息
        std::map<AbsRTTask*, MetaSim::Tick> _task_start_times; // 任务开始执行时间
        std::map<AbsRTTask*, double> _task_start_consumed; // 任务开始时的累计消耗能量

        // V83修复：跟踪已deadline miss的任务，避免重复记录descheduled事件
        std::set<AbsRTTask*> _deadline_missed_tasks;

        // Early Abort专用：等待在descheduled之后补记的dline_miss
        std::map<AbsRTTask*, std::string> _pending_forced_dline_miss;

        // V98: fd 放在最后，确保先被销毁，避免缓冲区问题影响其他成员
        std::ofstream fd;

        void writeTaskEvent(const Task &tt, const std::string &evt_name);
        void writeEnergyInfo(); // 写入能量信息
        void writeTaskEnergyInfo(AbsRTTask *task); // 写入任务能量信息
        void writeForcedDlineMissNow(AbsRTTask *task, const std::string &reason);
        void beginEvent();
        static std::string escapeJson(const std::string &value);
        void writeSchedulerJob(const SchedulerTraceJob &job);
        void writeSchedulerJobArray(const std::vector<SchedulerTraceJob> &jobs);

    public:
        JSONTrace(const std::string &name);
        JSONTrace(const std::string &name, MetaSim::Tick max);

        ~JSONTrace();

        // 设置能量信息提供者
        void setEnergyProvider(EnergyInfoProvider *provider) {
            _energy_provider = provider;
        }

        void setSemanticTraceEnabled(bool enabled) {
            _semantic_trace_enabled = enabled;
        }

        bool semanticTraceEnabled() const {
            return _semantic_trace_enabled;
        }

        void logSchedulerDecision(
            const std::string &scheduler,
            double available_energy_mJ,
            const std::vector<SchedulerTraceJob> &ready_jobs,
            const std::vector<SchedulerTraceJob> &selected_jobs,
            const std::string &decision_reason);

        void logEnergyBlock(
            const std::string &scheduler,
            const SchedulerTraceJob &blocked_task,
            double available_energy_mJ);

        void logNonBlockBypass(
            const std::string &scheduler,
            const SchedulerTraceJob &blocked_higher_priority_task,
            const SchedulerTraceJob &bypassed_task,
            double available_energy_mJ);

        void logSyncBatchBlock(
            const std::string &scheduler,
            const std::vector<SchedulerTraceJob> &batch_tasks,
            double batch_required_energy_mJ,
            double available_energy_mJ,
            bool feasible_subset_exists);

        void logSTChargeEvent(
            const std::string &event_type,
            const std::string &scheduler,
            const std::vector<SchedulerTraceJob> &blocked_jobs,
            double available_energy_mJ,
            double required_energy_mJ,
            double slack_at_begin,
            const std::string &release_reason = "");

        void probe(ArrEvt &e);

        void probe(EndEvt &e);

        void probe(SchedEvt &e);

        void probe(DeschedEvt &e);

        void probe(DeadEvt &e);

        void probe(KillEvt &e);

        void attachToTask(AbsRTTask &t);

        // ⭐ V58新增：强制记录dline_miss事件（用于Early Abort场景）
        // 当调度器因能量耗尽/Slack<0等原因主动kill任务时，
        // DeadEvt可能不会被触发，需要主动注入dline_miss记录供Python脚本统计
        void forceLogDlineMiss(AbsRTTask *task, const std::string &reason = "early_abort_energy_depleted");

        // Early Abort专用：若任务刚被suspend，则等descheduled落盘后再补记dline_miss
        void forceLogDlineMissAfterDesched(AbsRTTask *task, const std::string &reason = "early_abort_energy_depleted");

        template <class X>
        void probe(GEvent<X> &e) {
            fd << "{ event: " << e.toString() << " }";
        }
    };
} // namespace RTSim

#endif
