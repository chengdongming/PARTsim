from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest

import experiments.v9_3.performance_engine as engine
from experiments.v9_3.performance_config import semantic_config_material
from experiments.v9_3.performance_engine import (
    PerformanceRequest, PerformanceRequestExecutionError,
    _execute_request_batch,
)
from experiments.v9_3.performance_environment import stage_scientific_config_hash
from experiments.v9_3.performance_identity import semantic_config_hash
from experiments.v9_3.result_writer import atomic_write_json
from v9_3_b4_helpers import config


def _requests(count=12):
    return tuple(
        PerformanceRequest(
            semantic_request_id=f"{index:064x}",
            execution_identity=f"execution-{index}",
            taskset_id=f"taskset-{index}",
            taskset_semantic_hash=f"taskset-hash-{index}",
            priority_hash=f"priority-{index}", power_hash=f"power-{index}",
            release_hash=f"release-{index}", u_norm="1/10",
            taskset_index=index, energy_condition="transition",
            energy_identity=f"energy-{index}",
            energy_material={"index": index}, scheduler_id="gpfp_asap_block",
            runtime_horizon_ms=1000,
            simulation_semantic_config_hash="semantic-config", retain_trace=False,
        )
        for index in range(count)
    )


def _execution_config(*, workers, resume=False, checkpoint=3):
    return {"execution": {
        "worker_count": workers, "checkpoint_every": checkpoint,
        "resume": resume,
    }}


def _tasksets(requests):
    return {request.taskset_id: object() for request in requests}


def _terminal(request):
    return {
        "schema": "ASAP_BLOCK_V9_3_B4_TERMINAL_RESULT_V1",
        **request.row(), "terminal": True, "stable_payload": request.taskset_index,
    }


def _terminal_path(root: Path, request: PerformanceRequest) -> Path:
    return root / "terminal_results" / f"{request.semantic_request_id}.json"


def test_worker_count_one_matches_reference_serial_terminal_json(monkeypatch, tmp_path):
    requests = _requests(6)
    calls = []

    def fake_execute(_config, request, _taskset, _root):
        calls.append((request.semantic_request_id, threading.current_thread().name))
        return _terminal(request)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    reference = tmp_path / "reference"
    for request in requests:
        atomic_write_json(_terminal_path(reference, request), fake_execute(None, request, None, reference))
    calls.clear()
    actual = tmp_path / "actual"
    summary = _execute_request_batch(
        _execution_config(workers=1), requests, _tasksets(requests), actual,
    )
    assert summary["configured_worker_count"] == 1
    assert summary["completed_this_invocation"] == len(requests)
    assert [request_id for request_id, _thread in calls] == [
        request.semantic_request_id for request in requests
    ]
    assert all(thread == threading.current_thread().name for _request, thread in calls)
    for request in requests:
        assert _terminal_path(actual, request).read_bytes() == _terminal_path(reference, request).read_bytes()


def test_four_workers_execute_twelve_once_with_parent_only_single_write(monkeypatch, tmp_path):
    requests = _requests()
    calls = Counter()
    writes = Counter()
    lock = threading.Lock()
    real_atomic_write = engine.atomic_write_json

    def fake_execute(_config, request, _taskset, _root):
        time.sleep((11 - request.taskset_index) * 0.001)
        with lock:
            calls[request.semantic_request_id] += 1
        return _terminal(request)

    def counted_write(path, document):
        path = Path(path)
        if path.parent.name == "terminal_results":
            writes[path.name] += 1
            assert threading.current_thread() is threading.main_thread()
        return real_atomic_write(path, document)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    monkeypatch.setattr(engine, "atomic_write_json", counted_write)
    summary = _execute_request_batch(
        _execution_config(workers=4), requests, _tasksets(requests), tmp_path,
    )
    assert summary["completed_this_invocation"] == 12
    assert summary["resumed_valid_results"] == 0
    assert calls == Counter({request.semantic_request_id: 1 for request in requests})
    assert set(writes.values()) == {1} and len(writes) == 12
    assert len(list((tmp_path / "terminal_results").glob("*.json"))) == 12


def test_parallel_completion_order_does_not_change_requests_or_terminals(monkeypatch, tmp_path):
    requests = _requests(8)
    frozen_rows = [request.row() for request in requests]
    direction = {"reverse": False}

    def fake_execute(_config, request, _taskset, _root):
        delay_index = 7 - request.taskset_index if direction["reverse"] else request.taskset_index
        time.sleep(delay_index * 0.001)
        return _terminal(request)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    first, second = tmp_path / "first", tmp_path / "second"
    _execute_request_batch(_execution_config(workers=4), requests, _tasksets(requests), first)
    direction["reverse"] = True
    _execute_request_batch(_execution_config(workers=4), requests, _tasksets(requests), second)
    assert [request.row() for request in requests] == frozen_rows
    for request in requests:
        assert _terminal_path(first, request).read_bytes() == _terminal_path(second, request).read_bytes()


