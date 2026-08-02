from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import inspect
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.rta4_energy_service_v4 import (
    EXACT_LINEAR_SERVICE_V1,
    RTA4EnergyServiceV4Error,
    VERIFIED_SHARED_ENERGY_MATERIAL_V1,
    exact_service_material_v4,
    normalize_energy_service_v4,
)
from experiments.v9_3.rta4_formal_config_v3 import (
    formal_taskset_store_identity_v3,
    load_rta4_campaign_v3,
)
from experiments.v9_3.rta4_explicit_parity_v4 import (
    run_explicit_spotcheck_parity_v4,
)
from experiments.v9_3.rta4_formal_config_v4 import (
    RTA4FormalConfigV4Error,
    formal_taskset_store_header_v4,
    formal_taskset_store_identity_v4,
    load_rta4_campaign_v4,
    normalize_rta4_campaign_v4,
    source_closure_identity_v4,
)
from experiments.v9_3.rta4_formal_lifecycle_v4 import (
    RTA4_EXECUTION_BACKEND_V4,
    RTA4FormalLifecycleV4Error,
    build_infrastructure_authorization_v4,
    build_prepared_config_v4,
    dry_run_campaign_v4,
    require_formal_campaign_authorization_v4,
    validate_prepared_config_v4,
)
from experiments.v9_3.rta4_formal_plan_v3 import describe_formal_plan_v3
from experiments.v9_3.rta4_formal_plan_v4 import describe_formal_plan_v4
from experiments.v9_3.rta4_formal_schema_v4 import formal_schema_material_v4
from experiments.v9_3.rta4_physical_core_slots_v3 import (
    PHYSICAL_CORE_EXECUTION_BACKEND_V3,
)
from experiments.v9_3.rta4_task_source_v4 import (
    EXPLICIT_MANIFEST_SCHEMA_V1,
    EXPLICIT_TASKSET_MANIFEST,
    FROZEN_T10_BACKGROUND_TASKS,
    FROZEN_T10_CORE_GENERATOR_CONTRACT,
    GENERAL_RANDOM_CONSTRAINED_V1,
    GENERATED_FAMILY,
    PRIORITY_POLICY_RM,
    RTA4TaskSourceV4Error,
    T10_BALANCED_V1,
    load_explicit_taskset_manifest_v4,
    normalize_generated_family_v4,
    revalidate_task_source_v4,
)
from experiments.v9_3.rta4_unified_adapter_v4 import (
    RTA4UnifiedAdapterV4Error,
    _service,
    execute_normalized_taskset_v4,
    execute_replay_requests_v4,
)


ROOT = Path(__file__).resolve().parents[1]
V3_E1 = ROOT / "configs/v9_3_rta4_e1_critical_e0_v1.yaml"
T10_MANIFEST = (
    ROOT / "artifacts/audit/rta4_t10_holdout_176_explicit_manifest_v4.json"
)
UNAUTHORIZED_CAMPAIGN = (
    ROOT / "configs/v9_3_rta4_e1_t10_balanced_exact_v4_UNAUTHORIZED.yaml"
)
A5_AUDIT = ROOT / "artifacts/audit/rta4_t10_stage_a5_service_contract_migration.json"


def _tasks() -> list[dict]:
    return [
        {"name": "tau_1", "C": 1, "D": 3, "T": 5, "power": "1/10"},
        {"name": "tau_2", "C": 1, "D": 6, "T": 10, "power": "1/5"},
    ]


def _manifest(*, tasks: list[dict] | None = None) -> dict:
    material = _tasks() if tasks is None else tasks
    return {
        "schema": EXPLICIT_MANIFEST_SCHEMA_V1,
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": len(material),
        "taskset_count": 1,
        "task_order": [task["name"] for task in material],
        "tasksets": [{
            "taskset_id": "explicit-000",
            "source_seed": None,
            "tasks": material,
        }],
    }


