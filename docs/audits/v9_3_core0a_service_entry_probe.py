#!/usr/bin/env python3
"""Adversarial service-curve probes and entry-point call counters."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as runner
import asap_block_v9_3_v1_3_12_microcases as microcases
import core0a_v9_3_evidence as evidence


HASH = "a" * 64


def context(label="probe"):
    import hashlib
    def h(value):
        return hashlib.sha256((label + value).encode()).hexdigest()
    return taskset.DependencyContext(
        h("t"), h("d"), h("p"), h("e"), h("s"), h("v"),
        "EXACT_RATIONAL", None, taskset.THEORY_DOCUMENT_SHA256,
        taskset.FIXED_CARRY_IN_INTERFACE_SHA256, h("f"),
    )


def analysis_input(beta, deadline=2):
    item = core.V93Task("t", 1, deadline, deadline + 1, Fraction(1))
    return taskset.TasksetAnalysisInput((item,), 1, Fraction(100), beta, context())


def closure(beta, deadline=2):
    item = core.V93Task("t", 1, deadline, deadline + 1, Fraction(1))
    return core.canonical_closure_search_v9_3(
        core.EnvelopeKind.COMPLETE, item, (), (), 1, {}, 100, beta,
    )


class Nondeterministic:
    def __init__(self):
        self.calls = 0
    def __call__(self, length):
        self.calls += 1
        return 0 if self.calls <= 2 else length + 1


def raises(length):
    if length == 1:
        raise RuntimeError("deliberate callback error")
    return 0


def run_curve_cases():
    legal = {
        "zero": ([0], 0), "two-zero": ([0, 0], 1),
        "step": ([0, 1, 1], 2), "convex": ([0, 1, 2, 4], 3),
        "fraction": ([Fraction(0), Fraction(1, 3), Fraction(2, 3)], 2),
        "scaled-integer": ([0, 3, 6], 2),
    }
    invalid = {
        "beta-zero": ([1, 1], 1), "decreasing": ([0, 2, 1], 2),
        "negative": ([0, -1], 1), "float": ([0, 1.0], 1),
        "bool": ([0, True], 1), "nan": ([0, math.nan], 1),
        "inf": ([0, math.inf], 1), "callback-nondeterministic": (Nondeterministic(), 1),
        "callback-exception": (raises, 1), "horizon-incomplete": ([0], 1),
    }
    result = {"legal": {}, "invalid": {}}
    for name, (beta, horizon) in legal.items():
        try:
            frozen = core.validate_service_curve_v9_3(beta, horizon)
            result["legal"][name] = {"accepted": True, "frozen": [str(x) for x in frozen]}
        except Exception as exc:
            result["legal"][name] = {"accepted": False, "exception": type(exc).__name__}
    for name, (beta, horizon) in invalid.items():
        direct_rejected = closure_rejected = taskset_rejected = False
        try:
            core.validate_service_curve_v9_3(beta, horizon)
        except Exception:
            direct_rejected = True
        # Recreate stateful inputs for each independent attempt.
        beta2 = Nondeterministic() if name == "callback-nondeterministic" else beta
        try:
            value = closure(beta2, horizon + 1)
            closure_rejected = value.candidate_response_time is None and value.solver_status is not core.V93SolverStatus.CANDIDATE
        except Exception:
            closure_rejected = True
        beta3 = Nondeterministic() if name == "callback-nondeterministic" else beta
        try:
            value = taskset.analyze_taskset_v9_3(
                "invalid-" + name, taskset.AnalysisVariant.LOC_THETA_LOC,
                analysis_input(beta3, horizon + 1),
            )
            taskset_rejected = not value.taskset_proven and value.certification_status is not taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
        except Exception:
            taskset_rejected = True
        result["invalid"][name] = {
            "direct_rejected": direct_rejected,
            "closure_fail_closed": closure_rejected,
            "taskset_fail_closed": taskset_rejected,
        }
    return result


def call_counts():
    original = core.validate_service_curve_v9_3
    counts = {}
    active = [None]

    def spy(*args, **kwargs):
        counts[active[0]] = counts.get(active[0], 0) + 1
        return original(*args, **kwargs)

    core.validate_service_curve_v9_3 = spy
    try:
        active[0] = "single_task_closure"
        closure([0, 0])

        active[0] = "taskset_analyzer"
        taskset.analyze_taskset_v9_3(
            "entry-taskset", taskset.AnalysisVariant.LOC_THETA_LOC,
            analysis_input([0, 0]),
        )

        active[0] = "runner"
        runner.dispatch_rta_version(
            "v9.3", v93_request=runner.V93DispatchRequest(
                "entry-runner", taskset.AnalysisVariant.LOC_THETA_LOC,
                analysis_input([0, 0]),
            ),
        )

        active[0] = "finite_state_checker"
        evidence.produce_finite_state(HASH)

        active[0] = "microcase_formal_entry"
        with tempfile.TemporaryDirectory(prefix="core0a-service-entry-") as name:
            microcases.build_microcase_package(Path(name) / "package")
    finally:
        core.validate_service_curve_v9_3 = original
    return counts


def main():
    curves = run_curve_cases()
    counts = call_counts()
    ok = (
        all(value["accepted"] for value in curves["legal"].values())
        and all(all(value.values()) for value in curves["invalid"].values())
        and all(counts.get(name, 0) > 0 for name in (
            "single_task_closure", "taskset_analyzer", "runner",
            "finite_state_checker", "microcase_formal_entry",
        ))
    )
    value = {"status": "PASSED" if ok else "FAILED",
             "curve_cases": curves, "entry_call_counts": counts}
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
