from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3.result_writer import atomic_write_json, read_csv
from experiments.v9_3.rta4_formal_authorization import (
    RTA4AuthorizationError, authorize_candidate,
    build_authorization_candidate, validate_authorization_document,
    verify_live_authorization,
)
from experiments.v9_3.rta4_formal_aggregation import (
    RTA4FormalAggregationError, _core5b_scalability_rows,
    _figure5, aggregate_formal_run,
)
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, domain_hash, load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_environment import (
    RTA4EnvironmentError, build_command_chain_manifest,
    build_command_manifest,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, build_simulator_manifest,
    build_source_manifest, validate_bound_source_file,
    load_strict_json, validate_command_invocation, validate_source_manifest,
)
from experiments.v9_3.rta4_formal_freeze import (
    RTA4_FROZEN_ALL_PLAN_DIGEST, RTA4_TIMEOUT_METHODS,
    RTA4FreezeError, build_freeze_manifest, prepare_formal_configs,
    validate_prepared_config,
)
from experiments.v9_3.rta4_formal_execution import (
    AuthorizedRTA4Runner, ProductionRTAExecutor, ProductionTasksetProvider,
    RTA4ExecutionError, _bounded_execution_batches,
)
import experiments.v9_3.rta4_formal_execution as rta4_execution
from experiments.v9_3.rta4_formal_plan import (
    FormalPlanRecord, iter_core4_plan, iter_core5b_plan, iter_formal_plan,
)
from experiments.v9_3.rta4_formal_plotting import (
    render_formal_publication_figures,
)
from experiments.v9_3.rta4_formal_pipeline import (
    RTA4FormalPipelineError, _execution_peak_rss, _execution_seconds,
    mathematical_result_hash,
)
from experiments.v9_3.rta4_formal_validation import RTA4_CHECKPOINT_DOMAIN
from experiments.v9_3.rta4_formal_pilot import (
    RTA4_PILOT_OBSERVATIONS, RTA4_PILOT_OUTPUT_MARKER,
    RTA4_PILOT_REPORT, RTA4PilotError, build_pilot_manifest,
    build_pilot_observations, build_pilot_report,
    validate_pilot_observations, validate_pilot_report,
)
from experiments.v9_3.rta4_pilot_execution import (
    PilotExecutionRunner, PilotTasksetProvider,
    RTA4_PILOT_AUDIT_DOMAIN,
    RTA4_PILOT_EXECUTION_CONFIG, build_pilot_execution_config,
    build_pilot_raw_terminal, build_simulation_support,
)
import experiments.v9_3.rta4_pilot_execution as pilot_execution
from experiments.v9_3.constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE, FIXED_SLACK_FRACTION_VARIANT,
    GenerationRequest, SkeletonTask, build_taskset_identity_certificate,
)
from experiments.v9_3.release_applicability import (
    RELEASE_SNAPSHOT_STAGE, SIMULATOR_TRACE_CONTRACT_VERSION,
)
from experiments.v9_3.task_identity import runtime_task_name_for_source_id


ROOT = Path(__file__).resolve().parents[1]


def _real_domain_pilot_filesystem(root, configs, config_paths):
    output = root / "pilot"
    store = root / "pilot-taskset-store"
    pilot = build_pilot_manifest(
        configs,
        core_record_counts={
            core: (4 if core == "CORE-5B" else 1)
            for core in RTA4_CORES
        },
        selection_seed="RTA4-TEST-PILOT-V2",
        output_root=output, taskset_store=store,
        config_paths=config_paths,
    )
    output.mkdir()
    manifest_path = output / RTA4_PILOT_OUTPUT_MARKER
    atomic_write_json(manifest_path, pilot)

    source_repo = root / "pilot-source"
    source_repo.mkdir()
    base_system = source_repo / "base-system.yml"
    energy_config = source_repo / "energy.yml"
    base_system.write_text("system: synthetic-fixture\n", encoding="utf-8")
    energy_config.write_text("{}\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=source_repo, check=True)
    subprocess.run(
        ("git", "add", "base-system.yml", "energy.yml"),
        cwd=source_repo, check=True,
    )
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "pilot fixture",
        ),
        cwd=source_repo, check=True,
    )
    source_manifest = build_source_manifest(
        source_repo, (base_system, energy_config),
    )
    execution = build_pilot_execution_config(
        manifest_path, pilot, source_manifest=source_manifest,
        output_root=output, taskset_store=store,
        simulator_manifest=build_simulator_manifest("/bin/true"),
        simulation_support=build_simulation_support(
            base_system_path=base_system,
            energy_config_path=energy_config,
        ),
        default_worker_count=2, max_in_flight=4,
        provisional_rta_attempt_timeout_seconds=2,
        provisional_simulation_timeout_seconds=2,
        memory_soft_limit_bytes=1 << 60,
        checkpoint_interval_records=2, maximum_attempts=2,
    )
    atomic_write_json(output / RTA4_PILOT_EXECUTION_CONFIG, execution)
    runner = PilotExecutionRunner(configs, pilot, execution)
    store_manifest, certificates = runner._write_initial_namespace(
        PilotTasksetProvider(configs), None,
    )
    simulator = _synthetic_simulator(root / "pilot-fixture-traces")
    last_execution_id = None
    for record in runner.records:
        certificate = pilot_execution._certificate_for_record(
            record, certificates,
        )
        batch_id, (worker_root,) = runner._register_worker_batch(
            (record,),
        )
        try:
            callback = (
                simulator if record.kind == "simulation"
                else _synthetic_rta
            )
            result = pilot_execution._worker_execute(
                record, certificate, configs[record.core], execution,
                callback, str(worker_root),
            )
            metrics = pilot_execution._validate_metrics(
                result["metrics"], final=False,
            )
            metrics[
                "worker_throughput_milli_records_per_second"
            ] = 1000
            if record.kind == "simulation":
                simulation_id = pilot_execution._simulation_identity(
                    record, certificate,
                )[3]
                trace_size, trace_sha = runner._persist_trace(
                    record, certificate, result["trace_payload"],
                    simulation_id, worker_root,
                )
            else:
                simulation_id = None
                trace_size, trace_sha = 0, None
            metrics["trace_size_bytes"] = trace_size
            raw = build_pilot_raw_terminal(
                runner.selected[str(record.execution_id)],
                execution, certificate, metrics,
                trace_sha256=trace_sha, simulation_id=simulation_id,
            )
            pilot_execution._write_json_once(
                output / pilot_execution.RTA4_PILOT_RAW_TERMINAL_DIRECTORY
                / f"{record.execution_id}.json",
                raw,
            )
        finally:
            runner._cleanup_worker_batch(batch_id)
        last_execution_id = str(record.execution_id)
    runner._commit_checkpoint(
        store_manifest, certificates, phase="EXECUTING",
        triggering_execution_id=last_execution_id,
        transaction_hook=None,
    )
    audit = runner._finalize(store_manifest, certificates, None)
    return (
        pilot,
        load_strict_json(output / RTA4_PILOT_OBSERVATIONS),
        load_strict_json(output / RTA4_PILOT_REPORT),
        audit,
    )


