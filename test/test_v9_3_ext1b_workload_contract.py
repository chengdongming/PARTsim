from __future__ import annotations

from copy import deepcopy
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asap_block_rta_v9_3 as rta_core  # noqa: E402
from experiments.v9_3.cell_model import (  # noqa: E402
    expand_cells,
    generation_dimensions,
)
from experiments.v9_3.config import ConfigError, domain_hash  # noqa: E402
from experiments.v9_3.ext1b_config import (  # noqa: E402
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner  # noqa: E402
from experiments.v9_3.ext1b_generation import (  # noqa: E402
    StructuralRejection,
    build_scenario_instance,
    scenario_cells,
    workload_energy_table,
)
from experiments.v9_3.taskset_store import (  # noqa: E402
    ServiceCurveMaterial,
    StoredTaskset,
    TasksetStore,
    TasksetStoreError,
    prepare_service_curve,
)
from global_task_generator import EnergyAwareTaskGenerator  # noqa: E402


CANDIDATES = ("bzip2", "control", "decrypt", "encrypt", "hash")
CALIBRATION = ROOT / "configs/v9_3_ext1b3_timing_calibration.yaml"
SYSTEM_TEMPLATE = ROOT / "system_config_unified_template.yml"


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_ext1b_generator_contract_excludes_idle_across_many_tasksets():
    modeled = {name for name, _energy in workload_energy_table(SYSTEM_TEMPLATE)}
    observed = set()
    for seed in range(64):
        generator = EnergyAwareTaskGenerator(
            seed=seed,
            system_config_path=str(SYSTEM_TEMPLATE),
            task_workload_candidates=CANDIDATES,
        )
        tasks, _resources, _dag, _energy = generator.generate_taskset(
            n=10,
            total_utilization=0.8,
            min_period=40,
            max_period=200,
            num_cpus=4,
            implicit_deadline=False,
            dag_enabled=False,
            energy_aware=False,
            arrival_offset=False,
            min_task_util=0.01,
            max_task_util=0.8,
            wcet_rounding="compensated",
            actual_utilization_tolerance_total=0.01,
        )
        workloads = {str(task["workload"]) for task in tasks}
        observed.update(workloads)
        assert "idle" not in workloads
        assert workloads <= modeled
    assert observed == set(CANDIDATES)


def test_candidate_pool_is_stable_and_part_of_generation_identity():
    config = load_ext1b_config(CALIBRATION)
    assert tuple(config["generation"]["workload_candidates"]) == CANDIDATES
    cell = expand_cells(config)[0]
    dimensions = generation_dimensions(
        config, cell.processors, cell.task_count, cell.utilization,
    )
    contract = dimensions["task_workload_contract"]
    material = {"ordered_candidates": list(CANDIDATES)}
    assert contract == {
        "version": "NON_IDLE_V1",
        **material,
        "candidate_identity": domain_hash(
            "ASAP_BLOCK:V9.3:TASK_WORKLOAD_CANDIDATES:v1", material,
        ),
    }

    changed = deepcopy(config)
    changed["generation"]["workload_candidates"] = list(CANDIDATES[:-1])
    changed_cell = expand_cells(changed)[0]
    assert changed_cell.generation_id != cell.generation_id
    assert ext1b_config_hash(changed) != ext1b_config_hash(config)


@pytest.mark.parametrize(
    "candidates,match",
    [
        (None, "requires generation.workload_candidates"),
        (["bzip2", "idle"], "must not contain idle"),
        (list(reversed(CANDIDATES)), "stable lexical order"),
    ],
)
def test_actual_generator_order_requires_canonical_non_idle_pool(candidates, match):
    raw = yaml.safe_load(CALIBRATION.read_text(encoding="utf-8"))
    if candidates is None:
        raw["generation"].pop("workload_candidates")
    else:
        raw["generation"]["workload_candidates"] = candidates
    with pytest.raises(ConfigError, match=match):
        validate_ext1b_config(raw)


def test_same_b3_source_identity_repeats_exactly_and_freezes_provenance(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    cell = expand_cells(config)[0]
    service_a = prepare_service_curve(config, tmp_path / "service-a")
    service_b = prepare_service_curve(config, tmp_path / "service-b")
    first = TasksetStore(
        tmp_path / "store-a", config, service_a,
    ).get_or_create(cell, 7)
    second = TasksetStore(
        tmp_path / "store-b", config, service_b,
    ).get_or_create(cell, 7)

    assert first.seed == second.seed
    assert first.semantic_hash == second.semantic_hash
    assert first.task_payload == second.task_payload
    assert all(row["workload"] != "idle" for row in first.task_payload)
    assert {row["workload"] for row in first.task_payload} <= set(CANDIDATES)

    document = json.loads(first.canonical_path.read_text(encoding="utf-8"))
    assert document["schema"] == "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V2"
    frozen = document["task_workload_contract"]
    assert tuple(frozen["ordered_candidates"]) == CANDIDATES
    assert frozen["candidate_identity"] == generation_dimensions(
        config, cell.processors, cell.task_count, cell.utilization,
    )["task_workload_contract"]["candidate_identity"]
    assert len(frozen["power_model_identity"]) == 64


def test_power_model_change_invalidates_workload_provenance(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    original = yaml.safe_load(SYSTEM_TEMPLATE.read_text(encoding="utf-8"))
    changed = deepcopy(original)
    changed["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ]["hash"] = 0.81
    original_path = tmp_path / "original.yml"
    changed_path = tmp_path / "changed.yml"
    original_path.write_text(
        yaml.safe_dump(original, sort_keys=False), encoding="utf-8",
    )
    changed_path.write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8",
    )
    service_a = ServiceCurveMaterial((Fraction(0),), "same", "{}", original_path)
    service_b = ServiceCurveMaterial((Fraction(0),), "same", "{}", changed_path)
    root = tmp_path / "store"
    TasksetStore(root, config, service_a)
    with pytest.raises(TasksetStoreError, match="pairing manifest contract mismatch"):
        TasksetStore(root, config, service_b)


def test_idle_stored_taskset_still_fails_closed_in_actual_profile(tmp_path):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b3_smoke.yaml")
    payload = ({
        "task_id": "0",
        "source_name": "damaged",
        "priority_rank": 0,
        "C": 1,
        "D": 10,
        "T": 10,
        "P": "1",
        "D_over_T": "1",
        "workload": "idle",
        "arrival_offset": 0,
    },)
    stored = StoredTaskset(
        "damaged", "generation", 0, 17, "semantic", "priority", "power",
        Fraction(1, 10), Fraction(1, 10), 1, 1, "constrained",
        (rta_core.V93Task("0", 1, 10, 10, Fraction(1)),),
        payload, 0.0, "service", tmp_path / "damaged.json",
    )
    with pytest.raises(StructuralRejection) as caught:
        build_scenario_instance(
            stored,
            config,
            scenario_cells(config)[0],
            logical_taskset_index=0,
            attempt_index=0,
            system_root=tmp_path / "systems",
        )
    assert caught.value.code == "WORKLOAD_NOT_IN_ACTUAL_POWER_MODEL"
    assert caught.value.detail == "idle"


def test_formal_b3_plan_only_materializes_full_structure_without_simulation(
    tmp_path, monkeypatch,
):
    config = load_ext1b_config(CALIBRATION)
    config["execution"]["output_root"] = str(tmp_path / "plan")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    simulator = tmp_path / "simulator-not-to-be-run"
    simulator.write_bytes(b"plan identity only\n")
    config["simulation"]["simulator_bin"] = str(simulator)

    def forbidden_simulation(*_args, **_kwargs):
        raise AssertionError("plan-only invoked the simulator")

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        forbidden_simulation,
    )
    runner = Ext1BRunner(config)
    outcome = runner.materialize_plan()
    assert outcome.summary == {
        "schema": "ASAP_BLOCK_V9_3_EXT1B_PLAN_ONLY_V1",
        "output_root": str(tmp_path / "plan"),
        "plan_only": True,
        "simulator_invoked": False,
        "cell_count": 4,
        "generated_tasksets": 80,
        "generation_attempts": outcome.generation_attempts,
        "paired_instances": 80,
        "simulation_requests": 240,
    }

    generated = _rows(tmp_path / "plan/generated_tasksets.csv")
    attempts = _rows(tmp_path / "plan/generation_attempts.csv")
    instances = _rows(tmp_path / "plan/scenario_instances.csv")
    requests = _rows(tmp_path / "plan/simulation_requests.csv")
    assert len(generated) == len(instances) == 80
    assert len(requests) == 240
    assert all(row["request_status"] == "PLANNED" for row in requests)
    assert not list((tmp_path / "plan/simulation_terminal_results").glob("*.json"))
    assert all(
        int(row["source_taskset_index"])
        == int(row["logical_taskset_index"]) * 16 + int(row["attempt_index"])
        for row in attempts
    )
    assert not any(
        row["rejection_code"] == "WORKLOAD_NOT_IN_ACTUAL_POWER_MODEL"
        for row in attempts
    )
    modeled = {name for name, _energy in workload_energy_table(SYSTEM_TEMPLATE)}
    for row in generated:
        tasks = json.loads(row["task_input_json"])
        assert all(task["workload"] != "idle" for task in tasks)
        assert {task["workload"] for task in tasks} <= modeled
