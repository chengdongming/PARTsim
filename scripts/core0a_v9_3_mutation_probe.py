#!/usr/bin/env python3
"""Focused semantic probes used only by the real CORE-0A mutation harness."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from fractions import Fraction
from pathlib import Path

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_methods as methods
import asap_block_rta_v9_3_taskset as taskset
from experiments.v9_3 import exact_energy


def make_task(name, c, d, t, power=1):
    return core.V93Task(name, c, d, t, Fraction(power))


def context(label, tasks, e0, beta):
    import hashlib

    def h(suffix):
        return hashlib.sha256((label + suffix).encode()).hexdigest()

    exact_identity = exact_energy.exact_input_identity(
        task_powers=((item.name, item.power) for item in tasks),
        e0=e0,
        service_prefix=beta,
    )
    return taskset.DependencyContext(
        h("t"), h("d"), h("p"), h("e"), h("s"), h("v"),
        "EXACT_RATIONAL", None,
        taskset.THEORY_DOCUMENT_SHA256,
        taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        h("f"),
        exact_energy.NUMERIC_CONTRACT_SHA256,
        exact_energy.SOURCE_NUMERIC_MODEL,
        exact_energy.DEMAND_ROUNDING_MODE,
        exact_energy.SUPPLY_ROUNDING_MODE,
        exact_energy.E0_ROUNDING_MODE,
        exact_identity,
        exact_energy.FLOAT_DECISION_PATH,
    )


def inp(tasks, label="probe"):
    items = tuple(tasks)
    e0 = Fraction(100)
    beta = tuple(Fraction(0) for _ in range(max(item.deadline for item in items)))
    return taskset.TasksetAnalysisInput(
        items, 1, e0, beta, context(label, items, e0, beta)
    )


def assert_isolated_import_closure():
    raw_root = os.environ.get("CORE0A_MUTATION_SANDBOX_ROOT")
    if not raw_root:
        raise AssertionError("mutation sandbox identity is missing")
    root = Path(raw_root).resolve(strict=True)
    for module in (core, methods, taskset, exact_energy):
        module_path = Path(module.__file__).resolve(strict=True)
        try:
            module_path.relative_to(root)
        except ValueError as exc:
            raise AssertionError(
                "mutation sandbox imported host source: {}".format(module_path)
            ) from exc
    for entry in sys.path:
        candidate = Path(entry or ".").resolve()
        if candidate != root and (candidate / "asap_block_rta_v9_3.py").is_file():
            raise AssertionError(
                "mutation sandbox retains host source fallback: {}".format(candidate)
            )


def candidate(value):
    return taskset.SingleTaskSolverResult(
        taskset.TaskSolverStatus.CANDIDATE_FOUND,
        value, value, 0, 1, 1, 1, 1,
    )


def probe_envelope_target_term():
    target = make_task("k", 2, 3, 4, 7)
    assert core.complete_window_envelope_v9_3(target, (), (), 2, 2, 0, 2, {}) == 14


def probe_local_coverage():
    target = make_task("k", 1, 5, 6, 1)
    hp = (make_task("h", 1, 1, 2, 5),)
    local = core.local_window_envelope_v9_3(target, hp, (), 3, 1, 1, 1, {"h": 1})
    complete = core.complete_window_envelope_v9_3(target, hp, (), 3, 1, 1, 1, {"h": 1})
    assert local < complete


def probe_service_index():
    target = make_task("k", 1, 1, 2)
    try:
        result = core.canonical_closure_search_v9_3(
            core.EnvelopeKind.COMPLETE, target, (), (), 1, {}, 0, [0],
            envelope_function=lambda **_kwargs: 1,
        )
    except IndexError as exc:
        raise AssertionError("service lookup escaped h+q-1") from exc
    assert result.solver_status is core.V93SolverStatus.NO_CANDIDATE


def probe_h_visitation():
    target = make_task("k", 1, 2, 3)

    def envelope(**kwargs):
        return 0 if (kwargs["w"], kwargs["h"]) == (2, 1) else 1

    result = core.canonical_closure_search_v9_3(
        core.EnvelopeKind.COMPLETE, target, (), (), 1, {}, 0, [0, 0],
        envelope_function=envelope,
    )
    assert result.candidate_response_time == 2 and result.witness_h == 1


def probe_processor_truncation():
    target = make_task("k", 3, 8, 9)
    hp = (make_task("h", 3, 4, 4),)
    assert core.effective_hp_workloads_v9_3(target, hp, 3, {"h": 4}) == (1,)


def probe_lp_capacity():
    target = make_task("k", 1, 4, 5, 1)
    lp = (
        make_task("l0", 1, 4, 5, 20),
        make_task("l1", 1, 4, 5, 19),
    )
    assert core.complete_window_envelope_v9_3(target, (), lp, 2, 2, 0, 2, {}) == 21


def probe_no_early_certification():
    tasks = (make_task("t0", 1, 2, 3),)
    original = taskset.finalize_joint_certification

    def checked_finalization(**kwargs):
        assert all(
            record.certification_status
            is taskset.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
            for record in kwargs["records"]
        ), "single-task candidates were certified before joint finalization"
        return original(**kwargs)

    taskset.finalize_joint_certification = checked_finalization
    result = taskset.analyze_taskset_v9_3(
        "cert", taskset.AnalysisVariant.LOC_THETA_LOC, inp(tasks),
        single_task_solver=lambda **_kwargs: candidate(1),
    )
    assert result.taskset_proven and result.task_records[0].certification_status is taskset.TaskCertificationStatus.CERTIFIED


def probe_loc_frozen_vector():
    tasks = tuple(make_task("t{}".format(i), 1, 2, 3) for i in range(3))
    source = taskset.analyze_taskset_v9_3(
        "source", taskset.AnalysisVariant.CW_THETA_CW, inp(tasks, "same"),
        single_task_solver=lambda **_kwargs: candidate(2),
    )
    calls = []

    def solver(**kwargs):
        calls.append(dict(kwargs["carry_in_vector"]))
        return candidate(1)

    local = taskset.analyze_taskset_v9_3(
        "local", taskset.AnalysisVariant.LOC_THETA_CW, inp(tasks, "same"),
        source=source,
        dependency_check_status=taskset.DependencyVectorCheckStatus.VALID,
        single_task_solver=solver,
    )
    assert local.taskset_proven
    assert calls == [{"t0": 2, "t1": 2, "t2": 2}] * 3


def probe_task_hash_provenance():
    from asap_block_v1_3_12_schema_binding import V1312SchemaBinding

    binding = V1312SchemaBinding()
    sandbox = Path(os.environ["CORE0A_MUTATION_SANDBOX_ROOT"])
    results = (
        sandbox
        / "artifacts/v9_3_v1_3_12_runner_microcase/per_task_results.csv"
    )
    with results.open("r", encoding="utf-8", newline="") as handle:
        row = binding.decode_row("per_task_results.csv", next(csv.DictReader(handle)))
    changed = dict(row)
    changed["task_failure_reason_code"] = "NO_CANDIDATE"
    changed["task_failure_detail"] = "closure exhausted through task deadline"
    assert binding.task_result_hash(row) != binding.task_result_hash(changed)


def probe_beta_zero():
    try:
        core.validate_service_curve_v9_3([1, 1], 1)
    except core.V93NumericError:
        return
    raise AssertionError("beta(0) mutation survived")


def probe_monotonicity():
    try:
        core.validate_service_curve_v9_3([0, 2, 1], 2)
    except core.V93NumericError:
        return
    raise AssertionError("monotonicity mutation survived")


PROBES = {
    "delete_yk_power": probe_envelope_target_term,
    "local_q_plus_h_to_w": probe_local_coverage,
    "service_h_plus_q_minus_1_to_h_plus_q": probe_service_index,
    "terminate_all_h_after_first_failure": probe_h_visitation,
    "skip_intermediate_h": probe_h_visitation,
    "remove_processor_truncation": probe_processor_truncation,
    "remove_lp_capacity": probe_lp_capacity,
    "early_task_certification": probe_no_early_certification,
    "loc_uses_local_prefix": probe_loc_frozen_vector,
    "task_hash_drops_failure_provenance": probe_task_hash_provenance,
    "service_curve_skip_beta_zero": probe_beta_zero,
    "service_curve_skip_monotonicity": probe_monotonicity,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mutation_id", choices=sorted(PROBES))
    args = parser.parse_args()
    assert_isolated_import_closure()
    PROBES[args.mutation_id]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
