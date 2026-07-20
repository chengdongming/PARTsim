from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from experiments.v9_3.config import ConfigError, validate_config
from experiments.v9_3.ext1_scheduler_comparison import Ext1Runner
from experiments.v9_3.ext1b_aggregation import (
    _overall_relation,
    aggregate_ext1b_rows,
    classify_mechanism_activation,
)
from experiments.v9_3.ext1b_config import (
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import (
    Ext1BRunner,
    assert_ext1b_fair_pairing,
    verify_file_hashes,
)
from experiments.v9_3.ext1b_generation import (
    NATIVE_ENERGY_EPSILON_J,
    _apply_power_profile,
    StructuralRejection,
    bypass_structure,
    interpolate_exact,
    materialize_scenario_system,
    native_energy_affordable,
    scenario_cells,
    sync_batch_structure,
    transform_constrained_deadlines,
)
from experiments.v9_3.ext1b_statistics import (
    UNAVAILABLE,
    exact_mcnemar_p,
    holm_adjust,
    paired_bootstrap_ci,
    paired_statistics_rows,
)
from experiments.v9_3.result_writer import write_file_hashes
from experiments.v9_3.scheduler_registry import SCHEDULER_IDS, scheduler_by_id
from experiments.v9_3.simulation_engine import (
    SimulationExecution,
    load_simulation_terminal,
    run_paired_simulation,
    write_simulation_terminal,
)
from experiments.v9_3.simulation_result import (
    JobObservation,
    SimulationResult,
    SimulationStatus,
    TaskObservation,
    parse_simulation_trace,
)
from v9_3_core3_helpers import trace_document


ROOT = Path(__file__).resolve().parents[1]
TRACE_HASH = "a" * 64


def _raw_config(name: str = "v9_3_ext1b1_smoke.yaml"):
    return yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))


def _payload():
    return [
        {"task_id": "0", "priority_rank": 0, "C": 1, "D": 5, "T": 10,
         "D_over_T": "1/2", "workload": "video", "P": "3", "arrival_offset": 0},
        {"task_id": "1", "priority_rank": 1, "C": 1, "D": 6, "T": 12,
         "D_over_T": "1/2", "workload": "sensor", "P": "1", "arrival_offset": 0},
        {"task_id": "2", "priority_rank": 2, "C": 1, "D": 7, "T": 14,
         "D_over_T": "1/2", "workload": "control", "P": "2", "arrival_offset": 0},
    ]


def _fair_rows():
    common = {
        "paired_instance_id": "pair",
        "taskset_hash": "task",
        "trace_hash": "trace",
        "simulation_config_hash": "sim",
        "input_hash": "input",
        "initial_battery": "2",
        "battery_capacity": "3",
        "horizon": 60,
        "maximum_horizon": 60,
        "generation_seed": 17,
        "M": 2,
        "priority_hash": "priority",
        "power_hash": "power",
        "deadline_hash": "deadline",
        "release_hash": "release",
        "workload_vector_hash": "workload",
        "simulator_build_hash": "simulator",
    }
    return [{**common, "scheduler_id": scheduler} for scheduler in SCHEDULER_IDS]


def _result_rows(kind: str = "BYPASS_STRESS", subtype: str = "B1"):
    registry = scheduler_by_id()
    rows = []
    for scheduler in SCHEDULER_IDS:
        mechanism = registry[scheduler].mechanism
        rows.append({
            "request_id": scheduler,
            "paired_instance_id": "pair",
            "scenario_kind": kind,
            "scenario_subtype": subtype,
            "scenario_cell_id": "cell",
            "scheduler_id": scheduler,
            "status": "SIM_PASS_OBSERVED",
            "comparison_eligible": True,
            "taskset_hash": "task",
            "trace_hash": "trace",
            "simulation_config_hash": "sim",
            "input_hash": "input",
            "overall_success": True,
            "top_m_success": True,
            "top_m_max_response_time": 2,
            "first_missed_priority_rank_numeric": 3,
            "energy_blocked_ticks": 0,
            "processor_wait_ticks": 0,
            "synchronization_wait_ticks": 1 if scheduler == "gpfp_asap_sync" else UNAVAILABLE,
            "bypass_count": 1 if scheduler == "gpfp_asap_nonblock" else UNAVAILABLE,
            "st_charge_begin_count": 0 if scheduler.startswith("gpfp_st_") else UNAVAILABLE,
            "battery_trajectory_json": "[]",
        })
    return rows


def _task_rows(kind: str = "BYPASS_STRESS", subtype: str = "B1"):
    rows = []
    registry = scheduler_by_id()
    starts = {"ASAP": 0, "ALAP": 3, "ST": 1}
    for scheduler in SCHEDULER_IDS:
        for rank in range(2):
            rows.append({
                "paired_instance_id": "pair",
                "scenario_kind": kind,
                "scenario_subtype": subtype,
                "scenario_cell_id": "cell",
                "scheduler_id": scheduler,
                "priority_rank": rank,
                "first_execution_time": starts[registry[scheduler].timing_family] + rank,
                "observed_jobs": 1,
                "completed_jobs": 1,
                "missed_jobs": 0,
                "censored_jobs": 0,
                "minimum_jobs_satisfied": True,
                "request_comparison_eligible": True,
                "maximum_observed_response_time": 2,
            })
    return rows


def _requests(kind: str = "BYPASS_STRESS", subtype: str = "B1"):
    return [{
        **row,
        "request_id": row["scheduler_id"],
        "scenario_kind": kind,
        "scenario_subtype": subtype,
        "scenario_cell_id": "cell",
    } for row in _fair_rows()]


def test_ext1a_smoke_still_parses_and_has_original_cardinality():
    raw = yaml.safe_load(
        (ROOT / "configs/v9_3_ext1_smoke.yaml").read_text(encoding="utf-8")
    )
    assert validate_config(raw, expected_core="CORE-3")["extension"] == "EXT-1"
    assert Ext1Runner.from_path(
        ROOT / "configs/v9_3_ext1_smoke.yaml"
    ).describe()["simulation_request_count"] == 18


