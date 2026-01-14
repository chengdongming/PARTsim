#ifndef __INTERPOLATE_HPP__
#define __INTERPOLATE_HPP__

#include <cmath>

// This mechanism uses Inverse Distance Weighting to approximate an unknown
// function from a set of points in the form of an iterable set of pairs
// domain-codomain points.
// To achieve that goal, a distance function (a mathematical metric) in the
// domain must be defined and supplied to the interpolating functions.

template <class Domain, class Codomain, class Distance_fn,
          class Exponent = double>
static inline Codomain weight(Domain x, Domain xi, Distance_fn dist,
                              Exponent exp = 2.0) {
    return Codomain(1.0) / std::pow(dist(x, xi), exp);
}

template <class Domain, class Codomain, class BIter, class EIter,
          class Domain_fn, class Codomain_fn, class Distance_fn,
          class Exponent = double>
static inline Codomain interpolate(Domain x, BIter begin, EIter end,
                                   Domain_fn domain, Codomain_fn codomain,
                                   Distance_fn dist, Exponent exp = 2.0) {
    Codomain weightened_sum = 0;
    Codomain sum_of_weights = 0;
    for (auto cur = begin; cur != end; ++cur) {
        Codomain cur_weight = weight<Domain, Codomain, Distance_fn, Exponent>(
            x, domain(cur), dist, exp);
        weightened_sum += cur_weight * codomain(cur);
        sum_of_weights += cur_weight;
    }

    return weightened_sum / sum_of_weights;
}

#endif // __INTERPOLATE_HPP__
