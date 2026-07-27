#include <rtsim/harvesting/harvest_source_factory.hpp>

#include <rtsim/harvesting/legacy_solar_source.hpp>
#include <rtsim/harvesting/sampled_trace_source.hpp>
#include <rtsim/harvesting/scaled_piecewise_source.hpp>

#include <memory>
#include <variant>

namespace RTSim {
    namespace {
        struct HarvestSourceFactoryVisitor {
            std::unique_ptr<HarvestSource> operator()(
                const LegacySolarConfig &config) const {
                return std::make_unique<LegacySolarSource>(config);
            }

            std::unique_ptr<HarvestSource> operator()(
                const ScaledPiecewiseConfig &config) const {
                return std::make_unique<ScaledPiecewiseSource>(config);
            }

            std::unique_ptr<HarvestSource> operator()(
                const SampledTraceConfig &config) const {
                return std::make_unique<SampledTraceSource>(config);
            }
        };
    } // namespace

    std::unique_ptr<HarvestSource> makeHarvestSource(
        const HarvestSourceConfig &config) {
        if (config.valueless_by_exception()) {
            throw std::bad_variant_access();
        }
        return std::visit(HarvestSourceFactoryVisitor{}, config);
    }

} // namespace RTSim