def test_ext1a_default_has_no_ext1b_execution_switches():
    raw = yaml.safe_load(
        (ROOT / "configs/v9_3_ext1_smoke.yaml").read_text(encoding="utf-8")
    )
    assert "retain_trace" not in raw["simulation"]
    assert "allow_harvest_clipping" not in raw["energy"]


def test_ext1a_shared_defaults_keep_overflow_guard_and_failure_trace_path(
    tmp_path, monkeypatch,
):
    raw = yaml.safe_load(
        (ROOT / "configs/v9_3_ext1_smoke.yaml").read_text(encoding="utf-8")
    )
    config = validate_config(raw, expected_core="CORE-3")
    simulator = tmp_path / "rtsim"
    simulator.write_text("", encoding="utf-8")
    system, taskset = tmp_path / "system.yaml", tmp_path / "taskset.yaml"
    system.write_text("x", encoding="utf-8")
    taskset.write_text("x", encoding="utf-8")
    called = {"overflow_guard": 0}
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.materialize_simulation_inputs",
        lambda *args, **kwargs: (system, taskset),
    )

    def guard(*args, **kwargs):
        called["overflow_guard"] += 1
        return Fraction(0)

    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.validate_no_overflow_guard", guard,
    )

    def fake_run(command, **kwargs):
        trace = Path(command[command.index("-t") + 1])
        trace.write_text("{malformed", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("experiments.v9_3.simulation_engine.subprocess.run", fake_run)
    simulation = dict(config["simulation"])
    simulation["simulator_bin"] = str(simulator)
    execution = run_paired_simulation(
        simulation_id_value="x" * 64, base_system_path=system,
        run_root=tmp_path / "run", task_payload=_payload(),
        taskset_hash=TRACE_HASH, processors=2, exact_e0=Fraction(0),
        energy_config=config["energy"], simulation_config=simulation,
    )
    assert called["overflow_guard"] == 1
    assert execution.result.status is SimulationStatus.INTERNAL_ERROR
    assert execution.retained_trace_path.parent.name == "failure_traces"
    assert not (tmp_path / "run" / "retained_traces").exists()


def test_unknown_ext1b_field_is_rejected():
    raw = _raw_config()
    raw["scenario"]["outcome_filter"] = "ASAP_BLOCK_WINS"
    with pytest.raises(ConfigError, match="unknown scenario fields"):
        validate_ext1b_config(raw)


@pytest.mark.parametrize("kind", ["UNKNOWN", "BYPASS", "SYNC"])
def test_illegal_scenario_enum_is_rejected(kind):
    raw = _raw_config()
    raw["scenario"]["kind"] = kind
    with pytest.raises(ConfigError, match="unknown EXT-1B scenario kind"):
        validate_ext1b_config(raw)


def test_unknown_parameter_status_is_rejected():
    raw = _raw_config()
    raw["parameter_status"] = "READY"
    with pytest.raises(ConfigError, match="unknown EXT-1B parameter_status"):
        validate_ext1b_config(raw)


@pytest.mark.parametrize(
    ("scheduler_ids", "message"),
    [
        ([], "non-empty list"),
        (
            ["gpfp_asap_block", "gpfp_asap_block"],
            "contains duplicates",
        ),
        (["gpfp_asap_block", "unknown_scheduler"], "unknown schedulers"),
    ],
)
def test_scheduler_ids_reject_empty_duplicate_and_unknown(scheduler_ids, message):
    raw = _raw_config()
    raw["scheduler_ids"] = scheduler_ids
    with pytest.raises(ConfigError, match=message):
        validate_ext1b_config(raw)


def test_two_scheduler_plan_and_description_follow_config_order(tmp_path):
    selected = ["gpfp_asap_nonblock", "gpfp_asap_block"]
    raw = _raw_config()
    raw["scheduler_ids"] = selected
    raw["execution"]["output_root"] = str(tmp_path / "run")
    raw["execution"]["taskset_store"] = str(tmp_path / "store")
    runner = Ext1BRunner(validate_ext1b_config(raw))

    description = runner.describe(max_cells=1, max_tasksets=1)
    assert description["scheduler_count"] == 2
    assert description["paired_instance_count"] == 1
    assert description["simulation_request_count"] == 2
    assert description["scheduler_ids"] == selected

    _, _, _, requests = runner._plan(max_cells=1, max_tasksets=1)
    assert len(requests) == 2
    assert [row["scheduler_id"] for row in requests] == selected


def test_energy_calibration_dry_plan_has_required_cardinality():
    runner = Ext1BRunner.from_path(
        ROOT / "configs/v9_3_ext1b1_energy_calibration.yaml"
    )
    description = runner.describe()
    assert description["cell_count"] == 2 * 3 == 6
    assert description["tasksets_per_cell"] == 20
    assert description["scheduler_count"] == 2
    assert description["paired_instance_count"] == 120
    assert description["simulation_request_count"] == 240
    assert description["scheduler_ids"] == [
        "gpfp_asap_block", "gpfp_asap_nonblock",
    ]
    assert runner.config["seed_space"] == "EXT1B1_ENERGY_CALIBRATION_PILOT"
    assert runner.config["grid"]["base_seed"] != 931201
    assert runner.config["simulation"]["horizon"] == 400
    assert runner.config["simulation"]["maximum_horizon"] == 400


def test_b2_sync_calibration_contract_dry_plan_and_request_order(tmp_path):
    runner = Ext1BRunner.from_path(
        ROOT / "configs/v9_3_ext1b2_sync_calibration.yaml"
    )
    description = runner.describe()
    assert description["cell_count"] == 2 * 3 == 6
    assert description["tasksets_per_cell"] == 20
    assert description["scheduler_count"] == 2
    assert description["paired_instance_count"] == 120
    assert description["simulation_request_count"] == 240
    assert description["scheduler_ids"] == [
        "gpfp_asap_block", "gpfp_asap_sync",
    ]
    assert runner.config["required_outputs"] == [
        "b2_batch_decisions.csv", "b2_summary.csv",
    ]
    assert runner.config["seed_space"] == "EXT1B2_SYNC_CALIBRATION_PILOT"
    assert runner.config["grid"]["base_seed"] not in {931201, 941201}
    assert runner.config["simulation"]["horizon"] == 400
    assert runner.config["simulation"]["maximum_horizon"] == 400

    runner.root = tmp_path / "run"
    runner.terminals = runner.root / "simulation_terminal_results"
    runner.config["execution"]["output_root"] = str(runner.root)
    runner.config["execution"]["taskset_store"] = str(tmp_path / "store")
    _, _, _, requests = runner._plan(max_cells=1, max_tasksets=1)
    assert len(requests) == 2
    assert [row["scheduler_id"] for row in requests] == description["scheduler_ids"]
    assert len({row["input_hash"] for row in requests}) == 1
    assert len({row["taskset_hash"] for row in requests}) == 1


def test_b3_timing_calibration_contract_and_dry_run_cardinality():
    runner = Ext1BRunner.from_path(
        ROOT / "configs/v9_3_ext1b3_timing_calibration.yaml"
    )
    description = runner.describe()
    assert runner.config["experiment_id"] == (
        "asap-block-v9.3-ext1b3-timing-calibration"
    )
    assert runner.config["parameter_status"] == "PILOT"
    assert runner.config["seed_space"] == "EXT1B3_TIMING_CALIBRATION_PILOT_V2"
    assert runner.config["scheduler_ids"] == [
        "gpfp_asap_block", "gpfp_alap_block", "gpfp_st_block",
    ]
    assert runner.config["required_outputs"] == [
        "b3_timing_events.csv", "b3_summary.csv",
    ]
    assert runner.config["platform"] == {"cores": [4], "task_count": [10]}
    assert runner.config["grid"]["utilization_points"] == ["1/5", "2/5"]
    assert runner.config["grid"]["tasksets_per_cell"] == 20
    assert runner.config["grid"]["base_seed"] == 961201
    assert runner.config["simulation"]["retain_trace"] is True
    assert description["cell_count"] == 4
    assert description["tasksets_per_cell"] == 20
    assert description["scheduler_count"] == 3
    assert description["paired_instance_count"] == 80
    assert description["simulation_request_count"] == 240

    cells = scenario_cells(runner.config)
    assert [cell.cell_id for cell in cells] == [
        "positive-slack-energy-available", "slack-limited-charging",
    ]
    assert [cell.subtype for cell in cells] == [
        "POSITIVE_SLACK_ENERGY_AVAILABLE", "SLACK_LIMITED_CHARGING",
    ]
    assert [cell.deadline_ratio_min for cell in cells] == [
        Fraction(1, 2), Fraction(3, 4),
    ]
    assert [cell.deadline_ratio_max for cell in cells] == [
        Fraction(3, 4), Fraction(1),
    ]
    assert [cell.nominal_supply_ratio for cell in cells] == [
        Fraction(0), Fraction(1, 2),
    ]


def test_b3_calibration_seed_is_pilot_only_and_scheduler_order_is_exact():
    raw = _raw_config("v9_3_ext1b3_timing_calibration.yaml")
    smoke = deepcopy(raw)
    smoke["parameter_status"] = "SMOKE"
    with pytest.raises(ConfigError, match="SMOKE requires seed_space"):
        validate_ext1b_config(smoke)

    reversed_schedulers = deepcopy(raw)
    reversed_schedulers["scheduler_ids"] = list(
        reversed(reversed_schedulers["scheduler_ids"])
    )
    with pytest.raises(ConfigError, match="must equal"):
        validate_ext1b_config(reversed_schedulers)


def test_b2_seed_space_is_pilot_only_and_scheduler_pair_is_exact():
    raw = _raw_config("v9_3_ext1b2_sync_calibration.yaml")
    smoke = deepcopy(raw)
    smoke["parameter_status"] = "SMOKE"
    with pytest.raises(ConfigError, match="SMOKE requires seed_space"):
        validate_ext1b_config(smoke)

    reversed_pair = deepcopy(raw)
    reversed_pair["scheduler_ids"] = [
        "gpfp_asap_sync", "gpfp_asap_block",
    ]
    with pytest.raises(ConfigError, match="must equal"):
        validate_ext1b_config(reversed_pair)


def test_sync_batch_stress_requires_block_and_sync_schedulers():
    raw = _raw_config("v9_3_ext1b2_smoke.yaml")
    raw["scheduler_ids"] = ["gpfp_asap_sync"]
    with pytest.raises(ConfigError, match="must include"):
        validate_ext1b_config(raw)


def test_required_output_contracts_preserve_b1_b2_and_lock_b3():
    b1 = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_energy_calibration.yaml")
    b2 = load_ext1b_config(ROOT / "configs/v9_3_ext1b2_smoke.yaml")
    b3 = load_ext1b_config(ROOT / "configs/v9_3_ext1b3_smoke.yaml")
    assert b1["required_outputs"] == [
        "b1_bypass_episodes.csv", "b1_task_effects.csv",
        "b1_paired_effects.csv", "b1_summary.csv",
    ]
    assert b2["required_outputs"] == [
        "b2_batch_decisions.csv", "b2_summary.csv",
    ]
    assert b3["required_outputs"] == [
        "b3_timing_events.csv", "b3_summary.csv",
    ]


def test_existing_config_without_scheduler_ids_keeps_nine_scheduler_order():
    runner = Ext1BRunner.from_path(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    description = runner.describe(max_cells=1, max_tasksets=1)
    assert description["scheduler_count"] == 9
    assert description["simulation_request_count"] == 9
    assert description["scheduler_ids"] == list(SCHEDULER_IDS)
    b2 = Ext1BRunner.from_path(ROOT / "configs/v9_3_ext1b2_smoke.yaml")
    assert b2.describe(max_cells=1, max_tasksets=1)["scheduler_ids"] == list(
        SCHEDULER_IDS
    )


def test_retain_trace_requires_boolean():
    raw = _raw_config()
    raw["simulation"]["retain_trace"] = "false"
    with pytest.raises(ConfigError, match="retain_trace must be a boolean"):
        validate_ext1b_config(raw)


def test_timing_stress_requires_retained_semantic_trace():
    raw = _raw_config("v9_3_ext1b3_timing_calibration.yaml")
    raw["simulation"]["retain_trace"] = False
    with pytest.raises(ConfigError, match="TIMING_STRESS requires retained"):
        validate_ext1b_config(raw)


def test_b1_b2_retained_trace_configs_remain_valid():
    for name in (
        "v9_3_ext1b1_energy_calibration.yaml",
        "v9_3_ext1b2_sync_calibration.yaml",
    ):
        config = load_ext1b_config(ROOT / "configs" / name)
        assert config["simulation"]["retain_trace"] is True


@pytest.mark.parametrize(
    "path,value",
    [
        (("scenario", "interpolation_rho"), "1/3"),
        (("scenario", "structural_retry_limit"), 5),
        (("simulation", "horizon"), 59),
        (("grid", "base_seed"), 999),
        (("statistics", "bootstrap_seed"), 999),
        (("statistics", "top_m"), 1),
    ],
)
def test_config_hash_is_sensitive_to_semantic_fields(path, value):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    changed = deepcopy(config)
    changed[path[0]][path[1]] = value
    assert ext1b_config_hash(config) != ext1b_config_hash(changed)


def test_seed_spaces_and_seeds_are_separate():
    smoke = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    pilot = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_pilot.yaml")
    assert {smoke["seed_space"], pilot["seed_space"]} == {
        "EXT1B_SMOKE", "EXT1B_PILOT",
    }
    assert smoke["grid"]["base_seed"] != pilot["grid"]["base_seed"]


@pytest.mark.parametrize(
    "field",
    ["taskset_hash", "trace_hash", "initial_battery", "battery_capacity"],
)
def test_pairing_fails_closed_on_every_primary_fairness_field(field):
    rows = _fair_rows()
    assert_ext1b_fair_pairing(rows)
    rows[-1][field] = "different"
    with pytest.raises(RuntimeError, match="pairing mismatch|fairness mismatch"):
        assert_ext1b_fair_pairing(rows)


def test_pairing_requires_exactly_nine_unique_schedulers():
    assert len(_fair_rows()) == 9
    with pytest.raises(RuntimeError, match="missing/duplicate"):
        assert_ext1b_fair_pairing(_fair_rows()[:-1])


def test_scheduler_id_is_not_part_of_generation_seed():
    assert {row["generation_seed"] for row in _fair_rows()} == {17}


def test_b1_predicate_uses_actual_priority_and_power():
    initial, structure = bypass_structure(_payload(), Fraction(1, 2))
    assert structure["high_priority_rank"] < structure["low_priority_rank"]
    assert Fraction(structure["low_unit_energy"]) < Fraction(structure["high_unit_energy"])
    assert Fraction(structure["low_unit_energy"]) <= initial < Fraction(structure["high_unit_energy"])


def test_b1_power_profile_uses_priority_rank_not_input_order():
    shuffled = [_payload()[2], _payload()[0], _payload()[1]]
    transformed = _apply_power_profile(
        shuffled,
        (("low", Fraction(1)), ("middle", Fraction(2)), ("high", Fraction(3))),
        "HIGH_PRIORITY_HIGH_POWER",
    )
    by_rank = {row["priority_rank"]: row for row in transformed}
    assert by_rank[0]["workload"] == "high"
    assert by_rank[2]["workload"] == "low"


def test_exact_interpolation_never_rounds_to_upper_boundary():
    value = interpolate_exact(Fraction(1, 10), Fraction(1, 3), Fraction(2, 3))
    assert value == Fraction(23, 90)
    assert Fraction(1, 10) <= value < Fraction(1, 3)


def test_native_energy_epsilon_boundaries_match_constructor_predicates():
    required = Fraction(1)
    assert native_energy_affordable(required, required)
    assert native_energy_affordable(required - NATIVE_ENERGY_EPSILON_J, required)
    initial, structure = bypass_structure(_payload(), Fraction(1, 2))
    assert native_energy_affordable(initial, Fraction(structure["low_unit_energy"]))
    assert not native_energy_affordable(initial, Fraction(structure["high_unit_energy"]))


def test_b1_rejects_missing_power_antagonism():
    payload = [{**row, "P": str(index + 1)} for index, row in enumerate(_payload())]
    with pytest.raises(StructuralRejection, match="NO_PRIORITY_POWER_ANTAGONISM"):
        bypass_structure(payload, Fraction(1, 2))


def test_b1_construction_is_deterministic():
    assert bypass_structure(_payload(), Fraction(1, 2)) == bypass_structure(
        deepcopy(_payload()), Fraction(1, 2)
    )


def test_b2_prefix_interval_and_capacity_floor():
    initial, structure = sync_batch_structure(_payload(), 2, 1, Fraction(1, 2))
    prefix, batch = Fraction(structure["E_prefix"]), Fraction(structure["E_batch"])
    assert prefix <= initial < batch
    assert Fraction(structure["E_init_materialized"]) == initial
    assert structure["ready_job_count"] >= structure["q"] == 2
    assert native_energy_affordable(initial, prefix)
    assert not native_energy_affordable(initial, batch)
    capacity = initial
    assert capacity >= initial


@pytest.mark.parametrize("p", [0, 2, 3])
def test_b2_rejects_illegal_p_q(p):
    with pytest.raises(StructuralRejection, match="INVALID_AFFORDABLE_PREFIX"):
        sync_batch_structure(_payload(), 2, p, Fraction(1, 2))


def test_b2_rejects_insufficient_ready_jobs_and_top_m_smaller_than_two():
    delayed = deepcopy(_payload())
    delayed[1]["arrival_offset"] = 1
    delayed[2]["arrival_offset"] = 1
    with pytest.raises(
        StructuralRejection, match="INSUFFICIENT_READY_JOBS_FOR_TOP_M"
    ):
        sync_batch_structure(delayed, 2, 1, Fraction(1, 2))
    with pytest.raises(StructuralRejection, match="ACTIVE_TOP_M_TOO_SMALL"):
        sync_batch_structure(_payload(), 1, 1, Fraction(1, 2))


def test_b2_rejects_non_strict_or_native_epsilon_collapsed_interval():
    equal = deepcopy(_payload()[:2])
    equal[1]["P"] = "0"
    with pytest.raises(StructuralRejection, match="NON_STRICT_ENERGY_INTERVAL"):
        sync_batch_structure(equal, 2, 1, Fraction(1, 2))

    collapsed = deepcopy(_payload()[:2])
    collapsed[0]["P"] = "1"
    collapsed[1]["P"] = "1/2000000000"
    with pytest.raises(StructuralRejection, match="NON_STRICT_ENERGY_INTERVAL"):
        sync_batch_structure(collapsed, 2, 1, Fraction(1, 2))


def test_b2_top_q_is_selected_by_priority_not_input_order():
    shuffled = [_payload()[2], _payload()[0], _payload()[1]]
    _, structure = sync_batch_structure(shuffled, 2, 1, Fraction(1, 2))
    assert structure["top_q_task_ids"] == ["0", "1"]
    assert structure["q"] == 2


def test_b3_deadlines_obey_c_le_d_le_t_and_ratio_interval():
    transformed = transform_constrained_deadlines(
        _payload(), Fraction(1, 2), Fraction(3, 4), 44
    )
    assert all(row["C"] <= row["D"] <= row["T"] for row in transformed)
    assert all(Fraction(1, 2) <= Fraction(row["D"], row["T"]) <= Fraction(3, 4)
               for row in transformed)


def test_b3_deadline_transform_is_reproducible_and_seeded():
    left = transform_constrained_deadlines(_payload(), Fraction(1, 2), Fraction(1), 7)
    right = transform_constrained_deadlines(_payload(), Fraction(1, 2), Fraction(1), 7)
    assert left == right


def test_b3_contains_both_required_timing_cells():
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b3_smoke.yaml")
    cells = scenario_cells(config)
    assert {cell.subtype for cell in cells} == {
        "POSITIVE_SLACK_ENERGY_AVAILABLE", "SLACK_LIMITED_CHARGING",
    }
    assert cells[0].deadline_ratio_min > cells[1].deadline_ratio_min


def test_scenario_system_preserves_valid_flow_style_power_model(tmp_path):
    path = materialize_scenario_system(
        ROOT / "system_config_unified_template.yml",
        tmp_path / "system.yaml",
        processors=2,
        base_harvesting_rate_w=Fraction(3, 20),
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["cpu_islands"][0]["numcpus"] == 2
    assert document["energy_management"]["use_real_solar_data"] is False
    assert document["power_models"][0]["params"][0]["speed_params"] == [1, 0, 0, 0]


def test_timing_activation_uses_dedicated_audit_evidence():
    rows = _result_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    for row in rows:
        row["timing_activation"] = True
    tasks = _task_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    activations = classify_mechanism_activation(rows, tasks, [], top_m=2)
    assert len(activations) == 3
    assert all(row["runtime_activation"] for row in activations)
    assert all(row["activation_class"].startswith("C1_") for row in activations)


def test_first_execution_difference_is_not_a_timing_activation_proxy():
    rows = _result_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    tasks = _task_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    activations = classify_mechanism_activation(rows, tasks, [], top_m=2)
    assert all(row["runtime_observable"] is False for row in activations)
    assert all(row["activation_class"] == "B_RUNTIME_UNOBSERVABLE" for row in activations)


def _write_mechanism_trace(path: Path, scheduler: str, events):
    document = trace_document()
    document["configured_scheduler"] = scheduler
    document["events"][2:2] = events
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_trace_parser_preserves_bypass_and_sync_activation_events(tmp_path):
    nonblock = parse_simulation_trace(
        _write_mechanism_trace(tmp_path / "nonblock.json", "gpfp_asap_nonblock", [{
            "time": 0, "event_type": "nonblock_bypass", "scheduler": "ASAP-NonBlock",
            "blocked_higher_priority_task": "v93_task_0", "bypassed_task": "v93_task_0",
            "blocked_task_unit_energy_mJ": 0.1, "bypassed_task_unit_energy_mJ": 0.1,
            "available_energy_mJ": 0.1, "reason": "lower_priority_bypass_due_to_energy",
        }]),
        [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
          "P": "1/10", "D_over_T": "1/2", "workload": "control",
          "arrival_offset": 0}],
        expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
        minimum_jobs_per_task=1, release_e0=Fraction(1),
        expected_scheduler="gpfp_asap_nonblock",
    )
    sync = parse_simulation_trace(
        _write_mechanism_trace(tmp_path / "sync.json", "gpfp_asap_sync", [
            {
                "time": 1, "event_type": "sync_batch_block", "scheduler": "ASAP-Sync",
                "batch_tasks": [{"task_name": "v93_task_0"}],
                "batch_required_energy_mJ": 0.2, "available_energy_mJ": 0.1,
                "feasible_subset_exists": True, "reason": "sync_batch_energy_insufficient",
            },
            {
                "time": 2, "event_type": "sync_batch_candidate_wait",
                "scheduler": "ASAP-Sync",
                "reason": "continuation_preserved_new_candidate_batch_energy_insufficient",
            },
        ]),
        [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
          "P": "1/10", "D_over_T": "1/2", "workload": "control",
          "arrival_offset": 0}],
        expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
        minimum_jobs_per_task=1, release_e0=Fraction(1),
        expected_scheduler="gpfp_asap_sync",
    )
    assert nonblock.metrics["bypass_count"] == 1
    # The additive continuation-wait event is intentionally not folded into
    # the legacy sync_batch_block aggregate.
    assert sync.metrics["synchronization_wait_ticks"] == 1


def test_trace_parser_matches_current_st_begin_hold_release_semantics(tmp_path):
    result = parse_simulation_trace(
        _write_mechanism_trace(tmp_path / "trace.json", "gpfp_st_block", [
            {"time": tick, "event_type": event_type, "scheduler": "ST-Block",
             "blocked_task": "v93_task_0", "available_energy_mJ": tick,
             "required_energy_mJ": 5, "slack_at_begin": 4,
             **({"release_reason": "battery_full"} if event_type == "st_charge_release" else {})}
            for tick, event_type in ((2, "st_charge_begin"), (3, "st_charge_hold"),
                                     (4, "st_charge_hold"), (5, "st_charge_release"))
        ]),
        [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
          "P": "1/10", "D_over_T": "1/2", "workload": "control",
          "arrival_offset": 0}],
        expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
        minimum_jobs_per_task=1, release_e0=Fraction(1),
        expected_scheduler="gpfp_st_block",
    )
    assert result.metrics["st_charge_begin_count"] == 1
    assert result.metrics["st_charge_hold_ticks"] == 2
    assert result.metrics["st_charge_release_count"] == 1
    assert result.metrics["st_charge_release_reasons"] == ["battery_full"]


