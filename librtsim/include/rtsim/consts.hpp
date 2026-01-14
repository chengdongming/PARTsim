#ifndef __RTSIM_CONSTS_H__
#define __RTSIM_CONSTS_H__

namespace RTSim {
    // =====================================================
    // YAML File Attributes
    // =====================================================
    static constexpr auto ATTR_NAME = "name";
    static constexpr auto ATTR_CPU_ISLANDS = "cpu_islands";
    static constexpr auto ATTR_NUMCPUS = "numcpus";
    static constexpr auto ATTR_KERNEL = "kernel";
    static constexpr auto ATTR_SCHEDULER = "scheduler";
    static constexpr auto ATTR_PLACEMENT = "task_placement";
    static constexpr auto ATTR_VOLTS = "volts";
    static constexpr auto ATTR_FREQS = "freqs";
    static constexpr auto ATTR_BASE_FREQ = "base_freq";
    static constexpr auto ATTR_POWER_MODEL = "power_model";
    static constexpr auto ATTR_SPEED_MODEL = "speed_model";
    static constexpr auto ATTR_POWER_MODELS = "power_models";
    static constexpr auto ATTR_TYPE = "type";
    static constexpr auto ATTR_FILENAME = "filename";
    static constexpr auto ATTR_PARAMS = "params";
    static constexpr auto ATTR_WORKLOAD = "workload";
    static constexpr auto ATTR_POWER_PARAMS = "power_params";
    static constexpr auto ATTR_SPEED_PARAMS = "speed_params";

    // =====================================================
    // CSV File Fields
    // =====================================================
    static constexpr auto FIELD_POWER_D = "power_d";
    static constexpr auto FIELD_POWER_E = "power_e";
    static constexpr auto FIELD_POWER_G = "power_g";
    static constexpr auto FIELD_POWER_K = "power_k";
    static constexpr auto FIELD_SPEED_A = "speed_a";
    static constexpr auto FIELD_SPEED_B = "speed_b";
    static constexpr auto FIELD_SPEED_C = "speed_c";
    static constexpr auto FIELD_SPEED_D = "speed_d";
    static constexpr auto FIELD_FREQ = "freq";
    static constexpr auto FIELD_VOLT = "voltage";
    static constexpr auto FIELD_POWER = "power";
    static constexpr auto FIELD_SPEED = "speed";
    static constexpr auto FIELD_MODEL = "model";
    static constexpr auto FIELD_WORKLOAD = "workload";
} // namespace RTSim

#endif // __RTSIM_CONSTS_H__
