import random
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_seq as seq_core
import asap_block_rta_v9_3_taskset as ts
from experiments.v9_3 import exact_energy


SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "ASAP_BLOCK_v1_3_10_机器合同静态冻结候选包"
    / "ASAP_BLOCK_experiment_schema_v1_3_10.yaml"
)
DICTIONARY = SCHEMA.with_name("ASAP_BLOCK_data_dictionary_v1_3_10.yaml")


def context(tag="base"):
    return ts.DependencyContext(
        taskset_identity="taskset-" + tag,
        task_definitions_identity="definitions-" + tag,
        priority_order_identity="priority-" + tag,
        e0_canonical_identity="e0-" + tag,
        service_curve_identity="service-" + tag,
        power_vector_identity="power-" + tag,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=ts.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=ts.FIXED_CARRY_IN_INTERFACE_SHA256,
        formal_contract_identity="formal-" + tag,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity="exact-input-" + tag,
        float_decision_path=False,
    )


def tasks(count=3):
    return tuple(
        core.V93Task("t{}".format(i), 1, 3 + i, 4 + i, 1 + i)
        for i in range(count)
    )


def identity_for(items, e0, beta):
    required = max(item.deadline for item in items) - 1
    horizon = required if callable(beta) else len(beta) - 1
    service_prefix = core.validate_service_curve_v9_3(beta, horizon)
    return exact_energy.exact_input_identity(
        task_powers=((item.name, item.power) for item in items),
        e0=core.exact_fraction_v9_3(e0, "E0"),
        service_prefix=service_prefix,
    )


def analysis_input(items=None, ctx=None, e0=1000, beta=None):
    items = items or tasks()
    beta = beta or (lambda length: 0 if length == 0 else 1000)
    return ts.TasksetAnalysisInput(
        tasks=tuple(items),
        processors=max(1, len(items)),
        e0=e0,
        beta=beta,
        dependency_context=ctx or context(),
    )


def bind_exact_identity(inp):
    return replace(
        inp,
        dependency_context=replace(
            inp.dependency_context,
            exact_input_identity=identity_for(inp.tasks, inp.e0, inp.beta),
        ),
    )


def exact_identity_input(items=None, e0=Fraction(3), beta=None, identity=None):
    items = tuple(items or (
        core.V93Task("tie-a", 1, 3, 5, Fraction(2)),
        core.V93Task("tie-b", 1, 4, 5, Fraction(3)),
    ))
    beta = tuple(beta or (Fraction(0), Fraction(1), Fraction(2), Fraction(3), Fraction(4), Fraction(5)))
    computed = identity_for(items, e0, beta)
    ctx = replace(
        context("exact-identity"),
        exact_input_identity=computed if identity is None else identity,
    )
    return analysis_input(items, ctx=ctx, e0=e0, beta=beta), computed


def candidate(value):
    return ts.SingleTaskSolverResult(
        ts.TaskSolverStatus.CANDIDATE_FOUND,
        candidate_response_time=value,
        closing_w=value,
        witness_h=0,
        checked_w_count=1,
        checked_h_count=1,
        checked_q_count=1,
        envelope_call_count=1,
    )


def failure(status):
    return ts.SingleTaskSolverResult(status, failure_reason=status.value)


def provisional_record(**overrides):
    fields = {
        "task_id": "t0",
        "priority_rank": 0,
        "solver_status": ts.TaskSolverStatus.CANDIDATE_FOUND,
        "certification_status": ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED,
        "candidate_response_time": 1,
        "carry_in_values_used": (),
        "closing_w": 1,
        "witness_h": 0,
        "checked_w_count": 1,
        "checked_h_count": 1,
        "checked_q_count": 1,
        "envelope_call_count": 1,
    }
    fields.update(overrides)
    return ts.TaskAnalysisRecord(**fields)


class ScriptedSolver:
    def __init__(self, outcomes):
        self.outcomes = dict(outcomes)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(
            {
                "task": kwargs["task"].name,
                "carry": dict(kwargs["carry_in_vector"]),
                "window": kwargs["window_mode"],
            }
        )
        outcome = self.outcomes[kwargs["task"].name]
        return outcome(kwargs) if callable(outcome) else outcome


def run_scripted(variant, outcomes, **kwargs):
    solver = ScriptedSolver(outcomes)
    inp = kwargs.pop("input", None)
    if inp is None:
        inp = analysis_input()
        if variant in {
            ts.AnalysisVariant.PH_THETA_PH,
            ts.AnalysisVariant.SEQ_THETA_SEQ,
        }:
            inp = bind_exact_identity(inp)
    result = ts.analyze_taskset_v9_3(
        "analysis",
        variant,
        inp,
        single_task_solver=solver,
        **kwargs
    )
    return result, solver


