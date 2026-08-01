from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from experiments.v9_3.rta4_formal_config import load_rta4_formal_config
from experiments.v9_3.rta4_formal_config_v2 import load_rta4_formal_config_v2
from experiments.v9_3.rta4_formal_config_v3 import (
    RTA4FormalConfigV3Error,
    load_rta4_campaign_v3,
    normalize_rta4_campaign_v3,
    validate_source_binding_v3,
)
from experiments.v9_3.rta4_formal_lifecycle_v3 import (
    RTA4FormalLifecycleV3Error,
    build_authorization_v3,
    build_prepared_config_v3,
    ensure_result_namespace_v3,
    validate_authorization_v3,
    validate_checkpoint_v3,
)
from experiments.v9_3.rta4_formal_plan_v3 import (
    RTA4FormalPlanV3Error,
    core4_conditions_v3,
    describe_formal_plan_v3,
    iter_formal_plan_v3,
)
from experiments.v9_3.rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES,
)
from experiments.v9_3.rta4_production_build_manifest_v3 import (
    PRODUCTION_BUILD_MANIFEST_DOMAIN_V3,
    PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
    PRODUCTION_BUILD_PROFILE_V3,
)
from experiments.v9_3.rta4_formal_config import domain_hash
from scripts.create_v9_3_rta4_campaign import campaign_template


ROOT = Path(__file__).resolve().parents[1]
E1 = ROOT / "configs/v9_3_rta4_e1_critical_e0_v1.yaml"


def _write(tmp_path: Path, raw: dict, name: str = "campaign.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_rta4_campaign_v3(path)


def _small(core: str) -> dict:
    raw = deepcopy(campaign_template(core))
    raw["campaign_id"] = f"test-{core.lower()}-parameterized-v3"
    if core == "CORE-1":
        raw["normalized_utilization"] = ["1/2"]
        raw["tasksets_per_utilization"] = 2
        raw["e0"] = ["0", "1/2"]
        raw["methods"] = ["CW_THETA_CW", "SEQ_THETA_SEQ"]
    elif core == "CORE-2":
        raw["source"]["taskset_count"] = 2
        raw["e0"] = ["0"]
        raw["methods"] = ["CW_D"]
    elif core == "CORE-3":
        raw["source"]["taskset_count"] = 2
        raw["release_modes"] = ["SYNC_V1"]
        raw["finite_battery_capacities"] = ["20"]
    elif core == "CORE-4":
        raw["normalized_utilization"] = ["1/2"]
        raw["skeletons_per_utilization"] = 2
        raw["axes"] = {
            "e0": ["1/20", "1/2"],
            "service_scale": ["1", "3/2"],
            "power_scale": ["1", "3/2"],
            "deadline_slack_fraction": ["3/4", "1"],
        }
        raw["methods"] = ["CW_THETA_CW"]
    elif core == "CORE-5A":
        raw["task_count_axis"] = {"values": [5], "processors": 4, "tasksets": 2}
        raw["processor_axis"] = {"values": [2], "task_count": 10, "tasksets": 2}
        raw["integer_time_scale_axis"] = {"values": [1, 2], "base_tasksets": 2}
        raw["methods"] = ["CW_THETA_CW"]
    elif core == "CORE-5B":
        raw["source"]["taskset_count"] = 2
        raw["utilization_strata"] = ["1/2"]
        raw["candidates_per_method_stratum"] = 3
        raw["selected_per_method_stratum"] = 2
        raw["methods"] = ["CW_THETA_CW"]
        raw["workers"] = [1, 2]
    return raw


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "production-manifest.json"
    material = {
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
        "formal_profile": PRODUCTION_BUILD_PROFILE_V3,
    }
    path.write_text(json.dumps({
        **material,
        "manifest_id": domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, material),
    }), encoding="utf-8")
    return path


def _prepared(tmp_path: Path, raw: dict | None = None, name: str = "campaign.yaml"):
    campaign = _write(tmp_path, _small("CORE-1") if raw is None else raw, name)
    prepared = build_prepared_config_v3(
        campaign, production_manifest_path=_manifest(tmp_path),
        output_root=tmp_path / "results", taskset_store=tmp_path / "store",
        worker_count=2, max_in_flight=4, timeout_seconds=30,
    )
    return campaign, prepared, build_authorization_v3(prepared)


def test_v1_and_v2_loaders_remain_available_and_cross_version_isolation_holds():
    for core in ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B"):
        slug = core.lower().replace("-", "")
        load_rta4_formal_config(
            ROOT / "configs" / f"v9_3_rta4_{slug}_unauthorized_pre_pilot_v1.yaml",
            expected_core=core,
        )
        load_rta4_formal_config_v2(
            ROOT / "configs" / f"v9_3_rta4_{slug}_unauthorized_pre_pilot_v2_shared_energy.yaml",
            expected_core=core,
        )


