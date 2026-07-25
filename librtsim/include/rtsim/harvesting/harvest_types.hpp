#ifndef RTSIM_HARVEST_TYPES_HPP
#define RTSIM_HARVEST_TYPES_HPP

#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace RTSim {

    enum class HarvestSourceKind {
        LegacySolar,
        ScaledPiecewise,
        SampledTrace,
    };

    // Stable identity for one left-closed, right-open physical interval:
    // [start_time_ms, end_time_ms), with start < end. index is the monotonic
    // interval identity in the battery domain. Validation and the applied-
    // interval ledger belong to the future HarvestRuntime, not to this
    // data-only type.
    struct HarvestInterval {
        std::uint64_t index = 0;
        std::uint64_t start_time_ms = 0;
        std::uint64_t end_time_ms = 0;
    };

    // Energy values are joules. offered_j is source- and interval-only;
    // actual_j is the energy written to the battery, and clipped_j is the
    // capacity-limited remainder. This type does not update a battery.
    struct HarvestResult {
        double offered_j = 0.0;
        double actual_j = 0.0;
        double clipped_j = 0.0;
        double battery_before_j = 0.0;
        double battery_after_j = 0.0;
    };

    // Power is watts and time is milliseconds. Recovery policy is
    // intentionally excluded because it is not an external energy source.
    struct LegacySolarConfig {
        double base_harvesting_power_w = 0.054;
        std::uint64_t start_offset_ms = 0;
        bool use_real_solar_data = false;
        std::string solar_data_file =
            "data/processed/shenyang_solar_minute.csv";
        double pv_efficiency = 0.18;
        double pv_area_m2 = 1.0;
    };

    struct PiecewiseSegment {
        std::uint64_t start_time_ms = 0;
        std::uint64_t end_time_ms = 0;
        // Strict configuration parsing canonicalizes both signed zeros to
        // +0.0 so physically identical inputs have one binary64 identity.
        double multiplier = 0.0;
    };

    struct ScaledPiecewiseConfig {
        // Strict configuration parsing canonicalizes both signed zeros to
        // +0.0 so physically identical inputs have one binary64 identity.
        double scale_w = 0.0;
        std::vector<PiecewiseSegment> segments;
    };

    enum class TraceValueType {
        ElectricalPower,
        Irradiance,
    };

    enum class TraceInterpolation {
        ZeroOrderHold,
    };

    enum class TraceAfterEnd {
        Zero,
    };

    struct SampledTraceConfig {
        std::string file;
        std::string time_column = "timestamp_ms";
        std::string value_column = "power_w";
        TraceValueType value_type = TraceValueType::ElectricalPower;
        TraceInterpolation interpolation =
            TraceInterpolation::ZeroOrderHold;
        TraceAfterEnd after_trace = TraceAfterEnd::Zero;
        double panel_area_m2 = 0.0;
        double conversion_efficiency = 0.0;
        std::uint64_t max_file_size_bytes = UINT64_C(268435456);
        std::uint64_t max_rows = UINT64_C(5000000);
    };

    using HarvestSourceConfig = std::variant<
        LegacySolarConfig,
        ScaledPiecewiseConfig,
        SampledTraceConfig>;

    inline HarvestSourceKind sourceKind(
        const HarvestSourceConfig &config) {
        struct ExhaustiveVisitor {
            HarvestSourceKind operator()(
                const LegacySolarConfig &) const noexcept {
                return HarvestSourceKind::LegacySolar;
            }

            HarvestSourceKind operator()(
                const ScaledPiecewiseConfig &) const noexcept {
                return HarvestSourceKind::ScaledPiecewise;
            }

            HarvestSourceKind operator()(
                const SampledTraceConfig &) const noexcept {
                return HarvestSourceKind::SampledTrace;
            }
        };

        // std::visit throws std::bad_variant_access for a valueless variant.
        // The overload set is deliberately exhaustive: adding an alternative
        // without adding its mapping is a compile-time error.
        return std::visit(ExhaustiveVisitor{}, config);
    }

} // namespace RTSim

#endif // RTSIM_HARVEST_TYPES_HPP
