#ifndef RTSIM_SCALED_PIECEWISE_SOURCE_HPP
#define RTSIM_SCALED_PIECEWISE_SOURCE_HPP

#include <rtsim/harvesting/harvest_source.hpp>

namespace RTSim {

    class ScaledPiecewiseSource final : public HarvestSource {
    public:
        explicit ScaledPiecewiseSource(ScaledPiecewiseConfig config);

        double offeredEnergyForInterval(
            const HarvestInterval &interval) const override;

    private:
        const ScaledPiecewiseConfig _config;

        static ScaledPiecewiseConfig validateConfig(
            ScaledPiecewiseConfig config);
    };

} // namespace RTSim

#endif // RTSIM_SCALED_PIECEWISE_SOURCE_HPP
