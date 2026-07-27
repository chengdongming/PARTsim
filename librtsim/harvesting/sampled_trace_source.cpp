#include <rtsim/harvesting/sampled_trace_source.hpp>

#include <rtsim/harvesting/sha256.hpp>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace RTSim {
    namespace {
        constexpr std::uint64_t max_exact_binary64_integer =
            UINT64_C(1) << 53;

        struct CsvLine {
            std::string_view text;
            std::size_t number = 0;
        };

        [[noreturn]] void traceError(const std::filesystem::path &path,
                                     std::size_t line,
                                     const std::string &reason) {
            throw std::runtime_error(
                path.string() + ": line " + std::to_string(line) +
                ": " + reason);
        }

        double positiveZero(double value) noexcept {
            return value == 0.0 ? 0.0 : value;
        }

        double roundedMultiply(double lhs, double rhs) noexcept {
            volatile double result = lhs * rhs;
            return result;
        }

        double roundedAdd(double lhs, double rhs) noexcept {
            volatile double result = lhs + rhs;
            return result;
        }

        double roundedSubtract(double lhs, double rhs) noexcept {
            volatile double result = lhs - rhs;
            return result;
        }

        bool asciiWhitespace(char value) noexcept {
            return value == ' ' || value == '\t' || value == '\n' ||
                   value == '\r' || value == '\v' || value == '\f';
        }

        std::string_view trimAscii(std::string_view value) noexcept {
            while (!value.empty() && asciiWhitespace(value.front())) {
                value.remove_prefix(1);
            }
            while (!value.empty() && asciiWhitespace(value.back())) {
                value.remove_suffix(1);
            }
            return value;
        }

        std::size_t lineNumberAt(std::string_view bytes,
                                 std::size_t position) {
            return 1 + static_cast<std::size_t>(std::count(
                           bytes.begin(), bytes.begin() + position, '\n'));
        }

        std::vector<CsvLine> splitLines(const std::string &bytes,
                                        const std::filesystem::path &path) {
            if (bytes.empty()) {
                traceError(path, 1, "trace file is empty");
            }
            const std::string bom("\xef\xbb\xbf", 3);
            const bool has_bom = bytes.size() >= bom.size() &&
                                 bytes.compare(0, bom.size(), bom) == 0;
            const std::size_t unexpected_bom =
                bytes.find(bom, has_bom ? bom.size() : 0);
            if (unexpected_bom != std::string::npos) {
                traceError(path,
                           lineNumberAt(bytes, unexpected_bom),
                           "UTF-8 BOM is only allowed at the start of file");
            }
            const std::size_t nul = bytes.find('\0');
            if (nul != std::string::npos) {
                traceError(path,
                           lineNumberAt(bytes, nul),
                           "NUL byte is not valid CSV text");
            }

            std::vector<CsvLine> lines;
            std::size_t position = has_bom ? bom.size() : 0;
            std::size_t line_number = 1;
            while (position < bytes.size()) {
                const std::size_t newline = bytes.find('\n', position);
                const bool has_newline = newline != std::string::npos;
                const std::size_t end = has_newline ? newline : bytes.size();
                std::string_view line(bytes.data() + position, end - position);
                if (has_newline && !line.empty() && line.back() == '\r') {
                    line.remove_suffix(1);
                }
                if (line.find('\r') != std::string_view::npos) {
                    traceError(path,
                               line_number,
                               "carriage return must be part of CRLF");
                }
                lines.push_back({line, line_number});
                if (!has_newline) {
                    break;
                }
                position = newline + 1;
                ++line_number;
            }
            if (lines.empty()) {
                traceError(path, 1, "CSV header is missing");
            }
            return lines;
        }

        std::vector<std::string_view> splitFields(
            const CsvLine &line,
            const std::filesystem::path &path) {
            if (line.text.find('"') != std::string_view::npos) {
                traceError(path,
                           line.number,
                           "quoted CSV fields are not supported");
            }
            std::vector<std::string_view> fields;
            std::size_t position = 0;
            while (true) {
                const std::size_t comma = line.text.find(',', position);
                const std::size_t end =
                    comma == std::string_view::npos ? line.text.size() : comma;
                fields.push_back(trimAscii(
                    line.text.substr(position, end - position)));
                if (comma == std::string_view::npos) {
                    break;
                }
                position = comma + 1;
            }
            return fields;
        }

        std::uint64_t parseTimestamp(std::string_view field,
                                     const std::filesystem::path &path,
                                     std::size_t line) {
            if (field.empty()) {
                traceError(path, line, "timestamp is empty");
            }
            std::uint64_t value = 0;
            for (const char character : field) {
                if (character < '0' || character > '9') {
                    traceError(
                        path,
                        line,
                        "timestamp must be a complete non-negative decimal "
                        "uint64");
                }
                const std::uint64_t digit =
                    static_cast<std::uint64_t>(character - '0');
                if (value >
                    (std::numeric_limits<std::uint64_t>::max() - digit) / 10) {
                    traceError(path, line, "timestamp exceeds uint64 range");
                }
                value = value * 10 + digit;
            }
            return value;
        }

        double parseValue(std::string_view field,
                          const std::filesystem::path &path,
                          std::size_t line) {
            if (field.empty()) {
                traceError(path, line, "trace value is empty");
            }
            std::istringstream input{std::string(field)};
            input.imbue(std::locale::classic());
            input >> std::noskipws;
            double value = 0.0;
            if (!(input >> value)) {
                traceError(path, line, "trace value is not a complete double");
            }
            char trailing = 0;
            if (input >> trailing) {
                traceError(path,
                           line,
                           "trace value contains trailing characters");
            }
            if (!input.eof()) {
                traceError(path, line, "trace value is not a complete double");
            }
            if (!std::isfinite(value)) {
                traceError(path, line, "trace value must be finite");
            }
            if (value < 0.0) {
                traceError(path, line, "trace value must be non-negative");
            }
            return positiveZero(value);
        }

        SampledTraceConfig validateConfig(SampledTraceConfig config) {
            const std::filesystem::path display_path =
                config.file.empty() ? std::filesystem::path("<empty>")
                                    : std::filesystem::path(config.file);
            if (config.file.empty() || config.time_column.empty() ||
                config.value_column.empty()) {
                traceError(display_path,
                           0,
                           "trace file and selected columns must be non-empty");
            }
            if (config.time_column == config.value_column) {
                traceError(display_path,
                           0,
                           "time and value columns must be distinct");
            }
            if (config.interpolation != TraceInterpolation::ZeroOrderHold) {
                traceError(display_path,
                           0,
                           "only zero-order-hold interpolation is supported");
            }
            if (config.after_trace != TraceAfterEnd::Zero) {
                traceError(display_path,
                           0,
                           "only zero after-trace behavior is supported");
            }
            if (config.max_file_size_bytes == 0 || config.max_rows == 0) {
                traceError(display_path,
                           0,
                           "trace resource limits must be greater than zero");
            }

            switch (config.value_type) {
            case TraceValueType::ElectricalPower:
                if (!std::isfinite(config.panel_area_m2) ||
                    !std::isfinite(config.conversion_efficiency) ||
                    config.panel_area_m2 != 0.0 ||
                    config.conversion_efficiency != 0.0) {
                    traceError(
                        display_path,
                        0,
                        "electrical-power traces must not provide irradiance "
                        "conversion parameters");
                }
                config.panel_area_m2 = 0.0;
                config.conversion_efficiency = 0.0;
                break;
            case TraceValueType::Irradiance:
                if (!std::isfinite(config.panel_area_m2) ||
                    config.panel_area_m2 <= 0.0 ||
                    !std::isfinite(config.conversion_efficiency) ||
                    config.conversion_efficiency <= 0.0 ||
                    config.conversion_efficiency > 1.0) {
                    traceError(display_path,
                               0,
                               "irradiance conversion parameters are invalid");
                }
                break;
            default:
                traceError(display_path, 0, "trace value type is unknown");
            }
            return config;
        }

        std::filesystem::path resolvePath(const SampledTraceConfig &config) {
            std::error_code error;
            std::filesystem::path resolved = std::filesystem::absolute(
                std::filesystem::path(config.file), error);
            if (error) {
                traceError(config.file,
                           0,
                           "cannot resolve trace path: " + error.message());
            }
            return resolved.lexically_normal();
        }

        std::string readRawFile(const std::filesystem::path &path,
                                std::uint64_t maximum_size) {
            std::error_code error;
            if (!std::filesystem::is_regular_file(path, error) || error) {
                traceError(path, 0, "trace file does not exist or is not regular");
            }
            const std::uintmax_t size = std::filesystem::file_size(path, error);
            if (error) {
                traceError(path,
                           0,
                           "cannot determine trace file size: " +
                               error.message());
            }
            if (size > maximum_size) {
                traceError(path, 0, "trace file exceeds max_file_size_bytes");
            }
            if (size > std::numeric_limits<std::size_t>::max() ||
                size > static_cast<std::uintmax_t>(
                           std::numeric_limits<std::streamsize>::max())) {
                traceError(path, 0, "trace file is too large for this process");
            }

            std::ifstream input(path, std::ios::binary);
            if (!input.is_open()) {
                traceError(path, 0, "trace file is not readable");
            }
            std::string bytes(static_cast<std::size_t>(size), '\0');
            if (!bytes.empty()) {
                input.read(bytes.data(), static_cast<std::streamsize>(size));
                if (input.gcount() != static_cast<std::streamsize>(size)) {
                    traceError(path, 0, "trace file changed while being read");
                }
            }
            char extra = 0;
            if (input.get(extra)) {
                traceError(path, 0, "trace file changed while being read");
            }
            return bytes;
        }

        double normalizePower(const SampledTraceConfig &config,
                              double value,
                              const std::filesystem::path &path,
                              std::size_t line) {
            if (config.value_type == TraceValueType::ElectricalPower) {
                return positiveZero(value);
            }
            double power = roundedMultiply(value, config.panel_area_m2);
            power = roundedMultiply(power, config.conversion_efficiency);
            if (!std::isfinite(power) || power < 0.0) {
                traceError(path,
                           line,
                           "irradiance conversion produced invalid power");
            }
            return positiveZero(power);
        }

        std::uint64_t doubleBits(double value) noexcept {
            static_assert(sizeof(double) == sizeof(std::uint64_t),
                          "normalized trace identity requires binary64 double");
            std::uint64_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            return bits;
        }

        void appendBigEndian64(Sha256 &digest, std::uint64_t value) {
            std::array<std::uint8_t, 8> bytes{};
            for (std::size_t index = 0; index < bytes.size(); ++index) {
                bytes[index] = static_cast<std::uint8_t>(
                    value >> (56 - 8 * index));
            }
            digest.update(bytes.data(), bytes.size());
        }

        std::string normalizedTraceHash(
            const SampledTraceConfig &config,
            const std::vector<std::uint64_t> &timestamps,
            const std::vector<double> &electrical_powers) {
            static constexpr char identity[] = "PARTSIM_SAMPLED_TRACE_V1";
            Sha256 digest;
            digest.update(identity, sizeof(identity));
            const std::uint8_t value_type =
                config.value_type == TraceValueType::ElectricalPower
                    ? UINT8_C(0)
                    : UINT8_C(1);
            digest.update(&value_type, sizeof(value_type));
            appendBigEndian64(digest, doubleBits(config.panel_area_m2));
            appendBigEndian64(
                digest, doubleBits(config.conversion_efficiency));
            appendBigEndian64(
                digest, static_cast<std::uint64_t>(timestamps.size()));
            for (std::size_t index = 0; index < timestamps.size(); ++index) {
                appendBigEndian64(digest, timestamps[index]);
                appendBigEndian64(
                    digest, doubleBits(electrical_powers[index]));
            }
            return digest.finalHex();
        }
    } // namespace

    SampledTraceSource::SampledTraceSource(SampledTraceConfig config) :
        SampledTraceSource(loadTrace(std::move(config))) {}

    SampledTraceSource::SampledTraceSource(LoadedTrace loaded) :
        _config(std::move(loaded.config)),
        _resolved_path(std::move(loaded.resolved_path)),
        _raw_file_sha256(std::move(loaded.raw_file_sha256)),
        _normalized_trace_sha256(
            std::move(loaded.normalized_trace_sha256)),
        _timestamps(std::move(loaded.timestamps)),
        _electrical_powers(std::move(loaded.electrical_powers)),
        _prefix_energy(std::move(loaded.prefix_energy)) {}

    SampledTraceSource::LoadedTrace SampledTraceSource::loadTrace(
        SampledTraceConfig config) {
        LoadedTrace loaded;
        loaded.config = validateConfig(std::move(config));
        loaded.resolved_path = resolvePath(loaded.config);
        const std::string raw = readRawFile(
            loaded.resolved_path, loaded.config.max_file_size_bytes);
        loaded.raw_file_sha256 = sha256Hex(raw);

        const std::vector<CsvLine> lines =
            splitLines(raw, loaded.resolved_path);
        const std::vector<std::string_view> header =
            splitFields(lines.front(), loaded.resolved_path);
        std::unordered_map<std::string, std::size_t> columns;
        for (std::size_t index = 0; index < header.size(); ++index) {
            if (header[index].empty()) {
                traceError(loaded.resolved_path,
                           lines.front().number,
                           "CSV header names must be non-empty");
            }
            const std::string name(header[index]);
            if (!columns.emplace(name, index).second) {
                traceError(loaded.resolved_path,
                           lines.front().number,
                           "CSV header names must be unique");
            }
        }
        const auto time_column = columns.find(loaded.config.time_column);
        const auto value_column = columns.find(loaded.config.value_column);
        if (time_column == columns.end() || value_column == columns.end()) {
            traceError(loaded.resolved_path,
                       lines.front().number,
                       "selected timestamp or value column is missing");
        }

        std::vector<std::size_t> file_lines;
        double last_input_value = 0.0;
        for (std::size_t line_index = 1;
             line_index < lines.size();
             ++line_index) {
            const CsvLine &line = lines[line_index];
            if (trimAscii(line.text).empty()) {
                traceError(loaded.resolved_path,
                           line.number,
                           "blank CSV data rows are not allowed");
            }
            if (loaded.timestamps.size() >= loaded.config.max_rows) {
                traceError(loaded.resolved_path,
                           line.number,
                           "trace exceeds max_rows");
            }
            const std::vector<std::string_view> fields =
                splitFields(line, loaded.resolved_path);
            if (fields.size() != header.size()) {
                traceError(loaded.resolved_path,
                           line.number,
                           "CSV data field count does not match header");
            }
            const std::uint64_t timestamp = parseTimestamp(
                fields[time_column->second], loaded.resolved_path, line.number);
            if (!loaded.timestamps.empty() &&
                timestamp <= loaded.timestamps.back()) {
                traceError(loaded.resolved_path,
                           line.number,
                           "timestamps must be strictly increasing");
            }
            last_input_value = parseValue(
                fields[value_column->second], loaded.resolved_path, line.number);
            const double power = normalizePower(
                loaded.config,
                last_input_value,
                loaded.resolved_path,
                line.number);
            loaded.timestamps.push_back(timestamp);
            loaded.electrical_powers.push_back(power);
            file_lines.push_back(line.number);
        }

        if (loaded.timestamps.empty()) {
            traceError(loaded.resolved_path,
                       lines.front().number + 1,
                       "trace must contain a terminal sentinel row");
        }
        if (last_input_value != 0.0) {
            traceError(loaded.resolved_path,
                       file_lines.back(),
                       "last trace row must be a zero terminal sentinel");
        }

        loaded.prefix_energy.assign(loaded.timestamps.size(), 0.0);
        for (std::size_t index = 0;
             index + 1 < loaded.timestamps.size();
             ++index) {
            const std::uint64_t duration_ms =
                loaded.timestamps[index + 1] - loaded.timestamps[index];
            if (duration_ms > max_exact_binary64_integer) {
                traceError(
                    loaded.resolved_path,
                    file_lines[index + 1],
                    "sample duration cannot be represented exactly as "
                    "binary64 milliseconds");
            }
            double segment_energy = roundedMultiply(
                loaded.electrical_powers[index],
                static_cast<double>(duration_ms));
            segment_energy = roundedMultiply(segment_energy, 0.001);
            if (!std::isfinite(segment_energy) || segment_energy < 0.0) {
                traceError(loaded.resolved_path,
                           file_lines[index + 1],
                           "trace segment energy is invalid");
            }
            loaded.prefix_energy[index + 1] = roundedAdd(
                loaded.prefix_energy[index], segment_energy);
            if (!std::isfinite(loaded.prefix_energy[index + 1]) ||
                loaded.prefix_energy[index + 1] < 0.0) {
                traceError(loaded.resolved_path,
                           file_lines[index + 1],
                           "trace prefix energy is invalid");
            }
        }

        loaded.normalized_trace_sha256 = normalizedTraceHash(
            loaded.config,
            loaded.timestamps,
            loaded.electrical_powers);
        return loaded;
    }

    double SampledTraceSource::integralAt(std::uint64_t time_ms) const {
        if (time_ms <= _timestamps.front()) {
            return 0.0;
        }
        if (time_ms >= _timestamps.back()) {
            return _prefix_energy.back();
        }
        const auto upper = std::upper_bound(
            _timestamps.begin(), _timestamps.end(), time_ms);
        const std::size_t index = static_cast<std::size_t>(
            std::distance(_timestamps.begin(), upper) - 1);
        const std::uint64_t duration_ms = time_ms - _timestamps[index];
        double partial = roundedMultiply(
            _electrical_powers[index], static_cast<double>(duration_ms));
        partial = roundedMultiply(partial, 0.001);
        const double result = roundedAdd(_prefix_energy[index], partial);
        if (!std::isfinite(result) || result < 0.0) {
            throw std::overflow_error(
                "sampled trace integral is not finite and non-negative");
        }
        return positiveZero(result);
    }

    double SampledTraceSource::offeredEnergyForInterval(
        const HarvestInterval &interval) const {
        if (interval.start_time_ms >= interval.end_time_ms) {
            throw std::invalid_argument(
                "harvest interval start must precede end");
        }
        const std::uint64_t duration_ms =
            interval.end_time_ms - interval.start_time_ms;
        if (duration_ms > max_exact_binary64_integer) {
            throw std::invalid_argument(
                "harvest interval duration cannot be represented exactly as "
                "binary64 milliseconds");
        }
        const double start_integral = integralAt(interval.start_time_ms);
        const double end_integral = integralAt(interval.end_time_ms);
        const double offered = roundedSubtract(end_integral, start_integral);
        if (!std::isfinite(offered) || offered < 0.0) {
            throw std::overflow_error(
                "sampled trace offered energy is not finite and non-negative");
        }
        return positiveZero(offered);
    }

    const std::string &SampledTraceSource::rawFileSha256() const noexcept {
        return _raw_file_sha256;
    }

    const std::string &
        SampledTraceSource::normalizedTraceSha256() const noexcept {
        return _normalized_trace_sha256;
    }

    std::size_t SampledTraceSource::sampleCount() const noexcept {
        return _timestamps.size();
    }

    const std::filesystem::path &
        SampledTraceSource::resolvedPath() const noexcept {
        return _resolved_path;
    }

} // namespace RTSim