def test_malformed_mechanism_event_fails_closed(tmp_path):
    with pytest.raises(Exception, match="scheduler/applicability mismatch"):
        parse_simulation_trace(
            _write_mechanism_trace(
                tmp_path / "bad.json", "gpfp_asap_nonblock",
                [{"time": 0, "event_type": "nonblock_bypass"}],
            ),
            [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
              "P": "1/10", "D_over_T": "1/2", "workload": "control",
              "arrival_offset": 0}],
            expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
            minimum_jobs_per_task=1, release_e0=Fraction(0),
            expected_scheduler="gpfp_asap_nonblock",
        )


def test_mechanism_classification_distinguishes_b_c1_and_c2():
    rows = _result_rows()
    c1 = classify_mechanism_activation(rows, _task_rows(), [], top_m=2)
    assert c1[0]["activation_class"] == "C1_RUNTIME_ACTIVATED_OUTCOME_SAME"
    rows[1]["status"] = "SIM_DEADLINE_MISS"
    rows[1]["overall_success"] = False
    c2 = classify_mechanism_activation(rows, _task_rows(), [], top_m=2)
    assert c2[0]["activation_class"] == "C2_RUNTIME_ACTIVATED_OUTCOME_DIFFERENT"
    rejected = classify_mechanism_activation([], [], [{
        "attempt_status": "REJECTED", "scenario_kind": "BYPASS_STRESS",
        "scenario_subtype": "B1", "scenario_cell_id": "cell",
        "rejection_code": "NO_PRIORITY_POWER_ANTAGONISM",
        "rejection_detail": "fixture",
    }], top_m=2)
    assert rejected[0]["activation_class"] == "A_STRUCTURAL_REJECTED"