def test_e1_critical_e0_has_exact_dynamic_counts_and_no_float():
    campaign = load_rta4_campaign_v3(E1)
    plan = describe_formal_plan_v3(campaign.normalized_scientific_config)
    assert plan["taskset_skeleton_count"] == 800
    assert plan["mathematical_request_count"] == 9600
    assert plan["ordered_stream_count"] == 9600
    assert all(type(value) is str for value in campaign.normalized_scientific_config["e0"])


def test_core1_pairing_is_shared_across_three_e0_and_four_methods():
    campaign = load_rta4_campaign_v3(E1)
    first = list(iter_formal_plan_v3(campaign.normalized_scientific_config))[:12]
    assert len({record.taskset_slot_id for record in first}) == 1
    assert len({record.mathematical_request_id for record in first}) == 12


def test_e0_change_changes_scientific_and_plan_identity(tmp_path):
    first = _write(tmp_path, _small("CORE-1"), "first.yaml")
    changed = _small("CORE-1")
    changed["e0"] = ["0", "3/5"]
    second = _write(tmp_path, changed, "second.yaml")
    assert first.normalized_scientific_config_sha256 != second.normalized_scientific_config_sha256
    assert describe_formal_plan_v3(first.normalized_scientific_config)["plan_sha256"] != describe_formal_plan_v3(second.normalized_scientific_config)["plan_sha256"]


def test_output_root_changes_only_prepared_identity(tmp_path):
    campaign = _write(tmp_path, _small("CORE-1"))
    manifest = _manifest(tmp_path)
    first = build_prepared_config_v3(
        campaign, production_manifest_path=manifest,
        output_root=tmp_path / "one", taskset_store=tmp_path / "store",
    )
    second = build_prepared_config_v3(
        campaign, production_manifest_path=manifest,
        output_root=tmp_path / "two", taskset_store=tmp_path / "store",
    )
    assert first["normalized_scientific_config_sha256"] == second["normalized_scientific_config_sha256"]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["prepared_config_id"] != second["prepared_config_id"]


def test_output_root_change_inside_yaml_preserves_scientific_and_plan_hash(tmp_path):
    first_raw = _small("CORE-1")
    first_raw["runtime"] = {"output_root": "one", "taskset_store": "store"}
    second_raw = deepcopy(first_raw)
    second_raw["runtime"]["output_root"] = "two"
    first = _write(tmp_path, first_raw, "first-runtime.yaml")
    second = _write(tmp_path, second_raw, "second-runtime.yaml")
    assert first.raw_campaign_file_sha256 != second.raw_campaign_file_sha256
    assert first.normalized_scientific_config_sha256 == second.normalized_scientific_config_sha256
    assert describe_formal_plan_v3(first.normalized_scientific_config)["plan_sha256"] == describe_formal_plan_v3(second.normalized_scientific_config)["plan_sha256"]


def test_runtime_worker_and_timeout_change_only_prepared_identity(tmp_path):
    campaign = _write(tmp_path, _small("CORE-1"))
    manifest = _manifest(tmp_path)
    first = build_prepared_config_v3(
        campaign, production_manifest_path=manifest,
        output_root=tmp_path / "results", taskset_store=tmp_path / "store",
        worker_count=1, max_in_flight=1, timeout_seconds=30,
    )
    second = build_prepared_config_v3(
        campaign, production_manifest_path=manifest,
        output_root=tmp_path / "results", taskset_store=tmp_path / "store",
        worker_count=2, max_in_flight=4, timeout_seconds=60,
    )
    assert first["normalized_scientific_config_sha256"] == second["normalized_scientific_config_sha256"]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["prepared_config_id"] != second["prepared_config_id"]


def test_prepared_config_binds_physical_topology_and_checkpoint_policy(tmp_path):
    _, prepared, _ = _prepared(tmp_path)
    operation = prepared["operational"]
    assert operation["execution_backend"] == "PHYSICAL_CORE_PROCESS_SLOTS"
    assert operation["physical_core_binding_required"] is True
    assert operation["worker_count"] == 2
    assert operation["selected_logical_cpu_ids"] == [
        row["selected_logical_cpu_id"]
        for row in operation["selected_physical_cores"]
    ]
    assert len(operation["selected_physical_cores"]) == 2
    assert len({
        (row["physical_package_id"], row["physical_core_id"])
        for row in operation["selected_physical_cores"]
    }) == 2
    assert operation["checkpoint_every_records"] == 32
    assert operation["checkpoint_every_seconds"] == 5