@pytest.fixture(scope="module")
def frozen_contract(tmp_path_factory):
    root = tmp_path_factory.mktemp("rta4-auth-contract")
    paths = {
        core: (
            ROOT / "configs"
            / f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
        ).resolve()
        for core in RTA4_CORES
    }
    configs = {
        core: load_rta4_formal_config(path, expected_core=core)
        for core, path in paths.items()
    }
    (
        pilot, pilot_observations, report, audit,
    ) = _real_domain_pilot_filesystem(
        root, configs, paths,
    )
    timeout = {
        "contract_version": "ASAP_BLOCK_V9_3_RTA4_TIMEOUT_V1",
        "pilot_report_id": report["pilot_report_id"],
        "method_order": list(RTA4_TIMEOUT_METHODS),
        "methods": {
            method: {
                "initial_timeout_seconds": 10,
                "retry_timeout_seconds": 20,
                "maximum_attempts": 2,
                "failure_origin": "UNIFIED_RTA_ADAPTER",
                "pilot_evidence": report["pilot_report_id"],
            }
            for method in RTA4_TIMEOUT_METHODS
        },
    }
    operational = {}
    for core in RTA4_CORES:
        sources = (
            {"CORE-1": str(root / "output-CORE-1")}
            if core in {"CORE-2", "CORE-3"}
            else {"CORE-4": str(root / "output-CORE-4")}
            if core == "CORE-5B" else {}
        )
        operational[core] = {
            "worker_count": 2,
            "max_in_flight": 4,
            "memory_limit_bytes": 1024 * 1024,
            "checkpoint_interval_records": 2,
            "simulation_timeout_seconds": 30,
            "output_root": str(root / f"output-{core}"),
            "taskset_store": str(root / "taskset-store"),
            "source_closures": sources,
            "simulator_binary": "/bin/true" if core == "CORE-3" else None,
            "execution_order": (
                2 if core in {"CORE-2", "CORE-3", "CORE-5B"} else 1
            ),
            "resume_policy": "REVALIDATE_ALL_BINDINGS_SKIP_TERMINALS_V1",
        }
    prepared = prepare_formal_configs(
        configs, pilot_manifest=pilot,
        pilot_observations=pilot_observations, pilot_report=report,
        pilot_audit=audit,
        timeout_contract=timeout, operational=operational,
        config_paths=paths,
        pilot_root=root / "pilot",
    )
    freeze = build_freeze_manifest(prepared)
    documents = {
        "pilot": root / "pilot.json",
        "observations": root / "observations.json",
        "report": root / "report.json",
        "freeze": root / "freeze.json",
    }
    atomic_write_json(documents["pilot"], pilot)
    atomic_write_json(documents["observations"], pilot_observations)
    atomic_write_json(documents["report"], report)
    atomic_write_json(documents["freeze"], freeze)
    for core in RTA4_CORES:
        path = root / f"prepared-{core}.json"
        atomic_write_json(path, prepared[core])
        documents[f"prepared-{core}"] = path
    return {
        "root": root, "configs": configs, "pilot": pilot,
        "observations": pilot_observations, "report": report,
        "audit": audit,
        "timeout": timeout, "prepared": prepared, "freeze": freeze,
        "documents": documents,
    }


def _source_repo(root: Path) -> tuple[Path, dict]:
    repo = root / "source-repo"
    repo.mkdir()
    source = repo / "entry.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "add", "entry.py"), cwd=repo, check=True)
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "source",
        ),
        cwd=repo, check=True,
    )
    return repo, build_source_manifest(repo, ("entry.py",))


def _candidate(frozen_contract, source, *, test_mode=False):
    prepared = frozen_contract["prepared"]["CORE-1"]
    dependencies = build_dependency_manifest()
    environment = build_environment_manifest(dependencies)
    command = (
        build_command_manifest(
            ("python3", "scripts/run_v9_3_rta4_formal.py", "--execute"),
            cwd=ROOT, operation="execute", core="CORE-1",
        )
        if test_mode
        else build_command_chain_manifest(
            {
                operation: ("python3", f"{operation}.py")
                for operation in (
                    "execute", "resume", "validate-only", "audit",
                    "aggregate", "plot",
                )
            },
            cwd=ROOT, core="CORE-1",
        )
    )
    return build_authorization_candidate(
        prepared_config=prepared,
        freeze_manifest=frozen_contract["freeze"],
        all_prepared_configs=frozen_contract["prepared"],
        pilot_manifest=frozen_contract["pilot"],
        pilot_observations=frozen_contract["observations"],
        pilot_report=frozen_contract["report"],
        source_manifest=source,
        dependency_manifest=dependencies,
        environment_manifest=environment,
        hardware_manifest=build_hardware_manifest(),
        command_manifest=command,
        simulator_manifest=build_simulator_manifest(None),
        prepared_config_path=frozen_contract["documents"]["prepared-CORE-1"],
        freeze_manifest_path=frozen_contract["documents"]["freeze"],
        pilot_manifest_path=frozen_contract["documents"]["pilot"],
        pilot_observations_path=frozen_contract["documents"]["observations"],
        pilot_report_path=frozen_contract["documents"]["report"],
        authorization_path=frozen_contract["root"] / (
            "test-auth.json" if test_mode else "production-auth.json"
        ),
        test_mode=test_mode,
    )


def test_pilot_is_result_independent_and_report_is_engineering_only(
    frozen_contract,
):
    pilot = frozen_contract["pilot"]
    assert pilot["scientific_interpretation"].startswith("FORBIDDEN")
    assert all(
        len(pilot["selected_records"][core])
        == (4 if core == "CORE-5B" else 1)
        for core in RTA4_CORES
    )
    validate_pilot_report(
        frozen_contract["report"], pilot, frozen_contract["observations"],
    )
    contaminated = deepcopy(frozen_contract["report"])
    contaminated["scientific_results_included"] = True
    with pytest.raises(RTA4PilotError):
        validate_pilot_report(
            contaminated, pilot, frozen_contract["observations"],
        )


def test_freeze_preserves_scientific_identity_and_rejects_timeout_drift(
    frozen_contract,
):
    prepared = frozen_contract["prepared"]["CORE-1"]
    assert prepared["scientific_assertions"]["all_plan_digest"] == (
        RTA4_FROZEN_ALL_PLAN_DIGEST
    )
    validate_prepared_config(prepared)
    drift = deepcopy(prepared)
    drift["timeout_contract"]["methods"]["CW_D"][
        "pilot_evidence"
    ] = "0" * 64
    with pytest.raises(RTA4FreezeError):
        validate_prepared_config(drift)


