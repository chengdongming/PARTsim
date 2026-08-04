from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
import yaml

from experiments.v9_3 import rta4_local_execution_v5 as local_execution_v5
from experiments.v9_3 import rta4_formal_execution as formal_execution
from experiments.v9_3 import rta4_formal_runner_v5 as runner_v5
from experiments.v9_3 import rta4_physical_execution_v5 as physical_execution_v5
from experiments.common.exact_service_curve import (
    EXACT_LINEAR_SERVICE_CURVE_V1,
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)
from experiments.v9_3.rta4_formal_config_v5 import (
    CORE5A_FIXED_TICK_SERVICE_V1,
    LoadedCampaignV5,
    RTA4FormalConfigV5Error,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
)
from experiments.v9_3.rta4_energy_service_v5 import (
    core3_simulation_projection_v5,
    exact_service_material_v5,
)
from experiments.v9_3.rta4_formal_runner_v5 import (
    main as runner_v5_main,
    preflight_campaign_v5,
)
from experiments.v9_3.rta4_formal_plan_v3 import core4_conditions_v3
from experiments.v9_3.rta4_formal_plan_v5 import (
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from experiments.v9_3.rta4_task_source_v4 import (
    GENERAL_RANDOM_CONSTRAINED_V1,
    GENERATED_FAMILY,
    PRIORITY_POLICY_RM,
)
from experiments.v9_3.rta4_unified_adapter_v5 import (
    execute_normalized_taskset_v5,
)
from experiments.v9_3.rta4_local_execution_v5 import (
    CORE_EXECUTION_DISPATCH_V5,
    RTA4LocalExecutionV5Error,
    execute_loaded_campaign_v5,
)
from experiments.v9_3.rta4_formal_workers_v3 import (
    V3AttemptResponse,
    V3WorkerRequest,
)
from experiments.v9_3.rta4_physical_core_slots_v3 import (
    CPUTopologyV3,
    PhysicalCoreSlotPoolV3,
    PhysicalCoreV3,
    SlotCompletionV3,
    SlotStartedV3,
    WorkerDiagnosticV3,
    discover_cpu_topology_v3,
)
from experiments.v9_3.rta4_physical_execution_v5 import (
    PreparedPhysicalRecordV5,
    execute_physical_group_v5,
)
from scripts.create_v9_3_rta4_campaign import campaign_template


METHODS = ["CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ"]


def _physical_probe_attempt_worker(_state, request):
    """Pickle-safe process/affinity probe; it never calls an RTA solver."""

    time.sleep(0.12)
    attempt = {
        "attempt_index": request.attempt_index,
        "timeout_seconds": request.timeout_seconds,
        "status": "COMPLETED",
        "runtime_wall_seconds": "0.12",
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
        "error_classification": "NONE",
    }
    return V3AttemptResponse(
        request.record.record_id,
        request.record.execution_id,
        request.attempt_index,
        request.timeout_seconds,
        {
            "solver_status": "COMPLETED",
            "taskset_certification_status": "CERTIFIED_TASKSET",
            "taskset_proven": True,
            "attempts": (attempt,),
            "timeout_seconds": request.timeout_seconds,
            "runtime_wall_seconds": "0.12",
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
            "probe_pid": os.getpid(),
        },
    )


def _physical_retry_probe_attempt_worker(_state, request):
    """Force the first process past its hard timeout, then finish the retry."""

    if request.attempt_index == 0:
        time.sleep(request.timeout_seconds + 0.35)
    return _physical_probe_attempt_worker(_state, request)


def _physical_probe_pool_factory(selected_cores, **_ignored):
    return PhysicalCoreSlotPoolV3(
        selected_cores,
        worker_callable=_physical_probe_attempt_worker,
        worker_state=None,
        start_method="spawn",
    )


def _physical_retry_probe_pool_factory(selected_cores, **_ignored):
    return PhysicalCoreSlotPoolV3(
        selected_cores,
        worker_callable=_physical_retry_probe_attempt_worker,
        worker_state=None,
        start_method="spawn",
    )


def _physical_core3_probe_attempt_worker(_state, request):
    if request.record.kind != "simulation":
        return _physical_probe_attempt_worker(_state, request)
    time.sleep(0.05)
    return V3AttemptResponse(
        request.record.record_id,
        request.record.execution_id,
        request.attempt_index,
        request.timeout_seconds,
        {
            "status": "COMPLETED",
            "error_classification": "NONE",
            "runtime_wall_seconds": "0.05",
            "runtime_cpu_seconds": "0",
            "result": {
                "simulation_status": "COMPLETED",
                "observed_status": "SIM_PASS_OBSERVED",
                "probe_pid": os.getpid(),
            },
        },
    )


def _physical_core3_probe_pool_factory(selected_cores, **_ignored):
    return PhysicalCoreSlotPoolV3(
        selected_cores,
        worker_callable=_physical_core3_probe_attempt_worker,
        worker_state=None,
        start_method="spawn",
    )


def _physical_malformed_retry_attempt_worker(_state, request):
    if request.attempt_index == 1:
        return {"malformed": True}
    attempt = {
        "attempt_index": 0,
        "timeout_seconds": request.timeout_seconds,
        "status": "TIMEOUT",
        "runtime_wall_seconds": "0.01",
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
        "error_classification": "TEST_TIMEOUT",
    }
    return V3AttemptResponse(
        request.record.record_id,
        request.record.execution_id,
        0,
        request.timeout_seconds,
        {
            "solver_status": "TIMEOUT",
            "taskset_certification_status": "TIMEOUT",
            "taskset_proven": False,
            "attempts": (attempt,),
            "timeout_seconds": request.timeout_seconds,
            "runtime_wall_seconds": "0.01",
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
        },
    )


def _physical_malformed_retry_pool_factory(selected_cores, **_ignored):
    return PhysicalCoreSlotPoolV3(
        selected_cores,
        worker_callable=_physical_malformed_retry_attempt_worker,
        worker_state=None,
        start_method="spawn",
    )


def _source(*, processors=2, task_count=2, tasksets=1, time_scale=1):
    templates = []
    for index in range(task_count):
        period = (index + 1) * 10 * time_scale
        templates.append({
            "name": f"tau_{index + 1}",
            "C": [time_scale],
            "D": [(index + 1) * 6 * time_scale],
            "T": [period],
            "power": ["1/10"],
        })
    return {
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": {
            "processors": processors,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": task_count,
            "taskset_count": tasksets,
            "base_seed": 100,
            "generation_indices": list(range(tasksets)),
            "task_templates": templates,
        },
    }


