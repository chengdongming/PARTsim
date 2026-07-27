#include <clocale>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>

#if defined(__GLIBC__)
#include <gnu/libc-version.h>
#endif

namespace {

constexpr const char *kParserContract =
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_STOD_PARSER_V1";

std::string json_string(const std::string &value) {
    std::ostringstream out;
    out << '"';
    for (const unsigned char byte : value) {
        switch (byte) {
        case '"':
            out << "\\\"";
            break;
        case '\\':
            out << "\\\\";
            break;
        case '\b':
            out << "\\b";
            break;
        case '\f':
            out << "\\f";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            if (byte < 0x20) {
                out << "\\u00" << std::hex << std::setw(2)
                    << std::setfill('0') << static_cast<unsigned int>(byte)
                    << std::dec;
            } else {
                out << static_cast<char>(byte);
            }
        }
    }
    out << '"';
    return out.str();
}

std::string binary64_bits(double value) {
    static_assert(
        sizeof(double) == sizeof(std::uint64_t),
        "RTA4 solar verifier requires a 64-bit double");
    static_assert(
        std::numeric_limits<double>::is_iec559,
        "RTA4 solar verifier requires IEEE-754 double semantics");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << bits;
    return out.str();
}

std::string standard_library_identity() {
#if defined(_LIBCPP_VERSION)
    return "libc++:" + std::to_string(_LIBCPP_VERSION);
#elif defined(__GLIBCXX__)
    return "libstdc++:" + std::to_string(__GLIBCXX__);
#elif defined(_MSVC_STL_VERSION)
    return "msvc-stl:" + std::to_string(_MSVC_STL_VERSION);
#else
    return "unknown";
#endif
}

std::string libc_identity() {
#if defined(__GLIBC__)
    return "glibc:" + std::string(gnu_get_libc_version());
#elif defined(__APPLE__)
    return "apple-libc";
#elif defined(_WIN32)
    return "windows-crt";
#else
    return "unknown";
#endif
}

void emit_error(const std::string &category, const std::string &detail) {
    std::cerr << "{\"record_type\":\"error\",\"category\":"
              << json_string(category) << ",\"detail\":"
              << json_string(detail) << "}\n";
}

bool parse_nonnegative_integer(
    const char *text, std::int64_t *destination) {
    try {
        std::size_t consumed = 0;
        const std::string value(text);
        const long long parsed = std::stoll(value, &consumed, 10);
        if (consumed != value.size() || parsed < 0) {
            return false;
        }
        *destination = static_cast<std::int64_t>(parsed);
        return true;
    } catch (const std::exception &) {
        return false;
    }
}

void emit_identity() {
    std::cout
        << "{\"record_type\":\"identity\",\"parser_contract_version\":"
        << json_string(kParserContract)
        << ",\"numeric_locale\":\"LC_NUMERIC=C\""
        << ",\"cpp_standard\":" << __cplusplus
        << ",\"standard_library\":"
        << json_string(standard_library_identity())
        << ",\"libc\":" << json_string(libc_identity())
        << ",\"double_is_iec559\":"
        << (std::numeric_limits<double>::is_iec559 ? "true" : "false")
        << ",\"double_size_bytes\":" << sizeof(double)
        << "}\n";
}

bool emit_row(
    const std::string &line,
    std::int64_t physical_data_row_index) {
    std::string parse_status = "success";
    std::string exception_category = "none";
    std::size_t consumed = 0;
    bool finite = false;
    bool negative = false;
    std::string bits;

    try {
        const double value = std::stod(line, &consumed);
        finite = std::isfinite(value);
        negative = value < 0.0;
        bits = binary64_bits(value);
    } catch (const std::invalid_argument &) {
        parse_status = "exception";
        exception_category = "invalid_argument";
    } catch (const std::out_of_range &) {
        parse_status = "exception";
        exception_category = "out_of_range";
    } catch (const std::exception &) {
        parse_status = "exception";
        exception_category = "std_exception";
    }

    std::cout
        << "{\"record_type\":\"row\",\"physical_data_row_index\":"
        << physical_data_row_index
        << ",\"file_physical_line_number\":"
        << (physical_data_row_index + 2)
        << ",\"parse_status\":" << json_string(parse_status)
        << ",\"exception_category\":" << json_string(exception_category)
        << ",\"finite\":" << (finite ? "true" : "false")
        << ",\"negative\":" << (negative ? "true" : "false")
        << ",\"binary64_bits\":"
        << (bits.empty() ? "null" : json_string(bits))
        << ",\"consumed_characters\":" << consumed
        << ",\"raw_text_length\":" << line.size()
        << "}\n";

    return parse_status == "success" && finite && !negative;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc != 4) {
        emit_error(
            "usage",
            "expected: rta4_solar_stod_verifier CSV FIRST_ROW LAST_ROW");
        return 2;
    }

    std::int64_t first_row = 0;
    std::int64_t last_row = 0;
    if (!parse_nonnegative_integer(argv[2], &first_row)
        || !parse_nonnegative_integer(argv[3], &last_row)
        || first_row > last_row) {
        emit_error("invalid_row_range", "row range must be non-negative");
        return 2;
    }

    if (std::setlocale(LC_NUMERIC, "C") == nullptr) {
        emit_error("locale", "cannot set LC_NUMERIC=C");
        return 3;
    }
    std::locale::global(std::locale::classic());
    emit_identity();

    std::ifstream input(argv[1]);
    if (!input.is_open()) {
        emit_error("file_open", "cannot open CSV");
        return 3;
    }

    std::string line;
    if (!std::getline(input, line)) {
        emit_error("missing_header", "CSV has no header line");
        return 3;
    }

    bool safe = true;
    std::int64_t data_index = 0;
    while (data_index <= last_row && std::getline(input, line)) {
        if (data_index >= first_row) {
            safe = emit_row(line, data_index) && safe;
        }
        ++data_index;
    }
    if (data_index <= last_row) {
        emit_error("insufficient_rows", "CSV does not contain the requested row");
        return 3;
    }
    return safe ? 0 : 4;
}
