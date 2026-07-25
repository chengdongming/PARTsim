from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
from itertools import islice
import json
from pathlib import Path
import shutil
import tracemalloc

import pytest

from experiments.v9_3.aggregation import validate_run_closure_read_only
from experiments.v9_3.config import KNOWN_VARIANTS, config_hash, load_config
from experiments.v9_3.constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE, FIXED_SLACK_FRACTION_VARIANT,
    GenerationRequest, SkeletonTask,
    build_taskset_identity_certificate,
)
from experiments.v9_3 import exact_energy
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.release_applicability import (
    ASYNC_HASH_PHASE_V1, RELEASE_SNAPSHOT_STAGE,
    SIMULATOR_TRACE_CONTRACT_VERSION,
)
from experiments.v9_3.result_writer import (
    ResultWriter, ResultWriterError, TABLES, read_csv, write_csv,
)
from experiments.v9_3.rta4_formal_aggregation import (
    aggregate_formal_run, validate_aggregate_bundle,
)
from experiments.v9_3 import rta4_formal_aggregation as formal_aggregation
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORE2_METHODS, RTA4_FORMAL_PARAMETER_STATUS,
    RTA4_FORMAL_PROFILE, RTA4_RECURSIVE_METHODS,
    load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_pipeline import (
    RTA4FixtureInterruption, RTA4FormalAuthorizationError,
    RTA4FormalRunner, SimulationDeduplicator,
    build_formal_release_projection, formal_analysis_identity,
    formal_execution_identity, recompute_rta_result_hashes,
)
from experiments.v9_3.rta4_formal_plan import (
    FormalPlanRecord, RTA4FormalPlanError, describe_all_formal_plans,
    describe_formal_plan,
    exact_service_scale_identity,
    iter_core3_comparison_plan, iter_core4_plan,
    iter_core5a_plan, iter_core5b_math_references, iter_core5b_plan,
    iter_formal_plan, ordered_stream_digest,
)
from experiments.v9_3.rta4_formal_plotting import (
    render_formal_publication_figures, validate_plot_data,
)
from experiments.v9_3.rta4_formal_schema import (
    FORMAL_TABLES, formal_schema_hash, formal_schema_manifest,
    legacy_table_overlap,
)
from experiments.v9_3.rta4_formal_rows import TABLE_ROW_CONTRACTS
from experiments.v9_3.rta4_formal_store import (
    RTA4FormalTasksetStore, build_ofat_taskset_variant,
    scale_taskset_time_exact,
)
from experiments.v9_3.rta4_formal_validation import (
    P0, recompute_monotonicity_rows, validate_dominance,
    validate_formal_run_closure,
    validate_monotonicity, validate_soundness, validate_worker_consistency,
)
from experiments.v9_3.rta4_formal_writer import (
    RTA4FormalResultWriter, RTA4FormalWriterError,
)
from experiments.v9_3.task_identity import runtime_task_name_for_source_id


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATHS = {
    core: ROOT / "configs" / f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
    for core in ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B")
}


def _configs():
    return {core: load_rta4_formal_config(path, expected_core=core) for core, path in CONFIG_PATHS.items()}


def _request() -> GenerationRequest:
    return GenerationRequest(
        formal_master_seed=930700,
        formal_generation_id="3" * 64,
        processors=4,
        task_count=3,
        target_normalized_utilization=Fraction(1, 2),
        replicate_index=7,
        period_min=40,
        period_max=200,
        utilization_allocation_mode="uunifast_discard_v1",
        min_task_utilization=Fraction(1, 100),
        max_task_utilization=Fraction(4, 5),
        utilization_tolerance=Fraction(1, 100),
        wcet_rounding_mode="compensated",
        generator_version="global_task_generator_frozen_v1",
        power_generation_mode="generator_default_heterogeneous",
        power_generation_contract_identity="1" * 64,
        workload_candidate_identity="2" * 64,
        priority_policy="RM",
        dag_generation_mode="disabled",
        energy_aware_generation=False,
    )


def _certificate():
    skeleton = (
        SkeletonTask("tau-a", 0, 3, 11, Fraction(1, 3)),
        SkeletonTask("tau-b", 1, 5, 13, Fraction(2, 5)),
        SkeletonTask("tau-c", 2, 7, 17, Fraction(3, 7)),
    )
    return build_taskset_identity_certificate(
        _request(), skeleton, deadline_mode=CONSTRAINED_UNIFORM_SLACK_MODE,
    )


def _plan_certificate(record: FormalPlanRecord):
    material = record.material
    task_count = int(material.get("task_count", 10))
    request = GenerationRequest(
        formal_master_seed=930612,
        formal_generation_id=hashlib.sha256(
            f"RTA4-PLAN-CERTIFICATE:{record.taskset_skeleton_slot_id}".encode()
        ).hexdigest(),
        processors=int(material.get("processor_count", 4)),
        task_count=task_count,
        target_normalized_utilization=Fraction(
            str(material.get("normalized_utilization", "1/2"))
        ),
        replicate_index=int(material.get("replicate_index", 0)),
        period_min=40,
        period_max=200,
        utilization_allocation_mode="frozen_v9_3_generator_v1",
        min_task_utilization=Fraction(1, 100),
        max_task_utilization=Fraction(4, 5),
        utilization_tolerance=Fraction(1, 100),
        wcet_rounding_mode="compensated",
        generator_version="ASAP_BLOCK_V9_3_GENERATOR_V1",
        power_generation_mode="generator_default_heterogeneous",
        power_generation_contract_identity="1" * 64,
        workload_candidate_identity="2" * 64,
        priority_policy="RM",
        dag_generation_mode="disabled",
        energy_aware_generation=False,
    )
    skeleton = tuple(
        SkeletonTask(f"tau-{index:02d}", index, 1, 170 + index, Fraction(index + 1, 10))
        for index in range(task_count)
    )
    deadline = str(material.get("deadline_variant", CONSTRAINED_UNIFORM_SLACK_MODE))
    kwargs = {}
    mode = deadline
    if deadline.startswith("fixed_slack_fraction_v1:"):
        mode = FIXED_SLACK_FRACTION_VARIANT
        kwargs["fixed_slack_fraction"] = Fraction(deadline.split(":", 1)[1])
    return build_taskset_identity_certificate(
        request, skeleton, deadline_mode=mode,
        power_scale=Fraction(str(material.get("power_scale", "1"))),
        **kwargs,
    )


def _fake_rta(_record, certificate):
    return {
        "solver_status": "COMPLETED",
        "taskset_certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": True,
        "task_results": [
            {
                "task_solver_status": "CANDIDATE_FOUND",
                "task_certification_status": "CERTIFIED",
                "candidate_response_time": task.wcet,
                "witness": [task.wcet],
                "checked_w_count": 1,
            }
            for task in certificate.tasks
        ],
    }


