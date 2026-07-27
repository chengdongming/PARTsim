from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.v9_3.rta4_formal_config import canonical_json, domain_hash
from experiments.v9_3.rta4_formal_config_v2 import (
    default_rta4_formal_config_v2, formal_taskset_store_identity_v2,
)
from experiments.v9_3.rta4_formal_execution import (
    ProductionRTAExecutorV2, ProductionSimulationExecutorV2,
)
from experiments.v9_3.rta4_formal_lifecycle_v2 import (
    RTA4FormalLifecycleV2Error, RTA4FormalResultWriterV2,
    RTA4FormalTasksetStoreV2, RTA4_RESULT_ROW_SCHEMA_V2,
    build_test_authorization_v2, build_test_prepared_config_v2,
    retry_resume_identity_v2,
)
from experiments.v9_3.rta4_formal_plan_v2 import iter_formal_plan_v2
from experiments.v9_3.rta4_formal_schema_v2 import formal_schema_hash_v2
from experiments.v9_3.rta4_numeric_contract_v2 import (
    RTA4_NUMERIC_CONTRACT_V2_SHA256,
)
from experiments.v9_3.rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES, PRODUCTION_BUILD_MANIFEST_SCHEMA,
    generate_production_build_manifest, write_production_build_manifest,
)
from experiments.v9_3.rta4_shared_energy import (
    FrozenMapping, ServiceHorizonContract, ServiceMaterialRegistry,
    ServiceMaterialSpec, SharedEnergyRunContext, SharedEnergyMaterialError,
    VerifiedSolarServiceMaterialV2, construct_task_energy_material,
)
from experiments.v9_3.rta4_taskset_v2 import (
    ProductionTasksetProviderV2, TasksetIdentityCertificateV2,
)
from experiments.v9_3.simulation_engine import SharedSolarInput
from experiments.v9_3.simulation_engine import (
    SimulationTraceError, _task_payload_for_trace_id,
)


ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system_config_unified_template.yml"
SUPPORT = ROOT / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
BUILD_ID = "b" * 64


def _record_and_certificate(core="CORE-1"):
    config = default_rta4_formal_config_v2(core)
    record = next(iter_formal_plan_v2(config))
    provider = ProductionTasksetProviderV2(config)
    return config, record, provider, provider(record)


def _task_energy(record, provider, certificate, build_id=BUILD_ID):
    return construct_task_energy_material(
        certificate, provider.workloads_for(record, certificate),
        system_config_path=SYSTEM,
        taskset_store_identity=formal_taskset_store_identity_v2(),
        production_build_manifest_identity=build_id,
    )


def _service(certificate, *, build_id=BUILD_ID, core3=False):
    analysis = max(task.relative_deadline for task in certificate.tasks) - 1
    service_horizon = (
        30_000 + max(task.relative_deadline for task in certificate.tasks)
        if core3 else analysis
    )
    horizon = ServiceHorizonContract(
        analysis, service_horizon if core3 else 0, service_horizon,
    )
    trace = tuple(Fraction(1, 1024) for _ in range(service_horizon))
    beta = SharedSolarInput(trace, {}).beta(analysis)
    return VerifiedSolarServiceMaterialV2(
        "c" * 64, "d" * 64, "e" * 64, "f" * 64, build_id,
        "1" * 64, "2" * 64, "3" * 64, 1, 0, Fraction(1), horizon,
        trace, beta, "4" * 64, "5" * 64, "6" * 64, "{}",
    )


def _context(record, task_energy, service, *, formal_ready=True):
    binding = FrozenMapping({
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "service_material_identity": service.service_material_identity,
    })
    return SharedEnergyRunContext(
        BUILD_ID,
        FrozenMapping({task_energy.task_energy_material_identity: task_energy}),
        FrozenMapping({service.service_material_identity: service}),
        FrozenMapping({record.record_id: binding}), FrozenMapping({}),
        formal_ready,
    )


def _prepared(tmp_path, config, record):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        canonical_json({"manifest_id": BUILD_ID}) + "\n", encoding="utf-8",
    )
    method = str(record.material.get("method", "CW_THETA_CW"))
    return build_test_prepared_config_v2(
        config, output_root=tmp_path / "out-v2",
        taskset_store=tmp_path / "store-v2",
        production_manifest_path=manifest, source_root=ROOT,
        selected_ordinals=(record.ordinal,),
        timeout_contract={method: {
            "initial_timeout_seconds": 0,
            "retry_timeout_seconds": 1,
            "maximum_attempts": 2,
        }},
    )


