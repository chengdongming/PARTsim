#ifndef SYSTEM_DESCRIPTOR_HPP
#define SYSTEM_DESCRIPTOR_HPP

#include <array>
#include <string>
#include <vector>

#include <metasim/memory.hpp>
#include <rtsim/class_utils.hpp>
#include <rtsim/powermodel_params.hpp>
#include <rtsim/yaml.hpp>

namespace RTSim {

    class KernelDescriptor {
    public:
        std::string name;
        std::string scheduler;
        std::string placement;
    };

    class CPUMDescriptor {
    public:
        std::string name;
        CPUModelParams::key_type type;
        std::vector<std::unique_ptr<CPUModelParams>> params;

    public:
        CPUMDescriptor() = default;
        DISABLE_COPY(CPUMDescriptor);
        DEFAULT_MOVABLE(CPUMDescriptor);
    };

    class IslandDescriptor {
    public:
        std::string name;
        size_t numcpus;
        KernelDescriptor kernel;
        std::vector<volt_type> volts;
        std::vector<freq_type> freqs;
        freq_type base_freq;
        std::string power_model;
        std::string speed_model;

    public:
        IslandDescriptor() = default;
        DEFAULT_COPIABLE(IslandDescriptor);
        DEFAULT_MOVABLE(IslandDescriptor);
    };

    class SystemDescriptor {
    private:
        using map_type = std::map<std::string, CPUMDescriptor>;
        using vector_type = std::vector<IslandDescriptor>;

    public:
        map_type power_models;
        vector_type islands;

    public:
        SystemDescriptor(std::string fname);

    public:
        DEFAULT_COPIABLE(SystemDescriptor);
        DEFAULT_MOVABLE(SystemDescriptor);
    };

} // namespace RTSim

#endif // SYSTEM_DESCRIPTOR_HPP
