from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)
from experiments.b4_priority_energy.energy_source_v5 import (
    B4EnergySourceV5Error,
    B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1,
    B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
    B4_PE_THREE_STAGE_SOURCE_V1,
    build_source_material_v5,
    build_task_energy_material_v5,
    normalize_energy_source_v5,
    normalize_taskset_binding_v5,
    render_system_config_v5,
)
from experiments.b4_priority_energy.manifest_v5 import (
    B4ManifestV5Error,
    PROTOCOL_V5,
    build_manifest_v5,
    execute_local_campaign_v5,
    load_campaign_v5,
    normalize_campaign_v5,
    preflight_campaign_v5,
)
from experiments.b4_priority_energy.generate_manifest_v5 import (
    main as generate_v5_main,
)
from experiments.b4_priority_energy import materialization_common as legacy
from experiments.v9_3.rta4_energy_service_v5 import normalize_energy_service_v5


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "configs/b4_pe_exact_service_v5_example_UNAUTHORIZED.yaml"
BASE = ROOT / "configs/examples/b4_pe_v5_base_taskset_example.yml"
FAKE_V5 = Path(__file__).resolve().parent / "fixtures/fake_rtsim_v5.py"


def _exact_source(
    rate="1/1000", initial="1", maximum="100", task_scale="1",
):
    return {
        "model": B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        "service_curve": {
            "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            "rate": rate,
            "latency": "2",
            "time_unit": "tick",
        },
        "service_scale": "1",
        "task_energy_scale": task_scale,
        "initial_energy": initial,
        "max_energy": maximum,
    }


def _binding():
    return normalize_taskset_binding_v5({
        "taskset_id": "b4-v5-test-000",
        "taskset_identity": "1" * 64,
        "base_taskset_path": "configs/examples/b4_pe_v5_base_taskset_example.yml",
        "execution_taskset_path": (
            "configs/examples/b4_pe_v5_execution_taskset_rho1_example.yml"
        ),
    })


def _load_raw_campaign(tmp_path, raw, name="campaign-v5.yml"):
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8",
    )
    return load_campaign_v5(path)


def test_exact_source_material_maps_tick_intervals_to_generic_piecewise_runtime():
    source = normalize_energy_source_v5(
        _exact_source(), horizon_ms=10, tick_ms=1,
    )
    material = build_source_material_v5(source, _binding())
    assert [
        (row["start_time_ms"], row["end_time_ms"], row["power_w"])
        for row in material.exact_segments
    ] == [(0, 2, "0"), (2, 10, "1")]
    assert material.runtime_source == {
        "source": "scaled_piecewise",
        "scaled_piecewise": {
            "scale_w": 1.0,
            "segments": [
                {"start_ms": 0, "end_ms": 2, "multiplier": 0.0},
                {"start_ms": 2, "end_ms": 10, "multiplier": 1.0},
            ],
        },
    }
    rendered = yaml.safe_load(
        render_system_config_v5(material, "gpfp_asap_block")
    )
    assert "priority_energy" not in rendered
    assert rendered["harvesting"] == material.runtime_source
    assert rendered["cpu_islands"][0]["kernel"]["scheduler"] == "gpfp_asap_block"
    assert not {
        "day_of_year", "time_of_day_ms", "base_harvesting_rate",
        "harvesting_scale", "use_real_solar_data", "solar_data_file",
    }.intersection(rendered["energy_management"])


def test_rta_and_b4_share_the_same_configured_curve_identity():
    raw_curve = _exact_source()["service_curve"]
    rta_curve = normalize_energy_service_v5(raw_curve)
    b4_source = normalize_energy_source_v5(
        _exact_source(), horizon_ms=10, tick_ms=1,
    )
    assert b4_source.configured_curve is not None
    assert b4_source.configured_curve.identity == rta_curve.identity
    assert b4_source.configured_curve.harvest_trace(10) == rta_curve.harvest_trace(10)


def test_exact_source_and_bounds_have_separate_identities():
    binding = _binding()
    first = build_source_material_v5(
        normalize_energy_source_v5(
            _exact_source(), horizon_ms=10, tick_ms=1,
        ), binding,
    )
    changed_rate = build_source_material_v5(
        normalize_energy_source_v5(
            _exact_source(rate="1/500"), horizon_ms=10, tick_ms=1,
        ), binding,
    )
    changed_e0 = build_source_material_v5(
        normalize_energy_source_v5(
            _exact_source(initial="2"), horizon_ms=10, tick_ms=1,
        ), binding,
    )
    assert first.source_identity != changed_rate.source_identity
    assert first.source_identity == changed_e0.source_identity
    assert first.energy_bounds_identity != changed_e0.energy_bounds_identity
    assert first.configured_energy_system_identity != (
        changed_e0.configured_energy_system_identity
    )
    assert first.configured_energy_system_identity_scope == (
        B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1
    )
    first_yaml = render_system_config_v5(first, "gpfp_asap_block")
    second_yaml = render_system_config_v5(first, "gpfp_seq_block")
    assert "algorithm" not in first.descriptor
    assert yaml.safe_load(first_yaml)["harvesting"] == yaml.safe_load(second_yaml)[
        "harvesting"
    ]
    assert first_yaml != second_yaml


