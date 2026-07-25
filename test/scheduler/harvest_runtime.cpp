#include <gtest/gtest.h>

#include <rtsim/harvesting/harvest_runtime.hpp>
#include <rtsim/harvesting/scaled_piecewise_source.hpp>
#include <rtsim/scheduler/priority_energy_runtime.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace RTSim {
    namespace {
        class ControlledSourceFailure final : public std::runtime_error {
        public:
            ControlledSourceFailure() :
                std::runtime_error("controlled harvest source failure") {}
        };

        class ControlledSource final : public HarvestSource {
        public:
            explicit ControlledSource(double offered_j) :
                offered_j(offered_j) {}

            double offeredEnergyForInterval(
                const HarvestInterval &interval) const override {
                queries.push_back(interval);
                if (throw_on_query) {
                    throw ControlledSourceFailure();
                }
                return offered_j;
            }

            double offered_j = 0.0;
            bool throw_on_query = false;
            mutable std::vector<HarvestInterval> queries;
        };

        std::uint64_t binary64Bits(double value) {
            static_assert(sizeof(double) == sizeof(std::uint64_t),
                          "harvest runtime tests require binary64 double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        ScaledPiecewiseConfig b4PiecewiseConfig(double alpha_w) {
            ScaledPiecewiseConfig config;
            config.scale_w = alpha_w;
            config.segments = {
                {0, 5000, 1.0},
                {5000, 15000, 0.2},
                {15000, 30000, 1.0},
            };
            return config;
        }

        PriorityEnergyProfileConfig b4PriorityConfig(double alpha_w) {
            PriorityEnergyProfileConfig config;
            config.enabled = true;
            config.profile_id = "b4_pe_three_stage_v1";
            config.alpha_w = alpha_w;
            config.horizon_ms = 30000;
            config.tick_ms = 1;
            return config;
        }

        void expectResultBitsEqual(
            const HarvestResult &actual,
            const PriorityEnergyHarvestStep &expected,
            double expected_before) {
            EXPECT_EQ(binary64Bits(actual.offered_j),
                      binary64Bits(expected.offered_j));
            EXPECT_EQ(binary64Bits(actual.actual_j),
                      binary64Bits(expected.actual_j));
            EXPECT_EQ(binary64Bits(actual.clipped_j),
                      binary64Bits(expected.clipped_j));
            EXPECT_EQ(binary64Bits(actual.battery_before_j),
                      binary64Bits(expected_before));
            EXPECT_EQ(binary64Bits(actual.battery_after_j),
                      binary64Bits(expected.battery_after_j));
        }
    } // namespace

    TEST(HarvestRuntimeOwnership, RejectsNullAndExposesOwnedSource) {
        EXPECT_THROW(HarvestRuntime(nullptr), std::invalid_argument);

        auto source = std::make_unique<ControlledSource>(0.25);
        const ControlledSource *identity = source.get();
        HarvestRuntime runtime(std::move(source));
        EXPECT_EQ(&runtime.source(), identity);
        EXPECT_FALSE(runtime.hasAppliedInterval());
        EXPECT_THROW((void)runtime.lastAppliedIndex(), std::logic_error);
    }

    TEST(HarvestRuntimeOwnership, DirectSourceQueriesDoNotAdvanceLedger) {
        auto source = std::make_unique<ControlledSource>(0.125);
        ControlledSource *probe = source.get();
        HarvestRuntime runtime(std::move(source));

        EXPECT_DOUBLE_EQ(
            runtime.source().offeredEnergyForInterval({99, 100, 101}),
            0.125);
        EXPECT_DOUBLE_EQ(
            runtime.source().offeredEnergyForInterval({1, 0, 1}),
            0.125);
        EXPECT_FALSE(runtime.hasAppliedInterval());

        const HarvestResult result = runtime.applyInterval({7, 20, 21}, 0.0, 1.0);
        EXPECT_DOUBLE_EQ(result.offered_j, 0.125);
        EXPECT_EQ(runtime.lastAppliedIndex(), 7u);
        ASSERT_EQ(probe->queries.size(), 3u);
    }

    TEST(HarvestRuntimeLedger, AcceptsArbitraryFirstAndStrictContinuations) {
        auto source = std::make_unique<ControlledSource>(0.1);
        HarvestRuntime runtime(std::move(source));

        const HarvestResult first = runtime.applyInterval({7, 100, 110}, 1.0, 2.0);
        const HarvestResult second = runtime.applyInterval({8, 110, 120}, 1.25, 2.0);
        const HarvestResult third = runtime.applyInterval({9, 120, 135}, 0.0, 2.0);

        EXPECT_TRUE(runtime.hasAppliedInterval());
        EXPECT_EQ(runtime.lastAppliedIndex(), 9u);
        EXPECT_DOUBLE_EQ(first.battery_before_j, 1.0);
        EXPECT_DOUBLE_EQ(second.battery_before_j, 1.25);
        EXPECT_DOUBLE_EQ(third.battery_before_j, 0.0);
    }

    TEST(HarvestRuntimeLedger,
         RejectsDuplicatesSkipsGapsOverlapsAndThenRecovers) {
        auto source = std::make_unique<ControlledSource>(0.1);
        ControlledSource *probe = source.get();
        HarvestRuntime runtime(std::move(source));
        (void)runtime.applyInterval({10, 100, 110}, 0.0, 1.0);

        EXPECT_THROW((void)runtime.applyInterval({10, 110, 120}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({9, 110, 120}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({12, 110, 120}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({11, 111, 120}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({11, 109, 120}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({11, 110, 110}, 0.0, 1.0),
                     std::invalid_argument);
        EXPECT_EQ(runtime.lastAppliedIndex(), 10u);
        EXPECT_EQ(probe->queries.size(), 1u);

        (void)runtime.applyInterval({11, 110, 120}, 0.0, 1.0);
        EXPECT_EQ(runtime.lastAppliedIndex(), 11u);
        EXPECT_EQ(probe->queries.size(), 2u);
    }

    TEST(HarvestRuntimeLedger, RejectsSuccessorAfterMaximumIndex) {
        auto source = std::make_unique<ControlledSource>(0.0);
        ControlledSource *probe = source.get();
        HarvestRuntime runtime(std::move(source));
        const std::uint64_t maximum = std::numeric_limits<std::uint64_t>::max();

        (void)runtime.applyInterval({maximum, 0, 1}, 0.0, 1.0);
        EXPECT_THROW((void)runtime.applyInterval({0, 1, 2}, 0.0, 1.0),
                     std::overflow_error);
        EXPECT_EQ(runtime.lastAppliedIndex(), maximum);
        EXPECT_EQ(probe->queries.size(), 1u);
    }

    TEST(HarvestRuntimeLedger, SourceExceptionsNeverAdvanceAndRecoveryWorks) {
        auto source = std::make_unique<ControlledSource>(0.2);
        ControlledSource *probe = source.get();
        probe->throw_on_query = true;
        HarvestRuntime runtime(std::move(source));

        EXPECT_THROW((void)runtime.applyInterval({20, 0, 1}, 0.0, 1.0),
                     ControlledSourceFailure);
        EXPECT_FALSE(runtime.hasAppliedInterval());
        EXPECT_EQ(&runtime.source(), probe);

        probe->throw_on_query = false;
        (void)runtime.applyInterval({20, 0, 1}, 0.0, 1.0);
        probe->throw_on_query = true;
        EXPECT_THROW((void)runtime.applyInterval({21, 1, 2}, 0.0, 1.0),
                     ControlledSourceFailure);
        EXPECT_EQ(runtime.lastAppliedIndex(), 20u);

        probe->throw_on_query = false;
        (void)runtime.applyInterval({21, 1, 2}, 0.0, 1.0);
        EXPECT_EQ(runtime.lastAppliedIndex(), 21u);
    }

    TEST(HarvestRuntimeNumeric, InvalidBatteryFailsBeforeSourceAndRecovers) {
        auto source = std::make_unique<ControlledSource>(0.1);
        ControlledSource *probe = source.get();
        HarvestRuntime runtime(std::move(source));
        const double nan = std::numeric_limits<double>::quiet_NaN();
        const double infinity = std::numeric_limits<double>::infinity();

        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, -0.1, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, 1.1, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, 0.0, -1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, nan, 1.0),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, infinity, infinity),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, 0.0, nan),
                     std::invalid_argument);
        EXPECT_THROW((void)runtime.applyInterval({3, 0, 1}, 0.0, infinity),
                     std::invalid_argument);
        EXPECT_FALSE(runtime.hasAppliedInterval());
        EXPECT_TRUE(probe->queries.empty());

        (void)runtime.applyInterval({3, 0, 1}, 0.0, 1.0);
        EXPECT_EQ(runtime.lastAppliedIndex(), 3u);
    }

    TEST(HarvestRuntimeNumeric, InvalidOfferedFailsClosedAndRecovers) {
        auto source = std::make_unique<ControlledSource>(0.0);
        ControlledSource *probe = source.get();
        HarvestRuntime runtime(std::move(source));

        for (const double invalid : {
                 std::numeric_limits<double>::quiet_NaN(),
                 std::numeric_limits<double>::infinity(),
                 -0.1}) {
            probe->offered_j = invalid;
            EXPECT_THROW((void)runtime.applyInterval({5, 10, 11}, 0.0, 1.0),
                         std::domain_error);
            EXPECT_FALSE(runtime.hasAppliedInterval());
        }

        probe->offered_j = 0.25;
        const HarvestResult result = runtime.applyInterval({5, 10, 11}, 0.0, 1.0);
        EXPECT_DOUBLE_EQ(result.actual_j, 0.25);
        EXPECT_EQ(runtime.lastAppliedIndex(), 5u);
    }

    TEST(HarvestRuntimeNumeric, ReturnsFiveFieldsAndExactClippingDifference) {
        auto source = std::make_unique<ControlledSource>(0.75);
        HarvestRuntime runtime(std::move(source));

        const HarvestResult result = runtime.applyInterval({0, 0, 1}, 2.0, 2.5);
        EXPECT_EQ(binary64Bits(result.offered_j), binary64Bits(0.75));
        EXPECT_EQ(binary64Bits(result.actual_j), binary64Bits(0.5));
        EXPECT_EQ(binary64Bits(result.clipped_j), binary64Bits(0.75 - 0.5));
        EXPECT_EQ(binary64Bits(result.battery_before_j), binary64Bits(2.0));
        EXPECT_EQ(binary64Bits(result.battery_after_j), binary64Bits(2.5));
        EXPECT_LE(result.actual_j, result.offered_j);
        EXPECT_GE(result.clipped_j, 0.0);
        EXPECT_EQ(result.battery_after_j,
                  result.battery_before_j + result.actual_j);
    }

    TEST(HarvestRuntimeNumeric,
         FullZeroCapacityAndZeroOfferPreserveFrozenZeroSemantics) {
        {
            auto source = std::make_unique<ControlledSource>(1.0);
            HarvestRuntime runtime(std::move(source));
            const HarvestResult full = runtime.applyInterval({0, 0, 1}, 2.0, 2.0);
            EXPECT_EQ(binary64Bits(full.actual_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(full.clipped_j), binary64Bits(1.0));
            EXPECT_EQ(binary64Bits(full.battery_after_j), binary64Bits(2.0));
        }
        {
            auto source = std::make_unique<ControlledSource>(1.0);
            HarvestRuntime runtime(std::move(source));
            const HarvestResult zero_capacity =
                runtime.applyInterval({0, 0, 1}, 0.0, 0.0);
            EXPECT_EQ(binary64Bits(zero_capacity.actual_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(zero_capacity.clipped_j), binary64Bits(1.0));
            EXPECT_EQ(binary64Bits(zero_capacity.battery_after_j), UINT64_C(0));
        }
        {
            const double negative_zero = -0.0;
            auto source = std::make_unique<ControlledSource>(negative_zero);
            HarvestRuntime runtime(std::move(source));
            const HarvestResult zero =
                runtime.applyInterval({44, 100, 101}, negative_zero, 1.0);
            EXPECT_EQ(binary64Bits(zero.offered_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(zero.actual_j), UINT64_C(0));
            EXPECT_EQ(binary64Bits(zero.clipped_j), UINT64_C(0));
            EXPECT_TRUE(std::signbit(zero.battery_before_j));
            EXPECT_TRUE(std::signbit(zero.battery_after_j));
            EXPECT_EQ(binary64Bits(zero.battery_before_j),
                      binary64Bits(negative_zero));
            EXPECT_EQ(binary64Bits(zero.battery_after_j),
                      binary64Bits(negative_zero));
        }
    }

    TEST(HarvestRuntimeNumeric, TightensRepresentableSpaceByOneUlp) {
        auto source = std::make_unique<ControlledSource>(0.001);
        HarvestRuntime runtime(std::move(source));
        const double before = 1.3213248500141373e-31;
        const double capacity = 3.7098376936859965e-31;
        constexpr std::uint64_t initial_actual_bits =
            UINT64_C(0x399360bf418bd994);
        constexpr std::uint64_t corrected_actual_bits =
            UINT64_C(0x399360bf418bd993);

        const HarvestResult result =
            runtime.applyInterval({0, 0, 1}, before, capacity);
        EXPECT_EQ(binary64Bits(capacity - before), initial_actual_bits);
        EXPECT_EQ(binary64Bits(result.actual_j), corrected_actual_bits);
        EXPECT_LE(result.battery_after_j, capacity);
        EXPECT_EQ(binary64Bits(result.clipped_j),
                  binary64Bits(result.offered_j - result.actual_j));
    }

    TEST(HarvestRuntimeB4, ThirtyThousandOfferedValuesMatchPriorityRuntimeBits) {
        constexpr double alpha_w = 0.054;
        HarvestRuntime runtime(std::make_unique<ScaledPiecewiseSource>(
            b4PiecewiseConfig(alpha_w)));
        const PriorityEnergyRuntime oracle(b4PriorityConfig(alpha_w));

        for (std::uint64_t index = 0; index < 30000; ++index) {
            const HarvestResult actual = runtime.applyInterval(
                {index, index, index + 1},
                0.0,
                std::numeric_limits<double>::max());
            const PriorityEnergyHarvestStep expected =
                oracle.applyHarvest(
                    index + 1,
                    0.0,
                    std::numeric_limits<double>::max());
            if (binary64Bits(actual.offered_j) !=
                binary64Bits(expected.offered_j)) {
                ADD_FAILURE() << "offered mismatch at interval " << index;
                return;
            }
        }
        EXPECT_EQ(runtime.lastAppliedIndex(), UINT64_C(29999));
    }

    TEST(HarvestRuntimeB4, ClippingMatchesPriorityRuntimeBits) {
        constexpr double alpha_w = 1.0;
        HarvestRuntime runtime(std::make_unique<ScaledPiecewiseSource>(
            b4PiecewiseConfig(alpha_w)));
        const PriorityEnergyRuntime oracle(b4PriorityConfig(alpha_w));
        const double offered = oracle.applyHarvest(
            1, 0.0, std::numeric_limits<double>::max()).offered_j;
        const double negative_zero = -0.0;
        const std::array<std::pair<double, double>, 7> cases = {{
            {0.0, 1.0},
            {1.0, 1.0},
            {0.0, offered},
            {0.0, std::nextafter(offered, 0.0)},
            {1.3213248500141373e-31, 3.7098376936859965e-31},
            {negative_zero, 1.0},
            {0.0, 0.0},
        }};

        for (std::uint64_t index = 0; index < cases.size(); ++index) {
            const double before = cases[index].first;
            const double capacity = cases[index].second;
            const HarvestResult actual = runtime.applyInterval(
                {index, index, index + 1}, before, capacity);
            const PriorityEnergyHarvestStep expected =
                oracle.applyHarvest(index + 1, before, capacity);
            SCOPED_TRACE("interval=" + std::to_string(index));
            expectResultBitsEqual(actual, expected, before);
        }

        HarvestRuntime zero_runtime(std::make_unique<ScaledPiecewiseSource>(
            b4PiecewiseConfig(0.0)));
        const PriorityEnergyRuntime zero_oracle(b4PriorityConfig(0.0));
        const HarvestResult actual = zero_runtime.applyInterval(
            {77, 10, 11}, negative_zero, 1.0);
        const PriorityEnergyHarvestStep expected =
            zero_oracle.applyHarvest(1, negative_zero, 1.0);
        expectResultBitsEqual(actual, expected, negative_zero);
    }

} // namespace RTSim
