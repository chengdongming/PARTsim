import random
from dataclasses import replace
from fractions import Fraction

import pytest

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_methods as methods
import asap_block_rta_v9_3_taskset as taskset
from experiments.v9_3 import exact_energy


def ordered(items):
    return tuple(
        item
        for _index, item in sorted(
            enumerate(items), key=lambda indexed: indexed[1].period
        )
    )


def exact_identity(items, e0, beta):
    items = ordered(items)
    required = max(item.deadline for item in items) - 1
    service = core.validate_service_curve_v9_3(
        beta, required if callable(beta) else len(beta) - 1
    )
    return exact_energy.exact_input_identity(
        task_powers=((item.name, item.power) for item in items),
        e0=core.exact_fraction_v9_3(e0, "E0"),
        service_prefix=service,
    )


def context(items, e0, beta, tag="unified", identity=None):
    return taskset.DependencyContext(
        taskset_identity="taskset-" + tag,
        task_definitions_identity="definitions-" + tag,
        priority_order_identity="priority-" + tag,
        e0_canonical_identity="e0-" + tag,
        service_curve_identity="service-" + tag,
        power_vector_identity="power-" + tag,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            taskset.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity="formal-" + tag,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=(
            exact_identity(items, e0, beta)
            if identity is None
            else identity
        ),
        float_decision_path=False,
    )


def simple_tasks():
    return (
        core.V93Task("t0", 1, 3, 4, Fraction(1)),
        core.V93Task("t1", 1, 4, 5, Fraction(2)),
        core.V93Task("t2", 1, 5, 6, Fraction(3)),
    )


def analysis_input(
    items=None,
    *,
    e0=Fraction(10_000),
    beta=None,
    processors=3,
    timeout_seconds=None,
    ctx=None,
):
    items = tuple(items or simple_tasks())
    beta = beta or tuple(
        Fraction(0) for _ in range(max(item.deadline for item in items))
    )
    return taskset.TasksetAnalysisInput(
        tasks=items,
        processors=processors,
        e0=e0,
        beta=beta,
        dependency_context=ctx or context(items, e0, beta),
        timeout_seconds=timeout_seconds,
    )


def scripted_result(kwargs, outcome):
    spec = kwargs["method_spec"]
    task = kwargs["task"]
    hp_tasks = kwargs["hp_tasks"]
    carry = kwargs["carry_in_vector"]
    energy_input = kwargs["energy_input"]
    if isinstance(outcome, int):
        status = taskset.TaskSolverStatus.CANDIDATE_FOUND
        candidate = outcome
    else:
        status = outcome
        candidate = None
    found = status is taskset.TaskSolverStatus.CANDIDATE_FOUND
    a_value = h_max = None
    sequence = ()
    witness_h = 0 if found else None
    phase_calls = impossible = None
    flow_value = None
    if found and spec.kernel in {
        methods.V93Kernel.PH,
        methods.V93Kernel.SEQ,
    }:
        a_value = core.processor_progress_v9_3(
            task,
            hp_tasks,
            candidate,
            energy_input.processors,
            carry,
        )
        h_max = candidate - a_value
        assert h_max >= 0
        phase_calls = 1
        impossible = 0
        flow_value = 1
        if spec.kernel is methods.V93Kernel.SEQ:
            sequence = (0,) * a_value
    failure_reason = None if found else status.value
    return taskset.V93KernelTaskResult(
        solver_status=status,
        kernel_solver_status="SCRIPTED_" + status.value,
        candidate_response_time=candidate,
        closing_w=candidate,
        witness_h=witness_h,
        processor_progress_a=a_value,
        maximum_blocking_h=h_max,
        witness_sequence=sequence,
        checked_w_count=1,
        checked_h_count=1,
        checked_q_count=1,
        envelope_call_count=1,
        impossible_prefix_count=impossible,
        phase_safe_calls=phase_calls,
        flow_solver_calls=flow_value,
        flow_feasible_count=flow_value,
        flow_infeasible_count=0 if flow_value is not None else None,
        z_branch_count=flow_value,
        flow_node_count=2 if flow_value is not None else None,
        flow_edge_count=3 if flow_value is not None else None,
        flow_feasibility_augmentations=(
            0 if flow_value is not None else None
        ),
        flow_optimality_cycle_cancellations=(
            0 if flow_value is not None else None
        ),
        flow_optimality_units_augmented=(
            0 if flow_value is not None else None
        ),
        failure_reason=failure_reason,
        unavailable_metrics=("cache_hit_rate",),
    )


