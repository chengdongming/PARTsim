#include <gtest/gtest.h>

#include <rtsim/harvesting/sampled_trace_source.hpp>
#include <rtsim/harvesting/sha256.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unistd.h>
#include <utility>
#include <vector>

namespace RTSim {
    namespace {
        class TemporaryTraceDirectory {
        public:
            TemporaryTraceDirectory() {
                char pattern[] = "/tmp/partsim_sampled_trace_XXXXXX";
                char *created = ::mkdtemp(pattern);
                if (created == nullptr) {
                    throw std::runtime_error(
                        "cannot create sampled-trace temporary directory");
                }
                _path = created;
            }

            ~TemporaryTraceDirectory() {
                std::error_code error;
                std::filesystem::remove_all(_path, error);
            }

            const std::filesystem::path &path() const noexcept {
                return _path;
            }

            std::filesystem::path write(const std::string &name,
                                        const std::string &contents) const {
                const std::filesystem::path file = _path / name;
                std::ofstream output(
                    file, std::ios::binary | std::ios::trunc);
                if (!output.is_open()) {
                    throw std::runtime_error("cannot write temporary trace");
                }
                output.write(contents.data(),
                             static_cast<std::streamsize>(contents.size()));
                if (!output.good()) {
                    throw std::runtime_error("cannot finish temporary trace");
                }
                return file;
            }

        private:
            std::filesystem::path _path;
        };

        class ScopedCurrentPath {
        public:
            explicit ScopedCurrentPath(const std::filesystem::path &path) :
                _original(std::filesystem::current_path()) {
                std::filesystem::current_path(path);
            }

            ~ScopedCurrentPath() {
                std::error_code error;
                std::filesystem::current_path(_original, error);
            }

        private:
            std::filesystem::path _original;
        };

        std::uint64_t binary64Bits(double value) {
            static_assert(sizeof(double) == sizeof(std::uint64_t),
                          "sampled trace tests require binary64 double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        double roundedMultiply(double lhs, double rhs) {
            volatile double value = lhs * rhs;
            return value;
        }

        double roundedAdd(double lhs, double rhs) {
            volatile double value = lhs + rhs;
            return value;
        }

        SampledTraceConfig electricalConfig(
            const std::filesystem::path &path) {
            SampledTraceConfig config;
            config.file = path.string();
            config.time_column = "timestamp_ms";
            config.value_column = "power_w";
            config.value_type = TraceValueType::ElectricalPower;
            config.interpolation = TraceInterpolation::ZeroOrderHold;
            config.after_trace = TraceAfterEnd::Zero;
            config.panel_area_m2 = 0.0;
            config.conversion_efficiency = 0.0;
            config.max_file_size_bytes = UINT64_C(1048576);
            config.max_rows = UINT64_C(10000);
            return config;
        }

        SampledTraceConfig irradianceConfig(
            const std::filesystem::path &path,
            double area,
            double efficiency) {
            SampledTraceConfig config = electricalConfig(path);
            config.value_column = "irradiance_w_m2";
            config.value_type = TraceValueType::Irradiance;
            config.panel_area_m2 = area;
            config.conversion_efficiency = efficiency;
            return config;
        }

        double query(const SampledTraceSource &source,
                     std::uint64_t start,
                     std::uint64_t end,
                     std::uint64_t index = 0) {
            return source.offeredEnergyForInterval({index, start, end});
        }

        void expectConstructionFailure(
            const std::string &contents,
            const std::function<void(SampledTraceConfig &)> &mutate = {}) {
            TemporaryTraceDirectory directory;
            const std::filesystem::path path =
                directory.write("trace.csv", contents);
            SampledTraceConfig config = electricalConfig(path);
            if (mutate) {
                mutate(config);
            }
            try {
                const SampledTraceSource source(config);
                (void)source;
                ADD_FAILURE() << "expected sampled trace construction failure";
            } catch (const std::exception &error) {
                const std::string message = error.what();
                EXPECT_NE(message.find(config.file), std::string::npos)
                    << message;
                EXPECT_NE(message.find("line "), std::string::npos)
                    << message;
            }
        }

        double directZohOracle(
            const std::vector<std::uint64_t> &timestamps,
            const std::vector<double> &powers,
            std::uint64_t start,
            std::uint64_t end) {
            double total = 0.0;
            for (std::size_t index = 0; index + 1 < timestamps.size(); ++index) {
                const std::uint64_t overlap_start =
                    std::max(start, timestamps[index]);
                const std::uint64_t overlap_end =
                    std::min(end, timestamps[index + 1]);
                if (overlap_start >= overlap_end) {
                    continue;
                }
                double energy = roundedMultiply(
                    powers[index],
                    static_cast<double>(overlap_end - overlap_start));
                energy = roundedMultiply(energy, 0.001);
                total = roundedAdd(total, energy);
            }
            return total == 0.0 ? 0.0 : total;
        }

        bool isLowerHexDigest(const std::string &digest) {
            return digest.size() == 64 &&
                   std::all_of(
                       digest.begin(), digest.end(), [](char value) {
                           return (value >= '0' && value <= '9') ||
                                  (value >= 'a' && value <= 'f');
                       });
        }
    } // namespace

