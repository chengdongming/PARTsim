// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// #pragma once
#ifndef CMDARG_HELP_FORMATTER_H_
#define CMDARG_HELP_FORMATTER_H_

// System headers
#include <ostream>
#include <string>
#include <vector>

// Project headers
#include <cmdarg/argument.hpp>

namespace cmdarg {

class HelpFormatter {
 public:
    struct Params {
        std::string prog;
        std::string description;
        std::string epilogue;
        int indent_base = 4;
        int indent_help = 32;
        int max_length = 80;
        int indent_max_usage = 32;
    };

 protected:
    Params _params;

 public:
    explicit HelpFormatter(const Params &p) : _params(p) {
    }

 protected:
    // Common formatting functions
    virtual std::string _parameter(const Argument &arg) const;

    // Usage formatting functions
    virtual std::string _usage_opt_option(const Argument &arg) const;
    virtual std::string _usage_req_option(const Argument &arg) const;
    virtual std::string _usage_option(const Argument &arg) const;

    // Help formatting functions
    virtual std::string _short_option(const Argument &arg) const;
    virtual std::string _long_option(const Argument &arg) const;
    virtual std::string _sl_option_separator(const Argument &arg) const;
    virtual std::string _default(const Argument &arg) const;
    virtual std::string _description(const Argument &arg) const;
    // virtual std::string _full_help(const Argument &arg) const;

    virtual void _formatDescription(std::ostream *os) const;
    virtual void _formatEpilogue(std::ostream *os) const;

    virtual void _formatUsage(std::ostream *os,
                              const std::vector<Argument> &required,
                              const std::vector<Argument> &optional) const;

    virtual void _formatArgumentHelp(std::ostream *os,
                                     const Argument &arg) const;

 public:
    virtual std::string metavar(const Argument &arg) const;

    virtual void formatHelp(std::ostream *os,
                            const std::vector<Argument> &required,
                            const std::vector<Argument> &optional) const;
};

class TerminalWrapHelpFormatter : public virtual HelpFormatter {
 public:
    explicit TerminalWrapHelpFormatter(const HelpFormatter::Params &p);
};

}  // namespace cmdarg

#endif  // CMDARG_HELP_FORMATTER_H_
