from __future__ import annotations

import pytest

from experiments.v9_3.censored_runtime import runtime_summary
from experiments.v9_3.config import canonical_json
from experiments.v9_3.core5_aggregation import _runtime_rows
from experiments.v9_3.core5_contract import SCALABILITY_CELL_COLUMNS
from experiments.v9_3.core5_terminal import (
    Core5TerminalClass,
    classify_core5_terminal,
)
from experiments.v9_3.result_writer import (
    TASKSET_RESULT_COLUMNS,
    write_csv,
)


@pytest.mark.parametrize("outer_timeout", [False, True, "False", "True"])
@pytest.mark.parametrize(
    ("solver_status", "expected"),
    [
        ("COMPLETED", Core5TerminalClass.SCIENTIFIC_COMPLETION),
        ("NO_CANDIDATE", Core5TerminalClass.SCIENTIFIC_COMPLETION),
        ("TIMEOUT", Core5TerminalClass.RIGHT_CENSORED),
        (
            "INTERNAL_CONFORMANCE_FAILURE",
            Core5TerminalClass.TECHNICAL_FAILURE,
        ),
        ("NUMERIC_ERROR", Core5TerminalClass.TECHNICAL_FAILURE),
        ("INVALID_RESULT", Core5TerminalClass.TECHNICAL_FAILURE),
        ("ALIEN_STATUS", Core5TerminalClass.TECHNICAL_FAILURE),
        ("completed", Core5TerminalClass.TECHNICAL_FAILURE),
        ("", Core5TerminalClass.TECHNICAL_FAILURE),
    ],
)
def test_strict_terminal_matrix_ignores_outer_timeout_override(
    solver_status, outer_timeout, expected,
):
    assert classify_core5_terminal(
        solver_status, outer_timeout=outer_timeout
    ) is expected


@pytest.mark.parametrize("solver_status", [None, 1, True, (), [], {}])
@pytest.mark.parametrize("outer_timeout", [False, True])
def test_malformed_terminal_status_fails_closed(solver_status, outer_timeout):
    assert classify_core5_terminal(
        solver_status, outer_timeout=outer_timeout
    ) is Core5TerminalClass.TECHNICAL_FAILURE


def test_runtime_excludes_outer_timeout_flagged_technical_from_all_science(
    tmp_path,
):
    write_csv(tmp_path / "scalability_cells.csv", SCALABILITY_CELL_COLUMNS, [{
        "scalability_cell_id": "cell", "scaling_axis": "worker_count",
        "level_index": 0, "level_id": "1", "level_value": "1",
        "M": 2, "task_n": 6, "period_min": 40, "period_max": 200,
        "utilization": "1/5", "worker_count": 1,
        "variants": canonical_json(["CW_THETA_CW"]),
        "tasksets_requested": 1,
        "analysis_ids_json": canonical_json(["analysis"]),
        "cell_wall_seconds": 1, "terminal_analysis_count": 1,
        "throughput_analyses_per_second": 1,
    }])
    write_csv(
        tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS,
        [{
            "analysis_id": "analysis", "analysis_variant": "CW_THETA_CW",
            "solver_status": "NUMERIC_ERROR", "outer_timeout": True,
            "timeout_budget_seconds": 4, "runtime_wall_seconds": 4,
        }],
    )
    rows = _runtime_rows(tmp_path)
    assert rows[0]["terminal_class"] == "TECHNICAL_FAILURE"
    assert rows[0]["censoring_status"] == "TECHNICAL_FAILURE"
    assert rows[0]["observed_time_seconds"] == "UNAVAILABLE"
    summary = runtime_summary(rows)
    assert summary["technical_failure_count"] == 1
    assert summary["runtime_evaluable_count"] == 0
    assert summary["timeout_rate_evaluable_denominator"] == 0
    assert summary["restricted_mean_runtime_seconds"] is None
