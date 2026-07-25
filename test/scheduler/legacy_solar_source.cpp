#include <gtest/gtest.h>

#include <rtsim/harvesting/harvest_source.hpp>
#include <rtsim/harvesting/legacy_solar_source.hpp>

#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <system_error>
#include <type_traits>
#include <utility>
#include <vector>

namespace RTSim {
    namespace {
        class TemporaryLegacySolarData {
        public:
            explicit TemporaryLegacySolarData(
                const std::string &contents,
                const std::string &filename = "legacy_solar.txt") {
                const std::string pattern =
                    (std::filesystem::temp_directory_path() /
                     "partsim_legacy_solar_XXXXXX")
                        .string();
                std::vector<char> mutable_pattern(
                    pattern.begin(), pattern.end());
                mutable_pattern.push_back('\0');
                char *created = ::mkdtemp(mutable_pattern.data());
                if (!created) {
                    throw std::system_error(
                        errno,
                        std::generic_category(),
                        "cannot create temporary legacy solar directory");
                }
                _directory = created;
                _path = _directory / filename;

                std::ofstream output(_path);
                if (!output) {
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error(
                        "cannot create temporary legacy solar data");
                }
                output << contents;
                if (!output) {
                    output.close();
                    std::error_code error;
                    std::filesystem::remove_all(_directory, error);
                    throw std::runtime_error(
                        "cannot write temporary legacy solar data");
                }
            }

            ~TemporaryLegacySolarData() {
                std::error_code error;
                std::filesystem::remove_all(_directory, error);
            }

            TemporaryLegacySolarData(
                const TemporaryLegacySolarData &) = delete;
            TemporaryLegacySolarData &operator=(
                const TemporaryLegacySolarData &) = delete;

            std::string path() const {
                return _path.string();
            }

            std::string filename() const {
                return _path.filename().string();
            }

            const std::filesystem::path &directory() const {
                return _directory;
            }

            void removeFile() {
                std::error_code error;
                (void)std::filesystem::remove(_path, error);
                if (error) {
                    throw std::system_error(
                        error, "cannot remove temporary legacy solar data");
                }
            }

        private:
            std::filesystem::path _directory;
            std::filesystem::path _path;
        };

        class ScopedCurrentPath {
        public:
            explicit ScopedCurrentPath(
                const std::filesystem::path &path) :
                _original(std::filesystem::current_path()) {
                std::filesystem::current_path(path);
            }

            ~ScopedCurrentPath() {
                std::error_code error;
                std::filesystem::current_path(_original, error);
            }

            ScopedCurrentPath(const ScopedCurrentPath &) = delete;
            ScopedCurrentPath &operator=(const ScopedCurrentPath &) = delete;

        private:
            std::filesystem::path _original;
        };

