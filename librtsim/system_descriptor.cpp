
#include <algorithm>
#include <filesystem>
#include <sstream>

#include <rtsim/consts.hpp>
#include <rtsim/system_descriptor.hpp>

namespace RTSim {
    // Generic converter from string to any type (that supports std::streams)
    template <typename T>
    T from_str(const std::string &str) {
        std::istringstream ss(str);
        T num;
        ss >> num;
        return num;
    }

    // Base template, use one of its full specializations below
    template <class T>
    static T createFrom(const std::string &dirname, yaml::Object_ptr ptr);

    // TODO: more parameters to choose from, including kernel type and so on
    template <>
    KernelDescriptor createFrom(const std::string &dirname,
                                yaml::Object_ptr ptr) {
        KernelDescriptor kd;
        kd.name = ptr->get(ATTR_NAME)->get();
        kd.scheduler = ptr->get(ATTR_SCHEDULER)->get();
        kd.placement = ptr->get(ATTR_PLACEMENT)->get();
        return kd;
    }

    template <>
    CPUMDescriptor createFrom(const std::string &dirname,
                              yaml::Object_ptr ptr) {
        CPUMDescriptor pm;

        pm.name = ptr->get(ATTR_NAME)->get();
        pm.type = ptr->get(ATTR_TYPE)->get();

        // Load parameters from csv table
        std::string fname = ptr->get(ATTR_FILENAME)->get();

        if (fname.length() > 0) {
            auto fpath = std::filesystem::path(fname);
            if (fpath.is_relative()) {
                // Append to absolute path to get the actual path in a
                // multi-platform way
                fpath = std::filesystem::absolute(dirname) / fpath;
            }

            csv::CSVDocument doc(fpath);

            const size_t num_rows = doc.rows();

            for (size_t rix = 0; rix < num_rows; ++rix) {
                // Skip rows that do not match
                if (doc.at(FIELD_MODEL, rix) == pm.name) {
                    pm.params.push_back(
                        createCPUModelParams(pm.type, doc, rix));
                }
            }
        } else {
            for (auto param : *ptr->get(ATTR_PARAMS)) {
                pm.params.push_back(createCPUModelParams(pm.type, param));
            }
        }

        return pm;
    }

    template <>
    IslandDescriptor createFrom(const std::string &dirname,
                                yaml::Object_ptr ptr) {
        IslandDescriptor island;

        island.name = ptr->get(ATTR_NAME)->get();
        island.power_model = ptr->get(ATTR_POWER_MODEL)->get();
        island.speed_model = ptr->get(ATTR_SPEED_MODEL)->get();
        island.numcpus = from_str<size_t>(ptr->get(ATTR_NUMCPUS)->get());
        island.kernel =
            createFrom<KernelDescriptor>(dirname, ptr->get(ATTR_KERNEL));

        for (auto v : *ptr->get(ATTR_VOLTS)) {
            island.volts.push_back(from_str<volt_type>(v->get()));
        }
        std::sort(island.volts.begin(), island.volts.end());

        for (auto f : *ptr->get(ATTR_FREQS)) {
            island.freqs.push_back(from_str<freq_type>(f->get()));
        }
        std::sort(island.freqs.begin(), island.freqs.end());

        island.base_freq = from_str<freq_type>(ptr->get(ATTR_BASE_FREQ)->get());

        return island;
    }

    SystemDescriptor::SystemDescriptor(std::string fname) {
        yaml::Object_ptr descriptor_obj = yaml::parse(fname);

        // Get the absolute path for the supplied filename and its absolute
        // directory path
        auto apath = std::filesystem::absolute(std::filesystem::path(fname));
        std::string dirname = apath.parent_path();

        for (auto i : *descriptor_obj->get(ATTR_CPU_ISLANDS)) {
            islands.push_back(createFrom<IslandDescriptor>(dirname, i));
        }

        for (auto pm : *descriptor_obj->get(ATTR_POWER_MODELS)) {
            auto pmd = createFrom<CPUMDescriptor>(dirname, pm);
            power_models.emplace(pmd.name, std::move(pmd));
        }
    }
} // namespace RTSim
