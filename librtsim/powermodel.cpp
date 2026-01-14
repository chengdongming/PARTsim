
#include <rtsim/powermodel.hpp>

#include <rtsim/stateless_cpumodel_base.hpp>
#include <rtsim/stateless_cpumodel_bp.hpp>
#include <rtsim/stateless_cpumodel_minimal.hpp>
#include <rtsim/stateless_cpumodel_tb.hpp>
#include <rtsim/stateless_cpumodel_tbapprox.hpp>

#include <rtsim/system_descriptor.hpp>

namespace RTSim {
    // =====================================================
    // StatelessCPUModel
    // =====================================================

    template <ModelType model_type>
    std::unique_ptr<StatelessCPUModel<model_type>>
        StatelessCPUModel<model_type>::create(const CPUMDescriptor &desc) {
        if (desc.type == CPUModelMinimalParams::key) {
            return MinimalCPUModel<model_type>::create(desc);
        }

        if (desc.type == CPUModelBPParams::key) {
            return BPCPUModel<model_type>::create(desc);
        }

        if (desc.type == CPUModelTBParams::key) {
            return TBCPUModel<model_type>::create(desc);
        }
        if (desc.type == CPUModelTBApproxParams::key) {
            return TBApproxCPUModel<model_type>::create(desc);
        }

        throw std::exception{};
    }

    // =====================================================
    // CPUModel
    // =====================================================

    std::unique_ptr<CPUModel> CPUModel::create(const CPUMDescriptor &power_desc,
                                               const CPUMDescriptor &speed_desc,
                                               const OPP &start_opp,
                                               freq_type f_max) {
        // NOTE: Instantiating unique pointers like this is (typically) not
        // exception-memory-safe in the case of instruction reordering!
        std::unique_ptr<CPUModel> model =
            std::unique_ptr<CPUModel>(new CPUModel(start_opp, f_max));

        model->power_model =
            StatelessCPUModel<ModelType::Power>::create(power_desc);

        model->speed_model =
            StatelessCPUModel<ModelType::Speed>::create(power_desc);

        return model;
    }

} // namespace RTSim
