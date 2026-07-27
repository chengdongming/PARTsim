#ifndef RTSIM_HARVEST_RUNTIME_HPP
#define RTSIM_HARVEST_RUNTIME_HPP

#include <cstdint>
#include <memory>

#include <rtsim/harvesting/harvest_source.hpp>

namespace RTSim {

    class HarvestRuntime final {
    public:
        explicit HarvestRuntime(std::unique_ptr<HarvestSource> source);

        HarvestResult applyInterval(
            const HarvestInterval &interval,
            double battery_before_j,
            double battery_capacity_j);

        const HarvestSource &source() const noexcept;
        bool hasAppliedInterval() const noexcept;
        std::uint64_t lastAppliedIndex() const;

    private:
        void validateInterval(const HarvestInterval &interval) const;
        void commitInterval(const HarvestInterval &interval) noexcept;

        std::unique_ptr<HarvestSource> _source;
        bool _has_applied_interval = false;
        std::uint64_t _last_index = 0;
        std::uint64_t _last_start_time_ms = 0;
        std::uint64_t _last_end_time_ms = 0;
    };

} // namespace RTSim

#endif // RTSIM_HARVEST_RUNTIME_HPP
