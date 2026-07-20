from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.v9_3.cell_model import expand_cells
from experiments.v9_3.config import config_hash, load_config
from experiments.v9_3.formal_authorization import FORMAL_PARAMETER_STATUS
from experiments.v9_3.core3_pairing import (
    Core3PairingRunner,
    _observation_comparison_eligibility,
    _release_e0_valid,
)
from experiments.v9_3.simulation_engine import SimulationExecution
from experiments.v9_3.simulation_result import SimulationResult, SimulationStatus


ROOT = Path(__file__).resolve().parents[1]
B20 = ROOT / "configs/v9_3_core3_formal_b20_r2.yaml"
B100 = ROOT / "configs/v9_3_core3_formal_b100_r2.yaml"


@pytest.fixture(params=[B20, B100])
def formal_config(request):
    return load_config(request.param, expected_core="CORE-3")


def test_formal_parameter_contract(formal_config):
    assert formal_config["parameter_status"] == FORMAL_PARAMETER_STATUS
    assert formal_config["platform"] == {"cores": [4], "task_count": [10]}
    assert formal_config["grid"]["utilization_points"] == [
        "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
    ]
    assert formal_config["grid"]["tasksets_per_cell"] == 200
    assert formal_config["grid"]["base_seed"] == 930433
    assert formal_config["grid"]["taskset_index_start"] == 0
    assert formal_config["energy"]["initial_energy_values"] == ["0", "1/20", "1"]
    assert formal_config["simulation"]["horizon"] == 30000
    assert formal_config["simulation"]["maximum_horizon"] == 30000
    assert formal_config["simulation"]["horizon_extension_policy"] == "none"
    assert formal_config["simulation"]["reuse_across_e0"] is True


def test_formal_dry_run_counts_are_expanded_not_hard_coded(formal_config):
    plan = Core3PairingRunner(formal_config).describe()
    assert len(expand_cells(formal_config)) == 24
    assert plan["unique_taskset_count"] == 8 * 200
    assert plan["rta_request_count"] == 8 * 3 * 200 * 2
    assert plan["simulation_request_count"] == 8 * 200
    assert plan["total_terminal_count"] == 11200


def test_formal_tracks_pair_generation_but_isolate_artifacts_and_hashes():
    b20 = load_config(B20, expected_core="CORE-3")
    b100 = load_config(B100, expected_core="CORE-3")
    assert {
        cell.generation_id for cell in expand_cells(b20)
    } == {
        cell.generation_id for cell in expand_cells(b100)
    }
    assert b20["execution"]["output_root"] != b100["execution"]["output_root"]
    assert b20["execution"]["taskset_store"] != b100["execution"]["taskset_store"]
    assert config_hash(b20) != config_hash(b100)


def test_reused_simulation_recomputes_release_gate_per_e0():
    result = SimulationResult(
        SimulationStatus.PASS_OBSERVED, "minimum_jobs_observed", 30000,
        (), (), True, 0.05, {}, 2, "gpfp_asap_block", True,
        "reached_horizon",
    )
    execution = SimulationExecution(
        "sim", result, 1.0, 1, (30000,), Path("system"), Path("tasks"), None,
    )
    assert _release_e0_valid(execution, Fraction(0))
    assert _release_e0_valid(execution, Fraction(1, 20))
    assert not _release_e0_valid(execution, Fraction(1))
    assert _observation_comparison_eligibility(execution, Fraction(0))[0]
    assert not _observation_comparison_eligibility(execution, Fraction(1))[0]


def test_smoke_dry_run_contract_is_unchanged():
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_v9_3_core3.py", "--config",
            "configs/v9_3_core3_smoke.yaml", "--dry-run",
        ],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert '"request_count": 4' in completed.stdout
    assert '"simulation_request_count": 2' in completed.stdout
    assert '"unique_taskset_count"' not in completed.stdout