    static_assert(std::is_base_of<HarvestSource, SampledTraceSource>::value,
                  "SampledTraceSource must implement HarvestSource");
    static_assert(
        std::is_same<
            decltype(std::declval<const SampledTraceSource &>()
                         .offeredEnergyForInterval(
                             std::declval<const HarvestInterval &>())),
            double>::value,
        "sampled trace query must be const and return double joules");

    TEST(SampledTraceSource, ElectricalPowerUsesZohAndTerminalSentinel) {
        TemporaryTraceDirectory directory;
        const auto path = directory.write(
            "electrical.csv",
            "timestamp_ms,power_w\n"
            "100,2\n"
            "110,4\n"
            "125,1\n"
            "140,0\n");
        const SampledTraceSource source(electricalConfig(path));

        EXPECT_EQ(source.sampleCount(), 4u);
        EXPECT_EQ(binary64Bits(query(source, 0, 100)), UINT64_C(0));
        EXPECT_EQ(binary64Bits(query(source, 100, 105)),
                  binary64Bits((2.0 * 5.0) * 0.001));
        EXPECT_EQ(binary64Bits(query(source, 110, 125)),
                  binary64Bits((4.0 * 15.0) * 0.001));
        EXPECT_EQ(binary64Bits(query(source, 140, 1000)), UINT64_C(0));
        EXPECT_EQ(binary64Bits(query(source, 0, 1000)),
                  binary64Bits(
                      ((2.0 * 10.0) * 0.001 +
                       (4.0 * 15.0) * 0.001) +
                      (1.0 * 15.0) * 0.001));

        const std::vector<std::uint64_t> timestamps = {100, 110, 125, 140};
        const std::vector<double> powers = {2.0, 4.0, 1.0, 0.0};
        const double expected =
            directZohOracle(timestamps, powers, 105, 130);
        EXPECT_NEAR(query(source, 105, 130), expected, 1e-15);
    }

    TEST(SampledTraceSource, IrradianceUsesFrozenConversionOrder) {
        TemporaryTraceDirectory directory;
        const auto path = directory.write(
            "irradiance.csv",
            "timestamp_ms,irradiance_w_m2\n"
            "0,3.125\n"
            "7,0\n");
        const double area = 0.7;
        const double efficiency = 0.3;
        const SampledTraceSource source(
            irradianceConfig(path, area, efficiency));

        double expected_power = roundedMultiply(3.125, area);
        expected_power = roundedMultiply(expected_power, efficiency);
        double expected_energy = roundedMultiply(expected_power, 7.0);
        expected_energy = roundedMultiply(expected_energy, 0.001);
        EXPECT_EQ(binary64Bits(query(source, 0, 7)),
                  binary64Bits(expected_energy));
    }