def _small(core: str) -> dict:
    raw = deepcopy(campaign_template(core))
    raw["campaign_id"] = f"rta4-{core.lower()}-v5-test"
    raw["service_curve"] = {
        "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
        "rate": "1/10",
        "latency": "1",
        "time_unit": "tick",
    }
    raw["runtime"] = {}
    if core == "CORE-1":
        raw.update({
            "processors": 2,
            "task_count": 2,
            "normalized_utilization": ["1/2"],
            "tasksets_per_utilization": 1,
            "e0": ["0"],
            "methods": METHODS,
            "task_source": _source(),
        })
    elif core == "CORE-2":
        raw["source"] = deepcopy(raw["source"])
        raw["source"]["taskset_count"] = 1
        raw["e0"] = ["0"]
        raw["methods"] = ["CW_D", "SEQ_D"]
        raw["task_source"] = _source()
    elif core == "CORE-3":
        raw["simulation_tick_ms"] = 1
        raw["source"] = deepcopy(raw["source"])
        raw["source"]["taskset_count"] = 1
        raw["release_modes"] = ["SYNC_V1"]
        raw["finite_battery_capacities"] = ["20"]
        raw["projection_methods"] = ["CW_THETA_CW"]
        raw["projection_e0"] = ["0"]
        raw["simulation_horizon"] = {
            "release_horizon": 20,
            "observation_horizon": "release_horizon_plus_dmax",
        }
        raw["task_source"] = _source()
    elif core == "CORE-4":
        raw.update({
            "processors": 2,
            "task_count": 2,
            "normalized_utilization": ["1/2"],
            "skeletons_per_utilization": 1,
            "baseline": {
                "e0": "0", "service_scale": "1", "power_scale": "1",
                "deadline_slack_fraction": "3/4",
            },
            "axes": {
                "e0": ["0", "1"],
                "service_scale": ["1", "2"],
                "power_scale": ["1", "2"],
                "deadline_slack_fraction": ["3/4", "1"],
            },
            "methods": ["CW_THETA_CW", "SEQ_THETA_SEQ"],
            "task_source": _source(),
        })
    elif core == "CORE-5A":
        raw.update({
            "baseline": {
                "e0": "0", "normalized_utilization": "1/2",
                "service_scale": "1", "power_scale": "1",
                "deadline_slack_fraction": "3/4",
            },
            "task_count_axis": {"values": [1], "processors": 2, "tasksets": 1},
            "processor_axis": {"values": [2], "task_count": 2, "tasksets": 1},
            "integer_time_scale_axis": {"values": [1, 2], "base_tasksets": 1},
            "methods": ["CW_THETA_CW"],
            "integer_time_scale_service_semantics": (
                CORE5A_FIXED_TICK_SERVICE_V1
            ),
            "task_sources": [
                {"axis": "task_count", "axis_value": 1,
                 "task_source": _source(task_count=1)},
                {"axis": "processor_count", "axis_value": 2,
                 "task_source": _source()},
                {"axis": "integer_time_scale", "axis_value": 1,
                 "task_source": _source(time_scale=1)},
                {"axis": "integer_time_scale", "axis_value": 2,
                 "task_source": _source(time_scale=2)},
            ],
        })
    elif core == "CORE-5B":
        raw["source_baseline_exact_e0"] = "0"
        raw["source"] = deepcopy(raw["source"])
        raw["source"]["taskset_count"] = 3
        raw["utilization_strata"] = ["1/2"]
        raw["candidates_per_method_stratum"] = 3
        raw["selected_per_method_stratum"] = 2
        raw["methods"] = ["CW_THETA_CW"]
        raw["workers"] = [1, 2]
        raw["task_source"] = _source(tasksets=3)
    return raw


def _normalized(core: str):
    return normalize_rta4_campaign_v5(_small(core))


def _loaded(
    core: str, output_root: Path, *, simulation_tick_ms: int | None = None,
) -> LoadedCampaignV5:
    raw = _small(core)
    if simulation_tick_ms is not None:
        raw["simulation_tick_ms"] = simulation_tick_ms
    normalized = normalize_rta4_campaign_v5(raw)
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV5(
        output_root / "campaign.yml",
        "a" * 64,
        scientific,
        rta4_formal_config_hash_v5(scientific),
        {
            "output_root": str(output_root),
            "worker_count": 1,
            "max_in_flight": 1,
            "timeout_seconds": 2,
        },
        normalized["v3_scientific_config"],
        normalized["task_sources"],
        normalized["service_curve"],
    )


def _loaded_from_raw(
    raw: dict, output_root: Path, *, runtime: dict,
) -> LoadedCampaignV5:
    normalized = normalize_rta4_campaign_v5(raw)
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV5(
        output_root / "campaign.yml",
        "b" * 64,
        scientific,
        rta4_formal_config_hash_v5(scientific),
        runtime,
        normalized["v3_scientific_config"],
        normalized["task_sources"],
        normalized["service_curve"],
    )


def _synthetic_topology(count: int, fingerprint: str = "synthetic-topology"):
    cores = tuple(
        PhysicalCoreV3(0, index, index, (index,))
        for index in range(count)
    )
    return CPUTopologyV3(
        tuple(range(count)), cores, fingerprint,
        "TEST_ONLY_SYNTHETIC_SELECTION",
    )


def _clean_rta_result(*, attempts=None):
    return {
        "solver_status": "COMPLETED",
        "taskset_certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": True,
        "attempts": (
            [{"attempt_index": 0, "status": "COMPLETED"}]
            if attempts is None else deepcopy(attempts)
        ),
    }


def _fake_physical_rta_result(*, logical_cpu: int, worker_pid: int):
    return {
        **_clean_rta_result(),
        "worker_backend": "PHYSICAL_CORE_PROCESS_SLOTS",
        "physical_core_binding_required": True,
        "execution_attempt_diagnostics": ({
            "attempt_index": 0,
            "worker_pid": worker_pid,
            "slot_id": 0,
            "worker_generation": 0,
            "logical_cpu_id": logical_cpu,
            "physical_package_id": 0,
            "physical_core_id": logical_cpu,
            "affinity_mask": [logical_cpu],
            "started_monotonic_ns": 100,
            "finished_monotonic_ns": 200,
            "timed_out": False,
            "worker_exit": None,
            "error_classification": None,
        },),
    }


@pytest.mark.parametrize("core", [
    "CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B",
])
def test_all_six_v3_grids_have_a_v5_preflight_path(core):
    normalized = _normalized(core)
    scientific = normalized["normalized_scientific_config"]
    plan = describe_formal_plan_v5(
        scientific, normalized["task_sources"], normalized["service_curve"],
    )
    assert plan["core"] == core
    assert plan["ordered_stream_count"] > 0
    records = tuple(iter_formal_plan_v5(
        scientific, normalized["task_sources"], normalized["service_curve"],
    ))
    assert all(
        record.configured_service_identity
        == scientific["service_curve_identity"]
        for record in records
    )
    assert all(record.taskset_identity for record in records)


def test_core1_four_methods_share_task_and_effective_service():
    normalized = _normalized("CORE-1")
    records = list(iter_formal_plan_v5(
        normalized["normalized_scientific_config"],
        normalized["task_sources"], normalized["service_curve"],
    ))
    assert len(records) == 4
    assert len({record.taskset_identity for record in records}) == 1
    assert len({record.effective_service_identity for record in records}) == 1
    assert len({record.mathematical_request_id for record in records}) == 4


def test_service_change_changes_config_plan_and_math_identity():
    first = _normalized("CORE-1")
    changed = _small("CORE-1")
    changed["service_curve"]["rate"] = "1/5"
    second = normalize_rta4_campaign_v5(changed)
    first_plan = describe_formal_plan_v5(
        first["normalized_scientific_config"], first["task_sources"],
        first["service_curve"],
    )
    second_plan = describe_formal_plan_v5(
        second["normalized_scientific_config"], second["task_sources"],
        second["service_curve"],
    )
    assert first_plan["normalized_scientific_config_sha256"] != second_plan[
        "normalized_scientific_config_sha256"
    ]
    assert first_plan["plan_sha256"] != second_plan["plan_sha256"]
    first_record = next(iter_formal_plan_v5(
        first["normalized_scientific_config"], first["task_sources"],
        first["service_curve"],
    ))
    second_record = next(iter_formal_plan_v5(
        second["normalized_scientific_config"], second["task_sources"],
        second["service_curve"],
    ))
    assert first_record.mathematical_request_id != second_record.mathematical_request_id


def test_core3_tracks_bind_the_same_prefix_and_trace_material():
    normalized = _normalized("CORE-3")
    records = list(iter_formal_plan_v5(
        normalized["normalized_scientific_config"],
        normalized["task_sources"], normalized["service_curve"],
    ))
    assert {record.material["v3_grid_material"]["track"] for record in records} == {
        "THEOREM_ALIGNED", "FINITE_BATTERY_EMPIRICAL",
    }
    assert len({
        record.material["service_material"]["material_identity"]
        for record in records
    }) == 1
    assert len({
        record.material["service_material"]["trace_sha256"]
        for record in records
    }) == 1


@pytest.mark.parametrize("value", [None, 0, -1, True, 1.0])
def test_core3_requires_explicit_positive_plain_simulation_tick_ms(value):
    raw = _small("CORE-3")
    if value is None:
        raw.pop("simulation_tick_ms")
    else:
        raw["simulation_tick_ms"] = value
    with pytest.raises(RTA4FormalConfigV5Error, match="simulation_tick_ms"):
        normalize_rta4_campaign_v5(raw)


