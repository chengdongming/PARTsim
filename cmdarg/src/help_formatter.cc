// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// C headers
#include <sys/ioctl.h>
#include <unistd.h>

// System headers
#include <algorithm>
#include <iosfwd>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>
#include <limits>

// Project headers
#include <cmdarg/help_formatter.hpp>

namespace cmdarg {

static inline std::vector<std::string> _tokenize(std::string s) {
    std::istringstream iss{s};

    return std::vector<std::string>{std::istream_iterator<std::string>(iss),
                                    {}};
}

static inline std::string _newline() {
    std::ostringstream oss;
    oss << std::endl;
    return oss.str();
}

std::string HelpFormatter::metavar(const Argument &arg) const {
    std::string out = arg.long_opt;
    std::transform(out.begin(), out.end(), out.begin(), ::toupper);
    return out;
}

std::string HelpFormatter::_parameter(const Argument &arg) const {
    std::string out;
    std::string the_metavar = metavar(arg);

    switch (arg.parameter_required) {
        case Argument::ParameterRequired::NO:
            break;
        case Argument::ParameterRequired::REQUIRED:
            out += " " + the_metavar;
            break;
        case Argument::ParameterRequired::OPTIONAL:
            out += " [" + the_metavar + "]";
            break;
    }

    return out;
}

std::string HelpFormatter::_usage_opt_option(const Argument &arg) const {
    std::string out = (arg.short_opt) ? _short_option(arg) : _long_option(arg);
    return "[" + out + "]";
}

std::string HelpFormatter::_usage_req_option(const Argument &arg) const {
    return metavar(arg);
}

std::string HelpFormatter::_usage_option(const Argument &arg) const {
    return (arg.required) ? _usage_req_option(arg) : _usage_opt_option(arg);
}

std::string HelpFormatter::_short_option(const Argument &arg) const {
    if (arg.required || !arg.short_opt) return "";

    std::string out = "-";
    out.push_back(arg.short_opt);
    return out + _parameter(arg);
}

std::string HelpFormatter::_long_option(const Argument &arg) const {
    if (arg.required) {
        return metavar(arg);
    }

    return "--" + std::string(arg.long_opt) + _parameter(arg);
}

std::string HelpFormatter::_sl_option_separator(const Argument &arg) const {
    if (arg.required || !arg.short_opt) return "";
    return ",";
}

// std::string HelpFormatter::_short_long_option(const Argument &arg) const {
//     std::string _short = _short_option(arg);
//     std::string _long = _long_option(arg);
//     if (_short.length() > 0) {
//         return _short + _sl_option_separator(arg) + _long;
//     }
//     return _long;
// }

std::string HelpFormatter::_default(const Argument &arg) const {
    if (arg.required) return "";
    if (arg.parameter_required == Argument::ParameterRequired::NO) return "";

    return " (default: '" + std::string(arg.default_value) + "')";
}

std::string HelpFormatter::_description(const Argument &arg) const {
    return std::string(arg.help) + _default(arg);
}

template <class StringIt>
std::vector<std::string> formatWrappedTokens(StringIt begin, StringIt end,
                                             const int wraplen) {
    std::vector<std::string> out;
    // std::string line;
    std::ostringstream oss;
    int printed = 0;

    for (auto it = begin; it < end; ++it) {
        if (it != begin) {
            if (printed + it->length() + 1 > wraplen) {
                out.push_back(oss.str());
                oss.clear();
                oss.str("");
                printed = 0;
            } else {
                oss << " ";
                ++printed;
            }
        }

        oss << *it;
        printed += it->length();
    }

    out.push_back(oss.str());

    return out;
}

void HelpFormatter::_formatArgumentHelp(std::ostream *os,
                                        const Argument &arg) const {
    std::string indent_arg = std::string(_params.indent_base, ' ');
    std::string indent_help = std::string(_params.indent_help, ' ');

    std::vector argument_format = {
        _short_option(arg) + _sl_option_separator(arg), _long_option(arg)};

    argument_format =
        formatWrappedTokens(argument_format.cbegin(), argument_format.cend(),
                            _params.indent_help - _params.indent_base);

    std::vector<std::string> description = _tokenize(_description(arg));
    description = formatWrappedTokens(description.cbegin(), description.cend(),
                                      _params.max_length - _params.indent_help);

    std::string next_indent = indent_arg;
    for (const auto token : argument_format) {
        *os << next_indent << token;
        next_indent = _newline() + indent_arg;
    }

    int remaining = (_params.indent_help - _params.indent_base) -
                    argument_format.back().length();
    if (remaining > 0) {
        next_indent = std::string(remaining, ' ');
    } else {
        *os << std::endl;
        next_indent = indent_help;
    }

    for (const auto token : description) {
        *os << next_indent << token << std::endl;
        next_indent = indent_help;
    }
}

void HelpFormatter::_formatUsage(std::ostream *os,
                                 const std::vector<Argument> &required,
                                 const std::vector<Argument> &optional) const {
    std::string usage_prog = "usage: " + _params.prog;
    std::string next_indent;

    *os << usage_prog;

    std::string usage_indent;
    int indent_len = usage_prog.length() + 1;
    if (indent_len > _params.indent_max_usage) {
        indent_len = _params.indent_max_usage;
        *os << std::endl;

        usage_indent = std::string(indent_len, ' ');
        next_indent = usage_indent;
    } else {
        usage_indent = std::string(indent_len, ' ');
        next_indent = " ";
    }

    std::vector<std::string> full_arguments;
    for (const auto arg : optional) {
        full_arguments.emplace_back(_usage_opt_option(arg));
    }
    for (const auto arg : required) {
        full_arguments.emplace_back(_usage_req_option(arg));
    }

    full_arguments =
        formatWrappedTokens(full_arguments.cbegin(), full_arguments.cend(),
                            _params.max_length - indent_len);

    for (const auto token : full_arguments) {
        *os << next_indent << token << std::endl;
        next_indent = usage_indent;
    }
}

void printWrappedNoIndent(std::ostream *os, const std::string &str, int len) {
    std::vector tokens = _tokenize(str);
    tokens = formatWrappedTokens(tokens.cbegin(), tokens.cend(), len);
    for (const auto token : tokens) {
        *os << token << std::endl;
    }
}

void HelpFormatter::_formatDescription(std::ostream *os) const {
    if (_params.description.length() > 0) {
        *os << std::endl;
        printWrappedNoIndent(os, _params.description, _params.max_length);
    }
}

void HelpFormatter::_formatEpilogue(std::ostream *os) const {
    if (_params.epilogue.length() > 0) {
        *os << std::endl;
        printWrappedNoIndent(os, _params.epilogue, _params.max_length);
    }
}

void HelpFormatter::formatHelp(std::ostream *os,
                               const std::vector<Argument> &required,
                               const std::vector<Argument> &optional) const {
    _formatUsage(os, required, optional);
    // *os << std::endl;

    _formatDescription(os);

    if (required.size()) {
        *os << std::endl;
        *os << "Required positional arguments:" << std::endl;
        for (const auto arg : required) {
            _formatArgumentHelp(os, arg);
        }
    }

    if (optional.size()) {
        *os << std::endl;
        *os << "Optional arguments:" << std::endl;
        for (const auto arg : optional) {
            _formatArgumentHelp(os, arg);
        }
    }

    _formatEpilogue(os);
}

TerminalWrapHelpFormatter::TerminalWrapHelpFormatter(const Params &p)
    : HelpFormatter(p) {
    ::winsize window_size;

    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &window_size) == 0) {
        _params.max_length = window_size.ws_col;
    } else {
        _params.max_length =
            std::numeric_limits<typeof(_params.max_length)>::max();
    }
}

}  // namespace cmdarg