def _manifest():
    return {
        "manifest_id": BUILD_ID,
        "repository": {"git_commit": "a" * 40, "git_tree": "9" * 40},
    }


def test_v2_provider_is_w_free_and_historical_energy_is_non_authoritative(monkeypatch):
    from experiments.v9_3.rta4_formal_execution import ProductionTasksetProvider
    from global_task_generator import EnergyAwareTaskGenerator

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy W projection was called")

    monkeypatch.setattr(ProductionTasksetProvider, "_generate_skeleton", forbidden)

    class Generator:
        historical = 1

        def __init__(self, *args, **kwargs):
            self.inner = EnergyAwareTaskGenerator(*args, **kwargs)
            self.scheduler_energy_model = self.inner.scheduler_energy_model
            self.task_workload_candidates = self.inner.task_workload_candidates

        def generate_taskset(self, **kwargs):
            tasks, resources, dag, energy = self.inner.generate_taskset(**kwargs)
            for task in tasks:
                task["energy"] = self.historical
            return tasks, resources, dag, energy

    config = default_rta4_formal_config_v2("CORE-1")
    record = next(iter_formal_plan_v2(config))
    first_provider = ProductionTasksetProviderV2(config, generator_factory=Generator)
    first = first_provider(record)
    first_energy = _task_energy(record, first_provider, first)
    Generator.historical = 10 ** 30
    second_provider = ProductionTasksetProviderV2(config, generator_factory=Generator)
    second = second_provider(record)
    second_energy = _task_energy(record, second_provider, second)
    assert first == second
    assert first_energy == second_energy
    encoded = first.canonical_bytes().decode("utf-8")
    assert "actual_power" not in encoded
    assert "P_exact" not in encoded
    assert "watts" not in encoded
    assert all(entry.unit == "J/tick" for entry in first_energy.entries)


def test_manifest_default_closure_contains_formal_v2_pipeline():
    required = {
        "experiments/v9_3/rta4_taskset_v2.py",
        "experiments/v9_3/rta4_formal_plan_v2.py",
        "experiments/v9_3/rta4_formal_lifecycle_v2.py",
        "experiments/v9_3/rta4_formal_runner_v2.py",
        "experiments/v9_3/rta4_formal_execution.py",
        "experiments/v9_3/rta4_production_build_manifest.py",
        "scripts/run_v9_3_rta4_formal.py",
        "scripts/build_v9_3_rta4_production_manifest.py",
        "scripts/build_v9_3_rta4_v2_contracts.py",
        "configs/v9_3_rta4_shared_energy_support_v2.yaml",
    }
    required.update(
        f"configs/v9_3_rta4_core{suffix}_unauthorized_pre_pilot_v2_shared_energy.yaml"
        for suffix in ("1", "2", "3", "4", "5a", "5b")
    )
    assert required.issubset(DEFAULT_RELEVANT_SOURCES)


def test_v2_context_and_retry_evidence_are_deeply_immutable():
    config, record, provider, certificate = _record_and_certificate()
    task_energy = _task_energy(record, provider, certificate)
    service = _service(certificate)
    context = _context(record, task_energy, service)
    with pytest.raises(TypeError):
        context.record_bindings[record.record_id]["service_material_identity"] = "x"
    executor = ProductionRTAExecutorV2(
        config, run_context=context,
        timeout_contract={record.material["method"]: {
            "initial_timeout_seconds": 0,
            "retry_timeout_seconds": 1,
            "maximum_attempts": 2,
        }},
    )
    result = executor(record, certificate)
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["status"] == "TIMEOUT"
    assert result["attempts"][0]["attempt_index"] == 0
    assert result["attempts"][1]["attempt_index"] == 1
    assert result["failure_reason"] != "numeric/theory contract mismatch"
    assert result["attempts"][0]["taskset_identity"] == certificate.taskset_id
    with pytest.raises(TypeError):
        result["attempts"][0]["status"] = "FORGED"
    with pytest.raises(Exception):
        ProductionRTAExecutorV2(
            config, run_context=_context(
                record, task_energy, service, formal_ready=False,
            ),
            timeout_contract={record.material["method"]: {
                "initial_timeout_seconds": 0,
                "retry_timeout_seconds": 1,
                "maximum_attempts": 2,
            }},
        )


