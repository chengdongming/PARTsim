from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
from itertools import islice
import json
from pathlib import Path
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
from experiments.v9_3.release_applicability import ASYNC_HASH_PHASE_V1
from experiments.v9_3.result_writer import ResultWriter, ResultWriterError, TABLES
from experiments.v9_3.rta4_formal_aggregation import (
    aggregate_formal_run, validate_aggregate_bundle,
)
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORE2_METHODS, RTA4_FORMAL_PARAMETER_STATUS,
    RTA4_FORMAL_PROFILE, RTA4_RECURSIVE_METHODS,
    load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_pipeline import (
    RTA4FormalAuthorizationError, RTA4FormalRunner, SimulationDeduplicator,
    build_formal_release_projection, formal_analysis_identity,
    formal_execution_identity,
)
from experiments.v9_3.rta4_formal_plan import (
    FormalPlanRecord, RTA4FormalPlanError, describe_all_formal_plans,
    describe_formal_plan,
    exact_service_scale_identity,
    iter_core3_comparison_plan, iter_core4_plan,
    iter_core5a_plan, iter_core5b_math_references, iter_core5b_plan,
    ordered_stream_digest,
)
from experiments.v9_3.rta4_formal_plotting import (
    render_formal_publication_figures, validate_plot_data,
)
from experiments.v9_3.rta4_formal_schema import (
    FORMAL_TABLES, formal_schema_hash, formal_schema_manifest,
    legacy_table_overlap,
)
from experiments.v9_3.rta4_formal_store import (
    RTA4FormalTasksetStore, build_ofat_taskset_variant,
    scale_taskset_time_exact,
)
from experiments.v9_3.rta4_formal_validation import (
    P0, validate_dominance, validate_formal_run_closure,
    validate_monotonicity, validate_soundness, validate_worker_consistency,
)
from experiments.v9_3.rta4_formal_writer import (
    RTA4FormalResultWriter, RTA4FormalWriterError,
)


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


