"""Plan, execute and checkpoint the isolated B4 paired simulations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .performance_config import (
    ALL_SCHEDULERS, CONTRACT_VERSION, INITIAL_KAPPAS, PRIMARY_SCHEDULERS,
    assert_execution_seals, semantic_config_material,
)
from .performance_energy import (
    EnergyMaterial, build_energy_material, raw_solar_reference,
    runner_energy_config,
)
from .performance_environment import (
    StageEnvironmentError, assert_environment_compatible,
    build_stage_environment,
)
from .performance_identity import (
    assert_unique_request_ids, calibration_selection_identity, execution_identity,
    formal_plan_identity, horizon_selection_identity, semantic_config_hash,
    semantic_request_id, trace_sample_selected, REQUEST_CONTRACT_VERSION,
)
from .performance_outcome import evaluate_simulation_result
from .performance_taskset_store import PerformanceTaskset, PerformanceTasksetStore
from .result_writer import atomic_write_json
from .simulation_engine import run_paired_simulation
from .simulation_result import SimulationStatus


PROJECT_ROOT = Path(__file__).resolve().parents[2]
B4_RUNNER_RELEASE_E0 = Fraction(0)
class PerformanceExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PerformanceRequest:
    semantic_request_id: str
    execution_identity: str
    taskset_id: str
    taskset_semantic_hash: str
    priority_hash: str
    power_hash: str
    release_hash: str
    u_norm: str
    taskset_index: int
    energy_condition: str
    energy_identity: str
    energy_material: Mapping[str, Any]
    scheduler_id: str
    runtime_horizon_ms: int
    simulation_semantic_config_hash: str
    retain_trace: bool

    def row(self) -> Dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_source_commit(project_root: Path = PROJECT_ROOT) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project_root),
        capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        raise PerformanceExecutionError("cannot resolve exact source commit")
    return completed.stdout.strip()


def load_selection_seal(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceExecutionError(f"cannot load selection seal: {path}") from exc
    required = {"kappa_star", "eta_low", "eta_transition", "eta_high"}
    if not required.issubset(document):
        raise PerformanceExecutionError("CAL selection seal is incomplete")
    return document


def load_calibration_control(path: Path) -> Mapping[str, Any]:
    """Load either a provisional extension decision or a final CAL seal."""

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceExecutionError(f"cannot load CAL control document: {path}") from exc
    if not isinstance(document, dict):
        raise PerformanceExecutionError("CAL control document must be an object")
    return document


def load_horizon_seal(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceExecutionError(f"cannot load horizon seal: {path}") from exc
    if document.get("state") not in {"SELECT_30S", "SELECT_60S"}:
        raise PerformanceExecutionError("horizon selection seal is invalid")
    if int(document.get("selected_horizon_ms", 0)) not in {30000, 60000}:
        raise PerformanceExecutionError("horizon selection value is invalid")
    claimed = document.get("horizon_selection_identity")
    material = {key: value for key, value in document.items() if key != "horizon_selection_identity"}
    if not claimed or horizon_selection_identity(material) != claimed:
        raise PerformanceExecutionError("horizon selection identity mismatch")
    return document


def _conditions(config: Mapping[str, Any], phase: str = "default") -> Tuple[Tuple[str, str, str], ...]:
    stage = config["stage"]
    if stage == "CALIBRATION":
        control_path = config["execution"].get("calibration_seal") or config["energy"].get("selection_seal")
        if phase in {"extension_a", "extension_b", "confirmation_full_grid"}:
            if not control_path:
                raise PerformanceExecutionError(f"CAL phase {phase} requires a control document")
            control = load_calibration_control(Path(control_path))
            requested = tuple(str(value) for value in control.get("requested_extension_etas", ()))
            if phase == "extension_a":
                if control.get("extension_branch") != "A" or not control.get("kappa_star") or not requested:
                    raise PerformanceExecutionError("branch-A CAL extension control is incomplete")
                return tuple(
                    (f"k{control['kappa_star']}-e{eta}", str(control["kappa_star"]), eta)
                    for eta in requested
                )
            if phase == "extension_b":
                if control.get("extension_branch") != "B" or not requested:
                    raise PerformanceExecutionError("branch-B CAL extension control is incomplete")
                return tuple(
                    (f"k{kappa}-e{eta}", kappa, eta)
                    for kappa in INITIAL_KAPPAS for eta in requested
                )
            q_values = control.get("q_values")
            if not isinstance(q_values, list) or not q_values:
                raise PerformanceExecutionError("full-grid 30-second fallback requires final CAL Q cells")
            cells = sorted(
                {(str(row["kappa"]), str(row["eta"])) for row in q_values},
                key=lambda pair: (Fraction(pair[0]), Fraction(pair[1])),
            )
            return tuple((f"k{kappa}-e{eta}", kappa, eta) for kappa, eta in cells)
        if phase == "confirmation":
            if not control_path:
                raise PerformanceExecutionError("CAL confirmation requires a selection seal")
            selection = load_selection_seal(Path(control_path))
            return (
                ("low", str(selection["kappa_star"]), str(selection["eta_low"])),
                ("transition", str(selection["kappa_star"]), str(selection["eta_transition"])),
                ("high", str(selection["kappa_star"]), str(selection["eta_high"])),
            )
        return tuple(
            (f"k{kappa}-e{eta}", kappa, eta)
            for kappa in config["energy"]["kappa_values"]
            for eta in config["energy"]["eta_values"]
        )
    if stage == "SMOKE":
        return (("transition", config["energy"]["kappa_values"][0], config["energy"]["eta_values"][0]),)
    selection_path = config["execution"].get("calibration_seal") or config["energy"].get("selection_seal")
    selection = load_selection_seal(Path(selection_path))
    if stage == "HORIZON_GATE":
        return (("transition", str(selection["kappa_star"]), str(selection["eta_transition"])),)
    return (
        ("low", str(selection["kappa_star"]), str(selection["eta_low"])),
        ("transition", str(selection["kappa_star"]), str(selection["eta_transition"])),
        ("high", str(selection["kappa_star"]), str(selection["eta_high"])),
    )


def _horizons(config: Mapping[str, Any], phase: str = "default") -> Tuple[int, ...]:
    if config["stage"] == "CALIBRATION":
        return (30000,) if phase in {"confirmation", "confirmation_full_grid"} else (10000,)
    if config["stage"] == "FORMAL" and config["execution"].get("horizon_seal"):
        seal = load_horizon_seal(Path(config["execution"]["horizon_seal"]))
        return (int(seal["selected_horizon_ms"]),)
    return tuple(int(value) for value in config["simulation"]["horizons_ms"])


def _schedulers(config: Mapping[str, Any]) -> Tuple[str, ...]:
    return PRIMARY_SCHEDULERS if config["stage"] in {"CALIBRATION", "HORIZON_GATE"} else ALL_SCHEDULERS


def calibration_phase_plan_counts(config: Mapping[str, Any], phase: str) -> Dict[str, Any]:
    if config["stage"] != "CALIBRATION":
        raise PerformanceExecutionError("CAL phase counts require a CALIBRATION config")
    energy_cells = len(_conditions(config, phase))
    horizons = len(_horizons(config, phase))
    tasksets = len(config["grid"]["utilization_points"]) * int(config["grid"]["tasksets_per_utilization"])
    return {
        "stage": "CALIBRATION", "phase": phase,
        "unique_tasksets": tasksets, "energy_cells": energy_cells,
        "primary_schedulers": len(PRIMARY_SCHEDULERS), "horizons": horizons,
        "requests": tasksets * energy_cells * len(PRIMARY_SCHEDULERS) * horizons,
        "simulator_invoked": False,
    }


def build_requests(
    config: Mapping[str, Any], tasksets: Sequence[PerformanceTaskset], *,
    source_commit: str, simulator_binary_sha256: str, phase: str = "default",
) -> Tuple[PerformanceRequest, ...]:
    semantic_hash = semantic_config_hash(semantic_config_material(config))
    template = Path(config["generation"]["system_template"])
    if not template.is_absolute():
        template = PROJECT_ROOT / template
    solar = raw_solar_reference(template)
    requests = []
    for taskset in sorted(tasksets, key=lambda item: (Fraction(item.utilization), item.taskset_index)):
        for condition, kappa, eta in _conditions(config, phase):
            material = build_energy_material(
                task_payload=taskset.tasks,
                taskset_semantic_hash=taskset.taskset_semantic_hash,
                processors=int(config["platform"]["cores"]), kappa=kappa, eta=eta,
                solar_reference=solar, power_contract_hash=taskset.power_hash,
            )
            for horizon in _horizons(config, phase):
                for scheduler in _schedulers(config):
                    request_id = semantic_request_id(
                        contract_version=REQUEST_CONTRACT_VERSION,
                        taskset_semantic_hash=taskset.taskset_semantic_hash,
                        energy_identity_value=material.identity,
                        scheduler_id=scheduler, runtime_horizon_ms=horizon,
                        simulation_semantic_config_hash=semantic_hash,
                    )
                    requests.append(PerformanceRequest(
                        request_id,
                        execution_identity(request_id, source_commit, simulator_binary_sha256),
                        taskset.taskset_id, taskset.taskset_semantic_hash,
                        taskset.priority_hash, taskset.power_hash, taskset.release_hash,
                        taskset.utilization, taskset.taskset_index,
                        condition, material.identity, material.material(), scheduler,
                        horizon, semantic_hash, trace_sample_selected(request_id),
                    ))
    assert_unique_request_ids(request.row() for request in requests)
    return tuple(requests)


def tasksets_from_store(config: Mapping[str, Any], store: PerformanceTasksetStore) -> Tuple[PerformanceTaskset, ...]:
    manifest = store.verify_manifest()
    tasksets = []
    for entry in manifest["entries"]:
        tasksets.append(store.load(str(entry["utilization"]), int(entry["taskset_index"])))
    expected = {
        "CALIBRATION": 90, "HORIZON_GATE": 1600,
        "FORMAL": 1600, "SMOKE": 1,
    }[config["stage"]]
    if len(tasksets) != expected:
        raise PerformanceExecutionError(
            f"frozen store is incomplete for {config['stage']}: {len(tasksets)} != {expected}"
        )
    if config["stage"] == "HORIZON_GATE":
        groups: Dict[str, list] = {}
        for taskset in tasksets:
            groups.setdefault(taskset.utilization, []).append(taskset)
        selected = []
        count = int(config["grid"]["selected_tasksets_per_utilization"])
        for utilization in config["grid"]["utilization_points"]:
            ordered = sorted(
                groups.get(utilization, []),
                key=lambda taskset: taskset.taskset_semantic_hash,
            )
            if len(ordered) < count:
                raise PerformanceExecutionError(
                    f"horizon gate lacks frozen tasksets at {utilization}"
                )
            selected.extend(ordered[:count])
        return tuple(selected)
    return tuple(tasksets)


def formal_plan_document(
    config: Mapping[str, Any], requests: Sequence[PerformanceRequest], *,
    source_commit: str, simulator_binary_sha256: str, taskset_store_identity_value: str,
    stage_environment: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    request_ids = [request.semantic_request_id for request in requests]
    material = {
        "contract_version": CONTRACT_VERSION, "config_hash": config["config_hash"],
        "source_commit": source_commit, "simulator_binary_sha256": simulator_binary_sha256,
        "taskset_store_identity": taskset_store_identity_value,
        "request_count": len(requests),
    }
    if stage_environment is not None:
        material["stage_environment_identity"] = stage_environment["environment_identity"]
    document = {
        "schema": "ASAP_BLOCK_V9_3_B4_FORMAL_PLAN_V1", **material,
        "formal_plan_identity": formal_plan_identity(request_ids, material),
        "requests": [request.row() for request in requests],
    }
    if stage_environment is not None:
        document["stage_environment"] = dict(stage_environment)
    return document


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _calibration_request_row(request: Mapping[str, Any]) -> Dict[str, Any]:
    energy = request["energy_material"]
    return {
        "semantic_request_id": request["semantic_request_id"],
        "execution_identity": request["execution_identity"],
        "taskset_id": request["taskset_id"],
        "taskset_semantic_hash": request["taskset_semantic_hash"],
        "u_norm": request["u_norm"], "kappa": energy["kappa"],
        "eta": energy["eta"], "energy_identity": request["energy_identity"],
        "scheduler_id": request["scheduler_id"],
        "runtime_horizon_ms": request["runtime_horizon_ms"],
    }


def _refresh_calibration_csvs(output_root: Path) -> None:
    request_rows: Dict[str, Dict[str, Any]] = {}
    for path in sorted(output_root.glob("calibration*_requests.json")):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            for request in plan.get("requests", []):
                request_rows[str(request["semantic_request_id"])] = _calibration_request_row(request)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    if request_rows:
        _write_csv(output_root / "calibration_requests.csv", list(request_rows.values()))
    result_rows = []
    for path in sorted((output_root / "terminal_results").glob("*.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            energy = result["energy_material"]
            outcome = result["outcome"]
            result_rows.append({
                "semantic_request_id": result["semantic_request_id"],
                "execution_identity": result["execution_identity"],
                "taskset_id": result["taskset_id"],
                "taskset_semantic_hash": result["taskset_semantic_hash"],
                "u_norm": result["u_norm"], "kappa": energy["kappa"],
                "eta": energy["eta"], "energy_identity": result["energy_identity"],
                "scheduler_id": result["scheduler_id"],
                "runtime_horizon_ms": result["runtime_horizon_ms"],
                "observed_pass": outcome["observed_pass"],
                "outcome_reason": outcome["reason"],
                "legacy_status": result["legacy_status"],
            })
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    if result_rows:
        _write_csv(output_root / "calibration_results.csv", result_rows)


def _assert_provenance(document: Mapping[str, Any], source_commit: str, binary_hash: str, label: str) -> None:
    if document.get("source_commit") != source_commit:
        raise PerformanceExecutionError(f"source commit changed after {label}")
    if document.get("simulator_binary_sha256") != binary_hash:
        raise PerformanceExecutionError(f"simulator binary changed after {label}")


def _reuse_selected_gate_results(
    config: Mapping[str, Any], requests: Sequence[PerformanceRequest], output_root: Path,
    source_commit: str, binary_hash: str,
) -> set[str]:
    if config["stage"] != "FORMAL":
        return set()
    seal = load_horizon_seal(Path(config["execution"]["horizon_seal"]))
    _assert_provenance(seal, source_commit, binary_hash, "horizon gate")
    selected = set(str(value) for value in seal.get("selected_gate_request_ids", ()))
    unselected = set(str(value) for value in seal.get("unselected_gate_request_ids", ()))
    formal = {request.semantic_request_id: request for request in requests}
    if len(selected) != 2000 or not selected < set(formal):
        raise PerformanceExecutionError("horizon seal does not contain the strict 2000-request formal subset")
    if not unselected or not unselected.isdisjoint(formal):
        raise PerformanceExecutionError("unselected gate horizon overlaps the formal plan")
    gate_root = str(config["execution"].get("gate_results_root", "")).strip()
    if not gate_root:
        raise PerformanceExecutionError("formal execution requires execution.gate_results_root")
    source_root = Path(gate_root) / "terminal_results"
    target_root = output_root / "terminal_results"
    for request_id in sorted(selected):
        source = source_root / f"{request_id}.json"
        try:
            result = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PerformanceExecutionError(f"selected gate result is unavailable: {request_id}") from exc
        expected = formal[request_id]
        if (
            result.get("semantic_request_id") != request_id
            or result.get("execution_identity") != expected.execution_identity
            or result.get("terminal") is not True
        ):
            raise PerformanceExecutionError(f"selected gate result identity/terminal mismatch: {request_id}")
        target = target_root / f"{request_id}.json"
        if target.is_file() and config["execution"]["resume"]:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing != result:
                raise PerformanceExecutionError(f"resumed gate result differs: {request_id}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, result)
    return selected


def _runner_simulation_config(config: Mapping[str, Any], request: PerformanceRequest, timeout: int) -> Dict[str, Any]:
    return {
        "horizon": request.runtime_horizon_ms,
        "maximum_horizon": request.runtime_horizon_ms,
        "horizon_extension_policy": "none", "warmup": config["simulation"]["warmup_ms"],
        "minimum_jobs_per_task": config["simulation"]["minimum_adjudicable_jobs_per_task"],
        "timeout_seconds": timeout, "trace_mode": "job",
        "trace_on_failure": True, "retain_trace": request.retain_trace,
        # Ordinary deadline misses do not trigger bulk trace retention in B4.
        "retain_trace_statuses": [SimulationStatus.INTERNAL_ERROR.value],
        "simulator_bin": config["simulation"]["simulator_bin"],
    }


def execute_request(
    config: Mapping[str, Any], request: PerformanceRequest,
    taskset: PerformanceTaskset, run_root: Path,
) -> Dict[str, Any]:
    if taskset.taskset_semantic_hash != request.taskset_semantic_hash:
        raise PerformanceExecutionError("request/taskset semantic hash mismatch")
    material = EnergyMaterial(**dict(request.energy_material))
    if material.identity != request.energy_identity:
        raise PerformanceExecutionError("request energy identity mismatch")
    attempts = []
    execution = None
    base_system = Path(config["generation"]["system_template"])
    if not base_system.is_absolute():
        base_system = PROJECT_ROOT / base_system
    for timeout in (
        int(config["simulation"]["timeout_seconds"]),
        int(config["simulation"]["retry_timeout_seconds"]),
    ):
        execution = run_paired_simulation(
            simulation_id_value=request.semantic_request_id,
            base_system_path=base_system,
            run_root=Path(run_root), task_payload=taskset.tasks,
            taskset_hash=taskset.taskset_semantic_hash,
            processors=int(config["platform"]["cores"]),
            exact_e0=B4_RUNNER_RELEASE_E0,
            energy_config=runner_energy_config(material),
            simulation_config=_runner_simulation_config(config, request, timeout),
            scheduler_id=request.scheduler_id,
        )
        attempts.append({
            "timeout_seconds": timeout, "runtime_seconds": execution.runtime_seconds,
            "legacy_status": execution.result.status.value,
            "reason": execution.result.reason,
        })
        if execution.result.status is not SimulationStatus.RUNTIME_TIMEOUT:
            break
    assert execution is not None
    technical_error = None
    if execution.result.status in {SimulationStatus.INTERNAL_ERROR, SimulationStatus.RUNTIME_TIMEOUT}:
        technical_error = execution.result.reason
    outcome = evaluate_simulation_result(
        execution.result, taskset.tasks,
        horizon_ms=request.runtime_horizon_ms,
        warmup_ms=int(config["simulation"]["warmup_ms"]),
        minimum_jobs_per_task=int(config["simulation"]["minimum_adjudicable_jobs_per_task"]),
        technical_error=technical_error, processors=int(config["platform"]["cores"]),
    )
    return {
        "schema": "ASAP_BLOCK_V9_3_B4_TERMINAL_RESULT_V1",
        **request.row(), "attempts": attempts,
        "arrival_offsets_zero": all(int(task.get("arrival_offset", -1)) == 0 for task in taskset.tasks),
        "rta_release_e0_certificate": "NOT_APPLICABLE",
        "runner_release_e0_value": "0",
        "planned_initial_energy": material.planned_initial_energy,
        "terminal": execution.result.status is not SimulationStatus.RUNTIME_TIMEOUT,
        "legacy_status": execution.result.status.value,
        "legacy_reason": execution.result.reason,
        "simulation_completed": execution.result.simulation_completed,
        "completion_reason": execution.result.completion_reason,
        "outcome": outcome.row(),
        "retained_trace_path": str(execution.retained_trace_path) if execution.retained_trace_path else None,
        "stdout_tail": execution.stdout_tail, "stderr_tail": execution.stderr_tail,
    }


def execute_plan(
    config: Mapping[str, Any], store: PerformanceTasksetStore, *,
    max_requests: Optional[int] = None, phase: str = "default",
) -> Dict[str, Any]:
    assert_execution_seals(config)
    manifest = store.verify_manifest()
    simulator = Path(config["simulation"]["simulator_bin"])
    if not simulator.is_absolute():
        simulator = PROJECT_ROOT / simulator
    source_commit = exact_source_commit()
    binary_hash = _sha256(simulator)
    try:
        stage_environment = build_stage_environment(
            config, project_root=PROJECT_ROOT, simulator_path=simulator,
        )
    except StageEnvironmentError as exc:
        raise PerformanceExecutionError(str(exc)) from exc
    if stage_environment["exact_source_commit"] != source_commit:
        raise PerformanceExecutionError("stage environment/source commit mismatch")
    if stage_environment["simulator_binary_sha256"] != binary_hash:
        raise PerformanceExecutionError("stage environment/simulator mismatch")
    if config["stage"] in {"HORIZON_GATE", "FORMAL"}:
        selection = load_selection_seal(Path(config["execution"]["calibration_seal"]))
        if selection.get("confirmation_status") != "CONFIRMED":
            raise PerformanceExecutionError("CAL selection has not passed the 30-second confirmation gate")
        claimed = selection.get("selection_identity")
        material = {key: value for key, value in selection.items() if key != "selection_identity"}
        if not claimed or calibration_selection_identity(material) != claimed:
            raise PerformanceExecutionError("CAL selection identity mismatch")
        _assert_provenance(selection, source_commit, binary_hash, "CAL")
        try:
            assert_environment_compatible(stage_environment, selection["stage_environment"])
        except (KeyError, StageEnvironmentError) as exc:
            raise PerformanceExecutionError(f"CAL stage environment mismatch: {exc}") from exc
        if config["stage"] == "FORMAL":
            horizon_seal = load_horizon_seal(Path(config["execution"]["horizon_seal"]))
            try:
                assert_environment_compatible(stage_environment, horizon_seal["stage_environment"])
            except (KeyError, StageEnvironmentError) as exc:
                raise PerformanceExecutionError(f"horizon stage environment mismatch: {exc}") from exc
    elif config["stage"] == "CALIBRATION" and phase != "default":
        control_path = config["execution"].get("calibration_seal") or config["energy"].get("selection_seal")
        control = load_calibration_control(Path(control_path))
        _assert_provenance(control, source_commit, binary_hash, "initial CAL phase")
        try:
            assert_environment_compatible(
                stage_environment, control["stage_environment"], require_stage_config=True,
            )
        except (KeyError, StageEnvironmentError) as exc:
            raise PerformanceExecutionError(f"CAL phase environment mismatch: {exc}") from exc
    tasksets = tasksets_from_store(config, store)
    requests = build_requests(
        config, tasksets, source_commit=source_commit,
        simulator_binary_sha256=binary_hash, phase=phase,
    )
    output_root = Path(config["execution"]["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    plan = formal_plan_document(
        config, requests, source_commit=source_commit,
        simulator_binary_sha256=binary_hash,
        taskset_store_identity_value=manifest["store_identity"],
        stage_environment=stage_environment,
    )
    plan_name = {
        "CALIBRATION": (
            {
                "default": "calibration_requests.json",
                "extension_a": "calibration_extension_a_requests.json",
                "extension_b": "calibration_extension_b_requests.json",
                "confirmation": "calibration_confirmation_requests.json",
                "confirmation_full_grid": "calibration_confirmation_full_grid_requests.json",
            }[phase]
        ),
        "HORIZON_GATE": "horizon_gate_requests.json",
        "FORMAL": "formal_plan.json", "SMOKE": "smoke_plan.json",
    }[config["stage"]]
    atomic_write_json(output_root / plan_name, plan)
    if config["stage"] == "CALIBRATION":
        _refresh_calibration_csvs(output_root)
    reused_gate_ids = _reuse_selected_gate_results(
        config, requests, output_root, source_commit, binary_hash,
    )
    pending = tuple(request for request in requests if request.semantic_request_id not in reused_gate_ids)
    selected = pending if max_requests is None else pending[:max_requests]
    taskset_by_id = {taskset.taskset_id: taskset for taskset in tasksets}
    completed = 0
    for request in selected:
        result_path = output_root / "terminal_results" / f"{request.semantic_request_id}.json"
        if result_path.is_file() and config["execution"]["resume"]:
            completed += 1
            continue
        result = execute_request(config, request, taskset_by_id[request.taskset_id], output_root)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(result_path, result)
        completed += 1
    if config["stage"] == "CALIBRATION":
        _refresh_calibration_csvs(output_root)
    return {
        "planned_requests": len(requests), "reused_gate_requests": len(reused_gate_ids),
        "selected_requests": len(selected),
        "completed_requests": completed, "simulator_invoked": bool(selected),
        "source_commit": source_commit, "simulator_binary_sha256": binary_hash,
    }
