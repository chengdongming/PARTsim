/***************************************************************************
begin                : 2021-02-08 12:08:57+01:00
copyright            : (C) 2021 by Gabriele Ara
email                : gabriele.ara@santannapisa.it, gabriele.ara@live.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
#ifndef __STATELESSMODELS_TB_HPP__
#define __STATELESSMODELS_TB_HPP__

#include <map>

#include <rtsim/powermodel_params.hpp>
#include <rtsim/stateless_cpumodel_base.hpp>
#include <rtsim/stateless_cpumodel_table.hpp>

namespace RTSim {
    // =====================================================
    // TBCPUModel
    // =====================================================

#define TBVTYPE StatelessCPUModel<model_type>::value_type

    // TB model class declaration
    MODEL_CLASS(TBCPUModel, public TableBasedModel<typename TBVTYPE>);

    // =====================================================
    // Constructor and factory
    // =====================================================

    template <ModelType model_type>
    static typename ModelTypeToValueType<model_type>::value_type
        convertTBValue(const CPUModelTBParams &);

    template <>
    typename ModelTypeToValueType<ModelType::Power>::value_type
        convertTBValue<ModelType::Power>(const CPUModelTBParams &pp) {
        return pp.power;
    }

    template <>
    typename ModelTypeToValueType<ModelType::Speed>::value_type
        convertTBValue<ModelType::Speed>(const CPUModelTBParams &pp) {
        return pp.speed;
    }

    template <ModelType model_type>
    TBCPUModel<model_type>::TBCPUModel(const CPUMDescriptor &desc) :
        StatelessCPUModel<model_type>(desc) {
        using value_type =
            typename ModelTypeToValueType<model_type>::value_type;

        for (const auto &p : desc.params) {
            const auto *pp = dynamic_cast<CPUModelTBParams *>(p.get());
            const OPP opp = {pp->freq, pp->volt};
            TableBasedModel<value_type>::setParams(
                opp, pp->workload, convertTBValue<model_type>(*pp));
        }
    }

    MODEL_CREATE(TBCPUModel)

    // =====================================================
    // Implementation
    // =====================================================

    // Both method implementations for power and speed are the same!
    template <ModelType model_type>
    typename TBCPUModel<model_type>::value_type
        TBCPUModel<model_type>::lookupValue(const OPP &opp,
                                            const wclass_type &workload,
                                            freq_type) const {
        using value_type = typename TBCPUModel<model_type>::value_type;

        // FIXME: measurement units!!!
        // OPP opp_copy = opp;
        // opp_copy.frequency = 1000;
        // opp_copy.voltage *= 1000;

        return TableBasedModel<value_type>::exact_table_lookup(opp, workload);
    }

} // namespace RTSim

#endif // __STATELESSMODELS_TB_HPP__
