// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// #pragma once
#ifndef CMDARG_PARSER_H_
#define CMDARG_PARSER_H_

// System headers
#include <map>
#include <memory>
#include <string>

// Project headers
#include <cmdarg/argument.hpp>
#include <cmdarg/help_formatter.hpp>

namespace cmdarg {

using Options = std::map<std::string, std::string>;

class ParserInterface {
 public:
    virtual ~ParserInterface() noexcept = default;

    virtual int addArgument(const Argument &arg_options) = 0;
    virtual Options parse(int argc, char *const argv[]) = 0;
    virtual void clear() = 0;
    virtual std::string getHelp() const = 0;
};

class Parser {
    std::unique_ptr<ParserInterface> _impl;

 public:
    explicit Parser(const HelpFormatter::Params &p);
    explicit Parser(const HelpFormatter &fmt);

    ~Parser() noexcept = default;

    // Copy construction and assignment
    Parser(const Parser &rhs) = delete;
    Parser &operator=(const Parser &rhs) = delete;

    // Move construction and assignment
    Parser(Parser &&rhs) = default;
    Parser &operator=(Parser &&rhs) = default;

    inline int addArgument(const Argument &arg_options) {
        return _impl->addArgument(arg_options);
    }

    inline Options parse(int argc, char *const argv[]) {
        return _impl->parse(argc, argv);
    }

    inline void clear() {
        _impl->clear();
    }

    inline std::string getHelp() const {
        return _impl->getHelp();
    }
};

}  // namespace cmdarg

#endif  // CMDARG_PARSER_H_