class TestPHThetaPHDirectedRecursion:
    def test_self_recursive_carry_trace_and_atomic_joint_certification(self):
        observed = []
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {"t0": candidate(2), "t1": candidate(2), "t2": candidate(3)},
            finalization_observer=lambda phase, records: observed.append(
                (phase, tuple(record.certification_status for record in records))
            ),
        )
        assert [call["carry"] for call in solver.calls] == [
            {},
            {"t0": 2},
            {"t0": 2, "t1": 2},
        ]
        assert all(
            call["window"] is core.EnvelopeKind.LOCAL for call in solver.calls
        )
        assert result.taskset_proven
        assert result.certification_status is ts.AnalysisCertificationStatus.CERTIFIED_TASKSET
        assert observed[0][0] == "before"
        assert set(observed[0][1]) == {
            ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        }
        assert observed[1][0] == "after"
        assert set(observed[1][1]) == {ts.TaskCertificationStatus.CERTIFIED}

    def test_prefix_failure_stops_without_fallback(self):
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {
                "t0": candidate(2),
                "t1": failure(ts.TaskSolverStatus.NO_CANDIDATE),
                "t2": candidate(3),
            },
        )
        assert [call["task"] for call in solver.calls] == ["t0", "t1"]
        assert solver.calls[1]["carry"] == {"t0": 2}
        assert result.first_failed_priority == 1
        assert result.task_records[2].solver_status is (
            ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        )
        assert result.task_records[2].carry_in_values_used == ()
        assert not result.taskset_proven

    @pytest.mark.parametrize(
        "terminal",
        [ts.TaskSolverStatus.TIMEOUT, ts.TaskSolverStatus.NUMERIC_ERROR],
    )
    def test_timeout_or_numeric_prefix_failure_never_certifies_later_tasks(
        self, terminal
    ):
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {
                "t0": candidate(2),
                "t1": failure(terminal),
                "t2": candidate(3),
            },
        )
        assert [call["task"] for call in solver.calls] == ["t0", "t1"]
        assert result.task_records[2].solver_status is (
            ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        )
        assert not result.taskset_proven
        assert result.solver_status is {
            ts.TaskSolverStatus.TIMEOUT: ts.AnalysisSolverStatus.TIMEOUT,
            ts.TaskSolverStatus.NUMERIC_ERROR: ts.AnalysisSolverStatus.NUMERIC_ERROR,
        }[terminal]

    def test_ph_rejects_non_rm_input_order(self):
        items = (
            core.V93Task("slow", 1, 3, 7, 1),
            core.V93Task("fast", 1, 3, 4, 1),
        )
        with pytest.raises(ts.CertificationError, match="RM period order"):
            ts.analyze_taskset_v9_3(
                "ph-order",
                ts.AnalysisVariant.PH_THETA_PH,
                analysis_input(items),
            )

    def test_real_recursive_ph_candidates_do_not_exceed_recursive_loc(self):
        inp = bind_exact_identity(analysis_input())
        loc = ts.analyze_taskset_v9_3(
            "loc-recursive", ts.AnalysisVariant.LOC_THETA_LOC, inp
        )
        phase = ts.analyze_taskset_v9_3(
            "ph-recursive", ts.AnalysisVariant.PH_THETA_PH, inp
        )
        assert loc.taskset_proven and phase.taskset_proven
        loc_vector = {
            record.task_id: record.candidate_response_time
            for record in loc.task_records
        }
        phase_vector = {
            record.task_id: record.candidate_response_time
            for record in phase.task_records
        }
        assert phase_vector.keys() == loc_vector.keys()
        assert all(
            phase_vector[task_id] <= loc_vector[task_id]
            for task_id in phase_vector
        )

    def test_correct_exact_input_identity_is_accepted(self):
        inp, identity = exact_identity_input()
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {item.name: candidate(item.wcet) for item in inp.tasks},
            input=inp,
        )
        assert inp.dependency_context.exact_input_identity == identity
        assert len(solver.calls) == len(inp.tasks)
        assert result.taskset_proven

    @pytest.mark.parametrize(
        "mutation",
        [
            "nonempty-zero",
            "other-power",
            "other-e0",
            "other-beta",
            "other-order",
            "missing",
            "malformed",
        ],
    )
    def test_wrong_exact_input_identity_fails_closed_before_ph_solver(
        self, mutation
    ):
        inp, correct = exact_identity_input()
        items = inp.tasks
        beta = inp.beta
        wrong = {
            "nonempty-zero": "0" * 64,
            "other-power": exact_energy.exact_input_identity(
                task_powers=((items[0].name, items[0].power + 1), (items[1].name, items[1].power)),
                e0=inp.e0,
                service_prefix=beta,
            ),
            "other-e0": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in items),
                e0=inp.e0 + 1,
                service_prefix=beta,
            ),
            "other-beta": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in items),
                e0=inp.e0,
                service_prefix=beta[:-1] + (beta[-1] + 1,),
            ),
            "other-order": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in reversed(items)),
                e0=inp.e0,
                service_prefix=beta,
            ),
            "missing": "",
            "malformed": "not-a-sha256",
        }[mutation]
        assert wrong != correct
        bad_context = replace(inp.dependency_context, exact_input_identity=wrong)
        bad_input = replace(inp, dependency_context=bad_context)
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {item.name: candidate(item.wcet) for item in items},
            input=bad_input,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NUMERIC_ERROR
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_CERTIFIED
        assert result.n_tasks_candidate_found == 0
        assert not result.taskset_proven

    def test_full_service_prefix_tail_is_bound_by_exact_identity(self):
        inp, original = exact_identity_input()
        changed_beta = inp.beta[:-1] + (inp.beta[-1] + 1,)
        changed = identity_for(inp.tasks, inp.e0, changed_beta)
        assert changed != original
        stale = replace(inp, beta=changed_beta)
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {item.name: candidate(item.wcet) for item in inp.tasks},
            input=stale,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NUMERIC_ERROR

    def test_direct_analyzer_accepts_execution_engine_canonical_identity(self):
        from experiments.v9_3 import execution_engine

        inp, expected = exact_identity_input()
        stored = SimpleNamespace(
            tasks=inp.tasks,
            semantic_hash="a" * 64,
            task_payload=[{"task_id": item.name} for item in inp.tasks],
            priority_hash="b" * 64,
            power_hash="c" * 64,
        )
        cell = SimpleNamespace(exact_e0=inp.e0, numerical_mode="EXACT_RATIONAL")
        service = SimpleNamespace(values=inp.beta, identity="d" * 64)
        produced = execution_engine._dependency_context(stored, cell, service)
        assert produced.exact_input_identity == expected
        direct_input = replace(inp, dependency_context=produced)
        result, solver = run_scripted(
            ts.AnalysisVariant.PH_THETA_PH,
            {item.name: candidate(item.wcet) for item in inp.tasks},
            input=direct_input,
        )
        assert len(solver.calls) == len(inp.tasks)
        assert result.taskset_proven

    def test_same_period_order_and_task_id_are_bound_but_ctd_are_not_numeric_material(self):
        inp, original = exact_identity_input()
        reversed_identity = identity_for(tuple(reversed(inp.tasks)), inp.e0, inp.beta)
        renamed = (
            replace(inp.tasks[0], name="renamed"),
            inp.tasks[1],
        )
        renamed_identity = identity_for(renamed, inp.e0, inp.beta)
        ctd_changed = (
            replace(inp.tasks[0], deadline=4, period=5),
            inp.tasks[1],
        )
        ctd_identity = identity_for(ctd_changed, inp.e0, inp.beta)
        assert reversed_identity != original
        assert renamed_identity != original
        assert ctd_identity == original
        assert inp.dependency_context.task_definitions_identity
        assert inp.dependency_context.priority_order_identity


