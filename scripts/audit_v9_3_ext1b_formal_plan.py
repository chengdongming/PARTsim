#!/usr/bin/env python3
"""Read-only acceptance audit for a materialized EXT-1B formal plan."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_capacity_contract import (  # noqa: E402
    NATIVE_ENERGY_EPSILON_J,
)
from experiments.v9_3.ext1b_config import (  # noqa: E402
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import (  # noqa: E402
    EXT1B_FAIRNESS_FIELDS,
    Ext1BRunner,
    assert_ext1b_fair_pairing,
    verify_file_hashes,
)
from experiments.v9_3.taskset_store import (  # noqa: E402
    PAIRING_MANIFEST_SCHEMA,
)


class PlanAuditError(RuntimeError):
    """Raised when a formal plan is incomplete, mixed, or unauditable."""


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise PlanAuditError(f"required plan table is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanAuditError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PlanAuditError(f"JSON artifact must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise PlanAuditError(f"simulator identity file is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(PROJECT_ROOT), capture_output=True,
            text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PlanAuditError("cannot establish repository identity") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_equal(observed: Any, expected: Any, label: str) -> None:
    if observed != expected:
        raise PlanAuditError(
            f"{label} mismatch: observed={observed!r}, expected={expected!r}"
        )


def _runtime_config_copy(
    source_config: Path, output_root: Path, taskset_store: Path,
    simulator_bin: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = load_ext1b_config(source_config)
    run_path = output_root / "run_config.yaml"
    try:
        persisted = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlanAuditError("cannot read persisted run_config.yaml") from exc
    if not isinstance(persisted, dict):
        raise PlanAuditError("persisted run_config.yaml must be a mapping")

    expected_output = str(output_root.resolve())
    expected_store = str(taskset_store.resolve())
    expected_simulator = str(simulator_bin.resolve())
    _require_equal(
        persisted["execution"]["output_root"], expected_output,
        "run_config output_root",
    )
    _require_equal(
        persisted["execution"]["taskset_store"], expected_store,
        "run_config taskset_store",
    )
    _require_equal(
        persisted["simulation"]["simulator_bin"], expected_simulator,
        "run_config simulator_bin",
    )
    if persisted["execution"].get("resume") is not False:
        raise PlanAuditError("formal plan run_config must keep resume=false")

    # Path overrides are the runner's documented deployment projection. Put
    # the frozen source values back and validate every other persisted field.
    frozen_projection = deepcopy(persisted)
    frozen_projection["execution"]["output_root"] = source["execution"][
        "output_root"
    ]
    frozen_projection["execution"]["taskset_store"] = source["execution"][
        "taskset_store"
    ]
    frozen_projection["simulation"]["simulator_bin"] = source["simulation"][
        "simulator_bin"
    ]
    normalized_projection = validate_ext1b_config(frozen_projection)
    if normalized_projection != source:
        raise PlanAuditError(
            "persisted run_config differs from the frozen config outside "
            "documented path overrides"
        )

    runtime = deepcopy(source)
    runtime["execution"]["output_root"] = expected_output
    runtime["execution"]["taskset_store"] = expected_store
    runtime["simulation"]["simulator_bin"] = expected_simulator
    return source, runtime


def _pairing_audit(
    requests: list[dict[str, str]], scheduler_ids: list[str],
) -> dict[str, Any]:
    try:
        assert_ext1b_fair_pairing(requests, scheduler_ids)
    except RuntimeError as exc:
        raise PlanAuditError(f"paired request audit failed: {exc}") from exc
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in requests:
        groups[row["paired_instance_id"]].append(row)
    for pair_id, group in groups.items():
        _require_equal(
            [row["scheduler_id"] for row in group], scheduler_ids,
            f"scheduler group for {pair_id}",
        )
        if any(row.get("request_status") != "PLANNED" for row in group):
            raise PlanAuditError(f"non-PLANNED request in pair {pair_id}")
        for field in EXT1B_FAIRNESS_FIELDS:
            if len({row[field] for row in group}) != 1:
                raise PlanAuditError(
                    f"paired fairness mismatch for {pair_id}/{field}"
                )
    return {
        "pairing_failure_count": 0,
        "paired_instance_count": len(groups),
        "complete_scheduler_group_count": len(groups),
        "scheduler_ids": scheduler_ids,
    }


def _source_index_audit(
    attempts: list[dict[str, str]], retry_limit: int,
) -> dict[str, Any]:
    rejection_counts: Counter[str] = Counter()
    accepted = 0
    for row in attempts:
        logical = int(row["logical_index"])
        attempt = int(row["attempt_index"])
        source = int(row["source_index"])
        if source != logical * retry_limit + attempt:
            raise PlanAuditError(
                "source-index rule mismatch for "
                f"{row['scenario_cell_id']}/{logical}/{attempt}"
            )
        if not 0 <= attempt < retry_limit:
            raise PlanAuditError("attempt index is outside the frozen retry limit")
        if row["attempt_status"] == "ACCEPTED":
            accepted += 1
        elif row["attempt_status"] == "REJECTED":
            rejection_counts[row["rejection_code"]] += 1
        else:
            raise PlanAuditError("unknown generation attempt status")
    return {
        "source_index_rule": "logical_index * retry_limit + attempt_index",
        "source_index_audit": "PASS",
        "retry_limit": retry_limit,
        "accepted_attempt_count": accepted,
        "rejection_count": sum(rejection_counts.values()),
        "rejection_distribution": dict(sorted(rejection_counts.items())),
    }


def _store_audit(
    taskset_store: Path, generated: list[dict[str, str]], source: dict[str, Any],
) -> dict[str, Any]:
    manifest = _json(taskset_store / "pairing_manifest.json")
    _require_equal(manifest.get("schema"), PAIRING_MANIFEST_SCHEMA, "store schema")
    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        raise PlanAuditError("pairing manifest contract must be a mapping")
    workload = contract.get("task_workload_contract")
    if not isinstance(workload, dict):
        raise PlanAuditError("pairing manifest lacks workload contract V2")
    _require_equal(
        workload.get("version"), "REAL_TIME_TASK_WORKLOAD_CONTRACT_V2",
        "store workload contract",
    )
    _require_equal(contract.get("base_seed"), source["grid"]["base_seed"], "store seed")
    if source["scenario"]["kind"] == "TIMING_STRESS":
        capacity = contract.get("scenario_capacity_feasibility_contract")
        if not isinstance(capacity, dict):
            raise PlanAuditError("B3 pairing manifest lacks capacity contract V1")
        _require_equal(
            capacity.get("version"),
            source["scenario"]["capacity_feasibility_contract"],
            "store capacity contract",
        )
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise PlanAuditError("pairing manifest entries must be a list")
    entry_keys = [
        (row.get("generation_id"), row.get("taskset_index")) for row in entries
    ]
    if len(entry_keys) != len(set(entry_keys)):
        raise PlanAuditError("pairing manifest contains duplicate entries")
    identities = {
        (row.get("taskset_id"), row.get("taskset_semantic_hash"))
        for row in entries
    }
    missing = [
        row["source_taskset_id"] for row in generated
        if (row["source_taskset_id"], row["source_taskset_hash"])
        not in identities
    ]
    if missing:
        raise PlanAuditError(
            f"generated tasksets are absent from pairing manifest: {missing[:3]}"
        )
    return {
        "store_pairing_audit": "PASS",
        "store_schema": manifest["schema"],
        "store_pairing_id": manifest.get("pairing_id"),
        "store_entry_count": len(entries),
        "legacy_store_count": 0,
    }


def _capacity_audit(
    generated: Iterable[dict[str, str]], instances: Iterable[dict[str, str]],
) -> dict[str, Any]:
    capacity_by_taskset = {
        row["taskset_id"]: Fraction(row["battery_capacity"])
        for row in instances
    }
    minimum_headroom: Fraction | None = None
    violations = 0
    violation_tasksets: set[str] = set()
    for row in generated:
        capacity = capacity_by_taskset[row["taskset_id"]]
        tasks = json.loads(row["task_input_json"])
        for task in tasks:
            headroom = capacity + NATIVE_ENERGY_EPSILON_J - Fraction(
                str(task["P"])
            )
            if minimum_headroom is None or headroom < minimum_headroom:
                minimum_headroom = headroom
            if headroom < 0:
                violations += 1
                violation_tasksets.add(row["taskset_id"])
    if violations:
        raise PlanAuditError(
            "accepted task exceeds effective battery capacity plus native epsilon"
        )
    return {
        "capacity_execution_audit": "PASS",
        "capacity_infeasible_task_count": 0,
        "capacity_infeasible_taskset_count": 0,
        "minimum_capacity_headroom_j": str(minimum_headroom),
        "native_affordability_epsilon_j": str(NATIVE_ENERGY_EPSILON_J),
    }


def audit_plan(
    config_path: Path, output_root: Path, taskset_store: Path,
    simulator_bin: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source, runtime = _runtime_config_copy(
        config_path, output_root, taskset_store, simulator_bin,
    )
    expected = Ext1BRunner(source).describe()
    summary = _json(output_root / "plan_summary.json")
    workload = _json(output_root / "workload_contract_summary.json")
    metadata = _json(output_root / "run_metadata.json")
    generated = _rows(output_root / "generated_tasksets.csv")
    attempts = _rows(output_root / "generation_attempts.csv")
    instances = _rows(output_root / "scenario_instances.csv")
    requests = _rows(output_root / "simulation_requests.csv")
    failures = _rows(output_root / "failures.csv")
    simulation_attempts = _rows(output_root / "simulation_attempts.csv")

    expected_counts = {
        "cell_count": expected["cell_count"],
        "generated_tasksets": expected["paired_instance_count"],
        "paired_instances": expected["paired_instance_count"],
        "simulation_requests": expected["simulation_request_count"],
    }
    for field, value in expected_counts.items():
        _require_equal(summary.get(field), value, f"plan_summary {field}")
    _require_equal(len(generated), expected_counts["generated_tasksets"], "generated rows")
    _require_equal(len(instances), expected_counts["paired_instances"], "instance rows")
    _require_equal(len(requests), expected_counts["simulation_requests"], "request rows")
    _require_equal(summary.get("plan_only"), True, "plan_only")
    _require_equal(summary.get("simulator_invoked"), False, "simulator_invoked")
    _require_equal(workload.get("idle_task_count"), 0, "idle workload count")
    _require_equal(workload.get("unknown_workload_count"), 0, "unknown workload count")
    _require_equal(workload.get("power_mismatch_count"), 0, "power mismatch count")
    _require_equal(workload.get("legacy_taskset_count"), 0, "legacy taskset count")
    _require_equal(metadata.get("config_hash"), ext1b_config_hash(runtime), "runtime config hash")
    simulator_hash = _sha256(simulator_bin.resolve())
    _require_equal(metadata.get("simulator_build_hash"), simulator_hash, "simulator hash")
    if failures or simulation_attempts:
        raise PlanAuditError("plan-only contains failure or simulation-attempt rows")
    terminal_root = output_root / "simulation_terminal_results"
    terminals = list(terminal_root.glob("*.json")) if terminal_root.is_dir() else []
    if terminals:
        raise PlanAuditError("plan-only contains simulator terminal results")
    if not verify_file_hashes(output_root):
        raise PlanAuditError("plan file hash verification failed")

    pairing = _pairing_audit(requests, list(source["scheduler_ids"]))
    source_indices = _source_index_audit(
        attempts, int(source["scenario"]["structural_retry_limit"]),
    )
    _require_equal(
        source_indices["accepted_attempt_count"],
        expected_counts["paired_instances"],
        "accepted generation attempts",
    )
    store = _store_audit(taskset_store, generated, source)
    capacity = _capacity_audit(generated, instances)
    if source["scenario"]["kind"] == "TIMING_STRESS":
        _require_equal(
            summary.get("capacity_infeasible_task_count"), 0,
            "B3 accepted capacity-infeasible tasks",
        )
        _require_equal(
            summary.get("capacity_infeasible_taskset_count"), 0,
            "B3 accepted capacity-infeasible tasksets",
        )

    report = {
        "schema": "ASAP_BLOCK_V9_3_EXT1B_FORMAL_PLAN_ACCEPTANCE_V1",
        "status": "PASS",
        "experiment_id": source["experiment_id"],
        "parameter_status": source["parameter_status"],
        "seed_space": source["seed_space"],
        "base_seed": source["grid"]["base_seed"],
        "config_path": str(config_path.resolve()),
        "config_hash": ext1b_config_hash(runtime),
        "output_root": str(output_root.resolve()),
        "taskset_store": str(taskset_store.resolve()),
        "git_commit": metadata.get("git_head"),
        "simulator_sha256": simulator_hash,
        "hash_audit": "PASS",
        "workload_audit": "COMPLIANT",
        "simulator_invoked": False,
        "simulator_terminal_count": 0,
        **expected_counts,
        "generation_attempts": len(attempts),
        **pairing,
        **source_indices,
        **store,
        **capacity,
    }
    environment = {
        "schema": "ASAP_BLOCK_V9_3_EXT1B_FORMAL_PLAN_ENVIRONMENT_V1",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_tree": _git("rev-parse", "HEAD^{tree}"),
        "git_status_porcelain": _git("status", "--porcelain"),
        "python_version": sys.version,
        "platform": platform.platform(),
        "simulator_path": str(simulator_bin.resolve()),
        "simulator_sha256": simulator_hash,
        "config_path": str(config_path.resolve()),
        "output_root": str(output_root.resolve()),
        "taskset_store": str(taskset_store.resolve()),
    }
    return report, environment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--taskset-store", type=Path, required=True)
    parser.add_argument("--simulator-bin", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--environment-manifest", type=Path)
    args = parser.parse_args()
    try:
        report, environment = audit_plan(
            args.config, args.output_root, args.taskset_store,
            args.simulator_bin,
        )
    except PlanAuditError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 2
    if args.report is not None:
        _write_json(args.report, report)
    if args.environment_manifest is not None:
        _write_json(args.environment_manifest, environment)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
