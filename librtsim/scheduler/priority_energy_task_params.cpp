#include <rtsim/scheduler/priority_energy_task_params.hpp>

#include <cerrno>
#include <cmath>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>

namespace RTSim {
namespace {

class ErrnoGuard {
public:
    ErrnoGuard() noexcept : saved_errno_(errno) {}
    ~ErrnoGuard() noexcept { errno = saved_errno_; }

    ErrnoGuard(const ErrnoGuard &) = delete;
    ErrnoGuard &operator=(const ErrnoGuard &) = delete;

private:
    int saved_errno_;
};

const char *const kFactorKey = "task_energy_factor";

bool isAsciiWhitespace(char character) {
    switch (character) {
    case ' ':
    case '\t':
    case '\n':
    case '\r':
    case '\f':
    case '\v':
        return true;
    default:
        return false;
    }
}

std::string trimAsciiWhitespace(const std::string &text) {
    std::size_t first = 0;
    while (first < text.size() && isAsciiWhitespace(text[first])) {
        ++first;
    }

    std::size_t last = text.size();
    while (last > first && isAsciiWhitespace(text[last - 1])) {
        --last;
    }

    return text.substr(first, last - first);
}

double parseFactorValue(const std::string &value_text) {
    if (value_text.empty()) {
        throw std::invalid_argument(
            "task_energy_factor: value is empty");
    }

    std::istringstream input(value_text);
    input.imbue(std::locale::classic());
    input >> std::noskipws;

    double value = 0.0;
    input >> value;
    if (input.fail()) {
        throw std::invalid_argument(
            "task_energy_factor: numeric text is invalid or outside double range");
    }

    char trailing = '\0';
    if (input >> trailing) {
        throw std::invalid_argument(
            "task_energy_factor: trailing characters after numeric value");
    }

    if (!std::isfinite(value)) {
        throw std::invalid_argument(
            "task_energy_factor: value is not finite");
    }
    if (!(value > 0.0)) {
        throw std::invalid_argument(
            "task_energy_factor: value must be greater than zero");
    }

    return value;
}

}  // namespace

double parsePriorityEnergyTaskFactor(const std::string &params) {
    const ErrnoGuard errno_guard;
    bool found = false;
    double factor = 1.0;

    std::size_t token_begin = 0;
    while (token_begin <= params.size()) {
        const std::size_t comma = params.find(',', token_begin);
        const std::size_t token_end =
            comma == std::string::npos ? params.size() : comma;
        const std::string token = trimAsciiWhitespace(
            params.substr(token_begin, token_end - token_begin));

        const std::size_t equals = token.find('=');
        if (equals == std::string::npos) {
            if (trimAsciiWhitespace(token) == kFactorKey) {
                throw std::invalid_argument(
                    "task_energy_factor: missing '=' and value");
            }
        } else {
            const std::string key =
                trimAsciiWhitespace(token.substr(0, equals));
            if (key == kFactorKey) {
                if (found) {
                    throw std::invalid_argument(
                        "task_energy_factor: duplicate key");
                }
                found = true;
                factor = parseFactorValue(trimAsciiWhitespace(
                    token.substr(equals + 1)));
            }
        }

        if (comma == std::string::npos) {
            break;
        }
        token_begin = comma + 1;
    }

    return factor;
}

}  // namespace RTSim
