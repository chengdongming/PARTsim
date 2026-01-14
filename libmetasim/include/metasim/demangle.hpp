// #pragma once
#ifndef METASIM_DEMANGLE_HPP
#define METASIM_DEMANGLE_HPP

#include <cassert>
#include <cxxabi.h>
#include <string>

// NOTE: MSVC does not return mangled names when using typeid((something).name()
// so this implementation may not work!

static inline std::string demangle_compiler_name(const char *mangled_name) {
    int status;
    std::string demangled_name =
        abi::__cxa_demangle(mangled_name, nullptr, nullptr, &status);

    switch (status) {
    case 0:
        // The demangling operation succeeded.
        return demangled_name;
    case -2:
        // The mangled_name is not a valid name under the C++ ABI mangling
        // rules.
        return std::string{mangled_name};
    case -1:
        // A memory allocation failiure occurred.
    case -3:
        // One of the arguments is invalid.
    default:
        assert(false);
        return "XXXX::XXXXXXXX";
    }
}

#endif // METASIM_DEMANGLE_HPP