class TestSEQThetaSEQDirectedRecursion:
    def test_self_recursive_carry_trace_and_atomic_joint_certification(self):
        observed = []
        result, solver = run_scripted(
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            {"t0": candidate(2), "t1": candidate(2), "t2": candidate(3)},
            finalization_observer=lambda phase, records: observed.append(
                (phase, tuple(record.certification_status for record in records))
            ),
        )
        assert [call["carry"] for call in solver.calls] == [
            {},
            {"t0": 2},
            {"t0": 2, "t1": 2},
        ]
        assert all(
            call["window"] is core.EnvelopeKind.LOCAL for call in solver.calls
        )
        assert result.taskset_proven
        assert result.method_role is ts.AnalysisMethodRole.DIAGNOSTIC
        assert observed[0][0] == "before"
        assert set(observed[0][1]) == {
            ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        }
        assert observed[1][0] == "after"
        assert set(observed[1][1]) == {ts.TaskCertificationStatus.CERTIFIED}

    def test_middle_prefix_failure_stops_without_fallback(self):
        result, solver = run_scripted(
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            {
                "t0": candidate(2),
                "t1": failure(ts.TaskSolverStatus.NO_CANDIDATE),
                "t2": candidate(3),
            },
        )
        assert [call["task"] for call in solver.calls] == ["t0", "t1"]
        assert solver.calls[1]["carry"] == {"t0": 2}
        assert result.first_failed_priority == 1
        assert result.task_records[2].solver_status is (
            ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        )
        assert result.task_records[2].carry_in_values_used == ()
        assert not result.taskset_proven

    def test_spoofed_closure_certificate_stops_taskset_prefix(self, monkeypatch):
        original_response = seq_core.seq_response_time_v9_3
        closure = object.__new__(seq_core.SEQClosureResult)
        forged_fields = {
            "status": seq_core.SEQClosureStatus.CLOSED,
            "witness_sequence": (99,),
            "witness_h": 99,
            "processor_progress_a": 1,
            "maximum_blocking_h": 99,
            "checked_h_count": 1,
            "checked_q_count": 1,
            "envelope_call_count": 1,
            "impossible_prefix_count": 0,
            "failure_reason": None,
        }
        for name, value in forged_fields.items():
            object.__setattr__(closure, name, value)

        def spoofed_response(**kwargs):
            return original_response(
                **kwargs, _closure_checker=lambda **_closure_kwargs: closure
            )

        monkeypatch.setattr(seq_core, "seq_response_time_v9_3", spoofed_response)
        inp = bind_exact_identity(analysis_input())
        result = ts.analyze_taskset_v9_3(
            "spoofed-seq-certificate",
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            inp,
        )
        assert result.solver_status is (
            ts.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        )
        assert result.task_records[0].solver_status is (
            ts.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        )
        assert all(
            record.solver_status
            is ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
            for record in result.task_records[1:]
        )
        assert not result.taskset_proven

    def test_candidate_construction_timeout_stops_taskset_prefix(self, monkeypatch):
        class ArmableClock:
            def __init__(self):
                self.armed = False

            def __call__(self):
                return 1 if self.armed else 0

        clock = ArmableClock()
        original_post_init = seq_core.SEQSearchResult.__post_init__
        original_response = seq_core.seq_response_time_v9_3

        def wrapped_post_init(search_result):
            original_post_init(search_result)
            if search_result.solver_status is seq_core.SEQSearchStatus.CANDIDATE:
                clock.armed = True

        def response_with_clock(**kwargs):
            return original_response(**kwargs, clock=clock)

        monkeypatch.setattr(
            seq_core.SEQSearchResult, "__post_init__", wrapped_post_init
        )
        monkeypatch.setattr(
            seq_core, "seq_response_time_v9_3", response_with_clock
        )
        inp = bind_exact_identity(
            replace(analysis_input(), timeout_seconds=1)
        )
        result = ts.analyze_taskset_v9_3(
            "candidate-construction-timeout",
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            inp,
        )
        assert result.solver_status is ts.AnalysisSolverStatus.TIMEOUT
        assert result.task_records[0].solver_status is ts.TaskSolverStatus.TIMEOUT
        assert result.task_records[0].candidate_response_time is None
        assert all(
            record.solver_status
            is ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
            for record in result.task_records[1:]
        )
        assert result.certification_status is (
            ts.AnalysisCertificationStatus.NOT_CERTIFIED
        )
        assert not result.taskset_proven

    def test_period_decrease_is_rejected(self):
        items = (
            core.V93Task("slow", 1, 3, 7, 1),
            core.V93Task("fast", 1, 3, 4, 1),
        )
        with pytest.raises(ts.CertificationError, match="RM period order"):
            ts.analyze_taskset_v9_3(
                "seq-order",
                ts.AnalysisVariant.SEQ_THETA_SEQ,
                analysis_input(items),
            )

    def test_same_period_preserves_caller_priority_order(self):
        inp, _identity = exact_identity_input()
        result, solver = run_scripted(
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            {item.name: candidate(item.wcet) for item in inp.tasks},
            input=inp,
        )
        assert [call["task"] for call in solver.calls] == ["tie-a", "tie-b"]
        assert result.taskset_proven

    def test_duplicate_task_id_is_rejected_before_analysis(self):
        duplicate = (
            core.V93Task("same", 1, 2, 3, 1),
            core.V93Task("same", 1, 3, 4, 2),
        )
        with pytest.raises(ts.CertificationError, match="unique"):
            analysis_input(duplicate)

    def test_correct_exact_input_identity_is_accepted(self):
        inp, identity = exact_identity_input()
        result, solver = run_scripted(
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            {item.name: candidate(item.wcet) for item in inp.tasks},
            input=inp,
        )
        assert inp.dependency_context.exact_input_identity == identity
        assert len(solver.calls) == len(inp.tasks)
        assert result.taskset_proven

    @pytest.mark.parametrize(
        "mutation",
        [
            "nonempty-zero",
            "other-power",
            "other-e0",
            "other-beta",
            "other-order",
            "missing",
            "non-hex",
            "wrong-length",
        ],
    )
    def test_wrong_exact_identity_fails_before_seq_solver(self, mutation):
        inp, correct = exact_identity_input()
        items = inp.tasks
        wrong = {
            "nonempty-zero": "0" * 64,
            "other-power": exact_energy.exact_input_identity(
                task_powers=(
                    (items[0].name, items[0].power + 1),
                    (items[1].name, items[1].power),
                ),
                e0=inp.e0,
                service_prefix=inp.beta,
            ),
            "other-e0": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in items),
                e0=inp.e0 + 1,
                service_prefix=inp.beta,
            ),
            "other-beta": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in items),
                e0=inp.e0,
                service_prefix=inp.beta[:-1] + (inp.beta[-1] + 1,),
            ),
            "other-order": exact_energy.exact_input_identity(
                task_powers=((item.name, item.power) for item in reversed(items)),
                e0=inp.e0,
                service_prefix=inp.beta,
            ),
            "missing": "",
            "non-hex": "g" * 64,
            "wrong-length": "a" * 63,
        }[mutation]
        assert wrong != correct
        bad_input = replace(
            inp,
            dependency_context=replace(
                inp.dependency_context, exact_input_identity=wrong
            ),
        )
        result, solver = run_scripted(
            ts.AnalysisVariant.SEQ_THETA_SEQ,
            {item.name: candidate(item.wcet) for item in items},
            input=bad_input,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NUMERIC_ERROR
        assert not result.taskset_proven
        assert result.n_tasks_candidate_found == 0

    @pytest.mark.parametrize(
        ("seq_status", "task_status"),
        [
            (seq_core.SEQSearchStatus.CANDIDATE, ts.TaskSolverStatus.CANDIDATE_FOUND),
            (seq_core.SEQSearchStatus.NO_CANDIDATE, ts.TaskSolverStatus.NO_CANDIDATE),
            (seq_core.SEQSearchStatus.UNPROVEN_TIMEOUT, ts.TaskSolverStatus.TIMEOUT),
            (seq_core.SEQSearchStatus.UNPROVEN_NUMERIC, ts.TaskSolverStatus.NUMERIC_ERROR),
            (
                seq_core.SEQSearchStatus.UNPROVEN_INTERNAL,
                ts.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            ),
        ],
    )
    def test_single_task_adapter_maps_every_seq_terminal(
        self, monkeypatch, seq_status, task_status
    ):
        success = seq_status is seq_core.SEQSearchStatus.CANDIDATE
        fake = seq_core.SEQSearchResult(
            solver_status=seq_status,
            candidate_response_time=1 if success else None,
            closing_w=1 if success else None,
            processor_progress_a=1 if success else None,
            maximum_blocking_h=0 if success else None,
            witness_sequence=(0,) if success else (),
            witness_h=0 if success else None,
            checked_w_count=1,
            checked_h_count=2,
            checked_q_count=3,
            envelope_call_count=2,
            impossible_prefix_count=1,
            failure_reason=None if success else seq_status.value,
        )
        monkeypatch.setattr(seq_core, "seq_response_time_v9_3", lambda **_kwargs: fake)
        inp = bind_exact_identity(analysis_input((tasks(1)[0],)))
        actual = ts.solve_single_task_seq_v9_3(
            task=inp.tasks[0],
            hp_tasks=(),
            lp_tasks=(),
            carry_in_vector={},
            window_mode=core.EnvelopeKind.LOCAL,
            energy_input=inp,
            timeout_seconds=None,
        )
        assert actual.solver_status is task_status
        assert actual.candidate_response_time == (1 if success else None)
        assert actual.witness_h == (0 if success else None)

    def test_single_task_adapter_rejects_bypassed_malformed_search_certificate(
        self, monkeypatch
    ):
        fake = object.__new__(seq_core.SEQSearchResult)
        fields = {
            "solver_status": seq_core.SEQSearchStatus.CANDIDATE,
            "candidate_response_time": 1,
            "closing_w": 1,
            "processor_progress_a": 1,
            "maximum_blocking_h": 99,
            "witness_sequence": (99,),
            "witness_h": 99,
            "checked_w_count": 1,
            "checked_h_count": 1,
            "checked_q_count": 1,
            "envelope_call_count": 1,
            "impossible_prefix_count": 0,
            "failure_reason": None,
        }
        for name, value in fields.items():
            object.__setattr__(fake, name, value)
        monkeypatch.setattr(
            seq_core, "seq_response_time_v9_3", lambda **_kwargs: fake
        )
        inp = bind_exact_identity(analysis_input((tasks(1)[0],)))
        actual = ts.solve_single_task_seq_v9_3(
            task=inp.tasks[0],
            hp_tasks=(),
            lp_tasks=(),
            carry_in_vector={},
            window_mode=core.EnvelopeKind.LOCAL,
            energy_input=inp,
            timeout_seconds=None,
        )
        assert actual.solver_status is (
            ts.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        )
        assert actual.candidate_response_time is None
        assert "malformed SEQ search certificate" in actual.failure_reason


def certified_cw(ctx=None, values=None):
    values = values or {"t0": 2, "t1": 2, "t2": 2}
    result, _ = run_scripted(
        ts.AnalysisVariant.CW_THETA_CW,
        {name: candidate(value) for name, value in values.items()},
        input=analysis_input(ctx=ctx or context()),
    )
    return result


class TestMethodRolesAndSchema:
    def test_formal_five_roles_ph_direct_role_and_exact_main_method_set(self):
        assert ts.ROLE_BY_VARIANT == {
            ts.AnalysisVariant.CW_D: ts.AnalysisMethodRole.AUXILIARY_ABLATION,
            ts.AnalysisVariant.LOC_D: ts.AnalysisMethodRole.AUXILIARY_ABLATION,
            ts.AnalysisVariant.CW_THETA_CW: ts.AnalysisMethodRole.MAIN_METHOD,
            ts.AnalysisVariant.LOC_THETA_CW: ts.AnalysisMethodRole.AUXILIARY_ABLATION,
            ts.AnalysisVariant.LOC_THETA_LOC: ts.AnalysisMethodRole.MAIN_METHOD,
            ts.AnalysisVariant.PH_THETA_PH: ts.AnalysisMethodRole.DIAGNOSTIC,
            ts.AnalysisVariant.SEQ_THETA_SEQ: ts.AnalysisMethodRole.DIAGNOSTIC,
        }
        assert ts.MAIN_METHOD_VARIANTS == {
            ts.AnalysisVariant.CW_THETA_CW,
            ts.AnalysisVariant.LOC_THETA_LOC,
        }
        assert ts.AnalysisVariant.PH_THETA_PH not in ts.MAIN_METHOD_VARIANTS
        assert ts.AnalysisVariant.SEQ_THETA_SEQ not in ts.MAIN_METHOD_VARIANTS

    def test_schema_and_dictionary_enum_synchronization(self):
        schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
        dictionary = yaml.safe_load(DICTIONARY.read_text(encoding="utf-8"))
        pairs = {
            "analysis_variant": ts.AnalysisVariant,
            "analysis_method_role": ts.AnalysisMethodRole,
            "task_solver_status": ts.TaskSolverStatus,
            "task_certification_status": ts.TaskCertificationStatus,
            "analysis_solver_status": ts.AnalysisSolverStatus,
            "analysis_certification_status": ts.AnalysisCertificationStatus,
            "dependency_vector_check_status": ts.DependencyVectorCheckStatus,
            "dominance_invariant_status": ts.DominanceInvariantStatus,
            "fixed_carry_in_corollary_status": ts.FixedCarryInInterfaceStatus,
        }
        for schema_name, enum_type in pairs.items():
            expected = {
                member.value for member in enum_type
            }
            if enum_type is ts.AnalysisVariant:
                expected.remove(ts.AnalysisVariant.PH_THETA_PH.value)
                expected.remove(ts.AnalysisVariant.SEQ_THETA_SEQ.value)
            assert set(schema["enums"][schema_name]) == expected
        assert (
            ts.AnalysisVariant.PH_THETA_PH.value
            not in schema["enums"]["analysis_variant"]
        )
        assert (
            ts.AnalysisVariant.SEQ_THETA_SEQ.value
            not in schema["enums"]["analysis_variant"]
        )
        enum_refs = {
            spec.get("enum_ref")
            for table in dictionary["tables"].values()
            for spec in table["fields"].values()
            if spec.get("enum_ref")
        }
        assert set(pairs) <= enum_refs | {"analysis_variant", "analysis_method_role"}
        assert dictionary["data_dictionary_metadata"]["schema_file"] == SCHEMA.name
        assert "TASK_LEVEL_CERTIFIED_ONLY" not in str(schema)
        assert "TASK_LEVEL_AUXILIARY" not in str(schema)
        assert "NOT_ACTIVE" not in schema["enums"]["fixed_carry_in_corollary_status"]
        semantics = schema["tables"]["per_taskset_results.csv"]["constraints"]
        assert "LOC_THETA_CW_COMPLETE_VECTOR_REQUIRES_JOINT_CERTIFICATION" in semantics


class TestSingleTaskAdapter:
    def test_real_core_end_to_end_returns_candidate_without_certification(self):
        item = core.V93Task("k", 1, 2, 3, 1)
        inp = analysis_input((item,), context("real"))
        result = ts.solve_single_task_v9_3(
            task=item,
            hp_tasks=(),
            lp_tasks=(),
            carry_in_vector={},
            window_mode=core.EnvelopeKind.COMPLETE,
            energy_input=inp,
            timeout_seconds=None,
        )
        assert result.solver_status is ts.TaskSolverStatus.CANDIDATE_FOUND
        assert result.candidate_response_time is not None
        assert not hasattr(result, "certification_status")


    @pytest.mark.parametrize(
        "core_status, expected",
        [
            (core.V93SolverStatus.CANDIDATE, ts.TaskSolverStatus.CANDIDATE_FOUND),
            (core.V93SolverStatus.NO_CANDIDATE, ts.TaskSolverStatus.NO_CANDIDATE),
            (core.V93SolverStatus.UNPROVEN_TIMEOUT, ts.TaskSolverStatus.TIMEOUT),
            (core.V93SolverStatus.UNPROVEN_NUMERIC, ts.TaskSolverStatus.NUMERIC_ERROR),
            (core.V93SolverStatus.UNPROVEN_OVERFLOW, ts.TaskSolverStatus.NUMERIC_ERROR),
            (object(), ts.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE),
        ],
    )
    def test_exhaustive_status_mapping_and_unknown_fail_closed(
        self, monkeypatch, core_status, expected
    ):
        value = 1 if core_status is core.V93SolverStatus.CANDIDATE else None
        monkeypatch.setattr(
            core,
            "canonical_closure_search_v9_3",
            lambda *args, **kwargs: SimpleNamespace(
                solver_status=core_status,
                candidate_response_time=value,
                closing_w=value,
                witness_h=0 if value else None,
                checked_w_count=0,
                checked_h_count=0,
                checked_q_count=0,
                envelope_call_count=0,
                failure_reason=None,
            ),
        )
        item = core.V93Task("k", 1, 2, 3, 1)
        result = ts.solve_single_task_v9_3(
            task=item,
            hp_tasks=(),
            lp_tasks=(),
            carry_in_vector={},
            window_mode=core.EnvelopeKind.COMPLETE,
            energy_input=analysis_input((item,), context("map")),
            timeout_seconds=None,
        )
        assert result.solver_status is expected

    def test_unexpected_exception_is_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            core,
            "canonical_closure_search_v9_3",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        item = core.V93Task("k", 1, 2, 3, 1)
        with pytest.raises(RuntimeError, match="boom"):
            ts.solve_single_task_v9_3(
                task=item,
                hp_tasks=(),
                lp_tasks=(),
                carry_in_vector={},
                window_mode=core.EnvelopeKind.COMPLETE,
                energy_input=analysis_input((item,), context("exception")),
                timeout_seconds=None,
            )


class TestPlainIntegerContract:
    @pytest.mark.parametrize(
        "overrides,field",
        (
            ({"candidate_response_time": True, "closing_w": 1}, "candidate_response_time"),
            ({"candidate_response_time": 1, "closing_w": True}, "closing_w"),
            ({"witness_h": True}, "witness_h"),
            ({"checked_w_count": True}, "checked_w_count"),
        ),
    )
    def test_single_task_result_rejects_bool_scientific_integer(
        self, overrides, field
    ):
        values = {
            "solver_status": ts.TaskSolverStatus.CANDIDATE_FOUND,
            "candidate_response_time": 1,
            "closing_w": 1,
            "witness_h": 0,
            "checked_w_count": 0,
            "checked_h_count": 0,
            "checked_q_count": 0,
            "envelope_call_count": 0,
        }
        values.update(overrides)
        with pytest.raises(ts.CertificationError, match=field):
            ts.SingleTaskSolverResult(**values)

    @pytest.mark.parametrize(
        "overrides,field",
        (
            ({"priority_rank": True}, "priority_rank"),
            ({"candidate_response_time": True, "closing_w": 1}, "candidate_response_time"),
            ({"closing_w": True}, "closing_w"),
            ({"witness_h": True}, "witness_h"),
            ({"checked_q_count": True}, "checked_q_count"),
            ({"carry_in_values_used": (("t0", True),)}, "carry_in_values_used"),
        ),
    )
    def test_task_record_rejects_bool_scientific_integer(
        self, overrides, field
    ):
        with pytest.raises(ts.CertificationError, match=field):
            provisional_record(**overrides)

    @pytest.mark.parametrize(
        "field",
        (
            "n_tasks_total",
            "n_tasks_evaluated",
            "n_tasks_candidate_found",
            "n_tasks_certified",
        ),
    )
    def test_taskset_result_rejects_bool_counter(self, field):
        result, _solver = run_scripted(
            ts.AnalysisVariant.CW_THETA_CW,
            {"t0": candidate(1)},
            input=analysis_input(tasks(1)),
        )
        with pytest.raises(ts.CertificationError, match=field):
            replace(result, **{field: True})

    def test_taskset_result_rejects_bool_first_failed_priority(self):
        result, _solver = run_scripted(
            ts.AnalysisVariant.CW_THETA_CW,
            {"t0": failure(ts.TaskSolverStatus.NO_CANDIDATE)},
            input=analysis_input(tasks(1)),
        )
        with pytest.raises(ts.CertificationError, match="first_failed_priority"):
            replace(result, first_failed_priority=False)

    @pytest.mark.parametrize(
        "field",
        (
            "priority_rank",
            "source_candidate",
            "local_candidate",
            "checked_h_count",
        ),
    )
    def test_dominance_counterexample_rejects_bool_integer(self, field):
        values = {
            "task_id": "t0",
            "priority_rank": 0,
            "source_candidate": 1,
            "local_candidate": 1,
            "carry_in_vector": (("t0", 1),),
            "checked_w_count": 0,
            "checked_h_count": 0,
            "checked_q_count": 0,
            "envelope_call_count": 0,
        }
        values[field] = True
        with pytest.raises(ts.CertificationError, match=field):
            ts.DominanceCounterexample(**values)

    def test_direct_analyzer_rejects_bool_candidate_and_closing(self):
        def bool_solver(**_kwargs):
            return ts.SingleTaskSolverResult(
                ts.TaskSolverStatus.CANDIDATE_FOUND,
                candidate_response_time=True,
                closing_w=True,
                witness_h=0,
            )

        with pytest.raises(ts.CertificationError, match="candidate_response_time"):
            ts.analyze_taskset_v9_3(
                "bool-direct",
                ts.AnalysisVariant.CW_THETA_CW,
                analysis_input(tasks(1)),
                single_task_solver=bool_solver,
            )

    @pytest.mark.parametrize(
        "mutation,field",
        (
            ("candidate", "candidate_response_time"),
            ("closing", "closing_w"),
            ("witness", "witness_h"),
            ("priority", "priority_rank"),
            ("checked", "checked_q_count"),
            ("taskset_counter", "n_tasks_evaluated"),
            ("carry", "carry_in_values_used"),
            ("source_vector", "source_candidate_vector"),
        ),
    )
    def test_loc_theta_cw_rejects_bool_source_before_solver_call(
        self, mutation, field
    ):
        source = certified_cw()
        records = list(source.task_records)
        if mutation == "candidate":
            object.__setattr__(records[0], "candidate_response_time", True)
        elif mutation == "closing":
            object.__setattr__(records[0], "closing_w", True)
        elif mutation == "witness":
            object.__setattr__(records[0], "witness_h", True)
        elif mutation == "priority":
            object.__setattr__(records[0], "priority_rank", True)
        elif mutation == "checked":
            object.__setattr__(records[0], "checked_q_count", True)
        elif mutation == "taskset_counter":
            object.__setattr__(source, "n_tasks_evaluated", True)
        elif mutation == "carry":
            object.__setattr__(
                records[1], "carry_in_values_used", (("t0", True),)
            )
        elif mutation == "source_vector":
            object.__setattr__(
                source, "source_candidate_vector", (("t0", True),)
            )
        else:
            raise AssertionError(mutation)
        object.__setattr__(source, "task_records", tuple(records))

        calls = []

        def counting_solver(**_kwargs):
            calls.append(1)
            return candidate(1)

        error = None
        try:
            ts.analyze_taskset_v9_3(
                "bool-source-direct",
                ts.AnalysisVariant.LOC_THETA_CW,
                analysis_input(),
                source=source,
                dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
                single_task_solver=counting_solver,
            )
        except ts.CertificationError as exc:
            error = exc
        assert error is not None, (
            f"{field} bool source was accepted; solver_calls={len(calls)}"
        )
        assert field in str(error)
        assert calls == []

    def test_plain_integer_zero_and_one_remain_valid(self):
        result = ts.SingleTaskSolverResult(
            ts.TaskSolverStatus.CANDIDATE_FOUND,
            candidate_response_time=1,
            closing_w=1,
            witness_h=0,
            checked_w_count=0,
            checked_h_count=1,
            checked_q_count=0,
            envelope_call_count=1,
        )
        assert result.candidate_response_time == 1
        assert result.witness_h == 0


@pytest.mark.parametrize(
    "variant, window",
    [
        (ts.AnalysisVariant.CW_THETA_CW, core.EnvelopeKind.COMPLETE),
        (ts.AnalysisVariant.LOC_THETA_LOC, core.EnvelopeKind.LOCAL),
    ],
)
class TestRecursiveStateMachine:
    def test_all_success_jointly_certifies_atomically(self, variant, window):
        observations = []
        result, solver = run_scripted(
            variant,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            finalization_observer=lambda stage, records: observations.append(
                (stage, tuple(record.certification_status for record in records))
            ),
        )
        assert [call["window"] for call in solver.calls] == [window] * 3
        assert solver.calls[0]["carry"] == {}
        assert solver.calls[1]["carry"] == {"t0": 1}
        assert solver.calls[2]["carry"] == {"t0": 1, "t1": 1}
        assert observations == [
            (
                "before",
                (ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED,) * 3,
            ),
            ("after", (ts.TaskCertificationStatus.CERTIFIED,) * 3),
        ]
        assert result.certification_status is ts.AnalysisCertificationStatus.CERTIFIED_TASKSET
        assert result.taskset_proven
        assert result.n_tasks_certified == result.n_tasks_total == 3

    @pytest.mark.parametrize(
        "failed_status, analysis_status",
        [
            (ts.TaskSolverStatus.NO_CANDIDATE, ts.AnalysisSolverStatus.NO_CANDIDATE),
            (ts.TaskSolverStatus.TIMEOUT, ts.AnalysisSolverStatus.TIMEOUT),
            (ts.TaskSolverStatus.NUMERIC_ERROR, ts.AnalysisSolverStatus.NUMERIC_ERROR),
        ],
    )
    def test_middle_failure_preserves_provisional_prefix(
        self, variant, window, failed_status, analysis_status
    ):
        result, solver = run_scripted(
            variant,
            {
                "t0": candidate(1),
                "t1": failure(failed_status),
                "t2": candidate(1),
            },
        )
        assert [call["task"] for call in solver.calls] == ["t0", "t1"]
        assert result.solver_status is analysis_status
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_CERTIFIED
        assert not result.taskset_proven
        assert result.first_failed_priority == 1
        assert result.task_records[0].certification_status is ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        assert result.task_records[1].certification_status is ts.TaskCertificationStatus.NOT_CERTIFIED
        assert result.task_records[2].solver_status is ts.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
        assert result.task_records[2].certification_status is ts.TaskCertificationStatus.NOT_APPLICABLE


@pytest.mark.parametrize(
    "variant, window",
    [
        (ts.AnalysisVariant.CW_D, core.EnvelopeKind.COMPLETE),
        (ts.AnalysisVariant.LOC_D, core.EnvelopeKind.LOCAL),
    ],
)
class TestDeadlineCarryIn:
    def test_active_interface_uses_frozen_deadlines_and_certifies(self, variant, window):
        result, solver = run_scripted(
            variant,
            {"t0": candidate(1), "t1": candidate(2), "t2": candidate(2)},
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
        )
        deadlines = {task.name: task.deadline for task in tasks()}
        assert all(call["carry"] == deadlines for call in solver.calls)
        assert all(call["window"] is window for call in solver.calls)
        assert result.taskset_proven
        assert all(
            record.candidate_response_time <= deadlines[record.task_id]
            for record in result.task_records
        )

    def test_middle_failure_never_certifies_prefix(self, variant, window):
        result, _ = run_scripted(
            variant,
            {
                "t0": candidate(1),
                "t1": failure(ts.TaskSolverStatus.NO_CANDIDATE),
                "t2": candidate(1),
            },
        )
        assert result.task_records[0].certification_status is ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        assert result.n_tasks_certified == 0

    def test_inactive_formal_is_not_applicable(self, variant, window):
        result, solver = run_scripted(
            variant,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.HASH_MISMATCH,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_APPLICABLE

    def test_active_claim_with_wrong_hash_is_derived_as_hash_mismatch(self, variant, window):
        bad_context = replace(
            context("bad-interface"),
            fixed_carry_in_interface_sha256="0" * 64,
        )
        result, solver = run_scripted(
            variant,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            input=analysis_input(ctx=bad_context),
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
        )
        assert solver.calls == []
        assert result.fixed_carry_in_interface_status is ts.FixedCarryInInterfaceStatus.HASH_MISMATCH

    def test_explicit_diagnostic_never_certifies(self, variant, window):
        result, _ = run_scripted(
            variant,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.HASH_MISMATCH,
            diagnostic_mode=True,
        )
        assert result.certification_status is ts.AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
        assert not result.taskset_proven
        assert all(
            record.certification_status is ts.TaskCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
            for record in result.task_records
        )


class TestLocThetaCw:
    def test_positive_frozen_source_and_joint_certification(self):
        source = certified_cw()
        source_snapshot = repr(source)
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {"t0": candidate(1), "t1": candidate(1), "t2": candidate(2)},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
        )
        frozen = {"t0": 2, "t1": 2, "t2": 2}
        assert all(call["carry"] == frozen for call in solver.calls)
        assert repr(source) == source_snapshot
        assert result.certification_status is ts.AnalysisCertificationStatus.CERTIFIED_TASKSET
        assert result.dominance_invariant_status is ts.DominanceInvariantStatus.SATISFIED
        assert result.source_candidate_vector == tuple(sorted(frozen.items()))

    @pytest.mark.parametrize("source_kind", ["wrong_variant", "failed", "diagnostic"])
    def test_invalid_source_is_not_applicable(self, source_kind):
        if source_kind == "wrong_variant":
            source, _ = run_scripted(
                ts.AnalysisVariant.CW_D,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
            )
        elif source_kind == "failed":
            source, _ = run_scripted(
                ts.AnalysisVariant.CW_THETA_CW,
                {
                    "t0": candidate(1),
                    "t1": failure(ts.TaskSolverStatus.NO_CANDIDATE),
                    "t2": candidate(1),
                },
            )
        else:
            source, _ = run_scripted(
                ts.AnalysisVariant.CW_THETA_CW,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
                diagnostic_mode=True,
            )
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
        )
        assert solver.calls == []
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_APPLICABLE

    def test_taskset_proven_false_cannot_be_forged_on_certified_source(self):
        with pytest.raises(ts.CertificationError, match="taskset_proven"):
            replace(certified_cw(), taskset_proven=False)

    def test_missing_or_provisional_source_candidate_cannot_be_forged(self):
        source = certified_cw()
        provisional = replace(
            source.task_records[0],
            certification_status=ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED,
            _certification_token=None,
        )
        with pytest.raises(ts.CertificationError):
            replace(
                source,
                task_records=(provisional,) + source.task_records[1:],
            )
        missing = replace(
            provisional,
            solver_status=ts.TaskSolverStatus.NO_CANDIDATE,
            certification_status=ts.TaskCertificationStatus.NOT_CERTIFIED,
            candidate_response_time=None,
            closing_w=None,
            witness_h=None,
        )
        with pytest.raises(ts.CertificationError):
            replace(source, task_records=(missing,) + source.task_records[1:])

    def test_dependency_identity_mismatch_is_not_applicable(self):
        source = certified_cw(context("source"))
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            input=analysis_input(ctx=context("target")),
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
        assert result.dependency_check_status is ts.DependencyVectorCheckStatus.INVALID

    def test_interface_inactive_is_not_applicable(self):
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=certified_cw(),
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.HASH_MISMATCH,
        )
        assert solver.calls == []
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_APPLICABLE

    @pytest.mark.parametrize("bad_outcome", [candidate(3), failure(ts.TaskSolverStatus.NO_CANDIDATE)])
    def test_dominance_failures_are_internal_conformance_failures(self, bad_outcome):
        result, _ = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {"t0": candidate(1), "t1": bad_outcome, "t2": candidate(1)},
            source=certified_cw(),
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
        )
        assert result.solver_status is ts.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_CERTIFIED
        assert result.dominance_invariant_status is ts.DominanceInvariantStatus.DOMINANCE_INVARIANT_VIOLATION
        assert result.dominance_counterexample.task_id == "t1"
        assert result.task_records[0].certification_status is ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        assert result.n_tasks_certified == 0

    def test_local_candidate_is_never_fed_back(self):
        source = certified_cw(values={"t0": 2, "t1": 2, "t2": 2})
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {"t0": candidate(1), "t1": candidate(1), "t2": candidate(1)},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
        )
        assert result.taskset_proven
        assert [call["carry"] for call in solver.calls] == [
            {"t0": 2, "t1": 2, "t2": 2}
        ] * 3

    def test_source_is_frozen_and_cannot_be_modified(self):
        source = certified_cw()
        with pytest.raises(FrozenInstanceError):
            source.taskset_proven = False
        with pytest.raises(FrozenInstanceError):
            source.task_records[0].candidate_response_time = 99

    def test_explicit_diagnostic_cannot_fallback_to_another_vector(self):
        source, _ = run_scripted(
            ts.AnalysisVariant.CW_THETA_CW,
            {name: candidate(2) for name in ("t0", "t1", "t2")},
            diagnostic_mode=True,
        )
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.INVALID,
            diagnostic_mode=True,
        )
        assert solver.calls == []
        assert result.solver_status is ts.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
        assert result.certification_status is ts.AnalysisCertificationStatus.NOT_APPLICABLE
        assert not result.taskset_proven
        with pytest.raises(ts.CertificationError, match="may not fall back"):
            run_scripted(
                ts.AnalysisVariant.LOC_THETA_CW,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
                source=source,
                dependency_check_status=ts.DependencyVectorCheckStatus.INVALID,
                diagnostic_mode=True,
                diagnostic_carry_in_vector={"t0": 2, "t1": 2, "t2": 2},
            )