@pytest.mark.parametrize("value", [1, 2])
def test_core3_accepts_positive_simulation_tick_ms(value):
    raw = _small("CORE-3")
    raw["simulation_tick_ms"] = value
    normalized = normalize_rta4_campaign_v5(raw)
    assert normalized["normalized_scientific_config"][
        "simulation_tick_ms"
    ] == value


def test_simulation_tick_two_maps_exact_energy_to_two_ms_intervals():
    material = exact_service_material_v5(
        _normalized("CORE-3")["service_curve"], 2,
    )
    projection = core3_simulation_projection_v5(
        exact_service_material_identity=material.identity,
        harvest_trace=(material.harvest_trace[0], Fraction(1)),
        simulation_tick_ms=2,
    )
    assert [
        (segment["start_ms"], segment["end_ms"])
        for segment in projection["segments"]
    ] == [(0, 2), (2, 4)]
    assert projection["segments"][1]["energy_per_tick_j"] == "1"
    assert projection["segments"][1]["power_w"] == "500"


def test_core3_tick_changes_math_identity_but_not_pure_curve_identity():
    first = _normalized("CORE-3")
    raw = _small("CORE-3")
    raw["simulation_tick_ms"] = 2
    second = normalize_rta4_campaign_v5(raw)
    first_record = next(iter_formal_plan_v5(
        first["normalized_scientific_config"], first["task_sources"],
        first["service_curve"],
    ))
    second_record = next(iter_formal_plan_v5(
        second["normalized_scientific_config"], second["task_sources"],
        second["service_curve"],
    ))
    assert first["service_curve"].identity == second["service_curve"].identity
    assert first_record.mathematical_request_id != (
        second_record.mathematical_request_id
    )
    assert first_record.material["service_material"]["material_identity"] == (
        second_record.material["service_material"]["material_identity"]
    )
    assert first_record.material["service_material"]["simulation_projection"][
        "simulation_projection_identity"
    ] != second_record.material["service_material"]["simulation_projection"][
        "simulation_projection_identity"
    ]


def test_non_core3_rejects_simulation_tick_ms():
    raw = _small("CORE-1")
    raw["simulation_tick_ms"] = 1
    with pytest.raises(RTA4FormalConfigV5Error):
        normalize_rta4_campaign_v5(raw)


def test_core3_example_preflight_reports_explicit_simulation_tick_ms():
    summary = preflight_campaign_v5(
        "configs/v9_3_rta4_core3_exact_service_v5_example_UNAUTHORIZED.yaml"
    )
    assert summary["simulation_tick_ms"] == 1
    assert summary["execution_started"] is False
    assert "UNAUTHORIZED" in summary["formal_campaign_authorization_status"]


def test_core4_keeps_v3_one_factor_at_a_time_conditions():
    normalized = _normalized("CORE-4")
    grid = normalized["v3_scientific_config"]
    conditions = core4_conditions_v3(grid)
    assert [row["axis"] for row in conditions] == [
        "baseline", "e0", "service_scale", "power_scale",
        "deadline_slack_fraction",
    ]
    baseline = conditions[0]
    for row in conditions[1:]:
        assert sum(
            row[key] != baseline[key]
            for key in (
                "e0", "service_scale", "power_scale",
                "deadline_slack_fraction",
            )
        ) == 1


def test_core5b_worker_axis_changes_execution_only():
    normalized = _normalized("CORE-5B")
    records = list(iter_formal_plan_v5(
        normalized["normalized_scientific_config"],
        normalized["task_sources"], normalized["service_curve"],
    ))
    assert len(records) == 4
    pairs = [records[index:index + 2] for index in range(0, len(records), 2)]
    for pair in pairs:
        assert pair[0].mathematical_request_id == pair[1].mathematical_request_id
        assert pair[0].execution_id != pair[1].execution_id


def test_core5a_rejects_power_or_noninteger_time_scaling_drift():
    raw = _small("CORE-5A")
    raw["task_sources"][-1]["task_source"]["parameters"]["task_templates"][0][
        "power"
    ] = ["1/5"]
    with pytest.raises(RTA4FormalConfigV5Error, match="unchanged power"):
        normalize_rta4_campaign_v5(raw)


def test_v5_rejects_missing_unknown_float_and_noncanonical_science():
    raw = _small("CORE-1")
    raw.pop("service_curve")
    with pytest.raises(RTA4FormalConfigV5Error):
        normalize_rta4_campaign_v5(raw)
    raw = _small("CORE-1")
    raw["implicit_service"] = True
    with pytest.raises(RTA4FormalConfigV5Error):
        normalize_rta4_campaign_v5(raw)
    raw = _small("CORE-1")
    raw["e0"] = [0.5]
    with pytest.raises(RTA4FormalConfigV5Error, match="float"):
        normalize_rta4_campaign_v5(raw)
    raw = _small("CORE-1")
    raw["e0"] = ["2/4"]
    with pytest.raises(RTA4FormalConfigV5Error, match="canonical"):
        normalize_rta4_campaign_v5(raw)


def test_runtime_changes_do_not_change_v5_scientific_identity():
    first = _small("CORE-1")
    first["runtime"] = {"worker_count": 1, "max_in_flight": 1}
    second = deepcopy(first)
    second["runtime"] = {"worker_count": 2, "max_in_flight": 4}
    first_n = normalize_rta4_campaign_v5(first)
    second_n = normalize_rta4_campaign_v5(second)
    assert first_n["normalized_scientific_config"] == second_n[
        "normalized_scientific_config"
    ]


def test_linear_curve_is_also_accepted_without_v4_identity_reuse():
    raw = _small("CORE-1")
    raw["service_curve"] = {
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "1/10",
        "time_unit": "tick",
    }
    normalized = normalize_rta4_campaign_v5(raw)
    assert normalized["service_curve"].model == EXACT_LINEAR_SERVICE_CURVE_V1


def test_v5_adapter_reaches_the_unchanged_math_kernel_on_a_micro_replay():
    normalized = _normalized("CORE-1")
    source = normalized["task_sources"][0].source
    result = execute_normalized_taskset_v5(
        taskset=source.tasksets[0],
        processors=source.processors,
        task_source_identity=source.identity,
        taskset_store_identity="2" * 64,
        production_build_manifest_identity="3" * 64,
        service_curve=normalized["service_curve"],
        e0="10",
        method="CW_THETA_CW",
        timeout_seconds=2,
    )
    assert result["result"]["method_id"] == "CW_THETA_CW"
    assert result["result"]["solver_status"] == "COMPLETED"
    assert result["result"]["certification_status"] == "CERTIFIED_TASKSET"
    assert len(result["kernel_result_hash"]) == 64
    assert len(result["mathematical_result_hash"]) == 64


def test_preflight_does_not_call_solver(monkeypatch):
    def forbidden_solver(*_args, **_kwargs):
        raise AssertionError("preflight called the RTA solver")

    monkeypatch.setattr(
        "experiments.v9_3.rta4_formal_execution.dispatch_formal_rta",
        forbidden_solver,
    )
    summary = preflight_campaign_v5(
        "configs/v9_3_rta4_core1_exact_service_v5_example_UNAUTHORIZED.yaml"
    )
    assert summary["execution_started"] is False


@pytest.mark.parametrize("core", [
    "CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B",
])
def test_all_six_cores_select_their_named_lightweight_dispatch(
    core, tmp_path, monkeypatch,
):
    calls = []

    def fake_dispatch(_campaign, record, _operation):
        calls.append((record.core, record.kind, record.execution_id))
        return {
            "solver_status": "COMPLETED",
            "taskset_certification_status": "CERTIFIED_TASKSET",
            "taskset_proven": True,
            "attempts": [{"status": "COMPLETED"}],
        }

    monkeypatch.setattr(
        local_execution_v5, "_dispatch_existing_worker_v5", fake_dispatch,
    )
    summary = execute_loaded_campaign_v5(
        _loaded(core, tmp_path / core.lower()),
        acknowledge_not_for_paper=True,
        max_records=1,
        dispatchers=CORE_EXECUTION_DISPATCH_V5,
    )
    assert calls and calls[0][0] == core
    assert summary["processed_records"] == 1
    assert summary["execution_started"] is True
    assert summary["formal_campaign_started"] is False
    assert summary["paper_result_authorized"] is False
    assert summary["not_for_paper"] is True
    terminal = next(
        (tmp_path / core.lower() / "local_terminal_results_v5").glob("*.json")
    )
    row = json.loads(terminal.read_text(encoding="utf-8"))
    assert row["core"] == core
    assert row["not_for_paper"] is True


