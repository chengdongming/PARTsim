from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.rta4_core3_calibration_v6 import (
    freeze_calibration_v6,
    materialize_calibration_campaigns_v6,
    summarize_calibration_v6,
)
from experiments.v9_3.rta4_core3_experiment1_audit_v6 import (
    EXPERIMENT1_E0_V6,
    EXPERIMENT1_METHODS_V6,
    RTA4Core3Experiment1AuditV6Error,
    _core3_result_material,
    audit_core3_against_experiment1_v6,
    load_experiment1_rta_v6,
)
from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_formal_config_v5 import (
    CORE3_RESULT_DOMAIN_V6,
    CORE3_RESULT_SCHEMA_V6,
    RTA4FormalConfigV5Error,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
)
from experiments.v9_3.rta4_formal_plan_v5 import iter_formal_plan_v5
from experiments.v9_3.rta4_local_execution_v5 import (
    _exact_piecewise_system_v5,
    write_core3_job_observations_v6,
)
from experiments.v9_3.simulation_result import (
    CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6,
    SimulationStatus,
    SimulationTraceError,
    conditional_release_coverage_v6,
    parse_simulation_trace,
)


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_CONFIG = (
    ROOT / "configs/v9_3_rta4_core3_calibration_v6_UNAUTHORIZED.yaml"
)


def _calibration_raw():
    return yaml.safe_load(CALIBRATION_CONFIG.read_text(encoding="utf-8"))


def _v6_campaign():
    return deepcopy(_calibration_raw()["base_campaign"])


def _normalized(raw=None):
    return normalize_rta4_campaign_v5(raw or _v6_campaign())


def _records(raw=None):
    normalized = _normalized(raw)
    return list(iter_formal_plan_v5(
        normalized["normalized_scientific_config"],
        normalized["task_sources"],
        normalized["service_curve"],
    ))


def test_physical_initial_energy_is_independent_and_effective_material_is_hashed():
    raw = _v6_campaign()
    first = _normalized(raw)
    record = _records(raw)[0]
    effective = record.material["effective_core3_simulation_material"]
    assert record.material["v3_grid_material"]["physical_initial_energy"] == "34"
    assert effective["physical_initial_energy"] == "0"
    assert effective["battery_capacity"] == "1000000000"
    changed = deepcopy(raw)
    changed["physical_initial_energy"] = "1"
    second = _normalized(changed)
    assert rta4_formal_config_hash_v5(
        first["normalized_scientific_config"]
    ) != rta4_formal_config_hash_v5(second["normalized_scientific_config"])
    assert _records(raw)[0].mathematical_request_id != _records(changed)[0].mathematical_request_id


def test_physical_initial_energy_zero_is_written_to_actual_system(tmp_path):
    projection = {
        "simulation_projection_identity": "p" * 64,
        "segments": [{"start_ms": 0, "end_ms": 1, "power_w": "0"}],
    }
    path = _exact_piecewise_system_v5(
        ROOT / "system_config_unified_template.yml", tmp_path,
        processors=4, scheduler="gpfp_asap_block",
        initial_energy=Fraction(0), max_energy=Fraction(100),
        simulation_projection=projection,
        simulator_compatible_lists=True,
    )
    system = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert system["energy_management"]["initial_energy"] == 0


def test_v6_finite_capacity_grid_is_cartesian_without_mutating_v3_evidence():
    raw = _v6_campaign()
    records = _records(raw)
    finite = [
        record for record in records
        if record.material["effective_core3_simulation_material"]["track"]
        == "FINITE_BATTERY_EMPIRICAL"
    ]
    expected = {
        (capacity, release_mode)
        for capacity in raw["finite_battery_capacities"]
        for release_mode in raw["release_modes"]
    }
    assert {
        (
            record.material["effective_core3_simulation_material"][
                "battery_capacity"
            ],
            record.material["effective_core3_simulation_material"][
                "release_mode"
            ],
        )
        for record in finite
    } == expected
    sync = next(
        record for record in finite
        if record.material["effective_core3_simulation_material"]["release_mode"]
        == "SYNC_V1"
    )
    assert sync.material["v3_grid_material"]["release_mode"] == (
        "ASYNC_HASH_PHASE_V1"
    )