def _fake_simulator_factory(trace_root: Path, calls: list[str]):
    def execute(_record, certificate, projection, window, payload, simulation_id):
        calls.append(simulation_id)
        releases = {}
        for task in payload:
            for release in range(task["arrival_offset"], window.release_horizon, task["T"]):
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
                    "arrival_time": str(release), "available_energy_mJ": 1000,
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
            "simulator_trace_contract_version": SIMULATOR_TRACE_CONTRACT_VERSION,
            "release_horizon_ms": window.release_horizon,
            "observation_horizon_ms": window.observation_horizon,
            "release_cutoff_enabled": True,
            "observation_horizon_reached": True,
            "simulation_completion_reason": "reached_horizon",
        }
        path = trace_root / f"{simulation_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        job_results = [
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
            "job_results": job_results,
        }
    return execute


def test_opt_in_configs_freeze_profile_status_and_unified_method_orders():
    configs = _configs()
    for core, config in configs.items():
        assert config["experiment_contract"]["profile"] == RTA4_FORMAL_PROFILE
        assert config["experiment_contract"]["parameter_status"] == RTA4_FORMAL_PARAMETER_STATUS
        assert config["execution"]["mode"] == "FORMAL"
        assert config["execution"]["timeout_contract"] == "UNFROZEN_PRE_PILOT"
    assert tuple(configs["CORE-1"]["plan"]["methods"]) == RTA4_RECURSIVE_METHODS
    assert tuple(configs["CORE-2"]["plan"]["methods"]) == RTA4_CORE2_METHODS
    assert tuple(configs["CORE-4"]["plan"]["methods"]) == RTA4_RECURSIVE_METHODS
    assert tuple(configs["CORE-5A"]["plan"]["methods"]) == RTA4_RECURSIVE_METHODS


def test_all_five_experiment_plans_have_exact_counts_and_reuse_contracts():
    summary = describe_all_formal_plans(_configs())
    plans = summary["plans"]
    assert plans["CORE-1"]["counts"] == {
        "unique_tasksets": 1600, "unique_skeletons": 1600, "rta_requests": 19200,
    }
    assert plans["CORE-2"]["counts"] == {
        "reused_tasksets": 1600, "reused_skeletons": 1600,
        "rta_requests": 28800,
        "source_analysis_references": 9600,
    }
    assert plans["CORE-3"]["counts"] == {
        "new_rta_requests": 0, "reused_tasksets": 1600,
        "reused_skeletons": 1600, "simulations": 6400,
        "applicability_comparisons": 76800,
    }
    assert plans["CORE-4"]["counts"] == {
        "unique_skeletons": 1000, "conditions_per_skeleton": 18,
        "rta_requests": 72000,
    }
    assert plans["CORE-5A"]["counts"] == {
        "unique_scenario_tasksets": 1100, "unique_scenario_skeletons": 1100,
        "rta_requests": 4400,
    }
    assert plans["CORE-5B"]["counts"]["unique_mathematical_requests"] == 3000
    assert plans["CORE-5B"]["counts"]["executions"] == 12000
    assert 750 <= plans["CORE-5B"]["counts"]["selected_tasksets"] <= 1000
    assert summary["total_unique_rta_requests"] == 124400
    assert summary["total_simulations"] == 6400
    assert all(len(plan["ordered_stream_digest"]) == 64 for plan in plans.values())


def test_core3_comparison_projection_is_complete_without_new_rta_requests():
    rows = iter_core3_comparison_plan()
    first = next(rows)
    assert first["method"] == "CW_THETA_CW"
    assert first["exact_e0"] == "0"
    assert sum(1 for _ in rows) + 1 == 6400 * 4 * 3


def test_core4_is_exact_ofat_and_never_cartesian_or_noop_scaled():
    records = list(iter_core4_plan())
    first_skeleton = records[0].taskset_skeleton_slot_id
    first = [record for record in records if record.taskset_skeleton_slot_id == first_skeleton]
    assert len(first) == 18 * 4
    conditions = {
        (
            row.material["axis"], row.material["axis_value"], row.material["exact_e0"],
            row.material["service_scale"], row.material["power_scale"],
            row.material["deadline_variant"],
        ) for row in first
    }
    assert len(conditions) == 18
    assert not any(
        row.material["axis"] == "power_scale" and row.material["axis_value"] == "1"
        for row in first
    )
    assert all(not isinstance(value, float) for row in first for value in row.material.values())


def test_core5a_scenarios_are_not_silently_deduplicated():
    records = list(iter_core5a_plan())
    assert len(records) == 4400
    counts = {}
    for row in records:
        counts[row.material["scenario"]] = counts.get(row.material["scenario"], 0) + 1
    assert counts == {"TASK_COUNT": 1600, "PROCESSORS": 1200, "INTEGER_TIME_SCALE": 1600}


def test_core5b_selection_is_result_independent_and_worker_excluded_from_math_identity():
    first = list(iter_core5b_math_references())
    second = list(iter_core5b_math_references())
    assert len(first) == 3000
    assert [row.record_id for row in first] == [row.record_id for row in second]
    assert len({row.mathematical_request_id for row in first}) == 3000
    executions = list(iter_core5b_plan())
    assert len(executions) == 12000
    by_math = {}
    for execution in executions:
        by_math.setdefault(execution.mathematical_request_id, set()).add(
            execution.material["worker_count"]
        )
    assert all(workers == {1, 2, 4, 8} for workers in by_math.values())
    assert all("worker_count" not in reference.material for reference in first)


def test_schema_has_exact_18_table_independent_manifest_and_hash():
    assert len(FORMAL_TABLES) == 18
    assert not legacy_table_overlap()
    assert len(formal_schema_hash()) == 64
    assert formal_schema_manifest()["schema_sha256"] == formal_schema_hash()
    for columns in FORMAL_TABLES.values():
        assert columns[:4] == (
            "schema_version", "schema_sha256", "plan_sha256", "config_semantic_hash",
        )
        assert len(columns) == len(set(columns))
    assert set(TABLE_ROW_CONTRACTS) == set(FORMAL_TABLES)
    for name, columns in FORMAL_TABLES.items():
        contract = TABLE_ROW_CONTRACTS[name]
        assert contract.required_fields | contract.nullable_fields == set(columns)
        assert not contract.required_fields & contract.nullable_fields
        assert contract.sha256_fields <= set(columns)
        assert contract.domain_identity_fields <= contract.sha256_fields


