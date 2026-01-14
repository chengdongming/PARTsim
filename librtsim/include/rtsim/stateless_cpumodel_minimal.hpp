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
#ifndef __STATELESSMODELS_MINIMAL_HPP__
#define __STATELESSMODELS_MINIMAL_HPP__

#include <map>

#include <rtsim/stateless_cpumodel_base.hpp>

namespace RTSim {
    // =====================================================
    // MinimalCPUModel
    // =====================================================

    // Minimal model class declaration
    MODEL_CLASS(MinimalCPUModel);

    // =====================================================
    // Constructor and factory
    // =====================================================

    template <ModelType model_type>
    MinimalCPUModel<model_type>::MinimalCPUModel(const CPUMDescriptor &desc) :
        StatelessCPUModel<model_type>(desc) {}

    MODEL_CREATE(MinimalCPUModel)

    // =====================================================
    // Implementation
    // =====================================================

    // TODO: why did the old implementation set the frequency to KHz
    // internally?
    // TODO: check formulae against measurement units

    template <>
    MinimalCPUModel<ModelType::Power>::value_type
        MinimalCPUModel<ModelType::Power>::lookupValue(const OPP &opp,
                                                       const wclass_type &,
                                                       freq_type) const {
        return (opp.voltage * opp.voltage) * opp.frequency;
    }

    template <>
    MinimalCPUModel<ModelType::Speed>::value_type
        MinimalCPUModel<ModelType::Speed>::lookupValue(const OPP &opp,
                                                       const wclass_type &,
                                                       freq_type f_max) const {
        return speed_type(f_max) / speed_type(opp.frequency);
    }

} // namespace RTSim

#endif // __STATELESSMODELS_MINIMAL_HPP__
