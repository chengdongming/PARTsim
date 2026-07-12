from fractions import Fraction
from pathlib import Path
import shutil

import pytest

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as runner
from asap_block_v1_3_12_schema_binding import (
    CONTRACT_DIRECTORY_NAME,
    ContractBindingError,
    DEFAULT_CONTRACT_ROOT,
    V1312SchemaBinding,
)


HASH = "a" * 64


def dependency_context():
    return taskset.DependencyContext(
        taskset_identity=HASH,
        task_definitions_identity="b" * 64,
        priority_order_identity="c" * 64,
        e0_canonical_identity="d" * 64,
        service_curve_identity="e" * 64,
        power_vector_identity="f" * 64,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        formal_contract_identity="1" * 64,
    )


def analysis_input(e0=100, three_tasks=False):
    tasks = [
        core.V93Task("0", 1, 5, 10, Fraction(1)),
        core.V93Task("1", 1, 7, 12, Fraction(1)),
    ]
    if three_tasks:
        tasks.append(core.V93Task("2", 1, 9, 15, Fraction(1)))
    return taskset.TasksetAnalysisInput(
        tuple(tasks),
        1,
        Fraction(e0),
        lambda _index: Fraction(0),
        dependency_context(),
    )


def analysis_ids(prefix="analysis"):
    return {variant: "{}-{}".format(prefix, variant.name) for variant in taskset.AnalysisVariant}


def test_runner_rejects_illegal_service_curve_before_dispatch(monkeypatch):
    inp = taskset.TasksetAnalysisInput(
        analysis_input().tasks,
        1,
        Fraction(100),
        [1] * 9,
        dependency_context(),
    )
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(True)

    monkeypatch.setattr(taskset, "analyze_taskset_v9_3", forbidden)
    with pytest.raises(core.V93NumericError, match=r"beta\(0\)"):
        runner.dispatch_rta_version(
            "v9.3",
            v93_request=runner.V93DispatchRequest(
                "invalid-curve",
                taskset.AnalysisVariant.LOC_THETA_LOC,
                inp,
            ),
        )
    assert called == []


def task_definitions(inp):
    return {
        task.name: {
            "taskset_id": "taskset",
            "task_id": int(task.name),
            "C_i": task.wcet,
            "T_i": task.period,
            "D_i": task.deadline,
            "P_raw": "1",
            "P_analysis": "1",
            "priority_rank": rank,
            "power_latent_value": "1",
            "P_analysis_scaled": None,
            "P_rounding_mode": None,
        }
        for rank, task in enumerate(inp.tasks)
    }


def taskset_result_base(binding, inp, request_id="request"):
    row = binding.empty_row("per_taskset_results.csv")
    row.update(
        {
            "run_phase": "DIAGNOSTIC",
            "request_id": request_id,
            "build_identity_hash": "2" * 64,
            "rta_implementation_hash": "3" * 64,
            "generation_request_id": "generation",
            "taskset_id": "taskset",
            "taskset_materialization_request_id": "generation-request",
            "generator_contract_hash": "4" * 64,
            "experiment_config_version": "MICROCASE_V1",
            "experiment_config_hash": "5" * 64,
            "M": inp.processors,
            "n": len(inp.tasks),
            "target_total_utilization": "1/2",
            "actual_total_utilization": "1/2",
            "target_rho_p": "1/2",
            "actual_rho_p": "1/2",
            "target_rho_e": "1/2",
            "actual_rho_e_raw": "1/2",
            "actual_rho_e_analysis": "1/2",
            "rho_e_tolerance": "0",
            "rho_e_tolerance_mode": "EXACT",
            "rho_e_parameterization_status": "ACCEPTED",
            "numeric_coverage_status": "VALID",
            "service_rate_reference": "0",
            "service_rate_r_raw": "0",
            "service_curve_integerization_mode": "EXACT",
            "power_scale_alpha": "1",
            "target_power_demand": "1",
            "actual_power_demand_raw": "1",
            "actual_power_demand_analysis": "1",
            "target_service_latency_ratio": "0",
            "realized_service_latency_L": 0,
            "realized_service_latency_ratio": "0",
            "power_latent_seed": "0",
            "power_latent_vector_hash": "6" * 64,
            "power_latent_mapping_version": "MICROCASE_POWER_V1",
            "priority_reference_delta": "DM",
            "priority_rank_reference_hash": "7" * 64,
            "E0_target_raw": str(inp.e0),
            "E0_analysis_effective": str(inp.e0),
            "E0_rounding_error": "0",
            "target_epsilon_0": "0",
            "realized_epsilon_0_analysis": "0",
            "e0_parameterization_policy": "EXACT_GRID",
            "e0_parameterization_status": "ACCEPTED",
            "theorem_conditioning_mode": "CONDITIONAL_E0_POSITIVE",
            "service_latency_L": 0,
            "service_curve_raw_spec": "beta(t)=0",
            "runtime_wall": "0",
            "runtime_cpu": "0",
            "rta_formula_version": "v9.3",
            "theory_document_sha256": taskset.THEORY_DOCUMENT_SHA256,
            "fixed_carry_in_corollary_hash": taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
            "taskset_semantic_hash": HASH,
            "priority_rank_hash": "b" * 64,
            "power_vector_raw_hash": "c" * 64,
            "analysis_E0_canonical_hash": "d" * 64,
            "analysis_power_vector_canonical_hash": "e" * 64,
            "analysis_service_curve_canonical_hash": "f" * 64,
            "energy_numeric_mode": "EXACT_RATIONAL",
            "energy_demand_rounding": "EXACT",
            "energy_supply_rounding": "EXACT",
            "numeric_integer_type": "ARBITRARY_PRECISION_INTEGER",
            "numeric_range_check_status": "VALID",
            "service_curve_raw_hash": "8" * 64,
            "plan_context_hash": "9" * 64,
            "analysis_energy_unit_hash": "0" * 64,
            "formal_contract_hash": "1" * 64,
            "formal_contract_version": "MICROCASE_V1",
        }
    )
    return row


