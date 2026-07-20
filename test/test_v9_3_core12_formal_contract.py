from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from experiments.v9_3.cell_model import expand_cells, generation_dimensions
from experiments.v9_3.config import (
    TASK_WORKLOAD_CONTRACT_VERSION,
    config_hash,
    load_config,
)
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.formal_authorization import (
    FORMAL_PARAMETER_STATUS,
    FormalAuthorizationError,
    taskset_store_identity,
    verify_authorization,
)
from experiments.v9_3.taskset_store import (
    ServiceCurveMaterial,
    TasksetStore,
    TasksetStoreError,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "configs"
SYSTEM = ROOT / "system_config_unified_template.yml"
FORMAL_PATHS = {
    "CORE-1": CONFIG_ROOT / "v9_3_core1_formal.yaml",
    "CORE-2": CONFIG_ROOT / "v9_3_core2_formal.yaml",
}
CANDIDATE_PATHS = {
    "CORE-1": CONFIG_ROOT / "v9_3_core1_formal_candidate.yaml",
    "CORE-2": CONFIG_ROOT / "v9_3_core2_formal_candidate.yaml",
}
EXPECTED_CANDIDATE_FILE_HASHES = {
    "CORE-1": "d0ad84932512d596d8490f60baaafc32e41734121131288ec6e2557ee44ad6f5",
    "CORE-2": "b0a3e5e3a11dd6f28295465fd005503222099bfe89ff1ee6fdd8148374b7d21a",
}
EXPECTED_FORMAL_CONFIG_HASHES = {
    "CORE-1": "e0fe1259d2f23a9883b6f8635bed12e2b13211bd91f670e562bb573cd1bbd183",
    "CORE-2": "5e3c7333ba619f31b074b9fd5df495993c436900d8ca201d5cd0c272bbd30b6e",
}
EXPECTED_UTILIZATIONS = (
    Fraction(1, 10), Fraction(1, 5), Fraction(3, 10), Fraction(2, 5),
    Fraction(1, 2), Fraction(3, 5), Fraction(7, 10), Fraction(4, 5),
)
EXPECTED_E0 = (Fraction(0), Fraction(1, 20), Fraction(1))
EXPECTED_WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")
EXPECTED_METHODS = {
    "CORE-1": ["CW_THETA_CW", "LOC_THETA_LOC"],
    "CORE-2": [
        "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW",
        "LOC_THETA_LOC",
    ],
}
EXPECTED_REQUESTS = {"CORE-1": 9600, "CORE-2": 24000}


def _formal_configs():
    return {
        core: load_config(path, expected_core=core)
        for core, path in FORMAL_PATHS.items()
    }


def test_formal_configs_freeze_exact_grid_scale_methods_and_workloads():
    configs = _formal_configs()
    for core, config in configs.items():
        assert config["parameter_status"] == FORMAL_PARAMETER_STATUS
        assert config["platform"] == {"cores": [4], "task_count": [10]}
        assert config["generation"]["deadline_mode"] == "constrained"
        assert config["generation"]["priority_policy"] == "RM"
        assert tuple(config["generation"]["workload_candidates"]) == (
            EXPECTED_WORKLOADS
        )
        workload_contract = config["generation"]["workload_contract"]
        assert workload_contract["version"] == TASK_WORKLOAD_CONTRACT_VERSION
        assert tuple(workload_contract["ordered_candidates"]) == EXPECTED_WORKLOADS
        assert workload_contract["idle_system_state_reserved"] is True
        assert all(
            row["workload"] != "idle"
            for row in workload_contract["power_model"]
        )
        assert tuple(map(Fraction, config["grid"]["utilization_points"])) == (
            EXPECTED_UTILIZATIONS
        )
        assert tuple(map(Fraction, config["energy"]["initial_energy_values"])) == (
            EXPECTED_E0
        )
        assert config["grid"]["tasksets_per_cell"] == 200
        assert config["grid"]["base_seed"] == 930612
        assert config["grid"]["seed_mode"] == "generation_dimensions"
        assert config["analysis"]["variants"] == EXPECTED_METHODS[core]

        cells = expand_cells(config)
        assert len(cells) == 24
        assert {
            (cell.utilization, cell.exact_e0) for cell in cells
        } == {
            (utilization, exact_e0)
            for utilization in EXPECTED_UTILIZATIONS
            for exact_e0 in EXPECTED_E0
        }
        description = ExecutionEngine(config).describe()
        assert description["cell_count"] == 24
        assert description["unique_taskset_count"] == 1600
        assert description["request_count"] == EXPECTED_REQUESTS[core]


def test_core1_core2_share_exact_generation_and_1600_tasksets_across_e0():
    configs = _formal_configs()
    core1 = configs["CORE-1"]
    core2 = configs["CORE-2"]
    assert core1["generation"] == core2["generation"]
    assert core1["platform"] == core2["platform"]
    assert core1["energy"] == core2["energy"]
    assert core1["grid"] == core2["grid"]

    generation_maps = []
    all_taskset_keys = set()
    for config in (core1, core2):
        by_utilization = {}
        for cell in expand_cells(config):
            dimensions = generation_dimensions(
                config, cell.processors, cell.task_count, cell.utilization,
            )
            prior = by_utilization.setdefault(
                cell.utilization, (cell.generation_id, dimensions),
            )
            assert prior == (cell.generation_id, dimensions)
            for taskset_index in range(config["grid"]["tasksets_per_cell"]):
                all_taskset_keys.add((cell.generation_id, taskset_index))
        assert len(by_utilization) == 8
        generation_maps.append(by_utilization)

    assert generation_maps[0] == generation_maps[1]
    assert len(all_taskset_keys) == 1600


def test_formal_id_roots_store_and_hashes_are_new_while_candidates_are_preserved():
    configs = _formal_configs()
    candidate_configs = {
        core: load_config(path, expected_core=core)
        for core, path in CANDIDATE_PATHS.items()
    }
    formal_store = {
        config["execution"]["taskset_store"] for config in configs.values()
    }
    assert len(formal_store) == 1
    assert "workload_contract_v2" in next(iter(formal_store))

    for core, config in configs.items():
        candidate = candidate_configs[core]
        assert config_hash(config) == EXPECTED_FORMAL_CONFIG_HASHES[core]
        assert config["experiment_id"] != candidate["experiment_id"]
        assert config["execution"]["output_root"] != (
            candidate["execution"]["output_root"]
        )
        assert config["execution"]["taskset_store"] != (
            candidate["execution"]["taskset_store"]
        )
        assert hashlib.sha256(CANDIDATE_PATHS[core].read_bytes()).hexdigest() == (
            EXPECTED_CANDIDATE_FILE_HASHES[core]
        )


def test_frozen_formal_execution_requires_both_authorization_cli_bindings(tmp_path):
    config = _formal_configs()["CORE-1"]
    with pytest.raises(
        FormalAuthorizationError,
        match="--formal-authorization.*--source-freeze-config",
    ):
        verify_authorization(
            config,
            authorization_path=None,
            source_freeze_config=None,
            prepared_config=FORMAL_PATHS["CORE-1"],
            project_root=ROOT,
        )

    source_path = tmp_path / "source-freeze.yaml"
    source_path.write_bytes(FORMAL_PATHS["CORE-1"].read_bytes())
    with pytest.raises(
        FormalAuthorizationError,
        match="--formal-authorization.*--source-freeze-config",
    ):
        verify_authorization(
            config,
            authorization_path=None,
            source_freeze_config=source_path,
            prepared_config=FORMAL_PATHS["CORE-1"],
            project_root=ROOT,
        )

    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text("{}", encoding="utf-8")
    with pytest.raises(FormalAuthorizationError, match="--source-freeze-config"):
        verify_authorization(
            config,
            authorization_path=authorization_path,
            source_freeze_config=None,
            prepared_config=FORMAL_PATHS["CORE-1"],
            project_root=ROOT,
        )


def test_formal_runner_rejects_missing_authorization_flags(tmp_path):
    document = yaml.safe_load(FORMAL_PATHS["CORE-1"].read_text(encoding="utf-8"))
    document["execution"]["output_root"] = str(tmp_path / "formal-output")
    document["execution"]["taskset_store"] = str(tmp_path / "formal-store")
    prepared = tmp_path / "prepared-formal.yaml"
    prepared.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_v9_3_core1.py"),
            "--config",
            str(prepared),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "--formal-authorization" in completed.stderr
    assert "--source-freeze-config" in completed.stderr


def test_formal_authorization_and_taskset_store_reject_legacy_schema(tmp_path):
    legacy_store = tmp_path / "legacy-store"
    legacy_store.mkdir()
    (legacy_store / "pairing_manifest.json").write_text(
        json.dumps({
            "schema": "ASAP_BLOCK_V9_3_CORE12_PAIRING_MANIFEST_V1",
            "contract": {},
            "entries": [],
        }),
        encoding="utf-8",
    )
    with pytest.raises(
        FormalAuthorizationError, match="workload-contract-v2 schema"
    ):
        taskset_store_identity(legacy_store)

    config = _formal_configs()["CORE-1"]
    service = ServiceCurveMaterial((Fraction(0),), "service", "{}", SYSTEM)
    with pytest.raises(TasksetStoreError, match="legacy taskset store"):
        TasksetStore(legacy_store, config, service)
