#ifndef RTSIM_HARVEST_SOURCE_FACTORY_HPP
#define RTSIM_HARVEST_SOURCE_FACTORY_HPP

#include <memory>

#include <rtsim/harvesting/harvest_source.hpp>

namespace RTSim {

    std::unique_ptr<HarvestSource> makeHarvestSource(
        const HarvestSourceConfig &config);

} // namespace RTSim

#endif // RTSIM_HARVEST_SOURCE_FACTORY_HPP
