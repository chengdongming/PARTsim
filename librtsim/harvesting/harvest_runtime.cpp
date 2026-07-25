#include <rtsim/harvesting/harvest_runtime.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>

namespace RTSim {
    namespace {
        double positiveZero(double value) noexcept {
            return value == 0.0 ? 0.0 : value;
        }

        double roundedAdd(double lhs, double rhs) noexcept {
            volatile double result = lhs + rhs;
            return result;
        }

        double roundedSubtract(double lhs, double rhs) noexcept {
            volatile double result = lhs - rhs;
            return result;
        }

        bool sameBinary64(double lhs, double rhs) noexcept {
            static_assert(sizeof(double) == sizeof(std::uint64_t),
                          "harvest runtime requires binary64 double");
            std::uint64_t lhs_bits = 0;
            std::uint64_t rhs_bits = 0;
            std::memcpy(&lhs_bits, &lhs, sizeof(lhs_bits));
            std::memcpy(&rhs_bits, &rhs, sizeof(rhs_bits));
            return lhs_bits == rhs_bits;
        }

        void validateBatteryInput(double battery_before_j,
                                  double battery_capacity_j) {
            if (!std::isfinite(battery_before_j)) {
                throw std::invalid_argument(
                    "battery_before_j must be finite");
            }
            if (!std::isfinite(battery_capacity_j)) {
                throw std::invalid_argument(
                    "battery_capacity_j must be finite");
            }
            if (battery_capacity_j < 0.0) {
                throw std::invalid_argument(
                    "battery_capacity_j must be non-negative");
            }
            if (battery_before_j < 0.0 ||
                battery_before_j > battery_capacity_j) {
                throw std::invalid_argument(
                    "battery_before_j must be within [0, battery_capacity_j]");
            }
        }

        double validateOfferedEnergy(double offered_j) {
            if (!std::isfinite(offered_j) || offered_j < 0.0) {
                throw std::domain_error(
                    "harvest source offered energy must be finite and "
                    "non-negative");
            }
            return positiveZero(offered_j);
        }

        void validateResult(const HarvestResult &result,
                            double battery_capacity_j) {
            const double expected_clipped_j = positiveZero(
                roundedSubtract(result.offered_j, result.actual_j));
            const double expected_battery_after_j =
                result.offered_j == 0.0
                    ? result.battery_before_j
                    : positiveZero(roundedAdd(
                          result.battery_before_j, result.actual_j));
            if (!std::isfinite(result.offered_j) ||
                !std::isfinite(result.actual_j) ||
                !std::isfinite(result.clipped_j) ||
                !std::isfinite(result.battery_before_j) ||
                !std::isfinite(result.battery_after_j) ||
                result.offered_j < 0.0 || result.actual_j < 0.0 ||
                result.clipped_j < 0.0 ||
                result.actual_j > result.offered_j ||
                result.battery_after_j < result.battery_before_j ||
                result.battery_after_j > battery_capacity_j ||
                !sameBinary64(result.clipped_j, expected_clipped_j) ||
                !sameBinary64(
                    result.battery_after_j, expected_battery_after_j)) {
                throw std::logic_error("harvest result invariant violation");
            }
        }
    } // namespace

    HarvestRuntime::HarvestRuntime(std::unique_ptr<HarvestSource> source) :
        _source(std::move(source)) {
        if (!_source) {
            throw std::invalid_argument("harvest source must not be null");
        }
    }

    HarvestResult HarvestRuntime::applyInterval(
        const HarvestInterval &interval,
        double battery_before_j,
        double battery_capacity_j) {
        validateInterval(interval);
        validateBatteryInput(battery_before_j, battery_capacity_j);

        const double offered_j = validateOfferedEnergy(
            _source->offeredEnergyForInterval(interval));

        HarvestResult result;
        result.offered_j = offered_j;
        result.battery_before_j = battery_before_j;
        if (offered_j == 0.0) {
            result.actual_j = 0.0;
            result.clipped_j = 0.0;
            result.battery_after_j = battery_before_j;
        } else {
            double space_j = roundedSubtract(
                battery_capacity_j, battery_before_j);
            if (space_j < 0.0) {
                throw std::logic_error(
                    "validated battery input produced negative capacity space");
            }
            space_j = positiveZero(space_j);

            double actual_j = std::min(offered_j, space_j);
            actual_j = positiveZero(actual_j);
            double battery_after_j = roundedAdd(
                battery_before_j, actual_j);
            while (actual_j > 0.0 &&
                   battery_after_j > battery_capacity_j) {
                actual_j = std::nextafter(actual_j, 0.0);
                battery_after_j = roundedAdd(
                    battery_before_j, actual_j);
            }

            result.actual_j = positiveZero(actual_j);
            result.clipped_j = positiveZero(
                roundedSubtract(offered_j, result.actual_j));
            result.battery_after_j = positiveZero(battery_after_j);
        }

        validateResult(result, battery_capacity_j);
        commitInterval(interval);
        return result;
    }

    const HarvestSource &HarvestRuntime::source() const noexcept {
        return *_source;
    }

    bool HarvestRuntime::hasAppliedInterval() const noexcept {
        return _has_applied_interval;
    }

    std::uint64_t HarvestRuntime::lastAppliedIndex() const {
        if (!_has_applied_interval) {
            throw std::logic_error("no harvest interval has been applied");
        }
        return _last_index;
    }

    void HarvestRuntime::validateInterval(
        const HarvestInterval &interval) const {
        if (interval.start_time_ms >= interval.end_time_ms) {
            throw std::invalid_argument(
                "harvest interval start must precede end");
        }
        if (!_has_applied_interval) {
            return;
        }
        if (_last_index == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("harvest interval index is exhausted");
        }
        if (interval.index != _last_index + UINT64_C(1)) {
            throw std::invalid_argument(
                "harvest interval index must be exactly consecutive");
        }
        if (interval.start_time_ms != _last_end_time_ms) {
            throw std::invalid_argument(
                "harvest interval time must be exactly contiguous");
        }
    }

    void HarvestRuntime::commitInterval(
        const HarvestInterval &interval) noexcept {
        _last_index = interval.index;
        _last_start_time_ms = interval.start_time_ms;
        _last_end_time_ms = interval.end_time_ms;
        _has_applied_interval = true;
    }

} // namespace RTSim
