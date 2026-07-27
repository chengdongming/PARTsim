#ifndef RTSIM_HARVEST_SOURCE_HPP
#define RTSIM_HARVEST_SOURCE_HPP

#include <rtsim/harvesting/harvest_types.hpp>

namespace RTSim {

    class HarvestSource {
    public:
        virtual ~HarvestSource() = default;

        virtual double offeredEnergyForInterval(
            const HarvestInterval &interval) const = 0;
    };

} // namespace RTSim

#endif // RTSIM_HARVEST_SOURCE_HPP
