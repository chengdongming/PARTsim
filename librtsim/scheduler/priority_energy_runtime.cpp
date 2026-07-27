#include <rtsim/scheduler/priority_energy_runtime.hpp>

#include <algorithm>
#include <cmath>
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>

namespace RTSim {
    namespace {
        constexpr const char *profile_id = "b4_pe_three_stage_v1";
        constexpr std::uint64_t horizon_ms = 30000;
        constexpr std::uint64_t tick_ms = 1;

        [[noreturn]] void configError(const std::string &field,
                                      const std::string &reason) {
            const std::string prefix = field.empty()
                                           ? "priority_energy"
                                           : "priority_energy." + field;
            throw PriorityEnergyConfigError(prefix + ": " + reason);
        }

        void validateBatteryInput(double battery_before_j,
                                  double battery_capacity_j) {
            if (!std::isfinite(battery_before_j)) {
                throw PriorityEnergyRuntimeError(
                    "battery_before_j must be finite");
            }
            if (!std::isfinite(battery_capacity_j)) {
                throw PriorityEnergyRuntimeError(
                    "battery_capacity_j must be finite");
            }
            if (battery_capacity_j < 0.0) {
                throw PriorityEnergyRuntimeError(
                    "battery_capacity_j must be non-negative");
            }
            if (battery_before_j < 0.0 ||
                battery_before_j > battery_capacity_j) {
                throw PriorityEnergyRuntimeError(
                    "battery_before_j must be within [0, battery_capacity_j]");
            }
        }
    } // namespace

    void validatePriorityEnergyProfileConfig(
        const PriorityEnergyProfileConfig &config) {
        if (!std::isfinite(config.alpha_w)) {
            configError("alpha_w", "must be a finite double");
        }
        if (!config.enabled) {
            return;
        }
        if (config.profile_id != profile_id) {
            configError("profile_id", "must equal b4_pe_three_stage_v1");
        }
        if (config.alpha_w < 0.0) {
            configError("alpha_w", "must be non-negative");
        }
        if (config.horizon_ms != horizon_ms) {
            configError("horizon_ms", "must equal 30000");
        }
        if (config.tick_ms != tick_ms) {
            configError("tick_ms", "must equal 1");
        }
    }

    PriorityEnergyProfileConfig
        loadPriorityEnergyProfileConfig(const std::string &system_config_file) {
        EnergyBridge::ensureConfigCallbackRegistered();
        ConfigManager parsed;
        if (!parsed.loadSystemConfig(system_config_file)) {
            configError(
                {},
                parsed.getLastConfigError().empty()
                    ? "strict PyYAML configuration load failed for system "
                      "YAML '" +
                          system_config_file + "'"
                    : parsed.getLastConfigError());
        }
        const PriorityEnergyProfileConfig config =
            parsed.getPriorityEnergyProfileConfig();
        validatePriorityEnergyProfileConfig(config);
        return config;
    }

    PriorityEnergyRuntime::PriorityEnergyRuntime(
        const PriorityEnergyProfileConfig &config) :
        _config(config) {
        validatePriorityEnergyProfileConfig(_config);
    }

    bool PriorityEnergyRuntime::enabled() const noexcept {
        return _config.enabled;
    }

    double PriorityEnergyRuntime::offeredEnergyForDecisionTime(
        std::uint64_t decision_time_ms) const noexcept {
        if (!_config.enabled || decision_time_ms == 0 ||
            decision_time_ms > horizon_ms) {
            return 0.0;
        }

        const std::uint64_t interval_index = decision_time_ms - 1;
        const double segment_multiplier =
            interval_index < 5000 || interval_index >= 15000 ? 1.0 : 0.2;
        return (_config.alpha_w * segment_multiplier) * 0.001;
    }

    PriorityEnergyHarvestStep PriorityEnergyRuntime::applyHarvest(
        std::uint64_t decision_time_ms,
        double battery_before_j,
        double battery_capacity_j) const {
        validateBatteryInput(battery_before_j, battery_capacity_j);

        const double offered =
            offeredEnergyForDecisionTime(decision_time_ms);
        if (offered == 0.0) {
            return {0.0, 0.0, 0.0, battery_before_j};
        }

        const double available_space =
            std::max(0.0, battery_capacity_j - battery_before_j);
        double actual = std::min(offered, available_space);
        double after = battery_before_j + actual;
        while (actual > 0.0 && after > battery_capacity_j) {
            actual = std::nextafter(actual, 0.0);
            after = battery_before_j + actual;
        }
        const double clipped = offered - actual;
        const double reconciled = actual + clipped;
        const double reconciliation_scale =
            std::max({1.0, std::abs(reconciled), std::abs(offered)});

        if (actual < 0.0 || clipped < 0.0 || after < 0.0 ||
            after > battery_capacity_j ||
            std::abs(reconciled - offered) >
                1e-12 * reconciliation_scale) {
            throw std::logic_error(
                "priority-energy harvest invariant violation");
        }

        return {offered, actual, clipped, after};
    }

} // namespace RTSim
