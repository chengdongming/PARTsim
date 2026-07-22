from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

import experiments.v9_3.performance_engine as engine
from experiments.v9_3.performance_config import semantic_config_material
from experiments.v9_3.performance_engine import PerformanceRequest, build_requests
from experiments.v9_3.performance_environment import stage_scientific_config_hash
from experiments.v9_3.performance_identity import semantic_config_hash
from experiments.v9_3.performance_taskset_store import PerformanceTaskset
from experiments.v9_3.result_writer import atomic_write_json
from v9_3_b4_helpers import PROJECT_ROOT, config, task_payload


CLI_PATH = PROJECT_ROOT / "scripts/run_v9_3_b4_performance.py"
SMOKE_CONFIG = PROJECT_ROOT / "configs/v9_3_b4_smoke.yaml"
B4_CONFIGS = (
    "v9_3_b4_calibration_r1.yaml",
    "v9_3_b4_horizon_gate_r1.yaml",
    "v9_3_b4_formal_template_r1.yaml",
    "v9_3_b4_smoke.yaml",
)


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("run_v9_3_b4_performance", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _requests(count=9):
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


def _terminal(request):
    return {
        "schema": "ASAP_BLOCK_V9_3_B4_TERMINAL_RESULT_V1",
        **request.row(), "terminal": True, "stable_payload": request.taskset_index,
    }


def _terminal_path(root, request):
    return Path(root) / "terminal_results" / f"{request.semantic_request_id}.json"


def _invoke_execute(cli, monkeypatch, capsys, output_root, execute_plan, *, resume):
    argv = [
        str(CLI_PATH), "--config", str(SMOKE_CONFIG), "--execute",
        "--output-root", str(output_root),
        "--taskset-store", str(output_root / "unused-store"),
    ]
    if resume:
        argv.append("--resume")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "PerformanceTasksetStore", lambda *_args: object())
    monkeypatch.setattr(cli, "execute_plan", execute_plan)
    assert cli.main() == 0
    return json.loads(capsys.readouterr().out)


def test_checked_in_configs_and_default_cli_execution_remain_resume_false(
    cli, monkeypatch, capsys, tmp_path,
):
    assert all(config(name)["execution"]["resume"] is False for name in B4_CONFIGS)
    observed = []

    def capture(execution_config, _store, **_kwargs):
        observed.append(execution_config["execution"]["resume"])
        return {"resume": observed[-1]}

    summary = _invoke_execute(
        cli, monkeypatch, capsys, tmp_path, capture, resume=False,
    )
    assert observed == [False]
    assert summary == {"resume": False}


def test_resume_execute_overrides_only_runtime_execution_config(
    cli, monkeypatch, capsys, tmp_path,
):
    observed = []

    def capture(execution_config, _store, **_kwargs):
        observed.append(execution_config["execution"]["resume"])
        return {"resume": observed[-1]}

    summary = _invoke_execute(
        cli, monkeypatch, capsys, tmp_path, capture, resume=True,
    )
    assert observed == [True]
    assert summary == {"resume": True}
    assert config()["execution"]["resume"] is False


@pytest.mark.parametrize("mode", ("--plan-only", "--freeze-tasksets", "--analyze-only"))
def test_resume_is_rejected_outside_execute(cli, monkeypatch, capsys, mode):
    monkeypatch.setattr(
        sys, "argv", [str(CLI_PATH), "--config", str(SMOKE_CONFIG), mode, "--resume"],
    )
    with pytest.raises(SystemExit) as captured:
        cli.main()
    assert captured.value.code == 2
    assert "--resume requires --execute" in capsys.readouterr().err


def test_cli_resume_with_partial_evidence_executes_only_missing_requests(
    cli, monkeypatch, capsys, tmp_path,
):
    requests = _requests()
    for request in requests[:4]:
        atomic_write_json(_terminal_path(tmp_path, request), _terminal(request))
    calls = []

    def fake_request(_config, request, _taskset, _root):
        calls.append(request.semantic_request_id)
        return _terminal(request)

    def execute_plan(execution_config, _store, *, max_requests=None, **_kwargs):
        return engine._execute_request_batch(
            execution_config, requests,
            {request.taskset_id: object() for request in requests},
            Path(execution_config["execution"]["output_root"]),
            max_requests=max_requests,
        )

    monkeypatch.setattr(engine, "execute_request", fake_request)
    summary = _invoke_execute(
        cli, monkeypatch, capsys, tmp_path, execute_plan, resume=True,
    )
    assert summary["resumed_valid_results"] == 4
    assert summary["completed_this_invocation"] == 5
    assert Counter(calls) == Counter({
        request.semantic_request_id: 1 for request in requests[4:]
    })


def test_cli_resume_with_complete_evidence_invokes_no_simulator_and_changes_no_terminal(
    cli, monkeypatch, capsys, tmp_path,
):
    requests = _requests()
    for request in requests:
        atomic_write_json(_terminal_path(tmp_path, request), _terminal(request))
    before = {
        path.name: path.read_bytes()
        for path in sorted((tmp_path / "terminal_results").glob("*.json"))
    }
    calls = []

    def execute_plan(execution_config, _store, *, max_requests=None, **_kwargs):
        return engine._execute_request_batch(
            execution_config, requests,
            {request.taskset_id: object() for request in requests},
            Path(execution_config["execution"]["output_root"]),
            max_requests=max_requests,
        )

    monkeypatch.setattr(
        engine, "execute_request", lambda *_args: calls.append("unexpected"),
    )
    summary = _invoke_execute(
        cli, monkeypatch, capsys, tmp_path, execute_plan, resume=True,
    )
    after = {
        path.name: path.read_bytes()
        for path in sorted((tmp_path / "terminal_results").glob("*.json"))
    }
    assert summary["resumed_valid_results"] == 9
    assert summary["completed_this_invocation"] == 0
    assert summary["simulator_invoked"] is False
    assert calls == []
    assert after == before


def test_resume_override_does_not_change_scientific_or_semantic_request_identity():
    baseline = config()
    resumed = deepcopy(baseline)
    resumed["execution"]["resume"] = True
    assert stage_scientific_config_hash(baseline) == stage_scientific_config_hash(resumed)
    assert semantic_config_material(baseline) == semantic_config_material(resumed)
    assert semantic_config_hash(semantic_config_material(baseline)) == semantic_config_hash(
        semantic_config_material(resumed)
    )
    taskset = PerformanceTaskset(
        "taskset", "taskset-hash", "priority", "power", "release",
        "1/10", 0, 0, "1/10", "1", "1", tuple(task_payload()), Path("/unused"),
    )
    baseline_ids = [
        request.semantic_request_id for request in build_requests(
            baseline, (taskset,), source_commit="source", simulator_binary_sha256="binary",
        )
    ]
    resumed_ids = [
        request.semantic_request_id for request in build_requests(
            resumed, (taskset,), source_commit="source", simulator_binary_sha256="binary",
        )
    ]
    assert resumed_ids == baseline_ids
