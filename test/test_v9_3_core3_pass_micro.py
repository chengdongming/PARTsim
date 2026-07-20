from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_microcase_covers_rta_pass_sim_pass_and_denominators():
    root = ROOT / "artifacts/v9_3_core3_pass_micro"
    with (root / "soundness_matrix.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        soundness = list(csv.DictReader(handle))
    summary = json.loads((root / "summary.json").read_text())
    with (root / "simulation_taskset_results.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        simulations = list(csv.DictReader(handle))
    assert len(soundness) == 2
    assert {row["soundness_class"] for row in soundness} == {"RTA_PASS_SIM_PASS"}
    assert len({row["taskset_hash"] for row in soundness}) == 1
    assert {row["analysis_variant"] for row in soundness} == {
        "CW_THETA_CW", "LOC_THETA_LOC"
    }
    assert simulations[0]["missed_jobs"] == "0"
    assert summary["certification_taskset_denominator"] == 2
    assert summary["certified_taskset_numerator"] == 2
    assert summary["tightness_denominator_scope"] == "jointly_certified_tasksets_only"


def test_uncertified_core3_smoke_excludes_partial_candidates_from_certified_tightness():
    root = ROOT / "artifacts/v9_3_core3_smoke"
    with (root / "per_taskset_results.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        results = list(csv.DictReader(handle))
    assert any(row["taskset_proven"] == "False" for row in results)
    # The refreshed smoke summary is produced by the persisted-data pipeline.
    summary = json.loads((root / "summary.json").read_text())
    assert summary["tightness_denominator_scope"] == "jointly_certified_tasksets_only"
    assert summary["tightness_common_task_denominator"] == 0
    assert summary["partial_candidate_rows_excluded_from_certified_tightness"] == 24