def _write_json(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _general_parameters() -> dict:
    return {
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": 2,
        "taskset_count": 1,
        "base_seed": 1000,
        "generation_indices": [0],
        "task_templates": [
            {
                "name": "tau_1", "C": [1], "D": [3], "T": [5],
                "power": ["1/10"],
            },
            {
                "name": "tau_2", "C": [1], "D": [6], "T": [10],
                "power": ["1/5"],
            },
        ],
    }


def _general_source():
    return normalize_generated_family_v4({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })


def _campaign_raw(task_source: dict) -> dict:
    return {
        "campaign_id": "v4-exact-task-source-test",
        "core": "CORE-1",
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_source": task_source,
        "energy_service": {
            "model": EXACT_LINEAR_SERVICE_V1,
            "rate": "1/10",
        },
        "e0": ["21/40", "11/20"],
        "methods": [
            "CW_THETA_CW", "LOC_THETA_LOC",
            "PH_THETA_PH", "SEQ_THETA_SEQ",
        ],
        "runtime": {},
    }


def _t10_generated_from_regression_manifest():
    explicit = load_explicit_taskset_manifest_v4(T10_MANIFEST)
    base_seed = 1918273645
    indices = [taskset.source_seed - base_seed for taskset in explicit.tasksets]
    return normalize_generated_family_v4({
        "mode": GENERATED_FAMILY,
        "family_id": T10_BALANCED_V1,
        "parameters": {
            "processors": 4,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": 10,
            "mechanism_core_task_count": 7,
            "background_utilization": "1/12",
            "background_tasks": FROZEN_T10_BACKGROUND_TASKS,
            "taskset_count": 176,
            "base_seed": base_seed,
            "generation_indices": indices,
            "core_generator_contract": FROZEN_T10_CORE_GENERATOR_CONTRACT,
        },
    })


def test_v3_e1_identities_are_unchanged():
    campaign = load_rta4_campaign_v3(V3_E1)
    plan = describe_formal_plan_v3(campaign.normalized_scientific_config)
    assert campaign.raw_campaign_file_sha256 == (
        "f0632b46b405afd576b815c34b99b87bb2766ee19c3ef3b5f951413f90c3420b"
    )
    assert campaign.normalized_scientific_config_sha256 == (
        "d5762c90ea9df3e386360c2448039ea6e39c70f4ebfc0372d2a729b6ba915638"
    )
    assert plan["plan_sha256"] == (
        "81231be0dce9693afbf72111493c0fe25500bd8792cbddac9b8ac99796d4f46f"
    )
    assert formal_taskset_store_identity_v3(
        campaign.normalized_scientific_config
    ) == "cc43d5b55c6d4157a1270d9c659e925aa82d7d1e88b60dead6a7274d9658606d"


@pytest.mark.parametrize("missing", ["task_source", "energy_service"])
def test_v4_rejects_missing_required_scientific_inputs(missing):
    raw = _campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })
    raw.pop(missing)
    with pytest.raises(RTA4FormalConfigV4Error):
        normalize_rta4_campaign_v4(raw)


def test_v4_rejects_unknown_campaign_and_family_fields():
    raw = _campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })
    raw["task_family"] = "T10_BALANCED"
    with pytest.raises(RTA4FormalConfigV4Error):
        normalize_rta4_campaign_v4(raw)
    parameters = _general_parameters()
    parameters["implicit_default"] = 1
    with pytest.raises(RTA4TaskSourceV4Error):
        normalize_generated_family_v4({
            "mode": GENERATED_FAMILY,
            "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
            "parameters": parameters,
        })


@pytest.mark.parametrize("field,value", [
    ("rate", 0.1),
    ("e0", [0.5]),
])
def test_v4_rejects_float_scientific_inputs(field, value):
    raw = _campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })
    if field == "rate":
        raw["energy_service"]["rate"] = value
    else:
        raw[field] = value
    with pytest.raises(RTA4FormalConfigV4Error):
        normalize_rta4_campaign_v4(raw)


def test_explicit_manifest_parses_exactly_and_binds_all_identities(tmp_path):
    path = _write_json(tmp_path / "tasks.json", _manifest())
    source = load_explicit_taskset_manifest_v4(path)
    assert source.mode == EXPLICIT_TASKSET_MANIFEST
    assert source.task_count == 2
    assert source.taskset_count == 1
    assert [task.material() for task in source.tasksets[0].tasks] == _tasks()
    assert len(source.identity) == 64
    assert len(source.manifest_file_sha256) == 64
    assert len(source.manifest_semantic_sha256) == 64
    assert len(source.tasksets[0].content_sha256) == 64
    assert len(source.tasksets[0].task_order_sha256) == 64
    assert len(source.content_certificate["content_certificate_identity"]) == 64