    TEST(SampledTraceSource, SingleZeroSentinelIsValidAndAlwaysPositiveZero) {
        TemporaryTraceDirectory directory;
        const auto path = directory.write(
            "zero.csv", "timestamp_ms,power_w\n123,-0.0\n");
        const SampledTraceSource source(electricalConfig(path));
        EXPECT_EQ(source.sampleCount(), 1u);
        EXPECT_EQ(binary64Bits(query(source, 0, 123)), UINT64_C(0));
        EXPECT_EQ(binary64Bits(query(source, 123, 1000)), UINT64_C(0));
    }

    TEST(SampledTraceQuery, IsPreloadedStatelessAndIndexIndependent) {
        TemporaryTraceDirectory directory;
        const std::string contents =
            "timestamp_ms,power_w\n0,2\n10,1\n20,0\n";
        const auto path = directory.write("relative.csv", contents);
        std::unique_ptr<SampledTraceSource> source;
        {
            ScopedCurrentPath cwd(directory.path());
            SampledTraceConfig config =
                electricalConfig(std::filesystem::path("relative.csv"));
            source = std::make_unique<SampledTraceSource>(config);
        }

        ASSERT_TRUE(source->resolvedPath().is_absolute());
        EXPECT_EQ(source->resolvedPath(), path.lexically_normal());
        const std::uint64_t expected_bits =
            binary64Bits(query(*source, 5, 15, 1));
        const std::string raw_hash = source->rawFileSha256();
        const std::string normalized_hash = source->normalizedTraceSha256();

        directory.write("relative.csv", "not,a,valid,trace\n");
        EXPECT_EQ(binary64Bits(query(*source, 5, 15, 999)), expected_bits);
        EXPECT_EQ(source->rawFileSha256(), raw_hash);
        EXPECT_EQ(source->normalizedTraceSha256(), normalized_hash);
        ASSERT_TRUE(std::filesystem::remove(path));
        EXPECT_EQ(binary64Bits(query(*source, 5, 15, 0)), expected_bits);

        const std::array<HarvestInterval, 4> intervals =
            {{{4, 0, 5}, {1, 5, 15}, {8, 15, 25}, {0, 0, 25}}};
        std::array<std::uint64_t, intervals.size()> forward{};
        std::array<std::uint64_t, intervals.size()> reverse{};
        for (std::size_t index = 0; index < intervals.size(); ++index) {
            forward[index] = binary64Bits(
                source->offeredEnergyForInterval(intervals[index]));
        }
        for (std::size_t reverse_index = intervals.size();
             reverse_index > 0;
             --reverse_index) {
            const std::size_t index = reverse_index - 1;
            reverse[index] = binary64Bits(
                source->offeredEnergyForInterval(intervals[index]));
        }
        EXPECT_EQ(forward, reverse);
    }

    TEST(SampledTraceQuery, NonUniformLongIntervalsMatchIndependentZohOracle) {
        TemporaryTraceDirectory directory;
        const auto path = directory.write(
            "nonuniform.csv",
            "timestamp_ms,power_w\n"
            "11,0.25\n"
            "19,1.5\n"
            "37,0.125\n"
            "90,2.0\n"
            "144,0\n");
        const SampledTraceSource source(electricalConfig(path));
        const std::vector<std::uint64_t> timestamps = {11, 19, 37, 90, 144};
        const std::vector<double> powers = {0.25, 1.5, 0.125, 2.0, 0.0};

        for (const std::pair<std::uint64_t, std::uint64_t> interval :
             std::array<std::pair<std::uint64_t, std::uint64_t>, 6>{
                 {{0, 11}, {0, 144}, {12, 18}, {15, 100}, {38, 143}, {90, 500}}}) {
            const double expected = directZohOracle(
                timestamps, powers, interval.first, interval.second);
            EXPECT_NEAR(
                query(source, interval.first, interval.second),
                expected,
                std::max(1e-15, std::abs(expected) * 1e-14));
        }
        EXPECT_THROW((void)query(source, 5, 5), std::invalid_argument);
        EXPECT_THROW((void)query(source, 6, 5), std::invalid_argument);
        EXPECT_THROW(
            (void)query(source, 0, (UINT64_C(1) << 53) + 1),
            std::invalid_argument);
    }

