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
#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <metasim/baseexc.hpp>
#include <metasim/basetype.hpp>
#include <metasim/event.hpp>
#include <metasim/particle.hpp>
#include <metasim/trace.hpp>

#include <rtsim/rttask.hpp>
#include <rtsim/taskevt.hpp>
#include <rtsim/energy_info_provider.hpp>

namespace RTSim {
    enum class DecisionExclusionReason : std::uint8_t {
        None = 0,
        CpuCapacity,
        TimingDefer,
        DirectEnergyShortage,
        BlockHeadOfLine,
        SyncAtomicUnaffordable,
        StEnergyChargeWait,
    };

    // Data-only snapshot of one job at a scheduler decision.  All strings
    // and values are owned by the record; no scheduler containers or task
    // pointers are retained.
    struct DecisionJobRecord {
        std::string job_id;
        std::string task_name;
        std::uint32_t priority_rank = 0;
        bool candidate = false;
        bool infinite_energy_dispatch_demand = false;
        bool actual_dispatch = false;
        bool is_top4 = false;
        bool is_bottom6 = false;
        double incremental_energy_cost_j = 0.0;
        DecisionExclusionReason exclusion_reason =
            DecisionExclusionReason::None;
    };

    struct DecisionRecord {
        std::int64_t tick = -1;
        std::size_t processor_count = 0;
        double available_energy_j = 0.0;
        double energy_epsilon_j = 1e-9;
        std::vector<DecisionJobRecord> jobs;
    };

    // Pure constructor used by scheduler wiring. Pointer-bearing containers
    // are consumed only while constructing the snapshot; DecisionRecord owns
    // every identity and retains no scheduler references.
    DecisionRecord makeOwnedDecisionRecord(
        std::int64_t tick,
        std::size_t processor_count,
        double available_energy_j,
        double energy_epsilon_j,
        const std::vector<AbsRTTask *> &priority_universe,
        const std::vector<AbsRTTask *> &observed_jobs,
        const std::set<AbsRTTask *> &candidates,
        const std::set<AbsRTTask *> &infinite_energy_dispatch_demand,
        const std::set<AbsRTTask *> &actual_dispatch,
        const std::map<AbsRTTask *, double> &incremental_energy_costs_j,
        const std::map<AbsRTTask *, DecisionExclusionReason>
            &exclusion_reasons);

    struct MechanismSummary {
        std::uint64_t bypass_opportunity_ticks = 0;
        std::uint64_t actual_bypass_ticks = 0;
        std::uint64_t low_priority_bypass_core_ticks = 0;
        std::uint64_t hp_dispatch_demand_ticks = 0;
        std::uint64_t hp_energy_blocked_ticks = 0;
        std::uint64_t hp_energy_blocked_job_ticks = 0;
        std::uint64_t observed_decision_ticks = 0;
    };

    enum class ObservabilitySummaryState : std::uint8_t {
        Disabled = 0,
        Enabled,
        Finalized,
    };

    class MechanismSummaryAccumulator {
    public:
        MechanismSummaryAccumulator() = default;
        explicit MechanismSummaryAccumulator(std::uint64_t horizon_ticks);

        void reset(std::uint64_t horizon_ticks);
        void observe(const DecisionRecord &record);
        const MechanismSummary &finalize();

        bool isConfigured() const noexcept { return _configured; }
        bool isFinalized() const noexcept { return _finalized; }
        const MechanismSummary &summary() const noexcept { return _summary; }

    private:
        bool _configured = false;
        bool _finalized = false;
        std::uint64_t _horizon_ticks = 0;
        std::uint64_t _next_tick = 0;
        std::size_t _processor_count = 0;
        MechanismSummary _summary;
    };

