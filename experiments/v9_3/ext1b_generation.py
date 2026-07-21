"""Outcome-independent targeted input construction for EXT-1B."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from pathlib import Path
import random
from typing import Any, Dict, Mapping, Sequence, Tuple

import asap_block_rta as legacy_rta

from .config import (
    canonical_json, domain_hash, fraction_text, task_workload_energy_model,
)
from .ext1b_capacity_contract import (
    NATIVE_ENERGY_EPSILON_J,
    capacity_contract_identity,
    capacity_feasibility_violations,
    capacity_rejection_detail,
)
from .ext1b_b3_target_trace import (
    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
    B3_V2_HARVEST_TRACE_DOMAIN,
    B3_V2_TASKSET_DOMAIN,
    B3_V2_TASKSET_SCHEMA,
    actual_trace_recovery,
    binary64_materialized_text,
    is_b3_target_trace_v2,
    native_binary64_affordable,
    recovery_prefix_affordable_at_capacity,
    runtime_recovery_prefix,
    v2_paired_instance_identity,
    v2_scenario_candidate_identity,
)
from .result_writer import atomic_write_json, atomic_write_text
from .taskset_store import StoredTaskset
from .task_identity import runtime_job_id, runtime_task_name_for_source_id


PEAK_TIME_OF_DAY_MS = 11 * 60 * 60 * 1000


class StructuralRejection(ValueError):
    """A reproducible structural rejection that never inspects outcomes."""

    def __init__(
        self, code: str, detail: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}:{detail}")
        self.code = code
        self.detail = detail
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class ScenarioCell:
    cell_id: str
    kind: str
    subtype: str
    deadline_ratio_min: Fraction
    deadline_ratio_max: Fraction
    nominal_supply_ratio: Fraction
    initial_energy_policy: str
    interpolation_rho: Fraction | None = None
    recovery_margin_ticks: int | None = None
    scenario_contract_id: str | None = None

    def row(self) -> Dict[str, Any]:
        result = {
            "scenario_cell_id": self.cell_id,
            "scenario_kind": self.kind,
            "scenario_subtype": self.subtype,
            "deadline_ratio_min": fraction_text(self.deadline_ratio_min),
            "deadline_ratio_max": fraction_text(self.deadline_ratio_max),
            "nominal_energy_supply_ratio": fraction_text(self.nominal_supply_ratio),
            "initial_energy_policy": self.initial_energy_policy,
        }
        if self.scenario_contract_id is not None:
            result.update({
                "scenario_contract_id": self.scenario_contract_id,
                "interpolation_rho": fraction_text(self.interpolation_rho),
                "recovery_margin_ticks": self.recovery_margin_ticks,
            })
        return result


@dataclass(frozen=True)
class ScenarioInstance:
    paired_instance_id: str
    scenario_candidate_identity: str
    capacity_feasibility_contract_identity: str
    scenario_cell: ScenarioCell
    logical_taskset_index: int
    attempt_index: int
    generation_seed: int
    source_taskset_id: str
    source_taskset_hash: str
    taskset_id: str
    taskset_hash: str
    priority_hash: str
    power_hash: str
    deadline_hash: str
    release_hash: str
    trace_hash: str
    processors: int
    tasks: Tuple[Mapping[str, Any], ...]
    initial_battery: Fraction
    battery_capacity: Fraction
    nominal_demand_j_per_tick: Fraction
    nominal_harvest_j_per_tick: Fraction
    base_harvesting_rate_w: Fraction
    allow_harvest_clipping: bool
    system_template_path: Path
    structure: Mapping[str, Any]

    @property
    def subtype(self) -> str:
        return self.scenario_cell.subtype


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def interpolate_exact(lower: Fraction, upper: Fraction, rho: Fraction) -> Fraction:
    if not lower < upper:
        raise StructuralRejection("NON_STRICT_ENERGY_INTERVAL", f"{lower} !< {upper}")
    if not 0 < rho < 1:
        raise ValueError("rho must lie strictly between zero and one")
    result = lower + rho * (upper - lower)
    if not lower <= result < upper:
        raise AssertionError("exact interpolation escaped its half-open interval")
    return result


def native_energy_affordable(available: Fraction, required: Fraction) -> bool:
    """Mirror the simulator's joule-domain affordability tolerance."""

    return available + NATIVE_ENERGY_EPSILON_J >= required