def test_prepared_validation_rejects_allowed_topology_fingerprint_drift(
    tmp_path, monkeypatch,
):
    _, prepared, _ = _prepared(tmp_path)
    import experiments.v9_3.rta4_formal_lifecycle_v3 as lifecycle
    from experiments.v9_3.rta4_physical_core_slots_v3 import CPUTopologyV3

    original = lifecycle.discover_cpu_topology_v3()
    drifted = CPUTopologyV3(
        original.allowed_logical_cpus, original.physical_cores, "0" * 64,
        original.selection_policy,
    )
    monkeypatch.setattr(
        lifecycle, "discover_cpu_topology_v3", lambda: drifted,
    )
    with pytest.raises(RTA4FormalLifecycleV3Error, match="operational identity drift"):
        lifecycle.validate_prepared_config_v3(prepared)


def test_process_pool_prepared_and_authorization_are_rejected(tmp_path):
    _, prepared, authorization = _prepared(tmp_path)
    import experiments.v9_3.rta4_formal_lifecycle_v3 as lifecycle

    old_prepared = deepcopy(prepared)
    old_prepared["prepared_schema"] = (
        "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V3_PARAMETERIZED"
    )
    with pytest.raises(RTA4FormalLifecycleV3Error, match="field/schema"):
        lifecycle.validate_prepared_config_v3(old_prepared)
    old_authorization = deepcopy(authorization)
    old_authorization["authorization_schema"] = (
        "ASAP_BLOCK_V9_3_RTA4_AUTHORIZATION_V3_PARAMETERIZED"
    )
    with pytest.raises(RTA4FormalLifecycleV3Error, match="authorization"):
        validate_authorization_v3(
            old_authorization, prepared_config=prepared,
        )


@pytest.mark.parametrize("value", [0.5, float("nan"), float("inf")])
def test_float_nan_and_infinity_scientific_values_are_rejected(value):
    raw = _small("CORE-1")
    raw["e0"] = [value]
    with pytest.raises(RTA4FormalConfigV3Error, match="exact rational string"):
        normalize_rta4_campaign_v3(raw)


def test_negative_e0_is_rejected():
    raw = _small("CORE-1")
    raw["e0"] = ["-1/20"]
    with pytest.raises(RTA4FormalConfigV3Error, match="allowed range"):
        normalize_rta4_campaign_v3(raw)


def test_duplicate_e0_is_rejected():
    raw = _small("CORE-1")
    raw["e0"] = ["1/2", "2/4"]
    with pytest.raises(RTA4FormalConfigV3Error, match="duplicates"):
        normalize_rta4_campaign_v3(raw)


def test_duplicate_method_is_rejected():
    raw = _small("CORE-1")
    raw["methods"] = ["CW_THETA_CW", "CW_THETA_CW"]
    with pytest.raises(RTA4FormalConfigV3Error, match="duplicate methods"):
        normalize_rta4_campaign_v3(raw)


def test_unknown_method_is_rejected():
    raw = _small("CORE-1")
    raw["methods"] = ["NOT_A_METHOD"]
    with pytest.raises(RTA4FormalConfigV3Error, match="unknown methods"):
        normalize_rta4_campaign_v3(raw)


def test_empty_utilization_axis_is_rejected():
    raw = _small("CORE-1")
    raw["normalized_utilization"] = []
    with pytest.raises(RTA4FormalConfigV3Error, match="non-empty"):
        normalize_rta4_campaign_v3(raw)


def test_zero_sample_count_is_rejected():
    raw = _small("CORE-1")
    raw["tasksets_per_utilization"] = 0
    with pytest.raises(RTA4FormalConfigV3Error, match="positive integer"):
        normalize_rta4_campaign_v3(raw)


def test_invalid_processor_and_task_counts_are_rejected():
    for key in ("processors", "task_count"):
        raw = _small("CORE-1")
        raw[key] = 0
        with pytest.raises(RTA4FormalConfigV3Error, match="positive integer"):
            normalize_rta4_campaign_v3(raw)


def test_selected_greater_than_candidates_is_rejected():
    raw = _small("CORE-5B")
    raw["selected_per_method_stratum"] = 4
    with pytest.raises(RTA4FormalConfigV3Error, match="exceeds"):
        normalize_rta4_campaign_v3(raw)