def test_resume_only_schedules_missing_requests(monkeypatch, tmp_path):
    requests = _requests(12)
    for request in requests[:5]:
        atomic_write_json(_terminal_path(tmp_path, request), _terminal(request))
    calls = []

    def fake_execute(_config, request, _taskset, _root):
        calls.append(request.semantic_request_id)
        return _terminal(request)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    summary = _execute_request_batch(
        _execution_config(workers=4, resume=True),
        requests, _tasksets(requests), tmp_path,
    )
    assert summary["resumed_valid_results"] == 5
    assert summary["completed_this_invocation"] == 7
    assert Counter(calls) == Counter({
        request.semantic_request_id: 1 for request in requests[5:]
    })


def test_max_requests_selects_from_pending_not_full_plan(monkeypatch, tmp_path):
    requests = _requests(8)
    for request in requests[:3]:
        atomic_write_json(_terminal_path(tmp_path, request), _terminal(request))
    calls = []

    def fake_execute(_config, request, _taskset, _root):
        calls.append(request.semantic_request_id)
        return _terminal(request)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    summary = _execute_request_batch(
        _execution_config(workers=1, resume=True),
        requests, _tasksets(requests), tmp_path, max_requests=2,
    )
    assert summary["selected_requests"] == 2
    assert calls == [request.semantic_request_id for request in requests[3:5]]


@pytest.mark.parametrize("existing", ("corrupt", "identity", "resume_false"))
def test_existing_terminal_evidence_fails_closed(monkeypatch, tmp_path, existing):
    request = _requests(1)[0]
    path = _terminal_path(tmp_path, request)
    path.parent.mkdir(parents=True, exist_ok=True)
    if existing == "corrupt":
        path.write_text("{partial", encoding="utf-8")
        resume = True
    else:
        document = _terminal(request)
        if existing == "identity":
            document["execution_identity"] = "wrong"
        atomic_write_json(path, document)
        resume = existing != "resume_false"
    calls = []
    monkeypatch.setattr(
        engine, "execute_request",
        lambda *_args: calls.append("called"),
    )
    with pytest.raises(PerformanceRequestExecutionError) as captured:
        _execute_request_batch(
            _execution_config(workers=4, resume=resume),
            (request,), _tasksets((request,)), tmp_path,
        )
    assert captured.value.failed_request_id == request.semantic_request_id
    assert calls == []


def test_worker_failure_keeps_atomic_results_recoverable(monkeypatch, tmp_path):
    requests = _requests(12)
    failing_id = requests[2].semantic_request_id
    fail = {"enabled": True}

    def fake_execute(_config, request, _taskset, _root):
        time.sleep(request.taskset_index * 0.001)
        if fail["enabled"] and request.semantic_request_id == failing_id:
            raise RuntimeError("synthetic worker failure")
        return _terminal(request)

    monkeypatch.setattr(engine, "execute_request", fake_execute)
    with pytest.raises(PerformanceRequestExecutionError) as captured:
        _execute_request_batch(
            _execution_config(workers=4), requests, _tasksets(requests), tmp_path,
        )
    assert captured.value.failed_request_id == failing_id
    landed = list((tmp_path / "terminal_results").glob("*.json"))
    for path in landed:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["terminal"] is True
        assert document["semantic_request_id"] == path.stem

    fail["enabled"] = False
    summary = _execute_request_batch(
        _execution_config(workers=4, resume=True),
        requests, _tasksets(requests), tmp_path,
    )
    assert summary["resumed_valid_results"] == len(landed)
    assert summary["completed_this_invocation"] == 12 - len(landed)
    assert len(list((tmp_path / "terminal_results").glob("*.json"))) == 12


def test_worker_count_is_excluded_from_scientific_and_request_identity():
    serial = config()
    parallel = deepcopy(serial)
    serial["execution"]["worker_count"] = 1
    parallel["execution"]["worker_count"] = 24
    assert stage_scientific_config_hash(serial) == stage_scientific_config_hash(parallel)
    assert semantic_config_material(serial) == semantic_config_material(parallel)
    assert semantic_config_hash(semantic_config_material(serial)) == semantic_config_hash(
        semantic_config_material(parallel)
    )


def test_checked_in_autodl_worker_counts_are_execution_only():
    assert config("v9_3_b4_calibration_r1.yaml")["execution"]["worker_count"] == 24
    assert config("v9_3_b4_horizon_gate_r1.yaml")["execution"]["worker_count"] == 24
    assert config("v9_3_b4_formal_template_r1.yaml")["execution"]["worker_count"] == 24
    assert config("v9_3_b4_smoke.yaml")["execution"]["worker_count"] == 4
