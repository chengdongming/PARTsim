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
#ifndef __STATELESS_BASE_HPP__
#define __STATELESS_BASE_HPP__

#include <limits>
#include <string>

#include <metasim/cloneable.hpp>
#include <metasim/memory.hpp>
#include <rtsim/class_utils.hpp>
#include <rtsim/opp.hpp>

namespace RTSim {
    // Forward declarations
    class CPUMDescriptor;

    // Defined in opp.hpp
    // using volt_type = double;       /// Represents voltage in Volts
    // using freq_type = unsigned int; /// Represents frequency in MHz

    using watt_type = double; /// Represents power in Watts
    using speed_type = double; /// Represents speedup ratio
    using wclass_type = std::string; /// Represents the workload class of a task

    constexpr freq_type FREQ_MAX = std::numeric_limits<freq_type>::max();

    // =========================================================================
    // ModelType
    // =========================================================================

    enum class ModelType {
        Power,
        Speed,
    };

    template <ModelType mtype>
    struct ModelTypeToValueType;

    template <>
    struct ModelTypeToValueType<ModelType::Power> {
        using value_type = watt_type;
    };

    template <>
    struct ModelTypeToValueType<ModelType::Speed> {
        using value_type = speed_type;
    };

    // =========================================================================
    // StatelessCPUModel
    // =========================================================================

    // #define VALUE_TYPE(mtype) StatelessCPUModel<model_type>::value_type

    /// This class is basically an interface and a factory for classes that
    /// implement stateless CPU models, for either Power or Speed (each needs a
    /// separate class implementation, templates can help with that)
    template <ModelType model_type>
    class StatelessCPUModel {
    public:
        using value_type =
            typename ModelTypeToValueType<model_type>::value_type;

        // =================================================
        // Static
        // =================================================
    public:
        /// Factory method
        /// @todo add proper description
        ///
        /// @param key the key that identifies the type of CPU model to be
        /// instantiated
        /// @param desc the descriptor of the CPU model, used to set all its
        /// parameters before returning it to the user
        ///
        /// @returns a new CPU model (wrapped in a std::unique_ptr) generated
        /// from the given description and initialized by forwarding the given
        /// parameters to the model constructor.
        ///
        /// @todo Placeholder, until the integration with the GenericFactory
        /// is ready use this method.
        static std::unique_ptr<StatelessCPUModel>
            create(const CPUMDescriptor &desc);

        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        /// @todo Add a proper description/construction mechanism for subclasses
        StatelessCPUModel(const CPUMDescriptor &desc) {}

        DEFAULT_COPIABLE(StatelessCPUModel);

        /// @returns a new instance of this class obtained by copy construction,
        /// wrapped in a std::unique_ptr
        BASE_CLONEABLE(StatelessCPUModel);

        /// Requires a virtual destructor, but adopting default compiler
        /// behavior
        DEFAULT_VIRTUAL_DES(StatelessCPUModel);

        // =================================================
        // Methods
        // =================================================
    public:
        /// Checks what would be the value when the triple given as input is
        /// selected for the current cpu.
        ///
        /// @param opp the OPP to select
        /// @param workload the workload class of the task
        /// @param f_max the maximum frequency that could be set
        ///
        /// @returns the forshadowed value
        virtual value_type lookupValue(const OPP &opp,
                                       const wclass_type &workload,
                                       freq_type f_max = FREQ_MAX) const = 0;
    };

    // =========================================================================
    // MACROS FOR MODELS DECLARATION
    // =========================================================================

    // The two ## before __VA_ARGS__ are necessary, since the variadic arguments
    // are entirely optional.
#define DERIVED_MODEL_CLASS(cls_name, base_cls_name, ...)                      \
    template <ModelType model_type>                                            \
    class cls_name : public base_cls_name<model_type>, ##__VA_ARGS__ {         \
    public:                                                                    \
        using base_type = StatelessCPUModel<model_type>;                       \
        using base_cls_type = base_cls_name<model_type>;                       \
        using value_type = typename base_type::value_type;                     \
                                                                               \
    public:                                                                    \
        static std::unique_ptr<base_type> create(const CPUMDescriptor &desc);  \
                                                                               \
    public:                                                                    \
        cls_name(const CPUMDescriptor &desc = {});                             \
        DEFAULT_COPIABLE(cls_name);                                            \
        CLONEABLE(base_type, cls_name, override);                              \
                                                                               \
        virtual value_type                                                     \
            lookupValue(const OPP &opp, const wclass_type &workload,           \
                        freq_type f_max = FREQ_MAX) const override;            \
    }

#define MODEL_CLASS(cls_name, ...)                                             \
    DERIVED_MODEL_CLASS(cls_name, StatelessCPUModel, ##__VA_ARGS__)

    // =========================================================================
    // MACRO FOR FACTORY METHOD DECLARATION
    // =========================================================================

#define MODEL_CREATE(cls_name)                                                 \
    template <ModelType model_type>                                            \
    std::unique_ptr<StatelessCPUModel<model_type>>                             \
        cls_name<model_type>::create(const CPUMDescriptor &desc) {             \
        return std::make_unique<cls_name<model_type>>(desc);                   \
    }

} // namespace RTSim

#endif