@pytest.mark.parametrize("core", ["CORE-2", "CORE-3"])
def test_core2_and_core3_wrong_source_are_rejected(core):
    raw = _small(core)
    raw["source"]["core"] = "CORE-4"
    with pytest.raises(RTA4FormalConfigV3Error, match="source must bind CORE-1"):
        normalize_rta4_campaign_v3(raw)


def test_core5b_wrong_source_or_nonbaseline_scope_is_rejected():
    raw = _small("CORE-5B")
    raw["source"]["source_scope"] = "CORE4_ALL_CONDITIONS"
    with pytest.raises(RTA4FormalConfigV3Error, match="CORE4_BASELINE"):
        normalize_rta4_campaign_v3(raw)


@pytest.mark.parametrize("core", ["CORE-2", "CORE-3", "CORE-5B"])
def test_downstream_observed_source_identity_mismatch_is_rejected(core):
    config = normalize_rta4_campaign_v3(_small(core))["normalized_scientific_config"]
    observed = deepcopy(config["source"])
    observed["source_plan_sha256"] = "f" * 64
    with pytest.raises(RTA4FormalConfigV3Error, match="source campaign"):
        validate_source_binding_v3(config, observed)


def test_core4_ofat_changes_exactly_one_axis_and_deduplicates_baseline():
    config = normalize_rta4_campaign_v3(_small("CORE-4"))["normalized_scientific_config"]
    conditions = core4_conditions_v3(config)
    assert sum(condition["axis"] == "baseline" for condition in conditions) == 1
    assert len(conditions) == 5
    baseline = conditions[0]
    for condition in conditions[1:]:
        assert sum(
            condition[key] != baseline[key]
            for key in ("e0", "service_scale", "power_scale", "deadline_slack_fraction")
        ) == 1


def test_dynamic_expected_count_mismatch_fails_closed(monkeypatch):
    import experiments.v9_3.rta4_formal_plan_v3 as plan_module

    config = normalize_rta4_campaign_v3(_small("CORE-1"))["normalized_scientific_config"]
    original = plan_module.expected_counts_v3

    def wrong(value):
        result = original(value)
        result["ordered_stream_count"] += 1
        return result

    monkeypatch.setattr(plan_module, "expected_counts_v3", wrong)
    with pytest.raises(RTA4FormalPlanV3Error, match="dynamic plan count mismatch"):
        plan_module.describe_formal_plan_v3(config)


def test_external_campaign_is_not_in_v2_production_source_closure():
    assert "configs/v9_3_rta4_e1_critical_e0_v1.yaml" not in DEFAULT_RELEVANT_SOURCES
    assert all("campaign" not in path for path in DEFAULT_RELEVANT_SOURCES)


def test_prepared_config_binds_campaign_file_sha(tmp_path):
    campaign, prepared, _ = _prepared(tmp_path)
    assert prepared["campaign_file"]["absolute_path"] == str(campaign.campaign_path)
    assert prepared["campaign_file"]["raw_campaign_file_sha256"] == campaign.raw_campaign_file_sha256


def test_prepared_validation_rejects_in_place_campaign_change(tmp_path):
    campaign, prepared, _ = _prepared(tmp_path)
    campaign.campaign_path.write_text("changed: true\n", encoding="utf-8")
    from experiments.v9_3.rta4_formal_lifecycle_v3 import validate_prepared_config_v3

    with pytest.raises(Exception):
        validate_prepared_config_v3(prepared)


def test_authorization_binds_prepared_config_id(tmp_path):
    _, prepared, authorization = _prepared(tmp_path)
    assert authorization["prepared_config_id"] == prepared["prepared_config_id"]
    validate_authorization_v3(authorization, prepared_config=prepared)


def test_downstream_preparation_requires_observed_source_binding(tmp_path):
    campaign = _write(tmp_path, _small("CORE-2"))
    with pytest.raises(RTA4FormalLifecycleV3Error, match="observed source"):
        build_prepared_config_v3(
            campaign, production_manifest_path=_manifest(tmp_path),
            output_root=tmp_path / "results", taskset_store=tmp_path / "store",
        )
    prepared = build_prepared_config_v3(
        campaign, production_manifest_path=_manifest(tmp_path),
        output_root=tmp_path / "results", taskset_store=tmp_path / "store",
        observed_source_binding=campaign.normalized_scientific_config["source"],
        source_taskset_store=tmp_path / "source-store",
    )
    assert prepared["source_binding"] == campaign.normalized_scientific_config["source"]


def test_resume_uses_the_same_campaign_identity(tmp_path):
    _, prepared, authorization = _prepared(tmp_path)
    first = ensure_result_namespace_v3(prepared, authorization, resume=False)
    resumed = ensure_result_namespace_v3(prepared, authorization, resume=True)
    assert first == resumed


