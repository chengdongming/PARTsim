from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from experiments.v9_3.cell_model import analysis_id, expand_cells
from experiments.v9_3.config import load_config
from experiments.v9_3.taskset_store import (
    ServiceCurveMaterial, TasksetStore, TasksetStoreError,
    prepare_service_curve,
)
from v9_3_experiment_helpers import FakeStore, make_config


def test_all_variants_and_e0_share_frozen_taskset(tmp_path):
    config = make_config(tmp_path, "CORE-2", e0=["0", "1"])
    cells = expand_cells(config)
    store = FakeStore(tmp_path)
    first = store.get_or_create(cells[0], 0)
    second = store.get_or_create(cells[1], 0)
    assert first.semantic_hash == second.semantic_hash
    assert first.seed == second.seed
    ids = {
        analysis_id(cells[0], first.semantic_hash, variant)
        for variant in config["analysis"]["variants"]
    }
    assert len(ids) == 5


def test_core1_core2_generation_identity_is_reusable(tmp_path):
    core1 = expand_cells(make_config(tmp_path, "CORE-1"))[0]
    core2 = expand_cells(make_config(tmp_path, "CORE-2"))[0]
    assert core1.generation_id == core2.generation_id


def _service(tmp_path, identity="service"):
    return ServiceCurveMaterial(
        (Fraction(0),), identity, "{}",
        Path(__file__).resolve().parents[1] / "system_config_unified_template.yml",
    )


def test_core1_core2_accept_the_same_pairing_manifest_contract(tmp_path):
    root = tmp_path / "shared-store"
    core1 = make_config(tmp_path, "CORE-1", e0=["0", "1"])
    core2 = make_config(tmp_path, "CORE-2", e0=["0", "1"])
    first = TasksetStore(root, core1, _service(tmp_path))
    second = TasksetStore(root, core2, _service(tmp_path))
    assert (
        first.manifest_document()["pairing_id"]
        == second.manifest_document()["pairing_id"]
    )


@pytest.mark.parametrize(
    "mutation",
    ("seed", "missing_taskset", "payload", "e0", "dimensions", "service"),
)
def test_pairing_manifest_rejects_every_cross_core_mismatch(
    tmp_path, mutation
):
    root = tmp_path / "shared-store"
    base = make_config(tmp_path, "CORE-1", e0=["0", "1"])
    store = TasksetStore(root, base, _service(tmp_path))
    if mutation == "missing_taskset":
        with pytest.raises(TasksetStoreError, match="missing or has extra"):
            store.verify_pairing_manifest(require_complete=True)
        return
    if mutation == "payload":
        stored = FakeStore(tmp_path).get_or_create(expand_cells(base)[0], 0)
        store._register_pairing_entry(stored)
        altered = replace(
            stored,
            task_payload=tuple(
                ({**dict(row), "C": int(row["C"]) + 1} if index == 0 else row)
                for index, row in enumerate(stored.task_payload)
            ),
        )
        with pytest.raises(TasksetStoreError, match="payload mismatch"):
            store._register_pairing_entry(altered)
        return
    changed = make_config(tmp_path, "CORE-2", e0=["0", "1"])
    service = _service(tmp_path)
    if mutation == "seed":
        changed["grid"]["base_seed"] += 1
    elif mutation == "e0":
        changed["energy"]["initial_energy_values"] = ["0", "2"]
    elif mutation == "dimensions":
        changed["platform"]["task_count"] = [3]
    else:
        service = _service(tmp_path, identity="different-service")
    with pytest.raises(TasksetStoreError, match="pairing manifest contract mismatch"):
        TasksetStore(root, changed, service)


def test_real_generator_same_seed_reproducible_and_constrained_d_le_t(tmp_path):
    config = load_config("configs/v9_3_constrained_deadline_smoke.yaml")
    cell = expand_cells(config)[0]
    service_a = prepare_service_curve(config, tmp_path / "run-a")
    service_b = prepare_service_curve(config, tmp_path / "run-b")
    first = TasksetStore(tmp_path / "store-a", config, service_a).get_or_create(cell, 0)
    second = TasksetStore(tmp_path / "store-b", config, service_b).get_or_create(cell, 0)
    assert first.seed == second.seed
    assert first.semantic_hash == second.semantic_hash
    assert first.task_payload == second.task_payload
    assert all(task.wcet <= task.deadline <= task.period for task in first.tasks)
    assert any(task.deadline < task.period for task in first.tasks)