def test_mechanism_classification_distinguishes_structural_only_and_unobservable():
    structural_only = _result_rows()
    next(
        row for row in structural_only
        if row["scheduler_id"] == "gpfp_asap_nonblock"
    )["bypass_count"] = 0
    classified = classify_mechanism_activation(
        structural_only, _task_rows(), [], top_m=2,
    )
    assert classified[0]["runtime_observable"] is True
    assert classified[0]["runtime_activation"] is False
    assert classified[0]["activation_class"] == "B_STRUCTURAL_ONLY"

    unobservable = _result_rows()
    next(
        row for row in unobservable
        if row["scheduler_id"] == "gpfp_asap_nonblock"
    )["bypass_count"] = UNAVAILABLE
    classified = classify_mechanism_activation(
        unobservable, _task_rows(), [], top_m=2,
    )
    assert classified[0]["runtime_observable"] is False
    assert classified[0]["runtime_activation"] == UNAVAILABLE
    assert classified[0]["activation_class"] == "B_RUNTIME_UNOBSERVABLE"


@pytest.mark.parametrize(
    "left_status,right_status,expected",
    [
        ("SIM_PASS_OBSERVED", "SIM_PASS_OBSERVED", "TIE"),
        ("SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS", "LEFT_WIN"),
        ("SIM_DEADLINE_MISS", "SIM_PASS_OBSERVED", "RIGHT_WIN"),
        ("SIM_DEADLINE_MISS", "SIM_DEADLINE_MISS", "TIE"),
    ],
)
def test_overall_relation_covers_all_terminal_outcome_pairs(
    left_status, right_status, expected,
):
    left = {"status": left_status, "comparison_eligible": True}
    right = {"status": right_status, "comparison_eligible": True}
    assert _overall_relation(left, right) == expected