def test_core4_ofat_service_scale_is_materialized_exactly_once(tmp_path):
    campaign = _loaded("CORE-4", tmp_path / "core4-scale")
    records = list(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    baseline = next(
        row for row in records
        if row.material["v3_grid_material"]["axis"] == "baseline"
    )
    scaled = next(
        row for row in records
        if row.material["v3_grid_material"]["axis"] == "service_scale"
        and row.material["v3_grid_material"]["axis_value"] == "2"
    )
    _, _, baseline_context, _ = local_execution_v5._prepared_record_material(
        campaign, baseline,
    )
    _, _, scaled_context, _ = local_execution_v5._prepared_record_material(
        campaign, scaled,
    )
    baseline_binding = baseline_context.binding_for(baseline.record_id)
    scaled_binding = scaled_context.binding_for(scaled.record_id)
    baseline_service = baseline_context.service_materials[
        baseline_binding["service_material_identity"]
    ]
    scaled_service = scaled_context.service_materials[
        scaled_binding["service_material_identity"]
    ]
    scaled_provenance = json.loads(scaled_service.immutable_provenance_json)
    assert scaled_provenance["service_curve"]["rate"] == "1/5"
    assert scaled.effective_service_identity != baseline.effective_service_identity
    assert scaled_binding["task_energy_material_identity"] == baseline_binding[
        "task_energy_material_identity"
    ]
    assert scaled_service.beta(2) == baseline_service.beta(2) * 2


def test_local_execution_requires_explicit_not_for_paper_ack(tmp_path):
    with pytest.raises(
        RTA4LocalExecutionV5Error, match="acknowledge_not_for_paper"
    ):
        execute_loaded_campaign_v5(
            _loaded("CORE-1", tmp_path / "run"),
            acknowledge_not_for_paper=False,
            max_records=1,
        )


def test_local_execution_cli_rejects_missing_not_for_paper_ack(tmp_path):
    assert runner_v5_main([
        "--campaign",
        "configs/v9_3_rta4_core1_exact_service_v5_example_UNAUTHORIZED.yaml",
        "--execute-local",
        "--output-root",
        str(tmp_path / "unused"),
        "--max-records",
        "0",
    ]) == 2


@pytest.mark.parametrize("result", [
    {
        "solver_status": "TIMEOUT",
        "taskset_certification_status": "NOT_CERTIFIED_TASKSET",
        "taskset_proven": False,
        "attempts": [
            {"attempt_index": 0, "status": "TIMEOUT"},
            {"attempt_index": 1, "status": "TIMEOUT"},
        ],
    },
    {
        "solver_status": "COMPLETED",
        "taskset_certification_status": "UNPROVEN_TASKSET",
        "taskset_proven": False,
        "attempts": [{"attempt_index": 0, "status": "COMPLETED"}],
    },
    {
        "solver_status": "INTERNAL_ERROR",
        "taskset_certification_status": "NOT_CERTIFIED_TASKSET",
        "taskset_proven": False,
        "failure_reason": "injected failure",
        "attempts": [{"attempt_index": 0, "status": "INTERNAL_ERROR"}],
    },
])
def test_local_execution_preserves_timeout_unproven_and_error_results(
    result, tmp_path,
):
    dispatchers = {
        name: (lambda _campaign, _record, _operation: deepcopy(result))
        for name in CORE_EXECUTION_DISPATCH_V5
    }
    root = tmp_path / result["solver_status"].lower()
    execute_loaded_campaign_v5(
        _loaded("CORE-1", root),
        acknowledge_not_for_paper=True,
        max_records=1,
        dispatchers=dispatchers,
    )
    terminal = next((root / "local_terminal_results_v5").glob("*.json"))
    observed = json.loads(terminal.read_text(encoding="utf-8"))["result"]
    assert observed == result


def test_local_execution_resume_uses_terminal_and_checkpoint(tmp_path):
    calls = []

    def fake_dispatch(_campaign, record, _operation):
        calls.append(record.execution_id)
        return {"solver_status": "COMPLETED", "taskset_proven": True}

    dispatchers = {
        name: fake_dispatch for name in CORE_EXECUTION_DISPATCH_V5
    }
    root = tmp_path / "resume"
    campaign = _loaded("CORE-1", root)
    first = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        max_records=1,
        dispatchers=dispatchers,
    )
    assert first["processed_records"] == 1
    second = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        resume=True,
        max_records=1,
        dispatchers=dispatchers,
    )
    assert second["processed_records"] == 1
    assert len(calls) == 2
    checkpoint = json.loads(
        (root / "local_checkpoint_v5.json").read_text(encoding="utf-8")
    )
    assert len(checkpoint["completed_execution_ids"]) == 2


@pytest.mark.parametrize("core", [
    "CORE-1", "CORE-2", "CORE-4", "CORE-5A", "CORE-5B",
])
def test_pure_rta_cores_reach_existing_v3_worker_and_rta_executor(
    core, tmp_path, monkeypatch,
):
    requests = []
    final_solver_calls = []
    existing_worker = local_execution_v5.execute_worker_request_v3

    def lowest_boundary_solver(*, analysis_id, method, analysis_input):
        final_solver_calls.append((analysis_id, method, analysis_input))
        completed = SimpleNamespace(value="COMPLETED")
        certified = SimpleNamespace(value="CERTIFIED")
        task_rows = tuple(
            SimpleNamespace(
                solver_status=completed,
                certification_status=certified,
                candidate_response_time=task.wcet,
                checked_w_count=1,
                checked_q_count=1,
                checked_h_count=1,
                failure_reason=None,
                witness_sequence=(),
                task_id=task.name,
                priority_rank=index,
            )
            for index, task in enumerate(analysis_input.tasks)
        )
        return SimpleNamespace(
            task_results=task_rows,
            solver_status=completed,
            analysis_certification_status=SimpleNamespace(
                value="CERTIFIED_TASKSET"
            ),
            taskset_proven=True,
            failure_reason=None,
            mechanism_telemetry=(),
            analysis_id=analysis_id,
            method_id=SimpleNamespace(value=method),
        )

    def observe_existing_worker(request):
        requests.append(request)
        return existing_worker(request)

    monkeypatch.setattr(
        local_execution_v5, "execute_worker_request_v3", observe_existing_worker,
    )
    monkeypatch.setattr(
        formal_execution, "dispatch_formal_rta", lowest_boundary_solver,
    )
    root = tmp_path / f"real-worker-{core.lower()}"
    campaign = _loaded(core, root)
    planned = next(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    summary = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        max_records=1,
        dispatchers=CORE_EXECUTION_DISPATCH_V5,
    )
    assert len(requests) == 1
    assert len(final_solver_calls) == 1
    assert type(requests[0]) is V3WorkerRequest
    assert requests[0].record.core == core
    assert requests[0].record.record_id == planned.record_id
    assert requests[0].record.execution_id == planned.execution_id
    assert len(requests[0].certificate.taskset_id) == 64
    assert len(requests[0].certificate.taskset_source_sha256) == 64
    binding = requests[0].run_context.binding_for(requests[0].record.record_id)
    assert binding["task_energy_material_identity"]
    assert binding["service_material_identity"]
    assert planned.material["taskset_content_sha256"]
    assert planned.material["task_order_sha256"]
    assert planned.configured_service_identity
    assert planned.effective_service_identity
    assert planned.mathematical_request_id
    assert summary["processed_records"] == 1
    assert summary["not_for_paper"] is True
    terminal = next((root / "local_terminal_results_v5").glob("*.json"))
    row = json.loads(terminal.read_text(encoding="utf-8"))
    assert row["taskset_identity"] == planned.taskset_identity
    assert row["configured_service_identity"] == (
        planned.configured_service_identity
    )
    assert row["effective_service_identity"] == planned.effective_service_identity
    assert row["not_for_paper"] is True
    result = row["result"]
    assert result["solver_status"] == "COMPLETED"
    assert result["taskset_certification_status"] == "CERTIFIED_TASKSET"
    assert result["attempts"][0]["status"] == "COMPLETED"
    assert result["attempts"][0]["error_classification"] == "NONE"
    if core == "CORE-2":
        grid = planned.material["v3_grid_material"]
        assert grid["source"] == campaign.v3_scientific_config["source"]
        assert planned.material["task_source_identity"]
        assert planned.taskset_identity
        assert planned.effective_service_identity
        assert grid["exact_e0"] == "0"
        assert grid["method"] in {"CW_D", "SEQ_D"}
        assert campaign.v3_scientific_config["referenced_recursive_methods"] == [
            "LOC_THETA_LOC", "PH_THETA_PH",
        ]


