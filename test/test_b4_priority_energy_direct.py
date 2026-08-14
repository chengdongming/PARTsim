import hashlib
import json

from experiments.b4_priority_energy.experiment import (
    ALGORITHMS,
    GRID,
    PHASE_COUNTS,
    generate_base_taskset,
    iter_requests,
    request_plan,
    source_energy,
)


def test_direct_grid_and_scheduler_contract():
    rows = request_plan()
    assert len(rows) == 25800
    assert {row["algorithm"] for row in rows} == set(ALGORITHMS)
    assert {phase: sum(row["phase"] == phase for row in rows) for phase in PHASE_COUNTS} == PHASE_COUNTS
    assert len({row["case_id"] for row in rows}) == len(rows)


def test_direct_ids_retain_existing_reproducibility_vectors():
    row = next(row for row in request_plan(("pilot",)) if row["algorithm"] == "ASAP-BLOCK" and row["lambda_E"] == "0.70" and row["rho_E"] == "1")
    assert row["taskset_seed"] == 1979506832282504405
    assert row["taskset_id"] == "ts-76ef159067fe346e01596b1443f4f14d7dc6e3c0689360ea71328623288da5c6"
    assert row["source_id"] == "src-dc2baf44e8076f4b5e42482a53140678a70583cb402fe225347ffe1dba62b060"
    assert row["case_id"] == "case-3a8b02d0a0e0d8fefc8dd1617ac5bd86ed8bc75f464963789856c060b13f0e90"


def test_direct_task_generation_and_energy_are_deterministic():
    request = next(iter_requests(("pilot",)))
    document, payload = generate_base_taskset(request)
    again, again_payload = generate_base_taskset(request)
    assert payload == again_payload
    assert document == again
    assert hashlib.sha256(payload).hexdigest() == "3f448f1265d3a84b708ac2939e3d12c1a7ce4437ea3300995c3600e0be38e8ff"
    energy = source_energy(document, request.lambda_E)
    assert energy["E0_j"] > 0
    assert energy["Emax_j"] == 2 * energy["E0_j"]
    assert energy["alpha_w"] >= 0


def test_direct_plan_is_json_serializable():
    assert json.loads(json.dumps(request_plan(("pilot",))))
