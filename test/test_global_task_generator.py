import math
import subprocess
import sys
from pathlib import Path

import yaml
import pytest

import global_task_generator as taskgen
import acceptance_ratio_test as acceptance


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _regular_tasks(tasks):
    return [
        task for task in tasks
        if str(task.get("name", "")).startswith("task_")
    ]


def test_global_task_generator_help_exits_zero():
    completed = subprocess.run(
        [sys.executable, "global_task_generator.py", "--help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--min-task-util" in completed.stdout
    assert "--wcet-rounding" in completed.stdout
    assert "compensated" in completed.stdout
    assert "--actual-utilization-tolerance-total" in completed.stdout


def test_uunifast_sum_and_default_discard_bounds():
    generator = taskgen.UUniFastDiscard(seed=123)
    utilizations = generator.generate(8, 2.4)

    assert math.isclose(sum(utilizations), 2.4, rel_tol=0, abs_tol=1e-12)
    assert all(0.01 <= value <= 0.8 for value in utilizations)


def test_uunifast_custom_max_allows_higher_task_utilization():
    # With this seed, the first valid draw exceeds the default 0.8 cap but
    # remains legal for a sequential task when max_task_util is relaxed to 1.0.
    generator = taskgen.UUniFastDiscard(seed=1)
    utilizations = generator.generate(3, 2.2, max_task_util=1.0)

    assert math.isclose(sum(utilizations), 2.2, rel_tol=0, abs_tol=1e-12)
    assert max(utilizations) > 0.8
    assert max(utilizations) <= 1.0


def test_generated_integer_tasks_are_legal_with_implicit_deadlines():
    generator = taskgen.EnergyAwareTaskGenerator(seed=42, energy_manager=None)
    tasks, _resources, _dag, _energy = generator.generate_taskset(
        n=5,
        total_utilization=2.0,
        min_period=40,
        max_period=80,
        num_cpus=4,
        implicit_deadline=True,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
    )

    regular = _regular_tasks(tasks)
    assert len(regular) == 5
    for task in regular:
        assert task["runtime"] >= 1
        assert task["runtime"] <= task["deadline"] <= task["iat"]
        assert task["deadline"] == task["iat"]


def _actual_total_utilization(tasks):
    return sum(
        task["runtime"] / task["iat"]
        for task in _regular_tasks(tasks)
    )


def test_floor_mode_preserves_downward_rounding_semantics():
    target_total_utilization = 2.0
    generator = taskgen.EnergyAwareTaskGenerator(seed=2026, energy_manager=None)
    tasks, _resources, _dag, _energy = generator.generate_taskset(
        n=10,
        total_utilization=target_total_utilization,
        min_period=40,
        max_period=200,
        num_cpus=4,
        implicit_deadline=True,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
        wcet_rounding="floor",
    )

    assert _actual_total_utilization(tasks) <= target_total_utilization


def test_compensated_rounding_reduces_realized_utilization_error():
    for normalized_utilization in (0.1, 0.5, 1.0):
        target_total_utilization = normalized_utilization * 4
        floor_generator = taskgen.EnergyAwareTaskGenerator(
            seed=1200 + int(normalized_utilization * 100),
            energy_manager=None,
        )
        compensated_generator = taskgen.EnergyAwareTaskGenerator(
            seed=1200 + int(normalized_utilization * 100),
            energy_manager=None,
        )
        floor_tasks, _resources, _dag, _energy = floor_generator.generate_taskset(
            n=10,
            total_utilization=target_total_utilization,
            min_period=40,
            max_period=200,
            num_cpus=4,
            implicit_deadline=True,
            dag_enabled=False,
            energy_aware=False,
            arrival_offset=False,
            wcet_rounding="floor",
        )
        compensated_tasks, _resources, _dag, _energy = compensated_generator.generate_taskset(
            n=10,
            total_utilization=target_total_utilization,
            min_period=40,
            max_period=200,
            num_cpus=4,
            implicit_deadline=True,
            dag_enabled=False,
            energy_aware=False,
            arrival_offset=False,
            wcet_rounding="compensated",
        )

        floor_error = abs(
            _actual_total_utilization(floor_tasks) - target_total_utilization
        )
        compensated_error = abs(
            _actual_total_utilization(compensated_tasks)
            - target_total_utilization
        )
        assert compensated_error < floor_error


def test_actual_utilization_tolerance_discards_or_accepts_tasksets():
    strict_generator = taskgen.EnergyAwareTaskGenerator(
        seed=3030,
        energy_manager=None,
    )
    with pytest.raises(RuntimeError):
        strict_generator.generate_taskset(
            n=10,
            total_utilization=2.003,
            min_period=40,
            max_period=80,
            num_cpus=4,
            implicit_deadline=True,
            dag_enabled=False,
            energy_aware=False,
            arrival_offset=False,
            wcet_rounding="compensated",
            actual_utilization_tolerance_total=1e-12,
        )

    relaxed_generator = taskgen.EnergyAwareTaskGenerator(
        seed=3030,
        energy_manager=None,
    )
    tasks, _resources, _dag, _energy = relaxed_generator.generate_taskset(
        n=10,
        total_utilization=2.0,
        min_period=40,
        max_period=200,
        num_cpus=4,
        implicit_deadline=True,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
        wcet_rounding="compensated",
        actual_utilization_tolerance_total=0.01,
    )
    assert abs(_actual_total_utilization(tasks) - 2.0) <= 0.01


def test_compensated_rounding_respects_max_task_utilization():
    generator = taskgen.EnergyAwareTaskGenerator(seed=4040, energy_manager=None)
    tasks, _resources, _dag, _energy = generator.generate_taskset(
        n=5,
        total_utilization=2.0,
        min_period=40,
        max_period=200,
        num_cpus=4,
        implicit_deadline=False,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
        max_task_util=0.5,
        wcet_rounding="compensated",
        actual_utilization_tolerance_total=0.02,
    )

    for task in _regular_tasks(tasks):
        assert task["runtime"] >= 1
        assert task["runtime"] <= task["deadline"] <= task["iat"]
        assert task["runtime"] / task["iat"] <= 0.5


def test_generated_constrained_deadlines_are_legal_and_can_be_tight():
    generator = taskgen.EnergyAwareTaskGenerator(seed=99, energy_manager=None)
    tasks, _resources, _dag, _energy = generator.generate_taskset(
        n=5,
        total_utilization=2.0,
        min_period=40,
        max_period=80,
        num_cpus=4,
        implicit_deadline=False,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
    )

    regular = _regular_tasks(tasks)
    assert all(task["runtime"] <= task["deadline"] <= task["iat"] for task in regular)
    assert any(task["deadline"] < task["iat"] for task in regular)


def test_yaml_metadata_records_actual_utilization(tmp_path):
    generator = taskgen.EnergyAwareTaskGenerator(seed=7, energy_manager=None)
    tasks, resources, _dag, energy = generator.generate_taskset(
        n=5,
        total_utilization=2.0,
        min_period=40,
        max_period=80,
        num_cpus=4,
        implicit_deadline=True,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
    )
    regular = _regular_tasks(tasks)
    actual_total = sum(task["runtime"] / task["iat"] for task in regular)
    metadata = {
        "target_total_utilization": 2.0,
        "target_normalized_utilization": 0.5,
        "actual_total_utilization": actual_total,
        "actual_normalized_utilization": actual_total / 4,
        "task_util_min": 0.01,
        "task_util_max": 0.8,
        "wcet_rounding": "floor",
        "deadline_mode": "implicit",
        "actual_utilization_tolerance_total": 0.01,
        "period_min": 40,
        "period_max": 80,
        "num_tasks": 5,
        "num_cores": 4,
        "M": 4,
    }
    output = tmp_path / "tasks.yml"
    output.write_text(
        taskgen.create_yaml_content(
            tasks,
            resources,
            total_utilization=actual_total,
            dag_enabled=False,
            energy_info=energy,
            generation_metadata=metadata,
        ),
        encoding="utf-8",
    )

    document = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert document["metadata"]["actual_total_utilization"] == actual_total
    assert document["metadata"]["actual_normalized_utilization"] == actual_total / 4

    loaded = acceptance.load_taskset_utilization_metadata(
        output,
        target_normalized_utilization=0.5,
        target_total_utilization=2.0,
        num_cores=4,
    )
    assert loaded["actual_total_utilization"] == actual_total
    assert loaded["actual_normalized_utilization"] == actual_total / 4
    assert loaded["actual_utilization_tolerance_total"] == 0.01


def test_same_seed_generates_identical_tasksets():
    def generate_once():
        generator = taskgen.EnergyAwareTaskGenerator(seed=1234, energy_manager=None)
        tasks, _resources, _dag, _energy = generator.generate_taskset(
            n=5,
            total_utilization=2.0,
            min_period=40,
            max_period=80,
            num_cpus=4,
            implicit_deadline=True,
            dag_enabled=False,
            energy_aware=False,
            arrival_offset=False,
        )
        return [
            (task["iat"], task["runtime"], task["deadline"], task["params"])
            for task in _regular_tasks(tasks)
        ]

    assert generate_once() == generate_once()