def test_explicit_yaml_manifest_has_the_same_normalized_semantics(tmp_path):
    json_source = load_explicit_taskset_manifest_v4(
        _write_json(tmp_path / "tasks.json", _manifest())
    )
    yaml_path = tmp_path / "tasks.yaml"
    yaml_path.write_text(
        yaml.safe_dump(_manifest(), sort_keys=False), encoding="utf-8",
    )
    yaml_source = load_explicit_taskset_manifest_v4(yaml_path)
    assert yaml_source.manifest_semantic_sha256 == (
        json_source.manifest_semantic_sha256
    )
    assert yaml_source.manifest_file_sha256 != json_source.manifest_file_sha256
    assert [task.material() for task in yaml_source.tasksets[0].tasks] == _tasks()


def test_explicit_manifest_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema":"x","schema":"y"}\n', encoding="utf-8",
    )
    with pytest.raises(RTA4TaskSourceV4Error):
        load_explicit_taskset_manifest_v4(path)


@pytest.mark.parametrize("mutation", [
    lambda m: m["tasksets"][0]["tasks"][0].update(C=0),
    lambda m: m["tasksets"][0]["tasks"][0].update(C=4),
    lambda m: m["tasksets"][0]["tasks"][0].update(D=6),
    lambda m: m["tasksets"][0]["tasks"][0].update(power=-0.1),
    lambda m: m.update(task_count=3),
    lambda m: m.update(task_order=["tau_2", "tau_1"]),
    lambda m: m["tasksets"][0]["tasks"][1].update(T=4),
])
def test_explicit_manifest_rejects_invalid_task_science(tmp_path, mutation):
    manifest = _manifest()
    mutation(manifest)
    path = _write_json(tmp_path / "invalid.json", manifest)
    with pytest.raises(RTA4TaskSourceV4Error):
        load_explicit_taskset_manifest_v4(path)


