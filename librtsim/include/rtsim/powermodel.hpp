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
#ifndef __POWERMODEL_HPP__
#define __POWERMODEL_HPP__

#include <metasim/cloneable.hpp>
#include <metasim/memory.hpp>
#include <rtsim/class_utils.hpp>
#include <rtsim/opp.hpp>
#include <rtsim/sortedcont.hpp>

#include <rtsim/powermodel_params.hpp>
#include <rtsim/stateless_cpumodel_base.hpp>

#include <cmath>
#include <limits>
#include <map>
#include <string>

namespace RTSim {
    class CPUModel {
        // =================================================
        // Static
        // =================================================
    public:
        /// Factory method
        /// @todo add proper description
        ///
        /// @param key the key that identifies the type of CPUModel to be
        /// instantiated
        /// @param desc the descriptor of the CPU model, used to set all its
        /// parameters before returning it to the user
        /// @param opp the initial OPP of the CPU (in Volts and MHz)
        /// @param f_max the maximum frequency of the CPU (in MHz)
        ///
        /// @returns a new CPU model (wrapped in a std::unique_ptr) generated
        /// from the given description and initialized by forwarding the
        /// given parameters to the model constructor.
        ///
        /// @todo Placeholder, until the integration with the GenericFactory
        /// is ready use this method.
        static std::unique_ptr<CPUModel>
            create(const CPUMDescriptor &power_desc,
                   const CPUMDescriptor &speed_desc, const OPP &start_opp = {},
                   freq_type f_max = FREQ_MAX);

        // =================================================
        // Constructors and destructors
        // =================================================
    private:
        /// @todo Add a proper description
        /// @param opp   the initial OPP of the CPU (in Volts and MHz)
        /// @param f_max the maximum frequency of the CPU (in MHz)
        CPUModel(const OPP &opp = {}, freq_type f_max = FREQ_MAX) :
            _opp(opp),
            _F_max(f_max) {}

    public:
        DEFAULT_COPIABLE(CPUModel);

        /// @returns a new instance of this class obtained by copy construction,
        /// wrapped in a std::unique_ptr
        CLONEABLE(CPUModel, CPUModel);

        /// Requires a virtual destructor, adopting default compiler behavior
        DEFAULT_VIRTUAL_DES(CPUModel);

        // =================================================
        // Methods
        // =================================================
    public:
        /*
        /// Links this CPUModel to the given CPU
        /// @todo how about simply making the CPU a public attribyte?
        void setCPU(CPU *c) {
            _cpu = c;
        }

        /// @returns a bare pointer the CPU linked to this CPUModel
        /// @todo how about simply making the CPU a public attribyte?
        CPU *getCPU() const {
            return _cpu;
        }
        */

        /// Set the current voltage of the CPU (in Volts)
        void setVoltage(volt_type v) {
            OPP opp = _opp;
            opp.voltage = v;
            setOPP(opp, _workload);
        }

        /// Set the current frequency of the CPU (in MHz)
        ///
        /// @note Any value of f greater than the maximum allowed frequency
        /// will be capped to the maximum frequency value.
        void setFrequency(freq_type f) {
            OPP opp = _opp;
            opp.frequency = f;
            setOPP(opp, _workload);
        }

        /// Set the current workload running on the CPU
        void setWorkload(wclass_type workload) {
            setOPP(_opp, workload);
        }

        /// Set the maximum frequency that can be set to this model.
        /// Any frequency supplied greater than the maximum one will be
        /// implicitly capped implicitly when using setFrequency/setOPP
        /// methods.
        ///
        /// @note The maximum frequency value is used as base frequency to
        /// calculate the speedup of a task when the OPP changes.
        ///
        /// @note Setting a maximum value that is lower than the current
        /// frequency will implicitly trigger an OPP change for the linked
        /// CPU (basically the current frequency will be capped to the new
        /// maximum value).
        ///
        /// @param f_max the new maximum frequency to be used from now on
        void setFrequencyMax(freq_type f_max) {
            _F_max = f_max;

            // Setting another time the OPP will cap the frequency to the new
            // max and re-calculate the modeled values
            if (_F_max < _opp.frequency)
                setOPP(_opp, _workload);
        }