class TestFiveVariantSourceContractMatrix:
    NON_DEPENDENCY_VARIANTS = (
        ts.AnalysisVariant.CW_D,
        ts.AnalysisVariant.LOC_D,
        ts.AnalysisVariant.CW_THETA_CW,
        ts.AnalysisVariant.LOC_THETA_LOC,
    )

    @pytest.mark.parametrize("variant", NON_DEPENDENCY_VARIANTS)
    def test_non_dependency_variants_emit_no_external_source_provenance(self, variant):
        result, _ = run_scripted(
            variant,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=None,
            dependency_check_status=ts.DependencyVectorCheckStatus.NOT_CHECKED,
        )
        assert result.source_analysis_id is None
        assert result.source_candidate_vector == ()
        assert (
            result.dependency_check_status
            is ts.DependencyVectorCheckStatus.NOT_CHECKED
        )
        if variant is ts.AnalysisVariant.LOC_THETA_LOC:
            assert (
                result.fixed_carry_in_interface_status
                is ts.FixedCarryInInterfaceStatus.NOT_APPLICABLE
            )

    @pytest.mark.parametrize("variant", NON_DEPENDENCY_VARIANTS)
    def test_non_dependency_variants_reject_nonempty_source(self, variant):
        source = certified_cw()
        with pytest.raises(ts.CertificationError, match="source"):
            run_scripted(
                variant,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
                source=source,
                dependency_check_status=ts.DependencyVectorCheckStatus.NOT_CHECKED,
            )

    @pytest.mark.parametrize("variant", NON_DEPENDENCY_VARIANTS)
    def test_non_dependency_variants_reject_checked_dependency_status(self, variant):
        with pytest.raises(ts.CertificationError, match="NOT_CHECKED"):
            run_scripted(
                variant,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
                dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            )

    def test_loc_theta_cw_binds_the_complete_certified_source_vector(self):
        source = certified_cw(values={"t0": 2, "t1": 2, "t2": 2})
        result, _ = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
        )
        assert result.source_analysis_id == source.analysis_id
        assert (
            result.dependency_check_status
            is ts.DependencyVectorCheckStatus.VALID
        )
        assert result.source_candidate_vector == tuple(
            (record.task_id, record.candidate_response_time)
            for record in source.task_records
        )

    def test_loc_theta_cw_missing_source_fails_closed(self):
        with pytest.raises(ts.CertificationError, match="requires a source"):
            run_scripted(
                ts.AnalysisVariant.LOC_THETA_CW,
                {name: candidate(1) for name in ("t0", "t1", "t2")},
                source=None,
                dependency_check_status=ts.DependencyVectorCheckStatus.INVALID,
            )

    def test_loc_theta_cw_uncertified_source_only_returns_dependency_na(self):
        source, _ = run_scripted(
            ts.AnalysisVariant.CW_THETA_CW,
            {
                "t0": candidate(1),
                "t1": failure(ts.TaskSolverStatus.NO_CANDIDATE),
                "t2": candidate(1),
            },
        )
        result, solver = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.INVALID,
        )
        assert solver.calls == []
        assert (
            result.solver_status
            is ts.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
        )
        assert (
            result.certification_status
            is ts.AnalysisCertificationStatus.NOT_APPLICABLE
        )
        assert (
            result.dependency_check_status
            is ts.DependencyVectorCheckStatus.INVALID
        )


