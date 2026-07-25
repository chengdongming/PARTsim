"""Trusted-plan closure validation and recomputed hard gates for RTA4."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

import asap_block_rta_v9_3_methods as method_registry

from .constrained_taskset_identity import TasksetIdentityCertificate
from .release_applicability import (
    E0_CONDITION_NOT_SATISFIED, FINITE_BATTERY_EMPIRICAL, RTA_FAIL, RTA_PASS,
    SIM_DEADLINE_MISS, SIM_NO_DEADLINE_MISS, SIMULATOR_TRACE_CONTRACT_VERSION,
    TARGET_SCHEDULER, ReleaseObservationWindow, assess_applicability,
    build_no_overflow_evidence, build_release_projection, evaluate_e0_condition,
    parse_release_trace, project_certificate_for_simulation,
    simulation_applicability_identity, validate_simulation_evidence,
)
from .rta4_formal_config import (
    canonical_json, default_rta4_formal_config, domain_hash,
    validate_rta4_formal_config,
)
from .rta4_formal_manifest import (
    NONFORMAL_TEST_FIXTURE, RTA4_CONFIG_CHECKPOINT, RTA4_PLAN_MANIFEST,
    config_checkpoint, trusted_plan_records, validate_trusted_plan_manifest,
)
from .rta4_formal_plan import FormalPlanRecord, formal_service_identity
from .rta4_formal_rows import NA, RTA4FormalRowError, normalize_formal_row
from .rta4_formal_schema import (
    FORMAL_TABLES, RTA4_FORMAL_SCHEMA_MANIFEST, formal_schema_hash,
    formal_schema_manifest,
)
from .rta4_formal_store import certificate_rows
from .rta4_formal_writer import FORMAL_RUN_METADATA, FORMAL_TERMINAL_DIRECTORY
from .task_identity import runtime_task_name_for_source_id


P0, P1, P2, P3 = "P0", "P1", "P2", "P3"
SEVERITIES = (P0, P1, P2, P3)
RECURSIVE_CHAIN = ("CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ")
FIXED_D_CHAIN = ("CW_D", "LOC_D", "PH_D", "SEQ_D")


class RTA4FormalValidationError(RuntimeError):
    """Raised when persisted evidence is not one trusted complete closure."""


@dataclass(frozen=True)
class FormalFinding:
    severity: str
    code: str
    detail: str
    identity: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise RTA4FormalValidationError("unknown finding severity")


@dataclass(frozen=True)
class ValidatedFormalClosure:
    root: Path
    metadata: Mapping[str, Any]
    config: Mapping[str, Any]
    plan_manifest: Mapping[str, Any]
    tables: Mapping[str, Tuple[Mapping[str, str], ...]]
    terminal_payloads: Tuple[Mapping[str, Any], ...]
    closure_sha256: str

    def table(self, filename: str) -> Tuple[Mapping[str, str], ...]:
        try:
            return self.tables[filename]
        except KeyError as exc:
            raise RTA4FormalValidationError(f"unknown closure table: {filename}") from exc


def _strict_json(path: Path) -> Mapping[str, Any]:
    def reject_constant(token: str) -> None:
        raise RTA4FormalValidationError(f"non-finite JSON token: {token}")
    def unique_object(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RTA4FormalValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                           object_pairs_hook=unique_object)
    except RTA4FormalValidationError:
        raise
    except Exception as exc:
        raise RTA4FormalValidationError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise RTA4FormalValidationError(f"JSON root must be a mapping: {path}")
    return value


def _read_exact_csv(path: Path, columns: Sequence[str]) -> Tuple[Mapping[str, str], ...]:
    if not path.is_file():
        raise RTA4FormalValidationError(f"missing formal table: {path.name}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(columns):
            raise RTA4FormalValidationError(f"exact header mismatch: {path.name}")
        rows = tuple(dict(row) for row in reader)
    if any(None in row for row in rows):
        raise RTA4FormalValidationError(f"overflow columns in {path.name}")
    for row in rows:
        common = {key: row[key] for key in columns[:4]}
        body = {key: row[key] for key in columns[4:]}
        try:
            normalized = normalize_formal_row(path.name, body, common)
        except RTA4FormalRowError as exc:
            raise RTA4FormalValidationError(f"semantic row mismatch in {path.name}: {exc}") from exc
        if dict(row) != normalized:
            raise RTA4FormalValidationError(f"non-canonical row encoding in {path.name}")
    return rows


def _unique(rows: Iterable[Mapping[str, str]], key: str, label: str) -> Dict[str, Mapping[str, str]]:
    result: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        identity = row.get(key, "")
        if not identity or identity == NA:
            raise RTA4FormalValidationError(f"empty {label} identity")
        if identity in result:
            raise RTA4FormalValidationError(f"duplicate {label}: {identity}")
        result[identity] = row
    return result


def _truth(value: Any) -> bool:
    if value is True or value in {"true", "True", "1", 1}: return True
    if value is False or value in {"false", "False", "0", 0}: return False
    raise RTA4FormalValidationError(f"invalid strict boolean: {value!r}")


def _optional_int(value: Any) -> int | None:
    if value in {None, "", NA}: return None
    if isinstance(value, bool): raise RTA4FormalValidationError("boolean is not an integer")
    try: result = int(value)
    except (TypeError, ValueError) as exc: raise RTA4FormalValidationError(f"invalid integer: {value!r}") from exc
    if result < 0: raise RTA4FormalValidationError("integer evidence must be non-negative")
    return result


def _certificate_path(root: Path, text: str) -> Path:
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RTA4FormalValidationError("unsafe run-relative path")
    path = root.joinpath(*relative.parts)
    try: path.resolve().relative_to(root.resolve())
    except ValueError as exc: raise RTA4FormalValidationError("run-relative path escapes root") from exc
    return path


def _stringify_expected(table: str, material: Mapping[str, Any], common: Mapping[str, str]) -> Mapping[str, str]:
    try: return normalize_formal_row(table, material, common)
    except RTA4FormalRowError as exc: raise RTA4FormalValidationError(f"internal canonical row reconstruction failed: {exc}") from exc


def _validate_taskset_certificates(
    root: Path, tables: Mapping[str, Tuple[Mapping[str, str], ...]], common: Mapping[str, str],
) -> Dict[str, TasksetIdentityCertificate]:
    skeleton_rows = tables["formal_taskset_skeletons.csv"]
    taskset_rows = tables["formal_tasksets.csv"]
    task_rows = tables["formal_tasks.csv"]
    skeletons = _unique(skeleton_rows, "taskset_skeleton_id", "skeleton")
    _unique(taskset_rows, "taskset_id", "taskset")
    certificates: Dict[str, TasksetIdentityCertificate] = {}
    expected_tasks: list[Mapping[str, str]] = []
    for row in taskset_rows:
        path = _certificate_path(root, row["certificate_path"])
        try:
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != row["certificate_sha256"]:
                raise RTA4FormalValidationError("taskset certificate SHA-256 mismatch")
            certificate = TasksetIdentityCertificate.from_canonical_bytes(payload)
        except RTA4FormalValidationError: raise
        except Exception as exc: raise RTA4FormalValidationError("taskset certificate validation failed") from exc
        expected = certificate_rows(certificate, certificate_path=row["certificate_path"],
                                    certificate_sha256=row["certificate_sha256"])
        if dict(row) != _stringify_expected("formal_tasksets.csv", expected.taskset, common):
            raise RTA4FormalValidationError("taskset row/certificate canonical mismatch")
        certificates[certificate.taskset_id] = certificate
        expected_tasks.extend(_stringify_expected("formal_tasks.csv", task, common) for task in expected.tasks)
    if tuple(task_rows) != tuple(expected_tasks):
        raise RTA4FormalValidationError("formal task rows are not exact canonical certificate rows/order")
    for skeleton_id, row in skeletons.items():
        path = _certificate_path(root, row["certificate_path"])
        try:
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != row["certificate_sha256"]:
                raise RTA4FormalValidationError("skeleton certificate SHA-256 mismatch")
            certificate = TasksetIdentityCertificate.from_canonical_bytes(payload)
        except RTA4FormalValidationError: raise
        except Exception as exc: raise RTA4FormalValidationError("skeleton certificate validation failed") from exc
        if certificate.taskset_skeleton_id != skeleton_id:
            raise RTA4FormalValidationError("skeleton certificate identity mismatch")
        expected = certificate_rows(certificate, certificate_path=row["certificate_path"],
                                    certificate_sha256=row["certificate_sha256"])
        if dict(row) != _stringify_expected("formal_taskset_skeletons.csv", expected.skeleton, common):
            raise RTA4FormalValidationError("skeleton row/certificate canonical mismatch")
    if {certificate.taskset_skeleton_id for certificate in certificates.values()} != set(skeletons):
        raise RTA4FormalValidationError("taskset/skeleton certificate set mismatch")
    return certificates


def validate_dominance(taskset_rows: Sequence[Mapping[str, Any]], task_rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    findings: list[FormalFinding] = []
    for row in taskset_rows:
        if _truth(row.get("fallback_used")):
            findings.append(FormalFinding(P0, "METHOD_FALLBACK", "PH/SEQ fallback is forbidden", str(row.get("analysis_id", ""))))
    taskset_index = {(str(r.get("taskset_id")), str(r.get("exact_e0")), str(r.get("method")), str(r.get("service_identity", ""))): r for r in taskset_rows}
    task_index = {(str(r.get("taskset_id")), str(r.get("exact_e0")), str(r.get("method")), str(r.get("analysis_id")), str(r.get("task_id"))): r for r in task_rows}
    for taskset_id, e0, service in sorted({(k[0], k[1], k[3]) for k in taskset_index}):
        for chain in (RECURSIVE_CHAIN, FIXED_D_CHAIN):
            for weak, strong in zip(chain, chain[1:]):
                left, right = taskset_index.get((taskset_id, e0, weak, service)), taskset_index.get((taskset_id, e0, strong, service))
                if left is None or right is None: continue
                if _truth(left.get("taskset_proven")) and not _truth(right.get("taskset_proven")):
                    findings.append(FormalFinding(P0, "CERTIFICATION_SET_NOT_CONTAINED", f"{strong} does not contain {weak}", taskset_id))
                left_tasks = {k[4]: r for k, r in task_index.items() if k[0] == taskset_id and k[1] == e0 and k[2] == weak and k[3] == left["analysis_id"]}
                right_tasks = {k[4]: r for k, r in task_index.items() if k[0] == taskset_id and k[1] == e0 and k[2] == strong and k[3] == right["analysis_id"]}
                for task_id in sorted(set(left_tasks) & set(right_tasks)):
                    wc, sc = _optional_int(left_tasks[task_id].get("candidate_response_time")), _optional_int(right_tasks[task_id].get("candidate_response_time"))
                    if wc is not None and sc is not None and sc > wc:
                        findings.append(FormalFinding(P0, "CANDIDATE_DOMINANCE_VIOLATION", f"{strong} candidate exceeds {weak}", f"{taskset_id}:{task_id}"))
    return tuple(findings)


def validate_monotonicity(
    taskset_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]] | None = None,
) -> Tuple[FormalFinding, ...]:
    """Recompute E0/service/power certification and per-task candidate vectors."""
    findings: list[FormalFinding] = []
    applicable = {"e0", "service_scale", "power_scale"}
    groups: Dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in taskset_rows:
        axis = str(row.get("axis", ""))
        if axis not in applicable: continue
        key = (str(row.get("taskset_skeleton_id")), str(row.get("method")), axis, str(row.get("scenario", "")))
        groups.setdefault(key, []).append(row)
    task_by_analysis: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for row in task_rows or ():
        task_by_analysis.setdefault(str(row.get("analysis_id")), {})[str(row.get("task_id"))] = row
    for key, group in groups.items():
        try: ordered = sorted(group, key=lambda row: Fraction(str(row["axis_value"])))
        except (KeyError, ValueError, ZeroDivisionError) as exc: raise RTA4FormalValidationError("invalid monotonicity axis value") from exc
        if key[2] == "power_scale": ordered.reverse()
        for weaker, stronger in zip(ordered, ordered[1:]):
            if _truth(weaker.get("taskset_proven")) and not _truth(stronger.get("taskset_proven")):
                findings.append(FormalFinding(P0, "MONOTONICITY_CERTIFICATION_VIOLATION", f"stronger {key[2]} condition lost certification", key[0]))
            weak_tasks, strong_tasks = task_by_analysis.get(str(weaker.get("analysis_id")), {}), task_by_analysis.get(str(stronger.get("analysis_id")), {})
            task_ids = sorted(set(weak_tasks) | set(strong_tasks))
            missing_evidence = not task_ids
            for task_id in task_ids:
                if task_id not in weak_tasks or task_id not in strong_tasks:
                    missing_evidence = True
                    continue
                wc, sc = _optional_int(weak_tasks[task_id].get("candidate_response_time")), _optional_int(strong_tasks[task_id].get("candidate_response_time"))
                if wc is None or sc is None:
                    missing_evidence = True
                elif sc > wc:
                    findings.append(FormalFinding(P0, "MONOTONICITY_CANDIDATE_VIOLATION", f"stronger {key[2]} condition worsened candidate", f"{key[0]}:{task_id}"))
            if missing_evidence:
                findings.append(FormalFinding(
                    P0, "MONOTONICITY_CANDIDATE_EVIDENCE_MISSING",
                    f"{key[2]} pair lacks comparable candidate evidence", key[0],
                ))
    return tuple(findings)


def validate_soundness(rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    findings: list[FormalFinding] = []
    for row in rows:
        eligible, counterexample = _truth(row.get("theorem_comparison_eligible")), _truth(row.get("soundness_counterexample"))
        if eligible and (counterexample or str(row.get("comparison_status")) == "RTA_PASS_SIM_FAIL"):
            findings.append(FormalFinding(P0, "THEOREM_APPLICABLE_COUNTEREXAMPLE", "theorem-applicable RTA pass/simulation fail", str(row.get("comparison_id", ""))))
        candidate, observed = _optional_int(row.get("candidate_response_time")), _optional_int(row.get("observed_response_time"))
        if eligible and candidate is not None and observed is not None and observed > candidate:
            findings.append(FormalFinding(P0, "OBSERVED_RESPONSE_EXCEEDS_CANDIDATE", "theorem-applicable response exceeds candidate", str(row.get("comparison_id", ""))))
    return tuple(findings)


def recompute_dominance_rows(
    taskset_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """Materialize dominance evidence only from canonical raw result rows."""

    result_index = {
        (
            row["taskset_skeleton_id"], row["taskset_id"], row["exact_e0"],
            row["service_identity"], row["power_vector_hash"],
            row["deadline_variant"], row["scenario"], row["axis"],
            row["axis_value"], row["method"],
        ): row
        for row in taskset_rows
    }
    tasks_by_analysis: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for row in task_rows:
        tasks_by_analysis.setdefault(str(row["analysis_id"]), {})[
            str(row["task_id"])
        ] = row
    output = []
    for chain in (RECURSIVE_CHAIN, FIXED_D_CHAIN):
        for weak, strong in zip(chain, chain[1:]):
            for key, left in sorted(result_index.items()):
                if key[-1] != weak:
                    continue
                right = result_index.get((*key[:-1], strong))
                if right is None:
                    continue
                left_tasks = tasks_by_analysis.get(str(left["analysis_id"]), {})
                right_tasks = tasks_by_analysis.get(str(right["analysis_id"]), {})
                for task_id in sorted(set(left_tasks) & set(right_tasks)):
                    left_task, right_task = left_tasks[task_id], right_tasks[task_id]
                    left_candidate = _optional_int(left_task["candidate_response_time"])
                    right_candidate = _optional_int(right_task["candidate_response_time"])
                    left_certified = left_task["task_certification_status"] == "CERTIFIED"
                    right_certified = right_task["task_certification_status"] == "CERTIFIED"
                    valid = not (left_certified and not right_certified)
                    if left_candidate is not None and right_candidate is not None:
                        valid = valid and right_candidate <= left_candidate
                    identity = {
                        "left_analysis_id": left["analysis_id"],
                        "right_analysis_id": right["analysis_id"],
                        "task_id": task_id,
                    }
                    output.append({
                        "check_id": domain_hash(
                            "ASAP_BLOCK:V9.3:RTA4_DOMINANCE_CHECK:v1", identity
                        ),
                        "taskset_skeleton_id": left["taskset_skeleton_id"],
                        "taskset_id": left["taskset_id"],
                        "exact_e0": left["exact_e0"],
                        "carry_policy": left["carry_policy"],
                        "left_method": weak, "right_method": strong,
                        "task_id": task_id,
                        "left_candidate": NA if left_candidate is None else left_candidate,
                        "right_candidate": NA if right_candidate is None else right_candidate,
                        "left_certified": left_certified,
                        "right_certified": right_certified,
                        "check_status": "PASS" if valid else "P0_VIOLATION",
                        "failure_severity": NA if valid else P0,
                    })
    return tuple(output)


def recompute_monotonicity_rows(
    taskset_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """Materialize CORE-4 hard monotonicity from per-task candidate vectors."""

    groups: Dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in taskset_rows:
        axis = str(row["axis"])
        if axis not in {"e0", "service_scale", "power_scale"}:
            continue
        key = (
            str(row["taskset_skeleton_id"]), str(row["method"]), axis,
            str(row["scenario"]),
        )
        groups.setdefault(key, []).append(row)
    tasks_by_analysis: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for row in task_rows:
        tasks_by_analysis.setdefault(str(row["analysis_id"]), {})[
            str(row["task_id"])
        ] = row
    output = []
    for key, rows in sorted(groups.items()):
        ordered = sorted(rows, key=lambda row: Fraction(str(row["axis_value"])))
        if key[2] == "power_scale":
            ordered.reverse()
        for weaker, stronger in zip(ordered, ordered[1:]):
            certification_valid = not (
                _truth(weaker["taskset_proven"])
                and not _truth(stronger["taskset_proven"])
            )
            comparable = 0
            missing_evidence = False
            candidate_valid = True
            weak_tasks = tasks_by_analysis.get(str(weaker["analysis_id"]), {})
            strong_tasks = tasks_by_analysis.get(str(stronger["analysis_id"]), {})
            task_ids = sorted(set(weak_tasks) | set(strong_tasks))
            if not task_ids:
                missing_evidence = True
            for task_id in task_ids:
                if task_id not in weak_tasks or task_id not in strong_tasks:
                    missing_evidence = True
                    continue
                weak_candidate = _optional_int(
                    weak_tasks[task_id]["candidate_response_time"]
                )
                strong_candidate = _optional_int(
                    strong_tasks[task_id]["candidate_response_time"]
                )
                if weak_candidate is None or strong_candidate is None:
                    missing_evidence = True
                    continue
                comparable += 1
                candidate_valid = candidate_valid and strong_candidate <= weak_candidate
            candidate_status = (
                "NOT_COMPARABLE" if missing_evidence or comparable == 0
                else "PASS" if candidate_valid else "P0_VIOLATION"
            )
            certification_status = (
                "PASS" if certification_valid else "P0_VIOLATION"
            )
            valid = (
                not missing_evidence and comparable > 0
                and candidate_valid and certification_valid
            )
            identity = {
                "weaker_analysis_id": weaker["analysis_id"],
                "stronger_analysis_id": stronger["analysis_id"],
                "axis": key[2],
            }
            output.append({
                "check_id": domain_hash(
                    "ASAP_BLOCK:V9.3:RTA4_MONOTONICITY_CHECK:v1", identity
                ),
                "taskset_skeleton_id": key[0], "method": key[1],
                "axis": key[2], "weaker_value": weaker["axis_value"],
                "stronger_value": stronger["axis_value"],
                "weaker_analysis_id": weaker["analysis_id"],
                "stronger_analysis_id": stronger["analysis_id"],
                "candidate_status": candidate_status,
                "certification_status": certification_status,
                "check_status": "PASS" if valid else "P0_VIOLATION",
                "failure_severity": NA if valid else P0,
            })
    return tuple(output)


def recompute_worker_consistency_rows(
    results: Sequence[Mapping[str, str]], terminals: Mapping[str, Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    groups: Dict[str, list[Mapping[str, str]]] = {}
    for row in results: groups.setdefault(row["request_id"], []).append(row)
    expected = []
    for request_id, rows in sorted(groups.items()):
        if len(rows) <= 1: continue
        ordered = sorted(
            rows,
            key=lambda row: int(terminals[row["execution_run_id"]]["worker_count"]),
        )
        reference = ordered[0]
        ref_terminal = terminals[reference["execution_run_id"]]
        for compared in ordered[1:]:
            terminal = terminals[compared["execution_run_id"]]
            fields = {
                "solver_status_match": reference["solver_status"] == compared["solver_status"],
                "candidate_match": reference["candidate_vector_hash"] == compared["candidate_vector_hash"],
                "witness_match": reference["witness_vector_hash"] == compared["witness_vector_hash"],
                "certification_match": reference["certification_vector_hash"] == compared["certification_vector_hash"],
                "failure_reason_match": reference["failure_reason_vector_hash"] == compared["failure_reason_vector_hash"],
                "math_hash_match": reference["exact_result_hash"] == compared["exact_result_hash"],
            }
            check_id = hashlib.sha256(("ASAP_BLOCK:V9.3:RTA4_WORKER_CHECK:v1\0" + request_id + reference["execution_run_id"] + compared["execution_run_id"]).encode()).hexdigest()
            expected.append({
                "check_id": check_id, "mathematical_request_id": request_id,
                "reference_execution_id": reference["execution_run_id"], "compared_execution_id": compared["execution_run_id"],
                "reference_worker_count": ref_terminal["worker_count"], "compared_worker_count": terminal["worker_count"],
                **fields, "reference_math_result_hash": reference["exact_result_hash"],
                "compared_math_result_hash": compared["exact_result_hash"],
                "check_status": "PASS" if all(fields.values()) else "P0_MISMATCH",
                "failure_severity": "NA" if all(fields.values()) else P0,
            })
    return tuple(expected)


def validate_worker_consistency(rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    findings = []
    for row in rows:
        fields = ("solver_status_match", "candidate_match", "witness_match", "certification_match", "failure_reason_match", "math_hash_match")
        if not all(_truth(row.get(field)) for field in fields):
            findings.append(FormalFinding(P0, "WORKER_MATHEMATICAL_MISMATCH", "worker count changed mathematical output", str(row.get("mathematical_request_id", ""))))
        if row.get("reference_math_result_hash") != row.get("compared_math_result_hash"):
            findings.append(FormalFinding(P0, "WORKER_RESULT_HASH_MISMATCH", "worker mathematical result hash differs", str(row.get("mathematical_request_id", ""))))
    return tuple(findings)


def _closure_digest(root: Path) -> str:
    digest = hashlib.sha256(b"ASAP_BLOCK:V9.3:RTA4_CLOSURE:v1\0")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "formal_file_hashes.sha256": continue
        relative, payload = path.relative_to(root).as_posix().encode(), path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big")); digest.update(relative); digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def refresh_validated_closure(
    source: Path | str | ValidatedFormalClosure, *, require_complete: bool = True,
    source_closures: Mapping[str, Path | str | ValidatedFormalClosure] | None = None,
) -> ValidatedFormalClosure:
    """Revalidate the current on-disk source even when passed an old object.

    A ``ValidatedFormalClosure`` is evidence about one inventory snapshot, not
    a permanent capability for its mutable root.  Refreshing closes the stale
    object path and returns a new snapshot only when every bound identity and
    the canonical closure inventory hash remain unchanged.
    """

    previous = source if isinstance(source, ValidatedFormalClosure) else None
    root = previous.root if previous is not None else Path(source)
    refreshed = validate_formal_run_closure(
        root, require_complete=require_complete,
        source_closures=source_closures,
    )
    if previous is None:
        return refreshed
    identity_keys = (
        "schema_version", "schema_sha256", "plan_sha256",
        "config_semantic_hash", "core", "execution_class",
    )
    if any(
        refreshed.metadata.get(key) != previous.metadata.get(key)
        for key in identity_keys
    ):
        raise RTA4FormalValidationError(
            "refreshed closure identity differs from validated source object"
        )
    if (
        dict(refreshed.plan_manifest) != dict(previous.plan_manifest)
        or dict(refreshed.config) != dict(previous.config)
        or refreshed.closure_sha256 != previous.closure_sha256
    ):
        raise RTA4FormalValidationError(
            "validated source object is stale relative to its current root"
        )
    return refreshed


def _config_from_checkpoint(checkpoint: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        core = checkpoint["core"]
        material = deepcopy(checkpoint["validated_config_material"])
        defaults = default_rta4_formal_config(str(core))
        material["execution"]["output_root"] = defaults["execution"]["output_root"]
        material["execution"]["taskset_store"] = defaults["execution"]["taskset_store"]
        material["execution"]["resume"] = False
        config = validate_rta4_formal_config(material, expected_core=str(core))
    except Exception as exc:
        raise RTA4FormalValidationError("cannot reconstruct validated config checkpoint") from exc
    if config_checkpoint(config) != dict(checkpoint):
        raise RTA4FormalValidationError("config checkpoint is not trusted/canonical")
    return config


def _comparison_status(e0_status: str, rta: str, simulation: str) -> str:
    if e0_status == E0_CONDITION_NOT_SATISFIED: return E0_CONDITION_NOT_SATISFIED
    return {
        (RTA_PASS, SIM_DEADLINE_MISS): "RTA_PASS_SIM_FAIL",
        (RTA_PASS, SIM_NO_DEADLINE_MISS): "RTA_PASS_SIM_PASS",
        (RTA_FAIL, SIM_DEADLINE_MISS): "RTA_FAIL_SIM_FAIL",
        (RTA_FAIL, SIM_NO_DEADLINE_MISS): "RTA_FAIL_SIM_PASS",
    }[(rta, simulation)]


def _validate_taskset_result_vector(
    result: Mapping[str, str], rows: Sequence[Mapping[str, str]],
) -> None:
    """Bind the taskset solver state to its complete raw task-status vector."""

    solver = result["solver_status"]
    task_solvers = [row["task_solver_status"] for row in rows]
    task_certifications = [row["task_certification_status"] for row in rows]
    candidates = [_optional_int(row["candidate_response_time"]) for row in rows]
    if solver == "TIMEOUT":
        if "TIMEOUT" not in task_solvers:
            raise RTA4FormalValidationError(
                "timeout taskset lacks a timeout raw task result"
            )
        if any(status == "CERTIFIED" for status in task_certifications) or any(
            candidate is not None for candidate in candidates
        ):
            raise RTA4FormalValidationError(
                "timeout taskset carries certified candidate evidence"
            )
    elif solver == "NO_CANDIDATE":
        if "NO_CANDIDATE" not in task_solvers:
            raise RTA4FormalValidationError(
                "no-candidate taskset lacks a no-candidate raw task result"
            )
        if any(status in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_ERROR"} for status in task_solvers):
            raise RTA4FormalValidationError(
                "no-candidate taskset carries a conflicting task solver state"
            )
    elif solver in {"NUMERIC_ERROR", "INTERNAL_ERROR"}:
        if solver not in task_solvers:
            raise RTA4FormalValidationError(
                "error taskset lacks a matching raw task error"
            )
    elif solver == "COMPLETED":
        if any(status != "CANDIDATE_FOUND" for status in task_solvers):
            raise RTA4FormalValidationError(
                "completed taskset carries an incomplete task solver state"
            )
    else:
        raise RTA4FormalValidationError("unknown taskset solver state")


def _validate_certificate_plan_binding(
    config: Mapping[str, Any], record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
) -> None:
    request = certificate.generation_request
    generation = config["generation"]
    material = record.material
    expected = {
        "formal_master_seed": generation["formal_master_seed"],
        "processors": material.get("processor_count", 4),
        "task_count": material.get("task_count", 10),
        "target_normalized_utilization": Fraction(
            str(material.get("normalized_utilization", "1/2"))
        ),
        "replicate_index": material.get("replicate_index", 0),
        "period_min": generation["period_min"],
        "period_max": generation["period_max"],
        "utilization_allocation_mode": generation["utilization_allocation_mode"],
        "min_task_utilization": Fraction(generation["minimum_task_utilization"]),
        "max_task_utilization": Fraction(generation["maximum_task_utilization"]),
        "utilization_tolerance": Fraction(generation["utilization_tolerance"]),
        "wcet_rounding_mode": generation["wcet_rounding"],
        "generator_version": generation["generator_version"],
        "power_generation_mode": generation["power_generation_mode"],
        "priority_policy": generation["priority_policy"],
    }
    if any(getattr(request, key) != value for key, value in expected.items()):
        raise RTA4FormalValidationError(
            "certificate generation provenance/trusted plan mismatch"
        )
    if certificate.power_variant.scale != Fraction(
        str(material.get("power_scale", "1"))
    ):
        raise RTA4FormalValidationError("certificate power variant/trusted plan mismatch")
    deadline = str(material.get("deadline_variant", "constrained_uniform_slack_v1"))
    if deadline.startswith("fixed_slack_fraction_v1:"):
        if certificate.deadline_variant.fixed_slack_fraction != Fraction(
            deadline.split(":", 1)[1]
        ):
            raise RTA4FormalValidationError(
                "certificate deadline variant/trusted plan mismatch"
            )
    elif certificate.deadline_variant.mode != deadline:
        raise RTA4FormalValidationError(
            "certificate deadline variant/trusted plan mismatch"
        )


def _reconstruct_simulation_result_rows(
    *, simulation: Mapping[str, str], certificate: TasksetIdentityCertificate,
    projection: Any, window: ReleaseObservationWindow,
    actual_jobs: Sequence[Mapping[str, str]], trace_path: Path,
    common: Mapping[str, str],
) -> tuple[Tuple[Mapping[str, str], ...], Tuple[Mapping[str, str], ...], int, int, str, str]:
    jobs_by_key: Dict[tuple[str, int], Mapping[str, str]] = {}
    for row in actual_jobs:
        key = (row["task_id"], int(row["release_time"]))
        if key in jobs_by_key:
            raise RTA4FormalValidationError("duplicate simulation task/release job")
        jobs_by_key[key] = row
    expected_keys = {
        (task.task_id, release)
        for task, offset in zip(certificate.tasks, projection.offsets)
        for release in range(offset.arrival_offset, window.release_horizon, task.period)
    }
    if set(jobs_by_key) != expected_keys:
        raise RTA4FormalValidationError(
            "simulation jobs are not the complete expected release set"
        )
    trace = _strict_json(trace_path)
    runtime_to_task = {
        runtime_task_name_for_source_id(task.task_id): task.task_id
        for task in certificate.tasks
    }
    trace_misses = set()
    for event in trace.get("events", ()):
        if isinstance(event, Mapping) and event.get("event_type") == "dline_miss":
            name = event.get("task_name")
            if name not in runtime_to_task:
                raise RTA4FormalValidationError("trace miss has unknown task")
            release = _optional_int(event.get("arrival_time"))
            if release is None:
                raise RTA4FormalValidationError("trace miss lacks arrival time")
            trace_misses.add((runtime_to_task[str(name)], release))
    expected_job_rows: list[Mapping[str, str]] = []
    expected_task_rows: list[Mapping[str, str]] = []
    marked_misses = set()
    global_max = 0
    for task in certificate.tasks:
        releases = sorted(
            release for task_id, release in expected_keys
            if task_id == task.task_id
        )
        responses = []
        misses = 0
        for job_index, release in enumerate(releases):
            actual = jobs_by_key[(task.task_id, release)]
            completion = _optional_int(actual["completion_time"])
            if completion is not None and (
                completion < release or completion > window.observation_horizon
            ):
                raise RTA4FormalValidationError(
                    "simulation completion lies outside observation window"
                )
            deadline = release + task.relative_deadline
            missed = completion is None or completion > deadline
            response = None if completion is None else completion - release
            if missed:
                misses += 1
                marked_misses.add((task.task_id, release))
            if response is not None:
                responses.append(response)
                global_max = max(global_max, response)
            expected_job_rows.append(_stringify_expected(
                "formal_simulation_job_results.csv", {
                    "simulation_job_result_id": domain_hash(
                        "ASAP_BLOCK:V9.3:RTA4_SIMULATION_JOB_RESULT:v1", {
                            "simulation_id": simulation["simulation_id"],
                            "task_id": task.task_id, "release_time": release,
                        }
                    ),
                    "simulation_id": simulation["simulation_id"],
                    "taskset_id": certificate.taskset_id,
                    "task_id": task.task_id, "job_index": job_index,
                    "release_time": release,
                    "completion_time": NA if completion is None else completion,
                    "absolute_deadline": deadline,
                    "observed_response_time": NA if response is None else response,
                    "deadline_missed": missed, "within_release_horizon": True,
                    "observation_status": (
                        "DEADLINE_MISSED" if missed else "COMPLETED"
                    ),
                }, common,
            ))
        expected_task_rows.append(_stringify_expected(
            "formal_simulation_task_results.csv", {
                "simulation_task_result_id": domain_hash(
                    "ASAP_BLOCK:V9.3:RTA4_SIMULATION_TASK_RESULT:v1", {
                        "simulation_id": simulation["simulation_id"],
                        "task_id": task.task_id,
                    }
                ),
                "simulation_id": simulation["simulation_id"],
                "taskset_id": certificate.taskset_id,
                "task_id": task.task_id, "priority_rank": task.priority_rank,
                "released_job_count": len(releases),
                "completed_job_count": len(responses),
                "deadline_miss_count": misses,
                "max_observed_response": max(responses, default=0),
                "simulation_status": "COMPLETED",
            }, common,
        ))
    if marked_misses != trace_misses:
        raise RTA4FormalValidationError(
            "simulation raw jobs/deadline-miss trace set mismatch"
        )
    common_keys = set(common)
    task_material = [
        {key: value for key, value in row.items() if key not in common_keys}
        for row in expected_task_rows
    ]
    job_material = [
        {key: value for key, value in row.items() if key not in common_keys}
        for row in expected_job_rows
    ]
    return (
        tuple(expected_task_rows), tuple(expected_job_rows), len(marked_misses),
        global_max,
        domain_hash("ASAP_BLOCK:V9.3:RTA4_SIMULATION_TASK_VECTOR:v1", task_material),
        domain_hash("ASAP_BLOCK:V9.3:RTA4_SIMULATION_JOB_VECTOR:v1", job_material),
    )


def validate_formal_run_closure(
    root: Path | str, *, require_complete: bool = True,
    require_authorized_formal: bool = False,
    source_closures: Mapping[str, Path | str | ValidatedFormalClosure] | None = None,
) -> ValidatedFormalClosure:
    root = Path(root)
    marker = _strict_json(root / RTA4_FORMAL_SCHEMA_MANIFEST)
    if dict(marker) != formal_schema_manifest() or marker.get("schema_sha256") != formal_schema_hash():
        raise RTA4FormalValidationError("schema manifest/hash mismatch")
    checkpoint = _strict_json(root / RTA4_CONFIG_CHECKPOINT)
    config = _config_from_checkpoint(checkpoint)
    plan_manifest = _strict_json(root / RTA4_PLAN_MANIFEST)
    try: validate_trusted_plan_manifest(plan_manifest, config)
    except Exception as exc: raise RTA4FormalValidationError("trusted plan manifest mismatch") from exc
    if plan_manifest["execution_class"] != NONFORMAL_TEST_FIXTURE:
        raise RTA4FormalValidationError("PR-D rejects FORMAL closure unconditionally")
    metadata = _strict_json(root / FORMAL_RUN_METADATA)
    expected_metadata = {
        "schema_version": config["experiment_contract"]["schema_version"],
        "schema_sha256": formal_schema_hash(), "plan_sha256": plan_manifest["manifest_sha256"],
        "full_plan_sha256": plan_manifest["full_plan_sha256"],
        "config_semantic_hash": checkpoint["config_semantic_hash"], "core": config["core"],
        "parameter_status": config["experiment_contract"]["parameter_status"],
        "execution_class": NONFORMAL_TEST_FIXTURE, "formal_authorized": False,
    }
    if dict(metadata) != expected_metadata:
        raise RTA4FormalValidationError("run metadata is not the trusted plan checkpoint")
    tables = {name: _read_exact_csv(root / name, columns) for name, columns in FORMAL_TABLES.items()}
    common = {"schema_version": expected_metadata["schema_version"], "schema_sha256": expected_metadata["schema_sha256"],
              "plan_sha256": expected_metadata["plan_sha256"], "config_semantic_hash": expected_metadata["config_semantic_hash"]}
    for name, rows in tables.items():
        for row in rows:
            if any(row[key] != common[key] for key in common):
                raise RTA4FormalValidationError(f"row identity drift in {name}")
    certificates = _validate_taskset_certificates(root, tables, common)
    records = trusted_plan_records(config, plan_manifest)
    expected_records = {record.record_id: record for record in records}
    rta_records = {key: value for key, value in expected_records.items() if value.kind != "simulation"}
    simulation_records = {key: value for key, value in expected_records.items() if value.kind == "simulation"}
    requests = _unique(tables["formal_rta_requests.csv"], "plan_record_id", "request plan record")
    request_exec = _unique(tables["formal_rta_requests.csv"], "execution_run_id", "request execution")
    if require_complete and set(requests) != set(rta_records):
        raise RTA4FormalValidationError("trusted plan/request membership mismatch")
    expected_cells: Dict[str, Mapping[str, str]] = {}
    for plan_id, request in requests.items():
        record = rta_records.get(plan_id)
        if record is None: raise RTA4FormalValidationError("request is outside trusted plan")
        material = record.material
        expected_pairs = {
            "request_id": record.mathematical_request_id, "execution_run_id": record.execution_id,
            "taskset_slot_id": record.taskset_slot_id, "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "method": material.get("method"), "exact_e0": material.get("exact_e0"),
            "scenario": material.get("scenario", "MAIN"), "axis": material.get("axis", "baseline"),
            "axis_value": material.get("axis_value", "baseline"), "service_scale": material.get("service_scale", "1"),
            "power_scale": material.get("power_scale", "1"), "deadline_variant": material.get("deadline_variant", "constrained_uniform_slack_v1"),
            "worker_count": str(material.get("worker_count", 1)),
            "method_role": (
                "WORKER_CONSISTENCY" if record.core == "CORE-5B"
                else "MAIN_METHOD"
            ),
            "carry_policy": method_registry.method_spec_v9_3(
                str(material["method"])
            ).carry_policy.value,
            "theory_document_sha256": config["identity"]["theory_document_sha256"],
            "numeric_contract_sha256": config["identity"]["numeric_contract_sha256"],
            "timeout_contract": config["execution"]["timeout_contract"],
            "source_analysis_id": NA,
            "request_status": "PLANNED",
        }
        cell_material = {
            "core": record.core, "scenario": material.get("scenario", "MAIN"),
            "axis": material.get("axis", "baseline"),
            "axis_value": material.get("axis_value", "baseline"),
            "processor_count": material.get("processor_count", 4),
            "task_count": material.get("task_count", 10),
            "normalized_utilization": material.get("normalized_utilization", "1/2"),
            "exact_e0": material["exact_e0"],
            "service_scale": material.get("service_scale", "1"),
            "power_scale": material.get("power_scale", "1"),
            "deadline_variant": material.get(
                "deadline_variant", "constrained_uniform_slack_v1"
            ),
        }
        expected_cell_id = domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_FIXTURE_CELL:v1", cell_material
        )
        expected_pairs["cell_id"] = expected_cell_id
        expected_cells.setdefault(
            expected_cell_id,
            _stringify_expected("formal_cells.csv", {
                "cell_id": expected_cell_id, **cell_material,
                "generation_status": "GENERATED_AND_CERTIFIED",
            }, common),
        )
        if any(str(request[key]) != str(value) for key, value in expected_pairs.items()):
            raise RTA4FormalValidationError("request/plan record identity mismatch")
        certificate = certificates.get(request["taskset_id"])
        if certificate is None or request["taskset_skeleton_id"] != certificate.taskset_skeleton_id or request["taskset_hash"] != certificate.taskset_hash or request["power_vector_hash"] != certificate.power_vector_hash:
            raise RTA4FormalValidationError("request/taskset certificate mismatch")
        _validate_certificate_plan_binding(config, record, certificate)
        expected_service = formal_service_identity(material.get("service_scale", "1"))
        if request["service_identity"] != expected_service:
            raise RTA4FormalValidationError("request/service-scale identity mismatch")
        power_text = str(certificate.power_variant.scale.numerator) if certificate.power_variant.scale.denominator == 1 else f"{certificate.power_variant.scale.numerator}/{certificate.power_variant.scale.denominator}"
        if request["power_scale"] != power_text:
            raise RTA4FormalValidationError("request/power-variant identity mismatch")
        deadline_plan = request["deadline_variant"]
        if deadline_plan.startswith("fixed_slack_fraction_v1:"):
            expected_slack = deadline_plan.split(":", 1)[1]
            actual_slack = certificate.deadline_variant.fixed_slack_fraction
            actual_text = None if actual_slack is None else (str(actual_slack.numerator) if actual_slack.denominator == 1 else f"{actual_slack.numerator}/{actual_slack.denominator}")
            if actual_text != expected_slack:
                raise RTA4FormalValidationError("request/deadline-variant identity mismatch")
        elif certificate.deadline_variant.mode != deadline_plan:
            raise RTA4FormalValidationError("request/deadline-variant identity mismatch")
        try:
            from .rta4_formal_pipeline import formal_analysis_identity
            analysis_id, exact_input, _ = formal_analysis_identity(
                certificate=certificate, method=request["method"],
                exact_e0=request["exact_e0"], service_identity=expected_service,
                numeric_contract_sha256=request["numeric_contract_sha256"],
                theory_document_sha256=request["theory_document_sha256"],
                timeout_contract=request["timeout_contract"],
                source_analysis_id=None if request["source_analysis_id"] == NA else request["source_analysis_id"],
            )
        except Exception as exc:
            raise RTA4FormalValidationError("cannot reconstruct request mathematical identity") from exc
        if request["analysis_id"] != analysis_id or request["exact_input_identity"] != exact_input:
            raise RTA4FormalValidationError("request mathematical identity mismatch")
    if tables["formal_cells.csv"] != tuple(expected_cells.values()):
        raise RTA4FormalValidationError("formal cells do not equal trusted plan cells")
    results = _unique(tables["formal_rta_taskset_results.csv"], "execution_run_id", "taskset result execution")
    if require_complete and set(results) != set(request_exec):
        raise RTA4FormalValidationError("request/result execution set mismatch")
    shared = ("plan_record_id", "analysis_id", "request_id", "execution_run_id", "cell_id", "taskset_skeleton_slot_id", "taskset_slot_id", "taskset_skeleton_id", "taskset_id", "taskset_hash", "method", "method_role", "carry_policy", "exact_e0", "service_identity", "power_vector_hash", "theory_document_sha256", "numeric_contract_sha256", "exact_input_identity", "timeout_contract", "source_analysis_id", "scenario", "axis", "axis_value", "service_scale", "power_scale", "deadline_variant")
    for execution_id, result in results.items():
        request = request_exec.get(execution_id)
        if request is None or any(result[key] != request[key] for key in shared):
            raise RTA4FormalValidationError("request/taskset-result mathematical identity mismatch")
    task_groups: Dict[str, list[Mapping[str, str]]] = {}
    for row in tables["formal_rta_task_results.csv"]: task_groups.setdefault(row["execution_run_id"], []).append(row)
    if require_complete and set(task_groups) != set(results):
        raise RTA4FormalValidationError("task-result execution set mismatch")
    for execution_id, result in results.items():
        certificate = certificates[result["taskset_id"]]
        rows = task_groups.get(execution_id, [])
        if len(rows) != len(certificate.tasks): raise RTA4FormalValidationError("task result set/certificate count mismatch")
        for row, task in zip(rows, certificate.tasks):
            expected = {"plan_record_id": result["plan_record_id"], "analysis_id": result["analysis_id"], "request_id": result["request_id"], "execution_run_id": execution_id,
                        "task_result_id": hashlib.sha256(("ASAP_BLOCK:V9.3:RTA4_TASK_RESULT_ROW:v1\0" + canonical_json({"execution_run_id": execution_id, "task_id": task.task_id})).encode()).hexdigest(),
                        "taskset_skeleton_id": certificate.taskset_skeleton_id, "taskset_id": certificate.taskset_id,
                        "method": result["method"], "exact_e0": result["exact_e0"], "task_id": task.task_id,
                        "priority_rank": str(task.priority_rank), "D": str(task.relative_deadline)}
            if any(row[key] != value for key, value in expected.items()):
                raise RTA4FormalValidationError("task result/request/certificate binding mismatch")
        from .rta4_formal_pipeline import recompute_rta_result_hashes
        hashes = recompute_rta_result_hashes(result, rows)
        for row, exact_task_hash in zip(rows, hashes["task_hashes"]):
            if row["exact_task_result_hash"] != exact_task_hash:
                raise RTA4FormalValidationError(
                    "task result hash is not derived from canonical raw task evidence"
                )
        for field in (
            "exact_result_hash", "candidate_vector_hash", "witness_vector_hash",
            "certification_vector_hash", "failure_reason_vector_hash",
        ):
            if result[field] != hashes[field]:
                raise RTA4FormalValidationError(
                    "taskset summary hash is not derived from canonical raw task results"
                )
        if result["checked_w_count"] != str(sum(int(row["checked_w_count"]) for row in rows)) or result["checked_q_count"] != str(sum(int(row["checked_q_count"]) for row in rows)) or result["checked_h_count"] != str(sum(int(row["checked_h_count"]) for row in rows)):
            raise RTA4FormalValidationError("taskset checked-count summary mismatch")
        utilization = sum(
            (Fraction(task.wcet, task.period) for task in certificate.tasks),
            Fraction(),
        ) / certificate.processors
        utilization_text = (
            str(utilization.numerator) if utilization.denominator == 1
            else f"{utilization.numerator}/{utilization.denominator}"
        )
        if result["normalized_utilization"] != utilization_text:
            raise RTA4FormalValidationError("taskset result utilization mismatch")
        failed_ranks = [
            int(row["priority_rank"]) for row in rows
            if row["task_certification_status"] != "CERTIFIED"
        ]
        expected_failed = NA if not failed_ranks else str(min(failed_ranks))
        if result["first_failed_priority"] != expected_failed:
            raise RTA4FormalValidationError("taskset first-failed priority mismatch")
        expected_proven = (
            not failed_ranks
            and result["taskset_certification_status"] == "CERTIFIED_TASKSET"
        )
        if _truth(result["taskset_proven"]) != expected_proven:
            raise RTA4FormalValidationError("taskset certification/raw-task mismatch")
        _validate_taskset_result_vector(result, rows)
    mechanism_keys = set()
    raw_task_index = {
        (row["analysis_id"], row["task_id"]): row
        for row in tables["formal_rta_task_results.csv"]
    }
    for mechanism in tables["formal_rta_mechanisms.csv"]:
        key = (mechanism["analysis_id"], mechanism["task_id"])
        task = raw_task_index.get(key)
        if key in mechanism_keys or task is None:
            raise RTA4FormalValidationError(
                "mechanism telemetry has duplicate/unknown analysis task"
            )
        mechanism_keys.add(key)
        if (
            mechanism["taskset_id"] != task["taskset_id"]
            or mechanism["method"] != task["method"]
            or mechanism["priority_rank"] != task["priority_rank"]
        ):
            raise RTA4FormalValidationError(
                "mechanism telemetry/task-result identity mismatch"
            )
    terminal_dir = root / FORMAL_TERMINAL_DIRECTORY
    terminal_paths = sorted(terminal_dir.glob("*.json")) if terminal_dir.is_dir() else []
    terminal_payloads = tuple(_strict_json(path) for path in terminal_paths)
    terminal_map: Dict[str, Mapping[str, Any]] = {}
    for path, payload in zip(terminal_paths, terminal_payloads):
        execution_id = str(payload.get("execution_run_id", ""))
        if execution_id != path.stem or execution_id in terminal_map: raise RTA4FormalValidationError("terminal JSON identity conflict")
        if any(payload.get(key) != metadata[key] for key in (
            "schema_version", "schema_sha256", "plan_sha256",
            "config_semantic_hash",
        )):
            raise RTA4FormalValidationError("terminal JSON closure identity mismatch")
        terminal_map[execution_id] = payload
    expected_terminal_ids = set(request_exec)
    # Simulation terminal IDs are the trusted plan execution IDs.
    expected_terminal_ids.update(str(record.execution_id) for record in simulation_records.values())
    if require_complete and set(terminal_map) != expected_terminal_ids:
        raise RTA4FormalValidationError("trusted execution/terminal set mismatch")
    for execution_id, result in results.items():
        payload = terminal_map.get(execution_id)
        expected_terminal_keys = {
            "schema_version", "schema_sha256", "plan_sha256",
            "config_semantic_hash", "execution_run_id", "plan_record_id",
            "analysis_id", "request_id", "worker_count", "solver_status",
            "exact_result_hash", "candidate_vector_hash", "witness_vector_hash",
            "certification_vector_hash", "failure_reason_vector_hash",
        }
        request = request_exec[execution_id]
        if payload is None or set(payload) != expected_terminal_keys or payload.get("plan_record_id") != result["plan_record_id"] or payload.get("request_id") != result["request_id"] or payload.get("analysis_id") != result["analysis_id"] or payload.get("worker_count") != int(request["worker_count"]) or payload.get("solver_status") != result["solver_status"] or any(payload.get(field) != result[field] for field in ("exact_result_hash", "candidate_vector_hash", "witness_vector_hash", "certification_vector_hash", "failure_reason_vector_hash")):
            raise RTA4FormalValidationError("terminal/result mathematical summary mismatch")
    attempts = _unique(tables["formal_rta_attempts.csv"], "attempt_id", "attempt")
    attempts_by_execution: Dict[str, list[Mapping[str, str]]] = {}
    for attempt in attempts.values():
        request = request_exec.get(attempt["execution_run_id"])
        if request is None or attempt["analysis_id"] != request["analysis_id"] or attempt["request_id"] != request["request_id"] or attempt["plan_record_id"] != request["plan_record_id"]:
            raise RTA4FormalValidationError("attempt belongs to unknown/mismatched request")
        if attempt["worker_count"] != request["worker_count"]:
            raise RTA4FormalValidationError("attempt worker count/request mismatch")
        result = results.get(attempt["execution_run_id"])
        if result is None or attempt["solver_status"] != result["solver_status"] or attempt["parent_attempt_id"] != NA or attempt["timeout_budget_seconds"] != "0" or attempt["started_at_utc"] != NONFORMAL_TEST_FIXTURE:
            raise RTA4FormalValidationError("attempt/result fixture-state mismatch")
        error_solver = result["solver_status"] in {"NUMERIC_ERROR", "INTERNAL_ERROR"}
        if (attempt["failure_origin"] == NA) == error_solver:
            raise RTA4FormalValidationError(
                "attempt failure origin/result solver state mismatch"
            )
        attempts_by_execution.setdefault(attempt["execution_run_id"], []).append(attempt)
    if require_complete and set(attempts_by_execution) != set(request_exec):
        raise RTA4FormalValidationError("request/attempt execution set mismatch")
    if any(
        len(rows) != 1 or rows[0]["attempt_number"] != "1"
        for rows in attempts_by_execution.values()
    ):
        raise RTA4FormalValidationError("bounded fixture requires exactly one canonical attempt")
    # External source relations are exact trusted-plan members and exact joins.
    expected_relations = {row["plan_relation_id"]: row for row in plan_manifest["source_relations"]}
    dependencies = _unique(tables["formal_dependencies.csv"], "plan_relation_id", "dependency")
    if require_complete and set(dependencies) != set(expected_relations):
        raise RTA4FormalValidationError("trusted source relation membership mismatch")
    sources = source_closures or {}
    source_core1 = sources.get("CORE-1")
    source_closure = None
    if expected_relations:
        if source_core1 is None: raise RTA4FormalValidationError("required CORE-1 source closure is missing")
        source_closure = refresh_validated_closure(
            source_core1, require_complete=True,
        )
        if source_closure.metadata["core"] != "CORE-1": raise RTA4FormalValidationError("source closure is not CORE-1")
    if source_closure is not None:
        source_requests = {row["request_id"]: row for row in source_closure.table("formal_rta_requests.csv")}
        source_results = {row["request_id"]: row for row in source_closure.table("formal_rta_taskset_results.csv")}
        local_by_slot_e0: Dict[tuple[str, str], Mapping[str, str]] = {}
        for row in requests.values(): local_by_slot_e0.setdefault((row["taskset_slot_id"], row["exact_e0"]), row)
        for relation_id, dependency in dependencies.items():
            plan = expected_relations[relation_id]
            source_request = source_requests.get(plan["source_analysis_id"])
            source_result = source_results.get(plan["source_analysis_id"])
            target = local_by_slot_e0.get((plan["taskset_slot_id"], plan["exact_e0"]))
            if config["core"] == "CORE-3":
                target = source_request
            if source_request is None or source_result is None or target is None: raise RTA4FormalValidationError("source/target analysis is missing")
            allowed_source_methods = (
                {"LOC_THETA_LOC", "PH_THETA_PH"}
                if config["core"] == "CORE-2" else set(RECURSIVE_CHAIN)
            )
            if plan["method"] not in allowed_source_methods:
                raise RTA4FormalValidationError("source relation method is not allowed")
            if any(
                source_request[key] != target[key]
                for key in (
                    "taskset_skeleton_id", "taskset_id", "taskset_hash",
                    "exact_e0", "service_identity", "power_vector_hash",
                    "theory_document_sha256", "numeric_contract_sha256",
                )
            ):
                raise RTA4FormalValidationError(
                    "source/target adapter mathematical-input relation mismatch"
                )
            expected = {
                "analysis_id": target["analysis_id"], "source_analysis_id": source_request["analysis_id"],
                "source_request_id": source_request["request_id"], "source_core": "CORE-1", "target_core": config["core"],
                "taskset_skeleton_id": target["taskset_skeleton_id"], "taskset_id": target["taskset_id"], "taskset_hash": target["taskset_hash"],
                "method": plan["method"], "exact_e0": plan["exact_e0"], "service_identity": source_request["service_identity"],
                "power_vector_hash": source_request["power_vector_hash"], "theory_document_sha256": source_request["theory_document_sha256"],
                "numeric_contract_sha256": source_request["numeric_contract_sha256"], "source_exact_input_identity": source_request["exact_input_identity"],
                "target_exact_input_identity": target["exact_input_identity"], "source_result_hash": source_result["exact_result_hash"],
                "source_plan_sha256": source_closure.metadata["plan_sha256"], "source_closure_sha256": source_closure.closure_sha256,
                "dependency_status": "VALIDATED_EXTERNAL_SOURCE", "fallback_used": "false",
            }
            if any(dependency[key] != value for key, value in expected.items()): raise RTA4FormalValidationError("source closure exact-input/result binding mismatch")
    simulations = _unique(tables["formal_simulation_runs.csv"], "plan_record_id", "simulation plan record")
    if require_complete and set(simulations) != set(simulation_records):
        raise RTA4FormalValidationError("trusted simulation membership mismatch")
    used_tasksets = {row["taskset_id"] for row in requests.values()}
    used_tasksets.update(row["taskset_id"] for row in simulations.values())
    if set(certificates) != used_tasksets:
        raise RTA4FormalValidationError(
            "certificate-derived tables do not equal the trusted plan taskset set"
        )
    audits: Dict[str, Any] = {}
    simulation_by_id: Dict[str, Mapping[str, str]] = {}
    _unique(
        tables["formal_simulation_task_results.csv"],
        "simulation_task_result_id", "simulation task result",
    )
    _unique(
        tables["formal_simulation_job_results.csv"],
        "simulation_job_result_id", "simulation job result",
    )
    jobs_by_simulation: Dict[str, list[Mapping[str, str]]] = {}
    for row in tables["formal_simulation_job_results.csv"]:
        jobs_by_simulation.setdefault(row["simulation_id"], []).append(row)
    expected_simulation_task_rows: list[Mapping[str, str]] = []
    expected_simulation_job_rows: list[Mapping[str, str]] = []
    for plan_id, simulation in simulations.items():
        record = simulation_records[plan_id]
        if simulation["plan_simulation_id"] != record.execution_id or simulation["execution_run_id"] != record.execution_id or simulation["taskset_slot_id"] != record.taskset_slot_id or simulation["taskset_skeleton_slot_id"] != record.taskset_skeleton_slot_id:
            raise RTA4FormalValidationError("simulation/plan identity mismatch")
        material = record.material
        expected_plan_fields = {
            "release_mode": material["release_mode"],
            "applicability_track": material["applicability_track"],
            "battery_model": material["battery_model"],
            "battery_capacity": material["battery_capacity"],
            "physical_initial_energy": material["physical_initial_energy"],
            "service_harvest_identity": formal_service_identity(
                material["service_scale"]
            ),
        }
        if any(simulation[key] != str(value) for key, value in expected_plan_fields.items()):
            raise RTA4FormalValidationError("simulation/trusted plan parameter mismatch")
        certificate = certificates.get(simulation["taskset_id"])
        if certificate is None: raise RTA4FormalValidationError("simulation taskset certificate missing")
        _validate_certificate_plan_binding(config, record, certificate)
        projection = build_release_projection(certificate, release_mode=simulation["release_mode"])
        window = ReleaseObservationWindow.for_certificate(certificate)
        offsets = canonical_json([row.arrival_offset for row in projection.offsets])
        if simulation["taskset_skeleton_id"] != certificate.taskset_skeleton_id or simulation["taskset_hash"] != certificate.taskset_hash or simulation["release_projection_id"] != projection.release_projection_id or simulation["release_vector_hash"] != projection.release_vector_hash or simulation["exact_offsets_json"] != offsets or simulation["release_horizon"] != str(window.release_horizon) or simulation["observation_horizon"] != str(window.observation_horizon) or simulation["scheduler"] != TARGET_SCHEDULER or simulation["trace_contract"] != SIMULATOR_TRACE_CONTRACT_VERSION or simulation["simulation_status"] != "COMPLETED":
            raise RTA4FormalValidationError("simulation PR-C projection/window identity mismatch")
        actual_id = simulation_applicability_identity(taskset_id=certificate.taskset_id, release_projection_id=projection.release_projection_id,
            scheduler=TARGET_SCHEDULER, service_identity=simulation["service_harvest_identity"], initial_battery=simulation["physical_initial_energy"],
            battery_capacity=simulation["battery_capacity"], window=window, applicability_track=simulation["applicability_track"])
        if simulation["simulation_id"] != actual_id: raise RTA4FormalValidationError("simulation mathematical identity mismatch")
        trace_path = _certificate_path(root, simulation["trace_path"])
        if hashlib.sha256(trace_path.read_bytes()).hexdigest() != simulation["trace_sha256"]: raise RTA4FormalValidationError("simulation trace hash mismatch")
        (
            reconstructed_task_rows, reconstructed_job_rows,
            reconstructed_misses, reconstructed_max,
            reconstructed_task_hash, reconstructed_job_hash,
        ) = _reconstruct_simulation_result_rows(
            simulation=simulation, certificate=certificate,
            projection=projection, window=window,
            actual_jobs=jobs_by_simulation.get(simulation["simulation_id"], ()),
            trace_path=trace_path, common=common,
        )
        expected_simulation_task_rows.extend(reconstructed_task_rows)
        expected_simulation_job_rows.extend(reconstructed_job_rows)
        if (
            simulation["deadline_miss_count"] != str(reconstructed_misses)
            or simulation["max_observed_response"] != str(reconstructed_max)
            or simulation["task_result_vector_hash"] != reconstructed_task_hash
            or simulation["job_result_vector_hash"] != reconstructed_job_hash
        ):
            raise RTA4FormalValidationError(
                "simulation summary is not derived from canonical raw jobs"
            )
        audit = parse_release_trace(trace_path, project_certificate_for_simulation(certificate, projection), expected_simulation_id=actual_id,
            expected_taskset_hash=certificate.taskset_hash, expected_certificate=certificate, expected_projection=projection, window=window)
        no_overflow = build_no_overflow_evidence(initial_battery=simulation["physical_initial_energy"], battery_capacity=simulation["battery_capacity"],
            offered_harvest=simulation["offered_harvest"], required_margin=simulation["required_margin"], service_identity=simulation["service_harvest_identity"], observation_horizon=window.observation_horizon)
        evidence = validate_simulation_evidence(audit, service_identity=simulation["service_harvest_identity"], initial_battery=simulation["physical_initial_energy"],
            battery_capacity=simulation["battery_capacity"], applicability_track=simulation["applicability_track"])
        if simulation["release_audit_id"] != audit.release_trace_audit_id or simulation["no_overflow_evidence_id"] != no_overflow.evidence_id or simulation["validated_simulation_evidence_id"] != evidence.evidence_id:
            raise RTA4FormalValidationError("simulation evidence identity mismatch")
        if (audit.simulation_outcome == SIM_DEADLINE_MISS) != (
            int(simulation["deadline_miss_count"]) > 0
        ):
            raise RTA4FormalValidationError("simulation miss count/trace outcome mismatch")
        from .rta4_formal_pipeline import recompute_simulation_result_hash
        terminal = terminal_map.get(record.execution_id)
        expected_terminal_keys = {
            "schema_version", "schema_sha256", "plan_sha256",
            "config_semantic_hash", "execution_run_id", "plan_record_id",
            "analysis_id", "request_id", "worker_count", "solver_status",
            "exact_result_hash",
        }
        if terminal is None or set(terminal) != expected_terminal_keys or terminal["plan_record_id"] != record.record_id or terminal["analysis_id"] != NA or terminal["request_id"] != record.execution_id or terminal["solver_status"] != "COMPLETED" or terminal["worker_count"] != 1 or terminal["exact_result_hash"] != recompute_simulation_result_hash(simulation):
            raise RTA4FormalValidationError("simulation terminal/raw result mismatch")
        audits[actual_id] = (audit, no_overflow, evidence)
        simulation_by_id[actual_id] = simulation
    if tables["formal_simulation_task_results.csv"] != tuple(expected_simulation_task_rows):
        raise RTA4FormalValidationError(
            "simulation task rows do not match canonical raw job aggregation"
        )
    if tables["formal_simulation_job_results.csv"] != tuple(expected_simulation_job_rows):
        raise RTA4FormalValidationError(
            "simulation job rows are not canonical complete release evidence"
        )
    expected_comparisons = {row["plan_comparison_id"]: row for row in plan_manifest["applicability_rows"]}
    applicability = _unique(tables["formal_applicability.csv"], "plan_comparison_id", "applicability plan row")
    if require_complete and set(applicability) != set(expected_comparisons): raise RTA4FormalValidationError("trusted applicability membership mismatch")
    source_request_by_plan = {}
    source_result_by_plan = {}
    if source_closure is not None:
        source_request_by_plan = {r["request_id"]: r for r in source_closure.table("formal_rta_requests.csv")}
        source_result_by_plan = {r["request_id"]: r for r in source_closure.table("formal_rta_taskset_results.csv")}
    plan_sim_to_actual = {r["plan_simulation_id"]: r for r in simulations.values()}
    source_tasks = {r["analysis_id"]: [] for r in (source_result_by_plan.values() if source_result_by_plan else [])}
    if source_closure is not None:
        for row in source_closure.table("formal_rta_task_results.csv"): source_tasks.setdefault(row["analysis_id"], []).append(row)
    for comparison_id, row in applicability.items():
        plan = expected_comparisons[comparison_id]
        simulation = plan_sim_to_actual.get(plan["simulation_id"])
        source_request = source_request_by_plan.get(plan["source_analysis_id"])
        source_result = source_result_by_plan.get(plan["source_analysis_id"])
        if simulation is None or source_request is None or source_result is None: raise RTA4FormalValidationError("applicability source/simulation missing")
        if (
            source_request["taskset_id"] != simulation["taskset_id"]
            or source_request["taskset_hash"] != simulation["taskset_hash"]
            or source_request["method"] != plan["method"]
            or source_request["exact_e0"] != plan["exact_e0"]
        ):
            raise RTA4FormalValidationError(
                "applicability analysis/simulation mathematical join mismatch"
            )
        audit, no_overflow, evidence = audits[simulation["simulation_id"]]
        evaluation = evaluate_e0_condition(audit, plan["exact_e0"])
        rta_outcome = RTA_PASS if _truth(source_result["taskset_proven"]) else RTA_FAIL
        simulation_outcome = audit.simulation_outcome
        assessment = assess_applicability(requested_track=simulation["applicability_track"], release_trace_audit=audit,
            requested_e0=plan["exact_e0"], e0_evaluation=evaluation, no_overflow_evidence=no_overflow, simulation_evidence=evidence,
            expected_taskset_id=simulation["taskset_id"], expected_taskset_hash=simulation["taskset_hash"], expected_release_projection_id=simulation["release_projection_id"],
            expected_simulation_id=simulation["simulation_id"], rta_outcome=rta_outcome, simulation_outcome=simulation_outcome)
        candidates = [_optional_int(task["candidate_response_time"]) for task in source_tasks.get(source_request["analysis_id"], [])]
        candidate = max((value for value in candidates if value is not None), default=None)
        expected = {
            "analysis_id": source_request["analysis_id"], "simulation_id": simulation["simulation_id"], "taskset_id": simulation["taskset_id"],
            "method": plan["method"], "exact_e0": plan["exact_e0"], "release_audit_id": audit.release_trace_audit_id,
            "e0_evaluation_id": evaluation.evaluation_id, "no_overflow_evidence_id": no_overflow.evidence_id,
            "validated_simulation_evidence_id": evidence.evidence_id, "applicability_track": simulation["applicability_track"],
            "e0_condition_status": evaluation.status, "theorem_applicability": assessment.category,
            "theorem_comparison_eligible": "true" if assessment.theorem_comparison_eligible else "false",
            "rta_outcome": rta_outcome, "simulation_outcome": simulation_outcome,
            "comparison_status": _comparison_status(evaluation.status, rta_outcome, simulation_outcome),
            "candidate_response_time": NA if candidate is None else str(candidate),
            "observed_response_time": simulation["max_observed_response"],
            "soundness_counterexample": "true" if assessment.theorem_applicable_soundness_counterexample else "false",
            "empirical_difference": "true" if assessment.empirical_difference else "false",
        }
        from .rta4_formal_pipeline import formal_comparison_id
        expected["comparison_id"] = formal_comparison_id(
            plan_comparison_id=plan["plan_comparison_id"],
            analysis_id=source_request["analysis_id"],
            simulation_id=simulation["simulation_id"],
            release_audit_id=audit.release_trace_audit_id,
            e0_evaluation_id=evaluation.evaluation_id,
            no_overflow_evidence_id=no_overflow.evidence_id,
            validated_simulation_evidence_id=evidence.evidence_id,
            rta_outcome=rta_outcome,
            simulation_outcome=simulation_outcome,
        )
        if any(row[key] != value for key, value in expected.items()): raise RTA4FormalValidationError("applicability PR-C evidence/classification mismatch")
    expected_dominance = tuple(
        _stringify_expected("formal_dominance_checks.csv", row, common)
        for row in recompute_dominance_rows(
            tuple(results.values()), tables["formal_rta_task_results.csv"]
        )
    )
    if tables["formal_dominance_checks.csv"] != expected_dominance:
        raise RTA4FormalValidationError(
            "dominance evidence does not match canonical raw task results"
        )
    expected_monotonicity = tuple(
        _stringify_expected("formal_monotonicity_checks.csv", row, common)
        for row in recompute_monotonicity_rows(
            tuple(results.values()), tables["formal_rta_task_results.csv"]
        )
    )
    if tables["formal_monotonicity_checks.csv"] != expected_monotonicity:
        raise RTA4FormalValidationError(
            "monotonicity evidence does not match canonical raw task results"
        )
    expected_worker = recompute_worker_consistency_rows(tuple(results.values()), terminal_map)
    actual_worker = tables["formal_worker_consistency.csv"]
    expected_normalized = tuple(_stringify_expected("formal_worker_consistency.csv", row, common) for row in expected_worker)
    if actual_worker != expected_normalized: raise RTA4FormalValidationError("worker consistency rows do not match raw execution results")
    findings = (*validate_dominance(tuple(results.values()), tables["formal_rta_task_results.csv"]),
                *validate_monotonicity(tuple(results.values()), tables["formal_rta_task_results.csv"]),
                *validate_soundness(tuple(applicability.values())), *validate_worker_consistency(actual_worker))
    if any(f.severity == P0 for f in findings): raise RTA4FormalValidationError("P0 hard validator finding in run closure")
    if any(row["severity"] == P0 for row in tables["formal_failures.csv"]): raise RTA4FormalValidationError("persisted P0 failure in run closure")
    return ValidatedFormalClosure(root, metadata, config, plan_manifest, tables, terminal_payloads, _closure_digest(root))


__all__ = ["FIXED_D_CHAIN", "FormalFinding", "P0", "P1", "P2", "P3", "RECURSIVE_CHAIN",
           "RTA4FormalValidationError", "ValidatedFormalClosure", "validate_dominance",
           "refresh_validated_closure",
           "recompute_dominance_rows", "recompute_monotonicity_rows",
           "recompute_worker_consistency_rows",
           "validate_formal_run_closure", "validate_monotonicity", "validate_soundness",
           "validate_worker_consistency"]
