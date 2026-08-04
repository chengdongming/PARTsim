from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.rta4_core3_calibration_v6 import (
    CORE3_CALIBRATION_SUMMARY_DOMAIN_V6,
    RTA4Core3CalibrationV6Error,
    freeze_calibration_v6,
    materialize_calibration_campaigns_v6,
    summarize_calibration_v6,
    write_calibration_campaigns_v6,
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
    load_rta4_campaign_v5,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
)
from experiments.v9_3.rta4_formal_plan_v5 import (
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from experiments.v9_3.rta4_local_execution_v5 import (
    LocalResultWriterV5,
    RTA4_LOCAL_RESULT_DOMAIN_V6,
    _exact_piecewise_system_v5,
    _prepared_record_material,
    _terminal_row,
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


def _physical_environment():
    return {
        "execution_backend": "PHYSICAL_CORE_PROCESS_SLOTS",
        "physical_core_binding_required": True,
        "topology_fingerprint": "synthetic-calibration",
        "available_physical_core_count": 1,
        "allowed_logical_cpus": [0],
        "topology_selection_policy": "SYNTHETIC_CALIBRATION_TEST",
        "physical_execution_groups": [{"selected_physical_cores": [{
            "logical_cpu_id": 0, "physical_package_id": 0,
            "physical_core_id": 0,
        }]}],
    }


def _small_calibration_raw():
    raw = _calibration_raw()
    raw["base_campaign"]["source"]["taskset_count"] = 2
    parameters = raw["base_campaign"]["task_source"]["parameters"]
    parameters["taskset_count"] = 2
    parameters["generation_indices"] = [0, 1]
    raw["finite_battery_candidate_capacities"] = ["50", "100"]
    raw["base_campaign"]["finite_battery_capacities"] = ["50", "100"]
    return raw


def _write_calibration_fixture(root: Path):
    config = root / "calibration.yaml"
    config.write_text(
        yaml.safe_dump(_small_calibration_raw(), sort_keys=False),
        encoding="utf-8",
    )
    evidence = root / "evidence"
    manifest = write_calibration_campaigns_v6(config, evidence)
    roots = {}
    pairs = {}
    for item in manifest["campaigns"]:
        horizon = item["release_horizon"]
        campaign = load_rta4_campaign_v5(evidence / item["relative_path"])
        plan = describe_formal_plan_v5(
            campaign.normalized_scientific_config,
            campaign.task_sources, campaign.service_curve,
        )
        records = tuple(iter_formal_plan_v5(
            campaign.normalized_scientific_config,
            campaign.task_sources, campaign.service_curve,
        ))
        run_root = root / f"run-{horizon}"
        roots[horizon] = run_root
        writer = LocalResultWriterV5(
            run_root, campaign=campaign, plan=plan, records=records,
            execution_environment=_physical_environment(), resume=False,
        )
        prepared_cache = {}
        for record in records:
            effective = record.material["effective_core3_simulation_material"]
            cache_key = (
                record.taskset_identity, effective["observation_horizon"],
            )
            if cache_key not in prepared_cache:
                _worker, _certificate, context, _local = _prepared_record_material(
                    campaign, record,
                )
                binding = context.binding_for(record.record_id)
                service = context.service_materials[
                    binding["service_material_identity"]
                ]
                prepared_cache[cache_key] = (binding, service)
            binding, service = prepared_cache[cache_key]
            pair_key = (
                record.taskset_identity, effective["track"],
                effective["release_mode"], str(effective["battery_capacity"]),
            )
            sidecar = (
                run_root / "artifacts" / record.execution_id
                / "simulation_job_observations_v6.json"
            )
            sidecar.parent.mkdir(parents=True)
            sidecar_value = {
                "job_observations_schema_version": (
                    CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6
                ),
                "execution_identity": record.execution_id,
                "job_observation_count": 0,
                "job_observations": [],
            }
            sidecar.write_text(json.dumps(sidecar_value), encoding="utf-8")
            scientific = {
                "result_schema_version": CORE3_RESULT_SCHEMA_V6,
                "simulation_status": "COMPLETED",
                "observed_status": "SIM_PASS_OBSERVED",
                "track": effective["track"],
                "release_mode": effective["release_mode"],
                "battery_model": effective["battery_model"],
                "battery_capacity": str(effective["battery_capacity"]),
                "physical_initial_energy": str(
                    effective["physical_initial_energy"]
                ),
                "release_horizon": int(effective["release_horizon"]),
                "dmax": int(effective["dmax"]),
                "observation_horizon": int(effective["observation_horizon"]),
                "release_cutoff_enabled": True,
                "observation_horizon_reached": True,
                "released_job_count": 0, "completed_job_count": 0,
                "deadline_miss_job_count": 0, "unfinished_job_count": 0,
                "unfinished_without_miss_count": 0, "classified_job_count": 0,
                "conditional_coverage": [],
                "minimum_release_energy_j": "0",
                "maximum_release_energy_j": "0",
                "mean_release_energy_j": "0", "offered_energy_j": "0",
                "credited_energy_j": "0", "clipped_energy_j": "0",
                "consumed_energy_j": "0", "overflow_energy_j": "0",
                "overflow_ratio_numerator": "0",
                "overflow_ratio_denominator": "0", "battery_min_j": "0",
                "battery_max_j": "0", "battery_final_j": "0",
                "battery_empty_ticks": 0, "battery_full_ticks": 0,
                "observed_energy_intervals": effective["observation_horizon"],
                "theorem_alignment_valid": True,
                "theorem_alignment_failure_reason": None,
                "job_observations_relative_path": sidecar.relative_to(
                    run_root
                ).as_posix(),
                "job_observations_sha256": hashlib.sha256(
                    sidecar.read_bytes()
                ).hexdigest(),
                "job_observation_count": 0,
                "job_observations_schema_version": (
                    CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6
                ),
                "task_energy_material_identity": binding[
                    "task_energy_material_identity"
                ],
                "service_material_identity": binding[
                    "service_material_identity"
                ],
                "beta_material_identity": service.beta_material_identity,
                "simulation_tick_ms": record.material["simulation_tick_ms"],
                "simulation_projection_identity": record.material[
                    "service_material"
                ]["simulation_projection"]["simulation_projection_identity"],
                "release_projection_identity": domain_hash(
                    "TEST:RELEASE", {"execution": record.execution_id}
                ),
                "trace_schema_version": 3,
                "trace_sha256": hashlib.sha256(
                    record.execution_id.encode("utf-8")
                ).hexdigest(),
            }
            scientific["simulation_result_identity"] = domain_hash(
                CORE3_RESULT_DOMAIN_V6, _core3_result_material(scientific),
            )
            envelope = {
                "status": "COMPLETED", "runtime_wall_seconds": "1/2",
                "result": scientific,
            }
            terminal = _terminal_row(
                writer, record, envelope,
                worker_backend="PHYSICAL_CORE_PROCESS_SLOTS",
                physical_core_binding_required=True,
            )
            writer.write_result(terminal)
            pairs.setdefault(pair_key, {})[horizon] = (
                writer.terminals / f"{record.execution_id}.json"
            )
    return evidence / "core3_calibration_manifest_v6.json", roots, pairs


def _summarize(fixture):
    manifest, roots, _pairs = fixture
    return summarize_calibration_v6(manifest, roots[30000], roots[60000])


def _rehash_terminal(path: Path):
    terminal = json.loads(path.read_text(encoding="utf-8"))
    nested = terminal.get("result", {}).get("result")
    if isinstance(nested, dict) and nested.get("result_schema_version"):
        nested["simulation_result_identity"] = domain_hash(
            CORE3_RESULT_DOMAIN_V6, _core3_result_material(nested),
        )
        for key, value in nested.items():
            terminal[key] = value
    unsigned = dict(terminal)
    unsigned.pop("result_identity", None)
    terminal["result_identity"] = domain_hash(
        RTA4_LOCAL_RESULT_DOMAIN_V6, unsigned,
    )
    path.write_text(json.dumps(terminal), encoding="utf-8")


def test_calibration_campaigns_remain_eight_independent_tasksets():
    manifest = materialize_calibration_campaigns_v6(_calibration_raw())
    assert manifest["taskset_count"] == 8
    assert [row["release_horizon"] for row in manifest["campaigns"]] == [30000, 60000]
    for row in manifest["campaigns"]:
        normalized = normalize_rta4_campaign_v5(row["campaign"])
        assert len(list(iter_formal_plan_v5(
            normalized["normalized_scientific_config"],
            normalized["task_sources"], normalized["service_curve"],
        ))) == 64


def test_calibration_complete_plan_allows_summary_and_strict_freeze(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is True
    assert summary["expected_run_count"] == summary["actual_run_count"] == 24
    assert summary["pair_count"] == summary["expected_pair_count"] == 12
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    frozen = freeze_calibration_v6(
        summary_path, tmp_path / "freeze.json",
        release_horizon=60000, b_low="50", b_high="100",
    )
    assert frozen["selection_mode"] == (
        "EXPLICIT_HUMAN_REVIEWED_NO_AUTOMATIC_SELECTION"
    )
    assert frozen["calibration_summary_sha256"] == hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()


def test_calibration_detects_pair_missing_from_both_horizons(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    pair = next(iter(fixture[2].values()))
    pair[30000].unlink()
    pair[60000].unlink()
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is False
    assert len(summary["missing_execution_ids"]) == 2
    assert summary["pair_count"] < summary["expected_pair_count"]


def test_calibration_detects_one_sided_missing_and_extra_terminal(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    path = next(iter(fixture[2].values()))[60000]
    path.unlink()
    extra = fixture[1][30000] / "local_terminal_results_v5" / f"{'f' * 64}.json"
    source = next((fixture[1][30000] / "local_terminal_results_v5").glob("*.json"))
    extra.write_bytes(source.read_bytes())
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is False
    assert summary["missing_execution_ids"]
    assert summary["extra_execution_ids"]
    assert any("duplicate" in value for value in summary["invalid_execution_ids"])


@pytest.mark.parametrize("status", ["INTERNAL_ERROR", "TIMEOUT"])
def test_calibration_rejects_terminal_error_or_timeout_on_both_horizons(
    tmp_path, status,
):
    fixture = _write_calibration_fixture(tmp_path)
    for path in next(iter(fixture[2].values())).values():
        terminal = json.loads(path.read_text(encoding="utf-8"))
        terminal["result"]["status"] = status
        unsigned = dict(terminal)
        unsigned.pop("result_identity")
        terminal["result_identity"] = domain_hash(
            RTA4_LOCAL_RESULT_DOMAIN_V6, unsigned,
        )
        path.write_text(json.dumps(terminal), encoding="utf-8")
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is False
    assert len(summary["invalid_execution_ids"]) >= 2


def test_calibration_rejects_malformed_json_and_sidecar_tamper(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    pair = next(iter(fixture[2].values()))
    pair[30000].write_text("{", encoding="utf-8")
    terminal = json.loads(pair[60000].read_text(encoding="utf-8"))
    sidecar = fixture[1][60000] / terminal["job_observations_relative_path"]
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is False
    assert len(summary["invalid_execution_ids"]) >= 2


def test_calibration_preserves_horizon_insufficient_diagnostic(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    path = next(iter(fixture[2].values()))[30000]
    terminal = json.loads(path.read_text(encoding="utf-8"))
    terminal["result"]["result"]["observed_status"] = "SIM_HORIZON_INSUFFICIENT"
    path.write_text(json.dumps(terminal), encoding="utf-8")
    _rehash_terminal(path)
    summary = _summarize(fixture)
    assert summary["pairing_complete"] is False
    assert summary["horizon_insufficient_execution_ids"]
    summary_path = tmp_path / "insufficient-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RTA4Core3CalibrationV6Error, match="incomplete"):
        freeze_calibration_v6(
            summary_path, tmp_path / "freeze.json",
            release_horizon=30000, b_low="50", b_high="100",
        )


@pytest.mark.parametrize(
    "mutation,rehash",
    [
        (lambda row: row.update(calibration_summary_identity="f" * 64), False),
        (lambda row: row.update(pairing_complete=False), False),
        (lambda row: row["missing_execution_ids"].append("missing"), True),
        (
            lambda row: row.update(
                actual_run_count=row["expected_run_count"] - 1
            ),
            True,
        ),
        (
            lambda row: row.update(
                pair_count=row["expected_pair_count"] - 1
            ),
            True,
        ),
    ],
)
def test_freeze_rejects_forged_or_incomplete_summary(
    tmp_path, mutation, rehash,
):
    fixture = _write_calibration_fixture(tmp_path)
    summary = _summarize(fixture)
    mutation(summary)
    if rehash:
        unsigned = dict(summary)
        unsigned.pop("calibration_summary_identity")
        summary["calibration_summary_identity"] = domain_hash(
            CORE3_CALIBRATION_SUMMARY_DOMAIN_V6, unsigned,
        )
    path = tmp_path / "forged-summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RTA4Core3CalibrationV6Error):
        freeze_calibration_v6(
            path, tmp_path / "freeze.json",
            release_horizon=60000, b_low="50", b_high="100",
        )


def test_freeze_rejects_capacity_outside_manifest_candidates(tmp_path):
    fixture = _write_calibration_fixture(tmp_path)
    summary = _summarize(fixture)
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RTA4Core3CalibrationV6Error, match="capacity"):
        freeze_calibration_v6(
            path, tmp_path / "freeze.json",
            release_horizon=60000, b_low="50", b_high="200",
        )
