from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

from experiments.v9_3 import rta_load_cross as cross
from scripts import run_rta_load_uc_fixed_scale as fixed_runner


def _skeleton() -> tuple[dict[str, object], ...]:
    return (
        {"name": "task_0", "priority": 0, "C": 4, "D": 10, "T": 20, "workload": "bzip2"},
        {"name": "task_1", "priority": 1, "C": 6, "D": 15, "T": 30, "workload": "control"},
    )


def _base_energies() -> dict[str, Fraction]:
    return {"bzip2": Fraction(5), "control": Fraction(7)}


def test_legacy_target_ue_path_remains_exact() -> None:
    old = cross.scale_skeleton(
        _skeleton(), target_uc=Fraction(1, 2), target_ue=Fraction(3, 5),
        generation_index=0, seed=123, processors=2, rho=Fraction(11, 2),
        base_energies=_base_energies(),
    )
    assert old["target_ue"] == "3/5"
    assert old["actual_ue"] == "3/5"
    assert "energy_mode" not in old


def test_fixed_energy_scale_preserves_exact_kappa_and_observes_actual_ue() -> None:
    result = cross.scale_skeleton_fixed_energy_scale(
        _skeleton(), target_uc=Fraction(1, 2), energy_scale=Fraction(3, 2),
        generation_index=0, seed=123, processors=2, rho=Fraction(11, 2),
        base_energies=_base_energies(),
    )
    assert result["energy_mode"] == "fixed_scale"
    assert result["energy_scale"] == "3/2"
    assert result["target_ue"] is None
    assert result["actual_uc"] == "1/5"
    assert result["actual_ue"] == "36/55"
    assert [row["energy_per_tick"] for row in result["tasks"]] == ["15/2", "21/2"]


def test_fixed_scale_pairing_is_stable_and_independent_of_uc() -> None:
    first = cross.scale_skeleton_fixed_energy_scale(
        _skeleton(), target_uc=Fraction(3, 10), energy_scale=Fraction(3, 2),
        generation_index=4, seed=456, processors=2, rho=Fraction(11, 2),
        base_energies=_base_energies(),
    )
    second = cross.scale_skeleton_fixed_energy_scale(
        _skeleton(), target_uc=Fraction(1, 2), energy_scale=Fraction(3, 2),
        generation_index=4, seed=456, processors=2, rho=Fraction(11, 2),
        base_energies=_base_energies(),
    )
    assert [row["energy_per_tick"] for row in first["tasks"]] == [
        row["energy_per_tick"] for row in second["tasks"]
    ]
    assert first["taskset_id"] != second["taskset_id"]
    assert first["taskset_id"] == cross.fixed_scale_taskset_id(
        Fraction(3, 10), 4, Fraction(3, 2)
    )
    assert first["taskset_id"] == cross.scale_skeleton_fixed_energy_scale(
        _skeleton(), target_uc=Fraction(3, 10), energy_scale=Fraction(3, 2),
        generation_index=4, seed=456, processors=2, rho=Fraction(11, 2),
        base_energies=_base_energies(),
    )["taskset_id"]


def _runner_args(output: Path, *, energy_scale: str = "3/2", resume: bool = False) -> list[str]:
    args = [
        "--output", str(output), "--seed", "20260814", "--workers", "1",
        "--samples-per-uc", "1", "--processors", "4", "--tasks", "10",
        "--period-min", "40", "--period-max", "200", "--min-task-util", "0.01",
        "--max-task-util", "0.8", "--util-tolerance-total", "0.01",
        "--uc-values", "0.3,0.5", "--energy-scale", energy_scale,
        "--e0-values", "37", "--methods", "CW", "--rho", "11/2",
        "--latency", "2/5", "--timeout-first", "5", "--timeout-retry", "10",
    ]
    if resume:
        args.append("--resume")
    return args


def test_fixed_scale_runner_smoke_and_resume(tmp_path: Path) -> None:
    output = tmp_path / "fixed-scale-smoke"
    assert fixed_runner.main(_runner_args(output)) == 0

    tasksets = [json.loads(line) for line in (output / "tasksets.jsonl").read_text().splitlines()]
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    config = json.loads((output / "run_config.json").read_text())
    assert len(tasksets) == 2
    assert len(results) == 2
    assert config["energy_mode"] == "fixed_scale"
    assert config["energy_scale"] == "3/2"
    assert len({row["request_id"] for row in results}) == 2
    assert all(row["energy_mode"] == "fixed_scale" for row in results)
    assert all(row["energy_scale"] == "3/2" for row in results)

    assert fixed_runner.main(_runner_args(output, resume=True)) == 0
    assert fixed_runner.main(
        _runner_args(output, energy_scale="4/3", resume=True)
    ) == 2