@pytest.mark.parametrize("value", ["00", "+0", "0/1", "0.0", "-1"])
def test_noncanonical_or_negative_physical_initial_energy_is_rejected(value):
    raw = _v6_campaign()
    raw["physical_initial_energy"] = value
    with pytest.raises(RTA4FormalConfigV5Error):
        normalize_rta4_campaign_v5(raw)


def test_initial_energy_above_any_capacity_is_rejected():
    raw = _v6_campaign()
    raw["physical_initial_energy"] = "51"
    with pytest.raises(RTA4FormalConfigV5Error, match="exceeds"):
        normalize_rta4_campaign_v5(raw)


def test_horizon_material_is_exact_release_plus_dmax():
    record = _records()[0]
    effective = record.material["effective_core3_simulation_material"]
    assert effective["release_horizon"] == 30000
    assert effective["dmax"] > 0
    assert effective["observation_horizon"] == 30000 + effective["dmax"]
    assert record.material["service_material"]["maximum_length"] == effective[
        "observation_horizon"
    ]


def test_legacy_v5_config_does_not_gain_v6_material_or_identity_fields():
    raw = _v6_campaign()
    for field in (
        "physical_initial_energy", "theorem_battery_capacity",
        "core3_campaign_type",
    ):
        raw.pop(field)
    raw["projection_e0"] = ["34"]
    normalized = _normalized(raw)
    scientific = normalized["normalized_scientific_config"]
    assert "core3_simulation_contract" not in scientific
    assert all(
        "effective_core3_simulation_material" not in record.material
        for record in iter_formal_plan_v5(
            scientific, normalized["task_sources"], normalized["service_curve"],
        )
    )


def test_seven_e0_integer_coverage_and_monotonicity():
    coverage = conditional_release_coverage_v6(
        [33, 34, 36, 40, 41], [str(value) for value in range(34, 41)],
    )
    assert [row["covered_job_count"] for row in coverage] == [4, 3, 3, 2, 2, 2, 2]
    assert all(row["coverage_rate_denominator"] == 5 for row in coverage)
    assert all(
        row["coverage_rate_numerator"] == row["covered_job_count"]
        for row in coverage
    )


def _task_payload():
    return [{
        "task_id": str(index), "priority_rank": index,
        "C": 1, "D": 10, "T": 20 + index,
    } for index in range(10)]


def _schema3_trace():
    energies = [33, 34, 36, 40, 41, 33, 34, 36, 40, 41]
    events = []
    for index, energy in enumerate(energies):
        name = f"v93_task_{index}"
        events.extend([
            {
                "time": index, "event_type": "arrival", "task_name": name,
                "arrival_time": index, "current_energy_mJ": energy * 1000,
                "total_harvested_mJ": 0, "total_consumed_mJ": 0,
            },
            {
                "time": index, "event_type": "release_energy_snapshot",
                "task_name": name, "arrival_time": index,
                "available_energy_mJ": energy * 1000,
                "sampling_stage": "post_harvest_pre_consumption",
                "scheduler": "gpfp_asap_block",
            },
            {
                "time": index, "event_type": "scheduled", "task_name": name,
                "arrival_time": index, "task_unit_energy_mJ": 1000,
            },
            {
                "time": index + 1, "event_type": "end_instance",
                "task_name": name, "arrival_time": index,
            },
        ])
    return {
        "events": events,
        "trace_schema_version": 3,
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "expected_simulation_horizon_ms": 30,
        "observed_simulation_end_ms": 30,
        "release_horizon_ms": 20,
        "observation_horizon_ms": 30,
        "release_cutoff_enabled": True,
        "observation_horizon_reached": True,
        "observability_summary_contract_version": 2,
        "observability_summary_horizon_ms": 30,
        "energy_summary": {
            "offered_energy_j": 10, "credited_energy_j": 9,
            "clipped_energy_j": 1, "consumed_energy_j": 4,
            "battery_min_j": 0, "battery_max_j": 10,
            "battery_final_j": 5, "battery_empty_ticks": 1,
            "battery_full_ticks": 2, "observed_energy_intervals": 30,
        },
    }


def _parse_trace(tmp_path, trace=None, *, theorem=True):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace or _schema3_trace()), encoding="utf-8")
    return parse_simulation_trace(
        path, _task_payload(), expected_taskset_hash="a" * 64,
        horizon=30, warmup=0, minimum_jobs_per_task=0,
        release_e0=Fraction(0), expected_processors=4,
        require_core3_observability=True, release_horizon=20,
        physical_initial_energy=Fraction(0), battery_capacity=Fraction(100),
        conditional_e0=[str(value) for value in range(34, 41)],
        theorem_alignment_track=theorem,
    )


