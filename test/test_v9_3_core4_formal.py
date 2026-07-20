from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.v9_3.config import load_config
from experiments.v9_3.core4_sensitivity import Core4SensitivityRunner
from experiments.v9_3.formal_authorization import (
    FORMAL_PARAMETER_STATUS,
    FormalAuthorizationError,
)
from experiments.v9_3.monotonicity import service_curve_relation


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "configs/v9_3_core4_formal.yaml"
SMOKE = ROOT / "configs/v9_3_core4_smoke.yaml"


def test_formal_parameter_and_aggregate_contract():
    config = load_config(FORMAL, expected_core="CORE-4")
    assert config["parameter_status"] == FORMAL_PARAMETER_STATUS
    assert config["platform"] == {"cores": [4], "task_count": [10]}
    assert config["grid"]["utilization_points"] == [
        "3/10", "2/5", "1/2", "3/5", "7/10",
    ]
    assert config["grid"]["tasksets_per_cell"] == 200
    assert config["grid"]["base_seed"] == 930444
    assert config["sensitivity"]["baseline"] == {
        "E0": "1/20", "power_scale": "1",
        "service_curve_scale": "1", "battery_capacity": "20",
    }
    services = config["sensitivity"]["axes"]["service_curve"]["variants"]
    assert [row["exact_scale"] for row in services] == [
        "1/2", "3/4", "1", "5/4", "3/2",
    ]
    assert all(row["availability"] == "AVAILABLE" for row in services)

    plan = Core4SensitivityRunner(config).describe()
    assert plan["planned_sensitivity_row_count"] == 36000
    assert plan["solver_request_count"] == 36000
    assert plan["dependency_unavailable_row_count"] == 0
    assert plan["total_terminal_count"] == 36000
    assert plan["axis_counts"] == {
        "initial_energy": {
            "planned_row_count": 14000, "solver_request_count": 14000,
            "dependency_unavailable_count": 0, "terminal_count": 14000,
        },
        "service_curve": {
            "planned_row_count": 10000, "solver_request_count": 10000,
            "dependency_unavailable_count": 0, "terminal_count": 10000,
        },
        "power_scale": {
            "planned_row_count": 10000, "solver_request_count": 10000,
            "dependency_unavailable_count": 0, "terminal_count": 10000,
        },
        "method": {
            "planned_row_count": 2000, "solver_request_count": 2000,
            "dependency_unavailable_count": 0, "terminal_count": 2000,
        },
    }


def test_formal_profile_fails_closed_before_creating_output(tmp_path):
    config = deepcopy(load_config(FORMAL, expected_core="CORE-4"))
    config["execution"]["output_root"] = str(tmp_path / "formal")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    runner = Core4SensitivityRunner(config)
    with pytest.raises(FormalAuthorizationError, match="formal-authorization"):
        runner._initialize(resume=False)
    assert not runner.root.exists()


def test_smoke_metadata_remains_explicitly_nonformal(tmp_path):
    config = deepcopy(load_config(SMOKE, expected_core="CORE-4"))
    config["execution"]["output_root"] = str(tmp_path / "smoke")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    runner = Core4SensitivityRunner(config)

    runner._initialize(resume=False)
    metadata = json.loads(
        (runner.root / "run_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["formal_large_scale_run"] is False
    assert metadata["formal_authorization_id"] is None
    assert metadata["finite_sample_consistency_check_only"] is True

    # The same profile-specific flags are part of the fail-closed resume
    # envelope, not merely labels written on first initialization.
    runner._initialize(resume=True)


def test_formal_service_curves_are_exact_and_ordered_over_full_horizon(tmp_path):
    config = deepcopy(load_config(FORMAL, expected_core="CORE-4"))
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    runner = Core4SensitivityRunner(config)
    runner.root.mkdir(parents=True)
    _base, materials, relations = runner._service_materials()
    specs = config["sensitivity"]["axes"]["service_curve"]["variants"]
    curves = [materials[spec["id"]].values for spec in specs]
    assert all(len(curve) == 30001 for curve in curves)
    assert all(
        service_curve_relation(left, right) == "RIGHT_STRONGER"
        for left, right in zip(curves, curves[1:])
    )
    base = curves[2]
    for spec, curve in zip(specs, curves):
        scale = Fraction(spec["exact_scale"])
        assert all(value == original * scale for value, original in zip(curve, base))
    assert list(relations.values()) == [
        "FIRST_LEVEL", "RIGHT_STRONGER", "RIGHT_STRONGER",
        "RIGHT_STRONGER", "RIGHT_STRONGER",
    ]
    catalog = json.loads(
        (runner.root / "service_curve_catalog.json").read_text(encoding="utf-8")
    )
    assert [row["scale"] for row in catalog["curves"]] == [
        "1/2", "3/4", "1", "5/4", "3/2",
    ]
    assert all(row["point_count"] == 30001 for row in catalog["curves"])
    assert all(len(row["source_template_sha256"]) == 64 for row in catalog["curves"])
    assert all(len(row["curve_sha256"]) == 64 for row in catalog["curves"])
    assert all(len(row["semantic_hash"]) == 64 for row in catalog["curves"])


def test_smoke_service_materials_do_not_require_formal_catalog_fields(tmp_path):
    config = deepcopy(load_config(SMOKE, expected_core="CORE-4"))
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    runner = Core4SensitivityRunner(config)
    runner.root.mkdir(parents=True)
    _base, materials, relations = runner._service_materials()
    assert "repository-default-service-v1" in materials
    assert relations == {
        "repository-default-service-v1": "FIRST_LEVEL",
        "second-formal-service-curve": "DEPENDENCY_UNAVAILABLE",
    }
    assert not (runner.root / "service_curve_catalog.json").exists()


def test_smoke_v2_dry_run_contract_is_unchanged():
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_v9_3_core4.py", "--config",
            "configs/v9_3_core4_smoke.yaml", "--dry-run",
        ],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert '"planned_sensitivity_row_count": 14' in completed.stdout
    assert '"available_solver_request_count": 12' in completed.stdout
    assert '"dependency_unavailable_row_count": 2' in completed.stdout
    assert '"axis_counts"' not in completed.stdout
