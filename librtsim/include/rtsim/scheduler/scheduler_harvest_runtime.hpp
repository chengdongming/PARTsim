#ifndef RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP
#define RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP

#include <cstdint>
#include <memory>
#include <string>

#include <rtsim/harvesting/harvest_runtime.hpp>
#include <rtsim/harvesting/harvest_types.hpp>

namespace RTSim {

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

    class SchedulerHarvestRuntime final {
    public:
        void beginRun(const HarvestSourceConfig &config);

        HarvestResult applyAtDecisionTime(
            std::int64_t decision_time_ms,
            double battery_before_j,
            double battery_capacity_j);

        bool isInitialized() const noexcept;
        const HarvestRuntime &runtime() const;
        const EnergySummary &energySummary() const noexcept {
            return _energy_summary;
        }
        const EnergySummary &finalizeEnergySummary(
            std::uint64_t expected_horizon_ms) const;

    private:
        void resetEnergySummary() noexcept;
        void observeInitialBattery(double battery_initial_j,
                                   double battery_capacity_j) noexcept;
        void observeEnergyInterval(const HarvestResult &result,
                                   double battery_capacity_j) noexcept;
        void recordEnergySummaryError(const std::string &message) noexcept;

        std::unique_ptr<HarvestRuntime> _runtime;
        EnergySummary _energy_summary;
        bool _energy_initial_observed = false;
        double _energy_initial_j = 0.0;
        double _energy_capacity_j = 0.0;
        double _last_post_harvest_battery_j = 0.0;
        bool _energy_summary_invalid = false;
        std::string _energy_summary_error;
    };

} // namespace RTSim

#endif // RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP
