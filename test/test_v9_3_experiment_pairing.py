from experiments.v9_3.cell_model import analysis_id, expand_cells
from experiments.v9_3.config import load_config
from experiments.v9_3.taskset_store import TasksetStore, prepare_service_curve
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
