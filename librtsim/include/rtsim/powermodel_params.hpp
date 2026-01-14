// #include <rtsim/yaml.hpp>

#ifndef __POWERMODEL_PARAMS_HPP__
#define __POWERMODEL_PARAMS_HPP__

#include <array>
#include <string>
#include <vector>

#include <metasim/memory.hpp>
#include <rtsim/class_utils.hpp>
#include <rtsim/csv.hpp>
#include <rtsim/powermodel.hpp>
#include <rtsim/yaml.hpp>
// TODO: change slightly the factory.hpp and then use it for these objects

namespace RTSim {
    using watt_type = double; /// Represents power in Watts
    using volt_type = double; /// Represents voltage in Volts
    using freq_type = unsigned int; /// Represents frequency in MHz
    using speed_type = double; /// Represents speedup ratio
    using wclass_type = std::string; /// Represents the workload class of a task

    // Base class, empty
    class CPUModelParams {
    public:
        using key_type = std::string;

    public:
        CPUModelParams(){};
        DEFAULT_COPIABLE(CPUModelParams);
        DEFAULT_MOVABLE(CPUModelParams);
        DEFAULT_VIRTUAL_DES(CPUModelParams);
    };

    // Parameter class for CPUModelMinimal
    class CPUModelMinimalParams : public CPUModelParams {
    public:
        static const key_type key;

    public:
        CPUModelMinimalParams() = default;
        DEFAULT_COPIABLE(CPUModelMinimalParams);
        DEFAULT_MOVABLE(CPUModelMinimalParams);
        DEFAULT_VIRTUAL_DES(CPUModelMinimalParams);
    };

    // Parameter class for CPUModelBP
    class CPUModelBPParams : public CPUModelParams {
    public:
        static const key_type key;

    public:
        struct PowerModelParams {
            /// Constant "displacement"
            double d;

            /// Constant "eta"
            /// Factor modeling the P_short = eta * P_charge
            double e;

            /// Constant "gamma"
            /// Factor modeling the Temperature effect on
            /// P_leak = gamma * V * P_dyn
            double g;

            /// Constant "K"
            /// Factor modeling the percentage of CPU activity
            double k;
        };

        /// @todo do we really need long double after all?
        struct SpeedModelParams {
            /// @todo document this
            long double a;
            /// @todo document this
            long double b;
            /// @todo document this
            long double c;
            /// @todo document this
            long double d;
        };

    public:
        std::string workload;
        struct Params {
            PowerModelParams power;
            SpeedModelParams speed;
        } params;

    public:
        CPUModelBPParams() = default;
        DEFAULT_COPIABLE(CPUModelBPParams);
        DEFAULT_MOVABLE(CPUModelBPParams);
        DEFAULT_VIRTUAL_DES(CPUModelBPParams);

    public:
        CPUModelBPParams(const std::string &workload, const Params &params) :
            workload(workload),
            params{params} {}
    };

    // Parameter class for CPUModelTB
    class CPUModelTBParams : public CPUModelParams {
    public:
        static const key_type key;

    public:
        std::string workload;
        freq_type freq;
        volt_type volt;
        watt_type power;
        speed_type speed;

    public:
        CPUModelTBParams() = default;
        DEFAULT_COPIABLE(CPUModelTBParams);
        DEFAULT_MOVABLE(CPUModelTBParams);
        DEFAULT_VIRTUAL_DES(CPUModelTBParams);
    };

    // Parameter class for CPUModelTBApprox
    class CPUModelTBApproxParams : public CPUModelTBParams {
    public:
        static const key_type key;

    public:
        CPUModelTBApproxParams() = default;

        CPUModelTBApproxParams(const CPUModelTBParams &s) :
            CPUModelTBParams(s){};
        CPUModelTBApproxParams(CPUModelTBParams &&s) : CPUModelTBParams(s){};

        DEFAULT_COPIABLE(CPUModelTBApproxParams);
        DEFAULT_MOVABLE(CPUModelTBApproxParams);
        DEFAULT_VIRTUAL_DES(CPUModelTBApproxParams);
    };

    extern std::unique_ptr<CPUModelParams>
        createCPUModelParams(CPUModelParams::key_type k, csv::CSVDocument &doc,
                             size_t rix);

    extern std::unique_ptr<CPUModelParams>
        createCPUModelParams(CPUModelParams::key_type k, yaml::Object_ptr ptr);

} // namespace RTSim

#endif // __POWERMODEL_PARAMS_HPP__
