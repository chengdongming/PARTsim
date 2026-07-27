#include <rtsim/scheduler/scheduler_harvest_runtime.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

#include <rtsim/harvesting/harvest_source_factory.hpp>
#include <rtsim/scheduler/st_energy_utils.hpp>

namespace RTSim {
    namespace {
        bool energyApproximatelyEqual(double lhs, double rhs) {
            const double scale = std::max(
                1.0, std::max(std::fabs(lhs), std::fabs(rhs)));
            return std::fabs(lhs - rhs) <=
                STEnergy::kEnergyEpsilonJ * scale;
        }

        HarvestResult zeroIntervalResult(double battery_before_j,
                                         double battery_capacity_j) {
            if (!std::isfinite(battery_before_j) ||
                !std::isfinite(battery_capacity_j) ||
                battery_before_j < 0.0 ||
                battery_capacity_j < 0.0 ||
                battery_before_j > battery_capacity_j) {
                throw std::invalid_argument(
                    "scheduler harvest battery must be finite and within capacity");
            }

            HarvestResult result;
            result.offered_j = 0.0;
            result.actual_j = 0.0;
            result.clipped_j = 0.0;
            result.battery_before_j = battery_before_j;
            result.battery_after_j = result.battery_before_j;
            return result;
        }
    } // namespace

    void SchedulerHarvestRuntime::beginRun(
        const HarvestSourceConfig &config) {
        _runtime.reset();
        resetEnergySummary();
        auto next_runtime = std::make_unique<HarvestRuntime>(
            makeHarvestSource(config));
        _runtime = std::move(next_runtime);
    }

    HarvestResult SchedulerHarvestRuntime::applyAtDecisionTime(
        std::int64_t decision_time_ms,
        double battery_before_j,
        double battery_capacity_j) {
        if (!_runtime) {
            throw std::logic_error(
                "scheduler harvest runtime has not begun a run");
        }
        if (decision_time_ms < 0) {
            throw std::invalid_argument(
                "scheduler decision time must be non-negative");
        }
        if (decision_time_ms == 0) {
            const HarvestResult result = zeroIntervalResult(
                battery_before_j, battery_capacity_j);
            observeInitialBattery(
                result.battery_before_j, battery_capacity_j);
            return result;
        }
        if (!_runtime->hasAppliedInterval() && decision_time_ms != 1) {
            throw std::logic_error(
                "scheduler harvest run must begin with interval zero");
        }

        const auto end_time_ms =
            static_cast<std::uint64_t>(decision_time_ms);
        const auto interval_index = end_time_ms - 1;
        const HarvestResult result = _runtime->applyInterval(
            HarvestInterval{
                interval_index,
                interval_index,
                end_time_ms},
            battery_before_j,
            battery_capacity_j);
        observeEnergyInterval(result, battery_capacity_j);
        return result;
    }

    bool SchedulerHarvestRuntime::isInitialized() const noexcept {
        return static_cast<bool>(_runtime);
    }

    const HarvestRuntime &SchedulerHarvestRuntime::runtime() const {
        if (!_runtime) {
            throw std::logic_error(
                "scheduler harvest runtime has not begun a run");
        }
        return *_runtime;
    }

    void SchedulerHarvestRuntime::resetEnergySummary() noexcept {
        _energy_summary = EnergySummary{};
        _energy_initial_observed = false;
        _energy_initial_j = 0.0;
        _energy_capacity_j = 0.0;
        _last_post_harvest_battery_j = 0.0;
        _energy_summary_invalid = false;
        _energy_summary_error.clear();
    }

    void SchedulerHarvestRuntime::recordEnergySummaryError(
        const std::string &message) noexcept {
        _energy_summary_invalid = true;
        if (_energy_summary_error.empty()) {
            try {
                _energy_summary_error = message;
            } catch (...) {
                // Preserve the invalid flag without allowing observational
                // diagnostics to affect the completed harvest transaction.
            }
        }
    }