def test_two_step_authorization_and_test_domain_are_disjoint(frozen_contract):
    _, source = _source_repo(frozen_contract["root"])
    candidate = _candidate(frozen_contract, source)
    with pytest.raises(RTA4AuthorizationError, match="exact"):
        authorize_candidate(candidate, confirm_authorization_id="approve")
    authorized = authorize_candidate(
        candidate, confirm_authorization_id=candidate["authorization_id"],
    )
    assert verify_live_authorization(authorized) == authorized
    test_candidate = _candidate(frozen_contract, source, test_mode=True)
    test_authorized = authorize_candidate(
        test_candidate,
        confirm_authorization_id=test_candidate["authorization_id"],
        test_mode=True,
    )
    with pytest.raises(RTA4AuthorizationError, match="TEST"):
        validate_authorization_document(test_authorized)
    validate_authorization_document(test_authorized, allow_test=True)
    with pytest.raises(RTA4ExecutionError, match="explicit synthetic"):
        AuthorizedRTA4Runner(
            frozen_contract["prepared"]["CORE-1"], test_authorized,
        ).run()
    with pytest.raises(RTA4ExecutionError, match="refuses synthetic"):
        AuthorizedRTA4Runner(
            frozen_contract["prepared"]["CORE-1"], authorized,
        ).run(synthetic_ordinals=(0,))
    with pytest.raises(RTA4ExecutionError, match="live command"):
        AuthorizedRTA4Runner(
            frozen_contract["prepared"]["CORE-1"], authorized,
        ).run(max_records=0)
    with pytest.raises(RTA4ExecutionError, match="injected"):
        AuthorizedRTA4Runner(
            frozen_contract["prepared"]["CORE-1"], authorized,
        ).run(
            max_records=0, certificate_provider=_synthetic_certificate,
            rta_executor=_synthetic_rta,
        )


def test_live_environment_drift_invalidates_authorization(
    frozen_contract, monkeypatch,
):
    drift_root = frozen_contract["root"] / "environment-drift"
    drift_root.mkdir()
    _, source = _source_repo(drift_root)
    candidate = _candidate(frozen_contract, source)
    authorized = authorize_candidate(
        candidate, confirm_authorization_id=candidate["authorization_id"],
    )
    monkeypatch.setenv("PYTHONHASHSEED", "RTA4_DRIFT_SENTINEL")
    with pytest.raises(RTA4AuthorizationError, match="environment"):
        verify_live_authorization(authorized)


def test_source_manifest_rejects_byte_and_dirty_state_drift(
    frozen_contract,
):
    repo = frozen_contract["root"] / "drift-repo"
    repo.mkdir()
    path = repo / "bound.py"
    path.write_text("A = 1\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "add", "bound.py"), cwd=repo, check=True)
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "source",
        ),
        cwd=repo, check=True,
    )
    manifest = build_source_manifest(repo, ("bound.py",))
    path.write_text("A = 2\n", encoding="utf-8")
    with pytest.raises(RTA4EnvironmentError):
        validate_source_manifest(manifest)


def test_command_chain_and_runtime_support_files_are_exact(frozen_contract):
    command_root = frozen_contract["root"] / "command-source"
    command_root.mkdir()
    repo, source = _source_repo(command_root)
    commands = {
        operation: ("python3", f"{operation}.py")
        for operation in (
            "execute", "resume", "validate-only", "audit", "aggregate", "plot",
        )
    }
    manifest = build_command_chain_manifest(
        commands, cwd=ROOT, core="CORE-3",
    )
    validate_command_invocation(
        manifest, argv=commands["resume"], cwd=ROOT,
        operation="resume", core="CORE-3",
    )
    with pytest.raises(RTA4EnvironmentError, match="argv"):
        validate_command_invocation(
            manifest, argv=("python3", "other.py"), cwd=ROOT,
            operation="resume", core="CORE-3",
        )
    assert validate_bound_source_file(source, repo / "entry.py")["path"] == (
        "entry.py"
    )
    with pytest.raises(RTA4EnvironmentError, match="outside"):
        validate_bound_source_file(
            source, frozen_contract["documents"]["pilot"],
        )
    duplicate = frozen_contract["root"] / "duplicate.json"
    duplicate.write_text('{"core":"CORE-1","core":"CORE-3"}', encoding="utf-8")
    with pytest.raises(RTA4EnvironmentError, match="duplicate"):
        load_strict_json(duplicate)


def test_core5b_execution_batches_use_all_real_worker_conditions(frozen_contract):
    records = tuple(iter_core5b_plan())[:8]
    batches = tuple(_bounded_execution_batches(
        records, max_in_flight=4, default_workers=2,
        config=frozen_contract["configs"]["CORE-5B"],
    ))
    assert [workers for workers, _ in batches] == [1, 2, 4, 8]
    assert [
        [record.material["worker_count"] for record in batch]
        for _, batch in batches
    ] == [[1, 1], [2, 2], [4, 4], [8, 8]]


def test_freeze_requires_complete_pilot(frozen_contract):
    stale = deepcopy(frozen_contract["report"])
    stale["pilot_status"] = "PILOT_PARTIAL"
    with pytest.raises(RTA4PilotError):
        validate_pilot_report(
            stale, frozen_contract["pilot"], frozen_contract["observations"],
        )


def _synthetic_certificate(record: FormalPlanRecord):
    material = record.material
    count = int(material.get("task_count", 10))
    request = GenerationRequest(
        formal_master_seed=930612,
        formal_generation_id=hashlib.sha256(
            f"RTA4-SYNTHETIC:{record.taskset_skeleton_slot_id}".encode()
        ).hexdigest(),
        processors=int(material.get("processor_count", 4)),
        task_count=count,
        target_normalized_utilization=Fraction(
            str(material.get("normalized_utilization", "1/2"))
        ),
        replicate_index=int(material.get("replicate_index", 0)),
        period_min=40, period_max=200,
        utilization_allocation_mode="frozen_v9_3_generator_v1",
        min_task_utilization=Fraction(1, 100),
        max_task_utilization=Fraction(4, 5),
        utilization_tolerance=Fraction(1, 100),
        wcet_rounding_mode="compensated",
        generator_version="ASAP_BLOCK_V9_3_GENERATOR_V1",
        power_generation_mode="generator_default_heterogeneous",
        power_generation_contract_identity="1" * 64,
        workload_candidate_identity="2" * 64,
        priority_policy="RM", dag_generation_mode="disabled",
        energy_aware_generation=False,
    )
    skeleton = tuple(
        SkeletonTask(
            f"tau-{index:02d}", index, 1, 170 + index,
            Fraction(index + 1, 10),
        )
        for index in range(count)
    )
    deadline = str(material.get(
        "deadline_variant", CONSTRAINED_UNIFORM_SLACK_MODE,
    ))
    mode, fixed = deadline, None
    if deadline.startswith(f"{FIXED_SLACK_FRACTION_VARIANT}:"):
        mode = FIXED_SLACK_FRACTION_VARIANT
        fixed = Fraction(deadline.split(":", 1)[1])
    return build_taskset_identity_certificate(
        request, skeleton, deadline_mode=mode,
        fixed_slack_fraction=fixed,
        power_scale=Fraction(str(material.get("power_scale", "1"))),
    )