def test_manifest_one_byte_change_changes_source_identity(tmp_path):
    path = _write_json(tmp_path / "tasks.json", _manifest())
    first = load_explicit_taskset_manifest_v4(path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    second = load_explicit_taskset_manifest_v4(path)
    assert first.manifest_semantic_sha256 == second.manifest_semantic_sha256
    assert first.manifest_file_sha256 != second.manifest_file_sha256
    assert first.identity != second.identity
    assert first.tasksets[0].identity != second.tasksets[0].identity


def test_manifest_runtime_revalidation_prevents_toctou(tmp_path):
    path = _write_json(tmp_path / "tasks.json", _manifest())
    source = load_explicit_taskset_manifest_v4(path)
    changed = _manifest()
    changed["tasksets"][0]["tasks"][0]["power"] = "1/9"
    _write_json(path, changed)
    with pytest.raises(RTA4TaskSourceV4Error):
        revalidate_task_source_v4(source)


def test_t10_generated_family_reproduces_all_176_regression_tasksets():
    explicit = load_explicit_taskset_manifest_v4(T10_MANIFEST)
    generated = _t10_generated_from_regression_manifest()
    assert len(generated.tasksets) == 176
    assert generated.identity == (
        "ceb0afe02df4eee27d72aa2aefebd62188faec7c46ac74557a3441e8653f8273"
    )
    for expected, observed in zip(explicit.tasksets, generated.tasksets):
        assert expected.source_seed == observed.source_seed
        assert [task.material() for task in expected.tasks] == [
            task.material() for task in observed.tasks
        ]


def test_t10_background_contract_and_generation_are_byte_deterministic():
    first = _t10_generated_from_regression_manifest()
    second = _t10_generated_from_regression_manifest()
    assert first.identity == second.identity
    assert [taskset.canonical_bytes() for taskset in first.tasksets] == [
        taskset.canonical_bytes() for taskset in second.tasksets
    ]
    for taskset in first.tasksets:
        assert [task.material() for task in taskset.tasks[7:]] == (
            FROZEN_T10_BACKGROUND_TASKS
        )
        assert sum(
            (Fraction(task.C, task.T) for task in taskset.tasks[7:]),
            Fraction(),
        ) == Fraction(1, 12)


def test_registered_families_and_parameters_have_isolated_identities():
    general = _general_source()
    changed_parameters = _general_parameters()
    changed_parameters["base_seed"] += 1
    changed = normalize_generated_family_v4({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": changed_parameters,
    })
    t10 = _t10_generated_from_regression_manifest()
    assert len({general.identity, changed.identity, t10.identity}) == 3
    assert general.tasksets[0].canonical_bytes() == _general_source().tasksets[0].canonical_bytes()


def test_exact_service_uses_no_float_conversion_path():
    service = normalize_energy_service_v4({
        "model": EXACT_LINEAR_SERVICE_V1, "rate": "1/10",
    })
    material = exact_service_material_v4(service, 33)
    assert material.beta_prefix == tuple(
        Fraction(length, 10) for length in range(34)
    )
    source = inspect.getsource(exact_service_material_v4) + inspect.getsource(
        type(service).beta
    )
    for forbidden in ("float(", "Fraction.from_float", "Decimal.from_float"):
        assert forbidden not in source
    with pytest.raises(RTA4EnergyServiceV4Error):
        normalize_energy_service_v4({
            "model": "LEGACY_BINARY64_MATERIALIZED_LINEAR_SERVICE_V1",
            "rate": "1/10",
        })


def test_full_176_explicit_manifest_matches_frozen_direct_entry():
    source = load_explicit_taskset_manifest_v4(T10_MANIFEST)
    service = normalize_energy_service_v4({
        "model": EXACT_LINEAR_SERVICE_V1, "rate": "1/10",
    })
    summary = run_explicit_spotcheck_parity_v4(
        task_source=source,
        energy_service=service,
        e0_values=["21/40", "11/20"],
        methods=[
            "CW_THETA_CW", "LOC_THETA_LOC",
            "PH_THETA_PH", "SEQ_THETA_SEQ",
        ],
        timeout_seconds=120,
        production_build_manifest_identity="0" * 64,
    )
    assert summary["method_unit_count"] == 1408
    assert summary["task_result_record_count"] == 14080
    assert summary["input_mismatch_count"] == 0
    assert summary["adapter_parity_mismatch_count"] == 0
    assert summary["internal_error_count"] == 0
    assert summary["script_error_count"] == 0
    assert summary["dominance_violation_count"] == 0
    assert summary["certified_counts"] == {
        "11/20:CW": 30,
        "11/20:LOC": 56,
        "11/20:PH": 164,
        "11/20:SEQ": 169,
        "21/40:CW": 10,
        "21/40:LOC": 31,
        "21/40:PH": 125,
        "21/40:SEQ": 131,
    }
    assert summary["parity_passed"] is True


def test_explicit_and_generated_sources_share_one_unified_adapter(tmp_path):
    explicit = load_explicit_taskset_manifest_v4(
        _write_json(tmp_path / "tasks.json", _manifest())
    )
    generated = _general_source()
    service = normalize_energy_service_v4({
        "model": EXACT_LINEAR_SERVICE_V1, "rate": "1/10",
    })
    results = []
    for source in (explicit, generated):
        raw = _campaign_raw({
            "mode": EXPLICIT_TASKSET_MANIFEST,
            "manifest_path": str(tmp_path / "tasks.json"),
        }) if source is explicit else _campaign_raw({
            "mode": GENERATED_FAMILY,
            "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
            "parameters": _general_parameters(),
        })
        normalized = normalize_rta4_campaign_v4(raw)
        scientific = normalized["normalized_scientific_config"]
        results.append(execute_normalized_taskset_v4(
            taskset=source.tasksets[0], processors=4,
            task_source_identity=source.identity,
            taskset_store_identity=formal_taskset_store_identity_v4(scientific),
            production_build_manifest_identity="0" * 64,
            energy_service=service, e0="21/40", method="PH_THETA_PH",
            timeout_seconds=120,
        ))
    assert results[0]["kernel_result_hash"] == results[1]["kernel_result_hash"]
    assert results[0]["mathematical_result_hash"] != results[1]["mathematical_result_hash"]


def test_verified_shared_energy_material_is_explicitly_bound_and_executable():
    source = _general_source()
    build_identity = "0" * 64
    exact = normalize_energy_service_v4({
        "model": EXACT_LINEAR_SERVICE_V1, "rate": "1/10",
    })
    runtime_material = _service(
        source.tasksets[0], exact,
        production_build_manifest_identity=build_identity,
        verified_shared_service=None,
    )
    shared = normalize_energy_service_v4({
        "model": VERIFIED_SHARED_ENERGY_MATERIAL_V1,
        "material_schema": "ASAP_BLOCK_RTA4_SHARED_ENERGY_MATERIAL_V2",
        "service_material_identity": runtime_material.service_material_identity,
        "beta_material_identity": runtime_material.beta_material_identity,
        "production_build_manifest_identity": build_identity,
        "source_closure_identity": "1" * 64,
    })
    raw = _campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })
    raw["energy_service"] = dict(shared.normalized_config)
    for field in (
        "schema", "version", "implicit_solar_fallback_allowed",
    ):
        raw["energy_service"].pop(field)
    normalized = normalize_rta4_campaign_v4(raw)
    request = {
        "taskset": source.tasksets[0],
        "processors": 4,
        "task_source_identity": source.identity,
        "taskset_store_identity": formal_taskset_store_identity_v4(
            normalized["normalized_scientific_config"]
        ),
        "production_build_manifest_identity": build_identity,
        "energy_service": shared,
        "e0": "21/40",
        "method": "PH_THETA_PH",
        "timeout_seconds": 120,
    }
    with pytest.raises(RTA4UnifiedAdapterV4Error):
        execute_normalized_taskset_v4(**request)
    shared_result = execute_normalized_taskset_v4(
        **request, verified_shared_service=runtime_material,
    )
    assert shared_result["service_material_identity"] == (
        runtime_material.service_material_identity
    )