    struct PerTaskLifecycleSummary {
        std::string task_name;
        std::uint64_t released_jobs = 0;
        std::uint64_t completed_jobs = 0;
        std::uint64_t deadline_miss_jobs = 0;
        std::uint64_t unfinished_at_horizon_jobs = 0;
        std::uint64_t executed_core_ticks = 0;
        std::uint64_t completed_response_time_count = 0;
        std::uint64_t completed_response_time_sum_ms = 0;
        std::uint64_t completed_response_time_max_ms = 0;
    };

    class PerTaskLifecycleAccumulator {
    public:
        void reset(std::int64_t horizon_ms);

        void onRelease(const std::string &task_name,
                       std::int64_t release_time_ms);
        void onSchedule(const std::string &task_name,
                        std::int64_t release_time_ms,
                        std::int64_t schedule_time_ms);
        void onDeschedule(const std::string &task_name,
                          std::int64_t release_time_ms,
                          std::int64_t deschedule_time_ms);
        void onCompletion(const std::string &task_name,
                          std::int64_t release_time_ms,
                          std::int64_t completion_time_ms);
        void onDeadlineMiss(const std::string &task_name,
                            std::int64_t release_time_ms,
                            std::int64_t miss_time_ms);
        void onKill(const std::string &task_name,
                    std::int64_t release_time_ms,
                    std::int64_t kill_time_ms);
        void finalize(std::int64_t horizon_ms);

        bool isConfigured() const noexcept { return _configured; }
        bool isFinalized() const noexcept { return _finalized; }
        bool hasActiveJob(const std::string &task_name,
                          std::int64_t release_time_ms) const;
        std::size_t retainedJobIdentityCount() const noexcept {
            return _active_jobs.size();
        }
        std::vector<PerTaskLifecycleSummary> summaries() const;

    private:
        using JobIdentity = std::pair<std::string, std::int64_t>;

        struct ActiveJobState {
            bool running = false;
            std::int64_t running_since_ms = 0;
            bool deadline_miss_recorded = false;
        };

        void validateEvent(const std::string &task_name,
                           std::int64_t release_time_ms,
                           std::int64_t event_time_ms) const;
        void closeRunning(const JobIdentity &job,
                          ActiveJobState &state,
                          std::int64_t close_time_ms);

        bool _configured = false;
        bool _finalized = false;
        std::int64_t _horizon_ms = -1;
        std::map<std::string, PerTaskLifecycleSummary> _task_summaries;
        std::map<JobIdentity, ActiveJobState> _active_jobs;
    };

    struct SchedulerTraceJob {
        std::string task_name;
        double arrival_time;
        double priority;
        int ready_order;
        double task_unit_energy_mJ;
        double remaining_time_ms;
        double absolute_deadline;
    };

    struct ReleaseEnergySnapshotJob {
        std::string task_name;
        MetaSim::Tick arrival_time;
    };

    // EXT-1B/B3 read-only timing evidence.  Schedulers populate this only
    // after their native selection decision has been finalized; no field is
    // consumed by scheduling code.
    struct B3TimingObservation {
        std::string scheduler;
        std::string scheduler_family;
        std::string blocking_policy;
        std::string task_name;
        std::string task_id;
        double arrival_time;
        std::string job_id;
        double remaining_time_ms;
        double rounded_remaining_ms;
        double absolute_deadline;
        double scheduler_slack;
        bool ready;
        bool timing_gate_open;
        bool cpu_available;
        bool continuation;
        bool selected;
        double job_required_energy_mJ;
        double decision_required_energy_mJ;
        double available_energy_mJ;
        bool job_energy_affordable;
        bool decision_energy_affordable;
        double native_epsilon_mJ;
        std::string blocking_policy_reason;
        std::string actual_outcome;
        std::string reason_code;
    };

    class JSONTrace {
    public:
        static constexpr int TRACE_SCHEMA_VERSION = 2;

