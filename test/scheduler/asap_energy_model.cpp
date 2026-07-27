#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <gtest/gtest.h>

#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/scheduler/energy_bridge.hpp>

namespace RTSim {

double calculateGPFPASAPEnergyForDuration(
    double base_power,
    double workload_coefficient,
    double frequency_ratio,
    double duration_ms);
double resolveGPFPASAPWorkloadCoefficient(
    const std::map<std::string, double> &power_coefficients,
    const std::string &workload_type);

namespace {
    class TemporarySystemYaml {
    public:
        explicit TemporarySystemYaml(const std::string &contents) {
            const std::string pattern =
                (std::filesystem::temp_directory_path() /
                 "partsim_asap_energy_model_XXXXXX")
                    .string();
            std::vector<char> mutable_pattern(
                pattern.begin(),
                pattern.end());
            mutable_pattern.push_back('\0');
            char *created = ::mkdtemp(mutable_pattern.data());
            if (!created) {
                throw std::system_error(
                    errno,
                    std::generic_category(),
                    "cannot create ASAP energy-model temporary directory");
            }
            _directory = created;
            _path = _directory / "system.yml";

            std::ofstream output(_path);
            if (!output) {
                std::error_code error;
                std::filesystem::remove_all(_directory, error);
                throw std::runtime_error(
                    "cannot create ASAP energy-model temporary YAML");
            }
            output << contents;
            if (!output) {
                output.close();
                std::error_code error;
                std::filesystem::remove_all(_directory, error);
                throw std::runtime_error(
                    "cannot write ASAP energy-model temporary YAML");
            }
        }

        TemporarySystemYaml(const TemporarySystemYaml &) = delete;
        TemporarySystemYaml &operator=(const TemporarySystemYaml &) = delete;

        ~TemporarySystemYaml() {
            std::error_code error;
            std::filesystem::remove_all(_directory, error);
        }

        std::string path() const {
            return _path.string();
        }

    private:
        std::filesystem::path _directory;
        std::filesystem::path _path;
    };

    std::string stableSystemConfigPath() {
        return (std::filesystem::path(__FILE__)
                    .parent_path()
                    .parent_path()
                    .parent_path() /
                "v9_3_b4_priority_energy_system_template.yml")
            .string();
    }

    class ASAPEnergyModelConfigTest : public ::testing::Test {
    protected:
        void SetUp() override {
            resetToStableConfiguration();
            ASSERT_TRUE(ConfigManager::getInstance().isConfigLoaded());
        }

        void TearDown() override {
            resetToStableConfiguration();
            ConfigManager &config = ConfigManager::getInstance();
            EXPECT_TRUE(config.isConfigLoaded());
            EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());
            EXPECT_EQ(config.getNumCores(), 4);
            EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 9000.0);
        }

    private:
        static void resetToStableConfiguration() {
            EnergyBridge::getInstance().shutdown();
            EnergyBridge::ensureConfigCallbackRegistered();
            ASSERT_TRUE(ConfigManager::getInstance().loadSystemConfig(
                stableSystemConfigPath()));
        }
    };
} // namespace

TEST(ASAPEnergyModel, MatchesPythonGoldenCases) {
    EXPECT_DOUBLE_EQ(
        calculateGPFPASAPEnergyForDuration(0.5, 1.2, 0.93, 1.0),
        0.000558);
    EXPECT_DOUBLE_EQ(
        calculateGPFPASAPEnergyForDuration(0.5, 0.1, 0.93, 50.0),
        0.002325);
    EXPECT_DOUBLE_EQ(
        calculateGPFPASAPEnergyForDuration(0.37, 0.42, 0.77, 1.0),
        0.000119658);
}

TEST(ASAPEnergyModel, UsesConfiguredControlAndIdleCoefficients) {
    const std::map<std::string, double> coefficients = {
        {"control", 0.42},
        {"idle", 0.17},
        {"bzip2", 0.81},
    };

    EXPECT_DOUBLE_EQ(
        resolveGPFPASAPWorkloadCoefficient(coefficients, "control"),
        0.42);
    EXPECT_DOUBLE_EQ(
        resolveGPFPASAPWorkloadCoefficient(coefficients, "idle"),
        0.17);
    EXPECT_DOUBLE_EQ(
        resolveGPFPASAPWorkloadCoefficient(coefficients, "bzip2"),
        0.81);
    EXPECT_DOUBLE_EQ(
        resolveGPFPASAPWorkloadCoefficient(coefficients, "unknown"),
        1.0);
}

