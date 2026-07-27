#include <rtsim/harvesting/scaled_piecewise_source.hpp>

#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace RTSim {
    namespace {
        constexpr std::uint64_t max_exact_binary64_integer =
            UINT64_C(1) << 53;

        double positiveZero(double value) noexcept {
            return value == 0.0 ? 0.0 : value;
        }

        // Volatile stores are intentional binary64 rounding barriers. They
        // keep multiply/add contraction from changing the frozen B4 order.
        double roundedMultiply(double lhs, double rhs) noexcept {
            volatile double result = lhs * rhs;
            return result;
        }

        double roundedAdd(double lhs, double rhs) noexcept {
            volatile double result = lhs + rhs;
            return result;
        }
    } // namespace

    ScaledPiecewiseSource::ScaledPiecewiseSource(
        ScaledPiecewiseConfig config) :
        _config(validateConfig(std::move(config))) {}

    ScaledPiecewiseConfig ScaledPiecewiseSource::validateConfig(
        ScaledPiecewiseConfig config) {
        if (!std::isfinite(config.scale_w) || config.scale_w < 0.0) {
            throw std::invalid_argument(
                "scaled piecewise scale must be finite and non-negative");
        }
        if (config.segments.empty()) {
            throw std::invalid_argument(
                "scaled piecewise segments must not be empty");
        }

        config.scale_w = positiveZero(config.scale_w);
        for (std::size_t index = 0; index < config.segments.size(); ++index) {
            PiecewiseSegment &segment = config.segments[index];
            if (segment.start_time_ms >= segment.end_time_ms) {
                throw std::invalid_argument(
                    "scaled piecewise segment start must precede end");
            }
            if (!std::isfinite(segment.multiplier) ||
                segment.multiplier < 0.0) {
                throw std::invalid_argument(
                    "scaled piecewise multiplier must be finite and "
                    "non-negative");
            }
            if (index > 0 &&
                segment.start_time_ms <
                    config.segments[index - 1].end_time_ms) {
                throw std::invalid_argument(
                    "scaled piecewise segments must be strictly ordered and "
                    "non-overlapping");
            }
            segment.multiplier = positiveZero(segment.multiplier);
        }
        return config;
    }

    double ScaledPiecewiseSource::offeredEnergyForInterval(
        const HarvestInterval &interval) const {
        if (interval.start_time_ms >= interval.end_time_ms) {
            throw std::invalid_argument(
                "harvest interval start must precede end");
        }

        const std::uint64_t interval_duration_ms =
            interval.end_time_ms - interval.start_time_ms;
        if (interval_duration_ms > max_exact_binary64_integer) {
            throw std::invalid_argument(
                "harvest interval duration cannot be represented exactly as "
                "binary64 milliseconds");
        }

        double offered_j = 0.0;
        for (const PiecewiseSegment &segment : _config.segments) {
            if (segment.start_time_ms >= interval.end_time_ms) {
                break;
            }
            if (segment.end_time_ms <= interval.start_time_ms) {
                continue;
            }

            const std::uint64_t overlap_start =
                interval.start_time_ms > segment.start_time_ms
                    ? interval.start_time_ms
                    : segment.start_time_ms;
            const std::uint64_t overlap_end =
                interval.end_time_ms < segment.end_time_ms
                    ? interval.end_time_ms
                    : segment.end_time_ms;
            if (overlap_start >= overlap_end) {
                continue;
            }

            const std::uint64_t duration_ms = overlap_end - overlap_start;
            double segment_energy_j =
                roundedMultiply(_config.scale_w, segment.multiplier);
            segment_energy_j = roundedMultiply(
                segment_energy_j, static_cast<double>(duration_ms));
            segment_energy_j = roundedMultiply(segment_energy_j, 0.001);
            if (!std::isfinite(segment_energy_j) || segment_energy_j < 0.0) {
                throw std::overflow_error(
                    "scaled piecewise segment energy is not finite and "
                    "non-negative");
            }

            offered_j = roundedAdd(offered_j, segment_energy_j);
            if (!std::isfinite(offered_j) || offered_j < 0.0) {
                throw std::overflow_error(
                    "scaled piecewise offered energy is not finite and "
                    "non-negative");
            }
        }

        return positiveZero(offered_j);
    }

} // namespace RTSim