def test_plan_store_and_source_closure_are_strictly_source_isolated(tmp_path):
    path = _write_json(tmp_path / "tasks.json", _manifest())
    explicit_normalized = normalize_rta4_campaign_v4(_campaign_raw({
        "mode": EXPLICIT_TASKSET_MANIFEST, "manifest_path": str(path),
    }))
    generated_normalized = normalize_rta4_campaign_v4(_campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    }))
    identities = []
    for normalized in (explicit_normalized, generated_normalized):
        science = normalized["normalized_scientific_config"]
        plan = describe_formal_plan_v4(science, normalized["task_source"])
        identities.append((
            plan["plan_sha256"],
            formal_taskset_store_identity_v4(science),
            source_closure_identity_v4(science),
        ))
    assert all(left != right for left, right in zip(*identities))


def test_old_176_regression_manifest_has_a_1408_unit_dry_plan():
    normalized = normalize_rta4_campaign_v4(_campaign_raw({
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "manifest_path": str(T10_MANIFEST),
    }))
    plan = describe_formal_plan_v4(
        normalized["normalized_scientific_config"],
        normalized["task_source"],
    )
    assert plan["taskset_count"] == 176
    assert plan["mathematical_request_count"] == 1408
    assert plan["ordered_stream_count"] == 1408


def test_v4_reuses_physical_core_slot_backend():
    assert RTA4_EXECUTION_BACKEND_V4 == PHYSICAL_CORE_EXECUTION_BACKEND_V3
    schema = formal_schema_material_v4()
    assert schema["execution_backend"] == PHYSICAL_CORE_EXECUTION_BACKEND_V3
    assert schema["task_source_contract"]["registered_families"] == [
        GENERAL_RANDOM_CONSTRAINED_V1, T10_BALANCED_V1,
    ]
    assert schema["energy_service_contract"]["registered_models"] == [
        EXACT_LINEAR_SERVICE_V1, VERIFIED_SHARED_ENERGY_MATERIAL_V1,
    ]


def test_worker_count_does_not_change_kernel_result_hash():
    source = _general_source()
    raw = _campaign_raw({
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": _general_parameters(),
    })
    normalized = normalize_rta4_campaign_v4(raw)
    science = normalized["normalized_scientific_config"]
    request = {
        "taskset": source.tasksets[0],
        "processors": 4,
        "task_source_identity": source.identity,
        "taskset_store_identity": formal_taskset_store_identity_v4(science),
        "production_build_manifest_identity": "0" * 64,
        "energy_service": normalized["energy_service"],
        "e0": "21/40",
        "method": "SEQ_THETA_SEQ",
        "timeout_seconds": 120,
    }
    one = execute_replay_requests_v4([request, request], worker_count=1)
    two = execute_replay_requests_v4([request, request], worker_count=2)
    assert [row["kernel_result_hash"] for row in one] == [
        row["kernel_result_hash"] for row in two
    ]