        std::uint64_t binary64Bits(double value) {
            static_assert(
                sizeof(double) == sizeof(std::uint64_t),
                "golden oracle requires binary64 double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        void expectExactBits(double expected, double actual) {
            EXPECT_EQ(binary64Bits(actual), binary64Bits(expected))
                << "expected=" << expected << " actual=" << actual;
        }

        void expectPositiveZero(double actual) {
            EXPECT_EQ(binary64Bits(actual), UINT64_C(0));
        }

        LegacySolarConfig syntheticConfig() {
            LegacySolarConfig config;
            config.base_harvesting_power_w = 0.054;
            config.start_offset_ms = 0;
            config.use_real_solar_data = false;
            config.pv_efficiency = 0.18;
            config.pv_area_m2 = 1.0;
            return config;
        }

        LegacySolarConfig realConfig(const std::string &path) {
            LegacySolarConfig config = syntheticConfig();
            config.use_real_solar_data = true;
            config.solar_data_file = path;
            return config;
        }

        // Frozen test-only oracle copied from the pre-I3B-2 production path:
        // ASAPBlockScheduler::collectSolarEnergy/getSolarIrradiance at
        // 4aee3ea8c1602115357148f6d4b7fd2ae7fde61b. It deliberately
        // performs the old per-query file read and shares no implementation
        // helper with LegacySolarSource.
        double preMigrationRealIrradiance(
            const LegacySolarConfig &config,
            std::int64_t time_ms) {
            const std::int64_t actual_time_ms =
                time_ms +
                static_cast<std::int64_t>(config.start_offset_ms);
            const std::int64_t total_minutes =
                actual_time_ms / INT64_C(60000);
            const int line_number =
                static_cast<int>(total_minutes + INT64_C(2));

            std::ifstream file(config.solar_data_file);
            if (!file.is_open()) {
                return 0.0;
            }

            std::string line;
            int current_line = 1;
            while (current_line < line_number &&
                   std::getline(file, line)) {
                ++current_line;
            }
            if (std::getline(file, line)) {
                try {
                    return std::stod(line);
                } catch (const std::exception &) {
                    return 0.0;
                }
            }
            return 0.0;
        }

        double preMigrationOfferedEnergy(
            const LegacySolarConfig &config,
            const HarvestInterval &interval) {
            const std::int64_t current_ms =
                static_cast<std::int64_t>(interval.end_time_ms);
            const std::int64_t elapsed =
                static_cast<std::int64_t>(interval.end_time_ms) -
                static_cast<std::int64_t>(interval.start_time_ms);
            if (elapsed <= 0) {
                return 0.0;
            }

            double energy = 0.0;
            if (config.use_real_solar_data) {
                const double irradiance =
                    preMigrationRealIrradiance(config, current_ms);
                const double elapsed_seconds =
                    static_cast<double>(elapsed) * 0.001;
                energy = irradiance * config.pv_area_m2 *
                         config.pv_efficiency * elapsed_seconds;
            } else {
                const std::int64_t actual_time_ms =
                    current_ms +
                    static_cast<std::int64_t>(
                        config.start_offset_ms);
                const std::int64_t ms_of_day =
                    actual_time_ms % INT64_C(86400000);
                const double hour_of_day =
                    static_cast<double>(ms_of_day) / 3600000.0;

                double time_factor = 0.0;
                if (hour_of_day < 6.0) {
                    time_factor = 0.0;
                } else if (hour_of_day < 11.0) {
                    time_factor = (hour_of_day - 6.0) / 5.0;
                } else if (hour_of_day < 13.0) {
                    time_factor = 1.0;
                } else if (hour_of_day < 18.0) {
                    time_factor = (18.0 - hour_of_day) / 5.0;
                } else {
                    time_factor = 0.0;
                }

                const double peak_irradiance =
                    config.base_harvesting_power_w /
                    (config.pv_area_m2 * config.pv_efficiency);
                const double irradiance =
                    peak_irradiance * time_factor;
                const double elapsed_seconds =
                    static_cast<double>(elapsed) * 0.001;
                energy = irradiance * config.pv_area_m2 *
                         config.pv_efficiency * elapsed_seconds;
            }
            return energy;
        }

        std::string selectedDomainError(
            const LegacySolarSource &source,
            const HarvestInterval &interval) {
            try {
                (void)source.offeredEnergyForInterval(interval);
            } catch (const std::domain_error &error) {
                return error.what();
            } catch (const std::exception &error) {
                ADD_FAILURE()
                    << "expected std::domain_error, got: " << error.what();
                return {};
            }
            ADD_FAILURE() << "expected std::domain_error";
            return {};
        }
    } // namespace

    TEST(LegacySolarSource, MinimalContractIsDeterministicAndUnclipped) {
        static_assert(
            std::is_base_of_v<HarvestSource, LegacySolarSource>,
            "LegacySolarSource must implement HarvestSource");

        LegacySolarConfig config = syntheticConfig();
        config.base_harvesting_power_w = 2000.0;
        std::unique_ptr<HarvestSource> source =
            std::make_unique<LegacySolarSource>(config);
        const HarvestInterval interval{
            7u,
            UINT64_C(39600000),
            UINT64_C(39601000),
        };

        const double first =
            source->offeredEnergyForInterval(interval);
        const double second =
            source->offeredEnergyForInterval(interval);
        expectExactBits(first, second);
        EXPECT_TRUE(std::isfinite(first));
        EXPECT_GE(first, 0.0);
        EXPECT_GT(first, 1000.0);
    }

    TEST(LegacySolarSource,
         RelativePathIsResolvedAtConstructionAndDataIsPreloaded) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "12.5\n");
        const HarvestInterval interval{0u, 0u, 1u};
        std::unique_ptr<LegacySolarSource> source;
        double expected = 0.0;
        {
            ScopedCurrentPath current_path(file.directory());
            LegacySolarConfig config = realConfig(file.filename());
            expected = preMigrationOfferedEnergy(config, interval);
            source = std::make_unique<LegacySolarSource>(config);
        }

