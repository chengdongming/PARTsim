// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// #pragma once
#ifndef CMDARG_ARGUMENT_H_
#define CMDARG_ARGUMENT_H_

#include <cmdarg/actions.hpp>

namespace cmdarg {

struct Argument {
 private:
    using ActionType = actions::Type;

 public:
    enum class ParameterRequired {
        NO = 0,
        REQUIRED = 1,
        OPTIONAL = 2,
    };

    const char *long_opt;
    const char short_opt = 0;
    const bool required = false;
    const ParameterRequired parameter_required = ParameterRequired::NO;
    const char *help = "";
    const char *default_value = "";
    const ActionType action = actions::store_string;
};

}  // namespace cmdarg

#endif  // CMDARG_ARGUMENT_H_
