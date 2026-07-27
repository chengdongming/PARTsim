#ifndef RTSIM_OBSERVABILITY_SUMMARY_HPP
#define RTSIM_OBSERVABILITY_SUMMARY_HPP

#include <cstddef>
#include <cstdint>
#include <string>

namespace RTSim {

    constexpr int B4_OBSERVABILITY_TRACE_SCHEMA_VERSION = 3;
    constexpr int B4_OBSERVABILITY_SUMMARY_CONTRACT_VERSION = 1;
    constexpr std::size_t B4_EXPECTED_TASK_COUNT = 10;
    constexpr std::size_t B4_TOP_PRIORITY_TASK_COUNT = 4;
    constexpr std::size_t B4_BOTTOM_PRIORITY_TASK_COUNT = 6;

    struct ObservabilityTaskMetadata {
        std::string task_name;
        std::uint32_t priority_rank = 0;

        bool isTop4() const noexcept {
            return priority_rank < B4_TOP_PRIORITY_TASK_COUNT;
        }
        bool isBottom6() const noexcept {
            return priority_rank >= B4_TOP_PRIORITY_TASK_COUNT;
        }
    };

    struct MechanismSummary {
        std::uint64_t bypass_opportunity_ticks = 0;
        std::uint64_t actual_bypass_ticks = 0;
        std::uint64_t low_priority_bypass_core_ticks = 0;
        std::uint64_t hp_dispatch_demand_ticks = 0;
        std::uint64_t hp_energy_blocked_ticks = 0;
        std::uint64_t hp_energy_blocked_job_ticks = 0;
        std::uint64_t observed_decision_ticks = 0;
    };

    struct PerTaskLifecycleSummary {
        std::string task_name;
        std::uint64_t released_jobs = 0;
        std::uint64_t completed_jobs = 0;
        std::uint64_t terminated_jobs = 0;
        std::uint64_t deadline_miss_jobs = 0;
        std::uint64_t unfinished_at_horizon_jobs = 0;
        std::uint64_t executed_core_ticks = 0;
        std::uint64_t completed_response_time_count = 0;
        std::uint64_t completed_response_time_sum_ms = 0;
        std::uint64_t completed_response_time_max_ms = 0;
    };

    struct EnergySummary {
        double offered_energy_j = 0.0;
        double credited_energy_j = 0.0;
        double clipped_energy_j = 0.0;
        double consumed_energy_j = 0.0;
        double battery_min_j = 0.0;
        double battery_max_j = 0.0;
        double battery_final_j = 0.0;
        std::uint64_t battery_empty_ticks = 0;
        std::uint64_t battery_full_ticks = 0;
        std::uint64_t observed_energy_intervals = 0;
    };

    struct B4ObservabilityEnergySnapshot {
        EnergySummary summary;
        double initial_energy_j = 0.0;
        double capacity_j = 0.0;
        std::uint64_t horizon_ms = 0;
    };

} // namespace RTSim

#endif // RTSIM_OBSERVABILITY_SUMMARY_HPP