        /// Set the current Operating Performance Point (OPP)
        ///
        /// @note Any value of @p f greater than the maximum allowed
        /// frequency will be capped to the maximum frequency value.
        ///
        /// @param opp the new OPP
        void setOPP(const OPP &opp, const wclass_type &workload = "") {
            _opp = opp;
            if (workload != "")
                _workload = workload;

            if (_opp.frequency > _F_max)
                _opp.frequency = _F_max;

            power_value = power_model->lookupValue(_opp, _workload, _F_max);
            speed_value = speed_model->lookupValue(_opp, _workload, _F_max);
        }

        /// @returns the current OPP
        OPP getOPP() const {
            return _opp;
        }

        /// @returns the current frequency of the CPU in MHz
        freq_type getFrequency() const {
            return getOPP().frequency; // / 1000;
        }

        /// @returns the maximum frequency of the CPU in MHz
        freq_type getFrequencyMax() const {
            return _F_max;
        }

        /// @returns the current voltage of the CPU in Volts
        volt_type getVoltage() const {
            return getOPP().voltage;
        }

        /// Performs a prediction on the power consumption if the given working
        /// condition was set.
        ///
        /// @param workload the workload class of the task
        /// @param opp desired OPP [MHz and Volts]
        ///
        /// @returns the forshadowed power
        watt_type lookupPower(const OPP &opp,
                              const std::string &workload) const {
            return power_model->lookupValue(opp, workload);
        }

        /// Performs a prediction on the power consumption if the given working
        /// condition was set.
        ///
        /// @param workload the workload class of the task
        /// @param opp desired OPP [MHz and Volts]
        ///
        /// @returns the forshadowed power
        speed_type lookupSpeed(const OPP &opp,
                               const std::string &workload) const {
            return speed_model->lookupValue(opp, workload);
        }

        /// Simple getter
        ///
        /// @note The value returned from this function is valid only if the
        /// system is in a "valid" state (i.e. the OPP set on this model is
        /// one acceptable for the linked CPU). On some implementations of
        /// this class, this method may work after simly calling
        /// setFrequency(), but it is advisable to call always both
        /// setFrequency() and setVoltage() (or even better, setOPP()) to
        /// ensure the system is in a consistent state.
        ///
        /// @returns the current power consumption of the linked CPU
        virtual watt_type getPower() {
            return power_value;
        }

        /// Simple getter
        ///
        /// @note The value returned from this function is valid only if the
        /// system is in a "valid" state (i.e. the OPP set on this model is
        /// one acceptable for the linked CPU). On some implementations of
        /// this class, this method may work after simly calling
        /// setFrequency(), but it is advisable to call always both
        /// setFrequency() and setVoltage() (or even better, setOPP()) to
        /// ensure the system is in a consistent state.
        ///
        /// @returns the current speedup of the task running on the linked
        /// CPU
        /// @todo why was it long double?
        virtual speed_type getSpeed() {
            return speed_value;
        }

        // =================================================
        // Data
        // =================================================
    private:
        // Following values correspond to current task running on the linked
        // CPU

        /// Current workload class
        wclass_type _workload = "idle";

        /// The current OPP of the modeled CPU. Units are MHz and Volts.
        OPP _opp;

        /// Maximum frequency that can be set to the given processor [MHz]
        freq_type _F_max = FREQ_MAX;

        /// Current power consumption [W]
        watt_type power_value = 0;

        /// Current speedup/slowness
        /// @todo CHECK WHICH OF THE TWO IT IS!
        speed_type speed_value = 0;

        // Shared pointers are fine because the models must be truly stateless!

        /// Model used for the power consumption
        std::shared_ptr<StatelessCPUModel<ModelType::Power>> power_model =
            nullptr;

        /// Model used for the power consumption
        std::shared_ptr<StatelessCPUModel<ModelType::Speed>> speed_model =
            nullptr;
    };
} // namespace RTSim

#endif