TEST_F(ASAPEnergyModelConfigTest, DirectConfigFallsBackToConsumptionModel) {
    TemporarySystemYaml config_file(
        "energy_management:\n"
        "  consumption_model:\n"
        "    base_power: 0.25\n"
        "    workload_coefficients:\n"
        "      control: 0.4\n"
        "      idle: 0.2\n"
        "    frequency_scaling:\n"
        "      8100: 0.5\n");

    EnergyBridge::ensureConfigCallbackRegistered();
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_file.path()));
    ASSERT_TRUE(config.isConfigLoaded());
    EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.25);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.4);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.2);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.5);
}

TEST_F(ASAPEnergyModelConfigTest,
       DirectConfigPrefersCanonicalModelAndFrequencyRatios) {
    TemporarySystemYaml config_file(
        "energy_management:\n"
        "  consumption_model:\n"
        "    base_power: 0.9\n"
        "    workload_coefficients:\n"
        "      control: 0.9\n"
        "      idle: 0.9\n"
        "    frequency_scaling:\n"
        "      8100: 0.1\n"
        "  scheduler_energy_model:\n"
        "    base_power: 0.4\n"
        "    workload_coefficients:\n"
        "      control: 0.3\n"
        "      idle: 0.2\n"
        "    frequency_scaling:\n"
        "      8100: 0.2\n"
        "    frequency_power_ratios:\n"
        "      8100: 0.8\n");

    EnergyBridge::ensureConfigCallbackRegistered();
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_file.path()));
    ASSERT_TRUE(config.isConfigLoaded());
    EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.4);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.3);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.2);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.8);
}

TEST_F(ASAPEnergyModelConfigTest,
       DirectCanonicalModelFallsBackToFrequencyScaling) {
    TemporarySystemYaml config_file(
        "energy_management:\n"
        "  scheduler_energy_model:\n"
        "    base_power: 0.3\n"
        "    workload_coefficients:\n"
        "      control: 0.5\n"
        "    frequency_scaling:\n"
        "      8100: 0.7\n");

    EnergyBridge::ensureConfigCallbackRegistered();
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_file.path()));
    ASSERT_TRUE(config.isConfigLoaded());
    EXPECT_FALSE(config.isPriorityEnergyProfileEnabled());

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.3);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.5);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.7);
}

TEST_F(ASAPEnergyModelConfigTest,
       EnergyBridgeExportsSchedulerModelWithoutDefaultOverride) {
    TemporarySystemYaml config_file(
        "cpu_islands:\n"
        "  - name: island0\n"
        "    numcpus: 4\n"
        "    base_freq: 8100\n"
        "energy_management:\n"
        "  initial_energy: 10.0\n"
        "  max_energy: 20.0\n"
        "  periodic_collection_interval_ms: 1\n"
        "  scheduler_energy_model:\n"
        "    base_power: 0.37\n"
        "    workload_coefficients:\n"
        "      control: 0.42\n"
        "      idle: 0.17\n"
        "    frequency_power_ratios:\n"
        "      8100: 0.77\n");

    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_file.path()));
    ASSERT_TRUE(config.isConfigLoaded());
    const std::uint64_t config_generation =
        config.getConfigGeneration();
    ASSERT_NE(config_generation, 0u);

    EnergyBridge &bridge = EnergyBridge::getInstance();
    ASSERT_TRUE(bridge.initialize());
    EXPECT_TRUE(bridge.isInitialized());
    EXPECT_EQ(bridge.getConfigGeneration(), config_generation);
    EXPECT_EQ(config.getConfigGeneration(), config_generation);
    EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 8100.0);
    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.37);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.42);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.17);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.77);
}

} // namespace RTSim
