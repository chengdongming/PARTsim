#include <rtsim/harvesting/legacy_solar_source.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <utility>

namespace RTSim {
    namespace {
        constexpr std::int64_t SyntheticHorizonMs = INT64_C(60000);
        constexpr double SyntheticBaseFactor = 0.975;
        constexpr double SyntheticRampSpan = 0.05;
        constexpr double SyntheticFinalFactor = 1.025;
        constexpr double MillisecondsToSeconds = 0.001;

        double positiveZero(double value) {
            return value == 0.0 ? 0.0 : value;
        }
    } // namespace

    LegacySolarSource::LegacySolarSource(LegacySolarConfig config) :
        _config(validateConfig(std::move(config))),
        _legacy_irradiance_rows(
            _config.use_real_solar_data
                ? preloadLegacyIrradiance(_config.solar_data_file)
                : std::vector<LegacyIrradianceRow>{}) {}

    LegacySolarConfig LegacySolarSource::validateConfig(
        LegacySolarConfig config) {
        if (!std::isfinite(config.base_harvesting_power_w) ||
            config.base_harvesting_power_w < 0.0) {
            throw std::invalid_argument(
                "legacy solar base harvesting power must be finite and "
                "non-negative");
        }
        if (!std::isfinite(config.pv_area_m2) ||
            config.pv_area_m2 <= 0.0) {
            throw std::invalid_argument(
                "legacy solar photovoltaic area must be finite and positive");
        }
        if (!std::isfinite(config.pv_efficiency) ||
            config.pv_efficiency <= 0.0) {
            throw std::invalid_argument(
                "legacy solar photovoltaic efficiency must be finite and "
                "positive");
        }
        if (config.start_offset_ms >
            static_cast<std::uint64_t>(
                std::numeric_limits<std::int64_t>::max())) {
            throw std::invalid_argument(
                "legacy solar start offset exceeds the signed time domain");
        }
        config.base_harvesting_power_w =
            positiveZero(config.base_harvesting_power_w);
        return config;
    }

    std::vector<LegacySolarSource::LegacyIrradianceRow>
        LegacySolarSource::preloadLegacyIrradiance(
            const std::string &path) {
        std::ifstream file(path);
        if (!file.is_open()) {
            return {};
        }

        std::string line;
        if (!std::getline(file, line)) {
            return {};
        }

        std::vector<LegacyIrradianceRow> rows;
        std::size_t file_line = 1;
        while (std::getline(file, line)) {
            ++file_line;
            LegacyIrradianceRow row;
            row.file_line = file_line;
            try {
                row.value = std::stod(line);
                if (!std::isfinite(row.value)) {
                    row.kind =
                        LegacyIrradianceRowKind::InvalidDomainNonFinite;
                } else if (row.value < 0.0) {
                    row.kind =
                        LegacyIrradianceRowKind::InvalidDomainNegative;
                } else {
                    row.value = positiveZero(row.value);
                }
            } catch (const std::exception &) {
                // The pre-migration path maps ordinary stod failures to zero.
                row.value = 0.0;
            }
            rows.push_back(row);
        }
        return rows;
    }

    double LegacySolarSource::offeredEnergyForInterval(
        const HarvestInterval &interval) const {
        if (interval.end_time_ms <= interval.start_time_ms) {
            return 0.0;
        }
        if (interval.start_time_ms >
                static_cast<std::uint64_t>(
                    std::numeric_limits<std::int64_t>::max()) ||
            interval.end_time_ms >
                static_cast<std::uint64_t>(
                    std::numeric_limits<std::int64_t>::max())) {
            throw std::overflow_error(
                "legacy solar interval exceeds the signed time domain");
        }

        const std::int64_t start_time_ms =
            static_cast<std::int64_t>(interval.start_time_ms);
        const std::int64_t end_time_ms =
            static_cast<std::int64_t>(interval.end_time_ms);
        const std::int64_t elapsed_ms = end_time_ms - start_time_ms;
        const std::int64_t end_with_offset =
            intervalEndWithOffset(interval);

        return _config.use_real_solar_data
                   ? realOfferedEnergy(
                         interval, end_with_offset, elapsed_ms)
                   : syntheticOfferedEnergy(
                         interval, end_with_offset, elapsed_ms);
    }

