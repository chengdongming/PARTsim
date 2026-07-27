#ifndef RTSIM_LEGACY_SOLAR_SOURCE_HPP
#define RTSIM_LEGACY_SOLAR_SOURCE_HPP

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <rtsim/harvesting/harvest_source.hpp>

namespace RTSim {

    class LegacySolarSource final : public HarvestSource {
    public:
        explicit LegacySolarSource(LegacySolarConfig config);

        double offeredEnergyForInterval(
            const HarvestInterval &interval) const override;

    private:
        enum class LegacyIrradianceRowKind {
            Value,
            InvalidDomainNonFinite,
            InvalidDomainNegative,
        };

        struct LegacyIrradianceRow {
            double value = 0.0;
            LegacyIrradianceRowKind kind =
                LegacyIrradianceRowKind::Value;
            std::size_t file_line = 0;
        };

        const LegacySolarConfig _config;
        const std::vector<LegacyIrradianceRow> _legacy_irradiance_rows;

        static LegacySolarConfig validateConfig(
            LegacySolarConfig config);
        static std::vector<LegacyIrradianceRow>
            preloadLegacyIrradiance(const std::string &path);

        std::int64_t intervalEndWithOffset(
            const HarvestInterval &interval) const;
        double syntheticOfferedEnergy(
            const HarvestInterval &interval,
            std::int64_t end_time_ms,
            std::int64_t elapsed_ms) const;
        double realOfferedEnergy(
            const HarvestInterval &interval,
            std::int64_t end_time_ms,
            std::int64_t elapsed_ms) const;
        double checkedOfferedEnergy(double offered_j) const;
    };

} // namespace RTSim

#endif // RTSIM_LEGACY_SOLAR_SOURCE_HPP