def test_prepared_and_infrastructure_authorization_bind_v4_inputs(tmp_path):
    manifest_path = _write_json(tmp_path / "tasks.json", _manifest())
    campaign_document = _campaign_raw({
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "manifest_path": "tasks.json",
    })
    campaign_document["runtime"] = {
        "output_root": str(tmp_path / "output"),
        "taskset_store": str(tmp_path / "store"),
    }
    campaign_path = tmp_path / "campaign.yaml"
    campaign_path.write_text(
        yaml.safe_dump(campaign_document, sort_keys=False), encoding="utf-8",
    )
    campaign = load_rta4_campaign_v4(campaign_path)
    prepared = build_prepared_config_v4(
        campaign,
        repository_commit="1" * 40,
        repository_tree="2" * 40,
        production_build_manifest_identity="3" * 64,
    )
    assert validate_prepared_config_v4(prepared) == prepared
    assert prepared["taskset_store_header"] == formal_taskset_store_header_v4(
        campaign.normalized_scientific_config
    )
    authorization = build_infrastructure_authorization_v4(
        prepared, stage_a5_audit_path=A5_AUDIT,
        maximum_request_count=prepared["plan"]["ordered_stream_count"],
    )
    assert authorization["task_source_identity"] == campaign.task_source.identity
    assert authorization["manifest_file_sha256"] == campaign.task_source.manifest_file_sha256
    assert authorization["manifest_semantic_sha256"] == campaign.task_source.manifest_semantic_sha256
    assert authorization["task_source_content_certificate"] == campaign.task_source.content_certificate
    assert authorization["taskset_store_header"] == prepared["taskset_store_header"]
    assert authorization["energy_service_identity"] == campaign.energy_service.identity
    assert authorization["formal_t10_campaign_authorized"] is False
    invalid_audit = json.loads(A5_AUDIT.read_text(encoding="utf-8"))
    invalid_audit["exact_adapter_parity_mismatch_count"] = 1
    invalid_audit_path = _write_json(tmp_path / "invalid-a5.json", invalid_audit)
    with pytest.raises(RTA4FormalLifecycleV4Error):
        build_infrastructure_authorization_v4(
            prepared, stage_a5_audit_path=invalid_audit_path,
            maximum_request_count=1,
        )
    with pytest.raises(RTA4FormalLifecycleV4Error):
        require_formal_campaign_authorization_v4(prepared, authorization)
    assert manifest_path.is_file()


def test_dry_run_creates_no_output_or_store_namespace(tmp_path):
    _write_json(tmp_path / "tasks.json", _manifest())
    campaign_document = _campaign_raw({
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "manifest_path": "tasks.json",
    })
    output = tmp_path / "must-not-exist-output"
    store = tmp_path / "must-not-exist-store"
    campaign_document["runtime"] = {
        "output_root": str(output), "taskset_store": str(store),
    }
    campaign_path = tmp_path / "campaign.yaml"
    campaign_path.write_text(
        yaml.safe_dump(campaign_document, sort_keys=False), encoding="utf-8",
    )
    dry = dry_run_campaign_v4(load_rta4_campaign_v4(campaign_path))
    assert dry["plan"]["ordered_stream_count"] == 8
    assert dry["writes_performed"] is False
    assert not output.exists()
    assert not store.exists()


def test_formal_t10_campaign_remains_an_invalid_parameter_placeholder():
    text = UNAUTHORIZED_CAMPAIGN.read_text(encoding="utf-8")
    assert "UNAUTHORIZED_PARAMETER_PLACEHOLDER" in text
    with pytest.raises(RTA4FormalConfigV4Error):
        load_rta4_campaign_v4(UNAUTHORIZED_CAMPAIGN)


def test_stage_a5_authorizes_infrastructure_but_not_formal_campaign():
    audit = json.loads(A5_AUDIT.read_text(encoding="utf-8"))
    assert audit["stage_b_infrastructure_authorized"] is True
    assert audit["formal_t10_campaign_authorized"] is False
