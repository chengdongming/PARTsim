from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from experiments.v9_3 import exact_energy
from experiments.v9_3.constrained_taskset_identity import (
    GenerationRequest,
    IMPLICIT_DEADLINE_MODE,
    SkeletonTask,
    build_taskset_identity_certificate,
)
import experiments.v9_3.rta4_formal_execution as formal_execution
from experiments.v9_3.rta4_formal_plan import FormalPlanRecord, iter_core1_plan
from experiments.v9_3.rta4_shared_energy import (
    ServiceHorizonContract,
    ServiceMaterialRegistry,
    ServiceMaterialSpec,
    SharedEnergyMaterialError,
    VerifiedSolarServiceMaterialV2,
    core3_shared_energy_projection,
    construct_task_energy_material,
    derive_service_horizon_contract,
    initialize_shared_energy_run,
    project_core3_shared_energy_payload,
    validate_core3_shared_energy_projection,
)
from experiments.v9_3.release_applicability import (
    SYNC_V1, build_release_projection, project_certificate_for_simulation,
)
from experiments.v9_3.simulation_engine import SharedSolarInput


ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system_config_unified_template.yml"
WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")


def _request(wcet: int, *, token: str = "a") -> GenerationRequest:
    return GenerationRequest(
        formal_master_seed=930612,
        formal_generation_id=hashlib.sha256(
            f"RTA4-G2:{token}:{wcet}".encode()
        ).hexdigest(),
        processors=4,
        task_count=1,
        target_normalized_utilization=Fraction(wcet, 200),
        replicate_index=wcet,
        period_min=200,
        period_max=200,
        utilization_allocation_mode="frozen_v9_3_generator_v1",
        min_task_utilization=Fraction(1, 200),
        max_task_utilization=Fraction(1),
        utilization_tolerance=Fraction(0),
        wcet_rounding_mode="compensated",
        generator_version="ASAP_BLOCK_V9_3_GENERATOR_V1",
        power_generation_mode="generator_default_heterogeneous",
        power_generation_contract_identity="1" * 64,
        workload_candidate_identity="2" * 64,
        priority_policy="RM",
        dag_generation_mode="disabled",
        energy_aware_generation=False,
    )


def _certificate(wcet: int = 3, *, legacy_power: Fraction = Fraction(99)):
    return build_taskset_identity_certificate(
        _request(wcet),
        (SkeletonTask("tau-0", 0, wcet, 200, legacy_power),),
        deadline_mode=IMPLICIT_DEADLINE_MODE,
    )


def _task_material(certificate=None, workload="bzip2"):
    certificate = certificate or _certificate()
    return construct_task_energy_material(
        certificate,
        (workload,),
        system_config_path=SYSTEM,
        workload_config_path=SYSTEM,
        taskset_store_identity="V2_TASKSET_STORE_FIXTURE",
        production_build_manifest_identity="b" * 64,
    )


def _service_material(horizon=199, *, scale=Fraction(1)):
    contract = ServiceHorizonContract(horizon, 0, horizon)
    trace = tuple(Fraction.from_float((index + 1) * 0.001) for index in range(horizon))
    shared = SharedSolarInput(trace, {})
    beta = shared.beta(horizon)
    return VerifiedSolarServiceMaterialV2(
        "c" * 64, "d" * 64, "e" * 64, "f" * 64, "b" * 64,
        "1" * 64, "2" * 64, "3" * 64, 1, 0, scale, contract,
        trace, beta, "4" * 64, "5" * 64, "6" * 64, "{}",
    )


def test_task_energy_all_workloads_and_wcet_1_through_200_use_exact_g1_source():
    import asap_block_rta as legacy_rta

    system = legacy_rta.load_system_config(str(SYSTEM))
    for workload in WORKLOADS:
        for wcet in range(1, 201):
            certificate = _certificate(wcet)
            material = _task_material(certificate, workload)
            entry = material.entries[0]
            direct = exact_energy.materialize_task_demand_upper_bound(
                base_power=system.base_power,
                workload_coefficient=system.workload_coefficient(workload),
                frequency_ratio=system.frequency_ratio(),
                wcet=wcet,
                energy_coefficient=1.0,
                label="direct",
            )
            assert entry.energy_j_per_tick == direct.exact_value
            assert entry.energy_j_per_tick_binary64 == direct.binary64_hex
            assert entry.unit == "J/tick"


