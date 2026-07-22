import argparse
from copy import deepcopy
from pathlib import Path

import pytest

from global_task_generator import configure_arrival_offset_arguments
from experiments.v9_3.performance_taskset_store import (
    PerformanceTasksetStore, PerformanceTasksetStoreError,
    generation_command, generation_seed,
)
from v9_3_b4_helpers import PROJECT_ROOT, config


def test_no_arrival_offset_switch_is_explicit_and_mutually_exclusive(tmp_path):
    parser = configure_arrival_offset_arguments(argparse.ArgumentParser())
    assert parser.parse_args([]).arrival_offset is True
    assert parser.parse_args(["--arrival-offset"]).arrival_offset is True
    assert parser.parse_args(["--no-arrival-offset"]).arrival_offset is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--arrival-offset", "--no-arrival-offset"])
    command = generation_command(config(), utilization=__import__("fractions").Fraction("1/10"), seed=1, output=tmp_path / "x.yml")
    assert "--no-arrival-offset" in command


def test_generation_seed_is_dimension_stable():
    assert generation_seed(982201, 2, 200, 17) == 982618
    assert generation_seed(982201, 2, 200, 17, 1) == 1982618


def test_stale_same_size_store_rejected_for_seed_config_and_template(tmp_path):
    smoke = config("v9_3_b4_smoke.yaml")
    root = tmp_path / "store"
    store = PerformanceTasksetStore(root, smoke)
    manifest = store.freeze()
    assert store.verify_manifest()["store_identity"] == manifest["store_identity"]

    wrong_seed = deepcopy(smoke)
    wrong_seed["grid"]["base_seed"] += 1
    with pytest.raises(PerformanceTasksetStoreError, match="current config mismatch"):
        PerformanceTasksetStore(root, wrong_seed).verify_manifest()

    wrong_contract = deepcopy(smoke)
    wrong_contract["generation"]["wcet_rounding"] = "different"
    with pytest.raises(PerformanceTasksetStoreError, match="current config mismatch"):
        PerformanceTasksetStore(root, wrong_contract).verify_manifest()

    source_template = Path(smoke["generation"]["system_template"])
    if not source_template.is_absolute():
        source_template = PROJECT_ROOT / source_template
    changed_template = tmp_path / "changed-system.yml"
    changed_template.write_text(source_template.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    wrong_template = deepcopy(smoke)
    wrong_template["generation"]["system_template"] = str(changed_template)
    with pytest.raises(PerformanceTasksetStoreError, match="current config mismatch"):
        PerformanceTasksetStore(root, wrong_template).verify_manifest()
