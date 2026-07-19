#include <rtsim/json_trace.hpp>
#include <iomanip>
#include <cmath>
#include <limits>
#include <sstream>

namespace RTSim {

    using namespace MetaSim;

    static std::string exactDoubleString(double value) {
        std::ostringstream out;
        out << std::setprecision(std::numeric_limits<double>::max_digits10)
            << value;
        return out.str();
    }

    JSONTrace::JSONTrace(const string &name) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : [" << std::endl;
        first_event = true;
        max_time = MetaSim::Tick(-1);
        _observed_simulation_end = MetaSim::Tick(-1);
        _simulation_completed = false;
        _simulation_completion_reason = "not_completed";
        _run_generation = std::numeric_limits<std::uint64_t>::max();
        _energy_provider = nullptr;
        _semantic_trace_enabled = false;
    }

    JSONTrace::JSONTrace(const string &name, MetaSim::Tick max) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : [" << std::endl;
        first_event = true;
        max_time = max;
        _observed_simulation_end = MetaSim::Tick(-1);
        _simulation_completed = false;
        _simulation_completion_reason = "not_completed";
        _run_generation = std::numeric_limits<std::uint64_t>::max();
        _energy_provider = nullptr;
        _semantic_trace_enabled = false;
    }

    // V98修复：显式清空容器，避免析构顺序问题
    JSONTrace::~JSONTrace() {
        fd << "]," << std::endl;
        fd << "    \"trace_schema_version\": " << TRACE_SCHEMA_VERSION
           << "," << std::endl;
        fd << "    \"run_count\": " << _run_generations_seen.size()
           << "," << std::endl;
        fd << "    \"target_run_generation\": " << _run_generation
           << "," << std::endl;
        fd << "    \"run_generation\": " << _run_generation << ","
           << std::endl;
        fd << "    \"run_id\": \"" << escapeJson(_run_id) << "\","
           << std::endl;
        fd << "    \"taskset_semantic_hash\": \""
           << escapeJson(_taskset_semantic_hash) << "\"," << std::endl;
        fd << "    \"configured_scheduler\": \""
           << escapeJson(_configured_scheduler) << "\"," << std::endl;
        fd << "    \"scheduler_display_name\": \""
           << escapeJson(_scheduler_display_name) << "\"," << std::endl;
        fd << "    \"scheduler_implementation\": \""
           << escapeJson(_scheduler_implementation) << "\"," << std::endl;
        fd << "    \"scheduler_rtti_name\": \""
           << escapeJson(_scheduler_rtti_name) << "\"," << std::endl;
        fd << "    \"expected_simulation_horizon_ms\": "
           << max_time << "," << std::endl;
        fd << "    \"observed_simulation_end_ms\": "
           << _observed_simulation_end << "," << std::endl;
        fd << "    \"simulation_completed\": "
           << (_simulation_completed ? "true" : "false") << ","
           << std::endl;
        fd << "    \"simulation_completion_reason\": \""
           << escapeJson(_simulation_completion_reason) << "\"" << std::endl;
        fd << "}" << std::endl;

        // 先清空所有容器
        _task_start_times.clear();
        _task_start_consumed.clear();
        _deadline_missed_tasks.clear();
        _logged_deadline_misses.clear();
        _pending_forced_dline_miss.clear();

        fd.close();
    }

    // ⭐ 写入全局能量信息
    void JSONTrace::writeEnergyInfo() {
        if (_energy_provider) {
            fd << ", \"current_energy_mJ\": " << (_energy_provider->getCurrentEnergy() * 1000.0);
            fd << ", \"total_consumed_mJ\": " << (_energy_provider->getTotalEnergyConsumed() * 1000.0);
            fd << ", \"total_harvested_mJ\": " << (_energy_provider->getTotalEnergyHarvested() * 1000.0);
        }
    }

    // ⭐ 写入任务能量信息
    void JSONTrace::writeTaskEnergyInfo(AbsRTTask *task) {
        if (_energy_provider && task) {
            fd << ", \"task_unit_energy_mJ\": " << (_energy_provider->getTaskUnitEnergy(task) * 1000.0);
            fd << ", \"task_total_energy_mJ\": " << (_energy_provider->getTaskTotalEnergy(task) * 1000.0);
        }
    }

    void JSONTrace::writeDeadlineMissNow(Task &tt,
                                         MetaSim::Tick release_time,
                                         MetaSim::Tick absolute_deadline,
                                         const std::string &reason) {
        ensureCurrentRun();
        AbsRTTask *task = dynamic_cast<AbsRTTask *>(&tt);
        if (!task) return;
        if (max_time >= 0 && SIMUL.getTime() > max_time) return;

        const double remaining_execution =
            std::max(0.0, tt.getRemainingWCET());
        // A same-tick completion/deadline ordering may invoke the deadline
        // callback after the job has reached zero remaining work.  Such a job
        // is not a miss and would violate the formal miss-payload contract.
        if (remaining_execution <= 0.0) return;

        const auto job_key = std::make_pair(
            task,
            static_cast<MetaSim::Tick::impl_t>(release_time));
        if (!_logged_deadline_misses.insert(job_key).second) return;

        _deadline_missed_tasks.insert(task);
        _task_start_times.erase(task);
        _task_start_consumed.erase(task);

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"dline_miss\", ";
        fd << "\"task_name\": \"" << escapeJson(tt.getName()) << "\", ";
        fd << "\"job_id\": \"" << escapeJson(tt.getName()) << "@"
           << release_time << "\", ";
        fd << "\"arrival_time\": \"" << release_time << "\", ";
        fd << "\"deadline\": \"" << absolute_deadline << "\", ";
        fd << "\"remaining_execution_ms\": " << remaining_execution << ", ";
        fd << "\"miss_amount\": \""
           << (SIMUL.getTime() - absolute_deadline) << "\"";
        writeEnergyInfo();
        fd << ", \"reason\": \"" << escapeJson(reason) << "\"";
        fd << "}";
    }

