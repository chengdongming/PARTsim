import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from experiments.v9_3.performance_energy import build_energy_material
from experiments.v9_3.performance_engine import PerformanceRequest, execute_request
from experiments.v9_3.performance_outcome import PERF_OUTCOME_VERSION
from experiments.v9_3.performance_taskset_store import PerformanceTaskset
from experiments.v9_3.simulation_result import SimulationStatus
from v9_3_b4_helpers import config, task_payload


def test_job_trace_feasibility_evidence_if_present():
    path = __import__("pathlib").Path("/tmp/b4_job_trace_recovery1.json")
    if not path.is_file():
        return
    trace = json.loads(path.read_text(encoding="utf-8"))
    kinds = {row["event_type"] for row in trace["events"]}
    assert {"arrival", "scheduled", "end_instance", "simulation_run_outcome"}.issubset(kinds)
    assert trace["simulation_completion_reason"] == "reached_horizon"
    assert PERF_OUTCOME_VERSION == "PERF_OUTCOME_V2"


def test_b4_runner_uses_zero_release_certificate_but_keeps_actual_half_battery(monkeypatch, tmp_path):
    tasks = task_payload()
    reference = {
        "reference_power_j_per_tick": Fraction(2),
        "solar_source_hash": "solar", "solar_phase_ms": 7,
        "raw_reference_pv_area_m2": Fraction(1),
        "normalization_horizon_ms": 60000,
        "system_template_hash": "template",
    }
    material = build_energy_material(
        task_payload=tasks, taskset_semantic_hash="taskset", processors=4,
        kappa="50", eta="1", solar_reference=reference,
        power_contract_hash="power",
    )
    request = PerformanceRequest(
        "request", "execution", "taskset-id", "taskset", "priority",
        "power", "release", "1/10", 0, "transition", material.identity,
        material.material(), "gpfp_asap_block", 1000, "semantic", False,
    )
    taskset = PerformanceTaskset(
        "taskset-id", "taskset", "priority", "power", "release",
        "1/10", 0, 1, "1/10", "1", "1", tuple(tasks), Path("unused"),
    )
    jobs = tuple(SimpleNamespace(
        task_id=str(index), release=0, absolute_deadline=int(task["D"]),
        completion=1, deadline_miss=False,
    ) for index, task in enumerate(tasks))
    parsed = SimpleNamespace(
        status=SimulationStatus.PASS_OBSERVED, reason="pass", jobs=jobs,
        simulation_completed=True, completion_reason="reached_horizon",
    )
    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            result=parsed, runtime_seconds=0.01, retained_trace_path=None,
            stdout_tail="", stderr_tail="",
        )

    monkeypatch.setattr("experiments.v9_3.performance_engine.run_paired_simulation", fake_runner)
    smoke = config()
    terminal = execute_request(smoke, request, taskset, tmp_path)
    assert captured["exact_e0"] == 0
    assert captured["energy_config"]["simulation_initial_battery"] == material.materialized_initial_energy
    assert Fraction(material.planned_initial_energy) == Fraction(material.planned_battery) / 2
    assert terminal["rta_release_e0_certificate"] == "NOT_APPLICABLE"
    assert terminal["runner_release_e0_value"] == "0"
    assert terminal["planned_initial_energy"] == material.planned_initial_energy