class ScriptedDispatcher:
    def __init__(self, outcomes):
        self.outcomes = dict(outcomes)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(
            {
                "method_id": kwargs["method_spec"].method_id,
                "kernel": kwargs["method_spec"].kernel,
                "task": kwargs["task"].name,
                "carry": dict(kwargs["carry_in_vector"]),
                "timeout": kwargs["timeout_seconds"],
            }
        )
        outcome = self.outcomes[kwargs["task"].name]
        if callable(outcome):
            return outcome(kwargs)
        return scripted_result(kwargs, outcome)


def run_scripted(method_id, outcomes, *, inp=None, **kwargs):
    dispatcher = ScriptedDispatcher(outcomes)
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="unified-" + methods.V93MethodId(method_id).value,
        method_spec=method_id,
        analysis_input=inp or analysis_input(),
        kernel_dispatcher=dispatcher,
        **kwargs,
    )
    return result, dispatcher


@pytest.mark.parametrize("spec", methods.V93_METHOD_SPECS)
def test_all_eight_methods_have_real_taskset_entrypoints(spec):
    inp = analysis_input()
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="real-" + spec.method_id.value,
        method_spec=spec,
        analysis_input=inp,
    )
    assert result.method_id is spec.method_id
    assert result.kernel is spec.kernel
    assert result.carry_policy is spec.carry_policy
    assert result.taskset_proven
    assert result.analysis_certification_status is (
        taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    )
    assert result.exact_input_identity == (
        inp.dependency_context.exact_input_identity
    )
    assert all(task.solver_call_count == 1 for task in result.task_results)


@pytest.mark.parametrize(
    "method_id",
    (
        methods.V93MethodId.CW_D,
        methods.V93MethodId.LOC_D,
        methods.V93MethodId.PH_D,
        methods.V93MethodId.SEQ_D,
    ),
)
def test_fixed_d_uses_only_higher_priority_deadlines(method_id):
    result, dispatcher = run_scripted(
        method_id, {"t0": 1, "t1": 1, "t2": 1}
    )
    assert [call["carry"] for call in dispatcher.calls] == [
        {},
        {"t0": 3},
        {"t0": 3, "t1": 4},
    ]
    assert all(call["kernel"] is result.kernel for call in dispatcher.calls)
    assert result.taskset_proven


@pytest.mark.parametrize(
    "method_id",
    (
        methods.V93MethodId.CW_D,
        methods.V93MethodId.LOC_D,
        methods.V93MethodId.PH_D,
        methods.V93MethodId.SEQ_D,
    ),
)
def test_fixed_d_continues_after_ordinary_no_candidate(method_id):
    result, dispatcher = run_scripted(
        method_id,
        {
            "t0": taskset.TaskSolverStatus.NO_CANDIDATE,
            "t1": 1,
            "t2": 1,
        },
    )
    assert [call["task"] for call in dispatcher.calls] == [
        "t0",
        "t1",
        "t2",
    ]
    assert dispatcher.calls[1]["carry"] == {"t0": 3}
    assert result.solver_status is taskset.AnalysisSolverStatus.NO_CANDIDATE
    assert result.first_failed_task == "t0"
    assert result.task_results[1].solver_status is (
        taskset.TaskSolverStatus.CANDIDATE_FOUND
    )
    assert not result.taskset_proven
    assert not any(
        task.certification_status
        is taskset.TaskCertificationStatus.CERTIFIED
        for task in result.task_results
    )


