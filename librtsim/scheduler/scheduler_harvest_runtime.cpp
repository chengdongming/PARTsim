#include <rtsim/scheduler/scheduler_harvest_runtime.hpp>

#include <cmath>
#include <stdexcept>
#include <utility>

#include <rtsim/harvesting/harvest_source_factory.hpp>

namespace RTSim {
    namespace {
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
            return zeroIntervalResult(
                battery_before_j, battery_capacity_j);
        }
        if (!_runtime->hasAppliedInterval() && decision_time_ms != 1) {
            throw std::logic_error(
                "scheduler harvest run must begin with interval zero");
        }

        const auto end_time_ms =
            static_cast<std::uint64_t>(decision_time_ms);
        const auto interval_index = end_time_ms - 1;
        return _runtime->applyInterval(
            HarvestInterval{
                interval_index,
                interval_index,
                end_time_ms},
            battery_before_j,
            battery_capacity_j);
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

} // namespace RTSim
