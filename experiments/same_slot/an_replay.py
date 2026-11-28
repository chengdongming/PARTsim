"""Exact finite AN replay and optional audit of a native same-slot trace.

The integer replay is independent of RTA window/energy-bound computations.
A native trace is checked against the same finite inputs; agreement does
not establish universal binary64 or infinite-capacity equivalence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
from fractions import Fraction

from . import an_rta


def _validate_harvest(H, B, dmax):
    if not H or any(type(v) is not int or v < 0 for v in H):
        raise ValueError("harvest must be nonnegative scaled integers")
    prefix = [0]
    for p in H:
        prefix.append(prefix[-1]+p)
    for length in range(1, min(len(H), dmax)+1):
        for start in range(len(H)-length+1):
            if prefix[start+length]-prefix[start] < B[length]:
                raise ValueError(f"finite trace violates beta({length}) at {start}")


def replay(case, harvest, releases, initial_energy=0, certificate=None):
    """Reproduce legal releases and WCET/shorter demands until first actual miss.

    releases[i] is a list of [release, actual_execution_units] for task i.
    All times and amounts are exact integers; index order is strict priority.
    The given finite harvest must meet beta on every contained window.
    """
    tasks, M, B, e0, names = an_rta.normalize_case(case)
    H = tuple(harvest)
    _validate_harvest(H, B, max(t[1] for t in tasks))
    if type(initial_energy) is not int or initial_energy < 0:
        raise ValueError("invalid initial energy")
    if len(releases) != len(tasks):
        raise ValueError("one release list per task required")
    releases = [tuple(tuple(r) for r in row) for row in releases]
    arrivals = {}
    for i, rows in enumerate(releases):
        C, _, T, _ = tasks[i]
        previous = -T
        for row in rows:
            if len(row) != 2 or any(type(v) is not int for v in row):
                raise ValueError("release/demand must be integers")
            r, demand = row
            if not 0 <= r < len(H) or r-previous < T or not 1 <= demand <= C:
                raise ValueError("illegal release or execution demand")
            previous = r
            arrivals.setdefault(r, []).append((i, demand))
    X = None
    if certificate is not None:
        an_rta.verify_certificate(case, certificate)
        X = certificate["profile"]
    energy = initial_energy
    spent_total = 0
    active = [None]*len(tasks)
    maxF = [None]*len(tasks)
    maxR = [None]*len(tasks)
    complete = []
    slots = []
    missed = None
    first_violation = None
    for tick, supply in enumerate(H):
        for i, demand in arrivals.get(tick, ()):
            if energy < e0:
                raise ValueError(f"release at {tick} violates guaranteed pre-harvest floor")
            if active[i] is not None:
                raise RuntimeError("old job survived its deadline")
            active[i] = dict(task=i, release=tick, remaining=demand, first=None)
        ready = [dict(j) for j in active if j is not None]
        before = energy
        energy += supply
        budget = energy
        selected = []
        for i, job in enumerate(active):
            if job is None or energy < tasks[i][3]:
                continue
            if len(selected) == M:
                break
            energy -= tasks[i][3]
            selected.append([i, job["release"]])
            if job["first"] is None:
                job["first"] = tick+1
                val = tick+1-job["release"]
                maxF[i] = val if maxF[i] is None else max(maxF[i], val)
                if X and val > X[i][0] and first_violation is None:
                    first_violation = dict(task=i, release=job["release"],
                                           boundary=tick+1, kind="FIRST")
            job["remaining"] -= 1
            if job["remaining"] == 0:
                val = tick+1-job["release"]
                maxR[i] = val if maxR[i] is None else max(maxR[i], val)
                complete.append(dict(task=i, release=job["release"],
                                     first=job["first"], completion=tick+1))
                if X and val > X[i][1] and first_violation is None:
                    first_violation = dict(task=i, release=job["release"],
                                           boundary=tick+1, kind="RESPONSE")
                active[i] = None
        spent = budget-energy
        spent_total += spent
        slots.append(dict(t=tick, before=before, harvest=supply, budget=budget,
                          ready=ready, selected=selected, spent=spent, after=energy))
        for i, job in enumerate(active):
            if job is None:
                continue
            age = tick+1-job["release"]
            if X and first_violation is None:
                if job["first"] is None and age >= X[i][0]:
                    first_violation = dict(task=i, release=job["release"],
                                           boundary=tick+1, kind="FIRST")
                elif age >= X[i][1]:
                    first_violation = dict(task=i, release=job["release"],
                                           boundary=tick+1, kind="RESPONSE")
            if age >= tasks[i][1] and missed is None:
                missed = dict(task=i, name=names[i], release=job["release"],
                              boundary=tick+1, remaining=job["remaining"])
        if missed is not None:
            break
    ratios = None if X is None else [
        None if observed is None else X[i][1]/observed
        for i, observed in enumerate(maxR)]
    return dict(status="DEADLINE_MISS" if missed else "PASS_OBSERVED",
                response_bound_to_observed_max=ratios,
                observation_horizon=len(H), observed_slots=len(slots),
                first_deadline_miss=missed, certificate_violation=first_violation,
                max_observed_first_completion=maxF, max_observed_response=maxR,
                completed_jobs=complete,
                unfinished_jobs=[dict(j) for j in active if j is not None],
                initial_energy=initial_energy, final_energy=energy,
                spent_energy=spent_total, slots=slots)


def periodic_releases(case, offsets, cutoff):
    tasks, _, _, _, _ = an_rta.normalize_case(case)
    if type(cutoff) is not int or cutoff <= 0 or len(offsets) != len(tasks):
        raise ValueError("invalid release cutoff or offsets")
    result = []
    for (C, _, T, _), offset in zip(tasks, offsets):
        if type(offset) is not int or not 0 <= offset < min(T, cutoff):
            raise ValueError("offset must be in first period and before cutoff")
        result.append([[r, C] for r in range(offset, cutoff, T)])
    return result


def audit_native_trace(case, trace, reference, *, tolerance_j=1e-10):
    """Require matching decisions, completions and no-clipping ledger.

    A finite non-miss trajectory is fully comparable. A native mismatch is
    an audit failure, not evidence against the mathematical certificate.
    tolerance_j checks reported binary64 numeric observations only; selected
    identities and job boundaries must match exactly. This does not prove
    decision equivalence at untested floating-point margins.
    """
    tasks, _, _, _, _ = an_rta.normalize_case(case)
    scale = case["model"]["energy_scale"]
    if not math.isfinite(tolerance_j) or tolerance_j <= 0:
        raise ValueError("native numeric observation tolerance must be positive")
    tolerance_j = min(tolerance_j, 0.25/scale)
    if reference["first_deadline_miss"] is not None:
        raise ValueError("native full-trace audit requires reference without a deadline miss")
    if (trace.get("configured_scheduler") != "gpfp_asap_nonblock"
            or trace.get("energy_timing") != "HARVEST_BEFORE_DEBIT"
            or not trace.get("simulation_completed")
            or trace.get("observed_simulation_end_ms") != reference["observation_horizon"]):
        raise ValueError("native trace timing/scheduler/horizon mismatch")
    from experiments.pipeline.task_identity import runtime_task_name_for_source_id
    name_to_index = {runtime_task_name_for_source_id(i): i for i in range(len(tasks))}
    events = trace["events"]
    decisions = {}
    arrivals = []
    completions = []
    native_first = {}
    max_abs_error_j = 0.0

    def check_amount(observed, units, description, millijoules=False):
        nonlocal max_abs_error_j
        actual = float(observed)/(1000 if millijoules else 1)
        expected = units/scale
        err = abs(actual-expected)
        if not math.isfinite(actual) or err > tolerance_j:
            raise ValueError(f"native {description} differs: {actual} vs {expected}")
        max_abs_error_j = max(max_abs_error_j, err)

    def as_int(value):
        number = Fraction(str(value))
        if number.denominator != 1:
            raise ValueError("noninteger native job boundary")
        return int(number)

    def job_key(job):
        return [name_to_index[job["task_name"]], as_int(job["arrival_time"])]

    for event in events:
        kind = event["event_type"]
        if kind == "scheduler_decision":
            t = as_int(event["time"])
            if t in decisions or event.get("scheduler") != "ASAP-NonBlock":
                raise ValueError("duplicate or foreign native decision")
            decisions[t] = event
            for j in event["selected_jobs"]:
                key = tuple(job_key(j))
                native_first.setdefault(key, t+1)
        elif kind == "arrival":
            arrivals.append(job_key(event))
        elif kind == "end_instance":
            completions.append(job_key(event)+[as_int(event["time"])])
        elif kind == "dline_miss":
            raise ValueError("unexpected native deadline miss on reference passing trajectory")
    expected_arrivals = sorted(
        [j["task"], j["release"]]
        for slot in reference["slots"] for j in slot["ready"]
        if j["release"] == slot["t"])
    if sorted(arrivals) != expected_arrivals:
        raise ValueError("native release identity mismatch")
    decision_count = 0
    for slot in reference["slots"]:
        event = decisions.pop(slot["t"], None)
        if not slot["ready"]:
            if event is not None and (event["ready_jobs"] or event["selected_jobs"]):
                raise ValueError("native executes while reference is idle")
            continue
        if event is None:
            raise ValueError(f"missing native decision at {slot['t']}")
        decision_count += 1
        expected_ready = [[j["task"], j["release"]] for j in slot["ready"]]
        if ([job_key(j) for j in event["ready_jobs"]] != expected_ready
                or [job_key(j) for j in event["selected_jobs"]] != slot["selected"]):
            raise ValueError(f"native selected/ready set mismatch at {slot['t']}")
        check_amount(event["available_energy_mJ_exact"], slot["budget"],
                     "decision budget", True)
        for j, ref in zip(event["ready_jobs"], slot["ready"]):
            if as_int(j["remaining_time_ms"]) != ref["remaining"]:
                raise ValueError("native remaining execution mismatch")
            check_amount(j["task_unit_energy_mJ_exact"], tasks[ref["task"]][3],
                         "unit energy", True)
    if decisions:
        raise ValueError("native decisions outside reference horizon")
    expected_completed = sorted([j["task"], j["release"], j["completion"]]
                                for j in reference["completed_jobs"])
    if sorted(completions) != expected_completed:
        raise ValueError("native completion boundaries differ")
    for j in reference["completed_jobs"] + reference["unfinished_jobs"]:
        if j["first"] is not None and native_first.get((j["task"], j["release"])) != j["first"]:
            raise ValueError("native first-unit completion differs")
    ledger = trace["same_slot_energy_summary"]
    offered = sum(s["harvest"] for s in reference["slots"])
    if (ledger["observed_energy_intervals"] != reference["observed_slots"]
            or float(ledger["clipped_energy_j"]) != 0.0
            or float(ledger["capacity_j"]) < (reference["initial_energy"]+offered)/scale):
        raise ValueError("native storage may clip or has incomplete accounting")
    for field, units in (("initial_energy_j", reference["initial_energy"]),
                         ("offered_energy_j", offered), ("credited_energy_j", offered),
                         ("consumed_energy_j", reference["spent_energy"]),
                         ("battery_final_j", reference["final_energy"])):
        check_amount(ledger[field], units, field)
    return dict(status="PASS_FINITE_TRACE", decision_ticks=decision_count,
                observed_slots=reference["observed_slots"],
                completed_jobs=len(expected_completed),
                maximum_numeric_observation_error_j=max_abs_error_j,
                tolerance_j=tolerance_j, clipped_energy_j=ledger["clipped_energy_j"])


def run_native_periodic(case, harvest, initial_energy, offsets, cutoff, binary,
                        output, certificate=None):
    """Run the repository's native simulator on a finite periodic WCET test.

    Uses a sampled electrical-power trace, no separate background energy,
    explicit strict ranks, and a capacity above the total possible credit.
    """
    from experiments.pipeline.simulation_engine import materialize_simulation_inputs
    from experiments.pipeline.numeric_projection import canonical_binary64_decimal_v7
    tasks, M, _, _, _ = an_rta.normalize_case(case)
    harvest = tuple(harvest)
    if cutoff > len(harvest):
        raise ValueError("release cutoff exceeds observation horizon")
    releases = periodic_releases(case, offsets, cutoff)
    reference = replay(case, harvest, releases, initial_energy, certificate)
    if reference["first_deadline_miss"] is not None:
        # The exact first violation is reported separately; native trace
        # abort/cutoff semantics must not be silently compared as a full trace.
        raise ValueError("native consistency audit requires a passing reference trajectory")
    scale = case["model"]["energy_scale"]
    root = Path(__file__).resolve().parents[2]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Base template's hash workload has configured 93/250000 J per unit.
    # Each desired exact cost is projected as a task energy factor. The
    # actual reported unit cost is checked against that integer ledger.
    base_cost = Fraction(93, 250000)
    factors = {str(i): canonical_binary64_decimal_v7(Fraction(p, scale)/base_cost)
               for i, (_, _, _, p) in enumerate(tasks)}
    payload = tuple(dict(task_id=str(i), priority_rank=i, C=C, D=D, T=T,
                         P=str(Fraction(p, scale)), workload="hash", arrival_offset=offsets[i])
                    for i, (C, D, T, p) in enumerate(tasks))
    cap = Fraction(initial_energy+sum(harvest)+max(t[3] for t in tasks)+1, scale)
    system, taskset = materialize_simulation_inputs(
        root/"configs/templates/system_config_unified_template.yml", output, payload,
        processors=M, initial_battery=Fraction(initial_energy, scale),
        battery_capacity=cap, scheduler_id="gpfp_asap_nonblock",
        service_curve=dict(solar_scale="1", use_real_solar_data=False),
        release_horizon=cutoff, task_energy_factors=factors)
    # The task projection is RM by default. Add explicit strict ranks so
    # arbitrary INPUT_ORDER and equal periods exactly match the RTA model.
    task_lines = taskset.read_text().splitlines()
    index = 0
    for n, line in enumerate(task_lines):
        if 'params: "' in line:
            task_lines[n] = line.replace('params: "', f'params: "fixed_priority_rank={index},', 1)
            index += 1
    if index != len(tasks):
        raise ValueError("cannot bind native fixed-priority task ranks")
    taskset.write_text("\n".join(task_lines)+"\n")
    hp = output/"harvest.csv"
    hp.write_text("timestamp_ms,power_w\n"+"".join(
        f"{i},{float(Fraction(p*1000, scale)):.17g}\n"
        for i,p in enumerate(harvest+(0,))))
    legacy = ("base_harvesting_rate", "day_of_year", "harvesting_scale", "pv_area_m2",
              "pv_efficiency", "solar_data_file", "time_of_day_ms", "use_real_solar_data")
    lines = [l for l in system.read_text().splitlines()
             if not re.match(r"\s+("+"|".join(legacy)+r"):", l)]
    system.write_text("\n".join(lines)+"\n"+
        "\nharvesting:\n  source: sampled_trace\n  sampled_trace:\n"+
        f"    file: {json.dumps(str(hp))}\n"+
        "    time_column: timestamp_ms\n    value_column: power_w\n"+
        "    value_type: electrical_power\n    interpolation: zero_order_hold\n"+
        "    after_trace: zero\n")
    trace_path = output/"trace.json"
    from .campaign import digest, sha
    binary = Path(binary).resolve(strict=True)
    (output/"input.json").write_text(json.dumps(
        dict(case=case, harvest=harvest, initial_energy=initial_energy,
             offsets=offsets, release_horizon=cutoff, certificate=certificate),
        indent=2)+"\n")
    result = subprocess.run([str(binary), str(system), str(taskset),
                             str(len(harvest)), "-t", str(trace_path),
                             "--energy-timing", "same-slot", "--semantic-traces",
                             "--taskset-semantic-hash", digest(payload),
                             "--run-id", "an-rta-finite-audit"],
                            cwd=root, capture_output=True, text=True, timeout=60)
    (output/"native.stdout.log").write_text(result.stdout)
    (output/"native.stderr.log").write_text(result.stderr)
    if result.returncode != 0:
        raise RuntimeError("native simulator failed; see native.stderr.log")
    trace = json.loads(trace_path.read_text())
    if (trace.get("run_id") != "an-rta-finite-audit"
            or trace.get("taskset_semantic_hash") != digest(payload)):
        raise ValueError("native trace does not belong to the projected input")
    audit = audit_native_trace(case, trace, reference)
    audit.update(binary_sha256=sha(binary), trace_sha256=sha(trace_path),
                 exact_input_sha256=digest(case["model"]))
    (output/"audit.json").write_text(json.dumps(audit, indent=2)+"\n")
    (output/"reference.json").write_text(json.dumps(reference, indent=2)+"\n")
    return audit
