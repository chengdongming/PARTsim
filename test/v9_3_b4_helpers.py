from copy import deepcopy
from pathlib import Path

from experiments.v9_3.config import domain_hash
from experiments.v9_3.performance_calibration import (
    final_10s_grid_cells, final_10s_grid_identity,
)
from experiments.v9_3.performance_calibration_audit import calibration_audit_set_identity
from experiments.v9_3.performance_config import load_performance_config
from experiments.v9_3.performance_environment import STAGE_ENVIRONMENT_DOMAIN
from experiments.v9_3.performance_identity import calibration_selection_identity


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def config(name="v9_3_b4_smoke.yaml"):
    return deepcopy(load_performance_config(PROJECT_ROOT / "configs" / name))


def calibration_control_document(*, cells=None, **updates):
    if cells is None:
        cells = [
            {"kappa": kappa, "eta": eta}
            for kappa in ("10", "50", "200")
            for eta in ("1/2", "3/4", "1", "5/4", "3/2")
        ]
    frozen_cells = list(final_10s_grid_cells(cells))
    environment = {
        "schema": "B4_STAGE_ENVIRONMENT_V1",
        "exact_source_commit": "source", "tracked_worktree_clean": True,
        "simulator_binary_sha256": "binary",
        "system_template_sha256": "template", "solar_data_sha256": "solar",
        "workload_power_contract_identity": "power",
        "outcome_contract_version": "PERF_OUTCOME_V2",
        "outcome_source_sha256": "outcome", "energy_contract_version": "energy",
        "request_contract_version": "request", "stage_config_hash": "config",
    }
    environment["environment_identity"] = domain_hash(
        STAGE_ENVIRONMENT_DOMAIN, environment,
    )
    document = {
        "schema": "ASAP_BLOCK_V9_3_B4_CALIBRATION_SELECTION_V1",
        "status": "SELECTED", "kappa_star": "50",
        "eta_low": "3/4", "eta_transition": "1", "eta_high": "5/4",
        "q_values": list(frozen_cells),
        "phase_audits": [{
            "phase": "initial", "status": "CAL_VALID", "audit_identity": "audit",
        }],
        "stage_environment": environment,
        "final_10s_grid_cells": frozen_cells,
        "final_10s_grid_identity": final_10s_grid_identity(frozen_cells),
    }
    document.update(updates)
    document["calibration_audit_identity"] = calibration_audit_set_identity(
        document["phase_audits"],
    )
    document["selection_identity"] = calibration_selection_identity(document)
    return document


def task_payload():
    rows = []
    powers = ["4", "3", "2", "1", "1", "1", "1", "1", "1", "1"]
    for rank in range(10):
        rows.append({
            "task_id": str(rank), "source_name": f"task_{rank}",
            "priority_rank": rank, "C": 1, "D": 5 + rank,
            "T": 10 + rank, "P": powers[rank],
            "workload": "bzip2", "arrival_offset": 0,
        })
    return rows