def test_schema3_release_snapshots_jobs_coverage_and_overflow(tmp_path):
    result = _parse_trace(tmp_path)
    assert result.status == SimulationStatus.PASS_OBSERVED
    assert len(result.jobs) == 10
    assert all(job.release_energy_j is not None for job in result.jobs)
    assert all(
        job.release_energy_sampling_stage == "post_harvest_pre_consumption"
        for job in result.jobs
    )
    assert result.metrics["released_job_count"] == 10
    assert result.metrics["classified_job_count"] == 10
    assert result.metrics["unfinished_without_miss_count"] == 0
    assert result.metrics["clipped_energy_j"] == 1
    assert result.metrics["theorem_alignment_valid"] is False
    assert result.metrics["theorem_alignment_failure_reason"] == "ENERGY_OVERFLOW_OBSERVED"


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda t: t["events"].pop(1), "snapshot job sets differ"),
        (lambda t: t["events"].append(deepcopy(t["events"][1])), "duplicate release"),
        (lambda t: t["events"][1].update(sampling_stage="wrong"), "sampling stage"),
        (lambda t: t["events"][1].update(arrival_time=1), "time/release"),
    ],
)
def test_release_snapshot_fail_closed(tmp_path, mutation, match):
    trace = _schema3_trace()
    mutation(trace)
    with pytest.raises(SimulationTraceError, match=match):
        _parse_trace(tmp_path, trace)


def test_deadline_miss_unfinished_is_classified(tmp_path):
    trace = _schema3_trace()
    trace["events"] = [
        event for event in trace["events"]
        if not (
            event.get("task_name") == "v93_task_0"
            and event["event_type"] in {"scheduled", "end_instance"}
        )
    ]
    trace["events"].append({
        "time": 10, "event_type": "dline_miss",
        "task_name": "v93_task_0", "arrival_time": 0,
        "deadline": 10, "remaining_execution_ms": 1,
    })
    result = _parse_trace(tmp_path, trace, theorem=False)
    assert result.status == SimulationStatus.DEADLINE_MISS
    assert result.metrics["unfinished_job_count"] == 1
    assert result.metrics["unfinished_without_miss_count"] == 0
    assert result.metrics["classified_job_count"] == 10


def test_unfinished_without_miss_is_horizon_insufficient(tmp_path):
    trace = _schema3_trace()
    trace["events"] = [
        event for event in trace["events"]
        if not (
            event.get("task_name") == "v93_task_0"
            and event["event_type"] in {"scheduled", "end_instance"}
        )
    ]
    result = _parse_trace(tmp_path, trace, theorem=False)
    assert result.status == SimulationStatus.HORIZON_INSUFFICIENT
    assert result.metrics["unfinished_without_miss_count"] == 1
    assert result.metrics["classified_job_count"] == 9


def test_release_at_hrel_and_missing_energy_summary_fail_closed(tmp_path):
    trace = _schema3_trace()
    for event in trace["events"]:
        if event.get("task_name") == "v93_task_0" and event["event_type"] in {
            "arrival", "release_energy_snapshot", "scheduled",
        }:
            event["time"] = event["arrival_time"] = 20
    with pytest.raises(SimulationTraceError, match="before H_rel"):
        _parse_trace(tmp_path, trace)
    trace = _schema3_trace()
    del trace["energy_summary"]
    with pytest.raises(SimulationTraceError, match="missing energy_summary"):
        _parse_trace(tmp_path, trace)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("offered_energy_j", 11, "offered energy"),
        ("battery_final_j", 6, "battery energy balance"),
        ("battery_max_j", 101, "battery bounds"),
    ],
)
def test_energy_closure_fail_closed(tmp_path, field, value, match):
    trace = _schema3_trace()
    trace["energy_summary"][field] = value
    with pytest.raises(SimulationTraceError, match=match):
        _parse_trace(tmp_path, trace)


def test_legacy_schema2_path_remains_available(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "events": [], "trace_schema_version": 2,
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "expected_simulation_horizon_ms": 10,
        "observed_simulation_end_ms": 10,
    }), encoding="utf-8")
    result = parse_simulation_trace(
        path, _task_payload(), expected_taskset_hash="a" * 64,
        horizon=10, warmup=0, minimum_jobs_per_task=0,
        release_e0=Fraction(0), expected_processors=4,
    )
    assert result.trace_schema_version == 2


