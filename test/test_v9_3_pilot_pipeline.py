from fractions import Fraction
from pathlib import Path
import time

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner
from asap_block_v1_3_12_schema_binding import V1312SchemaBinding
from scripts import run_v9_3_pilot as pilot
from scripts import analyze_v9_3_pilot as pilot_analysis


CONFIG = Path(__file__).resolve().parents[1] / "configs" / "v9_3_pilot.yaml"


def _generated(e0=100):
    tasks = (
        core.V93Task("0", 1, 5, 10, Fraction(1)),
        core.V93Task("1", 1, 7, 12, Fraction(1)),
    )
    payload = tuple(
        {
            "task_id": task.name,
            "source_name": "task_{}".format(task.name),
            "priority_rank": rank,
            "C": task.wcet,
            "D": task.deadline,
            "T": task.period,
            "P": str(task.power),
            "workload": "control",
            "arrival_offset": 0,
        }
        for rank, task in enumerate(tasks)
    )
    return pilot.GeneratedTaskset(
        seed=123,
        taskset_id="pilot-test-taskset",
        u_norm="0.2",
        u_norm_index=0,
        e0=e0,
        e0_index=0,
        taskset_index=0,
        target_total_utilization=Fraction(1, 2),
        actual_total_utilization=sum(Fraction(task.wcet, task.period) for task in tasks),
        semantic_hash="a" * 64,
        priority_hash="b" * 64,
        power_hash="c" * 64,
        tasks=tasks,
        task_payload=payload,
        generation_runtime_seconds=0.01,
    )


def test_frozen_config_and_seed_derivation_are_deterministic():
    config = pilot.load_pilot_config(CONFIG)
    assert config["task_generation"]["base_seed"] == 930012
    observed = {
        pilot.derive_seed(930012, u_index, e0_index, taskset_index)
        for u_index in range(3)
        for e0_index in range(2)
        for taskset_index in range(10)
    }
    assert len(observed) == 60
    assert pilot.derive_seed(930012, 2, 1, 9) == pilot.derive_seed(930012, 2, 1, 9)
    assert format(float(Fraction("0.2") * 4), ".15g") == "0.8"


def test_production_five_variant_pipeline_serializes_and_checks_dominance():
    generated = _generated()
    beta = tuple(Fraction(0) for _ in range(200))
    inp = pilot._analysis_input(generated, beta, "d" * 64, "e" * 64, 3.0)
    ids = pilot._analysis_ids(generated)
    binding = V1312SchemaBinding()
    results = {}
    serializations = {}
    for variant in pilot.VARIANT_ORDER:
        source = results.get(taskset.AnalysisVariant.CW_THETA_CW)
        dependency = taskset.DependencyVectorCheckStatus.NOT_CHECKED
        request_source = None
        if variant is taskset.AnalysisVariant.LOC_THETA_CW:
            request_source = source
            dependency = taskset.DependencyVectorCheckStatus.VALID
        execution = pilot.execute_analysis(
            production_runner.V93DispatchRequest(
                ids[variant], variant, inp, source=request_source,
                dependency_check_status=dependency,
            ),
            3.0,
        )
        assert not execution.outer_timeout
        assert execution.result is not None
        pilot.validate_analysis_result(execution.result, generated, request_source)
        serialized = production_runner.serialize_taskset_analysis_v1_3_12(
            execution.result,
            binding,
            pilot._analysis_base(
                binding, generated, execution.result,
                execution.wall_seconds, execution.cpu_seconds,
                "e" * 64, "d" * 64, '{"profile":"test"}',
            ),
            pilot._task_definitions(generated),
            source=(
                serializations[taskset.AnalysisVariant.CW_THETA_CW]
                if variant is taskset.AnalysisVariant.LOC_THETA_CW else None
            ),
        )
        results[variant] = execution.result
        serializations[variant] = serialized
    assert tuple(results) == pilot.VARIANT_ORDER
    assert all(result.taskset_proven for result in results.values())
    assert not any(
        row["status"] == "VIOLATION"
        for row in pilot.dominance_rows(generated, results)
    )


def test_hard_configuration_timeout_terminates_worker(monkeypatch):
    generated = _generated()
    beta = tuple(Fraction(0) for _ in range(200))
    inp = pilot._analysis_input(generated, beta, "d" * 64, "e" * 64, 3.0)

    def blocked_dispatch(*_args, **_kwargs):
        time.sleep(5)

    monkeypatch.setattr(production_runner, "dispatch_rta_version", blocked_dispatch)
    result = pilot.execute_analysis(
        production_runner.V93DispatchRequest(
            "timeout", taskset.AnalysisVariant.CW_D, inp
        ),
        0.05,
    )
    assert result.outer_timeout
    assert result.result is None


def test_shared_configuration_budget_returns_production_timeout_state():
    generated = _generated()
    beta = tuple(Fraction(0) for _ in range(200))
    inp = pilot._analysis_input(generated, beta, "d" * 64, "e" * 64, 3.0)
    result = pilot.execute_analysis(
        production_runner.V93DispatchRequest(
            "budget-timeout", taskset.AnalysisVariant.CW_D, inp,
            configuration_timeout_seconds=0.0,
        ),
        1.0,
    )
    assert not result.outer_timeout
    assert result.result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT
    assert result.result.task_records[0].solver_status is taskset.TaskSolverStatus.TIMEOUT


def test_smoke_comparison_ignores_only_timeout_cutoff_counters(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    header = (
        "task_id,task_solver_status,candidate_response_time,checked_w_count,"
        "checked_h_count,checked_q_count,envelope_calls\n"
    )
    (left / "per_task_results.csv").write_text(
        header + "7,TIMEOUT,,53,1,102,102\n", encoding="utf-8"
    )
    (right / "per_task_results.csv").write_text(
        header + "7,TIMEOUT,,53,1,99,99\n", encoding="utf-8"
    )
    assert pilot_analysis._normalized_rows(
        left, "per_task_results.csv", ()
    ) == pilot_analysis._normalized_rows(right, "per_task_results.csv", ())
    (right / "per_task_results.csv").write_text(
        header + "8,TIMEOUT,,53,1,99,99\n", encoding="utf-8"
    )
    assert pilot_analysis._normalized_rows(
        left, "per_task_results.csv", ()
    ) != pilot_analysis._normalized_rows(right, "per_task_results.csv", ())
