#include <gtest/gtest.h>

#include <rtsim/harvesting/scaled_piecewise_source.hpp>
#include <rtsim/scheduler/priority_energy_runtime.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace RTSim {
    namespace {
        std::uint64_t binary64Bits(double value) {
            static_assert(sizeof(std::uint64_t) == sizeof(double),
                          "binary64 representation requires 64-bit double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        ScaledPiecewiseConfig piecewise(
            double scale_w,
            std::initializer_list<PiecewiseSegment> segments) {
            ScaledPiecewiseConfig config;
            config.scale_w = scale_w;
            config.segments.assign(segments.begin(), segments.end());
            return config;
        }

        double offered(const ScaledPiecewiseSource &source,
                       std::uint64_t start_time_ms,
                       std::uint64_t end_time_ms,
                       std::uint64_t index = 0) {
            return source.offeredEnergyForInterval(
                {index, start_time_ms, end_time_ms});
        }

        constexpr std::uint64_t fnv_offset_basis =
            UINT64_C(14695981039346656037);
        constexpr std::uint64_t fnv_prime = UINT64_C(1099511628211);

        void appendBinary64Bits(std::uint64_t &hash, std::uint64_t bits) {
            for (unsigned shift = 0; shift < 64; shift += 8) {
                hash ^= (bits >> shift) & UINT64_C(0xff);
                hash *= fnv_prime;
            }
        }

        std::string hexadecimal(std::uint64_t value) {
            std::ostringstream output;
            output << "0x" << std::hex << std::setfill('0') << std::setw(16)
                   << value;
            return output.str();
        }

        PriorityEnergyProfileConfig b4RuntimeConfig(double alpha_w) {
            PriorityEnergyProfileConfig config;
            config.enabled = true;
            config.profile_id = "b4_pe_three_stage_v1";
            config.alpha_w = alpha_w;
            config.horizon_ms = 30000;
            config.tick_ms = 1;
            return config;
        }

        ScaledPiecewiseConfig b4PiecewiseConfig(double alpha_w) {
            return piecewise(
                alpha_w,
                {{0, 5000, 1.0},
                 {5000, 15000, 0.2},
                 {15000, 30000, 1.0}});
        }

        std::size_t b4Segment(std::uint64_t interval_index) {
            if (interval_index < 5000) {
                return 0;
            }
            return interval_index < 15000 ? 1 : 2;
        }

        const char *b4SegmentName(std::size_t segment) {
            switch (segment) {
            case 0:
                return "high[0,5000)";
            case 1:
                return "low[5000,15000)";
            default:
                return "high[15000,30000)";
            }
        }
    } // namespace

    static_assert(std::is_base_of<HarvestSource,
                                  ScaledPiecewiseSource>::value,
                  "ScaledPiecewiseSource must implement HarvestSource");
    static_assert(
        std::is_same<
            decltype(std::declval<const ScaledPiecewiseSource &>()
                         .offeredEnergyForInterval(
                             std::declval<const HarvestInterval &>())),
            double>::value,
        "offered-energy query must be const and return joules as double");

    TEST(ScaledPiecewiseSource, IntegratesSingleSegmentAndContainedInterval) {
        const ScaledPiecewiseSource source(
            piecewise(2.0, {{10, 30, 3.0}}));

        const double expected_full = ((2.0 * 3.0) * 20.0) * 0.001;
        const double expected_inner = ((2.0 * 3.0) * 5.0) * 0.001;
        EXPECT_EQ(binary64Bits(offered(source, 10, 30)),
                  binary64Bits(expected_full));
        EXPECT_EQ(binary64Bits(offered(source, 12, 17)),
                  binary64Bits(expected_inner));
    }

    TEST(ScaledPiecewiseSource, IntegratesAdjacentSegmentsInOriginalOrder) {
        const ScaledPiecewiseSource source(
            piecewise(1.0, {{0, 5, 1.0}, {5, 10, 2.0}}));

        const double first = ((1.0 * 1.0) * 1.0) * 0.001;
        const double second = ((1.0 * 2.0) * 1.0) * 0.001;
        const double expected = first + second;
        EXPECT_EQ(binary64Bits(offered(source, 4, 6)),
                  binary64Bits(expected));
    }

    TEST(ScaledPiecewiseSource, IntegratesAcrossGapsWithoutFillingThem) {
        const ScaledPiecewiseSource source(
            piecewise(1.0, {{0, 5, 2.0}, {10, 15, 3.0}}));

        const double first = ((1.0 * 2.0) * 1.0) * 0.001;
        const double second = ((1.0 * 3.0) * 1.0) * 0.001;
        EXPECT_EQ(binary64Bits(offered(source, 4, 11)),
                  binary64Bits(first + second));
        EXPECT_EQ(binary64Bits(offered(source, 5, 10)), UINT64_C(0));
    }

    TEST(ScaledPiecewiseSource, IntegratesAcrossMoreThanThreeSegments) {
        const ScaledPiecewiseSource source(piecewise(
            1.0,
            {{0, 2, 1.0}, {2, 4, 2.0}, {4, 6, 3.0}, {6, 8, 4.0}}));

        const double first = ((1.0 * 1.0) * 1.0) * 0.001;
        const double second = ((1.0 * 2.0) * 2.0) * 0.001;
        const double third = ((1.0 * 3.0) * 2.0) * 0.001;
        const double fourth = ((1.0 * 4.0) * 1.0) * 0.001;
        const double expected = ((first + second) + third) + fourth;
        EXPECT_EQ(binary64Bits(offered(source, 1, 7)),
                  binary64Bits(expected));
    }

    TEST(ScaledPiecewiseSource, OutsideAndExactBoundariesReturnPositiveZero) {
        const ScaledPiecewiseSource source(
            piecewise(1.0, {{10, 20, 1.0}}));

        for (const HarvestInterval interval :
             std::array<HarvestInterval, 4>{
                 {{0, 0, 10}, {1, 20, 30}, {2, 0, 5}, {3, 25, 30}}}) {
            EXPECT_EQ(binary64Bits(
                          source.offeredEnergyForInterval(interval)),
                      UINT64_C(0));
        }
    }

    TEST(ScaledPiecewiseSource, ZeroScaleMultiplierAndSignedZeroArePositiveZero) {
        const double negative_zero = -0.0;
        const ScaledPiecewiseSource zero_scale(
            piecewise(negative_zero, {{0, 10, 1.0}}));
        const ScaledPiecewiseSource zero_multiplier(
            piecewise(1.0, {{0, 10, negative_zero}}));

        EXPECT_EQ(binary64Bits(offered(zero_scale, 0, 10)), UINT64_C(0));
        EXPECT_EQ(binary64Bits(offered(zero_multiplier, 0, 10)),
                  UINT64_C(0));
    }

    TEST(ScaledPiecewiseSource, SupportsLargeTimestampsWithExactSmallDuration) {
        const std::uint64_t maximum =
            std::numeric_limits<std::uint64_t>::max();
        const ScaledPiecewiseSource source(
            piecewise(1.0, {{maximum - 10, maximum, 1.0}}));
        const double expected = ((1.0 * 1.0) * 6.0) * 0.001;

        EXPECT_EQ(binary64Bits(offered(source, maximum - 8, maximum - 2)),
                  binary64Bits(expected));
    }

    TEST(ScaledPiecewiseSource, RejectsInvalidIntervalsAndNumericOverflow) {
        const ScaledPiecewiseSource source(
            piecewise(1.0, {{0, 10, 1.0}}));
        EXPECT_THROW((void)offered(source, 5, 5), std::invalid_argument);
        EXPECT_THROW((void)offered(source, 6, 5), std::invalid_argument);
        EXPECT_THROW(
            (void)offered(source, 0, (UINT64_C(1) << 53) + 1),
            std::invalid_argument);

        const double maximum = std::numeric_limits<double>::max();
        const ScaledPiecewiseSource overflowing(
            piecewise(maximum, {{0, 1, maximum}}));
        EXPECT_THROW((void)offered(overflowing, 0, 1),
                     std::overflow_error);
    }

    TEST(ScaledPiecewiseSource, RejectsInvalidDirectConfigurations) {
        const double nan = std::numeric_limits<double>::quiet_NaN();
        const double infinity = std::numeric_limits<double>::infinity();
        const auto expect_invalid = [](ScaledPiecewiseConfig config) {
            EXPECT_THROW((void)ScaledPiecewiseSource(std::move(config)),
                         std::invalid_argument);
        };

        expect_invalid(piecewise(nan, {{0, 1, 1.0}}));
        expect_invalid(piecewise(infinity, {{0, 1, 1.0}}));
        expect_invalid(piecewise(-1.0, {{0, 1, 1.0}}));
        expect_invalid(piecewise(1.0, {}));
        expect_invalid(piecewise(1.0, {{1, 1, 1.0}}));
        expect_invalid(piecewise(1.0, {{2, 1, 1.0}}));
        expect_invalid(piecewise(1.0, {{0, 1, nan}}));
        expect_invalid(piecewise(1.0, {{0, 1, infinity}}));
        expect_invalid(piecewise(1.0, {{0, 1, -1.0}}));
        expect_invalid(piecewise(1.0, {{10, 20, 1.0}, {0, 5, 1.0}}));
        expect_invalid(piecewise(1.0, {{0, 10, 1.0}, {9, 20, 1.0}}));
    }

    TEST(ScaledPiecewiseSource, RepeatedAndReorderedQueriesAreBitStable) {
        const ScaledPiecewiseSource source(piecewise(
            0.054,
            {{0, 5, 1.0}, {5, 15, 0.2}, {20, 30, 1.5}}));
        const std::array<HarvestInterval, 6> intervals = {
            {{9, 0, 1},
             {2, 4, 6},
             {100, 10, 25},
             {7, 15, 20},
             {4, 21, 29},
             {0, 0, 30}}};
        std::array<std::uint64_t, intervals.size()> forward{};
        std::array<std::uint64_t, intervals.size()> reverse{};

        for (std::size_t index = 0; index < intervals.size(); ++index) {
            forward[index] = binary64Bits(
                source.offeredEnergyForInterval(intervals[index]));
            EXPECT_EQ(
                binary64Bits(source.offeredEnergyForInterval(intervals[index])),
                forward[index]);
        }
        for (std::size_t reverse_index = intervals.size();
             reverse_index > 0;
             --reverse_index) {
            const std::size_t index = reverse_index - 1;
            reverse[index] = binary64Bits(
                source.offeredEnergyForInterval(intervals[index]));
        }
        EXPECT_EQ(forward, reverse);
    }

    TEST(ScaledPiecewiseSourceB4,
         ThirtyThousandIntervalsMatchPriorityRuntimeOfferedBits) {
        const std::array<double, 5> alphas = {
            0.0,
            1.0,
            0.054,
            std::nextafter(1.0, 0.0),
            std::nextafter(1.0, std::numeric_limits<double>::infinity())};
        const std::array<std::size_t, 3> expected_counts =
            {{5000, 10000, 15000}};

        for (const double alpha : alphas) {
            const std::uint64_t alpha_bits = binary64Bits(alpha);
            SCOPED_TRACE("alpha_bits=" + hexadecimal(alpha_bits));
            const ScaledPiecewiseSource source(b4PiecewiseConfig(alpha));
            const PriorityEnergyRuntime oracle(b4RuntimeConfig(alpha));
            std::uint64_t source_hash = fnv_offset_basis;
            std::uint64_t oracle_hash = fnv_offset_basis;
            std::array<std::uint64_t, 3> source_segment_hashes =
                {{fnv_offset_basis, fnv_offset_basis, fnv_offset_basis}};
            std::array<std::uint64_t, 3> oracle_segment_hashes =
                {{fnv_offset_basis, fnv_offset_basis, fnv_offset_basis}};
            std::array<std::size_t, 3> counts{};

            for (std::uint64_t index = 0; index < 30000; ++index) {
                const double actual = source.offeredEnergyForInterval(
                    {index, index, index + 1});
                const double expected =
                    oracle
                        .applyHarvest(
                            index + 1,
                            0.0,
                            std::numeric_limits<double>::max())
                        .offered_j;
                const std::uint64_t actual_bits = binary64Bits(actual);
                const std::uint64_t expected_bits = binary64Bits(expected);
                const std::size_t segment = b4Segment(index);
                if (actual_bits != expected_bits) {
                    ADD_FAILURE()
                        << "alpha_bits=" << hexadecimal(alpha_bits)
                        << " interval_index=" << index
                        << " expected_bits=" << hexadecimal(expected_bits)
                        << " actual_bits=" << hexadecimal(actual_bits)
                        << " segment=" << b4SegmentName(segment);
                    return;
                }

                appendBinary64Bits(source_hash, actual_bits);
                appendBinary64Bits(oracle_hash, expected_bits);
                appendBinary64Bits(source_segment_hashes[segment], actual_bits);
                appendBinary64Bits(
                    oracle_segment_hashes[segment], expected_bits);
                ++counts[segment];
            }

            EXPECT_EQ(source_hash, oracle_hash);
            EXPECT_EQ(source_segment_hashes, oracle_segment_hashes);
            EXPECT_EQ(counts, expected_counts);
            std::cout << "[B4 offered sequence hash] alpha_bits="
                      << hexadecimal(alpha_bits)
                      << " all=" << hexadecimal(source_hash)
                      << " high0=" << hexadecimal(source_segment_hashes[0])
                      << " low=" << hexadecimal(source_segment_hashes[1])
                      << " high1=" << hexadecimal(source_segment_hashes[2])
                      << '\n';
        }
    }

} // namespace RTSim