def test_v2_store_writer_resume_and_bidirectional_isolation(tmp_path):
    from experiments.v9_3.rta4_formal_store import RTA4FormalTasksetStore

    config, record, provider, certificate = _record_and_certificate()
    task_energy = _task_energy(record, provider, certificate)
    service = _service(certificate)
    prepared = _prepared(tmp_path, config, record)
    authorization = build_test_authorization_v2(prepared)
    store = RTA4FormalTasksetStoreV2(
        tmp_path / "store-v2", production_manifest_identity=BUILD_ID,
    )
    first = store.put(certificate, task_energy)
    assert store.put(certificate, task_energy) == first
    assert store.load_certificate(certificate.taskset_id) == certificate
    with pytest.raises(Exception):
        RTA4FormalTasksetStore(tmp_path / "store-v2")
    with pytest.raises(RTA4FormalLifecycleV2Error):
        RTA4FormalTasksetStoreV2(
            tmp_path / "store-v1", production_manifest_identity=BUILD_ID,
        ).put(object(), task_energy)

    writer = RTA4FormalResultWriterV2(
        tmp_path / "out-v2", prepared_config=prepared,
        authorization=authorization, production_manifest=_manifest(),
        records=(record,),
    )
    retry = retry_resume_identity_v2(
        prepared_config_id=prepared["prepared_config_id"],
        authorization_id=authorization["authorization_id"],
        plan_identity=prepared["plan_identity"],
        production_manifest_identity=BUILD_ID,
        plan_record_identity=record.record_id,
        taskset_identity=certificate.taskset_id,
        task_energy_material_identity=task_energy.task_energy_material_identity,
        service_material_identity=service.service_material_identity,
        beta_material_identity=service.beta_material_identity,
        method=record.material["method"], exact_e0=record.material["exact_e0"],
        timeout_sequence=(0, 1),
    )
    attempt = {
        "attempt_index": 0, "timeout_seconds": 0, "status": "TIMEOUT",
        "runtime_wall_seconds": "0", "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0, "error_classification": "TIMEOUT",
        "analysis_identity": "8" * 64,
        "taskset_identity": certificate.taskset_id,
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "production_build_manifest_identity": BUILD_ID,
    }
    material = {
        "row_schema": RTA4_RESULT_ROW_SCHEMA_V2,
        "profile": config["experiment_contract"]["profile"],
        "schema_sha256": formal_schema_hash_v2(),
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "theory_document_sha256": config["identity"]["theory_document_sha256"],
        "config_identity": prepared["config_identity"],
        "plan_identity": prepared["plan_identity"],
        "plan_record_identity": record.record_id,
        "execution_identity": record.execution_id,
        "production_build_manifest_identity": BUILD_ID,
        "source_commit": "a" * 40, "source_tree": "9" * 40,
        "taskset_source_sha256": certificate.taskset_source_sha256,
        "taskset_identity": certificate.taskset_id,
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "method": record.material["method"], "exact_e0": record.material["exact_e0"],
        "status": "TIMEOUT", "response_result": {"taskset_proven": False},
        "timeout_seconds": 0, "attempts": [attempt],
        "retry_resume_identity": retry,
    }
    row = {**material, "result_identity": domain_hash(
        "ASAP_BLOCK:V9.3:RTA4_RESULT:v2", material,
    )}
    assert writer.write_result(row) == row
    assert writer.write_result(row) == row
    resumed = RTA4FormalResultWriterV2(
        tmp_path / "out-v2", prepared_config=prepared,
        authorization=authorization, production_manifest=_manifest(),
        records=(record,), require_existing_namespace=True,
    )
    assert resumed.completed_rows()[record.execution_id] == row
    v1 = dict(row)
    v1["row_schema"] = "V1"
    with pytest.raises(RTA4FormalLifecycleV2Error):
        resumed.write_result(v1)


