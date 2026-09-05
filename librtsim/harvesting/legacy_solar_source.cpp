#include <rtsim/harvesting/legacy_solar_source.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <utility>

namespace RTSim {
    namespace {
        constexpr std::int64_t SyntheticPeriodMs = INT64_C(60000);
        constexpr double MillisecondsToSeconds = 0.001;

        double positiveZero(double value) {
            return value == 0.0 ? 0.0 : value;
        }

        double normalizedLinearIntegral(
            std::int64_t start,
            std::int64_t end,
            double intercept,
            double slope) {
            if (end <= start) {
                return 0.0;
            }
            const double start_factor =
                intercept + slope * static_cast<double>(start);
            const double end_factor =
                intercept + slope * static_cast<double>(end);
            return static_cast<double>(end - start) *
                   (start_factor + end_factor) * 0.5;
        }

        double normalizedPrimitiveWithinPeriod(std::int64_t time_ms) {
            if (time_ms <= 0) {
                return 0.0;
            }
            if (time_ms <= INT64_C(10000)) {
                return normalizedLinearIntegral(
                    0, time_ms, 1.0, -1.0 / 50000.0);
            }

            double area = normalizedLinearIntegral(
                0, INT64_C(10000), 1.0, -1.0 / 50000.0);
            if (time_ms <= INT64_C(20000)) {
                return area + static_cast<double>(time_ms - 10000) *
                    (4.0 / 5.0);
            }

            area += static_cast<double>(10000) * (4.0 / 5.0);
            if (time_ms <= INT64_C(40000)) {
                return area + normalizedLinearIntegral(
                    INT64_C(20000), time_ms, 0.4, 1.0 / 50000.0);
            }

            area += normalizedLinearIntegral(
                INT64_C(20000), INT64_C(40000), 0.4, 1.0 / 50000.0);
            if (time_ms <= INT64_C(50000)) {
                return area + static_cast<double>(time_ms - 40000) *
                    (6.0 / 5.0);
            }

            area += static_cast<double>(10000) * (6.0 / 5.0);
            return area + normalizedLinearIntegral(
                INT64_C(50000), time_ms, 2.2, -1.0 / 50000.0);
        }

        double normalizedCumulative(std::int64_t time_ms) {
            if (time_ms < 0) {
                throw std::invalid_argument(
                    "synthetic harvesting time must be non-negative");
            }
            const std::int64_t periods = time_ms / SyntheticPeriodMs;
            const std::int64_t remainder = time_ms % SyntheticPeriodMs;
            return static_cast<double>(periods) *
                       static_cast<double>(SyntheticPeriodMs) +
                   normalizedPrimitiveWithinPeriod(remainder);
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
        const double normalized_area_ms =
            normalizedCumulative(end_time_ms) -
            normalizedCumulative(start_time_ms);
        const double offered_j =
            _config.base_harvesting_power_w * normalized_area_ms *
            MillisecondsToSeconds;
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