    TEST(SampledTraceParser, AcceptsLfCrlfBomWhitespaceAndExtraColumns) {
        TemporaryTraceDirectory directory;
        const auto lf = directory.write(
            "lf.csv",
            " timestamp_ms , power_w , ignored \n"
            " 0 , 2 , alpha \n"
            " 10 , -0.0 , beta \n");
        const auto crlf = directory.write(
            "crlf.csv",
            std::string("\xef\xbb\xbf", 3) +
                "timestamp_ms,power_w,ignored\r\n"
                "0,2,changed\r\n"
                "10,0,again\r\n");
        const SampledTraceSource first(electricalConfig(lf));
        const SampledTraceSource second(electricalConfig(crlf));
        EXPECT_EQ(first.sampleCount(), 2u);
        EXPECT_EQ(second.sampleCount(), 2u);
        EXPECT_EQ(binary64Bits(query(first, 0, 10)),
                  binary64Bits(query(second, 0, 10)));
        EXPECT_NE(first.rawFileSha256(), second.rawFileSha256());
        EXPECT_EQ(first.normalizedTraceSha256(),
                  second.normalizedTraceSha256());
    }

    TEST(SampledTraceParser, RejectsInvalidHeadersAndCsvStructure) {
        const std::vector<std::string> invalid = {
            "",
            "timestamp_ms,power_w\n",
            "timestamp_ms,power_w,power_w\n0,1,2\n1,0,0\n",
            "timestamp_ms,other\n0,1\n1,0\n",
            "timestamp_ms,,power_w\n0,x,1\n1,x,0\n",
            "timestamp_ms,power_w\n0,1,extra\n1,0\n",
            "timestamp_ms,power_w\n0,1\n\n1,0\n",
            "timestamp_ms,power_w\n\"0\",1\n1,0\n",
            "timestamp_ms,power_w\r0,1\n1,0\n",
            std::string("timestamp_ms,power_w\n0,1\n", 25) +
                std::string("\xef\xbb\xbf", 3) + "1,0\n",
        };
        for (const std::string &contents : invalid) {
            SCOPED_TRACE(contents);
            expectConstructionFailure(contents);
        }
    }

    TEST(SampledTraceParser, RejectsInvalidTimestamps) {
        const std::vector<std::string> invalid_timestamps = {
            "+0", "-0", "1.0", "1e3", "true", "1x",
            "18446744073709551616"};
        for (const std::string &timestamp : invalid_timestamps) {
            SCOPED_TRACE(timestamp);
            expectConstructionFailure(
                "timestamp_ms,power_w\n" + timestamp +
                ",1\n18446744073709551615,0\n");
        }
        expectConstructionFailure(
            "timestamp_ms,power_w\n0,1\n0,0\n");
        expectConstructionFailure(
            "timestamp_ms,power_w\n10,1\n9,0\n");
    }

    TEST(SampledTraceParser, RejectsInvalidValuesAndMissingSentinel) {
        for (const std::string &value :
             std::vector<std::string>{"nan", "inf", "-1", "true", "1x", ""}) {
            SCOPED_TRACE(value);
            expectConstructionFailure(
                "timestamp_ms,power_w\n0," + value + "\n1,0\n");
        }
        expectConstructionFailure(
            "timestamp_ms,power_w\n0,1\n1,2\n");
    }

    TEST(SampledTraceParser, EnforcesDirectConfigAndResourceLimits) {
        const std::string valid =
            "timestamp_ms,power_w\n0,1\n10,0\n";
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.max_file_size_bytes = 1;
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.max_rows = 1;
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.max_rows = 0;
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.time_column = config.value_column;
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.interpolation = static_cast<TraceInterpolation>(99);
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.after_trace = static_cast<TraceAfterEnd>(99);
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.value_type = static_cast<TraceValueType>(99);
        });
        expectConstructionFailure(valid, [](SampledTraceConfig &config) {
            config.panel_area_m2 = 1.0;
        });