def test_nonterminal_outcomes_are_neither_pass_miss_nor_ties():
    assert _overall_relation(
        {"status": "SIM_RUNTIME_TIMEOUT"},
        {"status": "SIM_RUNTIME_TIMEOUT"},
    ) == "NOT_COMPARABLE"


def test_comparison_ineligible_terminal_outcomes_are_not_ties():
    assert _overall_relation(
        {"status": "SIM_PASS_OBSERVED", "comparison_eligible": False},
        {"status": "SIM_PASS_OBSERVED", "comparison_eligible": True},
    ) == "NOT_COMPARABLE"
    assert _overall_relation(
        {"status": "SIM_HORIZON_INSUFFICIENT"},
        {"status": "SIM_DEADLINE_MISS"},
    ) == "NOT_COMPARABLE"


def test_aggregation_has_explicit_denominators_and_not_comparable():
    results = _result_rows()
    results[0]["status"] = "SIM_RUNTIME_TIMEOUT"
    results[0]["overall_success"] = UNAVAILABLE
    tables = aggregate_ext1b_rows(
        _requests(), results, _task_rows(), [], top_m=2,
        bootstrap_seed=3, bootstrap_resamples=20,
    )
    primary = next(
        row for row in tables["scheduler_summary"]
        if row["scheduler_id"] == SCHEDULER_IDS[0]
    )
    assert primary["requested_denominator"] == primary["terminal_denominator"] == 1
    assert primary["valid_terminal_denominator"] == 0
    assert primary["timeout_count"] == 1
    assert primary["pass_count"] == primary["deadline_miss_count"] == 0
    assert primary["not_comparable"] == 8
    assert any(row["plot"] == "first_execution_timeline" for row in tables["plots"])
    scenario = tables["scenario_summary"][0]
    assert scenario["structural_activation_denominator"] == 9
    assert scenario["runtime_activation_denominator"] == 9