def enforce_b3_capacity_feasibility(
    tasks: Sequence[Mapping[str, Any]],
    battery_capacity: Fraction,
    config: Mapping[str, Any],
) -> None:
    """Reject a whole B3 scenario candidate if any task cannot run a tick."""

    violations = capacity_feasibility_violations(
        tasks, battery_capacity, config,
    )
    if not violations:
        return
    representative = dict(violations[0])
    rejection_code = str(representative.pop("code"))
    diagnostics = {
        **representative,
        "capacity_infeasible_task_count": len(violations),
        "capacity_infeasible_taskset_count": 1,
        "violations_json": canonical_json(violations),
    }
    raise StructuralRejection(
        rejection_code,
        capacity_rejection_detail(violations),
        diagnostics,
    )


def _native_blocking_interpolation(
    lower: Fraction, upper: Fraction, rho: Fraction,
) -> Fraction:
    """Choose a point that is affordable at ``lower`` but blocked at ``upper``."""

    blocking_upper = upper - NATIVE_ENERGY_EPSILON_J
    initial = interpolate_exact(lower, blocking_upper, rho)
    if not native_energy_affordable(initial, lower):
        raise AssertionError("constructed initial energy cannot afford the lower bound")
    if native_energy_affordable(initial, upper):
        raise AssertionError("constructed initial energy is not natively blocked")
    return initial


def scenario_cells(config: Mapping[str, Any]) -> Tuple[ScenarioCell, ...]:
    scenario = config["scenario"]
    kind = str(scenario["kind"])
    if kind == "TIMING_STRESS":
        if is_b3_target_trace_v2(config):
            grid = scenario["calibration_grid"]
            result = []
            for row in scenario["timing_cells"]:
                common = (
                    kind,
                    str(row["subtype"]),
                    Fraction(str(row["deadline_ratio_min"])),
                    Fraction(str(row["deadline_ratio_max"])),
                )
                if row["subtype"] == "POSITIVE_SLACK_ENERGY_AVAILABLE":
                    result.append(ScenarioCell(
                        str(row["id"]), *common,
                        Fraction(str(row["nominal_energy_supply_ratio"])),
                        str(row["initial_energy_policy"]),
                        Fraction(str(scenario["interpolation_rho"])),
                        0,
                        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
                    ))
                    continue
                for margin_index, rho_index, eta_index in product(
                    range(len(grid["recovery_margin_ticks"])),
                    range(len(grid["interpolation_rhos"])),
                    range(len(grid["nominal_energy_supply_ratios"])),
                ):
                    margin = int(grid["recovery_margin_ticks"][margin_index])
                    rho = Fraction(str(grid["interpolation_rhos"][rho_index]))
                    eta = Fraction(str(
                        grid["nominal_energy_supply_ratios"][eta_index]
                    ))
                    cell_id = (
                        f"{row['id']}-margin-{margin_index:02d}-"
                        f"rho-{rho_index:02d}-eta-{eta_index:02d}"
                    )
                    result.append(ScenarioCell(
                        cell_id, *common, eta,
                        str(row["initial_energy_policy"]), rho, margin,
                        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
                    ))
            return tuple(result)
        return tuple(ScenarioCell(
            str(row["id"]), kind, str(row["subtype"]),
            Fraction(str(row["deadline_ratio_min"])),
            Fraction(str(row["deadline_ratio_max"])),
            Fraction(str(row["nominal_energy_supply_ratio"])),
            str(row["initial_energy_policy"]),
        ) for row in scenario["timing_cells"])
    return tuple(ScenarioCell(
        f"{str(scenario['subtype']).lower()}-eta-{index:02d}",
        kind,
        str(scenario["subtype"]),
        Fraction(str(scenario["deadline_ratio_min"])),
        Fraction(str(scenario["deadline_ratio_max"])),
        Fraction(str(eta)),
        str(scenario["initial_energy_policy"]),
    ) for index, eta in enumerate(scenario["nominal_energy_supply_ratios"]))


def workload_energy_table(system_path: Path) -> Tuple[Tuple[str, Fraction], ...]:
    """Read and sort the simulator's configured workload energy model."""

    values = tuple(sorted(
        task_workload_energy_model(system_path),
        key=lambda item: (item[1], item[0]),
    ))
    if len(values) < 2 or values[0][1] >= values[-1][1]:
        raise StructuralRejection(
            "POWER_MODEL_HAS_NO_STRICT_ORDER",
            canonical_json([(name, fraction_text(value)) for name, value in values]),
        )
    return values