    // ⭐ 写入Early Abort强制注入的dline_miss事件
    void JSONTrace::writeForcedDlineMissNow(AbsRTTask *task, const std::string &reason) {
        if (!task) return;

        Task *tt = dynamic_cast<Task*>(task);
        if (!tt) return;
        writeDeadlineMissNow(
            *tt, tt->getLastArrival(), tt->getDeadline(), reason);
    }

    void JSONTrace::beginEvent() {
        ensureCurrentRun();
        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;
        fd << "{ \"run_generation\": " << _run_generation << ", ";
    }

    void JSONTrace::beginRun(std::uint64_t generation) {
        _run_generations_seen.insert(generation);
        if (_run_generation == generation) return;
        _run_generation = generation;
        _task_start_times.clear();
        _task_start_consumed.clear();
        _deadline_missed_tasks.clear();
        _logged_deadline_misses.clear();
        _pending_forced_dline_miss.clear();
        _observed_simulation_end = MetaSim::Tick(-1);
        _simulation_completed = false;
        _simulation_completion_reason = "not_completed";
    }

    void JSONTrace::ensureCurrentRun() {
        beginRun(SIMUL.getRunGeneration());
    }

    void JSONTrace::setSimulationOutcome(MetaSim::Tick end_time,
                                         bool completed,
                                         const std::string &reason) {
        ensureCurrentRun();
        _observed_simulation_end = end_time;
        _simulation_completed = completed;
        _simulation_completion_reason = reason;
        beginEvent();
        fd << "\"time\": \"" << end_time << "\", ";
        fd << "\"event_type\": \"simulation_run_outcome\", ";
        fd << "\"simulation_completed\": "
           << (completed ? "true" : "false") << ", ";
        fd << "\"simulation_completion_reason\": \""
           << escapeJson(reason) << "\"}";
    }

    std::string JSONTrace::escapeJson(const std::string &value) {
        std::ostringstream out;
        for (char c : value) {
            switch (c) {
                case '"':
                    out << "\\\"";
                    break;
                case '\\':
                    out << "\\\\";
                    break;
                case '\b':
                    out << "\\b";
                    break;
                case '\f':
                    out << "\\f";
                    break;
                case '\n':
                    out << "\\n";
                    break;
                case '\r':
                    out << "\\r";
                    break;
                case '\t':
                    out << "\\t";
                    break;
                default:
                    out << c;
                    break;
            }
        }
        return out.str();
    }

    void JSONTrace::writeSchedulerJob(const SchedulerTraceJob &job) {
        fd << "{";
        fd << "\"task_name\": \"" << escapeJson(job.task_name) << "\"";
        fd << ", \"arrival_time\": " << job.arrival_time;
        fd << ", \"priority\": " << job.priority;
        fd << ", \"ready_order\": " << job.ready_order;
        fd << ", \"task_unit_energy_mJ\": " << job.task_unit_energy_mJ;
        fd << ", \"task_unit_energy_mJ_exact\": \""
           << exactDoubleString(job.task_unit_energy_mJ) << "\"";
        fd << ", \"remaining_time_ms\": " << job.remaining_time_ms;
        fd << ", \"absolute_deadline\": " << job.absolute_deadline;
        fd << "}";
    }

    void JSONTrace::writeSchedulerJobArray(const std::vector<SchedulerTraceJob> &jobs) {
        fd << "[";
        for (std::size_t i = 0; i < jobs.size(); ++i) {
            if (i > 0) {
                fd << ", ";
            }
            writeSchedulerJob(jobs[i]);
        }
        fd << "]";
    }