def test_core3_reaches_existing_worker_and_only_mocks_external_launch(
    tmp_path, monkeypatch,
):
    requests = []
    launches = []
    existing_worker = local_execution_v5.execute_worker_request_v3

    def observe_existing_worker(request):
        requests.append(request)
        return existing_worker(request)

    def fake_external_launch(command, **kwargs):
        assert "capture_output" not in kwargs
        assert kwargs["stdout"] is local_execution_v5.subprocess.DEVNULL
        assert kwargs["stderr"] is local_execution_v5.subprocess.PIPE
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 2
        trace_path = Path(command[command.index("-t") + 1])
        semantic_hash = command[
            command.index("--taskset-semantic-hash") + 1
        ]
        horizon = int(command[3])
        system = yaml.safe_load(Path(command[1]).read_text(encoding="utf-8"))
        projection_path = Path(command[1]).with_name(
            "service_projection_v5.json"
        )
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        segments = system["harvesting"]["scaled_piecewise"]["segments"]
        assert projection["simulation_tick_ms"] == 2
        assert segments[0]["start_ms"] == 0
        assert segments[0]["end_ms"] % 2 == 0
        assert all(segment["end_ms"] % 2 == 0 for segment in segments)
        assert [segment["multiplier"] for segment in segments] == [
            float(Fraction(segment["power_w"]))
            for segment in projection["segments"]
        ]
        trace_path.write_text(json.dumps({
            "events": [],
            "trace_schema_version": 2,
            "run_id": command[command.index("--run-id") + 1],
            "taskset_semantic_hash": semantic_hash,
            "configured_scheduler": "gpfp_asap_block",
            "expected_simulation_horizon_ms": horizon,
            "observed_simulation_end_ms": horizon,
            "simulation_completed": True,
            "simulation_completion_reason": "reached_horizon",
        }) + "\n", encoding="utf-8")
        launches.append({
            "command": command,
            "projection": projection,
            "segments": segments,
        })
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        local_execution_v5, "execute_worker_request_v3", observe_existing_worker,
    )
    monkeypatch.setattr(local_execution_v5.subprocess, "run", fake_external_launch)
    root = tmp_path / "core3-worker-chain"
    campaign = _loaded("CORE-3", root, simulation_tick_ms=2)
    planned = list(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    simulation_record = next(row for row in planned if row.kind == "simulation")
    summary = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        max_records=len(planned),
        dispatchers=CORE_EXECUTION_DISPATCH_V5,
    )
    assert launches and launches[0]["projection"][
        "simulation_projection_identity"
    ] == simulation_record.material["service_material"]["simulation_projection"][
        "simulation_projection_identity"
    ]
    assert any(type(request) is V3WorkerRequest for request in requests)
    simulation_request = next(
        request for request in requests if request.record.kind == "simulation"
    )
    binding = simulation_request.run_context.binding_for(
        simulation_request.record.record_id
    )
    assert binding["task_energy_material_identity"]
    assert binding["service_material_identity"]
    assert binding["simulation_tick_ms"] == 2
    assert summary["not_for_paper"] is True
    terminal = root / "local_terminal_results_v5" / (
        simulation_record.execution_id + ".json"
    )
    row = json.loads(terminal.read_text(encoding="utf-8"))
    assert row["simulation_tick_ms"] == 2
    assert row["simulation_projection_identity"] == binding[
        "simulation_projection_identity"
    ]
    assert row["result"]["status"] == "COMPLETED"
    assert row["result"]["result"]["simulation_tick_ms"] == 2
    assert row["not_for_paper"] is True


@pytest.mark.parametrize(("stderr", "detail"), [
    ("  bounded failure  ", "bounded failure"),
    (None, ""),
])
def test_core3_simulator_failure_uses_only_bounded_stderr(
    tmp_path, monkeypatch, stderr, detail,
):
    campaign = _loaded("CORE-3", tmp_path / "campaign")
    planned = list(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    plan_record = next(row for row in planned if row.kind == "simulation")
    record, certificate, context, _ = local_execution_v5._prepared_record_material(
        campaign, plan_record,
    )
    simulator = tmp_path / "runtime" / "rtsim"
    output = tmp_path / "output"
    observed = {}

    def fail_external_launch(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=23, stderr=stderr)

    monkeypatch.setattr(
        local_execution_v5.subprocess, "run", fail_external_launch,
    )
    executor = local_execution_v5.ExactServiceSimulationExecutorV5(
        {},
        run_context=context,
        production_manifest={"simulator_path": str(simulator)},
        system_config_path=(
            Path(__file__).resolve().parents[1]
            / "system_config_unified_template.yml"
        ),
        energy_support_path=tmp_path / "unused-energy-support",
        output_root=output,
        simulation_timeout_seconds=37,
    )

    with pytest.raises(RTA4LocalExecutionV5Error) as exc_info:
        executor(record, certificate)
    assert str(exc_info.value) == f"CORE-3 simulator exited 23: {detail}"
    failure = local_execution_v5._test_failure_result_v5(
        plan_record, str(exc_info.value), malformed=False,
    )
    assert failure["failure_reason"] == str(exc_info.value)
    assert failure["result"]["failure_reason"] == str(exc_info.value)

    run_root = (
        output / "bounded_core3_simulations_v5" / record.execution_id
    )
    _, window, _ = local_execution_v5.build_formal_release_projection_v2(
        certificate, str(record.material["release_mode"]),
    )
    assert observed["command"] == [
        str(simulator),
        str(run_root / "system_config_v5.yaml"),
        str(run_root / "taskset_v5.yaml"),
        str(window.observation_horizon),
        "-t", str(run_root / "trace_v5.json"),
        "--run-id", f"rta4-v5-{record.execution_id[:16]}",
        "--taskset-semantic-hash", certificate.taskset_hash,
        "--semantic-traces",
    ]
    assert observed["kwargs"] == {
        "stdout": local_execution_v5.subprocess.DEVNULL,
        "stderr": local_execution_v5.subprocess.PIPE,
        "text": True,
        "timeout": 37,
        "check": False,
    }


def test_terminal_complete_is_distinct_from_clean_complete(tmp_path):
    result = {
        "solver_status": "INTERNAL_ERROR",
        "taskset_certification_status": "ERROR",
        "taskset_proven": False,
        "attempts": [{"attempt_index": 0, "status": "INTERNAL_ERROR"}],
    }
    dispatchers = {
        core: (lambda _campaign, _record, _operation: deepcopy(result))
        for core in CORE_EXECUTION_DISPATCH_V5
    }
    summary = execute_loaded_campaign_v5(
        _loaded("CORE-1", tmp_path / "terminal-not-clean"),
        acknowledge_not_for_paper=True,
        dispatchers=dispatchers,
    )
    assert summary["terminal_count"] == summary["expected_count"] == 4
    assert summary["terminal_complete"] is True
    assert summary["complete"] is True
    assert summary["internal_error_count"] == 4
    assert summary["clean_complete"] is False


def test_one_dispatch_exception_writes_one_internal_terminal(tmp_path):
    def dispatch(_campaign, record, _operation):
        if record.ordinal == 1:
            raise RuntimeError("injected dispatcher failure")
        return _clean_rta_result()

    root = tmp_path / "one-internal"
    summary = execute_loaded_campaign_v5(
        _loaded("CORE-2", root),
        acknowledge_not_for_paper=True,
        dispatchers={core: dispatch for core in CORE_EXECUTION_DISPATCH_V5},
    )
    assert summary["expected_count"] == 2
    assert summary["terminal_count"] == 2
    assert summary["terminal_complete"] is True
    assert summary["complete"] is True
    assert summary["internal_error_count"] == 1
    assert summary["clean_complete"] is False
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (root / "local_terminal_results_v5").glob("*.json")
    ]
    assert sum(
        row["result"]["solver_status"] == "INTERNAL_ERROR" for row in rows
    ) == 1


