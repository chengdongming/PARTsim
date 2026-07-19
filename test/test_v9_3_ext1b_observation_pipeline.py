"""Production EXT-1B trace-auditor-CSV integration tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from experiments.v9_3.ext1b_b2_batch_audit import (
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
    B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER,
    B2_STATE_CONTINUATION_ONLY,
    B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
    CONTROL_STATUS_ELIGIBLE_MATCHED_STATE,
    CONTROL_STATUS_NOT_APPLICABLE,
    audit_asap_sync_trace,
    summarize_b2_observations,
)
from experiments.v9_3.ext1b_b3_timing_audit import audit_timing_trace
from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_engine import Ext1BRunner, analyze_ext1b
from experiments.v9_3.ext1b_observation import (
    Ext1BObservationError,
    write_ext1b_observation_outputs,
)
from experiments.v9_3.result_writer import read_csv, write_csv
from test_scheduler_trace_identity import require_rtsim_binary


def _request(pair_id: str, scheduler: str):
    return {
        "request_id": scheduler,
        "paired_instance_id": pair_id,
        "scenario_kind": "SYNC_BATCH_STRESS",
        "scenario_cell_id": "cell",
        "taskset_hash": "a" * 64,
        "input_hash": "b" * 64,
        "scheduler_id": scheduler,
        "M": 2,
    }


def _result(tmp_path: Path, pair_id: str, scheduler: str):
    trace = tmp_path / f"{scheduler}.json"
    trace.write_text("{}\n", encoding="utf-8")
    return {
        "request_id": scheduler,
        "paired_instance_id": pair_id,
        "scenario_kind": "SYNC_BATCH_STRESS",
        "scenario_cell_id": "cell",
        "taskset_hash": "a" * 64,
        "input_hash": "b" * 64,
        "scheduler_id": scheduler,
        "status": "SIM_PASS_OBSERVED",
        "synchronization_wait_ticks": 0,
        "idle_cores_while_ready_jobs_exist_ticks": 1,
        "top_m_first_execution_vector": "[0,1]",
        "maximum_observed_response_time": 2,
        "missed_jobs": 0,
        "timing_activation": "UNAVAILABLE",
        "retained_trace_path": str(trace),
    }


def _minimal_b2_root(tmp_path: Path):
    pair_id = "pair"
    requests = [
        _request(pair_id, "gpfp_asap_block"),
        _request(pair_id, "gpfp_asap_sync"),
    ]
    results = [
        _result(tmp_path, pair_id, "gpfp_asap_block"),
        _result(tmp_path, pair_id, "gpfp_asap_sync"),
    ]
    write_csv(tmp_path / "simulation_requests.csv", tuple(requests[0]), requests)
    write_csv(tmp_path / "simulation_results.csv", tuple(results[0]), results)
    scenario = {
        "paired_instance_id": pair_id,
        "scenario_kind": "SYNC_BATCH_STRESS",
        "scenario_cell_id": "cell",
        "normalized_utilization": "2/10",
        "nominal_energy_supply_ratio": "2/8",
    }
    write_csv(tmp_path / "scenario_instances.csv", tuple(scenario), [scenario])
    return {"scenario": {"affordable_prefix_length": 1}}


def _b2_row(state: str):
    atomic_wait = state == (
        B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
    )
    return {
        "request_id": "gpfp_asap_sync",
        "pair_id": "pair",
        "taskset_semantic_hash": "a" * 64,
        "scheduler_id": "gpfp_asap_sync",
        "tick": 0,
        "idle_core_count": 1,
        "active_top_m_job_ids": ["H@0", "L@0"] if atomic_wait else ["H@0"],
        "continuation_job_ids": [] if atomic_wait else ["H@0"],
        "candidate_job_ids": ["H@0", "L@0"] if atomic_wait else [],
        "candidate_count": 2 if atomic_wait else 0,
        "affordable_prefix_length": 1 if atomic_wait else 0,
        "whole_batch_required_energy_mJ": 2.0 if atomic_wait else 1.0,
        "available_energy_mJ": 1.0,
        "whole_batch_affordable": not atomic_wait,
        "feasible_subset_exists": atomic_wait,
        "selected_count": 0,
        "actual_launch_count": 0,
        "atomic_opportunity": False,
        "atomic_wait_with_affordable_member": False,
        "partial_launch_violation": state == B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
        "classified_state": state,
        "classification_errors": [],
        "evidence_event_ids": {},
        "sync_batch_block_present": False,
    }


def test_b2_zero_denominator_is_explicit(tmp_path, monkeypatch):
    config = _minimal_b2_root(tmp_path)
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_sync_trace",
        lambda *args, **kwargs: [_b2_row(B2_STATE_CONTINUATION_ONLY)],
    )
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_block_pair_trace",
        lambda *args, **kwargs: [],
    )
    write_ext1b_observation_outputs(tmp_path, config)
    summary = read_csv(tmp_path / "b2_summary.csv")[0]
    assert summary["atomic_wait_share"] == ""
    assert summary["denominator_zero"] == "True"
    assert summary["continuation_only_decision_count"] == "1"
    assert summary["normalized_utilization"] == "1/5"
    assert summary["nominal_energy_supply_ratio"] == "1/4"
    assert summary["mechanism_activated"] == "False"


def test_b2_illegal_partial_launch_fails_closed(tmp_path, monkeypatch):
    config = _minimal_b2_root(tmp_path)
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_sync_trace",
        lambda *args, **kwargs: [_b2_row(B2_STATE_ILLEGAL_PARTIAL_LAUNCH)],
    )
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_block_pair_trace",
        lambda *args, **kwargs: [],
    )
    with pytest.raises(Ext1BObservationError, match="did not close"):
        write_ext1b_observation_outputs(tmp_path, config)
    assert read_csv(tmp_path / "b2_batch_decisions.csv")[0][
        "classified_state"
    ] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH


def test_b2_core_wait_has_exact_dimensions_and_direct_activation(tmp_path, monkeypatch):
    config = _minimal_b2_root(tmp_path)
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_sync_trace",
        lambda *args, **kwargs: [_b2_row(
            B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
        )],
    )
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_block_pair_trace",
        lambda *args, **kwargs: [{
            "control_status": CONTROL_STATUS_ELIGIBLE_MATCHED_STATE,
            "control_passed": True,
        }],
    )
    write_ext1b_observation_outputs(tmp_path, config)
    decision = read_csv(tmp_path / "b2_batch_decisions.csv")[0]
    summary = read_csv(tmp_path / "b2_summary.csv")[0]
    for row in (decision, summary):
        assert row["scenario_cell_id"] == "cell"
        assert row["normalized_utilization"] == "1/5"
        assert row["nominal_energy_supply_ratio"] == "1/4"
    assert summary["atomic_wait_with_affordable_member_count"] == "1"
    assert summary["audit_closed"] == "True"
    assert summary["mechanism_activated"] == "True"


def test_b2_no_affordable_member_does_not_activate(tmp_path, monkeypatch):
    config = _minimal_b2_root(tmp_path)
    row = _b2_row(B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER)
    row.update({
        "active_top_m_job_ids": ["H@0", "L@0"],
        "continuation_job_ids": [],
        "candidate_job_ids": ["H@0", "L@0"],
        "candidate_count": 2,
        "whole_batch_affordable": False,
        "feasible_subset_exists": False,
    })
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_sync_trace",
        lambda *args, **kwargs: [row],
    )
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_block_pair_trace",
        lambda *args, **kwargs: [],
    )
    write_ext1b_observation_outputs(tmp_path, config)
    summary = read_csv(tmp_path / "b2_summary.csv")[0]
    assert summary["atomic_wait_with_affordable_member_count"] == "0"
    assert summary["audit_closed"] == "True"
    assert summary["mechanism_activated"] == "False"


def test_b2_predecision_fingerprint_mismatch_fails_closed(tmp_path, monkeypatch):
    config = _minimal_b2_root(tmp_path)
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_sync_trace",
        lambda *args, **kwargs: [_b2_row(
            B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
        )],
    )
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_observation.audit_asap_block_pair_trace",
        lambda *args, **kwargs: [{
            "control_status": CONTROL_STATUS_NOT_APPLICABLE,
            "control_passed": None,
        }],
    )
    with pytest.raises(Ext1BObservationError, match="did not close"):
        write_ext1b_observation_outputs(tmp_path, config)
    summary = read_csv(tmp_path / "b2_summary.csv")[0]
    assert summary["audit_closed"] == "False"
    assert summary["mechanism_activated"] == "False"


def _integration_config(tmp_path: Path, name: str, binary: Path):
    config = deepcopy(load_ext1b_config(ROOT / "configs" / name))
    config["simulation"]["simulator_bin"] = str(binary)
    config["simulation"]["retain_trace"] = True
    config["execution"]["output_root"] = str(tmp_path / "results")
    config["execution"]["taskset_store"] = str(tmp_path / "tasksets")
    config["execution"]["checkpoint_every"] = 1
    return config


def test_real_b1_runner_trace_analyzer_four_csvs_and_reanalysis(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PARTSIM_LOG_DIR", str(tmp_path / "logs"))
    binary, _ = require_rtsim_binary()
    config = _integration_config(tmp_path, "v9_3_ext1b1_smoke.yaml", binary)
    outcome = Ext1BRunner(config).run(max_cells=1, max_tasksets=1)
    assert outcome.requested == outcome.terminal == 9
    root = outcome.output_root
    names = (
        "b1_bypass_episodes.csv", "b1_task_effects.csv",
        "b1_paired_effects.csv", "b1_summary.csv",
    )
    before = {name: (root / name).read_bytes() for name in names}
    effects = read_csv(root / "b1_task_effects.csv")
    paired = read_csv(root / "b1_paired_effects.csv")
    summary = read_csv(root / "b1_summary.csv")
    episodes = read_csv(root / "b1_bypass_episodes.csv")
    for rows in (episodes, effects, paired, summary):
        assert rows
        assert {row["normalized_utilization"] for row in rows} == {"1/5"}
        assert {row["nominal_energy_supply_ratio"] for row in rows} == {"0"}
    assert len(effects) == 2
    assert {row["scheduler"] for row in effects} == {
        "gpfp_asap_block", "gpfp_asap_nonblock",
    }
    assert len(paired) == len(summary) == 1
    assert summary[0]["total_pairs"] == "1"
    assert int(summary[0]["bypass_event_count"]) >= 1
    assert int(summary[0]["bypass_episode_count"]) >= 1
    analyze_ext1b(root)
    assert {name: (root / name).read_bytes() for name in names} == before


def test_real_b2_runner_trace_auditor_csv_and_reanalysis(tmp_path, monkeypatch):
    monkeypatch.setenv("PARTSIM_LOG_DIR", str(tmp_path / "logs"))
    binary, _ = require_rtsim_binary()
    config = _integration_config(tmp_path, "v9_3_ext1b2_smoke.yaml", binary)
    outcome = Ext1BRunner(config).run(max_cells=1, max_tasksets=1)
    assert outcome.requested == outcome.terminal == 9
    root = outcome.output_root
    summaries = read_csv(root / "b2_summary.csv")
    decisions = read_csv(root / "b2_batch_decisions.csv")
    assert len(summaries) == 1
    assert decisions
    assert summaries[0]["audit_closed"] == "True"

    requests = read_csv(root / "simulation_requests.csv")
    results = read_csv(root / "simulation_results.csv")
    sync_request = next(
        row for row in requests if row["scheduler_id"] == "gpfp_asap_sync"
    )
    sync_result = next(
        row for row in results if row["scheduler_id"] == "gpfp_asap_sync"
    )
    direct_rows = audit_asap_sync_trace(
        root / "retained_traces" / f"{sync_request['request_id']}.json",
        processors=int(sync_request["M"]),
        request_id=sync_request["request_id"],
        pair_id=sync_request["paired_instance_id"],
    )
    direct = summarize_b2_observations(
        direct_rows,
        reported_synchronization_wait_ticks=int(
            sync_result["synchronization_wait_ticks"]
        ),
    )
    assert summaries[0]["affordable_atomic_launch_count"] == str(
        direct["affordable_atomic_launch_count"]
    )
    before = (root / "b2_summary.csv").read_bytes()
    analyze_ext1b(root)
    assert (root / "b2_summary.csv").read_bytes() == before


def test_real_two_scheduler_b2_completed_resume_is_byte_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("PARTSIM_LOG_DIR", str(tmp_path / "logs"))
    binary, _ = require_rtsim_binary()
    config = _integration_config(
        tmp_path, "v9_3_ext1b2_sync_calibration.yaml", binary,
    )
    config["grid"]["utilization_points"] = ["1/5"]
    config["grid"]["tasksets_per_cell"] = 1
    config["scenario"]["nominal_energy_supply_ratios"] = ["1/4"]
    runner = Ext1BRunner(config)
    initial = runner.run()
    assert initial.requested == initial.terminal == 2
    summary = read_csv(initial.output_root / "b2_summary.csv")[0]
    assert summary["normalized_utilization"] == "1/5"
    assert summary["nominal_energy_supply_ratio"] == "1/4"
    assert summary["audit_closed"] == "True"
    assert summary["mechanism_activated"] == "True"

    before = {
        path.relative_to(initial.output_root).as_posix(): path.read_bytes()
        for path in initial.output_root.rglob("*") if path.is_file()
    }
    resumed = runner.run(resume=True)
    after = {
        path.relative_to(initial.output_root).as_posix(): path.read_bytes()
        for path in initial.output_root.rglob("*") if path.is_file()
    }
    assert resumed.requested == resumed.terminal == 2
    assert after == before


def test_real_b3_runner_trace_auditor_csv_and_reanalysis(tmp_path, monkeypatch):
    monkeypatch.setenv("PARTSIM_LOG_DIR", str(tmp_path / "logs"))
    binary, _ = require_rtsim_binary()
    config = _integration_config(tmp_path, "v9_3_ext1b3_smoke.yaml", binary)
    outcome = Ext1BRunner(config).run(max_cells=1, max_tasksets=1)
    assert outcome.requested == outcome.terminal == 9
    root = outcome.output_root
    summaries = read_csv(root / "b3_summary.csv")
    assert len(summaries) == 9
    assert {row["comparison_scope"] for row in summaries} >= {"PRIMARY_BLOCK"}
    assert all(row["audit_closed"] == "True" for row in summaries)
    for row in summaries:
        trace = root / "retained_traces" / f"{row['request_id']}.json"
        direct = audit_timing_trace(trace, expected_scheduler=row["scheduler_id"])
        direct.assert_audit_closed()
        assert row["timing_unclassifiable_count"] == "0"
        assert row["timing_illegal_count"] == "0"
        assert sum(direct.state_counts.values()) > 0
    results = read_csv(root / "simulation_results.csv")
    assert all(
        row["timing_activation"] in {"True", "False"}
        for row in results if row["scenario_kind"] == "TIMING_STRESS"
    )
    before = (root / "b3_summary.csv").read_bytes()
    analyze_ext1b(root)
    assert (root / "b3_summary.csv").read_bytes() == before
