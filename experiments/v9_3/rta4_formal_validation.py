"""Closure and hard-stop validators for the opt-in RTA4 formal profile."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .constrained_taskset_identity import TasksetIdentityCertificate
from .rta4_formal_config import canonical_json
from .rta4_formal_schema import (
    FORMAL_TABLES, RTA4_FORMAL_SCHEMA_MANIFEST, formal_schema_hash,
    formal_schema_manifest,
)
from .rta4_formal_writer import FORMAL_RUN_METADATA, FORMAL_TERMINAL_DIRECTORY


P0 = "P0"
P1 = "P1"
P2 = "P2"
P3 = "P3"
SEVERITIES = (P0, P1, P2, P3)
RECURSIVE_CHAIN = (
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
FIXED_D_CHAIN = ("CW_D", "LOC_D", "PH_D", "SEQ_D")


class RTA4FormalValidationError(RuntimeError):
    """Raised when persisted evidence is not one complete validated run."""


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
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
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
    return rows


def _unique(rows: Iterable[Mapping[str, str]], key: str, label: str) -> Dict[str, Mapping[str, str]]:
    result: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        identity = row.get(key, "")
        if not identity:
            raise RTA4FormalValidationError(f"empty {label} identity")
        if identity in result:
            raise RTA4FormalValidationError(f"duplicate {label}: {identity}")
        result[identity] = row
    return result


def _truth(value: Any) -> bool:
    if value is True or value in {"true", "True", "1", 1}:
        return True
    if value is False or value in {"false", "False", "0", "", 0, None}:
        return False
    raise RTA4FormalValidationError(f"invalid strict boolean: {value!r}")


def _optional_int(value: Any) -> int | None:
    if value in {None, "", "NA"}:
        return None
    if isinstance(value, bool):
        raise RTA4FormalValidationError("boolean is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RTA4FormalValidationError(f"invalid integer: {value!r}") from exc


def _certificate_path(root: Path, text: str) -> Path:
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RTA4FormalValidationError("unsafe certificate path")
    path = root.joinpath(*relative.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RTA4FormalValidationError("certificate escapes run root") from exc
    return path


def _validate_taskset_certificates(
    root: Path, skeletons: Sequence[Mapping[str, str]],
    tasksets: Sequence[Mapping[str, str]], tasks: Sequence[Mapping[str, str]],
) -> None:
    skeleton_by_id = _unique(skeletons, "taskset_skeleton_id", "skeleton")
    taskset_by_id = _unique(tasksets, "taskset_id", "taskset")
    task_keys: set[tuple[str, str]] = set()
    for row in tasks:
        key = (row["taskset_id"], row["task_id"])
        if key in task_keys:
            raise RTA4FormalValidationError("duplicate formal task row")
        task_keys.add(key)
    for taskset_id, row in taskset_by_id.items():
        path = _certificate_path(root, row["certificate_path"])
        try:
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != row["certificate_sha256"]:
                raise RTA4FormalValidationError("taskset certificate SHA-256 mismatch")
            certificate = TasksetIdentityCertificate.from_canonical_bytes(payload)
        except RTA4FormalValidationError:
            raise
        except Exception as exc:
            raise RTA4FormalValidationError("taskset certificate validation failed") from exc
        if (
            certificate.taskset_id != taskset_id
            or certificate.taskset_hash != row["taskset_hash"]
            or certificate.taskset_skeleton_id != row["taskset_skeleton_id"]
            or certificate.power_vector_hash != row["power_vector_hash"]
        ):
            raise RTA4FormalValidationError("taskset certificate/table identity mismatch")
        skeleton = skeleton_by_id.get(certificate.taskset_skeleton_id)
        if skeleton is None or skeleton["generation_request_id"] != certificate.generation_request_id:
            raise RTA4FormalValidationError("skeleton/certificate provenance mismatch")
        if len([key for key in task_keys if key[0] == taskset_id]) != len(certificate.tasks):
            raise RTA4FormalValidationError("task rows/certificate count mismatch")


def validate_dominance(
    taskset_rows: Sequence[Mapping[str, Any]],
    task_rows: Sequence[Mapping[str, Any]],
) -> Tuple[FormalFinding, ...]:
    findings: list[FormalFinding] = []
    for row in taskset_rows:
        if _truth(row.get("fallback_used")):
            findings.append(FormalFinding(P0, "METHOD_FALLBACK", "PH/SEQ fallback is forbidden", str(row.get("analysis_id", ""))))
    taskset_index = {
        (str(row.get("taskset_id")), str(row.get("exact_e0")), str(row.get("method"))): row
        for row in taskset_rows
    }
    task_index = {
        (
            str(row.get("taskset_id")), str(row.get("exact_e0")),
            str(row.get("method")), str(row.get("task_id")),
        ): row for row in task_rows
    }
    groups = {(key[0], key[1]) for key in taskset_index}
    for taskset_id, e0 in sorted(groups):
        for chain in (RECURSIVE_CHAIN, FIXED_D_CHAIN):
            for weak, strong in zip(chain, chain[1:]):
                left = taskset_index.get((taskset_id, e0, weak))
                right = taskset_index.get((taskset_id, e0, strong))
                if left is None or right is None:
                    continue
                if _truth(left.get("taskset_proven")) and not _truth(right.get("taskset_proven")):
                    findings.append(FormalFinding(P0, "CERTIFICATION_SET_NOT_CONTAINED", f"{strong} does not contain {weak}", taskset_id))
                task_ids = {
                    key[3] for key in task_index
                    if key[:2] == (taskset_id, e0) and key[2] in {weak, strong}
                }
                for task_id in task_ids:
                    weak_row = task_index.get((taskset_id, e0, weak, task_id))
                    strong_row = task_index.get((taskset_id, e0, strong, task_id))
                    if weak_row is None or strong_row is None:
                        continue
                    weak_candidate = _optional_int(weak_row.get("candidate_response_time"))
                    strong_candidate = _optional_int(strong_row.get("candidate_response_time"))
                    if weak_candidate is not None and strong_candidate is not None and strong_candidate > weak_candidate:
                        findings.append(FormalFinding(P0, "CANDIDATE_DOMINANCE_VIOLATION", f"{strong} candidate exceeds {weak}", f"{taskset_id}:{task_id}"))
    return tuple(findings)


def validate_monotonicity(rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    """Check only E0/service/power; deadline is intentionally excluded."""

    findings: list[FormalFinding] = []
    applicable = {"e0", "service_scale", "power_scale"}
    groups: Dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        axis = str(row.get("axis", ""))
        if axis not in applicable:
            continue
        key = (str(row.get("taskset_skeleton_id")), str(row.get("method")), axis)
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        try:
            ordered = sorted(group, key=lambda row: Fraction(str(row["axis_value"])))
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalValidationError("invalid monotonicity axis value") from exc
        if key[2] == "power_scale":
            ordered.reverse()
        for weaker, stronger in zip(ordered, ordered[1:]):
            weak_certified = _truth(weaker.get("taskset_proven"))
            strong_certified = _truth(stronger.get("taskset_proven"))
            weak_candidate = _optional_int(weaker.get("candidate_response_time"))
            strong_candidate = _optional_int(stronger.get("candidate_response_time"))
            if weak_certified and not strong_certified:
                findings.append(FormalFinding(P0, "MONOTONICITY_CERTIFICATION_VIOLATION", f"stronger {key[2]} condition lost certification", key[0]))
            if weak_candidate is not None and strong_candidate is not None and strong_candidate > weak_candidate:
                findings.append(FormalFinding(P0, "MONOTONICITY_CANDIDATE_VIOLATION", f"stronger {key[2]} condition worsened candidate", key[0]))
    return tuple(findings)


def validate_soundness(rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    findings: list[FormalFinding] = []
    for row in rows:
        eligible = _truth(row.get("theorem_comparison_eligible"))
        counterexample = _truth(row.get("soundness_counterexample"))
        if eligible and (
            counterexample or str(row.get("comparison_status")) == "RTA_PASS_SIM_FAIL"
        ):
            findings.append(FormalFinding(P0, "THEOREM_APPLICABLE_COUNTEREXAMPLE", "theorem-applicable RTA pass/simulation fail", str(row.get("comparison_id", ""))))
        candidate = _optional_int(row.get("candidate_response_time"))
        observed = _optional_int(row.get("observed_response_time"))
        if eligible and candidate is not None and observed is not None and observed > candidate:
            findings.append(FormalFinding(P0, "OBSERVED_RESPONSE_EXCEEDS_CANDIDATE", "theorem-applicable response exceeds candidate", str(row.get("comparison_id", ""))))
    return tuple(findings)


def validate_worker_consistency(rows: Sequence[Mapping[str, Any]]) -> Tuple[FormalFinding, ...]:
    findings = []
    for row in rows:
        fields = (
            "solver_status_match", "candidate_match", "witness_match",
            "certification_match", "failure_reason_match", "math_hash_match",
        )
        if not all(_truth(row.get(field)) for field in fields):
            findings.append(FormalFinding(P0, "WORKER_MATHEMATICAL_MISMATCH", "worker count changed mathematical output", str(row.get("mathematical_request_id", ""))))
        if row.get("reference_math_result_hash") != row.get("compared_math_result_hash"):
            findings.append(FormalFinding(P0, "WORKER_RESULT_HASH_MISMATCH", "worker mathematical result hash differs", str(row.get("mathematical_request_id", ""))))
    return tuple(findings)


def _closure_digest(root: Path) -> str:
    digest = hashlib.sha256(b"ASAP_BLOCK:V9.3:RTA4_CLOSURE:v1\0")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "formal_file_hashes.sha256":
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def validate_formal_run_closure(
    root: Path | str, *, require_complete: bool = True,
    require_authorized_formal: bool = False,
) -> ValidatedFormalClosure:
    root = Path(root)
    marker = _strict_json(root / RTA4_FORMAL_SCHEMA_MANIFEST)
    if dict(marker) != formal_schema_manifest() or marker.get("schema_sha256") != formal_schema_hash():
        raise RTA4FormalValidationError("schema manifest/hash mismatch")
    metadata = _strict_json(root / FORMAL_RUN_METADATA)
    for key in ("schema_sha256", "plan_sha256", "config_semantic_hash"):
        if not metadata.get(key):
            raise RTA4FormalValidationError(f"run metadata lacks {key}")
    if metadata["schema_sha256"] != formal_schema_hash():
        raise RTA4FormalValidationError("run metadata/schema mismatch")
    if require_authorized_formal and (
        metadata.get("execution_class") == "FORMAL"
        and metadata.get("formal_authorized") is not True
    ):
        raise RTA4FormalValidationError("unauthorized formal run cannot be aggregated")
    tables = {
        name: _read_exact_csv(root / name, columns)
        for name, columns in FORMAL_TABLES.items()
    }
    for name, rows in tables.items():
        for row in rows:
            if (
                row["schema_sha256"] != metadata["schema_sha256"]
                or row["plan_sha256"] != metadata["plan_sha256"]
                or row["config_semantic_hash"] != metadata["config_semantic_hash"]
            ):
                raise RTA4FormalValidationError(f"row identity drift in {name}")

    requests = _unique(tables["formal_rta_requests.csv"], "request_id", "request")
    analyses = _unique(tables["formal_rta_requests.csv"], "analysis_id", "analysis")
    attempts = _unique(tables["formal_rta_attempts.csv"], "attempt_id", "attempt")
    for attempt in attempts.values():
        if attempt["analysis_id"] not in analyses:
            raise RTA4FormalValidationError("attempt belongs to unknown analysis")
    result_rows = tables["formal_rta_taskset_results.csv"]
    result_ids = _unique(result_rows, "request_id", "taskset result")
    for result in result_rows:
        if result["analysis_id"] not in analyses:
            raise RTA4FormalValidationError("result belongs to unknown analysis")
    terminal_directory = root / FORMAL_TERMINAL_DIRECTORY
    terminal_paths = sorted(terminal_directory.glob("*.json")) if terminal_directory.is_dir() else []
    terminal_payloads = tuple(_strict_json(path) for path in terminal_paths)
    terminal_ids = set()
    for path, payload in zip(terminal_paths, terminal_payloads):
        request_id = str(payload.get("request_id", ""))
        if request_id != path.stem or request_id in terminal_ids:
            raise RTA4FormalValidationError("terminal JSON identity conflict")
        terminal_ids.add(request_id)
        for key in ("schema_sha256", "plan_sha256", "config_semantic_hash"):
            if payload.get(key) != metadata[key]:
                raise RTA4FormalValidationError("terminal JSON closure identity mismatch")
    if require_complete and (
        set(requests) != set(result_ids) or set(requests) != terminal_ids
    ):
        raise RTA4FormalValidationError("request/result/terminal set mismatch")

    _validate_taskset_certificates(
        root, tables["formal_taskset_skeletons.csv"],
        tables["formal_tasksets.csv"], tables["formal_tasks.csv"],
    )
    taskset_ids = {row["taskset_id"] for row in tables["formal_tasksets.csv"]}
    for request in requests.values():
        if request["taskset_id"] not in taskset_ids:
            raise RTA4FormalValidationError("request/taskset certificate mismatch")

    dependency_sources = set()
    for dependency in tables["formal_dependencies.csv"]:
        source = dependency["source_analysis_id"]
        dependency_sources.add(source)
        if source not in analyses:
            if (
                dependency["dependency_status"] != "VALIDATED_EXTERNAL_SOURCE"
                or not dependency["source_plan_sha256"]
                or not dependency["source_closure_sha256"]
            ):
                raise RTA4FormalValidationError("source-analysis join mismatch")
        if _truth(dependency["fallback_used"]):
            raise RTA4FormalValidationError("dependency fallback is forbidden")

    simulations = _unique(tables["formal_simulation_runs.csv"], "simulation_id", "simulation")
    simulation_math_keys = set()
    for simulation in simulations.values():
        key = (
            simulation["taskset_id"], simulation["release_projection_id"],
            simulation["scheduler"], simulation["service_harvest_identity"],
            simulation["physical_initial_energy"], simulation["battery_capacity"],
            simulation["release_horizon"], simulation["observation_horizon"],
            simulation["applicability_track"],
        )
        if key in simulation_math_keys:
            raise RTA4FormalValidationError("simulation reuse mismatch/duplicate mathematical run")
        simulation_math_keys.add(key)
    for applicability in tables["formal_applicability.csv"]:
        if applicability["simulation_id"] not in simulations:
            raise RTA4FormalValidationError("applicability references unknown simulation")
        if applicability["analysis_id"] not in analyses and applicability["analysis_id"] not in dependency_sources:
            raise RTA4FormalValidationError("applicability references unknown source analysis")
        required_evidence = (
            "release_audit_id", "e0_evaluation_id",
            "validated_simulation_evidence_id",
        )
        if any(not applicability[key] for key in required_evidence):
            raise RTA4FormalValidationError("applicability evidence mismatch")
        simulation = simulations[applicability["simulation_id"]]
        if (
            applicability["taskset_id"] != simulation["taskset_id"]
            or applicability["release_audit_id"] != simulation["release_audit_id"]
            or applicability["no_overflow_evidence_id"] != simulation["no_overflow_evidence_id"]
            or applicability["validated_simulation_evidence_id"]
            != simulation["validated_simulation_evidence_id"]
        ):
            raise RTA4FormalValidationError("applicability/simulation evidence mismatch")

    findings = (
        *validate_dominance(result_rows, tables["formal_rta_task_results.csv"]),
        *validate_monotonicity(result_rows),
        *validate_soundness(tables["formal_applicability.csv"]),
        *validate_worker_consistency(tables["formal_worker_consistency.csv"]),
    )
    if any(finding.severity == P0 for finding in findings):
        raise RTA4FormalValidationError("P0 hard validator finding in run closure")
    if any(row["severity"] == P0 for row in tables["formal_failures.csv"]):
        raise RTA4FormalValidationError("persisted P0 failure in run closure")
    return ValidatedFormalClosure(
        root, metadata, tables, terminal_payloads, _closure_digest(root),
    )


__all__ = [
    "FIXED_D_CHAIN", "FormalFinding", "P0", "P1", "P2", "P3",
    "RECURSIVE_CHAIN", "RTA4FormalValidationError", "ValidatedFormalClosure",
    "validate_dominance", "validate_formal_run_closure",
    "validate_monotonicity", "validate_soundness",
    "validate_worker_consistency",
]