def test_nonmapping_dispatch_result_is_terminal_but_malformed(tmp_path):
    dispatchers = {
        core: (lambda _campaign, _record, _operation: object())
        for core in CORE_EXECUTION_DISPATCH_V5
    }
    summary = execute_loaded_campaign_v5(
        _loaded("CORE-2", tmp_path / "malformed"),
        acknowledge_not_for_paper=True,
        dispatchers=dispatchers,
    )
    assert summary["terminal_complete"] is True
    assert summary["internal_error_count"] == 2
    assert summary["malformed_result_count"] == 2
    assert summary["clean_complete"] is False


@pytest.mark.parametrize(
    (
        "attempts", "solver_status", "attempt_timeout",
        "terminal_timeout", "clean",
    ),
    [
        (
            [
                {"attempt_index": 0, "status": "TIMEOUT"},
                {"attempt_index": 1, "status": "COMPLETED"},
            ],
            "COMPLETED",
            4,
            0,
            True,
        ),
        (
            [
                {"attempt_index": 0, "status": "TIMEOUT"},
                {"attempt_index": 1, "status": "TIMEOUT"},
            ],
            "TIMEOUT",
            8,
            4,
            False,
        ),
    ],
)
def test_attempt_timeout_and_terminal_timeout_are_counted_separately(
    attempts, solver_status, attempt_timeout, terminal_timeout, clean,
    tmp_path,
):
    result = {
        **_clean_rta_result(attempts=attempts),
        "solver_status": solver_status,
        "taskset_proven": solver_status == "COMPLETED",
    }
    dispatchers = {
        core: (lambda _campaign, _record, _operation: deepcopy(result))
        for core in CORE_EXECUTION_DISPATCH_V5
    }
    summary = execute_loaded_campaign_v5(
        _loaded("CORE-1", tmp_path / f"timeout-{solver_status.lower()}"),
        acknowledge_not_for_paper=True,
        dispatchers=dispatchers,
    )
    assert summary["attempt_timeout_count"] == attempt_timeout
    assert summary["terminal_timeout_count"] == terminal_timeout
    assert summary["clean_complete"] is clean


def test_bounded_smoke_reports_invocation_clean_without_claiming_full_complete(
    tmp_path,
):
    dispatchers = {
        core: (lambda _campaign, _record, _operation: _clean_rta_result())
        for core in CORE_EXECUTION_DISPATCH_V5
    }
    summary = execute_loaded_campaign_v5(
        _loaded("CORE-1", tmp_path / "bounded-clean"),
        acknowledge_not_for_paper=True,
        max_records=1,
        dispatchers=dispatchers,
    )
    assert summary["bounded_smoke"] is True
    assert summary["invocation_target_count"] == 1
    assert summary["invocation_terminal_count"] == 1
    assert summary["invocation_clean"] is True
    assert summary["terminal_complete"] is False
    assert summary["clean_complete"] is False


def test_resume_keeps_prior_internal_error_out_of_clean_complete(tmp_path):
    def first_dispatch(_campaign, record, _operation):
        if record.ordinal == 1:
            raise RuntimeError("persist this INTERNAL_ERROR")
        return _clean_rta_result()

    def forbidden_dispatch(*_args, **_kwargs):
        raise AssertionError("fully terminal resume dispatched new work")

    first_dispatchers = {
        core: first_dispatch for core in CORE_EXECUTION_DISPATCH_V5
    }
    forbidden_dispatchers = {
        core: forbidden_dispatch for core in CORE_EXECUTION_DISPATCH_V5
    }
    campaign = _loaded("CORE-1", tmp_path / "resume-internal")
    first = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        dispatchers=first_dispatchers,
    )
    assert first["terminal_complete"] is True
    assert first["internal_error_count"] == 1
    summary = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        resume=True,
        dispatchers=forbidden_dispatchers,
    )
    assert summary["processed_records"] == 0
    assert summary["invocation_target_count"] == 0
    assert summary["invocation_clean"] is True
    assert summary["internal_error_count"] == 1
    assert summary["terminal_complete"] is True
    assert summary["clean_complete"] is False


def test_core3_nonadmissible_observation_is_a_simulation_failure(tmp_path):
    def dispatch(_campaign, record, _operation):
        if record.kind != "simulation":
            return _clean_rta_result()
        return {
            "status": "COMPLETED",
            "result": {
                "simulation_status": "COMPLETED",
                "observed_status": "SIM_HORIZON_INSUFFICIENT",
            },
        }

    summary = execute_loaded_campaign_v5(
        (campaign := _loaded(
            "CORE-3", tmp_path / "core3-classification",
        )),
        acknowledge_not_for_paper=True,
        dispatchers={core: dispatch for core in CORE_EXECUTION_DISPATCH_V5},
    )
    simulation_count = sum(
        row.kind == "simulation" for row in iter_formal_plan_v5(
            campaign.normalized_scientific_config,
            campaign.task_sources,
            campaign.service_curve,
        )
    )
    assert summary["terminal_complete"] is True
    assert summary["simulation_failure_count"] == simulation_count
    assert summary["malformed_result_count"] == 0
    assert summary["clean_complete"] is False


@pytest.mark.parametrize(
    ("bounded", "clean_complete", "invocation_clean", "expected"),
    [
        (False, True, True, 0),
        (False, False, True, 1),
        (True, False, True, 0),
        (True, False, False, 1),
    ],
)
def test_cli_exit_code_uses_full_or_bounded_clean_status(
    bounded, clean_complete, invocation_clean, expected, monkeypatch,
):
    monkeypatch.setattr(
        runner_v5,
        "execute_local_campaign_v5",
        lambda *_args, **_kwargs: {
            "bounded_smoke": bounded,
            "clean_complete": clean_complete,
            "invocation_clean": invocation_clean,
        },
    )
    assert runner_v5.main([
        "--campaign", "unused-test-campaign.yml",
        "--execute-local",
        "--acknowledge-not-for-paper",
    ]) == expected


def test_v5_production_source_has_no_thread_pool_fallback():
    source = Path(local_execution_v5.__file__).read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" not in source
    assert "concurrent.futures" not in source


def test_v5_physical_group_uses_two_distinct_pinned_processes(tmp_path):
    topology = discover_cpu_topology_v3()
    if topology.physical_core_count < 2:
        pytest.skip("physical process probe requires two allowed cores")
    campaign = _loaded("CORE-1", tmp_path / "physical-two-processes")
    plan_records = tuple(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))[:2]
    operation = {
        "output_root": tmp_path / "physical-two-processes",
        "timeout_seconds": 2,
        "simulator_path": None,
    }
    bootstrap = local_execution_v5._worker_bootstrap_v5(
        campaign, plan_records, operation,
    )
    prepared = []
    for plan_record in plan_records:
        worker, certificate, context, _identity = (
            local_execution_v5._prepared_record_material(
                campaign, plan_record,
            )
        )
        prepared.append(PreparedPhysicalRecordV5(
            plan_record, worker, certificate, context,
        ))
    terminals = []
    evidence = execute_physical_group_v5(
        worker_count=2,
        selected_cores=topology.select(2),
        prepared_records=prepared,
        bootstrap=bootstrap,
        max_in_flight=2,
        terminal_callback=lambda record, result: terminals.append(
            (record, result)
        ),
        pool_factory=_physical_probe_pool_factory,
    )
    bindings = evidence["worker_affinity_bindings"]
    assert evidence["completed_record_count"] == 2
    assert evidence["max_concurrent_active_slots"] == 2
    assert len(evidence["worker_process_ids"]) == 2
    assert len({row["worker_pid"] for row in bindings}) == 2
    assert all(
        tuple(row["affinity_mask"]) == (row["logical_cpu_id"],)
        for row in bindings
    )
    assert len({
        (row["physical_package_id"], row["physical_core_id"])
        for row in bindings
    }) == 2
    assert all(
        terminal[1]["physical_core_binding_required"] is True
        for terminal in terminals
    )


