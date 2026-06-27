import json
import os
import sys
from pathlib import Path


os.environ.setdefault("MPLBACKEND", "Agg")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


def test_trace_parser_extracts_max_completed_response_by_task(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps({
            "events": [
                {
                    "event_type": "end_instance",
                    "task_name": "task_1",
                    "arrival_time": "0",
                    "time": "5",
                },
                {
                    "event_type": "end_instance",
                    "task_name": "task_1",
                    "arrival_time": "10",
                    "time": "18",
                },
                {
                    "event_type": "completion",
                    "task_name": "task_2",
                    "arrival_time": 2,
                    "time": 6,
                },
                {"event_type": "end_instance", "task_name": "missing"},
                {
                    "event_type": "end_instance",
                    "task_name": "negative",
                    "arrival_time": 9,
                    "time": 8,
                },
                {
                    "event_type": "arrival",
                    "task_name": "unfinished",
                    "arrival_time": 20,
                    "time": 20,
                },
            ]
        }),
        encoding="utf-8",
    )

    assert acceptance.TraceParser(
        str(trace)
    ).get_max_response_times_by_task() == {
        "task_1": 8.0,
        "task_2": 4.0,
    }


def _proven_rta_result():
    return {
        "rta_status": "proven_under_assumptions",
        "rta_report": {
            "tasks": [
                {
                    "task_name": "task_1",
                    "proven": True,
                    "response_time_bound": 10,
                },
                {
                    "task_name": "task_2",
                    "proven_under_assumptions": True,
                    "response_time_bound": 6,
                },
            ]
        },
    }


def test_extract_rta_bounds_uses_only_valid_proven_tasks():
    result = _proven_rta_result()
    result["rta_report"]["tasks"].extend([
        {
            "task_name": "unproven",
            "proven": False,
            "response_time_bound": 20,
        },
        {"task_name": "missing_bound", "proven": True},
        {
            "task_name": "infinite",
            "proven": True,
            "response_time_bound": float("inf"),
        },
        {"task_name": "zero", "proven": True, "response_time_bound": 0},
    ])

    assert acceptance.extract_rta_bounds_by_task(result) == {
        "task_1": 10.0,
        "task_2": 6.0,
    }

    result["rta_status"] = "rta_unproven"
    assert acceptance.extract_rta_bounds_by_task(result) == {}


def test_compute_task_tightness_samples_pairs_tasks():
    result = _proven_rta_result()
    responses = {"task_1": 8, "task_2": 4}

    assert acceptance.compute_task_tightness_samples(
        acceptance.ASAP_BLOCK_ALGORITHM, result, responses
    ) == [1.25, 1.5]


def test_compute_task_tightness_samples_rejects_invalid_samples():
    result = _proven_rta_result()
    assert acceptance.compute_task_tightness_samples(
        acceptance.ASAP_BLOCK_ALGORITHM,
        result,
        {"task_1": 0},
    ) == []
    assert acceptance.compute_task_tightness_samples(
        "gpfp_asap_nonblock",
        result,
        {"task_1": 8, "task_2": 4},
    ) == []

    result["rta_status"] = "rta_unproven"
    assert acceptance.compute_task_tightness_samples(
        acceptance.ASAP_BLOCK_ALGORITHM,
        result,
        {"task_1": 8, "task_2": 4},
    ) == []


def test_rta_proven_status_alias_is_supported():
    result = _proven_rta_result()
    result["rta_status"] = "rta_proven"
    assert acceptance.compute_task_tightness_samples(
        acceptance.ASAP_BLOCK_ALGORITHM,
        result,
        {"task_1": 8, "task_2": 4},
    ) == [1.25, 1.5]
