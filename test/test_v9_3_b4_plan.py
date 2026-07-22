from experiments.v9_3.performance_audit import audit_plan_counts
from experiments.v9_3.performance_engine import build_requests, calibration_phase_plan_counts
from experiments.v9_3.performance_identity import audit_gate_formal_relationship
from experiments.v9_3.performance_identity import horizon_selection_identity
from experiments.v9_3.performance_taskset_store import PerformanceTaskset
from v9_3_b4_helpers import calibration_control_document, config, task_payload


def test_plan_audit_requires_exact_count_and_unique_ids():
    plan = {"requests": [{"semantic_request_id": str(index)} for index in range(4000)]}
    assert audit_plan_counts("HORIZON_GATE", plan)["valid"]
    plan["requests"][-1]["semantic_request_id"] = "0"
    assert not audit_plan_counts("HORIZON_GATE", plan)["valid"]


def _tasksets(count, utilization_points):
    rows = []
    per_u = count // len(utilization_points)
    for u_index, utilization in enumerate(utilization_points):
        for index in range(per_u):
            identity = f"task-{u_index}-{index}"
            rows.append(PerformanceTaskset(
                identity, identity, f"priority-{identity}", f"power-{identity}",
                f"release-{identity}", utilization, index, index,
                utilization, "1", "1", tuple(task_payload()),
                __import__("pathlib").Path("/unused"),
            ))
    return rows


def test_actual_cal_gate_formal_request_counts_and_subset(tmp_path):
    selection = tmp_path / "calibration_selection.json"
    selection.write_text(
        __import__("json").dumps(calibration_control_document()), encoding="utf-8",
    )
    horizon = tmp_path / "horizon_selection.json"
    horizon_document = {
        "state": "SELECT_30S", "selected_horizon_ms": 30000,
    }
    horizon_document["horizon_selection_identity"] = horizon_selection_identity(horizon_document)
    horizon.write_text(__import__("json").dumps(horizon_document), encoding="utf-8")

    cal = config("v9_3_b4_calibration_r1.yaml")
    cal_tasksets = _tasksets(90, cal["grid"]["utilization_points"])
    assert len(build_requests(cal, cal_tasksets, source_commit="s", simulator_binary_sha256="b")) == 6750
    cal["execution"]["calibration_seal"] = str(selection)
    assert len(build_requests(
        cal, cal_tasksets, source_commit="s", simulator_binary_sha256="b",
        phase="confirmation",
    )) == 1350

    gate = config("v9_3_b4_horizon_gate_r1.yaml")
    gate["execution"]["calibration_seal"] = str(selection)
    all_tasksets = _tasksets(1600, gate["grid"]["utilization_points"])
    selected_tasksets = []
    for utilization in gate["grid"]["utilization_points"]:
        selected_tasksets.extend([
            taskset for taskset in all_tasksets if taskset.utilization == utilization
        ][:50])
    gate_requests = build_requests(
        gate, selected_tasksets, source_commit="s", simulator_binary_sha256="b",
    )
    assert len(gate_requests) == 4000

    formal = config("v9_3_b4_formal_template_r1.yaml")
    formal["execution"]["calibration_seal"] = str(selection)
    formal["execution"]["horizon_seal"] = str(horizon)
    formal_requests = build_requests(
        formal, all_tasksets, source_commit="s", simulator_binary_sha256="b",
    )
    assert len(formal_requests) == 43200
    selected_ids = {
        request.semantic_request_id for request in gate_requests
        if request.runtime_horizon_ms == 30000
    }
    unselected_ids = {
        request.semantic_request_id for request in gate_requests
        if request.runtime_horizon_ms == 60000
    }
    formal_ids = {request.semantic_request_id for request in formal_requests}
    audit_gate_formal_relationship(selected_ids, unselected_ids, formal_ids)


def test_cal_extension_and_full_grid_phase_counts(tmp_path):
    cal = config("v9_3_b4_calibration_r1.yaml")
    control = tmp_path / "calibration_control.json"
    control.write_text(__import__("json").dumps(calibration_control_document(
        status="EXTENSION_REQUIRED", extension_branch="A", kappa_star="50",
        requested_extension_etas=["1/4"],
    )), encoding="utf-8")
    cal["execution"]["calibration_seal"] = str(control)
    assert calibration_phase_plan_counts(cal, "extension_a")["requests"] == 450

    control.write_text(__import__("json").dumps(calibration_control_document(
        status="EXTENSION_REQUIRED", extension_branch="B",
        requested_extension_etas=["1/4", "2"],
    )), encoding="utf-8")
    assert calibration_phase_plan_counts(cal, "extension_b")["requests"] == 2700

    branch_a_cells = [
        {"kappa": kappa, "eta": eta}
        for kappa in ("10", "50", "200")
        for eta in ("1/2", "3/4", "1", "5/4", "3/2")
    ] + [{"kappa": "50", "eta": "1/4"}]
    control.write_text(__import__("json").dumps(
        calibration_control_document(cells=branch_a_cells),
    ), encoding="utf-8")
    fallback = calibration_phase_plan_counts(cal, "confirmation_full_grid")
    assert fallback["requests"] == 7200 and fallback["horizons"] == 1