def test_core3_records_execute_inside_a_physical_slot_process(tmp_path):
    topology = discover_cpu_topology_v3()
    root = tmp_path / "core3-physical-probe"
    campaign = _loaded("CORE-3", root)
    summary = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        _topology_discoverer=lambda: topology,
        _pool_factory=_physical_core3_probe_pool_factory,
    )
    plan_records = tuple(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    simulation_record = next(
        row for row in plan_records if row.kind == "simulation"
    )
    terminal = json.loads((
        root / "local_terminal_results_v5"
        / f"{simulation_record.execution_id}.json"
    ).read_text(encoding="utf-8"))
    worker_pid = terminal["result"]["result"]["probe_pid"]
    diagnostics = terminal["result"]["execution_attempt_diagnostics"]
    assert worker_pid != os.getpid()
    assert diagnostics[0]["worker_pid"] == worker_pid
    assert diagnostics[0]["affinity_mask"] == [
        diagnostics[0]["logical_cpu_id"]
    ]
    assert terminal["worker_backend"] == "PHYSICAL_CORE_PROCESS_SLOTS"
    assert summary["simulation_failure_count"] == 0
    assert summary["internal_error_count"] == 0
    assert summary["clean_complete"] is True


def test_v5_physical_timeout_replaces_slot_and_retry_can_finish(
    tmp_path, monkeypatch,
):
    topology = discover_cpu_topology_v3()
    campaign = _loaded("CORE-1", tmp_path / "physical-retry")
    plan_record = next(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    operation = {
        "output_root": tmp_path / "physical-retry",
        "timeout_seconds": 1,
        "simulator_path": None,
    }
    bootstrap = local_execution_v5._worker_bootstrap_v5(
        campaign, (plan_record,), operation,
    )
    worker, certificate, context, _identity = (
        local_execution_v5._prepared_record_material(campaign, plan_record)
    )

    def fake_timeout_projection(_bootstrap, request):
        return {
            "solver_status": "TIMEOUT",
            "taskset_certification_status": "TIMEOUT",
            "taskset_proven": False,
            "attempts": ({
                "attempt_index": request.attempt_index,
                "timeout_seconds": request.timeout_seconds,
                "status": "TIMEOUT",
                "runtime_wall_seconds": str(request.timeout_seconds),
                "runtime_cpu_seconds": "0",
                "peak_rss_bytes": 0,
                "error_classification": "TEST_HARD_TIMEOUT",
            },),
            "timeout_seconds": request.timeout_seconds,
            "runtime_wall_seconds": str(request.timeout_seconds),
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
        }

    monkeypatch.setattr(
        physical_execution_v5,
        "project_hard_timeout_result_v3",
        fake_timeout_projection,
    )
    terminals = []
    evidence = physical_execution_v5.execute_physical_group_v5(
        worker_count=1,
        selected_cores=topology.select(1),
        prepared_records=(PreparedPhysicalRecordV5(
            plan_record, worker, certificate, context,
        ),),
        bootstrap=bootstrap,
        max_in_flight=1,
        terminal_callback=lambda _record, result: terminals.append(result),
        pool_factory=_physical_retry_probe_pool_factory,
    )
    assert evidence["slot_replacement_count"] == 1
    assert evidence["timeout_kill_count"] == 1
    assert terminals[0]["solver_status"] == "COMPLETED"
    assert [row["status"] for row in terminals[0]["attempts"]] == [
        "TIMEOUT", "COMPLETED",
    ]
    assert [
        row["timed_out"]
        for row in terminals[0]["execution_attempt_diagnostics"]
    ] == [True, False]


def test_retry_protocol_failure_preserves_first_timeout_history(tmp_path):
    topology = discover_cpu_topology_v3()
    campaign = _loaded("CORE-1", tmp_path / "malformed-retry")
    plan_record = next(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    operation = {
        "output_root": tmp_path / "malformed-retry",
        "timeout_seconds": 1,
        "simulator_path": None,
    }
    bootstrap = local_execution_v5._worker_bootstrap_v5(
        campaign, (plan_record,), operation,
    )
    worker, certificate, context, _identity = (
        local_execution_v5._prepared_record_material(campaign, plan_record)
    )
    terminals = []
    execute_physical_group_v5(
        worker_count=1,
        selected_cores=topology.select(1),
        prepared_records=(PreparedPhysicalRecordV5(
            plan_record, worker, certificate, context,
        ),),
        bootstrap=bootstrap,
        max_in_flight=1,
        terminal_callback=lambda _record, result: terminals.append(result),
        pool_factory=_physical_malformed_retry_pool_factory,
    )
    result = terminals[0]
    assert result["solver_status"] == "INTERNAL_ERROR"
    assert result["protocol_malformed_result"] is True
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "TIMEOUT", "INTERNAL_ERROR",
    ]
    assert [
        row["attempt_index"]
        for row in result["execution_attempt_diagnostics"]
    ] == [0, 1]


def test_core5b_runs_scientific_worker_groups_sequentially(tmp_path):
    raw = _small("CORE-5B")
    raw["workers"] = [1, 2, 4]
    root = tmp_path / "core5b-groups"
    campaign = _loaded_from_raw(raw, root, runtime={
        "output_root": str(root),
        "worker_count": 4,
        "max_in_flight": 4,
        "timeout_seconds": 2,
    })
    lifecycle = []

    class FakePhysicalPool:
        def __init__(self, selected_cores):
            self.selected_cores = tuple(selected_cores)
            self.events = []
            self.busy = set()
            self.worker_affinity_bindings = []
            self.worker_intervals = []
            self.slot_replacement_count = 0
            self.timeout_kill_count = 0

        def start(self):
            lifecycle.append(("start", len(self.selected_cores)))
            self.worker_affinity_bindings.extend(
                WorkerDiagnosticV3(
                    3000 + len(self.selected_cores) * 10 + slot_id,
                    core.logical_cpu_id,
                    core.physical_package_id,
                    core.physical_core_id,
                    (core.logical_cpu_id,),
                    slot_id,
                    0,
                ).as_dict()
                for slot_id, core in enumerate(self.selected_cores)
            )

        @property
        def idle_slot_ids(self):
            return tuple(
                slot_id for slot_id in range(len(self.selected_cores))
                if slot_id not in self.busy
            )

        def submit(self, slot_id, task_id, request, _timeout_seconds):
            self.busy.add(slot_id)
            binding = self.worker_affinity_bindings[slot_id]
            worker = WorkerDiagnosticV3(
                binding["worker_pid"],
                binding["logical_cpu_id"],
                binding["physical_package_id"],
                binding["physical_core_id"],
                tuple(binding["affinity_mask"]),
                binding["slot_id"],
                binding["worker_generation"],
            )
            started = time.monotonic_ns()
            finished = started + 100
            attempt = {
                "attempt_index": request.attempt_index,
                "timeout_seconds": request.timeout_seconds,
                "status": "COMPLETED",
                "runtime_wall_seconds": "0.0000001",
                "runtime_cpu_seconds": "0",
                "peak_rss_bytes": 0,
                "error_classification": "NONE",
            }
            response = V3AttemptResponse(
                request.record.record_id,
                request.record.execution_id,
                request.attempt_index,
                request.timeout_seconds,
                {
                    "solver_status": "COMPLETED",
                    "taskset_certification_status": "CERTIFIED_TASKSET",
                    "taskset_proven": True,
                    "attempts": (attempt,),
                    "timeout_seconds": request.timeout_seconds,
                    "runtime_wall_seconds": "0.0000001",
                    "runtime_cpu_seconds": "0",
                    "peak_rss_bytes": 0,
                },
            )
            self.events.extend((
                SlotStartedV3(slot_id, task_id, worker, started),
                SlotCompletionV3(
                    slot_id,
                    task_id,
                    worker,
                    started,
                    finished,
                    0.0,
                    response,
                ),
            ))

        def poll(self):
            event = self.events.pop(0)
            if isinstance(event, SlotCompletionV3):
                self.busy.remove(event.slot_id)
                self.worker_intervals.append({
                    **event.worker.as_dict(),
                    "task_id": event.task_id,
                    "attempt_started_monotonic_ns": (
                        event.started_monotonic_ns
                    ),
                    "attempt_finished_monotonic_ns": (
                        event.finished_monotonic_ns
                    ),
                })
            return event

        def shutdown(self):
            lifecycle.append(("shutdown", len(self.selected_cores)))

    def fake_pool_factory(selected_cores, **_ignored):
        return FakePhysicalPool(selected_cores)

    summary = execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        _topology_discoverer=lambda: _synthetic_topology(4),
        _pool_factory=fake_pool_factory,
    )
    assert lifecycle == [
        ("start", 1), ("shutdown", 1),
        ("start", 2), ("shutdown", 2),
        ("start", 4), ("shutdown", 4),
    ]
    assert summary["terminal_complete"] is True
    assert summary["clean_complete"] is True
    assert [
        row["worker_count"] for row in summary["physical_execution_groups"]
    ] == [1, 2, 4]
    assert all(
        row["elapsed_wall_seconds"] > 0
        and len(row["selected_physical_cores"]) == row["worker_count"]
        and row["worker_process_ids"]
        and row["worker_affinity_bindings"]
        for row in summary["physical_execution_groups"]
    )
    manifest = json.loads(
        (root / "local_run_manifest_v5.json").read_text(encoding="utf-8")
    )
    assert manifest["physical_core_binding_required"] is True
    assert manifest["topology_fingerprint"] == "synthetic-topology"
    assert [
        row["worker_count"] for row in manifest["physical_execution_groups"]
    ] == [1, 2, 4]


def test_core5b_insufficient_topology_fails_before_writing_terminals(tmp_path):
    raw = _small("CORE-5B")
    raw["workers"] = [1, 2, 4]
    root = tmp_path / "core5b-insufficient"
    campaign = _loaded_from_raw(raw, root, runtime={
        "output_root": str(root),
        "worker_count": 4,
        "max_in_flight": 4,
        "timeout_seconds": 2,
    })
    with pytest.raises(
        RTA4LocalExecutionV5Error, match="requested 4 physical",
    ):
        execute_loaded_campaign_v5(
            campaign,
            acknowledge_not_for_paper=True,
            _topology_discoverer=lambda: _synthetic_topology(2),
            _physical_group_executor=lambda **_kwargs: pytest.fail(
                "execution began despite insufficient physical cores"
            ),
        )
    assert not root.exists()


def test_core5b_runtime_worker_cap_must_cover_scientific_maximum(tmp_path):
    raw = _small("CORE-5B")
    raw["workers"] = [1, 2, 4]
    root = tmp_path / "core5b-cap"
    campaign = _loaded_from_raw(raw, root, runtime={
        "output_root": str(root),
        "worker_count": 2,
        "max_in_flight": 4,
        "timeout_seconds": 2,
    })
    with pytest.raises(
        RTA4LocalExecutionV5Error, match="below the scientific maximum",
    ):
        execute_loaded_campaign_v5(
            campaign,
            acknowledge_not_for_paper=True,
            _topology_discoverer=lambda: pytest.fail(
                "worker cap must fail before topology use"
            ),
        )
    assert not root.exists()


def test_resume_refuses_topology_fingerprint_drift_before_execution(tmp_path):
    root = tmp_path / "topology-drift"
    campaign = _loaded("CORE-1", root)

    def clean_group(**kwargs):
        for row in kwargs["prepared_records"]:
            kwargs["terminal_callback"](
                row.plan_record,
                _fake_physical_rta_result(
                    logical_cpu=0, worker_pid=1001,
                ),
            )
        return {
            "worker_count": kwargs["worker_count"],
            "requested_record_count": len(kwargs["prepared_records"]),
            "completed_record_count": len(kwargs["prepared_records"]),
        }

    execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=True,
        max_records=1,
        _topology_discoverer=lambda: _synthetic_topology(1, "topology-A"),
        _physical_group_executor=clean_group,
    )
    with pytest.raises(
        RTA4LocalExecutionV5Error, match="another V5 run",
    ):
        execute_loaded_campaign_v5(
            campaign,
            acknowledge_not_for_paper=True,
            resume=True,
            max_records=1,
            _topology_discoverer=lambda: _synthetic_topology(
                1, "topology-B",
            ),
            _physical_group_executor=lambda **_kwargs: pytest.fail(
                "resume executed despite topology drift"
            ),
        )