        file.removeFile();
        expectExactBits(
            expected, source->offeredEnergyForInterval(interval));
        expectExactBits(
            expected, source->offeredEnergyForInterval(interval));
    }

    TEST(LegacySolarSource, InvalidConfigurationFailsExplicitly) {
        LegacySolarConfig config = syntheticConfig();
        config.base_harvesting_power_w =
            std::numeric_limits<double>::infinity();
        EXPECT_THROW(
            (void)LegacySolarSource(config), std::invalid_argument);

        config = syntheticConfig();
        config.pv_area_m2 = 0.0;
        EXPECT_THROW(
            (void)LegacySolarSource(config), std::invalid_argument);

        config = syntheticConfig();
        config.pv_efficiency =
            std::numeric_limits<double>::quiet_NaN();
        EXPECT_THROW(
            (void)LegacySolarSource(config), std::invalid_argument);
    }

    TEST(LegacySolarSourceGolden, SyntheticMatchesFrozenProductionBits) {
        struct Case {
            const char *label;
            LegacySolarConfig config;
            HarvestInterval interval;
        };

        const auto at = [](std::uint64_t end, std::uint64_t elapsed) {
            return HarvestInterval{0u, end - elapsed, end};
        };
        std::vector<Case> cases;
        cases.push_back(
            {"time-zero", syntheticConfig(), {0u, 0u, 0u}});
        cases.push_back(
            {"before-6h", syntheticConfig(), at(21599999u, 1u)});
        cases.push_back(
            {"at-6h", syntheticConfig(), at(21600000u, 1u)});
        cases.push_back(
            {"after-6h", syntheticConfig(), at(21600001u, 1u)});
        cases.push_back(
            {"before-11h", syntheticConfig(), at(39599999u, 1u)});
        cases.push_back(
            {"at-11h", syntheticConfig(), at(39600000u, 1u)});
        cases.push_back(
            {"after-11h", syntheticConfig(), at(39600001u, 1u)});
        cases.push_back(
            {"before-13h", syntheticConfig(), at(46799999u, 1u)});
        cases.push_back(
            {"at-13h", syntheticConfig(), at(46800000u, 1u)});
        cases.push_back(
            {"after-13h", syntheticConfig(), at(46800001u, 1u)});
        cases.push_back(
            {"before-18h", syntheticConfig(), at(64799999u, 1u)});
        cases.push_back(
            {"at-18h", syntheticConfig(), at(64800000u, 1u)});
        cases.push_back(
            {"after-18h", syntheticConfig(), at(64800001u, 1u)});
        cases.push_back(
            {"24h-wrap", syntheticConfig(), at(86400000u, 1u)});

        LegacySolarConfig offset = syntheticConfig();
        offset.start_offset_ms = UINT64_C(21600000);
        cases.push_back(
            {"nonzero-offset", offset, at(1u, 1u)});

        cases.push_back(
            {"elapsed-many-ms",
             syntheticConfig(),
             at(UINT64_C(39612345), UINT64_C(12345))});

        LegacySolarConfig zero_base = syntheticConfig();
        zero_base.base_harvesting_power_w = 0.0;
        cases.push_back(
            {"zero-base", zero_base, at(39600000u, 1u)});

        LegacySolarConfig nondefault = syntheticConfig();
        nondefault.base_harvesting_power_w = 0.123;
        nondefault.pv_area_m2 = 0.037;
        nondefault.pv_efficiency = 0.213;
        cases.push_back(
            {"nondefault-area-efficiency",
             nondefault,
             at(40123456u, 57u)});

        LegacySolarConfig tiny = syntheticConfig();
        tiny.base_harvesting_power_w =
            std::numeric_limits<double>::min();
        tiny.pv_area_m2 = 1.0;
        tiny.pv_efficiency = 1.0;
        cases.push_back(
            {"smallest-normal-positive",
             tiny,
             at(39600000u, 1000u)});

        for (const Case &test_case : cases) {
            SCOPED_TRACE(test_case.label);
            LegacySolarSource source(test_case.config);
            const double expected = preMigrationOfferedEnergy(
                test_case.config, test_case.interval);
            const double actual =
                source.offeredEnergyForInterval(test_case.interval);
            expectExactBits(expected, actual);
            EXPECT_TRUE(std::isfinite(actual));
            EXPECT_GE(actual, 0.0);
        }

        LegacySolarSource night(syntheticConfig());
        expectPositiveZero(night.offeredEnergyForInterval(
            at(1000u, 1u)));
    }

    TEST(LegacySolarSourceGolden, RealRowsMatchFrozenProductionBits) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "0\n"
            "12.5\n"
            "0.125\n"
            "987.654321\n");
        LegacySolarConfig config = realConfig(file.path());
        LegacySolarSource source(config);

        const std::vector<HarvestInterval> intervals = {
            {0u, 0u, 1u},
            {1u, 59998u, 59999u},
            {2u, 59999u, 60000u},
            {3u, 119999u, 120000u},
            {4u, 100000u, 120000u},
            {5u, 179999u, 180000u},
            {6u, 239999u, 240000u},
            {7u, 1199999u, 1200000u},
        };
        for (const HarvestInterval &interval : intervals) {
            SCOPED_TRACE(interval.index);
            const double expected =
                preMigrationOfferedEnergy(config, interval);
            const double actual =
                source.offeredEnergyForInterval(interval);
            expectExactBits(expected, actual);
            EXPECT_TRUE(std::isfinite(actual));
            EXPECT_GE(actual, 0.0);
        }

        LegacySolarConfig offset = config;
        offset.start_offset_ms = 60000u;
        LegacySolarSource offset_source(offset);
        const HarvestInterval offset_interval{8u, 0u, 1u};
        expectExactBits(
            preMigrationOfferedEnergy(offset, offset_interval),
            offset_source.offeredEnergyForInterval(offset_interval));
    }

    TEST(LegacySolarSourceGolden,
         MissingMalformedEofAndOutOfRangeReturnPositiveZero) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "not-a-number\n"
            "1e99999\n"
            "4.5\n");

        LegacySolarConfig missing =
            realConfig((file.directory() / "missing.txt").string());
        LegacySolarSource missing_source(missing);
        const HarvestInterval row_zero{0u, 0u, 1u};
        expectExactBits(
            preMigrationOfferedEnergy(missing, row_zero),
            missing_source.offeredEnergyForInterval(row_zero));
        expectPositiveZero(
            missing_source.offeredEnergyForInterval(row_zero));

        LegacySolarConfig malformed = realConfig(file.path());
        LegacySolarSource malformed_source(malformed);
        const std::vector<HarvestInterval> zero_intervals = {
            {1u, 0u, 1u},
            {2u, 59999u, 60000u},
            {3u, 179999u, 180000u},
            {4u, 5999999u, 6000000u},
        };
        for (const HarvestInterval &interval : zero_intervals) {
            SCOPED_TRACE(interval.index);
            const double expected =
                preMigrationOfferedEnergy(malformed, interval);
            const double actual =
                malformed_source.offeredEnergyForInterval(interval);
            expectExactBits(expected, actual);
            expectPositiveZero(actual);
        }

        const HarvestInterval valid{5u, 119999u, 120000u};
        expectExactBits(
            preMigrationOfferedEnergy(malformed, valid),
            malformed_source.offeredEnergyForInterval(valid));
    }

    TEST(LegacySolarSourceGolden,
         InvalidDomainRowsThrowWithPathLineAndClassification) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "nan\n"
            "inf\n"
            "-2.5\n");
        LegacySolarConfig config = realConfig(file.path());
        LegacySolarSource source(config);

        const std::vector<std::pair<HarvestInterval, std::string>> cases = {
            {{0u, 0u, 1u}, "line 2: invalid domain (non-finite"},
            {{1u, 59999u, 60000u},
             "line 3: invalid domain (non-finite"},
            {{2u, 119999u, 120000u},
             "line 4: invalid domain (negative"},
        };
        for (const auto &test_case : cases) {
            const std::string message =
                selectedDomainError(source, test_case.first);
            EXPECT_NE(message.find(file.path()), std::string::npos);
            EXPECT_NE(
                message.find(test_case.second), std::string::npos);
        }
    }

    TEST(LegacySolarSourceGolden,
         InvalidDomainIsLazyAndLegalQueriesRecoverAfterException) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "1.25\n"
            "-inf\n"
            "2.5\n");
        LegacySolarConfig config = realConfig(file.path());
        LegacySolarSource source(config);

        const HarvestInterval first{0u, 0u, 1u};
        const HarvestInterval invalid{1u, 59999u, 60000u};
        const HarvestInterval third{2u, 119999u, 120000u};

        expectExactBits(
            preMigrationOfferedEnergy(config, first),
            source.offeredEnergyForInterval(first));
        EXPECT_THROW(
            source.offeredEnergyForInterval(invalid),
            std::domain_error);
        expectExactBits(
            preMigrationOfferedEnergy(config, third),
            source.offeredEnergyForInterval(third));
        expectExactBits(
            preMigrationOfferedEnergy(config, first),
            source.offeredEnergyForInterval(first));
    }

    TEST(LegacySolarSourceGolden,
         NegativeZeroIrradianceIsCanonicalPositiveZero) {
        TemporaryLegacySolarData file(
            "irradiance\n"
            "-0.0\n");
        LegacySolarConfig config = realConfig(file.path());
        LegacySolarSource source(config);
        expectPositiveZero(source.offeredEnergyForInterval(
            HarvestInterval{0u, 0u, 1u}));
    }

} // namespace RTSim