class TestAtomicFinalizer:
    def test_failed_finalizer_leaves_no_partial_certification(self):
        captured = []
        source = certified_cw()
        result, _ = run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            finalization_observer=lambda stage, records: captured.append((stage, records)),
        )
        before = captured[0][1]
        assert result.taskset_proven
        bad_records = (
            replace(before[0], candidate_response_time=3, closing_w=3),
        ) + before[1:]
        with pytest.raises(ts.CertificationError, match="exceeds"):
            ts.finalize_joint_certification(
                analysis_id="bad-finalize",
                variant=ts.AnalysisVariant.LOC_THETA_CW,
                tasks=tasks(),
                records=bad_records,
                context=context(),
                interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
                dependency_status=ts.DependencyVectorCheckStatus.VALID,
                compatibility_vector={"t0": 2, "t1": 2, "t2": 2},
                source=source,
            )
        assert all(
            record.certification_status is ts.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
            for record in before
        )

    def test_direct_finalizer_cannot_certify_loc_without_source(self):
        captured = []
        source = certified_cw()
        run_scripted(
            ts.AnalysisVariant.LOC_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            finalization_observer=lambda stage, records: captured.append((stage, records)),
        )
        before = captured[0][1]
        with pytest.raises(ts.CertificationError, match="certified CW source"):
            ts.finalize_joint_certification(
                analysis_id="source-less",
                variant=ts.AnalysisVariant.LOC_THETA_CW,
                tasks=tasks(),
                records=before,
                context=context(),
                interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
                dependency_status=ts.DependencyVectorCheckStatus.VALID,
                compatibility_vector={"t0": 2, "t1": 2, "t2": 2},
            )

    def test_callers_cannot_construct_certified_task(self):
        with pytest.raises(ts.CertificationError, match="only be produced"):
            ts.TaskAnalysisRecord(
                task_id="t0",
                priority_rank=0,
                solver_status=ts.TaskSolverStatus.CANDIDATE_FOUND,
                certification_status=ts.TaskCertificationStatus.CERTIFIED,
                candidate_response_time=1,
                carry_in_values_used=(),
                closing_w=1,
                witness_h=0,
                checked_w_count=1,
                checked_h_count=1,
                checked_q_count=1,
                envelope_call_count=1,
            )

    def test_finalizer_rejects_candidate_below_execution_time(self):
        observations = []
        run_scripted(
            ts.AnalysisVariant.CW_THETA_CW,
            {name: candidate(1) for name in ("t0", "t1", "t2")},
            finalization_observer=lambda stage, records: observations.append(records),
        )
        invalid = (replace(observations[0][0], candidate_response_time=0, closing_w=0),) + observations[0][1:]
        with pytest.raises(ts.CertificationError, match="C_i <= R_i"):
            ts.finalize_joint_certification(
                analysis_id="below-c",
                variant=ts.AnalysisVariant.CW_THETA_CW,
                tasks=tasks(),
                records=invalid,
                context=context(),
                interface_status=ts.FixedCarryInInterfaceStatus.NOT_APPLICABLE,
                dependency_status=ts.DependencyVectorCheckStatus.NOT_CHECKED,
            )


