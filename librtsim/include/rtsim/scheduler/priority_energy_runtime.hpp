#ifndef RTSIM_PRIORITY_ENERGY_RUNTIME_HPP
#define RTSIM_PRIORITY_ENERGY_RUNTIME_HPP

#include <cstdint>
#include <stdexcept>
#include <string>

namespace RTSim {

    struct PriorityEnergyProfileConfig {
        bool enabled = false;
        std::string profile_id;
        double alpha_w = 0.0;
        std::uint64_t horizon_ms = 30000;
        std::uint64_t tick_ms = 1;
    };

    struct PriorityEnergyHarvestStep {
        double offered_j = 0.0;
        double actual_j = 0.0;
        double clipped_j = 0.0;
        double battery_after_j = 0.0;
    };

    class PriorityEnergyConfigError : public std::invalid_argument {
    public:
        explicit PriorityEnergyConfigError(const std::string &message) :
            std::invalid_argument(message) {}
    };

    class PriorityEnergyRuntimeError : public std::invalid_argument {
    public:
        explicit PriorityEnergyRuntimeError(const std::string &message) :
            std::invalid_argument(message) {}
    };

    PriorityEnergyProfileConfig
        loadPriorityEnergyProfileConfig(const std::string &system_config_file);

    void validatePriorityEnergyProfileConfig(
        const PriorityEnergyProfileConfig &config);

    class PriorityEnergyRuntime {
    public:
        explicit PriorityEnergyRuntime(
            const PriorityEnergyProfileConfig &config);

        bool enabled() const noexcept;

        double offeredEnergyForDecisionTime(
            std::uint64_t decision_time_ms) const noexcept;

        PriorityEnergyHarvestStep applyHarvest(
            std::uint64_t decision_time_ms,
            double battery_before_j,
            double battery_capacity_j) const;

    private:
        PriorityEnergyProfileConfig _config;
    };

} // namespace RTSim

#endif // RTSIM_PRIORITY_ENERGY_RUNTIME_HPP