    void SchedulerHarvestRuntime::observeInitialBattery(
        double battery_initial_j,
        double battery_capacity_j) noexcept {
        if (_energy_initial_observed) {
            if (!energyApproximatelyEqual(
                    battery_initial_j, _energy_initial_j) ||
                !energyApproximatelyEqual(
                    battery_capacity_j, _energy_capacity_j)) {
                recordEnergySummaryError(
                    "time-zero battery observation changed within one run");
            }
            return;
        }

        _energy_initial_observed = true;
        _energy_initial_j = battery_initial_j;
        _energy_capacity_j = battery_capacity_j;
        _last_post_harvest_battery_j = battery_initial_j;
        _energy_summary.battery_min_j = battery_initial_j;
        _energy_summary.battery_max_j = battery_initial_j;
        _energy_summary.battery_final_j = battery_initial_j;
    }

    void SchedulerHarvestRuntime::observeEnergyInterval(
        const HarvestResult &result,
        double battery_capacity_j) noexcept {
        if (!_energy_initial_observed) {
            recordEnergySummaryError(
                "energy summary is missing the time-zero battery observation");
        } else {
            if (!energyApproximatelyEqual(
                    battery_capacity_j, _energy_capacity_j)) {
                recordEnergySummaryError(
                    "battery capacity changed within one run");
            }

            const double consumed =
                _last_post_harvest_battery_j -
                result.battery_before_j;
            if (!std::isfinite(consumed) ||
                consumed < -STEnergy::kEnergyEpsilonJ) {
                recordEnergySummaryError(
                    "battery gained energy outside the harvest runtime");
            } else {
                _energy_summary.consumed_energy_j +=
                    std::max(0.0, consumed);
            }
        }

        const bool result_valid =
            std::isfinite(result.offered_j) &&
            std::isfinite(result.actual_j) &&
            std::isfinite(result.clipped_j) &&
            std::isfinite(result.battery_before_j) &&
            std::isfinite(result.battery_after_j) &&
            result.offered_j >= 0.0 &&
            result.actual_j >= 0.0 &&
            result.clipped_j >= 0.0 &&
            result.battery_before_j >= 0.0 &&
            result.battery_after_j >= 0.0;
        if (!result_valid) {
            recordEnergySummaryError(
                "harvest result contains non-finite or negative energy");
            return;
        }
        if (!energyApproximatelyEqual(
                result.offered_j,
                result.actual_j + result.clipped_j)) {
            recordEnergySummaryError(
                "offered energy does not reconcile with credited and clipped energy");
        }

        _energy_summary.offered_energy_j += result.offered_j;
        _energy_summary.credited_energy_j += result.actual_j;
        _energy_summary.clipped_energy_j += result.clipped_j;
        _energy_summary.battery_min_j = std::min(
            _energy_summary.battery_min_j,
            std::min(result.battery_before_j,
                     result.battery_after_j));
        _energy_summary.battery_max_j = std::max(
            _energy_summary.battery_max_j,
            std::max(result.battery_before_j,
                     result.battery_after_j));
        _energy_summary.battery_final_j =
            result.battery_after_j;
        if (result.battery_before_j <=
            STEnergy::kEnergyEpsilonJ) {
            ++_energy_summary.battery_empty_ticks;
        }
        if (result.battery_before_j +
                STEnergy::kEnergyEpsilonJ >=
            battery_capacity_j) {
            ++_energy_summary.battery_full_ticks;
        }
        ++_energy_summary.observed_energy_intervals;
        _last_post_harvest_battery_j =
            result.battery_after_j;

        if (!std::isfinite(_energy_summary.offered_energy_j) ||
            !std::isfinite(_energy_summary.credited_energy_j) ||
            !std::isfinite(_energy_summary.clipped_energy_j) ||
            !std::isfinite(_energy_summary.consumed_energy_j)) {
            recordEnergySummaryError(
                "energy summary accumulation overflowed");
        }
    }

