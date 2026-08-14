from fractions import Fraction

from experiments.b4_priority_energy.experiment import (
    B4Request,
    generate_base_taskset,
    source_energy,
)


def _request() -> B4Request:
    return next(
        request
        for request in __import__("experiments.b4_priority_energy.experiment", fromlist=["iter_requests"]).iter_requests(("pilot",))
        if request.utilization == "0.3"
        and request.replicate_index == 1
        and request.lambda_E == "0.70"
        and request.rho_E == "1"
    )


def test_direct_energy_source_preserves_frozen_bounds():
    document, _payload = generate_base_taskset(_request())
    energy = source_energy(document, "0.70")
    assert energy["Emax_j"] == 2 * energy["E0_j"]
    assert energy["alpha_w"] >= 0
    assert energy["nominal_demand_j"] > 0


def test_direct_energy_source_uses_exact_fraction_arithmetic():
    document, _payload = generate_base_taskset(_request())
    energy = source_energy(document, "0.70")
    assert all(isinstance(value, Fraction) for value in energy.values())
