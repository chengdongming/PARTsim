from __future__ import annotations

import pytest

from experiments.v9_3.solar_parse_proof import (
    build_diagnostic_untrusted_verifier_binary,
)


@pytest.fixture(scope="session")
def rta4_solar_stod_diagnostic_untrusted_verifier(tmp_path_factory):
    binary = (
        tmp_path_factory.mktemp("rta4-solar-stod-verifier")
        / "rta4_solar_stod_verifier"
    )
    build_diagnostic_untrusted_verifier_binary(binary)
    return binary