    const EnergySummary &SchedulerHarvestRuntime::finalizeEnergySummary(
        std::uint64_t expected_horizon_ms) const {
        if (!_runtime) {
            throw std::logic_error(
                "scheduler harvest runtime has not begun a run");
        }
        if (!_energy_initial_observed) {
            throw std::logic_error(
                "energy summary has no time-zero battery observation");
        }
        if (_energy_summary_invalid) {
            throw std::logic_error(
                _energy_summary_error.empty()
                    ? "energy summary validation failed"
                    : _energy_summary_error);
        }
        if (_energy_summary.observed_energy_intervals !=
            expected_horizon_ms) {
            throw std::logic_error(
                "energy summary interval count does not match the horizon");
        }
        if (!energyApproximatelyEqual(
                _energy_summary.offered_energy_j,
                _energy_summary.credited_energy_j +
                    _energy_summary.clipped_energy_j)) {
            throw std::logic_error(
                "energy summary offered-energy reconciliation failed");
        }
        if (!energyApproximatelyEqual(
                _energy_initial_j +
                    _energy_summary.credited_energy_j -
                    _energy_summary.consumed_energy_j,
                _energy_summary.battery_final_j)) {
            throw std::logic_error(
                "energy summary battery conservation failed");
        }
        if (_energy_summary.offered_energy_j < 0.0 ||
            _energy_summary.credited_energy_j < 0.0 ||
            _energy_summary.clipped_energy_j < 0.0 ||
            _energy_summary.consumed_energy_j < 0.0 ||
            _energy_summary.battery_min_j <
                -STEnergy::kEnergyEpsilonJ ||
            _energy_summary.battery_min_j >
                _energy_summary.battery_final_j +
                    STEnergy::kEnergyEpsilonJ ||
            _energy_summary.battery_final_j >
                _energy_summary.battery_max_j +
                    STEnergy::kEnergyEpsilonJ ||
            _energy_summary.battery_max_j >
                _energy_capacity_j +
                    STEnergy::kEnergyEpsilonJ ||
            _energy_summary.battery_empty_ticks >
                _energy_summary.observed_energy_intervals ||
            _energy_summary.battery_full_ticks >
                _energy_summary.observed_energy_intervals) {
            throw std::logic_error(
                "energy summary bounds are inconsistent");
        }
        return _energy_summary;
    }

    B4ObservabilityEnergySnapshot
    SchedulerHarvestRuntime::finalizeObservabilityEnergySnapshot(
        std::uint64_t expected_horizon_ms,
        double final_battery_j,
        double battery_capacity_j) {
        if (!std::isfinite(final_battery_j) ||
            !std::isfinite(battery_capacity_j) ||
            final_battery_j < 0.0 ||
            battery_capacity_j < 0.0 ||
            final_battery_j > battery_capacity_j) {
            throw std::invalid_argument(
                "final observation battery must be finite and within capacity");
        }
        if (_energy_initial_observed &&
            !energyApproximatelyEqual(
                battery_capacity_j, _energy_capacity_j)) {
            throw std::logic_error(
                "final observation battery capacity does not match the run");
        }

        if (_energy_summary.observed_energy_intervals <
                expected_horizon_ms &&
            _energy_summary.observed_energy_intervals + UINT64_C(1) ==
                expected_horizon_ms) {
            if (expected_horizon_ms >
                static_cast<std::uint64_t>(
                    std::numeric_limits<std::int64_t>::max())) {
                throw std::overflow_error(
                    "summary horizon exceeds scheduler decision time");
            }
            (void)applyAtDecisionTime(
                static_cast<std::int64_t>(expected_horizon_ms),
                final_battery_j,
                battery_capacity_j);
        } else if (_energy_summary.observed_energy_intervals ==
                       expected_horizon_ms &&
                   !energyApproximatelyEqual(
                       final_battery_j,
                       _energy_summary.battery_final_j)) {
            throw std::logic_error(
                "final observation battery does not match the energy ledger");
        }

        const EnergySummary &summary =
            finalizeEnergySummary(expected_horizon_ms);
        return B4ObservabilityEnergySnapshot{
            summary,
            _energy_initial_j,
            _energy_capacity_j,
            expected_horizon_ms};
    }

} // namespace RTSim
