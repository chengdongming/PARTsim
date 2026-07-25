#include <gtest/gtest.h>

#include <rtsim/harvesting/sha256.hpp>

#include <stdexcept>
#include <string>

namespace RTSim {

    TEST(Sha256, MatchesStandardVectors) {
        EXPECT_EQ(
            sha256Hex(""),
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855");
        EXPECT_EQ(
            sha256Hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad");
        EXPECT_EQ(
            sha256Hex(std::string(1000000, 'a')),
            "cdc76e5c9914fb9281a1c7e284d73e67"
            "f1809a48a497200e046d39ccc7112cd0");
    }

    TEST(Sha256, IncrementalUpdatesPreserveIdentityAndFinalizationIsStrict) {
        Sha256 digest;
        digest.update("a", 1);
        digest.update("b", 1);
        digest.update("c", 1);
        EXPECT_EQ(
            digest.finalHex(),
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad");
        EXPECT_THROW((void)digest.finalHex(), std::logic_error);
        EXPECT_THROW(digest.update("x", 1), std::logic_error);
    }

} // namespace RTSim