def test_schema_binding_matches_frozen_v1_3_12_contract():
    binding = V1312SchemaBinding()
    assert len(binding.table_names) == 23
    assert len(binding.canonical_columns("per_task_results.csv")) == 41
    assert binding.canonical_columns("per_task_results.csv")[14] == (
        "task_failure_reason_code"
    )
    assert binding.canonical_columns("per_task_results.csv")[25] == (
        "task_failure_detail"
    )
    assert binding.enum_values("task_failure_reason_code") == list(
        runner.FAILURE_DETAIL_BY_CODE
    )
    pairs = {
        "analysis_variant": taskset.AnalysisVariant,
        "analysis_method_role": taskset.AnalysisMethodRole,
        "task_solver_status": taskset.TaskSolverStatus,
        "task_certification_status": taskset.TaskCertificationStatus,
        "analysis_solver_status": taskset.AnalysisSolverStatus,
        "analysis_certification_status": taskset.AnalysisCertificationStatus,
        "dependency_vector_check_status": taskset.DependencyVectorCheckStatus,
        "dominance_invariant_status": taskset.DominanceInvariantStatus,
        "fixed_carry_in_corollary_status": taskset.FixedCarryInInterfaceStatus,
    }
    for enum_name, enum_type in pairs.items():
        binding.assert_python_enum(enum_type, enum_name)


def test_carry_in_source_certification_uses_analysis_enum():
    binding = V1312SchemaBinding()
    spec = binding.fields("per_task_results.csv")[
        "carry_in_source_certification_status"
    ]
    assert spec["enum_ref"] == "analysis_certification_status"
    binding.common.validate_scalar(
        "CERTIFIED_TASKSET", spec, binding.schema["enums"], binding.schema["failure_masks"]
    )
    with pytest.raises(ValueError):
        binding.common.validate_scalar(
            "CERTIFIED", spec, binding.schema["enums"], binding.schema["failure_masks"]
        )


def test_binding_rejects_unknown_column_and_float():
    binding = V1312SchemaBinding()
    row = binding.empty_row("task_definitions.csv")
    row["unknown"] = "x"
    with pytest.raises(ContractBindingError, match="row shape"):
        binding.encode_row("task_definitions.csv", row)
    row.pop("unknown")
    row.update(
        taskset_id="x",
        task_id=0,
        C_i=1,
        T_i=10,
        D_i=10,
        P_raw="1",
        P_analysis=1.0,
        priority_rank=0,
        power_latent_value="1",
    )
    with pytest.raises(ContractBindingError, match="float"):
        binding.encode_row("task_definitions.csv", row)


def test_binding_rejects_duplicate_yaml_key(tmp_path):
    target = tmp_path / CONTRACT_DIRECTORY_NAME
    shutil.copytree(DEFAULT_CONTRACT_ROOT, target)
    schema = target / "ASAP_BLOCK_experiment_schema_v1_3_12.yaml"
    schema.write_text(
        schema.read_text(encoding="utf-8") + "\nschema_metadata:\n  version: 9\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate YAML key"):
        V1312SchemaBinding(target)


def test_dispatch_default_v20_v21_and_v93(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner.v20,
        "analyze_taskset",
        lambda **kwargs: calls.append(("v20.4", kwargs)) or "v20",
    )
    monkeypatch.setattr(
        runner.v21,
        "analyze_taskset_v21",
        lambda **kwargs: calls.append(("v21", kwargs)) or "v21",
    )
    monkeypatch.setattr(
        runner.v93,
        "analyze_taskset_v9_3",
        lambda *args, **kwargs: calls.append(("v9.3", args, kwargs)) or "v93",
    )
    assert runner.dispatch_rta_version(v20_kwargs={"x": 1}) == "v20"
    assert runner.dispatch_rta_version("v21", v21_kwargs={"x": 2}) == "v21"
    request = runner.V93DispatchRequest(
        "analysis", taskset.AnalysisVariant.CW_D, analysis_input()
    )
    assert runner.dispatch_rta_version("v9.3", v93_request=request) == "v93"
    assert [call[0] for call in calls] == ["v20.4", "v21", "v9.3"]