@pytest.mark.parametrize(
    "method_id",
    (
        methods.V93MethodId.CW_THETA_CW,
        methods.V93MethodId.LOC_THETA_LOC,
        methods.V93MethodId.PH_THETA_PH,
        methods.V93MethodId.SEQ_THETA_SEQ,
    ),
)
def test_recursive_prefix_failure_stops_without_deadline_fallback(method_id):
    result, dispatcher = run_scripted(
        method_id,
        {
            "t0": 1,
            "t1": taskset.TaskSolverStatus.NO_CANDIDATE,
            "t2": 1,
        },
    )
    assert [call["task"] for call in dispatcher.calls] == ["t0", "t1"]
    assert dispatcher.calls[1]["carry"] == {"t0": 1}
    assert result.task_results[2].solver_status is (
        taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
    )
    assert result.task_results[2].solver_call_count == 0
    assert result.task_results[2].carry_in_values_used == ()
    assert not result.taskset_proven


def test_fixed_d_and_recursive_policy_diverge_on_same_prefix_failure():
    fixed, fixed_dispatcher = run_scripted(
        methods.V93MethodId.PH_D,
        {
            "t0": taskset.TaskSolverStatus.NO_CANDIDATE,
            "t1": 1,
            "t2": 1,
        },
    )
    recursive, recursive_dispatcher = run_scripted(
        methods.V93MethodId.PH_THETA_PH,
        {
            "t0": taskset.TaskSolverStatus.NO_CANDIDATE,
            "t1": 1,
            "t2": 1,
        },
    )
    assert len(fixed_dispatcher.calls) == 3
    assert len(recursive_dispatcher.calls) == 1
    assert fixed.task_results[1].solver_status is (
        taskset.TaskSolverStatus.CANDIDATE_FOUND
    )
    assert recursive.task_results[1].solver_status is (
        taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
    )


@pytest.mark.parametrize("method_id", tuple(methods.V93MethodId))
@pytest.mark.parametrize(
    "identity",
    ("", "not-a-sha256", "0" * 64, object()),
)
def test_bad_exact_identity_calls_no_kernel(method_id, identity):
    inp = analysis_input()
    bad = replace(
        inp,
        dependency_context=replace(
            inp.dependency_context, exact_input_identity=identity
        ),
    )
    result, dispatcher = run_scripted(
        method_id,
        {"t0": 1, "t1": 1, "t2": 1},
        inp=bad,
    )
    assert dispatcher.calls == []
    assert result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR
    assert result.task_results[0].solver_call_count == 0
    assert all(task.candidate_response_time is None for task in result.task_results)


@pytest.mark.parametrize("method_id", tuple(methods.V93MethodId))
def test_invalid_numeric_contract_calls_no_kernel(method_id):
    inp = analysis_input()
    bad = replace(
        inp,
        dependency_context=replace(
            inp.dependency_context,
            numeric_contract_sha256="0" * 64,
        ),
    )
    result, dispatcher = run_scripted(
        method_id,
        {"t0": 1, "t1": 1, "t2": 1},
        inp=bad,
    )
    assert dispatcher.calls == []
    assert result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR


@pytest.mark.parametrize("method_id", tuple(methods.V93MethodId))
@pytest.mark.parametrize(
    ("terminal", "analysis_status"),
    (
        (
            taskset.TaskSolverStatus.TIMEOUT,
            taskset.AnalysisSolverStatus.TIMEOUT,
        ),
        (
            taskset.TaskSolverStatus.NUMERIC_ERROR,
            taskset.AnalysisSolverStatus.NUMERIC_ERROR,
        ),
        (
            taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        ),
    ),
)
def test_operational_failures_stop_every_policy(
    method_id, terminal, analysis_status
):
    result, dispatcher = run_scripted(
        method_id,
        {"t0": terminal, "t1": 1, "t2": 1},
    )
    assert [call["task"] for call in dispatcher.calls] == ["t0"]
    assert result.solver_status is analysis_status
    assert all(
        task.solver_status
        is taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        for task in result.task_results[1:]
    )


