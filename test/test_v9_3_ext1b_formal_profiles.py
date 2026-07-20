"""Fail-closed configuration tests for the three EXT-1B formal profiles."""

from __future__ import annotations

from copy import deepcopy
import csv
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.config import ConfigError
from experiments.v9_3.ext1b_config import (
    FORMAL_PROFILE_BY_SEED_SPACE,
    dump_ext1b_config,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner
from scripts.audit_v9_3_ext1b_formal_plan import (
    PlanAuditError,
    _pairing_audit,
)


ROOT = Path(__file__).resolve().parents[1]
B1_PATH = ROOT / "configs/v9_3_ext1b1_formal_r1.yaml"
B2_PATH = (
    ROOT
    / "configs/v9_3_ext1b2_sync_formal_r1_workload_contract_v2.yaml"
)
B3_PATH = (
    ROOT
    / "configs/v9_3_ext1b3_timing_formal_r1_workload_contract_v2_capacity_contract_v1.yaml"
)

FORMAL_CASES = (
    (
        "B1",
        B1_PATH,
        "EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2",
        951201,
        ["gpfp_asap_block", "gpfp_asap_nonblock"],
        6,
        1200,
        2400,
    ),
    (
        "B2",
        B2_PATH,
        "EXT1B2_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2",
        971201,
        ["gpfp_asap_block", "gpfp_asap_sync"],
        6,
        1200,
        2400,
    ),
    (
        "B3",
        B3_PATH,
        (
            "EXT1B3_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2_"
            "CAPACITY_CONTRACT_V1"
        ),
        971301,
        ["gpfp_asap_block", "gpfp_alap_block", "gpfp_st_block"],
        4,
        800,
        2400,
    ),
)


def _raw(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mechanism_projection(raw: dict) -> dict:
    value = deepcopy(raw)
    for key in ("experiment_id", "parameter_status", "seed_space"):
        value.pop(key, None)
    value["grid"].pop("base_seed", None)
    value["grid"].pop("tasksets_per_cell", None)
    value["execution"].pop("output_root", None)
    value["execution"].pop("taskset_store", None)
    value["statistics"].pop("bootstrap_seed", None)
    return value


def test_formal_profile_registry_is_explicit_and_complete():
    assert set(FORMAL_PROFILE_BY_SEED_SPACE) == {
        item[2] for item in FORMAL_CASES
    }
    assert {
        profile["profile_id"]
        for profile in FORMAL_PROFILE_BY_SEED_SPACE.values()
    } == {"B1", "B2", "B3"}


@pytest.mark.parametrize(
    "profile_id,path,seed_space,base_seed,schedulers,cells,pairs,requests",
    FORMAL_CASES,
)
def test_formal_profiles_parse_normalize_and_have_frozen_cardinality(
    profile_id, path, seed_space, base_seed, schedulers, cells, pairs, requests,
):
    first = load_ext1b_config(path)
    second = load_ext1b_config(path)
    description = Ext1BRunner(first).describe()

    assert first == second
    assert first["parameter_status"] == "FORMAL"
    assert first["seed_space"] == seed_space
    assert first["grid"]["base_seed"] == base_seed
    assert first["grid"]["tasksets_per_cell"] == 200
    assert first["scheduler_ids"] == schedulers
    assert first["generation"]["workload_contract"]["version"] == (
        "REAL_TIME_TASK_WORKLOAD_CONTRACT_V2"
    )
    assert description["cell_count"] == cells
    assert description["paired_instance_count"] == pairs
    assert description["simulation_request_count"] == requests
    assert FORMAL_PROFILE_BY_SEED_SPACE[seed_space]["profile_id"] == profile_id


@pytest.mark.parametrize(
    "_profile_id,path,_seed_space,_base_seed,_schedulers,_cells,_pairs,_requests",
    FORMAL_CASES,
)
def test_normalized_formal_config_round_trip_is_stable(
    tmp_path, _profile_id, path, _seed_space, _base_seed, _schedulers,
    _cells, _pairs, _requests,
):
    normalized = load_ext1b_config(path)
    persisted = tmp_path / path.name
    dump_ext1b_config(normalized, persisted)
    assert load_ext1b_config(persisted) == normalized


def test_b1_existing_frozen_identity_is_unchanged():
    raw = _raw(B1_PATH)
    assert raw["experiment_id"] == (
        "asap-block-v9.3-ext1b1-formal-r1-workload-contract-v2"
    )
    assert raw["seed_space"] == "EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2"
    assert raw["grid"]["base_seed"] == 951201
    assert raw["execution"] == {
        "worker_count": 1,
        "checkpoint_every": 5,
        "output_root": "artifacts/v9_3_ext1b1_formal_r1_workload_contract_v2",
        "taskset_store": (
            "artifacts/v9_3_ext1b1_taskset_store_formal_r1_workload_contract_v2"
        ),
        "resume": False,
        "fail_fast_on_p0": True,
        "preserve_attempt_history": True,
    }


def test_b2_and_b3_change_only_registered_formal_identity_scale_and_bootstrap():
    pairs = (
        (B2_PATH, ROOT / "configs/v9_3_ext1b2_sync_calibration.yaml"),
        (B3_PATH, ROOT / "configs/v9_3_ext1b3_timing_calibration.yaml"),
    )
    for formal_path, calibration_path in pairs:
        assert _mechanism_projection(_raw(formal_path)) == _mechanism_projection(
            _raw(calibration_path)
        )


@pytest.mark.parametrize(
    "source_path,wrong_path",
    (
        (B1_PATH, B2_PATH),
        (B1_PATH, B3_PATH),
        (B2_PATH, B1_PATH),
        (B2_PATH, B3_PATH),
        (B3_PATH, B1_PATH),
        (B3_PATH, B2_PATH),
    ),
)
def test_formal_profiles_reject_every_cross_profile_mechanism_mix(
    source_path, wrong_path,
):
    raw = _raw(source_path)
    wrong = _raw(wrong_path)
    raw["scenario"] = deepcopy(wrong["scenario"])
    raw["scheduler_ids"] = list(wrong["scheduler_ids"])
    raw["required_outputs"] = list(wrong["required_outputs"])
    with pytest.raises(ConfigError, match="FORMAL profile .*scenario"):
        validate_ext1b_config(raw)


@pytest.mark.parametrize(
    "path,mutation,message",
    (
        (B2_PATH, ("scheduler_ids", None, ["gpfp_asap_sync"]), "scheduler_ids"),
        (B2_PATH, ("grid", "base_seed", 971202), "base_seed"),
        (B2_PATH, ("scenario", "affordable_prefix_length", 2), "affordable_prefix_length"),
        (B2_PATH, ("statistics", "top_m", 3), "top_m"),
        (
            B3_PATH,
            (
                "scheduler_ids",
                None,
                ["gpfp_st_block", "gpfp_alap_block", "gpfp_asap_block"],
            ),
            "scheduler_ids",
        ),
        (B3_PATH, ("grid", "tasksets_per_cell", 199), "tasksets_per_cell"),
    ),
)
def test_formal_profiles_reject_scheduler_seed_scale_and_mechanism_mutations(
    path, mutation, message,
):
    raw = _raw(path)
    section, field, value = mutation
    if field is None:
        raw[section] = value
    else:
        raw[section][field] = value
    with pytest.raises(ConfigError, match=message):
        validate_ext1b_config(raw)


def test_b3_profile_requires_capacity_contract_and_exact_timing_cells():
    missing = _raw(B3_PATH)
    missing["scenario"].pop("capacity_feasibility_contract")
    with pytest.raises(ConfigError, match="capacity feasibility contract"):
        validate_ext1b_config(missing)

    changed = _raw(B3_PATH)
    changed["scenario"]["timing_cells"][1]["deadline_ratio_min"] = "2/3"
    with pytest.raises(ConfigError, match="timing_cells"):
        validate_ext1b_config(changed)


def test_unknown_and_pilot_formal_seed_spaces_fail_closed():
    for seed_space in (
        "EXT1B_UNKNOWN_FORMAL",
        "EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
    ):
        raw = _raw(B2_PATH)
        raw["seed_space"] = seed_space
        with pytest.raises(ConfigError, match="FORMAL requires seed_space"):
            validate_ext1b_config(raw)


@pytest.mark.parametrize(
    "path,section,field,value,message",
    (
        (B1_PATH, None, "experiment_id", "changed", "experiment_id"),
        (B1_PATH, "grid", "base_seed", 971101, "base_seed"),
        (
            B1_PATH,
            "execution",
            "output_root",
            "artifacts/v9_3_ext1b1_energy_calibration_workload_contract_v2",
            "output_root",
        ),
        (
            B2_PATH,
            "execution",
            "taskset_store",
            "artifacts/v9_3_ext1b2_taskset_store_sync_calibration_workload_contract_v2",
            "taskset_store",
        ),
    ),
)
def test_formal_identity_and_pilot_path_reuse_fail_closed(
    path, section, field, value, message,
):
    raw = _raw(path)
    target = raw if section is None else raw[section]
    target[field] = value
    with pytest.raises(ConfigError, match=message):
        validate_ext1b_config(raw)


def test_legacy_workload_contract_is_rejected():
    raw = _raw(B2_PATH)
    raw["generation"]["workload_contract"] = {
        "version": "REAL_TIME_TASK_WORKLOAD_CONTRACT_V1"
    }
    with pytest.raises(ConfigError, match="workload_contract"):
        validate_ext1b_config(raw)


@pytest.mark.parametrize(
    "_profile_id,path,_seed_space,_base_seed,_schedulers,_cells,_pairs,_requests",
    FORMAL_CASES,
)
def test_formal_plan_only_never_invokes_simulator(
    tmp_path, monkeypatch, _profile_id, path, _seed_space, _base_seed,
    _schedulers, _cells, _pairs, _requests,
):
    config = load_ext1b_config(path)
    output = tmp_path / "output"
    store = tmp_path / "store"
    simulator = tmp_path / "simulator-identity-only"
    simulator.write_bytes(b"not executable; plan identity only\n")
    config["execution"]["output_root"] = str(output)
    config["execution"]["taskset_store"] = str(store)
    config["simulation"]["simulator_bin"] = str(simulator)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("plan-only invoked simulator")

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation", forbidden,
    )
    outcome = Ext1BRunner(config).materialize_plan(
        max_cells=1, max_tasksets=1,
    )
    assert outcome.summary["simulator_invoked"] is False
    assert outcome.summary["simulation_requests"] == len(config["scheduler_ids"])

    with (output / "simulation_requests.csv").open(
        "r", encoding="utf-8", newline="",
    ) as handle:
        requests = list(csv.DictReader(handle))
    assert _pairing_audit(requests, list(config["scheduler_ids"]))[
        "pairing_failure_count"
    ] == 0
    with pytest.raises(PlanAuditError, match="paired request audit failed"):
        _pairing_audit(requests[:-1], list(config["scheduler_ids"]))