def _apply_power_profile(
    payload: Sequence[Mapping[str, Any]],
    table: Sequence[tuple[str, Fraction]],
    profile: str,
) -> list[Dict[str, Any]]:
    energy_by_workload = dict(table)
    low, high = table[0], table[-1]
    middle = table[len(table) // 2]
    result = []
    task_count = len(payload)
    for raw in payload:
        row = dict(raw)
        if profile == "HIGH_PRIORITY_HIGH_POWER":
            priority_rank = int(row["priority_rank"])
            if priority_rank * 3 < task_count:
                workload, energy = high
            elif priority_rank * 3 >= 2 * task_count:
                workload, energy = low
            else:
                workload, energy = middle
        else:
            workload = str(row["workload"])
            if workload not in energy_by_workload:
                raise StructuralRejection(
                    "WORKLOAD_NOT_IN_ACTUAL_POWER_MODEL", workload,
                )
            energy = energy_by_workload[workload]
        row["workload"] = workload
        row["P"] = fraction_text(energy)
        row["arrival_offset"] = 0
        result.append(row)
    return result


def transform_constrained_deadlines(
    payload: Sequence[Mapping[str, Any]],
    lower_ratio: Fraction,
    upper_ratio: Fraction,
    seed: int,
) -> list[Dict[str, Any]]:
    """Apply the EXT-1B-local C <= D <= T deadline transform."""

    rng = random.Random(seed)
    transformed = []
    for position, raw in enumerate(payload):
        row = dict(raw)
        c_value, t_value = int(row["C"]), int(row["T"])
        lower = max(c_value, _ceil_fraction(lower_ratio * t_value))
        upper = min(t_value, (upper_ratio * t_value).numerator // (upper_ratio * t_value).denominator)
        if lower > upper:
            raise StructuralRejection(
                "DEADLINE_INTERVAL_EMPTY",
                f"task={position},C={c_value},T={t_value},lower={lower},upper={upper}",
            )
        deadline = rng.randint(lower, upper)
        row["D"] = deadline
        row["D_over_T"] = fraction_text(Fraction(deadline, t_value))
        transformed.append(row)
    return transformed


def nominal_demand_j_per_tick(payload: Sequence[Mapping[str, Any]]) -> Fraction:
    return sum((
        Fraction(int(row["C"]), int(row["T"])) * Fraction(str(row["P"]))
        for row in payload
    ), Fraction(0))


def bypass_structure(
    payload: Sequence[Mapping[str, Any]], rho: Fraction,
) -> tuple[Fraction, Dict[str, Any]]:
    ordered = sorted(payload, key=lambda row: int(row["priority_rank"]))
    selected = None
    for high_index, high in enumerate(ordered[:-1]):
        lower = [
            row for row in ordered[high_index + 1:]
            if Fraction(str(row["P"])) < Fraction(str(high["P"]))
        ]
        if lower:
            selected = (high, min(
                lower,
                key=lambda row: (Fraction(str(row["P"])), -int(row["priority_rank"])),
            ))
            break
    if selected is None:
        raise StructuralRejection("NO_PRIORITY_POWER_ANTAGONISM", "no e_l < e_h pair")
    high, low = selected
    e_high, e_low = Fraction(str(high["P"])), Fraction(str(low["P"]))
    initial = _native_blocking_interpolation(e_low, e_high, rho)
    return initial, {
        "high_task_id": high["task_id"],
        "high_priority_rank": high["priority_rank"],
        "high_unit_energy": fraction_text(e_high),
        "low_task_id": low["task_id"],
        "low_priority_rank": low["priority_rank"],
        "low_unit_energy": fraction_text(e_low),
        "rho": fraction_text(rho),
        "native_affordability_epsilon_j": fraction_text(NATIVE_ENERGY_EPSILON_J),
        "predicate": "E_init + native_epsilon >= e_l and E_init + native_epsilon < e_h",
        "predicate_satisfied": (
            native_energy_affordable(initial, e_low)
            and not native_energy_affordable(initial, e_high)
        ),
    }


def sync_batch_structure(
    payload: Sequence[Mapping[str, Any]], processors: int, p_value: int,
    rho: Fraction,
) -> tuple[Fraction, Dict[str, Any]]:
    ready = sorted(
        (row for row in payload if int(row.get("arrival_offset", 0)) == 0),
        key=lambda row: int(row["priority_rank"]),
    )
    if len(ready) < processors:
        raise StructuralRejection(
            "INSUFFICIENT_READY_JOBS_FOR_TOP_M",
            f"ready={len(ready)},M={processors}",
        )
    q_value = processors
    if q_value < 2:
        raise StructuralRejection(
            "ACTIVE_TOP_M_TOO_SMALL", f"q={q_value}"
        )
    if not 1 <= p_value < q_value:
        raise StructuralRejection("INVALID_AFFORDABLE_PREFIX", f"p={p_value},q={q_value}")
    top_q = ready[:q_value]
    energies = [Fraction(str(row["P"])) for row in top_q]
    prefix = sum(energies[:p_value], Fraction(0))
    batch = sum(energies, Fraction(0))
    if not prefix < batch:
        raise StructuralRejection(
            "NON_STRICT_ENERGY_INTERVAL",
            f"prefix={fraction_text(prefix)},batch={fraction_text(batch)}",
        )
    initial = _native_blocking_interpolation(prefix, batch, rho)
    # The simulator input materializer writes a round-trippable binary64
    # decimal.  Freeze that exact value here, then re-check the structural
    # predicate so quantization can never silently move E_init onto or outside
    # either native-affordability boundary.
    materialized_initial = Fraction(format(float(initial), ".17g"))
    if (
        materialized_initial < prefix
        or materialized_initial >= batch
        or not native_energy_affordable(materialized_initial, prefix)
        or native_energy_affordable(materialized_initial, batch)
    ):
        raise StructuralRejection(
            "QUANTIZED_INITIAL_ENERGY_OUTSIDE_INTERVAL",
            f"E_init={fraction_text(materialized_initial)},"
            f"prefix={fraction_text(prefix)},batch={fraction_text(batch)}",
        )
    initial = materialized_initial
    return initial, {
        "p": p_value,
        "q": q_value,
        "ready_job_count": len(ready),
        "top_q_task_ids": [row["task_id"] for row in top_q],
        "top_q_priority_ranks": [row["priority_rank"] for row in top_q],
        "top_q_unit_energies": [fraction_text(value) for value in energies],
        "E_prefix": fraction_text(prefix),
        "E_batch": fraction_text(batch),
        "E_init_materialized": fraction_text(initial),
        "energy_unit": "joule_per_tick",
        "rho": fraction_text(rho),
        "native_affordability_epsilon_j": fraction_text(NATIVE_ENERGY_EPSILON_J),
        "predicate": (
            "E_prefix <= E_init < E_batch and native affordability accepts "
            "the prefix but rejects the batch"
        ),
        "predicate_satisfied": (
            prefix <= initial < batch
            and native_energy_affordable(initial, prefix)
            and not native_energy_affordable(initial, batch)
        ),
    }


def _timing_structure(
    payload: Sequence[Mapping[str, Any]],
    cell: ScenarioCell,
    processors: int,
    rho: Fraction,
    *,
    target_trace_v2: bool = False,
) -> tuple[Fraction, Fraction, bool, Dict[str, Any]]:
    ordered = sorted(payload, key=lambda row: int(row["priority_rank"]))
    q_value = min(processors, len(ordered))
    top_q = ordered[:q_value]
    slacks = [int(row["D"]) - int(row["C"]) for row in top_q]
    if not slacks or min(slacks) <= 0:
        raise StructuralRejection("TOP_M_HAS_NONPOSITIVE_SLACK", canonical_json(slacks))
    demand = nominal_demand_j_per_tick(payload)
    harvest = demand * cell.nominal_supply_ratio
    target = ordered[0]
    target_energy = Fraction(str(target["P"]))
    target_slack = int(target["D"]) - int(target["C"])
    target_runtime_name = runtime_task_name_for_source_id(target["task_id"])
    target_arrival_time = 0
    target_identity = {
        "target_source_task_id": str(target["task_id"]),
        "target_runtime_task_name": target_runtime_name,
        "target_arrival_time": target_arrival_time,
        "target_job_id": runtime_job_id(
            target_runtime_name, target_arrival_time,
        ),
        "target_priority_rank": int(target["priority_rank"]),
        "target_workload": str(target["workload"]),
        "target_unit_energy": fraction_text(target_energy),
        "target_initial_slack": target_slack,
    }
    if cell.subtype == "POSITIVE_SLACK_ENERGY_AVAILABLE":
        if harvest != 0:
            raise StructuralRejection("AVAILABLE_CELL_REQUIRES_ZERO_HARVEST", fraction_text(harvest))
        required = sum((Fraction(str(row["P"])) for row in top_q), Fraction(0))
        structure = {
            "q": q_value,
            "top_q_task_ids": [row["task_id"] for row in top_q],
            "top_q_initial_slacks": slacks,
            "top_q_required_energy": fraction_text(required),
            "predicate": "positive slack and top-q energy available at release",
            "predicate_satisfied": True,
        }
        if target_trace_v2:
            structure.update({
                **target_identity,
                "target_recovery_contract_applicable": False,
                "recovery_prefix_identity": "",
                "recovery_prefix_length": 0,
                "recovery_prefix_required_energy": "",
                "materialized_battery_capacity": fraction_text(required),
            })
        return required, required, False, structure

    initial = rho * target_energy
    if harvest <= 0:
        raise StructuralRejection("CHARGING_CELL_HAS_ZERO_HARVEST", "eta*demand is zero")
    capacity = target_energy + harvest
    if target_trace_v2:
        # Freeze exactly what the simulator reads, then size the battery for
        # the complete runtime RM/task-number prefix rather than one task.
        initial = Fraction(binary64_materialized_text(initial))
        try:
            prefix = runtime_recovery_prefix(
                payload, processors, initial_energy=initial,
            )
        except ValueError as exc:
            raise StructuralRejection(
                "RECOVERY_PREFIX_IDENTITY_INVALID", str(exc),
            ) from exc
        capacity = Fraction(prefix["materialized_battery_capacity"])
        if prefix["recovery_prefix_task_ids"][0] != str(target["task_id"]):
            raise StructuralRejection(
                "RECOVERY_PREFIX_TARGET_MISMATCH",
                "highest-priority runtime prefix job is not the target",
                prefix,
            )
        if not prefix["target_blocked_at_initial_energy"]:
            raise StructuralRejection(
                "TARGET_NOT_BLOCKED_AT_INITIAL_ENERGY",
                "E_init + native epsilon reaches target unit energy",
                prefix,
            )
        if not recovery_prefix_affordable_at_capacity(prefix, capacity):
            raise StructuralRejection(
                "RECOVERY_PREFIX_NOT_AFFORDABLE_AT_FULL",
                "materialized full battery cannot reserve runtime top-q",
                prefix,
            )
        return initial, capacity, True, {
            **target_identity,
            **prefix,
            "interpolation_rho": fraction_text(rho),
            "native_affordability_epsilon_j": fraction_text(
                NATIVE_ENERGY_EPSILON_J
            ),
        }
    affordable_tick = _ceil_fraction((target_energy - initial) / harvest)
    full_tick = _ceil_fraction((capacity - initial) / harvest)
    if not 0 < affordable_tick < full_tick < target_slack:
        raise StructuralRejection(
            "CHARGING_TIMES_NOT_SEPARABLE",
            f"affordable={affordable_tick},full={full_tick},slack={target_slack}",
        )
    return initial, capacity, True, {
        "target_task_id": target["task_id"],
        "target_priority_rank": target["priority_rank"],
        "target_unit_energy": fraction_text(target_energy),
        "target_initial_slack": target_slack,
        "energy_affordable_tick": affordable_tick,
        "battery_full_tick": full_tick,
        "predicate": "E0 < e_target; affordable_tick < full_tick < initial_slack",
        "predicate_satisfied": True,
    }


def materialize_scenario_system(
    base_system_path: Path,
    destination: Path,
    *,
    processors: int,
    base_harvesting_rate_w: Fraction,
) -> Path:
    # Preserve the original flow-style power-model arrays.  The audited
    # simulation materializer rewrites speed_params in place and therefore
    # requires their continuation-free representation.
    source = base_system_path.read_text(encoding="utf-8")
    replacements = {
        "numcpus": str(processors),
        "time_of_day_ms": str(PEAK_TIME_OF_DAY_MS),
        "base_harvesting_rate": format(float(base_harvesting_rate_w), ".17g"),
        "harvesting_scale": "1.0",
        "use_real_solar_data": "false",
    }
    seen = {key: 0 for key in replacements}
    rendered = []
    for line in source.splitlines():
        stripped = line.strip()
        matched = next(
            (key for key in replacements if stripped.startswith(key + ":")),
            None,
        )
        if matched is None:
            rendered.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        comment = "  #" + line.split("#", 1)[1] if "#" in line else ""
        rendered.append(f"{indent}{matched}: {replacements[matched]}{comment}")
        seen[matched] += 1
    if any(count != 1 for count in seen.values()):
        raise StructuralRejection(
            "SYSTEM_TEMPLATE_REPLACEMENT_COUNT",
            canonical_json(seen),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, "\n".join(rendered) + "\n")
    return destination


def actual_trace_material(
    system_path: Path,
    horizon: int,
    *,
    target_trace_v2: bool = False,
) -> tuple[Tuple[Fraction, ...], str]:
    system = legacy_rta.load_system_config(str(system_path))
    values = tuple(Fraction(str(value)) for value in legacy_rta._harvest_trace_from_config(system, horizon))
    material: Any = [fraction_text(value) for value in values]
    domain = "ASAP_BLOCK:V9.3:EXT1B:HARVEST_TRACE:v1"
    if target_trace_v2:
        domain = B3_V2_HARVEST_TRACE_DOMAIN
        material = {
            "scenario_contract_id": (
                B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
            ),
            "tick_order": "trace[t-1]_is_harvested_before_decision_at_tick_t",
            "values": material,
        }
    identity = domain_hash(domain, material)
    return values, identity


def build_scenario_instance(
    stored: StoredTaskset,
    config: Mapping[str, Any],
    cell: ScenarioCell,
    *,
    logical_taskset_index: int,
    attempt_index: int,
    system_root: Path,
) -> ScenarioInstance:
    scenario = config["scenario"]
    target_trace_v2 = is_b3_target_trace_v2(config)
    base_system = Path(__file__).resolve().parents[2] / config["energy"]["service_curve"]["system_template"]
    power_table = workload_energy_table(base_system)
    payload = _apply_power_profile(
        stored.task_payload, power_table, str(scenario["priority_power_profile"])
    )
    transform_seed = int(domain_hash(
        (
            "ASAP_BLOCK:V9.3:EXT1B:DEADLINE_SEED:v2"
            if target_trace_v2
            else "ASAP_BLOCK:V9.3:EXT1B:DEADLINE_SEED:v1"
        ),
        {
            "generation_seed": stored.seed,
            "scenario_cell_id": cell.cell_id,
            "attempt_index": attempt_index,
        },
    )[:16], 16)
    if cell.kind == "TIMING_STRESS":
        payload = transform_constrained_deadlines(
            payload, cell.deadline_ratio_min, cell.deadline_ratio_max, transform_seed
        )
    rho = (
        cell.interpolation_rho
        if cell.interpolation_rho is not None
        else Fraction(str(scenario["interpolation_rho"]))
    )
    demand = nominal_demand_j_per_tick(payload)
    nominal_harvest = demand * cell.nominal_supply_ratio
    base_rate_w = nominal_harvest * 1000
    allow_clipping = False
    if cell.kind == "BYPASS_STRESS":
        initial, structure = bypass_structure(payload, rho)
        capacity = initial
    elif cell.kind == "SYNC_BATCH_STRESS":
        initial, structure = sync_batch_structure(
            payload, stored.processors,
            int(scenario["affordable_prefix_length"]), rho,
        )
        capacity = initial
    else:
        initial, capacity, allow_clipping, structure = _timing_structure(
            payload, cell, stored.processors, rho,
            target_trace_v2=target_trace_v2,
        )
        # Persist the exact scenario dimensions with the accepted instance.
        # B3 observation outputs consume this frozen metadata and never parse
        # dimensions from the human-readable scenario cell ID.
        structure = {
            **structure,
            "timing_dimensions": cell.row(),
        }

    provisional = domain_hash(
        (
            "ASAP_BLOCK:V9.3:EXT1B:SYSTEM_INPUT:v2"
            if target_trace_v2
            else "ASAP_BLOCK:V9.3:EXT1B:SYSTEM_INPUT:v1"
        ),
        {
            "cell": cell.row(), "source_hash": stored.semantic_hash,
            "logical_taskset_index": logical_taskset_index,
            "attempt_index": attempt_index,
        },
    )
    scenario_system = materialize_scenario_system(
        base_system,
        system_root / provisional / "base_system.yaml",
        processors=stored.processors,
        base_harvesting_rate_w=base_rate_w,
    )
    trace_values, trace_hash = actual_trace_material(
        scenario_system,
        int(config["simulation"]["maximum_horizon"]),
        target_trace_v2=target_trace_v2,
    )
    if not allow_clipping:
        capacity = initial + sum(trace_values, Fraction(0))
    if capacity < initial:
        raise StructuralRejection("CAPACITY_BELOW_INITIAL", "derived capacity is invalid")

    if target_trace_v2 and cell.subtype == "SLACK_LIMITED_CHARGING":
        try:
            recovery = actual_trace_recovery(
                trace_values,
                initial_energy=initial,
                battery_capacity=capacity,
                target_unit_energy=Fraction(str(structure["target_unit_energy"])),
                target_initial_slack=int(structure["target_initial_slack"]),
                recovery_margin_ticks=int(cell.recovery_margin_ticks),
                earliest_initial_deadline=int(
                    structure["recovery_earliest_initial_deadline"]
                ),
            )
        except ValueError as exc:
            message = str(exc)
            code = (
                "ACTUAL_TRACE_TARGET_NEVER_AFFORDABLE"
                if "target affordable" in message
                else "ACTUAL_TRACE_BATTERY_NEVER_FULL"
                if "full-battery" in message
                else "ACTUAL_TRACE_RECOVERY_INVALID"
            )
            raise StructuralRejection(code, message) from exc
        recovery_material = recovery.material()
        structure = {**structure, **recovery_material}
        prefix_affordable = recovery_prefix_affordable_at_capacity(
            structure, capacity,
        )
        structure["recovery_prefix_affordable_at_full"] = (
            prefix_affordable
        )
        structure["target_blocked_at_initial_energy"] = (
            not native_binary64_affordable(
                initial,
                structure["target_unit_energy"],
            )
        )
        structure["predicate"] = (
            str(structure["predicate"])
            + "; target initially blocked; full battery affords complete "
            "runtime RM/task-number top-q prefix"
        )
        structure["predicate_satisfied"] = bool(
            structure["predicate_satisfied"]
            and structure["target_blocked_at_initial_energy"]
            and prefix_affordable
        )
        if not recovery.predicate_satisfied:
            rejection_code = (
                "ACTUAL_TRACE_FULL_NOT_BEFORE_EARLIEST_INITIAL_DEADLINE"
                if recovery.earliest_initial_deadline is not None
                and recovery.full_tick >= recovery.earliest_initial_deadline
                else "ACTUAL_TRACE_RECOVERY_MARGIN_NOT_SATISFIED"
            )
            raise StructuralRejection(
                rejection_code,
                canonical_json(recovery_material),
                recovery_material,
            )
        if not prefix_affordable:
            raise StructuralRejection(
                "RECOVERY_PREFIX_NOT_AFFORDABLE_AT_FULL",
                canonical_json(structure),
                structure,
            )
    elif target_trace_v2 and cell.subtype == (
        "POSITIVE_SLACK_ENERGY_AVAILABLE"
    ):
        structure = {
            **structure,
            "actual_trace_affordable_tick": 0,
            "actual_trace_target_affordable_tick": 0,
            "actual_trace_full_tick": 0,
            "configured_recovery_margin_ticks": 0,
            "actual_trace_recovery_headroom": int(
                structure["target_initial_slack"]
            ),
            "target_recovery_contract_applicable": False,
        }

    capacity_identity = ""
    scenario_candidate_identity = ""
    if cell.kind == "TIMING_STRESS":
        enforce_b3_capacity_feasibility(payload, capacity, config)
        capacity_identity = capacity_contract_identity(config)
        candidate_material = {
                "scenario_cell": cell.row(),
                "source_taskset_hash": stored.semantic_hash,
                "logical_taskset_index": logical_taskset_index,
                "attempt_index": attempt_index,
                "capacity_feasibility_contract_identity": capacity_identity,
        }
        if target_trace_v2:
            candidate_material.update({
                "scenario_contract_id": (
                    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
                ),
                "trace_hash": trace_hash,
                "structure": structure,
            })
        scenario_candidate_identity = (
            v2_scenario_candidate_identity(
                scenario_cell=cell.row(),
                source_taskset_hash=stored.semantic_hash,
                logical_taskset_index=logical_taskset_index,
                attempt_index=attempt_index,
                capacity_feasibility_contract_identity=capacity_identity,
                trace_hash=trace_hash,
                structure=structure,
            )
            if target_trace_v2
            else domain_hash(
                "ASAP_BLOCK:V9.3:EXT1B:B3:SCENARIO_CANDIDATE:v1",
                candidate_material,
            )
        )

    task_material = {
        "schema": (
            B3_V2_TASKSET_SCHEMA
            if target_trace_v2
            else "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V3"
            if cell.kind == "TIMING_STRESS"
            else "ASAP_BLOCK_V9_3_EXT1B_TASKSET_V2"
        ),
        "scenario_cell": cell.row(),
        "source_taskset_hash": stored.semantic_hash,
        "logical_taskset_index": logical_taskset_index,
        "attempt_index": attempt_index,
        "generation_seed": stored.seed,
        "tasks": payload,
        "structure": structure,
        "task_workload_contract": config["generation"]["workload_contract"],
    }
    if cell.kind == "TIMING_STRESS":
        task_material.update({
            "scenario_candidate_identity": scenario_candidate_identity,
            "capacity_feasibility_contract": config["scenario"][
                "capacity_feasibility_contract"
            ],
            "capacity_feasibility_contract_identity": capacity_identity,
        })
        if target_trace_v2:
            task_material["scenario_contract_id"] = (
                B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
            )
    taskset_hash = domain_hash(
        (
            B3_V2_TASKSET_DOMAIN
            if target_trace_v2
            else "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v3"
            if cell.kind == "TIMING_STRESS"
            else "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v2"
        ),
        task_material,
    )
    priority_hash = domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:PRIORITY:v1",
        [(row["task_id"], row["priority_rank"]) for row in payload],
    )
    power_hash = domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:POWER:v1",
        [(row["task_id"], row["workload"], row["P"]) for row in payload],
    )
    deadline_hash = domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:DEADLINE:v1",
        [(row["task_id"], row["C"], row["D"], row["T"]) for row in payload],
    )
    release_hash = domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:RELEASE:v1",
        [(row["task_id"], row["arrival_offset"]) for row in payload],
    )
    paired_material = {
            "scenario_cell": cell.row(),
            "logical_taskset_index": logical_taskset_index,
            "taskset_hash": taskset_hash,
            "trace_hash": trace_hash,
            "initial_battery": fraction_text(initial),
            "battery_capacity": fraction_text(capacity),
            "processors": stored.processors,
            "horizon": config["simulation"]["horizon"],
    }
    if cell.kind == "TIMING_STRESS":
        paired_material.update({
            "scenario_candidate_identity": scenario_candidate_identity,
            "capacity_feasibility_contract_identity": capacity_identity,
        })
        if target_trace_v2:
            paired_material["scenario_contract_id"] = (
                B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
            )
    paired_id = (
        v2_paired_instance_identity(
            scenario_cell=cell.row(),
            logical_taskset_index=logical_taskset_index,
            taskset_hash=taskset_hash,
            trace_hash=trace_hash,
            initial_battery=fraction_text(initial),
            battery_capacity=fraction_text(capacity),
            processors=stored.processors,
            horizon=int(config["simulation"]["horizon"]),
            scenario_candidate_identity=scenario_candidate_identity,
            capacity_feasibility_contract_identity=capacity_identity,
        )
        if target_trace_v2
        else domain_hash(
            (
                "ASAP_BLOCK:V9.3:EXT1B:PAIRED_INSTANCE:v2"
                if cell.kind == "TIMING_STRESS"
                else "ASAP_BLOCK:V9.3:EXT1B:PAIRED_INSTANCE:v1"
            ),
            paired_material,
        )
    )
    taskset_id = f"ext1b-{cell.cell_id}-{logical_taskset_index:04d}-{taskset_hash[:12]}"
    canonical_path = system_root.parent / "scenario_tasksets" / f"{taskset_hash}.json"
    atomic_write_json(canonical_path, {**task_material, "taskset_id": taskset_id, "taskset_hash": taskset_hash})
    return ScenarioInstance(
        paired_id, scenario_candidate_identity, capacity_identity,
        cell, logical_taskset_index, attempt_index, stored.seed,
        stored.taskset_id, stored.semantic_hash, taskset_id, taskset_hash,
        priority_hash, power_hash, deadline_hash, release_hash, trace_hash,
        stored.processors, tuple(payload), initial, capacity, demand,
        nominal_harvest, base_rate_w, allow_clipping, scenario_system,
        structure,
    )
