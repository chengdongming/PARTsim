// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// System headers
#include <getopt.h>

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <sstream>
#include <vector>

// Project headers
#include <cmdarg/parser.hpp>

namespace cmdarg {

const Argument _HELP = {
    .long_opt = "help",
    .short_opt = 'h',
    .required = false,
    .parameter_required = Argument::ParameterRequired::NO,
    .help = "Show this help message and exit",
    .action = actions::show_help_and_exit,
};

const ::option _OPTIONS_TERMINATOR = {0};

bool _is_terminator(const ::option &opt) {
    return std::memcmp(&opt, &_OPTIONS_TERMINATOR, sizeof(::option)) == 0;
}

class ParserImpl : public ParserInterface {
    std::map<std::string, std::string> _values;
    std::vector<Argument> _required;
    std::vector<Argument> _optional;
    std::string _getopt_string;
    std::vector<::option> _getopt_options;

    HelpFormatter _formatter;

    void Init();

 public:
    explicit ParserImpl(const HelpFormatter::Params &p);
    explicit ParserImpl(const HelpFormatter &fmt);

    ~ParserImpl() noexcept override = default;

    // Copy construction and assignment
    ParserImpl(const ParserImpl &rhs) = default;
    ParserImpl &operator=(const ParserImpl &rhs) = default;

    // Move construction and assignment
    ParserImpl(ParserImpl &&rhs) = default;
    ParserImpl &operator=(ParserImpl &&rhs) = default;

    int addArgument(const Argument &opt) override;
    Options parse(int argc, char *const argv[]) override;
    void clear() override;
    std::string getHelp() const override;

 private:
    inline ::option _construct_option(const Argument &arg_options);
    inline std::string _construct_string(const Argument &arg_options);

    int _parse_and_update_value(const Argument &arg_options,
                                const std::string &src);
};

void ParserImpl::Init() {
    addArgument(_HELP);
}

ParserImpl::ParserImpl(const HelpFormatter::Params &p)
    : ParserImpl(HelpFormatter{p}) {
}

ParserImpl::ParserImpl(const HelpFormatter &fmt) : _formatter(fmt) {
    Init();
}

// TODO(gabara): error codes
int ParserImpl::addArgument(const Argument &arg_options) {
    if (arg_options.long_opt == nullptr ||
        std::strlen(arg_options.long_opt) < 2) {
        // Invalid argument
        return -1;
    }

    std::string optname = arg_options.long_opt;
    if (_values.find(optname) != _values.end()) {
        // String already taken
        return -2;
    }
    _values[arg_options.long_opt] = "";

    if (arg_options.required) {
        _required.emplace_back(arg_options);
    } else {
        _optional.emplace_back(arg_options);

        ::option opt = _construct_option(arg_options);
        if (_getopt_options.size() && _is_terminator(_getopt_options.back())) {
            // Overwrite terminator, will be re-inserted upon parsing
            _getopt_options.back() = opt;
        } else {
            _getopt_options.emplace_back(opt);
        }

        _getopt_string += _construct_string(arg_options);
    }

    return 0;
}

// int parse_optional(int *last, int argc, char *const argv[],
//                    ::option *getopt_options, const char *getopt_string) {
// }

Options ParserImpl::parse(int argc, char *const argv[]) {
    if (_getopt_options.empty()) return _values;

    // Force the last element to be all zeroes
    if (!_is_terminator(_getopt_options.back()))
        _getopt_options.emplace_back(_OPTIONS_TERMINATOR);

    // Clear all default values
    clear();

    // TODO(gabara): help is parsed alongside all the other variables.
    // This can lead to some issues when previous values show an error.

    optind = 1;

    // Parse optional arguments first
    while (true) {
        int option_index = -1;

        int c = getopt_long(argc, argv, _getopt_string.c_str(),
                            _getopt_options.data(), &option_index);
        if (c == -1) {
            break;
        }

        if (c == '?') {
            std::cerr << std::endl;
            _formatter.formatHelp(&std::cerr, _required, _optional);
            std::exit(EXIT_FAILURE);
        }

        if (option_index < 0) {
            // Happens when a short option is supplied,
            // track the corresponding long option index
            for (option_index = 0; option_index < _getopt_options.size();
                 ++option_index) {
                if (_getopt_options[option_index].val == c) break;
            }
            // assert(option_index != _getopt_options.size());
        }

        const char *arg_options = optarg;
        if (!arg_options) arg_options = "";

        int res = _parse_and_update_value(_optional[option_index], arg_options);
        if (res) {
            std::ostream *outs = &std::cerr;
            int exit_code = EXIT_FAILURE;
            if (_values["help"] == "true") {
                outs = &std::cout;
                exit_code = EXIT_SUCCESS;
            } else {
                *outs << std::endl;
            }

            _formatter.formatHelp(outs, _required, _optional);
            std::exit(exit_code);
        }
    }

    // Parse required options after optional ones
    int req_option = 0;
    while (optind < argc && req_option < _required.size()) {
        _parse_and_update_value(_required[req_option++], argv[optind++]);
    }

    if (optind < argc) {
        std::cerr << argv[0] << ": too many required options:";
        for (; optind < argc; ++optind) {
            std::cerr << " " << argv[optind];
        }
        std::cerr << std::endl;

        _formatter.formatHelp(&std::cerr, _required, _optional);
        std::exit(EXIT_FAILURE);
    }

    if (req_option < _required.size()) {
        std::cerr << argv[0] << ": missing required options:";

        for (; req_option < _required.size(); ++req_option) {
            std::cerr << " " << _formatter.metavar(_required[req_option]);
        }
        std::cerr << std::endl;

        _formatter.formatHelp(&std::cerr, _required, _optional);
        std::exit(EXIT_FAILURE);
    }

    return _values;
}

void ParserImpl::clear() {
    for (const auto arg : _required) {
        _values[arg.long_opt] = arg.default_value;
    }
    for (const auto arg : _optional) {
        _values[arg.long_opt] = arg.default_value;
    }
}

std::string ParserImpl::getHelp() const {
    std::ostringstream oss;
    _formatter.formatHelp(&oss, _required, _optional);
    return oss.str();
}

::option ParserImpl::_construct_option(const Argument &arg_options) {
    // NOTE: arg_options.parameter_required has the same representation as
    // has_arg on purpose
    ::option the_option = {
        .name = arg_options.long_opt,
        .has_arg = static_cast<int>(arg_options.parameter_required),
        .flag = 0,
        .val = arg_options.short_opt,
    };
    return the_option;
}

std::string ParserImpl::_construct_string(const Argument &arg_options) {
    std::string out;

    if (arg_options.short_opt) {
        out += arg_options.short_opt;
    }

    // Using property of GNU getopt, optional arguments are marked with a value
    // of 2 and exactly 2 ':' characters must be supplied
    for (int i = 0; i < static_cast<int>(arg_options.parameter_required); ++i) {
        out += ':';
    }

    return out;
}

int ParserImpl::_parse_and_update_value(const Argument &arg_options,
                                        const std::string &src) {
    // Get stored value
    std::string dest = _values[arg_options.long_opt];

    // Request action call
    int res = arg_options.action(&dest, arg_options, src);
    if (res > 0) return res;

    // Update stored value
    _values[arg_options.long_opt] = dest;

    return res;
}

Parser::Parser(const HelpFormatter::Params &p) {
    _impl = std::make_unique<ParserImpl>(p);
}

Parser::Parser(const HelpFormatter &fmt) {
    _impl = std::make_unique<ParserImpl>(fmt);
}

}  // namespace cmdarg
