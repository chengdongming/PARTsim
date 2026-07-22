import json
from fractions import Fraction
from types import SimpleNamespace

from experiments.v9_3.performance_audit import audit_retained_trace_sample
from experiments.v9_3.performance_engine import PerformanceRequest, _runner_simulation_config
from experiments.v9_3.performance_outcome import evaluate_simulation_result
from experiments.v9_3.simulation_engine import trace_retention_statuses
from experiments.v9_3.simulation_result import SimulationStatus, parse_simulation_trace
from v9_3_b4_helpers import config
from v9_3_core3_helpers import task_payload, write_trace


def request(retain=False):
    return PerformanceRequest(
        "r", "x", "t", "th", "ph", "pow", "rel", "1/10", 0,
        "transition", "e", {}, "gpfp_asap_block", 1000, "c", retain,
    )


def test_job_mode_and_b4_retention_do_not_treat_miss_as_trace_failure():
    value = _runner_simulation_config(config(), request(), 30)
    assert value["trace_mode"] == "job"
    assert value["retain_trace_statuses"] == [SimulationStatus.INTERNAL_ERROR.value]
    assert SimulationStatus.DEADLINE_MISS.value not in trace_retention_statuses(value)


def test_legacy_trace_retention_default_is_unchanged():
    statuses = trace_retention_statuses({})
    assert statuses == {SimulationStatus.DEADLINE_MISS.value, SimulationStatus.INTERNAL_ERROR.value}


class _SyntheticStore:
    def __init__(self, taskset):
        self.taskset = taskset

    def verify_manifest(self):
        return {"entries": [{
            "taskset_semantic_hash": self.taskset.taskset_semantic_hash,
            "utilization": "1/10", "taskset_index": 0,
        }]}

    def load(self, _utilization, _index):
        return self.taskset


def test_retained_trace_is_independently_reparsed_and_outcome_recomputed(tmp_path):
    taskset_hash = "a" * 64
    tasks = tuple(task_payload())
    trace = write_trace(
        tmp_path / "retained.json", horizon=10, completion=2,
        taskset_hash=taskset_hash, c=2, d=5, t=10,
    )
    parsed = parse_simulation_trace(
        trace, tasks, expected_taskset_hash=taskset_hash,
        horizon=10, warmup=0, minimum_jobs_per_task=1,
        release_e0=Fraction(0), expected_scheduler="gpfp_asap_block",
        expected_processors=1,
    )
    outcome = evaluate_simulation_result(
        parsed, tasks, horizon_ms=10, warmup_ms=0,
        minimum_jobs_per_task=1, processors=1,
    ).row()
    taskset = SimpleNamespace(
        taskset_semantic_hash=taskset_hash, power_hash="power", tasks=tasks,
    )
    plan = {"requests": [{
        "semantic_request_id": "request", "retain_trace": True,
        "taskset_semantic_hash": taskset_hash, "power_hash": "power",
        "runtime_horizon_ms": 10, "scheduler_id": "gpfp_asap_block",
    }]}
    result = {
        "semantic_request_id": "request",
        "taskset_semantic_hash": taskset_hash, "power_hash": "power",
        "runtime_horizon_ms": 10, "scheduler_id": "gpfp_asap_block",
        "retained_trace_path": str(trace), "outcome": outcome,
    }
    audit_config = {
        "simulation": {"warmup_ms": 0, "minimum_adjudicable_jobs_per_task": 1},
        "platform": {"cores": 1},
    }
    closed = audit_retained_trace_sample(
        audit_config, plan, [result], _SyntheticStore(taskset),
    )
    assert closed == {
        "sampled_trace_requests": 1, "audited_trace_requests": 1,
        "missing_sampled_trace": 0, "trace_outcome_mismatch": 0,
        "trace_identity_mismatch": 0,
    }

    mismatch = {**result, "outcome": {**outcome, "missed_jobs": 1}}
    assert audit_retained_trace_sample(
        audit_config, plan, [mismatch], _SyntheticStore(taskset),
    )["trace_outcome_mismatch"] == 1
    missing = {**result, "retained_trace_path": str(tmp_path / "missing.json")}
    assert audit_retained_trace_sample(
        audit_config, plan, [missing], _SyntheticStore(taskset),
    )["missing_sampled_trace"] == 1
    wrong_plan = {"requests": [{**plan["requests"][0], "scheduler_id": "gpfp_asap_nonblock"}]}
    assert audit_retained_trace_sample(
        audit_config, wrong_plan, [result], _SyntheticStore(taskset),
    )["trace_identity_mismatch"] == 1

    wrong_power_document = json.loads(trace.read_text(encoding="utf-8"))
    scheduled = next(
        event for event in wrong_power_document["events"]
        if event["event_type"] == "scheduled"
    )
    scheduled["task_unit_energy_mJ"] = 200.0
    wrong_power_trace = tmp_path / "wrong-power.json"
    wrong_power_trace.write_text(json.dumps(wrong_power_document), encoding="utf-8")
    wrong_power = {**result, "retained_trace_path": str(wrong_power_trace)}
    assert audit_retained_trace_sample(
        audit_config, plan, [wrong_power], _SyntheticStore(taskset),
    )["trace_identity_mismatch"] == 1


def test_release_energy_below_planned_initial_does_not_trigger_b4_bulk_retention(tmp_path):
    taskset_hash = "b" * 64
    tasks = tuple(task_payload())
    trace = write_trace(
        tmp_path / "miss.json", horizon=10, completion=None,
        deadline_miss=True, energy_j=20.0, taskset_hash=taskset_hash,
        c=2, d=5, t=10,
    )
    parsed = parse_simulation_trace(
        trace, tasks, expected_taskset_hash=taskset_hash,
        horizon=10, warmup=0, minimum_jobs_per_task=1,
        release_e0=Fraction(0), expected_scheduler="gpfp_asap_block",
        expected_processors=1,
    )
    assert parsed.release_e0_valid is True
    assert parsed.minimum_release_energy_j < 250  # a representative planned B/2
    b4_config = _runner_simulation_config(config(), request(retain=False), 30)
    assert parsed.status.value not in trace_retention_statuses(b4_config)
