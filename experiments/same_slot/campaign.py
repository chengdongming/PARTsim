"""Reproducible paired campaigns for the current unlimited-storage RTA.

Only the experiment layer changes. Native same-slot analytic/flow and the
already implemented conservative shifted SEQ use the existing engines.
The old synthetic inputs are verified and given NEW model and case identities.
No old results or measured release floors are imported.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "experiments/asapblock_unlimited"
ENTRY = ROOT / "scripts/run_same_slot_rta_campaign.py"
SCHEMA = "ASAP_BLOCK_SAME_SLOT_CAMPAIGN_V1"
METHODS = {
    "coverage": ("main", ("analytic", "translated_seq")),
    "sensitivity": ("latest", ("analytic", "translated_seq")),
    "flow": ("flow", ("analytic", "flow")),
    "scaling": ("scale_all", ("analytic", "translated_seq")),
    "constructed": ("constructed", ("analytic", "flow")),
}
TERMINAL = {"CERTIFIED", "NO_CERTIFICATE", "TIMEOUT", "ERROR"}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                              allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def frozen_data():
    path = FROZEN / "runtime/reproduction/optimized/experiments/ab_v7_2/data.py"
    spec = importlib.util.spec_from_file_location("frozen_case_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(output):
    # Fresh generation only: the frozen generator verifies every dataset
    # against its original input fingerprints, independently of any results.
    subprocess.run([sys.executable, str(FROZEN / "prepare.py"),
                    "--output", str(output)], cwd=ROOT, check=True)


def convert(source):
    """Convert one verified hypothetical input, never a measured trace."""
    data = frozen_data()
    data.validate_case(source)
    old = source["model"]
    if old["capacity"] is not None or old["background_power"] != 0:
        raise ValueError("only unlimited, zero-background inputs are supported")
    model = dict(old, harvest_order="HARVEST_BEFORE_DEBIT",
                 release_floor_stage="PRE_HARVEST")
    case = {
        "name": source["name"], "source_case_id": source["case_id"],
        "source_input_sha256": source["input_sha256"],
        "source_kind": "VERIFIED_FROZEN_HYPOTHETICAL_MODEL",
        "metadata": source["metadata"], "cohort": source["cohort"],
        "model": model, "input_sha256": digest(model),
    }
    case["case_id"] = digest(case)
    return case


def source_identity():
    # The active solver imports the frozen optimized analysis dependencies.
    paths = [Path(__file__), ENTRY, Path(__file__).with_name("ab_rta.py")]
    paths += sorted(p for p in (FROZEN / "runtime").rglob("*.py"))
    paths += sorted((FROZEN / "configs").glob("*.json"))
    paths += [FROZEN / "prepare.py"]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def build_plan(source_dir, suite, limit=None, timeout=30, workers=1):
    if suite not in METHODS or workers < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid suite, worker count, or timeout")
    if suite == "scaling" and workers != 1:
        raise ValueError("scaling must be measured serially")
    cohort, methods = METHODS[suite]
    path = source_dir / "inputs" / (cohort + ".json")
    data = frozen_data().load_dataset(path)
    source_cases = data["cases"]
    expected = read_json(FROZEN / "configs/expected_inputs.json")[cohort]
    actual = {"inputs": len(source_cases),
              "identity_sha256": digest(sorted((c["case_id"], c["input_sha256"])
                                              for c in source_cases))}
    if actual != expected:
        raise ValueError("source is not the approved frozen hypothetical cohort")
    if limit is not None and not 1 <= limit <= len(source_cases):
        raise ValueError("limit outside cohort")
    cases = [convert(c) for c in source_cases[:limit]]
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("duplicate converted case identities")
    manifest = dict(schema=SCHEMA, suite=suite, source_cohort=cohort,
                    source_file_sha256=sha(path), source_identity=actual,
                    model="HARVEST_BEFORE_DEBIT", release_floor_stage="PRE_HARVEST",
                    capacity=None, background_power=0,
                    run_kind="bounded_check" if limit is not None else "full_cohort",
                    requested_limit=limit, planned_cases=len(cases),
                    methods=list(methods), requests=len(cases)*len(methods),
                    timeout_wall_seconds=timeout, workers=workers,
                    python_version=platform.python_version(),
                    platform=platform.platform(), source_code_sha256=source_identity(),
                    input_sha256=digest(cases), case_ids=[c["case_id"] for c in cases])
    return manifest, cases


def evaluate(case, method):
    from experiments.same_slot import ab_rta
    m = case["model"]
    if m["harvest_order"] != "HARVEST_BEFORE_DEBIT" or m["release_floor_stage"] != "PRE_HARVEST":
        raise ValueError("new energy timing required")
    if m["capacity"] is not None or m["background_power"] != 0:
        raise ValueError("unsupported storage/background model")
    begin = time.process_time()
    if method in ("analytic", "flow"):
        result = ab_rta.analyze_case(case, bound=method)
        status = result["status"]
    elif method == "translated_seq":
        # Exact conservative translation already used by ab_rta.analyze_case:
        # A(t) = stored E(t) + H(t), with every-release floor e0+beta[1].
        tasks = [ab_rta.Task(t["name"], t["C"], t["D"], t["T"], t["power"])
                 for t in m["tasks"]]
        result = ab_rta.frozen.model.least_certificate(
            tasks, m["processors"], m["beta"], m["release_floor"] + m["beta"][1])
        status = result["status"]
        if status == "NO_CERTIFICATE_IN_THIS_FAMILY":
            status = "NO_CERTIFICATE"
    else:
        raise ValueError("unknown method")
    seconds = time.process_time() - begin
    if status not in {"CERTIFIED", "NO_CERTIFICATE"}:
        raise ValueError("unsupported result status: " + status)
    bounds = None
    if status == "CERTIFIED":
        if result["verification"]["status"] != "PASS":
            raise ValueError("certificate replay failed")
        bounds = result["certificate"]["response_bounds"]
        if len(bounds) != len(m["tasks"]) or any(
                type(r) is not int or not t["C"] <= r <= t["D"]
                for r, t in zip(bounds, m["tasks"])):
            raise ValueError("invalid response vector")
    return dict(status=status, result=result, response_bounds=bounds,
                solver_cpu_seconds=seconds)


def execute(case, method, timeout):
    start = time.monotonic()
    try:
        p = subprocess.run([sys.executable, str(ENTRY), "--worker", method],
                           input=json.dumps(case), text=True, capture_output=True,
                           cwd=ROOT, timeout=timeout)
        if p.returncode != 0:
            value = dict(status="ERROR", error=p.stderr[-4000:])
        else:
            value = json.loads(p.stdout)
            if value.get("status") not in {"CERTIFIED", "NO_CERTIFICATE"}:
                raise ValueError("unexpected worker result")
    except subprocess.TimeoutExpired:
        value = dict(status="TIMEOUT")
    except Exception as exc:
        value = dict(status="ERROR", error=str(exc))
    row = dict(case_id=case["case_id"], source_case_id=case["source_case_id"],
               input_sha256=case["input_sha256"], method=method,
               wall_seconds=time.monotonic()-start, **value)
    row["row_sha256"] = digest(row)
    return row


def compact_row(row):
    # Retain full certificates on disk only; coverage/timing summaries need
    # just the checked status, bounds and CPU time.
    return {k: row[k] for k in ("case_id", "source_case_id", "input_sha256",
            "method", "status", "response_bounds", "solver_cpu_seconds")
            if k in row}


def checked_rows(path, cases, methods):
    allowed = {(c["case_id"], m): c for c in cases for m in methods}
    found = {}
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                key = row["case_id"], row["method"]
                if (key not in allowed or key in found or
                        row["input_sha256"] != allowed[key]["input_sha256"] or
                        row["source_case_id"] != allowed[key]["source_case_id"] or
                        row["status"] not in TERMINAL or
                        row["row_sha256"] != digest({k:v for k,v in row.items()
                                                     if k != "row_sha256"})):
                    raise ValueError("invalid, changed or duplicate persisted result")
                found[key] = compact_row(row)
    return found


def summarize(cases, methods, rows):
    def cell(case):
        meta = case["metadata"]
        return dict(processors=case["model"]["processors"],
                    tasks=len(case["model"]["tasks"]),
                    target_uc=meta.get("target_uc"),
                    target_ue=meta.get("target_ue"),
                    deadline_mode=meta.get("deadline_mode"),
                    harvest_gap=meta.get("harvest_gap"),
                    time_factor=meta.get("time_factor"),
                    cohort=case["cohort"])
    groups = defaultdict(list)
    for c in cases:
        groups[json.dumps(cell(c), sort_keys=True)].append(c)
    groups["ALL"] = cases
    summaries, pairs = [], []
    for label, group in groups.items():
        for m in methods:
            selected = [rows.get((c["case_id"], m)) for c in group]
            counts = Counter(r["status"] if r else "MISSING" for r in selected)
            # Completed-only cost; do not impute timeout budget as execution time.
            times = [r["solver_cpu_seconds"] for r in selected if r and
                     r["status"] in {"CERTIFIED", "NO_CERTIFICATE"}]
            summaries.append(dict(cell=label, method=m, planned=len(group),
                                  certified=counts["CERTIFIED"],
                                  no_certificate=counts["NO_CERTIFICATE"],
                                  timeout=counts["TIMEOUT"], error=counts["ERROR"],
                                  missing=counts["MISSING"],
                                  certified_fraction=counts["CERTIFIED"]/len(group),
                                  completed_cpu_median=statistics.median(times) if times else None))
        a, b = methods
        counts = Counter()
        rd_a, rd_b, equal_vectors = [], [], 0
        timing_a, timing_b = [], []
        for c in group:
            left, right = (rows.get((c["case_id"], m)) for m in methods)
            if not left or not right or any(
                    r["status"] in {"TIMEOUT", "ERROR"} for r in (left, right)):
                counts["unresolved"] += 1
                continue
            ca, cb = left["status"] == "CERTIFIED", right["status"] == "CERTIFIED"
            counts[(ca, cb)] += 1
            timing_a.append(left["solver_cpu_seconds"])
            timing_b.append(right["solver_cpu_seconds"])
            if ca and cb:
                va, vb = left["response_bounds"], right["response_bounds"]
                equal_vectors += int(va == vb)
                d = [t["D"] for t in c["model"]["tasks"]]
                rd_a.append(statistics.mean(r/t for r,t in zip(va,d)))
                rd_b.append(statistics.mean(r/t for r,t in zip(vb,d)))
        pairs.append(dict(cell=label, method_a=a, method_b=b, planned=len(group),
                          both_certified=counts[(True,True)], a_only=counts[(True,False)],
                          b_only=counts[(False,True)], neither=counts[(False,False)],
                          unresolved=counts["unresolved"], equal_response_vectors=equal_vectors,
                          mean_response_deadline_ratio_a=statistics.mean(rd_a) if rd_a else None,
                          mean_response_deadline_ratio_b=statistics.mean(rd_b) if rd_b else None,
                          paired_completed=len(timing_a),
                          paired_cpu_median_a=statistics.median(timing_a) if timing_a else None,
                          paired_cpu_median_b=statistics.median(timing_b) if timing_b else None))
    return summaries, pairs


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(source_dir, output, suite, limit=None, timeout=30, workers=1, resume=False):
    manifest, cases = build_plan(source_dir, suite, limit, timeout, workers)
    output.mkdir(parents=True, exist_ok=resume)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config_path, inputs_path = output/"config.json", output/"inputs.json"
        results = output/"results.jsonl"
        if config_path.exists():
            if not resume or read_json(config_path) != manifest or read_json(inputs_path) != cases:
                raise ValueError("resume configuration, source, or inputs changed")
        elif resume:
            raise ValueError("no valid config to resume")
        else:
            write_json(config_path, manifest)
            write_json(inputs_path, cases)
        methods = manifest["methods"]
        rows = checked_rows(results, cases, methods)
        pending = [(c,m) for c in cases for m in methods if (c["case_id"],m) not in rows]
        # Keep only one bounded request per worker in flight, so interrupting
        # a run never waits for an entire queued formal cohort. Each completed
        # row is flushed before scheduling another request.
        pending_iter = iter(pending)
        with ThreadPoolExecutor(max_workers=workers) as pool, results.open("a", encoding="utf-8") as out:
            def submit():
                item = next(pending_iter, None)
                return None if item is None else pool.submit(execute, *item, timeout)
            active = {future for _ in range(workers) if (future := submit()) is not None}
            while active:
                finished, active = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    row = future.result()
                    out.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")
                    out.flush()
                    os.fsync(out.fileno())
                    rows[(row["case_id"], row["method"])] = compact_row(row)
                    next_future = submit()
                    if next_future is not None:
                        active.add(next_future)
        rows = checked_rows(results, cases, methods)
        summaries, pairs = summarize(cases, methods, rows)
        write_csv(output/"summary.csv", summaries)
        write_csv(output/"paired.csv", pairs)
        complete = len(rows) == manifest["requests"] and not any(r["status"]=="ERROR" for r in rows.values())
        terminal = dict(schema=SCHEMA, completed=len(rows), planned=manifest["requests"],
                        counts=dict(Counter(r["status"] for r in rows.values())),
                        status="COMPLETE" if complete else "ERROR",
                        config_sha256=sha(config_path), results_sha256=sha(results))
        write_json(output/"status.json", terminal)
        if complete:
            write_json(output/"COMPLETE.json", terminal)
        return terminal
