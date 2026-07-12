#ifndef RTSIM_ST_ENERGY_UTILS_HPP
#define RTSIM_ST_ENERGY_UTILS_HPP

#include <cmath>
#include <cstdint>
#include <limits>
#include <string>

namespace RTSim::STEnergy {

inline constexpr double kEnergyEpsilonJ = 1e-9;

inline bool isBatteryFull(double current_energy_j, double capacity_j) {
    return std::isfinite(current_energy_j) &&
           std::isfinite(capacity_j) &&
           capacity_j >= 0.0 &&
           current_energy_j + kEnergyEpsilonJ >= capacity_j;
}

inline std::string chargingReleaseReason(double current_energy_j,
                                         double capacity_j,
                                         bool slack_exhausted) {
    const bool battery_full = isBatteryFull(current_energy_j, capacity_j);
    if (battery_full && slack_exhausted) {
        return "battery_full_and_slack_exhausted";
    }
    if (battery_full) {
        return "battery_full";
    }
    if (slack_exhausted) {
        return "slack_exhausted";
    }
    return {};
}

// Convert a physical charging rate in W (J/s) into an integral millisecond
// delay. The saturated value lets callers safely cap the result by slack.
inline std::int64_t estimateChargeTimeMs(double energy_needed_j,
                                         double harvest_rate_w) {
    constexpr std::int64_t kSaturatedDelay =
        std::numeric_limits<std::int64_t>::max() / 4;

    if (!std::isfinite(energy_needed_j) ||
        !std::isfinite(harvest_rate_w)) {
        return kSaturatedDelay;
    }
    if (energy_needed_j <= kEnergyEpsilonJ) {
        return 0;
    }
    if (harvest_rate_w <= 0.0) {
        return kSaturatedDelay;
    }

    const long double rate_j_per_ms =
        static_cast<long double>(harvest_rate_w) * 0.001L;
    const long double delay_ms =
        static_cast<long double>(energy_needed_j) / rate_j_per_ms;
    if (!std::isfinite(delay_ms) ||
        delay_ms >= static_cast<long double>(kSaturatedDelay)) {
        return kSaturatedDelay;
    }

    // Avoid turning an exact physical integral duration (for example
    // 0.1 J / 0.1 W = 1000 ms) into N+1 because the decimal input was
    // represented a few ulps above the integer.
    const long double magnitude = std::fabs(delay_ms);
    const long double rounding_tolerance =
        1e-12L * (magnitude > 1.0L ? magnitude : 1.0L);
    const auto rounded = static_cast<std::int64_t>(
        std::ceil(delay_ms - rounding_tolerance));
    return rounded < 1 ? 1 : rounded;
}

} // namespace RTSim::STEnergy

#endif // RTSIM_ST_ENERGY_UTILS_HPP