def test_three_stage_mode_calls_the_frozen_v4_energy_contract():
    document = yaml.safe_load(BASE.read_text(encoding="utf-8"))
    source = normalize_energy_source_v5({
        "model": B4_PE_THREE_STAGE_SOURCE_V1,
        "lambda_E": "0.85",
    }, horizon_ms=30000, tick_ms=1)
    material = build_source_material_v5(
        source, _binding(), base_document=document,
    )
    expected = legacy.source_energy_contract(document, "0.85")
    assert material.initial_energy == expected["E0_j"]
    assert material.max_energy == expected["Emax_j"]
    assert material.trace_sha256 == legacy.offered_harvest_trace_sha256(
        expected["alpha_w"]
    )
    assert len(material.exact_segments) == 3


@pytest.mark.parametrize("raw", [
    {
        "model": B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        "service_curve": {
            "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            "rate": 0.1,
            "latency": "0",
            "time_unit": "tick",
        },
        "service_scale": "1", "task_energy_scale": "1",
        "initial_energy": "1", "max_energy": "2",
    },
    {
        "model": B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        "service_curve": {
            "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            "rate": "1", "latency": "0", "time_unit": "tick",
        },
        "service_scale": "1", "task_energy_scale": "1",
        "initial_energy": "2", "max_energy": "1",
    },
])
def test_b4_exact_source_rejects_float_and_invalid_bounds(raw):
    with pytest.raises(B4EnergySourceV5Error):
        normalize_energy_source_v5(raw, horizon_ms=10, tick_ms=1)


def test_three_stage_rejects_nonfrozen_horizon_and_lambda():
    with pytest.raises(B4EnergySourceV5Error):
        normalize_energy_source_v5({
            "model": B4_PE_THREE_STAGE_SOURCE_V1, "lambda_E": "0.85",
        }, horizon_ms=10, tick_ms=1)
    with pytest.raises(B4EnergySourceV5Error):
        normalize_energy_source_v5({
            "model": B4_PE_THREE_STAGE_SOURCE_V1, "lambda_E": "0.9",
        }, horizon_ms=30000, tick_ms=1)


def test_example_preflight_gives_nine_algorithms_one_source_and_no_execution():
    campaign = load_campaign_v5(EXAMPLE)
    records, sources, task_materials, configs = build_manifest_v5(campaign)
    assert [record["algorithm"] for record in records] == PROTOCOL_V5[
        "algorithm_order"
    ]
    assert len(records) == len(configs) == 9
    assert len(sources) == 1
    assert len(task_materials) == 1
    assert len({record["source_identity"] for record in records}) == 1
    assert len({record["taskset_identity"] for record in records}) == 1
    assert len({
        record["task_energy_material_identity"] for record in records
    }) == 1
    assert all(record["execution_authorized"] is False for record in records)
    preview = preflight_campaign_v5(EXAMPLE)
    assert preview["manifest_record_count"] == 9
    assert preview["execution_started"] is False
    assert preview["configured_energy_system_materials"][0][
        "identity_scope"
    ] == B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1


@pytest.mark.parametrize("scale", ["1", "55", "11/2"])
def test_explicit_task_energy_scale_accepts_canonical_positive_rationals(scale):
    source = normalize_energy_source_v5(
        _exact_source(task_scale=scale), horizon_ms=10, tick_ms=1,
    )
    assert source.normalized_config["task_energy_scale"] == scale


@pytest.mark.parametrize("scale", [
    "0", "-1", True, 1, 1.0, "NaN", "infinity", "01", "1.0", "1e2",
    "2/4",
])
def test_explicit_task_energy_scale_rejects_invalid_or_noncanonical_values(scale):
    with pytest.raises(B4EnergySourceV5Error):
        normalize_energy_source_v5(
            _exact_source(task_scale=scale), horizon_ms=10, tick_ms=1,
        )


