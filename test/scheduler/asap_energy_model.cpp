#include <cstdio>
#include <fstream>
#include <map>
#include <string>

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

void writeConfigFile(const std::string &path, const std::string &contents) {
    std::ofstream config(path);
    ASSERT_TRUE(config.is_open());
    config << contents;
}

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

TEST(ASAPEnergyModel, DirectConfigFallsBackToConsumptionModel) {
    const std::string config_path =
        "/tmp/partsim_consumption_model_fallback_test.yml";
    writeConfigFile(
        config_path,
        "energy_management:\n"
        "  consumption_model:\n"
        "    base_power: 0.25\n"
        "    workload_coefficients:\n"
        "      control: 0.4\n"
        "      idle: 0.2\n"
        "    frequency_scaling:\n"
        "      8100: 0.5\n");

    ConfigManager::setConfigCallback(nullptr);
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_path));

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.25);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.4);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.2);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.5);

    std::remove(config_path.c_str());
}

TEST(ASAPEnergyModel, DirectConfigPrefersCanonicalModelAndFrequencyRatios) {
    const std::string config_path =
        "/tmp/partsim_scheduler_model_priority_test.yml";
    writeConfigFile(
        config_path,
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

    ConfigManager::setConfigCallback(nullptr);
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_path));

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.4);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.3);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.2);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.8);

    std::remove(config_path.c_str());
}

TEST(ASAPEnergyModel, DirectCanonicalModelFallsBackToFrequencyScaling) {
    const std::string config_path =
        "/tmp/partsim_frequency_scaling_fallback_test.yml";
    writeConfigFile(
        config_path,
        "energy_management:\n"
        "  scheduler_energy_model:\n"
        "    base_power: 0.3\n"
        "    workload_coefficients:\n"
        "      control: 0.5\n"
        "    frequency_scaling:\n"
        "      8100: 0.7\n");

    ConfigManager::setConfigCallback(nullptr);
    ConfigManager &config = ConfigManager::getInstance();
    ASSERT_TRUE(config.loadSystemConfig(config_path));

    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.3);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.5);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.7);

    std::remove(config_path.c_str());
}

TEST(ASAPEnergyModel, EnergyBridgeExportsSchedulerModelWithoutDefaultOverride) {
    const std::string config_path =
        "/tmp/partsim_scheduler_energy_model_test.yml";
    {
        std::ofstream config(config_path);
        ASSERT_TRUE(config.is_open());
        config
            << "cpu_islands:\n"
            << "  - name: island0\n"
            << "    numcpus: 4\n"
            << "    base_freq: 8100\n"
            << "energy_management:\n"
            << "  initial_energy: 10.0\n"
            << "  max_energy: 20.0\n"
            << "  periodic_collection_interval_ms: 1\n"
            << "  scheduler_energy_model:\n"
            << "    base_power: 0.37\n"
            << "    workload_coefficients:\n"
            << "      control: 0.42\n"
            << "      idle: 0.17\n"
            << "    frequency_power_ratios:\n"
            << "      8100: 0.77\n";
    }

    ASSERT_TRUE(EnergyBridge::getInstance().initialize(config_path));

    ConfigManager &config = ConfigManager::getInstance();
    EXPECT_DOUBLE_EQ(config.getBaseFrequency(), 8100.0);
    EXPECT_DOUBLE_EQ(config.getBasePower(), 0.37);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("control"), 0.42);
    EXPECT_DOUBLE_EQ(config.getPowerCoefficient("idle"), 0.17);
    EXPECT_DOUBLE_EQ(config.getFrequencyPowerRatio(8100), 0.77);

    std::remove(config_path.c_str());
}

} // namespace RTSim
