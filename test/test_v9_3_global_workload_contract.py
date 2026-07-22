from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from experiments.v9_3.cell_model import expand_cells, taskset_id
from experiments.v9_3.config import (
    ConfigError,
    TASK_WORKLOAD_CONTRACT_VERSION,
    domain_hash,
    load_config,
    task_workload_contract_material,
    validate_config,
)
from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_generation import (
    StructuralRejection,
    build_scenario_instance,
    scenario_cells,
    workload_energy_table,
)
from experiments.v9_3.performance_config import CONFIG_SCHEMA as B4_CONFIG_SCHEMA
from experiments.v9_3.performance_config import load_performance_config
from experiments.v9_3.taskset_store import (
    FROZEN_TASKSET_SCHEMA,
    FROZEN_TASKSET_SEMANTIC_DOMAIN,
    PAIRING_CONTRACT_DOMAIN,
    ServiceCurveMaterial,
    TasksetStore,
    TasksetStoreError,
    prepare_service_curve,
)
from global_task_generator import (
    EnergyAwareTaskGenerator,
    _task_workload_candidate_identity,
)
from scripts import run_v9_3_pilot, run_v9_3_pilot2, run_v9_3_pilot3
from scripts.audit_v9_3_taskset_workload_contract import audit
from v9_3_experiment_helpers import make_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "configs"
SYSTEM = ROOT / "system_config_unified_template.yml"
CANDIDATES = ("bzip2", "control", "decrypt", "encrypt", "hash")