def test_taskset_store_and_writer_persist_pr_b_certificate_with_exact_identity(tmp_path):
    certificate = _certificate()
    store = RTA4FormalTasksetStore(tmp_path / "store")
    writer = RTA4FormalResultWriter(
        tmp_path / "run", plan_sha256="a" * 64,
        config_semantic_hash="b" * 64,
        parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
        execution_class="NONFORMAL_TEST_FIXTURE",
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
    old_root = tmp_path / "old"
    ResultWriter(old_root)
    with pytest.raises(RTA4FormalWriterError, match="legacy"):
        RTA4FormalResultWriter(
            old_root, plan_sha256="a" * 64, config_semantic_hash="b" * 64,
            parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
        )
    new_root = tmp_path / "new"
    RTA4FormalResultWriter(
        new_root, plan_sha256="a" * 64, config_semantic_hash="b" * 64,
        parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
    )
    with pytest.raises(ResultWriterError, match="RTA4 formal"):
        ResultWriter(new_root)


def test_schema_plan_resume_and_terminal_conflicts_fail_closed(tmp_path):
    root = tmp_path / "run"
    writer = RTA4FormalResultWriter(
        root, plan_sha256="a" * 64, config_semantic_hash="b" * 64,
        parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
    )
    with pytest.raises(RTA4FormalWriterError, match="plan/config"):
        RTA4FormalResultWriter(
            root, plan_sha256="c" * 64, config_semantic_hash="b" * 64,
            parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
        )
    writer.write_terminal("d" * 64, {"solver_status": "COMPLETED"})
    writer.write_terminal("d" * 64, {"solver_status": "COMPLETED"})
    with pytest.raises(RTA4FormalWriterError, match="terminal result conflict"):
        writer.write_terminal("d" * 64, {"solver_status": "TIMEOUT"})


def test_pre_pilot_runner_allows_dry_run_and_bounded_fixture_but_rejects_formal():
    runner = RTA4FormalRunner(_configs()["CORE-1"])
    assert runner.describe()["counts"]["rta_requests"] == 19200
    with pytest.raises(RTA4FormalAuthorizationError, match="PR-E"):
        runner.run()
    records = tuple(islice(iter_core4_plan(), 2))
    assert runner.run_nonformal_fixture(records, lambda record: record.record_id) == tuple(
        record.record_id for record in records
    )
    with pytest.raises(RTA4FormalAuthorizationError, match="100-request"):
        runner.run_nonformal_fixture(tuple(islice(iter_core4_plan(), 101)), lambda record: None)


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
        {"taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "0", "taskset_proven": "true", "candidate_response_time": "5"},
        {"taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "e0", "axis_value": "1", "taskset_proven": "false", "candidate_response_time": "6"},
        {"taskset_skeleton_id": "s", "method": "SEQ_THETA_SEQ", "axis": "deadline_slack_fraction", "axis_value": "1/4", "taskset_proven": "true", "candidate_response_time": "5"},
    ]
    assert len(validate_monotonicity(monotonic)) == 2
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
    store = RTA4FormalTasksetStore(tmp_path / "fixture-store")
    writer = RTA4FormalResultWriter(
        root, plan_sha256="a" * 64, config_semantic_hash="b" * 64,
        parameter_status=RTA4_FORMAL_PARAMETER_STATUS,
        execution_class="NONFORMAL_TEST_FIXTURE",
    )
    certificate = _certificate()
    writer.persist_taskset(store, certificate)
    request_id = "c" * 64
    analysis_id = "d" * 64
    writer.append("formal_rta_requests.csv", {
        "analysis_id": analysis_id, "request_id": request_id,
        "execution_run_id": "e" * 64, "cell_id": "fixture-cell",
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id, "taskset_hash": certificate.taskset_hash,
        "method": "SEQ_THETA_SEQ", "method_role": "MAIN_METHOD",
        "carry_policy": "SELF_RECURSIVE", "exact_e0": "0",
        "service_identity": "f" * 64, "power_vector_hash": certificate.power_vector_hash,
        "theory_document_sha256": "1" * 64, "numeric_contract_sha256": "2" * 64,
        "exact_input_identity": "3" * 64, "timeout_contract": "FIXTURE",
        "source_analysis_id": "", "request_status": "PLANNED",
    })
    writer.append("formal_rta_taskset_results.csv", {
        "analysis_id": analysis_id, "request_id": request_id,
        "execution_run_id": "e" * 64, "cell_id": "fixture-cell",
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id, "taskset_hash": certificate.taskset_hash,
        "method": "SEQ_THETA_SEQ", "method_role": "MAIN_METHOD",
        "carry_policy": "SELF_RECURSIVE", "exact_e0": "0",
        "service_identity": "f" * 64, "power_vector_hash": certificate.power_vector_hash,
        "solver_status": "COMPLETED", "taskset_certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": "true", "first_failed_priority": "", "failure_reason": "",
        "timeout": "false", "runtime_wall_seconds": "0.5",
        "runtime_cpu_seconds": "0.4", "peak_rss_bytes": "1000",
        "checked_w_count": "1", "checked_q_count": "0", "checked_h_count": "1",
        "exact_result_hash": "4" * 64, "source_analysis_id": "",
        "fallback_used": "false", "axis": "baseline", "axis_value": "baseline",
        "normalized_utilization": "1/2",
    })
    writer.append("formal_rta_task_results.csv", {
        "analysis_id": analysis_id,
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id, "method": "SEQ_THETA_SEQ",
        "exact_e0": "0", "task_id": "tau-a", "priority_rank": "0",
        "task_solver_status": "CANDIDATE_FOUND",
        "task_certification_status": "CERTIFIED",
        "candidate_response_time": "3", "D": str(certificate.tasks[0].relative_deadline),
        "checked_w_count": "1", "checked_q_count": "0", "checked_h_count": "1",
        "failure_reason": "", "exact_task_result_hash": "5" * 64,
    })
    writer.write_terminal(request_id, {"analysis_id": analysis_id, "solver_status": "COMPLETED"})
    aggregate_root = tmp_path / "aggregate"
    return root, aggregate_root


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
    assert len([name for name in plot_manifest["output_sha256"] if name.endswith(".png")]) == 5
    assert len([name for name in plot_manifest["output_sha256"] if name.endswith(".pdf")]) == 5


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
