from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import load_config
from experiments.v9_3.scheduler_registry import SCHEDULER_IDS, audited_scheduler_registry
from experiments.v9_3.taskset_store import TasksetStore, prepare_service_curve


ROOT = Path(__file__).resolve().parents[1]
FORMAL_RUNNERS = tuple(
    ROOT / "scripts" / f"run_v9_3_{name}.py"
    for name in ("core1", "core2", "core3", "core4", "core5", "ext1", "ext2", "ext4")
)


def test_formal_import_graph_excludes_legacy_broken_tools_and_pilots():
    production = list((ROOT / "experiments" / "v9_3").glob("*.py")) + list(FORMAL_RUNNERS)
    text = "\n".join(path.read_text(encoding="utf-8") for path in production)
    assert "tools.about" not in text
    assert "taskset_generator.taskgen" not in text
    assert "run_v9_3_pilot" not in text
    assert "v20.4" not in text
    assert "v21" not in text


def test_formal_identity_code_never_uses_process_randomized_builtin_hash():
    for path in list((ROOT / "experiments" / "v9_3").glob("*.py")) + list(FORMAL_RUNNERS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "hash"
        ]
        assert not calls, f"process-randomized hash() used by {path}"


def test_nine_scheduler_registry_maps_to_nine_distinct_production_sources():
    registrations = audited_scheduler_registry(ROOT)
    assert tuple(item.scheduler_id for item in registrations) == SCHEDULER_IDS
    assert len(SCHEDULER_IDS) == len(set(SCHEDULER_IDS)) == 9
    assert len({item.implementation_source for item in registrations}) == 9


def test_shared_generation_identity_is_cross_experiment_and_e0_invariant(tmp_path):
    base = load_config(ROOT / "configs/v9_3_core1_smoke.yaml", expected_core="CORE-1")
    base["platform"]["task_count"] = [4]
    base["generation"]["period_min"] = 20
    base["generation"]["period_max"] = 80
    base["grid"]["base_seed"] = 930499
    base["grid"]["utilization_points"] = ["0.2"]
    base["grid"]["tasksets_per_cell"] = 1
    base["energy"]["initial_energy_values"] = ["0", "1"]
    base["execution"]["output_root"] = str(tmp_path / "service")
    base["execution"]["taskset_store"] = str(tmp_path / "store")
    service = prepare_service_curve(base, tmp_path / "service")
    store = TasksetStore(tmp_path / "store", base, service)

    observed = []
    for core in ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5"):
        config = deepcopy(base)
        config["experiment_id"] = f"identity-{core.lower()}"
        config["core"] = core
        cells = expand_cells(config)
        assert cells[0].generation_id == cells[1].generation_id
        seed = derive_seed(config["grid"]["base_seed"], cells[0].generation_id, 0)
        frozen = store.get_or_create(cells[0], 0)
        observed.append((cells[0].generation_id, seed, frozen.semantic_hash, frozen.task_payload))
    assert len({item[0] for item in observed}) == 1
    assert len({item[1] for item in observed}) == 1
    assert len({item[2] for item in observed}) == 1
    assert all(item[3] == observed[0][3] for item in observed)


def test_plotting_entrypoints_are_frozen_csv_only():
    paths = (
        ROOT / "experiments/v9_3/plot_cli.py",
        ROOT / "experiments/v9_3/plotting_data.py",
        ROOT / "scripts/plot_v9_3_core1.py",
        ROOT / "scripts/plot_v9_3_core2.py",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "asap_block_rta" not in text
    assert "simulation_engine" not in text
    assert "subprocess" not in text