def test_core3_production_executor_prepares_only_v2_j_per_tick_projection(tmp_path):
    config, record, provider, certificate = _record_and_certificate("CORE-3")
    task_energy = _task_energy(record, provider, certificate)
    service = _service(certificate, core3=True)
    context = _context(record, task_energy, service)
    binary = ROOT / "rtsim/rtsim"
    manifest = {
        "manifest_id": BUILD_ID,
        "simulator": {"binary": {
            "path": str(binary),
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }},
    }
    executor = ProductionSimulationExecutorV2(
        config, run_context=context, production_manifest=manifest,
        system_config_path=SYSTEM, energy_support_path=SUPPORT,
        output_root=tmp_path, simulation_timeout_seconds=30,
    )
    release, window, payload, shared, observed_task, observed_service = (
        executor.prepare(record, certificate)
    )
    assert observed_task is task_energy
    assert observed_service is service
    assert release.taskset_id == certificate.taskset_id
    assert window.observation_horizon == service.horizon.service_material_horizon_ticks
    assert shared["production_build_manifest_identity"] == BUILD_ID
    assert all("actual_power" not in row for row in payload)
    assert all(Fraction(row["P"]) == entry.energy_j_per_tick for row, entry in zip(
        payload, task_energy.entries,
    ))
    assert _task_payload_for_trace_id(payload, "0") is payload[0]
    assert _task_payload_for_trace_id(payload, payload[0]["task_id"]) is payload[0]
    with pytest.raises(SimulationTraceError, match="not projectable"):
        _task_payload_for_trace_id(payload, "unknown-task")


def test_support_horizon_shortfall_fails_before_service_construction(tmp_path):
    config, record, provider, certificate = _record_and_certificate("CORE-3")
    task_energy = _task_energy(record, provider, certificate)
    short = ServiceHorizonContract(
        max(task.relative_deadline for task in certificate.tasks) - 1,
        30_000 + max(task.relative_deadline for task in certificate.tasks),
        30_000 + max(task.relative_deadline for task in certificate.tasks),
    )
    assert short.service_material_horizon_ticks > 30_000
    document = SUPPORT.read_text(encoding="utf-8").replace("horizon: 30200", "horizon: 100")
    path = tmp_path / "short.yaml"
    path.write_text(document, encoding="utf-8")
    calls = []

    def forbidden_constructor(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("trace construction must not start")

    registry = ServiceMaterialRegistry({
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "manifest_id": BUILD_ID,
        "cpp_toolchain": {"compiler": {"path": "/usr/bin/c++", "sha256": "c" * 64}},
        "solar_verifier": {"binary": {"sha256": "v" * 64}},
    }, constructor=forbidden_constructor)
    spec = ServiceMaterialSpec(
        str(SYSTEM), str(path), str(ROOT), Fraction(1), short,
    )
    with pytest.raises(SharedEnergyMaterialError, match="declared support horizon"):
        registry.prepare((spec,))
    registry.close()
    assert calls == []


def _run_official_cli(prepared_path, authorization_path, operation="--execute"):
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/run_v9_3_rta4_formal.py"),
            "--prepared-config", str(prepared_path),
            "--authorization", str(authorization_path), operation,
        ],
        cwd=str(ROOT), check=False, capture_output=True, text=True,
        timeout=360,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _write_test_run_artifacts(
    root, *, core, ordinals, manifest_path, worker_count,
    simulation_timeout_seconds=180,
):
    config = default_rta4_formal_config_v2(core)
    records = {
        record.ordinal: record for record in iter_formal_plan_v2(config)
        if record.ordinal in set(ordinals)
    }
    methods = {
        str(record.material.get("method", "CW_THETA_CW"))
        for record in records.values()
    }
    timeouts = {
        method: {
            "initial_timeout_seconds": 0,
            "retry_timeout_seconds": 3,
            "maximum_attempts": 2,
        }
        for method in methods
    }
    prepared = build_test_prepared_config_v2(
        config, output_root=root / "output-v2",
        taskset_store=root / "store-v2",
        production_manifest_path=manifest_path, source_root=ROOT,
        selected_ordinals=ordinals, timeout_contract=timeouts,
        worker_count=worker_count, max_in_flight=max(worker_count, len(ordinals)),
        simulation_timeout_seconds=simulation_timeout_seconds,
    )
    authorization = build_test_authorization_v2(prepared)
    prepared_path = root / "prepared-v2.json"
    authorization_path = root / "authorization-test-only-v2.json"
    from experiments.v9_3.result_writer import atomic_write_json
    atomic_write_json(prepared_path, prepared)
    atomic_write_json(authorization_path, authorization)
    return prepared_path, authorization_path


