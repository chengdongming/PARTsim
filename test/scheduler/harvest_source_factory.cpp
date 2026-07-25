#include <gtest/gtest.h>

#include <rtsim/harvesting/harvest_source_factory.hpp>
#include <rtsim/harvesting/legacy_solar_source.hpp>
#include <rtsim/harvesting/sampled_trace_source.hpp>
#include <rtsim/harvesting/scaled_piecewise_source.hpp>

#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace RTSim {
    namespace {
        class ForcedFactoryConstructionFailure final :
            public std::runtime_error {
        public:
            ForcedFactoryConstructionFailure() :
                std::runtime_error("forced factory construction failure") {}
        };

        struct ThrowingLegacyConfigConversion {
            operator LegacySolarConfig() const {
                throw ForcedFactoryConstructionFailure();
            }
        };

        class TemporaryFactoryTrace {
        public:
            TemporaryFactoryTrace() {
                char pattern[] = "/tmp/partsim_harvest_factory_XXXXXX";
                char *created = ::mkdtemp(pattern);
                if (created == nullptr) {
                    throw std::runtime_error(
                        "cannot create factory trace directory");
                }
                _directory = created;
                _path = _directory / "trace.csv";
                std::ofstream output(_path, std::ios::binary);
                output << "timestamp_ms,power_w\n0,2\n10,0\n";
                if (!output.good()) {
                    throw std::runtime_error("cannot write factory trace");
                }
            }

            ~TemporaryFactoryTrace() {
                std::error_code error;
                std::filesystem::remove_all(_directory, error);
            }

            const std::filesystem::path &path() const noexcept {
                return _path;
            }

        private:
            std::filesystem::path _directory;
            std::filesystem::path _path;
        };

        ScaledPiecewiseConfig scaledConfig() {
            ScaledPiecewiseConfig config;
            config.scale_w = 1.0;
            config.segments = {{0, 10, 2.0}};
            return config;
        }

        SampledTraceConfig traceConfig(const std::filesystem::path &path) {
            SampledTraceConfig config;
            config.file = path.string();
            config.time_column = "timestamp_ms";
            config.value_column = "power_w";
            config.value_type = TraceValueType::ElectricalPower;
            config.interpolation = TraceInterpolation::ZeroOrderHold;
            config.after_trace = TraceAfterEnd::Zero;
            config.panel_area_m2 = 0.0;
            config.conversion_efficiency = 0.0;
            config.max_file_size_bytes = 1024;
            config.max_rows = 10;
            return config;
        }
    } // namespace

    TEST(HarvestSourceFactory, ExhaustivelyMapsAllThreeSourceKinds) {
        HarvestSourceConfig legacy = LegacySolarConfig{};
        std::unique_ptr<HarvestSource> legacy_source =
            makeHarvestSource(legacy);
        EXPECT_NE(dynamic_cast<LegacySolarSource *>(legacy_source.get()),
                  nullptr);

        HarvestSourceConfig scaled = scaledConfig();
        std::unique_ptr<HarvestSource> scaled_source =
            makeHarvestSource(scaled);
        EXPECT_NE(dynamic_cast<ScaledPiecewiseSource *>(scaled_source.get()),
                  nullptr);
        EXPECT_GT(scaled_source->offeredEnergyForInterval({0, 0, 1}), 0.0);

        TemporaryFactoryTrace trace;
        HarvestSourceConfig sampled = traceConfig(trace.path());
        std::unique_ptr<HarvestSource> sampled_source =
            makeHarvestSource(sampled);
        EXPECT_NE(dynamic_cast<SampledTraceSource *>(sampled_source.get()),
                  nullptr);
        EXPECT_GT(sampled_source->offeredEnergyForInterval({0, 0, 1}), 0.0);
    }

    TEST(HarvestSourceFactory, DoesNotCacheOrShareProcessSources) {
        const HarvestSourceConfig config = scaledConfig();
        std::unique_ptr<HarvestSource> first = makeHarvestSource(config);
        std::unique_ptr<HarvestSource> second = makeHarvestSource(config);
        ASSERT_NE(first, nullptr);
        ASSERT_NE(second, nullptr);
        EXPECT_NE(first.get(), second.get());
    }

    TEST(HarvestSourceFactory, ValuelessVariantFailsExplicitly) {
        HarvestSourceConfig config = SampledTraceConfig{};
        EXPECT_THROW(
            config.emplace<LegacySolarConfig>(
                ThrowingLegacyConfigConversion{}),
            ForcedFactoryConstructionFailure);
        ASSERT_TRUE(config.valueless_by_exception());
        EXPECT_THROW((void)makeHarvestSource(config), std::bad_variant_access);
    }

    TEST(HarvestSourceFactory, PropagatesConstructionFailures) {
        ScaledPiecewiseConfig invalid_scaled;
        invalid_scaled.scale_w = 1.0;
        const HarvestSourceConfig scaled = invalid_scaled;
        EXPECT_THROW((void)makeHarvestSource(scaled), std::invalid_argument);

        TemporaryFactoryTrace trace;
        SampledTraceConfig missing = traceConfig(
            trace.path().parent_path() / "missing.csv");
        const HarvestSourceConfig sampled = missing;
        EXPECT_THROW((void)makeHarvestSource(sampled), std::runtime_error);
    }

} // namespace RTSim