def test_seeded_real_core_random_consistency_nonvacuous():
    rng = random.Random(0x931310)
    total = 200
    source_certified = 0
    local_certified = 0
    dominance_violations = 0
    for instance in range(total):
        count = rng.randint(1, 4)
        items = tuple(
            core.V93Task(
                "i{}_t{}".format(instance, rank),
                1,
                rng.randint(1, 4),
                5,
                rng.randint(1, 4),
            )
            for rank in range(count)
        )
        ctx = context("random-{}".format(instance))
        inp = analysis_input(items, ctx)
        source = ts.analyze_taskset_v9_3(
            "cw-{}".format(instance),
            ts.AnalysisVariant.CW_THETA_CW,
            inp,
        )
        if not source.taskset_proven:
            continue
        source_certified += 1
        local = ts.analyze_taskset_v9_3(
            "loc-{}".format(instance),
            ts.AnalysisVariant.LOC_THETA_CW,
            inp,
            source=source,
            dependency_check_status=ts.DependencyVectorCheckStatus.VALID,
            fixed_carry_in_interface_status=ts.FixedCarryInInterfaceStatus.ACTIVE,
        )
        dominance_violations += int(
            local.dominance_invariant_status
            is ts.DominanceInvariantStatus.DOMINANCE_INVARIANT_VIOLATION
        )
        assert all(
            record.solver_status is not ts.TaskSolverStatus.NO_CANDIDATE
            for record in local.task_records
        )
        assert all(
            local_record.candidate_response_time <= source_record.candidate_response_time
            for local_record, source_record in zip(local.task_records, source.task_records)
        )
        assert local.taskset_proven
        local_certified += 1
    assert source_certified > 0
    assert local_certified == source_certified
    assert dominance_violations == 0
    print(
        "random_consistency N={} N_source_cw_certified={} "
        "N_loc_theta_cw_joint_certified={} dominance_violations={}".format(
            total, source_certified, local_certified, dominance_violations
        )
    )


def test_taskset_entry_rejects_illegal_curve_before_injected_solver_runs():
    inp = replace(analysis_input(), beta=[1] * 8)
    called = []

    def forbidden_solver(**_kwargs):
        called.append(True)
        return candidate(1)

    result = ts.analyze_taskset_v9_3(
        "invalid-curve",
        ts.AnalysisVariant.LOC_THETA_LOC,
        inp,
        single_task_solver=forbidden_solver,
    )
    assert result.solver_status is ts.AnalysisSolverStatus.NUMERIC_ERROR
    assert result.certification_status is ts.AnalysisCertificationStatus.NOT_CERTIFIED
    assert result.taskset_proven is False
    assert "service curve" in result.task_records[0].failure_reason
    assert called == []
