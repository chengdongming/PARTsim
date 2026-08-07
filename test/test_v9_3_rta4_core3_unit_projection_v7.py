from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from experiments.v9_3.rta4_core3_contracts_v6 import (
    default_core3_energy_conservation_rule_v1,
)
from experiments.v9_3.rta4_core3_calibration_v7 import (
    CORE3_CALIBRATION_CONFIG_V7,
    materialize_calibration_campaigns_v7,
)
from experiments.v9_3.rta4_core3_contracts_v7 import (
    CORE3_RESULT_SCHEMA_V7,
    CORE3_SIMULATION_CONTRACT_V7,
    CORE3_TASK_WORKLOAD_V7,
    RTA4Core3ContractV7Error,
    core3_physical_execution_identity_v7,
    core3_task_physical_projection_v7,
    require_core3_task_physical_projection_v7,
)
from experiments.v9_3.rta4_energy_service_v5 import (
    core3_simulation_projection_v6,
)
from experiments.v9_3.rta4_formal_config_v5 import (
    RTA4FormalConfigV5Error,
    formal_taskset_store_identity_v5,
    load_rta4_campaign_v5,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
    source_closure_identity_v5,
)
from experiments.v9_3.rta4_formal_plan_v5 import (
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from experiments.v9_3.rta4_local_execution_v5 import (
    ExactServiceSimulationExecutorV5,
    _exact_piecewise_system_v5,
    _prepared_record_material,
    _verify_core3_execution_provenance_v7,
)
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    _render_taskset_yaml,
)
from experiments.v9_3.simulation_result import (
    SimulationTraceError,
    conditional_release_coverage_v7,
    parse_simulation_trace,
)


ROOT = Path(__file__).resolve().parents[1]
V6_SMOKE = ROOT / "configs/v9_3_rta4_core3_v6_smoke_UNAUTHORIZED.yaml"
V7_SMOKE = ROOT / "configs/v9_3_rta4_core3_v7_smoke_UNAUTHORIZED.yaml"
V7_CALIBRATION = (
    ROOT / "configs/v9_3_rta4_core3_calibration_v7_UNAUTHORIZED.yaml"
)
SYSTEM = ROOT / "system_config_unified_template.yml"


def _loaded_v7():
    return load_rta4_campaign_v5(V7_SMOKE)


def _first_record(campaign=None):
    campaign = campaign or _loaded_v7()
    return next(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))


def _task_energy_material():
    campaign = _loaded_v7()
    record = _first_record(campaign)
    worker, _certificate, context, _identity = _prepared_record_material(
        campaign, record,
    )
    binding = context.binding_for(worker.record_id)
    return context.task_energy_materials[
        binding["task_energy_material_identity"]
    ]


def _projection(system=SYSTEM):
    return core3_task_physical_projection_v7(
        base_system_path=system,
        task_energy_material=_task_energy_material(),
        model_energy_unit_joules="1/1000",
        simulation_tick_ms=1,
    )


def test_harvest_projection_is_model_units_to_joules_to_watts():
    projection = core3_simulation_projection_v6(
        exact_service_material_identity="a" * 64,
        harvest_trace=(Fraction(11, 2), Fraction(11, 2)),
        simulation_tick_ms=1,
        model_energy_unit_joules="1/1000",
    )
    segment = projection["segments"][0]
    assert segment["model_energy_per_tick"] == "11/2"
    assert segment["physical_energy_per_tick_j"] == "11/2000"
    assert segment["power_w"] == "11/2"
    assert Fraction(segment["power_w"]) != 5500


@pytest.mark.parametrize(
    "value", [1e-3, "0", "-1/1000", "2/2000", "0.001"],
)
def test_v7_energy_scale_rejects_float_nonpositive_and_noncanonical(value):
    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    raw["model_energy_unit_joules"] = value
    with pytest.raises(RTA4FormalConfigV5Error):
        normalize_rta4_campaign_v5(raw, base_directory=V7_SMOKE.parent)