        TemporaryTraceDirectory directory;
        SampledTraceConfig missing =
            electricalConfig(directory.path() / "missing.csv");
        try {
            const SampledTraceSource source(missing);
            (void)source;
            ADD_FAILURE() << "missing trace must fail";
        } catch (const std::exception &error) {
            EXPECT_NE(std::string(error.what()).find(missing.file),
                      std::string::npos);
            EXPECT_NE(std::string(error.what()).find("line 0"),
                      std::string::npos);
        }
    }

    TEST(SampledTraceParser, RejectsDurationPrefixAndConversionOverflow) {
        expectConstructionFailure(
            "timestamp_ms,power_w\n"
            "0,1\n"
            "9007199254740993,0\n");
        expectConstructionFailure(
            "timestamp_ms,power_w\n"
            "0,1.7976931348623157e308\n"
            "2,0\n");
        expectConstructionFailure(
            "timestamp_ms,irradiance_w_m2\n"
            "0,1.7976931348623157e308\n"
            "1,0\n",
            [](SampledTraceConfig &config) {
                config.value_column = "irradiance_w_m2";
                config.value_type = TraceValueType::Irradiance;
                config.panel_area_m2 =
                    std::numeric_limits<double>::max();
                config.conversion_efficiency = 1.0;
            });
    }

    TEST(SampledTraceHash, RawAndNormalizedIdentityFollowFrozenDomain) {
        TemporaryTraceDirectory directory;
        const std::string lf_contents =
            "timestamp_ms,power_w,ignored\n0,2,a\n10,0,b\n";
        const std::string crlf_contents =
            "timestamp_ms,power_w,ignored\r\n"
            "0,2,changed\r\n"
            "10,0,again\r\n";
        const auto lf = directory.write("lf.csv", lf_contents);
        const auto crlf = directory.write("crlf.csv", crlf_contents);
        const SampledTraceSource first(electricalConfig(lf));
        const SampledTraceSource second(electricalConfig(crlf));

        EXPECT_EQ(first.rawFileSha256(), sha256Hex(lf_contents));
        EXPECT_NE(first.rawFileSha256(), second.rawFileSha256());
        EXPECT_EQ(first.normalizedTraceSha256(),
                  second.normalizedTraceSha256());
        EXPECT_EQ(
            first.normalizedTraceSha256(),
            "f0b36ef3c819e53ff295ca5db4db047b"
            "6b64cad3109d40c815908a5ccd7f442e");
        EXPECT_TRUE(isLowerHexDigest(first.rawFileSha256()));
        EXPECT_TRUE(isLowerHexDigest(first.normalizedTraceSha256()));
        std::cout << "[SampledTrace identity] raw="
                  << first.rawFileSha256()
                  << " normalized=" << first.normalizedTraceSha256()
                  << '\n';
    }

    TEST(SampledTraceHash, TimestampValueAndValueTypeChangeNormalizedIdentity) {
        TemporaryTraceDirectory directory;
        const auto base_path = directory.write(
            "base.csv", "timestamp_ms,power_w\n0,2\n10,0\n");
        const auto time_path = directory.write(
            "time.csv", "timestamp_ms,power_w\n0,2\n11,0\n");
        const auto value_path = directory.write(
            "value.csv", "timestamp_ms,power_w\n0,3\n10,0\n");
        const auto irradiance_path = directory.write(
            "irradiance.csv",
            "timestamp_ms,irradiance_w_m2\n0,2\n10,0\n");
        const SampledTraceSource base(electricalConfig(base_path));
        const SampledTraceSource changed_time(electricalConfig(time_path));
        const SampledTraceSource changed_value(electricalConfig(value_path));
        const SampledTraceSource irradiance(
            irradianceConfig(irradiance_path, 1.0, 1.0));

        EXPECT_NE(base.normalizedTraceSha256(),
                  changed_time.normalizedTraceSha256());
        EXPECT_NE(base.normalizedTraceSha256(),
                  changed_value.normalizedTraceSha256());
        EXPECT_EQ(binary64Bits(query(base, 0, 10)),
                  binary64Bits(query(irradiance, 0, 10)));
        EXPECT_NE(base.normalizedTraceSha256(),
                  irradiance.normalizedTraceSha256());
    }

} // namespace RTSim
