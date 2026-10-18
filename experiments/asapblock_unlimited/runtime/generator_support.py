"""Original seed and exact energy-model helpers, without scheduler imports."""
from fractions import Fraction
from pathlib import Path
import hashlib
import yaml
FROZEN_WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")

def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"

def stable_seed(base_seed: int, processors: int, tasks: int, target_uc: Fraction, generation_index: int) -> int:
    material = f"{base_seed}|{processors}|{tasks}|{fraction_text(target_uc)}|{generation_index}"
    return int.from_bytes(hashlib.sha256(material.encode("ascii")).digest()[:8], "big")

def _load_exact_energy_model(config_path: Path) -> dict[str, Fraction]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = config["energy_management"]["scheduler_energy_model"]
        base_power = Fraction(str(model["base_power"]))
        ratio = Fraction(str(model["frequency_power_ratios"][8100]))
        coefficients = model["workload_coefficients"]
        return {
            workload: base_power * Fraction(str(coefficients[workload])) * ratio
            for workload in FROZEN_WORKLOADS
        }
    except (KeyError, OSError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"cannot read exact scheduler energy model from {config_path}") from exc
