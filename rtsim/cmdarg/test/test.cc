// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// System headers
#include <iostream>
#include <map>

// Project headers
#include <cmdarg.hpp>

int main(int argc, char *argv[]) {
    cmdarg::Parser p{cmdarg::TerminalWrapHelpFormatter{{
        .prog = argv[0],
        .description = "A test example for cmdarg",
        .epilogue = "That's all folks!",
    }}};
    // cmdarg::Parser p{{.prog = argv[0]}};

    p.addArgument({
        .long_opt = "count",
        .short_opt = 'c',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::REQUIRED,
        .help = "A simple counter with a rather long helpful message indeed",
        .default_value = "5",
        .action = cmdarg::actions::store_int,
    });

    p.addArgument({
        .long_opt = "supercalifragilistichespiralidoso",
        .short_opt = 's',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::OPTIONAL,
        .help = "A very long thing to say in one sentence",
        .default_value = "",
    });

    p.addArgument({
        .long_opt = "carramba",
        .short_opt = '\0',
        .required = false,
        .parameter_required = cmdarg::Argument::ParameterRequired::NO,
        .help = "What a surprise!",
        .default_value = "",
    });

    p.addArgument({
        .long_opt = "input",
        .required = true,
        .help = "The input file",
    });

    p.addArgument({
        .long_opt = "output",
        .required = true,
        .help = "The output file",
    });

    cmdarg::Options options = p.parse(argc, argv);

    for (auto [key, val] : options) {
        std::cout << key << ": " << val << std::endl;
    }

    return 0;
}