@pytest.mark.parametrize("method_id", tuple(methods.V93MethodId))
def test_real_zero_timeout_propagates_without_candidate(method_id):
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="real-timeout-" + method_id.value,
        method_spec=method_id,
        analysis_input=analysis_input(timeout_seconds=0),
    )
    assert result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT
    assert result.task_results[0].solver_status is (
        taskset.TaskSolverStatus.TIMEOUT
    )
    assert result.task_results[0].candidate_response_time is None
    assert all(
        task.solver_status
        is taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        for task in result.task_results[1:]
    )


def test_dispatcher_exception_is_internal_and_stops_prefix():
    calls = []

    def raising_dispatcher(**kwargs):
        calls.append(kwargs["task"].name)
        raise RuntimeError("injected dispatcher failure")

    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="dispatcher-exception",
        method_spec=methods.V93MethodId.PH_D,
        analysis_input=analysis_input(),
        kernel_dispatcher=raising_dispatcher,
    )
    assert calls == ["t0"]
    assert result.solver_status is (
        taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    )
    assert "RuntimeError" in result.task_results[0].failure_reason


def test_timeout_value_is_shared_unchanged_with_each_kernel_call():
    inp = analysis_input(timeout_seconds=1.5)
    result, dispatcher = run_scripted(
        methods.V93MethodId.SEQ_D,
        {"t0": 1, "t1": 1, "t2": 1},
        inp=inp,
    )
    assert result.taskset_proven
    assert [call["timeout"] for call in dispatcher.calls] == [
        1.5
    ] * 3


def test_stable_rm_sort_preserves_equal_period_input_order():
    items = (
        core.V93Task("slow", 1, 3, 9, 1),
        core.V93Task("tie-b", 1, 3, 5, 1),
        core.V93Task("tie-a", 1, 3, 5, 1),
    )
    inp = analysis_input(items)
    result, dispatcher = run_scripted(
        methods.V93MethodId.CW_D,
        {"slow": 1, "tie-b": 1, "tie-a": 1},
        inp=inp,
    )
    assert [call["task"] for call in dispatcher.calls] == [
        "tie-b",
        "tie-a",
        "slow",
    ]
    assert tuple(task.task_id for task in result.task_results) == (
        "tie-b",
        "tie-a",
        "slow",
    )
    assert dispatcher.calls[1]["carry"] == {"tie-b": 3}


def test_joint_certification_is_committed_atomically():
    observed = []
    result, _dispatcher = run_scripted(
        methods.V93MethodId.SEQ_THETA_SEQ,
        {"t0": 1, "t1": 1, "t2": 1},
        finalization_observer=lambda phase, records: observed.append(
            (
                phase,
                tuple(record.certification_status for record in records),
            )
        ),
    )
    assert result.taskset_proven
    assert observed == [
        (
            "before",
            (
                taskset.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED,
            )
            * 3,
        ),
        (
            "after",
            (taskset.TaskCertificationStatus.CERTIFIED,) * 3,
        ),
    ]


LEGACY_VARIANT = {
    methods.V93MethodId.CW_THETA_CW: taskset.AnalysisVariant.CW_THETA_CW,
    methods.V93MethodId.LOC_THETA_LOC: taskset.AnalysisVariant.LOC_THETA_LOC,
    methods.V93MethodId.PH_THETA_PH: taskset.AnalysisVariant.PH_THETA_PH,
    methods.V93MethodId.SEQ_THETA_SEQ: taskset.AnalysisVariant.SEQ_THETA_SEQ,
}


