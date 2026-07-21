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
    binary64_materialized_text,
    recovery_prefix_affordable_at_capacity,
    runtime_recovery_prefix,
)
from experiments.v9_3.ext1b_b3_timing_audit import (  # noqa: E402
    audit_timing_trace,
)
from experiments.v9_3.ext1b_capacity_contract import (  # noqa: E402
    NATIVE_ENERGY_EPSILON_J,
    capacity_feasibility_violations,
)
from experiments.v9_3.config import ConfigError, canonical_json  # noqa: E402
from experiments.v9_3.ext1b_config import (  # noqa: E402
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import (  # noqa: E402
    GENERATION_ATTEMPT_COLUMNS,
    Ext1BRunner,
    _generation_attempt_diagnostic_fields,
)
from experiments.v9_3.ext1b_generation import (  # noqa: E402
    StructuralRejection,
    _timing_structure,
    build_scenario_instance,
    scenario_cells,
)
from experiments.v9_3.ext1b_observation import (  # noqa: E402
    Ext1BObservationError,
    _b3_dimensions_by_pair,
    _store_manifest_audit,
    _validate_b3_target_identity,
    _v2_identity_audit,
)
from experiments.v9_3.result_writer import (  # noqa: E402
    ResultWriterError,
    write_csv,
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
    arrival_time: int = 0,
    available_energy_mj: float | None = None,
    decision_required_energy_mj: float = 2.0,
) -> dict[str, object]:
    remaining = 2
    selected = not wait
    available = (
        0.0 if wait else 3.0
        if available_energy_mj is None
        else available_energy_mj
    )
    return {
        "time": time,
        "event_type": "b3_timing_observation",
        "scheduler": "ST-Block",
        "scheduler_family": "ST",
        "blocking_policy": "BLOCK",
        "task_name": task_name,
        "task_id": task_name,
        "arrival_time": arrival_time,
        "job_id": f"{task_name}@{arrival_time}",
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
        "decision_required_energy_mJ": decision_required_energy_mj,
        "available_energy_mJ": available,
        "job_energy_affordable": not wait,
        "decision_energy_affordable": not wait,
        "native_epsilon_mJ": 1e-6,
        "blocking_policy_reason": "NONE",
        "actual_outcome": "TIMING_DEFERRED" if wait else "DISPATCH_SELECTED",
        "reason_code": "ST_WAIT" if wait else "ST_SELECTED",
    }


def _write_trace(
    tmp_path: Path,
    events: list[dict[str, object]],
    *,
    recovery: dict[str, object] | None = None,
) -> Path:
    path = tmp_path / "trace.json"
    document = {
        "trace_schema_version": 2,
        "configured_scheduler": "gpfp_st_block",
        "target_runtime_task_name": TARGET,
        "target_arrival_time": 0,
        "target_job_id": f"{TARGET}@0",
        "events": events,
    }
    if recovery is not None:
        document.update(recovery)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _arrivals(*names: str) -> list[dict[str, object]]:
    return [
        {"time": 0, "event_type": "arrival", "task_name": name,
         "arrival_time": 0}
        for name in names
    ]


def _scheduled(
    task_name: str, time: int, arrival_time: int = 0,
) -> dict[str, object]:
    return {
        "time": time,
        "event_type": "scheduled",
        "task_name": task_name,
        "arrival_time": arrival_time,
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
        target_arrival_time=0,
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
        target_arrival_time=0,
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
        target_arrival_time=0,
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
        target_arrival_time=0,
    )
    assert report.timing_activation is False
    assert report.target_positive_slack_transition is False
    assert report.target_terminated_without_transition is True
    assert report.target_audit_closed is True


def test_later_same_task_transition_cannot_activate_initial_target_job(tmp_path):
    events = _arrivals(TARGET) + [
        _observation(TARGET, 0, 8, wait=True),
        {"time": 10, "event_type": "arrival", "task_name": TARGET,
         "arrival_time": 10},
        _observation(TARGET, 10, 8, wait=True, arrival_time=10),
        _observation(TARGET, 12, 6, wait=False, arrival_time=10),
        _scheduled(TARGET, 12, arrival_time=10),
        _outcome(),
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
        target_arrival_time=0,
    )
    assert report.target_wait_observed is True
    assert report.target_positive_slack_transition is False
    assert report.any_target_job_positive_transition_count == 1
    assert report.later_target_job_positive_transition_count == 1


def test_later_same_task_terminal_does_not_terminate_initial_target_job(tmp_path):
    events = _arrivals(TARGET) + [
        _observation(TARGET, 0, 8, wait=True),
        {"time": 10, "event_type": "arrival", "task_name": TARGET,
         "arrival_time": 10},
        {"time": 12, "event_type": "killed", "task_name": TARGET,
         "arrival_time": 10},
        _outcome(),
    ]
    report = audit_timing_trace(
        _write_trace(tmp_path, events),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
        target_arrival_time=0,
    )
    assert report.target_wait_observed is True
    assert report.target_positive_slack_transition is False
    assert report.target_terminated_without_transition is False


def test_trace_target_job_identity_mismatch_fails_closed(tmp_path):
    path = _write_trace(
        tmp_path,
        _arrivals(TARGET) + [_observation(TARGET, 0, 8, wait=True), _outcome()],
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["target_job_id"] = f"{TARGET}@40"
    path.write_text(json.dumps(document), encoding="utf-8")
    report = audit_timing_trace(
        path,
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
        target_arrival_time=0,
    )
    assert report.target_audit_closed is False
    assert "trace target job identity mismatch" in report.errors


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
    # Native binary64 addition remains just below the affordability boundary
    # at tick 1; the exact-rational answer would incorrectly report tick 1.
    assert below_epsilon.affordable_tick == below_epsilon.full_tick == 2
    assert below_epsilon.predicate_satisfied is True


def test_actual_trace_rejection_diagnostics_are_canonical_json(tmp_path):
    import csv

    diagnostics = {
        "actual_trace_full_tick_strictly_before_earliest_initial_deadline": (
            False
        ),
        "actual_trace_target_affordable_tick": 17,
        "predicate": "actual-trace recovery predicate",
        "recovery_earliest_initial_deadline": 19,
    }

    mapped = _generation_attempt_diagnostic_fields(diagnostics)
    row = {"attempt_id": "attempt-1", **mapped}

    assert set(row) <= set(GENERATION_ATTEMPT_COLUMNS)
    assert not (set(diagnostics) & set(row))

    path = tmp_path / "generation_attempts.csv"
    write_csv(path, GENERATION_ATTEMPT_COLUMNS, [row])
    with path.open("r", encoding="utf-8", newline="") as handle:
        persisted = next(csv.DictReader(handle))

    assert persisted["diagnostics_json"] == canonical_json(diagnostics)
    assert json.loads(persisted["diagnostics_json"]) == diagnostics


def test_declared_diagnostic_stays_in_column_and_extra_is_preserved():
    extra_value = {
        "ticks": [3, 5, 8],
        "recovery": {"applicable": True},
    }
    mapped = _generation_attempt_diagnostic_fields({
        "predicate_satisfied": False,
        "future_diagnostic": extra_value,
    })

    assert mapped["predicate_satisfied"] is False
    assert json.loads(mapped["diagnostics_json"]) == {
        "future_diagnostic": extra_value,
    }
    assert mapped["diagnostics_json"] == canonical_json({
        "future_diagnostic": extra_value,
    })
    assert "future_diagnostic" not in mapped
    assert _generation_attempt_diagnostic_fields({
        "predicate_satisfied": True,
    })["diagnostics_json"] == canonical_json({})


def test_generation_attempt_csv_still_rejects_unexpected_top_level_column(
    tmp_path,
):
    with pytest.raises(ResultWriterError, match="unexpected columns"):
        write_csv(
            tmp_path / "generation_attempts.csv",
            GENERATION_ATTEMPT_COLUMNS,
            [{"attempt_id": "attempt-1", "truly_unexpected": "value"}],
        )


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


@pytest.mark.parametrize("earliest_deadline,expected", [(3, False), (4, True)])
def test_full_tick_earliest_initial_deadline_strict_boundary(
    earliest_deadline, expected,
):
    recovery = actual_trace_recovery(
        [Fraction(1), Fraction(1), Fraction(1)],
        initial_energy=Fraction(0),
        battery_capacity=Fraction(3),
        target_unit_energy=Fraction(1),
        target_initial_slack=5,
        recovery_margin_ticks=1,
        earliest_initial_deadline=earliest_deadline,
    )
    assert recovery.full_tick == 3
    assert recovery.predicate_satisfied is expected


def _prefix_tasks() -> tuple[dict[str, object], ...]:
    return (
        {"task_id": "0", "priority_rank": 0, "C": 1, "D": 20,
         "T": 40, "P": "1/1000", "workload": "bzip2",
         "arrival_offset": 0},
        {"task_id": "1", "priority_rank": 1, "C": 1, "D": 21,
         "T": 40, "P": "2/1000", "workload": "control",
         "arrival_offset": 0},
        {"task_id": "2", "priority_rank": 2, "C": 1, "D": 22,
         "T": 60, "P": "3/1000", "workload": "hash",
         "arrival_offset": 0},
    )


def test_binary64_full_capacity_affords_complete_runtime_top_q():
    prefix = runtime_recovery_prefix(
        _prefix_tasks(), 2, initial_energy=Fraction(1, 4_000),
    )
    assert prefix["recovery_prefix_length"] == 2
    assert prefix["recovery_prefix_task_ids"] == ["0", "1"]
    assert prefix["recovery_prefix_runtime_names"] == [TARGET, OTHER]
    assert prefix["recovery_prefix_priority_ranks"] == [0, 1]
    assert prefix["target_blocked_at_initial_energy"] is True
    assert prefix["recovery_prefix_affordable_at_full"] is True
    assert recovery_prefix_affordable_at_capacity(
        prefix, prefix["materialized_battery_capacity"]
    )


def test_capacity_below_prefix_beyond_native_epsilon_is_rejected(
    monkeypatch,
):
    config = load_ext1b_config(V2_CONFIG)
    cell = next(
        item for item in scenario_cells(config)
        if item.subtype == "SLACK_LIMITED_CHARGING"
    )
    real_prefix = runtime_recovery_prefix

    def undersized(tasks, processors, *, initial_energy):
        material = real_prefix(
            tasks, processors, initial_energy=initial_energy,
        )
        required = Fraction(material["recovery_prefix_required_energy"])
        material["materialized_battery_capacity"] = (
            binary64_materialized_text(
                required - 2 * NATIVE_ENERGY_EPSILON_J
            )
        )
        return material

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_generation.runtime_recovery_prefix",
        undersized,
    )
    with pytest.raises(
        StructuralRejection,
        match="RECOVERY_PREFIX_NOT_AFFORDABLE_AT_FULL",
    ):
        _timing_structure(
            _prefix_tasks(), cell, 2, Fraction(1, 2),
            target_trace_v2=True,
        )


def test_target_only_capacity_does_not_satisfy_top_q_recovery():
    prefix = runtime_recovery_prefix(
        _prefix_tasks(), 2, initial_energy=Fraction(1, 4_000),
    )
    target_only_capacity = _prefix_tasks()[0]["P"]
    assert recovery_prefix_affordable_at_capacity(
        prefix, target_only_capacity,
    ) is False


def test_runtime_rm_task_number_tie_break_matches_generated_prefix():
    prefix = runtime_recovery_prefix(
        _prefix_tasks(), 2, initial_energy=Fraction(1, 4_000),
    )
    assert prefix["recovery_prefix_task_ids"] == ["0", "1"]
    assert prefix["recovery_prefix_runtime_names"] == [TARGET, OTHER]
    assert prefix["recovery_earliest_initial_deadline"] == 20


def _recovery_trace_contract() -> dict[str, object]:
    return {
        "target_recovery_contract_applicable": True,
        "recovery_prefix_identity": "f" * 64,
        "recovery_prefix_length": 2,
        "recovery_prefix_runtime_names_json": json.dumps(
            [TARGET, OTHER], separators=(",", ":"),
        ),
        "recovery_prefix_required_energy": "0.004",
        "materialized_battery_capacity": "0.004",
        "actual_trace_target_affordable_tick": 1,
        "actual_trace_full_tick": 2,
    }


def test_full_battery_release_selects_initial_target_and_complete_prefix(
    tmp_path,
):
    events = _arrivals(TARGET, OTHER) + [
        _observation(TARGET, 0, 8, wait=True),
        _observation(
            TARGET, 2, 6, wait=False, available_energy_mj=4.0,
            decision_required_energy_mj=2.0,
        ),
        _observation(
            OTHER, 2, 7, wait=False, available_energy_mj=4.0,
            decision_required_energy_mj=4.0,
        ),
        _scheduled(TARGET, 2),
        _scheduled(OTHER, 2),
        _outcome(),
    ]
    contract = _recovery_trace_contract()
    report = audit_timing_trace(
        _write_trace(tmp_path, events, recovery=contract),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
        target_arrival_time=0,
        target_recovery_contract_applicable=True,
        recovery_prefix_identity=str(contract["recovery_prefix_identity"]),
        recovery_prefix_runtime_names=(TARGET, OTHER),
        recovery_prefix_required_energy="0.004",
        materialized_battery_capacity="0.004",
        actual_trace_full_tick=2,
    )
    assert report.target_positive_slack_transition is True
    assert report.full_release_target_present is True
    assert report.full_release_target_selected is True
    assert report.full_release_prefix_affordable is True
    assert report.runtime_recovery_prefix_matches is True
    assert report.recovery_prefix_audit_closed is True
    assert report.target_audit_closed is True


def test_runtime_prefix_mismatch_fails_closed(tmp_path):
    events = _arrivals(TARGET, OTHER) + [
        _observation(TARGET, 0, 8, wait=True),
        _observation(
            TARGET, 2, 6, wait=False, available_energy_mj=4.0,
        ),
        _observation(OTHER, 2, 7, wait=True),
        _scheduled(TARGET, 2),
        _outcome(),
    ]
    contract = _recovery_trace_contract()
    report = audit_timing_trace(
        _write_trace(tmp_path, events, recovery=contract),
        expected_scheduler="gpfp_st_block",
        target_runtime_task_name=TARGET,
        target_arrival_time=0,
        target_recovery_contract_applicable=True,
        recovery_prefix_identity=str(contract["recovery_prefix_identity"]),
        recovery_prefix_runtime_names=(TARGET, OTHER),
        recovery_prefix_required_energy="0.004",
        materialized_battery_capacity="0.004",
        actual_trace_full_tick=2,
    )
    assert report.recovery_prefix_audit_closed is False
    assert report.target_audit_closed is False
    assert "runtime RM/task-number prefix differs from structure" in report.errors


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
    assert B3_V2_TASKSET_SCHEMA.endswith("V5")
    assert B3_V2_TASKSET_DOMAIN.endswith(":v5")
    assert B3_V2_PAIRED_INSTANCE_DOMAIN.endswith(":v4")
    assert B3_V2_REQUEST_DOMAIN.endswith(":v4")
    assert v2["grid"]["base_seed"] == 981301
    assert v2["statistics"]["bootstrap_seed"] == 9813903


def test_v2_random_and_storage_identities_are_repository_unique():
    v2 = yaml.safe_load(V2_CONFIG.read_text(encoding="utf-8"))
    selectors = (
        ("experiment_id", lambda row: row["experiment_id"]),
        ("seed_space", lambda row: row["seed_space"]),
        ("base_seed", lambda row: row["grid"]["base_seed"]),
        ("bootstrap_seed", lambda row: row["statistics"]["bootstrap_seed"]),
        ("output_root", lambda row: row["execution"]["output_root"]),
        ("taskset_store", lambda row: row["execution"]["taskset_store"]),
    )
    for path in sorted((ROOT / "configs").glob("v9_3_ext1b*.yaml")):
        if path == V2_CONFIG:
            continue
        other = yaml.safe_load(path.read_text(encoding="utf-8"))
        for label, select in selectors:
            assert select(v2) != select(other), (label, path.name)


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
    outcome = Ext1BRunner(config).materialize_plan(max_cells=3, max_tasksets=1)
    assert outcome.paired_instances == 3

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
        assert structure["target_arrival_time"] == 0
        assert structure["target_job_id"] == f"{TARGET}@0"
        assert row["target_runtime_task_name"] == TARGET
        assert int(row["target_arrival_time"]) == 0
        assert row["target_job_id"] == f"{TARGET}@0"
        applicable = structure["target_recovery_contract_applicable"]
        assert (row["target_recovery_contract_applicable"] == "True") is (
            applicable
        )
        if applicable:
            assert structure["recovery_prefix_length"] == min(
                int(row["M"]), structure["initial_ready_job_count"]
            )
            assert structure["recovery_prefix_runtime_names"][0] == TARGET
            assert structure["recovery_prefix_affordable_at_full"] is True
            assert structure["target_blocked_at_initial_energy"] is True
            assert Fraction(row["battery_capacity"]) == Fraction(
                structure["recovery_prefix_required_energy"]
            )
            assert row["recovery_prefix_identity"] == structure[
                "recovery_prefix_identity"
            ]
        else:
            assert structure["recovery_prefix_identity"] == ""
            assert structure["recovery_prefix_length"] == 0
        pair_requests = [
            request for request in requests
            if request["paired_instance_id"] == row["paired_instance_id"]
        ]
        assert len(pair_requests) == 3
        assert all(
            request["target_runtime_task_name"] == TARGET
            and int(request["target_arrival_time"]) == 0
            and request["target_job_id"] == f"{TARGET}@0"
            and request["recovery_prefix_identity"]
            == row["recovery_prefix_identity"]
            and request["recovery_prefix_required_energy"]
            == row["recovery_prefix_required_energy"]
            and request["materialized_battery_capacity"]
            == row["materialized_battery_capacity"]
            for request in pair_requests
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
    dimensions = _b3_dimensions_by_pair(instances)
    first_pair = instances[0]["paired_instance_id"]
    bad_request = deepcopy(next(
        row for row in requests if row["paired_instance_id"] == first_pair
    ))
    bad_request["target_job_id"] = f"{TARGET}@40"
    with pytest.raises(Ext1BObservationError, match="target identity mismatch"):
        _validate_b3_target_identity(dimensions[first_pair], bad_request)

    charging = next(
        row for row in instances
        if row["target_recovery_contract_applicable"] == "True"
    )
    bad_prefix = deepcopy(next(
        row for row in requests
        if row["paired_instance_id"] == charging["paired_instance_id"]
    ))
    bad_prefix["recovery_prefix_identity"] = "0" * 64
    with pytest.raises(Ext1BObservationError, match="target identity mismatch"):
        _validate_b3_target_identity(
            dimensions[charging["paired_instance_id"]], bad_prefix,
        )

    bad_scenario = deepcopy(instances)
    bad_scenario[0]["target_arrival_time"] = "40"
    with pytest.raises(
        Ext1BObservationError,
        match="scenario/structure target identity mismatch",
    ):
        _b3_dimensions_by_pair(bad_scenario)

    store_closed, store_entries = _store_manifest_audit(
        tmp_path / "plan", config,
    )
    request_index = {}
    for request in requests:
        request_index.setdefault(request["paired_instance_id"], {})[
            request["scheduler_id"]
        ] = request
    audits = _v2_identity_audit(
        tmp_path / "plan",
        config,
        instances,
        {row["taskset_id"]: row for row in generated},
        request_index,
        attempts,
        store_closed,
        store_entries,
        True,
    )
    assert all(audits.values())
    unverified_output_audits = _v2_identity_audit(
        tmp_path / "plan",
        config,
        instances,
        {row["taskset_id"]: row for row in generated},
        request_index,
        attempts,
        store_closed,
        store_entries,
        False,
    )
    assert unverified_output_audits[
        "output_file_hash_verification_closed"
    ] is False
    assert unverified_output_audits["hash_audit_closed"] is False
    tampered_generated = deepcopy(generated)
    tampered_generated[0]["taskset_hash"] = "0" * 64
    tampered_audits = _v2_identity_audit(
        tmp_path / "plan",
        config,
        instances,
        {row["taskset_id"]: row for row in tampered_generated},
        request_index,
        attempts,
        store_closed,
        store_entries,
        True,
    )
    assert tampered_audits["identity_shape_audit_closed"] is True
    assert tampered_audits["taskset_hash_audit_closed"] is False
    assert tampered_audits["hash_audit_closed"] is False


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
