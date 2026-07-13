from copy import deepcopy
from fractions import Fraction

import pytest

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import ConfigError, exact_fraction, validate_config
from v9_3_experiment_helpers import make_config


def test_exact_rational_e0_round_trip_and_no_float(tmp_path):
    config = make_config(tmp_path, e0=[0, "1.25", "178487996829/2000000000000"])
    assert config["energy"]["initial_energy_values"] == [
        "0", "5/4", "178487996829/2000000000000"
    ]
    assert exact_fraction(config["energy"]["initial_energy_values"][2], "E0") == Fraction(178487996829, 2000000000000)
    with pytest.raises(ConfigError):
        exact_fraction(0.1, "E0")


@pytest.mark.parametrize("mutation", [
    lambda c: c["analysis"].update(timeout_seconds=0),
    lambda c: c["analysis"].update(retry_timeout_seconds=.5),
    lambda c: c["grid"].update(tasksets_per_cell=0),
    lambda c: c["analysis"].update(variants=["CW_THETA_CW", "CW_THETA_CW"]),
    lambda c: c["generation"].update(deadline_mode="invented"),
    lambda c: c["generation"].update(power_mode="invented"),
    lambda c: c["generation"].update(priority_policy="EDF"),
])
def test_invalid_configuration_rejected(tmp_path, mutation):
    config = make_config(tmp_path)
    mutation(config)
    with pytest.raises(ConfigError):
        validate_config(config)


def test_constrained_passthrough_is_limited_to_existing_generator_semantics(tmp_path):
    config = make_config(tmp_path)
    config["generation"]["deadline_mode"] = "constrained"
    assert validate_config(config)["generation"]["deadline_mode"] == "constrained"
    config["generation"]["constrained_deadline"]["d_over_t_values"] = ["1/2"]
    with pytest.raises(ConfigError, match="production generator"):
        validate_config(config)


def test_seed_is_stable_and_variant_free(tmp_path):
    cells = expand_cells(make_config(tmp_path, e0=["0", "1"]))
    assert cells[0].generation_id == cells[1].generation_id
    assert derive_seed(93, cells[0].generation_id, 0) == derive_seed(93, cells[1].generation_id, 0)
