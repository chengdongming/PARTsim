#!/usr/bin/env python3
"""Read-only audit for v9.3 real-time task workload artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.config import (  # noqa: E402
    TASK_WORKLOAD_CANDIDATE_DOMAIN,
    TASK_WORKLOAD_CONTRACT_DOMAIN,
    TASK_WORKLOAD_CONTRACT_VERSION,
    TASK_WORKLOAD_POWER_MODEL_DOMAIN,
    domain_hash,
)
from experiments.v9_3.exact_energy import numeric_contract_metadata  # noqa: E402
from experiments.v9_3.taskset_store import (  # noqa: E402
    FROZEN_TASKSET_SCHEMA,
    FROZEN_TASKSET_SEMANTIC_DOMAIN,
    PAIRING_CONTRACT_DOMAIN,
    PAIRING_MANIFEST_SCHEMA,
)


FROZEN_PREIMAGE_KEYS = (
    "schema", "generation_id", "taskset_index", "seed",
    "generation_parameters", "target_total_utilization",
    "actual_total_utilization", "priority_policy", "power_mode",
    "deadline_mode", "service_curve_reference", "tasks",
)
TASKSET_SCHEMAS = {
    "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1": (
        "ASAP_BLOCK:V9.3:TASKSET_SEMANTIC:v1", False,
    ),
    "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V2": (
        "ASAP_BLOCK:V9.3:TASKSET_SEMANTIC:v2", True,
    ),
    FROZEN_TASKSET_SCHEMA: (FROZEN_TASKSET_SEMANTIC_DOMAIN, True),
    "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V1": (
        "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v1", False,
    ),
    "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V2": (
        "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v2", True,
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for raw in paths:
        path = raw.resolve()
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(item.resolve() for item in path.rglob("*.json"))
            files.update(item.resolve() for item in path.rglob("run_config.yaml"))
            files.update(item.resolve() for item in path.rglob("pilot_config.yaml"))
        else:
            raise FileNotFoundError(f"audit input does not exist: {raw}")
    return tuple(sorted(files, key=str))


def _load_mapping(path: Path) -> Mapping[str, Any] | None:
    try:
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError):
        return None
    return value if isinstance(value, Mapping) else None


def _contract_errors(contract: Any) -> list[str]:
    if not isinstance(contract, Mapping):
        return ["missing_contract"]
    errors = []
    candidates = contract.get("ordered_candidates")
    if (
        contract.get("version") != TASK_WORKLOAD_CONTRACT_VERSION
        or contract.get("idle_system_state_reserved") is not True
        or not isinstance(candidates, list)
        or not candidates
        or candidates != sorted(candidates)
        or len(candidates) != len(set(candidates))
        or "idle" in candidates
    ):
        errors.append("candidate_contract")
        return errors
    candidate_material = {
        "version": TASK_WORKLOAD_CONTRACT_VERSION,
        "ordered_candidates": candidates,
    }
    candidate_identity = domain_hash(
        TASK_WORKLOAD_CANDIDATE_DOMAIN, candidate_material
    )
    if contract.get("candidate_identity") != candidate_identity:
        errors.append("candidate_identity")
    model = contract.get("power_model")
    if not isinstance(model, list) or [
        row.get("workload") for row in model if isinstance(row, Mapping)
    ] != candidates:
        errors.append("power_model")
        return errors
    power_material = {
        "version": TASK_WORKLOAD_CONTRACT_VERSION,
        "power_model": model,
    }
    power_identity = domain_hash(
        TASK_WORKLOAD_POWER_MODEL_DOMAIN, power_material
    )
    if contract.get("power_model_identity") != power_identity:
        errors.append("power_model_identity")
    identity_material = {
        "version": TASK_WORKLOAD_CONTRACT_VERSION,
        "candidate_identity": candidate_identity,
        "power_model_identity": power_identity,
    }
    if contract.get("contract_identity") != domain_hash(
        TASK_WORKLOAD_CONTRACT_DOMAIN, identity_material
    ):
        errors.append("contract_identity")
    return errors


def _semantic_preimage(document: Mapping[str, Any], schema: str) -> Mapping[str, Any]:
    if schema.startswith("ASAP_BLOCK_V9_3_FROZEN_TASKSET_"):
        keys = list(FROZEN_PREIMAGE_KEYS)
        if schema != "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1":
            keys.append("task_workload_contract")
        if schema == FROZEN_TASKSET_SCHEMA:
            keys.append("numeric_contract")
        return {key: document[key] for key in keys}
    keys = (
        "schema", "scenario_cell", "source_taskset_hash",
        "logical_taskset_index", "attempt_index", "generation_seed",
        "tasks", "structure",
    )
    values = list(keys)
    if schema == "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V2":
        values.append("task_workload_contract")
    return {key: document[key] for key in values}


def audit(paths: Iterable[Path], *, verify_hashes: bool) -> Dict[str, Any]:
    input_paths = tuple(Path(path).resolve() for path in paths)
    files = _candidate_files(input_paths)
    schema_counts: Counter[str] = Counter()
    contract_counts: Counter[str] = Counter()
    content_counts: Counter[str] = Counter()
    semantic_values: set[str] = set()
    files_with_idle = 0
    idle_records = 0
    unknown_workloads = 0
    power_mismatches = 0
    missing_contract = 0
    semantic_failures = 0
    numeric_contract_failures = 0
    missing_pairing_manifests = sum(
        1
        for path in input_paths
        if path.is_dir()
        and any(path.glob("*/taskset_*.json"))
        and not (path / "pairing_manifest.json").is_file()
    )
    pairing_failures = missing_pairing_manifests
    legacy_files = 0
    taskset_files = 0
    pairing_files = 0
    affected: list[Dict[str, Any]] = []

    documents: Dict[Path, Mapping[str, Any]] = {}
    for path in files:
        document = _load_mapping(path)
        if document is None:
            continue
        documents[path] = document
        if path.name in {"run_config.yaml", "pilot_config.yaml"}:
            affected.append({
                "path": str(path),
                "experiment_id": document.get("experiment_id"),
                "seed_space": document.get("seed_space"),
                "file_sha256": _sha256(path),
            })

    for path, document in documents.items():
        schema = str(document.get("schema", ""))
        if schema == PAIRING_MANIFEST_SCHEMA or path.name == "pairing_manifest.json":
            pairing_files += 1
            schema_counts[schema or "MISSING"] += 1
            contract = document.get("contract")
            failed = (
                schema != PAIRING_MANIFEST_SCHEMA
                or not isinstance(contract, Mapping)
                or _contract_errors(contract.get("task_workload_contract"))
                or document.get("pairing_id") != domain_hash(
                    PAIRING_CONTRACT_DOMAIN, contract or {}
                )
                or not isinstance(document.get("entries"), list)
            )
            if not failed:
                for entry in document["entries"]:
                    if not isinstance(entry, Mapping):
                        failed = True
                        break
                    try:
                        entry_index = int(entry.get("taskset_index"))
                    except (TypeError, ValueError):
                        failed = True
                        break
                    task_path = path.parent / str(entry.get("generation_id")) / (
                        f"taskset_{entry_index:05d}.json"
                    )
                    task_document = documents.get(task_path.resolve())
                    if task_document is None or any((
                        task_document.get("taskset_hash")
                        != entry.get("taskset_semantic_hash"),
                        task_document.get("tasks") != entry.get("task_payload"),
                        task_document.get("priority_hash") != entry.get("priority_hash"),
                        task_document.get("power_hash") != entry.get("power_hash"),
                    )):
                        failed = True
                        break
            pairing_failures += int(bool(failed))
            continue
        if schema not in TASKSET_SCHEMAS:
            continue

        taskset_files += 1
        schema_counts[schema] += 1
        content_counts[_sha256(path)] += 1
        semantic_values.add(str(document.get("taskset_hash") or _sha256(path)))
        contract = document.get("task_workload_contract")
        version = (
            str(contract.get("version"))
            if isinstance(contract, Mapping) else "MISSING"
        )
        contract_counts[version] += 1
        contract_errors = _contract_errors(contract)
        if "missing_contract" in contract_errors:
            missing_contract += 1
        if schema != FROZEN_TASKSET_SCHEMA and schema != (
            "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V2"
        ):
            legacy_files += 1
        if (
            schema == FROZEN_TASKSET_SCHEMA
            and document.get("numeric_contract") != numeric_contract_metadata()
        ):
            numeric_contract_failures += 1

        energy_by_workload: Dict[str, Fraction] = {}
        if isinstance(contract, Mapping) and isinstance(contract.get("power_model"), list):
            try:
                energy_by_workload = {
                    str(row["workload"]): Fraction(str(row["energy_per_tick"]))
                    for row in contract["power_model"]
                }
            except (KeyError, ValueError, ZeroDivisionError, TypeError):
                energy_by_workload = {}
        file_has_idle = False
        tasks = document.get("tasks")
        if not isinstance(tasks, list):
            semantic_failures += int(verify_hashes)
            continue
        for task in tasks:
            if not isinstance(task, Mapping):
                unknown_workloads += 1
                continue
            workload = str(task.get("workload"))
            if workload == "idle":
                file_has_idle = True
                idle_records += 1
            if energy_by_workload and workload not in energy_by_workload:
                unknown_workloads += 1
                continue
            if energy_by_workload:
                try:
                    power = Fraction(str(task.get("P")))
                except (ValueError, ZeroDivisionError):
                    power_mismatches += 1
                    continue
                # V4 P is the per-task C++ binary64 unit demand, whose final
                # multiply/divide rounding depends on C.  The V2 workload
                # table remains generation-only and is not an RTA oracle.
                if (
                    schema != FROZEN_TASKSET_SCHEMA
                    and power != energy_by_workload[workload]
                ):
                    power_mismatches += 1
        files_with_idle += int(file_has_idle)
        if verify_hashes:
            domain, _has_contract = TASKSET_SCHEMAS[schema]
            try:
                observed = domain_hash(domain, _semantic_preimage(document, schema))
            except (KeyError, TypeError):
                semantic_failures += 1
            else:
                semantic_failures += int(observed != document.get("taskset_hash"))

    duplicate_content = sum(count - 1 for count in content_counts.values())
    return {
        "schema": "ASAP_BLOCK_V9_3_TASKSET_WORKLOAD_AUDIT_V1",
        "status": (
            "LEGACY_NON_EXECUTABLE" if legacy_files else
            "COMPLIANT" if not any((
                files_with_idle, unknown_workloads, power_mismatches,
                missing_contract, semantic_failures, pairing_failures,
                numeric_contract_failures,
            )) else "NONCOMPLIANT"
        ),
        "input_paths": [str(path) for path in input_paths],
        "files_checked": len(files),
        "physical_file_count": taskset_files,
        "unique_taskset_hashes": len(semantic_values),
        "unique_semantic_taskset_count": len(semantic_values),
        "duplicate_file_count": duplicate_content,
        "duplicate_content_count": duplicate_content,
        "schema_counts": dict(sorted(schema_counts.items())),
        "contract_version_counts": dict(sorted(contract_counts.items())),
        "files_with_idle": files_with_idle,
        "idle_task_records": idle_records,
        "unknown_workloads": unknown_workloads,
        "power_model_mismatches": power_mismatches,
        "missing_contract": missing_contract,
        "semantic_hash_failures": semantic_failures,
        "numeric_contract_failures": numeric_contract_failures,
        "pairing_manifest_files": pairing_files,
        "missing_pairing_manifests": missing_pairing_manifests,
        "pairing_manifest_failures": pairing_failures,
        "legacy_non_executable_files": legacy_files,
        "affected_experiment_config_identity": affected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of explicit v9.3 taskset-store/result-root paths; "
            "no home-directory scan is performed."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--fail-on-idle", action="store_true")
    parser.add_argument("--fail-on-missing-contract", action="store_true")
    parser.add_argument("--fail-on-power-mismatch", action="store_true")
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()
    try:
        report = audit(args.paths, verify_hashes=args.verify_hashes)
    except (FileNotFoundError, OSError) as exc:
        parser.error(str(exc))
    print(
        "v9.3 workload audit: status={status}, physical={physical}, "
        "unique={unique}, idle={idle}, missing_contract={missing}, "
        "power_mismatch={power}, hash_failures={hashes}, "
        "pairing_failures={pairing}".format(
            status=report["status"], physical=report["physical_file_count"],
            unique=report["unique_semantic_taskset_count"],
            idle=report["idle_task_records"], missing=report["missing_contract"],
            power=report["power_model_mismatches"],
            hashes=report["semantic_hash_failures"],
            pairing=report["pairing_manifest_failures"],
        )
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    failed = False
    if args.fail_on_idle and report["idle_task_records"]:
        failed = True
    if args.fail_on_missing_contract and report["missing_contract"]:
        failed = True
    if args.fail_on_power_mismatch and (
        report["unknown_workloads"] or report["power_model_mismatches"]
    ):
        failed = True
    if args.verify_hashes and (
        report["semantic_hash_failures"] or report["pairing_manifest_failures"]
    ):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
