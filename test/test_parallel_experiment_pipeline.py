from fractions import Fraction

from experiments.v9_3 import rta_load_cross as rta
from experiments.v9_3 import scheduler_load_cross as scheduler
from experiments.v9_3.parallel_prepare import run_prepare_jobs


def test_rta_fixed_scale_preparation_is_exactly_order_independent(monkeypatch):
    def fake_skeleton(**kwargs):
        return ({"name": "task_0", "priority": 0, "C": 1, "D": 2, "T": 4, "workload": "hash", "energy_per_tick": "1"},)

    def fake_scale(skeleton, **kwargs):
        return {
            "taskset_id": f"{kwargs['target_uc']}-{kwargs['generation_index']}",
            "target_uc": str(kwargs["target_uc"]), "actual_uc": str(kwargs["target_uc"]),
            "target_ue": None, "actual_ue": "0", "generation_index": kwargs["generation_index"],
            "tasks": list(skeleton), "energy_scale": str(kwargs["energy_scale"]),
        }

    monkeypatch.setattr(rta, "generate_cpu_skeleton", fake_skeleton)
    monkeypatch.setattr(rta, "scale_skeleton_fixed_energy_scale", fake_scale)
    jobs = [
        {
            "seed": 7, "target_uc": Fraction(uc, 10), "generation_index": index,
            "processors": 4, "tasks": 1, "period_min": 4, "period_max": 8,
            "min_task_util": Fraction(1, 100), "max_task_util": Fraction(4, 5),
            "tolerance": Fraction(1, 100), "system_config": "system.yml",
            "rho": Fraction(11, 2), "base_energies": {"hash": Fraction(1)},
            "energy_scale": Fraction(3, 2),
        }
        for uc in (1, 2) for index in (0, 1)
    ]
    serial = run_prepare_jobs(
        jobs, rta.prepare_fixed_scale_taskset, workers=1,
        phase="test fixed serial", key=lambda row: (row["target_uc"], row["generation_index"]),
    )
    parallel = run_prepare_jobs(
        jobs, rta.prepare_fixed_scale_taskset, workers=2,
        phase="test fixed parallel", key=lambda row: (row["target_uc"], row["generation_index"]),
    )
    assert serial == parallel
    serial_requests = rta.make_requests(
        [row["taskset"] for row in serial.values()], [Fraction(37)], ["CW"],
        4, Fraction(11, 2), Fraction(2, 5), 1.0,
    )
    parallel_requests = rta.make_requests(
        [row["taskset"] for row in parallel.values()], [Fraction(37)], ["CW"],
        4, Fraction(11, 2), Fraction(2, 5), 1.0,
    )
    assert [row["request_id"] for row in serial_requests] == [
        row["request_id"] for row in parallel_requests
    ]


def test_rta_load_cross_preparation_reuses_one_skeleton_per_uc(monkeypatch):
    calls = []

    def fake_skeleton(**kwargs):
        calls.append(kwargs["target_uc"])
        return ({"name": "task_0", "priority": 0, "C": 1, "D": 2, "T": 4, "workload": "hash"},)

    def fake_scale(skeleton, **kwargs):
        return {
            "taskset_id": f"{kwargs['target_uc']}-{kwargs['generation_index']}-{kwargs['target_ue']}",
            "target_uc": str(kwargs["target_uc"]), "target_ue": str(kwargs["target_ue"]),
            "tasks": list(skeleton),
        }

    monkeypatch.setattr(rta, "generate_cpu_skeleton", fake_skeleton)
    monkeypatch.setattr(rta, "scale_skeleton", fake_scale)
    job = {
        "seed": 7, "target_uc": Fraction(1, 10), "generation_index": 0,
        "target_ues": [Fraction(2, 5), Fraction(4, 5)], "processors": 4,
        "tasks": 1, "period_min": 4, "period_max": 8,
        "min_task_util": Fraction(1, 100), "max_task_util": Fraction(4, 5),
        "tolerance": Fraction(1, 100), "system_config": "system.yml",
        "rho": Fraction(11, 2), "base_energies": {"hash": Fraction(1)},
    }
    result = rta.prepare_load_cross_group(job)
    assert calls == [Fraction(1, 10)]
    assert [row["target_ue"] for row in result["tasksets"]] == ["2/5", "4/5"]


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
