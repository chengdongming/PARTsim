from experiments.v9_3.performance_engine import PerformanceRequest, _runner_simulation_config
from experiments.v9_3.simulation_engine import trace_retention_statuses
from experiments.v9_3.simulation_result import SimulationStatus
from v9_3_b4_helpers import config


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