def test_sidecar_is_atomic_sha_bound_and_contains_no_terminal_job_array(tmp_path):
    destination = tmp_path / "simulation_job_observations_v6.json"
    rows = [{"task_id": "0", "release_energy_j": 34}]
    binding = write_core3_job_observations_v6(
        destination, execution_identity="e" * 64, jobs=rows,
    )
    assert binding["job_observation_count"] == 1
    assert binding["job_observations_sha256"] == hashlib.sha256(
        destination.read_bytes()
    ).hexdigest()
    assert not list(tmp_path.glob("*.tmp*"))
    assert "job_observations" not in binding


def test_calibration_campaigns_are_paired_and_freeze_binds_summary(tmp_path):
    manifest = materialize_calibration_campaigns_v6(_calibration_raw())
    assert [row["release_horizon"] for row in manifest["campaigns"]] == [30000, 60000]
    assert manifest["taskset_count"] == len(manifest["generation_indices"])
    assert manifest["calibration_task_source_identity"] != manifest[
        "experiment1_task_source_identity"
    ]
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({
        "pairing_complete": True,
        "calibration_summary_identity": "s" * 64,
        "capacity_summary": [
            {"battery_capacity": "50"}, {"battery_capacity": "100"},
        ],
    }), encoding="utf-8")
    frozen = freeze_calibration_v6(
        summary_path, tmp_path / "freeze.json",
        release_horizon=60000, b_low="50", b_high="100",
    )
    assert frozen["calibration_summary_sha256"] == hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()
    assert "AUTOMATIC" in frozen["selection_mode"]


def test_calibration_summary_uses_exact_material_and_paired_differences(tmp_path):
    for horizon, scale in ((30000, 1), (60000, 2)):
        root = tmp_path / f"hrel-{horizon}" / "local_terminal_results_v5"
        root.mkdir(parents=True)
        row = {
            "result_schema_version": CORE3_RESULT_SCHEMA_V6,
            "taskset_identity": "taskset-calibration",
            "track": "FINITE_BATTERY_EMPIRICAL",
            "release_mode": "SYNC_V1", "battery_capacity": "50",
            "release_horizon": horizon,
            "released_job_count": 10 * scale,
            "classified_job_count": 10 * scale,
            "unfinished_without_miss_count": 0,
            "deadline_miss_job_count": scale,
            "conditional_coverage": [{
                "exact_e0": str(e0),
                "coverage_rate_numerator": 8 * scale,
                "coverage_rate_denominator": 10 * scale,
            } for e0 in range(34, 41)],
            "offered_energy_j": str(3 * scale),
            "clipped_energy_j": str(scale),
            "battery_full_ticks": scale,
            "simulation_status": "COMPLETED",
            "result": {"runtime_wall_seconds": f"{scale}/2"},
        }
        (root / "terminal.json").write_text(
            json.dumps(row), encoding="utf-8",
        )
    summary = summarize_calibration_v6(tmp_path)
    assert summary["pairing_complete"] is True
    assert summary["capacity_summary"][0]["overflow_ratio"] == {
        "numerator": 3, "denominator": 9, "display": "1/3",
    }
    assert summary["paired_differences"][0]["delta"][
        "runtime_wall_seconds"
    ] == "1/2"
    assert len(summary["calibration_summary_identity"]) == 64


def _experiment1_rows():
    rows = []
    for taskset_index in range(800):
        taskset = f"taskset-{taskset_index:04d}"
        content = f"{taskset_index:064x}"
        order = f"{taskset_index + 800:064x}"
        task_energy = f"{taskset_index + 1600:064x}"
        for method in EXPERIMENT1_METHODS_V6:
            for e0 in EXPERIMENT1_E0_V6:
                rows.append({
                    "taskset_identity": taskset,
                    "taskset_content_sha256": content,
                    "task_order_sha256": order,
                    "configured_service_identity": "c" * 64,
                    "effective_service_identity": "d" * 64,
                    "task_energy_material_identity": task_energy,
                    "method": method, "exact_e0": e0,
                    "solver_status": "COMPLETED", "taskset_proven": True,
                    "task_results": [{
                        "task_id": "0", "task_proven": True,
                        "candidate_response_time": "10",
                    }],
                })
    return rows