    protected:
        // V98修复：成员变量按析构需要顺序排列
        // 成员变量按声明顺序的逆序销毁，所以 fd 放在最后，确保先销毁
        bool first_event;
        MetaSim::Tick max_time; // 最大时间，用于过滤事件
        MetaSim::Tick _observed_simulation_end;
        bool _simulation_completed;
        std::string _simulation_completion_reason;
        EnergyInfoProvider *_energy_provider; // 能量信息提供者
        bool _semantic_trace_enabled;
        std::string _configured_scheduler;
        std::string _scheduler_display_name;
        std::string _scheduler_implementation;
        std::string _scheduler_rtti_name;
        std::string _run_id;
        std::string _taskset_semantic_hash;
        bool _release_cutoff_enabled;
        MetaSim::Tick _release_horizon;
        MetaSim::Tick _observation_horizon;
        std::uint64_t _run_generation;
        std::set<std::uint64_t> _run_generations_seen;

        // 追踪任务执行信息
        std::map<AbsRTTask*, MetaSim::Tick> _task_start_times; // 任务开始执行时间
        std::map<AbsRTTask*, double> _task_start_consumed; // 任务开始时的累计消耗能量

        // V83修复：跟踪已deadline miss的任务，避免重复记录descheduled事件
        std::set<AbsRTTask*> _deadline_missed_tasks;
        std::set<std::pair<AbsRTTask*, MetaSim::Tick::impl_t>>
            _logged_deadline_misses;

        // Early Abort专用：等待在descheduled之后补记的dline_miss
        std::map<AbsRTTask*, std::string> _pending_forced_dline_miss;

        ObservabilitySummaryState _observability_summary_state;
        MetaSim::Tick _observability_summary_horizon;
        MechanismSummaryAccumulator _mechanism_summary;
        PerTaskLifecycleAccumulator _lifecycle_summary;

        // V98: fd 放在最后，确保先被销毁，避免缓冲区问题影响其他成员
        std::ofstream fd;

        void writeTaskEvent(const Task &tt, const std::string &evt_name);
        void writeEnergyInfo(); // 写入能量信息
        void writeTaskEnergyInfo(AbsRTTask *task); // 写入任务能量信息
        void writeForcedDlineMissNow(AbsRTTask *task, const std::string &reason);
        void writeDeadlineMissNow(Task &task,
                                  MetaSim::Tick release_time,
                                  MetaSim::Tick absolute_deadline,
                                  const std::string &reason);
        void beginEvent();
        void ensureCurrentRun();
        bool shouldAccumulateLifecycleSummary() const;
        static std::string escapeJson(const std::string &value);
        void writeSchedulerJob(const SchedulerTraceJob &job);
        void writeSchedulerJobArray(const std::vector<SchedulerTraceJob> &jobs);

    public:
        JSONTrace(const std::string &name);
        JSONTrace(const std::string &name, MetaSim::Tick max);

        ~JSONTrace();

        void beginRun(std::uint64_t generation);

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

        void setObservedSimulationEnd(MetaSim::Tick end_time) {
            ensureCurrentRun();
            _observed_simulation_end = end_time;
        }

        void setSimulationOutcome(MetaSim::Tick end_time,
                                  bool completed,
                                  const std::string &reason);

        void setSchedulerIdentity(const std::string &configured_scheduler,
                                  const std::string &display_name,
                                  const std::string &implementation,
                                  const std::string &rtti_name = "") {
            _configured_scheduler = configured_scheduler;
            _scheduler_display_name = display_name;
            _scheduler_implementation = implementation;
            _scheduler_rtti_name = rtti_name;
        }

        void setRunId(const std::string &run_id) { _run_id = run_id; }
        void setTasksetSemanticHash(const std::string &value) {
            _taskset_semantic_hash = value;
        }

        void setReleaseObservationWindow(
            MetaSim::Tick release_horizon,
            MetaSim::Tick observation_horizon);

        void logReleaseEnergySnapshots(
            const std::string &scheduler,
            double available_energy_mJ,
            const std::vector<ReleaseEnergySnapshotJob> &released_jobs);

