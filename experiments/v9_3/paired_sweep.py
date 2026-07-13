"""Canonical identities for paired v9.3 parameter sweeps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence

from .config import canonical_json, domain_hash


@dataclass(frozen=True)
class Sweep:
    parameter_name: str
    level_encodings: tuple[str, ...]
    sweep_id: str

    def row(self) -> Dict[str, Any]:
        return {
            "sweep_id": self.sweep_id,
            "parameter_name": self.parameter_name,
            "ordered_parameter_levels": canonical_json(self.level_encodings),
        }


def make_sweep(experiment_id: str, parameter_name: str, levels: Sequence[Any]) -> Sweep:
    encodings = tuple(canonical_json(level) for level in levels)
    if not encodings or len(encodings) != len(set(encodings)):
        raise ValueError("paired sweep levels must be non-empty and unique")
    identity = domain_hash(
        "ASAP_BLOCK:V9.3:PAIRED_SENSITIVITY_SWEEP:v1",
        {
            "experiment_id": experiment_id,
            "parameter_name": parameter_name,
            "ordered_levels": encodings,
        },
    )
    return Sweep(parameter_name, encodings, identity)


def paired_analysis_id(
    sweep_id: str, base_taskset_hash: str, variant: str, left: str, right: str
) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:PAIRED_ANALYSIS:v1",
        {
            "sweep_id": sweep_id,
            "base_taskset_hash": base_taskset_hash,
            "variant": variant,
            "left": left,
            "right": right,
        },
    )