def test_task_source_identity_is_automatic_stable_and_binds_bytes_and_operands():
    baseline = _task_material(_certificate(), "bzip2")
    repeated = _task_material(_certificate(), "bzip2")
    changed_taskset = _task_material(
        _certificate(legacy_power=Fraction(100)), "bzip2",
    )
    changed_operand = _task_material(_certificate(), "hash")
    assert baseline == repeated
    assert baseline.task_energy_material_identity != changed_taskset.task_energy_material_identity
    assert baseline.task_energy_material_identity != changed_operand.task_energy_material_identity
    assert baseline.entries[0].task_energy_source_identity != changed_operand.entries[0].task_energy_source_identity
    with pytest.raises(TypeError):
        construct_task_energy_material(  # type: ignore[call-arg]
            _certificate(), ("bzip2",), system_config_path=SYSTEM,
            taskset_store_identity="store",
            production_build_manifest_identity="b" * 64,
            source_identity="caller-forgery",
        )


def test_v2_adapter_ignores_legacy_actual_power_and_shares_inputs_across_methods(monkeypatch):
    certificate = _certificate(legacy_power=Fraction(999999))
    task_energy = _task_material(certificate, "control")
    service = _service_material()
    observed = []

    status = SimpleNamespace(value="CANDIDATE")
    certification = SimpleNamespace(value="CERTIFIED_TASKSET")
    task_row = SimpleNamespace(
        solver_status=status,
        certification_status=SimpleNamespace(value="CERTIFIED"),
        candidate_response_time=3,
        checked_w_count=1,
        checked_q_count=1,
        checked_h_count=1,
        failure_reason=None,
        witness_sequence=(),
    )

    def fake_dispatch(*, analysis_id, method, analysis_input):
        observed.append((
            method,
            analysis_input.tasks[0].power,
            analysis_input.beta(1),
            analysis_id,
        ))
        return SimpleNamespace(
            task_results=(task_row,), solver_status=status,
            analysis_certification_status=certification,
            taskset_proven=True, failure_reason=None,
            mechanism_telemetry=(),
        )

    monkeypatch.setattr(formal_execution, "dispatch_formal_rta", fake_dispatch)
    monkeypatch.setattr(formal_execution, "mechanism_telemetry_rows", lambda _result: ())
    base = next(iter_core1_plan())
    config = {
        "experiment_contract": {"profile": "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"},
        "identity": {
            "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        },
        "execution": {"timeout_contract": "TEST"},
    }
    for method in ("CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ"):
        record = FormalPlanRecord(
            base.kind, base.core, base.ordinal, base.mathematical_request_id,
            base.execution_id, base.taskset_slot_id,
            base.taskset_skeleton_slot_id, {**base.material, "method": method},
        )
        mapped, _raw = formal_execution._adapter_result_v2(
            record, certificate, config, 1, task_energy, service,
        )
        assert mapped["task_energy_material_identity"] == task_energy.task_energy_material_identity
        assert mapped["service_material_identity"] == service.service_material_identity
    assert {row[1] for row in observed} == {task_energy.entries[0].energy_j_per_tick}
    assert {row[2] for row in observed} == {service.beta(1)}
    assert task_energy.entries[0].energy_j_per_tick != certificate.tasks[0].actual_power


def _fake_manifest():
    compiler = Path(shutil.which("c++") or "c++").resolve()
    return {
        "manifest_schema": "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_V2",
        "manifest_id": "b" * 64,
        "cpp_toolchain": {
            "compiler": {
                "path": str(compiler),
                "sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
            }
        },
        "solar_verifier": {"binary": {"sha256": "v" * 64}},
    }


def _fake_constructor(counter):
    def construct(system, support, *, horizon, **_kwargs):
        counter.append((str(support), horizon))
        support_path = Path(support)
        document = yaml.safe_load(support_path.read_text(encoding="utf-8"))
        energy = document.get("energy", document)
        scale = Fraction(str(energy["service_curve"]["solar_scale"]))
        trace = tuple(
            Fraction.from_float(float(scale) * (index + 1) * 0.001)
            for index in range(horizon)
        )
        support_sha = hashlib.sha256(support_path.read_bytes()).hexdigest()
        semantic = hashlib.sha256(f"{support_sha}:{horizon}".encode()).hexdigest()
        binding = {
            "semantic_service_source_identity": semantic,
            "parser_environment_identity": "e" * 64,
            "live_proof_identity": hashlib.sha256((semantic + "live").encode()).hexdigest(),
            "compiler_sha256": _fake_manifest()["cpp_toolchain"]["compiler"]["sha256"],
            "verifier_binary_sha256": "v" * 64,
        }
        provenance = {
            "solar_stod_parser_binding": binding,
            "system_template": {"sha256": hashlib.sha256(Path(system).read_bytes()).hexdigest()},
            "energy_support": {"sha256": support_sha},
            "solar_csv": {"sha256": "3" * 64},
            "day_of_year": 1,
            "time_of_day_ms": 0,
        }
        return SharedSolarInput(trace, provenance)
    return construct


def _spec(tmp_path, scale=Fraction(1), analysis=3, material=3):
    support = tmp_path / "support.yaml"
    if not support.exists():
        support.write_text(
            yaml.safe_dump({
                "service_curve": {
                    "system_template": str(SYSTEM),
                    "horizon": 100,
                    "require_real_solar_data": True,
                    "solar_scale": "1",
                },
                "battery_capacity": "20",
                "simulation_initial_battery": "1",
            }, sort_keys=True),
            encoding="utf-8",
        )
    return ServiceMaterialSpec(
        str(SYSTEM), str(support), str(ROOT), scale,
        ServiceHorizonContract(analysis, 0, material),
    )


def test_service_registry_constructs_once_per_identity_and_reuses_for_e0_method_worker(tmp_path):
    calls = []
    spec = _spec(tmp_path)
    with ServiceMaterialRegistry(
        _fake_manifest(), constructor=_fake_constructor(calls),
    ) as registry:
        registry.prepare([spec for _e0 in range(10) for _method in range(10)])
        first = registry.material_for(spec)
        repeated = registry.material_for(spec)
        assert first is repeated
        assert len(calls) == 1
        assert registry.cache_statistics["construction_count"] == 1
        identities = {
            registry.material_for(spec).service_material_identity
            for _workers in (1, 2, 4)
        }
        assert identities == {first.service_material_identity}


def test_service_scale_phase_horizon_and_bounds_are_identity_bound(tmp_path):
    calls = []
    baseline = _spec(tmp_path)
    scaled = _spec(tmp_path, scale=Fraction(1, 2))
    longer = _spec(tmp_path, analysis=4, material=4)
    with ServiceMaterialRegistry(
        _fake_manifest(), constructor=_fake_constructor(calls),
    ) as registry:
        materials = registry.prepare((baseline, scaled, longer))
        values = [materials[item.spec_identity] for item in (baseline, scaled, longer)]
        assert len(calls) == 3
        assert len({value.service_material_identity for value in values}) == 3
        assert values[0].beta(1) != values[1].beta(1)
        with pytest.raises(SharedEnergyMaterialError, match="exceeds"):
            values[0].beta(4)


def test_horizon_contract_derives_rta_and_core3_semantics_without_magic_30200():
    certificate = _certificate(37)
    rta_only = derive_service_horizon_contract(certificate)
    core3 = derive_service_horizon_contract(
        certificate, include_core3_simulation=True,
    )
    assert rta_only.analysis_service_horizon_ticks == 199
    assert rta_only.simulation_observation_horizon_ticks == 0
    assert core3.simulation_observation_horizon_ticks == 30_000 + 200
    assert core3.service_material_horizon_ticks == core3.simulation_observation_horizon_ticks
    with pytest.raises(SharedEnergyMaterialError, match="cover"):
        ServiceHorizonContract(10, 20, 19)


def test_core3_projection_binds_same_task_service_build_phase_scale_and_horizon():
    certificate = _certificate()
    task = _task_material(certificate, "encrypt")
    service = _service_material()
    projection = build_release_projection(certificate, release_mode=SYNC_V1)
    legacy_payload = project_certificate_for_simulation(certificate, projection)
    payload = project_core3_shared_energy_payload(
        certificate, legacy_payload, task,
    )
    assert payload[0]["workload"] == "encrypt"
    assert Fraction(payload[0]["P"]) == task.entries[0].energy_j_per_tick
    assert Fraction(payload[0]["P"]) != certificate.tasks[0].actual_power
    identity = core3_shared_energy_projection(task_energy=task, service=service)
    validate_core3_shared_energy_projection(
        identity, task_energy=task, service=service,
    )
    drift = dict(identity)
    drift["solar_scale"] = "1/2"
    with pytest.raises(SharedEnergyMaterialError, match="drift"):
        validate_core3_shared_energy_projection(
            drift, task_energy=task, service=service,
        )


def test_run_initialization_freezes_unique_materials_before_workers(tmp_path):
    calls = []
    records = tuple(list(iter_core1_plan())[:8])
    certificate = _certificate()

    class Provider:
        def __call__(self, _record):
            return certificate

        def workloads_for(self, _record, _certificate):
            return ("hash",)

    support = _spec(tmp_path).energy_support_path
    context = initialize_shared_energy_run(
        records,
        taskset_provider=Provider(),
        production_build_manifest=_fake_manifest(),
        system_config_path=SYSTEM,
        energy_support_path=support,
        source_root=ROOT,
        taskset_store_identity="V2_STORE",
        service_constructor=_fake_constructor(calls),
    )
    assert len(context.task_energy_materials) == 1
    assert len(context.service_materials) == 1
    assert len(context.record_bindings) == len(records)
    assert context.cache_statistics["construction_count"] == 1
    assert len(calls) == 1
    assert len({
        context.binding_for(record.record_id)["service_material_identity"]
        for record in records
    }) == 1
