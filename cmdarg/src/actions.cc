// Copyright 2022 Gabriele Ara
// Licensed by GNU GENERAL PUBLIC LICENSE v3.0

// System headers
#include <iostream>
#include <sstream>

// Project headers
#include <cmdarg/actions.hpp>
#include <cmdarg/argument.hpp>

namespace cmdarg::actions {

template <class T>
using check_type = bool (*)(const T &v);

template <class T>
using converter_type = T (*)(const std::string &);

template <class T>
bool check_nocheck(const T &_v_unused) {
    return true;
}

template <class T>
bool check_positive(const T &v) {
    return v > 0;
}

template <class T>
bool check_negative(const T &v) {
    return v < 0;
}

template <class T>
bool check_nonnegative(const T &v) {
    return v >= 0;
}

template <class T>
bool check_nonpositive(const T &v) {
    return v <= 0;
}

template <class T>
bool check_nonzero(const T &v) {
    return v != 0;
}

template <class T>
int increment_number(std::string *dest, const Argument &opt,
                     const std::string &_src_unused) {
    std::istringstream iss{*dest};
    std::ostringstream oss;
    T val;

    iss >> val;
    if (!iss) val = 0;

    ++val;
    oss << val;
    *dest = oss.str();
    return 0;
}

template <class T>
int store_number(std::string *dest, const Argument &opt, const std::string &src,
                 check_type<T> check, converter_type<T> converter) {
    try {
        T val = converter(src);
        if (!check(val)) {
            goto error;
        }

        *dest = src;
        return 0;
    } catch (std::invalid_argument const &ex) {
    } catch (std::out_of_range const &ex) {
    }

error:
    std::cerr << "Error: argument ";
    if (opt.short_opt) {
        std::cerr << opt.short_opt << "/";
    }

    std::cerr << opt.long_opt << ": ";
    std::cerr << "invalid value: '" << src << "'";
    std::cerr << std::endl;
    return 1;
}

int stoi(const std::string &src) {
    return std::stoi(src);
}

long stol(const std::string &src) {
    return std::stol(src);
}

long long stoll(const std::string &src) {
    return std::stoll(src);
}

float stof(const std::string &src) {
    return std::stof(src);
}

double stod(const std::string &src) {
    return std::stod(src);
}

template <class T, converter_type<T> converter>
int store_number_nocheck(std::string *dest, const Argument &opt,
                         const std::string &src) {
    return store_number<T>(dest, opt, src, check_nocheck<T>, converter);
}

template <class T, T converter(const std::string &)>
int store_number_positive(std::string *dest, const Argument &opt,
                          const std::string &src) {
    return store_number<T>(dest, opt, src, check_positive<T>, converter);
}

template <class T, T converter(const std::string &)>
int store_number_negative(std::string *dest, const Argument &opt,
                          const std::string &src) {
    return store_number<T>(dest, opt, src, check_negative<T>, converter);
}

template <class T, T converter(const std::string &)>
int store_number_nonnegative(std::string *dest, const Argument &opt,
                             const std::string &src) {
    return store_number<T>(dest, opt, src, check_nonnegative<T>, converter);
}

template <class T, T converter(const std::string &)>
int store_number_nonpositive(std::string *dest, const Argument &opt,
                             const std::string &src) {
    return store_number<T>(dest, opt, src, check_nonpositive<T>, converter);
}

template <class T, T converter(const std::string &)>
int store_number_nonzero(std::string *dest, const Argument &opt,
                         const std::string &src) {
    return store_number<T>(dest, opt, src, check_nonzero<T>, converter);
}

int store_string(std::string *dest, const Argument &opt,
                 const std::string &src) {
    // TODO(gabara): check length non zero
    *dest = src;
    return 0;
}

int store_bool(std::string *dest, bool value) {
    *dest = value ? "true" : "false";
    return 0;
}

int store_true(std::string *dest, const Argument &opt,
               const std::string &_src_unused) {
    return store_bool(dest, true);
}

int store_false(std::string *dest, const Argument &opt,
                const std::string &_src_unused) {
    return store_bool(dest, false);
}

int show_help_and_exit(std::string *dest, const Argument &opt,
                       const std::string &_src_unused) {
    store_true(dest, opt, _src_unused);
    // Very important! Help must always generate an error!
    // It will be handled correctly by the parser
    // implementation.
    return -1;
}

int store_number_nocheck_int(std::string *dest, const Argument &opt,
                             const std::string &src) {
    return store_number_nocheck<int, stoi>(dest, opt, src);
}

const Type store_int = store_number_nocheck<int, stoi>;
const Type store_long = store_number_nocheck<long, stol>;
const Type store_long_long = store_number_nocheck<long long, stoll>;
const Type store_float = store_number_nocheck<float, stof>;
const Type store_double = store_number_nocheck<double, stod>;

const Type store_positive_int = store_number_positive<int, stoi>;
const Type store_positive_long = store_number_positive<long, stol>;
const Type store_positive_long_long = store_number_positive<long long, stoll>;
const Type store_positive_float = store_number_positive<float, stof>;
const Type store_positive_double = store_number_positive<double, stod>;

const Type store_negative_int = store_number_negative<int, stoi>;
const Type store_negative_long = store_number_negative<long, stol>;
const Type store_negative_long_long = store_number_negative<long long, stoll>;
const Type store_negative_float = store_number_negative<float, stof>;
const Type store_negative_double = store_number_negative<double, stod>;

const Type store_nonpositive_int = store_number_nonpositive<int, stoi>;
const Type store_nonpositive_long = store_number_nonpositive<long, stol>;
const Type store_nonpositive_long_long =
    store_number_nonpositive<long long, stoll>;
const Type store_nonpositive_float = store_number_nonpositive<float, stof>;
const Type store_nonpositive_double = store_number_nonpositive<double, stod>;

const Type store_nonnegative_int = store_number_nonnegative<int, stoi>;
const Type store_nonnegative_long = store_number_nonnegative<long, stol>;
const Type store_nonnegative_long_long =
    store_number_nonnegative<long long, stoll>;
const Type store_nonnegative_float = store_number_nonnegative<float, stof>;
const Type store_nonnegative_double = store_number_nonnegative<double, stod>;

const Type store_nonzero_int = store_number_nonzero<int, stoi>;
const Type store_nonzero_long = store_number_nonzero<long, stol>;
const Type store_nonzero_long_long = store_number_nonzero<long long, stoll>;
const Type store_nonzero_float = store_number_nonzero<float, stof>;
const Type store_nonzero_double = store_number_nonzero<double, stod>;

/*
 * ┌───────────────────────────────────────────────┐
 * │               Increment Actions               │
 * └───────────────────────────────────────────────┘
 */
const Type increment_int = increment_number<int>;
const Type increment_long = increment_number<long>;
const Type increment_long_long = increment_number<long long>;
const Type increment_float = increment_number<float>;
const Type increment_double = increment_number<double>;

}  // namespace cmdarg::actions
