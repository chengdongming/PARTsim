#!/usr/bin/env python3
"""Deterministic data admission, aggregation, and publication for B4-PE I5D."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import statistics_inference as inference


class StatisticsError(ValueError):
    """An input, computation, or output violates the frozen contract."""


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
CONTRACT_PATH = B4_DIR / "statistics_contract_v1.json"
ANALYSIS_CONTRACT_PATH = B4_DIR / "analysis_contract_v2.json"
OBSERVABILITY_CONTRACT_PATH = B4_DIR / "observability_summary_contract_v2.json"
CANDIDATE_PATH = B4_DIR / "b4_pe_freeze_candidate_v3.json"
IMPLEMENTATION_FILES = (
    CONTRACT_PATH,
    Path(__file__),
    B4_DIR / "statistics_inference.py",
    B4_DIR / "statistics_plotting.py",
    B4_DIR / "run_statistics.py",
)


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StatisticsError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                StatisticsError(f"non-finite JSON number: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StatisticsError(f"invalid JSON: {Path(path).name}") from exc


CONTRACT = load_json(CONTRACT_PATH)
ANALYSIS_CONTRACT = load_json(ANALYSIS_CONTRACT_PATH)
ALGORITHMS = tuple(CONTRACT["algorithm_order"])
PILOT_ALGORITHMS = tuple(CONTRACT["pilot_algorithm_order"])
COMPARATORS = tuple(CONTRACT["comparison_order"])
CLUSTER_FIELDS = tuple(CONTRACT["cluster_dimension_order"])
ROW_FIELDS = {
    name: tuple(fields) for name, fields in CONTRACT["row_field_order"].items()
}
OUTPUT_ORDER = tuple(CONTRACT["output_order"])
CASE_FIELDS = tuple(ANALYSIS_CONTRACT["case_field_order"])
TASK_FIELDS = tuple(ANALYSIS_CONTRACT["task_field_order"])
ROOT_SEED = CONTRACT["inference"]["root_seed"]
BOOTSTRAP_REPLICATES = CONTRACT["inference"]["bootstrap_replicates"]
RANDOMIZATION_RANDOM_DRAWS = CONTRACT["inference"]["randomization_random_draws"]


def _require(condition, message):
    if not condition:
        raise StatisticsError(message)


def _validate_statistics_contract():
    _require(CONTRACT["contract_version"] == 1, "statistics contract version mismatch")
    _require(len(ALGORITHMS) == 9 and len(set(ALGORITHMS)) == 9, "statistics algorithm order mismatch")
    _require(len(COMPARATORS) == 4 and set(COMPARATORS) < set(ALGORITHMS), "statistics comparison order mismatch")
    _require(len(CLUSTER_FIELDS) == len(set(CLUSTER_FIELDS)), "duplicate cluster dimension")
    _require(len(OUTPUT_ORDER) == 29 and len(OUTPUT_ORDER) == len(set(OUTPUT_ORDER)), "statistics output order mismatch")
    _require(OUTPUT_ORDER[-2:] == ("statistics_audit.json", "statistics_manifest.json"), "statistics hash DAG order mismatch")
    _require(BOOTSTRAP_REPLICATES == 10000, "bootstrap replicate contract mismatch")
    _require(
        RANDOMIZATION_RANDOM_DRAWS == 100000,
        "randomization random-draw contract mismatch",
    )
    inference_contract = CONTRACT["inference"]
    _require(
        inference_contract["randomization_observed_permutation_forced_in_draws"]
        is False,
        "observed permutation must not be forced into random draws",
    )
    _require(
        inference_contract["randomization_observed_permutation_accounting"]
        == "plus_one_only",
        "observed permutation accounting contract mismatch",
    )
    _require(
        inference_contract["randomization_plus_one_correction"] is True,
        "Monte Carlo plus-one contract mismatch",
    )
    _require(
        inference_contract["exact_sign_flip_plus_one_correction"] is False,
        "exact sign-flip plus-one contract mismatch",
    )
    _require(ROOT_SEED == 20260728, "root seed contract mismatch")
    for name, fields in ROW_FIELDS.items():
        _require(fields and len(fields) == len(set(fields)), f"duplicate output field: {name}")


_validate_statistics_contract()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(material):
    return hashlib.sha256(material).hexdigest()


def compact_json(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def pretty_json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _finite(value, label="value"):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        _require(math.isfinite(value), f"{label} is non-finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{label}.{key}")
        return
    raise StatisticsError(f"{label} has unsupported type")


def _is_within(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def validate_analysis_root(value):
    path = Path(value)
    _require(path.is_absolute(), "analysis-root must be absolute")
    _require(not path.is_symlink(), "analysis-root must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise StatisticsError("analysis-root is unavailable") from exc
    _require(stat.S_ISDIR(metadata.st_mode), "analysis-root must be a directory")
    _require(not _is_within(resolved, REPO_ROOT), "analysis-root must be outside repository")
    return resolved


def validate_statistics_root(value, require_unused=True):
    raw = Path(value)
    _require(raw.is_absolute(), "statistics-root must be absolute")
    _require(not raw.is_symlink(), "statistics-root must not be a symlink")
    resolved = raw.resolve(strict=False)
    _require(resolved != Path("/"), "statistics-root cannot be filesystem root")
    _require(not _is_within(resolved, REPO_ROOT), "statistics-root must be outside repository")
    _require(resolved.parent.exists() and resolved.parent.is_dir(), "statistics-root parent unavailable")
    if raw.exists():
        _require(raw.is_dir(), "statistics-root must be a directory")
        if require_unused:
            _require(not any(raw.iterdir()), "statistics-root must be absent or empty")
    return resolved


def _canonical_repo_file(relative, expected):
    _require(isinstance(relative, str) and relative, "repository path is invalid")
    posix = PurePosixPath(relative)
    _require(
        not posix.is_absolute()
        and str(posix) == relative
        and all(part not in {"", ".", ".."} for part in posix.parts),
        "repository path is not canonical",
    )
    path = (REPO_ROOT / Path(*posix.parts)).resolve(strict=True)
    _require(path == expected.resolve(strict=True), "repository identity path mismatch")
    return path


def _csv_cell(value):
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        _require(math.isfinite(value), "CSV float is non-finite")
        return format(value, ".17g")
    if isinstance(value, (dict, list)):
        return compact_json(value)
    _require(isinstance(value, str), "unsupported CSV cell")
    return value


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields)
    for row in rows:
        _require(tuple(row) == tuple(fields), "CSV field order mismatch")
        writer.writerow([_csv_cell(row[name]) for name in fields])
    return stream.getvalue().encode("utf-8")


def jsonl_bytes(rows, fields):
    lines = []
    for row in rows:
        _require(tuple(row) == tuple(fields), "JSONL field order mismatch")
        _finite(row)
        lines.append(compact_json(row))
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _read_jsonl(path, fields):
    material = path.read_bytes()
    try:
        text = material.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StatisticsError(f"{path.name} is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    _require(lines, f"{path.name} is empty")
    rows = []
    for line_number, line in enumerate(lines, 1):
        _require(line.endswith("\n") and line != "\n", f"invalid JSONL line {line_number}")
        try:
            row = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    StatisticsError(f"non-finite JSON number: {value}")
                ),
            )
        except json.JSONDecodeError as exc:
            raise StatisticsError(f"invalid JSONL line {line_number}") from exc
        _require(isinstance(row, dict), "JSONL row must be an object")
        _require(tuple(row) == tuple(fields), f"{path.name} has unknown or reordered fields")
        _finite(row, path.name)
        rows.append(row)
    return rows, material


def _verify_csv(path, rows, fields):
    try:
        parsed = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise StatisticsError(f"invalid CSV: {path.name}") from exc
    expected = [list(fields)] + [[_csv_cell(row[name]) for name in fields] for row in rows]
    _require(parsed == expected, f"JSONL/CSV parity failed: {path.name}")


def _git_identity():
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
            text=True, capture_output=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPO_ROOT, check=True, text=True, capture_output=True,
        ).stdout)
    except subprocess.CalledProcessError as exc:
        raise StatisticsError("cannot determine statistics source identity") from exc
    return head, dirty


def implementation_sha256():
    digest = hashlib.sha256()
    for path in IMPLEMENTATION_FILES:
        _require(path.is_file(), f"missing implementation file: {path.name}")
        relative = path.relative_to(REPO_ROOT).as_posix().encode("utf-8")
        material = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(material).to_bytes(8, "big"))
        digest.update(material)
    return digest.hexdigest()


def analysis_name(dataset):
    identity = {
        "analysis_schema_version": 2,
        "case_primary_keys": sorted(row["case_id"] for row in dataset["cases"]),
        "task_primary_keys": sorted(
            [row["case_id"], row["priority_rank"]] for row in dataset["tasks"]
        ),
    }
    return bytes_sha256(compact_json(identity).encode("utf-8"))


def _number_identity(value):
    _require(not isinstance(value, bool) and isinstance(value, (str, int, float)), "grid value invalid")
    try:
        number = float(value)
    except ValueError as exc:
        raise StatisticsError("grid value invalid") from exc
    _require(math.isfinite(number), "grid value non-finite")
    return format(number, ".15g")


def _utilization(row):
    value = row["utilization"]
    if value is None:
        value = row["target_normalized_utilization"]
    return _number_identity(value)


def cluster_dimensions(row):
    rho = (
        list(CONTRACT["cluster_contract"]["pilot_rho_normalization"])
        if row["phase"] == "pilot"
        else _number_identity(row["rho_E"])
    )
    values = {
        "phase": row["phase"],
        "rho_E": rho,
        "utilization": _utilization(row),
        "taskset_id": row["taskset_id"],
        "taskset_semantic_hash": row["taskset_semantic_hash"],
        "taskset_sha256": row["taskset_sha256"],
        "taskset_seed": row["taskset_seed"],
        "replicate_index": row["replicate_index"],
        "taskset_pool": row["taskset_pool"],
    }
    _require(tuple(values) == CLUSTER_FIELDS, "cluster dimension order mismatch")
    return values


def cluster_key(row):
    return bytes_sha256(compact_json(cluster_dimensions(row)).encode("utf-8"))


def _validate_candidate(manifest):
    candidate_path = _canonical_repo_file(manifest.get("candidate_v3_path"), CANDIDATE_PATH)
    candidate_sha = file_sha256(candidate_path)
    _require(manifest.get("candidate_v3_sha256") == candidate_sha, "candidate SHA mismatch")
    return load_json(candidate_path), candidate_sha


def _validate_grid_structure(
    cases,
    clusters,
    expected_u,
    expected_lambda,
    expected_rho,
    expected_algorithms,
    clusters_per_utilization,
    cases_per_cluster,
):
    _require({_utilization(row) for row in cases} == set(expected_u), "utilization grid mismatch")
    _require({_number_identity(row["lambda_E"]) for row in cases} == set(expected_lambda), "lambda grid mismatch")
    _require({_number_identity(row["rho_E"]) for row in cases} == set(expected_rho), "rho grid mismatch")
    _require({row["algorithm"] for row in cases} == set(expected_algorithms), "algorithm grid mismatch")
    per_u = Counter(_utilization(rows[0]) for rows in clusters.values())
    _require(
        set(per_u) == set(expected_u)
        and set(per_u.values()) == {clusters_per_utilization},
        "clusters per utilization mismatch",
    )
    expected_combinations = {
        (lam, rho, algorithm)
        for lam in expected_lambda
        for rho in expected_rho
        for algorithm in expected_algorithms
    }
    for rows in clusters.values():
        combinations = {
            (
                _number_identity(row["lambda_E"]),
                _number_identity(row["rho_E"]),
                row["algorithm"],
            )
            for row in rows
        }
        _require(
            len(rows) == cases_per_cluster
            and len(combinations) == len(rows)
            and combinations == expected_combinations,
            "cluster/cell grid is not closed",
        )


def load_analysis(analysis_root):
    root = validate_analysis_root(analysis_root)
    names = (
        "cases.jsonl", "tasks.jsonl", "cases.csv", "tasks.csv",
        "analysis_audit.json", "analysis_manifest.json",
    )
    for name in names:
        path = root / name
        _require(path.is_file() and not path.is_symlink(), f"missing authoritative analysis file: {name}")
    manifest = load_json(root / "analysis_manifest.json")
    audit = load_json(root / "analysis_audit.json")
    _require(manifest.get("analysis_schema_version") == 2, "analysis schema version mismatch")
    _require(audit.get("analysis_schema_version") == 2, "analysis audit schema mismatch")
    _require(audit.get("overall_pass") is True, "analysis audit overall_pass is false")
    _require(manifest.get("analysis_contract_sha256") == CONTRACT["required_analysis_contract_sha256"], "analysis contract identity mismatch")
    _require(manifest.get("observability_contract_sha256") == CONTRACT["required_observability_contract_sha256"], "observability contract identity mismatch")
    _canonical_repo_file(manifest.get("analysis_contract_path"), ANALYSIS_CONTRACT_PATH)
    _canonical_repo_file(manifest.get("observability_contract_path"), OBSERVABILITY_CONTRACT_PATH)
    _require(file_sha256(ANALYSIS_CONTRACT_PATH) == CONTRACT["required_analysis_contract_sha256"], "local analysis contract drift")
    _require(file_sha256(OBSERVABILITY_CONTRACT_PATH) == CONTRACT["required_observability_contract_sha256"], "local observability contract drift")
    candidate, candidate_sha = _validate_candidate(manifest)
    _require(isinstance(manifest.get("source_code_commit"), str) and manifest["source_code_commit"], "I5C source commit is empty")
    _require(isinstance(manifest.get("extractor_version_sha256"), str) and manifest["extractor_version_sha256"], "I5C extractor identity is empty")
    output_hashes = manifest.get("output_file_sha256")
    _require(isinstance(output_hashes, dict), "analysis output hashes missing")
    for name in ("cases.jsonl", "tasks.jsonl", "cases.csv", "tasks.csv", "analysis_audit.json"):
        _require(output_hashes.get(name) == file_sha256(root / name), f"analysis file SHA mismatch: {name}")
    cases, case_material = _read_jsonl(root / "cases.jsonl", CASE_FIELDS)
    tasks, task_material = _read_jsonl(root / "tasks.jsonl", TASK_FIELDS)
    preliminary_case_ids = [row["case_id"] for row in cases]
    preliminary_task_keys = [
        (row["case_id"], row["priority_rank"]) for row in tasks
    ]
    _require(
        len(preliminary_case_ids) == len(set(preliminary_case_ids)),
        "duplicate case primary key",
    )
    _require(
        len(preliminary_task_keys) == len(set(preliminary_task_keys)),
        "duplicate task primary key",
    )
    _verify_csv(root / "cases.csv", cases, CASE_FIELDS)
    _verify_csv(root / "tasks.csv", tasks, TASK_FIELDS)
    _require(manifest.get("case_row_count") == len(cases), "analysis case count mismatch")
    _require(manifest.get("task_row_count") == len(tasks), "analysis task count mismatch")
    _require(audit.get("case_row_count") == len(cases), "analysis audit case count mismatch")
    _require(audit.get("task_row_count") == len(tasks), "analysis audit task count mismatch")
    case_ids = [row["case_id"] for row in cases]
    task_keys = [(row["case_id"], row["priority_rank"]) for row in tasks]
    _require(len(case_ids) == len(set(case_ids)), "duplicate case primary key")
    _require(len(task_keys) == len(set(task_keys)), "duplicate task primary key")
    _require(set(case_ids) == {key[0] for key in task_keys}, "case/task set mismatch")
    by_case = defaultdict(list)
    for row in tasks:
        by_case[row["case_id"]].append(row)
    for case in cases:
        rows = sorted(by_case[case["case_id"]], key=lambda row: row["priority_rank"])
        _require(len(rows) == 10, "each case must have ten tasks")
        _require([row["priority_rank"] for row in rows] == list(range(10)), "task rank coverage mismatch")
        identity_fields = tuple(ANALYSIS_CONTRACT["case_identity_field_order"])
        _require(
            all(all(row[name] == case[name] for name in identity_fields) for row in rows),
            "case/task identity mismatch",
        )
        expected_task_pass = [
            row["adjudicable_jobs"] >= 100 and row["deadline_miss_jobs"] == 0
            for row in rows
        ]
        _require(
            [row["task_pass"] for row in rows] == expected_task_pass,
            "task pass recomputation mismatch",
        )
        _require(
            case["whole_pass"] == all(expected_task_pass)
            and case["hp_pass"] == all(expected_task_pass[:4])
            and case["lp_pass"] == all(expected_task_pass[4:]),
            "case pass recomputation mismatch",
        )
        for prefix, selected in (("all", rows), ("hp", rows[:4]), ("lp", rows[4:])):
            for name in ANALYSIS_CONTRACT["task_metric_field_order"]:
                values = [row[name] for row in selected]
                expected = max(values) if name == "completed_response_time_max_ms" else sum(values)
                _require(case[f"{prefix}_{name}"] == expected, "case/task aggregate mismatch")
    pairing = defaultdict(list)
    for row in cases:
        pairing[row["pairing_key"]].append(row)
    for rows in pairing.values():
        phase = rows[0]["phase"]
        expected = PILOT_ALGORITHMS if phase == "pilot" else ALGORITHMS
        _require(Counter(row["algorithm"] for row in rows) == Counter(expected), "pairing algorithm coverage mismatch")
        canonical = compact_json(rows[0]["pairing_dimensions"])
        _require(
            rows[0]["pairing_key"] == bytes_sha256(canonical.encode("utf-8")),
            "pairing key recomputation mismatch",
        )
        _require(all(compact_json(row["pairing_dimensions"]) == canonical for row in rows), "pairing dimensions mismatch")
    _require(manifest.get("pairing_group_count") == len(pairing), "analysis pairing count mismatch")
    _require(audit.get("pairing_group_count") == len(pairing), "analysis audit pairing count mismatch")
    return {
        "root": root,
        "manifest": manifest,
        "audit": audit,
        "candidate": candidate,
        "candidate_sha256": candidate_sha,
        "cases": cases,
        "tasks": tasks,
        "pairing_group_count": len(pairing),
        "manifest_sha256": file_sha256(root / "analysis_manifest.json"),
        "audit_sha256": file_sha256(root / "analysis_audit.json"),
        "cases_sha256": bytes_sha256(case_material),
        "tasks_sha256": bytes_sha256(task_material),
    }


def _mode_authorization(mode, dataset, dirty):
    cases = dataset["cases"]
    contract = CONTRACT["mode_contracts"][mode]
    phases = {row["phase"] for row in cases}
    clusters = defaultdict(list)
    for row in cases:
        clusters[cluster_key(row)].append(row)
    if mode == "validation":
        return clusters, {"grid_complete": False, "authorization": "validation_only"}
    _require(not dirty, f"{mode} requires a clean statistics worktree")
    governance = dataset["candidate"].get("governance", {})
    _require(
        governance.get("i5d_statistics_authorized") is True,
        "candidate i5d_statistics_authorized is false",
    )
    if mode == "pilot":
        _require(governance.get("pilot_runs_authorized") is True, "candidate does not authorize Pilot")
    elif mode == "negative-control":
        _require(governance.get("negative_control_runs_authorized") is True, "candidate does not authorize Negative Control")
    else:
        candidate = dataset["candidate"]
        _require(governance.get("formal_runs_authorized") is True, "candidate formal_runs_authorized is false")
        for field in ("final_code_commit", "final_git_tag", "formal_runtime_binary_path", "formal_runtime_binary_sha256"):
            _require(isinstance(candidate.get(field), str) and candidate[field], f"candidate {field} is empty")
        _require(candidate.get("freeze_status") in {"final", "final_executable", "frozen_final"}, "candidate freeze status is not final executable")
    _require(phases == {contract["phase"]}, f"{mode} phase mismatch")
    _require(len(cases) == contract["case_count"], f"{mode} case count mismatch")
    _require(len(dataset["tasks"]) == contract["task_count"], f"{mode} task count mismatch")
    _require(dataset["pairing_group_count"] == contract["pairing_group_count"], f"{mode} pairing count mismatch")
    _require(len(clusters) == contract["cluster_count"], f"{mode} cluster count mismatch")
    _require(all(row["not_for_paper"] is False for row in cases), f"{mode} forbids not_for_paper rows")
    expected_u = {_number_identity(value) for value in contract["utilizations"]}
    expected_lambda = {_number_identity(value) for value in contract["lambdas"]}
    expected_rho = {_number_identity(value) for value in contract["rhos"]}
    expected_algorithms = PILOT_ALGORITHMS if mode == "pilot" else ALGORITHMS
    _validate_grid_structure(
        cases,
        clusters,
        expected_u,
        expected_lambda,
        expected_rho,
        expected_algorithms,
        contract["clusters_per_utilization"],
        contract["cases_per_cluster"],
    )
    return clusters, {"grid_complete": True, "authorization": "authorized"}


CASE_RATIO_FIELDS = CONTRACT["ratio_contract"]["case_metrics"]


def _annotate(dataset):
    cases = []
    by_case = {}
    for original in dataset["cases"]:
        row = dict(original)
        row["_cluster_key"] = cluster_key(row)
        row["_utilization"] = _utilization(row)
        row["_lambda"] = _number_identity(row["lambda_E"])
        row["_rho"] = _number_identity(row["rho_E"])
        for metric, (numerator, denominator) in CASE_RATIO_FIELDS.items():
            row[f"_{metric}"] = inference.safe_ratio(row[numerator], row[denominator])
        cases.append(row)
        by_case[row["case_id"]] = row
    tasks = []
    for original in dataset["tasks"]:
        row = dict(original)
        case = by_case[row["case_id"]]
        row["_cluster_key"] = case["_cluster_key"]
        row["_utilization"] = case["_utilization"]
        row["_lambda"] = case["_lambda"]
        row["_rho"] = case["_rho"]
        row["_rank_jmr"] = inference.safe_ratio(row["deadline_miss_jobs"], row["adjudicable_jobs"])
        tasks.append(row)
    return cases, tasks


def _cluster_values(cases, algorithm, value_name):
    grouped = defaultdict(list)
    for row in cases:
        if row["algorithm"] == algorithm:
            value = row[value_name] if value_name in row else row[f"_{value_name}"]
            grouped[(row["_utilization"], row["_cluster_key"])].append(value)
    by_u = defaultdict(list)
    for (utilization, key), values in sorted(grouped.items()):
        by_u[utilization].append((key, inference.finite_mean(values)))
    return {u: values for u, values in sorted(by_u.items())}


def _bootstrap(cluster_rows, analysis_name, identity):
    values = {u: [value for _, value in rows] for u, rows in cluster_rows.items()}
    seed = inference.derive_seed_identity(
        file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name, identity
    )
    return inference.percentile_stratified_bootstrap(
        values, BOOTSTRAP_REPLICATES, seed,
        CONTRACT["inference"]["confidence_level"],
    )


def build_algorithm_summary(cases, analysis_name, algorithms=ALGORITHMS):
    rows = []
    for algorithm in algorithms:
        hp_values = _cluster_values(cases, algorithm, "hp_pass")
        whole_values = _cluster_values(cases, algorithm, "whole_pass")
        hp = _bootstrap(hp_values, analysis_name, f"algorithm:{algorithm}:hp_pass")
        whole = _bootstrap(whole_values, analysis_name, f"algorithm:{algorithm}:whole_pass")
        metrics = {}
        for metric in CASE_RATIO_FIELDS:
            values = _cluster_values(cases, algorithm, metric)
            metrics[metric] = inference.equal_strata_mean(
                {u: [value for _, value in items] for u, items in values.items()}
            )
        total = sum(len(items) for items in hp_values.values())
        defined = sum(value is not None for items in hp_values.values() for _, value in items)
        values = {
            "algorithm": algorithm,
            "hp_pass": hp["point_estimate"], "hp_ci_lower": hp["ci_lower"], "hp_ci_upper": hp["ci_upper"],
            "whole_pass": whole["point_estimate"], "whole_ci_lower": whole["ci_lower"], "whole_ci_upper": whole["ci_upper"],
            "overall_jmr": metrics["overall_jmr"], "top4_jmr": metrics["top4_jmr"], "bottom6_jmr": metrics["bottom6_jmr"],
            "completion_ratio": metrics["completion_ratio"], "unfinished_ratio": metrics["unfinished_ratio"],
            "defined_cluster_count": defined, "total_cluster_count": total,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "hp_seed_identity": hp["seed_identity"], "whole_seed_identity": whole["seed_identity"],
        }
        rows.append({name: values[name] for name in ROW_FIELDS["algorithm_summary"]})
    return rows


def _case_index(cases):
    index = {}
    for row in cases:
        key = (row["_cluster_key"], row["_lambda"], row["_rho"], row["algorithm"])
        _require(key not in index, "duplicate cluster/cell/algorithm")
        index[key] = row
    return index


def _effect_cluster_values(cases, comparator, flag):
    index = _case_index(cases)
    grouped = defaultdict(list)
    for key, block in index.items():
        cluster, lam, rho, algorithm = key
        if algorithm != "ASAP-BLOCK":
            continue
        other = index.get((cluster, lam, rho, comparator))
        _require(other is not None, f"missing comparator pair: {comparator}")
        grouped[(block["_utilization"], cluster)].append(
            float(block[flag]) - float(other[flag])
        )
    by_u = defaultdict(list)
    for (utilization, cluster), differences in sorted(grouped.items()):
        by_u[utilization].append((cluster, inference.finite_mean(differences)))
    return {u: rows for u, rows in sorted(by_u.items())}


def build_confirmatory_effects(cases, analysis_name):
    partial = []
    raw_p = {}
    index = _case_index(cases)
    for comparator in COMPARATORS:
        hp_values = _effect_cluster_values(cases, comparator, "hp_pass")
        whole_values = _effect_cluster_values(cases, comparator, "whole_pass")
        hp = _bootstrap(hp_values, analysis_name, f"comparison:{comparator}:hp_pass")
        whole = _bootstrap(whole_values, analysis_name, f"comparison:{comparator}:whole_pass")
        random_seed = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"comparison:{comparator}:hp_sign_flip",
        )
        randomization = inference.paired_sign_flip(
            {u: [value for _, value in rows] for u, rows in hp_values.items()},
            RANDOMIZATION_RANDOM_DRAWS, random_seed,
            CONTRACT["inference"]["exact_sign_flip_max_nonzero"],
        )
        raw_p[comparator] = randomization["raw_p"]
        counts = Counter()
        for key, block in index.items():
            cluster, lam, rho, algorithm = key
            if algorithm != "ASAP-BLOCK":
                continue
            other = index[(cluster, lam, rho, comparator)]
            pair = (bool(block["whole_pass"]), bool(other["whole_pass"]))
            counts[pair] += 1
        partial.append((comparator, hp, whole, randomization, counts))
    holm = inference.holm_step_down(raw_p, list(COMPARATORS))
    rows = []
    for comparator, hp, whole, randomization, counts in partial:
        values = {
            "comparator": comparator,
            "hp_point_estimate": hp["point_estimate"], "hp_ci_lower": hp["ci_lower"], "hp_ci_upper": hp["ci_upper"],
            "hp_bootstrap_replicates": hp["bootstrap_replicates"], "hp_seed_identity": hp["seed_identity"],
            "raw_p": randomization["raw_p"], "randomization_method": randomization["method"],
            "randomization_seed_identity": randomization["seed_identity"], "nonzero_cluster_count": randomization["nonzero_cluster_count"],
            "observed_statistic": randomization["observed_statistic"],
            "random_draws": randomization.get("random_draws"),
            "observed_permutation_forced_in_draws": randomization.get("observed_permutation_forced_in_draws"),
            "observed_permutation_accounting": randomization.get("observed_permutation_accounting"),
            "random_extreme_count": randomization.get("random_extreme_count"),
            "enumerated_permutations": randomization.get("enumerated_permutations"),
            "extreme_count": randomization.get("extreme_count"),
            "plus_one_correction": randomization["plus_one_correction"],
            "p_value_numerator": randomization["p_value_numerator"],
            "p_value_denominator": randomization["p_value_denominator"],
            "holm_adjusted_p": holm[comparator]["holm_adjusted_p"], "holm_rank": holm[comparator]["holm_rank"],
            "reject_at_0_05": holm[comparator]["reject_at_0_05"],
            "whole_point_estimate": whole["point_estimate"], "whole_ci_lower": whole["ci_lower"], "whole_ci_upper": whole["ci_upper"],
            "whole_bootstrap_replicates": whole["bootstrap_replicates"], "whole_seed_identity": whole["seed_identity"],
            "asap_block_only": counts[(True, False)], "comparator_only": counts[(False, True)],
            "both_pass": counts[(True, True)], "neither_pass": counts[(False, False)], "case_pair_count": sum(counts.values()),
        }
        rows.append({name: values[name] for name in ROW_FIELDS["confirmatory_effects"]})
    return rows


def _cell_sort_key(key):
    utilization, lam, rho = key
    return (float(utilization), float(lam), float(rho))


def build_cell_summary(cases, analysis_name, algorithms):
    cells = defaultdict(list)
    for row in cases:
        cells[(row["_utilization"], row["_lambda"], row["_rho"])].append(row)
    rows = []
    for cell in sorted(cells, key=_cell_sort_key):
        utilization, lam, rho = cell
        cell_rows = cells[cell]
        by_algorithm = defaultdict(list)
        for row in cell_rows:
            by_algorithm[row["algorithm"]].append(row)
        for algorithm in algorithms:
            selected = by_algorithm[algorithm]
            _require(selected, f"missing algorithm cell: {algorithm}")
            values = {
                "record_type": "algorithm", "utilization": utilization, "lambda_E": lam, "rho_E": rho,
                "algorithm": algorithm, "comparator": None,
                "hp_pass": inference.finite_mean([row["hp_pass"] for row in selected]),
                "whole_pass": inference.finite_mean([row["whole_pass"] for row in selected]),
                "overall_jmr": inference.finite_mean([row["_overall_jmr"] for row in selected]),
                "top4_jmr": inference.finite_mean([row["_top4_jmr"] for row in selected]),
                "bottom6_jmr": inference.finite_mean([row["_bottom6_jmr"] for row in selected]),
                "completion_ratio": inference.finite_mean([row["_completion_ratio"] for row in selected]),
                "unfinished_ratio": inference.finite_mean([row["_unfinished_ratio"] for row in selected]),
                "hp_risk_difference": None, "hp_ci_lower": None, "hp_ci_upper": None,
                "whole_risk_difference": None, "whole_ci_lower": None, "whole_ci_upper": None,
                "asap_block_only": None, "comparator_only": None,
                "hp_defined_taskset_count": len(selected),
                "whole_defined_taskset_count": len(selected),
                "overall_jmr_defined_taskset_count": sum(row["_overall_jmr"] is not None for row in selected),
                "top4_jmr_defined_taskset_count": sum(row["_top4_jmr"] is not None for row in selected),
                "bottom6_jmr_defined_taskset_count": sum(row["_bottom6_jmr"] is not None for row in selected),
                "completion_ratio_defined_taskset_count": sum(row["_completion_ratio"] is not None for row in selected),
                "unfinished_ratio_defined_taskset_count": sum(row["_unfinished_ratio"] is not None for row in selected),
                "total_taskset_count": len(selected),
            }
            rows.append({name: values[name] for name in ROW_FIELDS["cell_summary"]})
        if set(COMPARATORS) <= set(by_algorithm):
            block = {row["_cluster_key"]: row for row in by_algorithm["ASAP-BLOCK"]}
            for comparator in COMPARATORS:
                other = {row["_cluster_key"]: row for row in by_algorithm[comparator]}
                _require(set(block) == set(other), "cell pairing is not closed")
                hp_differences = [float(block[key]["hp_pass"]) - float(other[key]["hp_pass"]) for key in sorted(block)]
                whole_differences = [float(block[key]["whole_pass"]) - float(other[key]["whole_pass"]) for key in sorted(block)]
                hp_seed = inference.derive_seed_identity(file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name, f"cell:{utilization}:{lam}:{rho}:{comparator}:hp")
                whole_seed = inference.derive_seed_identity(file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name, f"cell:{utilization}:{lam}:{rho}:{comparator}:whole")
                hp = inference.percentile_stratified_bootstrap({utilization: hp_differences}, BOOTSTRAP_REPLICATES, hp_seed)
                whole = inference.percentile_stratified_bootstrap({utilization: whole_differences}, BOOTSTRAP_REPLICATES, whole_seed)
                pairs = Counter((bool(block[key]["whole_pass"]), bool(other[key]["whole_pass"])) for key in block)
                values = {
                    "record_type": "comparison", "utilization": utilization, "lambda_E": lam, "rho_E": rho,
                    "algorithm": "ASAP-BLOCK", "comparator": comparator,
                    "hp_pass": None, "whole_pass": None, "overall_jmr": None, "top4_jmr": None,
                    "bottom6_jmr": None, "completion_ratio": None, "unfinished_ratio": None,
                    "hp_risk_difference": hp["point_estimate"], "hp_ci_lower": hp["ci_lower"], "hp_ci_upper": hp["ci_upper"],
                    "whole_risk_difference": whole["point_estimate"], "whole_ci_lower": whole["ci_lower"], "whole_ci_upper": whole["ci_upper"],
                    "asap_block_only": pairs[(True, False)], "comparator_only": pairs[(False, True)],
                    "hp_defined_taskset_count": len(block),
                    "whole_defined_taskset_count": len(block),
                    "overall_jmr_defined_taskset_count": 0,
                    "top4_jmr_defined_taskset_count": 0,
                    "bottom6_jmr_defined_taskset_count": 0,
                    "completion_ratio_defined_taskset_count": 0,
                    "unfinished_ratio_defined_taskset_count": 0,
                    "total_taskset_count": len(block),
                }
                rows.append({name: values[name] for name in ROW_FIELDS["cell_summary"]})
    return rows


def build_rank_jmr(tasks, algorithms):
    rows = []
    for algorithm in algorithms:
        for rank in range(10):
            grouped = defaultdict(list)
            for row in tasks:
                if row["algorithm"] == algorithm and row["priority_rank"] == rank:
                    grouped[(row["_utilization"], row["_cluster_key"])].append(row["_rank_jmr"])
            by_u = defaultdict(list)
            for (utilization, cluster), values in sorted(grouped.items()):
                by_u[utilization].append(inference.finite_mean(values))
            point = inference.equal_strata_mean(by_u)
            total = sum(len(values) for values in by_u.values())
            defined = sum(value is not None for values in by_u.values() for value in values)
            values = {
                "algorithm": algorithm, "priority_rank": rank, "paper_rank": rank + 1,
                "is_top4": rank < 4, "point_estimate": point,
                "defined_cluster_count": defined, "total_cluster_count": total,
            }
            rows.append({name: values[name] for name in ROW_FIELDS["rank_jmr"]})
    return rows


def build_mechanism_summary(cases, algorithms):
    grouped = defaultdict(list)
    for row in cases:
        grouped[(row["_utilization"], row["_lambda"], row["_rho"], row["algorithm"])].append(row)
    rows = []
    for mechanism, definition in CONTRACT["mechanisms"].items():
        numerator = definition["numerator"]
        denominator = definition["denominator"]
        for algorithm in algorithms:
            for (utilization, lam, rho, cell_algorithm), selected in sorted(grouped.items(), key=lambda item: (_cell_sort_key(item[0][:3]), ALGORITHMS.index(item[0][3]) if item[0][3] in ALGORITHMS else 99)):
                if cell_algorithm != algorithm:
                    continue
                ratios = [inference.safe_ratio(row[numerator], row[denominator]) for row in selected]
                defined = sorted(value for value in ratios if value is not None)
                total_numerator = math.fsum(float(row[numerator]) for row in selected)
                total_denominator = math.fsum(float(row[denominator]) for row in selected)
                median = None
                if defined:
                    middle = len(defined) // 2
                    median = defined[middle] if len(defined) % 2 else (defined[middle - 1] + defined[middle]) / 2.0
                values = {
                    "mechanism": mechanism, "algorithm": algorithm, "utilization": utilization,
                    "lambda_E": lam, "rho_E": rho, "numerator_field": numerator,
                    "denominator_field": denominator, "macro_mean": inference.finite_mean(ratios),
                    "median": median,
                    "positive_numerator_taskset_count": sum(
                        row[numerator] > 0 for row in selected
                    ),
                    "positive_denominator_taskset_count": sum(
                        row[denominator] > 0 for row in selected
                    ),
                    "defined_taskset_count": len(defined), "total_taskset_count": len(selected),
                    "defined_fraction": len(defined) / len(selected), "total_numerator": total_numerator,
                    "total_denominator": total_denominator,
                    "exposure_pooled_rate": inference.safe_ratio(total_numerator, total_denominator),
                }
                rows.append({name: values[name] for name in ROW_FIELDS["mechanism_summary"]})
    return rows


def build_pilot_gate(dataset, cell_rows, mechanism_rows):
    evidence = dataset["manifest"].get("technical_evidence")
    evidence_complete = isinstance(evidence, dict) and all(
        key in evidence for key in (
            "technical_error_count", "final_timeout_count", "cpu_only_pass",
            "unit_identity_audit_pass", "instrumentation_non_interference_pass",
            "evidence_sha256",
        )
    )
    evidence_complete = evidence_complete and (
        isinstance(evidence["evidence_sha256"], str)
        and len(evidence["evidence_sha256"]) == 64
        and all(ch in "0123456789abcdef" for ch in evidence["evidence_sha256"])
    )
    technical = evidence_complete and evidence["technical_error_count"] == 0
    timeout = evidence_complete and evidence["final_timeout_count"] == 0
    identity = evidence_complete and evidence["cpu_only_pass"] is True and evidence["unit_identity_audit_pass"] is True
    noninterference = evidence_complete and evidence["instrumentation_non_interference_pass"] is True
    hp_cells = defaultdict(list)
    for row in cell_rows:
        if row["record_type"] == "algorithm" and row["rho_E"] == "2" and row["lambda_E"] in {"0.85", "1"}:
            hp_cells[(row["utilization"], row["lambda_E"], row["rho_E"])].append(row["hp_pass"])
    hp_neutral = any(len(values) == 5 and 0.15 <= sorted(values)[2] <= 0.85 for values in hp_cells.values())
    mechanisms = defaultdict(dict)
    for row in mechanism_rows:
        key = (row["utilization"], row["lambda_E"], row["rho_E"])
        definition = CONTRACT["mechanisms"][row["mechanism"]]
        if row["algorithm"] == definition["paper_algorithm"]:
            mechanisms[key][row["mechanism"]] = row
    exposure = False
    clipping = False
    for key, values in mechanisms.items():
        if key[2] != "2" or key[1] not in {"0.85", "1"}:
            continue
        required = set(CONTRACT["mechanisms"])
        if required <= set(values):
            exposure |= (
                values["HPEnergyBlockedFraction"]["positive_numerator_taskset_count"]
                / values["HPEnergyBlockedFraction"]["total_taskset_count"] >= 0.2
                and values["BypassRate"]["positive_denominator_taskset_count"]
                / values["BypassRate"]["total_taskset_count"] >= 0.2
                and values["SyncRejectRate"]["positive_numerator_taskset_count"]
                / values["SyncRejectRate"]["total_taskset_count"] >= 0.2
                and values["ALAPDeferralRate"]["total_denominator"] > 0
                and values["ALAPDeferralRate"]["total_numerator"] > 0
                and values["STChargingWaitRate"]["total_denominator"] > 0
                and values["STChargingWaitRate"]["total_numerator"] > 0
            )
            clipping |= values["clipping_ratio"]["median"] is not None and values["clipping_ratio"]["median"] <= 0.10
    checks = {
        "technical_error_zero": technical,
        "final_timeout_zero": timeout,
        "pairing_complete": True,
        "cpu_unit_identity_audit_pass": identity,
        "neutral_hp_pass_cell_present": hp_neutral,
        "mechanism_exposure_cell_present": exposure,
        "clipping_cell_present": clipping,
        "instrumentation_non_interference_explicit": noninterference,
    }
    if not evidence_complete:
        status = "incomplete"
    else:
        status = "pass" if all(checks.values()) else "fail"
    return {
        "gate_name": "B4-PE-I5D-pilot-neutral-gate-v1",
        "status": status,
        "checks": checks,
        "technical_evidence_present": evidence_complete,
        "contains_ranking_or_significance": False,
    }


def _format_percent(value):
    decimals = CONTRACT["table_contract"]["percent_decimals"]
    return "" if value is None else f"{100.0 * value:.{decimals}f}"


def _format_ci(lower, upper):
    if lower is None or upper is None:
        return ""
    decimals = CONTRACT["table_contract"]["percent_decimals"]
    return f"[{100.0 * lower:.{decimals}f}, {100.0 * upper:.{decimals}f}]"


def _format_p(value):
    digits = CONTRACT["table_contract"]["p_value_significant_digits"]
    return "" if value is None else format(value, f".{digits}g")


def _tex_escape(value):
    replacements = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#"}
    result = str(value)
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def build_table1(rows):
    fields = (
        "comparator", "HP risk difference (pp)", "HP 95% CI", "raw p",
        "Holm-adjusted p", "reject at 0.05", "WholePass risk difference (pp)",
        "WholePass 95% CI", "ASAP-BLOCK-only", "comparator-only", "both", "neither",
    )
    rendered = []
    for row in rows:
        rendered.append(dict(zip(fields, (
            row["comparator"], _format_percent(row["hp_point_estimate"]), _format_ci(row["hp_ci_lower"], row["hp_ci_upper"]),
            _format_p(row["raw_p"]), _format_p(row["holm_adjusted_p"]), "yes" if row["reject_at_0_05"] else "no",
            _format_percent(row["whole_point_estimate"]), _format_ci(row["whole_ci_lower"], row["whole_ci_upper"]),
            row["asap_block_only"], row["comparator_only"], row["both_pass"], row["neither_pass"],
        ))))
    return csv_bytes(rendered, fields), tex_table_bytes(rendered, fields)


def build_table2(rows):
    fields = (
        "algorithm", "HPPass %", "HPPass 95% CI", "WholePass %", "WholePass 95% CI",
        "overall JMR %", "Top4 JMR %", "Bottom6 JMR %", "completion ratio %", "unfinished ratio %",
    )
    rendered = []
    for row in rows:
        rendered.append(dict(zip(fields, (
            row["algorithm"], _format_percent(row["hp_pass"]), _format_ci(row["hp_ci_lower"], row["hp_ci_upper"]),
            _format_percent(row["whole_pass"]), _format_ci(row["whole_ci_lower"], row["whole_ci_upper"]),
            _format_percent(row["overall_jmr"]), _format_percent(row["top4_jmr"]), _format_percent(row["bottom6_jmr"]),
            _format_percent(row["completion_ratio"]), _format_percent(row["unfinished_ratio"]),
        ))))
    return csv_bytes(rendered, fields), tex_table_bytes(rendered, fields)


def tex_table_bytes(rows, fields):
    alignment = "l" + "r" * (len(fields) - 1)
    lines = [f"\\begin{{tabular}}{{{alignment}}}", " \\toprule", " & ".join(_tex_escape(name) for name in fields) + r" \\", " \\midrule"]
    for row in rows:
        cells = []
        for name in fields:
            value = row[name]
            cells.append("--" if value in (None, "") else _tex_escape(value))
        lines.append(" & ".join(cells) + r" \\")
    lines.extend([" \\bottomrule", "\\end{tabular}"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def _output_modes(mode):
    if mode in {"validation", "formal-main"}:
        generated = {
            "algorithm_summary.jsonl", "algorithm_summary.csv", "confirmatory_effects.jsonl", "confirmatory_effects.csv",
            "cell_summary.jsonl", "cell_summary.csv", "rank_jmr.jsonl", "rank_jmr.csv", "mechanism_summary.jsonl", "mechanism_summary.csv",
            "table1_confirmatory_effects.csv", "table1_confirmatory_effects.tex", "table2_algorithm_summary.csv", "table2_algorithm_summary.tex",
            "figure1_algorithm_pass_rates.pdf", "figure1_algorithm_pass_rates.png", "figure2_block_vs_nonblock_sync.pdf", "figure2_block_vs_nonblock_sync.png",
            "figure3_asap_vs_alap_st.pdf", "figure3_asap_vs_alap_st.png", "figure4_confirmatory_effects.pdf", "figure4_confirmatory_effects.png",
            "figure5_tradeoff_mechanisms.pdf", "figure5_tradeoff_mechanisms.png", "statistics_audit.json", "statistics_manifest.json",
        }
    elif mode == "pilot":
        generated = {"cell_summary.jsonl", "cell_summary.csv", "mechanism_summary.jsonl", "mechanism_summary.csv", "pilot_gate.json", "pilot_gate.csv", "statistics_audit.json", "statistics_manifest.json"}
    else:
        generated = {"algorithm_summary.jsonl", "algorithm_summary.csv", "cell_summary.jsonl", "cell_summary.csv", "rank_jmr.jsonl", "rank_jmr.csv", "mechanism_summary.jsonl", "mechanism_summary.csv", "negative_control_summary.csv", "statistics_audit.json", "statistics_manifest.json"}
    return [name for name in OUTPUT_ORDER if name in generated]


def _verify_seed_identities(rows_by_name, analysis_name):
    for row in rows_by_name.get("algorithm_summary", []):
        algorithm = row["algorithm"]
        expected_hp = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"algorithm:{algorithm}:hp_pass",
        )
        expected_whole = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"algorithm:{algorithm}:whole_pass",
        )
        if row["hp_seed_identity"] != expected_hp or row["whole_seed_identity"] != expected_whole:
            return False
    for row in rows_by_name.get("confirmatory_effects", []):
        comparator = row["comparator"]
        expected_hp = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"comparison:{comparator}:hp_pass",
        )
        expected_whole = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"comparison:{comparator}:whole_pass",
        )
        expected_random = inference.derive_seed_identity(
            file_sha256(CONTRACT_PATH), ROOT_SEED, analysis_name,
            f"comparison:{comparator}:hp_sign_flip",
        )
        if (
            row["hp_seed_identity"] != expected_hp
            or row["whole_seed_identity"] != expected_whole
            or row["randomization_seed_identity"] != expected_random
        ):
            return False
    return True


def _verify_holm(rows):
    if not rows:
        return True
    recalculated = inference.holm_step_down(
        {row["comparator"]: row["raw_p"] for row in rows},
        list(COMPARATORS),
    )
    return all(
        row["holm_adjusted_p"] == recalculated[row["comparator"]]["holm_adjusted_p"]
        and row["holm_rank"] == recalculated[row["comparator"]]["holm_rank"]
        and row["reject_at_0_05"] == recalculated[row["comparator"]]["reject_at_0_05"]
        for row in rows
    )


def _verify_randomization_metadata(rows):
    for row in rows:
        numerator = row["p_value_numerator"]
        denominator = row["p_value_denominator"]
        if (
            not isinstance(numerator, int)
            or not isinstance(denominator, int)
            or denominator <= 0
            or row["raw_p"] != numerator / denominator
        ):
            return False
        if row["randomization_method"] == "monte_carlo_sign_flip":
            random_draws = row["random_draws"]
            random_extreme_count = row["random_extreme_count"]
            if (
                random_draws != RANDOMIZATION_RANDOM_DRAWS
                or row["observed_permutation_forced_in_draws"] is not False
                or row["observed_permutation_accounting"] != "plus_one_only"
                or row["plus_one_correction"] is not True
                or not isinstance(random_extreme_count, int)
                or not 0 <= random_extreme_count <= random_draws
                or numerator != random_extreme_count + 1
                or denominator != random_draws + 1
                or row["enumerated_permutations"] is not None
                or row["extreme_count"] is not None
            ):
                return False
        elif row["randomization_method"] == "exact_sign_flip":
            enumerated = row["enumerated_permutations"]
            extreme_count = row["extreme_count"]
            if (
                row["random_draws"] is not None
                or row["observed_permutation_forced_in_draws"] is not None
                or row["observed_permutation_accounting"] is not None
                or row["random_extreme_count"] is not None
                or row["plus_one_correction"] is not False
                or enumerated != 1 << row["nonzero_cluster_count"]
                or not isinstance(extreme_count, int)
                or not 0 <= extreme_count <= enumerated
                or numerator != extreme_count
                or denominator != enumerated
            ):
                return False
        else:
            return False
    return True


def _verify_safe_ratios(cases, mechanism_rows):
    for row in cases:
        for metric, (numerator, denominator) in CASE_RATIO_FIELDS.items():
            expected = inference.safe_ratio(row[numerator], row[denominator])
            if row[f"_{metric}"] != expected:
                return False
    for row in mechanism_rows:
        if row["total_denominator"] == 0:
            if row["exposure_pooled_rate"] is not None or row["macro_mean"] is not None:
                return False
        elif row["exposure_pooled_rate"] != inference.safe_ratio(
            row["total_numerator"], row["total_denominator"]
        ):
            return False
    return True


def _verify_algorithm_aggregation(cases, rows):
    for row in rows:
        algorithm = row["algorithm"]
        for output_name, source_name in (
            ("hp_pass", "hp_pass"),
            ("whole_pass", "whole_pass"),
            ("overall_jmr", "overall_jmr"),
            ("top4_jmr", "top4_jmr"),
            ("bottom6_jmr", "bottom6_jmr"),
            ("completion_ratio", "completion_ratio"),
            ("unfinished_ratio", "unfinished_ratio"),
        ):
            clustered = _cluster_values(cases, algorithm, source_name)
            expected = inference.equal_strata_mean(
                {u: [value for _, value in values] for u, values in clustered.items()}
            )
            if row[output_name] != expected:
                return False
    return True


def _verify_confirmatory_effects(cases, rows):
    if not rows:
        return True
    index = _case_index(cases)
    for row in rows:
        comparator = row["comparator"]
        for output_name, flag in (
            ("hp_point_estimate", "hp_pass"),
            ("whole_point_estimate", "whole_pass"),
        ):
            clustered = _effect_cluster_values(cases, comparator, flag)
            expected = inference.equal_strata_mean(
                {u: [value for _, value in values] for u, values in clustered.items()}
            )
            if row[output_name] != expected:
                return False
        counts = Counter()
        for key, block in index.items():
            cluster, lam, rho, algorithm = key
            if algorithm == "ASAP-BLOCK":
                other = index[(cluster, lam, rho, comparator)]
                counts[(bool(block["whole_pass"]), bool(other["whole_pass"]))] += 1
        if (
            row["asap_block_only"] != counts[(True, False)]
            or row["comparator_only"] != counts[(False, True)]
            or row["both_pass"] != counts[(True, True)]
            or row["neither_pass"] != counts[(False, False)]
            or row["case_pair_count"] != sum(counts.values())
        ):
            return False
    return True


def _verify_table_parity(outputs, rows_by_name):
    if "table1_confirmatory_effects.csv" not in outputs:
        return True
    table1_csv, table1_tex = build_table1(rows_by_name["confirmatory_effects"])
    table2_csv, table2_tex = build_table2(rows_by_name["algorithm_summary"])
    return (
        outputs["table1_confirmatory_effects.csv"] == table1_csv
        and outputs["table1_confirmatory_effects.tex"] == table1_tex
        and outputs["table2_algorithm_summary.csv"] == table2_csv
        and outputs["table2_algorithm_summary.tex"] == table2_tex
    )


def _verify_no_runtime_identity(outputs, analysis_root):
    forbidden = (
        str(REPO_ROOT).encode("utf-8"),
        str(analysis_root).encode("utf-8"),
        b"CreationDate",
        b"ModDate",
    )
    for name, material in outputs.items():
        if any(token in material for token in forbidden):
            return False
        if name.endswith((".json", ".jsonl", ".csv", ".tex")):
            lowered = material.lower()
            if b"nan" in lowered or b"infinity" in lowered:
                return False
    return True


def build_outputs(analysis_root, mode, strict):
    _require(strict is True, "--strict is required")
    _require(mode in CONTRACT["mode_contracts"], "unknown statistics mode")
    dataset = load_analysis(analysis_root)
    source_head, dirty = _git_identity()
    clusters, mode_diagnostics = _mode_authorization(mode, dataset, dirty)
    cases, tasks = _annotate(dataset)
    algorithms = PILOT_ALGORITHMS if mode == "pilot" else ALGORITHMS
    analysis_identity = analysis_name(dataset)
    outputs = {}
    rows_by_name = {}
    applicable = set(_output_modes(mode))
    if "algorithm_summary.jsonl" in applicable:
        rows_by_name["algorithm_summary"] = build_algorithm_summary(cases, analysis_identity, algorithms)
    if "confirmatory_effects.jsonl" in applicable:
        rows_by_name["confirmatory_effects"] = build_confirmatory_effects(cases, analysis_identity)
    rows_by_name["cell_summary"] = build_cell_summary(cases, analysis_identity, algorithms)
    rows_by_name["mechanism_summary"] = build_mechanism_summary(cases, algorithms)
    if "rank_jmr.jsonl" in applicable:
        rows_by_name["rank_jmr"] = build_rank_jmr(tasks, algorithms)
    for stem in ("algorithm_summary", "confirmatory_effects", "cell_summary", "rank_jmr", "mechanism_summary"):
        if f"{stem}.jsonl" in applicable:
            rows = rows_by_name[stem]
            outputs[f"{stem}.jsonl"] = jsonl_bytes(rows, ROW_FIELDS[stem])
            outputs[f"{stem}.csv"] = csv_bytes(rows, ROW_FIELDS[stem])
    if "table1_confirmatory_effects.csv" in applicable:
        outputs["table1_confirmatory_effects.csv"], outputs["table1_confirmatory_effects.tex"] = build_table1(rows_by_name["confirmatory_effects"])
        outputs["table2_algorithm_summary.csv"], outputs["table2_algorithm_summary.tex"] = build_table2(rows_by_name["algorithm_summary"])
    if "negative_control_summary.csv" in applicable:
        outputs["negative_control_summary.csv"], _ = build_table2(rows_by_name["algorithm_summary"])
    if "pilot_gate.json" in applicable:
        gate = build_pilot_gate(dataset, rows_by_name["cell_summary"], rows_by_name["mechanism_summary"])
        outputs["pilot_gate.json"] = pretty_json_bytes(gate)
        gate_rows = [{"check": name, "passed": value} for name, value in gate["checks"].items()]
        outputs["pilot_gate.csv"] = csv_bytes(gate_rows, ("check", "passed"))
    figure_bindings = {}
    figure_spec = {}
    if "figure1_algorithm_pass_rates.pdf" in applicable:
        import statistics_plotting as plotting
        source_hashes = {
            stem: bytes_sha256(outputs[f"{stem}.jsonl"])
            for stem in ("algorithm_summary", "confirmatory_effects", "cell_summary", "rank_jmr", "mechanism_summary")
        }
        plot_outputs, figure_bindings, figure_spec = plotting.build_figures(
            rows_by_name, source_hashes, validation=(mode == "validation")
        )
        outputs.update(plot_outputs)
    output_hashes = {name: bytes_sha256(outputs[name]) for name in OUTPUT_ORDER if name in outputs}
    confirmatory_rows = rows_by_name.get("confirmatory_effects", [])
    safe_ratio_pass = _verify_safe_ratios(cases, rows_by_name["mechanism_summary"])
    seed_pass = _verify_seed_identities(rows_by_name, analysis_identity)
    aggregation_pass = _verify_algorithm_aggregation(
        cases, rows_by_name.get("algorithm_summary", [])
    )
    confirmatory_pass = _verify_confirmatory_effects(cases, confirmatory_rows)
    figure_binding_pass = all(
        name in outputs and source_sha.encode("ascii") in outputs[name]
        for name, source_sha in figure_bindings.items()
    )
    checks = {
        "input_identity": True, "case_task_closure": True, "cluster_closure": True,
        "phase_mode_consistency": True, "grid_contract_satisfied": True,
        "algorithm_coverage": True, "lambda_coverage": True, "utilization_coverage": True,
        "primary_keys_unique": True, "safe_ratio": safe_ratio_pass,
        "na_preserved": safe_ratio_pass,
        "aggregation_recomputed": aggregation_pass,
        "confirmatory_effect_recomputed": confirmatory_pass,
        "bootstrap_seed_identity": seed_pass,
        "randomization_seed_identity": seed_pass,
        "randomization_accounting": _verify_randomization_metadata(
            confirmatory_rows
        ),
        "holm_recomputed": _verify_holm(confirmatory_rows),
        "table_data_parity": _verify_table_parity(outputs, rows_by_name),
        "figure_source_data_binding": figure_binding_pass,
        "output_sha256": all(
            bytes_sha256(outputs[name]) == expected
            for name, expected in output_hashes.items()
        ),
        "finite_numbers_only": True,
        "no_timestamps_or_absolute_paths": _verify_no_runtime_identity(outputs, dataset["root"]),
        "validation_watermark": mode != "validation" or figure_spec.get("validation_watermark", False),
        "formal_authorization": mode != "formal-main" or mode_diagnostics["authorization"] == "authorized",
    }
    audit = {
        "statistics_contract_version": 1,
        "mode": mode,
        "overall_pass": all(checks.values()),
        "checks": checks,
        "diagnostics": {
            "grid_complete": mode_diagnostics["grid_complete"],
            "case_count": len(cases), "task_count": len(tasks), "cluster_count": len(clusters),
            "pairing_group_count": dataset["pairing_group_count"],
            "cell_count": len({(row["_utilization"], row["_lambda"], row["_rho"]) for row in cases}),
        },
        "figure_source_data_sha256": figure_bindings,
        "figure_structure": figure_spec,
        "output_file_sha256": dict(output_hashes),
        "issues": [],
    }
    _require(audit["overall_pass"], "statistics audit failed")
    audit_bytes = pretty_json_bytes(audit)
    outputs["statistics_audit.json"] = audit_bytes
    output_hashes["statistics_audit.json"] = bytes_sha256(audit_bytes)
    generated_names = _output_modes(mode)
    omitted = [
        {"name": name, "reason": f"not applicable in {mode} mode"}
        for name in OUTPUT_ORDER if name not in generated_names
    ]
    manifest = {
        "statistics_contract_path": CONTRACT_PATH.relative_to(REPO_ROOT).as_posix(),
        "statistics_contract_sha256": file_sha256(CONTRACT_PATH),
        "analysis_contract_path": ANALYSIS_CONTRACT_PATH.relative_to(REPO_ROOT).as_posix(),
        "analysis_contract_sha256": file_sha256(ANALYSIS_CONTRACT_PATH),
        "observability_contract_path": OBSERVABILITY_CONTRACT_PATH.relative_to(REPO_ROOT).as_posix(),
        "observability_contract_sha256": file_sha256(OBSERVABILITY_CONTRACT_PATH),
        "candidate_path": CANDIDATE_PATH.relative_to(REPO_ROOT).as_posix(),
        "candidate_sha256": dataset["candidate_sha256"],
        "i5c_analysis_manifest_sha256": dataset["manifest_sha256"],
        "i5c_analysis_audit_sha256": dataset["audit_sha256"],
        "cases_input_sha256": dataset["cases_sha256"], "tasks_input_sha256": dataset["tasks_sha256"],
        "statistics_source_commit": None if dirty else source_head,
        "statistics_source_base_commit": source_head,
        "statistics_implementation_sha256": implementation_sha256(),
        "analysis_name": analysis_identity,
        "mode": mode, "root_seed": ROOT_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "randomization_random_draws": RANDOMIZATION_RANDOM_DRAWS,
        "counts": audit["diagnostics"],
        "algorithm_order": list(ALGORITHMS), "comparison_order": list(COMPARATORS),
        "generated_outputs": [{"name": name, "sha256": output_hashes[name]} for name in generated_names if name != "statistics_manifest.json"],
        "omitted_outputs": omitted,
        "paper_results_authorized": mode == "formal-main",
        "validation_watermark": mode == "validation",
    }
    outputs["statistics_manifest.json"] = pretty_json_bytes(manifest)
    _require(set(outputs) == set(generated_names), "generated output set mismatch")
    return outputs, manifest, audit


def publish_outputs(statistics_root, outputs):
    target = validate_statistics_root(statistics_root, require_unused=True)
    parent = target.parent
    staging = Path(tempfile.mkdtemp(prefix=".b4pe-i5d-stage-", dir=parent))
    try:
        for name in OUTPUT_ORDER:
            if name not in outputs:
                continue
            path = staging / name
            path.write_bytes(outputs[name])
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        directory_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def write_failure_audit(statistics_root, mode):
    try:
        target = validate_statistics_root(statistics_root, require_unused=True)
    except (StatisticsError, OSError):
        return
    target.mkdir(mode=0o755, parents=False, exist_ok=True)
    failure = {
        "statistics_contract_version": 1,
        "mode": mode if mode in CONTRACT["mode_contracts"] else "invalid",
        "overall_pass": False,
        "checks": {},
        "issues": [{"code": "input_or_governance_error"}],
    }
    (target / "statistics_audit.json").write_bytes(pretty_json_bytes(failure))
    manifest = target / "statistics_manifest.json"
    if manifest.exists():
        raise StatisticsError("failure path unexpectedly contains success manifest")
