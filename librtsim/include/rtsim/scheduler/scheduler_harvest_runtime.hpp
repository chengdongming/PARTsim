#ifndef RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP
#define RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP

#include <cstdint>
#include <memory>

#include <rtsim/harvesting/harvest_runtime.hpp>
#include <rtsim/harvesting/harvest_types.hpp>

namespace RTSim {

    class SchedulerHarvestRuntime final {
    public:
        void beginRun(const HarvestSourceConfig &config);

        HarvestResult applyAtDecisionTime(
            std::int64_t decision_time_ms,
            double battery_before_j,
            double battery_capacity_j);

        bool isInitialized() const noexcept;
        const HarvestRuntime &runtime() const;

    private:
        std::unique_ptr<HarvestRuntime> _runtime;
    };

} // namespace RTSim

#endif // RTSIM_SCHEDULER_HARVEST_RUNTIME_HPP