def test_taskset_store_and_writer_persist_pr_b_certificate_with_exact_identity(tmp_path):
    config = _configs()["CORE-1"]
    record = next(iter_formal_plan(config))
    certificate = _plan_certificate(record)
    store = RTA4FormalTasksetStore(tmp_path / "store")
    writer = RTA4FormalResultWriter(
        tmp_path / "run", config=config, fixture_ordinals=(0,),
    )
    writer.persist_taskset(store, certificate)
    assert store.load(certificate.taskset_id) == certificate
    tasksets = list((tmp_path / "run" / "formal_tasksets.csv").read_text().splitlines())
    assert len(tasksets) == 2
    assert certificate.generation_request_id in tasksets[1]
    assert certificate.taskset_skeleton_id in tasksets[1]
    assert certificate.taskset_hash in tasksets[1]
    assert certificate.taskset_id in tasksets[1]


def test_ofat_service_and_integer_time_variants_use_exact_upstream_contracts():
    base_skeleton = (
        SkeletonTask("tau-a", 0, 3, 11, Fraction(1, 3)),
        SkeletonTask("tau-b", 1, 5, 13, Fraction(2, 5)),
        SkeletonTask("tau-c", 2, 7, 15, Fraction(3, 7)),
    )
    base = build_taskset_identity_certificate(
        _request(), base_skeleton,
        deadline_mode=FIXED_SLACK_FRACTION_VARIANT,
        fixed_slack_fraction=Fraction(3, 4),
    )
    power_variant = build_ofat_taskset_variant(
        base, deadline_slack_fraction=Fraction(3, 4),
        power_scale=Fraction(1, 2),
    )
    assert power_variant.taskset_skeleton_id == base.taskset_skeleton_id
    assert power_variant.taskset_id != base.taskset_id
    assert all(after.actual_power * 2 == before.actual_power for before, after in zip(base.tasks, power_variant.tasks))
    scaled_request = replace(
        _request(), formal_generation_id="4" * 64,
        period_min=80, period_max=400,
    )
    scaled = scale_taskset_time_exact(base, scale=2, scaled_request=scaled_request)
    assert all(
        (after.wcet, after.relative_deadline, after.period, after.actual_power)
        == (before.wcet * 2, before.relative_deadline * 2, before.period * 2, before.actual_power)
        for before, after in zip(base.tasks, scaled.tasks)
    )
    service = exact_service_scale_identity("a" * 64, Fraction(5, 4))
    assert service == exact_service_scale_identity("a" * 64, Fraction(5, 4))
    with pytest.raises(RTA4FormalPlanError, match="exact Fraction"):
        exact_service_scale_identity("a" * 64, 1.25)


def test_math_identity_excludes_worker_but_execution_identity_includes_it():
    certificate = _certificate()
    analysis_id, exact_input, material = formal_analysis_identity(
        certificate=certificate, method="SEQ_THETA_SEQ", exact_e0="1/20",
        service_identity="a" * 64,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        theory_document_sha256=exact_energy.THEORY_DOCUMENT_SHA256,
        timeout_contract="UNFROZEN_PRE_PILOT",
    )
    assert len(analysis_id) == len(exact_input) == 64
    assert "worker_count" not in material
    assert formal_execution_identity(
        analysis_id, worker_count=1, batch_identity="b" * 64,
    ) != formal_execution_identity(
        analysis_id, worker_count=8, batch_identity="b" * 64,
    )


def test_old_and_new_writer_namespaces_refuse_each_other(tmp_path):
    config = _configs()["CORE-1"]
    old_root = tmp_path / "old"
    ResultWriter(old_root)
    with pytest.raises(RTA4FormalWriterError, match="legacy"):
        RTA4FormalResultWriter(
            old_root, config=config, fixture_ordinals=(0,),
        )
    new_root = tmp_path / "new"
    RTA4FormalResultWriter(
        new_root, config=config, fixture_ordinals=(0,),
    )
    with pytest.raises(ResultWriterError, match="RTA4 formal"):
        ResultWriter(new_root)


def test_schema_plan_resume_and_terminal_conflicts_fail_closed(tmp_path):
    config = _configs()["CORE-1"]
    root = tmp_path / "run"
    writer = RTA4FormalResultWriter(
        root, config=config, fixture_ordinals=(0,),
    )
    with pytest.raises(RTA4FormalWriterError, match="plan manifest"):
        RTA4FormalResultWriter(
            root, config=config, fixture_ordinals=(1,),
        )
    with pytest.raises(RTA4FormalWriterError, match="config checkpoint"):
        RTA4FormalResultWriter(
            root, config=_configs()["CORE-2"], fixture_ordinals=(0,),
        )
    payload = {
        "plan_record_id": "a" * 64, "analysis_id": "b" * 64,
        "request_id": "c" * 64, "solver_status": "COMPLETED",
        "exact_result_hash": "e" * 64,
    }
    writer.write_terminal("d" * 64, payload)
    writer.write_terminal("d" * 64, payload)
    with pytest.raises(RTA4FormalWriterError, match="terminal result conflict"):
        writer.write_terminal("d" * 64, {**payload, "solver_status": "TIMEOUT"})
    marker_path = root / "formal_schema_manifest.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["schema_sha256"] = "0" * 64
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(RTA4FormalWriterError, match="schema mismatch"):
        RTA4FormalResultWriter(root, config=config, fixture_ordinals=(0,))


