"""Audited registry for the nine energy-aware GPFP schedulers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FACTORY_SOURCE = "librtsim/system.cpp"


@dataclass(frozen=True)
class SchedulerRegistration:
    scheduler_id: str
    display_name: str
    implementation_id: str
    timing_family: str
    mechanism: str
    factory_source: str
    implementation_source: str

    def row(self) -> Dict[str, str]:
        return asdict(self)


def _entry(timing: str, mechanism: str) -> SchedulerRegistration:
    lower_timing = timing.lower()
    lower_mechanism = mechanism.lower()
    scheduler_id = f"gpfp_{lower_timing}_{lower_mechanism}"
    display = f"{timing}-{mechanism.title() if mechanism != 'NONBLOCK' else 'NonBlock'}"
    implementation = f"GPFP{timing}{mechanism.title() if mechanism != 'NONBLOCK' else 'NonBlock'}Scheduler"
    return SchedulerRegistration(
        scheduler_id=scheduler_id,
        display_name=display,
        implementation_id=implementation,
        timing_family=timing,
        mechanism=mechanism,
        factory_source=FACTORY_SOURCE,
        implementation_source=f"librtsim/scheduler/{scheduler_id}_scheduler.cpp",
    )


SCHEDULERS: Tuple[SchedulerRegistration, ...] = tuple(
    _entry(timing, mechanism)
    for timing in ("ASAP", "ALAP", "ST")
    for mechanism in ("BLOCK", "NONBLOCK", "SYNC")
)
SCHEDULER_IDS = tuple(item.scheduler_id for item in SCHEDULERS)


def audited_scheduler_registry(project_root: Path = PROJECT_ROOT) -> Tuple[SchedulerRegistration, ...]:
    """Fail closed unless source registration and implementation files agree."""

    if len(SCHEDULER_IDS) != 9 or len(set(SCHEDULER_IDS)) != 9:
        raise RuntimeError("nine scheduler registrations must be unique")
    factory_text = (project_root / FACTORY_SOURCE).read_text(encoding="utf-8")
    for item in SCHEDULERS:
        expected = f'if (name == "{item.scheduler_id}")'
        if expected not in factory_text or item.implementation_id not in factory_text:
            raise RuntimeError(f"scheduler factory audit failed for {item.scheduler_id}")
        if not (project_root / item.implementation_source).is_file():
            raise RuntimeError(f"scheduler implementation missing: {item.implementation_source}")
    return SCHEDULERS


def scheduler_by_id() -> Dict[str, SchedulerRegistration]:
    return {item.scheduler_id: item for item in audited_scheduler_registry()}