@pytest.fixture(scope="module")
def experiment1_rows():
    return _experiment1_rows()


def test_experiment1_exact_cartesian_product_and_missing_duplicate_fail_closed(
    tmp_path, experiment1_rows,
):
    path = tmp_path / "rta.json"
    path.write_text(json.dumps(experiment1_rows), encoding="utf-8")
    assert len(load_experiment1_rta_v6(tmp_path)) == 22400
    path.write_text(json.dumps(experiment1_rows[:-1]), encoding="utf-8")
    with pytest.raises(RTA4Core3Experiment1AuditV6Error, match="22,400"):
        load_experiment1_rta_v6(tmp_path)
    duplicate = list(experiment1_rows[:-1]) + [experiment1_rows[0]]
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(RTA4Core3Experiment1AuditV6Error, match="duplicate"):
        load_experiment1_rta_v6(tmp_path)


def _write_core3_audit_fixture(root, experiment1_rows, *, deadline_miss=False):
    rta_root = root / "experiment1"
    core3_root = root / "core3"
    rta_root.mkdir()
    core3_root.mkdir()
    (rta_root / "rta.json").write_text(
        json.dumps(experiment1_rows), encoding="utf-8",
    )
    sidecar = core3_root / "simulation_job_observations_v6.json"
    sidecar.write_text(json.dumps({
        "job_observations_schema_version": CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6,
        "execution_identity": "e" * 64,
        "job_observation_count": 1,
        "job_observations": [{
            "task_id": "0", "task_name": "v93_task_0", "job_index": 0,
            "release": 0, "absolute_deadline": 20, "completion": 11,
            "response_time": 11, "deadline_miss": deadline_miss,
            "release_energy_j": 34,
            "release_energy_sampling_stage": "post_harvest_pre_consumption",
            "executed_ticks": 1, "energy_blocked_ticks": 0,
            "processor_wait_ticks": 10, "censored": False,
            "censoring_reason": None,
        }],
    }), encoding="utf-8")
    first = experiment1_rows[0]
    row = {
        "result_schema_version": CORE3_RESULT_SCHEMA_V6,
        "simulation_status": "COMPLETED", "observed_status": "SIM_PASS_OBSERVED",
        "track": "FINITE_BATTERY_EMPIRICAL", "release_mode": "SYNC_V1",
        "battery_model": "FINITE_CAPACITY_EXACT", "battery_capacity": "100",
        "physical_initial_energy": "0", "release_horizon": 20, "dmax": 10,
        "observation_horizon": 30, "release_cutoff_enabled": True,
        "observation_horizon_reached": True, "released_job_count": 1,
        "completed_job_count": 1, "deadline_miss_job_count": int(deadline_miss),
        "unfinished_job_count": 0, "unfinished_without_miss_count": 0,
        "classified_job_count": 1,
        "conditional_coverage": [], "minimum_release_energy_j": 34,
        "maximum_release_energy_j": 34, "mean_release_energy_j": 34,
        "offered_energy_j": 10, "credited_energy_j": 10,
        "clipped_energy_j": 0, "consumed_energy_j": 5,
        "overflow_energy_j": 0, "overflow_ratio_numerator": 0,
        "overflow_ratio_denominator": 10, "battery_min_j": 0,
        "battery_max_j": 10, "battery_final_j": 5,
        "battery_empty_ticks": 0, "battery_full_ticks": 0,
        "observed_energy_intervals": 30, "theorem_alignment_valid": True,
        "theorem_alignment_failure_reason": None,
        "job_observations_relative_path": sidecar.name,
        "job_observations_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
        "job_observation_count": 1,
        "job_observations_schema_version": CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6,
        "task_energy_material_identity": first["task_energy_material_identity"],
        "service_material_identity": "s" * 64,
        "beta_material_identity": "b" * 64, "simulation_tick_ms": 1,
        "simulation_projection_identity": "p" * 64,
        "release_projection_identity": "r" * 64,
        "trace_schema_version": 3, "trace_sha256": "t" * 64,
        "execution_identity": "e" * 64,
        "taskset_identity": first["taskset_identity"],
        "taskset_content_sha256": first["taskset_content_sha256"],
        "task_order_sha256": first["task_order_sha256"],
        "configured_service_identity": first["configured_service_identity"],
        "effective_service_identity": first["effective_service_identity"],
    }
    row["simulation_result_identity"] = domain_hash(
        CORE3_RESULT_DOMAIN_V6, _core3_result_material(row),
    )
    (core3_root / "terminal.json").write_text(json.dumps(row), encoding="utf-8")
    return rta_root, core3_root, sidecar


