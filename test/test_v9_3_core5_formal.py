from __future__ import annotations

from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from experiments.v9_3.config import config_hash, load_config
from experiments.v9_3.core5_contract import (
    Core5ContractError, validate_core5_artifact_contract,
)
from experiments.v9_3.core5_formal import (
    CORE5_FORMAL_CHECKPOINT_SCHEMA,
    CORE5_FORMAL_RUN_SCHEMA,
    Core5FormalContractError,
    Core5FormalRunner,
    analyze_core5_formal_artifacts,
    assert_worker_semantic_identity,
    core5b_execution_schedule,
    core5b_math_request_rows,
    exact_time_scale_payload,
    expand_core5a_cells,
)


ROOT = Path(__file__).resolve().parents[1]
CORE5A = ROOT / "configs/v9_3_core5a_formal_algorithmic.yaml"
CORE5B = ROOT / "configs/v9_3_core5b_formal_workers.yaml"
SMOKE = ROOT / "configs/v9_3_core5_smoke.yaml"


def test_core5a_formal_plan_and_duplicate_baseline_elimination():
    config = load_config(CORE5A, expected_core="CORE-5")
    cells = expand_core5a_cells(config)
    assert len(cells) == 24
    assert Counter(cell.utilization for cell in cells) == {
        Fraction(3, 10): 8, Fraction(1, 2): 8, Fraction(7, 10): 8,
    }
    for utilization in {cell.utilization for cell in cells}:
        members = [cell for cell in cells if cell.utilization == utilization]
        mathematical_inputs = {
            tuple(cell.mathematical_input().values()) for cell in members
        }
        assert len(mathematical_inputs) == 8
        assert sum(
            cell.processors == 4 and cell.task_count == 10
            and cell.period_min == 40 and cell.period_max == 200
            for cell in members
        ) == 1
        assert sum(
            cell.processors == 4 and cell.task_count == 20
            and cell.period_min == 40 and cell.period_max == 200
            for cell in members
        ) == 1
    plan = Core5FormalRunner(config).describe()
    assert plan["cell_count"] == 24
    assert plan["unique_scale_configurations_per_utilization"] == 8
    assert plan["mathematical_request_count"] == 4800
    assert plan["solver_execution_count"] == 4800


def test_core5a_exact_time_scaling_preserves_ratios_and_power():
    source = (
        {
            "task_id": "0", "priority_rank": 0, "C": 3, "D": 7, "T": 11,
            "P": "3/5", "D_over_T": "7/11", "workload": "hash",
        },
        {
            "task_id": "1", "priority_rank": 1, "C": 2, "D": 5, "T": 13,
            "P": "1/2", "D_over_T": "5/13", "workload": "control",
        },
    )
    for factor in (Fraction(1), Fraction(2), Fraction(4)):
        scaled = exact_time_scale_payload(source, factor)
        for original, transformed in zip(source, scaled):
            assert transformed["C"] == original["C"] * factor
            assert transformed["D"] == original["D"] * factor
            assert transformed["T"] == original["T"] * factor
            assert Fraction(transformed["C"], transformed["T"]) == Fraction(
                original["C"], original["T"]
            )
            assert Fraction(transformed["D_over_T"]) == Fraction(
                original["D_over_T"]
            )
            assert transformed["P"] == original["P"]
            assert transformed["task_id"] == original["task_id"]
            assert transformed["source_task_id"] == original["task_id"]


def test_core5b_math_identity_and_seeded_schedule_contract():
    config = load_config(CORE5B, expected_core="CORE-5")
    requests = core5b_math_request_rows(config)
    assert len(requests) == 300
    assert len({row["mathematical_request_id"] for row in requests}) == 300
    assert len({row["input_hash"] for row in requests}) == 300
    schedule = core5b_execution_schedule(config)
    assert schedule == core5b_execution_schedule(config)
    assert len(schedule) == 20
    assert {
        (row["worker_count"], row["repetition"]) for row in schedule
    } == {
        (worker, repetition) for worker in (1, 2, 4, 8)
        for repetition in range(5)
    }
    assert list(schedule) != sorted(
        schedule, key=lambda row: (row["worker_count"], row["repetition"])
    )
    plan = Core5FormalRunner(config).describe()
    assert plan["mathematical_request_count"] == 300
    assert plan["input_hash_count"] == 300
    assert plan["solver_execution_count"] == 6000


def _semantic_row(worker: int, response: str = "[10]"):
    return {
        "mathematical_request_id": "request",
        "worker_count": worker,
        "input_hash": "input",
        "terminal_class": "COMPLETED",
        "response_bound": response,
        "fixed_point_iterations": "[4]",
        "search_states": "[[5,6]]",
        "inverse_service_queries": "[7]",
        "candidate_count": 1,
    }


def test_core5b_worker_semantic_identity_is_p0_on_any_mismatch():
    assert_worker_semantic_identity([_semantic_row(1), _semantic_row(8)])
    with pytest.raises(Core5FormalContractError, match="P0 worker semantic mismatch"):
        assert_worker_semantic_identity([
            _semantic_row(1), _semantic_row(8, response="[11]"),
        ])


def test_formal_smoke_config_identity_output_store_and_seal_are_isolated():
    smoke = load_config(SMOKE, expected_core="CORE-5")
    core5a = load_config(CORE5A, expected_core="CORE-5")
    core5b = load_config(CORE5B, expected_core="CORE-5")
    configs = [smoke, core5a, core5b]
    assert len({config_hash(config) for config in configs}) == 3
    assert len({config["execution"]["output_root"] for config in configs}) == 3
    assert len({config["execution"]["taskset_store"] for config in configs}) == 3
    assert "profile" not in smoke["scalability"]
    assert core5a["scalability"]["profile"] == "formal-algorithmic-v1"
    assert core5b["scalability"]["profile"] == "formal-workers-v1"


def test_formal_and_bounded_analyzers_reject_mixed_profiles(tmp_path):
    (tmp_path / "run_metadata.json").write_text(json.dumps({
        "schema": CORE5_FORMAL_RUN_SCHEMA,
    }), encoding="utf-8")
    (tmp_path / "checkpoint.json").write_text(json.dumps({
        "schema": CORE5_FORMAL_CHECKPOINT_SCHEMA,
    }), encoding="utf-8")
    with pytest.raises(Core5ContractError, match="run metadata schema"):
        validate_core5_artifact_contract(tmp_path)

    shutil.copy2(SMOKE, tmp_path / "run_config.yaml")
    (tmp_path / "formal_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "formal_authorization_seal.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(Core5FormalContractError, match="bounded profile"):
        analyze_core5_formal_artifacts(tmp_path)


def test_smoke_v2_8_16_20_dry_run_contract_is_unchanged():
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_v9_3_core5.py", "--config",
            "configs/v9_3_core5_smoke.yaml", "--dry-run",
        ],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert '"cell_count": 8' in completed.stdout
    assert '"request_count": 16' in completed.stdout
    assert '"hard_analysis_limit": 20' in completed.stdout
    assert '"profile"' not in completed.stdout