@pytest.mark.parametrize("method_id", tuple(LEGACY_VARIANT))
def test_recursive_methods_agree_with_legacy_taskset_entry(method_id):
    inp = analysis_input()
    legacy = taskset.analyze_taskset_v9_3(
        "legacy-" + method_id.value,
        LEGACY_VARIANT[method_id],
        inp,
    )
    unified = taskset.analyze_method_taskset_v9_3(
        analysis_id="new-" + method_id.value,
        method_spec=method_id,
        analysis_input=inp,
    )
    assert unified.taskset_proven == legacy.taskset_proven
    assert unified.solver_status is legacy.solver_status
    assert unified.analysis_certification_status is legacy.certification_status
    assert len(unified.task_results) == len(legacy.task_records)
    for new, old in zip(unified.task_results, legacy.task_records):
        assert (
            new.task_id,
            new.priority_rank,
            new.solver_status,
            new.candidate_response_time,
            new.closing_w,
            new.witness_h,
            new.checked_w_count,
            new.checked_h_count,
            new.checked_q_count,
            new.envelope_call_count,
            new.failure_reason,
        ) == (
            old.task_id,
            old.priority_rank,
            old.solver_status,
            old.candidate_response_time,
            old.closing_w,
            old.witness_h,
            old.checked_w_count,
            old.checked_h_count,
            old.checked_q_count,
            old.envelope_call_count,
            old.failure_reason,
        )


@pytest.mark.parametrize(
    "method_id",
    (
        methods.V93MethodId.CW_D,
        methods.V93MethodId.LOC_D,
        methods.V93MethodId.PH_D,
        methods.V93MethodId.SEQ_D,
    ),
)
def test_fixed_d_matches_direct_single_task_api_for_every_task(method_id):
    inp = analysis_input()
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="fixed-direct-" + method_id.value,
        method_spec=method_id,
        analysis_input=inp,
    )
    spec = methods.method_spec_v9_3(method_id)
    for rank, observed in enumerate(result.task_results):
        hp_tasks = inp.tasks[:rank]
        expected = taskset.dispatch_single_task_method_v9_3(
            method_spec=spec,
            task=inp.tasks[rank],
            hp_tasks=hp_tasks,
            lp_tasks=inp.tasks[rank + 1 :],
            carry_in_vector={
                hp_task.name: hp_task.deadline for hp_task in hp_tasks
            },
            energy_input=inp,
            timeout_seconds=inp.timeout_seconds,
        )
        assert (
            observed.solver_status,
            observed.kernel_solver_status,
            observed.candidate_response_time,
            observed.closing_w,
            observed.witness_h,
            observed.processor_progress_a,
            observed.maximum_blocking_h,
            observed.witness_sequence,
            observed.checked_w_count,
            observed.checked_h_count,
            observed.checked_q_count,
            observed.envelope_call_count,
            observed.impossible_prefix_count,
            observed.flow_solver_calls,
            observed.flow_feasible_count,
            observed.flow_infeasible_count,
            observed.z_branch_count,
        ) == (
            expected.solver_status,
            expected.kernel_solver_status,
            expected.candidate_response_time,
            expected.closing_w,
            expected.witness_h,
            expected.processor_progress_a,
            expected.maximum_blocking_h,
            expected.witness_sequence,
            expected.checked_w_count,
            expected.checked_h_count,
            expected.checked_q_count,
            expected.envelope_call_count,
            expected.impossible_prefix_count,
            expected.flow_solver_calls,
            expected.flow_feasible_count,
            expected.flow_infeasible_count,
            expected.z_branch_count,
        )