    void JSONTrace::logSchedulerDecision(
        const std::string &scheduler,
        double available_energy_mJ,
        const std::vector<SchedulerTraceJob> &ready_jobs,
        const std::vector<SchedulerTraceJob> &selected_jobs,
        const std::string &decision_reason) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"scheduler_decision\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        fd << "\"available_energy_mJ\": " << available_energy_mJ << ", ";
        fd << "\"available_energy_mJ_exact\": \""
           << exactDoubleString(available_energy_mJ) << "\", ";
        fd << "\"ready_jobs\": ";
        writeSchedulerJobArray(ready_jobs);
        fd << ", \"selected_jobs\": ";
        writeSchedulerJobArray(selected_jobs);
        fd << ", \"decision_reason\": \"" << escapeJson(decision_reason) << "\"";
        fd << "}";
    }

    void JSONTrace::logEnergyBlock(
        const std::string &scheduler,
        const SchedulerTraceJob &blocked_task,
        double available_energy_mJ) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"energy_block\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        fd << "\"blocked_task\": \"" << escapeJson(blocked_task.task_name) << "\", ";
        fd << "\"blocked_task_unit_energy_mJ\": "
           << blocked_task.task_unit_energy_mJ << ", ";
        fd << "\"available_energy_mJ\": " << available_energy_mJ << ", ";
        fd << "\"reason\": \"highest_priority_energy_insufficient\"";
        fd << "}";
    }

    void JSONTrace::logNonBlockBypass(
        const std::string &scheduler,
        const SchedulerTraceJob &blocked_higher_priority_task,
        const SchedulerTraceJob &bypassed_task,
        double available_energy_mJ) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"nonblock_bypass\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        fd << "\"blocked_higher_priority_task\": \""
           << escapeJson(blocked_higher_priority_task.task_name) << "\", ";
        fd << "\"bypassed_task\": \"" << escapeJson(bypassed_task.task_name) << "\", ";
        fd << "\"blocked_task_unit_energy_mJ\": "
           << blocked_higher_priority_task.task_unit_energy_mJ << ", ";
        fd << "\"bypassed_task_unit_energy_mJ\": "
           << bypassed_task.task_unit_energy_mJ << ", ";
        fd << "\"available_energy_mJ\": " << available_energy_mJ << ", ";
        fd << "\"reason\": \"lower_priority_bypass_due_to_energy\"";
        fd << "}";
    }

    void JSONTrace::logSyncBatchBlock(
        const std::string &scheduler,
        const std::vector<SchedulerTraceJob> &batch_tasks,
        double batch_required_energy_mJ,
        double available_energy_mJ,
        bool feasible_subset_exists) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"sync_batch_block\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        fd << "\"batch_tasks\": ";
        writeSchedulerJobArray(batch_tasks);
        fd << ", \"batch_required_energy_mJ\": " << batch_required_energy_mJ;
        fd << ", \"batch_required_energy_mJ_exact\": \""
           << exactDoubleString(batch_required_energy_mJ) << "\"";
        fd << ", \"available_energy_mJ\": " << available_energy_mJ;
        fd << ", \"available_energy_mJ_exact\": \""
           << exactDoubleString(available_energy_mJ) << "\"";
        fd << ", \"feasible_subset_exists\": "
           << (feasible_subset_exists ? "true" : "false");
        fd << ", \"reason\": \"sync_batch_energy_insufficient\"";
        fd << "}";
    }

    void JSONTrace::logSyncBatchCandidateWait(
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
        double native_affordability_epsilon_mJ) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"sync_batch_candidate_wait\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        fd << "\"reason\": "
           << "\"continuation_preserved_new_candidate_batch_energy_insufficient\", ";
        fd << "\"active_top_m_tasks\": ";
        writeSchedulerJobArray(active_top_m_tasks);
        fd << ", \"continuation_tasks\": ";
        writeSchedulerJobArray(continuation_tasks);
        fd << ", \"new_candidate_tasks\": ";
        writeSchedulerJobArray(new_candidate_tasks);
        fd << ", \"selected_tasks\": ";
        writeSchedulerJobArray(selected_tasks);
        fd << ", \"active_top_m_count\": " << active_top_m_tasks.size();
        fd << ", \"continuation_count\": " << continuation_tasks.size();
        fd << ", \"new_candidate_count\": " << new_candidate_tasks.size();
        fd << ", \"selected_count\": " << selected_tasks.size();
        fd << ", \"active_top_m_required_energy_mJ\": "
           << active_top_m_required_energy_mJ;
        fd << ", \"active_top_m_required_energy_mJ_exact\": \""
           << exactDoubleString(active_top_m_required_energy_mJ) << "\"";
        fd << ", \"continuation_required_energy_mJ\": "
           << continuation_required_energy_mJ;
        fd << ", \"continuation_required_energy_mJ_exact\": \""
           << exactDoubleString(continuation_required_energy_mJ) << "\"";
        fd << ", \"new_candidate_required_energy_mJ\": "
           << new_candidate_required_energy_mJ;
        fd << ", \"new_candidate_required_energy_mJ_exact\": \""
           << exactDoubleString(new_candidate_required_energy_mJ) << "\"";
        fd << ", \"available_energy_before_decision_mJ\": "
           << available_energy_before_decision_mJ;
        fd << ", \"available_energy_before_decision_mJ_exact\": \""
           << exactDoubleString(available_energy_before_decision_mJ) << "\"";
        fd << ", \"residual_energy_after_continuation_reservation_mJ\": "
           << residual_energy_after_continuation_reservation_mJ;
        fd << ", \"residual_energy_after_continuation_reservation_mJ_exact\": \""
           << exactDoubleString(
                  residual_energy_after_continuation_reservation_mJ)
           << "\"";
        fd << ", \"whole_active_top_m_affordable\": "
           << (whole_active_top_m_affordable ? "true" : "false");
        fd << ", \"all_new_candidates_affordable_after_continuation\": "
           << (all_new_candidates_affordable_after_continuation ? "true" : "false");
        fd << ", \"feasible_new_candidate_subset_exists\": "
           << (feasible_new_candidate_subset_exists ? "true" : "false");
        fd << ", \"native_affordability_epsilon_mJ\": "
           << native_affordability_epsilon_mJ;
        fd << ", \"native_affordability_epsilon_mJ_exact\": \""
           << exactDoubleString(native_affordability_epsilon_mJ) << "\"";
        fd << "}";
    }

    void JSONTrace::logSTChargeEvent(
        const std::string &event_type,
        const std::string &scheduler,
        const std::vector<SchedulerTraceJob> &blocked_jobs,
        double available_energy_mJ,
        double required_energy_mJ,
        double slack_at_begin,
        const std::string &release_reason) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"" << escapeJson(event_type) << "\", ";
        fd << "\"scheduler\": \"" << escapeJson(scheduler) << "\", ";
        if (blocked_jobs.size() == 1) {
            fd << "\"blocked_task\": \""
               << escapeJson(blocked_jobs.front().task_name) << "\", ";
        } else {
            fd << "\"blocked_group\": ";
            writeSchedulerJobArray(blocked_jobs);
            fd << ", ";
        }
        fd << "\"available_energy_mJ\": " << available_energy_mJ << ", ";
        fd << "\"required_energy_mJ\": " << required_energy_mJ << ", ";
        fd << "\"slack_at_begin\": " << slack_at_begin;
        if (!release_reason.empty()) {
            fd << ", \"release_reason\": \""
               << escapeJson(release_reason) << "\"";
        }
        fd << "}";
    }

    void JSONTrace::logB3TimingObservation(
        const B3TimingObservation &observation) {
        if (!_semantic_trace_enabled ||
            (max_time >= 0 && SIMUL.getTime() >= max_time)) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"b3_timing_observation\", ";
        fd << "\"scheduler\": \""
           << escapeJson(observation.scheduler) << "\", ";
        fd << "\"scheduler_family\": \""
           << escapeJson(observation.scheduler_family) << "\", ";
        fd << "\"blocking_policy\": \""
           << escapeJson(observation.blocking_policy) << "\", ";
        fd << "\"task_name\": \""
           << escapeJson(observation.task_name) << "\", ";
        fd << "\"task_id\": \""
           << escapeJson(observation.task_id) << "\", ";
        fd << "\"arrival_time\": " << observation.arrival_time << ", ";
        fd << "\"job_id\": \""
           << escapeJson(observation.job_id) << "\", ";
        fd << "\"remaining_time_ms\": "
           << observation.remaining_time_ms << ", ";
        fd << "\"rounded_remaining_ms\": "
           << observation.rounded_remaining_ms << ", ";
        fd << "\"absolute_deadline\": "
           << observation.absolute_deadline << ", ";
        fd << "\"scheduler_slack\": "
           << observation.scheduler_slack << ", ";
        fd << "\"ready\": " << (observation.ready ? "true" : "false")
           << ", ";
        fd << "\"timing_gate_open\": "
           << (observation.timing_gate_open ? "true" : "false") << ", ";
        fd << "\"cpu_available\": "
           << (observation.cpu_available ? "true" : "false") << ", ";
        fd << "\"continuation\": "
           << (observation.continuation ? "true" : "false") << ", ";
        fd << "\"selected\": "
           << (observation.selected ? "true" : "false") << ", ";
        fd << "\"job_required_energy_mJ\": "
           << observation.job_required_energy_mJ << ", ";
        fd << "\"decision_required_energy_mJ\": "
           << observation.decision_required_energy_mJ << ", ";
        fd << "\"available_energy_mJ\": "
           << observation.available_energy_mJ << ", ";
        fd << "\"job_energy_affordable\": "
           << (observation.job_energy_affordable ? "true" : "false") << ", ";
        fd << "\"decision_energy_affordable\": "
           << (observation.decision_energy_affordable ? "true" : "false")
           << ", ";
        fd << "\"native_epsilon_mJ\": "
           << observation.native_epsilon_mJ << ", ";
        fd << "\"blocking_policy_reason\": \""
           << escapeJson(observation.blocking_policy_reason) << "\", ";
        fd << "\"actual_outcome\": \""
           << escapeJson(observation.actual_outcome) << "\", ";
        fd << "\"reason_code\": \""
           << escapeJson(observation.reason_code) << "\"";
        fd << "}";
    }

    void JSONTrace::logB3ASAPDecision(
        const std::string &scheduler,
        const std::string &blocking_policy,
        double available_energy_mJ,
        std::size_t processor_count,
        const std::vector<SchedulerTraceJob> &ready_jobs,
        const std::vector<SchedulerTraceJob> &selected_jobs,
        const std::vector<SchedulerTraceJob> &continuing_jobs,
        const std::string &decision_reason) {
        if (!_semantic_trace_enabled) return;

        auto identity = [](const SchedulerTraceJob &job) {
            std::ostringstream out;
            out << job.task_name << "@" << job.arrival_time;
            return out.str();
        };
        std::set<std::string> selected;
        std::set<std::string> continuing;
        for (const auto &job : selected_jobs) selected.insert(identity(job));
        for (const auto &job : continuing_jobs) continuing.insert(identity(job));

        constexpr double epsilon_mJ = 1e-6;
        double block_prefix_mJ = 0.0;
        double sync_group_mJ = 0.0;
        double sync_continuation_mJ = 0.0;
        for (std::size_t i = 0;
             i < std::min(processor_count, ready_jobs.size()); ++i) {
            sync_group_mJ += ready_jobs[i].task_unit_energy_mJ;
            if (continuing.count(identity(ready_jobs[i])) > 0) {
                sync_continuation_mJ += ready_jobs[i].task_unit_energy_mJ;
            }
        }

        bool nonblock_block_seen = false;
        for (std::size_t i = 0; i < ready_jobs.size(); ++i) {
            const SchedulerTraceJob &job = ready_jobs[i];
            const std::string key = identity(job);
            const bool is_selected = selected.count(key) > 0;
            const bool is_continuing = continuing.count(key) > 0;
            const bool cpu_available =
                is_selected || is_continuing || i < processor_count;

            double decision_required_mJ = job.task_unit_energy_mJ;
            if (blocking_policy == "BLOCK") {
                block_prefix_mJ += job.task_unit_energy_mJ;
                decision_required_mJ = block_prefix_mJ;
            } else if (blocking_policy == "SYNC") {
                const bool partial_continuation =
                    is_continuing && selected_jobs.size() <
                        std::min(processor_count, ready_jobs.size());
                decision_required_mJ = partial_continuation
                    ? sync_continuation_mJ : sync_group_mJ;
            }

            const bool job_affordable = available_energy_mJ + epsilon_mJ >=
                job.task_unit_energy_mJ;
            const bool decision_affordable =
                available_energy_mJ + epsilon_mJ >= decision_required_mJ;

            std::string policy_reason = "NONE";
            if (!cpu_available) {
                policy_reason = "CPU_CAPACITY";
            } else if (blocking_policy == "SYNC" &&
                       decision_reason == "sync_batch_energy_insufficient" &&
                       !is_selected) {
                policy_reason = "SYNC_ATOMIC_BATCH_WAIT";
            } else if (blocking_policy == "NONBLOCK") {
                if (!is_selected && !decision_affordable) {
                    policy_reason = "ENERGY_INSUFFICIENT";
                    nonblock_block_seen = true;
                } else if (is_selected && nonblock_block_seen) {
                    policy_reason = "NONBLOCK_BYPASS";
                } else if (!is_selected) {
                    policy_reason = "HIGHER_PRIORITY";
                }
            } else if (blocking_policy == "BLOCK" && !is_selected) {
                policy_reason = decision_affordable
                    ? "HIGHER_PRIORITY" : "BLOCK_HEAD_OF_LINE";
            } else if (!is_selected) {
                policy_reason = decision_affordable
                    ? "HIGHER_PRIORITY" : "ENERGY_INSUFFICIENT";
            }

            const double rounded_remaining =
                std::ceil(job.remaining_time_ms);
            B3TimingObservation observation{
                scheduler,
                "ASAP",
                blocking_policy,
                job.task_name,
                job.task_name,
                job.arrival_time,
                key,
                job.remaining_time_ms,
                rounded_remaining,
                job.absolute_deadline,
                job.absolute_deadline - rounded_remaining -
                    static_cast<double>(SIMUL.getTime()),
                true,
                true,
                cpu_available,
                is_continuing,
                is_selected,
                job.task_unit_energy_mJ,
                decision_required_mJ,
                available_energy_mJ,
                job_affordable,
                decision_affordable,
                epsilon_mJ,
                policy_reason,
                is_selected
                    ? (is_continuing ? "CONTINUE_SELECTED"
                                     : "DISPATCH_SELECTED")
                    : "BLOCKED",
                decision_reason,
            };
            logB3TimingObservation(observation);
        }
    }

    void JSONTrace::logB3ALAPDecision(
        const std::string &scheduler,
        const std::string &blocking_policy,
        double available_energy_mJ,
        std::size_t processor_count,
        const std::vector<SchedulerTraceJob> &ready_jobs,
        const std::vector<SchedulerTraceJob> &urgent_jobs,
        const std::vector<SchedulerTraceJob> &selected_jobs,
        const std::vector<SchedulerTraceJob> &continuing_jobs,
        const std::string &decision_reason) {
        if (!_semantic_trace_enabled) return;

        auto identity = [](const SchedulerTraceJob &job) {
            std::ostringstream out;
            out << job.task_name << "@" << job.arrival_time;
            return out.str();
        };
        std::set<std::string> urgent;
        std::set<std::string> selected;
        std::set<std::string> continuing;
        for (const auto &job : urgent_jobs) urgent.insert(identity(job));
        for (const auto &job : selected_jobs) selected.insert(identity(job));
        for (const auto &job : continuing_jobs) continuing.insert(identity(job));

        constexpr double epsilon_mJ = 1e-6;
        double urgent_group_mJ = 0.0;
        for (std::size_t i = 0;
             i < std::min(processor_count, urgent_jobs.size()); ++i) {
            urgent_group_mJ += urgent_jobs[i].task_unit_energy_mJ;
        }
        double urgent_prefix_mJ = 0.0;
        bool nonblock_block_seen = false;
        for (std::size_t i = 0; i < ready_jobs.size(); ++i) {
            const SchedulerTraceJob &job = ready_jobs[i];
            const std::string key = identity(job);
            const bool timing_gate_open = urgent.count(key) > 0;
            const bool is_selected = selected.count(key) > 0;
            const bool is_continuing = continuing.count(key) > 0;
            const bool cpu_available =
                is_selected || is_continuing || i < processor_count;

            double decision_required_mJ = job.task_unit_energy_mJ;
            if (timing_gate_open && blocking_policy == "BLOCK") {
                urgent_prefix_mJ += job.task_unit_energy_mJ;
                decision_required_mJ = urgent_prefix_mJ;
            } else if (timing_gate_open && blocking_policy == "SYNC") {
                decision_required_mJ = urgent_group_mJ;
            }
            const bool job_affordable = available_energy_mJ + epsilon_mJ >=
                job.task_unit_energy_mJ;
            const bool decision_affordable =
                available_energy_mJ + epsilon_mJ >= decision_required_mJ;

            std::string policy_reason = "NONE";
            if (!cpu_available) {
                policy_reason = "CPU_CAPACITY";
            } else if (timing_gate_open && blocking_policy == "SYNC" &&
                       !is_selected && !decision_affordable) {
                policy_reason = "SYNC_ATOMIC_BATCH_WAIT";
            } else if (timing_gate_open &&
                       blocking_policy == "NONBLOCK") {
                if (!is_selected && !decision_affordable) {
                    policy_reason = "ENERGY_INSUFFICIENT";
                    nonblock_block_seen = true;
                } else if (is_selected && nonblock_block_seen) {
                    policy_reason = "NONBLOCK_BYPASS";
                } else if (!is_selected) {
                    policy_reason = "HIGHER_PRIORITY";
                }
            } else if (timing_gate_open &&
                       blocking_policy == "BLOCK" && !is_selected) {
                policy_reason = decision_affordable
                    ? "HIGHER_PRIORITY" : "BLOCK_HEAD_OF_LINE";
            } else if (timing_gate_open && !is_selected) {
                policy_reason = decision_affordable
                    ? "HIGHER_PRIORITY" : "ENERGY_INSUFFICIENT";
            } else if (!timing_gate_open && !decision_affordable) {
                policy_reason = "ENERGY_INSUFFICIENT";
            }
            const double rounded_remaining =
                std::ceil(job.remaining_time_ms);
            const std::string outcome = is_selected
                ? (is_continuing ? "CONTINUE_SELECTED"
                                 : "DISPATCH_SELECTED")
                : (!timing_gate_open && policy_reason == "NONE"
                    ? "TIMING_DEFERRED" : "BLOCKED");
            B3TimingObservation observation{
                scheduler,
                "ALAP",
                blocking_policy,
                job.task_name,
                job.task_name,
                job.arrival_time,
                key,
                job.remaining_time_ms,
                rounded_remaining,
                job.absolute_deadline,
                job.absolute_deadline - rounded_remaining -
                    static_cast<double>(SIMUL.getTime()),
                true,
                timing_gate_open,
                cpu_available,
                is_continuing,
                is_selected,
                job.task_unit_energy_mJ,
                decision_required_mJ,
                available_energy_mJ,
                job_affordable,
                decision_affordable,
                epsilon_mJ,
                policy_reason,
                outcome,
                timing_gate_open ? decision_reason
                                 : "ALAP_POSITIVE_SLACK",
            };
            logB3TimingObservation(observation);
        }
    }

    void JSONTrace::logB3STDecision(
        const std::string &scheduler,
        const std::string &blocking_policy,
        double available_energy_mJ,
        double maximum_energy_mJ,
        std::size_t processor_count,
        const std::vector<SchedulerTraceJob> &ready_jobs,
        const std::vector<SchedulerTraceJob> &selected_jobs,
        const std::vector<SchedulerTraceJob> &continuing_jobs,
        const std::vector<SchedulerTraceJob> &timing_wait_jobs,
        const std::string &decision_reason) {
        if (!_semantic_trace_enabled) return;

        auto identity = [](const SchedulerTraceJob &job) {
            std::ostringstream out;
            out << job.task_name << "@" << job.arrival_time;
            return out.str();
        };
        std::set<std::string> selected;
        std::set<std::string> continuing;
        std::set<std::string> timing_wait;
        for (const auto &job : selected_jobs) selected.insert(identity(job));
        for (const auto &job : continuing_jobs) continuing.insert(identity(job));
        for (const auto &job : timing_wait_jobs) {
            timing_wait.insert(identity(job));
        }

        constexpr double epsilon_mJ = 1e-6;
        double sync_group_mJ = 0.0;
        for (std::size_t i = 0;
             i < std::min(processor_count, ready_jobs.size()); ++i) {
            sync_group_mJ += ready_jobs[i].task_unit_energy_mJ;
        }
        double reserved_prefix_mJ = 0.0;
        bool nonblock_wait_seen = false;
        for (std::size_t i = 0; i < ready_jobs.size(); ++i) {
            const SchedulerTraceJob &job = ready_jobs[i];
            const std::string key = identity(job);
            const bool is_selected = selected.count(key) > 0;
            const bool is_continuing = continuing.count(key) > 0;
            const bool is_timing_wait = timing_wait.count(key) > 0;
            const bool cpu_available =
                is_selected || is_continuing || i < processor_count;

            double decision_required_mJ = job.task_unit_energy_mJ;
            if (is_timing_wait) {
                decision_required_mJ = maximum_energy_mJ;
            } else if (blocking_policy == "SYNC") {
                decision_required_mJ = sync_group_mJ;
            } else {
                decision_required_mJ =
                    reserved_prefix_mJ + job.task_unit_energy_mJ;
            }
            if (is_selected) {
                reserved_prefix_mJ += job.task_unit_energy_mJ;
            }

            const bool job_affordable = available_energy_mJ + epsilon_mJ >=
                job.task_unit_energy_mJ;
            const bool decision_affordable =
                available_energy_mJ + epsilon_mJ >= decision_required_mJ;
            const double rounded_remaining =
                std::ceil(job.remaining_time_ms);
            const double slack = job.absolute_deadline - rounded_remaining -
                static_cast<double>(SIMUL.getTime());
            const bool timing_gate_open = !is_timing_wait;

            bool lower_selected = false;
            if (blocking_policy == "NONBLOCK" && is_timing_wait) {
                for (std::size_t lower = i + 1;
                     lower < ready_jobs.size(); ++lower) {
                    if (selected.count(identity(ready_jobs[lower])) > 0) {
                        lower_selected = true;
                        break;
                    }
                }
            }

            std::string policy_reason = "NONE";
            if (!cpu_available) {
                policy_reason = "CPU_CAPACITY";
            } else if (is_timing_wait && blocking_policy == "SYNC" &&
                       timing_wait_jobs.size() > 1) {
                policy_reason = "SYNC_ATOMIC_BATCH_WAIT";
            } else if (is_timing_wait && blocking_policy == "NONBLOCK" &&
                       lower_selected) {
                policy_reason = "NONBLOCK_BYPASS";
                nonblock_wait_seen = true;
            } else if (is_selected && blocking_policy == "NONBLOCK" &&
                       nonblock_wait_seen) {
                policy_reason = "NONBLOCK_BYPASS";
            } else if (!is_selected && !is_timing_wait) {
                if (blocking_policy == "SYNC" && i < processor_count) {
                    policy_reason = "SYNC_ATOMIC_BATCH_WAIT";
                } else if (!decision_affordable) {
                    policy_reason = blocking_policy == "BLOCK"
                        ? "BLOCK_HEAD_OF_LINE" : "ENERGY_INSUFFICIENT";
                } else {
                    policy_reason = "HIGHER_PRIORITY";
                }
            }

            const std::string outcome = is_selected
                ? (is_continuing ? "CONTINUE_SELECTED"
                                 : "DISPATCH_SELECTED")
                : (is_timing_wait ? "TIMING_DEFERRED" : "BLOCKED");
            B3TimingObservation observation{
                scheduler,
                "ST",
                blocking_policy,
                job.task_name,
                job.task_name,
                job.arrival_time,
                key,
                job.remaining_time_ms,
                rounded_remaining,
                job.absolute_deadline,
                slack,
                true,
                timing_gate_open,
                cpu_available,
                is_continuing,
                is_selected,
                job.task_unit_energy_mJ,
                decision_required_mJ,
                available_energy_mJ,
                job_affordable,
                decision_affordable,
                epsilon_mJ,
                policy_reason,
                outcome,
                decision_reason,
            };
            logB3TimingObservation(observation);
        }
    }

    void JSONTrace::writeTaskEvent(const Task &tt,
                                   const std::string &evt_name) {
        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return; // 超过最大时间，不记录此事件
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"" << evt_name << "\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        // ⭐ 修复：使用getLastArrival()获取当前实例的到达时间，而不是第一次实例的到达时间
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 对于scheduled和end_instance事件，添加任务能量信息
        if (evt_name == "scheduled" || evt_name == "end_instance") {
            AbsRTTask *task = const_cast<AbsRTTask*>(dynamic_cast<const AbsRTTask*>(&tt));
            writeTaskEnergyInfo(task);
        }

        fd << "}";
    }

    void JSONTrace::probe(ArrEvt &e) {
        Task &tt = *(e.getTask());

        // ⭐ 修复：arrival事件应该使用当前时间作为arrival_time
        // 因为在ArrEvt触发时，task->getLastArrival()还没有更新
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"arrival\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << SIMUL.getTime() << "\"";  // 使用当前时间

        // ⭐ 添加能量信息
        writeEnergyInfo();

        fd << "}";
    }

    void JSONTrace::probe(EndEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"end_instance\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加任务能量信息
        if (task) {
            writeTaskEnergyInfo(task);

            // ⭐ 计算execution_time和task_consumed
            if (_task_start_times.find(task) != _task_start_times.end()) {
                MetaSim::Tick execution_time = SIMUL.getTime() - _task_start_times[task];
                fd << ", \"execution_time_ms\": " << execution_time;

                // ⭐ V115修复：task_consumed 必须是"该任务本身的消耗"
                // 正确公式：executed_time (ms) × task_unit_energy (mJ/ms)
                // 不能用 global_total_consumed 的差值（那包含了所有并行任务的消耗）
                if (_energy_provider) {
                    double unit_energy = _energy_provider->getTaskUnitEnergy(task);  // J/ms
                    double task_consumed = (double)execution_time * unit_energy;     // J
                    fd << ", \"task_consumed_mJ\": " << (task_consumed * 1000.0);
                }

                // 清除记录
                _task_start_times.erase(task);
                _task_start_consumed.erase(task);
            }
        }

        fd << "}";
    }

    void JSONTrace::probe(SchedEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"scheduled\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加任务能量信息
        if (task) {
            writeTaskEnergyInfo(task);

            // ⭐ 添加energy_sufficient字段
            // 注意：这里读取的是scheduled事件写出时的当前能量，
            // 因此它反映的是“post-schedule snapshot”，不是admission前判定点。
            if (_energy_provider) {
                double current_energy = _energy_provider->getCurrentEnergy();
                double task_unit_energy = _energy_provider->getTaskUnitEnergy(task);
                bool energy_sufficient_post_schedule = (current_energy >= task_unit_energy);
                fd << ", \"energy_sufficient\": " << (energy_sufficient_post_schedule ? "true" : "false");
                fd << ", \"energy_sufficient_post_schedule\": " << (energy_sufficient_post_schedule ? "true" : "false");
                fd << ", \"energy_snapshot_phase\": \"post_schedule\"";
            }

            // ⭐ 记录任务开始执行的时间和累计消耗
            _task_start_times[task] = SIMUL.getTime();
            if (_energy_provider) {
                _task_start_consumed[task] = _energy_provider->getTotalEnergyConsumed();
            }
        }

        fd << "}";
    }

    void JSONTrace::probe(DeschedEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        // ⭐ V83修复：跳过已经deadline miss的任务
        // 当任务已经触发deadline miss时，不应该再记录descheduled事件
        // 因为任务已经被标记为死亡，descheduled事件在逻辑上是错误的
        if (task && _deadline_missed_tasks.find(task) != _deadline_missed_tasks.end()) {
            // 从集合中移除（因为kill事件会紧随其后）
            _deadline_missed_tasks.erase(task);
            return;
        }

        beginEvent();
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"descheduled\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加descheduled特定信息
        if (task) {
            // 计算已执行时间
            if (_task_start_times.find(task) != _task_start_times.end()) {
                MetaSim::Tick executed_time = SIMUL.getTime() - _task_start_times[task];
                fd << ", \"executed_time_ms\": " << executed_time;

                // ⭐ 修复：基于执行时间计算部分消耗能量，而不���能量差值
                // 原因：能量差值会排除调度时预扣的初始能量（在scheduled事件中扣除的1ms）
                //       导致partial_consumed记录不准确
                // 示例：任务执行10ms，单位能耗0.42mJ/ms
                //       - 能量差值法：4.2 - 0.42 = 3.78mJ (错误！缺少1ms的能量)
                //       - 执行时间法：10 * 0.42 = 4.2mJ (正确！)
                if (_energy_provider) {
                    double task_unit_energy = _energy_provider->getTaskUnitEnergy(task);
                    double partial_consumed = double(executed_time) * task_unit_energy;
                    fd << ", \"partial_consumed_mJ\": " << (partial_consumed * 1000.0);
                }

                // 尝试获取WCET并计算剩余时间
                // 注意：这里需要从任务模型获取WCET，但我们没有直接访问权限
                // 所以这个字段可能需要调度器提供额外接口
            }

            // ⭐ V115修复：使用调度器记录的真正挂起原因，消灭"幽灵抢占"
            if (_energy_provider) {
                std::string suspend_reason = _energy_provider->getSuspendReason(task);

                if (suspend_reason == "energy_depleted" || suspend_reason == "insufficient_energy" ||
                    suspend_reason == "early_abort_energy_depleted") {
                    fd << ", \"preempted_by\": \"energy_insufficient\"";
                    fd << ", \"reason\": \"insufficient_energy\"";
                } else if (suspend_reason == "preemption") {
                    fd << ", \"preempted_by\": \"higher_priority_task\"";
                    fd << ", \"reason\": \"preemption\"";
                } else {
                    fd << ", \"preempted_by\": \"higher_priority_task\"";
                    fd << ", \"reason\": \"preemption\"";
                }

                // 清除挂起原因记录
                _energy_provider->clearSuspendReason(task);
            }

            // 清除记录（任务被下处理机）
            _task_start_times.erase(task);
            _task_start_consumed.erase(task);

            auto pending_it = _pending_forced_dline_miss.find(task);
            if (pending_it != _pending_forced_dline_miss.end()) {
                fd << "}";
                writeForcedDlineMissNow(task, pending_it->second);
                _pending_forced_dline_miss.erase(pending_it);
                return;
            }
        }

        fd << "}";
    }

    void JSONTrace::probe(DeadEvt &e) {
        ensureCurrentRun();
        Task &tt = *(e.getTask());
        const MetaSim::Tick absolute_deadline = tt.getDeadline();
        if (!tt.isActive() || SIMUL.getTime() < absolute_deadline) return;

        std::string reason = "unknown";
        if (_energy_provider) {
            reason = _energy_provider->getCurrentEnergy() < 0.001
                ? "energy_depleted"
                : "insufficient_time";
        }
        writeDeadlineMissNow(
            tt, tt.getArrival(), absolute_deadline, reason);
    }

    void JSONTrace::probe(KillEvt &e) {
        ensureCurrentRun();
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // ⭐ V109修复：在任务被kill时，清理所有相关的指针
        // 避免在JSONTrace析构时访问无效指针导致崩溃
        if (task) {
            _task_start_times.erase(task);
            _task_start_consumed.erase(task);
            _deadline_missed_tasks.erase(task);
            _pending_forced_dline_miss.erase(task);
        }

        writeTaskEvent(tt, "kill");
    }

    void JSONTrace::attachToTask(AbsRTTask &t) {
        // new Particle<ArrEvt, JSONTrace>(&t->arrEvt, this);
        // new Particle<EndEvt, JSONTrace>(&t->endEvt, this);
        // new Particle<SchedEvt, JSONTrace>(&t->schedEvt, this);
        // new Particle<DeschedEvt, JSONTrace>(&t->deschedEvt, this);
        // new Particle<DeadEvt, JSONTrace>(&t->deadEvt, this);

        Task &tt = dynamic_cast<Task &>(t);
        attach_stat(*this, tt.arrEvt);
        attach_stat(*this, tt.endEvt);
        attach_stat(*this, tt.schedEvt);
        attach_stat(*this, tt.deschedEvt);
        attach_stat(*this, tt.deadEvt);
        attach_stat(*this, tt.killEvt);
    }

    // ⭐ V58新增：强制记录dline_miss事件（用于Early Abort场景）
    // 当调度器因能量耗尽等原因主动kill任务时，DeadEvt可能不会被触发
    void JSONTrace::forceLogDlineMiss(AbsRTTask *task, const std::string &reason) {
        writeForcedDlineMissNow(task, reason);
    }

    void JSONTrace::forceLogDlineMissAfterDesched(AbsRTTask *task, const std::string &reason) {
        if (!task) return;

        // 如果任务还有正在结算的执行片段，则延迟到descheduled之后再补记
        if (_task_start_times.find(task) != _task_start_times.end()) {
            _pending_forced_dline_miss[task] = reason;
            return;
        }

        writeForcedDlineMissNow(task, reason);
    }
} // namespace RTSim