def test_incomplete_nine_scheduler_group_is_excluded_from_comparisons():
    tables = aggregate_ext1b_rows(
        _requests(), _result_rows()[:-1], _task_rows(), [], top_m=2,
        bootstrap_seed=3, bootstrap_resamples=20,
    )
    assert tables["activation"] == []
    assert tables["paired"] == []
    assert tables["statistics"] == []
    assert tables["priority_summary"] == []
    assert tables["plots"] == []


def test_aggregation_rejects_result_fairness_mismatch():
    results = _result_rows()
    results[-1]["trace_hash"] = "different"
    with pytest.raises(RuntimeError, match="trace_hash mismatch"):
        aggregate_ext1b_rows(
            _requests(), results, _task_rows(), [], top_m=2,
            bootstrap_seed=3, bootstrap_resamples=20,
        )


def test_aggregation_rejects_duplicate_scheduler_terminal():
    results = _result_rows()
    results.append(deepcopy(results[0]))
    with pytest.raises(RuntimeError, match="duplicate EXT-1B terminal result"):
        aggregate_ext1b_rows(
            _requests(), results, _task_rows(), [], top_m=2,
            bootstrap_seed=3, bootstrap_resamples=20,
        )


def test_ineligible_rows_are_excluded_from_valid_counts_and_statistics():
    results = _result_rows()
    for row in results:
        row["comparison_eligible"] = False
    tables = aggregate_ext1b_rows(
        _requests(), results, _task_rows(), [], top_m=2,
        bootstrap_seed=3, bootstrap_resamples=20,
    )
    assert tables["scenario_summary"][0]["valid_terminal_denominator"] == 0
    assert all(row["overall_relation"] == "NOT_COMPARABLE" for row in tables["paired"])
    assert all(row["paired_count"] == 0 for row in tables["statistics"])


