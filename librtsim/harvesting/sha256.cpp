#include <rtsim/harvesting/sha256.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace RTSim {
    namespace {
        constexpr std::array<std::uint32_t, 64> round_constants = {{
            UINT32_C(0x428a2f98), UINT32_C(0x71374491),
            UINT32_C(0xb5c0fbcf), UINT32_C(0xe9b5dba5),
            UINT32_C(0x3956c25b), UINT32_C(0x59f111f1),
            UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5),
            UINT32_C(0xd807aa98), UINT32_C(0x12835b01),
            UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
            UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe),
            UINT32_C(0x9bdc06a7), UINT32_C(0xc19bf174),
            UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786),
            UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc),
            UINT32_C(0x2de92c6f), UINT32_C(0x4a7484aa),
            UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
            UINT32_C(0x983e5152), UINT32_C(0xa831c66d),
            UINT32_C(0xb00327c8), UINT32_C(0xbf597fc7),
            UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147),
            UINT32_C(0x06ca6351), UINT32_C(0x14292967),
            UINT32_C(0x27b70a85), UINT32_C(0x2e1b2138),
            UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
            UINT32_C(0x650a7354), UINT32_C(0x766a0abb),
            UINT32_C(0x81c2c92e), UINT32_C(0x92722c85),
            UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b),
            UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3),
            UINT32_C(0xd192e819), UINT32_C(0xd6990624),
            UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
            UINT32_C(0x19a4c116), UINT32_C(0x1e376c08),
            UINT32_C(0x2748774c), UINT32_C(0x34b0bcb5),
            UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a),
            UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3),
            UINT32_C(0x748f82ee), UINT32_C(0x78a5636f),
            UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
            UINT32_C(0x90befffa), UINT32_C(0xa4506ceb),
            UINT32_C(0xbef9a3f7), UINT32_C(0xc67178f2),
        }};

        std::uint32_t rotateRight(std::uint32_t value,
                                  unsigned shift) noexcept {
            return (value >> shift) | (value << (32 - shift));
        }
    } // namespace

    Sha256::Sha256() noexcept :
        _state{{UINT32_C(0x6a09e667),
                UINT32_C(0xbb67ae85),
                UINT32_C(0x3c6ef372),
                UINT32_C(0xa54ff53a),
                UINT32_C(0x510e527f),
                UINT32_C(0x9b05688c),
                UINT32_C(0x1f83d9ab),
                UINT32_C(0x5be0cd19)}} {}

    void Sha256::update(const void *data, std::size_t size) {
        if (_finalized) {
            throw std::logic_error("SHA-256 digest is already finalized");
        }
        if (size != 0 && data == nullptr) {
            throw std::invalid_argument("SHA-256 input pointer is null");
        }
        if (size == 0) {
            return;
        }
        constexpr std::uint64_t maximum_bytes =
            std::numeric_limits<std::uint64_t>::max() / UINT64_C(8);
        if (size > maximum_bytes - _total_bytes) {
            throw std::length_error("SHA-256 input is too long");
        }
        _total_bytes += static_cast<std::uint64_t>(size);

        const auto *input = static_cast<const std::uint8_t *>(data);
        if (_buffer_size != 0) {
            const std::size_t copied =
                std::min(size, _buffer.size() - _buffer_size);
            std::memcpy(_buffer.data() + _buffer_size, input, copied);
            _buffer_size += copied;
            input += copied;
            size -= copied;
            if (_buffer_size == _buffer.size()) {
                transform(_buffer.data());
                _buffer_size = 0;
            }
        }

        while (size >= _buffer.size()) {
            transform(input);
            input += _buffer.size();
            size -= _buffer.size();
        }
        if (size != 0) {
            std::memcpy(_buffer.data(), input, size);
            _buffer_size = size;
        }
    }

    void Sha256::update(std::string_view data) {
        update(data.data(), data.size());
    }

    std::string Sha256::finalHex() {
        if (_finalized) {
            throw std::logic_error("SHA-256 digest is already finalized");
        }
        const std::uint64_t bit_length = _total_bytes * UINT64_C(8);

        _buffer[_buffer_size++] = UINT8_C(0x80);
        if (_buffer_size > 56) {
            std::fill(_buffer.begin() + _buffer_size,
                      _buffer.end(),
                      UINT8_C(0));
            transform(_buffer.data());
            _buffer_size = 0;
        }
        std::fill(_buffer.begin() + _buffer_size,
                  _buffer.begin() + 56,
                  UINT8_C(0));
        for (std::size_t index = 0; index < 8; ++index) {
            _buffer[56 + index] = static_cast<std::uint8_t>(
                bit_length >> (56 - 8 * index));
        }
        transform(_buffer.data());
        _buffer_size = 0;
        _finalized = true;

        static constexpr char digits[] = "0123456789abcdef";
        std::string result(64, '0');
        std::size_t output = 0;
        for (const std::uint32_t word : _state) {
            for (int shift = 28; shift >= 0; shift -= 4) {
                result[output++] = digits[(word >> shift) & UINT32_C(0x0f)];
            }
        }
        return result;
    }

    void Sha256::transform(const std::uint8_t *block) noexcept {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index) {
            words[index] =
                (static_cast<std::uint32_t>(block[4 * index]) << 24) |
                (static_cast<std::uint32_t>(block[4 * index + 1]) << 16) |
                (static_cast<std::uint32_t>(block[4 * index + 2]) << 8) |
                static_cast<std::uint32_t>(block[4 * index + 3]);
        }
        for (std::size_t index = 16; index < words.size(); ++index) {
            const std::uint32_t s0 =
                rotateRight(words[index - 15], 7) ^
                rotateRight(words[index - 15], 18) ^
                (words[index - 15] >> 3);
            const std::uint32_t s1 =
                rotateRight(words[index - 2], 17) ^
                rotateRight(words[index - 2], 19) ^
                (words[index - 2] >> 10);
            words[index] = words[index - 16] + s0 + words[index - 7] + s1;
        }

        std::uint32_t a = _state[0];
        std::uint32_t b = _state[1];
        std::uint32_t c = _state[2];
        std::uint32_t d = _state[3];
        std::uint32_t e = _state[4];
        std::uint32_t f = _state[5];
        std::uint32_t g = _state[6];
        std::uint32_t h = _state[7];

        for (std::size_t index = 0; index < words.size(); ++index) {
            const std::uint32_t sigma1 =
                rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
            const std::uint32_t choose = (e & f) ^ ((~e) & g);
            const std::uint32_t temporary1 =
                h + sigma1 + choose + round_constants[index] + words[index];
            const std::uint32_t sigma0 =
                rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temporary2 = sigma0 + majority;

            h = g;
            g = f;
            f = e;
            e = d + temporary1;
            d = c;
            c = b;
            b = a;
            a = temporary1 + temporary2;
        }

        _state[0] += a;
        _state[1] += b;
        _state[2] += c;
        _state[3] += d;
        _state[4] += e;
        _state[5] += f;
        _state[6] += g;
        _state[7] += h;
    }

    std::string sha256Hex(std::string_view data) {
        Sha256 digest;
        digest.update(data);
        return digest.finalHex();
    }

} // namespace RTSim
