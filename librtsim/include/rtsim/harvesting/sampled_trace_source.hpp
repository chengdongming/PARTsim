#ifndef RTSIM_SAMPLED_TRACE_SOURCE_HPP
#define RTSIM_SAMPLED_TRACE_SOURCE_HPP

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include <rtsim/harvesting/harvest_source.hpp>

namespace RTSim {

    class SampledTraceSource final : public HarvestSource {
    public:
        explicit SampledTraceSource(SampledTraceConfig config);

        double offeredEnergyForInterval(
            const HarvestInterval &interval) const override;

        const std::string &rawFileSha256() const noexcept;
        const std::string &normalizedTraceSha256() const noexcept;
        std::size_t sampleCount() const noexcept;
        const std::filesystem::path &resolvedPath() const noexcept;

    private:
        struct LoadedTrace {
            SampledTraceConfig config;
            std::filesystem::path resolved_path;
            std::string raw_file_sha256;
            std::string normalized_trace_sha256;
            std::vector<std::uint64_t> timestamps;
            std::vector<double> electrical_powers;
            std::vector<double> prefix_energy;
        };

        explicit SampledTraceSource(LoadedTrace loaded);
        static LoadedTrace loadTrace(SampledTraceConfig config);
        double integralAt(std::uint64_t time_ms) const;

        const SampledTraceConfig _config;
        const std::filesystem::path _resolved_path;
        const std::string _raw_file_sha256;
        const std::string _normalized_trace_sha256;
        const std::vector<std::uint64_t> _timestamps;
        const std::vector<double> _electrical_powers;
        const std::vector<double> _prefix_energy;
    };

} // namespace RTSim

#endif // RTSIM_SAMPLED_TRACE_SOURCE_HPP