def test_dispatch_rejects_unknown_and_v93_exception_never_falls_back(monkeypatch):
    with pytest.raises(runner.RunnerConformanceError, match="unknown"):
        runner.dispatch_rta_version("future")
    legacy_calls = []
    monkeypatch.setattr(
        runner.v20, "analyze_taskset", lambda **kwargs: legacy_calls.append("v20")
    )
    monkeypatch.setattr(
        runner.v21,
        "analyze_taskset_v21",
        lambda **kwargs: legacy_calls.append("v21"),
    )
    monkeypatch.setattr(
        runner.v93,
        "analyze_taskset_v9_3",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("v93 failure")),
    )
    request = runner.V93DispatchRequest(
        "analysis", taskset.AnalysisVariant.CW_D, analysis_input()
    )
    with pytest.raises(RuntimeError, match="v93 failure"):
        runner.dispatch_rta_version("v9.3", v93_request=request)
    assert legacy_calls == []


def test_five_configuration_orchestration_certifies_frozen_source_and_target():
    run = runner.run_five_configurations_v9_3(
        analysis_input(), analysis_ids()
    )
    results = run.by_variant()
    assert tuple(results) == runner.VARIANT_ORDER
    source = results[taskset.AnalysisVariant.CW_THETA_CW]
    target = results[taskset.AnalysisVariant.LOC_THETA_CW]
    assert source.certification_status is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    assert target.certification_status is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    assert target.source_analysis_id == source.analysis_id
    assert target.dependency_check_status is taskset.DependencyVectorCheckStatus.VALID
    source_candidates = {r.task_id: r.candidate_response_time for r in source.task_records}
    assert all(
        record.candidate_response_time <= source_candidates[record.task_id]
        for record in target.task_records
    )


def test_serialization_preserves_analyzer_states_hashes_and_dependency():
    binding = V1312SchemaBinding()
    inp = analysis_input()
    run = runner.run_five_configurations_v9_3(inp, analysis_ids("serial"))
    results = run.by_variant()
    definitions = task_definitions(inp)
    source_result = results[taskset.AnalysisVariant.CW_THETA_CW]
    source = runner.serialize_taskset_analysis_v1_3_12(
        source_result,
        binding,
        taskset_result_base(binding, inp, "request-source"),
        definitions,
    )
    target_result = results[taskset.AnalysisVariant.LOC_THETA_CW]
    target = runner.serialize_taskset_analysis_v1_3_12(
        target_result,
        binding,
        taskset_result_base(binding, inp, "request-target"),
        definitions,
        source=source,
    )
    assert source.taskset_row["analysis_certification_status"] == "CERTIFIED_TASKSET"
    assert target.taskset_row["analysis_certification_status"] == "CERTIFIED_TASKSET"
    assert all(row["task_failure_reason_code"] == "NONE" for row in target.task_rows)
    assert all(row["task_failure_detail"] is None for row in target.task_rows)
    assert target.dependency_rows
    assert all(
        row["source_analysis_certification_status"] == "CERTIFIED_TASKSET"
        and row["dependency_vector_check_status"] == "VALID"
        for row in target.dependency_rows
    )
    for row in target.task_rows:
        assert row["task_result_hash"] == binding.task_result_hash(row)


def test_failure_mapper_preserves_prefix_and_dependency_semantics():
    failed_input = analysis_input(e0=1, three_tasks=True)
    source = taskset.analyze_taskset_v9_3(
        "source-failed", taskset.AnalysisVariant.CW_THETA_CW, failed_input
    )
    mapped = [
        runner.map_task_failure_provenance(record, source)
        for record in source.task_records
    ]
    assert [(item.code, item.detail) for item in mapped] == [
        ("NONE", None),
        ("NO_CANDIDATE", "closure exhausted through task deadline"),
        ("UPSTREAM_PREFIX_FAILURE", None),
    ]
    target = taskset.analyze_taskset_v9_3(
        "target-na",
        taskset.AnalysisVariant.LOC_THETA_CW,
        failed_input,
        source=source,
        dependency_check_status=taskset.DependencyVectorCheckStatus.INVALID,
    )
    assert all(
        runner.map_task_failure_provenance(record, target).code
        == "DEPENDENCY_NOT_APPLICABLE"
        for record in target.task_records
    )


def test_failure_mapper_rejects_unknown_raw_text():
    result = taskset.analyze_taskset_v9_3(
        "failed", taskset.AnalysisVariant.CW_THETA_CW, analysis_input(e0=1)
    )
    bad = taskset.TaskAnalysisRecord(
        task_id="1",
        priority_rank=1,
        solver_status=taskset.TaskSolverStatus.NO_CANDIDATE,
        certification_status=taskset.TaskCertificationStatus.NOT_CERTIFIED,
        candidate_response_time=None,
        carry_in_values_used=(),
        closing_w=None,
        witness_h=None,
        checked_w_count=1,
        checked_h_count=1,
        checked_q_count=1,
        envelope_call_count=1,
        failure_reason="arbitrary exception repr at 0x7ffeee",
    )
    with pytest.raises(runner.RunnerConformanceError, match="unknown"):
        runner.map_task_failure_provenance(bad, result)
