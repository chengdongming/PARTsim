"""Explicit model-energy to physical-joule contracts for CORE-3 V7."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from . import exact_energy
from .rta4_formal_config import domain_hash, fraction_text


CORE3_SIMULATION_CONTRACT_V7 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_CONTRACT_V7"
)
CORE3_SIMULATION_CONTRACT_DOMAIN_V7 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_CONTRACT:v7"
)
CORE3_RESULT_SCHEMA_V7 = "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_RESULT_V7"
CORE3_RESULT_DOMAIN_V7 = "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_RESULT:v7"
CORE3_MODEL_ENERGY_UNIT_JOULES_V7 = "1/1000"
CORE3_SIMULATION_TICK_MS_V7 = 1
CORE3_TASK_WORKLOAD_V7 = "RTA4_V4_EXACT_RATIONAL_TASK"
CORE3_TASK_PHYSICAL_PROJECTION_SCHEMA_V7 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_TASK_PHYSICAL_PROJECTION_V7"
)
CORE3_TASK_PHYSICAL_PROJECTION_DOMAIN_V7 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_TASK_PHYSICAL_PROJECTION:v7"
)
CORE3_SYSTEM_ENERGY_MODEL_DOMAIN_V7 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_SYSTEM_ENERGY_MODEL:v7"
)
CORE3_PHYSICAL_EXECUTION_DOMAIN_V7 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_PHYSICAL_EXECUTION:v7"
)


class RTA4Core3ContractV7Error(ValueError):
    """Raised before an ambiguous CORE-3 V7 projection can be used."""


def normalize_model_energy_unit_joules_v7(value: Any) -> str:
    if type(value) is not str:
        raise RTA4Core3ContractV7Error(
            "model_energy_unit_joules must be a canonical positive rational "
            "string"
        )
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4Core3ContractV7Error(
            "model_energy_unit_joules is not rational"
        ) from exc
    if exact <= 0 or fraction_text(exact) != value:
        raise RTA4Core3ContractV7Error(
            "model_energy_unit_joules must be canonical and positive"
        )
    return value


def model_energy_to_joules_v7(
    model_energy: Fraction, model_energy_unit_joules: str,
) -> Fraction:
    if type(model_energy) is not Fraction:
        raise RTA4Core3ContractV7Error(
            "model energy must be supplied as an exact Fraction"
        )
    scale = Fraction(
        normalize_model_energy_unit_joules_v7(model_energy_unit_joules)
    )
    return model_energy * scale


def canonical_binary64_decimal_v7(value: Fraction) -> str:
    """Use the repository's established 17-digit Fraction renderer."""

    if type(value) is not Fraction:
        raise RTA4Core3ContractV7Error(
            "binary64 projection source must be an exact Fraction"
        )
    if value == 0:
        return "0"
    with localcontext() as context:
        context.prec = 17
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _positive_runtime_number(value: Any, label: str) -> tuple[Fraction, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RTA4Core3ContractV7Error(f"{label} must be numeric")
    try:
        runtime = float(value)
        configured = Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise RTA4Core3ContractV7Error(f"{label} must be numeric") from exc
    if not math.isfinite(runtime) or runtime <= 0 or configured <= 0:
        raise RTA4Core3ContractV7Error(
            f"{label} must be finite and positive"
        )
    return configured, runtime


def _system_energy_model_v7(
    document: Any, *, base_system_sha256: str,
) -> dict[str, Any]:
    if (
        type(base_system_sha256) is not str
        or len(base_system_sha256) != 64
        or any(character not in "0123456789abcdef"
               for character in base_system_sha256)
    ):
        raise RTA4Core3ContractV7Error(
            "base system SHA-256 is invalid"
        )
    if not isinstance(document, Mapping):
        raise RTA4Core3ContractV7Error("base system is not a mapping")
    islands = document.get("cpu_islands")
    if not isinstance(islands, list) or len(islands) != 1:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 requires exactly one CPU island"
        )
    island = islands[0]
    if not isinstance(island, Mapping):
        raise RTA4Core3ContractV7Error("CPU island is not a mapping")
    frequency_exact, frequency_runtime = _positive_runtime_number(
        island.get("base_freq"), "cpu_islands[0].base_freq",
    )
    if frequency_exact.denominator != 1:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 base frequency must be an integer"
        )
    frequency = int(frequency_runtime)
    if frequency != frequency_exact.numerator:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 base frequency materialization is ambiguous"
        )
    freqs = island.get("freqs")
    if not isinstance(freqs, list) or frequency not in freqs:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 base frequency is not in the island OPP list"
        )
    energy = document.get("energy_management")
    scheduler_model = (
        energy.get("scheduler_energy_model")
        if isinstance(energy, Mapping) else None
    )
    if not isinstance(scheduler_model, Mapping):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 system has no explicit scheduler_energy_model"
        )
    base_exact, base_runtime = _positive_runtime_number(
        scheduler_model.get("base_power"),
        "scheduler_energy_model.base_power",
    )
    coefficients = scheduler_model.get("workload_coefficients")
    if not isinstance(coefficients, Mapping):
        raise RTA4Core3ContractV7Error(
            "scheduler workload coefficients are not a mapping"
        )
    if CORE3_TASK_WORKLOAD_V7 in coefficients:
        coefficient_exact, coefficient_runtime = _positive_runtime_number(
            coefficients[CORE3_TASK_WORKLOAD_V7],
            f"workload coefficient {CORE3_TASK_WORKLOAD_V7}",
        )
        if coefficient_exact != 1 or coefficient_runtime != 1.0:
            raise RTA4Core3ContractV7Error(
                "CORE-3 V7 fixed workload coefficient conflicts with 1.0"
            )
    coefficient_exact, coefficient_runtime = Fraction(1), 1.0
    ratios = scheduler_model.get("frequency_power_ratios")
    if not isinstance(ratios, Mapping):
        raise RTA4Core3ContractV7Error(
            "scheduler frequency power ratios are not a mapping"
        )
    matching = [
        value for key, value in ratios.items()
        if str(key) == str(frequency)
    ]
    if len(matching) != 1:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 requires one explicit ratio at the base frequency"
        )
    ratio_exact, ratio_runtime = _positive_runtime_number(
        matching[0], f"frequency power ratio {frequency}",
    )
    base_energy = (
        base_exact * coefficient_exact * ratio_exact * Fraction(1, 1000)
    )
    runtime_materialized = exact_energy.materialize_task_demand_upper_bound(
        base_power=base_runtime,
        workload_coefficient=coefficient_runtime,
        frequency_ratio=ratio_runtime,
        wcet=1,
        label="CORE-3 V7 base task energy",
    )
    material = {
        "base_system_sha256": base_system_sha256,
        "base_frequency_mhz": frequency,
        "base_power_w": fraction_text(base_exact),
        "base_power_binary64_hex": base_runtime.hex(),
        "workload": CORE3_TASK_WORKLOAD_V7,
        "workload_coefficient": "1",
        "workload_coefficient_binary64_hex": coefficient_runtime.hex(),
        "frequency_power_ratio": fraction_text(ratio_exact),
        "frequency_power_ratio_binary64_hex": ratio_runtime.hex(),
        "base_energy_j_per_tick": fraction_text(base_energy),
        "base_energy_binary64_exact_j_per_tick": fraction_text(
            runtime_materialized.exact_value
        ),
        "base_energy_binary64_hex": runtime_materialized.binary64_hex,
        "cpp_rule": (
            "base_power*workload_coefficient*frequency_power_ratio*"
            "(wcet*0.001)*task_energy_factor/wcet"
        ),
        "frequency_selection": "explicit_ratio_at_integer_base_frequency",
        "runtime_frequency_dynamic": False,
    }
    return {
        **material,
        "system_energy_model_identity": domain_hash(
            CORE3_SYSTEM_ENERGY_MODEL_DOMAIN_V7, material,
        ),
    }