def _synthetic_rta(_record, certificate):
    result = {
        "solver_status": "COMPLETED",
        "taskset_certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": True, "failure_reason": "NA",
        "fallback_used": False,
        "task_results": [
            {
                "task_solver_status": "CANDIDATE_FOUND",
                "task_certification_status": "CERTIFIED",
                "candidate_response_time": 1,
                "checked_w_count": 1, "checked_q_count": 0,
                "checked_h_count": 0, "failure_reason": "NA",
                "witness": [],
            }
            for _task in certificate.tasks
        ],
    }
    attempt = {
        "solver_status": "COMPLETED", "failure_origin": "NA",
        "runtime_wall_seconds": "0.25",
        "runtime_cpu_seconds": "0.125", "peak_rss_bytes": 4096,
    }
    return {
        **result, "attempts": [attempt],
        "runtime_wall_seconds": "0.25",
        "runtime_cpu_seconds": "0.125", "peak_rss_bytes": 4096,
    }


def _synthetic_simulator(trace_root: Path):
    def execute(_record, certificate, _projection, window, payload, simulation_id):
        releases = {}
        for task in payload:
            for release in range(
                task["arrival_offset"], window.release_horizon, task["T"]
            ):
                releases.setdefault(release, []).append(task)
        events = []
        for release, tasks in sorted(releases.items()):
            for task in tasks:
                events.append({
                    "time": str(release), "event_type": "arrival",
                    "task_name": runtime_task_name_for_source_id(task["task_id"]),
                    "arrival_time": str(release),
                })
            for task in tasks:
                events.append({
                    "time": str(release),
                    "event_type": "release_energy_snapshot",
                    "task_name": runtime_task_name_for_source_id(task["task_id"]),
                    "arrival_time": str(release),
                    "available_energy_mJ": 1000,
                    "sampling_stage": RELEASE_SNAPSHOT_STAGE,
                    "scheduler": "gpfp_asap_block",
                    "trace_contract_version": SIMULATOR_TRACE_CONTRACT_VERSION,
                })
        document = {
            "events": events, "trace_schema_version": 2,
            "run_id": simulation_id,
            "taskset_semantic_hash": certificate.taskset_hash,
            "configured_scheduler": "gpfp_asap_block",
            "expected_simulation_horizon_ms": window.observation_horizon,
            "observed_simulation_end_ms": window.observation_horizon,
            "simulation_completed": True,
            "simulator_trace_contract_version": (
                SIMULATOR_TRACE_CONTRACT_VERSION
            ),
            "release_horizon_ms": window.release_horizon,
            "observation_horizon_ms": window.observation_horizon,
            "release_cutoff_enabled": True,
            "observation_horizon_reached": True,
            "simulation_completion_reason": "reached_horizon",
        }
        path = trace_root / f"{simulation_id}.json"
        atomic_write_json(path, document)
        jobs = [
            {
                "task_id": task["task_id"], "release_time": release,
                "completion_time": release + 1, "deadline_missed": False,
            }
            for task in payload
            for release in range(
                task["arrival_offset"], window.release_horizon, task["T"]
            )
        ]
        return {
            "trace_path": path, "simulation_status": "COMPLETED",
            "deadline_miss_count": 0, "max_observed_response": 1,
            "offered_harvest": "0", "required_margin": "0",
            "job_results": jobs,
        }
    return execute


def _source_binding(closure):
    return {
        closure.metadata["core"]: {
            "source_core": closure.metadata["core"],
            "absolute_root": str(closure.root.resolve()),
            "plan_sha256": closure.metadata["plan_sha256"],
            "closure_sha256": closure.closure_sha256,
            "authorization_id": closure.metadata["authorization_id"],
        }
    }


def _test_authorization(
    frozen_contract, core, source_manifest, source_bindings,
):
    prepared = frozen_contract["prepared"][core]
    dependencies = build_dependency_manifest()
    command = build_command_manifest(
        ("synthetic-rta4", core), cwd=ROOT,
        operation="execute", core=core,
    )
    candidate = build_authorization_candidate(
        prepared_config=prepared,
        freeze_manifest=frozen_contract["freeze"],
        all_prepared_configs=frozen_contract["prepared"],
        pilot_manifest=frozen_contract["pilot"],
        pilot_observations=frozen_contract["observations"],
        pilot_report=frozen_contract["report"],
        source_manifest=source_manifest,
        dependency_manifest=dependencies,
        environment_manifest=build_environment_manifest(dependencies),
        hardware_manifest=build_hardware_manifest(),
        command_manifest=command,
        simulator_manifest=build_simulator_manifest(
            "/bin/true" if core == "CORE-3" else None
        ),
        prepared_config_path=frozen_contract["documents"][f"prepared-{core}"],
        freeze_manifest_path=frozen_contract["documents"]["freeze"],
        pilot_manifest_path=frozen_contract["documents"]["pilot"],
        pilot_observations_path=frozen_contract["documents"]["observations"],
        pilot_report_path=frozen_contract["documents"]["report"],
        authorization_path=frozen_contract["root"] / f"auth-{core}.json",
        source_closure_bindings=source_bindings,
        test_mode=True,
    )
    authorized = authorize_candidate(
        candidate, confirm_authorization_id=candidate["authorization_id"],
        test_mode=True,
    )
    atomic_write_json(
        frozen_contract["root"] / f"auth-{core}.json", authorized,
    )
    return authorized


