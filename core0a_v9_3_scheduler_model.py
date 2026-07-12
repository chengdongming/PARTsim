"""Independent exact-tick ASAP-BLOCK scheduler state machine for CORE-0A."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass
class ModelJob:
    job_id: str
    task_id: str
    priority_rank: int
    release: int
    wcet: int
    power: Fraction
    candidate: int
    remaining: int
    release_energy: Fraction
    certificate_satisfied: bool
    completion: Optional[int] = None
    processor_blocking_ticks: int = 0
    energy_blocking_ticks: int = 0


class ASAPBlockTickModel:
    """A small, explicit transition system independent of all RTA helpers."""

    def __init__(self, processors: int, e0: Fraction):
        self.processors = processors
        self.e0 = Fraction(e0)
        self.energy = Fraction(e0)
        self.jobs: List[ModelJob] = []
        self._completed_at: Dict[int, List[str]] = {}

    def step(
        self,
        tick: int,
        releases: Sequence[Mapping[str, object]],
        harvest_credit: Fraction,
    ) -> Dict[str, object]:
        start_energy = self.energy
        completion_events = sorted(self._completed_at.pop(tick, []))
        release_ids = []
        for release in releases:
            job = ModelJob(
                job_id=str(release["job_id"]),
                task_id=str(release["task_id"]),
                priority_rank=int(release["priority_rank"]),
                release=tick,
                wcet=int(release["wcet"]),
                power=Fraction(release["power"]),
                candidate=int(release["candidate"]),
                remaining=int(release["wcet"]),
                release_energy=start_energy,
                certificate_satisfied=start_energy >= self.e0,
            )
            self.jobs.append(job)
            release_ids.append(job.job_id)

        eligible = []
        ranks = sorted({job.priority_rank for job in self.jobs})
        for rank in ranks:
            pending = [
                job
                for job in self.jobs
                if job.priority_rank == rank and job.remaining > 0
            ]
            if pending:
                eligible.append(min(pending, key=lambda job: (job.release, job.job_id)))

        scan_order = []
        selected = []
        available = start_energy
        stopped_job = None
        for job in eligible:
            if len(selected) >= self.processors:
                break
            scan_order.append(job.job_id)
            if job.power > available:
                stopped_job = job
                break
            selected.append(job)
            available -= job.power

        for job in selected:
            job.remaining -= 1
            if job.remaining == 0:
                job.completion = tick + 1
                self._completed_at.setdefault(tick + 1, []).append(job.job_id)

        processor_blocked = []
        energy_blocked = []
        for job in eligible:
            if job in selected or job.remaining == 0:
                continue
            higher_selected = sum(
                other.priority_rank < job.priority_rank for other in selected
            )
            if higher_selected >= self.processors:
                job.processor_blocking_ticks += 1
                processor_blocked.append(job.job_id)
            elif stopped_job is not None:
                job.energy_blocking_ticks += 1
                energy_blocked.append(job.job_id)

        consumed = start_energy - available
        self.energy = available + Fraction(harvest_credit)
        return {
            "tick": tick,
            "start_energy": start_energy,
            "completion_events": completion_events,
            "release_events": sorted(release_ids),
            "eligible_hol": [job.job_id for job in eligible],
            "scan_order": scan_order,
            "execution_set": [job.job_id for job in selected],
            "energy_consumed": consumed,
            "harvest_credit": Fraction(harvest_credit),
            "post_tick_energy": self.energy,
            "processor_blocked_jobs": processor_blocked,
            "energy_blocked_jobs": energy_blocked,
        }