def core3_task_physical_projection_v7(
    *,
    base_system_path: Path | str,
    task_energy_material: Any,
    model_energy_unit_joules: str,
    simulation_tick_ms: int,
) -> dict[str, Any]:
    """Bind abstract TaskV4 power to the actual simulator energy model."""

    scale_text = normalize_model_energy_unit_joules_v7(
        model_energy_unit_joules
    )
    if simulation_tick_ms != CORE3_SIMULATION_TICK_MS_V7:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 simulation_tick_ms must equal 1"
        )
    path = Path(base_system_path).expanduser().resolve(strict=True)
    try:
        payload = path.read_bytes()
        document = yaml.safe_load(payload.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 base simulator system is unreadable"
        ) from exc
    system = _system_energy_model_v7(
        document, base_system_sha256=hashlib.sha256(payload).hexdigest(),
    )
    entries = getattr(task_energy_material, "entries", None)
    material_identity = getattr(
        task_energy_material, "task_energy_material_identity", None,
    )
    if (
        not isinstance(entries, Sequence)
        or not entries
        or type(material_identity) is not str
        or len(material_identity) != 64
        or any(character not in "0123456789abcdef"
               for character in material_identity)
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 task energy material is invalid"
        )
    scale = Fraction(scale_text)
    base_energy = Fraction(system["base_energy_j_per_tick"])
    rows: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for entry in entries:
        task_id = getattr(entry, "task_id", None)
        workload = getattr(entry, "workload", None)
        model_energy = getattr(entry, "energy_j_per_tick", None)
        task_identity = getattr(entry, "task_energy_source_identity", None)
        wcet = getattr(entry, "wcet", None)
        if (
            type(task_id) is not str
            or not task_id
            or task_id in task_ids
            or workload != CORE3_TASK_WORKLOAD_V7
            or type(model_energy) is not Fraction
            or model_energy <= 0
            or type(task_identity) is not str
            or len(task_identity) != 64
            or type(wcet) is not int
            or wcet <= 0
        ):
            raise RTA4Core3ContractV7Error(
                "CORE-3 V7 task energy entry is ambiguous"
            )
        task_ids.add(task_id)
        desired = model_energy * scale
        factor = desired / base_energy
        emitted = canonical_binary64_decimal_v7(factor)
        runtime_factor = float(emitted)
        if not math.isfinite(runtime_factor) or runtime_factor <= 0:
            raise RTA4Core3ContractV7Error(
                f"task {task_id} factor is outside binary64 range"
            )
        runtime_energy = exact_energy.materialize_task_demand_upper_bound(
            base_power=float.fromhex(system["base_power_binary64_hex"]),
            workload_coefficient=float.fromhex(
                system["workload_coefficient_binary64_hex"]
            ),
            frequency_ratio=float.fromhex(
                system["frequency_power_ratio_binary64_hex"]
            ),
            wcet=wcet,
            energy_coefficient=runtime_factor,
            label=f"CORE-3 V7 task {task_id} energy",
        )
        rows.append({
            "task_id": task_id,
            "task_energy_source_identity": task_identity,
            "workload": workload,
            "wcet": wcet,
            "model_energy_per_tick": fraction_text(model_energy),
            "expected_physical_energy_j_per_tick": fraction_text(desired),
            "exact_task_energy_factor": fraction_text(factor),
            "emitted_task_energy_factor_decimal": emitted,
            "emitted_task_energy_factor_binary64_hex": runtime_factor.hex(),
            "emitted_task_energy_factor_binary64_exact": fraction_text(
                Fraction.from_float(runtime_factor)
            ),
            "projected_binary64_energy_j_per_tick": fraction_text(
                runtime_energy.exact_value
            ),
            "projected_binary64_energy_hex": runtime_energy.binary64_hex,
        })
    material = {
        "schema": CORE3_TASK_PHYSICAL_PROJECTION_SCHEMA_V7,
        "task_energy_material_identity": material_identity,
        "model_energy_unit_joules": scale_text,
        "simulation_tick_ms": simulation_tick_ms,
        "conversion": (
            "desired_physical_energy_j_per_tick="
            "model_energy_per_tick*model_energy_unit_joules"
        ),
        "factor_formula": (
            "desired_physical_energy_j_per_tick/"
            "(base_power_w*workload_coefficient*"
            "frequency_power_ratio*1/1000)"
        ),
        "system_energy_model": system,
        "tasks": rows,
    }
    return {
        **material,
        "physical_task_projection_identity": domain_hash(
            CORE3_TASK_PHYSICAL_PROJECTION_DOMAIN_V7, material,
        ),
    }