def test_ph_result_preserves_witness_a_h_impossible_and_real_flow_counters():
    items = (
        core.V93Task("t1", 1, 7, 7, 9),
        core.V93Task("t2", 1, 1, 8, 5),
        core.V93Task("t3", 3, 11, 12, 3),
        core.V93Task("t4", 2, 11, 13, 6),
        core.V93Task("t5", 2, 15, 17, 4),
        core.V93Task("t6", 2, 17, 19, 7),
    )
    target = items[3]
    hp_tasks = items[:3]
    lp_tasks = items[4:]
    beta = lambda length: 14 * length
    inp = analysis_input(
        items,
        e0=0,
        beta=beta,
        processors=3,
    )
    result = taskset.dispatch_single_task_method_v9_3(
        method_spec=methods.method_spec_v9_3(methods.V93MethodId.PH_D),
        task=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        carry_in_vector={
            item.name: item.deadline for item in hp_tasks
        },
        energy_input=inp,
        timeout_seconds=None,
    )
    assert result.candidate_response_time == 7
    assert result.processor_progress_a == 3
    assert result.maximum_blocking_h == 4
    assert result.witness_h == 4
    assert result.impossible_prefix_count >= 0
    assert result.flow_solver_calls > 0
    assert result.z_branch_count == result.flow_solver_calls
    assert result.flow_feasible_count + result.flow_infeasible_count <= (
        result.flow_solver_calls
    )
    assert result.flow_node_count > 0
    assert result.flow_edge_count > 0
    assert "ph_stage_witness" in result.unavailable_metrics


def test_seq_result_preserves_sequence_a_h_and_mechanism_counters():
    target = core.V93Task("k", 3, 5, 8, Fraction(3, 2))
    hp_tasks = (
        core.V93Task("h0", 2, 4, 5, Fraction(1, 4)),
    )
    lp_tasks = (
        core.V93Task("l0", 1, 4, 4, Fraction(4)),
        core.V93Task("l1", 1, 1, 4, Fraction(1, 3)),
    )
    items = hp_tasks + (target,) + lp_tasks
    beta = tuple(map(Fraction, (0, 6, 6, 9, 11)))
    inp = analysis_input(
        items,
        e0=Fraction(9, 2),
        beta=beta,
        processors=3,
    )
    result = taskset.dispatch_single_task_method_v9_3(
        method_spec=methods.method_spec_v9_3(
            methods.V93MethodId.SEQ_D
        ),
        task=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        carry_in_vector={"h0": 2},
        energy_input=inp,
        timeout_seconds=None,
    )
    assert result.candidate_response_time == 4
    assert result.processor_progress_a == 3
    assert result.maximum_blocking_h == 1
    assert result.witness_sequence == (0, 0, 1)
    assert result.witness_h == 1
    assert result.phase_safe_calls == result.envelope_call_count
    assert result.impossible_prefix_count >= 0
    assert result.flow_solver_calls > 0


def bypassed_result(
    method_id=methods.V93MethodId.SEQ_THETA_SEQ, **overrides
):
    valid = scripted_result(
        {
            "method_spec": methods.method_spec_v9_3(method_id),
            "task": simple_tasks()[0],
            "hp_tasks": (),
            "carry_in_vector": {},
            "energy_input": analysis_input(),
        },
        1,
    )
    forged = object.__new__(taskset.V93KernelTaskResult)
    for field_name in valid.__dataclass_fields__:
        object.__setattr__(
            forged,
            field_name,
            overrides.get(field_name, getattr(valid, field_name)),
        )
    return forged


def test_forged_ph_witness_fails_closed_without_later_calls():
    forged = bypassed_result(
        methods.V93MethodId.PH_THETA_PH,
        witness_h=99,
    )
    dispatcher = ScriptedDispatcher(
        {"t0": lambda _kwargs: forged, "t1": 1, "t2": 1}
    )
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="forged-ph",
        method_spec=methods.V93MethodId.PH_THETA_PH,
        analysis_input=analysis_input(),
        kernel_dispatcher=dispatcher,
    )
    assert len(dispatcher.calls) == 1
    assert result.solver_status is (
        taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    )
    assert result.task_results[0].candidate_response_time is None
    assert result.task_results[0].witness_h is None