def _terminal_rows(root):
    paths = sorted((root / "output-v2" / (
        "formal_terminal_results_v2_shared_energy"
    )).glob("*.json"))
    return paths, [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def test_official_v2_bounded_e2e_without_core_mock_or_fake(tmp_path):
    """Real manifest/provider/solar/solver/writer/resume/CORE-3 micro E2E."""

    simulator_candidates = (
        Path(os.environ.get("RTA4_TEST_SIMULATOR", "")),
        Path("/home/devcontainers/builds/partsim-b4-release/rtsim/rtsim"),
    )
    simulator = next((path for path in simulator_candidates if path.is_file()), None)
    if simulator is None:
        pytest.skip("no dynamically complete bounded RTSim build is available")
    verifier = tmp_path / "solar-stod-verifier"
    subprocess.run([
        "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-pedantic",
        str(ROOT / "tools/rta4_solar_stod_verifier.cpp"),
        "-o", str(verifier),
    ], check=True, cwd=str(ROOT), timeout=60)
    manifest = generate_production_build_manifest(
        source_root=ROOT, simulator_binary=simulator,
        verifier_binary=verifier, compiler="c++",
        build_commands={
            "simulator": ["bounded-existing-rtsim"],
            "verifier": ["bounded-test-verifier"],
        },
        require_clean=True,
    )
    manifest_path = tmp_path / "production-manifest-v2.json"
    write_production_build_manifest(manifest_path, manifest)

    core1 = tmp_path / "core1-worker1"
    prepared, authorization = _write_test_run_artifacts(
        core1, core="CORE-1", ordinals=(0, 1, 2, 3),
        manifest_path=manifest_path, worker_count=1,
    )
    first = _run_official_cli(prepared, authorization)
    assert first["complete"] is True and first["processed_records"] == 4
    paths, rows = _terminal_rows(core1)
    assert {row["method"] for row in rows} == {
        "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
    }
    assert len({row["taskset_identity"] for row in rows}) == 1
    assert len({row["task_energy_material_identity"] for row in rows}) == 1
    assert len({row["service_material_identity"] for row in rows}) == 1
    assert all([
        attempt["timeout_seconds"] for attempt in row["attempts"]
    ] == [0, 3] for row in rows)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    resumed = _run_official_cli(prepared, authorization, "--resume")
    assert resumed["processed_records"] == 0 and resumed["complete"] is True
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert after == before

    reference = next(row for row in rows if row["method"] == "CW_THETA_CW")
    for workers in (2, 4):
        root = tmp_path / f"core1-worker{workers}"
        worker_prepared, worker_authorization = _write_test_run_artifacts(
            root, core="CORE-1", ordinals=(0,),
            manifest_path=manifest_path, worker_count=workers,
        )
        summary = _run_official_cli(worker_prepared, worker_authorization)
        assert summary["processed_records"] == 1
        _worker_paths, worker_rows = _terminal_rows(root)
        row = worker_rows[0]
        for key in (
            "taskset_source_sha256", "taskset_identity",
            "task_energy_material_identity", "service_material_identity",
            "beta_material_identity", "method", "exact_e0", "status",
            "response_result",
        ):
            assert row[key] == reference[key]
        assert [
            (attempt["attempt_index"], attempt["timeout_seconds"],
             attempt["status"], attempt["analysis_identity"])
            for attempt in row["attempts"]
        ] == [
            (attempt["attempt_index"], attempt["timeout_seconds"],
             attempt["status"], attempt["analysis_identity"])
            for attempt in reference["attempts"]
        ]

    core3 = tmp_path / "core3-worker1"
    core3_prepared, core3_authorization = _write_test_run_artifacts(
        core3, core="CORE-3", ordinals=(0,),
        manifest_path=manifest_path, worker_count=1,
        simulation_timeout_seconds=180,
    )
    core3_summary = _run_official_cli(core3_prepared, core3_authorization)
    assert core3_summary["processed_records"] == 1
    _core3_paths, core3_rows = _terminal_rows(core3)
    simulation = core3_rows[0]
    response = simulation["response_result"]
    assert simulation["status"] == "COMPLETED"
    assert response["simulation_status"] == "COMPLETED"
    assert response["task_energy_unit"] == "J/tick"
    assert response["core3_shared_energy_projection_identity"]
    assert response["release_projection_identity"]
    assert response["simulation_result_identity"]
    assert response["job_results"]
    assert simulation["production_build_manifest_identity"] == manifest["manifest_id"]
    assert simulation["profile"] == default_rta4_formal_config_v2(
        "CORE-3"
    )["experiment_contract"]["profile"]
    assert all(
        "actual_power" not in canonical_json(row)
        for row in (*rows, simulation)
    )