def test_synthetic_authorized_e2e_covers_all_core_dag_paths(frozen_contract):
    source_root = frozen_contract["root"] / "e2e-source"
    source_root.mkdir()
    _, source_manifest = _source_repo(source_root)

    auth1 = _test_authorization(
        frozen_contract, "CORE-1", source_manifest, {},
    )
    core1 = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-1"], auth1,
    ).run(
        synthetic_ordinals=tuple(range(12)),
        certificate_provider=_synthetic_certificate,
        rta_executor=_synthetic_rta,
    ).closure
    assert core1 is not None
    aggregate = aggregate_formal_run(
        core1.root, frozen_contract["root"] / "synthetic-aggregate",
        bootstrap_replicates=10, allow_test_authorization=True,
    )
    assert aggregate["execution_class"] == "SYNTHETIC_AUTHORIZED"
    plots = render_formal_publication_figures(
        frozen_contract["root"] / "synthetic-aggregate",
        frozen_contract["root"] / "synthetic-plots",
    )
    assert plots["source_aggregate_sha256"] == aggregate["aggregate_sha256"]

    source1 = _source_binding(core1)
    auth2 = _test_authorization(
        frozen_contract, "CORE-2", source_manifest, source1,
    )
    core2_provider = ProductionTasksetProvider(
        frozen_contract["prepared"]["CORE-2"],
        source_closures={"CORE-1": core1},
    )
    core2 = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-2"], auth2,
        source_closures={"CORE-1": core1},
    ).run(
        synthetic_ordinals=(0,), certificate_provider=core2_provider,
        rta_executor=_synthetic_rta,
    ).closure
    assert core2 is not None

    auth3 = _test_authorization(
        frozen_contract, "CORE-3", source_manifest, source1,
    )
    core3_provider = ProductionTasksetProvider(
        frozen_contract["prepared"]["CORE-3"],
        source_closures={"CORE-1": core1},
    )
    core3 = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-3"], auth3,
        source_closures={"CORE-1": core1},
    ).run(
        synthetic_ordinals=(0,), certificate_provider=core3_provider,
        simulator_executor=_synthetic_simulator(
            frozen_contract["root"] / "synthetic-traces"
        ),
    ).closure
    assert core3 is not None

    first5b = next(iter_core5b_plan())
    source_ordinal = next(
        row.ordinal for row in iter_core4_plan()
        if row.mathematical_request_id == first5b.mathematical_request_id
    )
    auth4 = _test_authorization(
        frozen_contract, "CORE-4", source_manifest, {},
    )
    core4 = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-4"], auth4,
    ).run(
        synthetic_ordinals=(source_ordinal,),
        certificate_provider=_synthetic_certificate,
        rta_executor=_synthetic_rta,
    ).closure
    assert core4 is not None

    auth5a = _test_authorization(
        frozen_contract, "CORE-5A", source_manifest, {},
    )
    core5a = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-5A"], auth5a,
    ).run(
        synthetic_ordinals=(0,),
        certificate_provider=_synthetic_certificate,
        rta_executor=_synthetic_rta,
    ).closure
    assert core5a is not None

    source4 = _source_binding(core4)
    auth5b = _test_authorization(
        frozen_contract, "CORE-5B", source_manifest, source4,
    )
    core5b_provider = ProductionTasksetProvider(
        frozen_contract["prepared"]["CORE-5B"],
        source_closures={"CORE-4": core4},
    )
    core5b = AuthorizedRTA4Runner(
        frozen_contract["prepared"]["CORE-5B"], auth5b,
        source_closures={"CORE-4": core4},
    ).run(
        synthetic_ordinals=(0, 1, 2, 3),
        certificate_provider=core5b_provider,
        rta_executor=_synthetic_rta,
    ).closure
    assert core5b is not None
    assert {
        row["worker_count"]
        for row in core5b.table("formal_rta_requests.csv")
    } == {"1", "2", "4", "8"}


def _variant_contract(frozen_contract, suffix):
    root = frozen_contract["root"] / suffix
    root.mkdir()
    paths = {
        core: (
            ROOT / "configs"
            / f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
        ).resolve()
        for core in RTA4_CORES
    }
    operational = {
        core: deepcopy(
            frozen_contract["prepared"][core]["operational"]
        )
        for core in RTA4_CORES
    }
    for core, row in operational.items():
        row["output_root"] = str(root / f"output-{core}")
        row["taskset_store"] = str(root / "taskset-store")
        if core in {"CORE-2", "CORE-3"}:
            row["source_closures"] = {
                "CORE-1": str(root / "output-CORE-1")
            }
        elif core == "CORE-5B":
            row["source_closures"] = {
                "CORE-4": str(root / "output-CORE-4")
            }
    prepared = prepare_formal_configs(
        frozen_contract["configs"],
        pilot_manifest=frozen_contract["pilot"],
        pilot_observations=frozen_contract["observations"],
        pilot_report=frozen_contract["report"],
        pilot_audit=frozen_contract["audit"],
        timeout_contract=frozen_contract["timeout"],
        operational=operational, config_paths=paths,
        pilot_root=frozen_contract["audit"]["pilot_root"],
    )
    freeze = build_freeze_manifest(prepared)
    documents = {
        "pilot": root / "pilot.json",
        "observations": root / "observations.json",
        "report": root / "report.json",
        "freeze": root / "freeze.json",
    }
    atomic_write_json(documents["pilot"], frozen_contract["pilot"])
    atomic_write_json(
        documents["observations"], frozen_contract["observations"],
    )
    atomic_write_json(documents["report"], frozen_contract["report"])
    atomic_write_json(documents["freeze"], freeze)
    for core in RTA4_CORES:
        documents[f"prepared-{core}"] = root / f"prepared-{core}.json"
        atomic_write_json(documents[f"prepared-{core}"], prepared[core])
    return {
        **frozen_contract, "root": root, "prepared": prepared,
        "freeze": freeze, "documents": documents,
    }


def test_freeze_rejects_test_execution_audit(frozen_contract):
    audit = deepcopy(frozen_contract["audit"])
    material = dict(audit)
    material.pop("audit_id")
    material["execution_class"] = "ENGINEERING_PILOT_TEST"
    material["freeze_eligible"] = False
    audit = {
        **material,
        "audit_id": domain_hash(RTA4_PILOT_AUDIT_DOMAIN, material),
    }
    with pytest.raises(RTA4FreezeError, match="differs|audited real"):
        prepare_formal_configs(
            frozen_contract["configs"],
            pilot_manifest=frozen_contract["pilot"],
            pilot_observations=frozen_contract["observations"],
            pilot_report=frozen_contract["report"],
            pilot_audit=audit,
            timeout_contract=frozen_contract["timeout"],
            operational={
                core: deepcopy(
                    frozen_contract["prepared"][core]["operational"]
                )
                for core in RTA4_CORES
            },
            config_paths={
                core: Path(
                    frozen_contract["prepared"][core]["source_config"][
                        "absolute_path"
                    ]
                )
                for core in RTA4_CORES
            },
            pilot_root=frozen_contract["audit"]["pilot_root"],
        )


