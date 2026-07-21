"""EXT-1B/B3-v2 target identity and actual-trace contract tests."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.v9_3.ext1b_b3_target_trace import (  # noqa: E402
    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
    B3_V2_PAIRED_INSTANCE_DOMAIN,
    B3_V2_REQUEST_DOMAIN,
    B3_V2_TASKSET_DOMAIN,
    B3_V2_TASKSET_SCHEMA,
    actual_trace_recovery,
)
from experiments.v9_3.ext1b_b3_timing_audit import (  # noqa: E402
    audit_timing_trace,
)
from experiments.v9_3.ext1b_capacity_contract import (  # noqa: E402
    NATIVE_ENERGY_EPSILON_J,
    capacity_feasibility_violations,
)
from experiments.v9_3.config import ConfigError  # noqa: E402
from experiments.v9_3.ext1b_config import (  # noqa: E402
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner  # noqa: E402
from experiments.v9_3.ext1b_generation import (  # noqa: E402
    build_scenario_instance,
    scenario_cells,
)
from experiments.v9_3.simulation_engine import _taskset_document  # noqa: E402
from experiments.v9_3.task_identity import (  # noqa: E402
    runtime_task_name_for_source_id,
)
from experiments.v9_3.taskset_store import (  # noqa: E402
    StoredTaskset,
    TasksetStore,
    TasksetStoreError,
    prepare_service_curve,
)


V1_CONFIG = ROOT / "configs/v9_3_ext1b3_timing_calibration.yaml"
V2_CONFIG = (
    ROOT
    / "configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml"
)
TARGET = "v93_task_0"
OTHER = "v93_task_1"


def _observation(
    task_name: str,
    time: int,
    slack: int,
    *,
    wait: bool,
) -> dict[str, object]:
    remaining = 2
    selected = not wait
    available = 0.0 if wait else 3.0
    return {
        "time": time,
        "event_type": "b3_timing_observation",
        "scheduler": "ST-Block",
        "scheduler_family": "ST",
        "blocking_policy": "BLOCK",
        "task_name": task_name,
        "task_id": task_name,
        "arrival_time": 0,
        "job_id": f"{task_name}@0",
        "remaining_time_ms": float(remaining),
        "rounded_remaining_ms": remaining,
        "absolute_deadline": time + remaining + slack,
        "scheduler_slack": slack,
        "ready": True,
        "timing_gate_open": selected,
        "cpu_available": True,
        "continuation": False,
        "selected": selected,
        "job_required_energy_mJ": 2.0,
        "decision_required_energy_mJ": 2.0,
        "available_energy_mJ": available,
        "job_energy_affordable": not wait,
        "decision_energy_affordable": not wait,
        "native_epsilon_mJ": 1e-6,
        "blocking_policy_reason": "NONE",
        "actual_outcome": "TIMING_DEFERRED" if wait else "DISPATCH_SELECTED",
        "reason_code": "ST_WAIT" if wait else "ST_SELECTED",
    }


def _write_trace(tmp_path: Path, events: list[dict[str, object]]) -> Path:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({
        "trace_schema_version": 2,
        "configured_scheduler": "gpfp_st_block",
        "events": events,
    }), encoding="utf-8")
    return path


def _arrivals(*names: str) -> list[dict[str, object]]:
    return [
        {"time": 0, "event_type": "arrival", "task_name": name,
         "arrival_time": 0}
        for name in names
    ]


def _scheduled(task_name: str, time: int) -> dict[str, object]:
    return {
        "time": time,
        "event_type": "scheduled",
        "task_name": task_name,
        "arrival_time": 0,
    }


def _outcome() -> dict[str, object]:
    return {"event_type": "simulation_run_outcome", "simulation_completed": True}


def test_source_task_id_runtime_name_uses_the_materializer_authority():
    assert runtime_task_name_for_source_id("0") == "v93_task_0"
    payload = [{
        "task_id": "0", "priority_rank": 0, "C": 1, "D": 2, "T": 3,
        "workload": "control", "arrival_offset": 0,
    }]
    assert _taskset_document(payload)["taskset"][0]["name"] == "v93_task_0"
    with pytest.raises(ValueError):
        runtime_task_name_for_source_id(" 0")


def test_non_target_transition_cannot_activate_the_target(tmp_path):
    events = _arrivals(TARGET, OTHER) + [
        _observation(TARGET, 0, 8, wait=True),
        _observation(OTHER, 0, 8, wait=True),
        _observation(OTHER, 1, 7, wait=False),
        _scheduled(OTHER, 1),
        _outcome(),
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
    )
    assert report.timing_activation is True
    assert report.target_wait_observed is True
    assert report.target_positive_slack_transition is False
    assert report.non_target_positive_transition_count == 1
    assert report.activation_from_other_job_only is True
    assert report.target_audit_closed is True


def test_target_wait_then_positive_slack_execution_activates(tmp_path):
    events = _arrivals(TARGET) + [
        _observation(TARGET, 0, 8, wait=True),
        _observation(TARGET, 2, 6, wait=False),
        _scheduled(TARGET, 2),
        _outcome(),
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
    )
    assert report.target_positive_slack_transition is True
    assert report.target_transition_after_slack_exhaustion is False
    assert report.activation_from_other_job_only is False


def test_target_execution_only_at_zero_slack_does_not_activate(tmp_path):
    events = _arrivals(TARGET) + [
        _observation(TARGET, 0, 8, wait=True),
        _observation(TARGET, 8, 0, wait=False),
        _scheduled(TARGET, 8),
        {"time": 9, "event_type": "end_instance", "task_name": TARGET,
         "arrival_time": 0},
        _outcome(),
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
    )
    assert report.timing_activation is False
    assert report.target_positive_slack_transition is False
    assert report.target_transition_after_slack_exhaustion is True
    assert report.target_terminated_without_transition is False


@pytest.mark.parametrize("terminal_event", ["dline_miss", "killed"])
def test_target_wait_then_terminal_without_transition_does_not_activate(
    tmp_path, terminal_event,
):
    events = _arrivals(TARGET) + [
        _observation(TARGET, 0, 8, wait=True),
        {"time": 2, "event_type": terminal_event, "task_name": TARGET,
         "arrival_time": 0},
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
    )
    assert report.timing_activation is False
    assert report.target_positive_slack_transition is False
    assert report.target_terminated_without_transition is True
    assert report.target_audit_closed is True


def test_actual_trace_tick_order_and_native_epsilon_boundary():
    target = Fraction(1)
    at_epsilon = actual_trace_recovery(
        [Fraction(1)],
        initial_energy=target - NATIVE_ENERGY_EPSILON_J,
        battery_capacity=target,
        target_unit_energy=target,
        target_initial_slack=3,
        recovery_margin_ticks=0,
    )
    assert at_epsilon.affordable_tick == at_epsilon.full_tick == 0
    assert at_epsilon.predicate_satisfied is False

    below_epsilon = actual_trace_recovery(
        [Fraction(2, 10**6), Fraction(1)],
        initial_energy=target - NATIVE_ENERGY_EPSILON_J - Fraction(2, 10**6),
        battery_capacity=target,
        target_unit_energy=target,
        target_initial_slack=3,
        recovery_margin_ticks=0,
    )
    assert below_epsilon.affordable_tick == below_epsilon.full_tick == 1
    assert below_epsilon.predicate_satisfied is True


@pytest.mark.parametrize("slack,expected", [(4, False), (5, True)])
def test_full_tick_recovery_margin_strict_boundary(slack, expected):
    recovery = actual_trace_recovery(
        [Fraction(1), Fraction(1), Fraction(1)],
        initial_energy=Fraction(0),
        battery_capacity=Fraction(3),
        target_unit_energy=Fraction(1),
        target_initial_slack=slack,
        recovery_margin_ticks=1,
    )
    assert recovery.affordable_tick == 1
    assert recovery.full_tick == 3
    assert recovery.predicate_satisfied is expected


def _stored_taskset(config, tmp_path: Path) -> StoredTaskset:
    energy = str(config["generation"]["workload_contract"]["power_model"][0][
        "energy_per_tick"
    ])
    tasks = tuple({
        "task_id": str(index), "source_name": f"source-{index}",
        "priority_rank": index, "C": 1, "D": 100, "T": 100,
        "P": energy, "D_over_T": "1", "workload": "bzip2",
        "arrival_offset": 0,
    } for index in range(2))
    return StoredTaskset(
        "source", "generation", 0, 123, "a" * 64, "b" * 64, "c" * 64,
        Fraction(1, 5), Fraction(1, 50), 2, 2, "constrained", tuple(), tasks,
        0.0, "service", tmp_path / "source.json",
    )


def test_build_uses_actual_trace_not_nominal_rate(tmp_path, monkeypatch):
    config = load_ext1b_config(V2_CONFIG)
    cell = next(
        cell for cell in scenario_cells(config)
        if cell.subtype == "SLACK_LIMITED_CHARGING"
    )

    def delayed_actual_trace(_path, horizon, *, target_trace_v2=False):
        assert target_trace_v2 is True
        return (tuple([Fraction(0), Fraction(0), Fraction(100)]
                      + [Fraction(0)] * (horizon - 3)), "d" * 64)

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_generation.actual_trace_material",
        delayed_actual_trace,
    )
    instance = build_scenario_instance(
        _stored_taskset(config, tmp_path), config, cell,
        logical_taskset_index=0, attempt_index=0,
        system_root=tmp_path / "systems",
    )
    assert instance.structure["actual_trace_affordable_tick"] == 3
    assert instance.structure["actual_trace_full_tick"] == 3
    assert instance.structure["predicate_satisfied"] is True
    assert instance.structure["target_source_task_id"] == "0"
    assert instance.structure["target_runtime_task_name"] == TARGET


def test_v2_config_and_identity_domains_are_isolated():
    v1 = load_ext1b_config(V1_CONFIG)
    v2 = load_ext1b_config(V2_CONFIG)
    assert v1["parameter_status"] == "PILOT"
    assert v2["parameter_status"] == "CALIBRATION"
    assert v1["experiment_id"] != v2["experiment_id"]
    assert v1["seed_space"] != v2["seed_space"]
    assert v1["grid"]["base_seed"] != v2["grid"]["base_seed"]
    assert v1["execution"]["output_root"] != v2["execution"]["output_root"]
    assert v1["execution"]["taskset_store"] != v2["execution"]["taskset_store"]
    assert v2["scenario"]["scenario_contract_id"] == (
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    )
    assert B3_V2_TASKSET_SCHEMA.endswith("V4")
    assert B3_V2_TASKSET_DOMAIN.endswith(":v4")
    assert B3_V2_PAIRED_INSTANCE_DOMAIN.endswith(":v3")
    assert B3_V2_REQUEST_DOMAIN.endswith(":v3")


def test_v2_calibration_interpolation_candidates_remain_strict():
    raw = yaml.safe_load(V2_CONFIG.read_text(encoding="utf-8"))
    raw["scenario"]["calibration_grid"]["interpolation_rhos"] = [
        "1/2", "1",
    ]
    with pytest.raises(ConfigError, match="0 < value < 1"):
        validate_ext1b_config(raw)


def test_v1_store_cannot_be_reused_by_v2(tmp_path):
    v1 = load_ext1b_config(V1_CONFIG)
    v1["grid"]["tasksets_per_cell"] = 1
    store_root = tmp_path / "store"
    service1 = prepare_service_curve(v1, tmp_path / "service-v1")
    TasksetStore(store_root, v1, service1)

    v2 = load_ext1b_config(V2_CONFIG)
    v2["grid"]["tasksets_per_cell"] = 1
    service2 = prepare_service_curve(v2, tmp_path / "service-v2")
    with pytest.raises(TasksetStoreError):
        TasksetStore(store_root, v2, service2)


def test_plan_persists_v2_contract_capacity_and_outcome_independent_indices(
    tmp_path,
):
    config = load_ext1b_config(V2_CONFIG)
    config["execution"]["output_root"] = str(tmp_path / "plan")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    config["simulation"]["simulator_bin"] = str(tmp_path / "not-invoked")
    outcome = Ext1BRunner(config).materialize_plan(max_cells=2, max_tasksets=1)
    assert outcome.paired_instances == 2

    def rows(name: str) -> list[dict[str, str]]:
        import csv
        with (tmp_path / "plan" / name).open(
            "r", encoding="utf-8", newline="",
        ) as handle:
            return list(csv.DictReader(handle))

    instances = rows("scenario_instances.csv")
    generated = rows("generated_tasksets.csv")
    requests = rows("simulation_requests.csv")
    attempts = rows("generation_attempts.csv")
    assert {row["scenario_contract_id"] for row in instances} == {
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    }
    assert {row["scenario_contract_id"] for row in generated} == {
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    }
    assert {row["scenario_contract_id"] for row in requests} == {
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    }
    for row in instances:
        tasks = json.loads(next(
            item["task_input_json"] for item in generated
            if item["taskset_id"] == row["taskset_id"]
        ))
        assert not capacity_feasibility_violations(
            tasks, Fraction(row["battery_capacity"]), config
        )
        structure = json.loads(row["structure_json"])
        assert structure["target_runtime_task_name"] == (
            runtime_task_name_for_source_id(structure["target_source_task_id"])
        )
    retry_limit = config["scenario"]["structural_retry_limit"]
    assert all(
        int(row["source_taskset_index"])
        == int(row["logical_taskset_index"]) * retry_limit
        + int(row["attempt_index"])
        for row in attempts
    )
    assert not any(
        "status" in key.lower() and key != "attempt_status"
        for key in attempts[0]
    )
    canonical = json.loads(Path(generated[1]["canonical_taskset_json"]).read_text())
    assert canonical["schema"] == B3_V2_TASKSET_SCHEMA
    assert canonical["scenario_contract_id"] == (
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    )


def test_resume_wrong_contract_or_store_fails_closed(tmp_path):
    v2 = load_ext1b_config(V2_CONFIG)
    v2["execution"]["output_root"] = str(tmp_path / "run")
    v2["execution"]["taskset_store"] = str(tmp_path / "store-v2")
    Ext1BRunner(v2)._initialize(resume=False)

    v1 = load_ext1b_config(V1_CONFIG)
    v1["execution"]["output_root"] = str(tmp_path / "run")
    with pytest.raises(RuntimeError, match="resume config hash mismatch"):
        Ext1BRunner(v1)._initialize(resume=True)

    wrong_store = deepcopy(v2)
    wrong_store["execution"]["taskset_store"] = str(tmp_path / "wrong-store")
    with pytest.raises(RuntimeError, match="resume config hash mismatch"):
        Ext1BRunner(wrong_store)._initialize(resume=True)
