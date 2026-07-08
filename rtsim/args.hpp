/*
 * Filename: rtsim/args.hpp
 * Path: rtsim
 * Created Date: Monday, May 16th 2022, 9:52:03 am
 * Author: Gabriele Ara
 *
 * Copyright (c) 2022 Gabriele Ara
 */

// #pragma once
#ifndef RTSIM_ARGS_H_
#define RTSIM_ARGS_H_

#include <iostream>
#include <sstream>

#include <cmdarg.hpp>

static inline bool string_endswith(const std::string &s,
                                   const std::string &end) {
    int endlen = end.length();
    if (s.length() < static_cast<size_t>(endlen)) {
        return false;
    }
    std::string s_end = s.substr(s.length() - endlen);
    return s_end == end;
}

static inline int list_append(std::string *dest, const cmdarg::Argument &opt,
                              const std::string &src) {
    std::ostringstream oss;
    oss << src << std::endl;
    *dest += oss.str();
    return 0;
}

static inline std::vector<std::string> list_split(const std::string &src) {
    std::vector<std::string> out;
    std::istringstream iss{src};

    std::string str;
    while (std::getline(iss, str)) {
        if (str.length() > 0)
            out.emplace_back(str);
    }

    return out;
}

static inline int store_txt(std::string *dest, const cmdarg::Argument &opt,
                            const std::string &src) {
    if (string_endswith(src, ".txt")) {
        return cmdarg::actions::store_string(dest, opt, src);
    }

    std::cerr << "Error: argument --" << opt.long_opt << "/-" << opt.short_opt
              << ": expected filename ending with '.txt'!" << std::endl;
    return 1;
}

static inline int list_append_txt_json(std::string *dest,
                                       const cmdarg::Argument &opt,
                                       const std::string &src) {
    if (string_endswith(src, ".txt")) {
        return list_append(dest, opt, src);
    }

    if (string_endswith(src, ".json")) {
        return list_append(dest, opt, src);
    }

    std::cerr << "Error: argument --" << opt.long_opt << "/-" << opt.short_opt
              << ": expected filename ending with '.txt' or '.json'!"
              << std::endl;
    return 1;
}

static inline cmdarg::Options parse_arguments(int argc, char *argv[]) {
    cmdarg::Parser parser{{
        .prog = argv[0],
        .description = "",
        .epilogue = "",
    }};

    parser.addArgument({
        .long_opt = "system",
        .required = true,
        .help = "The YAML file description for the system",
    });
    parser.addArgument({
        .long_opt = "taskset",
        .required = true,
        .help = "The file containing the taskset description",
    });
    parser.addArgument({
        .long_opt = "duration",
        .required = true,
        .help = "The duration of the simulation in time units",
    });
    parser.addArgument({
        .long_opt = "trace",
        .short_opt = 't',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::REQUIRED,
        .help = "The file name where to store a trace (either txt or json)",
        .action = list_append_txt_json,
    });
    parser.addArgument({
        .long_opt = "debug",
        .short_opt = 'd',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::NO,
        .help = "Enables debug prints throughout the simulation",
        .default_value = "false",
        .action = cmdarg::actions::store_true,
    });
    parser.addArgument({
        .long_opt = "debug-out",
        .short_opt = 'D',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::REQUIRED,
        .help = "The file name where to store debug prints",
        .default_value = "debug.txt",
        .action = store_txt,
    });
    parser.addArgument({
        .long_opt = "semantic-traces",
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::NO,
        .help = "Enable scheduler semantic decision events in JSON traces",
        .default_value = "false",
        .action = cmdarg::actions::store_true,
    });

    return parser.parse(argc, argv);
}

#endif // RTSIM_ARGS_H_