def test_authorized_checkpoint_resume_skips_terminals_and_rejects_inventory_drift(
    frozen_contract,
):
    contract = _variant_contract(frozen_contract, "resume-contract")
    source_root = contract["root"] / "source"
    source_root.mkdir()
    _, source = _source_repo(source_root)
    authorization = _test_authorization(
        contract, "CORE-1", source, {},
    )
    calls = []

    def counted(record, certificate):
        calls.append(record.execution_id)
        return _synthetic_rta(record, certificate)

    runner = AuthorizedRTA4Runner(contract["prepared"]["CORE-1"], authorization)
    first = runner.run(
        synthetic_ordinals=(0, 1, 2), max_records=1,
        certificate_provider=_synthetic_certificate,
        rta_executor=counted,
    )
    assert not first.complete and first.pending_records == 2
    with pytest.raises(Exception):
        aggregate_formal_run(
            Path(contract["prepared"]["CORE-1"]["operational"]["output_root"]),
            contract["root"] / "premature-aggregate",
            bootstrap_replicates=10, allow_test_authorization=True,
        )
    with pytest.raises(RTA4ExecutionError, match="non-empty"):
        runner.run(
            synthetic_ordinals=(0, 1, 2),
            certificate_provider=_synthetic_certificate,
            rta_executor=counted,
        )
    checkpoint = json.loads(first.checkpoint_path.read_text(encoding="utf-8"))
    first_terminal = next(
        (
            Path(contract["prepared"]["CORE-1"]["operational"]["output_root"])
            / "formal_terminal_results"
        ).glob("*.json")
    )
    first_terminal_bytes = first_terminal.read_bytes()
    damaged = deepcopy(checkpoint)
    damaged["completed_count"] = 99
    atomic_write_json(first.checkpoint_path, damaged)
    with pytest.raises(RTA4ExecutionError, match="checkpoint"):
        runner.run(
            resume=True, synthetic_ordinals=(0, 1, 2),
            certificate_provider=_synthetic_certificate,
            rta_executor=counted,
        )
    atomic_write_json(first.checkpoint_path, checkpoint)
    final = runner.run(
        resume=True, synthetic_ordinals=(0, 1, 2),
        certificate_provider=_synthetic_certificate,
        rta_executor=counted,
    )
    assert final.complete and len(calls) == 3
    assert first_terminal.read_bytes() == first_terminal_bytes
    complete_checkpoint = json.loads(
        final.checkpoint_path.read_text(encoding="utf-8")
    )
    incomplete = deepcopy(complete_checkpoint)
    incomplete.pop("checkpoint_id")
    incomplete["checkpoint_status"] = "INCOMPLETE_CHECKPOINT"
    incomplete["checkpoint_id"] = domain_hash(
        RTA4_CHECKPOINT_DOMAIN, incomplete,
    )
    atomic_write_json(final.checkpoint_path, incomplete)
    with pytest.raises(Exception):
        aggregate_formal_run(
            Path(contract["prepared"]["CORE-1"]["operational"]["output_root"]),
            contract["root"] / "incomplete-checkpoint-aggregate",
            bootstrap_replicates=10, allow_test_authorization=True,
        )
    atomic_write_json(final.checkpoint_path, complete_checkpoint)

    removed_payload = json.loads(first_terminal.read_text(encoding="utf-8"))
    first_terminal.unlink()
    with pytest.raises(Exception):
        aggregate_formal_run(
            Path(contract["prepared"]["CORE-1"]["operational"]["output_root"]),
            contract["root"] / "missing-terminal-aggregate",
            bootstrap_replicates=10, allow_test_authorization=True,
        )
    atomic_write_json(first_terminal, removed_payload)
    assert first_terminal.read_bytes() == first_terminal_bytes
    extra = (
        Path(contract["prepared"]["CORE-1"]["operational"]["output_root"])
        / "formal_terminal_results" / f"{'f' * 64}.json"
    )
    atomic_write_json(extra, {"unexpected": True})
    with pytest.raises(RTA4ExecutionError, match="outside the plan"):
        runner.run(
            resume=True, synthetic_ordinals=(0, 1, 2),
            certificate_provider=_synthetic_certificate,
            rta_executor=counted,
        )


def test_production_provider_uses_public_generator_once_per_slot(
    frozen_contract,
):
    record = next(iter_formal_plan(frozen_contract["configs"]["CORE-1"]))
    provider = ProductionTasksetProvider(
        frozen_contract["prepared"]["CORE-1"],
    )
    first = provider(record)
    second = provider(record)
    assert first is second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.generation_request.generator_seed >= 0


def _pilot_observation_inputs(document):
    wrapper = {
        "observation_id", "pilot_manifest_id", "selection_key", "core",
        "method", "taskset_skeleton_slot_id", "taskset_slot_id",
    }
    return [
        {
            key: value for key, value in row.items()
            if key not in wrapper
        }
        for row in document["observations"]
    ]


def test_pilot_raw_observations_reconstruct_report_and_reject_drift(
    frozen_contract,
):
    pilot = frozen_contract["pilot"]
    observed = frozen_contract["observations"]
    report = frozen_contract["report"]
    assert validate_pilot_observations(observed, pilot) == observed
    assert build_pilot_observations(
        pilot, list(reversed(_pilot_observation_inputs(observed))),
    ) == observed
    assert build_pilot_report(pilot, observed) == report

    for mutation in (
        "missing", "extra", "duplicate", "selection", "timing", "timeout",
    ):
        raw = _pilot_observation_inputs(observed)
        if mutation == "missing":
            raw.pop()
        elif mutation == "extra":
            raw.append(deepcopy(raw[0]))
        elif mutation == "duplicate":
            raw[-1] = deepcopy(raw[0])
        elif mutation == "selection":
            raw[0]["mathematical_request_id"] = "f" * 64
        elif mutation == "timing":
            raw[0]["runtime_wall_milliseconds"] += 1
        else:
            raw[0]["timed_out"] = not raw[0]["timed_out"]
        if mutation in {"timing", "timeout"}:
            changed = build_pilot_observations(pilot, raw)
            changed_report = build_pilot_report(pilot, changed)
            with pytest.raises(RTA4PilotError, match="reconstructed"):
                validate_pilot_report(changed_report, pilot, observed)
        else:
            with pytest.raises(RTA4PilotError):
                build_pilot_observations(pilot, raw)

    for field in (
        "runtime_wall_milliseconds_p50",
        "runtime_wall_milliseconds_p95",
    ):
        drifted_report = deepcopy(report)
        drifted_report["engineering_metrics"][field] += 1
        with pytest.raises(RTA4PilotError, match="reconstructed"):
            validate_pilot_report(drifted_report, pilot, observed)


class _AggregateClosure:
    def __init__(self, core, requests=(), results=(), dependencies=None):
        self.metadata = {"core": core}
        if dependencies is None and core == "CORE-5B" and requests:
            reference = requests[0]
            dependencies = ({
                key: reference[key]
                for key in (
                    "analysis_id", "taskset_skeleton_id", "taskset_id",
                    "taskset_hash", "method", "exact_e0",
                    "service_identity", "power_vector_hash",
                    "theory_document_sha256",
                    "numeric_contract_sha256",
                )
            },)
            dependencies[0].update({
                "source_plan_sha256": "d" * 64,
                "source_closure_sha256": "e" * 64,
            })
        self._tables = {
            "formal_rta_requests.csv": tuple(requests),
            "formal_rta_taskset_results.csv": tuple(results),
            "formal_dependencies.csv": tuple(dependencies or ()),
        }

    def table(self, name):
        return self._tables.get(name, ())


