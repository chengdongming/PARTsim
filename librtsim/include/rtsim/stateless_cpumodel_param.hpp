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
#ifndef __STATELESSMODELS_PARAM_HPP__
#define __STATELESSMODELS_PARAM_HPP__

#include <rtsim/stateless_cpumodel_base.hpp>

namespace RTSim {
    // =====================================================
    // ParametrizedModel
    // =====================================================

    // This class is to be used as implementation to declare a StatelessCPUModel
    // class. To accomplish this, inherit this class thanks to multiple
    // inheritance features of C++.

    /// Simple implementation of a key-value map of parameters
    template <class params_type>
    class ParametrizedModel {
    protected:
        using map_type = std::map<wclass_type, params_type>;
        using iter_type = typename map_type::iterator;
        using citer_type = typename map_type::const_iterator;

    public:
        /// @returns a const iterator to the params associated with the given
        /// workload, if any. Use foundParams to check whether the params were
        /// found or not
        citer_type findParams(const wclass_type &workload) const {
            return _params.find(workload);
        }

        /// @returns true if the iterator is "valid"
        bool foundParams(const citer_type &i) const {
            return i != _params.cend();
        }

        /// @returns the params from a "valid" iterator
        const params_type &getParams(const citer_type &i) const {
            return i->second;
        }

    protected:
        /// Sets the parameters for the given workload class
        ///
        /// @param workload the workload class; if it does not exist yet, it
        /// is added to the set of supported workload classes
        /// @param params the parameters used to calculate the estimated
        /// value
        void setParams(const wclass_type &workload, const params_type &params) {
            _params[workload] = params;
        }

    private:
        /// Stores all associations between workload classes and params
        map_type _params;
    };

} // namespace RTSim

#endif // __STATELESSMODELS_PARAM_HPP__
