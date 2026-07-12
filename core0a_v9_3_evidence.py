#!/usr/bin/env python3
"""Produce deterministic row-level evidence for v9.3 CORE-0A.

This producer writes observations only.  It never writes gate counts or gate
status; those are computed by the independent aggregator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import random
import shutil
from dataclasses import asdict, replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
from core0a_v9_3_evidence_schema import RAW_TABLES, SCHEMA_VERSION, TABLE_SCHEMAS
from core0a_v9_3_oracles import (
    envelope_reference,
    processor_reference,
    workload_reference,
)
from core0a_v9_3_scheduler_model import ASAPBlockTickModel


SEED = 0x93C0A1312
THEORY_SHA256 = "524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e"
CONTRACT_ZIP_SHA256 = "b67882290d4d4688a0e81fd98f95e9d998537facfb9f5945d1ec125143959895"
FINITE_DOMAIN_FILE = Path(__file__).resolve().parent / "docs/audits/v9_3_core0a_finite_state_domain.json"


class EvidenceProductionError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def semantic_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def truth(value: bool) -> str:
    return "true" if value else "false"


def exact_text(value: Any) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else "{}/{}".format(value.numerator, value.denominator)


def json_text(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def task_json(task: core.V93Task) -> Dict[str, Any]:
    return {
        "task_id": task.name,
        "C": task.wcet,
        "D": task.deadline,
        "T": task.period,
        "P": exact_text(task.power),
    }


def make_task(name: str, c: int, d: int, t: int, power: Any = 1) -> core.V93Task:
    return core.V93Task(name, c, d, t, Fraction(power))


def context(label: str) -> taskset.DependencyContext:
    def h(suffix: str) -> str:
        return hashlib.sha256((label + suffix).encode("utf-8")).hexdigest()

    return taskset.DependencyContext(
        taskset_identity=h(":taskset"),
        task_definitions_identity=h(":definitions"),
        priority_order_identity=h(":priority"),
        e0_canonical_identity=h(":e0"),
        service_curve_identity=h(":service"),
        power_vector_identity=h(":power"),
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=THEORY_SHA256,
        fixed_carry_in_interface_sha256=THEORY_SHA256,
        formal_contract_identity=h(":formal"),
    )


def analysis_input(
    tasks: Sequence[core.V93Task],
    label: str,
    *,
    processors: int = 1,
    e0: int = 1000,
    harvest: int = 0,
    beta: Any = None,
) -> taskset.TasksetAnalysisInput:
    curve = beta if beta is not None else (lambda length: Fraction(harvest * length))
    return taskset.TasksetAnalysisInput(
        tuple(tasks), processors, Fraction(e0), curve, context(label)
    )


def write_table(root: Path, name: str, rows: Iterable[Mapping[str, Any]]) -> int:
    schema = TABLE_SCHEMAS[name]
    materialized = [dict(row) for row in rows]
    fields = list(schema["fields"])
    for index, row in enumerate(materialized):
        if set(row) != set(fields):
            raise EvidenceProductionError(
                "{} row {} schema mismatch missing={} extra={}".format(
                    name,
                    index,
                    sorted(set(fields) - set(row)),
                    sorted(set(row) - set(fields)),
                )
            )
    pk = schema["primary_key"]
    materialized.sort(key=lambda row: tuple(str(row[field]) for field in pk))
    with (root / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)
    return len(materialized)


def produce_workload(build: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cases = []
    by_key = {}
    for c_value in range(1, 6):
        for period in range(c_value, 13):
            for deadline in range(c_value, period + 1):
                item = make_task("i", c_value, deadline, period)
                for theta in range(c_value, deadline + 1):
                    for length in range(31):
                        preimage = [c_value, period, deadline, theta, length]
                        case_id = semantic_hash("CORE0A:WORKLOAD:CASE", preimage)
                        actual = core.workload_bound_v9_3(item, length, theta)
                        expected = workload_reference(item, length, theta)
                        row = {
                            "case_id": case_id,
                            "input_hash": semantic_hash("CORE0A:WORKLOAD:INPUT", preimage),
                            "build_identity_hash": build,
                            "C": c_value,
                            "T": period,
                            "D": deadline,
                            "theta": theta,
                            "L": length,
                            "production_value": actual,
                            "oracle_value": expected,
                            "match": truth(actual == expected),
                        }
                        cases.append(row)
                        by_key[(c_value, period, deadline, theta, length)] = row
    checks = []

    def add_check(axis: str, left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
        preimage = [axis, left["case_id"], right["case_id"]]
        checks.append(
            {
                "check_id": semantic_hash("CORE0A:WORKLOAD:MONOTONIC", preimage),
                "input_hash": semantic_hash("CORE0A:WORKLOAD:MONOTONIC:INPUT", preimage),
                "build_identity_hash": build,
                "axis": axis,
                "left_case_id": left["case_id"],
                "right_case_id": right["case_id"],
                "left_value": left["production_value"],
                "right_value": right["production_value"],
                "passed": truth(int(right["production_value"]) >= int(left["production_value"])),
            }
        )

    for c_value in range(1, 6):
        for period in range(c_value, 13):
            for deadline in range(c_value, period + 1):
                for theta in range(c_value, deadline + 1):
                    for length in range(1, 31):
                        add_check(
                            "L",
                            by_key[(c_value, period, deadline, theta, length - 1)],
                            by_key[(c_value, period, deadline, theta, length)],
                        )
                for length in range(31):
                    for theta in range(c_value + 1, deadline + 1):
                        add_check(
                            "theta",
                            by_key[(c_value, period, deadline, theta - 1, length)],
                            by_key[(c_value, period, deadline, theta, length)],
                        )
    return cases, checks


def produce_processor(build: str) -> List[Dict[str, Any]]:
    rows = []

    def add(domain: str, target, hp, w, processors, theta, ordinal: int) -> None:
        data = {
            "domain": domain,
            "ordinal": ordinal,
            "target": task_json(target),
            "hp": [task_json(task) for task in hp],
            "theta": dict(sorted(theta.items())),
            "w": w,
            "M": processors,
        }
        actual = core.processor_delay_v9_3(target, hp, w, processors, theta)
        expected = processor_reference(target, hp, w, processors, theta)
        rows.append(
            {
                "case_id": semantic_hash("CORE0A:PROCESSOR:CASE", data),
                "input_hash": semantic_hash("CORE0A:PROCESSOR:INPUT", data),
                "build_identity_hash": build,
                "domain": domain,
                "M": processors,
                "target_json": json_text(data["target"]),
                "hp_json": json_text(data["hp"]),
                "theta_json": json_text(data["theta"]),
                "w": w,
                "production_value": actual,
                "oracle_value": expected,
                "match": truth(actual == expected),
            }
        )

    ordinal = 0
    for processors in range(1, 4):
        for target_c in range(1, 3):
            target = make_task("k", target_c, 4, 5)
            pool = [
                make_task("h0", 1, 2, 3),
                make_task("h1", 2, 3, 4),
                make_task("h2", 1, 4, 4),
                make_task("h3", 2, 4, 5),
            ]
            for hp_count in range(5):
                hp = pool[:hp_count]
                theta = {task.name: task.deadline for task in hp}
                for w in range(target_c, 5):
                    add("exhaustive", target, hp, w, processors, theta, ordinal)
                    ordinal += 1
    rng = random.Random(SEED ^ 0xD)
    for random_index in range(10_000):
        target_c = rng.randint(1, 4)
        target_d = rng.randint(target_c, 9)
        target = make_task("k", target_c, target_d, rng.randint(target_d, 12), rng.randint(1, 10))
        hp = []
        theta = {}
        for rank in range(rng.randint(0, 7)):
            c_value = rng.randint(1, 5)
            deadline = rng.randint(c_value, 10)
            item = make_task("h{}".format(rank), c_value, deadline, rng.randint(deadline, 12), rng.randint(1, 20))
            hp.append(item)
            theta[item.name] = rng.randint(c_value, deadline)
        add("random", target, hp, rng.randint(target_c, target_d), rng.randint(1, 5), theta, random_index)
    return rows


def produce_envelope(build: str, random_instances: int) -> List[Dict[str, Any]]:
    rows = []

    def add(domain, ordinal, target, hp, lp, w, q, h, processors, theta):
        base = {
            "domain": domain,
            "ordinal": ordinal,
            "target": task_json(target),
            "hp": [task_json(task) for task in hp],
            "lp": [task_json(task) for task in lp],
            "theta": dict(sorted(theta.items())),
            "w": w,
            "q": q,
            "h": h,
            "M": processors,
        }
        case_id = semantic_hash("CORE0A:ENVELOPE:CASE", base)
        for kind in (core.EnvelopeKind.COMPLETE, core.EnvelopeKind.LOCAL):
            actual = core.exact_energy_envelope_v9_3(kind, target, hp, lp, w, q, h, processors, theta)
            expected = envelope_reference(kind.value, target, hp, lp, w, q, h, processors, theta)
            rows.append(
                {
                    "case_id": case_id,
                    "kind": kind.value,
                    "input_hash": semantic_hash("CORE0A:ENVELOPE:INPUT", [base, kind.value]),
                    "build_identity_hash": build,
                    "domain": domain,
                    "target_json": json_text(base["target"]),
                    "hp_json": json_text(base["hp"]),
                    "lp_json": json_text(base["lp"]),
                    "theta_json": json_text(base["theta"]),
                    "w": w,
                    "q": q,
                    "h": h,
                    "M": processors,
                    "production_value": exact_text(actual),
                    "oracle_value": exact_text(expected),
                    "match": truth(actual == expected),
                }
            )

    ordinal = 0
    for processors in (1, 2):
        for target_power in (1, 3):
            for hp_count in (0, 1):
                for lp_count in (0, 1):
                    target = make_task("k", 1, 3, 4, target_power)
                    hp = [make_task("h", 1, 3, 4, 2)] if hp_count else []
                    lp = [make_task("l", 1, 3, 4, 4)] if lp_count else []
                    theta = {task.name: 2 for task in hp}
                    for w in range(1, 4):
                        for q in range(1, w + 1):
                            for h in range(w - q + 1):
                                add("exhaustive", ordinal, target, hp, lp, w, q, h, processors, theta)
                                ordinal += 1
    rng = random.Random(SEED ^ 0xE)
    for index in range(random_instances):
        target_c = rng.randint(1, 2)
        target_d = rng.randint(target_c, 4)
        target = make_task("k", target_c, target_d, rng.randint(target_d, 5), Fraction(rng.randint(1, 12), rng.randint(1, 4)))
        hp = []
        lp = []
        theta = {}
        for prefix, output in (("h", hp), ("l", lp)):
            for rank in range(rng.randint(0, 2)):
                c_value = rng.randint(1, 2)
                deadline = rng.randint(c_value, 4)
                item = make_task("{}{}".format(prefix, rank), c_value, deadline, rng.randint(deadline, 5), Fraction(rng.randint(1, 12), rng.randint(1, 4)))
                output.append(item)
                if prefix == "h":
                    theta[item.name] = rng.randint(c_value, deadline)
        w = rng.randint(target_c, target_d)
        q = rng.randint(1, w)
        h = rng.randint(0, w - q)
        add("random", index, target, hp, lp, w, q, h, rng.randint(1, 3), theta)
    return rows


def produce_search(build: str) -> List[Dict[str, Any]]:
    rows = []

    def run_case(case_label, kind, target, e0, beta_values, envelope_function):
        base = {
            "label": case_label,
            "variant": kind.value,
            "task": task_json(target),
            "hp": [],
            "lp": [],
            "theta": {},
            "M": 1,
            "E0": exact_text(e0),
            "service": [exact_text(value) for value in beta_values],
        }
        task_case_id = semantic_hash("CORE0A:SEARCH:CASE", base)
        observed = []
        result = core.canonical_closure_search_v9_3(
            kind,
            target,
            (),
            (),
            1,
            {},
            e0,
            beta_values,
            envelope_function=envelope_function,
            trace_observer=lambda event: observed.append(dict(event)),
        )
        for sequence, event in enumerate(observed):
            rows.append(
                {
                    "task_case_id": task_case_id,
                    "sequence_number": sequence,
                    "input_hash": semantic_hash("CORE0A:SEARCH:INPUT", base),
                    "build_identity_hash": build,
                    "variant": kind.value,
                    "task_json": json_text(base["task"]),
                    "hp_json": "[]",
                    "lp_json": "[]",
                    "theta_json": "{}",
                    "M": 1,
                    "E0": exact_text(e0),
                    "service_curve_json": json_text(base["service"]),
                    "result_status": result.solver_status.value,
                    "w": event["w"],
                    "A": event["A"],
                    "h": "" if event["h"] is None else event["h"],
                    "q": "" if event["q"] is None else event["q"],
                    "event_type": event["event_type"],
                    "envelope_value": "" if event["envelope_value"] is None else exact_text(event["envelope_value"]),
                    "service_value": "" if event["service_value"] is None else exact_text(event["service_value"]),
                    "service_index": "" if event["service_index"] is None else event["service_index"],
                    "coverage_index": "" if event["coverage_index"] is None else event["coverage_index"],
                    "q_result": event["q_result"],
                    "h_result": event["h_result"],
                    "w_result": event["w_result"],
                }
            )

    run_case(
        "all-w-h",
        core.EnvelopeKind.LOCAL,
        make_task("k", 1, 4, 5),
        0,
        [0, 0, 0, 0],
        lambda **_kwargs: 1,
    )

    def selective_failure(**kwargs):
        key = (kwargs["w"], kwargs["h"], kwargs["q"])
        return 1 if key in {(2, 0, 2), (3, 0, 1)} else 0

    run_case(
        "q-break-current-h-only",
        core.EnvelopeKind.COMPLETE,
        make_task("k", 2, 3, 4),
        0,
        [0, 0, 0],
        selective_failure,
    )
    for index in range(100):
        c_value = 1 + (index % 2)
        deadline = c_value + 1 + ((index // 2) % 3)
        period = deadline + 1 + ((index // 6) % 2)
        target = make_task("t", c_value, deadline, period, 1 + index % 7)
        run_case(
            "real-{:03d}".format(index),
            core.EnvelopeKind.COMPLETE,
            target,
            1000,
            [0] * deadline,
            core.exact_energy_envelope_v9_3,
        )
    return rows


def _curve_cases():
    def sequence_factory(values):
        return lambda: list(values)

    def nondeterministic_factory():
        calls = {0: 0, 1: 0, 2: 0}

        def curve(length):
            calls[length] += 1
            return length + (1 if length == 2 and calls[length] > 1 else 0)

        return curve

    def exception_factory():
        def curve(length):
            if length == 1:
                raise RuntimeError("frozen callback exception")
            return 0

        return curve

    return [
        ("valid-zero", True, "sequence", "[0]", 0, sequence_factory([0])),
        ("valid-flat", True, "sequence", "[0,0,0]", 2, sequence_factory([0, 0, 0])),
        ("valid-step", True, "sequence", "[0,1,1]", 2, sequence_factory([0, 1, 1])),
        ("valid-convex", True, "sequence", "[0,1,2,4]", 3, sequence_factory([0, 1, 2, 4])),
        ("valid-fraction", True, "sequence", "[0,1/3,2/3]", 2, sequence_factory([Fraction(0), Fraction(1, 3), Fraction(2, 3)])),
        ("invalid-beta-zero", False, "sequence", "[1,1,1]", 2, sequence_factory([1, 1, 1])),
        ("invalid-decreasing", False, "sequence", "[0,2,1]", 2, sequence_factory([0, 2, 1])),
        ("invalid-negative", False, "sequence", "[0,-1,0]", 2, sequence_factory([0, -1, 0])),
        ("invalid-float", False, "sequence", "[0,1.0,2]", 2, sequence_factory([0, 1.0, 2])),
        ("invalid-bool", False, "sequence", "[0,true,2]", 2, sequence_factory([0, True, 2])),
        ("invalid-nan", False, "sequence", "[0,NaN,2]", 2, sequence_factory([0, Decimal("NaN"), 2])),
        ("invalid-inf", False, "sequence", "[0,Inf,2]", 2, sequence_factory([0, Decimal("Infinity"), 2])),
        ("invalid-missing", False, "sequence", "[0,1] horizon=2", 2, sequence_factory([0, 1])),
        ("invalid-nondeterministic", False, "callback", "stateful length callback", 2, nondeterministic_factory),
        ("invalid-exception", False, "callback", "raises at L=1", 2, exception_factory),
    ]


def produce_service_curves(build: str) -> List[Dict[str, Any]]:
    rows = []
    for case_id, expected_valid, curve_kind, spec, horizon, factory in _curve_cases():
        accepted = False
        status = "VALID"
        try:
            core.validate_service_curve_v9_3(factory(), horizon)
            accepted = True
        except (core.V93NumericError, core.V93InputError) as exc:
            status = "REJECTED:{}".format(type(exc).__name__)
        target = make_task("curve", 1, horizon + 1, horizon + 2)
        analysis_attempted = True
        candidate_returned = False
        certification_returned = False
        closure = core.canonical_closure_search_v9_3(
            core.EnvelopeKind.COMPLETE,
            target,
            (),
            (),
            1,
            {},
            100,
            factory(),
            envelope_function=lambda **_kwargs: 0,
        )
        candidate_returned = closure.solver_status is core.V93SolverStatus.CANDIDATE
        try:
            analyzed = taskset.analyze_taskset_v9_3(
                "curve-{}".format(case_id),
                taskset.AnalysisVariant.LOC_THETA_LOC,
                analysis_input((target,), "curve-{}".format(case_id), beta=factory()),
            )
            certification_returned = analyzed.taskset_proven
        except taskset.CertificationError:
            certification_returned = False
        matched = expected_valid == accepted
        if not expected_valid:
            matched = matched and not candidate_returned and not certification_returned
        rows.append(
            {
                "curve_case_id": case_id,
                "input_hash": semantic_hash("CORE0A:SERVICE_CURVE:INPUT", [case_id, spec, horizon]),
                "build_identity_hash": build,
                "curve_kind": curve_kind,
                "curve_spec": spec,
                "required_horizon": horizon,
                "expected_valid": truth(expected_valid),
                "production_accepted": truth(accepted),
                "validation_status": status,
                "analysis_attempted": truth(analysis_attempted),
                "candidate_returned": truth(candidate_returned),
                "certification_returned": truth(certification_returned),
                "match": truth(matched),
            }
        )
    return rows


def produce_event_order(build: str) -> List[Dict[str, Any]]:
    scenarios = [
        {
            "id": "current-harvest-not-current-energy",
            "M": 1,
            "E0": 0,
            "ticks": [
                {
                    "releases": [{"job_id": "j", "task_id": "t", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 2}],
                    "harvest": 1,
                    "expect": {"execution_set": [], "post_tick_energy": "1", "energy_blocked_jobs": ["j"]},
                }
            ],
        },
        {
            "id": "completion-before-current-release",
            "M": 1,
            "E0": 2,
            "ticks": [
                {
                    "releases": [{"job_id": "old", "task_id": "old-task", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 1}],
                    "harvest": 0,
                    "expect": {"completion_events": [], "release_events": ["old"], "execution_set": ["old"]},
                },
                {
                    "releases": [{"job_id": "new", "task_id": "new-task", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 1}],
                    "harvest": 0,
                    "expect": {"completion_events": ["old"], "release_events": ["new"], "execution_set": ["new"]},
                },
            ],
        },
        {
            "id": "eligible-hol-fifo",
            "M": 1,
            "E0": 2,
            "ticks": [
                {
                    "releases": [
                        {"job_id": "j0", "task_id": "t", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 1},
                        {"job_id": "j1", "task_id": "t", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 2},
                    ],
                    "harvest": 0,
                    "expect": {"eligible_hol": ["j0"], "execution_set": ["j0"]},
                }
            ],
        },
        {
            "id": "block-prefix-stops-at-first-unaffordable",
            "M": 3,
            "E0": 2,
            "ticks": [
                {
                    "releases": [
                        {"job_id": "high", "task_id": "h", "priority_rank": 0, "wcet": 1, "power": 1, "candidate": 1},
                        {"job_id": "middle", "task_id": "m", "priority_rank": 1, "wcet": 1, "power": 5, "candidate": 2},
                        {"job_id": "low", "task_id": "l", "priority_rank": 2, "wcet": 1, "power": 1, "candidate": 3},
                    ],
                    "harvest": 0,
                    "expect": {"scan_order": ["high", "middle"], "execution_set": ["high"]},
                }
            ],
        },
        {
            "id": "processor-progress-m-higher-priority",
            "M": 2,
            "E0": 10,
            "ticks": [
                {
                    "releases": [
                        {"job_id": "h0", "task_id": "h0", "priority_rank": 0, "wcet": 2, "power": 1, "candidate": 2},
                        {"job_id": "h1", "task_id": "h1", "priority_rank": 1, "wcet": 2, "power": 1, "candidate": 2},
                        {"job_id": "target", "task_id": "t", "priority_rank": 2, "wcet": 1, "power": 1, "candidate": 3},
                    ],
                    "harvest": 0,
                    "expect": {"execution_set": ["h0", "h1"], "processor_blocked_jobs": ["target"]},
                }
            ],
        },
        {
            "id": "energy-blocked-fewer-than-m-hp",
            "M": 2,
            "E0": 2,
            "ticks": [
                {
                    "releases": [
                        {"job_id": "high", "task_id": "h", "priority_rank": 0, "wcet": 2, "power": 1, "candidate": 2},
                        {"job_id": "target", "task_id": "t", "priority_rank": 1, "wcet": 1, "power": 5, "candidate": 3},
                    ],
                    "harvest": 0,
                    "expect": {"execution_set": ["high"], "processor_blocked_jobs": [], "energy_blocked_jobs": ["target"]},
                }
            ],
        },
    ]
    rows = []
    event_order = [
        "complete_previous_tick_jobs",
        "credit_previous_tick_harvest",
        "release_current_tick_jobs",
        "read_boundary_energy",
        "scan_ASAP_BLOCK_prefix",
        "execute_one_tick",
        "credit_current_tick_harvest_for_next_boundary",
    ]
    for scenario in scenarios:
        model = ASAPBlockTickModel(scenario["M"], Fraction(scenario["E0"]))
        initial = [release for tick in scenario["ticks"] for release in tick["releases"]]
        input_hash = semantic_hash("CORE0A:EVENT_ORDER:INPUT", scenario)
        for tick, spec in enumerate(scenario["ticks"]):
            actual = model.step(tick, spec["releases"], Fraction(spec["harvest"]))
            normalized = {
                key: exact_text(value) if isinstance(value, Fraction) else value
                for key, value in actual.items()
            }
            expected = spec["expect"]
            observed_subset = {key: normalized[key] for key in expected}
            passed = observed_subset == expected
            rows.append(
                {
                    "microcase_id": scenario["id"],
                    "tick": tick,
                    "assertion_id": "tick-state",
                    "input_hash": input_hash,
                    "build_identity_hash": build,
                    "initial_tasks_json": json_text(initial),
                    "initial_energy": exact_text(scenario["E0"]),
                    "boundary_events_json": json_text(event_order),
                    "completion_events_json": json_text(actual["completion_events"]),
                    "harvest_credit": exact_text(actual["harvest_credit"]),
                    "release_events_json": json_text(actual["release_events"]),
                    "boundary_energy": exact_text(actual["start_energy"]),
                    "eligible_hol_json": json_text(actual["eligible_hol"]),
                    "scan_order_json": json_text(actual["scan_order"]),
                    "execution_set_json": json_text(actual["execution_set"]),
                    "energy_consumed": exact_text(actual["energy_consumed"]),
                    "post_tick_energy": exact_text(actual["post_tick_energy"]),
                    "expected_event": json_text(expected),
                    "actual_event": json_text(observed_subset),
                    "assertion_passed": truth(passed),
                }
            )
    return rows


class ScriptedSolver:
    def __init__(self, outcomes):
        self.outcomes = dict(outcomes)

    def __call__(self, **kwargs):
        return self.outcomes[kwargs["task"].name]


def candidate(value=1):
    return taskset.SingleTaskSolverResult(
        taskset.TaskSolverStatus.CANDIDATE_FOUND,
        value,
        value,
        0,
        1,
        1,
        1,
        1,
    )


def failure(status):
    return taskset.SingleTaskSolverResult(
        status, failure_reason=status.value
    )


def produce_joint_cases(build: str) -> List[Dict[str, Any]]:
    items = tuple(make_task("t{}".format(index), 1, 2, 3) for index in range(3))
    inp = analysis_input(items, "joint-state")
    rows = []

    def record(case_id, kind, variant, result, expected_solver, expected_cert, expected_proven):
        passed = (
            result.solver_status.value == expected_solver
            and result.certification_status.value == expected_cert
            and result.taskset_proven is expected_proven
        )
        payload = [case_id, kind, variant.value, expected_solver, expected_cert, expected_proven]
        rows.append(
            {
                "state_case_id": case_id,
                "input_hash": semantic_hash("CORE0A:JOINT:INPUT", payload),
                "build_identity_hash": build,
                "case_kind": kind,
                "variant": variant.value,
                "expected_solver_status": expected_solver,
                "actual_solver_status": result.solver_status.value,
                "expected_certification_status": expected_cert,
                "actual_certification_status": result.certification_status.value,
                "expected_taskset_proven": truth(expected_proven),
                "actual_taskset_proven": truth(result.taskset_proven),
                "production_api_used": "true",
                "passed": truth(passed),
            }
        )

    good = {item.name: candidate(1) for item in items}
    for variant in taskset.AnalysisVariant:
        if variant is taskset.AnalysisVariant.LOC_THETA_CW:
            continue
        result = taskset.analyze_taskset_v9_3(
            "joint-success-{}".format(variant.name),
            variant,
            inp,
            single_task_solver=ScriptedSolver(good),
        )
        record("success-{}".format(variant.name), "all-success", variant, result, "COMPLETED", "CERTIFIED_TASKSET", True)

    failure_statuses = (
        taskset.TaskSolverStatus.NO_CANDIDATE,
        taskset.TaskSolverStatus.TIMEOUT,
        taskset.TaskSolverStatus.NUMERIC_ERROR,
        taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
    )
    for variant in (taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC):
        for failed_status in failure_statuses:
            outcomes = dict(good)
            outcomes["t1"] = failure(failed_status)
            result = taskset.analyze_taskset_v9_3(
                "joint-failure-{}-{}".format(variant.name, failed_status.name),
                variant,
                inp,
                single_task_solver=ScriptedSolver(outcomes),
            )
            expected_solver = {
                taskset.TaskSolverStatus.NO_CANDIDATE: "NO_CANDIDATE",
                taskset.TaskSolverStatus.TIMEOUT: "TIMEOUT",
                taskset.TaskSolverStatus.NUMERIC_ERROR: "NUMERIC_ERROR",
                taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE: "INTERNAL_CONFORMANCE_FAILURE",
            }[failed_status]
            record("failure-{}-{}".format(variant.name, failed_status.name), "middle-failure", variant, result, expected_solver, "NOT_CERTIFIED", False)

    source = taskset.analyze_taskset_v9_3(
        "joint-source",
        taskset.AnalysisVariant.CW_THETA_CW,
        inp,
        single_task_solver=ScriptedSolver({item.name: candidate(2) for item in items}),
    )
    record("source-certified", "source", taskset.AnalysisVariant.CW_THETA_CW, source, "COMPLETED", "CERTIFIED_TASKSET", True)
    local = taskset.analyze_taskset_v9_3(
        "joint-local",
        taskset.AnalysisVariant.LOC_THETA_CW,
        inp,
        source=source,
        dependency_check_status=taskset.DependencyVectorCheckStatus.VALID,
        fixed_carry_in_interface_status=taskset.FixedCarryInInterfaceStatus.ACTIVE,
        single_task_solver=ScriptedSolver(good),
    )
    record("loc-frozen-success", "loc-source", taskset.AnalysisVariant.LOC_THETA_CW, local, "COMPLETED", "CERTIFIED_TASKSET", True)
    for case_id, invalid_source in (
        ("loc-missing-source", None),
        ("loc-identity-mismatch", replace(source, dependency_context=context("different"), _finalizer_token=source._finalizer_token)),
    ):
        result = taskset.analyze_taskset_v9_3(
            case_id,
            taskset.AnalysisVariant.LOC_THETA_CW,
            inp,
            source=invalid_source,
            dependency_check_status=taskset.DependencyVectorCheckStatus.VALID,
            single_task_solver=ScriptedSolver(good),
        )
        record(case_id, "dependency-not-applicable", taskset.AnalysisVariant.LOC_THETA_CW, result, "NOT_APPLICABLE_DEPENDENCY", "NOT_APPLICABLE", False)
    bad = dict(good)
    bad["t1"] = candidate(3)
    result = taskset.analyze_taskset_v9_3(
        "loc-dominance-failure",
        taskset.AnalysisVariant.LOC_THETA_CW,
        inp,
        source=source,
        dependency_check_status=taskset.DependencyVectorCheckStatus.VALID,
        single_task_solver=ScriptedSolver(bad),
    )
    record("loc-dominance-failure", "dominance-failure", taskset.AnalysisVariant.LOC_THETA_CW, result, "INTERNAL_CONFORMANCE_FAILURE", "NOT_CERTIFIED", False)
    return rows


def produce_dominance(build: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    taskset_rows = []
    task_rows = []
    parameter_sets = []
    for c_value in range(1, 4):
        for deadline in range(c_value, 11):
            for period in range(deadline, 13):
                for power in range(1, 11):
                    parameter_sets.append((c_value, deadline, period, power))
    for c_value, deadline, period, power in parameter_sets[:200]:
        item = make_task("t0", c_value, deadline, period, power)
        semantic = {
            "tasks": [task_json(item)],
            "priority_order": ["t0"],
            "M": 1,
            "E0": "1000",
            "service_curve": ["0"] * deadline,
        }
        taskset_hash = semantic_hash("CORE0A:DOMINANCE:TASKSET", semantic)
        inp = analysis_input((item,), "dominance:" + taskset_hash, processors=1, e0=1000)
        source = taskset.analyze_taskset_v9_3(
            "cw:" + taskset_hash,
            taskset.AnalysisVariant.CW_THETA_CW,
            inp,
        )
        local = taskset.analyze_taskset_v9_3(
            "loc:" + taskset_hash,
            taskset.AnalysisVariant.LOC_THETA_CW,
            inp,
            source=source,
            dependency_check_status=taskset.DependencyVectorCheckStatus.VALID,
            fixed_carry_in_interface_status=taskset.FixedCarryInInterfaceStatus.ACTIVE,
        )
        source_vector = list(source.source_candidate_vector)
        if not source_vector:
            source_vector = [
                [record.task_id, record.candidate_response_time]
                for record in source.task_records
            ]
        frozen_vector = [list(pair) for pair in local.source_candidate_vector]
        source_vector_hash = semantic_hash("CORE0A:DOMINANCE:VECTOR", source_vector)
        local_vector_hash = semantic_hash("CORE0A:DOMINANCE:VECTOR", frozen_vector)
        input_hash = semantic_hash("CORE0A:DOMINANCE:INPUT", semantic)
        taskset_rows.append(
            {
                "taskset_hash": taskset_hash,
                "input_hash": input_hash,
                "build_identity_hash": build,
                "tasks_json": json_text(semantic["tasks"]),
                "priority_order_json": json_text(semantic["priority_order"]),
                "processors": 1,
                "E0": "1000",
                "service_curve_json": json_text(semantic["service_curve"]),
                "source_analysis_id": source.analysis_id,
                "source_solver_status": source.solver_status.value,
                "source_certification_status": source.certification_status.value,
                "source_vector_json": json_text(source_vector),
                "source_vector_hash": source_vector_hash,
                "local_analysis_id": local.analysis_id,
                "local_solver_status": local.solver_status.value,
                "local_certification_status": local.certification_status.value,
                "local_frozen_vector_json": json_text(frozen_vector),
                "local_vector_hash": local_vector_hash,
                "joint_certified": truth(source.taskset_proven and local.taskset_proven),
            }
        )
        for source_record, local_record in zip(source.task_records, local.task_records):
            compared = (
                source_record.candidate_response_time is not None
                and local_record.candidate_response_time is not None
            )
            violation = bool(
                compared
                and local_record.candidate_response_time
                > source_record.candidate_response_time
            )
            task_rows.append(
                {
                    "taskset_hash": taskset_hash,
                    "task_id": source_record.task_id,
                    "input_hash": semantic_hash("CORE0A:DOMINANCE:TASK", [taskset_hash, source_record.task_id]),
                    "build_identity_hash": build,
                    "source_candidate": "" if source_record.candidate_response_time is None else source_record.candidate_response_time,
                    "local_candidate": "" if local_record.candidate_response_time is None else local_record.candidate_response_time,
                    "candidate_compared": truth(compared),
                    "dominance_violation": truth(violation),
                }
            )
    return taskset_rows, task_rows


def finite_domain_instances():
    instances = []
    for processors in (1, 2):
        for task_count in (1, 2):
            for powers in itertools.product((1, 2), repeat=task_count):
                tasks = tuple(
                    make_task(
                        "t{}".format(rank),
                        1,
                        2 + rank,
                        3 + rank,
                        powers[rank],
                    )
                    for rank in range(task_count)
                )
                instances.append((processors, tasks, 4, 0, "structural"))
    instances.append((1, (make_task("energy", 1, 3, 4, 2),), 1, 1, "energy-witness"))
    instances.append((1, (make_task("hp", 1, 2, 3, 1), make_task("lp", 1, 3, 4, 1)), 4, 0, "processor-witness"))
    return instances


def produce_finite_state(build: str):
    domain = json.loads(FINITE_DOMAIN_FILE.read_text(encoding="utf-8"))
    generation_horizon = int(domain["generation_horizon"])
    observation_horizon = int(domain["observation_horizon"])
    taskset_rows = []
    job_rows = []
    tick_rows = []
    certificate_rows = []
    bound_rows = []
    for ordinal, (processors, tasks, e0, harvest, label) in enumerate(finite_domain_instances()):
        semantic = {
            "domain_id": domain["domain_id"],
            "ordinal": ordinal,
            "label": label,
            "M": processors,
            "tasks": [task_json(task) for task in tasks],
            "E0": str(e0),
            "harvest": str(harvest),
        }
        taskset_id = semantic_hash("CORE0A:FINITE:TASKSET", semantic)
        inp = analysis_input(tasks, "finite:" + taskset_id, processors=processors, e0=e0, harvest=harvest)
        result = taskset.analyze_taskset_v9_3(
            "finite:" + taskset_id,
            taskset.AnalysisVariant.LOC_THETA_LOC,
            inp,
        )
        candidates = {
            record.task_id: record.candidate_response_time
            for record in result.task_records
        }
        model = ASAPBlockTickModel(processors, Fraction(e0))
        for tick in range(observation_horizon):
            releases = []
            if tick < generation_horizon:
                for rank, item in enumerate(tasks):
                    if tick % item.period == 0:
                        releases.append(
                            {
                                "job_id": "{}@{}".format(item.name, tick),
                                "task_id": item.name,
                                "priority_rank": rank,
                                "wcet": item.wcet,
                                "power": item.power,
                                "candidate": candidates[item.name],
                            }
                        )
            trace = model.step(tick, releases, Fraction(harvest))
            release_semantic = [
                dict(release, power=exact_text(release["power"]))
                for release in releases
            ]
            tick_rows.append(
                {
                    "taskset_id": taskset_id,
                    "tick": tick,
                    "input_hash": semantic_hash(
                        "CORE0A:FINITE:TICK",
                        [taskset_id, tick, release_semantic],
                    ),
                    "build_identity_hash": build,
                    "start_energy": exact_text(trace["start_energy"]),
                    "completion_events_json": json_text(trace["completion_events"]),
                    "previous_harvest_credit": exact_text(0 if tick == 0 else harvest),
                    "release_events_json": json_text(trace["release_events"]),
                    "eligible_hol_json": json_text(trace["eligible_hol"]),
                    "scan_order_json": json_text(trace["scan_order"]),
                    "execution_set_json": json_text(trace["execution_set"]),
                    "energy_consumed": exact_text(trace["energy_consumed"]),
                    "post_tick_energy": exact_text(trace["post_tick_energy"]),
                    "processor_blocked_jobs_json": json_text(trace["processor_blocked_jobs"]),
                    "energy_blocked_jobs_json": json_text(trace["energy_blocked_jobs"]),
                }
            )
        taskset_input_hash = semantic_hash("CORE0A:FINITE:INPUT", semantic)
        taskset_rows.append(
            {
                "taskset_id": taskset_id,
                "input_hash": taskset_input_hash,
                "build_identity_hash": build,
                "domain_id": domain["domain_id"],
                "tasks_json": json_text(semantic["tasks"]),
                "processors": processors,
                "E0": e0,
                "service_curve_json": json_text([str(harvest * length) for length in range(max(task.deadline for task in tasks))]),
                "generation_horizon": generation_horizon,
                "observation_horizon": observation_horizon,
                "enumeration_complete": "true",
                "analysis_variant": taskset.AnalysisVariant.LOC_THETA_LOC.value,
                "analysis_solver_status": result.solver_status.value,
                "analysis_certification_status": result.certification_status.value,
                "taskset_proven": truth(result.taskset_proven),
                "inconclusive_reason": "",
                "internal_error": "false",
            }
        )
        for job in model.jobs:
            response = "" if job.completion is None else job.completion - job.release
            job_input_hash = semantic_hash("CORE0A:FINITE:JOB", [taskset_id, job.job_id])
            job_rows.append(
                {
                    "taskset_id": taskset_id,
                    "job_id": job.job_id,
                    "input_hash": job_input_hash,
                    "build_identity_hash": build,
                    "task_id": job.task_id,
                    "priority_rank": job.priority_rank,
                    "release": job.release,
                    "wcet": job.wcet,
                    "candidate": job.candidate,
                    "completion": "" if job.completion is None else job.completion,
                    "response_time": response,
                    "release_energy": exact_text(job.release_energy),
                    "E0": e0,
                    "certificate_satisfied": truth(job.certificate_satisfied),
                    "processor_blocking_ticks": job.processor_blocking_ticks,
                    "energy_blocking_ticks": job.energy_blocking_ticks,
                }
            )
            certificate_rows.append(
                {
                    "taskset_id": taskset_id,
                    "job_id": job.job_id,
                    "input_hash": semantic_hash("CORE0A:FINITE:CERTIFICATE", [taskset_id, job.job_id, exact_text(job.release_energy), e0]),
                    "build_identity_hash": build,
                    "release": job.release,
                    "release_energy": exact_text(job.release_energy),
                    "E0": e0,
                    "positive_E0": truth(e0 > 0),
                    "candidate_jointly_certified": truth(result.taskset_proven),
                    "bound_check_executed": truth(job.certificate_satisfied and result.taskset_proven),
                    "certificate_status": "SATISFIED" if job.certificate_satisfied else "NOT_SATISFIED",
                }
            )
            if job.certificate_satisfied and result.taskset_proven:
                violation = job.completion is None or (job.completion - job.release) > job.candidate
                bound_rows.append(
                    {
                        "taskset_id": taskset_id,
                        "job_id": job.job_id,
                        "input_hash": semantic_hash("CORE0A:FINITE:BOUND", [taskset_id, job.job_id, job.candidate, job.completion]),
                        "build_identity_hash": build,
                        "variant": taskset.AnalysisVariant.LOC_THETA_LOC.value,
                        "release_boundary": job.release,
                        "candidate": job.candidate,
                        "actual_completion_boundary": "" if job.completion is None else job.completion,
                        "response_time": response,
                        "release_energy": exact_text(job.release_energy),
                        "E0": e0,
                        "certificate_satisfied": "true",
                        "processor_blocking_count": job.processor_blocking_ticks,
                        "energy_blocking_count": job.energy_blocking_ticks,
                        "violation": truth(violation),
                        "inconclusive_reason": "",
                    }
                )
    return taskset_rows, job_rows, tick_rows, certificate_rows, bound_rows


def produce_lineage(rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]], build: str):
    checks = []

    def add(check_type, source_file, source_key, target_file, target_key, expected, actual, passed):
        payload = [check_type, source_file, source_key, target_file, target_key]
        checks.append(
            {
                "check_id": semantic_hash("CORE0A:LINEAGE:CHECK", payload),
                "input_hash": semantic_hash("CORE0A:LINEAGE:INPUT", payload),
                "build_identity_hash": build,
                "check_type": check_type,
                "source_file": source_file,
                "source_key": source_key,
                "target_file": target_file,
                "target_key": target_key,
                "expected": str(expected),
                "actual": str(actual),
                "passed": truth(passed),
            }
        )

    for name, schema in TABLE_SCHEMAS.items():
        if name == "lineage_checks.csv":
            continue
        table_rows = list(rows_by_table[name])
        pk = schema["primary_key"]
        keys = [tuple(str(row[field]) for field in pk) for row in table_rows]
        add("PK_UNIQUENESS", name, json_text(list(pk)), name, json_text(list(pk)), len(keys), len(set(keys)), len(keys) == len(set(keys)))
        valid_input_hashes = sum(
            len(str(row["input_hash"])) == 64
            and all(char in "0123456789abcdef" for char in str(row["input_hash"]))
            for row in table_rows
        )
        add("INPUT_HASH_COVERAGE", name, "input_hash", name, "rows", len(table_rows), valid_input_hashes, valid_input_hashes == len(table_rows))
        matching_builds = sum(row["build_identity_hash"] == build for row in table_rows)
        add("BUILD_IDENTITY_COVERAGE", name, "build_identity_hash", "build_identity.json", "build_identity_hash", len(table_rows), matching_builds, matching_builds == len(table_rows))

    def fk(source_file, source_fields, target_file, target_fields):
        targets = {
            tuple(str(row[field]) for field in target_fields)
            for row in rows_by_table[target_file]
        }
        source_values = [
            tuple(str(row[field]) for field in source_fields)
            for row in rows_by_table[source_file]
        ]
        valid = sum(value in targets for value in source_values)
        add("FK_INTEGRITY", source_file, json_text(source_fields), target_file, json_text(target_fields), len(source_values), valid, valid == len(source_values))

    fk("dominance_task_results.csv", ["taskset_hash"], "dominance_tasksets.csv", ["taskset_hash"])
    for source in ("finite_state_jobs.csv", "finite_state_ticks.csv"):
        fk(source, ["taskset_id"], "finite_state_tasksets.csv", ["taskset_id"])
    for source in ("release_energy_certificates.csv", "bound_checks.csv"):
        fk(source, ["taskset_id", "job_id"], "finite_state_jobs.csv", ["taskset_id", "job_id"])
    return checks


def read_mutation_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != TABLE_SCHEMAS["mutation_runs.csv"]["fields"]:
            raise EvidenceProductionError("mutation_runs.csv header mismatch")
        rows = list(reader)
    if len(rows) != 15:
        raise EvidenceProductionError("exactly 15 real mutation rows are required")
    return rows


def produce_all(
    output: Path,
    build_identity_path: Path,
    mutation_runs_path: Path,
    random_envelope_instances: int = 50_000,
) -> Dict[str, Any]:
    build_identity = json.loads(build_identity_path.read_text(encoding="utf-8"))
    build = build_identity.get("build_identity_hash")
    if not isinstance(build, str) or len(build) != 64:
        raise EvidenceProductionError("build identity hash missing")
    if build_identity.get("theory_sha256") != THEORY_SHA256:
        raise EvidenceProductionError("theory hash mismatch in build identity")
    if build_identity.get("contract_zip_sha256") != CONTRACT_ZIP_SHA256:
        raise EvidenceProductionError("contract ZIP hash mismatch in build identity")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copyfile(build_identity_path, output / "build_identity.json")
    shutil.copyfile(FINITE_DOMAIN_FILE, output / "finite_state_domain.json")

    rows: Dict[str, List[Dict[str, Any]]] = {}
    workload, monotonic = produce_workload(build)
    rows["workload_cases.csv"] = workload
    rows["workload_monotonicity_checks.csv"] = monotonic
    rows["processor_cases.csv"] = produce_processor(build)
    rows["envelope_cases.csv"] = produce_envelope(build, random_envelope_instances)
    rows["search_trace_events.csv"] = produce_search(build)
    rows["service_curve_cases.csv"] = produce_service_curves(build)
    rows["scheduler_event_order_traces.csv"] = produce_event_order(build)
    rows["joint_certification_cases.csv"] = produce_joint_cases(build)
    dominance_tasksets, dominance_tasks = produce_dominance(build)
    rows["dominance_tasksets.csv"] = dominance_tasksets
    rows["dominance_task_results.csv"] = dominance_tasks
    finite = produce_finite_state(build)
    for name, table_rows in zip(
        (
            "finite_state_tasksets.csv",
            "finite_state_jobs.csv",
            "finite_state_ticks.csv",
            "release_energy_certificates.csv",
            "bound_checks.csv",
        ),
        finite,
    ):
        rows[name] = table_rows
    rows["mutation_runs.csv"] = read_mutation_rows(mutation_runs_path)
    rows["lineage_checks.csv"] = produce_lineage(rows, build)

    row_counts = {name: write_table(output, name, rows[name]) for name in RAW_TABLES}

    mismatch_tables = {
        "workload_cases.csv": [row for row in workload if row["match"] != "true"],
        "workload_monotonicity_checks.csv": [row for row in monotonic if row["passed"] != "true"],
        "processor_cases.csv": [row for row in rows["processor_cases.csv"] if row["match"] != "true"],
        "envelope_cases.csv": [row for row in rows["envelope_cases.csv"] if row["match"] != "true"],
        "service_curve_cases.csv": [row for row in rows["service_curve_cases.csv"] if row["match"] != "true"],
        "scheduler_event_order_traces.csv": [row for row in rows["scheduler_event_order_traces.csv"] if row["assertion_passed"] != "true"],
        "joint_certification_cases.csv": [row for row in rows["joint_certification_cases.csv"] if row["passed"] != "true"],
        "dominance_task_results.csv": [row for row in dominance_tasks if row["dominance_violation"] != "false"],
        "bound_checks.csv": [row for row in rows["bound_checks.csv"] if row["violation"] != "false" or row["inconclusive_reason"]],
        "mutation_runs.csv": [row for row in rows["mutation_runs.csv"] if row["detected"] != "true" or row["mutation_applied"] != "true" or row["failure_matches_target"] != "true" or row["original_file_hash"] != row["restored_file_hash"]],
        "lineage_checks.csv": [row for row in rows["lineage_checks.csv"] if row["passed"] != "true"],
    }
    failures = {name: values[:3] for name, values in mismatch_tables.items() if values}
    if len({row["taskset_hash"] for row in dominance_tasksets}) < 200:
        failures["dominance_tasksets.csv"] = ["fewer than 200 unique tasksets"]
    if not rows["bound_checks.csv"] or not any(
        row["positive_E0"] == "true" and row["certificate_status"] == "SATISFIED"
        for row in rows["release_energy_certificates.csv"]
    ):
        failures["positive_E0"] = ["positive-E0 evidence is empty"]
    if failures:
        (output / "production_failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise EvidenceProductionError("raw evidence contains a stop-condition failure")

    source_names = (
        "asap_block_rta_v9_3.py",
        "asap_block_rta_v9_3_taskset.py",
        "asap_block_v9_3_runner.py",
        "asap_block_v1_3_12_schema_binding.py",
        "asap_block_v9_3_v1_3_12_microcases.py",
        "core0a_v9_3_build_identity.py",
        "core0a_v9_3_oracles.py",
        "core0a_v9_3_scheduler_model.py",
        "core0a_v9_3_evidence.py",
        "core0a_v9_3_evidence_schema.py",
        "core0a_v9_3_independent_aggregator.py",
        "core0a_v9_3_package_validator.py",
        "scripts/core0a_v9_3_mutation_harness.py",
        "scripts/core0a_v9_3_mutation_probe.py",
    )
    project_root = Path(__file__).resolve().parent
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_identity_hash": build,
        "theory_sha256": THEORY_SHA256,
        "contract_zip_sha256": CONTRACT_ZIP_SHA256,
        "files": {name: file_hash(output / name) for name in RAW_TABLES},
        "row_counts": row_counts,
        "finite_state_domain_file": "finite_state_domain.json",
        "finite_state_domain_sha256": file_hash(output / "finite_state_domain.json"),
        "build_identity_file": "build_identity.json",
        "build_identity_sha256": file_hash(output / "build_identity.json"),
        "source_files": {name: file_hash(project_root / name) for name in source_names},
        "superseded_core0a_evidence": {
            "commit": "dcb55f6a22f4d772a74f94ac7799b79cf5da8541",
            "zip_sha256": "d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50",
            "status": "INVALIDATED",
            "reason": "independent evidence audit failure",
        },
    }
    (output / "raw_evidence_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"status": "PRODUCED", "row_counts": row_counts, "manifest_sha256": file_hash(output / "raw_evidence_manifest.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-identity", type=Path, required=True)
    parser.add_argument("--mutation-runs", type=Path, required=True)
    parser.add_argument("--random-envelope-instances", type=int, default=50_000)
    args = parser.parse_args()
    try:
        result = produce_all(
            args.output,
            args.build_identity,
            args.mutation_runs,
            args.random_envelope_instances,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