def test_exact_mcnemar_known_value():
    assert exact_mcnemar_p(5, 0) == pytest.approx(0.0625)
    assert exact_mcnemar_p(0, 0) == 1


def test_holm_step_down_known_values():
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_paired_bootstrap_is_reproducible():
    kwargs = {"seed": 19, "resamples": 100, "statistic": "median"}
    assert paired_bootstrap_ci([1, -1, 2], **kwargs) == paired_bootstrap_ci(
        [1, -1, 2], **kwargs
    )


def test_single_pair_bootstrap_is_degenerate_not_unavailable():
    assert paired_bootstrap_ci(
        [2.5], seed=19, resamples=10, statistic="median",
    ) == (2.5, 2.5)


def test_empty_paired_statistics_remain_unavailable_not_zero():
    rows = paired_statistics_rows(
        _result_rows()[:1], bootstrap_seed=1, bootstrap_resamples=10
    )
    binary = next(row for row in rows if row["metric_type"] == "BINARY")
    assert binary["paired_count"] == 0
    assert binary["risk_difference"] == UNAVAILABLE
    assert binary["mcnemar_exact_p"] == UNAVAILABLE
    assert binary["holm_adjusted_p"] == UNAVAILABLE


def test_b3_statistics_keep_u_cells_separate_and_compare_all_block_families(
    monkeypatch,
):
    schedulers = (
        "gpfp_asap_block", "gpfp_alap_block", "gpfp_st_block",
    )
    rows = []
    for utilization in ("1/5", "2/5"):
        pair_id = f"pair-{utilization}"
        for scheduler in schedulers:
            rows.append({
                "scenario_kind": "TIMING_STRESS",
                "scenario_subtype": "POSITIVE_SLACK_ENERGY_AVAILABLE",
                "scenario_cell_id": "opaque-timing-cell",
                "normalized_utilization": utilization,
                "paired_instance_id": pair_id,
                "scheduler_id": scheduler,
                "status": "SIM_PASS_OBSERVED",
                "comparison_eligible": True,
                "overall_success": True,
                "top_m_success": True,
            })

    sample_sizes = []

    def capture(differences, **_kwargs):
        sample_sizes.append(len(differences))
        return (0.0, 0.0)

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_statistics.paired_bootstrap_ci", capture
    )
    statistics_rows = paired_statistics_rows(
        rows, bootstrap_seed=1, bootstrap_resamples=10,
        scheduler_ids=schedulers,
    )
    binary = [
        row for row in statistics_rows if row["metric_type"] == "BINARY"
    ]
    assert len(binary) == 2 * 2 * 3
    assert {(
        row["primary_scheduler"], row["comparator_scheduler"]
    ) for row in binary} == {
        ("gpfp_asap_block", "gpfp_alap_block"),
        ("gpfp_asap_block", "gpfp_st_block"),
        ("gpfp_alap_block", "gpfp_st_block"),
    }
    assert all(row["paired_count"] == 1 for row in binary)
    assert all(size == 1 for size in sample_sizes)
    assert {row["normalized_utilization"] for row in binary} == {
        "1/5", "2/5",
    }
    assert any("1/5" in row["holm_family"] for row in binary)
    assert any("2/5" in row["holm_family"] for row in binary)