def test_pre_pilot_runner_executes_bounded_fixture_and_rejects_formal(tmp_path):
    runner = RTA4FormalRunner(_configs()["CORE-1"])
    assert runner.describe()["counts"]["rta_requests"] == 19200
    with pytest.raises(RTA4FormalAuthorizationError, match="PR-E"):
        runner.run()
    records = tuple(islice(iter_formal_plan(_configs()["CORE-1"]), 2))
    closure = runner.run_nonformal_fixture(
        records, root=tmp_path / "run", taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    assert len(closure.table("formal_rta_requests.csv")) == 2
    with pytest.raises(RTA4FormalAuthorizationError, match="100-request"):
        runner.run_nonformal_fixture(
            tuple(islice(iter_formal_plan(_configs()["CORE-1"]), 101)),
            root=tmp_path / "too-many", taskset_store=tmp_path / "too-many-store",
            certificate_provider=_plan_certificate, rta_executor=_fake_rta,
        )


def _run_core1_fixture(tmp_path: Path, count: int = 2):
    config = _configs()["CORE-1"]
    records = tuple(islice(iter_formal_plan(config), count))
    root = tmp_path / "run"
    closure = RTA4FormalRunner(config).run_nonformal_fixture(
        records, root=root, taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    return config, records, root, closure


def _rewrite_row(root: Path, table: str, row_index: int, **changes):
    rows = read_csv(root / table)
    rows[row_index].update({key: str(value) for key, value in changes.items()})
    write_csv(root / table, FORMAL_TABLES[table], rows)


def test_trusted_plan_rejects_partial_extra_and_metadata_formal(tmp_path):
    _config, _records, root, _closure = _run_core1_fixture(tmp_path)
    partial = tmp_path / "partial"
    shutil.copytree(root, partial)
    rows = read_csv(partial / "formal_rta_requests.csv")[:-1]
    write_csv(partial / "formal_rta_requests.csv", FORMAL_TABLES["formal_rta_requests.csv"], rows)
    with pytest.raises(Exception, match="membership"):
        validate_formal_run_closure(partial)

    extra = tmp_path / "extra"
    shutil.copytree(root, extra)
    rows = read_csv(extra / "formal_rta_requests.csv")
    rows.append({**rows[0], "plan_record_id": "a" * 64, "execution_run_id": "b" * 64})
    write_csv(extra / "formal_rta_requests.csv", FORMAL_TABLES["formal_rta_requests.csv"], rows)
    with pytest.raises(Exception, match="membership"):
        validate_formal_run_closure(extra)

    claimed = tmp_path / "claimed-formal"
    shutil.copytree(root, claimed)
    metadata_path = claimed / "formal_run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["execution_class"] = "FORMAL"
    metadata["formal_authorized"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(Exception, match="metadata"):
        validate_formal_run_closure(claimed)

    wrong_plan = tmp_path / "wrong-plan"
    shutil.copytree(root, wrong_plan)
    manifest_path = wrong_plan / "formal_plan_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["selection_manifest_digest"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Exception, match="trusted plan"):
        validate_formal_run_closure(wrong_plan)


def test_certificate_rows_and_request_result_cross_binding_are_exact(tmp_path):
    _config, _records, root, _closure = _run_core1_fixture(tmp_path, 1)
    mutated = tmp_path / "mutated-task"
    shutil.copytree(root, mutated)
    task = read_csv(mutated / "formal_tasks.csv")[0]
    _rewrite_row(mutated, "formal_tasks.csv", 0, C=int(task["C"]) + 1)
    with pytest.raises(Exception, match="certificate"):
        validate_formal_run_closure(mutated)

    reordered = tmp_path / "reordered-task"
    shutil.copytree(root, reordered)
    tasks = read_csv(reordered / "formal_tasks.csv")
    write_csv(reordered / "formal_tasks.csv", FORMAL_TABLES["formal_tasks.csv"], list(reversed(tasks)))
    with pytest.raises(Exception, match="canonical"):
        validate_formal_run_closure(reordered)

    rebound = tmp_path / "rebound-result"
    shutil.copytree(root, rebound)
    _rewrite_row(rebound, "formal_rta_taskset_results.csv", 0, method="LOC_THETA_LOC")
    with pytest.raises(Exception, match="request/taskset-result"):
        validate_formal_run_closure(rebound)


def test_bounded_runner_resume_is_idempotent_and_certificate_drift_fails(tmp_path):
    config = _configs()["CORE-1"]
    records = tuple(islice(iter_formal_plan(config), 2))
    calls = []

    def counted(record, certificate):
        calls.append(record.execution_id)
        return _fake_rta(record, certificate)

    runner = RTA4FormalRunner(config)
    with pytest.raises(RTA4FixtureInterruption):
        runner.run_nonformal_fixture(
            records, root=tmp_path / "resume", taskset_store=tmp_path / "store",
            certificate_provider=_plan_certificate, rta_executor=counted,
            interrupt_after=1,
        )
    closure = runner.run_nonformal_fixture(
        records, root=tmp_path / "resume", taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=counted,
    )
    resumed_hash = closure.closure_sha256
    assert len(calls) == 2
    assert runner.run_nonformal_fixture(
        records, root=tmp_path / "resume", taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=counted,
    ).closure_sha256 == resumed_hash
    assert len(calls) == 2

    def drifted(record):
        certificate = _plan_certificate(record)
        changed = replace(
            certificate.generation_request,
            formal_generation_id="9" * 64,
        )
        return build_taskset_identity_certificate(
            changed, certificate.skeleton_tasks,
            deadline_mode=certificate.deadline_variant.mode,
        )

    with pytest.raises(Exception, match="resume certificate"):
        runner.run_nonformal_fixture(
            records, root=tmp_path / "resume", taskset_store=tmp_path / "store",
            certificate_provider=drifted, rta_executor=counted,
        )


def test_writer_rejects_missing_float_and_negative_semantic_rows(tmp_path):
    config, _records, root, _closure = _run_core1_fixture(tmp_path, 1)
    writer = RTA4FormalResultWriter(root, config=config, fixture_ordinals=(0,))
    attempt = read_csv(root / "formal_rta_attempts.csv")[0]
    attempt_body = {
        key: value for key, value in attempt.items()
        if key not in FORMAL_TABLES["formal_rta_attempts.csv"][:4]
    }
    with pytest.raises(RTA4FormalWriterError, match="duplicate attempt"):
        writer.append_attempt(attempt_body)
    full = read_csv(root / "formal_rta_task_results.csv")[0]
    body = {key: value for key, value in full.items() if key not in FORMAL_TABLES["formal_rta_task_results.csv"][:4]}
    missing = dict(body)
    missing.pop("task_id")
    with pytest.raises(RTA4FormalWriterError, match="missing required"):
        writer.append("formal_rta_task_results.csv", missing)
    floating = dict(body)
    floating["candidate_response_time"] = 0.1
    with pytest.raises(RTA4FormalWriterError, match="integer"):
        writer.append("formal_rta_task_results.csv", floating)
    negative = dict(body)
    negative["candidate_response_time"] = "-1"
    with pytest.raises(RTA4FormalWriterError, match="non-negative"):
        writer.append("formal_rta_task_results.csv", negative)


def test_per_table_row_contracts_and_taskset_result_state_machine_fail_closed(tmp_path):
    config, _records, root, _closure = _run_core1_fixture(tmp_path, 1)
    request = read_csv(root / "formal_rta_requests.csv")[0]
    request_body = {
        key: value for key, value in request.items()
        if key not in FORMAL_TABLES["formal_rta_requests.csv"][:4]
    }
    assert "analysis_id" in TABLE_ROW_CONTRACTS["formal_rta_requests.csv"].required_fields
    assert "analysis_id" not in TABLE_ROW_CONTRACTS["formal_rta_requests.csv"].nullable_fields
    assert "analysis_id" in TABLE_ROW_CONTRACTS["formal_failures.csv"].nullable_fields
    assert "cell_id" in TABLE_ROW_CONTRACTS["formal_cells.csv"].sha256_fields

    empty_analysis = RTA4FormalResultWriter(
        tmp_path / "empty-analysis", config=config, fixture_ordinals=(0,),
    )
    with pytest.raises(RTA4FormalWriterError, match="analysis_id is required"):
        empty_analysis.append(
            "formal_rta_requests.csv", {**request_body, "analysis_id": ""},
        )

    cell = read_csv(root / "formal_cells.csv")[0]
    cell_body = {
        key: value for key, value in cell.items()
        if key not in FORMAL_TABLES["formal_cells.csv"][:4]
    }
    invalid_cell = RTA4FormalResultWriter(
        tmp_path / "invalid-cell", config=config, fixture_ordinals=(0,),
    )
    with pytest.raises(RTA4FormalWriterError, match="cell_id.*SHA-256"):
        invalid_cell.append("formal_cells.csv", {**cell_body, "cell_id": "x"})

    full_result = read_csv(root / "formal_rta_taskset_results.csv")[0]
    result_body = {
        key: value for key, value in full_result.items()
        if key not in FORMAL_TABLES["formal_rta_taskset_results.csv"][:4]
    }
    contradictions = (
        {
            "solver_status": "TIMEOUT", "timeout": "false",
            "taskset_proven": "false", "taskset_certification_status": "TIMEOUT",
            "first_failed_priority": "0", "failure_reason": "timeout",
        },
        {
            "solver_status": "TIMEOUT", "timeout": "true",
            "taskset_proven": "true", "taskset_certification_status": "TIMEOUT",
            "first_failed_priority": "0", "failure_reason": "timeout",
        },
        {"solver_status": "COMPLETED", "timeout": "true"},
        {
            "solver_status": "NO_CANDIDATE", "timeout": "false",
            "taskset_proven": "true", "taskset_certification_status": "NOT_CERTIFIED",
            "first_failed_priority": "0", "failure_reason": "none",
        },
        {
            "solver_status": "INTERNAL_ERROR", "timeout": "false",
            "taskset_proven": "true", "taskset_certification_status": "ERROR",
            "first_failed_priority": "0", "failure_reason": "internal",
        },
    )
    for index, changes in enumerate(contradictions):
        writer = RTA4FormalResultWriter(
            tmp_path / f"bad-state-{index}", config=config,
            fixture_ordinals=(0,),
        )
        with pytest.raises(RTA4FormalWriterError):
            writer.append(
                "formal_rta_taskset_results.csv", {**result_body, **changes},
            )

    # Preserve a row-valid TIMEOUT summary, recompute every task/result hash and
    # terminal/attempt field, but leave later tasks certified.  Closure must
    # reject the contradictory raw candidate vector rather than trust hashes.
    timeout_root = tmp_path / "timeout-certified-vector"
    shutil.copytree(root, timeout_root)
    results = read_csv(timeout_root / "formal_rta_taskset_results.csv")
    tasks = read_csv(timeout_root / "formal_rta_task_results.csv")
    failed_rank = tasks[0]["priority_rank"]
    tasks[0].update({
        "task_solver_status": "TIMEOUT",
        "task_certification_status": "TIMEOUT",
        "candidate_response_time": "NA", "failure_reason": "timeout",
    })
    results[0].update({
        "solver_status": "TIMEOUT", "timeout": "true",
        "taskset_proven": "false", "taskset_certification_status": "TIMEOUT",
        "first_failed_priority": failed_rank, "failure_reason": "timeout",
    })
    hashes = recompute_rta_result_hashes(results[0], tasks)
    for task, task_hash in zip(tasks, hashes["task_hashes"]):
        task["exact_task_result_hash"] = task_hash
    for field in (
        "exact_result_hash", "candidate_vector_hash", "witness_vector_hash",
        "certification_vector_hash", "failure_reason_vector_hash",
    ):
        results[0][field] = hashes[field]
    write_csv(
        timeout_root / "formal_rta_task_results.csv",
        FORMAL_TABLES["formal_rta_task_results.csv"], tasks,
    )
    write_csv(
        timeout_root / "formal_rta_taskset_results.csv",
        FORMAL_TABLES["formal_rta_taskset_results.csv"], results,
    )
    attempts = read_csv(timeout_root / "formal_rta_attempts.csv")
    attempts[0]["solver_status"] = "TIMEOUT"
    write_csv(
        timeout_root / "formal_rta_attempts.csv",
        FORMAL_TABLES["formal_rta_attempts.csv"], attempts,
    )
    terminal_path = next((timeout_root / "formal_terminal_results").glob("*.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["solver_status"] = "TIMEOUT"
    for field in (
        "exact_result_hash", "candidate_vector_hash", "witness_vector_hash",
        "certification_vector_hash", "failure_reason_vector_hash",
    ):
        terminal[field] = hashes[field]
    terminal_path.write_text(
        json.dumps(terminal, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="timeout taskset carries"):
        validate_formal_run_closure(timeout_root)


def test_core4_monotonicity_missing_candidate_evidence_is_p0():
    base_results = [
        {
            "analysis_id": "a", "taskset_skeleton_id": "s",
            "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "0",
            "scenario": "MAIN", "taskset_proven": "true",
        },
        {
            "analysis_id": "b", "taskset_skeleton_id": "s",
            "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "1",
            "scenario": "MAIN", "taskset_proven": "true",
        },
    ]

    def check(weak, strong):
        return recompute_monotonicity_rows(base_results, (
            {"analysis_id": "a", "task_id": "x", "candidate_response_time": weak},
            {"analysis_id": "b", "task_id": "x", "candidate_response_time": strong},
        ))[0]

    for weak, strong in (("NA", "NA"), ("NA", "1"), ("1", "NA")):
        row = check(weak, strong)
        assert row["candidate_status"] == "NOT_COMPARABLE"
        assert row["check_status"] == "P0_VIOLATION"
        assert row["failure_severity"] == "P0"
    assert check("1", "2")["check_status"] == "P0_VIOLATION"
    assert check("2", "1")["check_status"] == "PASS"

    certification = recompute_monotonicity_rows(
        (base_results[0], {**base_results[1], "taskset_proven": "false"}),
        (
            {"analysis_id": "a", "task_id": "x", "candidate_response_time": "1"},
            {"analysis_id": "b", "task_id": "x", "candidate_response_time": "1"},
        ),
    )[0]
    assert certification["certification_status"] == "P0_VIOLATION"
    assert certification["check_status"] == "P0_VIOLATION"
    deadline_results = tuple(
        {**row, "axis": "deadline_slack_fraction"} for row in base_results
    )
    assert recompute_monotonicity_rows(deadline_results, ()) == ()


def test_core2_source_closure_is_exact_and_has_no_fallback(tmp_path):
    configs = _configs()
    source_records = tuple(islice(iter_formal_plan(configs["CORE-1"]), 1, 3))
    source = RTA4FormalRunner(configs["CORE-1"]).run_nonformal_fixture(
        source_records, root=tmp_path / "source", taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    target_record = (next(iter_formal_plan(configs["CORE-2"])),)
    target_root = tmp_path / "target"
    target = RTA4FormalRunner(configs["CORE-2"]).run_nonformal_fixture(
        target_record, root=target_root, taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
        source_closures={"CORE-1": source},
    )
    dependencies = target.table("formal_dependencies.csv")
    assert len(dependencies) == 2
    assert all(row["fallback_used"] == "false" for row in dependencies)
    manifest = aggregate_formal_run(
        target_root, tmp_path / "aggregate-core2", bootstrap_replicates=5,
        source_closures={"CORE-1": source},
    )
    assert set(manifest["data_file_sha256"]) == {
        "figure_2_ablation_mechanisms.csv", "table_1_parameters.csv",
    }
    _rewrite_row(target_root, "formal_dependencies.csv", 0, source_result_hash="a" * 64)
    with pytest.raises(Exception, match="source closure"):
        validate_formal_run_closure(
            target_root, source_closures={"CORE-1": source},
        )


def test_validated_source_object_is_refreshed_for_validation_runner_and_aggregation(tmp_path):
    configs = _configs()
    source_records = tuple(islice(iter_formal_plan(configs["CORE-1"]), 1, 3))
    source_root = tmp_path / "source"
    source = RTA4FormalRunner(configs["CORE-1"]).run_nonformal_fixture(
        source_records, root=source_root, taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    target_records = (next(iter_formal_plan(configs["CORE-2"])),)
    target_root = tmp_path / "target"
    runner = RTA4FormalRunner(configs["CORE-2"])
    target = runner.run_nonformal_fixture(
        target_records, root=target_root, taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
        source_closures={"CORE-1": source},
    )

    unchanged_object = validate_formal_run_closure(
        target_root, source_closures={"CORE-1": source},
    )
    unchanged_path = validate_formal_run_closure(
        target_root, source_closures={"CORE-1": source_root},
    )
    assert unchanged_object.closure_sha256 == target.closure_sha256
    assert unchanged_path.closure_sha256 == target.closure_sha256

    drift = source_root / "post_validation_drift.txt"
    drift.write_text("drift\n", encoding="utf-8")
    with pytest.raises(Exception, match="source closure|stale"):
        validate_formal_run_closure(
            target_root, source_closures={"CORE-1": source_root},
        )
    with pytest.raises(Exception, match="stale"):
        validate_formal_run_closure(
            target_root, source_closures={"CORE-1": source},
        )
    with pytest.raises(Exception, match="stale"):
        aggregate_formal_run(
            target_root, tmp_path / "aggregate-stale",
            bootstrap_replicates=5, source_closures={"CORE-1": source},
        )

    resumed_calls = []
    with pytest.raises(Exception, match="stale"):
        runner.run_nonformal_fixture(
            target_records, root=target_root, taskset_store=tmp_path / "store",
            certificate_provider=_plan_certificate,
            rta_executor=lambda record, certificate: (
                resumed_calls.append(record.execution_id)
                or _fake_rta(record, certificate)
            ),
            source_closures={"CORE-1": source},
        )
    assert resumed_calls == []


def test_core3_projection_and_applicability_are_reconstructed(tmp_path):
    configs = _configs()
    source_records = tuple(islice(iter_formal_plan(configs["CORE-1"]), 12))
    source = RTA4FormalRunner(configs["CORE-1"]).run_nonformal_fixture(
        source_records, root=tmp_path / "source", taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    simulation_record = (next(iter_formal_plan(configs["CORE-3"])),)
    simulation_root = tmp_path / "simulation"
    calls = []
    closure = RTA4FormalRunner(configs["CORE-3"]).run_nonformal_fixture(
        simulation_record, root=simulation_root,
        taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate,
        simulator_executor=_fake_simulator_factory(tmp_path / "traces", calls),
        source_closures={"CORE-1": source},
    )
    assert len(calls) == 1
    assert len(closure.table("formal_applicability.csv")) == 12
    manifest = aggregate_formal_run(
        simulation_root, tmp_path / "aggregate-core3", bootstrap_replicates=5,
        source_closures={"CORE-1": source},
    )
    assert set(manifest["data_file_sha256"]) == {
        "figure_3_rta_simulation_audit.csv", "table_1_parameters.csv",
        "table_3_simulation_audit.csv",
    }
    assert RTA4FormalRunner(configs["CORE-3"]).run_nonformal_fixture(
        simulation_record, root=simulation_root,
        taskset_store=tmp_path / "store",
        certificate_provider=_plan_certificate,
        simulator_executor=_fake_simulator_factory(tmp_path / "traces", calls),
        source_closures={"CORE-1": source},
    ).closure_sha256 == closure.closure_sha256
    assert len(calls) == 1

    cache_drift = tmp_path / "cache-drift"
    shutil.copytree(simulation_root, cache_drift)
    cached_simulation = read_csv(cache_drift / "formal_simulation_runs.csv")[0]
    cached_trace = cache_drift / cached_simulation["trace_path"]
    cached_trace.write_text(
        cached_trace.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    with pytest.raises(Exception, match="trace hash"):
        RTA4FormalRunner(configs["CORE-3"]).run_nonformal_fixture(
            simulation_record, root=cache_drift,
            taskset_store=tmp_path / "store",
            certificate_provider=_plan_certificate,
            simulator_executor=_fake_simulator_factory(tmp_path / "traces", calls),
            source_closures={"CORE-1": source},
        )
    assert len(calls) == 1

    bad_projection = tmp_path / "bad-projection"
    shutil.copytree(simulation_root, bad_projection)
    _rewrite_row(bad_projection, "formal_simulation_runs.csv", 0, scheduler="wrong")
    with pytest.raises(Exception, match="projection"):
        validate_formal_run_closure(
            bad_projection, source_closures={"CORE-1": source},
        )

    bad_join = tmp_path / "bad-applicability"
    shutil.copytree(simulation_root, bad_join)
    _rewrite_row(bad_join, "formal_applicability.csv", 0, method="LOC_THETA_LOC")
    with pytest.raises(Exception, match="classification"):
        validate_formal_run_closure(
            bad_join, source_closures={"CORE-1": source},
        )

    bad_jobs = tmp_path / "bad-jobs"
    shutil.copytree(simulation_root, bad_jobs)
    first_job = read_csv(bad_jobs / "formal_simulation_job_results.csv")[0]
    _rewrite_row(
        bad_jobs, "formal_simulation_job_results.csv", 0,
        completion_time=int(first_job["completion_time"]) + 1,
        observed_response_time=int(first_job["observed_response_time"]) + 1,
    )
    with pytest.raises(Exception, match="raw job|task rows"):
        validate_formal_run_closure(
            bad_jobs, source_closures={"CORE-1": source},
        )


def test_worker_consistency_and_core4_monotonicity_use_raw_task_results(tmp_path):
    configs = _configs()
    worker_records = tuple(islice(iter_formal_plan(configs["CORE-5B"]), 4))

    def worker_sensitive(record, certificate):
        result = _fake_rta(record, certificate)
        if record.material["worker_count"] == 8:
            result["task_results"][0]["candidate_response_time"] = 2
        return result

    with pytest.raises(Exception, match="P0 hard validator"):
        RTA4FormalRunner(configs["CORE-5B"]).run_nonformal_fixture(
            worker_records, root=tmp_path / "workers",
            taskset_store=tmp_path / "store-workers",
            certificate_provider=_plan_certificate,
            rta_executor=worker_sensitive,
        )
    checks = read_csv(tmp_path / "workers" / "formal_worker_consistency.csv")
    assert any(row["candidate_match"] == "false" for row in checks)
    assert any(row["check_status"] == "P0_MISMATCH" for row in checks)

    core4_stream = iter_formal_plan(configs["CORE-4"])
    selected = tuple(
        record for index, record in enumerate(islice(core4_stream, 9))
        if index in {4, 8}
    )

    def monotonicity_violation(record, certificate):
        result = _fake_rta(record, certificate)
        result["task_results"][0]["candidate_response_time"] = (
            1 if record.material["axis_value"] == "0" else 2
        )
        return result

    with pytest.raises(Exception, match="P0 hard validator"):
        RTA4FormalRunner(configs["CORE-4"]).run_nonformal_fixture(
            selected, root=tmp_path / "monotonicity",
            taskset_store=tmp_path / "store-monotonicity",
            certificate_provider=_plan_certificate,
            rta_executor=monotonicity_violation,
        )


def test_pairing_uses_complete_axes_and_is_input_order_invariant():
    results = []
    tasks = []
    for service_index, service in enumerate(("a" * 64, "b" * 64)):
        for method, candidate in (("CW_THETA_CW", "4"), ("LOC_THETA_LOC", "2")):
            analysis_id = hashlib.sha256(
                f"{service_index}:{method}".encode()
            ).hexdigest()
            results.append({
                "analysis_id": analysis_id, "taskset_skeleton_id": "c" * 64,
                "taskset_id": "d" * 64, "exact_e0": "0",
                "service_identity": service, "power_vector_hash": "e" * 64,
                "deadline_variant": "constrained_uniform_slack_v1",
                "scenario": "MAIN", "axis": "baseline",
                "axis_value": "baseline", "method": method,
                "normalized_utilization": "1/2", "taskset_proven": "true",
                "runtime_wall_seconds": "1",
            })
            tasks.append({
                "analysis_id": analysis_id, "task_id": "tau",
                "candidate_response_time": candidate,
            })

    class Closure:
        metadata = {"core": "CORE-1", "plan_sha256": "f" * 64}
        closure_sha256 = "0" * 64

        def __init__(self, reverse=False):
            self._results = tuple(reversed(results)) if reverse else tuple(results)
            self._tasks = tuple(reversed(tasks)) if reverse else tuple(tasks)

        def table(self, name):
            if name == "formal_rta_taskset_results.csv":
                return self._results
            if name == "formal_rta_task_results.csv":
                return self._tasks
            raise AssertionError(name)

    forward = formal_aggregation._figure1(Closure(), 5)
    backward = formal_aggregation._figure1(Closure(True), 5)
    assert forward == backward
    paired = [row for row in forward if row["relation"] == "CW_TO_LOC"]
    assert paired == [{
        "row_type": "PAIRED_RESPONSE_REDUCTION", "method": "NA",
        "normalized_utilization": "1/2", "exact_e0": "0",
        "relation": "CW_TO_LOC", "sample_count": 2, "denominator": 2,
        "estimate": "0.5", "ci_lower": "NA", "ci_upper": "NA",
        "median": "0.5", "p95": "NA", "iqr_lower": "0.5",
        "iqr_upper": "0.5",
    }]


def test_figure2_pairing_has_explicit_target_source_provenance():
    def result(method, proven, analysis):
        return {
            "analysis_id": analysis, "taskset_skeleton_id": "c" * 64,
            "taskset_id": "d" * 64, "exact_e0": "0",
            "service_identity": "e" * 64, "power_vector_hash": "f" * 64,
            "deadline_variant": "constrained_uniform_slack_v1",
            "scenario": "MAIN", "axis": "baseline", "axis_value": "baseline",
            "method": method, "normalized_utilization": "1/2",
            "taskset_proven": proven,
        }

    class Closure:
        def __init__(self, core, closure_sha, rows, reverse=False):
            self.metadata = {
                "core": core,
                "plan_sha256": hashlib.sha256(core.encode()).hexdigest(),
            }
            self.closure_sha256 = closure_sha
            self.rows = tuple(reversed(rows)) if reverse else tuple(rows)

        def table(self, name):
            if name == "formal_rta_taskset_results.csv":
                return self.rows
            if name == "formal_rta_mechanisms.csv":
                return ()
            raise AssertionError(name)

    target_rows = (
        result("CW_THETA_CW", "false", "target-cw"),
        result("SEQ_THETA_SEQ", "false", "target-seq"),
    )
    source_rows = (
        result("CW_THETA_CW", "true", "source-cw-must-be-ignored"),
        result("LOC_THETA_LOC", "true", "source-loc"),
        result("PH_THETA_PH", "true", "source-ph"),
        result("SEQ_THETA_SEQ", "true", "source-seq-must-be-ignored"),
    )
    target = Closure("CORE-2", "1" * 64, target_rows)
    source = Closure("CORE-1", "2" * 64, source_rows)
    forward = formal_aggregation._figure2(target, source)
    backward = formal_aggregation._figure2(
        Closure("CORE-2", "1" * 64, target_rows, reverse=True),
        Closure("CORE-1", "2" * 64, source_rows, reverse=True),
    )
    assert forward == backward
    gains = {row["relation"]: row["estimate"] for row in forward}
    assert gains["LOC_THETA_MINUS_CW_THETA"] == "1"
    assert gains["SEQ_THETA_MINUS_PH_THETA"] == "-1"

    with pytest.raises(Exception, match="duplicate TARGET_CORE2"):
        formal_aggregation._figure2(
            Closure("CORE-2", "1" * 64, target_rows + (target_rows[0],)),
            source,
        )
    with pytest.raises(Exception, match="duplicate SOURCE_CORE1"):
        formal_aggregation._figure2(
            target,
            Closure("CORE-1", "2" * 64, source_rows + (source_rows[1],)),
        )


def test_core3_release_projection_reuses_pr_c_and_simulation_is_deduplicated():
    certificate = _certificate()
    projection, window, payload = build_formal_release_projection(
        certificate, ASYNC_HASH_PHASE_V1,
    )
    assert projection.taskset_id == certificate.taskset_id
    assert window.observation_horizon == 30000 + max(task.relative_deadline for task in certificate.tasks)
    assert tuple(row["arrival_offset"] for row in payload) == tuple(
        row.arrival_offset for row in projection.offsets
    )
    calls = []
    deduplicator = SimulationDeduplicator()
    for _method in RTA4_RECURSIVE_METHODS:
        for _e0 in ("0", "1/20", "1"):
            assert deduplicator.execute_once(
                projection.release_projection_id,
                lambda: calls.append("called") or {"status": "OK"},
            ) == {"status": "OK"}
    assert calls == ["called"]
    assert deduplicator.unique_simulation_count == 1


def test_hard_validators_detect_dominance_monotonicity_soundness_and_worker_p0():
    tasksets = [
        {"analysis_id": "a", "taskset_id": "t", "exact_e0": "0", "method": "CW_THETA_CW", "taskset_proven": "true", "fallback_used": "false"},
        {"analysis_id": "b", "taskset_id": "t", "exact_e0": "0", "method": "LOC_THETA_LOC", "taskset_proven": "false", "fallback_used": "false"},
    ]
    tasks = [
        {"taskset_id": "t", "exact_e0": "0", "method": "CW_THETA_CW", "task_id": "x", "candidate_response_time": "5"},
        {"taskset_id": "t", "exact_e0": "0", "method": "LOC_THETA_LOC", "task_id": "x", "candidate_response_time": "6"},
    ]
    assert all(finding.severity == P0 for finding in validate_dominance(tasksets, tasks))
    monotonic = [
        {"analysis_id": "a", "taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "0", "taskset_proven": "true"},
        {"analysis_id": "b", "taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "1", "taskset_proven": "false"},
        {"analysis_id": "c", "taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "deadline_slack_fraction", "axis_value": "1/4", "taskset_proven": "true"},
    ]
    monotonic_tasks = [
        {"analysis_id": "a", "task_id": "x", "candidate_response_time": "5"},
        {"analysis_id": "b", "task_id": "x", "candidate_response_time": "6"},
    ]
    assert len(validate_monotonicity(monotonic, monotonic_tasks)) == 2
    soundness = [{
        "comparison_id": "c", "theorem_comparison_eligible": "true",
        "soundness_counterexample": "false", "comparison_status": "RTA_PASS_SIM_FAIL",
        "candidate_response_time": "5", "observed_response_time": "6",
    }]
    assert len(validate_soundness(soundness)) == 2
    worker = [{
        "mathematical_request_id": "m", "solver_status_match": "true",
        "candidate_match": "true", "witness_match": "true",
        "certification_match": "true", "failure_reason_match": "true",
        "math_hash_match": "false", "reference_math_result_hash": "a",
        "compared_math_result_hash": "b",
    }]
    assert len(validate_worker_consistency(worker)) == 2


def _fixture_run(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "fixture-run"
    config = _configs()["CORE-1"]
    records = tuple(islice(iter_formal_plan(config), 4))
    RTA4FormalRunner(config).run_nonformal_fixture(
        records, root=root, taskset_store=tmp_path / "fixture-store",
        certificate_provider=_plan_certificate, rta_executor=_fake_rta,
    )
    return root, tmp_path / "aggregate"


def test_complete_fixture_closure_aggregation_and_plotting_are_validated(tmp_path):
    root, aggregate_root = _fixture_run(tmp_path)
    closure = validate_formal_run_closure(root, require_complete=True)
    assert closure.metadata["execution_class"] == "NONFORMAL_TEST_FIXTURE"
    manifest = aggregate_formal_run(root, aggregate_root, bootstrap_replicates=25)
    assert manifest["bootstrap_replicates"] == 25
    assert validate_aggregate_bundle(aggregate_root) == manifest
    assert validate_plot_data(aggregate_root)["aggregate_sha256"] == manifest["aggregate_sha256"]
    plot_root = tmp_path / "plots"
    plot_manifest = render_formal_publication_figures(aggregate_root, plot_root)
    assert len([name for name in plot_manifest["output_sha256"] if name.endswith(".png")]) == 1
    assert len([name for name in plot_manifest["output_sha256"] if name.endswith(".pdf")]) == 1


def test_legacy_profile_digests_counts_headers_and_allowlist_remain_exact():
    expected = {
        "CORE-1": (
            "v9_3_core1_formal.yaml",
            "e0fe1259d2f23a9883b6f8635bed12e2b13211bd91f670e562bb573cd1bbd183",
            9600, "2bec5d2a5bed2c0cf19e0d5f085cfa21bebddfb810b9bc6865ac6e1e7dbdc248",
        ),
        "CORE-2": (
            "v9_3_core2_formal.yaml",
            "5e3c7333ba619f31b074b9fd5df495993c436900d8ca201d5cd0c272bbd30b6e",
            24000, "48584e3a70e180d98578b6dce0d28b02ede30fee8161b863c72f2bac445159fa",
        ),
    }
    for core, (filename, expected_hash, expected_count, expected_plan) in expected.items():
        config = load_config(ROOT / "configs" / filename, expected_core=core)
        description = ExecutionEngine(config).describe()
        assert config_hash(config) == expected_hash
        assert description["request_count"] == expected_count
        assert hashlib.sha256(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest() == expected_plan
    material = [{"table": name, "columns": list(columns)} for name, columns in TABLES.items()]
    assert hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest() == "9cc03a5cb1797aa0f3d4734a3ff00f07a6a3d7f0a37ab5503cda7c6cc56140b5"
    assert KNOWN_VARIANTS == {
        "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC",
    }


def test_streaming_digest_handles_10000_records_without_materializing_payloads():
    consumed = 0

    def records(count):
        nonlocal consumed
        for index in range(count):
            consumed += 1
            yield FormalPlanRecord(
                "synthetic", "TEST", index, hashlib.sha256(str(index).encode()).hexdigest(),
                None, None, None, {"index": index},
            )
    tracemalloc.start()
    digest = ordered_stream_digest(records(10_000))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert digest.count == 10_000
    assert consumed == 10_000
    assert len(digest.sha256) == 64
    assert peak < 4_000_000