def test_task_energy_scale_changes_demand_material_not_service_curve_identity():
    campaign = load_campaign_v5(EXAMPLE)
    one = build_task_energy_material_v5(campaign.tasksets[0], "1")
    fifty_five = build_task_energy_material_v5(campaign.tasksets[0], "55")
    half_eleven = build_task_energy_material_v5(campaign.tasksets[0], "11/2")
    assert len({
        one.task_energy_material_identity,
        fifty_five.task_energy_material_identity,
        half_eleven.task_energy_material_identity,
    }) == 3
    assert one.descriptor["tasks"][0]["runtime_decimal"] == "1"
    assert fifty_five.descriptor["tasks"][0]["runtime_decimal"] == "55"
    assert half_eleven.descriptor["tasks"][0]["runtime_decimal"] == "5.5"
    first = normalize_energy_source_v5(
        _exact_source(task_scale="1"), horizon_ms=10, tick_ms=1,
    )
    changed = normalize_energy_source_v5(
        _exact_source(task_scale="55"), horizon_ms=10, tick_ms=1,
    )
    assert first.configured_curve.identity == changed.configured_curve.identity
    assert first.effective_curve.identity == changed.effective_curve.identity
    first_supply = build_source_material_v5(first, campaign.tasksets[0])
    changed_supply = build_source_material_v5(changed, campaign.tasksets[0])
    assert first_supply.source_identity == changed_supply.source_identity
    assert first_supply.configured_energy_system_identity != (
        changed_supply.configured_energy_system_identity
    )
    raw_one = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw_changed = deepcopy(raw_one)
    raw_changed["energy_source"]["task_energy_scale"] = "55"
    one_campaign = normalize_campaign_v5(raw_one)
    changed_campaign = normalize_campaign_v5(raw_changed)
    assert one_campaign["normalized_scientific_config_sha256"] != (
        changed_campaign["normalized_scientific_config_sha256"]
    )


def test_identity_layers_change_only_with_their_declared_scope(tmp_path):
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    scaled_raw = deepcopy(raw)
    scaled_raw["campaign_id"] = "b4-v5-task-scale-changed"
    scaled_raw["energy_source"]["task_energy_scale"] = "55"
    bounds_raw = deepcopy(raw)
    bounds_raw["campaign_id"] = "b4-v5-bounds-changed"
    bounds_raw["energy_source"]["initial_energy"] = "2"
    campaigns = [
        _load_raw_campaign(tmp_path, raw, "base.yml"),
        _load_raw_campaign(tmp_path, scaled_raw, "scaled.yml"),
        _load_raw_campaign(tmp_path, bounds_raw, "bounds.yml"),
    ]
    manifests = [build_manifest_v5(campaign) for campaign in campaigns]
    base_records, base_sources, base_tasks, _ = manifests[0]
    scaled_records, scaled_sources, scaled_tasks, _ = manifests[1]
    bounds_records, bounds_sources, bounds_tasks, _ = manifests[2]
    base_source, scaled_source, bounds_source = (
        base_sources[0], scaled_sources[0], bounds_sources[0]
    )
    assert base_source.service_curve_identity == scaled_source.service_curve_identity
    assert base_source.source_identity == scaled_source.source_identity
    assert base_tasks[0].task_energy_material_identity != (
        scaled_tasks[0].task_energy_material_identity
    )
    assert base_source.configured_energy_system_identity != (
        scaled_source.configured_energy_system_identity
    )
    assert base_records[0]["case_id"] != scaled_records[0]["case_id"]
    assert base_source.service_curve_identity == bounds_source.service_curve_identity
    assert base_source.source_identity == bounds_source.source_identity
    assert base_tasks[0].task_energy_material_identity == (
        bounds_tasks[0].task_energy_material_identity
    )
    assert base_source.configured_energy_system_identity != (
        bounds_source.configured_energy_system_identity
    )
    assert base_records[0]["case_id"] != bounds_records[0]["case_id"]
    first, second = base_records[:2]
    for field in (
        "service_curve_identity", "source_identity",
        "task_energy_material_identity", "configured_energy_system_identity",
    ):
        assert first[field] == second[field]
    assert first["case_id"] != second["case_id"]
    assert first["configured_energy_system_identity_scope"] == (
        B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1
    )
    assert PROTOCOL_V5["identity_scopes"][
        "configured_energy_system_identity"
    ] == B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1


def test_nine_algorithms_share_explicit_task_energy_scale_and_material():
    campaign = load_campaign_v5(EXAMPLE)
    records, _sources, materials, _configs = build_manifest_v5(campaign)
    assert {record["task_energy_scale"] for record in records} == {"1"}
    assert {record["task_energy_material_identity"] for record in records} == {
        materials[0].task_energy_material_identity
    }
    assert "55" not in campaign.energy_source.normalized_config.values()


@pytest.mark.parametrize("rate", ["33/10", "11/2"])
def test_exact_service_source_preserves_requested_rational_trace(rate):
    raw = _exact_source(rate=rate)
    raw["service_curve"]["latency"] = "0"
    source = normalize_energy_source_v5(raw, horizon_ms=4, tick_ms=1)
    material = build_source_material_v5(source, _binding())
    assert {
        row["energy_per_tick_j"] for row in material.exact_segments
    } == {rate}


