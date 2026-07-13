"""Source-audited generator/axis capability declaration for EXT-4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .config import (
    KNOWN_DEADLINE_MODES, KNOWN_POWER_MODES, KNOWN_PRIORITY_POLICIES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_FAMILIES = ("UUNIFAST_DISCARD",)


def audited_generator_capabilities() -> Dict[str, Any]:
    source = (PROJECT_ROOT / "global_task_generator.py").read_text(encoding="utf-8")
    required = ("class UUniFastDiscard", "random.randint(min_period, max_period)", "--constrained-deadlines")
    if any(marker not in source for marker in required):
        raise RuntimeError("generator source capability audit failed")
    return {
        "generator_families": list(GENERATOR_FAMILIES),
        "deadline_modes": sorted(KNOWN_DEADLINE_MODES),
        "period_range": "CONFIGURABLE_INTEGER_UNIFORM",
        "power_modes": sorted(KNOWN_POWER_MODES),
        "priority_policies": sorted(KNOWN_PRIORITY_POLICIES),
        "constrained_deadline_distribution": "generator_uniform_integer",
        "source": "global_task_generator.py",
    }