def test_v7_tick_is_fixed_to_one_and_fixed_semantics_are_explicit():
    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    normalized = normalize_rta4_campaign_v5(
        raw, base_directory=V7_SMOKE.parent,
    )["normalized_scientific_config"]
    assert normalized["simulation_tick_ms"] == 1
    assert normalized["model_energy_unit_joules"] == "1/1000"
    assert normalized["core3_simulation_contract"]["contract_version"] == (
        CORE3_SIMULATION_CONTRACT_V7
    )
    assert normalized["core3_simulation_contract"][
        "result_schema_version"
    ] == CORE3_RESULT_SCHEMA_V7
    assert "legacy_v5_core3_behavior_preserved" not in normalized[
        "fixed_semantics"
    ]
    assert all(normalized["fixed_semantics"][key] is True for key in (
        "core3_explicit_model_energy_to_joule_projection",
        "core3_task_energy_bound_to_simulator",
        "core3_simulation_tick_ms_fixed_to_one",
    ))
    raw["simulation_tick_ms"] = 2
    with pytest.raises(RTA4FormalConfigV5Error, match="must equal 1"):
        normalize_rta4_campaign_v5(raw, base_directory=V7_SMOKE.parent)


def test_v7_selection_is_explicit_and_missing_scale_cannot_downgrade():
    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    raw.pop("model_energy_unit_joules")
    with pytest.raises(
        RTA4FormalConfigV5Error, match="model_energy_unit_joules",
    ):
        normalize_rta4_campaign_v5(raw, base_directory=V7_SMOKE.parent)

    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    raw.pop("core3_simulation_contract_version")
    with pytest.raises(
        RTA4FormalConfigV5Error, match="explicit.*contract_version",
    ):
        normalize_rta4_campaign_v5(raw, base_directory=V7_SMOKE.parent)


def test_v7_plan_keeps_model_values_and_projects_physical_values():
    record = _first_record()
    material = record.material["effective_core3_simulation_material"]
    assert material["battery_capacity_model_units"] == "1000000000"
    assert material["battery_capacity_j"] == "1000000"
    assert material["physical_initial_energy_model_units"] == "0"
    assert material["physical_initial_energy_j"] == "0"
    assert material["projection_e0_model_units"] == [
        str(value) for value in range(34, 41)
    ]
    assert material["projection_e0_j"][0] == "17/500"
    assert material["projection_e0_j"][-1] == "1/25"
    assert "battery_capacity" not in material
    assert "physical_initial_energy" not in material


def test_v7_capacity_examples_are_exact():
    scale = Fraction(1, 1000)
    assert Fraction(160000) * scale == 160
    assert Fraction(166000) * scale == 166
    assert Fraction(1000000000) * scale == 1000000
    assert Fraction(0) * scale == 0


def test_v7_e0_coverage_uses_physical_joules():
    coverage = conditional_release_coverage_v7(
        [0.033999999, 0.034, 0.040], ["34", "40"], "1/1000",
    )
    assert coverage[0]["projection_e0_j"] == "17/500"
    assert coverage[0]["covered_job_count"] == 2
    assert coverage[1]["projection_e0_j"] == "1/25"
    assert coverage[1]["covered_job_count"] == 1


def _payload():
    return ({
        "task_id": "a", "priority_rank": 0,
        "C": 2, "D": 5, "T": 10,
        "workload": CORE3_TASK_WORKLOAD_V7,
        "arrival_offset": 0,
    }, {
        "task_id": "b", "priority_rank": 1,
        "C": 1, "D": 7, "T": 12,
        "workload": CORE3_TASK_WORKLOAD_V7,
        "arrival_offset": 1,
    })


def test_task_yaml_factor_projection_and_legacy_bytes():
    legacy = _render_taskset_yaml(_payload(), release_horizon=20)
    projected = _render_taskset_yaml(
        _payload(), release_horizon=20,
        task_energy_factors={"a": "0.5", "b": "2"},
    )
    assert "task_energy_factor" not in legacy
    assert "task_energy_factor=0.5,workload=" in projected
    assert "task_energy_factor=2,workload=" in projected
    assert projected.count("task_energy_factor=") == 2
    with pytest.raises(SimulationConfigurationError, match="exactly match"):
        _render_taskset_yaml(
            _payload(), task_energy_factors={"a": "1"},
        )
    with pytest.raises(SimulationConfigurationError, match="canonical"):
        _render_taskset_yaml(
            _payload(), task_energy_factors={"a": "0", "b": "2"},
        )
    duplicate_payload = (dict(_payload()[0], task_id="1"),)
    with pytest.raises(SimulationConfigurationError, match="duplicate"):
        _render_taskset_yaml(
            duplicate_payload, task_energy_factors={1: "1", "1": "2"},
        )


