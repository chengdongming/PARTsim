// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// #pragma once
#ifndef CMDARG_ACTIONS_H
#define CMDARG_ACTIONS_H

#include <string>

namespace cmdarg {

// Forward declaration
struct Argument;

namespace actions {
using Type = int (*)(std::string *dest, const Argument &opt,
                     const std::string &src);

extern int store_string(std::string *dest, const Argument &opt,
                        const std::string &src);

extern int store_true(std::string *dest, const Argument &opt,
                      const std::string &_src_unused);

extern int store_false(std::string *dest, const Argument &opt,
                       const std::string &_src_unused);

extern int show_help_and_exit(std::string *dest, const Argument &opt,
                              const std::string &_src_unused);

/*
 * ┌───────────────────────────────────────────────┐
 * │                 Store Actions                 │
 * └───────────────────────────────────────────────┘
 */

extern const Type store_int;
extern const Type store_long;
extern const Type store_long_long;
extern const Type store_float;
extern const Type store_double;

extern const Type store_positive_int;
extern const Type store_positive_long;
extern const Type store_positive_long_long;
extern const Type store_positive_float;
extern const Type store_positive_double;

extern const Type store_negative_int;
extern const Type store_negative_long;
extern const Type store_negative_long_long;
extern const Type store_negative_float;
extern const Type store_negative_double;

extern const Type store_nonpositive_int;
extern const Type store_nonpositive_long;
extern const Type store_nonpositive_long_long;
extern const Type store_nonpositive_float;
extern const Type store_nonpositive_double;

extern const Type store_nonnegative_int;
extern const Type store_nonnegative_long;
extern const Type store_nonnegative_long_long;
extern const Type store_nonnegative_float;
extern const Type store_nonnegative_double;

extern const Type store_nonzero_int;
extern const Type store_nonzero_long;
extern const Type store_nonzero_long_long;
extern const Type store_nonzero_float;
extern const Type store_nonzero_double;

/*
 * ┌───────────────────────────────────────────────┐
 * │               Increment Actions               │
 * └───────────────────────────────────────────────┘
 */
extern const Type increment_int;
extern const Type increment_long;
extern const Type increment_long_long;
extern const Type increment_float;
extern const Type increment_double;

}  // namespace actions
}  // namespace cmdarg

#endif  // CMDARG_ACTIONS_H
