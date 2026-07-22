"""Exact finite-battery and fixed-window solar normalization for B4."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import csv
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import yaml

from .config import exact_fraction, fraction_text
from .performance_config import NORMALIZATION_HORIZON_MS
from .performance_identity import energy_identity


ENERGY_CONTRACT_VERSION = "ASAP_BLOCK_V9_3_B4_ENERGY_V1"


def _fraction(value: Any, label: str) -> Fraction:
    return exact_fraction(value, label)


def taskset_demand(task_payload: Sequence[Mapping[str, Any]]) -> Fraction:
    total = Fraction(0)
    for index, task in enumerate(task_payload):
        c_value = int(task["C"])
        t_value = int(task["T"])
        if c_value <= 0 or t_value <= 0 or c_value > t_value:
            raise ValueError(f"invalid C/T for task {index}")
        total += Fraction(c_value, t_value) * _fraction(task["P"], f"tasks[{index}].P")
    return total


def burst_energy(task_payload: Sequence[Mapping[str, Any]], processors: int) -> Fraction:
    if processors <= 0:
        raise ValueError("processors must be positive")
    powers = sorted(
        (_fraction(task["P"], f"tasks[{index}].P") for index, task in enumerate(task_payload)),
        reverse=True,
    )
    return sum(powers[:min(processors, len(powers))], Fraction(0))


def battery_values(task_payload: Sequence[Mapping[str, Any]], processors: int, kappa: Any) -> tuple:
    exact_kappa = _fraction(kappa, "kappa")
    if exact_kappa <= 0:
        raise ValueError("kappa must be positive")
    burst = burst_energy(task_payload, processors)
    capacity = exact_kappa * burst
    return capacity, capacity / 2


def materialized_float(value: Fraction) -> str:
    """Freeze the current projection's stable binary64 textual materialization."""

    return format(float(value), ".17g")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_solar_reference(
    system_template: Path, *, normalization_horizon_ms: int = NORMALIZATION_HORIZON_MS,
) -> Dict[str, Any]:
    """Project the scale=1 legacy real-solar source for exactly 60,000 ticks."""

    if normalization_horizon_ms != NORMALIZATION_HORIZON_MS:
        raise ValueError("B4 normalization horizon must remain 60000 ms")
    system_template = Path(system_template)
    document = yaml.safe_load(system_template.read_text(encoding="utf-8"))
    energy = document["energy_management"]
    if energy.get("use_real_solar_data") is not True:
        raise ValueError("B4 requires the real solar system projection")
    source = Path(str(energy["solar_data_file"]))
    if not source.is_absolute():
        source = system_template.parent / source
    if not source.is_file():
        raise ValueError(f"solar source is missing: {source}")
    efficiency = Fraction(str(energy["pv_efficiency"]))
    area = Fraction(str(energy["pv_area_m2"]))
    day_of_year = int(energy.get("day_of_year", 1))
    if not 1 <= day_of_year <= 366:
        raise ValueError("system template day_of_year is invalid")
    time_of_day_ms = int(energy["time_of_day_ms"])
    if not 0 <= time_of_day_ms < 24 * 60 * 60 * 1000:
        raise ValueError("system template time_of_day_ms is invalid")
    # Match EnergyConfig.start_offset_minutes: the real-solar source is a
    # year-long minute vector, not a one-day vector.
    phase_ms = (day_of_year - 1) * 24 * 60 * 60 * 1000 + time_of_day_ms
    values = []
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if row:
                value = Fraction(row[0].strip())
                values.append(max(value, Fraction(0)))
    if not values:
        raise ValueError("solar source is empty")
    total = Fraction(0)
    for tick in range(normalization_horizon_ms):
        irradiance = values[((phase_ms + tick) // 60000) % len(values)]
        total += irradiance * efficiency * area / 1000
    reference = total / normalization_horizon_ms
    if reference <= 0:
        raise ValueError("P_raw_ref must be positive")
    return {
        "reference_power_j_per_tick": reference,
        "solar_source_path": str(source.resolve()),
        "solar_source_hash": _sha256(source),
        "solar_phase_ms": phase_ms,
        "day_of_year": day_of_year,
        "time_of_day_ms": time_of_day_ms,
        "system_template_hash": _sha256(system_template),
        "raw_reference_pv_area_m2": area,
        "pv_efficiency": efficiency,
        "normalization_horizon_ms": normalization_horizon_ms,
    }


@dataclass(frozen=True)
class EnergyMaterial:
    contract_version: str
    taskset_semantic_hash: str
    kappa: str
    eta: str
    p_dem: str
    e_burst: str
    planned_battery: str
    planned_initial_energy: str
    materialized_battery: str
    materialized_initial_energy: str
    solar_source_hash: str
    solar_phase_ms: int
    solar_scale: str
    materialized_effective_pv_area: str
    normalization_horizon_ms: int
    system_template_hash: str
    power_contract_hash: str

    def material(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def identity(self) -> str:
        return energy_identity(self.material())


def build_energy_material(
    *, task_payload: Sequence[Mapping[str, Any]], taskset_semantic_hash: str,
    processors: int, kappa: Any, eta: Any, solar_reference: Mapping[str, Any],
    power_contract_hash: str,
) -> EnergyMaterial:
    exact_kappa = _fraction(kappa, "kappa")
    exact_eta = _fraction(eta, "eta")
    if exact_kappa <= 0 or exact_eta <= 0:
        raise ValueError("kappa and eta must be positive")
    demand = taskset_demand(task_payload)
    burst = burst_energy(task_payload, processors)
    capacity = exact_kappa * burst
    initial = capacity / 2
    raw_reference = _fraction(
        solar_reference["reference_power_j_per_tick"], "P_raw_ref"
    )
    if raw_reference <= 0:
        raise ValueError("P_raw_ref must be positive")
    scale = exact_eta * demand / raw_reference
    reference_area = _fraction(
        solar_reference["raw_reference_pv_area_m2"], "raw_reference_pv_area_m2"
    )
    return EnergyMaterial(
        contract_version=ENERGY_CONTRACT_VERSION,
        taskset_semantic_hash=str(taskset_semantic_hash),
        kappa=fraction_text(exact_kappa), eta=fraction_text(exact_eta),
        p_dem=fraction_text(demand), e_burst=fraction_text(burst),
        planned_battery=fraction_text(capacity),
        planned_initial_energy=fraction_text(initial),
        materialized_battery=materialized_float(capacity),
        materialized_initial_energy=materialized_float(initial),
        solar_source_hash=str(solar_reference["solar_source_hash"]),
        solar_phase_ms=int(solar_reference["solar_phase_ms"]),
        solar_scale=fraction_text(scale),
        materialized_effective_pv_area=materialized_float(reference_area * scale),
        normalization_horizon_ms=int(solar_reference["normalization_horizon_ms"]),
        system_template_hash=str(solar_reference["system_template_hash"]),
        power_contract_hash=str(power_contract_hash),
    )


def runner_energy_config(material: EnergyMaterial) -> Dict[str, Any]:
    return {
        "simulation_initial_battery": material.materialized_initial_energy,
        "battery_capacity": material.materialized_battery,
        "allow_harvest_clipping": True,
        "service_curve": {
            "solar_scale": material.solar_scale,
            "raw_reference_pv_area_m2": "1",
        },
    }