    std::int64_t LegacySolarSource::intervalEndWithOffset(
        const HarvestInterval &interval) const {
        const std::int64_t end_time_ms =
            static_cast<std::int64_t>(interval.end_time_ms);
        const std::int64_t start_offset_ms =
            static_cast<std::int64_t>(_config.start_offset_ms);
        if (end_time_ms >
            std::numeric_limits<std::int64_t>::max() -
                start_offset_ms) {
            throw std::overflow_error(
                "legacy solar interval end plus offset overflows time");
        }
        return end_time_ms + start_offset_ms;
    }

    double LegacySolarSource::syntheticOfferedEnergy(
        const HarvestInterval &interval,
        std::int64_t,
        std::int64_t) const {
        const std::int64_t start_time_ms =
            static_cast<std::int64_t>(interval.start_time_ms);
        const std::int64_t end_time_ms =
            static_cast<std::int64_t>(interval.end_time_ms);
        double offered_j = 0.0;

        const auto add_linear_segment = [&](std::int64_t start,
                                            std::int64_t end) {
            if (end <= start) {
                return;
            }
            // The midpoint rule is exact for this linear segment and gives
            // the required [l,l+1) midpoint for a one-ms query.
            const double midpoint_ms =
                (static_cast<double>(start) +
                 static_cast<double>(end)) * 0.5;
            const double power =
                _config.base_harvesting_power_w *
                (SyntheticBaseFactor +
                 SyntheticRampSpan * midpoint_ms / 60000.0);
            const double elapsed_seconds =
                static_cast<double>(end - start) * MillisecondsToSeconds;
            offered_j += power * elapsed_seconds;
        };

        const auto add_hold_segment = [&](std::int64_t start,
                                          std::int64_t end) {
            if (end <= start) {
                return;
            }
            const double elapsed_seconds =
                static_cast<double>(end - start) * MillisecondsToSeconds;
            offered_j +=
                (_config.base_harvesting_power_w * SyntheticFinalFactor) *
                elapsed_seconds;
        };

        if (start_time_ms < SyntheticHorizonMs) {
            const std::int64_t linear_end =
                std::min(end_time_ms, SyntheticHorizonMs);
            add_linear_segment(start_time_ms, linear_end);
            add_hold_segment(linear_end, end_time_ms);
        } else {
            add_hold_segment(start_time_ms, end_time_ms);
        }
        return checkedOfferedEnergy(offered_j);
    }

    double LegacySolarSource::realOfferedEnergy(
        const HarvestInterval &,
        std::int64_t end_time_ms,
        std::int64_t elapsed_ms) const {
        const std::int64_t total_minutes = end_time_ms / INT64_C(60000);
        if (total_minutes < 0 ||
            static_cast<std::uint64_t>(total_minutes) >=
                _legacy_irradiance_rows.size()) {
            return 0.0;
        }

        const LegacyIrradianceRow &row =
            _legacy_irradiance_rows[
                static_cast<std::size_t>(total_minutes)];
        if (row.kind != LegacyIrradianceRowKind::Value) {
            const std::string classification =
                row.kind ==
                        LegacyIrradianceRowKind::InvalidDomainNonFinite
                    ? "non-finite"
                    : "negative";
            throw std::domain_error(
                "legacy solar data '" + _config.solar_data_file +
                "' line " + std::to_string(row.file_line) +
                ": invalid domain (" + classification +
                " irradiance)");
        }

        const double elapsed_seconds =
            static_cast<double>(elapsed_ms) * MillisecondsToSeconds;
        const double offered_j =
            row.value * _config.pv_area_m2 *
            _config.pv_efficiency * elapsed_seconds;
        return checkedOfferedEnergy(offered_j);
    }

    double LegacySolarSource::checkedOfferedEnergy(
        double offered_j) const {
        if (!std::isfinite(offered_j)) {
            throw std::overflow_error(
                "legacy solar offered energy is non-finite");
        }
        if (offered_j < 0.0) {
            throw std::domain_error(
                "legacy solar offered energy is negative");
        }
        return positiveZero(offered_j);
    }

} // namespace RTSim
