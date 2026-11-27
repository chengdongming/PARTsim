from fractions import Fraction

from experiments.pipeline import scheduler_load_cross as scheduler
from experiments.pipeline.parallel_prepare import run_prepare_jobs






def test_scheduler_energy_preparation_serial_parallel_and_deduplicated(monkeypatch):
    calls = []

    def fake_energy(taskset, target_ue, raw_trace, *, kappa, normalization_horizon=60000):
        calls.append((target_ue, kappa))
        return {"target_ue": str(target_ue), "eta": str(1 / target_ue)}

    monkeypatch.setattr(scheduler, "energy_material", fake_energy)
    jobs = []
    for target_ue in (Fraction(2, 5), Fraction(4, 5)):
        for scheduler_name in ("A", "B", "C", "D", "E"):
            del scheduler_name
            jobs.append({
                "taskset_id": "t0", "target_ue": target_ue,
                "task_payload": ({"C": 1, "T": 2, "P": "1"},),
                "processors": 1, "task_count": 1, "raw_trace": (Fraction(1),),
                "kappa": Fraction(10),
            })
    unique = {(row["taskset_id"], str(row["target_ue"])): row for row in jobs}
    serial = run_prepare_jobs(
        unique.values(), scheduler.prepare_energy_material, workers=1,
        phase="test energy serial", key=lambda row: (row["taskset_id"], row["target_ue"]),
    )
    assert len(calls) == 2
    parallel = run_prepare_jobs(
        unique.values(), scheduler.prepare_energy_material, workers=2,
        phase="test energy parallel", key=lambda row: (row["taskset_id"], row["target_ue"]),
    )
    assert serial == parallel


def test_prepare_worker_failure_fails_closed():
    def fail(item):
        if item == 2:
            raise RuntimeError("generation failed")
        return item, item

    try:
        run_prepare_jobs(
            [1, 2, 3], fail, workers=1,
            phase="test failure", key=lambda row: row[0],
        )
    except RuntimeError as exc:
        assert "generation failed" in str(exc)
    else:
        raise AssertionError("preparation failure must not be swallowed")