def test_execution_backend_and_physical_count_do_not_change_scientific_ids(
    tmp_path,
):
    base = _loaded("CORE-1", tmp_path / "unused")

    def clean_group(**kwargs):
        for row in kwargs["prepared_records"]:
            kwargs["terminal_callback"](
                row.plan_record,
                _fake_physical_rta_result(
                    logical_cpu=0,
                    worker_pid=2000 + kwargs["worker_count"],
                ),
            )
        return {
            "worker_count": kwargs["worker_count"],
            "requested_record_count": len(kwargs["prepared_records"]),
            "completed_record_count": len(kwargs["prepared_records"]),
            "selected_physical_cores": [
                row.as_dict() for row in kwargs["selected_cores"]
            ],
        }

    roots = [tmp_path / name for name in ("slots-1", "slots-2", "test-only")]
    campaign_one = replace(base, runtime={
        "output_root": str(roots[0]),
        "worker_count": 1,
        "max_in_flight": 1,
        "timeout_seconds": 2,
    })
    campaign_two = replace(base, runtime={
        "output_root": str(roots[1]),
        "worker_count": 2,
        "max_in_flight": 2,
        "timeout_seconds": 2,
    })
    execute_loaded_campaign_v5(
        campaign_one,
        acknowledge_not_for_paper=True,
        max_records=1,
        _topology_discoverer=lambda: _synthetic_topology(2, "same-host"),
        _physical_group_executor=clean_group,
    )
    execute_loaded_campaign_v5(
        campaign_two,
        acknowledge_not_for_paper=True,
        max_records=1,
        _topology_discoverer=lambda: _synthetic_topology(2, "same-host"),
        _physical_group_executor=clean_group,
    )
    test_dispatchers = {
        core: (lambda _campaign, _record, _operation: _clean_rta_result())
        for core in CORE_EXECUTION_DISPATCH_V5
    }
    execute_loaded_campaign_v5(
        base,
        acknowledge_not_for_paper=True,
        output_root=roots[2],
        max_records=1,
        dispatchers=test_dispatchers,
    )
    terminal_rows = [
        json.loads(next(
            (root / "local_terminal_results_v5").glob("*.json")
        ).read_text(encoding="utf-8"))
        for root in roots
    ]
    for field in (
        "mathematical_request_identity",
        "taskset_identity",
        "configured_service_identity",
        "effective_service_identity",
    ):
        assert len({row[field] for row in terminal_rows}) == 1
    manifests = [
        json.loads(
            (root / "local_run_manifest_v5.json").read_text(encoding="utf-8")
        )
        for root in roots
    ]
    assert [
        manifest["execution_backend"] for manifest in manifests
    ] == [
        "PHYSICAL_CORE_PROCESS_SLOTS",
        "PHYSICAL_CORE_PROCESS_SLOTS",
        "TEST_ONLY_EXPLICIT_DISPATCHERS",
    ]
    assert [
        len(manifest["physical_execution_groups"][0][
            "selected_physical_cores"
        ]) if manifest["physical_execution_groups"] else 0
        for manifest in manifests
    ] == [1, 2, 0]
    assert len({manifest["run_identity"] for manifest in manifests}) == 3