def test_local_execution_crosses_full_existing_state_machine_and_resumes(
    tmp_path,
):
    from experiments.b4_priority_energy import inspect_execution

    fake_root = tmp_path / "fake-bundle"
    fake_fixture_directory = fake_root / "fixtures"
    fake_fixture_directory.mkdir(parents=True)
    simulator = fake_fixture_directory / "fake_rtsim_v5.py"
    shutil.copyfile(FAKE_V5, simulator)
    simulator.chmod(0o755)
    output = tmp_path / "run"
    campaign = load_campaign_v5(EXAMPLE)
    record = build_manifest_v5(campaign)[0][0]
    result = execute_local_campaign_v5(
        EXAMPLE,
        acknowledge_not_for_paper=True,
        output_root=output,
        simulator_binary=simulator,
        limit=1,
    )
    assert result["executor_summary"]["executed_cases"] == 1
    assert result["executor_summary"]["succeeded"] == 1
    assert result["execution_started"] is True
    assert result["formal_campaign_started"] is False
    assert result["paper_result_authorized"] is False
    assert result["not_for_paper"] is True
    result_path = output.joinpath(*Path(record["result_relpath"]).parts)
    assert result_path.is_file() and result_path.stat().st_size > 0
    fixture_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert fixture_result["fixture"] == (
        "B4_PE_V5_STATE_MACHINE_ONLY_NOT_SCHEDULING_EVIDENCE"
    )
    state_path = output / ".b4pe/state" / f"{record['case_id']}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_status"] == "succeeded"
    assert state["attempt_count"] == 1
    assert state["attempts"][0]["termination_reason"] == "succeeded"
    assert state["attempts"][0]["publication"]["publication_status"] == (
        "committed"
    )
    assert (output / ".b4pe/locks" / f"{record['case_id']}.lock").is_file()
    for role, expected in (
        ("taskset", record["taskset_artifact_sha256"]),
        ("system", record["system_config_sha256"]),
    ):
        assert state[f"{role}_snapshot_sha256"] == expected
        snapshot = output.joinpath(*Path(
            state[f"{role}_snapshot_relpath"]
        ).parts)
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == expected
    source_artifact = output.joinpath(*Path(
        record["source_artifact_relpath"]
    ).parts)
    source_descriptor = json.loads(source_artifact.read_text(encoding="utf-8"))
    assert source_descriptor["trace_sha256"] == record["trace_sha256"]
    assert fixture_result["source_sha256"] == state["source_snapshot_sha256"]
    inspection = inspect_execution.inspect_output(
        output, simulator_binary=simulator,
    )
    assert inspect_execution.inspection_has_integrity_errors(inspection) is False
    resumed = execute_local_campaign_v5(
        EXAMPLE,
        acknowledge_not_for_paper=True,
        output_root=output,
        simulator_binary=simulator,
        limit=1,
        resume=True,
    )
    assert resumed["executor_summary"]["skipped_succeeded"] == 1
    assert resumed["executor_summary"]["executed_cases"] == 0
    resumed_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert resumed_state["attempt_count"] == 1


def test_local_execution_requires_not_for_paper_acknowledgement(tmp_path):
    with pytest.raises(B4ManifestV5Error, match="acknowledge_not_for_paper"):
        execute_local_campaign_v5(
            EXAMPLE,
            acknowledge_not_for_paper=False,
            output_root=tmp_path / "run",
            simulator_binary=tmp_path / "unused",
        )


def test_local_execution_cli_rejects_missing_not_for_paper_ack(tmp_path):
    assert generate_v5_main([
        "--config", str(EXAMPLE),
        "--execute-local",
        "--output-root", str(tmp_path / "unused"),
        "--simulator-binary", str(tmp_path / "unused-rtsim"),
    ]) == 2


def test_runtime_changes_do_not_change_b4_scientific_identity():
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    first = normalize_campaign_v5(raw)
    changed = deepcopy(raw)
    changed["runtime"] = {
        "output_root": "artifacts/previews",
        "timeout_seconds": 10,
    }
    second = normalize_campaign_v5(changed)
    assert first["normalized_scientific_config_sha256"] == second[
        "normalized_scientific_config_sha256"
    ]


def test_campaign_rejects_algorithm_override_and_missing_source():
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["algorithms"] = ["ASAP-BLOCK"]
    with pytest.raises(B4ManifestV5Error):
        normalize_campaign_v5(raw)
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw.pop("energy_source")
    with pytest.raises(B4ManifestV5Error):
        normalize_campaign_v5(raw)