def require_core3_task_physical_projection_v7(
    value: Any,
) -> dict[str, Any]:
    """Recompute every exact/binary64 binding in a stored V7 projection."""

    if not isinstance(value, Mapping):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 task projection is not a mapping"
        )
    projection = dict(value)
    observed_identity = projection.pop(
        "physical_task_projection_identity", None,
    )
    if (
        projection.get("schema")
        != CORE3_TASK_PHYSICAL_PROJECTION_SCHEMA_V7
        or projection.get("simulation_tick_ms") != 1
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 task projection schema/tick drift"
        )
    scale_text = normalize_model_energy_unit_joules_v7(
        projection.get("model_energy_unit_joules")
    )
    task_material_identity = projection.get("task_energy_material_identity")
    if (
        type(task_material_identity) is not str
        or len(task_material_identity) != 64
        or any(character not in "0123456789abcdef"
               for character in task_material_identity)
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 task material identity is invalid"
        )
    system = projection.get("system_energy_model")
    if not isinstance(system, Mapping):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 stored system energy model is invalid"
        )
    system_material = dict(system)
    system_identity = system_material.pop("system_energy_model_identity", None)
    if system_identity != domain_hash(
        CORE3_SYSTEM_ENERGY_MODEL_DOMAIN_V7, system_material,
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 system energy model identity drift"
        )
    try:
        base_energy = Fraction(str(system.get("base_energy_j_per_tick")))
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 stored base energy is invalid"
        ) from exc
    if base_energy <= 0:
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 stored base energy is invalid"
        )
    tasks = projection.get("tasks")
    if (
        not isinstance(tasks, Sequence)
        or isinstance(tasks, (str, bytes))
        or not tasks
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 stored task projection is empty"
        )
    seen: set[str] = set()
    scale = Fraction(scale_text)
    for row in tasks:
        if not isinstance(row, Mapping):
            raise RTA4Core3ContractV7Error(
                "CORE-3 V7 stored task projection row is invalid"
            )
        task_id = row.get("task_id")
        task_identity = row.get("task_energy_source_identity")
        wcet = row.get("wcet")
        if (
            type(task_id) is not str
            or not task_id
            or task_id in seen
            or row.get("workload") != CORE3_TASK_WORKLOAD_V7
            or type(task_identity) is not str
            or len(task_identity) != 64
            or any(character not in "0123456789abcdef"
                   for character in task_identity)
            or type(wcet) is not int
            or wcet <= 0
        ):
            raise RTA4Core3ContractV7Error(
                "CORE-3 V7 stored task identity/workload drift"
            )
        seen.add(task_id)
        try:
            model_energy = Fraction(str(row["model_energy_per_tick"]))
            expected = Fraction(str(
                row["expected_physical_energy_j_per_tick"]
            ))
            factor = Fraction(str(row["exact_task_energy_factor"]))
            emitted_exact = Fraction(str(
                row["emitted_task_energy_factor_binary64_exact"]
            ))
            projected_runtime = Fraction(str(
                row["projected_binary64_energy_j_per_tick"]
            ))
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3ContractV7Error(
                f"CORE-3 V7 task {task_id} rational provenance is invalid"
            ) from exc
        emitted = row.get("emitted_task_energy_factor_decimal")
        if (
            model_energy <= 0
            or expected != model_energy * scale
            or factor != expected / base_energy
            or row["model_energy_per_tick"] != fraction_text(model_energy)
            or row["expected_physical_energy_j_per_tick"]
            != fraction_text(expected)
            or row["exact_task_energy_factor"] != fraction_text(factor)
            or type(emitted) is not str
            or emitted != canonical_binary64_decimal_v7(factor)
        ):
            raise RTA4Core3ContractV7Error(
                f"CORE-3 V7 task {task_id} exact factor provenance drift"
            )
        runtime_factor = float(emitted)
        if (
            runtime_factor.hex()
            != row.get("emitted_task_energy_factor_binary64_hex")
            or emitted_exact != Fraction.from_float(runtime_factor)
        ):
            raise RTA4Core3ContractV7Error(
                f"CORE-3 V7 task {task_id} emitted factor provenance drift"
            )
        try:
            runtime_energy = exact_energy.materialize_task_demand_upper_bound(
                base_power=float.fromhex(system["base_power_binary64_hex"]),
                workload_coefficient=float.fromhex(
                    system["workload_coefficient_binary64_hex"]
                ),
                frequency_ratio=float.fromhex(
                    system["frequency_power_ratio_binary64_hex"]
                ),
                wcet=wcet,
                energy_coefficient=runtime_factor,
                label=f"CORE-3 V7 task {task_id} energy audit",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RTA4Core3ContractV7Error(
                f"CORE-3 V7 task {task_id} runtime model is invalid"
            ) from exc
        if (
            projected_runtime != runtime_energy.exact_value
            or row.get("projected_binary64_energy_hex")
            != runtime_energy.binary64_hex
        ):
            raise RTA4Core3ContractV7Error(
                f"CORE-3 V7 task {task_id} runtime energy provenance drift"
            )
    if observed_identity != domain_hash(
        CORE3_TASK_PHYSICAL_PROJECTION_DOMAIN_V7, projection,
    ):
        raise RTA4Core3ContractV7Error(
            "CORE-3 V7 physical task projection identity drift"
        )
    return dict(value)


def core3_physical_execution_identity_v7(
    *, base_execution_identity: str,
    physical_task_projection_identity: str,
    projected_system_sha256: str,
) -> str:
    for value, label in (
        (base_execution_identity, "base execution identity"),
        (physical_task_projection_identity, "physical task projection identity"),
        (projected_system_sha256, "projected system SHA-256"),
    ):
        if (
            type(value) is not str
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise RTA4Core3ContractV7Error(f"{label} is invalid")
    return domain_hash(CORE3_PHYSICAL_EXECUTION_DOMAIN_V7, {
        "base_execution_identity": base_execution_identity,
        "physical_task_projection_identity": (
            physical_task_projection_identity
        ),
        "projected_system_sha256": projected_system_sha256,
    })


__all__ = [
    "CORE3_MODEL_ENERGY_UNIT_JOULES_V7",
    "CORE3_PHYSICAL_EXECUTION_DOMAIN_V7",
    "CORE3_RESULT_DOMAIN_V7", "CORE3_RESULT_SCHEMA_V7",
    "CORE3_SIMULATION_CONTRACT_DOMAIN_V7",
    "CORE3_SIMULATION_CONTRACT_V7", "CORE3_SIMULATION_TICK_MS_V7",
    "CORE3_SYSTEM_ENERGY_MODEL_DOMAIN_V7",
    "CORE3_TASK_PHYSICAL_PROJECTION_DOMAIN_V7",
    "CORE3_TASK_PHYSICAL_PROJECTION_SCHEMA_V7", "CORE3_TASK_WORKLOAD_V7",
    "RTA4Core3ContractV7Error", "canonical_binary64_decimal_v7",
    "core3_physical_execution_identity_v7",
    "core3_task_physical_projection_v7", "model_energy_to_joules_v7",
    "normalize_model_energy_unit_joules_v7",
    "require_core3_task_physical_projection_v7",
]
