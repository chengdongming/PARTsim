#ifndef RTSIM_HARVESTING_SHA256_HPP
#define RTSIM_HARVESTING_SHA256_HPP

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace RTSim {

    class Sha256 final {
    public:
        Sha256() noexcept;

        void update(const void *data, std::size_t size);
        void update(std::string_view data);
        std::string finalHex();

    private:
        void transform(const std::uint8_t *block) noexcept;

        std::array<std::uint32_t, 8> _state;
        std::array<std::uint8_t, 64> _buffer{};
        std::size_t _buffer_size = 0;
        std::uint64_t _total_bytes = 0;
        bool _finalized = false;
    };

    std::string sha256Hex(std::string_view data);

} // namespace RTSim

#endif // RTSIM_HARVESTING_SHA256_HPP
