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
#ifndef __STATELESSMODELS_BP_HPP__
#define __STATELESSMODELS_BP_HPP__

#include <map>

#include <rtsim/powermodel_params.hpp>
#include <rtsim/stateless_cpumodel_base.hpp>
#include <rtsim/stateless_cpumodel_param.hpp>

// TODO: use only key and params vector
#include <rtsim/system_descriptor.hpp>

namespace RTSim {
    // =====================================================
    // BPCPUModel
    // =====================================================

    template <ModelType mType>
    struct ModelTypeToBPParamsType;

    template <>
    struct ModelTypeToBPParamsType<ModelType::Power> {
        using params_type = CPUModelBPParams::PowerModelParams;
    };

    template <>
    struct ModelTypeToBPParamsType<ModelType::Speed> {
        using params_type = CPUModelBPParams::SpeedModelParams;
    };

#define BPPTYPE ModelTypeToBPParamsType<model_type>::params_type

    // BP model class declaration
    MODEL_CLASS(BPCPUModel, public ParametrizedModel<typename BPPTYPE>);

    // =====================================================
    // Constructor and factory
    // =====================================================

    // These functions are helpers to provide the correct parameters to the
    // BPCPUModel class

    template <ModelType model_type>
    static typename ModelTypeToBPParamsType<model_type>::params_type
        convertBPParams(const CPUModelBPParams &);

    template <>
    typename ModelTypeToBPParamsType<ModelType::Power>::params_type
        convertBPParams<ModelType::Power>(const CPUModelBPParams &pp) {
        return pp.params.power;
    }

    template <>
    typename ModelTypeToBPParamsType<ModelType::Speed>::params_type
        convertBPParams<ModelType::Speed>(const CPUModelBPParams &pp) {
        return pp.params.speed;
    }

    template <ModelType model_type>
    BPCPUModel<model_type>::BPCPUModel(const CPUMDescriptor &desc) :
        StatelessCPUModel<model_type>(desc) {
        using params_type =
            typename ModelTypeToBPParamsType<model_type>::params_type;

        for (const auto &p : desc.params) {
            const auto *pp = dynamic_cast<CPUModelBPParams *>(p.get());
            ParametrizedModel<params_type>::setParams(
                pp->workload, convertBPParams<model_type>(*pp));
        }
    }

    MODEL_CREATE(BPCPUModel)

    // =====================================================
    // Implementation
    // =====================================================

    template <>
    BPCPUModel<ModelType::Power>::value_type
        BPCPUModel<ModelType::Power>::lookupValue(const OPP &opp,
                                                  const wclass_type &workload,
                                                  freq_type) const {
        // This model was trained using frequencies in KHz
        const freq_type f = opp.frequency * 1000;
        const volt_type v = opp.voltage;

        auto i = findParams(workload);
        if (!foundParams(i)) {
            // FALLBACK: see also below
            i = findParams("idle");

            // FIXME: if using setVoltage and setFrequency
            // independently, this exception may be
            // erroneously raised when there's no need for
            // these parameters!
            // TODO: some error message!
            // throw std::exception{};
        }

        const auto params = getParams(i);
        double K, eta, gamma, disp;
        double P_charge, P_short, P_dyn, P_leak;

        disp = params.d;
        K = params.k;
        eta = params.e;
        gamma = params.g;

        // Evaluation of the P_charge
        P_charge = K * f * (v * v);

        // Evaluation of the P_short
        P_short = eta * P_charge;

        // Evalution of the P_dyn
        P_dyn = P_short + P_charge;

        // Evaluation of P_leak
        P_leak = gamma * v * P_dyn;

        // Evaluation of the total Power
        return P_leak + P_dyn + disp;
    }

    template <>
    BPCPUModel<ModelType::Speed>::value_type
        BPCPUModel<ModelType::Speed>::lookupValue(const OPP &opp,
                                                  const wclass_type &workload,
                                                  freq_type) const {
        // This model was trained using frequencies in KHz
        const freq_type f = opp.frequency * 1000;
        const volt_type v = opp.voltage;

        auto i = findParams(workload);
        if (!foundParams(i)) {
            i = findParams("idle");
            // throw std::exception{};
        }
        const auto params = getParams(i);

        long double disp = params.a;
        long double ideal = params.b / static_cast<long double>(f);
        long double slope =
            params.c * std::exp(-(static_cast<long double>(f) / params.d));

        return 1.0 / (disp + ideal + slope);
    }

} // namespace RTSim

#endif // __STATELESSMODELS_BP_HPP__