def test_task_projection_uses_bound_system_and_distinct_task_power():
    projection = _projection()
    assert require_core3_task_physical_projection_v7(projection) == projection
    system = projection["system_energy_model"]
    assert system["base_power_w"] == "1/2"
    assert system["workload_coefficient"] == "1"
    assert system["frequency_power_ratio"] == "93/100"
    assert system["base_energy_j_per_tick"] == "93/200000"
    rows = projection["tasks"]
    assert rows[0]["exact_task_energy_factor"] == "20/93"
    assert rows[0]["expected_physical_energy_j_per_tick"] == "1/10000"
    assert len({row["exact_task_energy_factor"] for row in rows}) > 1
    for row in rows:
        assert float(row["emitted_task_energy_factor_decimal"]).hex() == (
            row["emitted_task_energy_factor_binary64_hex"]
        )
    tampered = deepcopy(projection)
    tampered["tasks"][0]["emitted_task_energy_factor_decimal"] = "2"
    with pytest.raises(RTA4Core3ContractV7Error, match="factor"):
        require_core3_task_physical_projection_v7(tampered)


@pytest.mark.parametrize("capacity", ["160000", "166000"])
def test_projected_system_uses_real_plan_initial_and_capacity(
    tmp_path, capacity,
):
    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    raw["finite_battery_capacities"] = [capacity]
    loaded = normalize_rta4_campaign_v5(
        raw, base_directory=V7_SMOKE.parent,
    )
    record = next(
        item for item in iter_formal_plan_v5(
            loaded["normalized_scientific_config"],
            loaded["task_sources"], loaded["service_curve"],
        )
        if item.material["effective_core3_simulation_material"]["track"]
        == "FINITE_BATTERY_EMPIRICAL"
    )
    material = record.material["effective_core3_simulation_material"]
    projection = record.material["service_material"]["simulation_projection"]
    path = _exact_piecewise_system_v5(
        SYSTEM, tmp_path, processors=4, scheduler="gpfp_asap_block",
        initial_energy=Fraction(material["physical_initial_energy_j"]),
        max_energy=Fraction(material["battery_capacity_j"]),
        simulation_projection=projection,
        simulator_compatible_lists=True,
        core3_v7_fixed_workload=CORE3_TASK_WORKLOAD_V7,
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    energy = document["energy_management"]
    assert material["physical_initial_energy_model_units"] == "0"
    assert material["physical_initial_energy_j"] == "0"
    assert material["battery_capacity_model_units"] == capacity
    assert material["battery_capacity_j"] == str(Fraction(capacity) / 1000)
    assert energy["initial_energy"] == 0.0
    assert energy["max_energy"] == float(Fraction(capacity) / 1000)
    assert energy["scheduler_energy_model"]["workload_coefficients"][
        CORE3_TASK_WORKLOAD_V7
    ] == 1.0
    assert any(
        segment["multiplier"] == 5.5
        for segment in document["harvesting"]["scaled_piecewise"]["segments"]
    )


def test_conflicting_fixed_workload_coefficient_fails_closed(tmp_path):
    document = yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    document["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ][CORE3_TASK_WORKLOAD_V7] = 2.0
    conflict = tmp_path / "conflict.yml"
    conflict.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RTA4Core3ContractV7Error, match="conflicts"):
        _projection(conflict)


def test_system_model_changes_physical_but_not_formal_math_identity(tmp_path):
    first = _projection()
    document = yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    document["energy_management"]["scheduler_energy_model"][
        "base_power"
    ] = 0.6
    changed_path = tmp_path / "changed.yml"
    changed_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    second = _projection(changed_path)
    assert first["physical_task_projection_identity"] != second[
        "physical_task_projection_identity"
    ]
    base_execution = _first_record().execution_id
    first_execution = core3_physical_execution_identity_v7(
        base_execution_identity=base_execution,
        physical_task_projection_identity=first[
            "physical_task_projection_identity"
        ],
        projected_system_sha256="1" * 64,
    )
    second_execution = core3_physical_execution_identity_v7(
        base_execution_identity=base_execution,
        physical_task_projection_identity=second[
            "physical_task_projection_identity"
        ],
        projected_system_sha256="2" * 64,
    )
    assert first_execution != second_execution
    assert base_execution == _first_record().execution_id


def _trace(task_unit_energy_mj: float):
    events = []
    for index in range(10):
        name = f"v93_task_{index}"
        events.extend(({
            "time": index, "event_type": "arrival", "task_name": name,
            "arrival_time": index, "current_energy_mJ": 34,
            "total_harvested_mJ": 100, "total_consumed_mJ": 100,
        }, {
            "time": index, "event_type": "release_energy_snapshot",
            "task_name": name, "arrival_time": index,
            "available_energy_mJ": 34,
            "sampling_stage": "post_harvest_pre_consumption",
            "scheduler": "gpfp_asap_block",
        }, {
            "time": index, "event_type": "scheduled", "task_name": name,
            "arrival_time": index,
            "task_unit_energy_mJ": task_unit_energy_mj,
        }, {
            "time": index + 1, "event_type": "end_instance",
            "task_name": name, "arrival_time": index,
        }))
    return {
        "events": events, "trace_schema_version": 3,
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "expected_simulation_horizon_ms": 30,
        "observed_simulation_end_ms": 30,
        "release_horizon_ms": 20, "observation_horizon_ms": 30,
        "release_cutoff_enabled": True,
        "observation_horizon_reached": True,
        "observability_summary_contract_version": 2,
        "observability_summary_horizon_ms": 30,
        "energy_summary": {
            "offered_energy_j": 0.1, "credited_energy_j": 0.1,
            "clipped_energy_j": 0, "consumed_energy_j": 0.1,
            "battery_min_j": 0, "battery_max_j": 0.1,
            "battery_final_j": 0, "battery_empty_ticks": 1,
            "battery_full_ticks": 0, "observed_energy_intervals": 30,
        },
    }


def _parse_task_energy_trace(tmp_path, observed_mj):
    path = tmp_path / f"trace-{observed_mj}.json"
    path.write_text(json.dumps(_trace(observed_mj)), encoding="utf-8")
    payload = [{
        "task_id": str(index), "priority_rank": index,
        "C": 1, "D": 10, "T": 20 + index,
    } for index in range(10)]
    expected = {str(index): Fraction(1, 100) for index in range(10)}
    provenance = {
        str(index): {"exact_task_energy_factor": "test"}
        for index in range(10)
    }
    return parse_simulation_trace(
        path, payload, expected_taskset_hash="a" * 64,
        horizon=30, warmup=0, minimum_jobs_per_task=0,
        release_e0=Fraction(0), expected_processors=4,
        require_core3_observability=True, release_horizon=20,
        physical_initial_energy=Fraction(0), battery_capacity=Fraction(1),
        conditional_e0=["34", "40"], theorem_alignment_track=False,
        energy_conservation_rule=default_core3_energy_conservation_rule_v1(),
        model_energy_unit_joules="1/1000",
        expected_task_energy_j_per_tick=expected,
        task_energy_factor_provenance=provenance,
    )


def test_expected_and_observed_task_energy_validation(tmp_path):
    result = _parse_task_energy_trace(tmp_path, 10)
    assert result.observed_task_power_j_per_tick == {
        str(index): 0.01 for index in range(10)
    }
    assert all(
        row["within_tolerance"]
        for row in result.metrics["task_energy_validation"]
    )
    for wrong in (0.465, 10000):
        with pytest.raises(SimulationTraceError, match="physical energy mismatch"):
            _parse_task_energy_trace(tmp_path, wrong)


def test_v7_and_v6_identities_are_isolated_and_calibration_base_is_v7():
    old = load_rta4_campaign_v5(V6_SMOKE)
    new = _loaded_v7()
    assert old.normalized_scientific_config[
        "core3_simulation_contract"
    ]["contract_version"] != CORE3_SIMULATION_CONTRACT_V7
    assert "model_energy_unit_joules" not in old.normalized_scientific_config
    assert rta4_formal_config_hash_v5(
        old.normalized_scientific_config
    ) != rta4_formal_config_hash_v5(new.normalized_scientific_config)
    assert formal_taskset_store_identity_v5(
        old.normalized_scientific_config
    ) != formal_taskset_store_identity_v5(new.normalized_scientific_config)
    assert source_closure_identity_v5(
        old.normalized_scientific_config
    ) != source_closure_identity_v5(new.normalized_scientific_config)
    old_plan = describe_formal_plan_v5(
        old.normalized_scientific_config, old.task_sources, old.service_curve,
    )
    new_plan = describe_formal_plan_v5(
        new.normalized_scientific_config, new.task_sources, new.service_curve,
    )
    assert old_plan["plan_sha256"] != new_plan["plan_sha256"]
    calibration = yaml.safe_load(V7_CALIBRATION.read_text(encoding="utf-8"))
    manifest = materialize_calibration_campaigns_v7(
        calibration, base_directory=V7_CALIBRATION.parent,
    )
    assert manifest["schema_version"] == CORE3_CALIBRATION_CONFIG_V7
    assert manifest["model_energy_unit_joules"] == "1/1000"
    assert len(manifest["campaigns"]) == 2
    normalized = normalize_rta4_campaign_v5(
        calibration["base_campaign"], base_directory=V7_CALIBRATION.parent,
    )["normalized_scientific_config"]
    assert normalized["core3_simulation_contract"]["contract_version"] == (
        CORE3_SIMULATION_CONTRACT_V7
    )


def test_model_energy_scale_changes_scientific_identity():
    raw = yaml.safe_load(V7_SMOKE.read_text(encoding="utf-8"))
    first_loaded = normalize_rta4_campaign_v5(
        raw, base_directory=V7_SMOKE.parent,
    )
    first = first_loaded["normalized_scientific_config"]
    changed = deepcopy(raw)
    changed["model_energy_unit_joules"] = "1/2000"
    second_loaded = normalize_rta4_campaign_v5(
        changed, base_directory=V7_SMOKE.parent,
    )
    second = second_loaded["normalized_scientific_config"]
    assert rta4_formal_config_hash_v5(first) != rta4_formal_config_hash_v5(
        second
    )
    first_record = next(iter_formal_plan_v5(
        first, first_loaded["task_sources"], first_loaded["service_curve"],
    ))
    second_record = next(iter_formal_plan_v5(
        second, second_loaded["task_sources"], second_loaded["service_curve"],
    ))
    assert first_record.mathematical_request_id != (
        second_record.mathematical_request_id
    )
    assert first_record.execution_id != second_record.execution_id


def test_v7_executor_binds_factors_system_and_result_provenance(
    tmp_path, monkeypatch,
):
    campaign = _loaded_v7()
    plan_record = _first_record(campaign)
    record, certificate, context, _identity = _prepared_record_material(
        campaign, plan_record,
    )

    def fake_simulator(command, **kwargs):
        assert kwargs["stdout"] is subprocess.DEVNULL
        taskset = yaml.safe_load(Path(command[2]).read_text(encoding="utf-8"))
        trace = _trace(1)
        for event in trace["events"]:
            index = int(event["task_name"].rsplit("_", 1)[1])
            task = taskset["taskset"][index]
            event["task_name"] = task["name"]
            if event["event_type"] == "scheduled":
                params = dict(
                    token.split("=", 1)
                    for token in task["params"].split(",")
                )
                event["task_unit_energy_mJ"] = (
                    float(params["task_energy_factor"]) * 0.465
                )
            if event["event_type"] == "end_instance":
                event["time"] = int(event["arrival_time"]) + int(
                    task["runtime"]
                )
        horizon = int(command[3])
        trace.update({
            "run_id": command[command.index("--run-id") + 1],
            "taskset_semantic_hash": certificate.taskset_hash,
            "configured_scheduler": str(record.material["scheduler"]),
            "expected_simulation_horizon_ms": horizon,
            "observed_simulation_end_ms": horizon,
            "release_horizon_ms": int(record.material["release_horizon"]),
            "observation_horizon_ms": horizon,
            "observability_summary_horizon_ms": horizon,
        })
        trace["energy_summary"]["observed_energy_intervals"] = horizon
        Path(command[command.index("-t") + 1]).write_text(
            json.dumps(trace), encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "experiments.v9_3.rta4_local_execution_v5.subprocess.run",
        fake_simulator,
    )
    executor = ExactServiceSimulationExecutorV5(
        {}, run_context=context,
        production_manifest={
            "simulator_path": str(tmp_path / "fake-rtsim"),
            "artifact_storage": campaign.runtime["artifact_storage"],
        },
        system_config_path=SYSTEM,
        energy_support_path=tmp_path / "unused",
        output_root=tmp_path / "run",
        simulation_timeout_seconds=10,
    )
    result = executor(record, certificate)
    assert result["result_schema_version"] == CORE3_RESULT_SCHEMA_V7
    assert result["model_energy_unit_joules"] == "1/1000"
    assert result["battery_capacity_model_units"] == "1000000000"
    assert result["battery_capacity_j"] == "1000000"
    assert result["projection_e0_j"][0] == "17/500"
    assert len(result["per_task_energy_projection"]) == 10
    assert require_core3_task_physical_projection_v7(
        result["physical_task_projection"]
    ) == result["physical_task_projection"]
    assert all(
        row["observed_energy_within_tolerance"]
        for row in result["per_task_energy_projection"]
    )
    assert len(result["physical_execution_identity"]) == 64
    _verify_core3_execution_provenance_v7(
        tmp_path / "run",
        {**dict(result), "execution_identity": record.execution_id},
    )
    projected = yaml.safe_load((
        tmp_path / "run" / "bounded_core3_simulations_v5"
        / record.execution_id / "system_config_v7.yaml"
    ).read_text(encoding="utf-8"))
    assert projected["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ][CORE3_TASK_WORKLOAD_V7] == 1.0