def _core5b_rows(runtimes=("8", "4", "2", "1")):
    request_identity = {
        "analysis_id": "a" * 64, "request_id": "b" * 64,
        "taskset_skeleton_slot_id": "c" * 64,
        "taskset_slot_id": "d" * 64,
        "taskset_skeleton_id": "e" * 64, "taskset_id": "1" * 64,
        "taskset_hash": "2" * 64, "method": "CW_D",
        "method_role": "WORKER_CONSISTENCY", "carry_policy": "FIXED_D",
        "exact_e0": "1/20", "service_identity": "3" * 64,
        "power_vector_hash": "4" * 64,
        "theory_document_sha256": "5" * 64,
        "numeric_contract_sha256": "6" * 64,
        "exact_input_identity": "7" * 64,
        "timeout_contract": "ASAP_BLOCK_V9_3_RTA4_TIMEOUT_V1",
        "source_analysis_id": "NA",
        "scenario": "CORE5B_WORKER_CONSISTENCY",
        "axis": "worker_count", "service_scale": "1",
        "power_scale": "1",
        "deadline_variant": "fixed_slack_fraction_v1:3/4",
    }
    result_identity = {
        "method": "CW_D", "solver_status": "COMPLETED",
        "taskset_certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": "true", "first_failed_priority": "NA",
        "failure_reason": "NA", "timeout": "false",
        "checked_w_count": "10", "checked_q_count": "0",
        "checked_h_count": "0", "exact_result_hash": "8" * 64,
        "candidate_vector_hash": "9" * 64,
        "witness_vector_hash": "a" * 64,
        "certification_vector_hash": "b" * 64,
        "failure_reason_vector_hash": "c" * 64,
        "fallback_used": "false", "normalized_utilization": "1/2",
    }
    requests, results = [], []
    for worker, runtime in zip((1, 2, 4, 8), runtimes):
        execution_id = format(worker, "064x")
        requests.append({
            **request_identity, "execution_run_id": execution_id,
            "worker_count": str(worker),
        })
        results.append({
            **result_identity, "execution_run_id": execution_id,
            "runtime_wall_seconds": runtime,
        })
    return requests, results


def test_core5b_scalability_is_strictly_paired_and_order_independent():
    requests, results = _core5b_rows()
    rows = _core5b_scalability_rows(
        _AggregateClosure("CORE-5B", requests, results)
    )
    assert rows == _core5b_scalability_rows(
        _AggregateClosure(
            "CORE-5B", list(reversed(requests)), list(reversed(results)),
        )
    )
    assert [row["worker_count"] for row in rows] == [1, 2, 4, 8]
    assert [row["runtime_median"] for row in rows] == ["8", "4", "2", "1"]
    assert [row["speedup"] for row in rows] == ["1", "2", "4", "8"]
    assert [row["parallel_efficiency"] for row in rows] == [
        "1", "1", "1", "1",
    ]
    assert not any(
        row[field] == "NA"
        for row in rows
        for field in (
            "runtime_median", "runtime_p95", "speedup",
            "parallel_efficiency",
        )
    )


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("missing", 1), ("missing", 4), ("duplicate", 2),
        ("runtime", "0"), ("runtime", "-1"), ("runtime", "NaN"),
        ("runtime", "Inf"), ("math", "f" * 64),
    ),
)
def test_core5b_scalability_rejects_incomplete_or_invalid_pairs(
    mutation, value,
):
    requests, results = _core5b_rows()
    if mutation == "missing":
        index = (1, 2, 4, 8).index(value)
        requests.pop(index)
        results.pop(index)
    elif mutation == "duplicate":
        duplicate_request = deepcopy(requests[1])
        duplicate_result = deepcopy(results[1])
        duplicate_request["execution_run_id"] = "f" * 64
        duplicate_result["execution_run_id"] = "f" * 64
        requests.append(duplicate_request)
        results.append(duplicate_result)
    elif mutation == "runtime":
        results[2]["runtime_wall_seconds"] = value
    else:
        results[2]["exact_result_hash"] = value
    with pytest.raises(RTA4FormalAggregationError):
        _core5b_scalability_rows(
            _AggregateClosure("CORE-5B", requests, results)
        )


def test_core5a_figure5_requires_measured_positive_runtime():
    valid = _AggregateClosure("CORE-5A", results=({
        "axis": "task_count", "axis_value": "10", "method": "CW_D",
        "runtime_wall_seconds": "0.25",
    },))
    rows = _figure5(valid)
    assert rows[0]["runtime_median"] == "0.25"
    invalid = _AggregateClosure("CORE-5A", results=({
        "axis": "task_count", "axis_value": "10", "method": "CW_D",
        "runtime_wall_seconds": "0",
    },))
    with pytest.raises(RTA4FormalAggregationError, match="positive"):
        _figure5(invalid)


@pytest.mark.parametrize(
    "value", ("-1", "NaN", "Inf", True, 0.5, object()),
)
def test_parent_rejects_forged_timing_types(value):
    with pytest.raises(RTA4FormalPipelineError):
        _execution_seconds(value, "runtime_wall_seconds")
    with pytest.raises(RTA4FormalPipelineError):
        _execution_peak_rss(value)


def test_execution_measurements_do_not_change_mathematical_identity():
    mathematical = {
        "solver_status": "COMPLETED", "task_results": [{"candidate": "1"}],
        "runtime_wall_seconds": "1", "runtime_cpu_seconds": "0.5",
        "peak_rss_bytes": 100, "worker_count": 1,
        "attempt_count": 1, "attempts": [{
            "attempt_number": 1, "runtime_wall_seconds": "1",
        }],
    }
    changed = deepcopy(mathematical)
    changed.update({
        "runtime_wall_seconds": "99", "runtime_cpu_seconds": "88",
        "peak_rss_bytes": 9999, "worker_count": 8, "attempt_count": 2,
        "attempts": [{"attempt_number": 2, "runtime_wall_seconds": "99"}],
    })
    assert mathematical_result_hash(mathematical) == (
        mathematical_result_hash(changed)
    )


