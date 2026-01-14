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
#ifndef __STATELESSMODELS_TBAPPROX_HPP__
#define __STATELESSMODELS_TBAPPROX_HPP__

#include <map>

#include <rtsim/powermodel_params.hpp>
#include <rtsim/stateless_cpumodel_tb.hpp>

namespace RTSim {
    // =====================================================
    // TBApproxCPUModel
    // =====================================================

    // TB Approx model class declaration
    DERIVED_MODEL_CLASS(TBApproxCPUModel, TBCPUModel);

    // =====================================================
    // Constructor and factory
    // =====================================================

    // Same as base class, changes only implementation of lookup!
    template <ModelType model_type>
    TBApproxCPUModel<model_type>::TBApproxCPUModel(const CPUMDescriptor &desc) :
        TBCPUModel<model_type>(desc) {}

    MODEL_CREATE(TBApproxCPUModel)

    // =====================================================
    // Implementation
    // =====================================================

#define cast(T, x) (static_cast<T>(x))
    template <class RT = long double>
    static inline RT opp_power_distance(const OPP &x, const OPP &xi) {
        return std::pow(cast(RT, x.voltage) - cast(RT, xi.voltage), 2) *
               std::abs(cast(RT, x.frequency) - cast(RT, xi.frequency));
    }
    template <class RT = long double>
    static inline RT opp_speed_distance(const OPP &x, const OPP &xi) {
        // TODO: hella not sure about this!
        return std::pow(cast(RT, x.frequency) - cast(RT, xi.frequency), 2);
    }
#undef cast

    // Both method implementations for power and speed are the same!
    template <ModelType model_type>
    typename TBApproxCPUModel<model_type>::value_type
        TBApproxCPUModel<model_type>::lookupValue(const OPP &opp,
                                                  const wclass_type &workload,
                                                  freq_type) const {
        // FIXME: measurement units!!!
        // OPP opp_copy = opp;
        // opp_copy.frequency *= 1000;
        // opp_copy.voltage *= 1000;

        long double (*distance_fn)(const OPP &, const OPP &);
        switch (model_type) {
        case ModelType::Power:
            distance_fn = opp_power_distance;
            break;
        case ModelType::Speed:
            distance_fn = opp_speed_distance;
            break;
        default:
            throw std::exception{};
        }

        using value_type = typename TBApproxCPUModel<model_type>::value_type;
        return TableBasedModel<value_type>::approx_table_lookup(opp, workload,
                                                                distance_fn);
    }

} // namespace RTSim

#endif // __STATELESSMODELS_TBAPPROX_HPP__
