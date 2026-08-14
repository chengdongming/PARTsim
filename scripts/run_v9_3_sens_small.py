#!/usr/bin/env python3
"""Plan, smoke-test, or execute the frozen SENS-SMALL experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.rta_load_cross import execute_requests  # noqa: E402
from experiments.v9_3.sens_small import (  # noqa: E402
    FORMAL_TIMEOUT_FIRST,
    FORMAL_TIMEOUT_RETRY,
    SENS_SMALL_PROTOCOL,
    U_C_VALUES,
    conditions,
    expand_condition_tasksets,
    generate_scaled_skeletons,
    make_requests,
    plan_rows,
    plan_summary,
)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def _mode_rows(mode: str) -> tuple[list[dict], list[dict], list[dict]]:
    if mode == "smoke":
        skeletons = generate_scaled_skeletons(
            target_ucs=(U_C_VALUES[0],), count=1,
            system_config=PROJECT_ROOT / "system_config_unified_template.yml",
            allow_bounded=True,
        )
        tasksets = expand_condition_tasksets(skeletons)
        requests = make_requests(tasksets, timeout=10.0)
        return skeletons, tasksets, requests
    skeletons = generate_scaled_skeletons(
        system_config=PROJECT_ROOT / "system_config_unified_template.yml",
    )
    tasksets = expand_condition_tasksets(skeletons)
    requests = make_requests(tasksets, timeout=FORMAL_TIMEOUT_FIRST)
    return skeletons, tasksets, requests


def _plan_document(mode: str, rows: list[dict]) -> dict:
    summary = plan_summary() if mode == "full" else {
        "protocol": SENS_SMALL_PROTOCOL,
        "U_C_POINTS": 1,
        "SKELETONS_PER_UC": 1,
        "UNIQUE_SKELETONS": 1,
        "CONDITIONS_PER_SKELETON": 5,
        "METHODS": 4,
        "REQUESTS": 20,
        "CENTER_REQUESTS": 4,
        "PAIRING": "PASS",
        "MISSING": 0,
        "DUPLICATE": 0,
        "PARTIAL_METHOD_GROUP": 0,
        "PARTIAL_CONDITION_GROUP": 0,
        "solver_invocations": 0,
    }
    return {
        "protocol": SENS_SMALL_PROTOCOL,
        "mode": mode,
        "summary": summary,
        "rows": rows,
        "deadline_values": ["1/2", "3/4", "1"],
        "latency_values": ["0", "2/5", "2"],
        "center_generated_once": True,
        "plan_identity": __import__("hashlib").sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "timeout_first": 10.0 if mode == "smoke" else FORMAL_TIMEOUT_FIRST,
        "timeout_retry": 10.0 if mode == "smoke" else FORMAL_TIMEOUT_RETRY,
        "retry_policy": "timeout_once",
    }


def _strict_config(existing: dict, requested: dict) -> None:
    keys = ("protocol", "mode", "plan_identity", "timeout_first", "timeout_retry", "retry_policy")
    mismatches = [key for key in keys if existing.get(key) != requested.get(key)]
    if mismatches:
        raise ValueError("resume configuration mismatch: " + ", ".join(mismatches))


def run(mode: str, output: Path, *, resume: bool, workers: int) -> dict:
    if workers < 1:
        raise ValueError("workers must be positive")
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "plan.json"
    config_path = output / "run_config.json"
    results_path = output / "results.jsonl"
    if resume and not plan_path.is_file():
        raise ValueError("resume requires an existing plan.json")
    skeletons, tasksets, requests = _mode_rows(mode)
    document = _plan_document(mode, [
        {"request_id": row["request_id"], **row["metadata"]}
        for row in requests
    ])
    if plan_path.is_file():
        existing_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing_plan.get("plan_identity") != document["plan_identity"]:
            raise ValueError("resume plan identity mismatch")
    else:
        _write_json(plan_path, document)
        _write_jsonl(output / "skeletons.jsonl", skeletons)
        _write_jsonl(output / "tasksets.jsonl", tasksets)
    requested_config = {
        "protocol": SENS_SMALL_PROTOCOL,
        "mode": mode,
        "plan_identity": document["plan_identity"],
        "timeout_first": document["timeout_first"],
        "timeout_retry": document["timeout_retry"],
        "retry_policy": "timeout_once",
        "workers": workers,
        "scientific_constants": {
            "U_C": ["3/10", "7/10"], "U_E": "4/5", "rho": "11/2",
            "E0": "0", "skeletons_per_uc": 300, "conditions": 5, "methods": 4,
        },
    }
    if config_path.is_file():
        _strict_config(json.loads(config_path.read_text(encoding="utf-8")), requested_config)
    else:
        _write_json(config_path, requested_config)
    existing = _read_jsonl(results_path) if resume else []
    seen = {str(row.get("request_id")) for row in existing}
    pending = [row for row in requests if row["request_id"] not in seen]
    if pending:
        with results_path.open("a", encoding="utf-8") as handle:
            def save(row: dict) -> None:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
            execute_requests(
                pending, workers=workers,
                timeout_first=document["timeout_first"],
                timeout_retry=document["timeout_retry"], on_result=save,
            )
    final = _read_jsonl(results_path)
    requested_config["planned_requests"] = len(requests)
    requested_config["observed_results"] = len(final)
    requested_config["pending"] = len(requests) - len({row.get("request_id") for row in final})
    _write_json(config_path, requested_config)
    return {
        "mode": mode, "requests": len(requests), "pending": requested_config["pending"],
        "conditions": len(conditions()), "methods": 4,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.plan or not (args.smoke or args.run):
        print(json.dumps(plan_summary(), sort_keys=True))
        return 0
    if not args.output:
        parser.error("--output is required for --smoke or --run")
    result = run("smoke" if args.smoke else "full", Path(args.output), resume=args.resume, workers=args.workers)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
