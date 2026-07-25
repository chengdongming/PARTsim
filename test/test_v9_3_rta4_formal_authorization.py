from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3.result_writer import atomic_write_json
from experiments.v9_3.rta4_formal_authorization import (
    RTA4AuthorizationError, authorize_candidate,
    build_authorization_candidate, validate_authorization_document,
    verify_live_authorization,
)
from experiments.v9_3.rta4_formal_aggregation import aggregate_formal_run
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, load_rta4_formal_config,
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
    AuthorizedRTA4Runner, ProductionTasksetProvider, RTA4ExecutionError,
    _bounded_execution_batches,
)
from experiments.v9_3.rta4_formal_plan import (
    FormalPlanRecord, iter_core4_plan, iter_core5b_plan, iter_formal_plan,
)
from experiments.v9_3.rta4_formal_plotting import (
    render_formal_publication_figures,
)
from experiments.v9_3.rta4_formal_pilot import (
    RTA4PilotError, build_pilot_manifest, build_pilot_report,
    validate_pilot_report,
)
from experiments.v9_3.constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE, FIXED_SLACK_FRACTION_VARIANT,
    GenerationRequest, SkeletonTask, build_taskset_identity_certificate,
)
from experiments.v9_3.release_applicability import (
    RELEASE_SNAPSHOT_STAGE, SIMULATOR_TRACE_CONTRACT_VERSION,
)
from experiments.v9_3.task_identity import runtime_task_name_for_source_id


ROOT = Path(__file__).resolve().parents[1]


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
    pilot = build_pilot_manifest(
        configs, core_record_counts={core: 1 for core in RTA4_CORES},
        selection_seed="RTA4-TEST-PILOT-V1", output_root=root / "pilot",
        taskset_store=root / "pilot-taskset-store",
        config_paths=paths,
    )
    observations = [
        {
            "plan_record_id": row["plan_record_id"],
            "runtime_wall_milliseconds": index + 1,
            "runtime_cpu_milliseconds": index + 1,
            "peak_rss_bytes": 1024 + index,
            "timed_out": False,
            "attempt_count": 1,
            "worker_throughput_milli_records_per_second": 1000,
            "checkpoint_overhead_milliseconds": 0,
            "resume_overhead_milliseconds": 0,
            "simulation_wall_milliseconds": 0,
            "trace_size_bytes": 0,
            "output_io_bytes": 0,
            "engineering_error": False,
            "ci_width_engineering_warning": False,
        }
        for index, core in enumerate(RTA4_CORES)
        for row in pilot["selected_records"][core]
    ]
    report = build_pilot_report(pilot, observations)
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
        configs, pilot_manifest=pilot, pilot_report=report,
        timeout_contract=timeout, operational=operational,
        config_paths=paths,
    )
    freeze = build_freeze_manifest(prepared)
    documents = {
        "pilot": root / "pilot.json",
        "report": root / "report.json",
        "freeze": root / "freeze.json",
    }
    atomic_write_json(documents["pilot"], pilot)
    atomic_write_json(documents["report"], report)
    atomic_write_json(documents["freeze"], freeze)
    for core in RTA4_CORES:
        path = root / f"prepared-{core}.json"
        atomic_write_json(path, prepared[core])
        documents[f"prepared-{core}"] = path
    return {
        "root": root, "configs": configs, "pilot": pilot, "report": report,
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
        len(pilot["selected_records"][core]) == 1 for core in RTA4_CORES
    )
    validate_pilot_report(frozen_contract["report"], pilot)
    contaminated = deepcopy(frozen_contract["report"])
    contaminated["scientific_results_included"] = True
    with pytest.raises(RTA4PilotError):
        validate_pilot_report(contaminated, pilot)


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
        validate_pilot_report(stale, frozen_contract["pilot"])


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
    return {
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
        pilot_report=frozen_contract["report"],
        timeout_contract=frozen_contract["timeout"],
        operational=operational, config_paths=paths,
    )
    freeze = build_freeze_manifest(prepared)
    documents = {
        "pilot": root / "pilot.json", "report": root / "report.json",
        "freeze": root / "freeze.json",
    }
    atomic_write_json(documents["pilot"], frozen_contract["pilot"])
    atomic_write_json(documents["report"], frozen_contract["report"])
    atomic_write_json(documents["freeze"], freeze)
    for core in RTA4_CORES:
        documents[f"prepared-{core}"] = root / f"prepared-{core}.json"
        atomic_write_json(documents[f"prepared-{core}"], prepared[core])
    return {
        **frozen_contract, "root": root, "prepared": prepared,
        "freeze": freeze, "documents": documents,
    }


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
