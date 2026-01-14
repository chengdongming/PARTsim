#include <sstream>

#include <metasim/baseexc.hpp>
#include <metasim/memory.hpp>

#include <rtsim/consts.hpp>
#include <rtsim/powermodel_params.hpp>

namespace RTSim {

    // =====================================================
    // Static attributes
    // =====================================================
    const CPUModelParams::key_type CPUModelMinimalParams::key("minimal");
    const CPUModelParams::key_type CPUModelBPParams::key("balsini_pannocchi");
    const CPUModelParams::key_type CPUModelTBParams::key("table_based");
    const CPUModelParams::key_type
        CPUModelTBApproxParams::key("table_based_approx");

    // =====================================================
    // Utilities
    // =====================================================

    // Generic converter from string to any type (that supports std::streams)
    template <class T>
    static T from_str(const std::string &str) {
        std::istringstream ss(str);
        T num;
        ss >> num;
        return num;
    }

    // Special case for strings, keep them the same, do not parse them with a
    // string stream
    template <>
    std::string from_str(const std::string &str) {
        return str;
    }

#define FROM_STR(dest, src) ((dest) = from_str<decltype(dest)>((src)))

    // =====================================================
    // Base Templates
    // =====================================================

    template <class T, class... Args>
    static T createFrom(csv::CSVDocument &doc, Args... args);

    template <class T, class... Args>
    static T createFrom(yaml::Object_ptr ptr, Args... args);

    // =====================================================
    // CPUModelMinimalParams
    // =====================================================

    template <>
    CPUModelMinimalParams createFrom(yaml::Object_ptr ptr) {
        return CPUModelMinimalParams();
    }

    template <>
    CPUModelMinimalParams createFrom(csv::CSVDocument &doc, size_t rix) {
        return CPUModelMinimalParams();
    }

    // =====================================================
    // CPUModelBPParams
    // =====================================================

    template <>
    CPUModelBPParams createFrom(yaml::Object_ptr ptr) {
        CPUModelBPParams bpp;

        bpp.workload = ptr->get(ATTR_WORKLOAD)->get();

        // If not enough parameters are supplied, an exception is thrown
        auto pp = ptr->get(ATTR_POWER_PARAMS);
        FROM_STR(bpp.params.power.d, pp->get(0)->get());
        FROM_STR(bpp.params.power.e, pp->get(1)->get());
        FROM_STR(bpp.params.power.g, pp->get(2)->get());
        FROM_STR(bpp.params.power.k, pp->get(3)->get());

        auto sp = ptr->get(ATTR_SPEED_PARAMS);
        FROM_STR(bpp.params.speed.a, sp->get(0)->get());
        FROM_STR(bpp.params.speed.b, sp->get(1)->get());
        FROM_STR(bpp.params.speed.c, sp->get(2)->get());
        FROM_STR(bpp.params.speed.d, sp->get(3)->get());

        return bpp;
    }

    template <>
    CPUModelBPParams createFrom(csv::CSVDocument &doc, size_t rix) {
        CPUModelBPParams bpp;

        bpp.workload = doc.at(FIELD_WORKLOAD, rix);

        FROM_STR(bpp.params.power.d, doc.at(FIELD_POWER_D, rix));
        FROM_STR(bpp.params.power.e, doc.at(FIELD_POWER_E, rix));
        FROM_STR(bpp.params.power.g, doc.at(FIELD_POWER_G, rix));
        FROM_STR(bpp.params.power.k, doc.at(FIELD_POWER_K, rix));

        FROM_STR(bpp.params.speed.a, doc.at(FIELD_SPEED_A, rix));
        FROM_STR(bpp.params.speed.b, doc.at(FIELD_SPEED_B, rix));
        FROM_STR(bpp.params.speed.c, doc.at(FIELD_SPEED_C, rix));
        FROM_STR(bpp.params.speed.d, doc.at(FIELD_SPEED_D, rix));

        return bpp;
    }

    // =====================================================
    // CPUModelTBParams
    // =====================================================

    // Not implemented! It would be a very big YAML table anyway!
    template <>
    CPUModelTBParams createFrom(yaml::Object_ptr ptr);

    template <>
    CPUModelTBParams createFrom(csv::CSVDocument &doc, size_t rix) {
        CPUModelTBParams tbp;

        // Get values from row
        tbp.workload = doc.at(FIELD_WORKLOAD, rix);

        FROM_STR(tbp.freq, doc.at(FIELD_FREQ, rix));
        FROM_STR(tbp.volt, doc.at(FIELD_VOLT, rix));
        FROM_STR(tbp.power, doc.at(FIELD_POWER, rix));
        FROM_STR(tbp.speed, doc.at(FIELD_SPEED, rix));

        return tbp;
    }

    // =====================================================
    // CPUModelTBApproxParams
    // =====================================================

    // Not implemented! It would be a very big YAML table anyway!
    template <>
    CPUModelTBApproxParams createFrom(yaml::Object_ptr ptr);

    template <>
    CPUModelTBApproxParams createFrom(csv::CSVDocument &doc, size_t rix) {
        return CPUModelTBApproxParams{createFrom<CPUModelTBParams>(doc, rix)};
    }

    // =====================================================
    // Factory methods implementation
    // =====================================================

    template <class T, class... Args>
    static std::unique_ptr<T> uniqueFrom(Args &&...args) {
        return std::make_unique<T>(createFrom<T>(std::forward<Args>(args)...));
    }

    std::unique_ptr<CPUModelParams>
        createCPUModelParams(CPUModelParams::key_type k, csv::CSVDocument &doc,
                             size_t rix) {
        if (k == CPUModelMinimalParams::key)
            return uniqueFrom<CPUModelMinimalParams>(doc, rix);

        if (k == CPUModelBPParams::key)
            return uniqueFrom<CPUModelBPParams>(doc, rix);

        if (k == CPUModelTBParams::key)
            return uniqueFrom<CPUModelTBParams>(doc, rix);

        if (k == CPUModelTBApproxParams::key)
            return uniqueFrom<CPUModelTBApproxParams>(doc, rix);

        throw MetaSim::BaseExc(
            "The CPU Model '" + k +
            "' does not support being instantiated from a CSV document!");
    }

    std::unique_ptr<CPUModelParams>
        createCPUModelParams(CPUModelParams::key_type k, yaml::Object_ptr ptr) {
        if (k == CPUModelMinimalParams::key)
            return uniqueFrom<CPUModelMinimalParams>(ptr);

        if (k == CPUModelBPParams::key)
            return uniqueFrom<CPUModelBPParams>(ptr);

        /*
            // Not implemented!
            if (k == CPUModelTBParams::key)
                return uniqueFrom<CPUModelTBParams>(ptr);

            // Not implemented!
            if (k == CPUModelTBApproxParams::key)
                return uniqueFrom<CPUModelTBParams>(ptr);
        */

        throw MetaSim::BaseExc("The CPU Model '" + k +
                               "' does not support being instantiated from "
                               "inline YAML parameters!");
    }

} // namespace RTSim