def _common_generation_configs():
    paths = []
    for path in sorted(CONFIG_ROOT.glob("v9_3*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "generation" in document:
            paths.append(path)
    return paths


def _load_generation_config(path: Path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw.get("schema") == B4_CONFIG_SCHEMA:
        return load_performance_config(path)
    if raw.get("extension") == "EXT-1B":
        return load_ext1b_config(path)
    return load_config(path)


def test_all_repository_v93_generation_configs_have_exact_contract():
    paths = _common_generation_configs()
    # Merged master contains the PR #46 B3 formal-confirmation profile plus
    # the four PR #45 B4 profiles. Keep the inventory exact and continue
    # validating every member below.
    assert len(paths) == 48
    assert {
        path.name for path in paths
    }.issuperset({
        "v9_3_ext1b2_sync_formal_r1_workload_contract_v2.yaml",
        (
            "v9_3_ext1b3_timing_formal_r1_workload_contract_v2_"
            "capacity_contract_v1.yaml"
        ),
        "v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml",
        "v9_3_ext1b3_b3_v2_formal_confirmation_r1.yaml",
        "v9_3_b4_calibration_r1.yaml",
        "v9_3_b4_horizon_gate_r1.yaml",
        "v9_3_b4_formal_template_r1.yaml",
        "v9_3_b4_smoke.yaml",
    })
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["generation"]["workload_candidates"] == list(CANDIDATES), path
        config = _load_generation_config(path)
        if raw.get("schema") == B4_CONFIG_SCHEMA:
            template = Path(config["generation"]["system_template"])
            if not template.is_absolute():
                template = ROOT / template
            contract = task_workload_contract_material(
                config["generation"]["workload_candidates"], template,
            )
        else:
            contract = config["generation"]["workload_contract"]
        assert contract["version"] == TASK_WORKLOAD_CONTRACT_VERSION, path
        assert contract["ordered_candidates"] == list(CANDIDATES), path
        assert [row["workload"] for row in contract["power_model"]] == list(
            CANDIDATES
        )
        assert contract["idle_system_state_reserved"] is True


def test_non_tasksetstore_pilot_configs_freeze_the_same_contract():
    pilot1 = run_v9_3_pilot.load_pilot_config(CONFIG_ROOT / "v9_3_pilot.yaml")
    pilot2 = run_v9_3_pilot2.load_config(CONFIG_ROOT / "v9_3_pilot2.yaml")
    pilot3 = run_v9_3_pilot3.load_config(CONFIG_ROOT / "v9_3_pilot3.yaml")
    generations = (
        pilot1["task_generation"],
        pilot2["screening"]["generation"],
        pilot3["screening"]["generation"],
    )
    assert all(
        item["workload_candidates"] == list(CANDIDATES)
        and item["workload_contract"]["version"]
        == TASK_WORKLOAD_CONTRACT_VERSION
        for item in generations
    )


def test_generator_default_is_lexical_non_idle_and_model_derived(tmp_path):
    document = yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    document["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ]["vision"] = 1.7
    path = tmp_path / "system.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    generator = EnergyAwareTaskGenerator(seed=7, system_config_path=str(path))
    assert generator.task_workload_candidates == tuple(sorted((*CANDIDATES, "vision")))
    assert "idle" not in generator.task_workload_candidates


def test_generator_default_produces_only_model_workloads_across_many_seeds():
    # Keep the model membership assertion independent of the generator's
    # candidate tuple so a bad tuple cannot prove itself correct.
    model_names = {
        row["workload"]
        for row in task_workload_contract_material(CANDIDATES, SYSTEM)[
            "power_model"
        ]
    }
    observed = []
    for seed in range(12):
        generator = EnergyAwareTaskGenerator(
            seed=930000 + seed, system_config_path=str(SYSTEM),
        )
        tasks, _resources, _dag, _energy = generator.generate_taskset(
            n=20, total_utilization=4.0, min_period=40, max_period=200,
            num_cpus=4, implicit_deadline=True, dag_enabled=False,
            energy_aware=False, arrival_offset=False,
        )
        observed.extend(
            str(task["workload"])
            for task in tasks
            if str(task.get("name", "")).startswith("task_")
        )
    assert len(observed) == 240
    assert "idle" not in observed
    assert set(observed) <= model_names


def test_candidate_and_power_model_identity_changes_are_auditable(tmp_path):
    baseline = task_workload_contract_material(CANDIDATES, SYSTEM)
    assert _task_workload_candidate_identity(CANDIDATES) == baseline[
        "candidate_identity"
    ]

    power_document = yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    power_document["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ]["control"] = 0.43
    power_path = tmp_path / "power.yml"
    power_path.write_text(
        yaml.safe_dump(power_document, sort_keys=False), encoding="utf-8",
    )
    changed_power = task_workload_contract_material(CANDIDATES, power_path)
    assert changed_power["candidate_identity"] == baseline["candidate_identity"]
    assert changed_power["power_model_identity"] != baseline[
        "power_model_identity"
    ]
    assert changed_power["contract_identity"] != baseline["contract_identity"]

    candidate_document = yaml.safe_load(SYSTEM.read_text(encoding="utf-8"))
    candidate_document["energy_management"]["scheduler_energy_model"][
        "workload_coefficients"
    ]["vision"] = 1.7
    candidate_path = tmp_path / "candidate.yml"
    candidate_path.write_text(
        yaml.safe_dump(candidate_document, sort_keys=False), encoding="utf-8",
    )
    expanded = tuple(sorted((*CANDIDATES, "vision")))
    changed_candidates = task_workload_contract_material(
        expanded, candidate_path,
    )
    assert changed_candidates["candidate_identity"] != baseline[
        "candidate_identity"
    ]
    assert changed_candidates["contract_identity"] != baseline[
        "contract_identity"
    ]


@pytest.mark.parametrize(
    "candidates,match",
    [
        ([], "must not be empty"),
        (["bzip2", "bzip2"], "must be unique"),
        (["bzip2", "idle"], "not a task workload"),
        (["bzip2", "unknown"], "absent from the configured model"),
        ([" bzip2"], "canonical names"),
        (["hash", "bzip2"], "stable lexical order"),
    ],
)
def test_generator_rejects_invalid_explicit_candidate_pool(candidates, match):
    with pytest.raises(ValueError, match=match):
        EnergyAwareTaskGenerator(
            seed=1,
            system_config_path=str(SYSTEM),
            task_workload_candidates=candidates,
        )


def test_formal_config_rejects_legal_subset_and_frozen_contract_tampering(tmp_path):
    config = make_config(tmp_path)
    subset = deepcopy(config)
    subset["generation"].pop("workload_contract")
    subset["generation"]["workload_candidates"] = list(CANDIDATES[:-1])
    with pytest.raises(ConfigError, match="do not exactly match"):
        validate_config(subset)
    tampered = deepcopy(config)
    tampered["generation"]["workload_contract"]["power_model_identity"] = "0" * 64
    with pytest.raises(ConfigError, match="does not match the actual system"):
        validate_config(tampered)


def _recompute_document_hashes(document):
    payload = document["tasks"]
    document["priority_hash"] = domain_hash(
        "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
        [
            {"task_id": row["task_id"], "priority_rank": row["priority_rank"]}
            for row in payload
        ],
    )
    document["power_hash"] = domain_hash(
        "ASAP_BLOCK:V9.3:POWER_VECTOR:v1",
        [{"task_id": row["task_id"], "P": row["P"]} for row in payload],
    )
    keys = [
        "schema", "generation_id", "taskset_index", "seed",
        "generation_parameters", "target_total_utilization",
        "actual_total_utilization", "priority_policy", "power_mode",
        "deadline_mode", "service_curve_reference", "tasks",
        "task_workload_contract",
    ]
    document["taskset_hash"] = domain_hash(
        FROZEN_TASKSET_SEMANTIC_DOMAIN,
        {key: document[key] for key in keys},
    )
    document["taskset_id"] = taskset_id(
        document["generation_id"], document["taskset_index"],
        document["taskset_hash"],
    )


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("legacy_v1", "legacy taskset store lacks mandatory"),
        ("missing_contract", "legacy taskset store lacks mandatory"),
        ("idle", "stored real-time task uses reserved idle workload"),
        ("unknown", "stored real-time task uses unknown workload"),
        ("power", "P does not match actual power model"),
        ("candidate_identity", "workload contract mismatch"),
        ("power_identity", "workload contract mismatch"),
        ("semantic_hash", "semantic hash mismatch"),
    ],
)
def test_taskset_store_load_rejects_every_illegal_contract_input(
    tmp_path, mutation, match,
):
    config = load_config(CONFIG_ROOT / "v9_3_core1_smoke.yaml")
    cell = expand_cells(config)[0]
    service = prepare_service_curve(config, tmp_path / "service")
    store = TasksetStore(tmp_path / "store", config, service)
    stored = store.get_or_create(cell, 0)
    document = json.loads(stored.canonical_path.read_text(encoding="utf-8"))
    if mutation == "legacy_v1":
        document["schema"] = "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1"
        document.pop("task_workload_contract")
    elif mutation == "missing_contract":
        document.pop("task_workload_contract")
    elif mutation == "idle":
        document["tasks"][0]["workload"] = "idle"
        _recompute_document_hashes(document)
    elif mutation == "unknown":
        document["tasks"][0]["workload"] = "unknown"
        _recompute_document_hashes(document)
    elif mutation == "power":
        document["tasks"][0]["P"] = "0"
        _recompute_document_hashes(document)
    elif mutation == "candidate_identity":
        document["task_workload_contract"]["candidate_identity"] = "0" * 64
    elif mutation == "power_identity":
        document["task_workload_contract"]["power_model_identity"] = "0" * 64
    else:
        document["taskset_hash"] = "0" * 64
    stored.canonical_path.write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(TasksetStoreError, match=match):
        store._load(stored.canonical_path, cell, 0)


def _fake_generator_run(workload):
    def run(command, **_kwargs):
        output = Path(command[command.index("-o") + 1])
        tasks = []
        for index in range(2):
            tasks.append({
                "name": f"task_{index}", "iat": 5, "runtime": 1,
                "deadline": 5,
                "params": (
                    "period=5,wcet=1,arrival_offset=0,"
                    f"workload={workload}"
                ),
                "code": [f"fixed(1, {workload})"],
            })
        output.write_text(
            yaml.safe_dump({"taskset": tasks}, sort_keys=False),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


@pytest.mark.parametrize(
    "workload,match",
    [
        ("idle", "generator produced reserved idle workload"),
        ("unknown", "outside task candidate contract"),
    ],
)
def test_taskset_store_generation_rejects_bad_generator_workloads(
    tmp_path, monkeypatch, workload, match,
):
    config = make_config(tmp_path)
    cell = expand_cells(config)[0]
    service = ServiceCurveMaterial((Fraction(0),), "service", "{}", SYSTEM)
    store = TasksetStore(tmp_path / "store", config, service)
    monkeypatch.setattr(
        "experiments.v9_3.taskset_store.subprocess.run",
        _fake_generator_run(workload),
    )
    with pytest.raises(TasksetStoreError, match=match):
        store._generate(store.path_for(cell.generation_id, 0), cell, 0)


def test_taskset_store_generation_rejects_wrong_authoritative_power(
    tmp_path, monkeypatch,
):
    config = make_config(tmp_path)
    cell = expand_cells(config)[0]
    service = ServiceCurveMaterial((Fraction(0),), "service", "{}", SYSTEM)
    store = TasksetStore(tmp_path / "store", config, service)
    monkeypatch.setattr(
        "experiments.v9_3.taskset_store.subprocess.run",
        _fake_generator_run("control"),
    )

    class WrongPowerSystem:
        def task_energy_per_tick(self, _workload):
            return 1

    monkeypatch.setattr(
        "experiments.v9_3.taskset_store.legacy_rta.load_system_config",
        lambda _path: WrongPowerSystem(),
    )
    with pytest.raises(TasksetStoreError, match="workload/P mismatch"):
        store._generate(store.path_for(cell.generation_id, 0), cell, 0)


@pytest.mark.parametrize(
    "core,path",
    [
        ("CORE-1", "v9_3_core1_smoke.yaml"),
        ("CORE-2", "v9_3_core2_smoke.yaml"),
        ("CORE-3", "v9_3_core3_smoke.yaml"),
        ("CORE-4", "v9_3_core4_smoke.yaml"),
        ("CORE-5", "v9_3_core5_smoke.yaml"),
    ],
)
def test_each_core_materializes_two_compliant_tasksets(tmp_path, core, path):
    config = load_config(CONFIG_ROOT / path, expected_core=core)
    config["platform"]["cores"] = config["platform"]["cores"][:1]
    config["platform"]["task_count"] = config["platform"]["task_count"][:1]
    config["grid"]["utilization_points"] = config["grid"][
        "utilization_points"
    ][:1]
    config["energy"]["initial_energy_values"] = config["energy"][
        "initial_energy_values"
    ][:1]
    config["grid"]["tasksets_per_cell"] = 2
    config["grid"]["taskset_index_start"] = 0
    service = prepare_service_curve(config, tmp_path / core / "service")
    store = TasksetStore(tmp_path / core / "store", config, service)
    cell = expand_cells(config)[0]
    tasksets = [store.get_or_create(cell, index) for index in range(2)]
    store.verify_pairing_manifest(require_complete=True)
    model = dict(store.task_workload_contract.power_model)
    assert len(tasksets) == 2
    assert all(
        row["workload"] != "idle"
        and row["workload"] in model
        and Fraction(str(row["P"])) == model[row["workload"]]
        for stored in tasksets for row in stored.task_payload
    )
    assert store.manifest_document()["contract"][
        "task_workload_contract"
    ]["contract_identity"] == store.task_workload_contract.contract_identity


def test_core1_core2_pairing_payload_and_manifest_identity_are_preserved(tmp_path):
    core1 = load_config(CONFIG_ROOT / "v9_3_core1_smoke.yaml")
    core2 = load_config(CONFIG_ROOT / "v9_3_core2_smoke.yaml")
    for config in (core1, core2):
        config["grid"]["tasksets_per_cell"] = 2
    service1 = prepare_service_curve(core1, tmp_path / "service1")
    service2 = prepare_service_curve(core2, tmp_path / "service2")
    store1 = TasksetStore(tmp_path / "store", core1, service1)
    store2 = TasksetStore(tmp_path / "store", core2, service2)
    cells = (expand_cells(core1)[0], expand_cells(core2)[0])
    for index in range(2):
        left = store1.get_or_create(cells[0], index)
        right = store2.get_or_create(cells[1], index)
        assert left.task_payload == right.task_payload
        assert left.semantic_hash == right.semantic_hash
        assert left.priority_hash == right.priority_hash
        assert left.power_hash == right.power_hash
    assert store1.manifest_document()["pairing_id"] == store2.manifest_document()[
        "pairing_id"
    ]
    old_id = domain_hash(
        "ASAP_BLOCK:V9.3:CORE12_PAIRING_CONTRACT:v1",
        store1.manifest_document()["contract"],
    )
    assert old_id != store1.manifest_document()["pairing_id"]


@pytest.mark.parametrize(
    "path",
    ("v9_3_ext1b1_smoke.yaml", "v9_3_ext1b2_smoke.yaml"),
)
def test_b1_b2_high_priority_power_mapping_remains_exact(tmp_path, path):
    config = load_ext1b_config(CONFIG_ROOT / path)
    base = expand_cells(config)[0]
    service = prepare_service_curve(config, tmp_path / path / "service")
    store = TasksetStore(tmp_path / path / "store", config, service)
    instance = None
    for index in range(16):
        stored = store.get_or_create(base, index)
        try:
            instance = build_scenario_instance(
                stored, config, scenario_cells(config)[0],
                logical_taskset_index=0, attempt_index=index,
                system_root=tmp_path / path / "systems",
            )
        except StructuralRejection:
            continue
        break
    assert instance is not None
    table = workload_energy_table(SYSTEM)
    low, high, middle = table[0], table[-1], table[len(table) // 2]
    task_count = len(instance.tasks)
    expected = []
    for row in instance.tasks:
        rank = int(row["priority_rank"])
        expected.append(
            high if rank * 3 < task_count else
            low if rank * 3 >= 2 * task_count else middle
        )
    assert [
        (row["workload"], Fraction(str(row["P"]))) for row in instance.tasks
    ] == expected
    assert all(row["workload"] != "idle" for row in instance.tasks)


def test_repository_auditor_accepts_new_store_and_classifies_legacy(tmp_path):
    config = load_config(CONFIG_ROOT / "v9_3_core1_smoke.yaml")
    service = prepare_service_curve(config, tmp_path / "service")
    cell = expand_cells(config)[0]
    store = TasksetStore(tmp_path / "store", config, service)
    stored = store.get_or_create(cell, 0)
    report = audit([tmp_path / "store"], verify_hashes=True)
    assert report["status"] == "COMPLIANT"
    assert report["physical_file_count"] == 1
    assert report["unique_semantic_taskset_count"] == 1
    assert report["idle_task_records"] == 0
    assert report["power_model_mismatches"] == 0
    assert report["pairing_manifest_failures"] == 0

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    document = json.loads(stored.canonical_path.read_text(encoding="utf-8"))
    document["schema"] = "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1"
    document.pop("task_workload_contract")
    document["tasks"][0]["workload"] = "idle"
    (legacy_root / "legacy-a.json").write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )
    (legacy_root / "legacy-b.json").write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )
    legacy = audit([legacy_root], verify_hashes=False)
    assert legacy["status"] == "LEGACY_NON_EXECUTABLE"
    assert legacy["physical_file_count"] == 2
    assert legacy["unique_semantic_taskset_count"] == 1
    assert legacy["duplicate_content_count"] == 1
    assert legacy["missing_contract"] == 2
    assert legacy["files_with_idle"] == 2
    assert legacy["idle_task_records"] == 2


def test_formal_and_calibration_identity_migration_is_explicit():
    paths = (
        "v9_3_core1_formal.yaml",
        "v9_3_core1_formal_candidate.yaml",
        "v9_3_core2_formal.yaml",
        "v9_3_core2_formal_candidate.yaml",
        "v9_3_core3_formal_b20_r2.yaml",
        "v9_3_core3_formal_b100_r2.yaml",
        "v9_3_core4_formal.yaml",
        "v9_3_core5a_formal_algorithmic.yaml",
        "v9_3_core5b_formal_workers.yaml",
        "v9_3_final_calibration.yaml",
        "v9_3_ext1b1_formal_r1.yaml",
        "v9_3_ext1b1_energy_calibration.yaml",
        "v9_3_ext1b2_sync_calibration.yaml",
        "v9_3_ext1b3_timing_calibration.yaml",
    )
    for name in paths:
        config = _load_generation_config(CONFIG_ROOT / name)
        assert config["experiment_id"].endswith("-workload-contract-v2")
        assert "workload_contract_v2" in config["execution"]["output_root"]
        assert "workload_contract_v2" in config["execution"]["taskset_store"]
        if "seed_space" in config:
            assert config["seed_space"].endswith("WORKLOAD_CONTRACT_V2")
