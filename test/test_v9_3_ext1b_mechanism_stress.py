from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
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
    _apply_power_profile,
    StructuralRejection,
    bypass_structure,
    interpolate_exact,
    materialize_scenario_system,
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
            "taskset_hash": "task",
            "trace_hash": "trace",
            "overall_success": True,
            "top_m_success": True,
            "top_m_max_response_time": 2,
            "first_missed_priority_rank_numeric": 3,
            "energy_blocked_ticks": 0,
            "processor_wait_ticks": 0,
            "synchronization_wait_ticks": 1 if mechanism == "SYNC" else 0,
            "bypass_count": 1 if mechanism == "NONBLOCK" else 0,
            "st_charge_begin_count": 0,
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
                "maximum_observed_response_time": 2,
            })
    return rows


def _requests(kind: str = "BYPASS_STRESS", subtype: str = "B1"):
    return [{
        "request_id": row["scheduler_id"],
        "paired_instance_id": "pair",
        "scenario_kind": kind,
        "scenario_subtype": subtype,
        "scenario_cell_id": "cell",
        "scheduler_id": row["scheduler_id"],
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


def test_formal_template_parses_but_runner_refuses_execution():
    runner = Ext1BRunner.from_path(ROOT / "configs/v9_3_ext1b_formal_template.yaml")
    with pytest.raises(RuntimeError, match="not executable"):
        runner.run()


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
    formal = load_ext1b_config(ROOT / "configs/v9_3_ext1b_formal_template.yaml")
    assert {smoke["seed_space"], pilot["seed_space"], formal["seed_space"]} == {
        "EXT1B_SMOKE", "EXT1B_PILOT", "EXT1B_FORMAL",
    }
    assert len({item["grid"]["base_seed"] for item in (smoke, pilot, formal)}) == 3


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
    capacity = initial
    assert capacity >= initial


@pytest.mark.parametrize("p", [0, 2, 3])
def test_b2_rejects_illegal_p_q(p):
    with pytest.raises(StructuralRejection, match="INVALID_AFFORDABLE_PREFIX"):
        sync_batch_structure(_payload(), 2, p, Fraction(1, 2))


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


def test_timing_activation_detects_actual_first_execution_difference():
    rows = _result_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    tasks = _task_rows("TIMING_STRESS", "POSITIVE_SLACK_ENERGY_AVAILABLE")
    activations = classify_mechanism_activation(rows, tasks, [], top_m=2)
    assert len(activations) == 3
    assert all(row["runtime_activation"] for row in activations)
    assert all(row["activation_class"].startswith("C1_") for row in activations)


def _write_mechanism_trace(path: Path):
    document = trace_document()
    document["events"][2:2] = [
        {"time": 0, "event_type": "nonblock_bypass"},
        {"time": 1, "event_type": "sync_batch_block"},
        {"time": 2, "event_type": "st_charge_begin"},
        {"time": 3, "event_type": "st_charge_hold"},
        {"time": 4, "event_type": "st_charge_hold"},
        {"time": 5, "event_type": "st_charge_release", "release_reason": "battery_full"},
    ]
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_trace_parser_preserves_bypass_and_sync_activation_events(tmp_path):
    result = parse_simulation_trace(
        _write_mechanism_trace(tmp_path / "trace.json"),
        [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
          "P": "1/10", "D_over_T": "1/2", "workload": "control",
          "arrival_offset": 0}],
        expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
        minimum_jobs_per_task=1, release_e0=Fraction(1),
    )
    assert result.metrics["bypass_count"] == 1
    assert result.metrics["synchronization_wait_ticks"] == 1


def test_trace_parser_matches_current_st_begin_hold_release_semantics(tmp_path):
    result = parse_simulation_trace(
        _write_mechanism_trace(tmp_path / "trace.json"),
        [{"task_id": "0", "priority_rank": 0, "C": 2, "D": 5, "T": 10,
          "P": "1/10", "D_over_T": "1/2", "workload": "control",
          "arrival_offset": 0}],
        expected_taskset_hash=TRACE_HASH, horizon=10, warmup=0,
        minimum_jobs_per_task=1, release_e0=Fraction(1),
    )
    assert result.metrics["st_charge_begin_count"] == 1
    assert result.metrics["st_charge_hold_ticks"] == 2
    assert result.metrics["st_charge_release_count"] == 1
    assert result.metrics["st_charge_release_reasons"] == ["battery_full"]


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


def test_nonterminal_outcomes_are_neither_pass_miss_nor_ties():
    assert _overall_relation(
        {"status": "SIM_RUNTIME_TIMEOUT"},
        {"status": "SIM_RUNTIME_TIMEOUT"},
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


def test_empty_paired_statistics_remain_unavailable_not_zero():
    rows = paired_statistics_rows(
        _result_rows()[:1], bootstrap_seed=1, bootstrap_resamples=10
    )
    binary = next(row for row in rows if row["metric_type"] == "BINARY")
    assert binary["paired_count"] == 0
    assert binary["risk_difference"] == UNAVAILABLE
    assert binary["mcnemar_exact_p"] == UNAVAILABLE
    assert binary["holm_adjusted_p"] == UNAVAILABLE


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


def test_no_miss_uses_none_and_ordered_numeric_sentinel():
    row, _ = _outcome_fixture(SimulationStatus.PASS_OBSERVED, None)
    assert row["top_m_success"] is True
    assert row["first_missed_priority_rank"] == "NONE"
    assert row["first_missed_priority_rank_numeric"] == 3


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