def test_forged_seq_a_h_and_sequence_fail_closed_without_later_calls():
    forged = bypassed_result(
        processor_progress_a=1,
        maximum_blocking_h=99,
        witness_sequence=(99,),
        witness_h=99,
    )
    dispatcher = ScriptedDispatcher(
        {"t0": lambda _kwargs: forged, "t1": 1, "t2": 1}
    )
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="forged-seq",
        method_spec=methods.V93MethodId.SEQ_THETA_SEQ,
        analysis_input=analysis_input(),
        kernel_dispatcher=dispatcher,
    )
    assert len(dispatcher.calls) == 1
    assert result.solver_status is (
        taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    )
    assert result.task_results[0].solver_status is (
        taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    )
    assert result.task_results[0].candidate_response_time is None
    assert result.task_results[0].witness_sequence == ()


def test_non_candidate_carrying_certificate_fails_closed():
    forged = bypassed_result(
        solver_status=taskset.TaskSolverStatus.NO_CANDIDATE,
        kernel_solver_status="FORGED_NO_CANDIDATE",
    )
    dispatcher = ScriptedDispatcher(
        {"t0": lambda _kwargs: forged, "t1": 1, "t2": 1}
    )
    result = taskset.analyze_method_taskset_v9_3(
        analysis_id="forged-noncandidate",
        method_spec=methods.V93MethodId.SEQ_THETA_SEQ,
        analysis_input=analysis_input(),
        kernel_dispatcher=dispatcher,
    )
    assert result.task_results[0].solver_status is (
        taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    )
    assert result.task_results[0].candidate_response_time is None
    assert result.task_results[0].witness_h is None


@pytest.mark.parametrize("method_id", tuple(methods.V93MethodId))
def test_candidate_at_deadline_is_valid_and_atomic(method_id):
    items = simple_tasks()
    result, _dispatcher = run_scripted(
        method_id,
        {item.name: item.deadline for item in items},
    )
    assert result.taskset_proven
    assert tuple(
        task.candidate_response_time for task in result.task_results
    ) == tuple(item.deadline for item in items)


def dominance_input(index):
    rng = random.Random(0x93D000 + index)
    items = tuple(
        core.V93Task(
            "d{}_t{}".format(index, rank),
            1,
            3 + rank,
            4 + rank,
            Fraction(rng.randint(1, 7), rng.randint(1, 4)),
        )
        for rank in range(3)
    )
    return analysis_input(items, e0=Fraction(10_000), processors=3)


@pytest.mark.parametrize(
    "method_ids",
    (
        (
            methods.V93MethodId.SEQ_D,
            methods.V93MethodId.PH_D,
            methods.V93MethodId.LOC_D,
            methods.V93MethodId.CW_D,
        ),
        (
            methods.V93MethodId.SEQ_THETA_SEQ,
            methods.V93MethodId.PH_THETA_PH,
            methods.V93MethodId.LOC_THETA_LOC,
            methods.V93MethodId.CW_THETA_CW,
        ),
    ),
)
def test_unified_adapter_dominance_chains_have_no_excluded_samples(method_ids):
    compared = violations = 0
    for index in range(12):
        inp = dominance_input(index)
        results = tuple(
            taskset.analyze_method_taskset_v9_3(
                analysis_id="dominance-{}-{}".format(index, method_id.value),
                method_spec=method_id,
                analysis_input=inp,
            )
            for method_id in method_ids
        )
        assert all(result.taskset_proven for result in results)
        for candidates in zip(
            *[
                tuple(
                    task.candidate_response_time
                    for task in result.task_results
                )
                for result in results
            ]
        ):
            compared += 1
            if not (
                candidates[0]
                <= candidates[1]
                <= candidates[2]
                <= candidates[3]
            ):
                violations += 1
            assert (
                candidates[0]
                <= candidates[1]
                <= candidates[2]
                <= candidates[3]
            )
    assert compared == 36
    assert violations == 0