        void enableObservabilitySummaries(MetaSim::Tick horizon);
        void finalizeObservabilitySummaries(MetaSim::Tick horizon);
        bool observabilitySummariesEnabled() const noexcept {
            return _observability_summary_state ==
                ObservabilitySummaryState::Enabled;
        }
        bool observabilitySummaryAcceptsDecisionTick(
            MetaSim::Tick tick) const noexcept {
            return observabilitySummariesEnabled() &&
                   tick >= MetaSim::Tick(0) &&
                   tick < _observability_summary_horizon;
        }
        bool observabilitySummariesFinalized() const noexcept {
            return _observability_summary_state ==
                ObservabilitySummaryState::Finalized;
        }
        ObservabilitySummaryState observabilitySummaryState() const noexcept {
            return _observability_summary_state;
        }
        void observeDecision(const DecisionRecord &record);
        const MechanismSummary &mechanismSummary() const noexcept {
            return _mechanism_summary.summary();
        }
        std::vector<PerTaskLifecycleSummary> perTaskLifecycleSummary() const {
            return _lifecycle_summary.summaries();
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

        void logSyncBatchCandidateWait(
            const std::string &scheduler,
            const std::vector<SchedulerTraceJob> &active_top_m_tasks,
            const std::vector<SchedulerTraceJob> &continuation_tasks,
            const std::vector<SchedulerTraceJob> &new_candidate_tasks,
            const std::vector<SchedulerTraceJob> &selected_tasks,
            double active_top_m_required_energy_mJ,
            double continuation_required_energy_mJ,
            double new_candidate_required_energy_mJ,
            double available_energy_before_decision_mJ,
            double residual_energy_after_continuation_reservation_mJ,
            bool whole_active_top_m_affordable,
            bool all_new_candidates_affordable_after_continuation,
            bool feasible_new_candidate_subset_exists,
            double native_affordability_epsilon_mJ);

        void logSTChargeEvent(
            const std::string &event_type,
            const std::string &scheduler,
            const std::vector<SchedulerTraceJob> &blocked_jobs,
            double available_energy_mJ,
            double required_energy_mJ,
            double slack_at_begin,
            const std::string &release_reason = "");

        void logB3TimingObservation(const B3TimingObservation &observation);

        void logB3ASAPDecision(
            const std::string &scheduler,
            const std::string &blocking_policy,
            double available_energy_mJ,
            std::size_t processor_count,
            const std::vector<SchedulerTraceJob> &ready_jobs,
            const std::vector<SchedulerTraceJob> &selected_jobs,
            const std::vector<SchedulerTraceJob> &continuing_jobs,
            const std::string &decision_reason);

        void logB3ALAPDecision(
            const std::string &scheduler,
            const std::string &blocking_policy,
            double available_energy_mJ,
            std::size_t processor_count,
            const std::vector<SchedulerTraceJob> &ready_jobs,
            const std::vector<SchedulerTraceJob> &urgent_jobs,
            const std::vector<SchedulerTraceJob> &selected_jobs,
            const std::vector<SchedulerTraceJob> &continuing_jobs,
            const std::string &decision_reason);

        void logB3STDecision(
            const std::string &scheduler,
            const std::string &blocking_policy,
            double available_energy_mJ,
            double maximum_energy_mJ,
            std::size_t processor_count,
            const std::vector<SchedulerTraceJob> &ready_jobs,
            const std::vector<SchedulerTraceJob> &selected_jobs,
            const std::vector<SchedulerTraceJob> &continuing_jobs,
            const std::vector<SchedulerTraceJob> &timing_wait_jobs,
            const std::string &decision_reason);

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
            beginEvent();
            fd << "\"time\": \"" << MetaSim::SIMUL.getTime() << "\", ";
            fd << "\"event_type\": \"generic_event\", ";
            fd << "\"description\": \"" << escapeJson(e.toString())
               << "\"}";
        }
    };
} // namespace RTSim

#endif