def test_read_only_pairing_reports_response_and_deadline_violations_and_sidecar_tamper(
    tmp_path, experiment1_rows,
):
    rta_root, core3_root, sidecar = _write_core3_audit_fixture(
        tmp_path, experiment1_rows,
    )
    audit = audit_core3_against_experiment1_v6(rta_root, core3_root)
    assert audit["rta_recomputed"] is False
    assert any(
        row["violation_type"] == "CERTIFIED_RESPONSE_BOUND_EXCEEDED"
        for row in audit["violations"]
    )
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(RTA4Core3Experiment1AuditV6Error, match="SHA-256"):
        audit_core3_against_experiment1_v6(rta_root, core3_root)


def test_read_only_pairing_reports_certified_deadline_miss(tmp_path, experiment1_rows):
    rta_root, core3_root, _ = _write_core3_audit_fixture(
        tmp_path, experiment1_rows, deadline_miss=True,
    )
    audit = audit_core3_against_experiment1_v6(rta_root, core3_root)
    assert any(
        row["violation_type"] == "CERTIFIED_JOB_DEADLINE_MISS"
        for row in audit["violations"]
    )


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("taskset_identity", "taskset-drift", "absent"),
        ("taskset_content_sha256", "f" * 64, "taskset_content_sha256"),
        ("configured_service_identity", "f" * 64, "configured_service_identity"),
    ],
)
def test_read_only_pairing_fails_closed_on_identity_drift(
    tmp_path, experiment1_rows, field, value, match,
):
    rta_root, core3_root, _ = _write_core3_audit_fixture(
        tmp_path, experiment1_rows,
    )
    terminal = core3_root / "terminal.json"
    row = json.loads(terminal.read_text(encoding="utf-8"))
    row[field] = value
    row["simulation_result_identity"] = domain_hash(
        CORE3_RESULT_DOMAIN_V6, _core3_result_material(row),
    )
    terminal.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(RTA4Core3Experiment1AuditV6Error, match=match):
        audit_core3_against_experiment1_v6(rta_root, core3_root)


def test_read_only_pairing_counts_uncovered_jobs_as_inapplicable(
    tmp_path, experiment1_rows,
):
    rta_root, core3_root, sidecar = _write_core3_audit_fixture(
        tmp_path, experiment1_rows,
    )
    sidecar_value = json.loads(sidecar.read_text(encoding="utf-8"))
    sidecar_value["job_observations"][0]["release_energy_j"] = 33
    sidecar.write_text(json.dumps(sidecar_value), encoding="utf-8")
    terminal = core3_root / "terminal.json"
    row = json.loads(terminal.read_text(encoding="utf-8"))
    row["job_observations_sha256"] = hashlib.sha256(
        sidecar.read_bytes()
    ).hexdigest()
    row["simulation_result_identity"] = domain_hash(
        CORE3_RESULT_DOMAIN_V6, _core3_result_material(row),
    )
    terminal.write_text(json.dumps(row), encoding="utf-8")
    audit = audit_core3_against_experiment1_v6(rta_root, core3_root)
    assert sum(row["covered_jobs"] for row in audit["summary"]) == 0
    assert sum(row["rta_inapplicable_jobs"] for row in audit["summary"]) == 28


def test_theorem_overflow_run_is_excluded_from_soundness_statistics(
    tmp_path, experiment1_rows,
):
    rta_root, core3_root, _ = _write_core3_audit_fixture(
        tmp_path, experiment1_rows,
    )
    terminal = core3_root / "terminal.json"
    row = json.loads(terminal.read_text(encoding="utf-8"))
    row.update({
        "track": "THEOREM_ALIGNED",
        "theorem_alignment_valid": False,
        "theorem_alignment_failure_reason": "ENERGY_OVERFLOW_OBSERVED",
    })
    row["simulation_result_identity"] = domain_hash(
        CORE3_RESULT_DOMAIN_V6, _core3_result_material(row),
    )
    terminal.write_text(json.dumps(row), encoding="utf-8")
    audit = audit_core3_against_experiment1_v6(rta_root, core3_root)
    assert sum(
        item["theorem_alignment_invalid_runs"] for item in audit["summary"]
    ) == 28
    assert audit["violations"] == []
