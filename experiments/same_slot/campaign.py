"""Reproducible paired campaigns for the current unlimited-storage RTA.

AB retains its existing analytic/flow and conservative shifted SEQ engines.
AN uses the reviewed independent integer and analytical window analyses.
Both use identical verified hypothetical input cohorts.
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
from itertools import combinations
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "experiments/asapblock_unlimited"
ENTRY = ROOT / "scripts/run_same_slot_rta_campaign.py"
SCHEMA = "ASAP_BLOCK_SAME_SLOT_CAMPAIGN_V1"  # preserved for legacy AB suites
JOINT_SCHEMA = "AB_AN_SAME_SLOT_CAMPAIGN_V2"
AB_METHODS = ("RTA-AB-OPT", "RTA-AB-ANA", "RTA-AB-SEQ")
AN_METHODS = ("RTA-AN-OPT", "RTA-AN-ANA", "RTA-AN-MR")
FORMAL_METHODS = AB_METHODS + AN_METHODS
AB_ALIASES = dict(zip(AB_METHODS, ("flow", "analytic", "translated_seq")))
LEGACY_METHODS = ("flow", "analytic", "translated_seq")
METHODS = {
    "coverage": ("main", ("analytic", "translated_seq")),
    "sensitivity": ("latest", ("analytic", "translated_seq")),
    "flow": ("flow", ("analytic", "flow")),
    "scaling": ("scale_all", ("analytic", "translated_seq")),
    "constructed": ("constructed", ("analytic", "flow")),
}
# Additive suite names leave all existing AB suite IDs and method strings intact.
for label, cohort in (("coverage", "main"), ("sensitivity", "latest"),
                      ("reference", "flow"), ("scaling", "scale_all"),
                      ("constructed", "constructed")):
    METHODS["an_"+label] = (cohort, AN_METHODS)
    METHODS["joint_"+label] = (cohort, FORMAL_METHODS)
# Freeze the auxiliary bound to isolate the effect of the main frequency bound.
METHODS["an_frequency"] = ("main", ("RTA-AN-ANA", "RTA-AN-MR"))
METHODS["an_fourcore10"] = ("fourcore10", AN_METHODS)
METHODS["joint_fourcore10"] = ("fourcore10", FORMAL_METHODS)
METHODS["an_frequency_fourcore10"] = ("fourcore10", ("RTA-AN-ANA", "RTA-AN-MR"))
TERMINAL = {"CERTIFIED", "NO_CERTIFICATE", "TIMEOUT", "RESOURCE_LIMIT", "ERROR"}
WORKER_STATUSES = {"CERTIFIED", "NO_CERTIFICATE", "RESOURCE_LIMIT"}


def scheduler_family(method):
    if method in AN_METHODS:
        return "AN"
    if method in AB_METHODS or method in LEGACY_METHODS:
        return "AB"
    raise ValueError("unknown RTA method")


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
    paths = [Path(__file__), ENTRY, ROOT / "scripts/run_same_slot_rta.py"] + [Path(__file__).with_name(name) for name in
             ("ab_rta.py", "an_rta.py", "an_rta_bounds.py", "fourcore10.py")]
    paths += [ROOT/"experiments/common/global_task_generator.py",
              ROOT/"configs/templates/system_config_unified_template.yml"]
    paths += sorted(p for p in (FROZEN / "runtime").rglob("*.py"))
    paths += sorted((FROZEN / "configs").glob("*.json"))
    paths += [FROZEN / "prepare.py"]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def build_plan(source_dir, suite, limit=None, timeout=30, workers=1):
    if suite not in METHODS or workers < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid suite, worker count, or timeout")
    if suite in ("scaling", "an_scaling", "joint_scaling") and workers != 1:
        raise ValueError("scaling must be measured serially")
    cohort, methods = METHODS[suite]
    path = source_dir / "inputs" / (cohort + ".json")
    if cohort == "fourcore10":
        from . import fourcore10
        data = fourcore10.load(path)
        source_cases = data["cases"]
        actual = {"inputs": len(source_cases),
                  "identity_sha256": digest(sorted((c["case_id"], c["input_sha256"])
                                                   for c in source_cases)),
                  "generation": data["generation"]}
        if limit is not None and not 1 <= limit <= len(source_cases):
            raise ValueError("limit outside cohort")
        cases = source_cases[:limit]
    else:
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
    manifest = dict(schema=JOINT_SCHEMA if any(m in AN_METHODS for m in methods) else SCHEMA,
                    suite=suite, source_cohort=cohort,
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
    if any(m in AN_METHODS for m in methods):
        manifest.update(an_gap="base" if suite in ("an_frequency", "an_frequency_fourcore10") else "auto",
                        analysis_methods="FORMAL_NAMES",
                        window_search="FULL_INTEGER_WINDOWS")
    return manifest, cases


def validate_formal_result(case, method, value, an_gap="auto"):
    """Bind formal summaries to the requested case and worker certificate.

    The worker already replays the mathematical proof. Resume checks the
    result's identity, status and vectors without repeating costly analysis.
    The row hash is an integrity check, not a cryptographic attestation.
    """
    if method not in FORMAL_METHODS:
        return

    def require(condition, message):
        if not condition:
            raise ValueError("invalid formal result: " + message)

    status = value.get("status")
    if status in ("TIMEOUT", "ERROR"):
        require(not any(k in value for k in ("result", "response_bounds",
                    "first_bounds", "solver_cpu_seconds")), "unresolved result carries bounds")
        return
    require(status in WORKER_STATUSES, "unsupported status")
    family = scheduler_family(method)
    require(value.get("scheduler") == family, "wrong scheduler")
    result = value.get("result")
    require(isinstance(result, dict), "missing worker result")
    seconds = value.get("solver_cpu_seconds")
    require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
            "invalid timing")
    expected_status = result.get("status")
    if method == "RTA-AB-SEQ" and expected_status == "NO_CERTIFICATE_IN_THIS_FAMILY":
        expected_status = "NO_CERTIFICATE"
    require(expected_status == status, "inconsistent terminal status")
    m = case["model"]
    rows = m["tasks"]
    seq = method == "RTA-AB-SEQ"
    if family == "AN":
        from . import an_rta
        gap = an_rta.gap_kind(method, an_gap)
        version, model = an_rta.VERSION, an_rta.MODEL
        fp = an_rta.fingerprint(case, method, gap)
        require(result.get("method") == method and result.get("gap") == gap,
                "wrong AN method or auxiliary bound")
    else:
        from . import ab_rta
        tasks = [ab_rta.Task(t["name"], t["C"], t["D"], t["T"], t["power"])
                 for t in rows]
        if seq:
            frozen = ab_rta.frozen.model
            version, model = frozen.VERSION, frozen.MODEL
            fp = frozen.fingerprint(tasks, m["processors"], m["beta"],
                                    m["release_floor"] + m["beta"][1])
        else:
            version, model = ab_rta.VERSION, ab_rta.MODEL
            fp = ab_rta.fingerprint(tasks, m["processors"], m["beta"],
                                    m["release_floor"], AB_ALIASES[method])
    require(result.get("version") == version and (seq or result.get("model") == model),
            "wrong worker model/version")
    require("response_bounds" in value and "first_bounds" in value and "certificate" in result,
            "missing result fields")
    cert = result["certificate"]
    response, first = value["response_bounds"], value["first_bounds"]
    if status != "CERTIFIED":
        require(cert is None and response is None and first is None
                and result.get("verification") is None, "uncertified result carries proof or bounds")
        return
    require(isinstance(cert, dict) and isinstance(result.get("verification"), dict)
            and result["verification"].get("status") == "PASS", "missing verified certificate")
    require(cert.get("version") == version and cert.get("model") == model
            and cert.get("input_sha256") == fp, "wrong certificate input/model/version")
    require(isinstance(response, list) and len(response) == len(rows)
            and all(type(r) is int and t["C"] <= r <= t["D"]
                    for t, r in zip(rows, response))
            and digest(response) == digest(cert.get("response_bounds")), "wrong response vector")
    if seq:
        require(first is None and "profile" not in cert, "unexpected SEQ first-unit bound")
        return
    profile = cert.get("profile")
    require(isinstance(profile, (list, tuple)) and len(profile) == len(rows)
            and all(isinstance(p, (list, tuple)) and len(p) == 2
                    and type(p[0]) is int and type(p[1]) is int
                    and 1 <= p[0] <= p[1] == r and (t["C"] != 1 or p[0] == r)
                    for p, t, r in zip(profile, rows, response)), "invalid certified profile")
    require(digest(first) == digest([p[0] for p in profile]), "wrong first-unit vector")
    if family == "AN":
        require(cert.get("method") == method and cert.get("gap") == gap
                and cert.get("task_names") == [t["name"] for t in rows]
                and digest(cert.get("first_bounds")) == digest(first), "wrong AN certificate identity")
    else:
        require(cert.get("bound") == AB_ALIASES[method], "wrong AB structural bound")


def evaluate(case, method, an_gap="auto"):
    """One bounded-worker request. Preserve legacy AB result payloads."""
    m = case["model"]
    family = scheduler_family(method)
    if m["harvest_order"] != "HARVEST_BEFORE_DEBIT" or m["release_floor_stage"] != "PRE_HARVEST":
        raise ValueError("new energy timing required")
    if m["capacity"] is not None or m["background_power"] != 0:
        raise ValueError("unsupported storage/background model")
    if method in FORMAL_METHODS:
        # All six formal methods agree on the exact same input contract,
        # including explicit scale, strict priority and every-release floor.
        from experiments.same_slot import an_rta
        an_rta.normalize_case(case)
    begin = time.process_time()
    if family == "AN":
        from experiments.same_slot import an_rta
        result = an_rta.analyze_case(case, method=method, gap=an_gap)
        status = result["status"]
    else:
        from experiments.same_slot import ab_rta
        engine_method = AB_ALIASES.get(method, method)
        if engine_method in ("analytic", "flow"):
            result = ab_rta.analyze_case(case, bound=engine_method)
            status = result["status"]
        else:
            # Conservative translation already used by ab_rta.analyze_case.
            # A(t) = E(t)+H(t); release floor e0+beta[1]. Do not
            # silently claim this is a native same-slot derivation.
            tasks = [ab_rta.Task(t["name"], t["C"], t["D"], t["T"], t["power"])
                     for t in m["tasks"]]
            result = ab_rta.frozen.model.least_certificate(
                tasks, m["processors"], m["beta"], m["release_floor"] + m["beta"][1])
            status = result["status"]
            if status == "NO_CERTIFICATE_IN_THIS_FAMILY":
                status = "NO_CERTIFICATE"
    seconds = time.process_time() - begin
    if status not in WORKER_STATUSES:
        raise ValueError("unsupported result status: " + status)
    response, first = None, None
    if status == "CERTIFIED":
        if result["verification"]["status"] != "PASS":
            raise ValueError("certificate replay failed")
        cert = result["certificate"]
        response = cert["response_bounds"]
        if len(response) != len(m["tasks"]) or any(
                type(r) is not int or not t["C"] <= r <= t["D"]
                for r, t in zip(response, m["tasks"])):
            raise ValueError("invalid response vector")
        if "profile" in cert:
            first = [p[0] for p in cert["profile"]]
            if len(first) != len(response) or any(
                    type(f) is not int or not 1 <= f <= r
                    for f, r in zip(first, response)):
                raise ValueError("invalid first-unit vector")
    value = dict(status=status, result=result, response_bounds=response,
                 solver_cpu_seconds=seconds)
    if method in FORMAL_METHODS:
        value.update(first_bounds=first, scheduler=family)
        validate_formal_result(case, method, value, an_gap)
    return value


def execute(case, method, timeout, an_gap="auto"):
    start = time.monotonic()
    try:
        cmd = [sys.executable, str(ENTRY), "--worker", method]
        if an_gap != "auto":
            cmd.append(an_gap)
        p = subprocess.run(cmd,
                           input=json.dumps(case), text=True, capture_output=True,
                           cwd=ROOT, timeout=timeout)
        if p.returncode != 0:
            value = dict(status="ERROR", error=p.stderr[-4000:])
        else:
            value = json.loads(p.stdout)
            if value.get("status") not in WORKER_STATUSES:
                raise ValueError("unexpected worker result")
            validate_formal_result(case, method, value, an_gap)
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
            "method", "status", "response_bounds", "first_bounds", "solver_cpu_seconds")
            if k in row}


def checked_rows(path, cases, methods, an_gap="auto"):
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
                validate_formal_result(allowed[key], key[1], row, an_gap)
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
    formal = any(m in FORMAL_METHODS for m in methods)
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
            summary = dict(cell=label, method=m, planned=len(group),
                                  certified=counts["CERTIFIED"],
                                  no_certificate=counts["NO_CERTIFICATE"],
                                  timeout=counts["TIMEOUT"], error=counts["ERROR"],
                                  missing=counts["MISSING"],
                                  certified_fraction=counts["CERTIFIED"]/len(group),
                                  completed_cpu_median=statistics.median(times) if times else None)
            if formal:
                summary["resource_limit"] = counts["RESOURCE_LIMIT"]
            summaries.append(summary)
        for a, b in combinations(methods, 2):
            counts = Counter()
            rd_a, rd_b, equal_vectors = [], [], 0
            timing_a, timing_b = [], []
            for c in group:
                left, right = (rows.get((c["case_id"], m)) for m in (a, b))
                if not left or not right or any(
                        r["status"] in {"TIMEOUT", "RESOURCE_LIMIT", "ERROR"} for r in (left, right)):
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
            pair = dict(cell=label, method_a=a, method_b=b, planned=len(group),
                              both_certified=counts[(True,True)], a_only=counts[(True,False)],
                              b_only=counts[(False,True)], neither=counts[(False,False)],
                              unresolved=counts["unresolved"], equal_response_vectors=equal_vectors,
                              mean_response_deadline_ratio_a=statistics.mean(rd_a) if rd_a else None,
                              mean_response_deadline_ratio_b=statistics.mean(rd_b) if rd_b else None,
                              paired_completed=len(timing_a),
                              paired_cpu_median_a=statistics.median(timing_a) if timing_a else None,
                              paired_cpu_median_b=statistics.median(timing_b) if timing_b else None)
            if formal:
                pair["comparison_scope"] = ("SAME_SCHEDULER" if scheduler_family(a)==scheduler_family(b)
                                            else "CROSS_SCHEDULER")
            pairs.append(pair)
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
            if not resume or digest(read_json(config_path)) != digest(manifest) or digest(read_json(inputs_path)) != digest(cases):
                raise ValueError("resume configuration, source, or inputs changed")
        elif resume:
            raise ValueError("no valid config to resume")
        else:
            write_json(config_path, manifest)
            write_json(inputs_path, cases)
        methods = manifest["methods"]
        rows = checked_rows(results, cases, methods, manifest.get("an_gap", "auto"))
        pending = [(c,m) for c in cases for m in methods if (c["case_id"],m) not in rows]
        # Keep only one bounded request per worker in flight, so interrupting
        # a run never waits for an entire queued formal cohort. Each completed
        # row is flushed before scheduling another request.
        pending_iter = iter(pending)
        with ThreadPoolExecutor(max_workers=workers) as pool, results.open("a", encoding="utf-8") as out:
            def submit():
                item = next(pending_iter, None)
                return None if item is None else pool.submit(execute, *item, timeout, manifest.get("an_gap", "auto"))
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
        rows = checked_rows(results, cases, methods, manifest.get("an_gap", "auto"))
        summaries, pairs = summarize(cases, methods, rows)
        write_csv(output/"summary.csv", summaries)
        write_csv(output/"paired.csv", pairs)
        complete = len(rows) == manifest["requests"] and not any(r["status"]=="ERROR" for r in rows.values())
        terminal = dict(schema=manifest["schema"], completed=len(rows), planned=manifest["requests"],
                        counts=dict(Counter(r["status"] for r in rows.values())),
                        status="COMPLETE" if complete else "ERROR",
                        config_sha256=sha(config_path), results_sha256=sha(results))
        write_json(output/"status.json", terminal)
        if complete:
            write_json(output/"COMPLETE.json", terminal)
        return terminal
