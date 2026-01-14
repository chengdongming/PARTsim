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
#ifndef __STATELESSMODELS_TABLE_HPP__
#define __STATELESSMODELS_TABLE_HPP__

#include <map>

#include <rtsim/interpolate.hpp>
#include <rtsim/stateless_cpumodel_base.hpp>

namespace RTSim {
    template <class T>
    static inline OPP to_first(T v) {
        return v->first;
    }

    template <class T, class RT = watt_type>
    static inline RT to_second(T v) {
        return v->second;
    }

    // =====================================================
    // TableBasedModel
    // =====================================================

    // This class is to be used as implementation to declare a StatelessCPUModel
    // class. To accomplish this, inherit this class thanks to multiple
    // inheritance features of C++.

    /// Simple implementation of a table-based map of parameters.
    /// The two values needed to get the params are a valid OPP and workload.
    template <class value_type>
    class TableBasedModel {
    protected:
        using mapping = std::pair<OPP, value_type>;

        using smap =
            sorted_map<OPP, value_type,
                       less_pair<OPP, value_type, OPPLessSortByFrequencyOnly>>;

        using map_map = std::map<std::string, smap>;
        using map_map_citer = typename map_map::const_iterator;

    protected:
        /// Sets the parameters for the given workload class
        ///
        /// @param workload the workload class; if it does not exist yet, it is
        /// added to the set of supported workload classes
        /// @param expected_value the parameters used to calculate the estimated
        /// value
        void setParams(const OPP &opp, const wclass_type &workload,
                       const value_type &expected_value) {
            mapping kv = std::make_pair(opp, expected_value);
            _map[workload].insert(kv);
        }

        inline map_map_citer
            find_suitable_map(const wclass_type &workload) const {
            // First look for the good workload
            auto res = _map.find(workload);
            if (res != _map.cend())
                return res;

            // TODO: WARN WHEN A WORKLOAD IS NOT FOUND!!!

            // If not found, use one of the following fallbacks
            const wclass_type fallbacks[2] = {{"busy"}, {"idle"}};
            for (auto key : fallbacks) {
                res = _map.find(key);
                if (res != _map.cend())
                    return res;
            }
            return _map.cend();
        }

        inline value_type exact_map_lookup(const smap &map, const OPP &key,
                                           const wclass_type &workload) const {
            auto res = map.find_from_key(key);
            if (res == map.cend())
                return 0;
            return res->second;
        }

        inline value_type
            exact_table_lookup(const OPP &opp,
                               const wclass_type &workload) const {
            auto res_map = find_suitable_map(workload);
            if (res_map == _map.end())
                return 0;

            auto &map = res_map->second;
            return exact_map_lookup(map, opp, workload);
        }

        template <class Distance_fn>
        inline value_type approx_table_lookup(const OPP &opp,
                                              const wclass_type &workload,
                                              Distance_fn distance) const {
            // The beginning is the same as the exact table lookup
            const auto res_map = find_suitable_map(workload);
            if (res_map == _map.end())
                return 0;

            auto &map = res_map->second;
            // However, we do a simple index lookup now
            auto res = map.first_non_less_key(opp);
            if (res == map.cend()) {
                // Use maximum value in the table if not found (at least one
                // value always present in each map)!
                res = map.cend() - 1;
            }

            // If key is greater or equal than the resulting value, we found
            // what we were looking for (or busted for the maximum value)
            auto res_key = res->first;
            if (!(opp < res_key)) {
                return res->second;
            }

            // At this point, the key-value pair is such that the key is the
            // first strictly greater than the supplied opp. We can get the
            // first key strictly less than the supplied opp by picking the
            // previous value with respect to this iterator (if any).
            if (res == map.cbegin())
                return res->second;

            auto next = res;
            auto prev = res - 1;

            using iterator_type = typename smap::const_iterator;

            auto to_codomain = to_second<decltype(res), value_type>;
            auto to_domain = to_first<iterator_type>;

            return interpolate<OPP, long double>(opp, prev, next, to_domain,
                                                 to_codomain, distance);
        }

    protected:
        /// The double-lookup table that from a pair of workload class and OPP
        /// can provide the expected value
        map_map _map;
    };

} // namespace RTSim

#endif // __STATELESSMODELS_TABLE_HPP__