def _outcome_fixture(status: SimulationStatus, missed_rank: int | None):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    runner = Ext1BRunner(config)
    tasks = tuple(_payload())
    instance = SimpleNamespace(tasks=tasks)
    request = {
        "request_id": "request", "paired_instance_id": "pair",
        "scenario_kind": "BYPASS_STRESS", "scenario_subtype": "B1",
        "scenario_cell_id": "cell", "taskset_id": "task",
        "taskset_hash": "hash", "trace_hash": "trace",
        "simulation_config_hash": "sim", "input_hash": "input",
        "scheduler_id": "gpfp_asap_block", "instance": instance,
    }
    observations = tuple(TaskObservation(
        str(rank), 1, 1, int(rank == missed_rank), 0, rank + 1, 1.0, True
    ) for rank in range(3))
    jobs = tuple(JobObservation(
        str(rank), 0, 0, rank + 1, 5, rank + 1,
        rank == missed_rank, rank, 0, 0, 0, 1, True, False, None
    ) for rank in range(3))
    result = SimulationResult(
        status, "fixture", 60, jobs, observations, True, 2.0, {}, 2,
        "gpfp_asap_block", True, "reached_horizon", {},
    )
    execution = SimpleNamespace(
        result=result, runtime_seconds=0.1, attempt_count=1,
        retained_trace_path=None,
    )
    return runner._task_and_request_outcomes(request, execution)


def test_task_outcomes_priority_top_m_and_first_miss_are_correct():
    row, tasks = _outcome_fixture(SimulationStatus.DEADLINE_MISS, 1)
    assert [item["priority_rank"] for item in tasks] == [0, 1, 2]
    assert row["top_m_success"] is False
    assert row["first_missed_priority_rank"] == 1
    assert row["first_missed_priority_rank_numeric"] == 1


def test_no_miss_is_categorical_and_not_an_ordered_numeric_sample():
    row, _ = _outcome_fixture(SimulationStatus.PASS_OBSERVED, None)
    assert row["top_m_success"] is True
    assert row["first_missed_priority_rank"] == "NONE"
    assert row["first_missed_priority_rank_numeric"] == UNAVAILABLE


def test_nonterminal_result_has_unavailable_taskset_endpoints():
    row, _ = _outcome_fixture(SimulationStatus.RUNTIME_TIMEOUT, None)
    assert row["overall_success"] == UNAVAILABLE
    assert row["top_m_success"] == UNAVAILABLE
    assert row["first_missed_priority_rank"] == UNAVAILABLE


def test_terminal_write_is_idempotent_and_conflict_checked(tmp_path):
    result = SimulationResult(
        SimulationStatus.RUNTIME_TIMEOUT, "timeout", 10, (), (), False, None,
        {}, 2, "gpfp_asap_block", False, "timeout", {},
    )
    execution = SimulationExecution(
        "simulation", result, 1.0, 1, (10,), tmp_path / "system",
        tmp_path / "tasks", None, "", "",
    )
    path = tmp_path / "terminal.json"
    write_simulation_terminal(path, execution)
    first = path.read_bytes()
    write_simulation_terminal(path, execution)
    assert path.read_bytes() == first


def test_resume_terminal_identity_is_checked_against_request(tmp_path):
    result = SimulationResult(
        SimulationStatus.RUNTIME_TIMEOUT, "timeout", 10, (), (), False, None,
        {}, 2, "gpfp_asap_block", False, "timeout", {},
    )
    execution = SimulationExecution(
        "wrong-id", result, 1.0, 1, (10,), tmp_path / "system",
        tmp_path / "tasks", None, "", "",
    )
    request = {"request_id": "expected-id", "scheduler_id": "gpfp_asap_block"}
    with pytest.raises(RuntimeError, match="simulation_id mismatch"):
        Ext1BRunner._validate_terminal_identity(request, execution)
    wrong_scheduler = SimulationExecution(
        "expected-id", SimulationResult(
            SimulationStatus.RUNTIME_TIMEOUT, "timeout", 10, (), (), False,
            None, {}, 2, "gpfp_alap_block", False, "timeout", {},
        ), 1.0, 1, (10,), tmp_path / "system", tmp_path / "tasks", None, "", "",
    )
    with pytest.raises(RuntimeError, match="scheduler mismatch"):
        Ext1BRunner._validate_terminal_identity(request, wrong_scheduler)


def test_resume_preserves_deadline_terminal_status(tmp_path):
    result = SimulationResult(
        SimulationStatus.DEADLINE_MISS, "deadline_miss", 16, (), (), True,
        1.0, {}, 2, "gpfp_asap_nonblock", True, "reached_horizon", {},
    )
    execution = SimulationExecution(
        "resume-deadline", result, 1.0, 1, (16,), tmp_path / "system",
        tmp_path / "tasks", None, "", "",
    )
    terminal = tmp_path / "terminal.json"
    write_simulation_terminal(terminal, execution)

    resumed = load_simulation_terminal(terminal)
    Ext1BRunner._validate_terminal_identity({
        "request_id": "resume-deadline",
        "scheduler_id": "gpfp_asap_nonblock",
    }, resumed)
    assert resumed.result.status is SimulationStatus.DEADLINE_MISS
    assert resumed.result.reason == "deadline_miss"


def test_checkpoint_is_atomically_replaceable(tmp_path):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    config["execution"]["output_root"] = str(tmp_path)
    runner = Ext1BRunner(config)
    runner._checkpoint(9, 1)
    runner._checkpoint(9, 2)
    payload = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert payload["terminal"] == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_file_hash_manifest_verifies_and_detects_change(tmp_path):
    (tmp_path / "result.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    write_file_hashes(tmp_path)
    assert verify_file_hashes(tmp_path)
    (tmp_path / "result.csv").write_text("a,b\n1,3\n", encoding="utf-8")
    assert not verify_file_hashes(tmp_path)
    (tmp_path / "file_hashes.sha256").write_text("malformed\n", encoding="utf-8")
    assert not verify_file_hashes(tmp_path)


def test_file_hash_manifest_detects_unlisted_extra_file(tmp_path):
    (tmp_path / "result.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    write_file_hashes(tmp_path)
    (tmp_path / "unexpected.txt").write_text("extra\n", encoding="utf-8")
    assert not verify_file_hashes(tmp_path)
