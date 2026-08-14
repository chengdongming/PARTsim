#!/usr/bin/env python3
"""Direct B4 runner: plan, bounded smoke, or full execution."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.b4_priority_energy.experiment import (  # noqa: E402
    GRID,
    PHASE_COUNTS,
    TASK_COUNT,
    generate_base_taskset,
    iter_requests,
    materialize_request,
    request_plan,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def _smoke_requests():
    # One deterministic taskset, two energy levels, and three scheduler IDs.
    selected = []
    for request in iter_requests(("pilot",)):
        if request.utilization == "0.3" and request.replicate_index == 1 and request.rho_E == "1" and request.lambda_E in {"0.70", "0.85"} and request.algorithm in {"ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC"}:
            selected.append(request)
    return selected


def _full_requests():
    return list(iter_requests())


def _plan_document(mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "experiment": "B4-PE",
        "mode": mode,
        "processors": 4,
        "task_count": TASK_COUNT,
        "horizon_ms": 30000,
        "request_count": len(rows),
        "rows": rows,
        "phase_counts": PHASE_COUNTS if mode == "full" else {"pilot": len(rows)},
        "algorithm_ids": sorted({row["algorithm"] for row in rows}),
        "energy_conditions": sorted({(row["lambda_E"], row["rho_E"]) for row in rows}),
        "pairing_complete": len({row["case_id"] for row in rows}) == len(rows),
    }


def _strict_resume_config(existing: dict[str, Any], current: dict[str, Any]) -> None:
    fields = ("experiment", "mode", "processors", "task_count", "horizon_ms", "request_count", "plan_sha256")
    mismatches = [field for field in fields if existing.get(field) != current.get(field)]
    if mismatches:
        raise ValueError("resume scientific configuration mismatch: " + ", ".join(mismatches))


def _run_one(case: dict[str, Any], root: Path, timeout: float, retry_timeout: float) -> dict[str, Any]:
    command = [case["command"][0], case["system_config_artifact"], case["taskset_artifact"], *case["command"][3:5], case["result_relpath"], *case["command"][6:]]
    attempts = []
    for attempt, limit in enumerate((timeout, retry_timeout), start=1):
        result_path = root / case["result_relpath"]
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if result_path.exists():
            result_path.unlink()
        try:
            process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=limit)
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                returncode = None
                attempts.append({"attempt": attempt, "status": "timeout", "timeout_seconds": limit})
                if attempt == 1:
                    continue
                return {**case, "status": "timeout", "technical_error": False, "attempts": attempts, "stdout": stdout[-4000:], "stderr": stderr[-4000:]}
            status = "success" if returncode == 0 and result_path.is_file() and result_path.stat().st_size > 0 else "error"
            attempts.append({"attempt": attempt, "status": status, "returncode": returncode, "timeout_seconds": limit})
            if status == "success":
                return {**case, "status": status, "technical_error": False, "attempts": attempts, "stdout": stdout[-4000:], "stderr": stderr[-4000:]}
            if attempt == 1:
                continue
            return {**case, "status": status, "technical_error": True, "attempts": attempts, "stdout": stdout[-4000:], "stderr": stderr[-4000:]}
        except OSError as exc:
            attempts.append({"attempt": attempt, "status": "error", "error": str(exc), "timeout_seconds": limit})
            if attempt == 2:
                return {**case, "status": "error", "technical_error": True, "attempts": attempts, "stdout": "", "stderr": str(exc)}
    raise AssertionError("unreachable")


def run(mode: str, output: Path, resume: bool) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    requests = _smoke_requests() if mode == "smoke" else _full_requests()
    rows = []
    cache: dict[str, dict[str, Any]] = {}
    for request in requests:
        rows.append(materialize_request(request, output, cache))
    plan = _plan_document(mode, rows)
    plan_bytes = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    metadata = {**{key: plan[key] for key in ("experiment", "mode", "processors", "task_count", "horizon_ms", "request_count")}, "plan_sha256": __import__("hashlib").sha256(plan_bytes).hexdigest()}
    metadata_path = output / "run_metadata.json"
    if metadata_path.is_file() and resume:
        _strict_resume_config(json.loads(metadata_path.read_text(encoding="utf-8")), metadata)
    elif metadata_path.is_file() and not resume:
        raise ValueError("output already exists; use --resume")
    else:
        (output / "plan.json").write_bytes(plan_bytes + b"\n")
        metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    results_path = output / "results.jsonl"
    completed = {str(row.get("case_id")) for row in _read_jsonl(results_path)} if resume else set()
    timeout, retry = ((30.0, 60.0) if mode == "smoke" else (300.0, 600.0))
    for case in rows:
        if case["case_id"] in completed:
            continue
        result = _run_one(case, output, timeout, retry)
        _append_jsonl(results_path, result)
    observed = _read_jsonl(results_path)
    return {"mode": mode, "requests": len(rows), "observed": len(observed), "pending": len(rows) - len({row.get("case_id") for row in observed}), "technical_errors": sum(bool(row.get("technical_error")) for row in observed)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--smoke", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=1, help="reserved execution parameter; direct runner is deterministic sequential by default")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        rows = request_plan()
        if args.output is not None:
            args.output.mkdir(parents=True, exist_ok=True)
            plan = _plan_document("full", [dict(row, command=[]) for row in rows])
            (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        print(json.dumps({"experiment": "B4-PE", "request_count": len(rows), "duplicate": len(rows) - len({row["case_id"] for row in rows}), "missing": 0, "pairing_complete": True, "phase_counts": PHASE_COUNTS}, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required for --smoke/--run")
    if args.workers < 1:
        parser.error("--workers must be positive")
    print(json.dumps(run("smoke" if args.smoke else "full", args.output, args.resume), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