def test_production_executor_retains_measured_retry_and_error_time(
    frozen_contract, monkeypatch,
):
    record = next(iter_formal_plan(frozen_contract["configs"]["CORE-1"]))
    certificate = _synthetic_certificate(record)
    completed = _synthetic_rta(record, certificate)
    responses = [
        ({**completed, "solver_status": "TIMEOUT"}, object()),
        (completed, object()),
    ]
    monkeypatch.setattr(
        rta4_execution, "_adapter_result",
        lambda *_args: responses.pop(0),
    )
    wall = iter((1.0, 1.5, 2.0, 2.25))
    cpu = iter((3.0, 3.2, 4.0, 4.1))
    rss = iter((100, 200, 300, 400))
    monkeypatch.setattr(
        rta4_execution.time, "perf_counter", lambda: next(wall),
    )
    monkeypatch.setattr(
        rta4_execution.time, "process_time", lambda: next(cpu),
    )
    monkeypatch.setattr(rta4_execution, "_rss_bytes", lambda: next(rss))
    result = ProductionRTAExecutor(
        frozen_contract["prepared"]["CORE-1"]
    )(record, certificate)
    assert [row["solver_status"] for row in result["attempts"]] == [
        "TIMEOUT", "COMPLETED",
    ]
    assert all(
        float(row["runtime_wall_seconds"]) > 0
        and float(row["runtime_cpu_seconds"]) > 0
        for row in result["attempts"]
    )
    assert result["peak_rss_bytes"] == 400

    monkeypatch.setattr(
        rta4_execution, "_adapter_result",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("boundary")),
    )
    wall = iter((10.0, 10.75))
    cpu = iter((20.0, 20.25))
    rss = iter((500, 600))
    monkeypatch.setattr(
        rta4_execution.time, "perf_counter", lambda: next(wall),
    )
    monkeypatch.setattr(
        rta4_execution.time, "process_time", lambda: next(cpu),
    )
    monkeypatch.setattr(rta4_execution, "_rss_bytes", lambda: next(rss))
    failed = ProductionRTAExecutor(
        frozen_contract["prepared"]["CORE-1"]
    )(record, certificate)
    assert failed["solver_status"] == "INTERNAL_ERROR"
    assert failed["attempts"][0]["runtime_wall_seconds"] == "0.75"
    assert failed["attempts"][0]["runtime_cpu_seconds"] == "0.25"


def test_parent_persists_attempt_timing_rss_and_resume_preserves_terminal(
    frozen_contract,
):
    contract = _variant_contract(frozen_contract, "timing-contract")
    source_root = contract["root"] / "source"
    source_root.mkdir()
    _, source = _source_repo(source_root)
    authorization = _test_authorization(
        contract, "CORE-1", source, {},
    )
    calls = []

    def measured(record, certificate):
        calls.append(record.execution_id)
        result = _synthetic_rta(record, certificate)
        result["attempts"] = [
            {
                "solver_status": "TIMEOUT",
                "failure_origin": "UNIFIED_RTA_ADAPTER",
                "runtime_wall_seconds": "0.1",
                "runtime_cpu_seconds": "0.3",
                "peak_rss_bytes": 8192,
            },
            {
                "solver_status": "COMPLETED", "failure_origin": "NA",
                "runtime_wall_seconds": "0.2",
                "runtime_cpu_seconds": "0.4",
                "peak_rss_bytes": 12288,
            },
        ]
        result.update({
            "runtime_wall_seconds": "0.30000000000000004",
            "runtime_cpu_seconds": "0.69999999999999996",
            "peak_rss_bytes": 12288,
        })
        return result

    runner = AuthorizedRTA4Runner(
        contract["prepared"]["CORE-1"], authorization,
    )
    summary = runner.run(
        synthetic_ordinals=(0,),
        certificate_provider=_synthetic_certificate,
        rta_executor=measured,
    )
    output = summary.checkpoint_path.parent
    attempts = read_csv(output / "formal_rta_attempts.csv")
    results = read_csv(
        output / "formal_rta_taskset_results.csv"
    )
    assert [row["solver_status"] for row in attempts] == [
        "TIMEOUT", "COMPLETED",
    ]
    assert [row["runtime_wall_seconds"] for row in attempts] == [
        "0.10000000000000001", "0.20000000000000001",
    ]
    assert [row["runtime_cpu_seconds"] for row in attempts] == [
        "0.29999999999999999", "0.40000000000000002",
    ]
    assert [row["peak_rss_bytes"] for row in attempts] == [
        "8192", "12288",
    ]
    assert results[0]["runtime_wall_seconds"] == "0.30000000000000004"
    assert results[0]["runtime_cpu_seconds"] == "0.69999999999999996"
    assert results[0]["peak_rss_bytes"] == "12288"
    terminal = next(
        (output / "formal_terminal_results").glob("*.json")
    )
    before = terminal.read_bytes()
    terminal_payload = json.loads(before)
    assert terminal_payload["attempt_count"] == 2
    assert terminal_payload["runtime_wall_seconds"] == (
        "0.30000000000000004"
    )
    assert terminal_payload["runtime_cpu_seconds"] == (
        "0.69999999999999996"
    )
    assert terminal_payload["peak_rss_bytes"] == 12288
    resumed = runner.run(
        resume=True, synthetic_ordinals=(0,),
        certificate_provider=_synthetic_certificate,
        rta_executor=measured,
    )
    assert resumed.complete and calls == [calls[0]]
    assert terminal.read_bytes() == before


def _tree_hashes(root):
    if not root.exists():
        return None
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_resume_and_validate_only_preflight_are_zero_write(
    frozen_contract,
):
    contract = _variant_contract(frozen_contract, "readonly-resume-contract")
    source_root = contract["root"] / "source"
    source_root.mkdir()
    _, source = _source_repo(source_root)
    authorization = _test_authorization(
        contract, "CORE-1", source, {},
    )
    runner = AuthorizedRTA4Runner(
        contract["prepared"]["CORE-1"], authorization,
    )
    output = Path(
        contract["prepared"]["CORE-1"]["operational"]["output_root"]
    )
    with pytest.raises(RTA4ExecutionError, match="existing"):
        runner.run(
            resume=True, synthetic_ordinals=(0, 1),
            certificate_provider=_synthetic_certificate,
            rta_executor=_synthetic_rta,
        )
    assert not output.exists()
    output.mkdir()
    with pytest.raises(RTA4ExecutionError, match="empty"):
        runner.run(
            resume=True, synthetic_ordinals=(0, 1),
            certificate_provider=_synthetic_certificate,
            rta_executor=_synthetic_rta,
        )
    assert _tree_hashes(output) == {}
    output.rmdir()

    partial = runner.run(
        synthetic_ordinals=(0, 1), max_records=1,
        certificate_provider=_synthetic_certificate,
        rta_executor=_synthetic_rta,
    )
    before_validate = _tree_hashes(output)
    validated = runner.run(
        validate_only=True, synthetic_ordinals=(0, 1),
        certificate_provider=_synthetic_certificate,
        rta_executor=_synthetic_rta,
    )
    assert not validated.complete
    assert _tree_hashes(output) == before_validate

    metadata = output / "formal_run_metadata.json"
    metadata.unlink()
    damaged = _tree_hashes(output)
    with pytest.raises(RTA4ExecutionError, match="missing required"):
        runner.run(
            resume=True, synthetic_ordinals=(0, 1),
            certificate_provider=_synthetic_certificate,
            rta_executor=_synthetic_rta,
        )
    assert _tree_hashes(output) == damaged
    assert partial.processed_records == 1
