from __future__ import annotations

from fractions import Fraction

from experiments.v9_3.core5a_standardized_timing import (
    BASE_E0,
    BASE_LATENCY,
    CORE5A_SCALED_E0_V1,
    CORE5A_SCALED_LATENCY_SERVICE_V1,
    MEASURED_REPETITIONS,
    REPETITIONS,
    TIMING_METHODS,
    exact_service_curve,
    method_order,
    plan_rows,
    plan_summary,
    scaled_e0,
    taskset_v4,
    timing_points,
)
from experiments.v9_3.taskset_store import StoredTaskset


def test_protocol_has_frozen_16_point_axes_and_exact_semantics():
    points = timing_points()
    assert len(points) == 16
    assert [row.axis_value for row in points[:6]] == [5, 8, 10, 12, 16, 20]
    assert [row.axis_value for row in points[6:12]] == [2, 3, 4, 6, 8, 10]
    assert [row.axis_value for row in points[12:]] == [1, 2, 3, 4]
    assert all(row.target_total_utilization == Fraction(2) for row in points[:6])
    assert all(row.target_normalized_utilization == Fraction(2, 5) for row in points[6:12])
    assert all(row.target_total_utilization == Fraction(2) for row in points[12:])
    assert CORE5A_SCALED_E0_V1 == "CORE5A_SCALED_E0_V1"
    assert CORE5A_SCALED_LATENCY_SERVICE_V1 == "CORE5A_SCALED_LATENCY_SERVICE_V1"
    assert scaled_e0(points[15]) == BASE_E0 * 4
    curve = exact_service_curve(points[15])
    assert curve.rate == Fraction(11, 2)
    assert curve.latency == BASE_LATENCY * 4


def test_plan_has_640_math_requests_1920_executions_and_warmup_exclusion():
    summary = plan_summary()
    rows = plan_rows()
    assert summary["grid_points"] == 16
    assert summary["mathematical_requests"] == 640
    assert summary["executions"] == 1920
    assert summary["warmup_executions"] == 640
    assert summary["measured_executions"] == 1280
    assert len({row["execution_id"] for row in rows}) == 1920
    assert len({row["mathematical_request_id"] for row in rows}) == 640
    assert all(row["measurement_class"] == ("WARMUP" if row["repetition"] == 0 else "MEASURED") for row in rows)
    assert all(row["repetition"] in REPETITIONS for row in rows)
    assert set(MEASURED_REPETITIONS) == {1, 2}


def test_deterministic_taskset_pairing_rotates_methods_without_result_dependency():
    assert method_order(0) == TIMING_METHODS
    assert method_order(1) == TIMING_METHODS[1:] + TIMING_METHODS[:1]
    assert method_order(4) == TIMING_METHODS
    rows = [
        row for row in plan_rows()
        if row["axis"] == "task_count" and row["axis_value"] == 5
        and row["taskset_index"] == 3 and row["repetition"] == 0
    ]
    assert [row["method"] for row in rows[:4]] == list(method_order(3))
    assert len({row["mathematical_request_id"] for row in rows}) == 4


def test_a3_scales_task_timing_inputs_but_not_power():
    point = timing_points()[13]  # A3 time_scale=2
    payload = tuple({"C": 3, "D": 7, "T": 10, "P": "5/2", "arrival_offset": 0} for _ in range(2))
    stored = StoredTaskset(
        taskset_id="stored", generation_id="generation", taskset_index=0,
        seed=9, semantic_hash="semantic", priority_hash="priority",
        power_hash="power", target_utilization=Fraction(1, 2),
        actual_utilization=Fraction(1, 2), processors=4, task_count=2,
        deadline_mode="constrained", tasks=(), task_payload=payload,
        generation_seconds=0.0, service_curve_reference="service", canonical_path=None,
    )
    taskset = taskset_v4(stored, point)
    assert [(task.C, task.D, task.T, task.power) for task in taskset.tasks] == [
        (6, 14, 20, "5/2"), (6, 14, 20, "5/2")
    ]


def test_no_retry_is_encoded_in_plan_contract():
    rows = plan_rows()
    assert {row["timeout_seconds"] for row in rows} == {1200}
    assert all(row["execution_id"] != row["mathematical_request_id"] for row in rows)