def test_different_campaign_cannot_reuse_existing_result_directory(tmp_path):
    _, prepared, authorization = _prepared(tmp_path)
    ensure_result_namespace_v3(prepared, authorization, resume=False)
    changed = _small("CORE-1")
    changed["e0"] = ["3/5"]
    _, other_prepared, other_authorization = _prepared(tmp_path, changed, "other.yaml")
    with pytest.raises(RTA4FormalLifecycleV3Error, match="another or legacy"):
        ensure_result_namespace_v3(other_prepared, other_authorization, resume=False)


def test_legacy_checkpoint_is_not_accepted_as_v3(tmp_path):
    _, prepared, authorization = _prepared(tmp_path)
    with pytest.raises(RTA4FormalLifecycleV3Error, match="legacy or malformed"):
        validate_checkpoint_v3(
            {"checkpoint_schema": "ASAP_BLOCK_V9_3_RTA4_CHECKPOINT_V2"},
            prepared_config=prepared, authorization=authorization,
        )


def test_malformed_yaml_fails_closed(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("campaign_id: [\n", encoding="utf-8")
    with pytest.raises(RTA4FormalConfigV3Error, match="cannot parse"):
        load_rta4_campaign_v3(path)


def test_unknown_field_fails_closed():
    raw = _small("CORE-1")
    raw["temporary_e0_override"] = "1"
    with pytest.raises(RTA4FormalConfigV3Error, match="unknown"):
        normalize_rta4_campaign_v3(raw)


def test_method_order_is_normalized_and_enters_plan_identity():
    raw = _small("CORE-1")
    raw["methods"] = ["SEQ_THETA_SEQ", "CW_THETA_CW"]
    config = normalize_rta4_campaign_v3(raw)["normalized_scientific_config"]
    assert config["methods"] == ["CW_THETA_CW", "SEQ_THETA_SEQ"]
    records = list(iter_formal_plan_v3(config))
    assert [record.material["method"] for record in records[:2]] == [
        "CW_THETA_CW", "SEQ_THETA_SEQ",
    ]


def test_core5b_worker_axis_does_not_change_math_request_identity():
    config = normalize_rta4_campaign_v3(_small("CORE-5B"))["normalized_scientific_config"]
    first, second = list(iter_formal_plan_v3(config))[:2]
    assert first.mathematical_request_id == second.mathematical_request_id
    assert first.execution_id != second.execution_id


@pytest.mark.parametrize(
    ("core", "mutate"),
    [
        ("CORE-1", lambda raw: raw.update(tasksets_per_utilization=3)),
        ("CORE-1", lambda raw: raw.update(normalized_utilization=["1/2", "3/5"])),
        ("CORE-4", lambda raw: raw["axes"].update(e0=["1/20", "1/2", "3/5"])),
        ("CORE-5A", lambda raw: raw["task_count_axis"].update(values=[5, 10])),
        ("CORE-5B", lambda raw: raw.update(workers=[1, 2, 4])),
    ],
)
def test_configurable_axes_change_plan_without_python_changes(core, mutate):
    before_raw = _small(core)
    after_raw = deepcopy(before_raw)
    mutate(after_raw)
    before = normalize_rta4_campaign_v3(before_raw)["normalized_scientific_config"]
    after = normalize_rta4_campaign_v3(after_raw)["normalized_scientific_config"]
    assert describe_formal_plan_v3(before)["plan_sha256"] != describe_formal_plan_v3(after)["plan_sha256"]


@pytest.mark.parametrize("core", ["CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B"])
def test_all_six_core_plans_match_their_dynamic_count(core):
    config = normalize_rta4_campaign_v3(_small(core))["normalized_scientific_config"]
    plan = describe_formal_plan_v3(config)
    assert plan["ordered_stream_count"] == sum(1 for _ in iter_formal_plan_v3(config))


def test_cli_rejects_scientific_parameter_override():
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_v9_3_rta4_formal.py",
            "--campaign-config", str(E1), "--dry-run", "--e0", "1/2",
        ],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.returncode != 0
    assert "unrecognized arguments" in completed.stderr


def test_template_generator_writes_a_valid_core1_campaign(tmp_path):
    output = tmp_path / "generated.yaml"
    subprocess.run(
        [
            sys.executable, "scripts/create_v9_3_rta4_campaign.py",
            "--core", "CORE-1", "--output", str(output),
        ],
        cwd=ROOT, check=True,
    )
    generated = load_rta4_campaign_v3(output)
    assert describe_formal_plan_v3(generated.normalized_scientific_config)[
        "ordered_stream_count"
    ] == 9600
